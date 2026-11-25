"""The final workflow (user, 24 Nov 2026): a one-command run that survives its driver, top-N artifact selection, and a financial range.
Offline, no model."""
import json
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src import act, pipeline, runctl, score

ROOT = Path(__file__).resolve().parent.parent
SCORE_CFG = {"hourly_rate": 85, "min_events": 2, "min_emails": 3, "min_threads": 2, "min_months": 1}


# --- the financial range: only the documented minutes assumption varies ---

def _proc(pid, low, base, high):
    return {"process_id": pid, "name": pid, "minutes_per_event": {"low": low, "base": base, "high": high, "rationale": "r", "basis_obs_ids": []}}


def _ev(eid, pid, n, mbs=("mb",)):
    return {"event_id": eid, "process_id": pid, "obs_ids": [f"{eid}o"], "msg_keys": [f"k{eid}", f"k{eid}b"], "threads": [f"t{eid}"],
            "actors": ["a", "b", "c"], "mailboxes": list(mbs), "stall_signals": {}}


WIN = {"mb": (datetime(2001, 1, 1, tzinfo=timezone.utc), datetime(2001, 7, 2, 7, 30, tzinfo=timezone.utc))}


def test_the_running_total_carries_a_range_from_the_low_and_high_minutes_and_nothing_else():
    evs = [_ev(f"A{i}", "P1", i) for i in range(6)] + [_ev(f"B{i}", "P2", i) for i in range(4)]
    r = score.score([_proc("P1", 10, 20, 40), _proc("P2", 15, 30, 45)], evs, WIN, SCORE_CFG, {})
    claims, o = r["claims"], {x["process_id"]: x for x in r["opportunities"]}
    total, low, high = (Decimal(claims[k]["value"]) for k in ("total", "total_low", "total_high"))
    assert low < total < high
    assert total == sum(Decimal(x["allocated_dollars"]) for x in o.values())
    assert low == sum(Decimal(x["dollars_low"]) for x in o.values()) and high == sum(Decimal(x["dollars_high"]) for x in o.values())
    # the range scales the minutes only: low / base is the ratio of the minutes, whatever the events and the rate
    assert Decimal(o["P1"]["dollars_low"]) / Decimal(o["P1"]["dollars_per_month"]) == Decimal(10) / Decimal(20)
    assert Decimal(claims["total"]["low"]) == low and "not varied" in claims["total"]["range_basis"]
    assert r["total_range"] == {"low": "total_low", "high": "total_high"}
    assert score.check_chains(claims, set()) == [] or all("evidence" in p or "needs evidence" in p for p in score.check_chains(claims, set()))
    assert set(claims["total_low"]["inputs"]) == {"P1:dollars", "P2:dollars"} and claims["total_low"]["kind"] == "estimated"


def test_with_no_scored_opportunity_the_range_is_zero_not_missing():
    r = score.score([_proc("P1", 10, 20, 40)], [_ev("A1", "P1", 1)], WIN, SCORE_CFG, {})
    assert Decimal(r["claims"]["total_low"]["value"]) == 0 == Decimal(r["claims"]["total_high"]["value"])


# --- artifact selection: the top five that pass the evidence rules ---

class _M:
    def __init__(self, key):
        self.original_path, self.text = f"corpus/{key}.", "x" * 400


class _St:
    def __init__(self, keys):
        self.messages = {k: _M(k) for k in keys}


def _opps(n=8):
    out = []
    for i in range(1, n + 1):
        out.append({"process_id": f"P{i}", "name": f"proc {i}", "status": "scored", "rank": i, "dollars_per_month": str(100 - i * 10),
                    "events": 5, "confidence": "high"})
    return out


def _world(opps, spans=3):
    procs, obs, keys = {}, {}, set()
    for o in opps:
        ids = []
        for j in range(spans):
            oid, key = f"{o['process_id']}-o{j}", f"mb/i/{o['process_id']}-{j}"
            obs[oid] = {"obs_id": oid, "msg_key": key, "start": 0, "end": 20, "evidence_date": "2001-01-01", "evidence_author": "a", "published_evidence": []}
            ids.append(oid)
            keys.add(key)
        procs[o["process_id"]] = {"process_id": o["process_id"], "obs_ids": ids}
    return procs, obs, _St(keys)


