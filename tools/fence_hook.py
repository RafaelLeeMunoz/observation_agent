"""PreToolUse hook: the fence around subagents that read emails (CLAUDE.md rule 7, PLAN 3.2).

The extractor reads untrusted email text, so whatever an email says, it may only
  - Read  a batch file:   data/batches/<slice>/bNNN.txt
  - Write its own output: data/extract/staging/bNNN.json
and every other tool call is denied. The grounder (design I/J, added 22 Nov 2026) reads email text
too, under the same rule with its own two paths:
  - Read  a packet file:  data/ground/packets/<run>/gNNN.txt
  - Write its own output: data/ground/staging/gNNN.json

Registered twice, so a gap in one does not open the fence:
  --agent extractor   from the agent's own frontmatter: always enforced (the hook only runs for it)
  --from-settings     from .claude/settings.json: enforced when the payload names a fenced agent

Every decision about a subagent's call is logged as one file in data/logs/fence/ (tool, path,
decision, why, the payload's field names and its session id and transcript path, never file
contents). Standard library only; run with -I.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# agent -> (folder its Read files sit one level under, Read name, Write folder, Write name)
PATHS = {
    "extractor": (("data", "batches"), re.compile(r"^b\d{3}\.txt$"), ("data", "extract", "staging"),
                  re.compile(r"^b\d{3}\.json$"), "data/batches/<slice>/bNNN.txt", "data/extract/staging/bNNN.json"),
    "grounder": (("data", "ground", "packets"), re.compile(r"^g\d{3}\.txt$"), ("data", "ground", "staging"),
                 re.compile(r"^g\d{3}\.json$"), "data/ground/packets/<run>/gNNN.txt", "data/ground/staging/gNNN.json"),
    # stages 5 and 8 (added 23 Nov 2026): they read code-written packets built from verified output, which
    # still carry text derived from untrusted email, so they are fenced the same way
    "synthesizer": (("data", "synth", "inputs"), re.compile(r"^s\d{3}\.txt$"), ("data", "synth", "staging"),
                    re.compile(r"^s\d{3}\.json$"), "data/synth/inputs/<slice>/sNNN.txt", "data/synth/staging/sNNN.json"),
    "artifact-writer": (("data", "act", "packets"), re.compile(r"^a\d{3}\.txt$"), ("data", "act", "staging"),
                        re.compile(r"^a\d{3}\.md$"), "data/act/packets/<slice>/aNNN.txt", "data/act/staging/aNNN.md"),
}
FENCED = set(PATHS)
# How a subagent hands its final reply back to the orchestrator; found by the live probe, which it
# would otherwise block (the reply then never arrives).
REPLY_TOOLS = {"SubagentHandback"}


def _resolve(raw: str, root: Path) -> Path:
    p = Path(raw)
    return (p if p.is_absolute() else root / p).resolve()


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def decide(payload: dict, agent: str | None = None, root: Path = ROOT) -> tuple[bool, str, str]:
    """Returns (enforced, decision, reason). Not enforced means the hook leaves the call alone."""
    who = agent or payload.get("agent_type")
    if who not in FENCED:
        return False, "allow", "not a fenced agent"
    tool = payload.get("tool_name", "")
    raw = (payload.get("tool_input") or {}).get("file_path") or ""
    if tool in REPLY_TOOLS:
        return True, "allow", "its reply to the orchestrator (no file, shell or web access)"
    if tool not in ("Read", "Write"):
        return True, "deny", f"the {who} may only Read its input file and Write its output; {tool!r} is not allowed"
    if not raw:
        return True, "deny", "no file_path"
    path = _resolve(raw, root)
    read_dir, read_name, write_dir, write_name, read_txt, write_txt = PATHS[who]
    if tool == "Read":
        if _same(path.parent.parent, root.joinpath(*read_dir).resolve()) and read_name.match(path.name):
            return True, "allow", "a batch file" if who == "extractor" else "its input file"
        return True, "deny", f"the {who} may only Read {read_txt}, not {raw!r}"
    if _same(path.parent, root.joinpath(*write_dir).resolve()) and write_name.match(path.name):
        return True, "allow", "its own output file"
    return True, "deny", f"the {who} may only Write {write_txt}, not {raw!r}"


def main(argv: list[str]) -> int:
    agent = argv[argv.index("--agent") + 1] if "--agent" in argv else None
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    enforced, decision, reason = decide(payload, agent)
    if enforced or payload.get("agent_type") or payload.get("agent_id"):
        # One file per decision: both registrations fire at the same moment, and two processes
        # appending to one file on Windows can overwrite each other (the live probe lost a record).
        log_dir = Path(os.environ.get("FENCE_LOG_DIR") or ROOT / "data" / "logs" / "fence")
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        mode = "frontmatter" if agent else "settings"
        rec = {"at": now.isoformat(timespec="milliseconds"), "mode": mode, "agent_type": payload.get("agent_type"),
               "agent_id": payload.get("agent_id"), "tool_use_id": payload.get("tool_use_id"),
               "tool": payload.get("tool_name"), "path": (payload.get("tool_input") or {}).get("file_path"),
               "enforced": enforced, "decision": decision, "reason": reason, "payload_keys": sorted(payload),
               # where Claude Code keeps the run's transcript: finalize reads the model id from it
               # (src/runmodel.py); a path, never transcript contents
               "session_id": payload.get("session_id"), "transcript_path": payload.get("transcript_path")}
        name = f"{now.strftime('%Y%m%dT%H%M%S%f')}-{mode}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        (log_dir / name).write_bytes(json.dumps(rec).encode("utf-8"))
    if enforced and decision == "deny":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "fence: " + reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
