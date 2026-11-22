"""The fence around the extractor (CLAUDE.md rule 7): Read its batch file, Write its output, nothing else."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fence_hook", ROOT / "tools" / "fence_hook.py")
fence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fence)


def call(tool, path, agent_type="extractor"):
    p = {"tool_name": tool, "tool_input": {"file_path": str(path)}}
    if agent_type:
        p["agent_type"] = agent_type
    return p


@pytest.mark.parametrize("tool, path, ok", [
    ("Read", "data/batches/bench/b001.txt", True),
    ("Read", ROOT / "data" / "batches" / "dev" / "b004.txt", True),             # absolute
    ("Read", str(ROOT / "data" / "batches" / "bench" / "b001.txt").upper(), True),   # Windows case
    ("Read", "data/batches/bench/manifest.json", False),
    ("Read", "data/batches/b001.txt", False),                                    # not inside a slice
    ("Read", "data/batches/bench/../../../src/verify.py", False),                # climbs out
    ("Read", "corpus/farmer-d/logistics/670.txt", False),
    ("Read", "README.md", False),
    ("Write", "data/extract/staging/b001.json", True),
    ("Write", ROOT / "data" / "extract" / "staging" / "b012.json", True),
    ("Write", "data/extract/staging/accepted/b001.json", False),                 # a sub-folder
    ("Write", "data/extract/verified/b001.json", False),
    ("Write", "data/extract/staging/../verified/b001.json", False),
    ("Write", "data/extract/staging/notes.txt", False),
    ("Write", "src/verify.py", False),
])
def test_the_extractor_reads_only_batch_files_and_writes_only_its_output(tool, path, ok):
    enforced, decision, _ = fence.decide(call(tool, path))
    assert enforced and decision == ("allow" if ok else "deny")


def test_every_other_tool_is_denied_to_the_extractor_and_other_agents_are_left_alone():
    for tool in ("Edit", "Bash", "Glob", "Grep", "WebFetch", "NotebookEdit"):
        assert fence.decide(call(tool, "data/extract/staging/b001.json"))[:2] == (True, "deny")
    assert fence.decide({"tool_name": "SubagentHandback", "agent_type": "extractor", "tool_input": {}})[1] == "allow"
    assert fence.decide(call("Write", "src/verify.py", agent_type=None))[0] is False          # the main session
    assert fence.decide(call("Write", "src/verify.py", agent_type="general-purpose"))[0] is False
    # From the agent's own frontmatter the fence holds even if the payload does not name the agent.
    assert fence.decide(call("Write", "src/verify.py", agent_type=None), agent="extractor")[:2] == (True, "deny")


def test_the_hook_command_denies_in_the_format_claude_code_reads_and_logs_each_decision(tmp_path):
    import os
    env = {**os.environ, "FENCE_LOG_DIR": str(tmp_path / "fence")}            # never the real log
    run = lambda p: subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", "extractor"],
                                   input=json.dumps(p).encode(), capture_output=True, check=True, env=env)
    hso = json.loads(run(call("Read", "README.md")).stdout)["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse" and hso["permissionDecision"] == "deny"
    assert run(call("Read", "data/batches/bench/b001.txt")).stdout == b""     # no decision: normal flow
    log = sorted((json.loads(p.read_text()) for p in (tmp_path / "fence").iterdir()), key=lambda r: r["at"])
    assert [(r["decision"], r["mode"]) for r in log] == [("deny", "frontmatter"), ("allow", "frontmatter")]


def test_two_hooks_firing_at_once_both_keep_their_record(tmp_path):
    import os
    env = {**os.environ, "FENCE_LOG_DIR": str(tmp_path / "fence")}
    hook = [sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py")]
    payload = json.dumps(call("Write", "src/verify.py")).encode()
    procs = [subprocess.Popen(hook + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
             for args in (["--from-settings"], ["--agent", "extractor"]) * 5]
    for p in procs:
        p.communicate(payload)
    recs = [json.loads(p.read_text()) for p in (tmp_path / "fence").iterdir()]
    assert len(recs) == 10 and all(r["decision"] == "deny" for r in recs)        # none lost, none torn
