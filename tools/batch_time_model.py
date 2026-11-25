r"""M4 2a: a code-computed full-corpus extraction-time estimate per batching rule, from measured batches only.

Usage: .venv\Scripts\python tools\batch_time_model.py [--out eval/results/m4_levers/batch_time_model.json]

Fits seconds = a + b * messages (least squares) on every measured extraction batch at the default effort: the
four M3 dev batches (eval/results/runtime_estimate.json) and the M4 lever runs (eval/results/m4_levers/*.json;
runs with an `--effort` or `--env` lever are left out). Then it builds the real full-corpus batches for each
message cap (code, no model), predicts each batch's seconds, and simulates the pipeline's queue: batches in id
order, each starting on the first free worker. Grounding (pipelined, shorter) is not included. This is an
estimate to choose a cap before step 2b, which measures the real thing; it never becomes a published number.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import batch, config, store  # noqa: E402


def points() -> list[tuple[str, int, float]]:
    pts = []
    est = json.loads((ROOT / "eval" / "results" / "runtime_estimate.json").read_text(encoding="utf-8"))["inputs"]["dev_batches"]
    for bid, v in sorted(est.items()):
        n = len(json.loads((ROOT / "data" / "runs" / "dev" / "extract" / f"{bid}.json").read_text(encoding="utf-8"))["messages"])
        pts.append((f"M3 dev {bid}", n, float(v["seconds"])))
    for f in sorted((ROOT / "eval" / "results" / "m4_levers").glob("*.json")):
        x = json.loads(f.read_text(encoding="utf-8"))
        if "lever" not in x or x["lever"].get("effort") or x["lever"].get("env"):
            continue
        for b, s in zip(x["batches"], x["runtime"]["extraction_sessions_s"]):
            n = len(json.loads((ROOT / b["archived"]).read_text(encoding="utf-8"))["messages"])
            pts.append((f"{x['name']} {b['batch']}", n, float(s)))
    return pts


def fit(pts) -> tuple[float, float, float]:
    xs, ys = [p[1] for p in pts], [p[2] for p in pts]
    n, mx, my = len(xs), sum(xs) / len(xs), sum(ys) / len(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    return a, b, (sum(r * r for r in resid) / max(1, n - 2)) ** 0.5


def simulate(times: list[float], workers: int) -> float:
    free = [0.0] * workers
    for t in times:                                       # FIFO in batch-id order, as the pipeline schedules
        i = free.index(min(free))
        free[i] += t
    return max(free)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "eval" / "results" / "m4_levers" / "batch_time_model.json")
    a_ = ap.parse_args(argv)
    pts = points()
    a, b, sd = fit(pts)
    cfg, st = config.load(), store.load()
    keys = [m.key for m in st.unique()]
    out = {"model": {"seconds": f"{a:.1f} + {b:.2f} * messages", "a": round(a, 2), "b": round(b, 3),
                     "residual_sd_s": round(sd, 1), "points": [{"batch": p[0], "messages": p[1], "seconds": p[2]} for p in pts]},
           "layouts": {}}
    for cap in (None, 40, 30, 20, 15):
        bs = batch.make_batches(st, keys, {**cfg["batch"], "max_messages": cap}, cfg["extract"]["prompt_version"])
        times = [a + b * len(x["keys"]) for x in bs]
        out["layouts"][str(cap)] = {
            "batches": len(bs), "max_messages_in_a_batch": max(len(x["keys"]) for x in bs),
            "predicted_slowest_batch_s": round(max(times)), "sum_of_batch_s": round(sum(times)),
            "minutes_by_workers": {w: round(simulate(times, w) / 60, 1) for w in (8, 12, 16)},
            "minutes_by_workers_slow_case": {w: round(simulate([t + sd for t in times], w) / 60, 1) for w in (8, 12, 16)}}
    a_.out.parent.mkdir(parents=True, exist_ok=True)
    a_.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print("fit:", out["model"]["seconds"], "residual sd", out["model"]["residual_sd_s"], "s over", len(pts), "batches")
    for cap, v in out["layouts"].items():
        print(f"cap {cap:>4}: {v['batches']:4d} batches, largest {v['max_messages_in_a_batch']:3d} msgs, slowest ~{v['predicted_slowest_batch_s']} s;"
              f" minutes at 8/12/16 workers {v['minutes_by_workers']} (slow case {v['minutes_by_workers_slow_case']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
