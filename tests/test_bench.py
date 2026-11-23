"""Benchmark scoring: labels must cite real text; scoring is deterministic and conservative."""
from pathlib import Path

import pytest

from src import bench, verify
from src.batch import make_batches

from conftest import BATCH_CFG, VERIFY_CFG, email

A = "Please confirm the nominations for the Katy hub by noon tomorrow."
B = "I will send the corrected meter list to scheduling on Friday."


@pytest.fixture
def setup(ingested):
    st = ingested({
        "mb/inbox/1": email(subject="Katy", body=A + "\r\n" + B + "\r\n", message_id="<1@x>"),
        "mb/inbox/2": email(subject="Lunch", body="Want to grab lunch at the usual place today?\r\n",
                            message_id="<2@x>", date="Tue, 5 Feb 2002 09:00:00 -0800"),
    })
    (b,) = make_batches(st, ["mb/inbox/1", "mb/inbox/2"], BATCH_CFG, "p1")
    return st, b


def labels():
    act = lambda i, q, state="requested": {"id": f"mb/inbox/1#{i}", "quote": q, "activity": "x", "actors": [],
                                            "action_state": state, "recurrence": "unclear", "recurrence_basis": "",
                                            "stall_signals": [], "stall_basis": "none"}
    return {"version": "bench-v1", "status": "draft", "messages": {
        "mb/inbox/1": {"outcome": "observations", "activities": [act(1, A), act(2, B, "requested")], "must_not": []},
        "mb/inbox/2": {"outcome": "not_business", "activities": [], "must_not": []}}}


def extraction(st, b, quotes, disp2="not_business", tasks=None):
    obs = []
    for i, (q, state, *sigs) in enumerate(quotes, 1):
        task = tasks[i - 1] if tasks else "t"
        obs.append({"obs_id": f"b001-o{i}", "msg_key": "mb/inbox/1", "task": task, "process_hint": "p", "actors": [],
                    "action_state": state, "trigger": "", "output": "", "stall_signals": sigs[0] if sigs else [],
                    # each signal's evidence is the observation's own quote, so these tests score signals only
                    "stall_evidence": [{"signal": s, "quote": q} for s in (sigs[0] if sigs else [])],
                    "quote": q, "confidence": "high", "limitations": ""})
    import json
    doc = {"batch_id": "b001", "attempt_id": "a", "observations": obs, "messages": [
        {"msg_key": "mb/inbox/1", "disposition": "observations" if obs else "ambiguous", "reason": "" if obs else "x",
         "work_items": len(obs)},
        {"msg_key": "mb/inbox/2", "disposition": disp2, "reason": "social", "work_items": 0}]}
    return verify.verify_batch(json.dumps(doc), b, st, VERIFY_CFG, attempt_id="a")


def test_label_quotes_must_verify_and_every_message_needs_a_label(setup):
    st, _ = setup
    bad = labels()
    bad["messages"]["mb/inbox/1"]["activities"][0]["quote"] = "Please confirm the nominations for Waha."
    del bad["messages"]["mb/inbox/2"]
    problems, spans = bench.check_labels(bad, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    assert any("does not verify" in p for p in problems) and any("no label for 1" in p for p in problems)
    assert list(spans) == ["mb/inbox/1#2"]


def test_label_states_are_the_extractor_schema_states(setup):
    from src import config, schema
    props = schema.load(f"{config.load()['extract']['schema']}.schema.json")["properties"]["observations"]["items"]["properties"]
    assert bench.STATES == tuple(props["action_state"]["enum"]) and "planned" in bench.STATES
    assert bench.STALLS == tuple(props["stall_signals"]["items"]["enum"])
    st, _ = setup
    lab = labels()
    lab["messages"]["mb/inbox/1"]["activities"][1]["action_state"] = "planned"      # announced for later
    assert bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)[0] == []


