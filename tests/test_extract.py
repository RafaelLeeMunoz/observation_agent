"""Stage 3, extract attempts: a retry can never pick up an earlier attempt's output."""
import json

import pytest

from src import batch, extract, ledger
from src.batch import make_batches

from conftest import BATCH_CFG, VERIFY_CFG, email

QUOTE = "Please confirm the nominations for the Katy hub by noon tomorrow."


@pytest.fixture
def env(ingested, tmp_path):
    st = ingested({"mb/inbox/1": email(subject="Katy noms", body=QUOTE + "\r\n")})
    batch.write(make_batches(st, ["mb/inbox/1"], BATCH_CFG, "p1"), tmp_path / "data" / "batches" / "bench")
    return st, extract.Paths(tmp_path)


def staged(p, attempt_id, quote=QUOTE):
    doc = {"batch_id": "b001", "attempt_id": attempt_id,
           "messages": [{"msg_key": "mb/inbox/1", "disposition": "observations", "reason": "", "work_items": 1}],
           "observations": [{"obs_id": "b001-o1", "msg_key": "mb/inbox/1", "task": "confirm noms",
                             "process_hint": "daily noms", "actors": [], "action_state": "requested",
                             "trigger": "", "output": "", "stall_signals": [], "stall_evidence": [], "quote": quote,
                             "confidence": "high", "limitations": ""}]}
    p.staging.mkdir(parents=True, exist_ok=True)
    (p.staging / "b001.json").write_text(json.dumps(doc), encoding="utf-8")


def test_a_successful_attempt_is_verified_filed_and_logged(env):
    st, p = env
    rec = extract.prepare(p, "bench", "b001", "haiku", "extract-v2")
    assert rec["attempt_id"].startswith("b001-") and rec["attempt_no"] == 1
    assert rec["attempt_id"] in extract.instruction(rec) and "data/extract/staging/b001.json" in extract.instruction(rec)
    staged(p, rec["attempt_id"])
    res = extract.finalize(p, "bench", "b001", st, VERIFY_CFG)
    assert res["status"] == "verified" and res["model"] == "haiku" and res["prompt_version"] == "extract-v2"
    assert json.loads((p.verified / "b001.json").read_text())["attempt_id"] == rec["attempt_id"]
    assert not (p.staging / "b001.json").exists()
    assert (p.staging / "accepted" / f"b001.{rec['attempt_id']}.json").exists()
    assert json.loads(p.status.read_text()) == {"mb/inbox/1": ledger.READ}
    log = [json.loads(l) for l in (p.logs / "runlog.jsonl").read_text().splitlines()]
    assert log[-1]["status"] == "verified" and log[-1]["model"] == "haiku" and "seconds" in log[-1]
    assert log[-1]["usage"] is None and res["usage"] is None                    # nothing reported: nothing invented


def test_the_harness_reported_usage_is_stored_with_the_run(env):
    st, p = env
    rec = extract.prepare(p, "bench", "b001", "haiku", "extract-v2")
    staged(p, rec["attempt_id"])
    res = extract.finalize(p, "bench", "b001", st, VERIFY_CFG, usage={"subagent_tokens": 72932, "tool_uses": 3})
    assert res["usage"] == {"subagent_tokens": 72932, "tool_uses": 3, "source": "harness report for the subagent"}
    log = [json.loads(l) for l in (p.logs / "runlog.jsonl").read_text().splitlines()]
    assert log[-1]["usage"]["subagent_tokens"] == 72932


def test_the_model_that_answered_is_recorded_beside_the_one_asked_for(env):
    st, p = env
    rec = extract.prepare(p, "bench", "b001", "haiku", "extract-v2")
    staged(p, rec["attempt_id"])
    session = p.root / "projects" / "s1.jsonl"
    t = session.with_suffix("") / "subagents" / "agent-a1.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-haiku-5-5"}}) + "\n", encoding="utf-8")
    p.fence.mkdir(parents=True)
    (p.fence / "1.json").write_text(json.dumps({"agent_type": "extractor", "agent_id": "a1", "tool": "Read",
                                                "path": rec["batch_file"], "decision": "allow",
                                                "transcript_path": str(session)}), encoding="utf-8")
    res = extract.finalize(p, "bench", "b001", st, VERIFY_CFG)
    assert res["model"] == "haiku" and res["model_observed"]["status"] == "confirmed"
    log = [json.loads(l) for l in (p.logs / "runlog.jsonl").read_text().splitlines()]
    assert log[-1]["model_observed"]["models"] == {"claude-haiku-5-5": 1}


