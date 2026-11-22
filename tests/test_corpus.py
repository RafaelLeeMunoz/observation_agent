"""The real corpus, offline. Two kinds of test:

* Regression pins: the expected numbers in PLAN.md 3.1. They catch any change in behaviour, but
  they were computed by this same code, so on their own they can't prove the code is right.
* Independent checks: re-derive facts a different way (another date parser, the raw bytes on
  disk, the rule definitions applied directly to every thread) and compare.
"""
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src import dates, dedupe, ingest, rawio, windows

pytestmark = pytest.mark.corpus
ROOT = Path(__file__).resolve().parent.parent


# --- regression pins (PLAN.md 3.1) ---

def test_ledger_covers_every_file(corpus_result):
    r = corpus_result
    assert len(r.messages) == 3240
    assert len(r.ledger_rows()) == 3240
    assert Counter(row.ingest_status == "unique" for row in r.ledger_rows()) == {True: 3200, False: 40}
    assert sorted(r.ignored_files) == [".gitattributes", "ABOUT.md", "MANIFEST.csv"]
    assert r.manifest_problems == []
    assert all(m.parse_status == "ok" for m in r.messages.values())


def test_reconciliation_invariants(corpus_result):
    rec = corpus_result.reconciliation()
    assert all(rec.values()), rec
    assert len(corpus_result.unique) - len(corpus_result.threads) == 464


def test_r1_dates(corpus_result):
    st = Counter(m.parsed_date.status for m in corpus_result.messages.values())
    assert st == {dates.OK: 3236, dates.MISSING: 1, dates.INVALID: 1, dates.OUT_OF_RANGE: 2}
    bad = {m.key: m.parsed_date.status for m in corpus_result.messages.values() if not m.parsed_date.ok}
    assert bad == {"giron-d/sent/795": dates.MISSING, "williams-w3/inbox/142": dates.INVALID,
                   "lokay-m/inbox/74": dates.OUT_OF_RANGE, "lokay-m/inbox/50": dates.OUT_OF_RANGE}
    years = Counter(m.date_utc.year for m in corpus_result.messages.values() if m.date_utc)
    assert years == {1999: 54, 2000: 1219, 2001: 1496, 2002: 467}


def test_r1_clock_check(corpus_result):
    clock = Counter(m.clock_check for m in corpus_result.unique)
    assert clock[dates.SHIFTED] == 572


def test_r2_duplicates(corpus_result):
    d = corpus_result.dedupe
    assert sum(len(g) - 1 for g in d.candidate_groups) == 49
    assert Counter(g.reason for g in d.merges) == {
        dedupe.EXPORT_TIMEZONE_COPY: 28, dedupe.SAME_MAIL_TWO_MAILBOXES: 6,
        dedupe.RESEND_WITHIN_1H: 5, dedupe.SAME_MESSAGE_ID: 1}
    assert d.ambiguous == []
    assert sorted(s.key for s in d.separate) == sorted(
        [f"farmer-d/logistics/{n}" for n in (1310, 1312, 1316, 1318, 1329, 1330, 1336, 1348)]
        + ["williams-w3/inbox/37"])
    assert sum(1 for g in d.merges if g.recipients_overlap == 0) == 7
    gaining = {m.key for m in corpus_result.unique if len(m.seen_in_mailboxes) > 1}
    assert len(gaining) == 7