def test_changes_from_the_frozen_draft_must_be_recorded_review_decisions():
    import copy, hashlib, json
    raw = json.dumps(labels()).encode("utf-8")
    draft = json.loads(raw)
    lab = copy.deepcopy(draft)
    lab.update(draft_sha256=hashlib.sha256(raw).hexdigest(), review=[])
    assert bench.check_review(lab, raw) == []
    lab["messages"]["mb/inbox/1"]["activities"][1]["action_state"] = "done"          # a silent edit
    assert any("without a review entry" in p for p in bench.check_review(lab, raw))
    entry = {"when": "2026-11-21", "by": "user", "reason": "the list was sent", "msg_key": "mb/inbox/1",
             "before": draft["messages"]["mb/inbox/1"], "after": copy.deepcopy(lab["messages"]["mb/inbox/1"])}
    lab["review"].append(entry)
    assert bench.check_review(lab, raw) == []
    lab["review"].append(dict(entry))              # a second entry whose 'before' ignores the first
    assert any("not the label as it stood" in p for p in bench.check_review(lab, raw))
    lab["review"].pop()
    assert any("does not match draft_sha256" in p for p in bench.check_review(lab, raw + b" "))


def test_approval_needs_a_record_and_goes_stale_after_a_later_review_entry():
    lab = labels()
    assert bench.check_approval(lab) == []                                       # a draft needs no approval
    lab.update(status="approved", reviewed_by_user=True, review=[{"n": 1}],
               approval={"by": "user", "when": "2026-11-21", "user_words": "Fix and approve", "after_review_entry": 1})
    assert bench.check_approval(lab) == []
    lab["review"].append({"n": 2})                                               # a change after the approval
    assert any("stale" in p for p in bench.check_approval(lab))
    lab["review"].pop()
    del lab["approval"]["user_words"]
    assert any("approval record" in p for p in bench.check_approval(lab))
    assert any("not approved" in p for p in bench.check_approval({**labels(), "reviewed_by_user": True}))


def test_only_approved_labels_score_unless_the_score_is_marked_provisional():
    lab = labels()
    lab["status"] = "in_review"
    assert bench.scoring_refusal(lab, [], provisional=False)                  # in review: refused ...
    assert bench.scoring_refusal(lab, [], provisional=True) is None            # ... unless provisional
    assert bench.scoring_refusal(lab, ["a problem"], provisional=True)        # never with problems
    lab["status"] = "draft"
    assert bench.scoring_refusal(lab, [], provisional=True)                   # a draft never scores
    lab["status"] = "approved"
    assert bench.scoring_refusal(lab, [], provisional=False) is None


def test_blind_comparison_needs_one_result_per_blind_message_and_code_counts_it():
    lab = labels()
    lab["blind_comparison"] = {"result": {"mb/inbox/1": "disagree", "mb/inbox/2": "agree"}}
    lab["review"] = [{"n": 1, "source": "blind_comparison", "msg_key": "mb/inbox/1"}]
    problems, counts = bench.blind_result(lab, ["mb/inbox/1", "mb/inbox/2"])
    assert problems == [] and counts == {"disagree": 1, "agree": 1}
    problems, _ = bench.blind_result(lab, ["mb/inbox/1", "mb/inbox/2", "mb/inbox/3"])   # a blind message left out
    assert any("one result for each blind message" in p for p in problems)
    lab["review"][0]["msg_key"] = "mb/inbox/2"                    # a blind-comparison change on an 'agree'
    assert any("recorded as 'agree'" in p for p in bench.blind_result(lab, ["mb/inbox/1", "mb/inbox/2"])[0])


def test_recall_counts_only_quotes_that_overlap_the_labelled_span(setup):
    st, b = setup
    lab = labels()
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    res = extraction(st, b, [(A, "requested")])                   # finds A, misses B
    s = bench.score(lab, spans, [res], st, VERIFY_CFG)
    assert (s["activities"], s["activities_found"], s["recall"]) == (2, 1, 0.5)
    assert s["action_state_agreement"] == "1/1" and s["unmatched_for_review"] == []
    assert s["missed"] == ["mb/inbox/1#2"] and s["disagreements"] == []          # named, not just counted
    s = bench.score(lab, spans, [extraction(st, b, [(B, "requested")])], st, VERIFY_CFG)
    assert s["missed"] == ["mb/inbox/1#1"]                     # a quote of B credits #2, not the first activity


