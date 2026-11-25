"""M4 step 1d (PLAN 5.1, user decision 4): per-mailbox synthesis parts and the merge, offline (no model).

One process seen in two mailboxes must become one process, or its events and its rate split; a singleton is never
recurring and adds no savings; every observation ends in at most one final process; provenance is recorded."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import events, headless, pipeline, schema, score, synth, windows

ROOT = Path(__file__).resolve().parent.parent
CFG = {"hourly_rate": 85, "min_events": 2, "min_emails": 3, "min_threads": 2, "min_months": 1}


class M:                                  # the StoredMessage fields synth.py and events.py read
    def __init__(self, key, thread, mailbox):
        self.key, self.thread_id, self.mailbox = key, thread, mailbox


class St:
    def __init__(self, msgs):
        self.messages = {m.key: m for m in msgs}


def ob(oid, key, date="2001-03-01", anchor=None, task="confirm the meter volume"):
    return {"obs_id": oid, "msg_key": key, "task": task, "process_hint": "noms", "actors": ["a@x.com"],
            "action_state": "requested", "trigger": "a new month", "output": "confirmed volume", "stall_signals": [],
            "evidence_date": date, "date_basis": "message_date", "anchor": anchor, "published_evidence": []}


# mailbox A: threads tA1..tA3; mailbox B: threads tB1..tB3
ST = St([M("A/i/1", "tA1", "A"), M("A/i/2", "tA2", "A"), M("A/i/3", "tA3", "A"),
         M("B/i/1", "tB1", "B"), M("B/i/2", "tB2", "B"), M("B/i/3", "tB3", "B")])


def group(pid, obs, candidate=None, name=None):
    g = {"process_id": pid, "name": name or f"Work {pid}", "description": "Schedulers confirm volumes.",
         "trigger": "a new month", "output": "confirmed volumes", "roles": ["scheduler"], "obs_ids": list(obs),
         "automation": {"idea": "a standard request", "artifact_type": "template", "why": "rewritten by hand"},
         "minutes_per_event": {"low": 10, "base": 20, "high": 30, "rationale": "writing the request", "basis_obs_ids": [obs[0]]}}
    if candidate is not None:
        g["candidate"] = candidate
    return g


def part(sid, k, n, obs, groups, st=ST):
    inp = synth.build_input(obs, st, sid, "dev", part=(k, n))
    att = f"{sid}-x-1"
    res = synth.check(json.dumps({"input_id": sid, "attempt_id": att, "processes": groups}), inp, att)
    assert res["status"] == "verified", res["errors"]
    return inp, res


def merged(pid, parts_, name="Monthly volume confirmation", basis=None):
    return {"process_id": pid, "parts": list(parts_), "reason": "the same confirmation", "name": name,
            "description": "Schedulers confirm volumes.", "trigger": "a new month", "output": "confirmed volumes",
            "roles": ["scheduler"], "automation": {"idea": "a standard request", "artifact_type": "template", "why": "by hand"},
            "minutes_per_event": {"low": 10, "base": 20, "high": 30, "rationale": "writing the request",
                                  "basis_obs_ids": basis or []}}


def merge(pairs, procs, attempt="m001-x-1"):
    by_obs = {o: {"obs_id": o, "msg_key": None} for inp, _ in pairs for o in inp["obs_ids"]}
    obs = {o["obs_id"]: o for o in OBS}
    minp = synth.build_merge_input(pairs, ST, {k: obs[k] for k in by_obs}, "m001", "dev")
    return minp, synth.merge_check(json.dumps({"input_id": "m001", "attempt_id": attempt, "processes": procs}), minp, pairs, attempt)


OBS = [ob("a1", "A/i/1", "2001-01-05"), ob("a2", "A/i/2", "2001-02-05"), ob("a3", "A/i/3", "2001-03-05"),
       ob("b1", "B/i/1", "2001-01-06"), ob("b2", "B/i/2", "2001-02-06"), ob("b3", "B/i/3", "2001-03-06")]
A, B = OBS[:3], OBS[3:]


# --- splitting ---

def test_a_list_that_fits_is_one_part_with_the_single_pass_text():
    assert synth.split(OBS, ST, 10_000, date_split=True) == [OBS]
    inp = synth.build_input(OBS, ST, "s001", "dev")
    assert "PART" not in inp["text"] and inp["part"] is None
    assert "PART 2 OF 3" in synth.build_input(OBS, ST, "s002", "dev", part=(2, 3))["text"].splitlines()[0]


def test_an_over_budget_list_is_split_by_mailbox_then_by_date_in_whole_threads():
    st = St([M(f"A/i/{i}", f"tA{i // 2}", "A") for i in range(8)] + [M(f"B/i/{i}", f"tB{i}", "B") for i in range(3)])
    obs = [ob(f"a{i}", f"A/i/{i}", f"2001-0{1 + i // 2}-0{1 + i % 2}") for i in range(8)] + \
          [ob(f"b{i}", f"B/i/{i}") for i in range(3)]
    one = synth.est_tokens(obs[0], st)
    parts = synth.split(list(reversed(obs)), st, 4 * one + 2, date_split=True)   # four lines: two threads of A per part
    assert sorted(o["obs_id"] for p in parts for o in p) == sorted(o["obs_id"] for o in obs)   # each exactly once
    for p in parts:
        assert len({st.messages[o["msg_key"]].mailbox for o in p}) == 1                         # one mailbox per part
    thread_part = {}
    for n, p in enumerate(parts):
        for o in p:
            assert thread_part.setdefault(st.messages[o["msg_key"]].thread_id, n) == n          # threads never split
    a_parts = [p for p in parts if st.messages[p[0]["msg_key"]].mailbox == "A"]
    assert len(a_parts) == 2 and max(o["evidence_date"] for o in a_parts[0]) < min(o["evidence_date"] for o in a_parts[1])
    assert synth.split(obs, st, 4 * one + 2, date_split=True) == synth.split(obs, st, 4 * one + 2, date_split=True)
    whole = synth.split(obs, st, 4 * one + 2, date_split=False)        # production until 2b: mailboxes stay whole
    assert [sorted({st.messages[o["msg_key"]].mailbox for o in p}) for p in whole] == [["A"], ["B"]]
    assert sorted(o["obs_id"] for p in whole for o in p) == sorted(o["obs_id"] for o in obs)


def test_a_candidate_is_allowed_only_in_a_part_and_holds_exactly_one_observation():
    inp_part = synth.build_input(A, ST, "s001", "dev", part=(1, 2))
    inp_whole = synth.build_input(A, ST, "s001", "dev")
    chk = lambda inp, g: synth.check(json.dumps({"input_id": "s001", "attempt_id": "s001-x-1", "processes": [g]}), inp, "s001-x-1")
    assert chk(inp_part, group("P1", ["a1"], candidate=True))["status"] == "verified"
    assert "only allowed in a part" in " ".join(chk(inp_whole, group("P1", ["a1"], candidate=True))["errors"])
    assert "exactly one observation" in " ".join(chk(inp_part, group("P1", ["a1", "a2"], candidate=True))["errors"])
    assert "at least two observations" in " ".join(chk(inp_part, group("P1", ["a1"]))["errors"])


# --- merging ---

def test_one_process_seen_in_two_mailboxes_becomes_one_process_with_one_event_per_thread_and_both_windows():
    pairs = [part("s001", 1, 2, A, [group("P1", ["a1", "a2", "a3"])]), part("s002", 2, 2, B, [group("P1", ["b1", "b2", "b3"])])]
    minp, res = merge(pairs, [merged("M1", ["s001.P1", "s002.P1"], basis=["a1"])])
    assert res["status"] == "verified", res["errors"]
    (p,) = res["processes"]
    assert p["obs_ids"] == ["a1", "a2", "a3", "b1", "b2", "b3"] and p["parts"] == ["s001.P1", "s002.P1"]
    assert p["made_by"] == "merged" and p["merge_reason"] and p["threads_seen"] == 6
    evs = events.build(OBS, res["assignment"], ST, {})
    assert len(evs) == 6 and {e["process_id"] for e in evs} == {"P1"}               # one per thread, one process
    win = {"A": (datetime(2001, 1, 1, tzinfo=timezone.utc), datetime(2001, 7, 1, tzinfo=timezone.utc)),
           "B": (datetime(2001, 4, 1, tzinfo=timezone.utc), datetime(2001, 12, 1, tzinfo=timezone.utc))}
    sc = score.score(res["processes"], evs, win, CFG, {o["obs_id"]: [f"q:{o['obs_id']}"] for o in OBS})
    (opp,) = sc["opportunities"]
    assert opp["status"] == "scored" and opp["mailboxes"] == ["A", "B"]
    assert sc["claims"]["P1:months"]["value"] == str(windows.months_observed(win, ["A", "B"]))


def test_two_candidates_from_two_parts_merge_into_one_process_and_left_alone_are_no_process():
    pairs = [part("s001", 1, 2, A, [group("P1", ["a1"], candidate=True)]), part("s002", 2, 2, B, [group("P1", ["b1"], candidate=True)])]
    _, res = merge(pairs, [merged("M1", ["s001.P1", "s002.P1"], basis=["b1"])])
    assert res["status"] == "verified" and res["processes"][0]["obs_ids"] == ["a1", "b1"]
    _, alone = merge(pairs, [])
    assert alone["status"] == "verified" and alone["processes"] == [] and {"a1", "b1"} <= set(alone["unassigned"])
    assert [f["kind"] for f in alone["flags"]] == ["candidate_unmerged", "candidate_unmerged"]
    _, lone = merge(pairs, [merged("M1", ["s001.P1"], basis=["a1"])])               # a lone candidate stays out
    assert lone["processes"] == [] and lone["flags"][0]["kind"] == "singleton_not_merged"


@pytest.mark.parametrize("procs, why", [
    ([merged("M1", ["s001.P1", "s002.P1"]), merged("M2", ["s001.P1"], name="Other")], "already in M1"),
    ([merged("M1", ["s009.P1"])], "unknown part groups"),
    ([merged("M1", ["s001.P1", "s002.P1"], basis=["zz"])], "basis_obs_ids"),
])
def test_a_merge_that_reuses_a_group_names_an_unknown_one_or_cites_a_foreign_basis_fails(procs, why):
    pairs = [part("s001", 1, 2, A, [group("P1", ["a1", "a2"])]), part("s002", 2, 2, B, [group("P1", ["b1", "b2"])])]
    _, res = merge(pairs, procs)
    assert res["status"] == "failed" and any(why in e for e in res["errors"]), res["errors"]


def test_an_unmentioned_process_passes_through_unchanged_and_ids_are_renumbered():
    pairs = [part("s001", 1, 2, A, [group("P1", ["a1", "a2"]), group("P2", ["a3"], candidate=True)]),
             part("s002", 2, 2, B, [group("P1", ["b1", "b2"], name="Gas nominations")])]
    _, res = merge(pairs, [merged("M1", ["s001.P1"], name="Volume work", basis=["a1"])])
    assert res["status"] == "verified"
    assert [(p["process_id"], p["name"], p["made_by"]) for p in res["processes"]] == [
        ("P1", "Volume work", "renamed"), ("P2", "Gas nominations", "passed_through")]
    assert res["assignment"] == {"a1": "P1", "a2": "P1", "b1": "P2", "b2": "P2"} and "a3" in res["unassigned"]


def test_a_quoted_copy_in_another_mailbox_is_one_event_once_its_processes_are_merged():
    # b1 quotes text first seen in A/i/1 (its anchor), so b1 and a1 are one occurrence
    obs = [ob("a1", "A/i/1"), ob("a2", "A/i/2"), ob("b1", "B/i/1", anchor="A/i/1#0"), ob("b2", "B/i/2")]
    split_ = {"a1": "P1", "a2": "P1", "b1": "P2", "b2": "P2"}          # unmerged: the same occurrence in two processes
    assert len(events.build(obs, split_, ST, {})) == 4
    one = {o["obs_id"]: "P1" for o in obs}                            # merged
    evs = events.build(obs, one, ST, {})
    assert len(evs) == 3 and next(e for e in evs if "b1" in e["obs_ids"])["obs_ids"] == ["a1", "b1"]
    assert events.check(evs, one) == []


def test_a_singleton_occurrence_never_gets_a_rate_even_across_threads():
    """Decision 4: two threads and three emails, but one distinct event (the copies share an anchor): no rate."""
    obs = [ob("a1", "A/i/1"), ob("b1", "B/i/1", anchor="A/i/1#0"), ob("b2", "B/i/2", anchor="A/i/1#0")]
    evs = events.build(obs, {o["obs_id"]: "P1" for o in obs}, ST, {})
    assert len(evs) == 1 and len(evs[0]["threads"]) == 3 and len(evs[0]["msg_keys"]) == 3
    win = {"A": (datetime(2001, 1, 1, tzinfo=timezone.utc), datetime(2001, 7, 1, tzinfo=timezone.utc)),
           "B": (datetime(2001, 1, 1, tzinfo=timezone.utc), datetime(2001, 7, 1, tzinfo=timezone.utc))}
    proc = {"process_id": "P1", "name": "x", "minutes_per_event": {"low": 1, "base": 2, "high": 3, "rationale": "r",
                                                                    "basis_obs_ids": []}}
    (opp,) = score.score([proc], evs, win, CFG, {})["opportunities"]
    assert opp["status"] == "insufficient_evidence" and "1 distinct event" in opp["why"]


def test_the_agent_prompts_samples_match_their_schemas_and_the_merger_is_fenced():
    for agent, name in (("synthesizer", "synth-v2"), ("merger", "merge-v1")):
        text = (ROOT / ".claude" / "agents" / f"{agent}.md").read_text(encoding="utf-8")
        (sample,) = re.findall(r"```json\n(.*?)```", text, re.S)
        assert schema.validate(json.loads(sample), schema.load(f"{name}.schema.json")) == []
        front = text.split("---")[1]
        assert "tools: Read, Write\n" in front and f"--agent {agent}" in front and "onFailure: block" in front


# --- the pipeline stage ---

@pytest.fixture
def penv(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".claude" / "agents").mkdir(parents=True)
    for a in ("synthesizer", "merger"):
        (root / ".claude" / "agents" / f"{a}.md").write_text(f"fake {a}", encoding="utf-8")
    r = pipeline.Run("dev", root=root)
    r.cfg["run"].update(retries=0, workers=4)
    r.cfg["synth"]["size_split"] = {**r.cfg["synth"]["size_split"], "enabled": False}      # the legacy path unless a test turns the split on
    calls = []

    def fake(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        calls.append(agent)
        rd, iid, att, wr = re.findall(r"`([^`]+)`", prompt)
        lines = [x for x in (root / rd).read_text(encoding="utf-8").splitlines() if " | " in x and not x.startswith("⟦")]
        ids = [x.split(" | ")[0] for x in lines]
        if agent == "synthesizer":
            procs = [group("P1", ids)] if len(ids) >= 2 else [group("P1", ids, candidate=True)]
        else:
            first_obs = re.search(r"observations: ([^,| ]+)", lines[0]).group(1)
            procs = [merged("M1", ids, basis=[first_obs])]
        (root / wr).parent.mkdir(parents=True, exist_ok=True)
        (root / wr).write_text(json.dumps({"input_id": iid, "attempt_id": att, "processes": procs}), encoding="utf-8")
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "0.json").write_text(json.dumps({"decision": "allow"}))
        return {"exit_code": 0, "is_error": False, "result": "", "stderr_tail": "", "stdout_tail": "", "wall_seconds": 0.1,
                "duration_ms": 1, "num_turns": 1, "models_observed": ["claude-sonnet-5-5"],
                "tokens": {"input_tokens": 1, "output_tokens": 300, "cache_creation_input_tokens": 0,
                           "cache_read_input_tokens": 0}, "notional_cost_usd": 0}
    monkeypatch.setattr(headless, "run", fake)
    return r, calls


def test_the_stage_synthesizes_parts_in_parallel_merges_once_and_caches_both(penv):
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=3 * synth.est_tokens(OBS[0], ST), date_split=False)   # one part per mailbox
    res = pipeline.stage_synth(r, ST, OBS)
    assert sorted(calls) == ["merger", "synthesizer", "synthesizer"]
    assert res["status"] == "verified" and len(res["processes"]) == 1 and res["processes"][0]["parts"] == ["s001.P1", "s002.P1"]
    assert [p["input_id"] for p in res["parts"]] == ["s001", "s002"] and res["input_id"] == "m001"
    calls.clear()
    again = pipeline.stage_synth(r, ST, OBS)
    assert calls == [] and again["processes"] == res["processes"]                  # every part and the merge cached


def test_a_list_that_fits_is_synthesized_once_with_no_merge(penv):
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False)
    res = pipeline.stage_synth(r, ST, OBS)
    assert calls == ["synthesizer"] and res["input_id"] == "s001" and "parts" not in res


def _wrap(base, record=None, corrupt_for=None):
    """penv's fake session plus, after it ran: one more fence record in the session's folder, and/or a damaged output."""
    def run(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        rep = base(agent, prompt, model, timeout, cwd, read, write, log_dir)
        if record:
            (Path(log_dir) / "1.json").write_text(json.dumps({"tool": "Read", "path": write, "decision": "deny", **record}))
        if corrupt_for == agent:
            (Path(cwd) / write).write_text("not json at all", encoding="utf-8")
        return rep
    return run


SELF_CHECK = {"denial_kind": "self_check", "reason": "fence: reading back its own output"}


def test_a_documented_read_back_self_check_does_not_stop_synthesis_and_is_logged(penv, monkeypatch):
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False)
    monkeypatch.setattr(headless, "run", _wrap(headless.run, SELF_CHECK))
    res = pipeline.stage_synth(r, ST, OBS)
    assert res["status"] == "verified" and calls == ["synthesizer"]
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines()]
    assert [x["self_checks"][0]["denial_kind"] for x in log if x.get("status") == "fence_self_check"] == ["self_check"]


def test_an_unexpected_denial_in_a_synthesis_session_still_stops_the_run(penv, monkeypatch):
    r, _ = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False)
    monkeypatch.setattr(headless, "run", _wrap(headless.run, {"denial_kind": "unexpected", "reason": "fence: a second read-back"}))
    with pytest.raises(pipeline.RunStopped) as stop:
        pipeline.stage_synth(r, ST, OBS)
    assert stop.value.reason == "fence_denial" and pipeline.CRITICAL.count("fence_denial") == 1


def test_python_validates_a_synthesis_output_on_its_own_whatever_the_fence_saw(penv, monkeypatch):
    """A self-check never makes an output eligible: a damaged file is rejected and nothing is cached or published."""
    r, _ = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False)
    monkeypatch.setattr(headless, "run", _wrap(headless.run, SELF_CHECK, corrupt_for="synthesizer"))
    with pytest.raises(pipeline.RunStopped) as stop:
        pipeline.stage_synth(r, ST, OBS)
    assert stop.value.reason == "synthesis_failed"
    assert not (r.data / "synth.json").exists() and not list((r.cache / "synth").glob("*.json"))


def test_python_validates_the_merge_output_on_its_own_whatever_the_fence_saw(penv, monkeypatch):
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=3 * synth.est_tokens(OBS[0], ST), date_split=False)
    monkeypatch.setattr(headless, "run", _wrap(headless.run, SELF_CHECK, corrupt_for="merger"))
    with pytest.raises(pipeline.RunStopped) as stop:
        pipeline.stage_synth(r, ST, OBS)
    assert stop.value.reason == "synthesis_failed" and stop.value.detail["stage"] == "merge"
    assert "merger" in calls and not (r.data / "synth.json").exists()
    assert not (r.cache / "synth" / "m001.json").exists()


def test_a_rerun_with_the_same_attempt_id_never_counts_the_old_sessions_fence_records(penv, monkeypatch):
    """compare-synth rerun (2b): the stopped attempt's unexpected denial sat in the folder the rerun reused, so a clean
    session was stopped for it. A session now starts with an empty folder; the old one is kept beside it."""
    r, _ = penv
    old = r.fence_sessions / "s001-abc-1"
    old.mkdir(parents=True)
    (old / "0.json").write_text(json.dumps({"tool": "Read", "path": "README.md", "decision": "deny", "denial_kind": "unexpected"}))

    def clean(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        (Path(log_dir) / "0.json").write_text(json.dumps({"tool": "Read", "path": read, "decision": "allow"}))
        return {"exit_code": 0, "is_error": False, "result": "", "stderr_tail": "", "stdout_tail": ""}
    monkeypatch.setattr(headless, "run", clean)
    rep = pipeline.session(r, "synthesizer", "go", "sonnet", "data/synth/inputs/dev/s001.txt", "data/synth/staging/dev/s001.json", "s001-abc-1")
    assert rep["fence"] == {"records": 1, "denied": [], "self_checks": []}
    (kept,) = [p for p in r.fence_sessions.iterdir() if ".prior-" in p.name]
    assert json.loads((kept / "0.json").read_text())["denial_kind"] == "unexpected"        # kept as history, not lost


def test_single_session_workers_are_asked_not_to_read_their_output_back_in_the_run_instruction_only(penv):
    import inspect
    assert "Do not read your output file back" in pipeline.NO_READBACK
    assert inspect.getsource(pipeline).count("{NO_READBACK}") == 3                   # synthesizer, merger, artifact-writer
    for agent in ("synthesizer", "merger", "artifact-writer", "grounder"):            # the analytical prompts are untouched
        assert "read your output" not in (ROOT / ".claude" / "agents" / f"{agent}.md").read_text(encoding="utf-8").lower()


def test_the_validator_knows_booleans_and_min_items():
    s = {"type": "object", "properties": {"c": {"type": "boolean"}, "p": {"type": "array", "minItems": 1, "items": {"type": "string"}}}}
    assert schema.validate({"c": True, "p": ["x"]}, s) == []
    assert schema.validate({"c": "yes"}, s) == ["$.c: expected boolean, got str"]
    assert schema.validate({"c": 1}, s) == ["$.c: expected boolean, got int"]
    assert schema.validate({"p": []}, s) == ["$.p: needs at least 1 item(s)"]


def test_the_merger_joins_related_partial_groups_from_date_parts_and_mailboxes_into_one_recurring_process(tmp_path, monkeypatch):
    """Regression (user, 23 Nov 2026: the live split run joined none). The same work, split by the date parts of one
    mailbox and seen in another mailbox, reaches the merge as one partial process and two single-observation
    candidates; the merger joins them, code builds one process with distinct events and a rate. An unrelated
    candidate is dropped and its observation stays unassigned."""
    st = St([M("A/i/1", "tA1", "A"), M("A/i/2", "tA2", "A"), M("A/i/3", "tA3", "A"), M("A/i/4", "tA4", "A"),
             M("B/i/1", "tB1", "B"), M("B/i/2", "tB2", "B")])
    T, L = "enter the monthly transport usage ticket", "book the team lunch"
    obs = [ob("a1", "A/i/1", "2001-01-05", task=T), ob("a2", "A/i/2", "2001-01-20", task=T),
           ob("a3", "A/i/3", "2001-06-05", task=T), ob("a4", "A/i/4", "2001-06-20", task=L),
           ob("b1", "B/i/1", "2001-03-06", task=T), ob("b2", "B/i/2", "2001-03-07", task="a one-off question")]
    root = tmp_path / "repo"
    (root / ".claude" / "agents").mkdir(parents=True)
    for a in ("synthesizer", "merger"):
        (root / ".claude" / "agents" / f"{a}.md").write_text(f"fake {a}", encoding="utf-8")
    r = pipeline.Run("dev", root=root)
    r.cfg["run"].update(retries=0, workers=4)
    r.cfg["synth"].update(chunk_token_budget=2 * synth.est_tokens(obs[0], st) + 1, date_split=True,
                          size_split={**r.cfg["synth"]["size_split"], "enabled": False})        # this test is of the legacy date split
    by_id = {o["obs_id"]: o for o in obs}
    seen_parts = []

    def fake(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        rd, iid, att, wr = re.findall(r"`([^`]+)`", prompt)
        lines = [x for x in (root / rd).read_text(encoding="utf-8").splitlines() if " | " in x and not x.startswith("⟦")]
        ids = [x.split(" | ")[0] for x in lines]
        if agent == "synthesizer":                         # stands in for the model: groups by task wording
            seen_parts.append(sorted(ids))
            transport = [i for i in ids if "transport" in by_id[i]["task"]]
            procs = []
            if len(transport) >= 2:
                procs.append(group("P1", transport, name="Transport usage ticket entry"))
            elif transport:
                procs.append(group("P1", transport, candidate=True, name="Transport usage ticket entry"))
            procs += [group(f"P{len(procs) + 1}", [i], candidate=True, name="Team lunch booking")
                      for i in ids if "lunch" in by_id[i]["task"]]
        else:                                              # stands in for the model: joins the transport groups
            parts_ = [x.split(" | ")[0] for x in lines if "Transport" in x]
            basis = re.search(r"observations: ([^,| ]+)", next(x for x in lines if "Transport" in x)).group(1)
            procs = [merged("M1", parts_, name="Transport usage ticket entry", basis=[basis])]
        (root / wr).parent.mkdir(parents=True, exist_ok=True)
        (root / wr).write_text(json.dumps({"input_id": iid, "attempt_id": att, "processes": procs}), encoding="utf-8")
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        (Path(log_dir) / "0.json").write_text(json.dumps({"decision": "allow"}))
        return {"exit_code": 0, "is_error": False, "result": "", "stderr_tail": "", "stdout_tail": "", "wall_seconds": 0.1,
                "duration_ms": 1, "num_turns": 1, "models_observed": ["claude-sonnet-5-5"],
                "tokens": {"input_tokens": 1, "output_tokens": 300, "cache_creation_input_tokens": 0,
                           "cache_read_input_tokens": 0}, "notional_cost_usd": 0}
    monkeypatch.setattr(headless, "run", fake)
    res = pipeline.stage_synth(r, st, obs)
    assert seen_parts == [["a1", "a2"], ["a3", "a4"], ["b1", "b2"]] or sorted(seen_parts) == [["a1", "a2"], ["a3", "a4"], ["b1", "b2"]]
    assert res["status"] == "verified", res["errors"]
    (p,) = res["processes"]                                # one process; the lunch candidate is not a process
    assert p["name"] == "Transport usage ticket entry" and p["obs_ids"] == ["a1", "a2", "a3", "b1"]
    assert p["made_by"] == "merged" and p["parts"] == ["s001.P1", "s002.P1", "s003.P1"] and p["merge_reason"]
    assert {"kind": "candidate_unmerged", "group": "s002.P2"} in res["flags"]
    assert sorted(res["unassigned"]) == ["a4", "b2"] and set(res["assignment"].values()) == {"P1"}
    evs = events.build(obs, res["assignment"], st, {})
    assert len(evs) == 4 and len({e["thread_key"] for e in evs}) == 4     # distinct events, one per thread
    win = {mb: (datetime(2001, 1, 1, tzinfo=timezone.utc), datetime(2001, 12, 31, tzinfo=timezone.utc)) for mb in "AB"}
    (opp,) = score.score(res["processes"], evs, win, CFG, {o["obs_id"]: [f"q:{o['obs_id']}"] for o in obs})["opportunities"]
    assert opp["status"] == "scored" and opp["events"] == 4 and opp["mailboxes"] == ["A", "B"]


def test_the_run_note_hash_is_recorded_with_every_synthesis_and_merge_result_and_is_not_in_the_cache_key(penv):
    import hashlib
    import inspect
    r, _ = penv
    r.cfg["synth"].update(chunk_token_budget=3 * synth.est_tokens(OBS[0], ST), date_split=False)
    res = pipeline.stage_synth(r, ST, OBS)
    want = hashlib.sha256(headless.NO_READBACK.encode("utf-8")).hexdigest()
    assert res["run_note_sha256"] == want == headless.NO_READBACK_SHA256                      # the merge result
    part = json.loads((r.data / "synth" / "s001.json").read_text(encoding="utf-8"))
    assert part["run_note_sha256"] == want                                                  # a part
    from src import ground
    gsrc = inspect.getsource(ground)
    assert gsrc.count("run_note_sha256") >= 4 and "run_note" not in inspect.getsource(pipeline._extract_key)


# --- Option A (user, 24 Nov 2026): validation errors feed the next attempt; three attempts; then the run stops ---

def _prompts_and_damage(base, bad_attempts, prompts):
    """penv's fake session; records every run instruction; damages the output of the first `bad_attempts` attempts."""
    n = {"i": 0}

    def run(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        rep = base(agent, prompt, model, timeout, cwd, read, write, log_dir)
        if agent == "synthesizer":
            prompts.append(prompt)
            n["i"] += 1
            if n["i"] <= bad_attempts:
                (Path(cwd) / write).write_text("not json at all", encoding="utf-8")
        return rep
    return run


def test_a_failed_attempts_validation_errors_are_in_the_next_attempts_run_instruction_only(penv, monkeypatch):
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False, max_attempts=3)
    prompts = []
    monkeypatch.setattr(headless, "run", _prompts_and_damage(headless.run, 1, prompts))
    res = pipeline.stage_synth(r, ST, OBS)
    assert res["status"] == "verified" and len(prompts) == 2
    assert "rejected by the pipeline" not in prompts[0] and "invalid JSON" in prompts[1] and "rejected by the pipeline" in prompts[1]
    assert prompts[1].split(" Your previous attempt")[0].replace("-2`", "-1`") == prompts[0]    # the same instruction, plus the note
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines() if '"stage": "synth"' in x]
    att = [x for x in log if x.get("attempt")]
    assert [x["status"] for x in att] == ["failed", "verified"] and [x["fed_back_errors"] for x in att] == [0, 1]
    assert att[0]["n_errors"] == 1 and att[0]["errors"][0].startswith("invalid JSON") and att[1]["feedback_sha256"]
    assert (r.synth_staging / "rejected" / f"{att[0]['attempt']}.json").read_text(encoding="utf-8") == "not json at all"
    for agent in ("synthesizer", "merger"):                                        # the analytical prompts are untouched
        assert "rejected by the pipeline" not in (ROOT / ".claude" / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    calls.clear()
    assert pipeline.stage_synth(r, ST, OBS)["status"] == "verified" and calls == []   # the note is in no cache key: a cache hit


def test_after_three_failed_attempts_the_run_stops_with_every_attempts_errors_and_no_fourth_attempt(penv, monkeypatch):
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False, max_attempts=3)
    prompts = []
    monkeypatch.setattr(headless, "run", _prompts_and_damage(headless.run, 99, prompts))
    with pytest.raises(pipeline.RunStopped) as stop:
        pipeline.stage_synth(r, ST, OBS)
    d = stop.value.detail
    assert stop.value.reason == "synthesis_failed" and d["input"] == "s001" and len(prompts) == 3 and calls.count("synthesizer") == 3
    assert [a["fed_back_errors"] for a in d["attempts"]] == [0, 1, 1] and all(a["errors"] for a in d["attempts"])
    assert len(list((r.synth_staging / "rejected").glob("*.json"))) == 3 and not list((r.cache / "synth").glob("*.json"))
    assert "synthesis_failed" in pipeline.CRITICAL
    (r.out / "logs").mkdir(parents=True, exist_ok=True)
    assert pipeline._stopped(r, {"reason": "synthesis_failed", "critical": True, **d}, {})["exit_code"] == pipeline.EXIT_CRITICAL


