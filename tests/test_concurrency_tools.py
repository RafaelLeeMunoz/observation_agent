"""M4 step 2b helpers, offline: the batch sets, the session report and the synthesis comparison (no model)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("concurrency_2b", ROOT / "tools" / "concurrency_2b.py")
c2b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c2b)


def test_the_sets_are_disjoint_of_the_right_sizes_spread_over_the_corpus_and_deterministic():
    ids = [f"b{i:03d}" for i in range(1, 82)]
    s = c2b.pick_sets(ids)
    assert {k: len(v) for k, v in s.items()} == {"A": 8, "B": 12, "C": 16}
    flat = [x for v in s.values() for x in v]
    assert len(flat) == len(set(flat)) == 36 and set(flat) <= set(ids)
    for v in s.values():                                       # every set reaches both ends of the id order
        assert min(v) <= "b010" and max(v) >= "b070"
    assert c2b.pick_sets(list(reversed(ids))) == s


def test_the_session_report_compares_measured_times_with_the_model_and_measures_the_start_spread():
    sessions = [{"stage": "extract", "name": "b001", "wall_seconds": 110.0, "kind": "ok", "started_at_utc": "2026-11-23T10:00:00Z"},
                {"stage": "extract", "name": "b002", "wall_seconds": 60.0, "kind": "ok", "started_at_utc": "2026-11-23T10:00:03Z"},
                {"stage": "extract", "name": "b003", "wall_seconds": 50.0, "kind": "rate_limited", "started_at_utc": "2026-11-23T10:01:00Z"},
                {"stage": "ground", "name": "b001", "wall_seconds": 20.0, "kind": "ok", "started_at_utc": "2026-11-23T10:02:00Z"}]
    rep = c2b.session_report(sessions, {"b001": 20, "b002": 10, "b003": 10}, {"a": 10.0, "b": 5.0}, workers=2)
    ex = rep["extract"]
    assert ex["sessions"] == 3 and ex["kinds"] == {"ok": 2, "rate_limited": 1}
    assert ex["first_wave_start_spread_s"] == 3.0 and ex["rows"][0]["predicted_s"] == 110.0
    assert ex["measured_over_predicted"] == {"mean": round((1.0 + 1.0 + 50 / 60) / 3, 2), "max": 1.0}
    assert rep["ground"]["sessions"] == 1


class M:
    def __init__(self, key, thread, mailbox="farmer-d"):
        self.key, self.thread_id, self.mailbox = key, thread, mailbox


class St:
    def __init__(self, msgs):
        self.messages = {m.key: m for m in msgs}


def o(oid, key):
    return {"obs_id": oid, "msg_key": key, "start": 0, "end": 30, "quote_raw": "x" * 30, "evidence_date": "2001-01-01",
            "published_evidence": [], "stall_signals": []}


def test_the_comparison_reports_identity_events_evidence_and_where_a_lost_process_went():
    st = St([M(f"f/i/{i}", f"t{i}") for i in range(1, 7)])
    obs = [o(f"o{i}", f"f/i/{i}") for i in range(1, 7)]
    whole = {"processes": [{"process_id": "P1", "name": "Volumes", "obs_ids": ["o1", "o2", "o3"]},
                           {"process_id": "P2", "name": "Tickets", "obs_ids": ["o4", "o5"]}],
             "assignment": {"o1": "P1", "o2": "P1", "o3": "P1", "o4": "P2", "o5": "P2"}}
    split = {"processes": [{"process_id": "P1", "name": "Volumes", "obs_ids": ["o1", "o2", "o3"]},
                           {"process_id": "P2", "name": "Other", "obs_ids": ["o5", "o6"]}],
             "assignment": {"o1": "P1", "o2": "P1", "o3": "P1", "o5": "P2", "o6": "P2"}}
    c = c2b.compare_processes(whole, split, obs, st, {})
    assert c["recovered_at_half_overlap"] == 1 and c["split_only"] == ["P2"]
    (lost,) = c["lost"]
    assert lost["whole"] == "P2" and lost["observations_went"] == {"unassigned": 1, "P2": 1} and lost["events"] == 2
    assert c["rows"][0]["events"] == 3 and c["rows"][0]["split_events"] == 3 and c["rows"][0]["quotes"] == 3
    assert c["events_in_recurring_processes"] == {"whole": 5, "split": 5}