CFG = {"selection": "top_n", "top_n": 5, "min_dollars_per_month": 0, "min_confidence": "medium", "min_events": 3, "min_citations": 3,
       "min_messages": 2, "max_artifacts": 6, "at_least_one": False}


def test_the_top_five_by_the_central_estimate_that_pass_the_evidence_rules_are_selected():
    opps = _opps()
    procs, obs, st = _world(opps)
    sel = act.select(opps, procs, obs, st, CFG)
    assert [o["process_id"] for o in sel["selected"]] == ["P1", "P2", "P3", "P4", "P5"] and sel["mode"] == "top_n" and sel["skipped"] == []


def test_an_opportunity_that_fails_a_rule_is_passed_over_with_its_reason_and_the_next_takes_its_place():
    opps = _opps()
    opps[0]["confidence"] = "low"                      # the best one is too weak
    opps[1]["events"] = 2                              # too few events
    procs, obs, st = _world(opps)
    procs["P3"]["obs_ids"] = procs["P3"]["obs_ids"][:2]  # only two quotes: too few to cite
    sel = act.select(opps, procs, obs, st, CFG)
    assert [o["process_id"] for o in sel["selected"]] == ["P4", "P5", "P6", "P7", "P8"]
    why = {s["process_id"]: " ".join(s["reasons"]) for s in sel["skipped"]}
    assert "confidence low" in why["P1"] and "2 events" in why["P2"] and "2 verified quotes" in why["P3"]


def test_fewer_qualifying_opportunities_give_fewer_artifacts_and_none_gives_none():
    opps = _opps(3)
    procs, obs, st = _world(opps)
    assert len(act.select(opps, procs, obs, st, CFG)["selected"]) == 3
    weak = [dict(o, confidence="low") for o in opps]
    assert act.select(weak, procs, obs, st, CFG)["selected"] == []                       # nothing is forced
    assert [o["process_id"] for o in act.select(opps, procs, obs, st, {**CFG, "min_dollars_per_month": 75})["selected"]] == ["P1", "P2"]
    assert len(act.select(_opps(), *_world(_opps()), {**CFG, "top_n": 2})["selected"]) == 2                 # configurable
    assert act.select([dict(o, status="insufficient_evidence") for o in opps], procs, obs, st, CFG)["selected"] == []


def test_the_earlier_fixed_threshold_rule_is_still_there_as_the_rollback():
    opps = _opps()
    procs, obs, st = _world(opps)
    old = {**CFG, "selection": "threshold", "min_dollars_per_month": 75, "max_artifacts": 6}
    assert [o["process_id"] for o in act.select(opps, procs, obs, st, old)["selected"]] == ["P1", "P2"]
    one = act.select(opps, procs, obs, st, {**old, "min_dollars_per_month": 1000, "at_least_one": True})
    assert [o["process_id"] for o in one["selected"]] == ["P1"]
    assert act.select(opps, procs, obs, st, {**old, "min_dollars_per_month": 1000})["selected"] == []


# --- the one command: a run that survives its driver, and a bounded wait ---

def _child(root, body, slice_name="t"):
    code = (f"import sys, time; sys.path.insert(0, {str(ROOT)!r}); from pathlib import Path; from src import runctl; "
            f"root = Path({str(root)!r}); {body}")
    return [sys.executable, "-c", code]


def test_start_records_the_run_refuses_a_second_start_and_wait_reports_the_exit_code(tmp_path):
    cmd = _child(tmp_path, "time.sleep(1.5); runctl.finish('t', 0, root, note='ok')")
    a = runctl.start("t", root=tmp_path, command=cmd)
    assert a["already_running"] is False and a["status"] == "running" and runctl.pid_alive(a["pid"])
    b = runctl.start("t", root=tmp_path, command=cmd)                            # never a second run on the same slice
    assert b["already_running"] is True and b["pid"] == a["pid"]
    code, msg = runctl.wait("t", max_seconds=0.3, root=tmp_path, poll=0.1)
    assert code == runctl.EXIT_RUNNING == 5 and "still running" in msg and "run `wait` again" in msg
    code, msg = runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.2)
    assert code == 0 and "exit code 0" in msg
    assert runctl.read_record("t", tmp_path)["status"] == "finished"
    again = runctl.start("t", root=tmp_path, command=cmd)                           # a finished run can be started again
    assert again["already_running"] is False and again["pid"] != a["pid"]
    runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.2)


