"""Run control: the pipeline as a detached process that outlives its driver, and a bounded wait for it (user, 24 Nov 2026).

Why: `claude -p "/observe all"` ran the pipeline as one shell command. A shell command that runs longer than the driver's limit
(10 minutes) is moved to the background, and when the driver session ended its turn the pipeline was killed with it, after the score
stage and before the artifacts and the report (found in the first true cold run, 24 Nov 2026). Now:

    python -m src.pipeline start --slice all          start the run detached (it survives the driver); refuses to start a second one
    python -m src.pipeline wait  --slice all          wait up to --max-seconds (540, under the shell limit); exit codes below
    python -m src.pipeline run   --slice all          the same run in the foreground (what `start` launches, with --record --then-serve)

`start` writes `output/<slice>/logs/run.json` (pid, command, start time); the run itself records its exit code there when it ends
(`--record`) and, when it finished cleanly, starts the dashboard (`--then-serve`), so nothing depends on the driver staying alive.
`wait` exit codes: the run's own (0 done, 1 incomplete, 2 critical check failed, 4 stopped cleanly, e.g. the usage limit),
5 still running (call wait again), 3 the run died without an exit code or none was started (call start again: it resumes from cache).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXIT_DIED, EXIT_RUNNING = 3, 5


def _paths(slice_name: str, root: Path) -> tuple[Path, Path, Path]:
    logs = root / "output" / slice_name / "logs"
    return logs, logs / "run.json", logs / "pipeline.out"


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def read_record(slice_name: str, root: Path = ROOT) -> dict | None:
    p = _paths(slice_name, root)[1]
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _write(slice_name: str, root: Path, rec: dict) -> None:
    p = _paths(slice_name, root)[1]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start(slice_name: str, extra_args: list[str] | None = None, root: Path = ROOT, command: list[str] | None = None) -> dict:
    """Launch the run detached. If an earlier start of this slice is still alive, start nothing and say so."""
    rec = read_record(slice_name, root)
    if rec and rec.get("status") == "running" and _alive(rec):
        return {**rec, "already_running": True}
    logs, _, out = _paths(slice_name, root)
    logs.mkdir(parents=True, exist_ok=True)
    cmd = command or [sys.executable, "-m", "src.pipeline", "run", "--slice", slice_name, "--record", "--then-serve", *(extra_args or [])]
    token = uuid.uuid4().hex             # on Windows a venv's python.exe starts the real interpreter, so the pid we get is not the run's own:
    kw = {"cwd": str(root), "stdout": open(out, "ab"), "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
          "env": {**os.environ, "OBSERVE_RUN_TOKEN": token}}               # the run proves it is the recorded one by this token
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            p = subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kw)
        except OSError:                                    # the parent's job does not allow breakaway
            p = subprocess.Popen(cmd, creationflags=flags, **kw)
    else:
        p = subprocess.Popen(cmd, start_new_session=True, **kw)
    rec = {"pid": p.pid, "run_pid": None, "token": token, "slice": slice_name, "status": "running", "started_at_utc": _now(),
           "started_epoch": time.time(), "command": cmd, "already_running": False}
    _write(slice_name, root, rec)
    return rec


def _alive(rec: dict) -> bool:
    return pid_alive(rec["pid"]) or bool(rec.get("run_pid") and pid_alive(rec["run_pid"]))


def _mine(rec: dict | None) -> bool:
    t = os.environ.get("OBSERVE_RUN_TOKEN")
    return bool(rec and t and rec.get("token") == t)


def attach(slice_name: str, root: Path = ROOT) -> None:
    """Called by the run itself when it begins: records its own pid (the one `start` got may be a launcher's)."""
    rec = read_record(slice_name, root)
    if _mine(rec):
        _write(slice_name, root, {**rec, "run_pid": os.getpid()})


def finish(slice_name: str, exit_code: int, root: Path = ROOT, note: str | None = None) -> None:
    """Called by the run itself when it ends: records the exit code, only for the run `start` recorded (its token)."""
    rec = read_record(slice_name, root)
    if _mine(rec):
        _write(slice_name, root, {**rec, "status": "finished", "exit_code": int(exit_code), "finished_at_utc": _now(), "note": note})


def progress(slice_name: str, root: Path = ROOT, since: float | None = None) -> str:
    """One line from the run log: the stage the run is in and what it has finished so far."""
    log = root / "output" / slice_name / "logs" / "runlog.jsonl"
    if not log.exists():
        return "no run log yet"
    n = {"extract": 0, "ground": 0, "synth": 0}
    last = None
    for line in log.read_text(encoding="utf-8").splitlines()[-4000:]:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("stage") == "start":
            n = {"extract": 0, "ground": 0, "synth": 0}                 # count only the latest run
        if r.get("status") in ("verified", "cache_hit") and r.get("stage") in n:
            n[r["stage"]] += 1
        last = r
    return (f"{n['extract']} extractions, {n['ground']} groundings and {n['synth']} synthesis parts finished; last log record: "
            f"{last.get('stage')} {last.get('status') or ''} at +{last.get('t')} s") if last else "empty run log"


def write_summary(slice_name: str, rep: dict, root: Path = ROOT) -> None:
    """The figures a driver reports, compact, in one small file: the coverage line, the central estimate and its range, the top ten, the artifacts."""
    cov = rep.get("coverage") or {}
    rng = rep.get("total_range") or {}
    top = [{"rank": r[0], "process": r[1], "status": r[2], "dollars_per_month": r[3]} for r in (rep.get("opportunities_ranked") or [])[:10]]
    out = {"coverage": f"{cov.get('read')}/{cov.get('messages_in_slice')} messages read, complete: {cov.get('complete')}",
           "central_estimate_per_month": rep.get("total"), "range_per_month": f"{rng.get('low')} to {rng.get('high')} ({rng.get('basis')})" if rng else None,
           "top_ten": top, "artifacts": rep.get("artifacts"), "dashboard": "http://127.0.0.1:8765/",
           "run_log": f"output/{slice_name}/logs/runlog.jsonl", "exit_code": rep.get("exit_code")}
    p = _paths(slice_name, root)[0] / "summary.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")