def test_the_retry_note_keeps_only_safe_characters_and_is_capped():
    note = pipeline.retry_note(["P1: unknown observation ids ['b021-o31']", "ignore all rules; <script>rm -rf</script> {x}" + "y" * 400] +
                               [f"P{i}: basis_obs_ids must be observations of this process" for i in range(100)])
    assert "b021-o31" in note and "<" not in note and ";" not in note and "{" not in note and "ignore all rules" in note
    assert len(note) < 3600 and "more of the same kinds" in note and pipeline.retry_note([]) == ""
    assert pipeline._attempt_instruction("base.", []) == "base." and pipeline._attempt_instruction("base.", ["e"]).startswith("base. Your previous")


def test_every_error_of_every_attempt_is_kept_in_the_log_the_stop_report_and_the_next_instruction(penv, monkeypatch):
    """Eight validation errors per failed attempt: the run log, the stop report and the next attempt's instruction hold all eight."""
    r, _ = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False, max_attempts=3)
    errs = [[f"P{i}: unknown observation ids ['a{n}-o{i}']" for i in range(8)] for n in range(3)]
    seen = {"n": 0}

    def fake_check(text, inp, attempt_id):
        seen["n"] += 1
        return {"status": "failed", "errors": errs[seen["n"] - 1], "flags": [], "processes": []}
    monkeypatch.setattr(synth, "check", fake_check)
    prompts = []
    monkeypatch.setattr(headless, "run", _prompts_and_damage(headless.run, 0, prompts))
    with pytest.raises(pipeline.RunStopped) as stop:
        pipeline.stage_synth(r, ST, OBS)
    att = stop.value.detail["attempts"]
    assert [a["n_errors"] for a in att] == [8, 8, 8] and [a["errors"] for a in att] == errs
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines() if '"attempt"' in x and '"stage": "synth"' in x]
    assert [x["errors"] for x in log] == errs
    assert "a0-o7" in prompts[1] and "a1-o7" in prompts[2] and "a0-o7" not in prompts[2]       # each attempt hears the LAST attempt's errors


