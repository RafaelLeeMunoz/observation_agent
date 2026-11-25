"""M4 step 1 (PLAN 5.1): run safety, offline. headless.run is faked: no model is ever called.

Covers the canary (first finished qualifying batch), sentinel batches, the cumulative rejection rate, critical
stops (fence denial, an unobserved fence, model mismatch), the clean stop at the usage limit and its resume,
the breaker, rate-limit back-off, subset runs with --stop-after, and grounding pipelined into one pool."""
import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest

from src import batch, headless, pipeline

from conftest import email

N_LINES = 12


def body(i: int) -> str:
    return "".join(f"Line {j}: please confirm the nomination volume for meter {i}-{j} today.\r\n" for j in range(N_LINES))


def good_quote(i: int, j: int) -> str:
    return f"please confirm the nomination volume for meter {i}-{j} today"


@pytest.fixture
def env(ingested, tmp_path, monkeypatch):
    n = 8
    days = [f"{d} Feb 2002 09:00:00 -0800" for d in ("Mon, 4", "Tue, 5", "Wed, 6", "Thu, 7", "Fri, 8", "Sat, 9",
                                                    "Sun, 10", "Mon, 11")]
    st = ingested({f"mb/inbox/{i}": email(subject=f"Topic {i}", body=body(i), message_id=f"<{i}@x>", date=days[i - 1])
                   for i in range(1, n + 1)})
    root = tmp_path / "repo"
    (root / ".claude" / "agents").mkdir(parents=True)
    for a in ("extractor", "grounder", "synthesizer", "artifact-writer"):
        (root / ".claude" / "agents" / f"{a}.md").write_text(f"fake {a} prompt", encoding="utf-8")
    bs = batch.make_batches(st, [m.key for m in st.unique()], {"chars_per_token": 4, "token_budget": 1,
                                                              "hard_cap_tokens": 40000, "recipients_shown": 10}, "extract-v8c")
    assert len(bs) == n
    batch.write(bs, root / "data" / "batches" / "dev")
    real_run = pipeline.Run

    def make_run(slice_name="dev", root_=None):
        r = real_run(slice_name, root=root)
        r.cfg["extract"].update(prompt_version="extract-v8c", schema="extract-v6", model="haiku")
        r.cfg["run"].update(retries=1, timeout_s=10, rate_limit_retries=2, rate_limit_backoff_s=0,
                            breaker_consecutive=3, breaker_max_output_tokens=100)
        return r
    calls, live, peak, lock = [], [0], [0], threading.Lock()
    spec = {}                                        # (agent, batch id, call number) -> behaviour overrides

    def key_of(bid):
        return next(x for x in bs if x["batch_id"] == bid)["keys"][0]

    def fake(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        try:
            ticks = re.findall(r"`([^`]+)`", prompt)
            if agent == "extractor":
                _, bid, att, out = ticks
            else:
                pk_file, pid, att, out = ticks
                bid = f"b{int(pid[1:]) - 100:03d}"
            with lock:
                calls.append((agent, bid))
                nth = sum(c == (agent, bid) for c in calls)
            b = {"good": 6, "bad": 0, "claims": False, "kind": "ok", "fence": "allow", "model": "claude-haiku-5-5",
                 "out_tokens": 500, "write": True, **spec.get((agent, bid), {}), **spec.get((agent, bid, nth), {})}
            if b["fence"] in ("allow", "deny") and log_dir:
                d = Path(log_dir)
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{len(list(d.glob('*.json'))):04d}.json").write_text(json.dumps(
                    {"tool": "Read", "path": read, "decision": b["fence"], "reason": "test", "mode": "session"}))
            if b["write"] and b["kind"] == "ok":
                if agent == "extractor":
                    k = key_of(bid)
                    i = int(k.rsplit("/", 1)[1])
                    obs = [{"obs_id": f"{bid}-o{j}", "msg_key": k, "task": "confirm volume", "process_hint": "noms",
                            "actors": [], "action_state": "requested", "trigger": "", "output": "",
                            "stall_signals": ["follow_up"] if (b["claims"] and j == 0) else [],
                            "quote": good_quote(i, j) if j < b["good"] else f"a fabricated sentence that is nowhere {j}",
                            "confidence": "high", "limitations": ""} for j in range(b["good"] + b["bad"])]
                    doc = {"batch_id": bid, "attempt_id": att, "observations": obs,
                           "messages": [{"msg_key": k, "disposition": "observations" if obs else "business_no_process_signal",
                                         "reason": "" if obs else "fyi", "work_items": len(obs)}]}
                else:
                    man = json.loads((root / pk_file.replace(".txt", ".manifest.json")).read_text(encoding="utf-8"))
                    doc = {"packet_id": pid, "attempt_id": att, "verdicts": [
                        {"claim_id": c["claim_id"], "verdict": "unsupported", "sentence_ids": [], "reason": "test"}
                        for c in man["claims"]]}
                (root / out).parent.mkdir(parents=True, exist_ok=True)
                (root / out).write_text(json.dumps(doc), encoding="utf-8")
            result = {"usage": "Claude AI usage limit reached|1764000000", "error": "something unexpected went wrong",
                      "rate": "API Error: 429 rate_limit_error"}.get(b["kind"], "")
            return {"exit_code": 0 if b["kind"] == "ok" else 1, "wall_seconds": 0.1, "duration_ms": 100,
                    "duration_api_ms": 90, "num_turns": 2, "is_error": b["kind"] != "ok", "subtype": "success",
                    "result": result, "models_observed": [b["model"]], "model_usage": {},
                    "tokens": {"input_tokens": 10, "output_tokens": b["out_tokens"], "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 0},
                    "notional_cost_usd": 0.0, "stderr_tail": "", "stdout_tail": ""}
        finally:
            with lock:
                live[0] -= 1
    monkeypatch.setattr(headless, "run", fake)
    monkeypatch.setattr(headless, "terminate_all", lambda: 0)
    monkeypatch.setattr(pipeline, "Run", make_run)
    monkeypatch.setattr(pipeline.store, "load", lambda *a, **k: st)
    monkeypatch.setattr(pipeline, "stage_batch", lambda r, s: {"batches": bs, "keys": [m.key for m in st.unique()]})

    def no_synth(*a, **k):
        raise AssertionError("synthesis must not run on a stopped or partial run")
    monkeypatch.setattr(pipeline, "stage_synth", no_synth)
    return {"st": st, "bs": bs, "root": root, "calls": calls, "spec": spec, "peak": peak, "make_run": make_run}


def ex_calls(e):
    return [b for a, b in e["calls"] if a == "extractor"]


def status(e):
    p = e["root"] / "data" / "extract" / "dev" / "status.json"
    return json.loads(p.read_text()) if p.exists() else {}


def keys(e, bid):
    return next(x for x in e["bs"] if x["batch_id"] == bid)["keys"]


# --- canary, sentinels, cumulative ---

def test_the_first_finished_qualifying_batch_is_the_canary_and_its_trip_stops_everything(env):
    env["spec"][("extractor", "b001")] = {"good": 3, "bad": 2}          # 5 checked, 40% rejected
    rep = pipeline.run("dev", workers=1, stop_after="ground", batch_ids=[b["batch_id"] for b in env["bs"]])
    assert rep["stopped"]["reason"] == "canary" and rep["exit_code"] == pipeline.EXIT_CRITICAL
    assert ex_calls(env) == ["b001"]                                     # nothing more was scheduled
    assert all(status(env).get(k) != "read" for b in env["bs"][1:] for k in b["keys"])
    stop = json.loads((env["root"] / "output" / "dev" / "logs" / "stop.json").read_text())
    assert stop["stopped"]["batch"] == "b001" and stop["stopped"]["critical"] is True


def test_a_batch_below_the_minimum_sample_is_not_the_canary_the_next_qualifying_one_is(env):
    env["spec"][("extractor", "b001")] = {"good": 2, "bad": 1}          # 3 checked: below the sample of 5
    env["spec"][("extractor", "b002")] = {"good": 10, "bad": 0}
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"] is None and ms["quality"]["canary"]["batch"] == "b002"
    assert len(ms["extracted"]) == len(env["bs"])


def test_the_sentinels_are_chosen_before_the_run_by_a_fixed_rule(env):
    assert pipeline.pick_sentinels(env["bs"], env["st"]) == ["b001", "b003", "b005", "b007"]   # first per mailbox + quarters
    assert pipeline.pick_sentinels(list(reversed(env["bs"])), env["st"]) == ["b001", "b003", "b005", "b007"]


def test_a_failing_sentinel_stops_the_run_but_a_failing_ordinary_batch_is_only_flagged(env):
    env["spec"][("extractor", "b002")] = {"good": 3, "bad": 2}          # ordinary batch, 40%: flagged
    env["spec"][("extractor", "b005")] = {"good": 3, "bad": 2}          # sentinel, 40%: stops
    for b in ("b001", "b003", "b004"):
        env["spec"][("extractor", b)] = {"good": 12}                    # keep the cumulative rate low
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"]["reason"] == "sentinel" and ms["stop"]["batch"] == "b005"
    assert [f["batch"] for f in ms["quality"]["flags"]] == ["b002"]
    assert ex_calls(env) == ["b001", "b002", "b003", "b004", "b005"]


def test_the_cumulative_rate_stops_a_run_whose_batches_each_pass(env):
    for b in env["bs"]:
        env["spec"][("extractor", b["batch_id"])] = {"good": 3, "bad": 1}   # 4 checked each: below the sample alone
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"]["reason"] == "cumulative" and ms["stop"]["cumulative"] == {"checked": 8, "rejected": 2}
    assert ex_calls(env) == ["b001", "b002"]


# --- critical failures ---

def test_a_fence_denial_stops_the_run_and_its_output_is_never_finalized(env):
    env["spec"][("extractor", "b002")] = {"fence": "deny"}
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"]["reason"] == "fence_denial" and ms["stop"]["denied"][0]["decision"] == "deny"
    assert "b002" not in ms["extracted"] and all(status(env).get(k) != "read" for k in keys(env, "b002"))
    assert not (r.ex.verified / "b002.json").exists()


def test_a_documented_self_check_is_counted_and_flagged_when_repeated_but_never_stops_the_run(env, monkeypatch):
    real = headless.run

    def with_self_checks(agent, prompt, model, **kw):
        rep = real(agent, prompt, model, **kw)
        n = 2 if "`b003`" in prompt else 1                       # b003 repeats its self-check
        d = Path(kw["log_dir"])
        for i in range(n):
            (d / f"sc{i}.json").write_text(json.dumps({"tool": "Read", "path": "schemas/extract-v6.schema.json",
                                                       "decision": "deny", "denial_kind": "self_check"}))
        return rep
    monkeypatch.setattr(headless, "run", with_self_checks)
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"] is None and len(ms["extracted"]) == len(env["bs"])
    assert ms["fence_self_checks"] == len(env["bs"]) + 1
    assert [f["name"] for f in ms["fence_self_check_flags"]] == ["b003"]


def test_an_unexpected_denial_without_a_kind_is_never_taken_for_a_self_check(env):
    env["spec"][("extractor", "b001")] = {"fence": "deny"}               # a record with no denial_kind
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"]["reason"] == "fence_denial"


def test_an_output_written_while_the_fence_saw_no_call_is_a_critical_failure(env):
    env["spec"][("extractor", "b001")] = {"fence": "none"}
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"]["reason"] == "fence_unobserved" and ms["extracted"] == {}


def test_a_model_mismatch_stops_the_run(env):
    env["spec"][("extractor", "b001")] = {"model": "claude-sonnet-5-5"}
    rep = pipeline.run("dev", workers=1, stop_after="extract", batch_ids=["b001", "b002"])
    assert rep["stopped"]["reason"] == "model_mismatch" and rep["exit_code"] == pipeline.EXIT_CRITICAL
    assert ex_calls(env) == ["b001"]


def test_a_critical_stop_stops_the_sessions_in_flight(env, monkeypatch):
    stopped = []
    monkeypatch.setattr(headless, "terminate_all", lambda: stopped.append(1) or 3)
    for b in env["bs"]:                                                   # whichever finishes first is the canary
        env["spec"][("extractor", b["batch_id"])] = {"good": 3, "bad": 2}
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=2, stop_after="extract")
    assert ms["stop"]["reason"] == "canary" and stopped == [1] and ms["terminated"] == 3


