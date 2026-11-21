"""R2 duplicates. Every fixture differs from a merge in exactly the way its name says."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src import dedupe
from src.dedupe import find_duplicates

from conftest import utc

T0 = utc(2001, 6, 1, 14, 0)


@dataclass
class M:
    key: str
    date_utc: datetime | None = T0
    mailbox: str = ""
    sender: str = "a@enron.com"
    subject: str = "Calpine Daily Gas Nomination"
    message_id: str | None = None
    to: list = field(default_factory=lambda: ["b@enron.com"])
    cc: list = field(default_factory=list)
    body: str = "Nomination: 1,200 MMBtu\nStatus: OK"
    body_hash: str = ""

    def __post_init__(self):
        self.mailbox = self.mailbox or self.key.split("/")[0]
        self.message_id = self.message_id or f"<{self.key}@thyme>"     # distinct by default
        self.body_hash = dedupe.body_hash(self.body)


def merges(*msgs):
    r = find_duplicates(list(msgs))
    return {m.copy: (m.canonical, m.reason) for m in r.merges}, {s.key: s.label for s in r.separate}


def test_export_copy_exactly_10_or_11_hours_with_different_message_ids():
    for h in (10, 11):
        a = M("farmer-d/logistics/1")
        b = M("farmer-d/logistics/2", date_utc=T0 + timedelta(hours=h), body="Nomination:   1,200 MMBtu Status: OK")
        assert merges(a, b)[0] == {b.key: (a.key, dedupe.EXPORT_TIMEZONE_COPY)}


def test_not_exactly_10_or_11_hours_is_ambiguous_not_merged():
    a = M("farmer-d/logistics/1")
    b = M("farmer-d/logistics/2", date_utc=T0 + timedelta(hours=10, minutes=30))
    copies, sep = merges(a, b)
    assert copies == {} and sep == {b.key: dedupe.AMBIGUOUS}


def test_same_message_id_merges_at_any_distance():
    a = M("farmer-d/logistics/880", message_id="<same@thyme>")
    b = M("williams-w3/inbox/143", message_id="<same@thyme>", date_utc=T0 + timedelta(days=3))
    assert merges(a, b)[0] == {b.key: (a.key, dedupe.SAME_MESSAGE_ID)}


def test_broadcast_in_two_mailboxes_from_different_aliases_merges():
    a = M("giron-d/inbox/54", sender="coo.jeff@enron.com", to=["dl-ga-all_netco@enron.com"])
    b = M("williams-w3/inbox/87", sender="no.address@enron.com", to=[], date_utc=T0 + timedelta(hours=3.8))
    copies, _ = merges(a, b)
    assert copies == {b.key: (a.key, dedupe.SAME_MAIL_TWO_MAILBOXES)}


def test_same_mail_in_two_mailboxes_more_than_12_hours_apart_is_a_separate_send():
    # "Ken Lay Resigns": sent again 18.2 h later from another address.
    a = M("giron-d/inbox/18", sender="announcements.enron@enron.com", subject="Ken Lay Resigns from Board")
    b = M("williams-w3/inbox/37", sender="no.address@enron.com", subject="Ken Lay Resigns from Board",
          date_utc=T0 + timedelta(hours=18.2))
    copies, sep = merges(a, b)
    assert copies == {} and sep == {b.key: dedupe.SEPARATE_SEND}


def test_resend_within_an_hour_merges_even_with_new_recipients():
    a = M("lokay-m/sent_items/7", subject="Enron Benefit Phone Numbers", to=["jimbpman@bigfoot.com"])
    b = M("lokay-m/sent_items/8", subject="FW: Enron Benefit Phone Numbers", to=["jimboman@bigfoot.com"],
          date_utc=T0 + timedelta(minutes=3))
    r = find_duplicates([a, b])
    assert [(g.copy, g.reason, g.recipients_overlap) for g in r.merges] == [(b.key, dedupe.RESEND_WITHIN_1H, 0.0)]


def test_daily_notice_with_same_subject_a_day_later_stays_separate():
    # Same body, same subject, same sender, same mailbox: only the time tells them apart.
    a = M("farmer-d/logistics/1316")
    b = M("farmer-d/logistics/1318", date_utc=T0 + timedelta(hours=24, minutes=5))
    copies, sep = merges(a, b)
    assert copies == {} and sep == {b.key: dedupe.SEPARATE_SEND}


def test_no_chains_a_copy_is_only_compared_with_canonicals():
    a = M("farmer-d/logistics/1")
    b = M("farmer-d/logistics/2", date_utc=T0 + timedelta(hours=11))
    c = M("farmer-d/logistics/3", date_utc=T0 + timedelta(hours=22))     # 11 h after b, 22 h after a
    copies, sep = merges(a, b, c)
    assert copies == {b.key: (a.key, dedupe.EXPORT_TIMEZONE_COPY)}
    assert sep == {c.key: dedupe.SEPARATE_SEND}


def test_same_body_different_subject_stays_separate():
    a = M("giron-d/inbox/1", subject="Gas nominations")
    b = M("giron-d/inbox/2", subject="Power nominations", date_utc=T0 + timedelta(minutes=5))
    assert merges(a, b) == ({}, {b.key: dedupe.DIFFERENT_SUBJECT})


def test_undated_candidate_stays_separate():
    a = M("giron-d/inbox/1")
    b = M("giron-d/inbox/2", date_utc=None)
    assert merges(a, b) == ({}, {b.key: dedupe.UNDATED})


def test_boundaries_are_exact():
    def pair(delta, a_mb="giron-d", b_mb="williams-w3", sender_b="a@enron.com"):
        a = M(f"{a_mb}/inbox/1")
        b = M(f"{b_mb}/inbox/2", date_utc=T0 + delta, sender=sender_b)
        return merges(a, b)
    s = timedelta(seconds=1)
    # Two mailboxes: 12 h exactly merges, one second more is a separate send.
    assert pair(timedelta(hours=12))[0]
    assert pair(timedelta(hours=12) + s) == ({}, {"williams-w3/inbox/2": dedupe.SEPARATE_SEND})
    # Same mailbox re-send: under one hour merges; exactly one hour doesn't (and is ambiguous).
    assert pair(timedelta(hours=1) - s, b_mb="giron-d")[0]
    assert pair(timedelta(hours=1), b_mb="giron-d") == ({}, {"giron-d/inbox/2": dedupe.AMBIGUOUS})
    # Export copy: exactly 10 h merges, 10 h and one second doesn't.
    assert pair(timedelta(hours=10), b_mb="giron-d")[0]
    assert pair(timedelta(hours=10) + s, b_mb="giron-d")[0] == {}
    # Same mailbox, different sender, 10 h apart: no reason fits.
    assert pair(timedelta(hours=10), b_mb="giron-d", sender_b="other@enron.com") == \
        ({}, {"giron-d/inbox/2": dedupe.AMBIGUOUS})


def test_a_later_message_can_merge_into_a_kept_separate_send():
    # a, then b a day later (separate), then c 10 h after b: c is a copy of b, not of a.
    a = M("farmer-d/logistics/1")
    b = M("farmer-d/logistics/2", date_utc=T0 + timedelta(days=1))
    c = M("farmer-d/logistics/3", date_utc=T0 + timedelta(days=1, hours=10))
    copies, sep = merges(a, b, c)
    assert copies == {c.key: (b.key, dedupe.EXPORT_TIMEZONE_COPY)} and sep == {b.key: dedupe.SEPARATE_SEND}


def test_canonical_is_the_lowest_key_and_whitespace_is_ignored():
    a = M("giron-d/inbox/10", body="Same   text\r\nhere")
    b = M("giron-d/inbox/9", body="Same text here", date_utc=T0 + timedelta(hours=10))
    assert merges(a, b)[0] == {a.key: (b.key, dedupe.EXPORT_TIMEZONE_COPY)}