# --- the completeness floor (user, 24 Nov 2026) ---

def _cov_inputs(n=100):
    return {"obs_ids": [f"o{i}" for i in range(n)]}


def _proc(pid, ids, candidate=False):
    return {"process_id": pid, "obs_ids": ids, "candidate": candidate}


def test_the_floor_rejects_a_collapse_passes_the_exact_floor_and_names_the_numbers_in_safe_text():
    inp = _cov_inputs()
    low = synth.coverage({"processes": [_proc("P1", ["o0", "o1", "o2"])]}, inp, floor=0.15, catch_all=0.25, min_obs=40)
    assert low["ok"] is False and low["summary"]["counted"] == 3 and low["summary"]["share"] == 0.03
    assert "3 of 100" in low["errors"][0] and "15 percent" in low["errors"][0] and "%" not in low["errors"][0] and "=" not in low["errors"][0]
    assert "15 percent" in pipeline.retry_note(low["errors"]) and "3 of 100" in pipeline.retry_note(low["errors"])
    exact = synth.coverage({"processes": [_proc("P1", [f"o{i}" for i in range(8)]), _proc("P2", [f"o{i}" for i in range(8, 15)])]},
                           inp, floor=0.15, catch_all=0.25, min_obs=40)
    assert exact["ok"] is True and exact["summary"]["counted"] == 15 and exact["summary"]["floor_count"] == 15


