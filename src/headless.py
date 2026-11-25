"""Run one fenced agent as a headless Claude Code session (`claude -p`), on the Pro subscription.

Why: Claude Code loads `.claude/agents/` when a session starts, so an agent added during a session
(the grounder) cannot be spawned as a subagent until the next session. A headless `claude -p --agent X`
process loads it, and it also reports its own duration, turns, tokens and the model ids that answered,
which is what the runtime measurements need. It is the PLAN 3.2 fallback ("Python launches one
tool-less `claude -p` per batch"), with Read and Write only.

Safety, in layers: the process gets only the Read and Write tools (`--tools`), no MCP servers
(`--strict-mcp-config`), and the fence hook is passed in `--settings` with `--agent <name>`, so it is
enforced whatever the hook payload says (the project settings register it a second time). Writes are
auto-accepted only so the run can finish unattended; the hook runs before that and denies every path
but the agent's own output file. `ANTHROPIC_API_KEY` must be unset (CLAUDE.md rule 3): the run refuses
to start otherwise, so it can never bill the API.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Failure kinds (M4 step 1c). Only BAD_OUTPUT and TIMEOUT spend one of a batch's retries (PLAN R8). USAGE_LIMIT
# stops the run cleanly; RATE_LIMITED is retried after a back-off without spending a retry.
OK, USAGE_LIMIT, RATE_LIMITED, TIMEOUT, ERROR, TERMINATED = "ok", "usage_limit", "rate_limited", "timeout", "error", "terminated"
# The CLI's wording is not fixed, so these are matched loosely; the breaker in src/pipeline.py stops a run on
# repeated empty failures whatever their text says. A real limit message, once seen, becomes a test fixture.
_USAGE = re.compile(r"usage limit|limit reached|hit your (?:usage )?limit|out of (?:extra )?usage|"
                    r"(?:5-hour|five-hour|weekly|session) limit", re.IGNORECASE)
_RATE = re.compile(r"rate[ _-]?limit|overloaded|\b429\b|\b529\b|too many requests", re.IGNORECASE)
_RESET = re.compile(r"(resets?\b[^\n|]{0,60}|\|\d{9,11}\b)", re.IGNORECASE)

# A fenced session never loads project instruction files (M4 step 2b, 23 Nov 2026). Set A stopped when an extractor
# followed CLAUDE.md ("log mistakes in private/DEVLOG.md") and tried to write there. The fence denied it; this removes
# the cause. `claudeMdExcludes` (Claude Code 2.1.330: globs matched against absolute paths with forward slashes, for
# User, Project and Local files) is passed in each worker's own `--settings`, never in a settings file, so interactive
# sessions keep CLAUDE.md. Checked live with a positive control (tools/instructions_probe.py): without it a session
# loads CLAUDE.md, CLAUDE.local.md and .claude/rules; with it, none. An InstructionsLoaded hook (tools/instructions_hook.py)
# records anything that still loads, and the pipeline stops the run on it. Nothing here changes a tool, agent, fence
# hook or file access. SESSION_ENV_VERSION is part of every stage's cache key, so no output made with the project
# instructions in context is reused. `autoMemoryEnabled: false` keeps a worker from being told to save memories.
INSTRUCTION_EXCLUDES = ["**/CLAUDE.md", "**/CLAUDE.local.md", "**/.claude/rules/**"]
FENCED_SETTINGS = {"claudeMdExcludes": INSTRUCTION_EXCLUDES, "autoMemoryEnabled": False}
SESSION_ENV_VERSION = "no-project-instructions-v2"
# One sentence added to the run instruction of a worker that writes one output file (never to its analytical prompt): the fence
# denies reading that output back, so asking the worker not to avoids a pointless denial.
NO_READBACK = "Do not read your output file back after writing it: the pipeline checks it."
NO_READBACK_SHA256 = hashlib.sha256(NO_READBACK.encode("utf-8")).hexdigest()    # recorded with each output; not in a cache key
INSTRUCTIONS_DIR = "instructions"          # below a session's fence log folder: one metadata record per file loaded

_running: set[subprocess.Popen] = set()
_running_lock = threading.Lock()


EFFORTS = ("low", "medium", "high", "xhigh", "max")          # `claude --effort` (2.1.330): sets how much the model thinks


def command(agent: str, prompt: str, model: str, read: str | None = None, write: str | None = None,
            log_dir: str | None = None, extra_settings: dict | None = None, stream: bool = False,
            effort: str | None = None, isolate: bool = True) -> list[str]:
    """`read`, `write`: this session's one input and output file (repo-relative); with them the hook holds the
    session to exactly those two files, and logs its decisions to `log_dir`. `extra_settings`: further settings
    keys for this session (an M4 2a lever; recorded with the run). `stream`: stream-json output, kept as the
    session's transcript (M4 2a diagnostics). `isolate=False` keeps the project instructions in: only the live
    check's positive control (tools/instructions_probe.py) passes it, never `run()`."""
    hook = f'"$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe" -I "$CLAUDE_PROJECT_DIR/tools/fence_hook.py" --agent {agent}'
    if read:
        hook += f' --read "{read}"'
    if write:
        hook += f' --write "{write}"'
    if log_dir:
        hook += f' --log-dir "{log_dir}"'
    hooks = {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": hook, "timeout": 10}]}]}
    if log_dir:
        # what the session loads is recorded beside what it does: metadata only, in a folder the fence summary ignores
        ihook = ('"$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe" -I "$CLAUDE_PROJECT_DIR/tools/instructions_hook.py" '
                 f'--log-dir "{(Path(log_dir) / INSTRUCTIONS_DIR).as_posix()}"')
        hooks["InstructionsLoaded"] = [{"hooks": [{"type": "command", "command": ihook, "timeout": 10}]}]
    settings = {**(extra_settings or {}), **(FENCED_SETTINGS if isolate else {}), "hooks": hooks}
    exe = shutil.which("claude") or "claude"
    return [exe, "-p", prompt, "--agent", agent, "--model", model, "--tools", "Read,Write",
            "--allowedTools", "Read", "Write", "--permission-mode", "acceptEdits", "--strict-mcp-config",
            "--settings", json.dumps(settings), "--output-format", "stream-json" if stream else "json",
            *(["--verbose"] if stream else []), *(["--effort", effort] if effort else []), "--no-session-persistence"]


def classify(rep: dict) -> tuple[str, str | None]:
    """(failure kind, the reset text exactly as the CLI wrote it, if any). Pure: rep is what run() returned."""
    if rep.get("exit_code") == -2:
        return TERMINATED, None
    if rep.get("exit_code") == -1 and rep.get("stderr_tail") == "timeout":
        return TIMEOUT, None
    if not rep.get("is_error") and rep.get("exit_code") == 0:
        return OK, None
    text = f"{rep.get('result') or ''}\n{rep.get('stderr_tail') or ''}\n{rep.get('stdout_tail') or ''}"
    if _USAGE.search(text):
        m = _RESET.search(text)
        return USAGE_LIMIT, (m.group(1).strip() if m else None)
    if _RATE.search(text):
        return RATE_LIMITED, None
    return ERROR, None


def terminate_all() -> int:
    """Stop every session still running (a critical validation failed). Returns how many were stopped."""
    with _running_lock:
        procs = list(_running)
        for p in procs:
            p.stopped_by_run = True          # run() then reports it as TERMINATED, never as a model failure
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass
    return len(procs)


def running() -> int:
    with _running_lock:
        return len(_running)


def parse_stream(text: str) -> tuple[dict, dict]:
    """(the final `result` event, the `system` init event) of a stream-json transcript; {} when absent."""
    result, init = {}, {}
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev
        elif ev.get("type") == "system" and ev.get("subtype") == "init" and not init:
            init = ev
    return result, init


def run(agent: str, prompt: str, model: str = "haiku", timeout: int = 900, cwd: Path = ROOT,
        read: str | None = None, write: str | None = None, log_dir: str | None = None,
        env_extra: dict | None = None, extra_settings: dict | None = None, transcript: str | None = None,
        effort: str | None = None) -> dict:
    """Run once; return what the CLI reported plus wall time. Never raises on a model failure.

    `env_extra` / `extra_settings`: per-session configuration (an M4 2a lever), recorded in the result.
    `transcript`: keep the session's stream-json transcript at this path (it holds email text: data/ only)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is set: refusing, a run would bill the API (CLAUDE.md rule 3)")
    if effort is not None and effort not in EFFORTS:
        raise ValueError(f"effort {effort!r} is not one of {EFFORTS}")
    if "ANTHROPIC_API_KEY" in (env_extra or {}):
        raise RuntimeError("refusing to pass ANTHROPIC_API_KEY to a session (CLAUDE.md rule 3)")
    env = {**os.environ, **(env_extra or {}), "CLAUDE_PROJECT_DIR": str(cwd)}
    t0 = time.time()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))
    proc = subprocess.Popen(command(agent, prompt, model, read, write, log_dir, extra_settings, bool(transcript), effort),
                            cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with _running_lock:
        _running.add(proc)
    try:
        try:
            ob, eb = proc.communicate(timeout=timeout)
            out, err, code = ob.decode("utf-8", "replace"), eb.decode("utf-8", "replace"), proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            ob, _ = proc.communicate()
            out, err, code = (ob or b"").decode("utf-8", "replace"), "timeout", -1
    finally:
        with _running_lock:
            _running.discard(proc)
    if getattr(proc, "stopped_by_run", False):
        code = -2
    wall = round(time.time() - t0, 1)
    init = {}
    if transcript:
        Path(transcript).parent.mkdir(parents=True, exist_ok=True)
        Path(transcript).write_text(out, encoding="utf-8", newline="")        # exactly as the CLI wrote it
        rep, init = parse_stream(out)
        if not rep:
            rep = {"parse_error": True, "stdout_tail": out[-400:]}
    else:
        try:
            rep = json.loads(out)
        except json.JSONDecodeError:
            rep = {"parse_error": True, "stdout_tail": out[-400:]}
    usage = rep.get("usage") or {}
    res = {"agent": agent, "model_requested": model, "exit_code": code, "wall_seconds": wall,
           "started_at_utc": started, "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "duration_ms": rep.get("duration_ms"), "duration_api_ms": rep.get("duration_api_ms"),
           "num_turns": rep.get("num_turns"), "is_error": rep.get("is_error", code != 0),
           "subtype": rep.get("subtype"), "result": (rep.get("result") or "")[:500],
           "models_observed": sorted((rep.get("modelUsage") or {}).keys()),
           "model_usage": rep.get("modelUsage"),
           "tokens": {k: usage.get(k) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens")},
           # part of output_tokens (never added to it): the CLI's own count of thinking tokens
           "thinking_tokens": (usage.get("output_tokens_details") or {}).get("thinking_tokens"),
           # notional only: on the Pro subscription nothing is billed; recorded to compare runs
           "notional_cost_usd": rep.get("total_cost_usd"), "stderr_tail": err[-400:],
           "stdout_tail": rep.get("stdout_tail", ""),
           "config": {"env_extra": env_extra or {}, "instruction_excludes": list(INSTRUCTION_EXCLUDES), "auto_memory": False,
                      "session_env": SESSION_ENV_VERSION,
                      "extra_settings": extra_settings or {}, "transcript": transcript,
                      "effort": effort,
                      "init": {k: init.get(k) for k in ("model", "claude_code_version", "permissionMode", "tools",
                                                        "apiKeySource", "per_turn_effort_active") if k in init}}}
    res["kind"], res["reset_text"] = classify(res)
    return res


def instructions_loaded(log_dir) -> list[dict]:
    """The instruction files a session reported loading (tools/instructions_hook.py records): metadata only, no
    contents. A correctly launched worker has none. A record that cannot be read counts as a file loaded (fails closed)."""
    d, out = Path(log_dir) / INSTRUCTIONS_DIR, []
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                out.append({k: rec.get(k) for k in ("file_path", "memory_type", "load_reason", "size_bytes", "sha256")})
            except (json.JSONDecodeError, UnicodeDecodeError, OSError, AttributeError):
                out.append({"file_path": f.name, "memory_type": "unreadable record"})
    return out


def total_tokens(rec: dict) -> int:
    return sum(v or 0 for v in rec["tokens"].values())
