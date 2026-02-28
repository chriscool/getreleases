#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import json
import sys
import shutil
import os
from datetime import datetime, timedelta
from collections import defaultdict

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

def update_mirrors(externals):
    """Interactively updates existing local mirrors."""
    local_paths = [line.split()[0] for line in externals.splitlines() if line.strip().startswith('/')]

    if not local_paths: return
    print(f"\nFound {len(local_paths)} local external(s): {', '.join(local_paths)}", file=sys.stderr)

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

def main():
    check_and_manage_environment()

    messages = get_lei_results()

    if not messages:
        print("No messages found in the search window.")
        return

    summarizable_threads = analyze_threads(messages)

    summarizable_threads.sort(key=lambda x: x['age_days'])

    term_width = os.get_terminal_size().columns or 130
    fixed_width = 3 + 4 + 3 + 8 + 12
    subject_width = max(20, term_width - fixed_width)

    print(f"\nFound {len(summarizable_threads)} threads worth summarizing:\n")
    print(f"{'Age':<3} | {'Msgs':<4} | {'Ppl':<3} | {'Blob ID':<8} | {'Subject':<{subject_width}}")
    print("-" * term_width)

    for t in summarizable_threads:
        subject = t['subject'][:subject_width-3] + "..." if len(t['subject']) > subject_width else t['subject']
        print(f"{t['age_days']:<3} | {t['count']:<4} | {t['participants']:<3} | {t['blob']:<8} | {subject:<{subject_width}}")

if __name__ == "__main__":
    main()
