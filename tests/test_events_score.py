"""Events (one per process per thread, quoted copies once) and Decimal scoring as provenance claims."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src import events, score

CFG = {"hourly_rate": 85, "min_events": 2, "min_emails": 3, "min_threads": 2, "min_months": 1}


class M:                      # the few StoredMessage fields events.py reads
    def __init__(self, key, thread, mailbox="mb"):
        self.key, self.thread_id, self.mailbox = key, thread, mailbox


class St:
    def __init__(self, msgs):
        self.messages = {m.key: m for m in msgs}


def o(oid, key, date="2001-03-01", anchor=None, signals=(), actors=("a@x.com",)):
    return {"obs_id": oid, "msg_key": key, "evidence_date": date, "date_basis": "message_date", "anchor": anchor,
            "actors": list(actors), "stall_signals": list(signals),
            "published_evidence": [{"signal": s, "msg_key": key, "start": 0, "end": 30} for s in signals]}


ST = St([M("mb/i/1", "t1"), M("mb/i/2", "t1"), M("mb/i/3", "t2"), M("mb/i/4", "t3"), M("mb/i/5", "t4", "mc")])


def test_one_event_per_process_per_thread_and_earliest_date():
    obs = [o("a", "mb/i/1", "2001-03-05"), o("b", "mb/i/2", "2001-03-02"), o("c", "mb/i/3")]
    ev = events.build(obs, {"a": "P1", "b": "P1", "c": "P1"}, ST, {})
    assert [(e["thread_key"], e["obs_ids"], e["date"]) for e in ev] == [("t1", ["a", "b"], "2001-03-02"), ("t2", ["c"], "2001-03-01")]
    assert events.check(ev, {"a": "P1", "b": "P1", "c": "P1"}) == []


def test_quoted_copies_in_other_threads_merge_into_their_anchors_event():
    # c and d quote text first seen in mb/i/1 (thread t1): with the original's own observation, one event
    obs = [o("a", "mb/i/1"), o("c", "mb/i/3", anchor="mb/i/1"), o("d", "mb/i/4", anchor="mb/i/1#0")]
    ev = events.build(obs, {x: "P1" for x in "acd"}, ST, {})
    assert len(ev) == 1 and ev[0]["obs_ids"] == ["a", "c", "d"] and ev[0]["merged_by"] == ["quoted_anchor", "thread"]
    assert ev[0]["threads"] == ["t1", "t2", "t3"]


def test_two_processes_in_one_thread_are_two_events_and_unassigned_observations_are_left_out():
    obs = [o("a", "mb/i/1"), o("b", "mb/i/2"), o("z", "mb/i/3")]
    ev = events.build(obs, {"a": "P1", "b": "P2"}, ST, {})
    assert sorted((e["process_id"], e["obs_ids"][0]) for e in ev) == [("P1", "a"), ("P2", "b")]


def test_mailboxes_include_every_mailbox_a_message_was_seen_in():
    ev = events.build([o("a", "mb/i/1")], {"a": "P1"}, ST, {"mb/i/1": ["mb", "mc"]})
    assert ev[0]["mailboxes"] == ["mb", "mc"]


def test_an_undated_event_is_kept_without_a_date():
    ev = events.build([o("a", "mb/i/1", date=None)], {"a": "P1"}, ST, {})
    assert ev[0]["date"] is None and ev[0]["date_basis"] == "undated"


# --- scoring ---

def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


WIN = {"mb": (utc(2001, 1, 1), utc(2001, 7, 2, 7, 30)), "mc": (utc(2001, 4, 1), utc(2001, 10, 1))}


def proc(pid, base=90, low=60, high=120):
    return {"process_id": pid, "name": pid, "minutes_per_event": {"low": low, "base": base, "high": high,
                                                                 "rationale": "r", "basis_obs_ids": []}}


def ev(eid, pid, msgs, threads, actors=("a", "b", "c"), mailboxes=("mb",)):
    return {"event_id": eid, "process_id": pid, "obs_ids": [f"{eid}o"], "msg_keys": msgs, "threads": threads,
            "actors": list(actors), "mailboxes": list(mailboxes), "stall_signals": {}}


def months(mbs):
    from src import windows
    return windows.months_observed(WIN, mbs)


def test_the_plan_worked_example_12_events_over_6_months_at_90_minutes_is_255_dollars():
    from datetime import timedelta
    win = {"mb": (utc(2001, 1, 1), utc(2001, 1, 1) + timedelta(days=6 * 30.4375))}       # exactly 6 months
    evs = [ev(f"E{i}", "P1", [f"k{i}"], [f"t{i}"]) for i in range(12)]
    r = score.score([proc("P1")], evs, win, CFG, {})
    (opp,) = r["opportunities"]
    assert Decimal(opp["hours_per_month"]) == Decimal(3) and score.money(Decimal(opp["dollars_per_month"])) == "$255.00"


def test_decimal_half_up_rounding_where_float_rounding_is_a_cent_off():
    assert score.money(Decimal("2.125")) == "$2.13" and round(2.125, 2) == 2.12


def test_minimum_evidence_and_short_windows_publish_no_rate():
    few = score.score([proc("P1")], [ev("E1", "P1", ["k1", "k2", "k3"], ["t1"])], WIN, CFG, {})
    assert few["opportunities"][0]["status"] == "insufficient_evidence" and "dollars" not in few["opportunities"][0]["claims"]
    tiny = {"mb": (utc(2001, 1, 1), utc(2001, 1, 20))}
    short = score.score([proc("P1")], [ev("E1", "P1", ["k1", "k2"], ["t1"]), ev("E2", "P1", ["k3"], ["t2"])], tiny, CFG, {})
    assert short["opportunities"][0]["status"] == "window_too_short"


def test_overlapping_windows_are_merged_and_shared_events_count_once_in_the_total():
    e = [ev("E1", "P1", ["k1", "k2"], ["t1"], mailboxes=("mb", "mc")), ev("E2", "P1", ["k3"], ["t2"]),
         ev("E3", "P2", ["k4", "k5"], ["t3"]), ev("E4", "P2", ["k6"], ["t4"])]
    e_shared = e + [dict(e[0], process_id="P2")]                   # P2 also claims event E1
    r = score.score([proc("P1", base=30), proc("P2", base=60)], e_shared, WIN, CFG, {})
    p1 = next(o for o in r["opportunities"] if o["process_id"] == "P1")
    assert Decimal(p1["months"]) == months(["mb", "mc"]) < months(["mb"]) + months(["mc"])
    first, second = r["opportunities"][:2]
    assert first["rank"] == 1 and Decimal(second["allocated_dollars"]) < Decimal(second["dollars_per_month"])
    total = sum(Decimal(o["allocated_dollars"]) for o in r["opportunities"] if o["status"] == "scored")
    assert Decimal(r["claims"]["total"]["value"]) == total


def test_every_claim_chain_ends_in_verified_quotes_or_documented_assumptions():
    e = [ev("E1", "P1", ["k1", "k2"], ["t1"]), ev("E2", "P1", ["k3"], ["t2"])]
    r = score.score([proc("P1")], e, WIN, CFG, {"E1o": ["q1"], "E2o": ["q2"]})
    assert score.check_chains(r["claims"], {"q1", "q2"}) == []
    d = r["claims"]["P1:dollars"]
    assert d["kind"] == "estimated" and set(d["inputs"]) == {"P1:hours", "rate"}
    kinds = {c["kind"] for c in r["claims"].values()}
    assert kinds <= {"counted", "assumption", "estimated", "observed"}


def test_the_chain_check_catches_a_fake_quote_a_missing_input_and_a_cycle():
    claims = {"a": {"kind": "counted", "inputs": [], "evidence": ["fake"]},
              "b": {"kind": "estimated", "inputs": ["nope"], "evidence": []},
              "c": {"kind": "estimated", "inputs": ["d"], "evidence": []},
              "d": {"kind": "estimated", "inputs": ["c"], "evidence": []},
              "e": {"kind": "assumption", "inputs": [], "evidence": [], "reason": ""}}
    p = " | ".join(score.check_chains(claims, set()))
    assert "not a verified quote" in p and "not a claim" in p and "cycle" in p and "needs a reason" in p


def test_confidence_rule():
    assert score.confidence(6, 4, 3) == "high" and score.confidence(3, 2, 1) == "medium" and score.confidence(2, 2, 9) == "low"
