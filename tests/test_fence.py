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
    # the gold standard is never visible to a model under evaluation
    ("Read", "eval/benchmark/labels.json", False),
    ("Read", "eval/benchmark/evidence_spans.json", False),
    ("Read", "eval/benchmark/negative_claims.json", False),
    ("Read", "eval/benchmark/match_adjudications.json", False),
    ("Read", "data/extract/bench_runs/haiku_v8c_score_v6.json", False),
    # M4 step 1a: every slice writes below its own staging folder; the M3 shared folder is closed
    ("Write", "data/extract/staging/bench/b001.json", True),
    ("Write", ROOT / "data" / "extract" / "staging" / "dev" / "b012.json", True),
    ("Write", "data/extract/staging/all/b049.json", True),
    ("Write", "data/extract/staging/b001.json", False),                          # the M3 shared folder
    ("Write", "data/extract/staging/prod/b001.json", False),                     # not a known slice
    ("Write", "data/extract/staging/bench/accepted/b001.json", False),           # a sub-folder
    ("Write", "data/extract/bench/verified/b001.json", False),                   # code's folder
    ("Write", "data/extract/verified/b001.json", False),
    ("Write", "data/extract/staging/bench/../../bench/verified/b001.json", False),
    ("Write", "data/extract/staging/bench/notes.txt", False),
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


def test_each_decision_records_where_the_run_transcript_is(tmp_path):
    import os
    env = {**os.environ, "FENCE_LOG_DIR": str(tmp_path / "fence")}
    p = {**call("Read", "data/batches/bench/b001.txt"), "agent_id": "a1", "session_id": "s1",
         "transcript_path": "C:/x/s1.jsonl", "tool_response": "email text must never be logged"}
    subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", "extractor"],
                   input=json.dumps(p).encode(), capture_output=True, check=True, env=env)
    (rec,) = [json.loads(f.read_text()) for f in (tmp_path / "fence").iterdir()]
    assert rec["transcript_path"] == "C:/x/s1.jsonl" and rec["session_id"] == "s1"
    assert "email text" not in json.dumps(rec)


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


@pytest.mark.parametrize("tool, path, ok", [
    ("Read", "data/ground/packets/bench/haiku_v8c/g001.txt", True),
    ("Read", ROOT / "data" / "ground" / "packets" / "dev" / "dev_b004" / "g104.txt", True),
    ("Read", "data/ground/packets/haiku_v8c/g001.txt", False),                 # the M3 layout: no slice
    ("Read", "data/ground/packets/bench/haiku_v8c/g001.manifest.json", False), # offsets and claims map: code only
    ("Read", "data/ground/packets/bench/g001.txt", False),                     # not inside a run folder
    ("Read", "data/ground/packets/bench/x/../../../../README.md", False),
    ("Read", "data/batches/bench/b001.txt", False),                            # the extractor's input, not its own
    ("Read", "data/extract/bench_runs/haiku_v8c_b001.json", False),
    ("Read", "eval/benchmark/labels.json", False),                             # the gold standard, never
    ("Read", "eval/benchmark/evidence_spans.json", False),
    ("Read", "eval/benchmark/negative_claims.json", False),
    ("Read", "corpus/farmer-d/logistics/673.txt", False),
    ("Write", "data/ground/staging/bench/g001.json", True),
    ("Write", "data/ground/staging/g001.json", False),                         # the M3 shared folder
    ("Write", "data/ground/staging/bench/accepted/g001.json", False),
    ("Write", "data/ground/bench/verified/g001.json", False),
    ("Write", "data/extract/staging/bench/b001.json", False),                  # the extractor's output
    ("Write", ".claude/agents/grounder.md", False),
])
def test_the_grounder_reads_only_packet_files_and_writes_only_its_output(tool, path, ok):
    enforced, decision, _ = fence.decide(call(tool, path, agent_type="grounder"))
    assert enforced and decision == ("allow" if ok else "deny")


