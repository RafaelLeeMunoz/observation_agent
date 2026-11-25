r"""M5/M9: the timing report and the audit trail of one full-corpus run (code only, no model).

Usage: .venv\Scripts\python tools\full_run_report.py [--slice all] [--dashboard-s 0.6] [--out eval/results/m5_full_run]

Timing (from the run log of the LAST run in output/<slice>/logs/runlog.jsonl): every model session and every code stage is an
interval with a real start and end (a session's own UTC timestamps; a code stage runs from the previous log line to its own),
so the report shows what ran at the same time, not only summed durations:
  stages      per stage: first start, last end, wall, sessions, summed seconds, retries and what they cost
  overlap     the wall-clock seconds in which each combination of stages was active (a sweep over the intervals), so the
              seconds that only one stage occupied, the critical path, can be read off
  synthesis   per mailbox (part -> mailbox from the split line), the merge, the financial calculation (score), artifacts
  dashboard   start-up seconds when `--dashboard-s` is given (measured by the caller: start the server, wait for the first page)
  config      the extractor and grounder configuration of every batch used (analytical prompt hash, run-note hash or none,
              session environment, model): mixed configurations are listed, never merged silently
Audit trail (data under data/<stage>/<slice>/): every quote the verifier rejected (kept by the pipeline's own log), every
signal the grounder left unsupported or unresolved with its reason, and the known coverage issues (the Credit Report subject
lines). Nothing is dropped from it; counts are exact.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_STAGES = ("extract", "ground", "synth", "merge", "act")
CODE_STAGES = ("batch", "events", "score", "report")


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def last_run(records: list[dict]) -> list[dict]:
    start = max(i for i, x in enumerate(records) if x.get("stage") == "start")
    return records[start:]


def intervals(records: list[dict]) -> list[dict]:
    """One interval per model session or code stage: {stage, name, start, end, seconds, status, attempt, part}. Epoch seconds."""
    out, prev = [], ts(records[0]["at"])
    for x in records:
        stage, usage = x.get("stage"), x.get("usage") or {}
        at = ts(x["at"])
        if stage in MODEL_STAGES and usage.get("started_at_utc") and usage.get("finished_at_utc"):
            out.append({"stage": stage, "name": x.get("batch") or x.get("input") or x.get("packet") or stage,
                        "start": ts(usage["started_at_utc"]), "end": ts(usage["finished_at_utc"]), "status": x.get("status"),
                        "attempt": x.get("attempt"), "part": x.get("part"), "kind": usage.get("kind")})
        elif stage in CODE_STAGES:
            out.append({"stage": stage, "name": stage, "start": prev, "end": at, "status": x.get("status"), "attempt": None,
                        "part": None, "kind": None})
        prev = max(prev, at)
    for i in out:
        i["seconds"] = round(i["end"] - i["start"], 1)
    return out


def stage_summary(iv: list[dict], t0: float) -> dict:
    by = defaultdict(list)
    for i in iv:
        by[i["stage"]].append(i)
    res = {}
    for stage, xs in by.items():
        ok = lambda i: i["status"] in ("verified", "ok")
        retries = [i for i in xs if not ok(i)]
        res[stage] = {"sessions": len(xs), "first_start_s": round(min(i["start"] for i in xs) - t0, 1),
                      "last_end_s": round(max(i["end"] for i in xs) - t0, 1),
                      "wall_s": round(max(i["end"] for i in xs) - min(i["start"] for i in xs), 1),
                      "summed_s": round(sum(i["seconds"] for i in xs), 1), "longest_s": max(i["seconds"] for i in xs),
                      "not_ok_attempts": len(retries), "not_ok_seconds": round(sum(i["seconds"] for i in retries), 1),
                      "not_ok_statuses": dict(Counter(i["status"] for i in retries))}
    return res


def overlap(iv: list[dict], t0: float) -> list[dict]:
    """Wall-clock seconds per combination of active stages (a sweep over the interval ends)."""
    cuts = sorted({round(i[k] - t0, 3) for i in iv for k in ("start", "end")})
    acc: Counter = Counter()
    for a, b in zip(cuts, cuts[1:]):
        mid = (a + b) / 2
        active = tuple(sorted({i["stage"] for i in iv if i["start"] - t0 <= mid < i["end"] - t0}))
        acc[active] += b - a
    return [{"stages": list(k) or ["(idle: code between log lines)"], "seconds": round(v, 1)} for k, v in acc.most_common()]


def synthesis_by_mailbox(records: list[dict], iv: list[dict]) -> list[dict]:
    split = next((x for x in records if x.get("stage") == "synth" and x.get("status") == "split"), None)
    boxes = split["mailboxes"] if split else []
    out = []
    for i in iv:
        if i["stage"] == "synth" and i["part"]:
            k = i["part"][0] - 1
            out.append({"part": i["part"], "mailboxes": boxes[k] if k < len(boxes) else None, "seconds": i["seconds"],
                        "status": i["status"], "attempt": i["attempt"]})
    return out


def audit_trail(root: Path, slice_name: str) -> dict:
    rej_path = root / "data" / "extract" / slice_name / "logs" / "rejections.jsonl"
    rej = [json.loads(l) for l in rej_path.read_text(encoding="utf-8").splitlines()] if rej_path.exists() else []
    credit = [r for r in rej if r.get("quote", "").lstrip().startswith("Subject: Credit Report")]
    unresolved = []
    for f in sorted((root / "data" / "runs" / slice_name / "ground").glob("b*.json")):
        for o in json.loads(f.read_text(encoding="utf-8"))["observations"]:
            for s in o.get("signal_outcomes", []):
                if s.get("grounded") and s.get("outcome") != "published":
                    unresolved.append({"batch": f.stem, "obs_id": o["obs_id"], "msg_key": o["msg_key"], "signal": s["signal"],
                                       "outcome": s["outcome"], "reason": (s.get("reason") or "")[:160]})
    return {"rejected_quotes": {"records": len(rej), "by_reason": dict(Counter(r["reason"].split(":")[0] for r in rej)),
                                "by_batch": dict(Counter(r["batch_id"] for r in rej)),
                                "items": [{"batch": r["batch_id"], "obs_id": r["obs_id"], "msg_key": r["msg_key"], "reason": r["reason"],
                                           "quote": r["quote"][:160]} for r in rej]},
            "signals_not_published": {"count": len(unresolved), "by_outcome": dict(Counter(u["outcome"] for u in unresolved)),
                                      "items": unresolved},
            "known_coverage_issues": [{
                "issue": "credit_report_subject_ambiguity",
                "what": "Quotes that are only a 'Subject: Credit Report...' line occur in two regions of their message, so the "
                        "verifier rejects them as ambiguous (the safe direction); a likely recurring process (the daily credit "
                        "report) loses those observations",
                "rejected": len(credit), "by_batch": dict(Counter(r["batch_id"] for r in credit)),
                "status": "documented, not changed (analytical prompt and verifier frozen for the performance validation)"}]}


def config_manifest(root: Path, slice_name: str) -> dict:
    ex, gr = Counter(), Counter()
    for f in sorted((root / "data" / "extract" / slice_name / "verified").glob("b*.json")):
        v = json.loads(f.read_text(encoding="utf-8"))
        ex[((v.get("prompt_sha256") or "")[:12], (v.get("run_note_sha256") or "none")[:12], (v.get("usage") or {}).get("session_env", "none"),
            (v.get("model_observed") or {}).get("status"))] += 1
    for f in sorted((root / "data" / "runs" / slice_name / "ground").glob("b*.json")):
        g = (json.loads(f.read_text(encoding="utf-8")).get("grounding") or {})
        gr[((g.get("prompt_sha256") or "")[:12], (g.get("usage") or {}).get("session_env", "none"), (g.get("model_observed") or {}).get("status"))] += 1
    return {"extraction": [{"analytical_prompt": k[0], "run_note": k[1], "session_env": k[2], "model": k[3], "batches": n} for k, n in ex.most_common()],
            "grounding": [{"analytical_prompt": k[0], "session_env": k[1], "model": k[2], "batches": n} for k, n in gr.most_common()]}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice", default="all")
    ap.add_argument("--dashboard-s", type=float, default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "eval" / "results" / "m5_full_run")
    a = ap.parse_args(argv)
    log = [json.loads(l) for l in (ROOT / "output" / a.slice / "logs" / "runlog.jsonl").read_text(encoding="utf-8").splitlines()]
    run = last_run(log)
    t0, iv = ts(run[0]["at"]), None
    iv = intervals(run)
    end = max(i["end"] for i in iv)
    done = next((x for x in reversed(run) if x.get("stage") == "done"), {})
    res = {"slice": a.slice, "started_at": run[0]["at"], "end_to_end_wall_s": round(end - t0, 1), "pipeline_reported_s": done.get("seconds"),
           "status": done.get("status"), "workers": run[0].get("workers"), "stages": stage_summary(iv, t0), "overlap": overlap(iv, t0),
           "synthesis_by_mailbox": synthesis_by_mailbox(run, iv), "dashboard_startup_s": a.dashboard_s,
           "cache_hits": dict(Counter(x["stage"] for x in run if x.get("status") == "cache_hit")),
           "config": config_manifest(ROOT, a.slice), "audit_trail_counts": None}
    audit = audit_trail(ROOT, a.slice)
    res["audit_trail_counts"] = {"rejected_quotes": audit["rejected_quotes"]["records"], "signals_not_published": audit["signals_not_published"]["count"]}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "timing_report.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    (a.out / "audit_trail.json").write_text(json.dumps(audit, indent=1) + "\n", encoding="utf-8")
    print(f"end-to-end wall {res['end_to_end_wall_s']} s (pipeline says {res['pipeline_reported_s']}), status {res['status']}, cache hits {res['cache_hits']}")
    for stage, s in res["stages"].items():
        print(f"  {stage:8s} sessions {s['sessions']:3d}  {s['first_start_s']:7.1f} -> {s['last_end_s']:7.1f} s  wall {s['wall_s']:7.1f}  summed {s['summed_s']:8.1f}"
              f"  longest {s['longest_s']:6.1f}  not-ok {s['not_ok_attempts']} ({s['not_ok_seconds']} s)")
    print("  overlap (seconds with these stages active):", [(o["stages"], o["seconds"]) for o in res["overlap"][:8]])
    print("  synthesis by mailbox:", res["synthesis_by_mailbox"])
    print("  config:", res["config"]["extraction"], "| grounding:", res["config"]["grounding"])
    print("  audit trail:", res["audit_trail_counts"], "| credit report:", audit["known_coverage_issues"][0]["rejected"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