def test_the_run_s_own_exit_code_comes_back_through_wait(tmp_path):
    for code_in in (1, 2, 4):
        runctl.start("t", root=tmp_path, command=_child(tmp_path, f"runctl.finish('t', {code_in}, root)"))
        code, msg = runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.1)
        assert code == code_in, msg


def test_a_run_that_died_without_an_exit_code_is_reported_as_3_and_can_be_started_again(tmp_path):
    runctl.start("t", root=tmp_path, command=[sys.executable, "-c", "import sys; sys.exit(7)"])        # never records itself
    code, msg = runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.1)
    assert code == runctl.EXIT_DIED == 3 and "ended without recording an exit code" in msg and "start" in msg
    assert runctl.read_record("t", tmp_path)["status"] == "died"
    assert runctl.wait("none", max_seconds=1, root=tmp_path)[0] == 3                                   # nothing was started
    b = runctl.start("t", root=tmp_path, command=_child(tmp_path, "runctl.finish('t', 0, root)"))
    assert b["already_running"] is False
    assert runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.1)[0] == 0


def test_a_finished_run_hands_the_driver_the_figures_to_report_in_a_compact_block(tmp_path):
    rep = {"exit_code": 0, "coverage": {"read": 3200, "messages_in_slice": 3200, "complete": True}, "total": "$1,385.79",
           "total_range": {"low": "$607.01", "high": "$2,662.21", "basis": "low and high minutes per event only"},
           "opportunities_ranked": [(i, f"Process {i}", "scored", f"${100 - i}.00") for i in range(1, 30)], "artifacts": ["P1-template", "P2-checklist"]}
    runctl.write_summary("t", rep, tmp_path)
    runctl.start("t", root=tmp_path, command=_child(tmp_path, "runctl.finish('t', 0, root)"))
    code, msg = runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.1)
    assert code == 0 and msg.splitlines()[1] == "SUMMARY (copy these figures exactly):"
    assert "coverage: 3200/3200 messages read, complete: True" in msg and "central estimate: $1,385.79 per month" in msg
    assert "range: $607.01 to $2,662.21 (low and high minutes per event only) per month" in msg
    assert "rank 1: Process 1 | scored | $99.00" in msg and "rank 10:" in msg and "rank 11:" not in msg and "artifacts: P1-template, P2-checklist" in msg
    assert len(msg.splitlines()) < 30                                   # short enough for a driver to read whole: the failure of the dev smoke
    runctl.start("t", root=tmp_path, command=_child(tmp_path, "runctl.finish('t', 2, root)"))
    code, msg = runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.1)
    assert code == 2 and "SUMMARY" not in msg                           # a failed run reports its log tail, not figures


def test_the_run_records_its_own_pid_because_a_venv_launcher_is_not_the_run(tmp_path):
    a = runctl.start("t", root=tmp_path, command=_child(tmp_path, "runctl.attach('t', root); time.sleep(0.5); runctl.finish('t', 0, root)"))
    assert runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.1)[0] == 0
    rec = runctl.read_record("t", tmp_path)
    assert rec["run_pid"] and rec["token"] and rec["pid"] == a["pid"] and rec["exit_code"] == 0


def test_only_the_recorded_process_may_write_the_exit_code(tmp_path):
    runctl.start("t", root=tmp_path, command=[sys.executable, "-c", "import time; time.sleep(2)"])
    runctl.finish("t", 0, root=tmp_path)                                    # this test process is not the recorded one
    assert runctl.read_record("t", tmp_path)["status"] == "running"
    runctl.wait("t", max_seconds=30, root=tmp_path, poll=0.2)


