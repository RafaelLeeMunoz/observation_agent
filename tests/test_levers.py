"""M4 step 2a tooling, offline: the context report, the transcript breakdown (sizes only, never text), combining
batch outputs for the frozen scorers, and the headless session's transcript and configuration options."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from src import headless

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lever_run", ROOT / "tools" / "lever_run.py")
lever = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lever)


def b(bid, keys, threads, pointers=(), split=None):
    return {"batch_id": bid, "keys": list(keys), "threads": list(threads), "est_tokens": 100, "split_threads": split or {},
            "pointers": [{"key": k, "block": 0, "points_to": t, "reason": "original_in_thread"} for k, t in pointers]}


def test_the_context_report_finds_split_threads_and_pointers_that_cross_batches():
    ok = lever.context_report([b("b001", ["mb/i/1", "mb/i/2"], ["t1"], [("mb/i/2", "mb/i/1")]), b("b002", ["mb/i/3"], ["t2"])])
    assert ok["threads_split"] == [] and ok["pointers_crossing_batches"] == [] and ok["messages_whose_text_changed"] == 0
    bad = lever.context_report([b("b001", ["mb/i/1"], ["t1"], split={"t1": ["b001", "b002"]}),
                                b("b002", ["mb/i/2"], ["t1"], [("mb/i/2", "mb/i/1")])])
    assert bad["threads_split"] == ["t1"] and bad["pointers_crossing_batches"][0]["in_batch"] == "b001"
    assert bad["messages_whose_text_changed"] is None


SECRET = "Please confirm the Katy volumes by noon"


def stream_lines():
    ev = [{"type": "system", "subtype": "init", "model": "claude-haiku-5-5", "claude_code_version": "9.9.9",
           "tools": ["Read", "Write"], "permissionMode": "acceptEdits"},
          {"type": "assistant", "message": {"id": "m1", "model": "claude-haiku-5-5", "usage": {"output_tokens": 40},
                                            "content": [{"type": "thinking", "thinking": "x" * 300}]}},
          {"type": "assistant", "message": {"id": "m1", "model": "claude-haiku-5-5", "usage": {"output_tokens": 120},
                                            "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "a"}}]}},
          {"type": "user", "message": {"content": [{"type": "tool_result", "content": SECRET}]}},
          {"type": "assistant", "message": {"id": "m2", "model": "claude-haiku-5-5", "usage": {"output_tokens": 500},
                                            "content": [{"type": "tool_use", "name": "Write",
                                                         "input": {"file_path": "o", "content": SECRET * 10}},
                                                        {"type": "text", "text": "done"}]}},
          {"type": "assistant", "message": {"id": "m3", "model": "claude-haiku-5-5", "usage": {"output_tokens": 480},
                                            "content": [{"type": "tool_use", "name": "Write",
                                                         "input": {"file_path": "o", "content": SECRET * 9}}]}},
          {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 1000, "num_turns": 3,
           "usage": {"input_tokens": 5, "output_tokens": 1100, "output_tokens_details": {"thinking_tokens": 700}},
           "modelUsage": {"claude-haiku-5-5": {}}, "result": "ok"}]
    return "\n".join(json.dumps(e) for e in ev) + "\n"


def test_the_transcript_breakdown_measures_block_kinds_and_sizes_and_keeps_no_text():
    bd = lever.transcript_breakdown(stream_lines())
    assert bd["assistant_messages"] == 3 and bd["output_tokens_summed"] == 120 + 500 + 480   # max per message id
    assert bd["thinking_present"] and bd["blocks"]["thinking"] == {"count": 1, "chars": 300}
    assert bd["write_calls"] == 2 and bd["blocks"]["tool_use:Read"]["count"] == 1
    assert abs(sum(bd["share_of_output_chars"].values()) - 1) < 0.01
    assert SECRET not in json.dumps(bd)                                   # sizes only: no email text is kept


def test_combine_keeps_every_batchs_messages_and_observations_in_batch_order():
    r1 = {"status": "verified", "batch_id": "b002", "messages": [{"msg_key": "k2"}], "observations": [{"obs_id": "b002-o1"}]}
    r0 = {"status": "verified", "batch_id": "b001", "messages": [{"msg_key": "k1"}], "observations": [{"obs_id": "b001-o1"}],
          "rejected": [{"q": 1}]}
    c = lever.combine([r1, r0])
    assert c["status"] == "verified" and c["batch_ids"] == ["b001", "b002"]
    assert [m["msg_key"] for m in c["messages"]] == ["k1", "k2"] and len(c["observations"]) == 2 and c["rejected"] == [{"q": 1}]
    assert lever.combine([r0, dict(r1, status="failed")])["status"] == "failed"


def test_hard_checks_fail_on_a_fence_denial_a_missing_receipt_or_an_unconfirmed_model():
    ok_ext = [{"res": {"status": "verified", "messages": [{"msg_key": "k1"}]}, "model": "confirmed",
               "fence": {"records": 3, "denied": [], "allowed_outside": [], "held": True}}]
    assert lever.hard_checks(ok_ext, [], ["k1"], {"citations": {"all_valid": True}})["all_hold"]
    broken = [dict(ok_ext[0], fence={"records": 3, "denied": [], "allowed_outside": ["README.md"], "held": False})]
    assert not lever.hard_checks(broken, [], ["k1"], {})["all_hold"]
    assert not lever.hard_checks(ok_ext, [], ["k1", "k2"], {})["every_message_dispositioned_once"]
    assert not lever.hard_checks([dict(ok_ext[0], model="unknown")], [], ["k1"], {})["every_model_confirmed"]
    no_claims = [{"res": {"status": "verified"}, "model": "confirmed",
                  "fence": {"records": 0, "denied": [], "allowed_outside": [], "held": True}, "usage": None}]
    assert lever.hard_checks(ok_ext, no_claims, ["k1"], {})["all_hold"]        # no grounder session, nothing to check


def test_a_session_can_keep_its_stream_transcript_and_records_its_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    src = tmp_path / "stream.txt"
    src.write_text(stream_lines(), encoding="utf-8", newline="")
    seen = {}

    def cmd(agent, prompt, model, read=None, write=None, log_dir=None, extra_settings=None, stream=False, effort=None):
        seen.update(stream=stream, extra=extra_settings, effort=effort)
        return [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(open(r'{src}', 'rb').read())"]
    monkeypatch.setattr(headless, "command", cmd)
    t = tmp_path / "t" / "x.jsonl"
    rep = headless.run("extractor", "go", "haiku", env_extra={"MAX_THINKING_TOKENS": "0"},
                       extra_settings={"alwaysThinkingEnabled": False}, transcript=str(t), effort="low")
    assert seen == {"stream": True, "extra": {"alwaysThinkingEnabled": False}, "effort": "low"}
    assert rep["thinking_tokens"] == 700 and rep["config"]["effort"] == "low"
    assert lever.thinking_from_transcript(t) == 700
    assert rep["kind"] == headless.OK and rep["tokens"]["output_tokens"] == 1100 and rep["models_observed"] == ["claude-haiku-5-5"]
    assert t.read_bytes() == stream_lines().encode("utf-8")          # kept byte for byte
    assert rep["config"]["env_extra"] == {"MAX_THINKING_TOKENS": "0"}
    assert rep["config"]["init"] == {"model": "claude-haiku-5-5", "claude_code_version": "9.9.9", "permissionMode": "acceptEdits",
                                     "tools": ["Read", "Write"]}


def test_the_stream_command_and_extra_settings_keep_the_fence_hook():
    cmd = headless.command("extractor", "go", "haiku", read="r", write="w", extra_settings={"alwaysThinkingEnabled": False},
                           stream=True)
    assert cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd and "--effort" not in cmd
    low = headless.command("extractor", "go", "haiku", effort="low")
    assert low[low.index("--effort") + 1] == "low"
    with pytest.raises(ValueError, match="effort"):
        headless.run("extractor", "go", effort="turbo")
    s = json.loads(cmd[cmd.index("--settings") + 1])
    assert s["alwaysThinkingEnabled"] is False and "fence_hook.py" in s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    evil = headless.command("extractor", "go", "haiku", extra_settings={"hooks": {}})          # cannot drop the fence
    assert "fence_hook.py" in json.loads(evil[evil.index("--settings") + 1])["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_every_session_runs_without_the_project_instructions_or_auto_memory(tmp_path, monkeypatch):
    """Set A of 2b stopped when an extractor followed CLAUDE.md; run() launches only the isolated command, and records it."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out, seen_args = tmp_path / "env.json", {}

    def fake_command(*a, **k):
        seen_args.update(positional=len(a), keywords=sorted(k))
        return [sys.executable, "-c", f"import json, os; json.dump({{'project': os.environ.get('CLAUDE_PROJECT_DIR')}}, open(r'{out}', 'w'))"]
    monkeypatch.setattr(headless, "command", fake_command)
    rep = headless.run("extractor", "go", "haiku", cwd=tmp_path)
    assert json.loads(out.read_text())["project"] == str(tmp_path)
    assert seen_args == {"positional": 9, "keywords": []}                  # run() never passes `isolate`: it cannot opt out
    assert rep["config"]["session_env"] == headless.SESSION_ENV_VERSION
    assert rep["config"]["instruction_excludes"] == headless.INSTRUCTION_EXCLUDES and rep["config"]["auto_memory"] is False


