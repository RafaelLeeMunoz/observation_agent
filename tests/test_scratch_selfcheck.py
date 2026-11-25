"""The scratch-write self-check (user, 24 Nov 2026), after the final cold retest stopped on extractor b081 writing `b081.json.tmp-unused`
once its real output was in place. The write is STILL DENIED and logged; it only does not stop the session or the run, and only for the
extractor and the grounder, only for their own output path plus `.tmp-<lowercase letters>`, only after that output was written, only once.
Everything else stays an unexpected (critical) denial. Offline: no model is called, and no worker is started."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fence_hook_scratch", ROOT / "tools" / "fence_hook.py")
fence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fence)

OUTS = {"extractor": "data/extract/staging/all/b081.json", "grounder": "data/ground/staging/all/g181.json"}
INS = {"extractor": "data/batches/all/b081.txt", "grounder": "data/ground/packets/all/run1/g181.txt"}
AGENTS = ["extractor", "grounder"]


def write(path, agent="extractor", tool="Write"):
    return {"tool_name": tool, "tool_input": {"file_path": str(path), "content": "x"}, "agent_type": agent}


def rec(tool, path, decision, kind=None):
    return {"tool": tool, "path": str(path), "decision": decision, "denial_kind": kind}


class Session:
    def __init__(self, tmp_path, agent):
        self.root, self.log, self.n, self.agent = tmp_path, tmp_path / "log", 0, agent
        self.out = OUTS[agent]
        self.log.mkdir()

    def add(self, r):
        self.n += 1
        (self.log / f"{self.n:03d}.json").write_text(json.dumps(r), encoding="utf-8")

    def wrote(self, text="{}"):
        """The legitimate output: its allowed Write logged and the file on disk."""
        self.add(rec("Write", self.out, "allow"))
        (self.root / self.out).parent.mkdir(parents=True, exist_ok=True)
        (self.root / self.out).write_text(text, encoding="utf-8")

    def kind(self, path, tool="Write", agent=None, exact=True, log=True):
        a = agent or self.agent
        return fence.denial_kind(write(path, a, tool), a, root=self.root, exact_write=self.out if exact else None,
                                 session_log=self.log if log else None)[0]


@pytest.fixture(params=AGENTS)
def s(request, tmp_path):
    return Session(tmp_path, request.param)


def scratch(s, suffix=".tmp-unused"):
    return s.out + suffix


def test_the_documented_scratch_write_after_the_real_output_is_the_self_check_for_the_extractor_and_the_grounder(s):
    s.wrote()
    for suffix in (".tmp-unused", ".tmp-check", ".tmp-x", ".tmp-abcdefghijkl"):
        kind, why = fence.denial_kind(write(scratch(s, suffix), s.agent), s.agent, root=s.root, exact_write=s.out, session_log=s.log)
        assert kind == "self_check" and "scratch" in why, suffix
    assert s.kind(s.root / scratch(s)) == "self_check"                               # the absolute form
    folded = str(s.root / s.out).upper() + ".tmp-unused"
    assert s.kind(folded) == "self_check"                                              # Windows folds the folder and file name's case
    assert s.kind(str(s.root / scratch(s)).upper()) == "unexpected"                    # but the suffix itself must be the lowercase one
    base = s.out.rsplit("/", 1)[0]
    assert s.kind(f"{base}/../{base.rsplit('/', 1)[1]}/" + scratch(s).rsplit("/", 1)[1]) == "self_check"   # resolves to the same file name


def test_the_write_is_still_denied_by_the_fence_itself_so_nothing_is_ever_created(s):
    decision = fence.decide(write(scratch(s), s.agent), exact_read=INS[s.agent], exact_write=s.out, root=s.root)
    assert decision[:2] == (True, "deny")                                              # only classified, never granted
    assert fence.decide(write(s.out, s.agent), exact_read=INS[s.agent], exact_write=s.out, root=s.root)[:2] == (True, "allow")


def test_before_the_real_output_exists_or_if_it_is_not_there_the_write_stays_unexpected(s):
    assert s.kind(scratch(s)) == "unexpected"                                          # nothing written
    s.add(rec("Write", s.out, "allow"))
    assert s.kind(scratch(s)) == "unexpected"                                          # logged as written, but no file on disk
    (s.root / s.out).parent.mkdir(parents=True, exist_ok=True)
    (s.root / s.out).write_text("", encoding="utf-8")
    assert s.kind(scratch(s)) == "unexpected"                                          # an empty file is not an output
    (s.root / s.out).write_text("{}", encoding="utf-8")
    assert s.kind(scratch(s)) == "self_check"                                          # now it is
    t = Session(s.root / "other", s.agent) if (s.root / "other").mkdir() is None else None
    t.add(rec("Write", s.out, "deny", "unexpected"))                                   # a DENIED write of the output does not count
    (t.root / s.out).parent.mkdir(parents=True, exist_ok=True)
    (t.root / s.out).write_text("{}", encoding="utf-8")
    assert t.kind(scratch(s)) == "unexpected"
    u = Session(s.root / "third", s.agent) if (s.root / "third").mkdir() is None else None
    (u.root / s.out).parent.mkdir(parents=True, exist_ok=True)
    (u.root / s.out).write_text("{}", encoding="utf-8")                               # a file on disk but no allowed Write in this session's log
    assert u.kind(scratch(s)) == "unexpected"


def test_a_second_scratch_write_is_unexpected_so_a_repeat_stops_the_run(s):
    s.wrote()
    assert s.kind(scratch(s)) == "self_check"
    s.add(rec("Write", scratch(s), "deny", "self_check"))                              # the hook logs the first one
    assert s.kind(scratch(s)) == "unexpected" and s.kind(scratch(s, ".tmp-check")) == "unexpected"


@pytest.mark.parametrize("suffix", [".tmp", ".tmp-", ".tmp-Check", ".tmp-check1", ".tmp-abcdefghijklm", ".tmp-ok.json", ".tmp_check",
                                    ".bak", ".json", "x", ".tmp-check/x", ".TMP-check", ".tmp-che ck", "..tmp-check", ".tmp--check"])
def test_any_other_suffix_stays_unexpected(s, suffix):
    s.wrote()
    assert s.kind(s.out + suffix) == "unexpected", suffix


def test_another_file_folder_or_session_stays_unexpected(s):
    s.wrote()
    folder, name = s.out.rsplit("/", 1)
    stem = name.split(".")[0]
    other = name.replace("81", "82")
    elsewhere = [f"{folder}/{other}.tmp-unused",                                       # another session's output
                 f"{folder}/{stem}.tmp-unused",                                        # the stem, not the output file
                 folder.replace("/all", "/dev") + f"/{name}.tmp-unused",              # another slice's folder
                 f"{folder}/sub/{name}.tmp-unused", f"{folder}/../{name}.tmp-unused",  # a subfolder, the parent folder
                 f"{folder}/../dev/{name}.tmp-unused", f"{name}.tmp-unused", f"/{name}.tmp-unused",
                 "README.md", "CLAUDE.md", "private/DEVLOG.md", "eval/benchmark/labels.json", "src/pipeline.py", "tools/fence_hook.py",
                 "config/settings.yaml", ".claude/settings.json", "data/extract/staging/all/.tmp-unused", f"{folder}/{name}"[:-1] + ".tmp-unused"]
    for p in elsewhere:
        assert s.kind(p) == "unexpected", p


def test_no_other_tool_and_no_other_agent_gets_the_exception(s):
    s.wrote()
    for tool in ("Read", "Edit", "Bash", "NotebookEdit", "MultiEdit"):
        assert s.kind(scratch(s), tool=tool) == "unexpected", tool
    for agent in ("synthesizer", "merger", "artifact-writer", "unknown-agent"):
        assert s.kind(scratch(s), agent=agent) == "unexpected", agent
    assert fence.denial_kind({"tool_name": "Write", "tool_input": {"file_path": scratch(s)}}, None, root=s.root, exact_write=s.out,
                             session_log=s.log)[0] == "unexpected"                    # no agent named at all


def test_an_unreadable_log_no_log_or_no_registered_output_fails_closed(s):
    s.wrote()
    assert s.kind(scratch(s)) == "self_check"
    (s.log / "999.json").write_text("{broken", encoding="utf-8")
    assert s.kind(scratch(s)) == "unexpected"
    (s.log / "999.json").write_text("[]", encoding="utf-8")                           # valid JSON, not a decision record
    assert s.kind(scratch(s)) == "unexpected"
    (s.log / "999.json").unlink()
    assert s.kind(scratch(s), log=False) == "unexpected"                               # the pattern-only registrations know no log
    assert s.kind(scratch(s), exact=False) == "unexpected"                             # and no assigned output


def test_the_existing_self_checks_are_unchanged(tmp_path):
    ex = "data/extract/staging/all/b001.json"
    assert fence.denial_kind({"tool_name": "Read", "tool_input": {"file_path": ex}, "agent_type": "extractor"}, "extractor", root=tmp_path, exact_write=ex)[0] == "self_check"
    assert fence.denial_kind({"tool_name": "Read", "tool_input": {"file_path": "schemas/extract-v6.schema.json"}, "agent_type": "extractor"},
                             "extractor", root=tmp_path)[0] == "self_check"
    assert fence.denial_kind({"tool_name": "Write", "tool_input": {"file_path": "data/extract/staging/all/b002.json"}, "agent_type": "extractor"},
                             "extractor", root=tmp_path, exact_write=ex)[0] == "unexpected"                 # another session's output: still critical


@pytest.mark.parametrize("agent", AGENTS)
def test_end_to_end_the_first_scratch_write_does_not_stop_the_session_and_the_second_does(agent, tmp_path):
    out_rel = {"extractor": "data/extract/staging/dev/b998.json", "grounder": "data/ground/staging/dev/g998.json"}[agent]
    in_rel = {"extractor": "data/batches/dev/b998.txt", "grounder": "data/ground/packets/dev/run1/g998.txt"}[agent]
    out = ROOT / out_rel

    def run(log, tool, path, **extra):
        hook = [sys.executable, "-I", str(ROOT / "tools" / "fence_hook.py"), "--agent", agent, "--read", in_rel, "--write", out_rel, "--log-dir", str(log)]
        p = {"tool_name": tool, "tool_input": {"file_path": path, **extra}, "agent_type": agent}
        raw = subprocess.run(hook, input=json.dumps(p).encode(), capture_output=True, check=True).stdout
        return json.loads(raw) if raw else None

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        early = run(tmp_path / "early", "Write", out_rel + ".tmp-unused", content="{}")             # before any real output: stops the session
        assert early["hookSpecificOutput"]["permissionDecision"] == "deny" and early["continue"] is False
        log = tmp_path / "s"
        assert run(log, "Read", in_rel) is None
        assert run(log, "Write", out_rel, content="{}") is None                                     # its real output is allowed
        out.write_text("{}", encoding="utf-8")                                                      # what the Write tool does
        first = run(log, "Write", out_rel + ".tmp-unused", content="{}")
        assert first["hookSpecificOutput"]["permissionDecision"] == "deny" and "continue" not in first
        assert not (ROOT / (out_rel + ".tmp-unused")).exists()                                      # nothing was created
        second = run(log, "Write", out_rel + ".tmp-check", content="{}")
        assert second["hookSpecificOutput"]["permissionDecision"] == "deny" and second["continue"] is False
        recs = sorted((json.loads(f.read_text()) for f in log.glob("*.json")), key=lambda r: r["at"])
        assert [(r["tool"], r["decision"], r["denial_kind"]) for r in recs] == [
            ("Read", "allow", None), ("Write", "allow", None), ("Write", "deny", "self_check"), ("Write", "deny", "unexpected")]
        assert recs[2]["path"].endswith(".tmp-unused") and recs[2]["self_check"] and recs[2]["content_chars"] == 2     # audited: what, how large
    finally:
        out.unlink(missing_ok=True)
        (ROOT / (out_rel + ".tmp-unused")).unlink(missing_ok=True)
