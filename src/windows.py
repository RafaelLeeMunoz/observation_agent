"""R7 months observed: the events/month denominator.

Each mailbox's window runs from its first to its last `ok`-dated unique message. A process
is divided by the union of the windows of the mailboxes where its events were seen
(including mailboxes where only a copy appeared), in days / 30.4375. Overlapping windows
are merged, never added. Arithmetic is Decimal (PLAN 3.4).
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

DAYS_PER_MONTH = Decimal("30.4375")
SECONDS_PER_DAY = Decimal(86400)

Window = tuple[datetime, datetime]


def mailbox_windows(dated: Iterable[tuple[str, datetime]]) -> dict[str, Window]:
    """(mailbox, utc) pairs from unique, ok-dated messages -> {mailbox: (first, last)}."""
    out: dict[str, Window] = {}
    for mailbox, utc in dated:
        if mailbox in out:
            a, b = out[mailbox]
            out[mailbox] = (min(a, utc), max(b, utc))
        else:
            out[mailbox] = (utc, utc)
    return out


def _seconds(a: datetime, b: datetime) -> Decimal:
    d = b - a
    return Decimal(d.days * 86400 + d.seconds) + Decimal(d.microseconds) / Decimal(10**6)


def months_observed(windows: dict[str, Window], mailboxes: Iterable[str]) -> Decimal:
    """Union of the named mailboxes' windows, in months. Unrounded; round only for display."""
    spans = sorted(windows[m] for m in set(mailboxes))
    if not spans:
        raise ValueError("months_observed needs at least one mailbox")
    total = Decimal(0)
    cur_a, cur_b = spans[0]
    for a, b in spans[1:]:
        if a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            total += _seconds(cur_a, cur_b)
            cur_a, cur_b = a, b
    total += _seconds(cur_a, cur_b)
    return total / SECONDS_PER_DAY / DAYS_PER_MONTH
