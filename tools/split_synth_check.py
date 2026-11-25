r"""M4 step 1d live check: synthesize the dev slice split into parts (budget forced low) and merge, then compare
with the M3 single-pass dev synthesis, by observation overlap (code only; the model runs only in stage_synth).

Usage: .venv\Scripts\python tools\split_synth_check.py [--budget 8000]
Spend: one Sonnet session per part (5 at 8000 tokens) and one merger session. Never the full corpus.

The M3 single-pass result (data/runs/dev/synth.json) is copied to data/runs/dev/synth_m3_single_pass.json first
and is never overwritten; the split result is saved as data/runs/dev/synth_split_check.json, the comparison as
data/runs/dev/split_synth_check.json. Afterwards `python -m src.pipeline run --slice dev` re-synthesizes the dev
slice in one pass with the current prompt (its cache entry changed with the M4 prompt).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import pipeline, store, synth  # noqa: E402


def overlap(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def compare(single: dict, split: dict) -> dict:
    """For each single-pass process, the split process sharing most of its observations (Jaccard), and the rest."""
    s_procs = {p["process_id"]: set(p["obs_ids"]) for p in single["processes"]}
    t_procs = {p["process_id"]: set(p["obs_ids"]) for p in split["processes"]}
    rows = []
    for pid, obs in s_procs.items():
        best = max(t_procs.items(), key=lambda kv: (overlap(obs, kv[1]), kv[0]), default=(None, set()))
        rows.append({"single": pid, "single_name": next(p["name"] for p in single["processes"] if p["process_id"] == pid),
                     "observations": len(obs), "split": best[0],
                     "split_name": next((p["name"] for p in split["processes"] if p["process_id"] == best[0]), None),
                     "jaccard": round(overlap(obs, best[1]), 3), "shared": len(obs & best[1])})
    matched = {r["split"] for r in rows if r["jaccard"] >= 0.5}
    return {"single_processes": len(s_procs), "split_processes": len(t_procs), "rows": rows,
            "recovered_at_half_overlap": sum(r["jaccard"] >= 0.5 for r in rows),
            "split_only": sorted(set(t_procs) - matched),
            "assigned_single": sum(len(v) for v in s_procs.values()), "assigned_split": sum(len(v) for v in t_procs.values()),
            "split_flags": split.get("flags", []), "parts": split.get("parts")}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=8000)
    a = ap.parse_args(argv)
    r = pipeline.Run("dev")
    single_path = r.data / "synth.json"
    keep = r.data / "synth_m3_single_pass.json"
    if not keep.exists():
        shutil.copy(single_path, keep)
    single = json.loads(keep.read_text(encoding="utf-8"))
    st = store.load()
    obs = [o for f in sorted((r.data / "ground").glob("b*.json")) for o in json.loads(f.read_text(encoding="utf-8"))["observations"]]
    parts = synth.split(obs, st, a.budget, date_split=True)
    print(f"{len(obs)} observations -> {len(parts)} parts at {a.budget} tokens: {[len(p) for p in parts]}")
    r.cfg["synth"].update(chunk_token_budget=a.budget, date_split=True)      # a dev measurement only
    res = pipeline.stage_synth(r, st, obs)
    (r.data / "synth_split_check.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    shutil.copy(keep, single_path)                     # the pipeline's dev synthesis file stays the single pass
    cmp_ = compare(single, res)
    (r.data / "split_synth_check.json").write_text(json.dumps(cmp_, indent=1), encoding="utf-8")
    for row in cmp_["rows"]:
        print(f"  {row['single']} ({row['observations']} obs) {row['single_name'][:50]!r} -> {row['split']} "
              f"jaccard {row['jaccard']} {str(row['split_name'])[:50]!r}")
    print(f"recovered (jaccard >= 0.5): {cmp_['recovered_at_half_overlap']}/{cmp_['single_processes']}; "
          f"split-only processes: {cmp_['split_only']}; assigned {cmp_['assigned_single']} vs {cmp_['assigned_split']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
