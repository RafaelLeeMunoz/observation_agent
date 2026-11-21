"""R5 threads (conversations), inferred: the corpus has no In-Reply-To/References headers.

A root (no Re:/Fw: prefix and no quoted text) always starts a new thread. A reply or forward
joins the most recent open thread with the same normalized subject that shares at least one
From/To/Cc address, when the thread's last message is no more than 14 days earlier.
Otherwise it starts its own thread. An empty subject never joins.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from .textutil import sort_key

MAX_GAP_DAYS = 14


class Msg(Protocol):
    key: str
    subject_norm: str
    people: set[str]
    is_root: bool
    date_utc: datetime | None


@dataclass
class Thread:
    thread_id: str
    subject_norm: str
    keys: list[str] = field(default_factory=list)       # reading order
    people: set[str] = field(default_factory=set)
    first_utc: datetime | None = None
    last_utc: datetime | None = None
    starts_with_root: bool = True
    inferred: bool = True


def gap_days(earlier: datetime, later: datetime) -> int:
    """Whole days elapsed (floor), the gap measure used for joining (M1 notebook §14c)."""
    return (later - earlier).days


def build_threads(messages: list[Msg], max_gap_days: int = MAX_GAP_DAYS) -> list[Thread]:
    """Group unique messages into threads. Deterministic: same input, same threads and IDs."""
    by_subject: dict[str, list[Msg]] = defaultdict(list)
    for m in messages:
        by_subject[m.subject_norm].append(m)
    threads: list[Thread] = []

    def start(m: Msg) -> Thread:
        t = Thread(thread_id=f"t:{m.key}", subject_norm=m.subject_norm, starts_with_root=m.is_root)
        threads.append(t)
        return t

    def add(t: Thread, m: Msg) -> None:
        t.keys.append(m.key)
        t.people |= m.people
        if m.date_utc is not None:
            t.first_utc = t.first_utc or m.date_utc
            t.last_utc = m.date_utc

    for subject in sorted(by_subject):
        group = by_subject[subject]
        dated = sorted((m for m in group if m.date_utc is not None), key=lambda m: (m.date_utc, sort_key(m.key)))
        undated = sorted((m for m in group if m.date_utc is None), key=lambda m: sort_key(m.key))
        open_threads: list[Thread] = []
        for m in dated:
            home = None
            if subject and not m.is_root:
                cands = [t for t in open_threads
                         if gap_days(t.last_utc, m.date_utc) <= max_gap_days and t.people & m.people]
                home = max(cands, key=lambda t: t.last_utc) if cands else None
            if home is None:
                home = start(m)
                open_threads.append(home)
            add(home, m)
        for m in undated:
            home = None
            if subject and not m.is_root:
                home = next((t for t in reversed(open_threads) if t.people & m.people), None)
            if home is None:
                home = start(m)
            add(home, m)
    return threads
