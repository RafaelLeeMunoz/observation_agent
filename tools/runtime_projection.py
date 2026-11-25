r"""M4 2c input: a full-run time and usage projection computed by code from the MEASURED 2b batches (a planning estimate,
never a published finding; it replaces the 2a estimate, which had no live grounding or 12/16-way data).

Usage: .venv\Scripts\python tools\runtime_projection.py [--out eval/results/m4_concurrency/projection.json]

Measured inputs: the 36 real batches of sets A, B and C (eval/results/m4_concurrency/run_*.json: each batch's extraction and
grounding seconds and message count, at the production setting: cap 40, default effort, isolated workers); the real batch
layout of the full corpus (data/batches/all/manifest.json, 81 batches); the farmer-d synthesis comparison
(synth_farmer-d.json: seconds of the whole-mailbox session, of the two date parts in parallel and of the merge); the plan-meter
readings recorded in the DEVLOG. Everything else is marked ASSUMED in the output.
Model: seconds = r * messages through the origin (r = total seconds / total messages), separately for extraction and
grounding, with the per-batch residual sd and a floor at the shortest measured session (the measured batches hold 39-40
messages, so a fitted slope would rest on a few small batches). The pool is simulated as the pipeline schedules it: W worker
slots shared by extraction and grounding, extractions in batch-id order, a batch's grounding queued the moment its extraction
ends and served before any new extraction. Cases: mean; slow (every job + 1 sd); worst (every job at the highest
seconds-per-message rate measured). The simulation is checked against the three measured sets before it is trusted.
Overlap: synthesis of a mailbox may start when the last batch holding that mailbox has been extracted AND grounded; the
estimate is run on the real layout and on a mailbox-ordered layout (a counterfactual that needs re-batching).
"""
import argparse
import heapq
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eval" / "results" / "m4_concurrency"
WORKERS = (8, 12, 16, 20, 24)                         # 8, 12 and 16 are measured (sets A-C); 20 and 24 are EXTRAPOLATED
MEASURED_WORKERS = 16
LIMIT_MIN = 30.0
CAP = 40
# synthesis, artifacts and code stages are not measured at full-corpus size: scale factors per case (ASSUMED)
SYNTH_FACTOR = {"mean": 1.0, "slow": 1.3, "worst": 2.0}
ACT_S = {"mean": 120, "slow": 180, "worst": 300}
CODE_S = {"mean": 10, "slow": 20, "worst": 40}                      # ASSUMED upper bounds; measured ~0.1 s at dev scale, events.build 0.0 s
DASH_S = {"mean": 2, "slow": 5, "worst": 10}                        # ASSUMED; measured 0.6 s to the first page on the dev slice
RETRY_S = {"mean": 0, "slow": 190, "worst": 380}                    # ASSUMED late retries (one 40-message extraction each): 1 in 58 attempts failed
EVIDENCE = {8: "measured, complete set A", 12: "measured, complete set B", 16: "measured, complete set C",
            20: "interpolated between measured 16 and partial 24",
            24: "set D complete: 24 sessions started in the same second and >= 22 stayed active for 140 s; 21 finished in 256 s; two resumes finished the other 3 (all 24 batches verified and grounded); not one clean 24-wide run"}
# plan-meter points per batch (5-hour window): the three sets' readings in the DEVLOG, all including the orchestrating session
POINTS_PER_BATCH = {"mean": 0.61, "slow": 0.83, "worst": 1.0}       # A-C mean; set B (highest measured); 1.0 ASSUMED
WEEKLY_PER_5H_POINT = 0.18                                          # A-C: weekly 85% -> 89% for 22 five-hour points


