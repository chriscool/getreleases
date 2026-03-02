#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import subprocess
import json
import sys
import shutil
import os
import re
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, List, Dict, Any, Tuple

try:
    import curses
except ImportError:
    print("Error: 'curses' module not found.", file=sys.stderr)
    print("On Windows, install it with: pip install windows-curses", file=sys.stderr)
    sys.exit(1)

import git_ml_converter


# --- Configuration ---
MIN_MSG_COUNT = 5
MAX_MSG_COUNT = 40
MIN_AGE_DAYS = 21
MAX_AGE_DAYS = 90
GIT_ML_URL = "https://lore.kernel.org/git/"
DEFAULT_CLONE_PATH = os.path.expanduser("~/git/git-mailing-list-public-inbox")


def compute_edition(date: datetime) -> int:
    """Compute the Git Rev News edition number being prepared for a given date.

    Editions are monthly. Edition 1 was published March 2015.
    Each edition covers two months and is published at the end of the second.

    If today is strictly before the 10th of the month, we are still working
    on the edition whose second covered month is the previous month.
    Otherwise, we are working on the edition whose second covered month is
    the current month.
    """
    if date.day < 10:
        ref = (date.replace(day=1) - timedelta(days=1))
    else:
        ref = date
    return (ref.year - 2015) * 12 + ref.month - 2


def get_threads_dir(edition: int) -> str:
    """Return the path of the threads directory for the given edition."""
    return f"threads_{edition}"


def find_or_create_threads_dir(edition: int) -> str:
    """Return the threads directory for the given edition, creating it if needed."""
    threads_dir = get_threads_dir(edition)
    os.makedirs(threads_dir, exist_ok=True)
    return threads_dir


def sanitize_filename(name: str) -> str:
    """Sanitize a string to be used as a filename."""
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[-\s]+', '-', name)
    return name.strip('-')[:50]


INDEX_FILENAME = "index.md"


def load_index(threads_dir: str) -> Dict[str, Any]:
    """Load the index.md from a threads directory.

    Returns a dict with:
      - 'edition': int
      - 'created': str (YYYY-MM-DD)
      - 'done_mids': set of Message-IDs already recorded
    """
    index_path = os.path.join(threads_dir, INDEX_FILENAME)
    result: Dict[str, Any] = {'edition': None, 'created': None, 'done_mids': set()}

    if not os.path.exists(index_path):
        return result

    with open(index_path, 'r', encoding='utf-8') as f:
        content = f.read()

    fm_match = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        m = re.search(r'^edition:\s*(\d+)', fm, re.MULTILINE)
        if m:
            result['edition'] = int(m.group(1))
        m = re.search(r'^created:\s*(\S+)', fm, re.MULTILINE)
        if m:
            result['created'] = m.group(1)

    for m in re.finditer(r'^\s+-\s+Message-ID:\s+`([^`]+)`', content, re.MULTILINE):
        result['done_mids'].add(m.group(1))

    return result


def save_index(threads_dir: str, edition: int, threads: List[Dict[str, Any]],
               existing: Optional[Dict[str, Any]] = None) -> None:
    """Write or update index.md in the given threads directory.

    New threads are appended; threads already in the existing index are kept.
    """
    index_path = os.path.join(threads_dir, INDEX_FILENAME)
    created = existing.get('created') if existing else None
    if not created:
        created = datetime.now().strftime('%Y-%m-%d')

    already_done = existing.get('done_mids', set()) if existing else set()
    new_threads = [t for t in threads if t['root_mid'] not in already_done]

    existing_body = ""
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            content = f.read()
        fm_end = content.find('\n---\n', content.find('---\n'))
        if fm_end != -1:
            after_fm = content[fm_end + 5:]
            lines = after_fm.splitlines(keepends=True)
            existing_body = ''.join(
                l for l in lines if not l.startswith('# Git Rev News')
            )

    new_entries = ""
    for t in new_threads:
        blob = t.get('blob', '')[:8]
        filename = f"{sanitize_filename(t['subject'])}_{blob}.txt"
        mid = t['root_mid']
        subject = t['subject']
        new_entries += (
            f"\n- **{subject}**\n"
            f"  - File: `{filename}`\n"
            f"  - Message-ID: `{mid}`\n"
            f"  - Notes:\n"
        )

    if not existing_body.strip():
        body = f"## Selected Threads\n{new_entries}"
    else:
        body = existing_body.strip() + "\n" + new_entries

    front_matter = f"---\nedition: {edition}\ncreated: {created}\n---\n"
    header = f"\n# Git Rev News Edition {edition} - Raw Materials\n\n"

    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(front_matter + header + body + "\n")


