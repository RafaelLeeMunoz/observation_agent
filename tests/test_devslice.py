"""The dev slice: whole threads, fixed rules, the hard cases present, the benchmark inside it."""
import json
from pathlib import Path

import pytest

from src import devslice

pytestmark = pytest.mark.corpus
ROOT = Path(__file__).resolve().parent.parent


def test_selection_is_deterministic_and_matches_the_committed_file(corpus_result):
    a = devslice.select(corpus_result)
    assert a == devslice.select(corpus_result)
    committed = json.loads((ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(a))          # regenerate with `python -m src.devslice`


def test_slice_holds_whole_threads_and_covers_the_hard_cases(corpus_result):
    s = devslice.select(corpus_result)
    by_tid = {t.thread_id: t.keys for t in corpus_result.threads}
    for t in s["threads"]:
        assert t["keys"] == by_tid[t["thread_id"]]
    keys = set(s["keys"])
    assert len(keys) == len(s["keys"]) == s["messages"] and 180 <= s["messages"] <= 230
    assert {"farmer-d/logistics/3221", "giron-d/sent/795", "williams-w3/inbox/142",
            "lokay-m/inbox/74", "lokay-m/inbox/50"} <= keys
    reasons = {t["reason"].split(" (")[0].split(":")[0] for t in s["threads"]}
    for r in ("calpine daily notice", "giant table message", "quoted-printable encoded message",
              "clock-shifted Lotus mail", "negative", "ordinary thread", "date problem", "logged reading"):
        assert r in reasons, r
    assert {k.split("/")[0] for k in keys} == {"farmer-d", "giron-d", "lokay-m", "williams-w3"}
    assert all(corpus_result.messages[k].duplicate_of is None for k in keys)


def test_holdout_is_outside_the_slice_and_spread(corpus_result):
    s = devslice.select(corpus_result)
    hold = s["holdout_keys"]
    assert not set(hold) & set(s["keys"])                     # never seen during development
    assert 25 <= len(hold) <= 35
    assert {k.split("/")[0] for k in hold} == {"farmer-d", "giron-d", "lokay-m", "williams-w3"}
    by_tid = {t.thread_id: t.keys for t in corpus_result.threads}
    assert [k for t in s["holdout_threads"] for k in by_tid[t]] == hold     # whole threads


def test_blind_set_is_whole_benchmark_threads(corpus_result):
    s = devslice.select(corpus_result)
    blind = s["blind_keys"]
    assert set(blind) <= set(s["benchmark_keys"]) and 8 <= len(blind) <= 12
    for t in s["threads"]:
        inside = [k in blind for k in t["keys"]]
        assert all(inside) or not any(inside)


def test_benchmark_is_whole_threads_inside_the_slice(corpus_result):
    s = devslice.select(corpus_result)
    bench = s["benchmark_keys"]
    assert set(bench) <= set(s["keys"]) and 35 <= len(bench) <= 45
    for t in s["threads"]:
        inside = [k in bench for k in t["keys"]]
        assert all(inside) or not any(inside)