def fit(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """(r, floor, residual sd) of seconds = max(floor, r * messages): r = total seconds / total messages, the floor the
    shortest measured session, the sd that of the per-batch residuals around r * messages."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    r = sum(ys) / sum(xs)
    sd = (sum((y - r * x) ** 2 for x, y in zip(xs, ys)) / max(1, len(xs) - 1)) ** 0.5
    return r, min(ys), sd


def simulate_pool(ex: list[float], gr: list[float], workers: int) -> dict:
    """The pipeline's shared pool: extraction i takes ex[i] seconds, then its grounding gr[i] seconds is queued and served
    before any extraction that has not started. Returns the makespan, when the last extraction finished and when each
    batch's grounding finished."""
    free = [0.0] * workers
    heapq.heapify(free)
    pending, ready, last_ex, end, gend = list(range(len(ex))), [], 0.0, 0.0, [0.0] * len(ex)
    while pending or ready:
        t = heapq.heappop(free)
        if ready and ready[0][0] <= t:
            _, i = heapq.heappop(ready)
            t += gr[i]
            end, gend[i] = max(end, t), t
            heapq.heappush(free, t)
        elif pending:
            i = pending.pop(0)
            t += ex[i]
            last_ex = max(last_ex, t)
            heapq.heappush(ready, (t, i))
            heapq.heappush(free, t)
        else:                                   # only grounding that is not ready yet: this slot waits for it
            heapq.heappush(free, ready[0][0])
    return {"makespan_s": end, "last_extraction_end_s": last_ex, "ground_end_s": gend}


def finish_with_overlap(done: dict[str, float], synth_s: dict[str, float], makespan: float) -> dict:
    """Synthesis of a mailbox starts when its last grounding ends (overlap), or when every grounding has ended (none).
    Returns when the last synthesis ends in each case (the merge, artifacts and code stages follow)."""
    none = makespan + max(synth_s.values())
    over = max(makespan, max(done[m] + synth_s[m] for m in synth_s))
    return {"synthesis_end_no_overlap_s": none, "synthesis_end_with_overlap_s": over, "saved_s": none - over}


def mailbox_done(gend: list[float], boxes: list[set[str]]) -> dict[str, float]:
    return {m: max(g for g, b in zip(gend, boxes) if m in b) for m in set().union(*boxes)}


def measured_rows() -> tuple[list[dict], dict]:
    rows, runs = [], {}
    for name in "ABC":
        run = json.loads((OUT / f"run_{name}.json").read_text(encoding="utf-8"))
        runs[name] = run
        ground = {g["batch"]: g for g in run["sessions"]["ground"]["rows"]}
        for e in run["sessions"]["extract"]["rows"]:
            g = ground[e["batch"]]
            rows.append({"set": name, "batch": e["batch"], "messages": e["messages"], "extract_s": e["seconds"],
                         "ground_s": g["seconds"]})
    for fn in ("run_D.json", "run_D-r2.json", "run_D-r3.json"):           # set D: the first wave and the resume; every finished session is a sample
        if (OUT / fn).exists():
            sess = json.loads((OUT / fn).read_text(encoding="utf-8"))["sessions"]
            rows += [{"set": "D", "batch": e["batch"], "messages": e["messages"], "extract_s": e["seconds"], "ground_s": None}
                     for e in sess["extract"]["rows"] if e["kind"] == "ok"]
            rows += [{"set": "D", "batch": g["batch"], "messages": g["messages"], "extract_s": None, "ground_s": g["seconds"]}
                     for g in sess["ground"]["rows"] if g["kind"] == "ok"]
    return rows, runs


def scenario_times(layout: list[int], ex_model, gr_model, case: str, worst_rate: tuple[float, float]):
    (er, ef, esd), (gr_, gf, gsd) = ex_model, gr_model
    if case == "mean":
        return [max(ef, er * n) for n in layout], [max(gf, gr_ * n) for n in layout]
    if case == "slow":
        return [max(ef, er * n) + esd for n in layout], [max(gf, gr_ * n) + gsd for n in layout]
    return [max(ef, worst_rate[0] * n) for n in layout], [max(gf, worst_rate[1] * n) for n in layout]


def mailbox_ordered_layout(counts: dict[str, int]) -> tuple[list[int], list[set[str]]]:
    """Counterfactual: batches that hold one mailbox each, the largest mailbox first (whole threads ignored: an estimate)."""
    sizes, boxes = [], []
    for m, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        full, rest = divmod(n, CAP)
        for s in [CAP] * full + ([rest] if rest else []):
            sizes.append(s)
            boxes.append({m})
    return sizes, boxes


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT / "projection.json")
    ap.add_argument("--subset-messages", type=int, default=506, help="farmer-d messages behind the measured synthesis (sets A-C)")
    ap.add_argument("--sonnet-points", type=float, default=1.5, help="meter points per Sonnet session (measured by the caller)")
    a_ = ap.parse_args(argv)
    rows, runs = measured_rows()
    exr, grr = [r for r in rows if r["extract_s"] is not None], [r for r in rows if r["ground_s"] is not None]
    ex_m = fit([(r["messages"], r["extract_s"]) for r in exr])
    gr_m = fit([(r["messages"], r["ground_s"]) for r in grr])
    worst = (max(r["extract_s"] / r["messages"] for r in exr), max(r["ground_s"] / r["messages"] for r in grr))
    manifest = json.loads((ROOT / "data" / "batches" / "all" / "manifest.json").read_text(encoding="utf-8"))["batches"]
    layout = [len(b["keys"]) for b in manifest]
    boxes = [{k.split("/")[0] for k in b["keys"]} for b in manifest]
    counts: dict[str, int] = {}
    for b in manifest:
        for k in b["keys"]:
            counts[k.split("/")[0]] = counts.get(k.split("/")[0], 0) + 1
    out = {"model": {"extract": {"s_per_message": round(ex_m[0], 3), "floor_s": ex_m[1], "sd_s": round(ex_m[2], 1)},
                     "ground": {"s_per_message": round(gr_m[0], 3), "floor_s": gr_m[1], "sd_s": round(gr_m[2], 1)},
                     "worst_s_per_message": {"extract": round(worst[0], 2), "ground": round(worst[1], 2)},
                     "measured_extraction_sessions": len(exr), "measured_grounding_sessions": len(grr), "messages_per_measured_batch": sorted({r["messages"] for r in exr})},
           "full_corpus": {"batches": len(layout), "messages": sum(layout), "messages_by_mailbox": counts,
                           "mixed_mailbox_batches": sum(len(b) > 1 for b in boxes)}}
    val = {}                                    # 1. the simulation must reproduce the measured sets before it is trusted
    for name, run in runs.items():
        rs = [r for r in rows if r["set"] == name]
        sim = simulate_pool([r["extract_s"] for r in rs], [r["ground_s"] for r in rs], run["workers"])
        val[name] = {"workers": run["workers"], "measured_wall_s": run["wall_s"], "simulated_s": round(sim["makespan_s"], 1),
                     "difference_s": round(run["wall_s"] - sim["makespan_s"], 1)}
    out["validation"] = val
    # 2. the tail inputs: MEASURED in compare-synth, then scaled by messages (an ASSUMPTION: time follows the work)
    comp = OUT / "synth_farmer-d.json"
    c = json.loads(comp.read_text(encoding="utf-8")) if comp.exists() else None
    per_msg = c["seconds"]["whole"] / a_.subset_messages if c else None
    synth_whole = {m: per_msg * n for m, n in counts.items()} if c else {}
    out["synthesis"] = {"measured": c["seconds"] if c else None, "measured_subset_messages": a_.subset_messages,
                        "whole_synthesis_s_by_mailbox_ASSUMED_linear_in_messages": {m: round(v) for m, v in synth_whole.items()},
                        "merge_s_measured_on_two_farmer_d_parts": c["seconds"]["merge"] if c else None}
    # 3. the full corpus under each case and worker count, with the tail, the overlap and the usage
    proj, ordered_sizes, ordered_boxes = {}, *mailbox_ordered_layout(counts)
    n_sonnet = len(counts) + 1 + 6                     # 4 mailbox syntheses, the merge, up to 6 artifacts (ASSUMED count)
    for case in ("mean", "slow", "worst"):
        ex, gr = scenario_times(layout, ex_m, gr_m, case, worst)
        ex_o, gr_o = scenario_times(ordered_sizes, ex_m, gr_m, case, worst)
        proj[case] = {}
        for w in WORKERS:
            sim = simulate_pool(ex, gr, w)
            row = {"measured_concurrency": w <= MEASURED_WORKERS, "evidence": EVIDENCE.get(w), "extract_and_ground_min": round(sim["makespan_s"] / 60, 1),
                   "last_extraction_end_min": round(sim["last_extraction_end_s"] / 60, 1),
                   "batches_per_min": round(len(layout) / (sim["makespan_s"] / 60), 2)}
            if c:
                synth = {m: v * SYNTH_FACTOR[case] for m, v in synth_whole.items()}
                fixed = c["seconds"]["merge"] * SYNTH_FACTOR[case] + ACT_S[case] + CODE_S[case] + DASH_S[case] + RETRY_S[case]
                cur = finish_with_overlap(mailbox_done(sim["ground_end_s"], boxes), synth, sim["makespan_s"])
                sim_o = simulate_pool(ex_o, gr_o, w)
                alt = finish_with_overlap(mailbox_done(sim_o["ground_end_s"], ordered_boxes), synth, sim_o["makespan_s"])
                total = (cur["synthesis_end_no_overlap_s"] + fixed) / 60
                row.update(total_min=round(total, 1), margin_min=round(LIMIT_MIN - total, 1),
                           total_with_overlap_current_layout_min=round((cur["synthesis_end_with_overlap_s"] + fixed) / 60, 1),
                           total_with_overlap_mailbox_ordered_layout_min=round((alt["synthesis_end_with_overlap_s"] + fixed) / 60, 1),
                           synthesis_s_longest=round(max(synth.values())), tail_after_grounding_min=round((total * 60 - sim["makespan_s"]) / 60, 1))
            pts = len(layout) * POINTS_PER_BATCH[case] + n_sonnet * a_.sonnet_points
            row["usage_5h_points"], row["usage_weekly_points"] = round(pts), round(pts * WEEKLY_PER_5H_POINT, 1)
            proj[case][w] = row
    out["projection"] = proj
    dpath = OUT / "run_D_analysis.json"
    if dpath.exists():
        dd = json.loads(dpath.read_text(encoding="utf-8"))
        out["set_D_partial"] = {k: dd[k] for k in ("workers", "wall_s", "exit_code", "concurrency", "extraction_latency_s",
                                                   "extraction_s_per_message_by_set", "quotes", "coverage", "resources")}
    out["assumptions"] = ["synthesis time scales linearly with a mailbox's messages from the measured farmer-d subset",
                          f"synthesis factor per case {SYNTH_FACTOR}; artifacts seconds {ACT_S}; code-stage seconds {CODE_S}; dashboard start {DASH_S}; late retries {RETRY_S}",
                          "the merge time is the measured one (two farmer-d parts), times the synthesis factor",
                          f"usage per batch {POINTS_PER_BATCH} (mean and set B measured; worst 1.0 assumed) plus {n_sonnet} Sonnet "
                          f"sessions at {a_.sonnet_points} points; weekly = 5-hour points x {WEEKLY_PER_5H_POINT}",
                          "overlap needs the last batch holding a mailbox grounded; the mailbox-ordered layout is a counterfactual"]
    a_.out.parent.mkdir(parents=True, exist_ok=True)
    a_.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print("extraction fit:", out["model"]["extract"], "| grounding fit:", out["model"]["ground"])
    print("validation:", json.dumps(val))
    print("full corpus:", out["full_corpus"])
    for case, v in proj.items():
        for w, x in v.items():
            print(f"  {case:5s} {w:2d} workers{'' if x['measured_concurrency'] else ' (EXTRAPOLATED)'}: ext+ground {x['extract_and_ground_min']:5.1f} min ({x['batches_per_min']}/min)"
                  + (f" | total {x['total_min']} (margin {x['margin_min']}); overlap: current layout {x['total_with_overlap_current_layout_min']},"
                     f" mailbox-ordered {x['total_with_overlap_mailbox_ordered_layout_min']}" if "total_min" in x else "")
                  + f" | usage ~{x['usage_5h_points']} points of a 5-hour window, ~{x['usage_weekly_points']} weekly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
