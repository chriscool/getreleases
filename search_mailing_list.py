#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
# Thread size constraints
MIN_MSG_COUNT = 5
MAX_MSG_COUNT = 40

# Age constraints for the *last* message in the thread
# The thread must have gone silent between 3 weeks and 3 months ago.
MIN_AGE_DAYS = 21   # 3 weeks
MAX_AGE_DAYS = 90   # ~3 months

# Defaults for setup
GIT_ML_URL = "https://lore.kernel.org/git/"
DEFAULT_CLONE_PATH = os.path.expanduser("~/git/git-mailing-list-public-inbox")

def ask_user(prompt):
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

def run_live_command(cmd, cwd=None):
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
    if ask_user("Clone now? [y/N]: ").lower() != 'y': return

    path_input = ask_user(f"Destination path [{DEFAULT_CLONE_PATH}]: ")
    target_path = os.path.expanduser(path_input) if path_input else DEFAULT_CLONE_PATH

    if not shutil.which("public-inbox-clone"):
        print("Error: 'public-inbox-clone' not found.", file=sys.stderr)
        return

    if not run_live_command(["public-inbox-clone", GIT_ML_URL, target_path]): return

    print("\nIndexing repository (using --split-shards)...", file=sys.stderr)
    run_live_command(["public-inbox-index", f"-j{os.cpu_count() or 1}", "--split-shards", target_path])
    print("\nRegistering with lei...", file=sys.stderr)
    run_live_command(["lei", "add-external", target_path])
    run_live_command(["lei", "up", target_path])
    print("\nSetup complete!", file=sys.stderr)

def get_latest_message_date(repo_path: str) -> Optional[str]:
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

def get_repo_path() -> Optional[str]:
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

def sanitize_filename(name: str) -> str:
    """Sanitize a string to be used as a filename."""
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[-\s]+', '-', name)
    return name.strip('-')[:50]

def is_recent(date_str: str) -> bool:
    """Check if the given date string is less than 1 day old."""
    if not date_str:
        return False
    try:
        dt = parse_date(date_str)
        if dt:
            return (datetime.now() - dt).days < 1
    except (ValueError, TypeError):
        pass
    return False

def update_mirrors(externals):
    """Interactively updates existing local mirrors."""
    local_paths = [line.split()[0] for line in externals.splitlines() if line.strip().startswith('/')]

    if not local_paths: return

    latest_dates = {}
    print(f"\nFound {len(local_paths)} local external(s):", file=sys.stderr)
    for path in local_paths:
        if os.path.isdir(path):
            latest_date = get_latest_message_date(path)
            latest_dates[path] = latest_date
            print(f"  {path} (latest: {latest_date or 'unknown'})", file=sys.stderr)

    all_recent = all(is_recent(date) for date in latest_dates.values() if date)
    if all_recent:
        print("All repos are up-to-date (latest message less than 1 day old).", file=sys.stderr)
        return

    if ask_user("Fetch new emails and update index? [y/N]: ").lower() != 'y': return

    for path in local_paths:
        if not os.path.isdir(path): continue
        print(f"\n--- Updating {path} ---", file=sys.stderr)
        # 1. Fetch new git objects
        if run_live_command(["public-inbox-fetch"], cwd=path):
            # 2. Update Xapian index (incremental)
            run_live_command(["public-inbox-index", f"-j{os.cpu_count() or 1}", path])
            # 3. Refresh lei
            run_live_command(["lei", "up", path])

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
            update_mirrors(externals)
    except subprocess.CalledProcessError as e:
        print(f"Error: 'lei' command failed to run: {e.stderr}", file=sys.stderr)
        sys.exit(1)

def get_lei_results():
    """Executes lei q to find candidate messages."""
    date_query = f"d:{MAX_AGE_DAYS}.days.ago..{MIN_AGE_DAYS}.days.ago"

    cmd = [
        "lei", "q",
        "-t",           # Get the full thread context
        "-f", "json",   # Output in JSON format
        date_query      # The time window
    ]

    print(f"\nRunning search: {' '.join(cmd)}", file=sys.stderr)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            return []
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running lei: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON output from lei: {e}", file=sys.stderr)
        sys.exit(1)