def test_the_extractor_cannot_use_the_grounders_paths_and_the_grounder_has_no_other_tool():
    assert fence.decide(call("Read", "data/ground/packets/bench/haiku_v8c/g001.txt"))[1] == "deny"
    assert fence.decide(call("Write", "data/ground/staging/bench/g001.json"))[1] == "deny"
    for tool in ("Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Agent"):
        assert fence.decide(call(tool, "data/ground/staging/g001.json", agent_type="grounder"))[:2] == (True, "deny")
    assert fence.decide(call("Write", "src/verify.py", agent_type=None), agent="grounder")[:2] == (True, "deny")


def test_the_grounder_agent_has_only_read_and_write_and_registers_the_fence():
    text = (ROOT / ".claude" / "agents" / "grounder.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    assert "tools: Read, Write\n" in front and "--agent grounder" in front and "onFailure: block" in front


@pytest.mark.parametrize("agent, tool, path, ok", [
    ("synthesizer", "Read", "data/synth/inputs/dev/s001.txt", True),
    ("synthesizer", "Write", "data/synth/staging/dev/s001.json", True),
    ("synthesizer", "Write", "data/synth/staging/s001.json", False),          # the M3 shared folder
    ("synthesizer", "Read", "data/synth/inputs/dev/m001.txt", False),         # the merger's input
    ("synthesizer", "Read", "data/extract/verified/b001.json", False),         # only its code-built input
    ("synthesizer", "Read", "corpus/farmer-d/logistics/673.txt", False),
    ("synthesizer", "Write", "data/synth/verified/s001.json", False),
    ("synthesizer", "Read", "eval/benchmark/labels.json", False),
    ("artifact-writer", "Read", "data/act/packets/dev/a001.txt", True),
    ("artifact-writer", "Write", "data/act/staging/dev/a001.md", True),
    ("artifact-writer", "Write", "data/act/staging/a001.md", False),          # the M3 shared folder
    ("artifact-writer", "Write", "output/artifacts/a001.md", False),           # code publishes, never the model
    ("artifact-writer", "Write", "data/act/staging/dev/a001.json", False),
    ("merger", "Read", "data/synth/inputs/all/m001.txt", True),
    ("merger", "Write", "data/synth/staging/all/m001.json", True),
    ("merger", "Read", "data/synth/inputs/all/s001.txt", False),             # chunk inputs are the synthesizer's
    ("merger", "Write", "data/synth/staging/all/s001.json", False),
    ("merger", "Read", "data/extract/all/verified/b001.json", False),
    ("artifact-writer", "Read", "data/synth/staging/s001.json", False),
    ("artifact-writer", "Read", "eval/benchmark/evidence_spans.json", False),
])
def test_the_synthesizer_and_artifact_writer_are_fenced_to_their_own_paths(agent, tool, path, ok):
    enforced, decision, _ = fence.decide(call(tool, path, agent_type=agent))
    assert enforced and decision == ("allow" if ok else "deny")
    for t in ("Bash", "Edit", "WebFetch"):
        assert fence.decide(call(t, path, agent_type=agent))[:2] == (True, "deny")


@pytest.mark.parametrize("tool, path, exact_write, kind", [
    ("Read", "schemas/extract-v6.schema.json", None, "self_check"),                  # named in its prompt
    ("Read", str(ROOT / "schemas" / "extract-v6.schema.json"), None, "self_check"),
    ("Read", "data/extract/staging/all/b007.json", "data/extract/staging/all/b007.json", "self_check"),   # its own output
    ("Read", "data/extract/staging/all/b008.json", "data/extract/staging/all/b007.json", "unexpected"),   # another's
    ("Read", "data/extract/staging/all/b007.json", None, "unexpected"),             # own output unknown outside a session
    ("Read", "schemas/extract-v7.schema.json", None, "unexpected"),                  # not the documented file
    ("Write", "schemas/extract-v6.schema.json", None, "unexpected"),                 # not the documented operation
    ("Read", "README.md", None, "unexpected"),
    ("Read", "eval/benchmark/labels.json", None, "unexpected"),
])
def test_only_exact_documented_self_checks_are_kind_self_check_and_they_stay_denied(tool, path, exact_write, kind):
    assert fence.decide(call(tool, path), exact_write=exact_write)[:2] == (True, "deny")   # never granted
    assert fence.denial_kind(call(tool, path), exact_write=exact_write)[0] == kind
    assert fence.denial_kind(call("Read", "schemas/extract-v6.schema.json", agent_type="grounder"))[0] == "unexpected"


def test_an_unexpected_denial_stops_the_session_a_self_check_does_not_and_both_are_logged(tmp_path):
    hook = [sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", "extractor",
            "--read", "data/batches/all/b007.txt", "--write", "data/extract/staging/all/b007.json",
            "--log-dir", str(tmp_path / "s")]
    run = lambda p: json.loads(subprocess.run(hook, input=json.dumps(p).encode(), capture_output=True, check=True).stdout)
    sc = run(call("Read", "schemas/extract-v6.schema.json"))
    assert sc["hookSpecificOutput"]["permissionDecision"] == "deny" and "continue" not in sc
    un = run(call("Read", "README.md"))
    assert un["hookSpecificOutput"]["permissionDecision"] == "deny" and un["continue"] is False and "stopped" in un["stopReason"]
    log = sorted((json.loads(p.read_text()) for p in (tmp_path / "s").iterdir()), key=lambda r: r["at"])
    assert [(r["decision"], r["denial_kind"]) for r in log] == [("deny", "self_check"), ("deny", "unexpected")]
    assert "schema" in log[0]["self_check"]
    pattern = [sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", "extractor"]   # no session paths
    out = json.loads(subprocess.run(pattern, input=json.dumps(call("Read", "README.md")).encode(), capture_output=True,
                                    check=True, env={**__import__("os").environ, "FENCE_LOG_DIR": str(tmp_path / "p")}).stdout)
    assert "continue" not in out                                  # only the session registration knows enough to stop


def test_the_fence_and_the_pipeline_admit_the_same_slices():
    from src import extract
    assert tuple(fence.SLICES) == tuple(extract.SLICES)


@pytest.mark.parametrize("tool, path, ok", [
    ("Read", "data/batches/all/b007.txt", True),                                # this session's own input
    ("Write", "data/extract/staging/all/b007.json", True),                     # ... and output
    ("Read", "data/batches/all/b008.txt", False),                              # a sibling batch of the same slice
    ("Write", "data/extract/staging/all/b008.json", False),                    # another session's output
    ("Write", "data/extract/staging/dev/b007.json", False),                    # the same id in another slice
    ("Read", "data/batches/dev/b007.txt", False),
])
def test_a_session_is_held_to_exactly_its_own_input_and_output(tool, path, ok):
    """M4 step 1a: the per-session registration adds the exact files to the pattern check."""
    d = fence.decide(call(tool, path), exact_read="data/batches/all/b007.txt", exact_write="data/extract/staging/all/b007.json")
    assert d[:2] == (True, "allow" if ok else "deny")


def test_an_exact_path_never_widens_the_pattern():
    """A session told to write outside its agent's pattern is still denied: both checks must pass."""
    d = fence.decide(call("Write", "src/verify.py"), exact_write="src/verify.py")
    assert d[:2] == (True, "deny")
    d = fence.decide(call("Read", "README.md", agent_type="grounder"), exact_read="README.md")
    assert d[:2] == (True, "deny")


def test_the_session_hook_logs_to_the_sessions_own_folder_and_denies_a_sibling(tmp_path):
    hook = [sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", "extractor",
            "--read", "data/batches/all/b007.txt", "--write", "data/extract/staging/all/b007.json",
            "--log-dir", str(tmp_path / "session")]
    run = lambda p: subprocess.run(hook, input=json.dumps(p).encode(), capture_output=True, check=True)
    assert run(call("Read", "data/batches/all/b007.txt")).stdout == b""
    hso = json.loads(run(call("Read", "data/batches/all/b008.txt")).stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    log = sorted((json.loads(p.read_text()) for p in (tmp_path / "session").iterdir()), key=lambda r: r["at"])
    assert [(r["decision"], r["mode"]) for r in log] == [("allow", "session"), ("deny", "session")]
