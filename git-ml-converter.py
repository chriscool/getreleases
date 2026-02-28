#!/usr/bin/env python3
"""
Convert Git mailing list archives to plain text format.
Preserves thread structure, code diffs, and important metadata.
"""

import re
import sys
import requests
import argparse
import time
import subprocess
import mailbox
import tempfile
import os
import json
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from bs4.element import Tag
from typing import Optional, List, Dict, Any

class GitMLConverterError(Exception):
    """Custom exception for git-ml-converter errors."""
    pass

# ==============================================================================
# DATA FETCHING FUNCTIONS
# ==============================================================================

def fetch_url_content(url: str) -> str:
    """Fetches and returns the HTML content, mimicking a wget request."""
    headers = {'User-Agent': 'Wget/1.21.3'}

    print(f"Fetching content from {url}...")
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        raise GitMLConverterError(f"Could not fetch URL: {e}")

def read_file_content(filepath: str) -> str:
    """Reads and returns the content from a local HTML file."""
    try:
        print(f"Reading content from {filepath}...")
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        raise GitMLConverterError(f"File not found at '{filepath}'")
    except UnicodeDecodeError:
        try:
            with open(filepath, 'r', encoding='latin-1') as f:
                return f.read()
        except Exception as e:
            raise GitMLConverterError(f"Could not read file: {e}")

def get_msgid_from_blob(blob_id: str, repo_path: Optional[str]) -> Optional[str]:
    """Uses git to read a blob and extract the Message-ID header."""
    print(f"Resolving Blob ID {blob_id} to Message-ID...", file=sys.stderr)
    try:
        cmd = ["git", "show", blob_id]

        # Handle Public-Inbox v2 structure (all.git)
        cwd = None
        if repo_path and os.path.isdir(repo_path):
            v2_all = os.path.join(repo_path, "all.git")
            if os.path.isdir(v2_all):
                # Use all.git which knows about all epochs (0.git, 1.git, etc.)
                cwd = v2_all
            else:
                cwd = repo_path

        # We assume 'git' is in PATH.
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=cwd, errors='replace')

        # Look for Message-ID in the first few lines
        for line in result.stdout.splitlines():
            if line.lower().startswith("message-id:"):
                # Extract clean ID
                return line.split(":", 1)[1].strip().strip("<>")

        print("Warning: No Message-ID header found in blob.", file=sys.stderr)
        return None
    except subprocess.CalledProcessError:
        print(f"Error: Could not read blob {blob_id} from git. Is --repo set correctly?", file=sys.stderr)
        return None

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

        # Handle cases where lei might return a list for the field
        if isinstance(latest_dt_str, list):
            return latest_dt_str[0]

        return latest_dt_str

    except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError, KeyError) as e:
        # Silently catch fetch/parse errors and return None
        return None

