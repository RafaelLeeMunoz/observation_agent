r"""Synthesis-only repeatability: the SAME input synthesized twice (user, 24 Nov 2026, milestone 4).

Usage: .venv\Scripts\python tools\synth_rerun.py --root DIR --parts s003 s021 s022 [--out DIR]

Re-runs the named synthesis parts of a finished run on their identical inputs, with a fresh cache and a separate result folder (`data/runs/<slice>_rerun/`,
`data/cache/<slice>_rerun/`), so the first run's results, caches and outputs are never touched. The production planner and `_synth_one` run (every
validator, the floor, the positional check, up to `synth.max_attempts` attempts). Then the two results are compared: counted-set Jaccard, pairwise
co-assignment agreement (are two observations that sat together in the first run together in the second, and the reverse), one-to-one process matching by
shared observations, and the attempts, seconds and tokens the rerun cost. This separates the model's own run-to-run variation in grouping from variation
that comes from extracting the messages again (tools/repeatability.py).
"""
import argparse
import itertools
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def counted_groups(res: dict) -> dict[str, str]:
    out = {}
    for p in res["processes"]:
        own = list(dict.fromkeys(p["obs_ids"]))
        if len(own) >= 2 and not p.get("candidate"):
            for o in own:
                out.setdefault(o, p["process_id"])
    return out


def compare(r1: dict, r2: dict) -> dict:
    g1, g2 = counted_groups(r1), counted_groups(r2)
    c1, c2 = set(g1), set(g2)
    both = sorted(c1 & c2)
    pairs = list(itertools.combinations(both, 2))
    same1 = {p for p in pairs if g1[p[0]] == g1[p[1]]}
    same2 = {p for p in pairs if g2[p[0]] == g2[p[1]]}
    inter = len(same1 & same2)
    prec = inter / len(same2) if same2 else 1.0
    rec = inter / len(same1) if same1 else 1.0
    members1, members2 = {}, {}
    for o, p in g1.items():
        members1.setdefault(p, set()).add(o)
    for o, p in g2.items():
        members2.setdefault(p, set()).add(o)
    cand = sorted(((len(members1[x] & members2[y]), x, y) for x in members1 for y in members2), reverse=True)
    ua, ub, match = set(), set(), []
    for n, x, y in cand:
        if n == 0:
            break
        if x in ua or y in ub:
            continue
        j = n / len(members1[x] | members2[y])
        if j >= 0.3:
            ua.add(x), ub.add(y)
            match.append((x, y, n, round(j, 2)))
    in_matched1 = sum(len(members1[x]) for x in ua)
    in_matched2 = sum(len(members2[y]) for y in ub)
    return {"counted_run1": len(c1), "counted_run2": len(c2), "counted_jaccard": round(len(c1 & c2) / len(c1 | c2), 3) if c1 | c2 else 1.0,
            "processes_run1": len(members1), "processes_run2": len(members2), "matched_processes": len(match),
            "observations_in_matched_run1": round(in_matched1 / max(1, len(c1)), 3), "observations_in_matched_run2": round(in_matched2 / max(1, len(c2)), 3),
            "co_assignment_precision": round(prec, 3), "co_assignment_recall": round(rec, 3),
            "co_assignment_f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
            "names_unmatched_run1": [(len(members1[x]), next(p["name"] for p in r1["processes"] if p["process_id"] == x)[:50]) for x in members1 if x not in ua][:6],
            "names_unmatched_run2": [(len(members2[y]), next(p["name"] for p in r2["processes"] if p["process_id"] == y)[:50]) for y in members2 if y not in ub][:6]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--parts", nargs="+", required=True)
    ap.add_argument("--slice", default="all")
    ap.add_argument("--out", default=str(HERE / "eval" / "results" / "m6_repeat"))
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    sys.path.insert(0, str(root))
    from src import pipeline, store, synth
    r, st = pipeline.Run(a.slice, root=root), store.load()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    r.cache = root / "data" / "cache" / f"{a.slice}_rerun"
    r.data = root / "data" / "runs" / f"{a.slice}_rerun"
    r.data.mkdir(parents=True, exist_ok=True)
    r.log_path = out / "synth_rerun_runlog.jsonl"
    allobs = [o for f in sorted((root / "data" / "runs" / a.slice / "ground").glob("b*.json")) for o in json.loads(f.read_text(encoding="utf-8"))["observations"]]
    sc = r.cfg["synth"]
    items = synth.plan(allobs, st, sc["chunk_token_budget"], sc["size_split"])
    mailboxes = max(i["index"] for i in items)
    want = {synth.part_id(i): i for i in items}
    missing = [p for p in a.parts if p not in want]
    if missing:
        raise SystemExit(f"parts not in the plan: {missing}; the plan has {sorted(want)}")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=len(a.parts)) as ex:
        futs = {p: ex.submit(pipeline._synth_one, r, st, want[p]["obs"], p, want[p]["chunk"] or (want[p]["index"], mailboxes)) for p in a.parts}
        done, failed = {}, {}
        for p, f in futs.items():
            try:
                done[p] = f.result()[1]
            except pipeline.RunStopped as e:
                failed[p] = {"reason": e.reason, "attempts": e.detail.get("attempts")}
    rep = {"root": str(root), "wall_s": round(time.time() - t0, 1), "failed": failed, "parts": {}}
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines() if x.strip()] if r.log_path.exists() else []
    for p, res2 in done.items():
        res1 = json.loads((root / "data" / "runs" / a.slice / "synth" / f"{p}.json").read_text(encoding="utf-8"))
        atts = [x for x in log if x.get("input") == p and x.get("attempt")]
        rep["parts"][p] = {"same_input": res1["input_sha256"] == res2["input_sha256"], "observations": len(want[p]["obs"]),
                           "first_run_attempt": res1["attempt_id"], "rerun_attempts": len(atts), "rerun_statuses": [x["status"] for x in atts],
                           "rerun_seconds": [x["seconds"] for x in atts], "rerun_tokens": sum((x.get("usage") or {}).get("subagent_tokens") or 0 for x in atts),
                           **compare(res1, res2)}
    (out / "synth_rerun.json").write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    for p, v in rep["parts"].items():
        print(p, {k: v[k] for k in ("same_input", "observations", "rerun_attempts", "counted_run1", "counted_run2", "counted_jaccard", "processes_run1",
                                    "processes_run2", "matched_processes", "observations_in_matched_run1", "observations_in_matched_run2",
                                    "co_assignment_precision", "co_assignment_recall", "co_assignment_f1")})
    print("failed:", failed, "wall", rep["wall_s"])
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