# --- clean stop, resume, breaker, back-off ---

def test_the_usage_limit_stops_cleanly_without_spending_a_retry_or_marking_anything_failed(env):
    env["spec"][("extractor", "b002")] = {"kind": "usage"}
    rep = pipeline.run("dev", workers=1, stop_after="extract", batch_ids=[b["batch_id"] for b in env["bs"]])
    assert rep["stopped"]["reason"] == "usage_limit" and rep["exit_code"] == pipeline.EXIT_STOPPED
    assert rep["stopped"]["reset_text"] == "|1764000000"                  # as the CLI wrote it, never converted
    assert ex_calls(env) == ["b001", "b002"]                              # b002 once: no retry spent
    st = status(env)
    assert all(st.get(k) == "read" for k in keys(env, "b001"))
    assert all(st.get(k) is None for b in env["bs"][1:] for k in b["keys"])   # unread, never `failed`
    assert rep["unfinished"] == [b["batch_id"] for b in env["bs"][1:]]
    assert "resume" in rep and "same command" in rep["resume"]


def test_a_resumed_run_calls_the_model_only_for_unfinished_batches(env):
    env["spec"][("extractor", "b003")] = {"kind": "usage"}
    ids = [b["batch_id"] for b in env["bs"]]
    pipeline.run("dev", workers=1, stop_after="extract", batch_ids=ids)
    assert ex_calls(env) == ["b001", "b002", "b003"]
    env["spec"].clear()
    env["calls"].clear()
    rep = pipeline.run("dev", workers=1, stop_after="extract", batch_ids=ids)
    assert rep["complete"] and rep["exit_code"] == pipeline.EXIT_OK
    assert ex_calls(env) == ids[2:]                                       # b001, b002 are cache hits


