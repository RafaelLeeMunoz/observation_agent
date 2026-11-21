"""R7 months observed: union of mailbox windows, Decimal arithmetic."""
from decimal import Decimal

import pytest

from src.windows import DAYS_PER_MONTH, mailbox_windows, months_observed

from conftest import utc

WIN = mailbox_windows([
    ("a", utc(2001, 1, 1)), ("a", utc(2001, 3, 2)),          # 60 days
    ("b", utc(2001, 2, 1)), ("b", utc(2001, 4, 2)),          # overlaps a
    ("c", utc(2001, 6, 1)), ("c", utc(2001, 6, 11)),         # disjoint, 10 days
])


def test_single_window():
    assert months_observed(WIN, ["a"]) == Decimal(60) / DAYS_PER_MONTH


def test_overlapping_windows_are_merged_not_added():
    # a ∪ b = 1 Jan .. 2 Apr = 91 days, not 60 + 60.
    assert months_observed(WIN, ["a", "b"]) == Decimal(91) / DAYS_PER_MONTH


def test_disjoint_windows_add_and_repeats_count_once():
    assert months_observed(WIN, ["a", "c", "c"]) == Decimal(70) / DAYS_PER_MONTH


def test_no_mailbox_is_an_error():
    with pytest.raises(ValueError):
        months_observed(WIN, [])