def test_the_session_environment_is_in_every_stage_cache_key():
    import inspect
    from src import pipeline
    src = inspect.getsource(pipeline)
    assert src.count('"session_env": headless.SESSION_ENV_VERSION') >= 5      # extract, ground, synth, merge, act


def test_an_api_key_is_never_passed_to_a_session(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        headless.run("extractor", "go", env_extra={"ANTHROPIC_API_KEY": "sk-x"})


def test_the_fence_held_when_every_allowed_call_was_on_the_sessions_own_files(tmp_path):
    def rec(n, tool, path, decision):
        (tmp_path / "log").mkdir(exist_ok=True)
        (tmp_path / "log" / f"{n}.json").write_text(json.dumps({"tool": tool, "path": path, "decision": decision}))
    rec(0, "Read", str(ROOT / "data" / "batches" / "bench" / "b001.txt"), "allow")
    rec(1, "Read", str(ROOT / "schemas" / "extract-v6.schema.json"), "deny")       # a self-check the fence stopped
    rec(2, "Write", "data/extract/staging/bench/b001.json", "allow")
    c = lever.fence_check(tmp_path / "log", "data/batches/bench/b001.txt", "data/extract/staging/bench/b001.json")
    assert c["held"] and c["records"] == 3 and [d["tool"] for d in c["denied"]] == ["Read"]
    rec(3, "Read", "README.md", "allow")                                          # an allow outside: not held
    assert not lever.fence_check(tmp_path / "log", "data/batches/bench/b001.txt", "data/extract/staging/bench/b001.json")["held"]
    assert not lever.fence_check(tmp_path / "none", "a", "b")["held"]             # nothing logged: not observed