def test_a_started_run_outlives_the_process_that_started_it(tmp_path):
    """The failure of the first cold run: the driver ended and took the pipeline with it. Start from a short-lived parent and check the child lives on."""
    parent = [sys.executable, "-c", (f"import sys; sys.path.insert(0, {str(ROOT)!r}); from pathlib import Path; from src import runctl; "
                                     f"r = runctl.start('t', root=Path({str(tmp_path)!r}), command=[sys.executable, '-c', 'import time; time.sleep(3)']); print(r['pid'])")]
    import subprocess
    pid = int(subprocess.run(parent, capture_output=True, text=True, check=True).stdout.strip())
    time.sleep(0.5)
    assert runctl.pid_alive(pid)                                            # the parent has exited; the run has not
    time.sleep(3.5)
    assert not runctl.pid_alive(pid)


def test_pipeline_main_wires_start_and_wait_and_records_the_run_exit_code(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(runctl, "start", lambda s, extra=None, **kw: calls.append(("start", s, extra)) or {
        "pid": 1, "slice": s, "status": "running", "started_at_utc": "x", "already_running": False})
    monkeypatch.setattr(runctl, "wait", lambda s, m, **kw: calls.append(("wait", s, m)) or (5, "still running"))
    assert pipeline.main(["start", "--slice", "dev", "--workers", "6"]) == 0 and calls[0] == ("start", "dev", ["--workers", "6"])
    assert pipeline.main(["wait", "--slice", "dev", "--max-seconds", "12"]) == 5 and calls[1] == ("wait", "dev", 12.0)
    fin = []
    monkeypatch.setattr(runctl, "finish", lambda s, c, *a, **k: fin.append((s, c)))
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: {"exit_code": 4, "stopped": {"reason": "usage_limit"}, "resume": "again"})
    assert pipeline.main(["run", "--slice", "dev", "--record"]) == 4 and fin == [("dev", 4)]      # the stop is recorded, not lost
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        pipeline.main(["run", "--slice", "dev", "--record"])
    assert fin[-1] == ("dev", 1)                                                                    # a crash is recorded as 1


def test_a_clean_finish_starts_the_dashboard_only_when_asked(monkeypatch, capsys):
    from src import serve
    started = []
    monkeypatch.setattr(serve, "stop", lambda: started.append("stop"))
    monkeypatch.setattr(serve, "detach", lambda s, p: started.append(("detach", s, p)) or {"url": "u"})
    rep = {"exit_code": 0, "coverage": {}, "total": "$1", "total_range": None, "opportunities_ranked": [], "artifacts": []}
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: rep)
    assert pipeline.main(["run", "--slice", "dev"]) == 0 and started == []
    assert pipeline.main(["run", "--slice", "dev", "--then-serve"]) == 0 and started == ["stop", ("detach", "dev", 8765)]
    started.clear()
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: {**rep, "exit_code": 1})
    assert pipeline.main(["run", "--slice", "dev", "--then-serve"]) == 1 and started == []         # never after a failed run


# --- the command file: what it may run, and that it uses the detached run ---

def _frontmatter():
    text = (ROOT / ".claude" / "commands" / "observe.md").read_text(encoding="utf-8")
    head = text.split("---")[1]
    return text, re.search(r"allowed-tools: (.*)", head).group(1)


def test_the_command_only_runs_what_its_own_allowed_tools_cover_so_a_clean_untrusted_clone_still_works():
    text, allowed = _frontmatter()
    patterns = re.findall(r"Bash\(([^:)]+):\*\)", allowed)
    cmds = re.findall(r"`(\.venv/Scripts/python -m src\.[a-z]+[^`]*)`", text)
    assert cmds and all(any(c.startswith(p.replace("\\\\", "/")) for p in patterns) for c in cmds)       # every command is pre-authorised
    assert "src.pipeline start" in text and "src.pipeline wait" in text and "--max-seconds 540" in text
    assert "src.pipeline run" in text and "Never run `src.pipeline run` yourself" in text              # the foreground run is for humans
    assert "600000" in text and "exit code" in text and re.search(r"\b5: still running", text)
    assert "Bash(" not in text.split("---")[2]                                                           # nothing broader is asked for in the body
    assert not re.search(r"allowed-tools:.*(Bash\(\*\)|Bash\(:\*\)|dangerously)", allowed)
