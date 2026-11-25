"""M4 step 2b: headless workers never load the project's instruction files (CLAUDE.md, CLAUDE.local.md, rules). What a
worker still loads is recorded as metadata only and stops the run. The fence keeps denying every path outside a worker's
own two files. No model is called (CLAUDE.md rule 6): the live proof is tools/instructions_probe.py.

Set A of 2b stopped when an extractor followed CLAUDE.md ("log mistakes in private/DEVLOG.md") and tried to write there."""
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src import headless, pipeline

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fence_hook", ROOT / "tools" / "fence_hook.py")
fence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fence)
HOOK = ROOT / "tools" / "instructions_hook.py"
AGENTS = sorted(fence.PATHS)


def settings_of(cmd):
    return json.loads(cmd[cmd.index("--settings") + 1])


def hook_cmds(settings, event):
    return [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]


@pytest.mark.parametrize("agent", AGENTS)
def test_every_worker_launch_excludes_instruction_files_and_keeps_its_agent_tools_and_hooks(agent):
    cmd = headless.command(agent, "go", "haiku", read="data/x/in.txt", write="data/x/out.json", log_dir="data/logs/s/a1")
    s = settings_of(cmd)
    for pattern in ("**/CLAUDE.md", "**/CLAUDE.local.md", "**/.claude/rules/**"):
        assert pattern in s["claudeMdExcludes"]
    assert s["claudeMdExcludes"] == headless.INSTRUCTION_EXCLUDES and s["autoMemoryEnabled"] is False
    assert "--bare" not in cmd                                           # hooks and subscription sign-in are kept
    assert cmd[cmd.index("--agent") + 1] == agent and cmd[cmd.index("--tools") + 1] == "Read,Write"
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    (fence_cmd,) = hook_cmds(s, "PreToolUse")                            # the fence is still the one hook on every call
    assert "fence_hook.py" in fence_cmd and f"--agent {agent}" in fence_cmd
    assert '--read "data/x/in.txt"' in fence_cmd and '--write "data/x/out.json"' in fence_cmd and "data/logs/s/a1" in fence_cmd
    (instr_cmd,) = hook_cmds(s, "InstructionsLoaded")
    assert "instructions_hook.py" in instr_cmd and "data/logs/s/a1/instructions" in instr_cmd


def test_extra_settings_can_neither_drop_nor_weaken_the_isolation_or_the_fence():
    cmd = headless.command("extractor", "go", "haiku", log_dir="d",
                           extra_settings={"claudeMdExcludes": [], "autoMemoryEnabled": True, "hooks": {}, "alwaysThinkingEnabled": False})
    s = settings_of(cmd)
    assert s["claudeMdExcludes"] == headless.INSTRUCTION_EXCLUDES and s["autoMemoryEnabled"] is False
    assert s["alwaysThinkingEnabled"] is False                          # a lever's own key still passes
    assert "fence_hook.py" in hook_cmds(s, "PreToolUse")[0] and hook_cmds(s, "InstructionsLoaded")


def test_only_the_live_checks_control_keeps_the_instructions_and_run_cannot_ask_for_it():
    control = settings_of(headless.command("extractor", "go", "haiku", log_dir="d", isolate=False))
    assert "claudeMdExcludes" not in control and "autoMemoryEnabled" not in control
    assert "fence_hook.py" in hook_cmds(control, "PreToolUse")[0]       # even the control keeps the fence
    assert "isolate" not in inspect.signature(headless.run).parameters
    assert "isolate=False" not in inspect.getsource(pipeline)


def test_interactive_sessions_keep_the_project_instructions():
    """The exclusion lives only in the worker launch, never in a settings file Claude Code reads for a normal session."""
    for name in ("settings.json", "settings.local.json"):
        f = ROOT / ".claude" / name
        if f.exists():
            s = json.loads(f.read_text(encoding="utf-8"))
            assert "claudeMdExcludes" not in s and "autoMemoryEnabled" not in s
    assert (ROOT / "CLAUDE.md").exists()


def test_the_session_environment_version_names_the_isolation_so_older_outputs_are_not_reused():
    assert headless.SESSION_ENV_VERSION == "no-project-instructions-v2"


# --- the InstructionsLoaded hook records metadata only ---

def run_hook(payload, log_dir=None, raw=None):
    args = [sys.executable, "-I", str(HOOK)] + (["--log-dir", str(log_dir)] if log_dir else [])
    return subprocess.run(args, input=raw if raw is not None else json.dumps(payload).encode(), capture_output=True)


