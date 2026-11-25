"""PreToolUse hook: the fence around subagents that read emails (CLAUDE.md rule 7, PLAN 3.2).

The extractor reads untrusted email text, so whatever an email says, it may only
  - Read  a batch file:   data/batches/<slice>/bNNN.txt
  - Write its own output: data/extract/staging/<slice>/bNNN.json
and every other tool call is denied. The other fenced agents read code-written files that still carry
text derived from email, under the same rule with their own two paths (PATHS below). Since M4 (PLAN 5.1,
step 1a) every stage reads and writes under its own slice, so dev, bench, production and holdout runs can
never displace each other; the slice must be one of SLICES. A model writes only below its stage's
`staging/` folder, the place its (frozen) prompt names; code-owned files live in data/<stage>/<slice>/.

Registered three times, so a gap in one does not open the fence; a call must pass every one that fires:
  --agent X            from the agent's own frontmatter: always enforced (the hook only runs for it)
  --from-settings      from .claude/settings.json: enforced when the payload names a fenced agent
  --agent X --read R --write W --log-dir D
                       from src/headless.py, per session: the pattern check above PLUS exactly this
                       session's one input file and one output file. Its decisions go to the session's
                       own log folder D, so the pipeline can count them (a denial stops the run).

Every decision about a subagent's call is logged as one file (tool, path, decision, why, the payload's
field names and its session id and transcript path, never file contents). Standard library only; run
with -I.
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
SLICES = ("dev", "bench", "all", "holdout")
_S = "(?:" + "|".join(SLICES) + ")"
_RX = lambda pattern: re.compile(pattern, re.IGNORECASE)        # Windows paths are compared case-folded
# agent -> (Read pattern, Write pattern, Read as text, Write as text); patterns match the repo-relative path
PATHS = {
    "extractor": (_RX(rf"^data/batches/{_S}/b\d{{3}}\.txt$"), _RX(rf"^data/extract/staging/{_S}/b\d{{3}}\.json$"),
                  "data/batches/<slice>/bNNN.txt", "data/extract/staging/<slice>/bNNN.json"),
    "grounder": (_RX(rf"^data/ground/packets/{_S}/[a-z0-9_-]+/g\d{{3}}\.txt$"),
                 _RX(rf"^data/ground/staging/{_S}/g\d{{3}}\.json$"),
                 "data/ground/packets/<slice>/<run>/gNNN.txt", "data/ground/staging/<slice>/gNNN.json"),
    # stages 5 and 8 (added 23 Nov 2026): they read code-written packets built from verified output, which
    # still carry text derived from untrusted email, so they are fenced the same way
    "synthesizer": (_RX(rf"^data/synth/inputs/{_S}/s\d{{3}}\.txt$"), _RX(rf"^data/synth/staging/{_S}/s\d{{3}}\.json$"),
                    "data/synth/inputs/<slice>/sNNN.txt", "data/synth/staging/<slice>/sNNN.json"),
    # M4 step 1d: the cross-chunk merge reads code-written process summaries only
    "merger": (_RX(rf"^data/synth/inputs/{_S}/m\d{{3}}\.txt$"), _RX(rf"^data/synth/staging/{_S}/m\d{{3}}\.json$"),
               "data/synth/inputs/<slice>/mNNN.txt", "data/synth/staging/<slice>/mNNN.json"),
    "artifact-writer": (_RX(rf"^data/act/packets/{_S}/a\d{{3}}\.txt$"), _RX(rf"^data/act/staging/{_S}/a\d{{3}}\.md$"),
                        "data/act/packets/<slice>/aNNN.txt", "data/act/staging/<slice>/aNNN.md"),
}
FENCED = set(PATHS)
# Known harmless self-checks (user, 23 Nov 2026): exact agent, operation and target combinations, each documented
# from the fence logs. They are STILL DENIED and logged (`denial_kind: self_check`); they do not stop the session or
# the run, and the pipeline flags a session that repeats them. Any other denial is `unexpected`: in a session it
# also stops the session (`continue: false`), and the pipeline stops the run. Nothing here grants any access.
SELF_CHECKS = {
    "extractor": {
        ("Read", "schemas/extract-v6.schema.json"):
            "its prompt names this schema file (seen in M3 runs C2, C3h and M4 run D1); the prompt is frozen",
    },
}
# The extractor reading back its own output file (seen in M3 runs v7c, v8c2): known exactly only in a session,
# where the hook is told that file.
OWN_OUTPUT_READ = {"extractor": "reading back its own output file (seen in M3 runs v7c, v8c2)"}
# The synthesizer, the merger and the grounder reading back their own output (user, 23 Nov 2026: after the compare-synth stop,
# where the synthesizer wrote its file, then tried to read it; then after the set D stop, where a grounder did the same). Stricter than the extractor's rule above, because a session's own
# log can prove the order: the read is a documented self-check only when ALL hold (1) it is a Read of exactly this
# session's `--write` file; (2) the session's own log holds an ALLOWED Write of that file and the file is on disk and not
# empty now (the write succeeded); (3) no earlier read-back of it was logged in this session; (4) every record in the
# session's log could be read. Before the write, a second time, another session's file, an unreadable log, or any other
# path is an unexpected denial. The read is still DENIED and logged; it only does not stop the session or the run.
# Python validates the written output on its own before anything uses it (src/synth.py check and merge_check).
READBACK_ONCE = {"synthesizer": "reading back its own output once, after writing it (seen in M4 2b compare-synth)",
                 "merger": "reading back its own output once, after writing it (the same pattern as the synthesizer)",
                 "grounder": "reading back its own output once, after writing it (seen in M4 2b set D, b017)"}
# The extractor or the grounder writing a SCRATCH file next to its own output, after it wrote that output (user, 24 Nov 2026: the final
# cold retest stopped at 12.6 minutes because extractor b081 wrote its valid output and then tried `b081.json.tmp-unused`; before it,
# set D saw `b013.json.tmp-check`; the run instruction 'create no scratch files' did not prevent it). The write is documented as a
# self-check only when ALL hold: (1) the tool is Write and the agent is the extractor or the grounder (no other agent); (2) the path is
# exactly this session's `--write` path followed by `.tmp-` and 1 to 12 lowercase letters, in the very same folder (so not another
# folder, another file, a longer or different suffix, a traversal, or anything with digits, dots or capitals); (3) the session's own
# log holds an ALLOWED Write of its exact output file and that file is on disk and not empty now; (4) no earlier scratch write was
# classified in this session (a second one is unexpected); (5) every record in the session's log could be read. The write is still
# DENIED, nothing is created, and it is logged with its kind; it only does not stop the session or the run. Python validates the
# legitimate output on its own before anything uses it (src/extract.py and src/ground.py finalize), exactly as for any session.
# This grants no write access of any kind.
SCRATCH_ONCE = {"extractor": "writing a scratch file next to its own output once, after writing it (seen in M4 2b set D b013 and "
                             "the final cold retest b081)",
                "grounder": "the same scratch write as the extractor's (not yet seen for a grounder; the same kind of worker)"}
_SCRATCH_SUFFIX = re.compile(r"\.tmp-[a-z]{1,12}")
# How a subagent hands its final reply back to the orchestrator; found by the live probe, which it
# would otherwise block (the reply then never arrives).
REPLY_TOOLS = {"SubagentHandback"}


def _resolve(raw: str, root: Path) -> Path:
    p = Path(raw)
    return (p if p.is_absolute() else root / p).resolve()


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _rel(path: Path, root: Path) -> str | None:
    """`path` relative to the repo root with forward slashes, or None when it lies outside the root."""
    a, b = os.path.normcase(str(path)), os.path.normcase(str(root.resolve())).rstrip("\\/")
    if not a.startswith(b + os.sep):
        return None
    return a[len(b) + 1:].replace("\\", "/")


def decide(payload: dict, agent: str | None = None, root: Path = ROOT, exact_read: str | None = None,
           exact_write: str | None = None) -> tuple[bool, str, str]:
    """Returns (enforced, decision, reason). Not enforced means the hook leaves the call alone.

    `exact_read` / `exact_write` (the per-session registration): the one input and output file of this session;
    a call must match its agent's pattern AND that file."""
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
    rel = _rel(path, root)
    read_rx, write_rx, read_txt, write_txt = PATHS[who]
    rx, txt, exact = (read_rx, read_txt, exact_read) if tool == "Read" else (write_rx, write_txt, exact_write)
    verb = "Read" if tool == "Read" else "Write"
    if rel is None or not rx.match(rel):
        return True, "deny", f"the {who} may only {verb} {txt}, not {raw!r}"
    if exact is not None and not _same(path, _resolve(exact, root)):
        return True, "deny", f"this {who} session may only {verb} {exact!r}, not {raw!r}"
    if tool == "Read":
        return True, "allow", "a batch file" if who == "extractor" else "its input file"
    return True, "allow", "its own output file"


