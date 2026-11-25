r"""Isolated test of the production size-triggered splitting on ONE mailbox (user, 24 Nov 2026: giron-d first, before it is applied for good).

Usage: .venv\Scripts\python tools\split_mailbox_test.py --mailbox giron-d [--merge-id m210] [--out eval/results/m5_giron_d_split]

Uses the production planner (`synth.plan`, the same ids and inputs a full run would use, so the chunk results are cache hits later), the
production `_synth_one` (structural check, 15 percent floor, 25 percent catch-all guard, positional check, three attempts with standing
requirements) and the production merger. Nothing the working run owns is touched: the merge is written under its own id (never `synth.json`),
the log is the test's own, and the whole-mailbox result (`s002.json` for giron-d) is only read. A chunk or merge that fails three attempts
stops the test and is recorded. Analysis: tools/split_mailbox_report.py.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import pipeline, store, synth  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mailbox", required=True)
    ap.add_argument("--merge-id", default="m210")
    ap.add_argument("--out", default="eval/results/m5_giron_d_split")
    a = ap.parse_args(argv)
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    if (out / "experiment.json").exists():
        raise SystemExit(f"{out / 'experiment.json'} exists: results are never overwritten")
    if a.merge_id == "m001":
        raise SystemExit("m001 is the working merge: pick another id")
    r, st = pipeline.Run("all"), store.load()
    r.log_path = out / "runlog.jsonl"                       # the test's own log; the working run log is not appended to
    allobs = [o for f in sorted((r.data / "ground").glob("b*.json")) for o in json.loads(f.read_text(encoding="utf-8"))["observations"]]
    sc = dict(r.cfg["synth"]["size_split"], enabled=True)
    items = synth.plan(allobs, st, r.cfg["synth"]["chunk_token_budget"], sc)
    mine = [i for i in items if i["mailbox"] == a.mailbox]
    if len(mine) < 2:
        raise SystemExit(f"{a.mailbox} is not split by the production rule ({len(mine)} part): nothing to test")
    obs = [o for i in mine for o in i["obs"]]
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mailbox": a.mailbox, "observations": len(obs),
           "chunks": [{"id": synth.part_id(i), "observations": len(i["obs"]), "chunk": i["chunk"]} for i in mine],
           "size_split": sc, "positional": r.cfg["synth"]["positional"], "max_attempts": pipeline._max_attempts(r), "status": "running"}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=len(mine)) as ex:
        futs = [(synth.part_id(i), ex.submit(pipeline._synth_one, r, st, i["obs"], synth.part_id(i), i["chunk"])) for i in mine]
        done, failed = [], []
        for sid, f in futs:
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
            merged = pipeline._merge(r, st, done, obs, mid=a.merge_id)
            rec.update(status="merged", merge_wall_s=round(time.time() - t1, 1), final_processes=len(merged["processes"]),
                       merged=sum(p.get("made_by") == "merged" for p in merged["processes"]), merge_flags=merged.get("flags"))
        except pipeline.RunStopped as e:
            rec.update(status="failed_merge", failed=[{"part": a.merge_id, "reason": e.reason, "attempts": e.detail.get("attempts")}])
    rec["wall_s"] = round(time.time() - t0, 1)
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines()] if r.log_path.exists() else []
    rec["sessions"] = [{"stage": x["stage"], "input": x.get("input") or a.merge_id, "attempt": x.get("attempt"), "status": x.get("status"),
                        "seconds": x.get("seconds"), "n_errors": x.get("n_errors"), "coverage": x.get("coverage"),
                        "processes": x.get("processes"), "fed_back_errors": x.get("fed_back_errors"), "errors": x.get("errors"),
                        "tokens": (x.get("usage") or {}).get("tokens"), "subagent_tokens": (x.get("usage") or {}).get("subagent_tokens"),
                        "started_at_utc": (x.get("usage") or {}).get("started_at_utc"), "finished_at_utc": (x.get("usage") or {}).get("finished_at_utc")}
                       for x in log if x.get("stage") in ("synth", "merge") and x.get("attempt")]
    rec["cache_hits"] = [x.get("input") for x in log if x.get("status") == "cache_hit"]
    (out / "experiment.json").write_text(json.dumps(rec, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: rec[k] for k in rec if k not in ("sessions",)}, indent=1, default=str)[:3000])
    for s in rec["sessions"]:
        print(f"  {s['input']:6s} {str(s['attempt'])[-12:]:12s} {s['status']:9s} {s['seconds']} s, {s['processes']} processes, errors {s['n_errors']}, "
              f"coverage {(s['coverage'] or {}).get('share')}")
    return 0 if rec["status"] == "merged" else 2


if __name__ == "__main__":
    sys.exit(main())
