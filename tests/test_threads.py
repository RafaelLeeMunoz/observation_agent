"""R5 threads. A root always starts a new thread; only replies/forwards join."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.threads import build_threads

from conftest import utc

T0 = utc(2001, 5, 1, 10, 0)


@dataclass
class M:
    key: str
    days: float | None = 0                  # offset from T0; None = undated
    is_root: bool = False
    subject_norm: str = "schedule update"
    people: set = field(default_factory=lambda: {"alice@enron.com", "bob@enron.com"})

    @property
    def date_utc(self) -> datetime | None:
        return None if self.days is None else T0 + timedelta(days=self.days)


def groups(*msgs):
    return [t.keys for t in build_threads(list(msgs))]


def test_a_root_always_starts_a_new_thread():
    # Same subject, shared people, one day apart: still two separate sends (daily notices).
    assert groups(M("mb/f/1", 0, is_root=True), M("mb/f/2", 1, is_root=True)) == [["mb/f/1"], ["mb/f/2"]]


def test_reply_joins_within_14_days_with_a_shared_address():
    assert groups(M("mb/f/1", 0, is_root=True), M("mb/f/2", 5)) == [["mb/f/1", "mb/f/2"]]


def test_gap_of_15_days_starts_a_new_thread():
    assert groups(M("mb/f/1", 0, is_root=True), M("mb/f/2", 15)) == [["mb/f/1"], ["mb/f/2"]]


def test_gap_is_whole_days_elapsed_rounded_down():
    assert groups(M("mb/f/1", 0, is_root=True), M("mb/f/2", 14 + 23 / 24)) == [["mb/f/1", "mb/f/2"]]
    assert groups(M("mb/f/1", 0, is_root=True), M("mb/f/2", 15 - 1 / 86400)) == [["mb/f/1", "mb/f/2"]]


def test_gap_is_measured_from_the_threads_last_message_not_its_first():
    a, b, c = M("mb/f/1", 0, is_root=True), M("mb/f/2", 10), M("mb/f/3", 20)
    assert groups(a, b, c) == [["mb/f/1", "mb/f/2", "mb/f/3"]]       # 20 d after a, 10 d after b


def test_reply_without_a_shared_address_starts_a_new_thread():
    a = M("mb/f/1", 0, is_root=True)
    b = M("mb/f/2", 1, people={"carol@enron.com"})
    assert groups(a, b) == [["mb/f/1"], ["mb/f/2"]]


def test_empty_subject_never_joins():
    assert groups(M("mb/f/1", 0, is_root=True, subject_norm=""), M("mb/f/2", 1, subject_norm="")) == \
        [["mb/f/1"], ["mb/f/2"]]


def test_reply_joins_the_most_recent_open_thread():
    a, b = M("mb/f/1", 0, is_root=True), M("mb/f/2", 3, is_root=True)
    reply = M("mb/f/3", 4)
    assert groups(a, b, reply) == [["mb/f/1"], ["mb/f/2", "mb/f/3"]]


def test_undated_messages():
    a = M("mb/f/1", 0, is_root=True)
    assert groups(a, M("mb/f/2", None)) == [["mb/f/1", "mb/f/2"]]                       # reply joins
    assert groups(a, M("mb/f/3", None, is_root=True)) == [["mb/f/1"], ["mb/f/3"]]       # root stands alone


def test_thread_ids_are_stable_and_marked_inferred():
    t = build_threads([M("mb/f/2", 1), M("mb/f/1", 0, is_root=True)])
    assert [x.thread_id for x in t] == ["t:mb/f/1"] and t[0].inferred and t[0].starts_with_root