def _session_records(log_dir) -> tuple[list[dict], int]:
    """(the decisions logged so far in this session's own folder, how many files could not be read)."""
    recs, bad = [], 0
    if log_dir and Path(log_dir).is_dir():
        for f in Path(log_dir).glob("*.json"):
            try:
                x = json.loads(f.read_bytes().decode("utf-8"))
            except (OSError, ValueError):
                bad += 1
                continue
            if isinstance(x, dict):
                recs.append(x)
            else:
                bad += 1
    return recs, bad


def _readback_ok(path: Path, root: Path, session_log) -> bool:
    """Conditions (2) to (4) of READBACK_ONCE: written and on disk, not read back before, the log fully readable."""
    recs, bad = _session_records(session_log)
    on = lambda r: bool(r.get("path")) and _same(_resolve(r["path"], root), path)
    written = any(r.get("tool") == "Write" and r.get("decision") == "allow" and on(r) for r in recs)
    again = any(r.get("tool") == "Read" and r.get("denial_kind") == "self_check" and on(r) for r in recs)
    return written and not again and not bad and path.is_file() and path.stat().st_size > 0


def _scratch_ok(path: Path, root: Path, exact_write: str, session_log) -> bool:
    """Conditions (2) to (5) of SCRATCH_ONCE: the exact output + `.tmp-<letters>` in its folder, written and on disk, first of its kind."""
    out = _resolve(exact_write, root)
    if os.path.normcase(str(path.parent)) != os.path.normcase(str(out.parent)):
        return False
    name, base = path.name, out.name
    if len(name) <= len(base) or os.path.normcase(name[:len(base)]) != os.path.normcase(base):
        return False
    if not _SCRATCH_SUFFIX.fullmatch(name[len(base):]):
        return False
    recs, bad = _session_records(session_log)
    on = lambda r: bool(r.get("path")) and _same(_resolve(r["path"], root), out)
    written = any(r.get("tool") == "Write" and r.get("decision") == "allow" and on(r) for r in recs)
    again = any(r.get("tool") == "Write" and r.get("denial_kind") == "self_check" for r in recs)
    return written and not again and not bad and out.is_file() and out.stat().st_size > 0