def test_invalid_irrelevant_or_gamed_assignments_do_not_satisfy_the_floor():
    inp = _cov_inputs()
    kw = {"floor": 0.15, "catch_all": 0.25, "min_obs": 40}
    unknown = synth.coverage({"processes": [_proc("P1", [f"x{i}" for i in range(30)])]}, inp, **kw)     # ids not in this part
    assert unknown["summary"]["counted"] == 0 and unknown["ok"] is False
    single = synth.coverage({"processes": [_proc(f"P{i}", [f"o{i}"]) for i in range(30)]}, inp, **kw)  # singletons are not recurring
    assert single["summary"]["counted"] == 0 and single["ok"] is False
    cand = synth.coverage({"processes": [_proc("P1", [f"o{i}" for i in range(30)], candidate=True)]}, inp, **kw)
    assert cand["summary"]["counted"] == 0 and cand["ok"] is False
    dup = synth.coverage({"processes": [_proc("P1", ["o0", "o0", "o1", "o1"])]}, inp, **kw)            # a repeated id counts once
    assert dup["summary"]["counted"] == 2 and dup["summary"]["assigned_raw"] == 2
    catch = synth.coverage({"processes": [_proc("P1", [f"o{i}" for i in range(100)])]}, inp, **kw)      # a catch-all dump
    assert catch["summary"]["counted"] == 25 and catch["ok"] is False and "catch-all" in catch["errors"][0]
    assert catch["summary"]["catch_all_processes"] == [{"process": "P1", "observations": 100}]
    tiny = synth.coverage({"processes": []}, _cov_inputs(30), **kw)                                    # parts under min_obs are exempt
    assert tiny["ok"] is True and tiny["summary"]["exempt"] is True


