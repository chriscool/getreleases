#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import subprocess
import json
import sys
import shutil
import os
import re
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, List, Dict, Any

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


class ThreadSelectorTUI:
    """Manages the curses-based thread selection interface."""

    def __init__(self, threads: List[Dict[str, Any]], repo_path: Optional[str] = None,
                 edition: Optional[int] = None, done_mids: Optional[set] = None):
        self.threads = threads
        self.selected = [False] * len(threads)
        self.cursor = 0
        self.offset = 0
        self.search_term = ""
        self.search_matches = []
        self.current_match_idx = -1
        self.searching = False
        self.show_help_overlay = False
        self.show_preview = True
        self.repo_path = repo_path
        self.edition = edition
        self.done_mids = done_mids or set()
        self._preview_cache = {}

    def _sanitize_for_curses(self, text: str) -> str:
        """Remove non-printable characters for curses display."""
        return ''.join(c if (32 <= ord(c) < 127 or c in '\n\r\t') else '?' for c in text)

    def fetch_email_body(self, blob_id: str, max_lines: int = 20) -> List[str]:
        """Fetch the email body using git show.

        Args:
            blob_id: The blob ID to fetch
            max_lines: Maximum number of body lines to return
        """
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

    def find_matches(self, term: str) -> List[int]:
        """Find thread indices matching the search term."""
        if not term:
            return []
        term_lower = term.lower()
        return [i for i, t in enumerate(self.threads) if term_lower in t['subject'].lower()]

    def show_help(self, stdscr):
        """Display help screen overlay."""
        h, w = stdscr.getmaxyx()

        stdscr.clear()

        lines = [
            "Help - Key Bindings",
            "=" * 30,
            "",
            "Navigation:",
            "  k / Up      - Move cursor up",
            "  j / Down    - Move cursor down",
            "",
            "Markers:",
            "  [ ]         - Not selected",
            "  [X]         - Selected for processing",
            "  [D]         - Already processed in a previous run",
            "",
            "Selection:",
            "  Space       - Toggle selection of current thread",
            "  a           - Toggle select all / deselect all",
            "",
            "Search:",
            "  /           - Start searching",
            "  n           - Go to next match",
            "  p           - Go to previous match",
            "  Enter       - Confirm search and jump to match",
            "  Escape      - Cancel search",
            "",
            "Other:",
            "  Ctrl+P      - Toggle preview window",
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

        stdscr.getch()
        self.show_help_overlay = False

    def render(self, stdscr):
        """Render the TUI."""
        if self.show_help_overlay:
            self.show_help(stdscr)
            return
        
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        if self.show_preview and w >= 105:
            list_width = 75
            if w < list_width + 30:
                list_width = max(50, w - 35)
            preview_width = w - list_width - 1
            show_preview = True
        else:
            list_width = w - 1
            preview_width = 0
            show_preview = False

        fixed_width = 3 + 4 + 3 + 12 + 2
        subject_width = max(20, list_width - fixed_width - 1)

        edition_prefix = f"Edition {self.edition} | " if self.edition is not None else ""
        if self.searching:
            title = f"{edition_prefix}Search: {self.search_term} (Enter: done, Esc: cancel, n/p next/prev, Ctrl+P preview)"
        else:
            title = f"{edition_prefix}Select threads (? help, / search, Ctrl+P preview, Space toggle, Q quit)"
        stdscr.addstr(0, 0, title[:list_width-1])
        if show_preview:
            stdscr.addstr(0, list_width, "│")
            stdscr.addstr(0, list_width + 1, "Preview")

        header = f"{'Age':<3} | {'Msgs':<4} | {'Ppl':<3} | {'Subject':<{subject_width}}"
        stdscr.addstr(1, 0, header[:list_width-1])
        if show_preview:
            stdscr.addstr(1, list_width, "│")
        stdscr.addstr(2, 0, "-" * min(list_width - 1, w - 1))
        if show_preview and list_width < w:
            stdscr.addstr(2, list_width, "├" + "─" * (preview_width - 1))

        visible_rows = h - 4
        if self.offset > self.cursor:
            self.offset = self.cursor
        elif self.cursor >= self.offset + visible_rows:
            self.offset = self.cursor - visible_rows + 1

        for i in range(self.offset, min(len(self.threads), self.offset + visible_rows)):
            row = i - self.offset + 3
            if row >= h:
                break

            t = self.threads[i]
            if t['root_mid'] in self.done_mids:
                marker = "[D]"
            elif self.selected[i]:
                marker = "[X]"
            else:
                marker = "[ ]"
            subject = t['subject'][:subject_width-3] + "..." if len(t['subject']) > subject_width else t['subject']
            line = f"{t['age_days']:<3} | {t['count']:<4} | {t['participants']:<3} | {marker} {subject:<{subject_width}}"

            if i == self.cursor:
                stdscr.addstr(row, 0, line[:list_width-1], curses.A_REVERSE)
            else:
                stdscr.addstr(row, 0, line[:list_width-1])

        current_thread = self.threads[self.cursor] if self.threads else None
        if show_preview and current_thread and preview_width > 10:
            preview_lines = [
                f"Subject: {current_thread['subject'][:preview_width-2]}",
                f"Messages: {current_thread['count']}",
                f"Participants: {current_thread['participants']}",
                f"Last activity: {current_thread['last_activity']}",
                f"Thread ID: {current_thread['thread_id'][:preview_width-2]}",
                "",
            ]

            body_lines = self.fetch_email_body(current_thread['blob'], max(h - 14, 5))
            for line in body_lines:
                if len(preview_lines) >= h - 3:
                    break
                preview_lines.append(self._sanitize_for_curses(line[:preview_width-1]))

            for i, line in enumerate(preview_lines):
                try:
                    if 3 + i < h:
                        stdscr.addstr(3 + i, list_width + 1, line[:preview_width - 1])
                except curses.error:
                    pass

        if self.search_matches:
            stdscr.addstr(h-2, 0, f"Match: {self.current_match_idx + 1}/{len(self.search_matches)}"[:list_width-1])
        stdscr.addstr(h-1, 0, f"Selected: {sum(self.selected)}/{len(self.threads)}"[:list_width-1])

    def handle_input(self, key: int) -> Optional[List[str]]:
        """Handle key input. Returns list of selected blobs if quit, None otherwise."""
        if key == 16:  # Ctrl+P
            self.show_preview = not self.show_preview
            return None
        if self.searching:
            if key in (curses.KEY_ENTER, 10, 13):
                self.searching = False
                if self.search_matches:
                    self.cursor = self.search_matches[self.current_match_idx]
            elif key == 27:  # Escape
                self.searching = False
                self.search_term = ""
                self.search_matches = []
                self.current_match_idx = -1
            elif key in (curses.KEY_BACKSPACE, 127):
                self.search_term = self.search_term[:-1]
                self.search_matches = self.find_matches(self.search_term)
                self.current_match_idx = 0 if self.search_matches else -1
            elif 32 <= key <= 126:
                self.search_term += chr(key)
                self.search_matches = self.find_matches(self.search_term)
                self.current_match_idx = 0 if self.search_matches else -1
            return None
        else:
            if key in (curses.KEY_UP, ord('k')):
                self.cursor = max(0, self.cursor - 1)
            elif key in (curses.KEY_DOWN, ord('j')):
                self.cursor = min(len(self.threads) - 1, self.cursor + 1)
            elif key == ord(' '):
                self.selected[self.cursor] = not self.selected[self.cursor]
            elif key in (ord('q'), ord('Q')):
                return [self.threads[i]['root_mid'] for i in range(len(self.threads)) if self.selected[i]]
            elif key == ord('?'):
                self.show_help_overlay = True
            elif key == ord('a'):
                all_selected = all(self.selected)
                self.selected = [not all_selected] * len(self.threads)
            elif key == ord('/'):
                self.searching = True
                self.search_term = ""
                self.search_matches = []
                self.current_match_idx = -1
            elif key == ord('n'):
                if self.search_matches:
                    self.current_match_idx = (self.current_match_idx + 1) % len(self.search_matches)
                    self.cursor = self.search_matches[self.current_match_idx]
            elif key == ord('p'):
                if self.search_matches:
                    self.current_match_idx = (self.current_match_idx - 1) % len(self.search_matches)
                    self.cursor = self.search_matches[self.current_match_idx]
            return None

    def run(self) -> List[str]:
        """Main entry point for the TUI."""
        if not self.threads:
            return []

        def curses_main(stdscr):
            curses.curs_set(0)

            while True:
                self.render(stdscr)
                key = stdscr.getch()

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

    repo_path = store.get_repo_path()
    tui = ThreadSelectorTUI(summarizable_threads, repo_path)
    selected_mids = tui.run()

    if selected_mids:
        threads_dir = find_or_create_threads_dir(edition)
        processor = ThreadProcessor(repo_path, threads_dir, edition)
        processor.process_selected_threads(summarizable_threads, selected_mids)


if __name__ == "__main__":
    main()
