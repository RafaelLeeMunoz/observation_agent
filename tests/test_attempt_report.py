"""The attempt report (user, 24 Nov 2026): reason, runtime and usage per attempt, and the fourth-attempt question. Offline, no model."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("attempt_report", ROOT / "tools" / "attempt_report.py")
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)


def row(stage, inp, n, status, errors=(), seconds=10.0, tokens=1000, **kw):
    r = {"stage": stage, "attempt": f"{inp}-abcd1234-{n}", "status": status, "errors": list(errors), "seconds": seconds,
         "usage": {"subagent_tokens": tokens}, "at": f"2026-11-24T00:00:{n:02d}+00:00", **kw}
    r["batch" if stage in ("extract", "ground") else "input"] = inp
    return r


ROWS = [
    row("synth", "s011", 1, "failed", ["P12: basis_obs_ids must be observations of this process"], 100, 400_000),
    row("synth", "s011", 2, "failed", ["P22: observation b071-o10 is already in P10"], 110, 410_000),
    row("synth", "s011", 3, "failed", ["P3: unknown observation ids ['b041-o31']"], 120, 420_000),
    row("synth", "s011", 4, "verified", [], 130, 430_000),
    row("synth", "s012", 1, "failed", ["P7: observation b050-o2 is already in P9"], 90, 300_000),
    row("synth", "s012", 2, "verified", [], 95, 310_000),
    row("synth", "s013", 1, "failed", ["P1: observation b001-o1 is already in P2", "coverage: only 3 of 100 observations count"], 80, 200_000),
    row("synth", "s013", 2, "failed", ["P4: unknown observation ids ['b002-o9']"], 85, 210_000),
    row("synth", "s013", 3, "failed", ["P4: unknown observation ids ['b002-o8']"], 85, 210_000),
    row("synth", "s013", 4, "failed", ["P4: unknown observation ids ['b002-o7']"], 85, 210_000),
    row("merge", "m001", 1, "verified", [], 40, 100_000),
    row("extract", "b001", 1, "verified", [], 70, 50_000),
    row("extract", "b002", 1, "terminated", [], 300, 0, kind="timeout"),
    row("extract", "b002", 2, "verified", [], 75, 52_000),
    {"stage": "synth", "input": "s011", "status": "cache_hit"},                   # no attempt: not an attempt
    {"stage": "done", "status": "ok"},
]


def test_attempts_are_counted_by_number_with_reason_seconds_and_tokens():
    rep = ar.build(ROWS)
    s = rep["stages"]["synth"]
    assert s["attempts"] == 10 and s["inputs"] == 3 and s["verified_attempts"] == 2 and s["failed_attempts"] == 8
    assert s["by_attempt_number"] == {"1": {"tried": 3, "verified": 0, "failed": 3}, "2": {"tried": 3, "verified": 1, "failed": 2},
                                      "3": {"tried": 2, "verified": 0, "failed": 2}, "4": {"tried": 2, "verified": 1, "failed": 1}}
    assert s["first_attempt_failure_rate"] == 1.0 and s["inputs_needing_more_than_one_attempt"] == 3
    assert s["seconds_failed"] == 100 + 110 + 120 + 90 + 80 + 85 + 85 + 85 and s["tokens_failed"] == 400_000 + 410_000 + 420_000 + 300_000 + 200_000 + 3 * 210_000
    assert s["inputs_never_verified"] == ["s013"]
    assert s["inputs_detail"]["s011"] == {"attempts": 4, "outcome": "verified", "verified_on_attempt": 4}


def test_the_fourth_attempt_question_is_answered_per_input_with_its_reasons():
    f = ar.build(ROWS)["stages"]["synth"]["fourth_attempt"]
    assert f["inputs_that_needed_one"] == 2 and f["resolved_the_errors"] == 1 and f["still_failing"] == 1
    bad = next(x for x in f["inputs"] if x["input"] == "s013")
    assert bad["status"] == "failed" and any("unknown observation ids" in r for r in bad["reasons"])
    assert ar.build(ROWS)["stages"]["merge"]["fourth_attempt"]["inputs_that_needed_one"] == 0


def test_reasons_group_the_same_slip_whatever_the_ids_and_numbers():
    assert ar.kind_of("P22: observation b071-o10 is already in P10") == ar.kind_of("P7: observation b050-o2 is already in P9")
    assert ar.kind_of("coverage: only 83 of 2433") == "coverage floor"
    assert ar.kind_of("positional coverage: the last third of the input (observations o200 to o299) has only 0 of 100") == "positional coverage: last third"
    reasons = {x["kind"]: x["attempts"] for x in ar.build(ROWS)["stages"]["synth"]["failure_reasons"]}
    assert max(reasons.values()) >= 3 and "coverage floor" in reasons


def test_other_stages_and_non_attempt_records_are_handled(tmp_path):
    rep = ar.build(ROWS)
    assert rep["stages"]["extract"]["failed_by_status"] == {"terminated": 1} and rep["stages"]["extract"]["inputs_needing_more_than_one_attempt"] == 1
    assert "fourth_attempt" not in rep["stages"]["extract"] and len(rep["attempts"]) == 14
    log = tmp_path / "output" / "x" / "logs" / "runlog.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(json.dumps(r) for r in ROWS) + "\nnot json\n", encoding="utf-8")
    assert ar.main(["--root", str(tmp_path), "--slice", "x"]) == 0
    saved = json.loads((log.parent / "attempts.json").read_text(encoding="utf-8"))
    assert saved["stages"]["synth"]["fourth_attempt"]["resolved_the_errors"] == 1
    assert "| synth |" in ar.markdown(rep)