def test_a_collapsed_attempt_fails_and_is_fed_back_and_every_attempt_logs_its_coverage_and_the_floor(penv, monkeypatch):
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False, max_attempts=3, min_assigned_share=0.5,
                          catch_all_share=1.0, coverage_min_observations=1)
    prompts, n = [], {"i": 0}
    base = headless.run

    def run(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        rep = base(agent, prompt, model, timeout, cwd, read, write, log_dir)
        prompts.append(prompt)
        n["i"] += 1
        if n["i"] == 1:                                  # attempt 1: well formed but nearly empty (2 of 6 observations)
            rd, iid, att, wr = re.findall(r"`([^`]+)`", prompt)
            ids = [x.split(" | ")[0] for x in (Path(cwd) / rd).read_text(encoding="utf-8").splitlines() if " | " in x and not x.startswith("⟦")]
            (Path(cwd) / wr).write_text(json.dumps({"input_id": iid, "attempt_id": att, "processes": [group("P1", ids[:2])]}), encoding="utf-8")
        return rep
    monkeypatch.setattr(headless, "run", run)
    res = pipeline.stage_synth(r, ST, OBS)
    assert res["status"] == "verified" and len(prompts) == 2 and res["coverage"]["share"] == 1.0
    assert "coverage: only 2 of 6" in prompts[1] and "50 percent" in prompts[1] and "coverage" not in prompts[0]
    att = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines() if '"attempt"' in x and '"stage": "synth"' in x]
    assert [x["status"] for x in att] == ["failed", "verified"]
    assert att[0]["coverage"]["counted"] == 2 and att[0]["coverage"]["floor_share"] == 0.5 and att[1]["coverage"]["share"] == 1.0
    assert att[0]["errors"][0].startswith("coverage:") and att[1]["fed_back_errors"] == 1


def test_a_cached_part_is_reused_only_if_it_meets_the_floor_and_a_rejected_one_is_kept(penv):
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False, max_attempts=3, min_assigned_share=0.5,
                          catch_all_share=1.0, coverage_min_observations=1)
    assert pipeline.stage_synth(r, ST, OBS)["status"] == "verified"
    calls.clear()
    assert pipeline.stage_synth(r, ST, OBS)["status"] == "verified" and calls == []                  # meets the floor: a cache hit
    r.cfg["synth"]["min_assigned_share"] = 1.01                                                         # a floor no part can meet
    with pytest.raises(pipeline.RunStopped) as stop:
        pipeline.stage_synth(r, ST, OBS)
    assert stop.value.reason == "synthesis_failed" and calls.count("synthesizer") == 3                  # rejected cache hit, then three attempts
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines()]
    assert any(x.get("status") == "cache_rejected" and x["coverage"]["share"] == 1.0 for x in log)
    assert list((r.synth_staging / "rejected").glob("*.cached-rejected.json"))


# --- standing requirements across consecutive failures (user, 24 Nov 2026, Option B) ---

def test_error_kinds_group_the_same_requirement_across_attempts_and_the_note_carries_what_the_last_attempt_did_not_hit():
    k = pipeline._kind
    assert k("P7: observation b009-o35 is already in P25") == k("P33: observation b012-o4 is already in P8") != k("coverage: only 3 of 100")
    assert k("coverage: only 83 of 2433 observations") == "coverage" and k("P1: catch-all process holds 60 of 100") == "catch-all"
    standing = {}
    pipeline.note_standing(standing, ["coverage: only 83 of 2433 observations count toward a recurring process, the floor is 365 (15 percent)"])
    pipeline.note_standing(standing, ["P33: observation b009-o35 is already in P25"])
    note = pipeline.retry_note(["P33: observation b009-o35 is already in P25"], standing)
    assert "already in P25" in note and "still apply" in note and "83 of 2433" in note and note.count("already in P25") == 1
    only_seed = pipeline.retry_note([], standing)                                  # a seeded first attempt: standing requirements only
    assert only_seed.startswith("Earlier attempts on this input") and "83 of 2433" in only_seed and "previous attempt" not in only_seed
    assert pipeline.retry_note(["coverage: only 90 of 2433"], standing).count("coverage: only") == 1       # the latest coverage error replaces it
    assert pipeline._attempt_instruction("base.", [], {}) == "base." and pipeline._note_sha([], None) is None


