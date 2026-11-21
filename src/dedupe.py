"""R2 duplicates: merge a copy only for a named reason.

Candidates share an identical whitespace-normalized body. A candidate is merged only when it
matches a canonical (first-kept) message, never another copy, so A~B and B~C can't chain A
into C. It also needs the same subject after Re:/Fw: is stripped, and one named reason.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from .textutil import norm_ws, sort_key, subject_norm

SAME_MESSAGE_ID = "same_message_id"
EXPORT_TIMEZONE_COPY = "export_timezone_copy"
SAME_MAIL_TWO_MAILBOXES = "same_mail_in_two_mailboxes"
RESEND_WITHIN_1H = "resend_within_1h"

EXPORT_COPY_SECONDS = (10 * 3600, 11 * 3600)   # exactly 10 or 11 hours apart
CROSS_MAILBOX_MAX_HOURS = 12
RESEND_MAX_HOURS = 1

# Labels for same-body messages that stay separate.
DIFFERENT_SUBJECT = "different_subject"
SEPARATE_SEND = "separate_send"            # more than 12 h apart: a different event
UNDATED = "undated"
AMBIGUOUS = "ambiguous_duplicate"          # within 12 h, same subject, but no named reason


class Msg(Protocol):
    key: str
    mailbox: str
    message_id: str | None
    sender: str
    subject: str
    to: list[str]
    cc: list[str]
    date_utc: datetime | None
    body_hash: str


def body_hash(body: str) -> str:
    return hashlib.sha256(norm_ws(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Merge:
    copy: str
    canonical: str
    reason: str
    gap_hours: float | None
    recipients_overlap: float        # Jaccard of To+Cc, recorded, not required


@dataclass(frozen=True)
class Separate:
    key: str
    compared_to: str
    label: str
    gap_hours: float | None


@dataclass
class DedupeResult:
    candidate_groups: list[list[str]] = field(default_factory=list)
    merges: list[Merge] = field(default_factory=list)
    separate: list[Separate] = field(default_factory=list)

    @property
    def duplicate_of(self) -> dict[str, str]:
        return {m.copy: m.canonical for m in self.merges}

    @property
    def ambiguous(self) -> list[Separate]:
        return [s for s in self.separate if s.label == AMBIGUOUS]


def _gap_seconds(a: Msg, b: Msg) -> int | None:
    if a.date_utc is None or b.date_utc is None:
        return None
    return abs(int((a.date_utc - b.date_utc).total_seconds()))


def _hours(sec: int | None) -> float | None:
    return None if sec is None else sec / 3600


def _overlap(a: Msg, b: Msg) -> float:
    ra, rb = set(a.to) | set(a.cc), set(b.to) | set(b.cc)
    union = ra | rb
    return len(ra & rb) / len(union) if union else 0.0     # no recipients listed: none shared


def merge_reason(canonical: Msg, cand: Msg) -> str | None:
    """The named reason `cand` is a copy of `canonical`, or None."""
    if subject_norm(canonical.subject) != subject_norm(cand.subject):
        return None
    if canonical.message_id and canonical.message_id == cand.message_id:
        return SAME_MESSAGE_ID
    gap = _gap_seconds(canonical, cand)
    if gap is None:
        return None
    if canonical.mailbox != cand.mailbox:
        return SAME_MAIL_TWO_MAILBOXES if gap <= CROSS_MAILBOX_MAX_HOURS * 3600 else None
    if canonical.sender != cand.sender:
        return None
    if gap in EXPORT_COPY_SECONDS:
        return EXPORT_TIMEZONE_COPY
    if gap < RESEND_MAX_HOURS * 3600:
        return RESEND_WITHIN_1H
    return None


def _separate_label(kept: list[Msg], cand: Msg) -> tuple[str, Msg, int | None]:
    """Why `cand` stays separate. Ambiguous when some canonical has the same subject and is
    within 12 hours, yet no named reason fits."""
    for c in kept:
        gap = _gap_seconds(c, cand)
        if subject_norm(c.subject) == subject_norm(cand.subject) and gap is not None \
                and gap <= CROSS_MAILBOX_MAX_HOURS * 3600:
            return AMBIGUOUS, c, gap
    first = kept[0]
    gap = _gap_seconds(first, cand)
    if subject_norm(first.subject) != subject_norm(cand.subject):
        return DIFFERENT_SUBJECT, first, gap
    if gap is None:
        return UNDATED, first, gap
    return SEPARATE_SEND, first, gap


def find_duplicates(messages: list[Msg]) -> DedupeResult:
    """Apply R2 to all messages. Canonical = lowest key in each body group."""
    by_key = {m.key: m for m in messages}
    groups: dict[str, list[str]] = defaultdict(list)
    for m in messages:
        groups[m.body_hash].append(m.key)
    result = DedupeResult()
    for keys in sorted((sorted(v, key=sort_key) for v in groups.values() if len(v) > 1),
                       key=lambda g: sort_key(g[0])):
        result.candidate_groups.append(keys)
        kept = [by_key[keys[0]]]
        for k in keys[1:]:
            cand = by_key[k]
            for c in kept:
                reason = merge_reason(c, cand)
                if reason:
                    gap = _gap_seconds(c, cand)
                    result.merges.append(Merge(k, c.key, reason, _hours(gap), round(_overlap(c, cand), 3)))
                    break
            else:
                label, other, gap = _separate_label(kept, cand)
                result.separate.append(Separate(k, other.key, label, _hours(gap)))
                kept.append(cand)
    return result
