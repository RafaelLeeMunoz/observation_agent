r"""Full-run feasibility from measured M3 timings (code arithmetic; a planning estimate, never a published finding).

Inputs, all measured this session (files under data/, see the DEVLOG):
  * extraction: the 4 dev batches run in parallel in-session (harness duration_ms per batch, est tokens per batch)
    and the headless bench runs (duration_ms);
  * grounding: the 8-way headless grounding test (duration_ms, claims per packet);
  * synthesis, artifacts and orchestration: the dev end-to-end run log (output/dev/logs/runlog.jsonl);
  * the full-corpus batch plan (49 batches with est tokens; src.batch --slice all, no model).
Model: a batch's extraction time = its est tokens x the measured seconds per est token (mean rate; pessimistic =
the slowest measured batch's rate for every batch); its grounding time = claims x measured seconds per claim
(claims per batch scaled from the dev slice), run as soon as its extraction ends, in the same worker pool. The
pool is simulated (longest job first onto the free worker), so the tail is counted: no perfect scaling.
Usage: .venv\Scripts\python tools\runtime_estimate.py <out.json>
"""
import heapq
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def simulate(extract_s, ground_s, workers):
    """Greedy list schedule: each batch's extraction, then its grounding, as separate jobs on `workers` slots."""
    free = [0.0] * workers
    heapq.heapify(free)
    ready = []                                    # (extraction end, grounding seconds)
    for e, g in sorted(zip(extract_s, ground_s), key=lambda x: -x[0]):
        t = heapq.heappop(free)
        heapq.heappush(free, t + e)
        ready.append((t + e, g))
    for end, g in sorted(ready):                  # each grounding starts after its own extraction, on a free slot
        t = heapq.heappop(free)
        heapq.heappush(free, max(t, end) + g)
    return max(free)


def main(argv):
    dev = json.loads((ROOT / "data" / "batches" / "dev" / "manifest.json").read_text())["batches"]
    allb = json.loads((ROOT / "data" / "batches" / "all" / "manifest.json").read_text())["batches"]
    ext = {}
    for b in dev:
        v = json.loads((ROOT / "data" / "runs" / "dev" / "extract_insession" / f"{b['batch_id']}.json").read_text())
        ext[b["batch_id"]] = (b["est_tokens"], v["usage"]["duration_ms"] / 1000,
                              sum(len(o["stall_signals_claimed"]) for o in v["observations"]))
    rate_mean = sum(s for _, s, _ in ext.values()) / sum(t for t, _, _ in ext.values())
    rate_worst = max(s / t for t, s, _ in ext.values())
    g8 = json.loads((ROOT / "data" / "eval" / "parallel_ground8.json").read_text())
    gdev = [j for j in g8["jobs"] if j["name"].startswith("dev_")]
    g_per_claim = sum(j["usage"]["duration_ms"] / 1000 for j in gdev) / sum(ext[j["name"][4:]][2] for j in gdev)
    g_fixed = min(j["usage"]["duration_ms"] / 1000 for j in gdev)
    claims_per_tok = sum(c for _, _, c in ext.values()) / sum(t for t, _, _ in ext.values())
    overhead = [j["usage"]["wall_seconds"] - j["usage"]["duration_ms"] / 1000 for j in g8["jobs"]]
    start_overhead = sum(overhead) / len(overhead)
    log = [json.loads(l) for l in (ROOT / "output" / "dev" / "logs" / "runlog.jsonl").read_text().splitlines()]
    last_run = log[max(i for i, x in enumerate(log) if x["stage"] == "start"):]
    synth_s = sum(x.get("seconds", 0) for x in last_run if x["stage"] == "synth" and x.get("status") != "cache_hit")
    act_s = [x.get("seconds", 0) for x in last_run if x["stage"] == "act" and x.get("status") != "cache_hit"]
    dev_obs = sum(len(json.loads((ROOT / "data" / "runs" / "dev" / "extract_insession" / f"{b}.json").read_text())["observations"])
                  for b in ext)
    toks_all = [b["est_tokens"] for b in allb]
    scale_obs = sum(toks_all) / sum(t for t, _, _ in ext.values())
    out = {"inputs": {"dev_batches": {k: {"est_tokens": t, "seconds": s, "claims": c} for k, (t, s, c) in ext.items()},
                      "seconds_per_est_token_mean": rate_mean, "seconds_per_est_token_worst": rate_worst,
                      "grounder_seconds_per_claim": g_per_claim, "grounder_min_seconds": g_fixed,
                      "headless_start_overhead_s": start_overhead, "synth_seconds_dev": synth_s, "act_seconds_dev": act_s,
                      "full_batches": len(toks_all), "full_est_tokens": sum(toks_all), "dev_observations": dev_obs,
                      "full_to_dev_ratio": scale_obs},
           "assumptions": ["full-corpus batches extract at the dev slice's seconds per est token (mean), or all at the slowest "
                           "dev batch's rate (pessimistic)",
                           "claims per est token as in the dev slice",
                           "synthesis of the full list takes the dev synthesis time x the observation ratio, split into "
                           "per-mailbox calls run in parallel plus one merge (not yet built or measured)",
                           "artifacts: up to 6 in parallel, each as long as the slowest measured dev artifact",
                           "the Pro plan sustains the given number of parallel sessions without throttling: measured only up to 8"],
           "scenarios": {}}
    synth_full = synth_s * scale_obs / 4 + synth_s          # per-mailbox in parallel (4 mailboxes) + a merge
    act_full = max(act_s or [0]) * 1.0
    fixed = 5 + synth_full + act_full + 5                     # ingest + batch, synth, act, report + server start
    for name, rate in (("mean", rate_mean), ("pessimistic", rate_worst)):
        e = [t * rate + start_overhead for t in toks_all]
        g = [g_fixed + max(0.0, t * claims_per_tok * g_per_claim - g_fixed) + start_overhead for t in toks_all]
        for w in (4, 8, 12, 16):
            total = simulate(e, g, w) + fixed
            out["scenarios"][f"{name}/{w} workers"] = {"extract_and_ground_s": round(simulate(e, g, w), 1),
                                                       "fixed_s": round(fixed, 1), "total_min": round(total / 60, 1),
                                                       "under_30_min": total < 1800}
    Path(argv[0]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out["inputs"], indent=1, default=str)[:1500])
    for k, v in out["scenarios"].items():
        print(f"{k:22s} extract+ground {v['extract_and_ground_s']:7.1f}s  fixed {v['fixed_s']:6.1f}s  total {v['total_min']:5.1f} min  "
              f"{'OK' if v['under_30_min'] else 'OVER'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