def test_an_activity_may_have_several_spans_but_two_activities_never_share_text(setup):
    st, b = setup
    lab = labels()
    acts = lab["messages"]["mb/inbox/1"]["activities"]
    acts[0]["other_quotes"] = [B]                     # A and B describe one piece of work ...
    del acts[1]                                       # ... and B is no longer its own activity
    problems, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    assert problems == [] and len(spans["mb/inbox/1#1"]) == 2
    s = bench.score(lab, spans, [extraction(st, b, [(B, "requested")])], st, VERIFY_CFG)
    assert s["activities_found"] == 1 and s["unmatched_for_review"] == []       # a quote of B credits #1
    lab = labels()
    lab["messages"]["mb/inbox/1"]["activities"][1]["other_quotes"] = [A]       # #2 claims #1's sentence too
    problems, _ = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    assert any("spans overlap" in p for p in problems)


def test_score_v2_measures_complete_messages_precision_duplicates_and_confusion(setup):
    st, b = setup
    lab = labels()
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    s = bench.score(lab, spans, [extraction(st, b, [(A, "requested"), (A, "done")])], st, VERIFY_CFG)
    assert (s["complete_messages"], s["incomplete_messages"]) == ("0/1", ["mb/inbox/1"])      # #2 was missed
    assert (s["messages_fully_right"], s["not_fully_right"]) == ("1/2", ["mb/inbox/1"])
    assert s["precision"] == "2/2"
    assert s["duplicate_hits"] == [{"id": "mb/inbox/1#1", "obs_ids": ["b001-o1", "b001-o2"]}]
    assert s["overlapping_observation_quotes"] == [["b001-o1", "b001-o2"]]
    assert s["state_confusion"] == {"requested -> requested": 1} and s["stall_confusion"] == {"none -> none": 1}
    s = bench.score(lab, spans, [extraction(st, b, [(A + " " + B, "requested")])], st, VERIFY_CFG)
    assert s["observations_spanning_activities"] == [{"obs_id": "b001-o1", "ids": ["mb/inbox/1#1", "mb/inbox/1#2"]}]
    # score-v4: the spanning quote is listed, but credits only one activity, so the message stays incomplete
    assert s["activities_found"] == 1 and (s["complete_messages"], s["messages_fully_right"]) == ("0/1", "1/2")


def test_one_quote_credits_only_the_activity_it_overlaps_most(setup):
    st, b = setup
    lab = labels()
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    # a quote with all of B and only the last words of A: credited to #2 (B) alone
    s = bench.score(lab, spans, [extraction(st, b, [(A[-30:] + " " + B, "requested")])], st, VERIFY_CFG)
    assert s["activities_found"] == 1 and s["missed"] == ["mb/inbox/1#1"]
    # two observations on one activity: both still credit it (a duplicate), neither is lost
    s = bench.score(lab, spans, [extraction(st, b, [(A, "requested"), (A, "done")])], st, VERIFY_CFG)
    assert s["duplicate_hits"] == [{"id": "mb/inbox/1#1", "obs_ids": ["b001-o1", "b001-o2"]}]


def test_a_duplicate_is_judged_on_the_copy_with_the_most_evidence_never_on_order_or_answer(setup):
    st, b = setup
    lab = labels()
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    full, frag = (A, "done"), (A[:32], "requested")       # the full sentence has the WRONG state
    for quotes in ([frag, full], [full, frag]):           # same result whichever copy comes first
        s = bench.score(lab, spans, [extraction(st, b, quotes)], st, VERIFY_CFG)
        judged = s["credited_obs"]["mb/inbox/1#1"]
        assert judged == f"b001-o{quotes.index(full) + 1}"            # most overlap with the label span
        assert s["action_state_agreement"] == "0/1"                   # not the copy that happens to be right
        assert s["duplicate_observations"] == [{"obs_id": f"b001-o{quotes.index(frag) + 1}",
                                                "activity": "mb/inbox/1#1", "credited_obs_id": judged}]
        assert s["activities_found"] == 1 and s["precision_one_to_one"] == "1/2" and s["precision"] == "2/2"


def test_tied_duplicates_are_broken_by_source_position_not_output_order(setup):
    st, b = setup
    lab = labels()
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    early, late = (A[:30], "requested"), (A[3:33], "done")     # same overlap (30 chars), same task words
    for quotes in ([early, late], [late, early]):
        s = bench.score(lab, spans, [extraction(st, b, quotes)], st, VERIFY_CFG)
        assert s["credited_obs"]["mb/inbox/1#1"] == f"b001-o{quotes.index(early) + 1}"