def test_the_instruction_hook_records_which_file_loaded_and_never_its_contents_or_anything_else(tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_text("# Secret project rules\nSECRET-INSTRUCTION-TEXT", encoding="utf-8")
    payload = {"hook_event_name": "InstructionsLoaded", "file_path": str(f), "memory_type": "Project", "load_reason": "session_start",
               "session_id": "s-1", "cwd": str(tmp_path), "prompt": "EMAIL-TEXT-SECRET", "api_key": "sk-ant-SECRET",
               "transcript_path": "t.jsonl"}
    r = run_hook(payload, tmp_path / "log")
    assert r.returncode == 0 and r.stdout == b""                         # prints nothing: it cannot change the session
    (rec_file,) = (tmp_path / "log").glob("*.json")
    text = rec_file.read_text(encoding="utf-8")
    rec = json.loads(text)
    assert rec["file_path"] == str(f) and rec["memory_type"] == "Project" and rec["load_reason"] == "session_start"
    assert rec["size_bytes"] == f.stat().st_size and rec["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    for secret in ("SECRET-INSTRUCTION-TEXT", "EMAIL-TEXT-SECRET", "sk-ant-SECRET"):
        assert secret not in text
    assert "prompt" in rec["payload_keys"] and "prompt" not in rec        # field names only
    assert set(rec) <= {"at", "payload_keys", "size_bytes", "sha256", "unreadable", *HOOK_KEYS()}


def HOOK_KEYS():
    spec = importlib.util.spec_from_file_location("instructions_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.KEYS


def test_the_instruction_hook_never_breaks_a_session(tmp_path):
    for raw, log in ((b"not json", tmp_path / "a"), (b"[]", tmp_path / "b"), (b"", tmp_path / "c")):
        r = run_hook(None, log, raw=raw)
        assert r.returncode == 0 and r.stdout == b""
    assert run_hook({"file_path": "x"}, None).returncode == 0              # no log folder: nothing to write, still fine
    gone = tmp_path / "missing.md"
    assert run_hook({"file_path": str(gone), "memory_type": "User"}, tmp_path / "d").returncode == 0
    (rec,) = (tmp_path / "d").glob("*.json")
    assert json.loads(rec.read_text())["unreadable"] == "FileNotFoundError"   # recorded, not hidden


# --- the pipeline reads the records and stops on them ---

def test_loaded_instruction_records_are_read_back_and_an_unreadable_record_counts_as_loaded(tmp_path):
    assert headless.instructions_loaded(tmp_path) == []
    d = tmp_path / headless.INSTRUCTIONS_DIR
    d.mkdir()
    (d / "instr-1.json").write_text(json.dumps({"file_path": "C:/r/CLAUDE.md", "memory_type": "Project", "load_reason": "session_start",
                                                "size_bytes": 5297, "sha256": "ab", "payload_keys": ["x"]}), encoding="utf-8")
    assert headless.instructions_loaded(tmp_path) == [{"file_path": "C:/r/CLAUDE.md", "memory_type": "Project",
                                                       "load_reason": "session_start", "size_bytes": 5297, "sha256": "ab"}]
    (d / "instr-2.json").write_text("{broken", encoding="utf-8")
    (d / "instr-3.json").write_text("[]", encoding="utf-8")
    loaded = headless.instructions_loaded(tmp_path)
    assert len(loaded) == 3 and {x["memory_type"] for x in loaded[1:]} == {"unreadable record"}


def test_the_fence_summary_ignores_the_instruction_folder(tmp_path):
    (tmp_path / "1.json").write_text(json.dumps({"tool": "Read", "path": "p", "decision": "allow"}), encoding="utf-8")
    (tmp_path / headless.INSTRUCTIONS_DIR).mkdir()
    (tmp_path / headless.INSTRUCTIONS_DIR / "instr-1.json").write_text(json.dumps({"file_path": "CLAUDE.md"}), encoding="utf-8")
    assert pipeline.fence_summary(tmp_path) == {"records": 1, "denied": [], "self_checks": []}


def _rep(**over):
    rep = {"kind": "ok", "output_exists": True, "fence": {"records": 2, "denied": [], "self_checks": []},
           "instructions": {"loaded": []}}
    rep.update(over)
    return rep


def test_a_loaded_instruction_file_is_a_critical_stop_but_a_clean_session_is_not():
    assert "instructions_loaded" in pipeline.CRITICAL
    assert pipeline._session_outcome(_rep(), False) is None
    loaded = _rep(instructions={"loaded": [{"file_path": "C:/r/CLAUDE.md", "memory_type": "Project"}]})
    assert pipeline._session_outcome(loaded, False) == "instructions_loaded"
    both = _rep(instructions=loaded["instructions"], fence={"records": 2, "denied": [{"tool": "Write"}], "self_checks": []})
    assert pipeline._session_outcome(both, False) == "fence_denial"       # a denial is reported first
    mon = pipeline.Monitor({"breaker_consecutive": 3, "breaker_max_output_tokens": 50})
    assert mon.request_stop("instructions_loaded", instructions=loaded["instructions"]["loaded"]) and mon.stop["critical"] is True


# --- the fence still denies every unauthorised path, for every fenced agent ---

FORBIDDEN = ["private/DEVLOG.md", "private/HANDOFF.md", "CLAUDE.md", "PLAN.md", "README.md", ".claude/settings.json",
             "eval/benchmark/labels.json", "eval/benchmark/blind_user.json", "config/devslice.json", "corpus/MANIFEST.csv",
             "tools/fence_hook.py", "src/verify.py", "output/all/report.md",
             "C:/Users/Xigua/.claude/projects/p/memory/MEMORY.md", "C:/Users/Xigua/.claude/CLAUDE.md"]


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("tool", ["Read", "Write"])
@pytest.mark.parametrize("path", FORBIDDEN)
def test_every_fenced_agent_is_denied_every_unauthorised_path_as_an_unexpected_denial(agent, tool, path):
    payload = {"tool_name": tool, "tool_input": {"file_path": path}, "agent_type": agent}
    assert fence.decide(payload)[:2] == (True, "deny")
    assert fence.decide(payload, exact_read=path, exact_write=path)[:2] == (True, "deny")    # a session's exact path never widens it
    assert fence.denial_kind(payload)[0] == "unexpected"


@pytest.mark.parametrize("agent", AGENTS)
def test_a_devlog_write_stops_the_session_for_every_agent_and_logs_its_size_but_not_its_text(agent, tmp_path):
    hook = [sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", agent, "--read", "data/batches/all/b001.txt",
            "--write", "data/extract/staging/all/b001.json", "--log-dir", str(tmp_path / "s")]
    content = "Logged a mistake: MODEL-WRITTEN-TEXT"
    payload = {"tool_name": "Write", "tool_input": {"file_path": "private/DEVLOG.md", "content": content}, "agent_type": agent,
               "permission_mode": "acceptEdits", "session_id": "s-9"}
    out = json.loads(subprocess.run(hook, input=json.dumps(payload).encode(), capture_output=True, check=True).stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and out["continue"] is False
    (rec_file,) = (tmp_path / "s").glob("*.json")
    rec = json.loads(rec_file.read_text(encoding="utf-8"))
    assert (rec["decision"], rec["denial_kind"], rec["tool"], rec["path"]) == ("deny", "unexpected", "Write", "private/DEVLOG.md")
    assert rec["content_chars"] == len(content) and rec["permission_mode"] == "acceptEdits" and rec["session_id"] == "s-9"
    assert "MODEL-WRITTEN-TEXT" not in rec_file.read_text(encoding="utf-8")


SCRATCH = ["b013.json.tmp-check", "b013.json.tmp", "b013.json.bak", "b013.json~", "b013-copy.json", "b013.JSON.bak", "scratch.txt",
           "b014.json", "rejected/b013.json", "accepted/b013.json"]


@pytest.mark.parametrize("name", SCRATCH)
def test_scratch_backup_and_sibling_writes_stay_unexpected_denials_for_the_extractor_and_stop_the_session(name, tmp_path):
    """The run note asks for no scratch files; the fence is what enforces it, and it is unchanged: no exception for any name."""
    own = "data/extract/staging/all/b013.json"
    path = f"data/extract/staging/all/{name}"
    payload = {"tool_name": "Write", "tool_input": {"file_path": path, "content": "ok"}, "agent_type": "extractor"}
    assert fence.decide(payload, exact_read="data/batches/all/b013.txt", exact_write=own)[:2] == (True, "deny")
    assert fence.denial_kind(payload, exact_write=own)[0] == "unexpected"
    if name != "b014.json":                       # another batch's output is a legal name for the pattern, not for this session
        assert fence.decide(payload)[:2] == (True, "deny")        # the pattern-only registrations deny it on their own
    hook = [sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", "extractor", "--read", "data/batches/all/b013.txt",
            "--write", own, "--log-dir", str(tmp_path / "s")]
    out = json.loads(subprocess.run(hook, input=json.dumps(payload).encode(), capture_output=True, check=True).stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and out["continue"] is False
    (rec,) = (tmp_path / "s").glob("*.json")
    assert json.loads(rec.read_text())["denial_kind"] == "unexpected"


def test_the_extractors_own_output_file_is_still_the_only_write_it_is_allowed():
    own = "data/extract/staging/all/b013.json"
    ok = {"tool_name": "Write", "tool_input": {"file_path": own}, "agent_type": "extractor"}
    assert fence.decide(ok, exact_read="data/batches/all/b013.txt", exact_write=own)[:2] == (True, "allow")
