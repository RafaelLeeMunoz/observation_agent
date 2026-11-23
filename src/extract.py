"""Stage 3, extract: one attempt per batch, from dispatch to verified output (PLAN 3.2-3.3).

    prepare  : new attempt id; earlier staging/verified output for this batch is moved aside
               (never reused); prints the instruction for the extractor subagent
    finalize : verifies the staged file against *this* attempt, writes data/extract/verified/
               only if it passes (failures go to data/extract/failed/), moves the staged file to
               staging/accepted/ or staging/rejected/, updates the analysis status (R8) and the
               run log

Run:  .venv\\Scripts\\python -m src.extract prepare  --slice bench --batch b001
      .venv\\Scripts\\python -m src.extract finalize --slice bench --batch b001 --first
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, runmodel, store, verify
from .filelock import SharedFileLock, write_atomic

ROOT = Path(__file__).resolve().parent.parent


class Paths:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.extract = root / "data" / "extract"
        self.staging = self.extract / "staging"
        self.verified = self.extract / "verified"
        self.failed = self.extract / "failed"
        self.attempts = self.extract / "attempts"
        self.status = self.extract / "status.json"
        self.logs = root / "data" / "logs"
        self.fence = self.logs / "fence"
        self.batches = root / "data" / "batches"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _move_aside(path: Path, dest_dir: Path, tag: str) -> None:
    if path.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_dir / f"{path.stem}.{tag}{path.suffix}"))


def load_batch(p: Paths, slice_name: str, batch_id: str) -> dict:
    manifest = json.loads((p.batches / slice_name / "manifest.json").read_text(encoding="utf-8"))
    return next(b for b in manifest["batches"] if b["batch_id"] == batch_id)


def prepare(p: Paths, slice_name: str, batch_id: str, model: str, prompt_version: str,
            schema_name: str = "extract-v7") -> dict:
    batch = load_batch(p, slice_name, batch_id)
    p.attempts.mkdir(parents=True, exist_ok=True)
    rec_path = p.attempts / f"{batch_id}.json"
    previous = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.exists() else None
    n = (previous["attempt_no"] + 1) if previous else 1
    attempt_id = f"{batch_id}-{batch['fingerprint'][:8]}-{n}"
    # Nothing from an earlier attempt can be picked up by this one.
    _move_aside(p.staging / f"{batch_id}.json", p.staging / "rejected", f"superseded-before-{attempt_id}")
    _move_aside(p.verified / f"{batch_id}.json", p.verified / "superseded", f"superseded-before-{attempt_id}")
    prompt = p.root / ".claude" / "agents" / "extractor.md"
    copy = p.root / "data" / "prompts" / f"{prompt_version}.md"
    if not copy.exists():                    # the committed copy of a kept alternative (eval/prompts/extract-v8h.md)
        copy = p.root / "eval" / "prompts" / f"{prompt_version}.md"
    if prompt.exists() and copy.exists() and prompt.read_bytes() != copy.read_bytes():
        # the attempt would record one prompt version and run another (design H swaps the agent file)
        raise ValueError(f"extractor.md is not {copy.relative_to(p.root).as_posix()} ({prompt_version}): "
                         "put the right prompt in place first")
    rec = {"batch_id": batch_id, "attempt_id": attempt_id, "attempt_no": n, "slice": slice_name,
           "fingerprint": batch["fingerprint"], "prompt_version": prompt_version, "model": model,
           "schema": schema_name,                # the output schema this attempt is verified against
           # the exact prompt text this attempt ran with, not only its version label
           "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest() if prompt.exists() else None,
           "batch_file": f"data/batches/{slice_name}/{batch_id}.txt",
           "output_file": f"data/extract/staging/{batch_id}.json",
           "started_at_utc": _now(), "started_monotonic": time.time()}
    rec_path.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    p.staging.mkdir(parents=True, exist_ok=True)
    return rec


def instruction(rec: dict) -> str:
    """What the orchestrator hands the extractor subagent for this attempt."""
    return (f"Read the batch file `{rec['batch_file']}`. batch_id is `{rec['batch_id']}`, attempt_id is "
            f"`{rec['attempt_id']}`. Follow your instructions and write your JSON to `{rec['output_file']}`.")


def finalize(p: Paths, slice_name: str, batch_id: str, st: store.Store, vcfg: dict, first: bool = False,
             usage: dict | None = None) -> dict:
    """`usage` is what the harness reported for the subagent run (tokens, tool calls, duration); the
    subagent cannot see it, so the orchestrator passes it in and it is stored as reported."""
    batch = load_batch(p, slice_name, batch_id)
    rec = json.loads((p.attempts / f"{batch_id}.json").read_text(encoding="utf-8"))
    staged = p.staging / f"{batch_id}.json"
    text = staged.read_text(encoding="utf-8") if staged.exists() else ""
    res = verify.verify_batch(text, batch, st, vcfg, attempt_id=rec["attempt_id"],
                              schema_name=rec.get("schema", "extract-v7"))   # records before 22 Nov: v7
    if not staged.exists():
        res["errors"].insert(0, "the extractor wrote no output file for this attempt")
    finished = time.time()
    res.update(model=rec["model"], prompt_version=rec["prompt_version"], prompt_sha256=rec.get("prompt_sha256"),
               attempt_no=rec["attempt_no"],
               seconds=round(finished - rec["started_monotonic"], 1),
               usage={**usage, "source": "harness report for the subagent"} if usage else None)
    # `model` is what was asked for; `model_observed` is what answered, from the subagent's transcript
    res["model_observed"] = runmodel.observe(p.fence, p.root / rec["batch_file"], rec["model"], root=p.root)
    if res["model_observed"]["status"] == runmodel.UNKNOWN and usage and "models_observed" in usage:
        res["model_observed"] = runmodel.from_headless(rec["model"], usage["models_observed"])   # claude -p run
    res["canary_tripped"] = bool(first and res["status"] == "verified" and verify.canary_tripped(res, vcfg))
    ok = res["status"] == "verified"
    out_dir = p.verified if ok else p.failed
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{batch_id}.json" if ok else f"{rec['attempt_id']}.json"
    (out_dir / name).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    _move_aside(staged, p.staging / ("accepted" if ok else "rejected"), rec["attempt_id"])
    p.logs.mkdir(parents=True, exist_ok=True)
    with shared_lock(p.logs):      # parallel finalizes share status.json and the logs (race found 23 Nov 2026)
        _finish_shared(p, batch, batch_id, rec, res)
    return res


# One lock for the files every attempt shares, across threads AND processes (src/filelock.py). The pipeline runs
# finalize from parallel worker threads, and an orchestrator may run finalize as parallel processes; without it one
# could read status.json while another had just truncated it (JSONDecodeError, found by the mutation check's
# baseline run, about 1 run in 20), or lose another batch's 'read' marks.
def shared_lock(logs_dir: Path) -> SharedFileLock:
    return SharedFileLock(Path(logs_dir) / ".shared.lock")


def _finish_shared(p: Paths, batch: dict, batch_id: str, rec: dict, res: dict) -> None:
    with (p.logs / "rejections.jsonl").open("a", encoding="utf-8") as f:
        for rj in res["rejected"]:
            f.write(json.dumps({"batch_id": batch_id, "attempt_id": rec["attempt_id"], **rj}) + "\n")
        for rj in res.get("rejected_evidence", []):      # a dropped stall signal, kept with what was claimed
            f.write(json.dumps({"batch_id": batch_id, "attempt_id": rec["attempt_id"], "kind": "stall_evidence",
                                **rj}) + "\n")
    status = json.loads(p.status.read_text(encoding="utf-8")) if p.status.exists() else {}
    verify.apply_to_status(res, batch, status)
    write_atomic(p.status, json.dumps(status, indent=1, sort_keys=True) + "\n")
    with (p.logs / "runlog.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"stage": "extract", "batch_id": batch_id, "attempt_id": rec["attempt_id"],
                            "model": rec["model"],
                            "model_observed": {k: res["model_observed"][k] for k in ("status", "models", "why")},
                            "prompt_version": rec["prompt_version"], "prompt_sha256": rec.get("prompt_sha256"),
                            "status": res["status"],
                            "quotes": res.get("quotes"), "evidence": res.get("evidence"), "errors": len(res["errors"]),
                            "canary_tripped": res["canary_tripped"], "started_at_utc": rec["started_at_utc"],
                            "finished_at_utc": _now(), "seconds": res["seconds"],
                            "est_tokens_in": batch["est_tokens"], "usage": res["usage"]}) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Prepare or finalize one extraction attempt.")
    ap.add_argument("action", choices=["prepare", "finalize"])
    ap.add_argument("--slice", default="dev")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--first", action="store_true", help="the run's first batch: the canary applies")
    ap.add_argument("--subagent-tokens", type=int, default=None, help="as the harness reported them")
    ap.add_argument("--tool-uses", type=int, default=None)
    ap.add_argument("--duration-ms", type=int, default=None)
    ap.add_argument("--prompt-version", default=None, help="override settings (an experiment, e.g. extract-v8h)")
    ap.add_argument("--schema", default=None, help="override settings (must go with --prompt-version)")
    args = ap.parse_args(argv)
    usage = {k: v for k, v in (("subagent_tokens", args.subagent_tokens), ("tool_uses", args.tool_uses),
                               ("duration_ms", args.duration_ms)) if v is not None} or None
    cfg = config.load()
    p = Paths()
    if args.action == "prepare":
        rec = prepare(p, args.slice, args.batch, args.model or cfg["extract"]["model"],
                      args.prompt_version or cfg["extract"]["prompt_version"], args.schema or cfg["extract"]["schema"])
        print(instruction(rec))
        return 0
    res = finalize(p, args.slice, args.batch, store.load(), cfg["verify"], args.first, usage)
    print(f"extract {args.batch} ({res['attempt_id']}): {res['status']}; quotes {res.get('quotes')}; "
          f"{res['seconds']} s")
    for e in res["errors"]:
        print("  error:", e)
    mo = res["model_observed"]
    print(f"  model: asked for {mo['requested']!r}; {mo['status']}: {mo['why']}")
    if res["status"] != "verified":
        return 1
    if mo["status"] == runmodel.MISMATCH:
        print("  MODEL MISMATCH: the output is filed, but this run must not be scored as the requested model")
        return 3
    if res["canary_tripped"]:
        print("  CANARY: too many rejected quotes in the first batch; stop the run and look at the prompt")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
