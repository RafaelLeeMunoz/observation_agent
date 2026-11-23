r"""Dev tool: one extraction attempt run headless (claude -p --agent extractor), then finalize and archive.

Usage: .venv\Scripts\python tools\run_extract.py <slice> <batch> <run_name> [--prompt-version V --schema S]
The agent file must already hold the prompt version asked for (prepare refuses otherwise).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import config, extract, headless, store  # noqa: E402


def run_one(slice_name, batch, name, prompt_version=None, schema=None, model=None, archive=True):
    cfg = config.load()
    p = extract.Paths()
    rec = extract.prepare(p, slice_name, batch, model or cfg["extract"]["model"],
                          prompt_version or cfg["extract"]["prompt_version"], schema or cfg["extract"]["schema"])
    rep = headless.run("extractor", extract.instruction(rec), rec["model"], timeout=1500)
    usage = {"subagent_tokens": headless.total_tokens(rep), "duration_ms": rep["duration_ms"],
             "wall_seconds": rep["wall_seconds"], "num_turns": rep["num_turns"], "tokens": rep["tokens"],
             "models_observed": rep["models_observed"], "notional_cost_usd": rep["notional_cost_usd"],
             "source": "claude -p JSON report (headless)"}
    res = extract.finalize(p, slice_name, batch, store.load(), cfg["verify"], usage=usage)
    if archive and res["status"] == "verified":
        dest = ROOT / "data" / "extract" / "bench_runs" / f"{name}_{batch}.json"
        shutil.copy(p.verified / f"{batch}.json", dest)
    fence = p.fence
    fdest = ROOT / "data" / "logs" / "fence_runs" / f"{name}_{batch}"
    if fence.exists() and archive:
        fdest.mkdir(parents=True, exist_ok=True)
        for f in fence.iterdir():
            shutil.move(str(f), str(fdest / f.name))
    return res, rep, usage


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("slice"); ap.add_argument("batch"); ap.add_argument("name")
    ap.add_argument("--prompt-version"); ap.add_argument("--schema"); ap.add_argument("--model")
    a = ap.parse_args(argv)
    res, rep, usage = run_one(a.slice, a.batch, a.name, a.prompt_version, a.schema, a.model)
    print(json.dumps({"status": res["status"], "errors": res["errors"][:5], "quotes": res.get("quotes"),
                      "evidence": res.get("evidence"), "model_observed": res["model_observed"]["status"]}))
    print(json.dumps(usage), "| result:", rep["result"][:200])
    return 0 if res["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
