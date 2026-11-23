"""Synthesis output checks and artifact citation checks (code only, no model)."""
import json

import pytest

from src import act, synth

INP = {"input_id": "s001", "obs_ids": ["b001-o1", "b001-o2", "b002-o1", "b002-o2"],
       "threads": {"b001-o1": "t1", "b001-o2": "t2", "b002-o1": "t3", "b002-o2": "t3"}}


def proc(pid="P1", obs=("b001-o1", "b001-o2"), low=10, base=20, high=30, rationale="looking up figures", **kw):
    p = {"process_id": pid, "name": f"Work {pid[-1]}x".replace("1", "one").replace("2", "two"),
         "description": "Schedulers confirm volumes.", "trigger": "a new month", "output": "confirmed volumes",
         "roles": ["scheduler"], "obs_ids": list(obs),
         "automation": {"idea": "a standard request", "artifact_type": "template", "why": "rewritten by hand"},
         "minutes_per_event": {"low": low, "base": base, "high": high, "rationale": rationale, "basis_obs_ids": [obs[0]]}}
    p.update(kw)
    return p


def out(*procs, attempt="s001-a-1", input_id="s001"):
    return json.dumps({"input_id": input_id, "attempt_id": attempt, "processes": list(procs)})


def test_a_valid_synthesis_assigns_each_observation_once_and_counts_threads():
    r = synth.check(out(proc()), INP, "s001-a-1")
    assert r["status"] == "verified" and r["assignment"] == {"b001-o1": "P1", "b001-o2": "P1"}
    assert r["processes"][0]["threads_seen"] == 2 and r["unassigned"] == ["b002-o1", "b002-o2"]


@pytest.mark.parametrize("bad, why", [
    (out(proc(obs=("b001-o1", "b009-o9"))), "unknown observation"),
    (out(proc(), proc("P2", obs=("b001-o1", "b002-o1"))), "already in P1"),
    (out(proc(obs=("b001-o1",))), "at least two observations"),
    (out(proc(low=30, base=20, high=40)), "low <= base <= high"),
    (out(proc(high=600)), "1-480"),
    (out(proc(rationale=" ")), "needs a reason"),
    (out(proc(), attempt="s001-old-1"), "stale or foreign"),
    ('{"input_id": "s001"', "invalid JSON"),
])
def test_bad_synthesis_output_fails(bad, why):
    r = synth.check(bad, INP, "s001-a-1")
    assert r["status"] == "failed" and any(why in e for e in r["errors"]), r["errors"]


def test_digits_in_model_prose_are_flagged_not_trusted():
    r = synth.check(out(proc(description="About 40 requests a month.")), INP, "s001-a-1")
    assert r["status"] == "verified" and {"kind": "numbers_lint", "process_id": "P1", "field": "description"} in r["flags"]


# --- artifacts ---

PK = {"packet_id": "a001", "process_id": "P1", "sha256": "x" * 64, "quotes": [
    {"qid": "Q1", "msg_key": "m/1", "text": "a"}, {"qid": "Q2", "msg_key": "m/2", "text": "b"},
    {"qid": "Q3", "msg_key": "m/2", "text": "c"}]}
CFG = {"min_citations": 3, "min_messages": 2}


def test_a_draft_with_enough_valid_citations_passes():
    r = act.check("# Checklist\n\nDo it [Q1]. Check [Q2][Q3].", PK, CFG)
    assert r["status"] == "verified" and r["cited"] == ["Q1", "Q2", "Q3"]


@pytest.mark.parametrize("draft, why", [
    ("No title [Q1][Q2][Q3]", "title"),
    ("# T\n[Q1][Q2][Q9]", "not in the packet"),
    ("# T\n[Q2][Q3]", "at least 3"),
    ("# T\n[Q1][Q2][Q3]\nWe have sent this to the desk.", "sent or posted"),
])
def test_bad_drafts_fail(draft, why):
    r = act.check(draft, PK, CFG)
    assert r["status"] == "failed" and any(why in e for e in r["errors"]), r["errors"]


def test_citations_from_one_email_only_fail():
    pk = {**PK, "quotes": [dict(q, msg_key="m/1") for q in PK["quotes"]]}
    assert "different emails" in " ".join(act.check("# T\n[Q1][Q2][Q3]", pk, CFG)["errors"])


def test_figures_in_the_draft_are_flagged_for_checking():
    r = act.check("# T\nConfirm by 10:00 on the 25th [Q1][Q2][Q3].", PK, CFG)
    assert r["status"] == "verified" and r["flags"][0]["kind"] == "numbers_lint"


def test_list_numbering_is_not_a_figure_for_the_numbers_lint():
    r = act.check("# T\n## 1. Check\n1. Look it up [Q1][Q2][Q3].\n2. Ask.", PK, CFG)
    assert r["status"] == "verified" and not r["flags"]
