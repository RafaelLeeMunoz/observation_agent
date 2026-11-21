"""Small text helpers shared by the ingest modules."""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")

# Re:/Fw:/Fwd: prefixes, repeated, optionally numbered ("RE[2]:").
SUBJ_PREFIX = re.compile(r"^\s*((re|fw|fwd)\s*(\[\d+\])?\s*:\s*)+", re.I)


def norm_ws(text: str) -> str:
    """Collapse every run of whitespace to one space and trim the ends."""
    return _WS.sub(" ", text or "").strip()


def subject_norm(subject: str | None) -> str:
    """Subject with Re:/Fw: prefixes removed, whitespace collapsed, lowercased."""
    return norm_ws(SUBJ_PREFIX.sub("", subject or "")).lower()


def has_reply_prefix(subject: str | None) -> bool:
    return bool(SUBJ_PREFIX.match(subject or ""))


def sort_key(key: str) -> tuple[str, str, int]:
    """Order message keys like 'giron-d/inbox/20' by mailbox, folder, then number."""
    mailbox, folder, number = key.split("/")
    return (mailbox, folder, int(number))
