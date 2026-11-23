"""Which model answered an extraction attempt: read from the subagent's transcript, never assumed."""
import json

from src import runmodel

BATCH = "data/batches/bench/b001.txt"


def fence_record(d, n, agent_id, path=BATCH, tool="Read", decision="allow", transcript=None, agent_type="extractor"):
    d.mkdir(parents=True, exist_ok=True)
    rec = {"agent_type": agent_type, "agent_id": agent_id, "tool": tool, "path": path, "decision": decision,
           "transcript_path": transcript}
    (d / f"{n:03d}.json").write_text(json.dumps(rec), encoding="utf-8")


def transcript(session_file, agent_id, models):
    """A subagent transcript as Claude Code lays it out: <session>/subagents/agent-<id>.jsonl."""
    t = session_file.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"
    t.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "message": {"role": "user", "content": "Read the batch file ..."}}]
    lines += [{"type": "assistant", "message": {"role": "assistant", "model": m, "content": []}} for m in models]
    lines.append({"type": "attachment", "attachment": {}})
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return t


def test_the_model_that_answered_is_read_from_the_subagent_transcript(tmp_path):
    session = tmp_path / "projects" / "s1.jsonl"
    transcript(session, "a1", ["claude-haiku-5-5"] * 3)
    fence_record(tmp_path / "fence", 1, "a1", transcript=str(session))
    fence_record(tmp_path / "fence", 2, "a1", tool="Write", path="data/extract/staging/b001.json", transcript=str(session))
    r = runmodel.observe(tmp_path / "fence", tmp_path / BATCH, "haiku", root=tmp_path)
    assert r["status"] == "confirmed" and r["models"] == {"claude-haiku-5-5": 3} and r["agent_ids"] == ["a1"]


def test_a_turn_by_another_model_is_a_mismatch(tmp_path):
    session = tmp_path / "projects" / "s1.jsonl"
    transcript(session, "a1", ["claude-sonnet-5-5", "claude-haiku-5-5"])
    fence_record(tmp_path / "fence", 1, "a1", transcript=str(session))
    r = runmodel.observe(tmp_path / "fence", tmp_path / BATCH, "haiku", root=tmp_path)
    assert r["status"] == "mismatch" and "claude-sonnet-5-5" in r["why"]


def test_without_proof_the_model_is_unknown_never_assumed(tmp_path):
    fence = tmp_path / "fence"
    batch = tmp_path / BATCH
    assert runmodel.observe(fence, batch, "haiku", root=tmp_path)["status"] == "unknown"          # no fence log
    fence_record(fence, 1, "a1", path="data/batches/bench/b002.txt", transcript=str(tmp_path / "s.jsonl"))
    fence_record(fence, 2, "a1", decision="deny", transcript=str(tmp_path / "s.jsonl"))
    fence_record(fence, 3, "a9", agent_type="other", transcript=str(tmp_path / "s.jsonl"))
    r = runmodel.observe(fence, batch, "haiku", root=tmp_path)
    assert r["status"] == "unknown" and "no fence record" in r["why"]              # other batch, denied, other agent
    fence_record(fence, 4, "a1", transcript=None)
    assert "no transcript_path" in runmodel.observe(fence, batch, "haiku", root=tmp_path)["why"]
    fence_record(fence, 5, "a2", transcript=str(tmp_path / "s.jsonl"))
    assert "2 subagents" in runmodel.observe(fence, batch, "haiku", root=tmp_path)["why"]


def test_a_missing_transcript_or_one_without_answers_is_unknown(tmp_path):
    session = tmp_path / "projects" / "s1.jsonl"
    fence_record(tmp_path / "fence", 1, "a1", transcript=str(session))
    r = runmodel.observe(tmp_path / "fence", tmp_path / BATCH, "haiku", root=tmp_path)
    assert r["status"] == "unknown" and "not on disk" in r["why"]
    transcript(session, "a1", [])
    assert "no assistant turn" in runmodel.observe(tmp_path / "fence", tmp_path / BATCH, "haiku", root=tmp_path)["why"]


def test_absolute_paths_and_older_fence_records_are_found(tmp_path):
    session = tmp_path / "projects" / "s1.jsonl"
    transcript(session, "a1", ["claude-sonnet-5-5"])
    fence_record(tmp_path / "fence", 1, "a1", path=str(tmp_path / BATCH).upper())   # absolute, Windows case
    r = runmodel.observe(tmp_path / "fence", tmp_path / BATCH, "sonnet", search_root=tmp_path / "projects",
                         root=tmp_path)
    assert r["status"] == "confirmed" and r["transcript"].endswith("agent-a1.jsonl")
