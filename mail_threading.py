#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""JWZ-style thread ordering helpers shared across getreleases scripts."""

from collections import defaultdict
from typing import Any, Dict, List


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