class MailingListStore:
    """Encapsulates all interactions with lei and the local git repository."""

    def __init__(self):
        self.min_msg_count = MIN_MSG_COUNT
        self.max_msg_count = MAX_MSG_COUNT
        self.min_age_days = MIN_AGE_DAYS
        self.max_age_days = MAX_AGE_DAYS

    def get_repo_path(self) -> Optional[str]:
        """Get the first local repo path from lei externals."""
        try:
            proc = subprocess.run(["lei", "ls-external"], capture_output=True, text=True)
            externals = proc.stdout.strip()
            if not externals:
                return None
            local_paths = [line.split()[0] for line in externals.splitlines() if line.strip().startswith('/')]
            for path in local_paths:
                if os.path.isdir(path):
                    return path
        except subprocess.CalledProcessError:
            pass
        return None

    def get_latest_message_date(self, repo_path: str) -> Optional[str]:
        """Queries lei for the most recent message date in the given repository."""
        cmd = [
            "lei", "q", "--only", repo_path,
            "-n", "1", "-s", "received", "dt:1.month.ago..", "-f", "json"
        ]

        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            if not result.stdout.strip():
                return None

            data = json.loads(result.stdout)
            if not data:
                return None

            latest_dt_str = data[0].get('dt')

            if isinstance(latest_dt_str, list):
                return latest_dt_str[0]

            return latest_dt_str

        except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError, KeyError):
            return None

    def parse_date(self, date_str: str) -> Optional[datetime]:
        """Parses the 'dt' field from lei JSON."""
        try:
            return datetime.strptime(date_str[:19].replace('T', ' '), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None

    def is_recent(self, date_str: Optional[str]) -> bool:
        """Check if the given date string is less than 1 day old."""
        if not date_str:
            return False
        dt = self.parse_date(date_str)
        if dt:
            return (datetime.now() - dt).days < 1
        return False

    def get_lei_results(self) -> List[Dict[str, Any]]:
        """Executes lei q to find candidate messages."""
        date_query = f"d:{self.max_age_days}.days.ago..{self.min_age_days}.days.ago"

        cmd = [
            "lei", "q",
            "-t",
            "-f", "json",
            date_query
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            if not result.stdout.strip():
                return []
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Error running lei: {e}") from e

    def analyze_threads(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Groups messages by thread_id and filters based on user criteria."""
        threads = defaultdict(list)

        for msg in messages:
            if not msg:
                continue

            refs = msg.get('refs', [])
            if refs:
                t_id = refs[0]
            else:
                t_id = msg.get('m')

            if t_id:
                threads[t_id].append(msg)

        valid_threads = []
        now = datetime.now()
        dropped_counts = {"count": 0, "age": 0}

        for t_id, msgs in threads.items():
            count = len(msgs)

            if not (self.min_msg_count <= count <= self.max_msg_count):
                dropped_counts["count"] += 1
                continue

            dates = []
            for m in msgs:
                d = self.parse_date(m.get('dt'))
                if d:
                    dates.append(d)

            if not dates:
                continue

            last_email_date = max(dates)
            age = (now - last_email_date).days

            participants = set()
            for m in msgs:
                for sender in m.get('f', []):
                    if len(sender) > 1:
                        participants.add(sender[1])

            if self.min_age_days <= age <= self.max_age_days:
                msgs.sort(key=lambda x: x.get('dt', ''))
                root_subject = msgs[0].get('s', '(No Subject)')

                valid_threads.append({
                    'thread_id': t_id,
                    'subject': root_subject,
                    'count': count,
                    'last_activity': last_email_date.strftime("%Y-%m-%d"),
                    'participants': len(participants),
                    'age_days': age,
                    'blob': msgs[0].get('blob', ''),
                    'root_mid': msgs[0].get('m', '')
                })
            else:
                dropped_counts["age"] += 1

        print(f"Filtered out: {dropped_counts['count']} by size, {dropped_counts['age']} by date.", file=sys.stderr)
        return valid_threads


def _decode_header(value: str) -> str:
    """Decode an RFC 2047 encoded email header value to a plain string."""
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _parse_overview_date(date_str: str) -> str:
    """Parse an RFC 2822 date string into YYYY-MM-DD, or return blanks on failure."""
    from email.utils import parsedate
    date_str = date_str.strip()
    if not date_str:
        return '          '
    parsed = parsedate(date_str)
    if parsed:
        try:
            return datetime(*parsed[:3]).strftime('%Y-%m-%d')
        except Exception:
            pass
    return '          '


class ThreadWorkspace:
    """Manages thread list state, selection, navigation and data fetching.

    Owns all state independent of how the UI is rendered: thread list,
    selection, search, message navigation within a thread, and data cache.
    Has no dependency on curses.
    """

    def __init__(self, threads: List[Dict[str, Any]], repo_path: Optional[str],
                 edition: Optional[int], done_mids: Optional[set]):
        self.threads = threads
        self.selected = [False] * len(threads)
        self.cursor = 0
        self.offset = 0
        self.repo_path = repo_path
        self.edition = edition
        self.done_mids = done_mids or set()

        self.search_term = ""
        self.search_matches: List[int] = []
        self.current_match_idx = -1
        self.searching = False

        self.thread_cursor = 0
        self.thread_scroll_offset = 0
        self.message_scroll_offset = 0

        self._preview_cache: Dict[str, List[str]] = {}
        self._overview_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._overview_loading: set = set()
        self._fetch_done = threading.Event()

    @property
    def is_loading(self) -> bool:
        """True if any background thread fetch is in progress."""
        return bool(self._overview_loading)

    def consume_fetch_done(self) -> bool:
        """Return True and clear the flag if a background fetch just completed."""
        if self._fetch_done.is_set():
            self._fetch_done.clear()
            return True
        return False

    def fetch_email_body(self, blob_id: str, max_lines: int = 20) -> List[str]:
        """Fetch the email body using git show, with caching."""
        cache_key = f"{blob_id}:{max_lines}"
        if cache_key in self._preview_cache:
            return self._preview_cache[cache_key]

        if not self.repo_path:
            return []

        try:
            cmd = ["git", "show", blob_id]
            cwd = None
            if os.path.isdir(self.repo_path):
                v2_all = os.path.join(self.repo_path, "all.git")
                if os.path.isdir(v2_all):
                    cwd = v2_all
                else:
                    cwd = self.repo_path

            result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, errors='replace', timeout=10)
            lines = result.stdout.splitlines()

            body_lines = []
            header_ended = False
            for idx, line in enumerate(lines):
                if not header_ended:
                    if line == '':
                        next_line = lines[idx + 1] if idx + 1 < len(lines) else ''
                        if not next_line or next_line[0] not in ' \t':
                            header_ended = True
                    if not header_ended:
                        continue
                body_lines.append(line)

            self._preview_cache[cache_key] = body_lines[:max_lines]
            return self._preview_cache[cache_key]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def fetch_thread_overview(self, root_mid: str) -> Optional[List[Dict[str, Any]]]:
        """Fetch the full thread messages for the given root Message-ID.

        Returns the list of message dicts if cached, or None if still loading.
        Fetching is done in a background thread; the result is cached.
        """
        if root_mid in self._overview_cache:
            return self._overview_cache[root_mid]

        if root_mid in self._overview_loading:
            return None

        self._overview_loading.add(root_mid)

        def _fetch():
            try:
                messages = git_ml_converter.fetch_lei_thread(root_mid, self.repo_path, quiet=True)
            except Exception:
                messages = []
            self._overview_cache[root_mid] = messages
            self._overview_loading.discard(root_mid)
            self._fetch_done.set()

        threading.Thread(target=_fetch, daemon=True).start()
        return None

    def find_matches(self, term: str) -> List[int]:
        """Find thread indices whose subject contains term (case-insensitive)."""
        if not term:
            return []
        term_lower = term.lower()
        return [i for i, t in enumerate(self.threads) if term_lower in t['subject'].lower()]

    def get_selected_mids(self) -> List[str]:
        """Return the root Message-IDs of all selected threads."""
        return [self.threads[i]['root_mid'] for i in range(len(self.threads)) if self.selected[i]]

    def move_cursor(self, delta: int) -> None:
        """Move the thread list cursor by delta, clamped to valid range.

        Resets in-thread navigation state since we are now on a different thread.
        """
        self.cursor = max(0, min(len(self.threads) - 1, self.cursor + delta))
        self.thread_cursor = 0
        self.thread_scroll_offset = 0
        self.message_scroll_offset = 0

    def move_thread_cursor(self, delta: int, msg_count: int) -> None:
        """Move the message cursor within the thread overview by delta."""
        self.thread_cursor = max(0, min(max(0, msg_count - 1), self.thread_cursor + delta))

    def scroll_message(self, delta: int) -> None:
        """Scroll the message body by delta lines (upper clamping at render time)."""
        self.message_scroll_offset = max(0, self.message_scroll_offset + delta)

    def toggle_selection(self) -> None:
        """Toggle selection state of the thread under the cursor."""
        self.selected[self.cursor] = not self.selected[self.cursor]

    def select_all(self) -> None:
        """Select all threads, or deselect all if all are already selected."""
        all_selected = all(self.selected)
        self.selected = [not all_selected] * len(self.threads)

    def start_search(self) -> None:
        """Enter search mode with an empty term."""
        self.searching = True
        self.search_term = ""
        self.search_matches = []
        self.current_match_idx = -1

    def update_search(self, term: str) -> None:
        """Update the search term and recompute matches."""
        self.search_term = term
        self.search_matches = self.find_matches(term)
        self.current_match_idx = 0 if self.search_matches else -1

    def confirm_search(self) -> None:
        """Exit search mode, keeping the cursor on the current match."""
        self.searching = False
        if self.search_matches:
            self.cursor = self.search_matches[self.current_match_idx]

    def cancel_search(self) -> None:
        """Exit search mode, clearing the search term and matches."""
        self.searching = False
        self.search_term = ""
        self.search_matches = []
        self.current_match_idx = -1

    def next_match(self) -> None:
        """Advance to the next search match, wrapping around."""
        if self.search_matches:
            self.current_match_idx = (self.current_match_idx + 1) % len(self.search_matches)
            self.cursor = self.search_matches[self.current_match_idx]

    def prev_match(self) -> None:
        """Go back to the previous search match, wrapping around."""
        if self.search_matches:
            self.current_match_idx = (self.current_match_idx - 1) % len(self.search_matches)
            self.cursor = self.search_matches[self.current_match_idx]


class ThreadSelectorTUI:
    """Manages the curses-based thread selection interface."""

    def __init__(self, threads: List[Dict[str, Any]], repo_path: Optional[str] = None,
                 edition: Optional[int] = None, done_mids: Optional[set] = None):
        self.ws = ThreadWorkspace(threads, repo_path, edition, done_mids)
        self.show_help_overlay = False
        self.show_preview = True
        self.preview_mode = 'THREAD'  # 'MESSAGE' or 'THREAD'
        self.view_mode = 'SPLIT'  # 'SPLIT' or 'FULLSCREEN'
        self.focus = 'THREAD_LIST'  # 'THREAD_LIST' or 'PREVIEW'

    def _sanitize_for_curses(self, text: str) -> str:
        """Remove non-printable and control characters for curses display."""
        return ''.join(c if 32 <= ord(c) < 127 else '?' for c in text)

    def _toggle_preview_mode(self, mode: str) -> None:
        """Toggle preview visibility and mode for the given mode ('MESSAGE' or 'THREAD').

        Logic:
        - If preview is showing the requested mode: hide preview
        - If preview is showing the other mode: switch to requested mode
        - If preview is hidden: show in requested mode
        """
        if self.show_preview and self.preview_mode == mode:
            self.show_preview = False
            self.focus = 'THREAD_LIST'  # Reset focus when preview hidden
        elif self.show_preview:
            self.preview_mode = mode
        else:
            self.show_preview = True
            self.preview_mode = mode

    def show_help(self, stdscr):
        """Display help screen overlay."""
        h, w = stdscr.getmaxyx()

        stdscr.clear()

        lines = [
            "Help - Key Bindings",
            "=" * 45,
            "",
            "Focus (split-pane mode):",
            "  Tab         - Toggle focus between thread list and preview",
            "  Escape      - Return focus to thread list (or exit full-screen)",
            "  ►           - Indicates which pane has focus",
            "",
            "Thread List (when thread list is focused):",
            "  k / Up      - Move cursor up",
            "  j / Down    - Move cursor down",
            "  Space       - Toggle selection of current thread",
            "  a           - Toggle select all / deselect all",
            "  /           - Search thread subjects",
            "  n / p       - Next / previous search match",
            "",
            "Preview Pane (when preview is focused or full-screen):",
            "  k / Up      - Scroll up (message body) or move up (thread overview)",
            "  j / Down    - Scroll down (message body) or move down (thread overview)",
            "  Ctrl+P      - View selected message (when in thread overview)",
            "",
            "Preview Modes:",
            "  Ctrl+P      - Message preview (toggle/show/switch)",
            "  Ctrl+T      - Thread overview (toggle/show/switch)",
            "  Ctrl+F      - Toggle full-screen mode",
            "",
            "Markers:",
            "  [ ]         - Not selected",
            "  [X]         - Selected for processing",
            "  [D]         - Already processed in a previous run",
            "",
            "Other:",
            "  ?           - Show this help",
            "  Q           - Quit and return selected threads",
            "",
            "Press any key to return...",
        ]

        block_width = max(len(line) for line in lines)
        start_x = max(0, (w - block_width) // 2)
        start_y = max(0, (h - len(lines)) // 2)
        for i, line in enumerate(lines):
            stdscr.addstr(start_y + i, start_x, line)

        curses.doupdate()

    def _build_body_preview(self, thread: Dict[str, Any], preview_width: int, h: int) -> List[Tuple[str, int]]:
        """Return preview lines showing a scrollable message body.

        Uses thread_cursor to select which message in the thread to show,
        and message_scroll_offset for vertical scrolling within that message.
        """
        messages = self.ws.fetch_thread_overview(thread['root_mid'])
        if messages:
            msg = messages[min(self.ws.thread_cursor, len(messages) - 1)]
            from_hdr = _decode_header(msg.get('from', ''))
            date_hdr = msg.get('date', '')
            subject_hdr = _decode_header(msg.get('subject', ''))
            body_lines = [self._sanitize_for_curses(l[:preview_width-1]) for l in msg.get('body', [])]
        else:
            from_hdr = ''
            date_hdr = ''
            subject_hdr = thread['subject']
            body_lines = [self._sanitize_for_curses(l[:preview_width-1])
                          for l in self.ws.fetch_email_body(thread['blob'], 10000)]

        header: List[Tuple[str, int]] = [
            (f"From:    {from_hdr[:preview_width-2]}", 0),
            (f"Date:    {date_hdr[:preview_width-2]}", 0),
            (f"Subject: {subject_hdr[:preview_width-2]}", 0),
            ("─" * min(preview_width - 1, 80), 0),
        ]

        available = max(1, h - len(header) - 4)
        self.ws.message_scroll_offset = min(self.ws.message_scroll_offset,
                                            max(0, len(body_lines) - available))
        visible = body_lines[self.ws.message_scroll_offset:self.ws.message_scroll_offset + available]

        return header + [(line, 0) for line in visible]

    def _build_thread_overview(self, messages: List[Dict[str, Any]],
                               preview_width: int, h: int) -> List[Tuple[str, int]]:
        """Return preview lines showing a lore-style thread overview with cursor highlight."""
        available = h - 5  # Lines available for messages (below header row)

        # Adjust scroll offset to keep thread_cursor visible
        if self.ws.thread_cursor < self.ws.thread_scroll_offset:
            self.ws.thread_scroll_offset = self.ws.thread_cursor
        elif self.ws.thread_cursor >= self.ws.thread_scroll_offset + available:
            self.ws.thread_scroll_offset = self.ws.thread_cursor - available + 1

        header = f"Thread overview: {len(messages)} messages"
        lines: List[Tuple[str, int]] = [(header, 0), ("", 0)]

        for idx in range(self.ws.thread_scroll_offset, min(len(messages), self.ws.thread_scroll_offset + available)):
            msg = messages[idx]
            subject = _decode_header(msg.get('subject', '(No Subject)').strip())
            sender = _decode_header(msg.get('from', '').strip())
            date_fmt = _parse_overview_date(msg.get('date', ''))

            refs = msg.get('references', '') or ''
            depth = len(refs.split()) if refs.strip() else 0
            indent = '` ' * depth

            cursor_marker = '►' if idx == self.ws.thread_cursor else ' '
            entry = f"{cursor_marker} {date_fmt} {indent}{subject} {sender}"
            attr = curses.A_REVERSE if idx == self.ws.thread_cursor else 0
            lines.append((self._sanitize_for_curses(entry[:preview_width-1]), attr))

        return lines

    def _get_preview_lines(self, thread: Dict[str, Any], preview_width: int, h: int) -> List[Tuple[str, int]]:
        """Return the lines to display in the preview pane for the given thread."""
        if self.preview_mode == 'MESSAGE':
            return self._build_body_preview(thread, preview_width, h)

        messages = self.ws.fetch_thread_overview(thread['root_mid'])
        if messages is None:
            return [("Loading...", 0)]
        return self._build_thread_overview(messages, preview_width, h)

    def _render_fullscreen(self, stdscr, h: int, w: int) -> None:
        """Render full-screen mode: preview/overview takes entire terminal."""
        current_thread = self.ws.threads[self.ws.cursor] if self.ws.threads else None
        if not current_thread:
            return

        edition_prefix = f"Edition {self.ws.edition} | " if self.ws.edition is not None else ""
        mode_name = "Thread Overview" if self.preview_mode == 'THREAD' else "Message Preview"
        title = f"{edition_prefix}{mode_name} - Full Screen (Ctrl+F: exit, Ctrl+P/T: switch mode)"
        stdscr.addstr(0, 0, title[:w-1], curses.A_BOLD)

        subject_line = f"Thread: {current_thread['subject']}"
        stdscr.addstr(1, 0, subject_line[:w-1])
        stdscr.addstr(2, 0, "─" * min(w - 1, 120))

        preview_lines = self._get_preview_lines(current_thread, w - 2, h)
        for i, (line, attr) in enumerate(preview_lines):
            try:
                if 3 + i < h - 1:
                    stdscr.addstr(3 + i, 0, line[:w-1], attr)
            except curses.error:
                pass

        status = f"Selected: {sum(self.ws.selected)}/{len(self.ws.threads)} | Thread {self.ws.cursor + 1}/{len(self.ws.threads)}"
        stdscr.addstr(h-1, 0, status[:w-1])

    def render(self, stdscr):
        """Render the TUI."""
        if self.show_help_overlay:
            self.show_help(stdscr)
            return

        stdscr.erase()
        h, w = stdscr.getmaxyx()

        if self.view_mode == 'FULLSCREEN':
            self._render_fullscreen(stdscr, h, w)
            return

        self._render_split(stdscr, h, w)

    def _render_split(self, stdscr, h: int, w: int) -> None:
        """Render split-pane mode: thread list on the left, optional preview on the right."""
        if self.show_preview and w >= 105:
            list_width = max(55, min(w // 2, 140))
            preview_width = w - list_width - 1
            show_preview = True
        else:
            list_width = w - 1
            preview_width = 0
            show_preview = False

        fixed_width = 3 + 4 + 3 + 12 + 2
        subject_width = max(20, list_width - fixed_width - 1)

        edition_prefix = f"Edition {self.ws.edition} | " if self.ws.edition is not None else ""
        list_focus = self.focus == 'THREAD_LIST'
        list_marker = "►" if list_focus else " "
        if self.ws.searching:
            title = f"{edition_prefix}Search: {self.ws.search_term} (Enter: done, Esc: cancel, n/p next/prev, Ctrl+F full)"
        else:
            title = f"{list_marker} {edition_prefix}Select threads (? help, / search, Ctrl+F full, Space toggle, Q quit)"
        title_attr = curses.A_BOLD if list_focus else 0
        stdscr.addstr(0, 0, title[:list_width-1], title_attr)
        if show_preview:
            stdscr.addstr(0, list_width, "│")
            preview_focus = self.focus == 'PREVIEW'
            preview_marker = "►" if preview_focus else " "
            mode_label = "Thread overview (Ctrl+T/P)" if self.preview_mode == 'THREAD' else "Message preview (Ctrl+P/T)"
            preview_label = f"{preview_marker} {mode_label} (Tab: switch focus)"
            preview_attr = curses.A_BOLD if preview_focus else 0
            stdscr.addstr(0, list_width + 1, preview_label[:preview_width - 1], preview_attr)

        header = f"{'Age':<3} | {'Msgs':<4} | {'Ppl':<3} | {'Subject':<{subject_width}}"
        stdscr.addstr(1, 0, header[:list_width-1])
        if show_preview:
            stdscr.addstr(1, list_width, "│")
        stdscr.addstr(2, 0, "-" * min(list_width - 1, w - 1))
        if show_preview and list_width < w:
            stdscr.addstr(2, list_width, "├" + "─" * (preview_width - 1))

        visible_rows = h - 4
        if self.ws.offset > self.ws.cursor:
            self.ws.offset = self.ws.cursor
        elif self.ws.cursor >= self.ws.offset + visible_rows:
            self.ws.offset = self.ws.cursor - visible_rows + 1

        for i in range(self.ws.offset, min(len(self.ws.threads), self.ws.offset + visible_rows)):
            row = i - self.ws.offset + 3
            if row >= h:
                break

            t = self.ws.threads[i]
            if t['root_mid'] in self.ws.done_mids:
                marker = "[D]"
            elif self.ws.selected[i]:
                marker = "[X]"
            else:
                marker = "[ ]"
            subject = t['subject'][:subject_width-3] + "..." if len(t['subject']) > subject_width else t['subject']
            line = f"{t['age_days']:<3} | {t['count']:<4} | {t['participants']:<3} | {marker} {subject:<{subject_width}}"

            if i == self.ws.cursor:
                stdscr.addstr(row, 0, line[:list_width-1], curses.A_REVERSE)
            else:
                stdscr.addstr(row, 0, line[:list_width-1])

        current_thread = self.ws.threads[self.ws.cursor] if self.ws.threads else None
        if show_preview and current_thread and preview_width > 10:
            preview_lines = self._get_preview_lines(current_thread, preview_width, h)

            for i, (line, attr) in enumerate(preview_lines):
                try:
                    if 3 + i < h:
                        stdscr.addstr(3 + i, list_width + 1, line[:preview_width - 1], attr)
                except curses.error:
                    pass

        if self.ws.search_matches:
            stdscr.addstr(h-2, 0, f"Match: {self.ws.current_match_idx + 1}/{len(self.ws.search_matches)}"[:list_width-1])
        stdscr.addstr(h-1, 0, f"Selected: {sum(self.ws.selected)}/{len(self.ws.threads)}"[:list_width-1])

    def _handle_input_search(self, key: int) -> None:
        """Handle key input while in search mode."""
        if key in (curses.KEY_ENTER, 10, 13):
            self.ws.confirm_search()
        elif key == 27:  # Escape
            self.ws.cancel_search()
        elif key in (curses.KEY_BACKSPACE, 127):
            self.ws.update_search(self.ws.search_term[:-1])
        elif 32 <= key <= 126:
            self.ws.update_search(self.ws.search_term + chr(key))

    def _handle_input_thread_list(self, key: int) -> Optional[List[str]]:
        """Handle key input when focus is on the thread list."""
        if key in (curses.KEY_UP, ord('k')):
            self.ws.move_cursor(-1)
            if self.preview_mode == 'THREAD':
                self.ws.fetch_thread_overview(self.ws.threads[self.ws.cursor]['root_mid'])
        elif key in (curses.KEY_DOWN, ord('j')):
            self.ws.move_cursor(+1)
            if self.preview_mode == 'THREAD':
                self.ws.fetch_thread_overview(self.ws.threads[self.ws.cursor]['root_mid'])
        elif key == ord(' '):
            self.ws.toggle_selection()
        elif key in (ord('q'), ord('Q')):
            return self.ws.get_selected_mids()
        elif key == ord('?'):
            self.show_help_overlay = True
        elif key == ord('a'):
            self.ws.select_all()
        elif key == ord('/'):
            self.ws.start_search()
        elif key == ord('n'):
            self.ws.next_match()
        elif key == ord('p'):
            self.ws.prev_match()
        return None

    def _handle_input_preview(self, key: int) -> Optional[List[str]]:
        """Handle key input when focus is on the preview pane."""
        messages = self.ws.fetch_thread_overview(self.ws.threads[self.ws.cursor]['root_mid'])
        msg_count = len(messages) if messages else 0

        if self.preview_mode == 'THREAD':
            if key in (curses.KEY_UP, ord('k')):
                self.ws.move_thread_cursor(-1, msg_count)
            elif key in (curses.KEY_DOWN, ord('j')):
                self.ws.move_thread_cursor(+1, msg_count)
        elif self.preview_mode == 'MESSAGE':
            if key in (curses.KEY_UP, ord('k')):
                self.ws.scroll_message(-1)
            elif key in (curses.KEY_DOWN, ord('j')):
                self.ws.scroll_message(+1)

        if key == 9:  # Tab - return focus to thread list (split-pane only)
            self.focus = 'THREAD_LIST'
        elif key == 27:  # Escape
            if self.view_mode == 'FULLSCREEN':
                self.view_mode = 'SPLIT'
                self.focus = 'THREAD_LIST'
            else:
                self.focus = 'THREAD_LIST'
        elif key in (ord('q'), ord('Q')):
            return self.ws.get_selected_mids()
        return None

    def handle_input(self, key: int) -> Optional[List[str]]:
        """Handle key input. Returns list of selected blobs if quit, None otherwise."""
        if self.show_help_overlay:
            self.show_help_overlay = False
            return None
        if key == 16:  # Ctrl+P - Message preview toggle
            self._toggle_preview_mode('MESSAGE')
            self.ws.message_scroll_offset = 0
            return None
        if key == 20:  # Ctrl+T - Thread overview toggle
            self._toggle_preview_mode('THREAD')
            return None
        if key == 6:  # Ctrl+F - Full-screen toggle
            if self.view_mode == 'FULLSCREEN':
                self.view_mode = 'SPLIT'
                self.focus = 'THREAD_LIST'
            else:
                self.show_preview = True
                self.view_mode = 'FULLSCREEN'
                self.focus = 'PREVIEW'
            return None
        if key == 9:  # Tab - toggle focus between thread list and preview (split-pane only)
            if self.view_mode == 'SPLIT' and self.show_preview:
                self.focus = 'PREVIEW' if self.focus == 'THREAD_LIST' else 'THREAD_LIST'
            return None
        if self.ws.searching:
            self._handle_input_search(key)
            return None
        if self.view_mode == 'FULLSCREEN' or self.focus == 'PREVIEW':
            return self._handle_input_preview(key)
        return self._handle_input_thread_list(key)

    def run(self) -> List[str]:
        """Main entry point for the TUI."""
        if not self.ws.threads:
            return []

        def curses_main(stdscr):
            curses.curs_set(0)

            while True:
                self.render(stdscr)
                curses.doupdate()

                fetch_done = self.ws.consume_fetch_done()
                if fetch_done or self.ws.is_loading:
                    stdscr.timeout(200)
                else:
                    stdscr.timeout(-1)

                key = stdscr.getch()
                if key == curses.ERR:
                    continue

                result = self.handle_input(key)
                if result is not None:
                    return result

        return curses.wrapper(curses_main)


class ThreadProcessor:
    """Handles post-selection workflow: converting threads and saving to a directory."""

    def __init__(self, repo_path: Optional[str], threads_dir: str,
                 edition: int, existing_index: Optional[Dict[str, Any]] = None):
        self.repo_path = repo_path
        self.threads_dir = threads_dir
        self.edition = edition
        self.existing_index = existing_index

    def process_selected_threads(self, threads: List[Dict[str, Any]], selected_mids: List[str]) -> None:
        """Process selected threads: convert to text files and update index.md."""

        if not self.repo_path:
            print("Warning: Could not find a local repo path. Skipping thread conversion.")
            return

        print(f"Using repo: {self.repo_path}", file=sys.stderr)

        thread_by_mid = {t['root_mid']: t for t in threads}
        processed_threads = []

        for mid in selected_mids:
            t = thread_by_mid.get(mid)
            subject = t['subject'] if t else "unknown"
            blob = t['blob'][:8] if t else mid[:8]

            filename = f"{sanitize_filename(subject)}_{blob}.txt"
            output_path = os.path.join(self.threads_dir, filename)

            print(f"Fetching thread {mid}: {subject}", file=sys.stderr)
            try:
                messages = git_ml_converter.fetch_lei_thread(mid, self.repo_path)
                git_ml_converter.convert_content_to_text(messages, mid, output_path, is_mbox=True)
                if t:
                    processed_threads.append(t)
            except git_ml_converter.GitMLConverterError as e:
                print(f"Error: Failed to fetch thread {mid}: {e}", file=sys.stderr)
            except Exception as e:
                print(f"Error fetching thread {mid}: {e}", file=sys.stderr)

        if processed_threads:
            save_index(self.threads_dir, self.edition, processed_threads, self.existing_index)
            print(f"Index updated: {os.path.join(self.threads_dir, INDEX_FILENAME)}")

        print(f"\nThreads saved to: {self.threads_dir}/")


# --- Helper functions ---

def ask_user(prompt: str) -> str:
    """Prints a prompt to stderr and reads input from stdin."""
    print(prompt, end='', file=sys.stderr, flush=True)
    try:
        line = sys.stdin.readline()
        if not line:
            return ""
        return line.strip()
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)

def run_live_command(cmd: List[str], cwd: Optional[str] = None) -> bool:
    """Runs a command and streams output to stderr."""
    print(f"Running: {' '.join(cmd)}", file=sys.stderr)
    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd
        )
        for line in process.stdout or []:
            print(line, end='', file=sys.stderr)
        process.wait()
        return process.returncode == 0
    except FileNotFoundError:
        print(f"Error: Command not found: {cmd[0]}", file=sys.stderr)
        return False

def setup_new_mirror():
    """Interactively clones and sets up the mailing list mirror."""
    print(f"\nNo externals found. Clone Git mailing list from {GIT_ML_URL}?", file=sys.stderr)
    if ask_user("Clone now? [y/N]: ").lower() != 'y':
        return

    path_input = ask_user(f"Destination path [{DEFAULT_CLONE_PATH}]: ")
    target_path = os.path.expanduser(path_input) if path_input else DEFAULT_CLONE_PATH

    if not shutil.which("public-inbox-clone"):
        print("Error: 'public-inbox-clone' not found.", file=sys.stderr)
        return

    if not run_live_command(["public-inbox-clone", GIT_ML_URL, target_path]):
        return

    print("\nIndexing repository (using --split-shards)...", file=sys.stderr)
    run_live_command(["public-inbox-index", f"-j{os.cpu_count() or 1}", "--split-shards", target_path])
    print("\nRegistering with lei...", file=sys.stderr)
    run_live_command(["lei", "add-external", target_path])
    run_live_command(["lei", "up", target_path])
    print("\nSetup complete!", file=sys.stderr)

def update_mirrors(store: MailingListStore):
    """Interactively updates existing local mirrors."""
    repo_path = store.get_repo_path()
    if not repo_path:
        return

    print(f"\nFound local external: {repo_path}", file=sys.stderr)
    latest_date = store.get_latest_message_date(repo_path)
    print(f"  {repo_path} (latest: {latest_date or 'unknown'})", file=sys.stderr)

    if store.is_recent(latest_date):
        print("Repo is up-to-date (latest message less than 1 day old).", file=sys.stderr)
        return

    if ask_user("Fetch new emails and update index? [y/N]: ").lower() != 'y':
        return

    print(f"\n--- Updating {repo_path} ---", file=sys.stderr)
    if run_live_command(["public-inbox-fetch"], cwd=repo_path):
        run_live_command(["public-inbox-index", f"-j{os.cpu_count() or 1}", repo_path])
        run_live_command(["lei", "up", repo_path])

def check_and_manage_environment():
    """Checks environment and offers to setup/update mirrors."""
    if not shutil.which("lei"):
        print("Error: 'lei' command not found in PATH.", file=sys.stderr)
        print("Please install public-inbox (which includes lei).", file=sys.stderr)
        print("""For example:
            git clone https://git.kernel.org/pub/scm/public-inbox/public-inbox.git
            cd public-inbox
            perl Makefile.PL
            make install""", file=sys.stderr)
        print("Then ensure $HOME/perl5/bin is in your PATH.", file=sys.stderr)
        sys.exit(1)

    try:
        proc = subprocess.run(["lei", "ls-external"], capture_output=True, text=True)
        externals = proc.stdout.strip()
        if not externals:
            setup_new_mirror()
        else:
            store = MailingListStore()
            update_mirrors(store)
    except subprocess.CalledProcessError as e:
        print(f"Error: 'lei' command failed to run: {e.stderr}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Browse and export Git mailing list threads.')
    parser.add_argument('--edition', type=int, default=None,
                        help='Override the edition number (default: auto-detected from current date).')
    args = parser.parse_args()

    edition = args.edition if args.edition is not None else compute_edition(datetime.now())

    check_and_manage_environment()

    store = MailingListStore()
    try:
        messages = store.get_lei_results()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    if not messages:
        print("No messages found in the search window.")
        return

    summarizable_threads = store.analyze_threads(messages)
    summarizable_threads.sort(key=lambda x: x['age_days'])

    if not summarizable_threads:
        print("No threads match the criteria.")
        return

    threads_dir = find_or_create_threads_dir(edition)
    existing_index = load_index(threads_dir)

    repo_path = store.get_repo_path()
    tui = ThreadSelectorTUI(summarizable_threads, repo_path,
                            edition=edition, done_mids=existing_index['done_mids'])
    selected_mids = tui.run()

    if selected_mids:
        processor = ThreadProcessor(repo_path, threads_dir, edition, existing_index)
        processor.process_selected_threads(summarizable_threads, selected_mids)


if __name__ == "__main__":
    main()