def test_each_attempt_records_the_hash_of_the_exact_prompt_it_ran_with(env):
    import hashlib
    st, p = env
    prompt = p.root / ".claude" / "agents" / "extractor.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_bytes(b"prompt A\n")
    rec_a = extract.prepare(p, "bench", "b001", "haiku", "extract-v7b")
    staged(p, rec_a["attempt_id"])
    res = extract.finalize(p, "bench", "b001", st, VERIFY_CFG)
    assert rec_a["prompt_sha256"] == res["prompt_sha256"] == hashlib.sha256(b"prompt A\n").hexdigest()
    prompt.write_bytes(b"prompt B\n")                                           # same version label, new text
    assert extract.prepare(p, "bench", "b001", "haiku", "extract-v7b")["prompt_sha256"] != rec_a["prompt_sha256"]
    log = [json.loads(l) for l in (p.logs / "runlog.jsonl").read_text().splitlines()]
    assert log[-1]["prompt_sha256"] == rec_a["prompt_sha256"]


def test_without_a_fence_record_the_model_answered_is_unknown(env):
    st, p = env
    rec = extract.prepare(p, "bench", "b001", "haiku", "extract-v2")
    staged(p, rec["attempt_id"])
    res = extract.finalize(p, "bench", "b001", st, VERIFY_CFG)
    assert res["status"] == "verified" and res["model_observed"]["status"] == "unknown"


def test_prepare_moves_earlier_output_aside(env):
    st, p = env
    rec1 = extract.prepare(p, "bench", "b001", "haiku", "extract-v2")
    staged(p, rec1["attempt_id"])
    extract.finalize(p, "bench", "b001", st, VERIFY_CFG)
    staged(p, rec1["attempt_id"])                                  # a leftover file appears again
    rec2 = extract.prepare(p, "bench", "b001", "sonnet", "extract-v2")
    assert rec2["attempt_no"] == 2 and rec2["attempt_id"] != rec1["attempt_id"]
    assert not (p.staging / "b001.json").exists() and not (p.verified / "b001.json").exists()
    assert list((p.staging / "rejected").glob("b001.superseded-before-*.json"))
    assert list((p.verified / "superseded").glob("b001.superseded-before-*.json"))


def test_a_stale_staging_file_from_an_earlier_attempt_is_refused(env):
    st, p = env
    rec1 = extract.prepare(p, "bench", "b001", "haiku", "extract-v2")
    extract.prepare(p, "bench", "b001", "haiku", "extract-v2")         # retry: attempt 2 is current
    staged(p, rec1["attempt_id"])                                     # but the file carries attempt 1's id
    res = extract.finalize(p, "bench", "b001", st, VERIFY_CFG)
    assert res["status"] == "failed" and any("stale or foreign" in e for e in res["errors"])
    assert json.loads(p.status.read_text()) == {"mb/inbox/1": ledger.FAILED}


def test_a_failed_attempt_never_lands_in_verified(env):
    st, p = env
    rec = extract.prepare(p, "bench", "b001", "haiku", "extract-v2")
    p.staging.mkdir(parents=True, exist_ok=True)
    (p.staging / "b001.json").write_text('{"batch_id": "b001", "attempt_id": ', encoding="utf-8")   # truncated
    res = extract.finalize(p, "bench", "b001", st, VERIFY_CFG)
    assert res["status"] == "failed"
    assert not (p.verified / "b001.json").exists()
    assert (p.failed / f"{rec['attempt_id']}.json").exists()
    assert (p.staging / "rejected" / f"b001.{rec['attempt_id']}.json").exists()


def test_no_output_file_is_a_failed_attempt(env):
    st, p = env
    extract.prepare(p, "bench", "b001", "haiku", "extract-v2")
    res = extract.finalize(p, "bench", "b001", st, VERIFY_CFG)
    assert res["status"] == "failed" and res["errors"][0] == "the extractor wrote no output file for this attempt"
