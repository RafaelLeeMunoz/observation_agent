r"""Bounded experiment (user, 24 Nov 2026): farmer-d synthesized as three date chunks of whole threads, joined by the existing merger.

Usage: .venv\Scripts\python tools\split_experiment.py

The working baseline is never touched: the chunks run as s201-s203 and the merge as m201 (their own inputs, staging, caches and result
files), the experiment writes its own run log, and nothing in output/all, the dashboard, data/runs/all/synth/s001.json or synth.json
changes. Every existing validator applies to each chunk (the structural check, the 15 percent completeness floor, the 25 percent
catch-all guard, three attempts with the standing requirements) and to the merge. If a chunk or the merge fails, the experiment stops.
Results: eval/results/m5_split_experiment/experiment.json (+ the run log). Analysis: tools/split_experiment_report.py.
"""
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import pipeline, store, synth  # noqa: E402

OUT = ROOT / "eval" / "results" / "m5_split_experiment"
K = 3


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "experiment.json").exists():
        raise SystemExit(f"{OUT / 'experiment.json'} exists: results are never overwritten")
    r, st = pipeline.Run("all"), store.load()
    r.log_path = OUT / "runlog.jsonl"                       # the experiment's own log; the working run log is not appended to
    fd = [o for f in sorted((r.data / "ground").glob("b*.json")) for o in json.loads(f.read_text(encoding="utf-8"))["observations"]
          if st.messages[o["msg_key"]].mailbox == "farmer-d"]
    total = sum(synth.est_tokens(o, st) for o in fd)
    budget = math.ceil(total / K)
    chunks = synth.split(fd, st, budget, date_split=True)
    while len(chunks) > K:                                   # whole threads: widen until exactly K chunks
        budget = math.ceil(budget * 1.05)
        chunks = synth.split(fd, st, budget, date_split=True)
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "observations": len(fd), "est_tokens": total,
           "chunks": [len(c) for c in chunks], "max_attempts": pipeline._max_attempts(r), "status": "running"}
    t0 = time.time()
    sids = [f"s{200 + k}" for k in range(1, len(chunks) + 1)]
    with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
        futs = [ex.submit(pipeline._synth_one, r, st, c, sid, (k, len(chunks))) for k, (c, sid) in enumerate(zip(chunks, sids), start=1)]
        done, failed = [], []
        for sid, f in zip(sids, futs):
            try:
                done.append(f.result())
            except pipeline.RunStopped as e:
                failed.append({"part": sid, "reason": e.reason, "attempts": e.detail.get("attempts")})
    rec["chunk_stage_wall_s"] = round(time.time() - t0, 1)
    if failed:
        rec.update(status="failed_chunk", failed=failed)
    else:
        t1 = time.time()
        try:
            merged = pipeline._merge(r, st, done, fd, mid="m201")
            rec.update(status="merged", merge_wall_s=round(time.time() - t1, 1), final_processes=len(merged["processes"]),
                       merged=sum(p.get("made_by") == "merged" for p in merged["processes"]), merge_flags=merged.get("flags"))
        except pipeline.RunStopped as e:
            rec.update(status="failed_merge", failed=[{"part": "m201", "reason": e.reason, "attempts": e.detail.get("attempts")}])
    rec["wall_s"] = round(time.time() - t0, 1)
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines()]
    rec["sessions"] = [{"stage": x["stage"], "input": x.get("input") or "m201", "attempt": x.get("attempt"), "status": x.get("status"),
                        "seconds": x.get("seconds"), "n_errors": x.get("n_errors"), "coverage": x.get("coverage"),
                        "processes": x.get("processes"), "fed_back_errors": x.get("fed_back_errors"), "errors": x.get("errors"),
                        "tokens": (x.get("usage") or {}).get("tokens"), "subagent_tokens": (x.get("usage") or {}).get("subagent_tokens"),
                        "started_at_utc": (x.get("usage") or {}).get("started_at_utc"), "finished_at_utc": (x.get("usage") or {}).get("finished_at_utc")}
                       for x in log if x.get("stage") in ("synth", "merge") and x.get("attempt")]
    (OUT / "experiment.json").write_text(json.dumps(rec, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: rec[k] for k in rec if k not in ("sessions",)}, indent=1, default=str)[:3000])
    for s in rec["sessions"]:
        print(f"  {s['input']:5s} {str(s['attempt'])[-12:]:12s} {s['status']:9s} {s['seconds']} s, {s['processes']} processes, errors {s['n_errors']}, coverage {(s['coverage'] or {}).get('share')}")
    return 0 if rec["status"] == "merged" else 2


if __name__ == "__main__":
    sys.exit(main())