def parse_date(date_str):
    """
    Parses the 'dt' field from lei JSON.
    Format is typically: YYYY-MM-DD HH:MM:SS +TZ
    """
    try:
        # Handle ISO 8601 format (e.g., 2025-09-02T06:36:08Z) by replacing T with space
        return datetime.strptime(date_str[:19].replace('T', ' '), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None

def analyze_threads(messages):
    """
    Groups messages by thread_id and filters based on user criteria.
    """
    threads = defaultdict(list)
    print(f"Loaded {len(messages)} raw messages from lei output.", file=sys.stderr)

    # 1. Group by Root Message ID (derived from refs)
    for msg in messages:
        if not msg:
            continue

        # Lei JSON uses 'refs' (References) to track threading.
        # We derive the thread_id from the first ref (the root) or the message ID itself.
        refs = msg.get('refs', [])
        if refs:
            t_id = refs[0]
        else:
            t_id = msg.get('m') # 'm' is the Message-ID

        if t_id:
            threads[t_id].append(msg)

    valid_threads = []
    now = datetime.now()

    print(f"Identified {len(threads)} unique threads. Filtering...", file=sys.stderr)
    dropped_counts = {"count": 0, "age": 0}

    # 2. Analyze each thread
    for t_id, msgs in threads.items():
        count = len(msgs)

        # CRITERIA 1: Message Count
        if not (MIN_MSG_COUNT <= count <= MAX_MSG_COUNT):
            dropped_counts["count"] += 1
            continue

        # Extract dates for all messages in the thread
        dates = []
        for m in msgs:
            d = parse_date(m.get('dt'))
            if d:
                dates.append(d)

        if not dates:
            continue

        # Determine the last email date in the thread
        last_email_date = max(dates)
        age = (now - last_email_date).days

        # Calculate unique participants
        participants = set()
        for m in msgs:
            # 'f' is a list of [name, email] pairs. We use email [1] for uniqueness.
            for sender in m.get('f', []):
                if len(sender) > 1:
                    participants.add(sender[1])

        # CRITERIA 2: Age of last email
        if MIN_AGE_DAYS <= age <= MAX_AGE_DAYS:

            # Find the subject (usually from the first message, or the one with the earliest date)
            msgs.sort(key=lambda x: x.get('dt', ''))
            root_subject = msgs[0].get('s', '(No Subject)')

            valid_threads.append({
                'thread_id': t_id,
                'subject': root_subject,
                'count': count,
                'last_activity': last_email_date.strftime("%Y-%m-%d"),
                'participants': len(participants),
                'age_days': age,
                'blob': msgs[0].get('blob', '')[:8],
                'root_mid': msgs[0].get('m', '')
            })
        else:
            dropped_counts["age"] += 1

    print(f"Filtered out: {dropped_counts['count']} by size, {dropped_counts['age']} by date.", file=sys.stderr)
    return valid_threads

def show_help(stdscr):
    """Display help screen."""
    h, w = stdscr.getmaxyx()

    while True:
        stdscr.clear()

        lines = [
            "Help - Key Bindings",
            "=" * 30,
            "",
            "Navigation:",
            "  k / Up      - Move cursor up",
            "  j / Down    - Move cursor down",
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
            "  ?           - Show this help",
            "  Q           - Quit and return selected threads",
            "",
            "Press any key to return...",
        ]

        start_y = max(0, (h - len(lines)) // 2)
        for i, line in enumerate(lines):
            stdscr.addstr(start_y + i, max(0, (w - len(line)) // 2), line)

        key = stdscr.getch()
        break

    stdscr.clear()

def select_threads_curses(threads):
    """Interactive thread selection using curses."""
    def curses_main(stdscr):
        curses.curs_set(0)
        stdscr.clear()

        selected = [False] * len(threads)
        cursor = 0
        offset = 0

        search_term = ""
        search_matches = []
        current_match_idx = -1
        searching = False

        def find_matches(term):
            if not term:
                return []
            term_lower = term.lower()
            return [i for i, t in enumerate(threads) if term_lower in t['subject'].lower()]

        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()

            fixed_width = 3 + 4 + 3 + 8 + 12 + 3
            subject_width = max(20, w - fixed_width - 1)

            if searching:
                title = f"Search: {search_term} (Enter: done, Esc: cancel)"
            else:
                title = f"Select threads (/ search, n next, p prev, ? help, Space toggle, Q quit)"
            stdscr.addstr(0, 0, title[:w-1])
            stdscr.addstr(1, 0, f"{'Age':<3} | {'Msgs':<4} | {'Ppl':<3} | {'Blob ID':<8} | {'Subject':<{subject_width}}")
            stdscr.addstr(2, 0, "-" * min(w - 1, fixed_width + subject_width))

            visible_rows = h - 4
            if offset > cursor:
                offset = cursor
            elif cursor >= offset + visible_rows:
                offset = cursor - visible_rows + 1

            for i in range(offset, min(len(threads), offset + visible_rows)):
                row = i - offset + 3
                if row >= h:
                    break

                t = threads[i]
                marker = "[X]" if selected[i] else "[ ]"
                subject = t['subject'][:subject_width-3] + "..." if len(t['subject']) > subject_width else t['subject']
                line = f"{t['age_days']:<3} | {t['count']:<4} | {t['participants']:<3} | {t['blob']:<8} | {marker} {subject:<{subject_width}}"

                if i == cursor:
                    stdscr.addstr(row, 0, line[:w-1], curses.A_REVERSE)
                else:
                    stdscr.addstr(row, 0, line[:w-1])

            if search_matches:
                stdscr.addstr(h-2, 0, f"Match: {current_match_idx + 1}/{len(search_matches)}"[:w-1])
            stdscr.addstr(h-1, 0, f"Selected: {sum(selected)}/{len(threads)}"[:w-1])

            key = stdscr.getch()

            if searching:
                if key in (curses.KEY_ENTER, 10, 13):
                    searching = False
                    if search_matches:
                        cursor = search_matches[current_match_idx]
                elif key == 27:  # Escape
                    searching = False
                    search_term = ""
                    search_matches = []
                    current_match_idx = -1
                elif key in (curses.KEY_BACKSPACE, 127):
                    search_term = search_term[:-1]
                    search_matches = find_matches(search_term)
                    current_match_idx = 0 if search_matches else -1
                elif 32 <= key <= 126:
                    search_term += chr(key)
                    search_matches = find_matches(search_term)
                    current_match_idx = 0 if search_matches else -1
            else:
                if key in (curses.KEY_UP, ord('k')):
                    cursor = max(0, cursor - 1)
                elif key in (curses.KEY_DOWN, ord('j')):
                    cursor = min(len(threads) - 1, cursor + 1)
                elif key == ord(' '):
                    selected[cursor] = not selected[cursor]
                elif key in (ord('q'), ord('Q')):
                    return []
                elif key == ord('?'):
                    show_help(stdscr)
                elif key == ord('a'):
                    all_selected = all(selected)
                    selected = [not all_selected] * len(threads)
                elif key == ord('/'):
                    searching = True
                    search_term = ""
                    search_matches = []
                    current_match_idx = -1
                elif key == ord('n'):
                    if search_matches:
                        current_match_idx = (current_match_idx + 1) % len(search_matches)
                        cursor = search_matches[current_match_idx]
                elif key == ord('p'):
                    if search_matches:
                        current_match_idx = (current_match_idx - 1) % len(search_matches)
                        cursor = search_matches[current_match_idx]

        return [threads[i]['blob'] for i in range(len(threads)) if selected[i]]

    return curses.wrapper(curses_main)

def main():
    check_and_manage_environment()

    messages = get_lei_results()

    if not messages:
        print("No messages found in the search window.")
        return

    summarizable_threads = analyze_threads(messages)

    summarizable_threads.sort(key=lambda x: x['age_days'])

    if not summarizable_threads:
        print("No threads match the criteria.")
        return

    selected_blobs = select_threads_curses(summarizable_threads)

    if selected_blobs:
        threads_dir = datetime.now().strftime("threads_%Y_%m_%d")
        os.makedirs(threads_dir, exist_ok=True)

        output_file = os.path.join(threads_dir, "selected_threads.txt")
        selected_set = set(selected_blobs)
        with open(output_file, 'w') as f:
            for t in summarizable_threads:
                if t['blob'] in selected_set:
                    f.write(f"{t['blob']} | {t['subject']}\n")

        print(f"\nSelected blob IDs saved to: {output_file}")

        repo_path = get_repo_path()
        if not repo_path:
            print("Warning: Could not find a local repo path. Skipping thread conversion.")
            return

        print(f"Using repo: {repo_path}", file=sys.stderr)

        for blob_id in selected_blobs:
            for t in summarizable_threads:
                if t['blob'] == blob_id:
                    subject = t['subject']
                    break
            else:
                subject = "unknown"

            filename = f"{sanitize_filename(subject)}_{blob_id}.txt"
            output_path = os.path.join(threads_dir, filename)

            print(f"Fetching thread {blob_id}: {subject}", file=sys.stderr)
            try:
                messages = git_ml_converter.fetch_lei_thread(blob_id, repo_path)
                git_ml_converter.convert_content_to_text(messages, blob_id, output_path, is_mbox=True)
            except git_ml_converter.GitMLConverterError as e:
                print(f"Error: Failed to fetch thread {blob_id}: {e}", file=sys.stderr)
            except Exception as e:
                print(f"Error fetching thread {blob_id}: {e}", file=sys.stderr)

        print(f"\nThreads saved to: {threads_dir}/")

if __name__ == "__main__":
    main()