def test_a_broad_quote_credits_one_activity_the_one_its_task_describes(setup):
    st, b = setup
    lab = labels()
    acts = lab["messages"]["mb/inbox/1"]["activities"]
    acts[0]["activity"], acts[1]["activity"] = "confirm the Katy hub nominations", "send the corrected meter list"
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    broad = A[-30:] + " " + B                              # overlaps B far more than A
    s = bench.score(lab, spans, [extraction(st, b, [(broad, "requested")], tasks=["confirm Katy nominations"])],
                    st, VERIFY_CFG)
    assert s["credited_obs"] == {"mb/inbox/1#1": "b001-o1"} and s["missed"] == ["mb/inbox/1#2"]
    assert s["matched_by_meaning"] == [{"obs_id": "b001-o1", "credited": "mb/inbox/1#1", "most_overlap": "mb/inbox/1#2"}]
    # with no words in common, the evidence decides, and it still credits only one activity
    s = bench.score(lab, spans, [extraction(st, b, [(broad, "requested")])], st, VERIFY_CFG)
    assert s["credited_obs"] == {"mb/inbox/1#2": "b001-o1"} and s["matched_by_meaning"] == []
    assert bench.meaning("Confirms the nominations", "confirm nomination") == 1.0


def test_stall_signals_are_scored_as_sets_with_per_signal_counts(setup):
    st, b = setup
    lab = labels()
    lab["messages"]["mb/inbox/1"]["activities"][0]["stall_signals"] = ["follow_up", "waiting"]
    problems, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    assert problems == []
    s = bench.score(lab, spans, [extraction(st, b, [(A, "requested", ["waiting"]), (B, "requested", ["rework"])])],
                    st, VERIFY_CFG)
    assert s["stall_agreement"] == "0/2" and s["stall_any_agreement"] == "1/2"     # #1 partly right, #2 invented
    assert s["stall_signals"]["waiting"] == {"labelled": 1, "extracted": 1, "both": 1}
    assert s["stall_signals"]["follow_up"] == {"labelled": 1, "extracted": 0, "both": 0}
    assert s["stall_signals"]["rework"] == {"labelled": 0, "extracted": 1, "both": 0}
    assert s["stall_confusion"]["follow_up+waiting -> waiting"] == 1
    # labels must be lists of known signals, without repeats, and the old single field is gone
    for bad in (["waiting", "waiting"], ["late"], "waiting"):
        lab["messages"]["mb/inbox/1"]["activities"][0]["stall_signals"] = bad
        assert any("stall_signals" in p for p in bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)[0])
    # output from before extract-v6 (one stall_signal) is read as a set of at most one
    assert bench.signals_of({"stall_signal": "none"}) == frozenset()
    assert bench.signals_of({"stall_signal": "waiting"}) == frozenset(["waiting"])


def test_outcomes_disagreements_and_unmatched_observations_are_reported(setup):
    st, b = setup
    lab = labels()
    lab["messages"]["mb/inbox/1"]["activities"] = lab["messages"]["mb/inbox/1"]["activities"][:1]
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    res = extraction(st, b, [(A, "done"), (B, "requested")], disp2="business_no_process_signal")
    s = bench.score(lab, spans, [res], st, VERIFY_CFG)
    assert s["outcome_exact"] == 1 and s["outcome_observation_vs_not"] == 2
    assert s["confusion"]["not_business -> business_no_process_signal"] == 1
    assert s["action_state_agreement"] == "0/1"
    assert s["disagreements"] == [{"id": "mb/inbox/1#1", "obs_id": "b001-o1", "label": "requested / none",
                                   "extracted": "done / none"}]
    assert [u["quote"] for u in s["unmatched_for_review"]] == [B]       # listed for the user, not auto-scored
    assert all(s["invariants"].values())


def test_stall_classification_scores_claims_and_published_signals_are_counted_apart(setup):
    st, b = setup
    lab = labels()
    lab["messages"]["mb/inbox/1"]["activities"][0]["stall_signals"] = ["waiting"]
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    res = extraction(st, b, [(A, "requested", ["waiting"])])
    (o,) = res["observations"]
    o["stall_signals"] = []                     # as the verifier leaves it when the signal is not grounded
    s = bench.score(lab, spans, [res], st, VERIFY_CFG)
    assert s["stall_signals"]["waiting"] == {"labelled": 1, "extracted": 1, "both": 1}        # the claim
    assert s["published_stall_signals"]["waiting"] == {"labelled": 1, "published": 0, "both": 0}
    assert s["stall_agreement"] == "1/1"


