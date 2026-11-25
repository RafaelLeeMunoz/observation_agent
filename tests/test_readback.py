"""M4 2b: a synthesizer or merger may read back its own output file ONCE, AFTER writing it, as a documented self-check
(user, 23 Nov 2026, after compare-synth stopped on exactly this). The read is still denied and logged; it only does not stop
the session. Before the write, a second time, another session's file, an unreadable log, any other agent and any other
path stay unexpected denials (the session stops, the run stops). No model is called.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fence_hook", ROOT / "tools" / "fence_hook.py")
fence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fence)

OUT = "data/synth/staging/all/s001.json"
OTHER = "data/synth/staging/all/s002.json"
BOTH = ["synthesizer", "merger", "grounder"]


def read(path, agent):
    return {"tool_name": "Read", "tool_input": {"file_path": str(path)}, "agent_type": agent}


def rec(tool, path, decision, kind=None):
    return {"tool": tool, "path": str(path), "decision": decision, "denial_kind": kind}


class Session:
    """A session folder in tmp_path: its own decision log, and the output file when `written`."""

    def __init__(self, tmp_path):
        self.root, self.log, self.n = tmp_path, tmp_path / "log", 0
        self.log.mkdir()

    def add(self, r):
        self.n += 1
        (self.log / f"{self.n:03d}.json").write_text(json.dumps(r), encoding="utf-8")

    def write_output(self, rel=OUT, text="{}"):
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(text, encoding="utf-8")

    def kind(self, agent, path=OUT, exact_write=OUT, log=True):
        return fence.denial_kind(read(path, agent), agent, root=self.root, exact_write=exact_write,
                                 session_log=self.log if log else None)[0]


@pytest.fixture
def s(tmp_path):
    return Session(tmp_path)


@pytest.mark.parametrize("agent", BOTH)
def test_the_first_read_back_after_a_successful_write_is_the_documented_self_check(s, agent):
    s.add(rec("Read", "data/synth/inputs/all/s001.txt", "allow"))
    s.add(rec("Write", OUT, "allow"))
    s.write_output()
    kind, why = fence.denial_kind(read(OUT, agent), agent, root=s.root, exact_write=OUT, session_log=s.log)
    assert kind == "self_check" and "once" in why
    assert s.kind(agent, path=s.root / OUT) == "self_check"                       # the absolute form of the same file
    assert s.kind(agent, path=str(s.root / OUT).upper()) == "self_check"        # Windows folds case


@pytest.mark.parametrize("agent", BOTH)
def test_the_read_is_still_denied_by_the_fence_itself(agent):
    own = fence.decide(read(OUT, agent), exact_read="data/synth/inputs/all/s001.txt", exact_write=OUT)
    assert own[:2] == (True, "deny")                                              # nothing is granted, only classified


@pytest.mark.parametrize("agent", BOTH)
def test_a_read_back_before_the_write_stays_unexpected(s, agent):
    assert s.kind(agent) == "unexpected"                                          # no write, no file
    s.write_output()                                                              # a file but no allowed Write in the log
    assert s.kind(agent) == "unexpected"
    s.add(rec("Read", "data/synth/inputs/all/s001.txt", "allow"))
    assert s.kind(agent) == "unexpected"


@pytest.mark.parametrize("agent", BOTH)
def test_a_write_that_was_denied_or_that_left_no_file_does_not_count(s, agent):
    s.add(rec("Write", OUT, "deny", "unexpected"))
    s.write_output()
    assert s.kind(agent) == "unexpected"                                          # the Write was denied
    (s.root / "other").mkdir()
    t = Session(s.root / "other")
    t.add(rec("Write", OUT, "allow"))
    assert t.kind(agent) == "unexpected"                                          # allowed, but no file on disk
    t.write_output(text="")
    assert t.kind(agent) == "unexpected"                                          # allowed, but the file is empty


@pytest.mark.parametrize("agent", BOTH)
def test_a_second_read_back_is_unexpected(s, agent):
    s.add(rec("Write", OUT, "allow"))
    s.write_output()
    assert s.kind(agent) == "self_check"
    s.add(rec("Read", OUT, "deny", "self_check"))                                 # the hook logs the first one
    assert s.kind(agent) == "unexpected"


@pytest.mark.parametrize("agent", BOTH)
def test_another_sessions_file_or_any_other_path_is_unexpected_even_after_a_good_write(s, agent):
    s.add(rec("Write", OUT, "allow"))
    s.write_output()
    s.write_output(OTHER)
    for path in (OTHER, "data/synth/inputs/all/s001.txt", "private/DEVLOG.md", "README.md", "CLAUDE.md", "eval/benchmark/labels.json"):
        assert s.kind(agent, path=path) == "unexpected", path
    s.add(rec("Write", OTHER, "allow"))                                           # a Write record for another file
    assert s.kind(agent, path=OTHER) == "unexpected"
    assert s.kind(agent, path=OTHER, exact_write=OTHER) == "self_check"           # only the session's own assigned file


@pytest.mark.parametrize("agent", BOTH)
def test_an_unreadable_log_or_no_log_fails_closed(s, agent):
    s.add(rec("Write", OUT, "allow"))
    s.write_output()
    assert s.kind(agent) == "self_check"
    (s.log / "999.json").write_text("{broken", encoding="utf-8")
    assert s.kind(agent) == "unexpected"
    (s.log / "999.json").write_text("[]", encoding="utf-8")                      # valid JSON, not a decision record
    assert s.kind(agent) == "unexpected"
    (s.log / "999.json").unlink()
    assert s.kind(agent, log=False) == "unexpected"                              # the pattern-only registrations know no log


@pytest.mark.parametrize("agent", ["artifact-writer", "unknown-agent"])
def test_no_other_agent_gets_the_read_back_exception(s, agent):
    s.add(rec("Write", OUT, "allow"))
    s.write_output()
    assert s.kind(agent) == "unexpected"


def test_the_extractors_own_documented_rules_are_unchanged(s):
    ex = "data/extract/staging/all/b001.json"
    assert fence.denial_kind(read(ex, "extractor"), "extractor", root=s.root, exact_write=ex)[0] == "self_check"
    assert fence.denial_kind(read("data/extract/staging/all/b002.json", "extractor"), "extractor", root=s.root, exact_write=ex)[0] == "unexpected"


@pytest.mark.parametrize("agent", BOTH)
def test_end_to_end_the_first_read_back_does_not_stop_the_session_and_the_second_does(agent, tmp_path):
    out_rel, in_rel = {"synthesizer": ("data/synth/staging/dev/s998.json", "data/synth/inputs/dev/s998.txt"),
                       "merger": ("data/synth/staging/dev/m998.json", "data/synth/inputs/dev/m998.txt"),
                       "grounder": ("data/ground/staging/dev/g998.json", "data/ground/packets/dev/run1/g998.txt")}[agent]
    out = ROOT / out_rel
    def run(log, tool, path, **extra):
        hook = [sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", agent, "--read", in_rel,
                "--write", out_rel, "--log-dir", str(log)]
        p = {"tool_name": tool, "tool_input": {"file_path": path, **extra}, "agent_type": agent}
        raw = subprocess.run(hook, input=json.dumps(p).encode(), capture_output=True, check=True).stdout
        return json.loads(raw) if raw else None

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        early = run(tmp_path / "early", "Read", out_rel)                             # before any write: stops the session
        assert early["hookSpecificOutput"]["permissionDecision"] == "deny" and early["continue"] is False
        log = tmp_path / "s"
        assert run(log, "Read", in_rel) is None                                      # its input is allowed
        assert run(log, "Write", out_rel, content="{}") is None                      # its own output is allowed
        out.write_text("{}", encoding="utf-8")                                       # what the Write tool does
        first = run(log, "Read", out_rel)
        assert first["hookSpecificOutput"]["permissionDecision"] == "deny" and "continue" not in first
        second = run(log, "Read", out_rel)
        assert second["hookSpecificOutput"]["permissionDecision"] == "deny" and second["continue"] is False
        recs = sorted((json.loads(f.read_text()) for f in log.glob("*.json")), key=lambda r: r["at"])
        assert [(r["tool"], r["decision"], r["denial_kind"]) for r in recs] == [
            ("Read", "allow", None), ("Write", "allow", None), ("Read", "deny", "self_check"), ("Read", "deny", "unexpected")]
    finally:
        out.unlink(missing_ok=True)