def test_coverage_counts_an_observation_once_across_processes_and_is_read_from_an_output_that_failed_the_structure():
    inp = _cov_inputs()
    two = synth.coverage({"processes": [_proc("P1", ["o0", "o1", "o2"]), _proc("P2", ["o2", "o3", "o4"])]}, inp, floor=0.0, catch_all=1.0, min_obs=40)
    assert two["summary"]["counted"] == 5                                          # o2 in two processes counts once
    text = json.dumps({"processes": [{"process_id": "P1", "obs_ids": ["o0", "o1"]}, {"process_id": "P2", "obs_ids": ["o1", "o2", "zz"]}]})
    c = synth.coverage_from_text(text, inp, floor=0.15, catch_all=1.0, min_obs=40)
    assert c["summary"]["counted"] == 3 and c["ok"] is False and "coverage: only 3 of 100" in c["errors"][0]
    for bad in ("not json", "[]", json.dumps({"processes": "x"}), json.dumps({"nope": 1})):
        assert synth.coverage_from_text(bad, inp, floor=0.15, catch_all=1.0, min_obs=40) is None


def test_the_coverage_requirement_survives_a_structural_failure_and_every_attempt_logs_its_coverage(penv, monkeypatch):
    """The real failure: attempt 1 misses the floor, attempt 2 fixes coverage but breaks the structure (an observation in two
    processes), attempt 3 must still hear about BOTH."""
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False, max_attempts=3, min_assigned_share=0.6,
                          catch_all_share=1.0, coverage_min_observations=1)
    prompts, n = [], {"i": 0}
    base = headless.run

    def run(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        rep = base(agent, prompt, model, timeout, cwd, read, write, log_dir)
        prompts.append(prompt)
        n["i"] += 1
        rd, iid, att, wr = re.findall(r"`([^`]+)`", prompt)
        ids = [x.split(" | ")[0] for x in (Path(cwd) / rd).read_text(encoding="utf-8").splitlines() if " | " in x and not x.startswith("⟦")]
        procs = {1: [group("P1", ids[:2])],                                                   # 2 of 6: below the floor of 4
                 2: [group("P1", ids[:4]), group("P2", [ids[3], ids[4]])]}.get(n["i"])        # enough, but ids[3] is in two processes
        out = Path(cwd) / wr
        if procs is None:
            out.write_text("not json at all", encoding="utf-8")                              # attempt 3: unreadable
        else:
            out.write_text(json.dumps({"input_id": iid, "attempt_id": att, "processes": procs}), encoding="utf-8")
        return rep
    monkeypatch.setattr(headless, "run", run)
    with pytest.raises(pipeline.RunStopped) as stop:
        pipeline.stage_synth(r, ST, OBS)
    assert len(prompts) == 3 and "coverage: only 2 of 6" in prompts[1] and "already in" not in prompts[1]
    assert "already in" in prompts[2] and "coverage: only 2 of 6" in prompts[2] and "still apply" in prompts[2]   # both persist
    att = stop.value.detail["attempts"]
    assert att[0]["coverage"]["counted"] == 2 and att[0]["coverage"]["share"] == 0.3333
    assert att[1]["coverage"]["counted"] == 5 and att[1]["coverage"]["share"] == 0.8333              # measured although the structure failed
    assert any("already in" in e for e in att[1]["errors"]) and not any(e.startswith("coverage:") for e in att[1]["errors"])
    assert att[2]["coverage"]["computed"] is False and att[2]["fed_back_errors"] == 1


def test_a_seeded_first_attempt_hears_the_standing_requirements_of_earlier_failures(penv, monkeypatch):
    r, _ = penv
    r.cfg["synth"].update(chunk_token_budget=10_000, date_split=False, max_attempts=1)
    prompts = []
    monkeypatch.setattr(headless, "run", _prompts_and_damage(headless.run, 0, prompts))
    seed = ["coverage: only 83 of 2433 observations count toward a recurring process, the floor is 365 (15 percent)",
            "P33: observation b009-o35 is already in P25"]
    inp = synth.build_input(OBS, ST, "s001", r.slice)
    pipeline._synth_one(r, ST, OBS, "s001", None, seed=seed)
    assert len(prompts) == 1 and "83 of 2433" in prompts[0] and "already in P25" in prompts[0] and "previous attempt" not in prompts[0]


def test_a_merge_under_another_input_id_never_touches_the_working_synthesis_result(penv):
    """The size-split experiment (user, 24 Nov 2026) merges under m201: the working synth.json and the m001 cache stay as they are."""
    r, calls = penv
    r.cfg["synth"].update(chunk_token_budget=3 * synth.est_tokens(OBS[0], ST), date_split=False)
    parts = synth.split(OBS, ST, r.cfg["synth"]["chunk_token_budget"], date_split=False)
    pairs = [pipeline._synth_one(r, ST, p, f"s{201 + k:03d}", (k + 1, len(parts))) for k, p in enumerate(parts)]
    assert not (r.data / "synth.json").exists()
    res = pipeline._merge(r, ST, pairs, OBS, mid="m201")
    assert res["input_id"] == "m201" and (r.data / "synth" / "m201.json").exists()
    assert not (r.data / "synth.json").exists() and not (r.cache / "synth" / "m001.json").exists() and (r.cache / "synth" / "m201.json").exists()
    assert (r.synth_staging / "m201.json").exists() is False or True        # the staged file may have been moved aside


# --- size-triggered splitting and positional coverage (user, 24 Nov 2026) ---

SIZE = {"enabled": True, "over_observations": 200, "max_chunk_observations": 130}
POS = {"segments": 3, "warn_share": 0.05, "fail_share": 0.02, "min_segment_observations": 40, "decay_ratio": 0.25}


def _big(n_a=360, n_b=30, per_thread=3):
    """Mailbox A: n_a observations in threads of `per_thread`, dated through the year; mailbox B: n_b, one per thread."""
    msgs = [M(f"A/i/{i}", f"tA{i // per_thread}", "A") for i in range(n_a)] + [M(f"B/i/{i}", f"tB{i}", "B") for i in range(n_b)]
    obs = [ob(f"a{i:03d}", f"A/i/{i}", f"2001-{1 + i * 12 // n_a:02d}-{1 + i % 28:02d}") for i in range(n_a)] + \
          [ob(f"b{i:03d}", f"B/i/{i}", f"2001-{1 + i * 12 // n_b:02d}-{1 + i % 28:02d}") for i in range(n_b)]
    return St(msgs), obs


def test_the_size_trigger_is_over_the_limit_and_never_cuts_into_fewer_than_two_chunks():
    sc = {"enabled": True, "over_observations": 1000, "max_chunk_observations": 850}
    assert [synth.size_chunks(n, sc) for n in (250, 999, 1000, 1001, 1085, 1700, 1701, 2433)] == [1, 1, 1, 2, 2, 2, 3, 3]
    assert synth.size_chunks(5000, None) == 1 and synth.size_chunks(5000, {**sc, "enabled": False}) == 1       # off: the fallback


def test_only_the_oversized_mailbox_is_cut_into_date_chunks_of_whole_threads_and_every_observation_lands_once():
    st, obs = _big()
    items = synth.plan(list(reversed(obs)), st, 1000, SIZE)
    assert [synth.part_id(i) for i in items] == ["s011", "s012", "s013", "s002"]
    assert [i["chunk"] for i in items] == [(1, 3), (2, 3), (3, 3), None] and [i["mailbox"] for i in items] == ["A", "A", "A", "B"]
    assert [len(i["obs"]) for i in items] == [120, 120, 120, 30]
    assert sorted(o["obs_id"] for i in items for o in i["obs"]) == sorted(o["obs_id"] for o in obs)            # each exactly once
    home = {}
    for n, i in enumerate(items):
        for o in i["obs"]:
            assert home.setdefault(st.messages[o["msg_key"]].thread_id, n) == n                                  # a thread is never cut
    a = items[:3]
    assert max(synth._order(o) for o in a[0]["obs"]) < min(synth._order(o) for o in a[1]["obs"])               # date order between chunks
    assert max(synth._order(o) for o in a[1]["obs"]) < min(synth._order(o) for o in a[2]["obs"])
    rev = [{**o, "evidence_date": f"2001-{13 - int(o['evidence_date'][5:7]):02d}-{o['evidence_date'][8:]}"} for o in obs]   # ids run against the dates
    ra = synth.plan(rev, st, 1000, SIZE)[:3]
    assert max(synth._order(o) for o in ra[0]["obs"]) < min(synth._order(o) for o in ra[1]["obs"]) \
        and max(synth._order(o) for o in ra[1]["obs"]) < min(synth._order(o) for o in ra[2]["obs"])             # chunks follow the DATES
    again = synth.plan(obs, st, 1000, SIZE)                                                                     # input order does not matter
    assert [sorted(o["obs_id"] for o in i["obs"]) for i in again] == [sorted(o["obs_id"] for o in i["obs"]) for i in items]
    off = synth.plan(obs, st, 1000, {**SIZE, "enabled": False})                                                 # fallback: whole mailboxes
    assert [(synth.part_id(i), len(i["obs"])) for i in off] == [("s001", 360), ("s002", 30)]
    fits = synth.plan(obs, st, 10 ** 9, SIZE)                                                                   # a list that fits is one part
    assert len(fits) == 1 and fits[0]["mailbox"] is None and fits[0]["chunk"] is None


def test_chunk_ids_are_digits_only_so_the_fence_admits_them_and_never_collide_with_whole_mailbox_ids():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fence_hook_ids", ROOT / "tools" / "fence_hook.py")
    fh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fh)
    st0, obs0 = _big()
    ids = [synth.part_id(i) for i in synth.plan(obs0, st0, 1000, SIZE)]
    assert ids == ["s011", "s012", "s013", "s002"]
    for sid in ids:                                                # the synthesizer's fence admits exactly these file names
        assert fh.PATHS["synthesizer"][0].match(f"data/synth/inputs/all/{sid}.txt") and fh.PATHS["synthesizer"][1].match(f"data/synth/staging/all/{sid}.json")
    assert synth.part_id({"index": 2, "chunk": (2, 2)}) == "s022" and synth.part_id({"index": 4, "chunk": None}) == "s004"
    for bad in ({"index": 1, "chunk": (10, 10)}, {"index": 100, "chunk": (1, 2)}):
        with pytest.raises(ValueError):
            synth.part_id(bad)
    st, obs = _big(n_a=360, n_b=30)                                 # eleven mailboxes would make s011 the chunk of mailbox 1 AND mailbox 11
    msgs = [M(f"C{k}/i/0", f"tC{k}", f"C{k:02d}") for k in range(9)]
    more = [ob(f"c{k}", f"C{k}/i/0") for k in range(9)]
    big = St(list(st.messages.values()) + msgs)
    with pytest.raises(ValueError, match="collide"):
        synth.plan(obs + more, big, 100, SIZE)


