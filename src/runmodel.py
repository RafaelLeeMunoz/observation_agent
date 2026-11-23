"""Which model really ran an extraction attempt (PLAN 3.2: the run log records the model per call).

`prepare` records the model that was *asked for* ("haiku"). This module finds the model that
*answered*, from Claude Code's own record of the subagent run:

  1. the fence log (tools/fence_hook.py) holds one record per tool call of the subagent, with its
     `agent_id` and `transcript_path`; the subagent that read this attempt's batch file is the one
  2. its transcript is `<session>/subagents/agent-<agent_id>.jsonl` next to the session's
     transcript (or the transcript path itself, when it already names the subagent's file)
  3. every assistant turn in it carries the model id, e.g. "claude-haiku-5-5"

Only the `model` field of assistant entries is read; the transcript also holds email text, which
is never used. Status: `confirmed` (one subagent, every turn by a model whose id contains the
requested name), `mismatch` (some turn by another model), or `unknown` (with the reason).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIRMED, MISMATCH, UNKNOWN = "confirmed", "mismatch", "unknown"


ROOT = Path(__file__).resolve().parent.parent


def _norm(p: str, root: Path) -> str:
    q = Path(p)
    return os.path.normcase(os.path.normpath(q if q.is_absolute() else root / q))


def agents_for_batch(fence_dir: Path, batch_file: Path, agent_type: str = "extractor",
                     root: Path = ROOT) -> dict[str, str | None]:
    """{agent_id: transcript_path} for every subagent the fence let read `batch_file` (paths in
    the fence log are as the subagent wrote them: relative ones are taken from the repo root)."""
    out: dict[str, str | None] = {}
    if not fence_dir.is_dir():
        return out
    want = _norm(str(batch_file), root)
    for f in sorted(fence_dir.glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if (r.get("agent_type") == agent_type and r.get("tool") == "Read" and r.get("decision") == "allow"
                and r.get("agent_id") and r.get("path") and _norm(r["path"], root) == want):
            out[r["agent_id"]] = out.get(r["agent_id"]) or r.get("transcript_path")
    return out


def subagent_transcript(transcript_path: str, agent_id: str) -> Path:
    p = Path(transcript_path)
    if p.name == f"agent-{agent_id}.jsonl":
        return p
    return p.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"


def models_in(transcript: Path) -> dict[str, int]:
    """How many assistant turns each model id answered."""
    counts: dict[str, int] = {}
    with transcript.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("type") == "assistant" and isinstance(d.get("message"), dict):
                m = d["message"].get("model") or "(none)"
                counts[m] = counts.get(m, 0) + 1
    return counts


def observe(fence_dir: Path, batch_file: Path, requested: str, search_root: Path | None = None,
            root: Path = ROOT) -> dict:
    """The model record for one attempt. `search_root` (optional) finds transcripts for fence
    records written before the hook logged `transcript_path`."""
    rec = {"requested": requested, "status": UNKNOWN, "agent_ids": [], "transcript": None, "models": {}, "why": ""}
    agents = agents_for_batch(fence_dir, batch_file, root=root)
    rec["agent_ids"] = sorted(agents)
    if not agents:
        rec["why"] = "no fence record of a subagent reading this batch file"
        return rec
    if len(agents) > 1:
        rec["why"] = f"{len(agents)} subagents read this batch file; cannot tell which wrote the output"
        return rec
    (agent_id, tpath), = agents.items()
    if tpath:
        t = subagent_transcript(tpath, agent_id)
    elif search_root is not None:
        found = sorted(search_root.glob(f"**/subagents/agent-{agent_id}.jsonl"))
        if len(found) != 1:
            rec["why"] = f"{len(found)} transcripts named agent-{agent_id}.jsonl under the search root"
            return rec
        t = found[0]
    else:
        rec["why"] = "the fence record has no transcript_path"
        return rec
    rec["transcript"] = str(t)
    if not t.is_file():
        rec["why"] = "the subagent transcript is not on disk"
        return rec
    rec["models"] = models_in(t)
    if not rec["models"]:
        rec["why"] = "the transcript has no assistant turn"
        return rec
    other = sorted(m for m in rec["models"] if requested.lower() not in m.lower())
    if other:
        rec["status"], rec["why"] = MISMATCH, f"asked for {requested!r}, answered by {other}"
    else:
        rec["status"], rec["why"] = CONFIRMED, f"every assistant turn by {sorted(rec['models'])}"
    return rec


def from_headless(requested: str, models: list[str] | None) -> dict:
    """The model record for a headless `claude -p` attempt, from the CLI's own `modelUsage` report (a
    headless main thread has no subagent transcript). Same statuses as `observe`."""
    rec = {"requested": requested, "status": UNKNOWN, "agent_ids": [], "transcript": None,
           "models": {m: None for m in models or []}, "why": "", "source": "claude -p modelUsage"}
    if not models:
        rec["why"] = "the headless report names no model"
        return rec
    other = sorted(m for m in models if requested.lower() not in m.lower())
    if other:
        rec["status"], rec["why"] = MISMATCH, f"asked for {requested!r}, answered by {other}"
    else:
        rec["status"], rec["why"] = CONFIRMED, f"every model in the headless report is {sorted(models)}"
    return rec


def main(argv: list[str] | None = None) -> int:
    """Check a finished run's fence log, e.g. one moved to data/logs/fence_runs/<run>_b001."""
    import argparse
    ap = argparse.ArgumentParser(description="Which model answered an extraction attempt.")
    ap.add_argument("--fence-dir", required=True, type=Path)
    ap.add_argument("--batch-file", required=True, type=Path, help="absolute path of the batch file it read")
    ap.add_argument("--requested", required=True)
    ap.add_argument("--search-root", type=Path, default=None)
    a = ap.parse_args(argv)
    print(json.dumps(observe(a.fence_dir, a.batch_file, a.requested, a.search_root), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