def test_r3_quoted_text(corpus_result):
    msgs = corpus_result.messages.values()
    with_quotes = [m for m in msgs if m.blocks]
    # 1,670: M1 found 1,630; M2 added 3 Lotus replies with two-digit-year headers; the review added
    # 7 ("Original Appointment", Sent-less header blocks); the benchmark reading added 30 (Lotus
    # internet-reply headers with seconds: '"X" <addr> on 08/23/2000 01:40:40 PM' + To:).
    assert len(with_quotes) == 1670
    body = sum(len(m.body) for m in msgs)
    quoted = sum(len(m.text) - m.new_text_end for m in with_quotes)
    assert round(100 * quoted / body, 1) == 59.9
    for m in with_quotes:
        assert "".join(m.text[b.start:b.end] for b in m.blocks) == m.text[m.new_text_end:]
    blocks = [b for m in corpus_result.unique for b in m.blocks]
    assert len(blocks) == 4079
    reasons = Counter((b.skip_reason or "kept").split(":")[0] for b in blocks)
    # R3: skip only when the same text is in the same thread. 143 -> 281 originals after block
    # text stopped including the quoted header (author line, wrapped cc: lines) when matching.
    assert (reasons["original_in_thread"], reasons["repeat_in_thread"]) == (281, 305)
    assert (corpus_result.skipped_originals, corpus_result.skipped_repeats) == (281, 305)
    linked = [b for b in blocks if b.links]
    assert len(linked) == 159 and not any(b.skip_reason for b in linked)
    assert (corpus_result.linked_to_original, corpus_result.linked_copies) == (57, 113)
    assert len({b.first_seen for b in linked}) == 99
    assert sum(1 for b in linked if "#" not in b.first_seen) == 57            # anchored on an original
    long_blocks = [b for m in corpus_result.unique for b in m.blocks
                   if len(" ".join(m.text[b.start:b.end].split())) >= 80]
    assert round(100 * sum(1 for b in long_blocks if b.quoted_date) / len(long_blocks), 1) == 95.6


def test_no_silent_readings(corpus_result):
    """Every non-literal reading, and every date that couldn't be read, is logged with why."""
    rd = corpus_result.readings
    assert Counter(x.rule for x in rd) == {"two_digit_year": 132, "quoted_printable_space": 30,
                                           "address_recovered": 12, "date_not_read": 7, "column_format": 4}
    assert len({x.key for x in rd}) == 78
    assert Counter((x.certainty, x.outcome) for x in rd) == {
        ("inferred", "read"): 145, ("exact", "read"): 33, ("none", "not_read"): 7}
    for x in rd:
        assert x.written in corpus_result.messages[x.key].text[x.start:x.end]
        assert x.why
        assert bool(x.read_as) == (x.outcome == "read")
    years = Counter(x.read_as[:4] for x in rd if x.rule == "two_digit_year")
    assert set(years) <= {"1999", "2000", "2001", "2002"}
    per_msg = {row.key: row.readings for row in corpus_result.ledger_rows()}
    assert sum(per_msg.values()) == len(rd)
    flow = next(x for x in rd if x.where == "table column FLOW_DATE")
    assert flow.read_as == "date day/month" and "2,160 of 3,600" in flow.why and flow.certainty == "inferred"


def test_r4_giant_message(corpus_result):
    special = {m.key: m for m in corpus_result.unique if m.special_handling}
    assert list(special) == ["farmer-d/logistics/3221"]
    g = special["farmer-d/logistics/3221"]
    assert g.special_handling == "table_parsed" and g.body_bytes == 349_596
    t = g.table
    assert len(t.rows) == 3600 and t.bad_rows == []
    assert t.column_types["FLOW_DATE"] == "date day/month"
    fd = t.summary["columns"]["FLOW_DATE"]
    assert (fd["invalid_as_day_month"], fd["invalid_as_month_day"]) == (0, 2160)
    assert t.summary["variance_check"]["pass"] == 3600
    assert t.summary["columns"]["STATUS"]["counts"] == {"PENDING": 1200, "SCHEDULED": 2400}
    assert len(t.prose) == 3 and t.prose[-1] == "Daren"
    second = max((m.body_bytes for m in corpus_result.messages.values() if m.key != g.key))
    assert second == 26_506


def test_r5_threads(corpus_result):
    th = corpus_result.threads
    assert len(th) == 2736
    assert sum(1 for t in th if len(t.keys) > 1) == 348
    assert sum(1 for t in th if not t.starts_with_root) == 1318
    assert sum(1 for t in th if t.subject_norm == "calpine daily gas nomination") == 18
    assert sum(1 for m in corpus_result.unique if m.subject_norm == "") == 107
    assert all(t.inferred for t in th)


def test_r7_months_observed(corpus_result):
    w = corpus_result.windows
    q = lambda mbs: windows.months_observed(w, mbs).quantize(Decimal("0.01"))
    assert {mb: q([mb]) for mb in w} == {"farmer-d": Decimal("24.06"), "giron-d": Decimal("20.00"),
                                         "lokay-m": Decimal("5.92"), "williams-w3": Decimal("9.05")}
    assert q(list(w)) == Decimal("27.39")
    assert q(["lokay-m", "williams-w3"]) == Decimal("10.58")


