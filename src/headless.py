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

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def command(agent: str, prompt: str, model: str) -> list[str]:
    hook = f'"$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe" -I "$CLAUDE_PROJECT_DIR/tools/fence_hook.py" --agent {agent}'
    settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": hook, "timeout": 10}]}]}}
    exe = shutil.which("claude") or "claude"
    return [exe, "-p", prompt, "--agent", agent, "--model", model, "--tools", "Read,Write",
            "--allowedTools", "Read", "Write", "--permission-mode", "acceptEdits", "--strict-mcp-config",
            "--settings", json.dumps(settings), "--output-format", "json", "--no-session-persistence"]


def run(agent: str, prompt: str, model: str = "haiku", timeout: int = 900, cwd: Path = ROOT) -> dict:
    """Run once; return what the CLI reported plus wall time. Never raises on a model failure."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is set: refusing, a run would bill the API (CLAUDE.md rule 3)")
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)}
    t0 = time.time()
    try:
        p = subprocess.run(command(agent, prompt, model), cwd=cwd, env=env, capture_output=True, timeout=timeout)
        out, err, code = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"), p.returncode
    except subprocess.TimeoutExpired as e:
        out, err, code = (e.stdout or b"").decode("utf-8", "replace"), "timeout", -1
    wall = round(time.time() - t0, 1)
    try:
        rep = json.loads(out)
    except json.JSONDecodeError:
        rep = {"parse_error": True, "stdout_tail": out[-400:]}
    usage = rep.get("usage") or {}
    return {"agent": agent, "model_requested": model, "exit_code": code, "wall_seconds": wall,
            "duration_ms": rep.get("duration_ms"), "duration_api_ms": rep.get("duration_api_ms"),
            "num_turns": rep.get("num_turns"), "is_error": rep.get("is_error", code != 0),
            "subtype": rep.get("subtype"), "result": (rep.get("result") or "")[:500],
            "models_observed": sorted((rep.get("modelUsage") or {}).keys()),
            "model_usage": rep.get("modelUsage"),
            "tokens": {k: usage.get(k) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                 "cache_read_input_tokens")},
            # notional only: on the Pro subscription nothing is billed; recorded to compare runs
            "notional_cost_usd": rep.get("total_cost_usd"), "stderr_tail": err[-400:]}


def total_tokens(rec: dict) -> int:
    return sum(v or 0 for v in rec["tokens"].values())