def test_the_breaker_stops_the_run_after_k_empty_failures_whatever_the_message(env):
    for b in env["bs"]:
        env["spec"][("extractor", b["batch_id"])] = {"kind": "error", "out_tokens": 0}
    rep = pipeline.run("dev", workers=1, stop_after="extract", batch_ids=[b["batch_id"] for b in env["bs"]])
    assert rep["stopped"]["reason"] == "systemic_failure" and rep["exit_code"] == pipeline.EXIT_STOPPED
    assert len(ex_calls(env)) == 3                                        # breaker_consecutive


def test_two_empty_failures_then_a_success_do_not_trip_the_breaker(env):
    env["spec"][("extractor", "b001", 1)] = {"kind": "error", "out_tokens": 0}
    env["spec"][("extractor", "b001", 2)] = {"kind": "error", "out_tokens": 0}
    r = env["make_run"]()                                                 # retries=1: b001 fails twice, then b002 succeeds
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"] is None and ms["failed_extract"] == ["b001"] and len(ms["extracted"]) == len(env["bs"]) - 1


def test_a_rate_limited_session_is_retried_after_a_back_off_without_spending_a_retry(env):
    env["spec"][("extractor", "b001", 1)] = {"kind": "rate"}
    env["spec"][("extractor", "b001", 2)] = {"kind": "rate"}
    r = env["make_run"]()
    r.cfg["run"]["retries"] = 0                                           # no retry at all for a bad output ...
    ms = pipeline.stage_models(r, env["st"], env["bs"][:1], workers=1, stop_after="extract")
    assert ms["stop"] is None and "b001" in ms["extracted"]               # ... yet two refusals were waited out
    assert ex_calls(env) == ["b001", "b001", "b001"]


