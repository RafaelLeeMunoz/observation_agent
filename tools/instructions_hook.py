"""InstructionsLoaded hook: records which instruction files a headless worker session loaded (M4 step 2b).

Why: set A of step 2b stopped when an extractor tried to follow the project's CLAUDE.md. The launch now excludes
CLAUDE.md files (src/headless.py, `claudeMdExcludes`), and this hook is how a run shows that nothing was loaded:
one small record per file Claude Code reports loading into a worker's context, written to the session's own
log folder. A worker that was launched correctly writes none.

Metadata only: the file's path, its memory type (User, Project, Local, Managed), why it was loaded, its size
and SHA-256 as stored on disk, the session id and agent. Never the file's contents, the prompt, any email text or
any environment value or credential. Standard library only; run with -I. It prints nothing and always exits 0, so it
can never change what the session does.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# the only payload fields recorded (all metadata; the payload carries no file contents)
KEYS = ("hook_event_name", "session_id", "agent_type", "agent_id", "file_path", "memory_type", "load_reason",
        "globs", "trigger_file_path", "parent_file_path")


def record(payload: dict) -> dict:
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
           **{k: payload.get(k) for k in KEYS if payload.get(k) is not None},
           "payload_keys": sorted(payload)}
    path = payload.get("file_path")
    if isinstance(path, str):
        try:
            data = Path(path).read_bytes()
            rec["size_bytes"], rec["sha256"] = len(data), hashlib.sha256(data).hexdigest()
        except OSError as e:
            rec["unreadable"] = type(e).__name__
    return rec


def main(argv: list[str]) -> int:
    try:
        log_dir = Path(argv[argv.index("--log-dir") + 1]) if "--log-dir" in argv else None
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            name = f"instr-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
            (log_dir / name).write_bytes(json.dumps(record(payload)).encode("utf-8"))
    except Exception:                       # a diagnostic must never break or block a worker session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
