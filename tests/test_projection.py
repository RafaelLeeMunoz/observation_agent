"""M4 2c: the runtime projection's pool simulation and fit (offline, no model)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("runtime_projection", ROOT / "tools" / "runtime_projection.py")
proj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proj)


def test_one_worker_does_every_job_in_turn_and_enough_workers_overlap_everything():
    ex, gr = [100.0, 60.0, 80.0], [10.0, 5.0, 7.0]
    assert proj.simulate_pool(ex, gr, 1)["makespan_s"] == sum(ex) + sum(gr)
    assert proj.simulate_pool(ex, gr, 3)["makespan_s"] == 110.0                    # the longest extraction + its own grounding
    assert proj.simulate_pool(ex, gr, 50)["makespan_s"] == 110.0


def test_more_workers_never_take_longer_and_grounding_is_served_before_a_new_extraction():
    ex, gr = [90.0] * 12, [20.0] * 12
    spans = [proj.simulate_pool(ex, gr, w)["makespan_s"] for w in (1, 2, 4, 8, 12, 16)]
    assert spans == sorted(spans, reverse=True)
    # 2 workers, three 100 s extractions with 10 s groundings: both groundings run at 100-110 BEFORE extraction 3, which
    # therefore starts at 110 and ends at 210; its own grounding ends at 220
    out = proj.simulate_pool([100.0, 100.0, 100.0], [10.0, 10.0, 10.0], 2)
    assert out["makespan_s"] == 220.0 and out["last_extraction_end_s"] == 210.0


def test_the_fit_is_total_seconds_per_total_message_with_a_floor_and_the_residual_sd():
    r, floor, sd = proj.fit([(10, 60.0), (20, 120.0), (40, 240.0)])
    assert (r, floor, round(sd, 9)) == (6.0, 60.0, 0.0)
    r, floor, sd = proj.fit([(40, 200.0), (40, 220.0)])                           # no spread in size: the mean rate, no slope
    assert (r, floor) == (5.25, 200.0) and round(sd, 6) == 14.142136
    ex, gr = proj.scenario_times([40, 5], (5.0, 100.0, 10.0), (0.5, 9.0, 2.0), "mean", (6.0, 0.7))
    assert ex == [200.0, 100.0] and gr == [20.0, 9.0]                              # a small batch is never faster than the floor
    ex, gr = proj.scenario_times([40], (5.0, 100.0, 10.0), (0.5, 9.0, 2.0), "slow", (6.0, 0.7))
    assert ex == [210.0] and gr == [22.0]
    ex, gr = proj.scenario_times([40], (5.0, 100.0, 10.0), (0.5, 9.0, 2.0), "worst", (6.0, 0.7))
    assert ex == [240.0] and gr == [28.0]


def test_each_batchs_grounding_end_is_reported_and_a_mailbox_is_done_when_its_last_batch_is():
    out = proj.simulate_pool([100.0, 100.0, 100.0], [10.0, 10.0, 10.0], 2)
    assert out["ground_end_s"] == [110.0, 110.0, 220.0]
    done = proj.mailbox_done(out["ground_end_s"], [{"a"}, {"a", "b"}, {"b"}])
    assert done == {"a": 110.0, "b": 220.0}


def test_synthesis_overlaps_only_for_a_mailbox_that_finishes_before_the_others():
    done, synth = {"a": 100.0, "b": 300.0}, {"a": 150.0, "b": 150.0}
    out = proj.finish_with_overlap(done, synth, makespan=300.0)
    assert out == {"synthesis_end_no_overlap_s": 450.0, "synthesis_end_with_overlap_s": 450.0, "saved_s": 0.0}   # b is the long pole
    done = {"a": 100.0, "b": 200.0}
    out = proj.finish_with_overlap(done, {"a": 150.0, "b": 60.0}, makespan=300.0)
    assert out["synthesis_end_with_overlap_s"] == 300.0 and out["saved_s"] == 150.0      # both end before the pool does


def test_the_mailbox_ordered_layout_keeps_every_message_and_puts_the_largest_mailbox_first():
    sizes, boxes = proj.mailbox_ordered_layout({"small": 45, "big": 100})
    assert sum(sizes) == 145 and sizes == [40, 40, 20, 40, 5] and boxes[0] == {"big"} and boxes[-1] == {"small"}


def test_a_further_set_takes_only_unused_batches_spread_over_the_whole_corpus_and_changes_no_earlier_set():
    spec = importlib.util.spec_from_file_location("concurrency_2b", ROOT / "tools" / "concurrency_2b.py")
    c2b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c2b)
    ids = [f"b{i:03d}" for i in range(1, 82)]
    sets = c2b.pick_sets(ids)                                                      # A, B, C exactly as measured
    used = [i for v in sets.values() for i in v]
    d = c2b.pick_extra_set(ids, used, 24)
    assert len(d) == 24 and len(set(d)) == 24 and not set(d) & set(used) and d == sorted(d)
    assert d == c2b.pick_extra_set(ids, used, 24)                                  # deterministic
    assert d[0] in ids[:8] and d[-1] in ids[-8:]                                   # spans the corpus start to end
    assert c2b.pick_sets(ids) == sets                                              # the earlier sets are untouched
    try:
        c2b.pick_extra_set(ids, used, 46)
    except ValueError:
        pass
    else:
        raise AssertionError("more batches than are unused must be refused")