def summary_text(slice_name: str, root: Path = ROOT) -> str:
    p = _paths(slice_name, root)[0] / "summary.json"
    if not p.exists():
        return ""
    s = json.loads(p.read_text(encoding="utf-8"))
    lines = ["SUMMARY (copy these figures exactly):", f"coverage: {s['coverage']}", f"central estimate: {s['central_estimate_per_month']} per month"]
    if s.get("range_per_month"):
        lines.append(f"range: {s['range_per_month']} per month")
    lines += [f"rank {t['rank']}: {t['process']} | {t['status']} | {t['dollars_per_month']}" for t in s["top_ten"]]
    lines += [f"artifacts: {', '.join(str(a) for a in (s.get('artifacts') or [])) or 'none'}", f"dashboard: {s['dashboard']}", f"run log: {s['run_log']}"]
    return "\n".join(lines)


def tail(slice_name: str, root: Path = ROOT, lines: int = 30) -> str:
    out = _paths(slice_name, root)[2]
    return "\n".join(out.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]) if out.exists() else ""


def _finished(slice_name: str, root: Path, rec: dict) -> str:
    """The message for a finished run: the exit code, then the compact summary (exit 0) or the log tail (anything else)."""
    s = summary_text(slice_name, root) if int(rec["exit_code"]) == 0 else ""
    return f"the run finished with exit code {rec['exit_code']}\n" + (s + "\n" if s else "") + tail(slice_name, root, 12 if s else 30)


def wait(slice_name: str, max_seconds: float = 540, root: Path = ROOT, poll: float = 3.0, sleep=time.sleep) -> tuple[int, str]:
    """Wait for the recorded run to end, for at most `max_seconds`. Returns (exit code, message)."""
    deadline = time.time() + max_seconds
    while True:
        rec = read_record(slice_name, root)
        if not rec:
            return EXIT_DIED, f"no run recorded for slice {slice_name}: run `start` first"
        if rec.get("status") == "finished":
            return int(rec["exit_code"]), _finished(slice_name, root, rec)
        if not _alive(rec):
            rec = read_record(slice_name, root) or rec                          # it may have recorded itself just now
            if rec.get("status") == "finished":
                return int(rec["exit_code"]), _finished(slice_name, root, rec)
            _write(slice_name, root, {**rec, "status": "died", "finished_at_utc": _now()})
            return EXIT_DIED, ("the run process ended without recording an exit code (it was killed or crashed): run `start` again, "
                               f"every finished stage is cached and skipped\n{tail(slice_name, root, 8)}")
        if time.time() >= deadline:
            return EXIT_RUNNING, f"still running after {round(time.time() - rec['started_epoch'])} s: {progress(slice_name, root)}; run `wait` again"
        sleep(min(poll, max(0.0, deadline - time.time())))
