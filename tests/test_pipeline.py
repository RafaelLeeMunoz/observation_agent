"""Pipeline stages offline: retries, partial failure, cache hits, restart adoption. headless.run is faked: no model."""
import json
import re

import pytest

from src import batch, headless, pipeline

from conftest import email


DATES = ["Mon, 4 Feb 2002 09:00:00 -0800", "Tue, 5 Feb 2002 09:00:00 -0800", "Wed, 6 Feb 2002 09:00:00 -0800",
         "Thu, 7 Feb 2002 09:00:00 -0800"]


def corpus():
    return {f"mb/inbox/{i}": email(subject=f"Topic {i}", body=f"Please confirm the nominations for meter {i} today, thanks.\r\n",
                                   message_id=f"<{i}@x>", date=DATES[i - 1]) for i in range(1, 5)}


@pytest.fixture
def env(ingested, tmp_path, monkeypatch):
    st = ingested(corpus())
    root = tmp_path / "repo"
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "extractor.md").write_text("fake prompt", encoding="utf-8")
    cfg = {"chars_per_token": 4, "token_budget": 120, "hard_cap_tokens": 40000, "recipients_shown": 10}
    bs = batch.make_batches(st, [m.key for m in st.unique()], cfg, "extract-v8c")
    batch.write(bs, root / "data" / "batches" / "dev")
    r = pipeline.Run("dev", root=root)
    r.cfg["extract"].update(prompt_version="extract-v8c", schema="extract-v6", model="haiku")
    r.cfg["run"].update(retries=1, timeout_s=10)
    calls = []

    def fake(behaviour):
        def run(agent, prompt, model, timeout=0, cwd=None):
            bid = re.search(r"batch_id is `(b\d+)`", prompt).group(1)
            att = re.search(r"attempt_id is `([^`]+)`", prompt).group(1)
            calls.append((bid, att))
            b = next(x for x in bs if x["batch_id"] == bid)
            mode = behaviour(bid, sum(c[0] == bid for c in calls))
            out = root / "data" / "extract" / "staging" / f"{bid}.json"
            if mode == "good":
                out.write_text(json.dumps({"batch_id": bid, "attempt_id": att, "observations": [],
                                           "messages": [{"msg_key": k, "disposition": "business_no_process_signal",
                                                         "reason": "fyi", "work_items": 0} for k in b["keys"]]}))
            elif mode == "broken":
                out.write_text('{"batch_id": "' + bid)                                     # truncated JSON
            return {"exit_code": 0, "wall_seconds": 0.1, "duration_ms": 100, "duration_api_ms": 90, "num_turns": 2,
                    "is_error": False, "subtype": "success", "result": "", "models_observed": ["claude-haiku-5-5"],
                    "model_usage": {}, "tokens": {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 0}, "notional_cost_usd": 0.0, "stderr_tail": ""}
        return run
    return st, r, bs, calls, fake, monkeypatch


def test_a_failed_attempt_is_retried_and_a_batch_failing_every_attempt_is_never_read(env):
    st, r, bs, calls, fake, mp = env
    assert len(bs) >= 2
    first, last = bs[0]["batch_id"], bs[-1]["batch_id"]
    mp.setattr(headless, "run", fake(lambda bid, n: "broken" if bid == last or (bid == first and n == 1) else "good"))
    done = pipeline.stage_extract(r, st, bs, workers=2)
    assert first in done and last not in done                                 # retried once, then verified
    assert [c[0] for c in calls].count(first) == 2 and [c[0] for c in calls].count(last) == 2   # 1 + retries
    status = json.loads((r.root / "data" / "extract" / "status.json").read_text())
    assert all(status[k] == "read" for k in bs[0]["keys"])
    assert all(status[k] != "read" for k in bs[-1]["keys"])                   # a failed batch never counts as read
    assert len(list((r.root / "data" / "extract" / "failed").glob(f"{last}-*.json"))) == 2
    log = [json.loads(l) for l in r.log_path.read_text().splitlines()]
    assert {x["status"] for x in log if x.get("batch") == first} == {"failed", "verified"}


def test_a_rerun_skips_finished_batches_and_a_prompt_change_reruns_them(env):
    st, r, bs, calls, fake, mp = env
    mp.setattr(headless, "run", fake(lambda bid, n: "good"))
    pipeline.stage_extract(r, st, bs, workers=2)
    n = len(calls)
    again = pipeline.stage_extract(r, st, bs, workers=2)
    assert len(calls) == n and set(again) == {b["batch_id"] for b in bs}       # every batch a cache hit
    hits = [json.loads(l) for l in r.log_path.read_text().splitlines() if '"cache_hit"' in l]
    assert len(hits) == len(bs)
    (r.root / ".claude" / "agents" / "extractor.md").write_text("changed prompt", encoding="utf-8")
    pipeline.stage_extract(r, st, bs, workers=2)
    assert len(calls) == n + len(bs)                                          # the prompt is in the cache key


def test_an_interrupted_run_adopts_a_batch_that_was_already_verified(env):
    st, r, bs, calls, fake, mp = env
    mp.setattr(headless, "run", fake(lambda bid, n: "good"))
    pipeline.stage_extract(r, st, bs[:1], workers=1)
    for f in (r.cache / "extract").glob("*.json"):                            # as if the run died before caching
        f.unlink()
    n = len(calls)
    done = pipeline.stage_extract(r, st, bs[:1], workers=1)
    assert len(calls) == n and bs[0]["batch_id"] in done


def test_the_headless_runner_refuses_to_start_with_an_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        headless.run("extractor", "x")


def test_the_headless_command_gives_only_read_and_write_and_the_fence_hook():
    cmd = headless.command("grounder", "go", "haiku")
    assert cmd[cmd.index("--tools") + 1] == "Read,Write" and "--strict-mcp-config" in cmd
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "fence_hook.py" in hook and "--agent grounder" in hook
    assert "--dangerously-skip-permissions" not in cmd and "bypassPermissions" not in cmd


def test_an_offline_run_refuses_any_model_call_for_an_uncached_stage(env):
    st, r, bs, calls, fake, mp = env
    mp.setattr(headless, "run", pipeline._refuse)
    with pytest.raises(pipeline.OfflineCacheMiss, match="not cached"):
        pipeline.stage_extract(r, st, bs[:1], workers=1)
    assert calls == []


def test_parallel_finalizes_never_lose_or_tear_the_shared_status_file(env):
    """Race found 23 Nov 2026: two workers finalizing at once could read status.json while the other had truncated
    it. Many finalizes at once must leave a status file that parses and marks every batch's messages read."""
    st, r, bs, calls, fake, mp = env
    mp.setattr(headless, "run", fake(lambda bid, n: "good"))
    from concurrent.futures import ThreadPoolExecutor
    from src import extract
    p = extract.Paths(r.root)

    def one(b):                                     # as the pipeline does: one attempt per batch at a time
        rec = extract.prepare(p, "dev", b["batch_id"], "haiku", "extract-v8c", "extract-v6")
        headless.run("extractor", extract.instruction(rec), "haiku")
        return extract.finalize(p, "dev", b["batch_id"], st, r.cfg["verify"], usage={"models_observed": ["claude-haiku-5-5"]})
    for _ in range(10):                             # rounds of distinct batches finalized at the same time
        with ThreadPoolExecutor(max_workers=len(bs)) as ex:
            assert all(res["status"] == "verified" for res in ex.map(one, bs))
    status = json.loads((r.root / "data" / "extract" / "status.json").read_text())
    assert all(status[k] == "read" for b in bs for k in b["keys"])
    assert not list((r.root / "data" / "extract").glob("*.tmp"))