def test_the_failure_kinds_are_read_from_the_report():
    base = {"exit_code": 1, "is_error": True, "stderr_tail": "", "stdout_tail": ""}
    assert headless.classify({**base, "result": "Claude AI usage limit reached|1764000000"}) == (headless.USAGE_LIMIT, "|1764000000")
    assert headless.classify({**base, "result": "You've hit your limit · resets 3pm (Europe/London)"})[0] == headless.USAGE_LIMIT
    assert headless.classify({**base, "result": "5-hour limit reached ∙ resets 9pm"}) == (headless.USAGE_LIMIT, "resets 9pm")
    assert headless.classify({**base, "result": "API Error: 529 overloaded_error"})[0] == headless.RATE_LIMITED
    assert headless.classify({**base, "result": "API Error: 429 rate_limit_error"})[0] == headless.RATE_LIMITED
    assert headless.classify({**base, "result": "boom"})[0] == headless.ERROR
    assert headless.classify({"exit_code": -1, "is_error": True, "stderr_tail": "timeout"})[0] == headless.TIMEOUT
    assert headless.classify({"exit_code": -2, "is_error": True})[0] == headless.TERMINATED
    assert headless.classify({"exit_code": 0, "is_error": False})[0] == headless.OK


# --- run controls and pipelined grounding ---

