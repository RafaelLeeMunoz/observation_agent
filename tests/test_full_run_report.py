"""M5: the full-run timing report separates stages, shows real overlap, counts retries and keeps the audit trail (no model)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("full_run_report", ROOT / "tools" / "full_run_report.py")
rep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rep)


def at(s):
    return f"2026-11-24T00:{s // 60:02d}:{s % 60:02d}+00:00"


def usage(a, b):
    return {"started_at_utc": at(a).replace("+00:00", "Z"), "finished_at_utc": at(b).replace("+00:00", "Z"), "kind": "ok"}


LOG = [
    {"at": at(0), "stage": "start", "workers": 24},
    {"at": at(1), "stage": "batch", "status": "ok"},
    {"at": at(100), "stage": "extract", "batch": "b001", "attempt": "b001-x-1", "status": "failed", "usage": usage(2, 100)},
    {"at": at(110), "stage": "extract", "batch": "b002", "attempt": "b002-x-1", "status": "verified", "usage": usage(2, 110)},
    {"at": at(120), "stage": "ground", "batch": "b002", "attempt": "g2", "status": "verified", "usage": usage(110, 120)},
    {"at": at(210), "stage": "extract", "batch": "b001", "attempt": "b001-x-2", "status": "verified", "usage": usage(100, 210)},
    {"at": at(230), "stage": "ground", "batch": "b001", "attempt": "g1", "status": "verified", "usage": usage(210, 230)},
    {"at": at(231), "stage": "synth", "status": "split", "parts": 2, "mailboxes": [["farmer-d"], ["giron-d"]]},
    {"at": at(300), "stage": "synth", "input": "s001", "part": [1, 2], "attempt": "s001-1", "status": "verified", "usage": usage(231, 300)},
    {"at": at(280), "stage": "synth", "input": "s002", "part": [2, 2], "attempt": "s002-1", "status": "verified", "usage": usage(231, 280)},
    {"at": at(320), "stage": "merge", "attempt": "m001-1", "status": "verified", "usage": usage(300, 320)},
    {"at": at(322), "stage": "score", "status": "ok"},
    {"at": at(380), "stage": "act", "packet": "a001", "status": "verified", "usage": usage(322, 380)},
    {"at": at(381), "stage": "done", "status": "ok", "seconds": 381.0},
]


def test_every_session_and_code_stage_is_an_interval_and_stages_are_separated():
    iv = rep.intervals(LOG)
    t0 = rep.ts(LOG[0]["at"])
    s = rep.stage_summary(iv, t0)
    assert set(s) == {"batch", "extract", "ground", "synth", "merge", "score", "act"}
    assert s["extract"]["sessions"] == 3 and s["extract"]["wall_s"] == 208.0 and s["extract"]["summed_s"] == 98 + 108 + 110
    assert s["extract"]["not_ok_attempts"] == 1 and s["extract"]["not_ok_seconds"] == 98.0       # the failed attempt is a retry cost
    assert s["synth"]["wall_s"] == 69.0 and s["synth"]["summed_s"] == 69 + 49                     # two parts in parallel: wall < sum
    assert s["merge"]["wall_s"] == 20.0 and s["score"]["sessions"] == 1 and s["act"]["wall_s"] == 58.0


def test_overlap_shows_what_ran_at_the_same_time_not_only_summed_durations():
    iv = rep.intervals(LOG)
    ov = {tuple(o["stages"]): o["seconds"] for o in rep.overlap(iv, rep.ts(LOG[0]["at"]))}
    assert ov[("extract", "ground")] == 10.0 and ov[("extract",)] == 198.0 and ov[("ground",)] == 20.0
    assert ov[("synth",)] == 69.0 and ov[("batch",)] == 1.0 and ov[("merge",)] == 20.0 and ov[("act",)] == 58.0
    assert sum(ov.values()) == 380.0                                                              # the sweep covers the run exactly once


def test_synthesis_is_reported_per_mailbox():
    iv = rep.intervals(LOG)
    by = {tuple(x["mailboxes"]): x["seconds"] for x in rep.synthesis_by_mailbox(LOG, iv)}
    assert by == {("farmer-d",): 69.0, ("giron-d",): 49.0}


def test_the_audit_trail_keeps_every_rejection_and_unresolved_signal_and_names_the_credit_report_issue(tmp_path):
    (tmp_path / "data" / "extract" / "all" / "logs").mkdir(parents=True)
    rows = [{"batch_id": "b036", "attempt_id": "a", "obs_id": "b036-o1", "msg_key": "m/1", "quote": "Subject: Credit Report--2/2/01",
             "reason": "ambiguous: occurs in 2 different regions"},
            {"batch_id": "b060", "attempt_id": "a", "obs_id": "b060-o2", "msg_key": "m/2", "quote": "Take these days off too.",
             "reason": "too short (under 25 characters)"}]
    (tmp_path / "data" / "extract" / "all" / "logs" / "rejections.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    g = tmp_path / "data" / "runs" / "all" / "ground"
    g.mkdir(parents=True)
    (g / "b036.json").write_text(json.dumps({"observations": [{"obs_id": "b036-o3", "msg_key": "m/3", "signal_outcomes": [
        {"signal": "waiting", "outcome": "unresolved", "reason": "unsure", "grounded": True},
        {"signal": "correction", "outcome": "published", "reason": "r", "grounded": True},
        {"signal": "rework", "outcome": "unsupported", "reason": "no", "grounded": True}]}]}), encoding="utf-8")
    a = rep.audit_trail(tmp_path, "all")
    assert a["rejected_quotes"]["records"] == 2 and a["rejected_quotes"]["by_reason"] == {"ambiguous": 1, "too short (under 25 characters)": 1}
    assert a["signals_not_published"]["by_outcome"] == {"unresolved": 1, "unsupported": 1} and a["signals_not_published"]["count"] == 2
    issue = a["known_coverage_issues"][0]
    assert issue["issue"] == "credit_report_subject_ambiguity" and issue["rejected"] == 1 and issue["by_batch"] == {"b036": 1}
