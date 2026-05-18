#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""JWZ-style thread ordering helpers shared across getreleases scripts."""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional


def normalize_msgid(value: str) -> str:
    """Normalize a Message-ID for in-thread matching.

    Strips angle brackets and whitespace, and lowercases the host part
    after the first '@' so References/In-Reply-To matches survive minor
    casing inconsistencies in the domain.
    """
    if not value:
        return ''
    s = value.strip().strip('<>').strip()
    if '@' in s:
        local, host = s.split('@', 1)
        s = local + '@' + host.lower()
    return s


def thread_sort(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-order messages into JWZ-style tree-traversal order.

    Each message is annotated in place with `_depth` (int) reflecting its
    position in the reply tree. Parents come before children; siblings
    are sorted by Date header ascending. Cycles in malformed input are
    broken via a visited set.
    """
    from email.utils import parsedate_tz, mktime_tz

    if not messages:
        return []

    by_id: Dict[str, Dict[str, Any]] = {}
    for m in messages:
        mid = normalize_msgid(m.get('id', ''))
        if mid and mid not in by_id:
            by_id[mid] = m

    children: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    roots: List[Dict[str, Any]] = []

    for m in messages:
        mid = normalize_msgid(m.get('id', ''))
        irt = normalize_msgid(m.get('in_reply_to', ''))
        parent = irt if irt and irt in by_id and irt != mid else ''
        if not parent:
            for ref in reversed((m.get('references', '') or '').split()):
                cand = normalize_msgid(ref)
                if cand and cand in by_id and cand != mid:
                    parent = cand
                    break
        if parent:
            children[parent].append(m)
        else:
            roots.append(m)

    def _ts(m: Dict[str, Any]) -> float:
        try:
            t = parsedate_tz(m.get('date', '') or '')
            if t:
                return float(mktime_tz(t))
        except Exception:
            pass
        return float('inf')

    for kids in children.values():
        kids.sort(key=_ts)
    roots.sort(key=_ts)

    ordered: List[Dict[str, Any]] = []
    visited: set = set()

    def _walk(msg: Dict[str, Any], depth: int) -> None:
        mid = normalize_msgid(msg.get('id', ''))
        if mid and mid in visited:
            return
        if mid:
            visited.add(mid)
        msg['_depth'] = depth
        ordered.append(msg)
        for child in children.get(mid, []):
            _walk(child, depth + 1)

    for r in roots:
        _walk(r, 0)

    seen_ids = {id(m) for m in ordered}
    for m in messages:
        if id(m) not in seen_ids:
            m.setdefault('_depth', 0)
            ordered.append(m)

    return ordered


def subtree_of(messages: List[Dict[str, Any]], parent_msgid: str) -> List[Dict[str, Any]]:
    """Return the subtree of `messages` rooted at `parent_msgid`.

    Runs thread_sort() on the input, locates the message whose normalized
    Message-ID matches `parent_msgid`, and returns that message followed
    by all its transitive descendants in DFS order. `_depth` is rebased
    so the matched message is at depth 0.

    Returns [] if `parent_msgid` is not present in the thread.
    """
    target = normalize_msgid(parent_msgid)
    if not target:
        return []

    ordered = thread_sort(messages)

    start = -1
    for i, m in enumerate(ordered):
        if normalize_msgid(m.get('id', '')) == target:
            start = i
            break

    if start < 0:
        return []

    base = ordered[start].get('_depth', 0)
    result: List[Dict[str, Any]] = [ordered[start]]
    for m in ordered[start + 1:]:
        if m.get('_depth', 0) <= base:
            break
        result.append(m)

    for m in result:
        m['_depth'] = m.get('_depth', 0) - base

    return result


def decode_header(value: str) -> str:
    """Decode an RFC 2047 encoded email header value to a plain string."""
    try:
        from email.header import decode_header as _dh, make_header
        return str(make_header(_dh(value)))
    except Exception:
        return value


def parse_overview_date(date_str: str) -> str:
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


def format_overview(messages: List[Dict[str, Any]], header: Optional[str] = None) -> List[str]:
    """Return a list of plain-text lines rendering a thread overview.

    Each entry is one line in the form
        "  YYYY-MM-DD ` ` ` Subject Sender"
    where the backtick-space indent reflects msg['_depth'] (set by
    thread_sort or subtree_of). The returned list starts with `header`
    (or a default "Thread overview: N messages") and a blank line.
    """
    if header is None:
        header = f"Thread overview: {len(messages)} messages"
    lines: List[str] = [header, ""]
    for msg in messages:
        subject = decode_header(msg.get('subject', '(No Subject)').strip())
        sender = decode_header(msg.get('from', '').strip())
        date_fmt = parse_overview_date(msg.get('date', ''))
        depth = msg.get('_depth', 0)
        indent = '` ' * depth
        lines.append(f"  {date_fmt} {indent}{subject} {sender}")
    return lines
