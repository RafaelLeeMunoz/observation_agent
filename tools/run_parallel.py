r"""Dev tool: run several fenced headless jobs at once and time them (the concurrency measurement).

Usage: .venv\Scripts\python tools\run_parallel.py <jobs.json> <out.json>
jobs.json: [{"kind": "ground", "run": "<verified run file>", "name": "...", "packet": "g002"},
            {"kind": "extract", "slice": "dev", "batch": "b001", "name": "..."} , ...]
Extraction jobs must use different batches (each batch has one staging file); grounding jobs different packet ids.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from src import ground, headless, store  # noqa: E402


def job(j, st, t0):
    start = time.time() - t0
    if j["kind"] == "ground":
        rec = ground.prepare(Path(j["run"]), j["name"], j["packet"], st)
        rep = headless.run("grounder", ground.instruction(rec), "haiku", timeout=1200)
        usage = {"subagent_tokens": headless.total_tokens(rep), "duration_ms": rep["duration_ms"],
                 "wall_seconds": rep["wall_seconds"], "num_turns": rep["num_turns"], "tokens": rep["tokens"],
                 "models_observed": rep["models_observed"], "notional_cost_usd": rep["notional_cost_usd"],
                 "source": "claude -p JSON report (headless)"}
        res = ground.finalize(j["name"], j["packet"], st, usage)
        status, detail = res["status"], res.get("outcomes")
    else:
        import run_extract
        res, rep, usage = run_extract.run_one(j["slice"], j["batch"], j["name"], archive=j.get("archive", True))
        status, detail = res["status"], res.get("quotes")
    return {**j, "status": status, "detail": detail, "start_s": round(start, 1), "end_s": round(time.time() - t0, 1),
            "usage": usage, "exit_code": rep["exit_code"], "is_error": rep["is_error"], "stderr": rep["stderr_tail"][-300:]}


def main(argv):
    jobs = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    st = store.load()
    t0 = time.time()
    began = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        out = list(ex.map(lambda j: job(j, st, t0), jobs))
    total = round(time.time() - t0, 1)
    rep = {"began_utc": began, "wall_seconds": total, "jobs": out,
           "sum_job_seconds": round(sum(o["end_s"] - o["start_s"] for o in out), 1),
           "tokens": sum(o["usage"]["subagent_tokens"] for o in out),
           "notional_cost_usd": round(sum(o["usage"]["notional_cost_usd"] or 0 for o in out), 4)}
    Path(argv[1]).parent.mkdir(parents=True, exist_ok=True)
    Path(argv[1]).write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    for o in out:
        print(o["name"], o.get("batch") or o.get("packet"), o["status"], o["detail"], o["start_s"], "->", o["end_s"], "s",
              o["usage"]["subagent_tokens"], "tok")
    print("wall", total, "s; sum of job times", rep["sum_job_seconds"], "s; tokens", rep["tokens"], "; notional $", rep["notional_cost_usd"])
    return 0 if all(o["status"] == "verified" for o in out) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