def test_a_subset_is_run_only_with_stop_after_and_never_synthesized(env):
    with pytest.raises(ValueError, match="stop-after"):
        pipeline.run("dev", workers=2, batch_ids=["b001"])
    rep = pipeline.run("dev", workers=2, stop_after="extract", batch_ids=["b002", "b004"])
    assert rep["complete"] and sorted(ex_calls(env)) == ["b002", "b004"]
    assert not [a for a, _ in env["calls"] if a == "grounder"]
    with pytest.raises(ValueError, match="unknown batches"):
        pipeline.run("dev", workers=2, stop_after="extract", batch_ids=["b099"])
    assert pipeline.parse_batches("b001-b003,b007") == ["b001", "b002", "b003", "b007"]


def test_grounding_starts_as_soon_as_its_batch_is_verified_within_the_same_pool(env):
    for b in env["bs"]:
        env["spec"][("extractor", b["batch_id"])] = {"claims": True}
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=2, stop_after="ground")
    assert ms["stop"] is None and len(ms["grounded"]) == len(env["bs"])
    order = env["calls"]
    for b in env["bs"]:                                                   # every grounding after its own extraction
        assert order.index(("extractor", b["batch_id"])) < order.index(("grounder", b["batch_id"]))
    assert order.index(("grounder", "b001")) < order.index(("extractor", env["bs"][-1]["batch_id"]))   # pipelined
    assert env["peak"][0] <= 2 and ms["peak_in_flight"] <= 2              # never more sessions than workers
    ms2 = pipeline.stage_models(r, env["st"], env["bs"], workers=2, stop_after="ground")
    assert len(ms2["grounded"]) == len(env["bs"]) and ms2["sessions"] == 0   # all cache hits on a rerun


def test_every_session_is_held_to_its_own_two_files_and_logs_its_fence_to_its_own_folder(env, monkeypatch):
    seen = []
    real = headless.run

    def spy(agent, prompt, model, **kw):
        seen.append((agent, kw["read"], kw["write"], kw["log_dir"]))
        return real(agent, prompt, model, **kw)
    monkeypatch.setattr(headless, "run", spy)
    env["spec"][("extractor", "b001")] = {"claims": True}
    r = env["make_run"]()
    pipeline.stage_models(r, env["st"], env["bs"][:1], workers=1, stop_after="ground")
    (ea, er, ew, el), (ga, gr, gw, gl) = seen
    assert (ea, er, ew) == ("extractor", "data/batches/dev/b001.txt", "data/extract/staging/dev/b001.json")
    assert ga == "grounder" and gr.startswith("data/ground/packets/dev/dev_b001/") and gw == "data/ground/staging/dev/g101.json"
    assert Path(el).parent == Path(gl).parent == r.fence_sessions and Path(el) != Path(gl)


