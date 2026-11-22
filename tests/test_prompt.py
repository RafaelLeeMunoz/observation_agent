"""The extractor prompt must not leak the test: no benchmark or holdout message, label or text in it."""
import json
import re
from pathlib import Path

from src import rawio
from src.textutil import norm_ws

ROOT = Path(__file__).resolve().parent.parent
PROMPT = norm_ws((ROOT / ".claude" / "agents" / "extractor.md").read_text(encoding="utf-8")).lower()


def test_the_extractor_prompt_holds_no_benchmark_or_holdout_text():
    sl = json.loads((ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))
    labels = json.loads((ROOT / "eval" / "benchmark" / "labels.json").read_text(encoding="utf-8"))
    keys = sl["benchmark_keys"] + sl["holdout_keys"]
    assert not [k for k in keys if k.lower() in PROMPT]
    quotes = [q for m in labels["messages"].values() for a in m["activities"]
              for q in [a["quote"], *a.get("other_quotes", [])]]
    assert not [q for q in quotes if norm_ws(q).lower() in PROMPT]
    raws = [rawio.read_raw(ROOT / "corpus" / f"{k}.txt") for k in keys]          # raw files, no database
    bodies = [r.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in r else r.split("\n\n", 1)[1] for r in raws]
    pieces = {norm_ws(line).lower() for b in bodies for line in b.splitlines()}           # whole lines ...
    pieces |= {s.lower() for b in bodies for s in re.split(r"(?<=[.?!])\s+", norm_ws(b))}  # ... and sentences
    pieces = {p for p in pieces if len(p) >= 30}
    assert len(pieces) > 500                                                    # the check really ran
    assert not [p for p in pieces if p in PROMPT]