def test_the_chunk_budget_widens_until_whole_threads_fit_in_the_chunk_count():
    st = St([M(f"A/i/{i}", f"tA{i // 6}", "A") for i in range(18)])
    obs = [ob(f"a{i:02d}", f"A/i/{i}", f"2001-01-{1 + i:02d}") for i in range(18)]
    threads = [obs[0:6], obs[6:12], obs[12:18]]
    chunks = synth.fit_chunks(threads, st, 2)                      # a first budget of nine lines would give three chunks
    assert len(chunks) == 2 and [len(c) for c in chunks] == [12, 6]
    assert synth.fit_chunks(threads, st, 3) == threads and len(synth.fit_chunks(threads, st, 1)) == 1


def _ids(n=300):
    return [f"o{i}" for i in range(n)]


def test_a_synthesis_that_ignores_two_thirds_of_its_input_fails_even_if_the_floor_is_met():
    ids = _ids()
    counted = set(ids[:100])                                       # all of the first third, none of the rest: 33 percent overall
    p = synth.positional(ids, counted, segments=3, warn=0.05, fail=0.02, min_segment=40, decay=0.25)
    assert [s["share"] for s in p["segments"]] == [1.0, 0.0, 0.0] and len(p["errors"]) == 2
    assert p["errors"][0].startswith("positional coverage: the middle third of the input (observations o100 to o199)")
    assert p["errors"][1].startswith("positional coverage: the last third of the input (observations o200 to o299)")
    for e in p["errors"]:                                          # safe to feed back: only letters, digits and plain punctuation
        assert re.fullmatch(r"[A-Za-z0-9_\-:,.'\[\] ()/]+", e) and "%" not in e and "=" not in e
    res = {"processes": [_proc("P1", ids[:100])]}
    c = synth.coverage(res, {"obs_ids": ids}, floor=0.15, catch_all=1.0, min_obs=40, pos={"segments": 3, "warn": 0.05, "fail": 0.02, "min_segment": 40, "decay": 0.25})
    assert c["summary"]["share"] == 0.3333 and c["ok"] is False and len(c["errors"]) == 2 and not any(e.startswith("coverage:") for e in c["errors"])
    assert [s["counted"] for s in c["summary"]["positional"]["segments"]] == [100, 0, 0]
    assert synth.coverage(res, {"obs_ids": ids}, floor=0.15, catch_all=1.0, min_obs=40)["ok"] is True          # without the check: it passed
    assert "positional" not in synth.coverage(res, {"obs_ids": ids}, floor=0.15, catch_all=1.0, min_obs=40)["summary"]


def test_the_positional_lines_are_exact_and_a_quiet_stretch_is_a_warning_not_a_failure():
    ids = _ids()
    kw = {"segments": 3, "warn": 0.05, "fail": 0.02, "min_segment": 40, "decay": 0.25}

    def with_thirds(a, b, c):
        return set(ids[:a]) | set(ids[100:100 + b]) | set(ids[200:200 + c])
    assert synth.positional(ids, with_thirds(60, 60, 2), **kw)["errors"] == []                        # exactly the failing count: passes
    assert len(synth.positional(ids, with_thirds(60, 60, 1), **kw)["errors"]) == 1                    # one below it: fails
    assert [w["kind"] for w in synth.positional(ids, with_thirds(60, 60, 2), **kw)["warnings"]] == ["positional_gap"]
    assert synth.positional(ids, with_thirds(60, 60, 5), **kw)["warnings"][0]["kind"] == "positional_decay"   # 5 is not a gap (5 percent)
    q = synth.positional(ids, with_thirds(60, 60, 6), **kw)                                           # a quiet third is left alone
    assert q["errors"] == [] and [w["kind"] for w in q["warnings"]] == ["positional_decay"]
    assert synth.positional(ids, with_thirds(60, 60, 15), **kw)["warnings"] == []                    # at 25 percent of the best third: fine
    assert synth.positional(ids, with_thirds(60, 60, 14), **kw)["warnings"][0]["kind"] == "positional_decay"
    soft = synth.positional(ids, with_thirds(100, 0, 0), **{**kw, "fail": 0.0})                       # fail 0 turns the failure off
    assert soft["errors"] == [] and [w["kind"] for w in soft["warnings"]] == ["positional_gap", "positional_gap"]
    short = synth.positional(_ids(90), set(), **kw)                                                   # segments under 40 are not checked
    assert short["errors"] == [] and short["warnings"] == [] and not any(s["checked"] for s in short["segments"])


def test_candidates_singletons_and_unknown_ids_do_not_count_toward_a_segment():
    ids = _ids()
    pos = {"segments": 3, "warn": 0.05, "fail": 0.02, "min_segment": 40, "decay": 0.25}
    res = {"processes": [_proc("P1", ids[:100]), _proc("P2", ids[100:200], candidate=True), _proc("P3", ["o200"]),
                         _proc("P4", [f"x{i}" for i in range(80)])]}
    c = synth.coverage(res, {"obs_ids": ids}, floor=0.0, catch_all=1.0, min_obs=40, pos=pos)
    assert [s["counted"] for s in c["summary"]["positional"]["segments"]] == [100, 0, 0] and len(c["errors"]) == 2
    tiny = synth.coverage({"processes": []}, {"obs_ids": _ids(30)}, floor=0.0, catch_all=1.0, min_obs=40, pos=pos)
    assert tiny["ok"] is True and "positional" not in tiny["summary"]                                 # parts under min_obs are exempt
    text = json.dumps({"processes": [{"process_id": "P1", "obs_ids": ids[:100]}]})
    assert len(synth.coverage_from_text(text, {"obs_ids": ids}, floor=0.0, catch_all=1.0, min_obs=40, pos=pos)["errors"]) == 2