def test_terminate_all_stops_a_running_session_which_then_reports_terminated(monkeypatch):
    """A real child process (no model): a critical stop must not leave sessions running."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(headless, "command", lambda *a, **k: [sys.executable, "-c", "import time; time.sleep(60)"])
    out = {}
    t = threading.Thread(target=lambda: out.update(headless.run("extractor", "x", timeout=120)))
    t.start()
    deadline = time.time() + 20
    while headless.running() == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert headless.terminate_all() == 1
    t.join(timeout=30)
    assert out["kind"] == headless.TERMINATED and out["wall_seconds"] < 30 and headless.running() == 0


def test_the_session_hook_is_given_its_two_files_and_its_own_log_folder():
    cmd = headless.command("extractor", "go", "haiku", read="data/batches/all/b007.txt",
                           write="data/extract/staging/all/b007.json", log_dir="C:/x/fence_sessions/all/b007-1")
    hook = json.loads(cmd[cmd.index("--settings") + 1])["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert '--agent extractor --read "data/batches/all/b007.txt" --write "data/extract/staging/all/b007.json"' in hook
    assert '--log-dir "C:/x/fence_sessions/all/b007-1"' in hook


def test_paths_refuse_a_slice_the_fence_does_not_admit(tmp_path):
    from src import extract, ground
    with pytest.raises(ValueError, match="unknown slice"):
        extract.Paths(tmp_path, "prod")
    with pytest.raises(ValueError, match="unknown slice"):
        ground.Paths(tmp_path, "prod")
    a, b = extract.Paths(tmp_path, "dev"), extract.Paths(tmp_path, "bench")
    assert a.verified != b.verified and a.staging != b.staging and a.status != b.status and a.attempts != b.attempts


def test_coverage_comes_from_the_read_receipts_the_run_used_never_from_a_side_file(env, tmp_path):
    """Found 23 Nov 2026: after the per-slice move the slice's status.json did not exist yet, and an offline rerun
    reported 0 of 202 read. R8: read = listed in the verified output of its batch, as used by this run."""
    from src import report
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=2, stop_after="extract")
    (r.root / "data").mkdir(exist_ok=True)
    (r.root / "data" / "ledger.csv").write_bytes((tmp_path / "ledger.csv").read_bytes())
    r.ex.status.unlink()                                                   # the side file is gone
    b = {"batches": env["bs"], "keys": [m.key for m in env["st"].unique()]}
    cov = report._coverage(r, env["st"], b, ms["extracted"], [])
    assert cov["read"] == len(b["keys"]) and cov["complete"]
    gone = keys(env, "b003")[0]                                            # a receipt that does not list its message
    f = ms["extracted"]["b003"]
    doc = json.loads(f.read_text(encoding="utf-8"))
    doc["messages"] = [m for m in doc["messages"] if m["msg_key"] != gone]
    f.write_text(json.dumps(doc), encoding="utf-8")
    cov = report._coverage(r, env["st"], b, ms["extracted"], [])
    assert gone in cov["unread"] and not cov["complete"]
    assert next(x for x in cov["ledger"] if x["key"] == gone)["analysis"] == "batched"


# --- the scratch-write self-check (user, 24 Nov 2026): nonfatal for the run, never a reason to skip validation ---

def _with_scratch_self_check(real, bid_garbage=None):
    """A session whose fence log also holds one classified scratch write (the hook's record); optionally its real output is garbage."""
    def run(agent, prompt, model, **kw):
        rep = real(agent, prompt, model, **kw)
        out = re.findall(r"`([^`]+)`", prompt)[-1]
        d = Path(kw["log_dir"])
        (d / "sc0.json").write_text(json.dumps({"tool": "Write", "path": out + ".tmp-unused", "decision": "deny", "denial_kind": "self_check",
                                                "self_check": "scratch file next to its own output", "mode": "session"}))
        if bid_garbage and f"`{bid_garbage}`" in prompt:
            (Path(kw["cwd"]) / out).write_text("this is not json", encoding="utf-8")
        return rep
    return run


def test_a_scratch_self_check_does_not_stop_the_run_is_logged_and_the_output_is_still_validated_and_accepted(env, monkeypatch):
    monkeypatch.setattr(headless, "run", _with_scratch_self_check(headless.run))
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"] is None and len(ms["extracted"]) == len(env["bs"]) and ms["failed_extract"] == []
    assert ms["fence_self_checks"] == len(env["bs"])
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines()]
    attempts = [x for x in log if x.get("stage") == "extract" and x.get("attempt")]
    assert attempts and all(x["status"] == "verified" and x["fence_self_checks"][0]["path"].endswith(".tmp-unused") for x in attempts)   # audited
    assert all(x["fence_self_checks"][0]["denial_kind"] == "self_check" for x in attempts)


def test_a_scratch_self_check_never_makes_a_bad_output_acceptable(env, monkeypatch):
    monkeypatch.setattr(headless, "run", _with_scratch_self_check(headless.run, bid_garbage="b002"))
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"] is None and ms["failed_extract"] == ["b002"] and "b002" not in ms["extracted"]      # validated on its own, and it failed
    assert not (r.ex.verified / "b002.json").exists() and all(status(env).get(k) != "read" for k in keys(env, "b002"))
    assert len(ms["extracted"]) == len(env["bs"]) - 1


def test_a_second_unclassified_denial_in_the_same_session_still_stops_the_run(env, monkeypatch):
    real = headless.run

    def run(agent, prompt, model, **kw):
        rep = real(agent, prompt, model, **kw)
        d = Path(kw["log_dir"])
        out = re.findall(r"`([^`]+)`", prompt)[-1]
        (d / "sc0.json").write_text(json.dumps({"tool": "Write", "path": out + ".tmp-unused", "decision": "deny", "denial_kind": "self_check"}))
        if "`b003`" in prompt:                                   # the repeat is classified unexpected by the hook
            (d / "sc1.json").write_text(json.dumps({"tool": "Write", "path": out + ".tmp-check", "decision": "deny", "denial_kind": "unexpected"}))
        return rep
    monkeypatch.setattr(headless, "run", run)
    r = env["make_run"]()
    ms = pipeline.stage_models(r, env["st"], env["bs"], workers=1, stop_after="extract")
    assert ms["stop"]["reason"] == "fence_denial" and ms["stop"]["denied"][0]["path"].endswith(".tmp-check")