def check_repo_up_to_date(repo_path: str) -> None:
    """Checks if the given lei repository/remote has recent emails."""
    print(f"Checking if repository '{repo_path}' is up-to-date...", file=sys.stderr)

    latest_dt_str = get_latest_message_date(repo_path)

    if not latest_dt_str:
        print("\n[!] WARNING: No recent messages found in the last month.", file=sys.stderr)
        print("    The repository might be severely outdated or unreachable.\n", file=sys.stderr)
        return

    try:
        # Parse the ISO 8601 date string
        latest_dt = datetime.strptime(latest_dt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - latest_dt

        # Warn if the database is more than 3 days behind
        if diff.days > 3:
            print(f"\n[!] WARNING: The repository seems outdated.", file=sys.stderr)
            print(f"    Latest message date: {latest_dt_str} ({diff.days} days behind).", file=sys.stderr)

            is_http = repo_path.startswith("http")

            if not is_http:
                updated = False
                # Check if running in an interactive terminal
                if sys.stdin.isatty() and sys.stdout.isatty():
                    ans = input("Should I try to update the repo [Y/n]? ").strip().lower()
                    if ans in ['', 'y', 'yes']:
                        try:
                            print(f"\nUpdating local repository at {repo_path}...", file=sys.stderr)
                            subprocess.run(["public-inbox-fetch"], cwd=repo_path, check=True)
                            subprocess.run(["public-inbox-index", "."], cwd=repo_path, check=True)
                            subprocess.run(["lei", "up", "--all"], check=True)

                            # --- Use the abstracted function to verify the new date ---
                            new_dt_str = get_latest_message_date(repo_path)
                            if new_dt_str:
                                print(f"Repository successfully updated! (New latest message: {new_dt_str})\n", file=sys.stderr)
                            else:
                                print("Repository successfully updated! (Could not verify new date)\n", file=sys.stderr)

                            updated = True
                        except subprocess.CalledProcessError as e:
                            print(f"\n[!] Error during update: {e}", file=sys.stderr)

                if not updated:
                    print("\n    To manually update the repository, please run:\n"
                          f"      cd {repo_path}\n"
                          "      public-inbox-fetch\n"
                          "      public-inbox-index .\n"
                          "      lei up --all\n", file=sys.stderr)
        else:
            print(f"Repository is up-to-date (Latest message: {latest_dt_str}).\n", file=sys.stderr)

    except Exception as e:
        print(f"Warning: Could not parse repo date. {e}", file=sys.stderr)

def fetch_lei_thread(input_id: str, repo_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetches a thread using lei and parses the mbox output."""

    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_mbox:
        temp_path = temp_mbox.name

    try:
        clean_str = input_id.strip()
        final_query = ""

        # DETECT ID TYPE:
        # 1. Git Blob ID (40 chars hex)
        if re.match(r'^[a-f0-9]{40}$', clean_str, re.IGNORECASE):
            msg_id = get_msgid_from_blob(clean_str, repo_path)
            if not msg_id:
                raise GitMLConverterError("Failed to resolve Blob ID to Message-ID.")
            # Quote the ID to handle special chars like '+' safely
            final_query = f'm:"<{msg_id}>"'
        else:
            # 2. Message-ID
            if clean_str.lower().startswith("m:"):
                clean_str = clean_str[2:]

            raw_id = clean_str.strip('<> ')
            # Quote the ID to handle special chars like '+' safely
            final_query = f'm:"<{raw_id}>"'

        print(f"Fetching thread via query: {final_query}")

        # 2. Build command
        cmd = ["lei", "q", "-t", "-o", f"mboxrd:{temp_path}"]

        # If a repo path is provided, use --only to search it directly
        # This bypasses the need for 'lei add-external' or 'lei up'
        if repo_path:
            if repo_path.startswith('http') or os.path.exists(repo_path):
                print(f"Using direct repository: {repo_path}")
                check_repo_up_to_date(repo_path)
                cmd.extend(["--only", repo_path])
            else:
                print(f"Warning: Repo path '{repo_path}' does not exist.", file=sys.stderr)

        cmd.append(final_query)

        # 3. Capture output
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        return parse_mbox_content(temp_path)
    except subprocess.CalledProcessError as e:
        error_msg = f"lei command failed (Exit Code {e.returncode}): {e.stderr}"
        if not repo_path:
            error_msg += ". Tip: Try providing the path to your git archive with --repo"
        raise GitMLConverterError(error_msg)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# ==============================================================================
# PARSING AND FORMATTING FUNCTIONS 
# ==============================================================================
            
def extract_message_id(soup: BeautifulSoup, elem: Tag) -> Optional[str]:
    """Extract message ID from anchor tags."""
    # Look for id attributes that start with 'm'
    if elem.get('id', '').startswith('m'):
        return elem['id'][1:]  # Remove the 'm' prefix
    return None

def extract_code_diff(pre_elem: Tag) -> str:
    """Extract and format code diffs from pre elements."""
    lines = []
    for elem in pre_elem.descendants:
        if isinstance(elem, str):
            lines.append(elem)
        elif elem.name == 'span':
            # Preserve diff markers
            if 'del' in elem.get('class', []):
                lines.append(f"-{elem.get_text()}")
            elif 'add' in elem.get('class', []):
                lines.append(f"+{elem.get_text()}")
            else:
                lines.append(elem.get_text())
    
    return ''.join(lines)

def parse_git_ml_html(html_content: str) -> List[Dict[str, Any]]:
    """Parse Git mailing list HTML and extract messages."""
    soup = BeautifulSoup(html_content, 'html.parser')
    messages = []
    
    # Find all message blocks (they're typically in <pre> tags)
    pre_blocks = soup.find_all('pre')
    
    for pre in pre_blocks:
        # Skip navigation/header blocks
        if pre.find('form') or 'help' in pre.get_text()[:100]:
            continue
            
        # Look for message indicators
        text = pre.get_text()
        if '@' not in text or 'From:' not in text:
            continue
            
        message = {
            'id': None,
            'subject': None,
            'from': None,
            'date': None,
            'body': [],
            'is_reply': False,
            'parent_id': None
        }
        
        # Extract message ID
        for elem in pre.find_all(['a', 'u']):
            msg_id = extract_message_id(soup, elem)
            if msg_id:
                message['id'] = msg_id
                break
        
        # Parse message headers and body
        lines = text.split('\n')
        in_headers = True
        
        for line in lines:
            line = line.strip()
            
            if in_headers:
                if line.startswith('From:'):
                    # Extract sender and date
                    from_match = re.search(r'From: (.+) @ (\d{4}-\d{2}-\d{2})', line)
                    if from_match:
                        message['from'] = from_match.group(1).strip()
                        message['date'] = from_match.group(2)
                elif line.startswith('Subject:') or line.startswith('[PATCH]'):
                    message['subject'] = line.replace('Subject:', '').strip()
                elif line.startswith('Re:'):
                    message['is_reply'] = True
                    message['subject'] = line.strip()
                elif line == '' and message['from']:
                    in_headers = False
            else:
                message['body'].append(line)
        
        # Check for code diffs
        if pre.find('span', class_=['del', 'add', 'hunk', 'head']):
            # This is a diff block
            diff_content = extract_code_diff(pre)
            message['body'] = [diff_content]
        
        if message['from'] and (message['subject'] or message['body']):
            messages.append(message)
    
    return messages

def parse_mbox_content(mbox_path: str) -> List[Dict[str, Any]]:
    """Parses a local mbox file into the message dictionary format."""
    messages = []
    mbox = mailbox.mbox(mbox_path)

    for msg in mbox:
        # Extract plain text payload
        body_text = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text += payload.decode('utf-8', errors='replace')
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body_text = payload.decode('utf-8', errors='replace')

        # Clean up keys for our format
        msg_dict = {
            'id': msg.get('Message-ID', '').strip('<> '),
            'subject': msg.get('Subject', '').replace('Subject:', '').strip(),
            'from': msg.get('From', '').strip(),
            'date': msg.get('Date', '').strip(),
            'body': body_text.splitlines(),
            'is_reply': msg.get('Subject', '').lower().strip().startswith('re:') or bool(msg.get('In-Reply-To'))
        }

        # Filter out empty messages often found in mbox
        if msg_dict['from']:
            messages.append(msg_dict)

    # Sort by date helps reconstruct flow, though threading logic handles grouping
    # messages.sort(key=lambda x: x['date'])
    return messages

def format_message(message: Dict[str, Any], level: int = 0) -> str:
    """Format a message for plain text output."""
    indent = "  " * level
    output = []
    
    if level == 0:
        output.append("=" * 80)
    else:
        output.append(f"{indent}---")
    
    if message['subject']:
        output.append(f"{indent}Subject: {message['subject']}")
    if message['from']:
        output.append(f"{indent}From: {message['from']}")
    if message['date']:
        output.append(f"{indent}Date: {message['date']}")
    if message['id']:
        output.append(f"{indent}Message-ID: {message['id']}")
    
    output.append("")  # Empty line
    
    # Format body
    body_text = '\n'.join(message['body']).strip()
    if body_text:
        # Indent body for replies
        if level > 0:
            body_lines = body_text.split('\n')
            body_text = '\n'.join(f"{indent}{line}" for line in body_lines)
        output.append(body_text)
    
    output.append("")  # Empty line after message
    
    return '\n'.join(output)

# ==============================================================================
# MAIN CONVERSION AND EXECUTION LOGIC
# ==============================================================================

def convert_content_to_text(content: Any, source_name: str, output_file: Optional[str] = None, is_mbox: bool = False) -> None:
    """Takes content (HTML string or list of dicts) and converts it to formatted plain text."""
    if is_mbox:
        messages = content # Content is already parsed list of dicts
    else:
        messages = parse_git_ml_html(content)

    # Build thread structure
    output_lines = []
    output_lines.append(f"Git Mailing List Archive - Converted from {source_name}")
    output_lines.append(f"Extracted {len(messages)} messages")
    output_lines.append("=" * 80)
    output_lines.append("")

    # Group messages by thread
    threads = []
    current_thread = []

    for msg in messages:
        if not msg['is_reply'] and current_thread:
            threads.append(current_thread)
            current_thread = [msg]
        else:
            current_thread.append(msg)

    if current_thread:
        threads.append(current_thread)

    # Format threads
    for thread in threads:
        for i, msg in enumerate(thread):
            level = 1 if i > 0 and msg['is_reply'] else 0
            output_lines.append(format_message(msg, level))

    output_text = '\n'.join(output_lines)

    # Write output
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(output_text)
        print(f"Successfully converted {source_name} -> {output_file}")
    else:
        print(output_text)

def main() -> None:
    """Parses command-line arguments and runs the conversion."""
    parser = argparse.ArgumentParser(
        description='Convert Git mailing list HTML to plain text from a URL or local file.'
    )
    parser.add_argument('source', help='Input URL or local HTML file path')
    parser.add_argument('-o', '--output', help='Output text file (default: stdout)')
    parser.add_argument('--repo', help='Path to local public-inbox git archive (for lei)', default=None)
    
    args = parser.parse_args()

    content = None
    is_mbox = False

    # Check if the source is a URL or a file path
    if args.source.startswith('http://') or args.source.startswith('https://'):
        content = fetch_url_content(args.source)
    elif os.path.exists(args.source) and (args.source.endswith('.html') or args.source.endswith('.htm')):
        content = read_file_content(args.source)
    else:
        # Assume it's a Message-ID for lei
        content = fetch_lei_thread(args.source, args.repo)
        is_mbox = True
    
    # Once content is fetched/read, convert it
    if content:
        convert_content_to_text(content, args.source, args.output, is_mbox)
    else:
        raise GitMLConverterError(f"No content found for source '{args.source}'. Check if the ID is correct.")

if __name__ == '__main__':
    try:
        main()
    except GitMLConverterError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