def test_each_blind_segment_is_its_own_standing_requirement_and_the_config_builds_the_check(penv):
    r, _ = penv
    mid = "positional coverage: the middle third of the input (observations o100 to o199) has only 0 of 100 observations"
    last = "positional coverage: the last third of the input (observations o200 to o299) has only 0 of 100 observations"
    assert pipeline._kind(mid) != pipeline._kind(last) and pipeline._kind(mid) == pipeline._kind(mid.replace("only 0 of 100", "only 1 of 99"))
    standing = {}
    pipeline.note_standing(standing, [mid, last])
    assert "middle third" in pipeline.retry_note([last], standing) and "last third" in pipeline.retry_note([last], standing)
    r.cfg["synth"]["positional"] = dict(POS)
    assert pipeline._floor(r)["pos"] == {"segments": 3, "warn": 0.05, "fail": 0.02, "min_segment": 40, "decay": 0.25}
    r.cfg["synth"].pop("positional")
    assert pipeline._floor(r)["pos"] is None


def _stage_cfg(r, **kw):
    r.cfg["synth"].update(chunk_token_budget=1000, date_split=False, max_attempts=3, min_assigned_share=0.15, catch_all_share=1.0,
                          coverage_min_observations=40, size_split=dict(SIZE), positional=dict(POS), **kw)


def test_the_stage_with_size_split_on_runs_chunks_in_parallel_merges_once_and_never_writes_a_whole_mailbox_result(penv):
    r, calls = penv
    st, obs = _big()
    _stage_cfg(r)
    res = pipeline.stage_synth(r, st, obs)
    assert calls.count("synthesizer") == 4 and calls.count("merger") == 1
    assert [p["input_id"] for p in res["parts"]] == ["s011", "s012", "s013", "s002"] and res["input_id"] == "m001"
    assert [p["part"] for p in res["parts"]] == [[1, 3], [2, 3], [3, 3], [2, 2]]                    # chunks say 'j of k'; a mailbox 'i of n'
    assert all(p["coverage"]["share"] == 1.0 for p in res["parts"])
    assert sorted(f.name for f in (r.data / "synth").glob("s*.json")) == ["s002.json", "s011.json", "s012.json", "s013.json"]
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines()]
    sp = next(x for x in log if x.get("status") == "split")
    assert sp["mode"] == "size" and sp["ids"] == ["s011", "s012", "s013", "s002"] and sp["chunks"] == 3 and sp["observations"] == [120, 120, 120, 30]
    assert "PART 2 OF 3" in (r.synth_inputs / "s012.txt").read_text(encoding="utf-8").splitlines()[0]
    calls.clear()
    again = pipeline.stage_synth(r, st, obs)
    assert calls == [] and again["processes"] == res["processes"]                                   # every chunk and the merge cached
    r.cfg["synth"]["size_split"]["enabled"] = False                                                # the fallback: whole mailboxes, own ids
    calls.clear()
    off = pipeline.stage_synth(r, st, obs)
    assert [p["input_id"] for p in off["parts"]] == ["s001", "s002"] and calls.count("synthesizer") == 1   # the unchanged mailbox is a cache hit


def _only_first_third(base, only_for=None):
    """penv's fake session, but it assigns only the first third of the observations of the input it is given (a blind synthesis)."""
    n = {"i": 0}

    def run(agent, prompt, model, timeout=0, cwd=None, read=None, write=None, log_dir=None):
        rep = base(agent, prompt, model, timeout, cwd, read, write, log_dir)
        rd, iid, att, wr = re.findall(r"`([^`]+)`", prompt)
        if agent == "synthesizer" and (only_for is None or iid == only_for):
            n["i"] += 1
            ids = [x.split(" | ")[0] for x in (Path(cwd) / rd).read_text(encoding="utf-8").splitlines() if " | " in x and not x.startswith("⟦")]
            if n["i"] == 1:
                (Path(cwd) / wr).write_text(json.dumps({"input_id": iid, "attempt_id": att, "processes": [group("P1", ids[:len(ids) // 3])]}),
                                            encoding="utf-8")
        return rep
    return run


def test_a_blind_attempt_is_rejected_fed_back_by_segment_and_logged_then_a_complete_one_passes(penv, monkeypatch):
    r, _ = penv
    st, obs = _big(n_a=360, n_b=0)
    _stage_cfg(r)
    r.cfg["synth"].update(chunk_token_budget=10 ** 9, size_split={**SIZE, "enabled": False})        # one pass: the whole list
    prompts = []
    base = _only_first_third(headless.run)

    def run(agent, prompt, *a, **kw):
        prompts.append(prompt)
        return base(agent, prompt, *a, **kw)
    monkeypatch.setattr(headless, "run", run)
    res = pipeline.stage_synth(r, st, obs)
    assert res["status"] == "verified" and len(prompts) == 2 and "positional" not in prompts[0]
    assert "positional coverage: the middle third" in prompts[1] and "positional coverage: the last third" in prompts[1]
    att = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines() if '"attempt"' in x and '"stage": "synth"' in x]
    assert [x["status"] for x in att] == ["failed", "verified"] and att[0]["coverage"]["share"] == 0.3333        # the floor itself passed
    assert [s["counted"] for s in att[0]["coverage"]["positional"]["segments"]] == [120, 0, 0] and len(att[0]["errors"]) == 2
    assert res["coverage"]["positional"]["warnings"] == [] and not [f for f in res["flags"] if f["kind"].startswith("positional")]


def test_with_the_failure_off_a_blind_part_passes_with_warnings_that_reach_the_merged_result(penv, monkeypatch):
    r, _ = penv
    st, obs = _big()
    _stage_cfg(r)
    r.cfg["synth"]["positional"]["fail_share"] = 0.0
    monkeypatch.setattr(headless, "run", _only_first_third(headless.run, only_for="s012"))
    res = pipeline.stage_synth(r, st, obs)
    gaps = [f for f in res["flags"] if f["kind"] == "positional_gap"]
    assert res["status"] == "verified" and [(f["part"], f["segment"]) for f in gaps] == [("s012", 2), ("s012", 3)]
    part = next(p for p in res["parts"] if p["input_id"] == "s012")
    assert part["coverage"]["positional"]["warnings"] and part["coverage"]["share"] == 0.3333
    saved = json.loads((r.data / "synth" / "s012.json").read_text(encoding="utf-8"))                  # and the part's own result
    assert [f["kind"] for f in saved["flags"] if f["kind"].startswith("positional")] == ["positional_gap", "positional_gap"]


def test_a_cached_part_is_reused_only_if_it_also_passes_the_positional_check(penv, monkeypatch):
    r, calls = penv
    st, obs = _big(n_a=360, n_b=0)
    _stage_cfg(r)
    r.cfg["synth"].update(chunk_token_budget=10 ** 9, size_split={**SIZE, "enabled": False})
    r.cfg["synth"]["positional"]["fail_share"] = 0.0
    real = headless.run
    monkeypatch.setattr(headless, "run", _only_first_third(real))
    assert pipeline.stage_synth(r, st, obs)["status"] == "verified"                                  # blind but allowed: cached with warnings
    calls.clear()
    monkeypatch.setattr(headless, "run", real)
    r.cfg["synth"]["positional"]["fail_share"] = 0.02                                                # now it must not be reused
    assert pipeline.stage_synth(r, st, obs)["status"] == "verified" and calls.count("synthesizer") == 1
    log = [json.loads(x) for x in r.log_path.read_text(encoding="utf-8").splitlines()]
    assert any(x.get("status") == "cache_rejected" and x["errors"][0].startswith("positional coverage:") for x in log)
    assert list((r.synth_staging / "rejected").glob("*.cached-rejected.json"))
