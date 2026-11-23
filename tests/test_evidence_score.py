"""Evidence-support scoring of published signals, on a synthetic yardstick (no model, no real run files)."""
import json

import pytest

from src import bench, evidence_bench, evidence_score, verify

from conftest import VERIFY_CFG, email
from test_evidence_bench import A, B, INTRO, entry, labels

OTHER = "Our gas control desk confirmed the Waha pipeline volumes this morning."


@pytest.fixture
def y(ingested):
    st = ingested({"mb/inbox/1": email(subject="Katy", body=INTRO + "\r\n" + A + "\r\n" + B + "\r\n" + OTHER + "\r\n",
                                       message_id="<1@x>")})
    lab = labels()
    lab["messages"]["mb/inbox/1"]["activities"][1]["stall_signals"] = []          # #2 carries no signal
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1"], VERIFY_CFG)
    ev, neg, problems = evidence_bench.resolve(
        {"entries": [entry(1, "follow_up", [INTRO], basis="same_message_context", note="the opening line chases it")]},
        lab, spans, st, VERIFY_CFG,
        {"claims": [{"id": "mb/inbox/1#2", "signal": "waiting", "why": "a plan, not a wait", "rule": "r",
                     "spans": [{"quote": B, "role": "r"}]}]})
    assert not problems, problems
    return {"st": st, "vcfg": VERIFY_CFG, "labels": lab, "spans": spans, "ev": ev, "neg": neg, "adj": [],
            "freeze": {"test": True}}


def o(y, n, quote, published=(), claimed=None, evidence=None, outcomes=None):
    m = y["st"].messages["mb/inbox/1"]
    chk = verify.check_quote(m, quote, 25)
    assert chk.pop("ok")
    claimed = list(claimed if claimed is not None else published)
    rec = {"obs_id": f"b001-o{n}", "msg_key": m.key, "task": "x", "action_state": "requested", "quote": quote, **chk,
           "stall_signals": list(published), "stall_signals_claimed": claimed, "stall_evidence": [],
           "published_evidence": [], "signal_outcomes": outcomes or []}
    for sig, q in (evidence or []):
        c = verify.check_quote(m, q, 25)
        rec["published_evidence"].append({"signal": sig, "msg_key": m.key, "start": c["start"], "end": c["end"],
                                          "quote_raw": c["quote_raw"]})
    return rec


def run(*observations):
    keys = ["mb/inbox/1"]
    return {"status": "verified", "messages": [{"msg_key": k, "disposition": "observations", "reason": "", "work_items": 1}
                                               for k in keys], "observations": list(observations), "rejected": []}


def row(res, aid="mb/inbox/1#1", sig="follow_up"):
    return next(r for r in res["rows"] if r["id"] == aid and r["signal"] == sig)


def test_a_published_signal_whose_evidence_is_the_approved_passage_is_supported(y):
    res = evidence_score.evaluate(run(o(y, 1, A, ["follow_up"], evidence=[("follow_up", INTRO)])), "I", y)
    r = row(res)
    assert r["category"] == "published_supported" and r["supports_activity"] and r["context_dependent"]
    assert res["end_to_end_published_signal_recall"] == "1/1" and res["citations"]["all_valid"]


def test_right_signal_with_evidence_that_is_not_an_approved_passage_is_kept_apart(y):
    res = evidence_score.evaluate(run(o(y, 1, A, ["follow_up"], evidence=[("follow_up", A)])), "H", y)
    assert row(res)["category"] == "published_other_passage"
    assert res["end_to_end_published_signal_recall"] == "0/1" and res["published_correct_any_evidence"] == "1/1"


@pytest.mark.parametrize("outcome, category", [("unsupported", "correct_unsupported"), ("rejected", "correct_rejected"),
                                               ("unresolved", "correct_unresolved"), ("not_grounded", "correct_not_grounded")])
def test_a_correct_claim_that_was_not_published_says_why(y, outcome, category):
    obs = o(y, 1, A, [], claimed=["follow_up"], outcomes=[{"signal": "follow_up", "outcome": outcome}])
    assert row(evidence_score.evaluate(run(obs), "I", y))["category"] == category


def test_missed_signals_and_missed_activities_count_against_recall_whatever_the_access(y):
    assert row(evidence_score.evaluate(run(o(y, 1, A)), "H", y))["category"] == "signal_missed"
    res = evidence_score.evaluate(run(o(y, 2, B)), "H", y)
    assert row(res)["category"] == "activity_missed" and res["end_to_end_published_signal_recall"] == "0/1"


