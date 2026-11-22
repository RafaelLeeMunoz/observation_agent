"""PreToolUse hook: the fence around subagents that read emails (CLAUDE.md rule 7, PLAN 3.2).

The extractor reads untrusted email text, so whatever an email says, it may only
  - Read  a batch file:   data/batches/<slice>/bNNN.txt
  - Write its own output: data/extract/staging/bNNN.json
and every other tool call is denied.

Registered twice, so a gap in one does not open the fence:
  --agent extractor   from the agent's own frontmatter: always enforced (the hook only runs for it)
  --from-settings     from .claude/settings.json: enforced when the payload names a fenced agent

Every decision about a subagent's call is logged as one file in data/logs/fence/ (tool, path,
decision, why, and the payload's field names, never file contents). Standard library only; run with -I.
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
FENCED = {"extractor"}
# How a subagent hands its final reply back to the orchestrator; found by the live probe, which it
# would otherwise block (the reply then never arrives).
REPLY_TOOLS = {"SubagentHandback"}
_BATCH = re.compile(r"^b\d{3}\.txt$")
_OUTPUT = re.compile(r"^b\d{3}\.json$")


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
        return True, "deny", f"the {who} may only Read its batch file and Write its output; {tool!r} is not allowed"
    if not raw:
        return True, "deny", "no file_path"
    path = _resolve(raw, root)
    if tool == "Read":
        batches = (root / "data" / "batches").resolve()
        if _same(path.parent.parent, batches) and _BATCH.match(path.name):
            return True, "allow", "a batch file"
        return True, "deny", f"the {who} may only Read data/batches/<slice>/bNNN.txt, not {raw!r}"
    staging = (root / "data" / "extract" / "staging").resolve()
    if _same(path.parent, staging) and _OUTPUT.match(path.name):
        return True, "allow", "its own output file"
    return True, "deny", f"the {who} may only Write data/extract/staging/bNNN.json, not {raw!r}"


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
               "enforced": enforced, "decision": decision, "reason": reason, "payload_keys": sorted(payload)}
        name = f"{now.strftime('%Y%m%dT%H%M%S%f')}-{mode}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        (log_dir / name).write_bytes(json.dumps(rec).encode("utf-8"))
    if enforced and decision == "deny":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "fence: " + reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
