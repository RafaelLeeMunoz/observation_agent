r"""One diagnostic synthesis attempt for the farmer-d part (user, 24 Nov 2026, Option B).

Usage: .venv\Scripts\python tools\farmer_d_attempt.py

Runs `pipeline._synth_one` for the farmer-d part ONLY, with a single attempt (`max_attempts = 1`), seeded with the validation
errors of the earlier failed attempts (read from output/all/logs/stop.json) as standing requirements, so the one attempt hears the
coverage floor and the structural error that earlier attempts hit. Every other part stays cached; nothing downstream is
regenerated either way; if the attempt fails, nothing else runs and no retry is made. If it verifies, the part is cached for the
next pipeline run (which re-checks it against the floor again). The result is written to eval/results/m5_farmer_d_diag/.
Exit code: 0 verified, 2 failed.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import pipeline, store, synth  # noqa: E402


def main() -> int:
    r, st = pipeline.Run("all"), store.load()
    r.cfg["synth"]["max_attempts"] = 1
    obs = [o for f in sorted((r.data / "ground").glob("b*.json")) for o in json.loads(f.read_text(encoding="utf-8"))["observations"]]
    parts = synth.split(obs, st, r.cfg["synth"]["chunk_token_budget"], date_split=r.cfg["synth"]["date_split"])
    n = len(parts)
    k = next(i for i, p in enumerate(parts, 1) if {st.messages[o["msg_key"]].mailbox for o in p} == {"farmer-d"})
    sid = f"s{k:03d}"
    stop = json.loads((ROOT / "output" / "all" / "logs" / "stop.json").read_text(encoding="utf-8"))["stopped"]
    if stop.get("input") != sid:
        raise SystemExit(f"stop.json is about {stop.get('input')}, not {sid}: nothing to seed from")
    seed = [e for a in stop.get("attempts", []) for e in a["errors"]]
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "part": sid, "of": n, "observations": len(parts[k - 1]),
           "seed_errors": seed, "max_attempts": 1}
    t0 = time.time()
    try:
        inp, res = pipeline._synth_one(r, st, parts[k - 1], sid, (k, n), seed=seed)
        rec.update(status="verified", processes=len(res["processes"]), coverage=res["coverage"], errors=[], flags=len(res["flags"]),
                   model=(res["model_observed"] or {}).get("status"))
    except pipeline.RunStopped as e:
        rec.update(status="failed", attempts=e.detail["attempts"])
    rec["wall_s"] = round(time.time() - t0, 1)
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines()]
    last = next((x for x in reversed(log) if x.get("stage") == "synth" and str(x.get("attempt", "")).startswith(sid)), {})
    u = last.get("usage") or {}
    rec["attempt_log"] = {k2: last.get(k2) for k2 in ("attempt", "status", "seconds", "processes", "errors", "n_errors", "coverage",
                                                      "fed_back_errors", "standing_kinds", "feedback_sha256", "model")}
    rec["usage"] = {"output_tokens": (u.get("tokens") or {}).get("output_tokens"), "tokens": u.get("tokens"), "wall_seconds": u.get("wall_seconds"),
                    "subagent_tokens": u.get("subagent_tokens"), "notional_cost_usd": u.get("notional_cost_usd")}
    out = ROOT / "eval" / "results" / "m5_farmer_d_diag"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"attempt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json").write_text(json.dumps(rec, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k2: rec.get(k2) for k2 in ("part", "observations", "status", "wall_s", "processes", "coverage", "attempt_log", "usage")}, indent=1, default=str))
    return 0 if rec["status"] == "verified" else 2


if __name__ == "__main__":
    sys.exit(main())