def test_a_published_negative_claim_and_an_unlabelled_publication_are_counted(y):
    res = evidence_score.evaluate(run(o(y, 1, A, ["follow_up"], evidence=[("follow_up", INTRO)]),
                                      o(y, 2, B, ["waiting"], evidence=[("waiting", B)]),
                                      o(y, 3, OTHER, ["correction"], evidence=[("correction", OTHER)])), "I", y)
    assert [(f["id"], f["signal"], f["negative_claim"]) for f in res["published_false"]] == [("mb/inbox/1#2", "waiting", True)]
    assert len(res["negative_claims_published"]) == 1
    assert [u["obs_id"] for u in res["published_on_unlabelled_observations"]] == ["b001-o3"]
    assert res["published_precision"].startswith("1/3")


def test_a_claim_withheld_correctly_is_recorded(y):
    obs = o(y, 2, B, [], claimed=["waiting"], outcomes=[{"signal": "waiting", "outcome": "unsupported"}])
    res = evidence_score.evaluate(run(obs), "I", y)
    assert res["withheld_correctly"] == [{"id": "mb/inbox/1#2", "obs_id": "b001-o2", "signal": "waiting",
                                          "outcome": "unsupported", "negative_claim": True}]
    assert res["negative_claims"][0]["claimed"] and not res["negative_claims"][0]["published"]


def test_evidence_not_literally_in_the_raw_file_fails_the_citation_check(y):
    obs = o(y, 1, A, ["follow_up"], evidence=[("follow_up", INTRO)])
    obs["published_evidence"][0]["quote_raw"] = "Following up on the open nomination items from LAST week's call."
    bare = o(y, 2, B, ["waiting"])                                                  # published with no evidence at all
    res = evidence_score.evaluate(run(obs, bare), "I", y)
    assert not res["citations"]["all_valid"] and len(res["citations"]["invalid"]) == 2


def test_a_duplicate_observation_that_publishes_counts_for_its_activity(y):
    first = o(y, 1, A, [])
    dup = o(y, 2, "Please confirm the nominations for the Katy hub", ["follow_up"], evidence=[("follow_up", INTRO)])
    res = evidence_score.evaluate(run(first, dup), "I", y)
    assert row(res)["category"] == "published_supported" and sorted(row(res)["obs_ids"]) == ["b001-o1", "b001-o2"]


def test_accessibility_follows_the_needed_passage_and_the_architecture():
    cross = {"basis": "cross_message_context", "spans": [{"scope": "activity", "visibility": "batch"},
                                                        {"scope": "other_message", "visibility": "linked_original"}]}
    own = {"basis": "own_text", "spans": [{"scope": "activity", "visibility": "batch"}]}
    assert not evidence_score.accessible(cross, "H") and evidence_score.accessible(cross, "I")
    assert evidence_score.accessible(own, "H") and evidence_score.accessible(own, "J")
    assert not evidence_score.accessible(own, "clean-C")


def test_the_scorer_matches_activities_with_the_frozen_benchmark_scorer(y):
    r = run(o(y, 1, A, ["follow_up"], evidence=[("follow_up", INTRO)]))
    res = evidence_score.evaluate(r, "I", y)
    sc = bench.score(y["labels"], y["spans"], [r], y["st"], y["vcfg"], [])
    assert res["extraction"]["activities_found"] == sc["activities_found"] == 1
    assert json.dumps(res)                                                          # serialisable as written


def test_the_counting_units_are_explicit_claims_versus_activity_signal_pairs(y):
    """Two observations of one activity publishing the same signal are 2 claims but 1 activity-signal pair;
    a signal on an observation matching no label is counted in the denominator and never as correct."""
    a = o(y, 1, A, ["follow_up"], evidence=[("follow_up", INTRO)])
    dup = o(y, 2, "Please confirm the nominations for the Katy hub", ["follow_up"], evidence=[("follow_up", INTRO)])
    other = o(y, 3, OTHER, ["correction"], evidence=[("correction", OTHER)])
    res = evidence_score.evaluate(run(a, dup, other), "I", y)
    assert res["published_counts"] == {"claims": 3, "activity_signal_pairs": 1, "on_unlabelled_observations": 1,
                                       "counted": 2, "correct": 1, "false_on_labelled_activities": 0}
    assert res["published_precision"].startswith("1/2 (1 activity-signal pairs + 1 on observations")
