"""The milestone analysis tools (user, 24 Nov 2026): their pure functions, offline, no model, no data folder."""
import importlib.util
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qb, fv, rp, sr = load("quality_baseline"), load("finance_validation"), load("repeatability"), load("synth_rerun")


def test_tokens_drop_stop_words_and_plurals_and_jaccard_is_symmetric():
    assert qb.toks("Moving the deals into bankruptcy books") == qb.toks("move deal into bankruptcy book") or \
        qb.toks("Moving the deals into bankruptcy books") >= {"deal", "bankruptcy", "book"}
    assert qb.jac(frozenset("abc"), frozenset("bcd")) == 0.5 and qb.jac(frozenset(), frozenset("a")) == 0.0
    assert qb.jac(qb.toks("meter flow deal"), qb.toks("deal flow meter")) == 1.0


def test_hint_clusters_link_similar_hints_and_leave_unrelated_ones_apart():
    items = {"a": qb.toks("daily gas nomination confirmation"), "b": qb.toks("gas nomination confirmation daily"),
             "c": qb.toks("daily gas nomination changes confirmation"), "d": qb.toks("performance review accomplishments list"),
             "e": qb.toks("performance review accomplishments")}
    groups = [sorted(g) for g in qb.clusters(items, 0.5)]
    assert ["a", "b", "c"] in groups and ["d", "e"] in groups and len(groups) == 2


def test_the_union_of_date_windows_is_counted_once_and_money_rounds_half_up():
    d = datetime.fromisoformat
    days = fv.union_days([(d("2001-01-01T00:00:00"), d("2001-01-31T00:00:00")), (d("2001-01-15T00:00:00"), d("2001-02-14T00:00:00")),
                          (d("2001-06-01T00:00:00"), d("2001-06-11T00:00:00"))])
    assert days == Decimal(44) + Decimal(10)                                  # Jan 1 to Feb 14 once, plus ten days in June
    assert fv.money(Decimal("1152.725")) == "$1,152.73" and fv.money(Decimal("0.004")) == "$0.00"
    assert fv.union_days([]) == Decimal(0)


def _res(groups):
    return {"processes": [{"process_id": f"P{i}", "name": f"proc {i}", "obs_ids": g} for i, g in enumerate(groups, 1)]}


def test_a_rerun_that_groups_the_same_way_agrees_and_one_that_regroups_is_measured():
    same = sr.compare(_res([["a", "b", "c"], ["d", "e"]]), _res([["d", "e"], ["a", "b", "c"]]))
    assert same["counted_jaccard"] == 1.0 and same["co_assignment_f1"] == 1.0 and same["matched_processes"] == 2
    moved = sr.compare(_res([["a", "b", "c"], ["d", "e", "f"]]), _res([["a", "b"], ["c", "d", "e", "f"]]))
    assert 0 < moved["co_assignment_f1"] < 1 and moved["counted_run1"] == moved["counted_run2"] == 6
    fewer = sr.compare(_res([["a", "b", "c"], ["d", "e"]]), _res([["a", "b", "c"]]))
    assert fewer["counted_jaccard"] == 0.6 and fewer["matched_processes"] == 1 and fewer["names_unmatched_run1"] == [(2, "proc 2")]
    cand = sr.compare(_res([["a", "b"]]), {"processes": [{"process_id": "P1", "name": "x", "obs_ids": ["a", "b"], "candidate": True}]})
    assert cand["counted_run2"] == 0                                           # a candidate is not a recurring process


def test_a_cold_copy_is_verified_against_the_working_tree_and_must_hold_no_run_state(tmp_path):
    import os
    import subprocess
    if os.name != "nt" or not (ROOT / ".venv").exists():
        pytest.skip("the copy tool junctions the Windows virtual environment")
    cc = load("make_cold_copy")
    dest = tmp_path / "copy"
    assert cc.main([str(dest)]) == 0 and cc.verify(dest) == [] and cc.main(["--verify", str(dest)]) == 0
    assert not (dest / "data").exists() and not (dest / "output").exists() and not (dest / "private").exists()
    doc = dest / "docs" / "RUNNING.md"
    doc.write_bytes(doc.read_bytes() + b"\nedited after the copy\n")                                   # documentation may change after the copy
    assert cc.verify(dest) == []
    victim = dest / "src" / "score.py"
    victim.write_bytes(victim.read_bytes() + b"\n# changed\n")
    (dest / "data").mkdir()
    diffs = cc.verify(dest)
    assert "differs: src/score.py" in diffs and any("not cold" in d for d in diffs) and cc.main(["--verify", str(dest)]) == 1
    subprocess.run(["cmd", "/c", "rmdir", str(dest / ".venv")], check=True, capture_output=True)       # remove the junction, never its target


def test_the_repeatability_overlap_helper_handles_empty_sets():
    assert rp.jac(set(), set()) == 1.0 and rp.jac({1, 2}, {2, 3}) == 1 / 3