# --- independent checks ---

def test_dates_agree_with_an_independent_parser(corpus_result):
    """strptime with %z, a different code path from parsedate_tz, must agree on every ok date."""
    for m in corpus_result.messages.values():
        if m.parsed_date.ok:
            s = re.sub(r"\s*\([A-Za-z]+\)\s*$", "", m.date_raw.strip())
            assert datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %z").astimezone(timezone.utc) == m.date_utc, m.key


def test_every_stored_offset_resolves_in_the_raw_bytes_on_disk(corpus_result):
    for m in corpus_result.messages.values():
        raw = rawio.read_bytes(ROOT / m.local_path)
        assert len(raw) == len(m.text) == m.size_bytes
        for b in m.blocks:
            assert raw[b.start:b.end].decode() == m.text[b.start:b.end]
            if b.date_offsets:
                assert raw[slice(*b.date_offsets)].decode() == b.date_source.text
        if m.table:
            for row in m.table.rows:
                assert raw[row.start:row.end].decode() == row.raw
    for x in corpus_result.readings:
        raw = rawio.read_bytes(ROOT / corpus_result.messages[x.key].local_path)
        assert x.written.encode() in raw[x.start:x.end]


def test_every_thread_obeys_the_rule_when_checked_directly(corpus_result):
    by_key = {m.key: m for m in corpus_result.unique}
    for t in corpus_result.threads:
        ms = [by_key[k] for k in t.keys]
        assert len({m.subject_norm for m in ms}) == 1
        for i, m in enumerate(ms[1:], 1):
            assert not m.is_root, (t.thread_id, m.key)
            assert m.subject_norm != ""
            assert m.people & set().union(*(x.people for x in ms[:i])), (t.thread_id, m.key)
            earlier = [x.date_utc for x in ms[:i] if x.date_utc]
            if m.date_utc and earlier:
                assert (m.date_utc - max(earlier)).days <= 14


def test_every_merge_is_rechecked_from_the_raw_fields(corpus_result):
    msgs = corpus_result.messages
    for g in corpus_result.dedupe.merges:
        a, b = msgs[g.canonical], msgs[g.copy]
        assert " ".join(a.body.split()) == " ".join(b.body.split())
        assert a.subject_norm == b.subject_norm
        assert a.duplicate_of is None                       # canonical, never a copy
        assert dedupe.merge_reason(a, b) == g.reason


def test_two_builds_are_identical():
    def snap(r):
        return ([(m.key, m.parsed_date.status, m.duplicate_of, m.thread_id, m.new_text_end,
                  [(b.start, b.end, b.skip_reason) for b in m.blocks]) for m in r.messages.values()],
                [(x.key, x.start, x.rule, x.read_as) for x in r.readings])
    assert snap(ingest.build(ROOT / "corpus")) == snap(ingest.build(ROOT / "corpus"))


def test_database_ledger_and_result_reconcile(corpus_result, tmp_path):
    import csv
    import sqlite3
    db, led = tmp_path / "i.sqlite", tmp_path / "l.csv"
    assert ingest.main(["--corpus", str(ROOT / "corpus"), "--db", str(db), "--ledger", str(led)]) == 0
    con = sqlite3.connect(db)
    q = lambda sql: con.execute(sql).fetchone()[0]
    assert q("SELECT COUNT(*) FROM messages") == 3240
    assert q("SELECT COUNT(*) FROM messages WHERE ingest_status = 'unique'") == 3200
    assert q("SELECT COUNT(*) FROM duplicates") == 40
    assert q("SELECT COUNT(*) FROM threads") == len(corpus_result.threads)
    assert q("SELECT COUNT(*) FROM table_rows") == 3600
    assert q("SELECT COUNT(*) FROM readings") == len(corpus_result.readings)
    assert q("SELECT value FROM meta WHERE name = 'reconciliation_ok'") == "1"
    rows = list(csv.DictReader(led.open(encoding="utf-8")))
    assert len(rows) == 3240 and {r["key"] for r in rows} == set(corpus_result.messages)
    assert sum(int(r["readings"]) for r in rows) == q("SELECT COUNT(*) FROM readings")
    con.close()
