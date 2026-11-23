r"""Dev tool: prepare one grounding attempt, run the fenced grounder headless, finalize, archive the fence log.

Usage: .venv\Scripts\python tools\run_ground.py <run_file> <name> <packet_id> [--select <strategy>]
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import ground, headless, store  # noqa: E402


def main(argv):
    run_file, name, pid = Path(argv[0]), argv[1], argv[2]
    st = store.load()
    rec = ground.prepare(run_file, name, pid, st)
    print("prepared", rec["attempt_id"], rec["claims"], "claims, ~", rec["est_tokens"], "packet tokens")
    rep = headless.run("grounder", ground.instruction(rec), "haiku", timeout=900)
    usage = {"subagent_tokens": headless.total_tokens(rep), "duration_ms": rep["duration_ms"],
             "wall_seconds": rep["wall_seconds"], "num_turns": rep["num_turns"], "tokens": rep["tokens"],
             "models_observed": rep["models_observed"], "notional_cost_usd": rep["notional_cost_usd"],
             "source": "claude -p JSON report (headless)"}
    res = ground.finalize(name, pid, st, usage)
    fence = ROOT / "data" / "logs" / "fence"
    dest = ROOT / "data" / "logs" / "fence_runs" / f"{name}_{pid}"
    if fence.exists():
        dest.mkdir(parents=True, exist_ok=True)
        for f in fence.iterdir():
            shutil.move(str(f), str(dest / f.name))
    print(json.dumps({k: res.get(k) for k in ("status", "errors", "outcomes", "seconds")}), json.dumps(usage))
    print("result line:", rep["result"][:200], "| exit", rep["exit_code"], rep["stderr_tail"][-200:])
    return 0 if res["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
