"""R8 coverage ledger: every file accounted for, every unique message tracked to `read`.

Ingest status, one per file: `unique` or `duplicate_of:<key>` (with its reason), or
`excluded:<reason>` only if a parse fails. Analysis status, one per unique message:
pending -> batched -> read | failed. A message is `read` only when its batch output passed the
schema check and its read receipt lists that message's key. A failed batch is retried at most
MAX_RETRIES times and is never counted as read.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
from pathlib import Path

PENDING, BATCHED, READ, FAILED = "pending", "batched", "read", "failed"
NOT_APPLICABLE = "n/a"          # copies are never sent to the model
MAX_RETRIES = 2


@dataclass
class LedgerRow:
    key: str
    original_path: str
    ingest_status: str
    duplicate_reason: str
    special_handling: str
    parse_status: str
    date_status: str
    clock_check: str
    thread_id: str
    readings: int                # non-literal readings logged for this message (see the readings table)
    analysis_status: str


def write_csv(rows: list[LedgerRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(LedgerRow.__dataclass_fields__))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def apply_batch_result(status: dict[str, str], batch_keys: list[str],
                       receipt_keys: list[str] | None, output_valid: bool) -> list[str]:
    """Update analysis status for one batch attempt. Returns the keys left unread.

    Only keys in both the batch and its receipt become `read`, and only when the output passed
    its schema check. Anything else in the batch is `failed` (to be retried).
    """
    listed = set(receipt_keys or ()) if output_valid else set()
    unread = []
    for k in batch_keys:
        if k in listed:
            status[k] = READ
        else:
            status[k] = FAILED
            unread.append(k)
    return unread


def coverage_problems(status: dict[str, str]) -> list[str]:
    """Unique messages that aren't `read`: a run with any of these fails its coverage check."""
    return sorted(k for k, s in status.items() if s != READ)