def test_matches_whose_task_shares_few_words_are_listed_for_review_and_still_scored(setup):
    st, b = setup
    lab = labels()
    acts = lab["messages"]["mb/inbox/1"]["activities"]
    acts[0]["activity"], acts[1]["activity"] = "confirm the Katy hub nominations", "send the corrected meter list"
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    res = extraction(st, b, [(A, "requested"), (B, "requested")], tasks=["notify scheduling", "send corrected meter list"])
    s = bench.score(lab, spans, [res], st, VERIFY_CFG)
    assert s["activities_found"] == 2                                       # the score does not change ...
    assert [m["activity"] for m in s["low_meaning_matches"]] == ["mb/inbox/1#1"]   # ... the doubtful one is listed


def test_a_person_judged_wrong_match_earns_no_credit_and_unjudged_doubts_are_counted(setup):
    st, b = setup
    lab = labels()
    acts = lab["messages"]["mb/inbox/1"]["activities"]
    acts[0]["activity"], acts[1]["activity"] = "confirm the Katy hub nominations", "send the corrected meter list"
    _, spans = bench.check_labels(lab, st, ["mb/inbox/1", "mb/inbox/2"], VERIFY_CFG)
    res = extraction(st, b, [(A, "requested"), (B, "requested")], tasks=["notify scheduling", "send corrected meter list"])
    s = bench.score(lab, spans, [res], st, VERIFY_CFG)
    assert s["activities_found"] == 2 and s["unadjudicated_low_meaning"] == 1        # doubtful, not yet judged
    o1 = res["observations"][0]
    adj = {"n": 1, "activity": "mb/inbox/1#1", "msg_key": "mb/inbox/1", "task": o1["task"], "quote": o1["quote"],
           "reason": "a different piece of work"}
    s = bench.score(lab, spans, [res], st, VERIFY_CFG, [{**adj, "decision": "no_credit"}])
    assert s["activities_found"] == 1 and s["missed"] == ["mb/inbox/1#1"] and s["unadjudicated_low_meaning"] == 0
    assert [x["obs_id"] for x in s["adjudicated_no_credit"]] == ["b001-o1"] and s["precision_one_to_one"] == "1/2"
    s = bench.score(lab, spans, [res], st, VERIFY_CFG, [{**adj, "decision": "credit"}])
    assert s["activities_found"] == 2 and s["unadjudicated_low_meaning"] == 0
    assert s["low_meaning_matches"][0]["adjudication"] == 1
    # a judgment is tied to the exact task and quote: a reworded observation is a new case
    s = bench.score(lab, spans, [res], st, VERIFY_CFG, [{**adj, "task": "something else", "decision": "no_credit"}])
    assert s["activities_found"] == 2 and s["unadjudicated_low_meaning"] == 1


def test_a_score_says_whether_it_was_made_with_the_frozen_benchmark(tmp_path, monkeypatch):
    import hashlib, json
    lab = tmp_path / "labels.json"
    lab.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bench, "FREEZE", tmp_path / "freeze.json")
    assert bench.freeze_status(lab)["matches"] is False                               # no freeze record
    fz = {"name": "t", "labels_sha256": hashlib.sha256(b"{}").hexdigest(),
          "rules_sha256": hashlib.sha256((bench.ROOT / "eval" / "benchmark" / "rules.json").read_bytes()).hexdigest(),
          "bench_py_sha256": hashlib.sha256(Path(bench.__file__).read_bytes()).hexdigest(),
          "score_version": bench.SCORE_VERSION}
    (tmp_path / "freeze.json").write_text(json.dumps(fz), encoding="utf-8")
    assert bench.freeze_status(lab) == {"freeze": "t", "matches": True, "differs": []}
    lab.write_text('{"x": 1}', encoding="utf-8")                                        # a label edit
    assert bench.freeze_status(lab)["differs"] == ["labels_sha256"]