def denial_kind(payload: dict, agent: str | None = None, root: Path = ROOT, exact_write: str | None = None,
                session_log=None) -> tuple[str, str]:
    """('self_check', why) for an exact documented combination, else ('unexpected', ''). For a denied call only.
    `session_log`: this session's own log folder (the per-session registration passes it)."""
    who = agent or payload.get("agent_type")
    tool = payload.get("tool_name", "")
    raw = (payload.get("tool_input") or {}).get("file_path") or ""
    if not raw or who not in FENCED:
        return "unexpected", ""
    path = _resolve(raw, root)
    rel = _rel(path, root)
    for (op, target), why in SELF_CHECKS.get(who, {}).items():
        if tool == op and rel is not None and rel.lower() == target.lower():
            return "self_check", why
    if tool == "Read" and who in OWN_OUTPUT_READ and exact_write and _same(path, _resolve(exact_write, root)):
        return "self_check", OWN_OUTPUT_READ[who]
    if tool == "Read" and who in READBACK_ONCE and exact_write and _same(path, _resolve(exact_write, root)):
        if _readback_ok(path, root, session_log):
            return "self_check", READBACK_ONCE[who]
    if tool == "Write" and who in SCRATCH_ONCE and exact_write and _scratch_ok(path, root, exact_write, session_log):
        return "self_check", SCRATCH_ONCE[who]
    return "unexpected", ""


def _content_chars(payload: dict) -> int | None:
    """The length of what a Write call carries (its text is never logged: it may hold email text)."""
    content = (payload.get("tool_input") or {}).get("content")
    return len(content) if isinstance(content, str) else None


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def main(argv: list[str]) -> int:
    agent = _arg(argv, "--agent")
    exact_read, exact_write, session_log = _arg(argv, "--read"), _arg(argv, "--write"), _arg(argv, "--log-dir")
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    enforced, decision, reason = decide(payload, agent, exact_read=exact_read, exact_write=exact_write)
    if enforced or payload.get("agent_type") or payload.get("agent_id"):
        # One file per decision: both registrations fire at the same moment, and two processes
        # appending to one file on Windows can overwrite each other (the live probe lost a record).
        log_dir = Path(session_log or os.environ.get("FENCE_LOG_DIR") or ROOT / "data" / "logs" / "fence")
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        mode = "session" if (exact_read or exact_write) else ("frontmatter" if agent else "settings")
        kind, why = (denial_kind(payload, agent, exact_write=exact_write, session_log=session_log)
                     if decision == "deny" else (None, ""))
        rec = {"at": now.isoformat(timespec="milliseconds"), "mode": mode, "agent_type": payload.get("agent_type"),
               "denial_kind": kind, "self_check": why or None,
               "agent_id": payload.get("agent_id"), "tool_use_id": payload.get("tool_use_id"),
               "tool": payload.get("tool_name"), "path": (payload.get("tool_input") or {}).get("file_path"),
               "enforced": enforced, "decision": decision, "reason": reason, "payload_keys": sorted(payload),
               # where Claude Code keeps the run's transcript: finalize reads the model id from it
               # (src/runmodel.py); a path, never transcript contents
               "session_id": payload.get("session_id"), "transcript_path": payload.get("transcript_path"),
               # failure diagnosis (M4 2b): how the session was running and how large a Write was, never what it said
               "permission_mode": payload.get("permission_mode"), "content_chars": _content_chars(payload)}
        name = f"{now.strftime('%Y%m%dT%H%M%S%f')}-{mode}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        (log_dir / name).write_bytes(json.dumps(rec).encode("utf-8"))
    if enforced and decision == "deny":
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": "fence: " + reason}}
        if mode == "session" and kind == "unexpected":         # an unexpected denial stops the session itself
            out.update({"continue": False, "stopReason": "fence: unexpected denial, session stopped: " + reason})
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
