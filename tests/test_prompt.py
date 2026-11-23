"""The extractor prompt must not leak the test: no benchmark or holdout message, label or text in it."""
import json

import pytest
import re
from pathlib import Path

from src import config, rawio, schema
from src.textutil import norm_ws

ROOT = Path(__file__).resolve().parent.parent
PROMPT_RAW = (ROOT / ".claude" / "agents" / "extractor.md").read_text(encoding="utf-8")
PROMPT = norm_ws(PROMPT_RAW).lower()


def test_the_prompts_sample_output_matches_the_schema():
    """The sample JSON in the prompt was updated by hand three times; it must stay a valid output."""
    blocks = re.findall(r"```json\n(.*?)```", PROMPT_RAW, re.S)
    assert len(blocks) == 1, "expected exactly one ```json sample in the extractor prompt"
    sample = json.loads(blocks[0])
    assert schema.validate(sample, schema.load(f"{config.load()['extract']['schema']}.schema.json")) == []
    assert sample["observations"] and sample["messages"]                       # both parts are shown
    obs_props = schema.load(f"{config.load()['extract']['schema']}.schema.json")["properties"]["observations"]["items"]["properties"]
    assert set(sample["observations"][0]) == set(obs_props)                     # every field, no stray one


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


# Four-word windows the prompt shares with the benchmark or dev slice that are ordinary English, reviewed
# 22 Nov 2026; anything else shared is a leak. Extend only after reading where the words come from.
GENERIC_4GRAMS = {"a list of the", "let me know when", "to the person who"}


def test_no_cue_phrase_in_the_prompt_comes_from_the_benchmark_or_dev_slice():
    """Short cue phrases slip past the 30-character check above: "the attachment keeps bouncing" (3221)
    sat in every prompt from v6 to v7g. No four-word window of the prompt may appear in a benchmark or
    dev-slice message (the holdout is not read here) unless it is listed as generic above."""
    words = lambda t: re.findall(r"[a-z0-9']+", t.lower())
    grams = lambda w: {" ".join(w[i:i + 4]) for i in range(len(w) - 3)}
    sl = json.loads((ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))
    keys = sorted(set(sl["keys"]) | set(sl["benchmark_keys"]))
    seen = set().union(*(grams(words(rawio.read_raw(ROOT / "corpus" / f"{k}.txt"))) for k in keys))
    assert len(seen) > 50000                                                    # the check really ran
    assert sorted(grams(words(PROMPT_RAW)) & seen - GENERIC_4GRAMS) == []


def test_the_prompt_states_the_audited_stall_rules_and_their_history_leads_there():
    """eval/benchmark/rules.json is the rule-change audit: the prompt must carry every current definition
    word for word, and replaying the history (baseline, then each change's after) must give that text."""
    rules = json.loads((ROOT / "eval" / "benchmark" / "rules.json").read_text(encoding="utf-8"))
    prompt = norm_ws(PROMPT_RAW)
    for sig, text in rules["signals"].items():
        assert f"`{sig}`: {text}" in prompt, sig
    now = {}
    for i, h in enumerate(rules["history"], 1):
        assert h["n"] == i and h["by"] == "user" and h["user_words"] and h["when"]
        if h["kind"] == "interpretation":
            continue
        if h["kind"] != "baseline":
            assert {s: now[s] for s in h["before"]} == h["before"], f"history entry {i}: 'before' is not the text as it stood"
        now.update(h["after"])
    assert now == rules["signals"]


@pytest.mark.parametrize("path", [".claude/agents/grounder.md", ".claude/agents/synthesizer.md",
                                  ".claude/agents/artifact-writer.md", "eval/prompts/extract-v8h.md"])
def test_the_other_agent_prompts_hold_no_benchmark_or_dev_slice_text(path):
    """The same leak rules for every other agent: no benchmark or holdout key, label quote, line or sentence of
    their text, and no 4-word window shared with a benchmark or dev-slice message beyond the reviewed generic
    ones (the holdout is not read for the 4-gram check)."""
    raw = (ROOT / path).read_text(encoding="utf-8")
    low = norm_ws(raw).lower()
    sl = json.loads((ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))
    labels = json.loads((ROOT / "eval" / "benchmark" / "labels.json").read_text(encoding="utf-8"))
    keys = sl["benchmark_keys"] + sl["holdout_keys"]
    assert not [k for k in keys if k.lower() in low]
    quotes = [q for m in labels["messages"].values() for a in m["activities"] for q in [a["quote"], *a.get("other_quotes", [])]]
    assert not [q for q in quotes if norm_ws(q).lower() in low]
    raws = [rawio.read_raw(ROOT / "corpus" / f"{k}.txt") for k in keys]
    bodies = [r.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in r else r.split("\n\n", 1)[1] for r in raws]
    pieces = {norm_ws(line).lower() for b in bodies for line in b.splitlines()}
    pieces |= {s.lower() for b in bodies for s in re.split(r"(?<=[.?!])\s+", norm_ws(b))}
    assert not [p for p in pieces if len(p) >= 30 and p in low]
    words = lambda t: re.findall(r"[a-z0-9']+", t.lower())
    grams = lambda w: {" ".join(w[i:i + 4]) for i in range(len(w) - 3)}
    dev = sorted(set(sl["keys"]) | set(sl["benchmark_keys"]))
    seen = set().union(*(grams(words(rawio.read_raw(ROOT / "corpus" / f"{k}.txt"))) for k in dev))
    assert sorted(grams(words(raw)) & seen - GENERIC_4GRAMS) == []


def test_the_active_extractor_prompt_is_the_committed_copy_of_the_configured_version():
    """Whichever design is selected (I: extract-v8c, the default; H: extract-v8h), the agent file must be exactly the
    committed copy named by config/settings.yaml, so the run records the prompt it really ran with."""
    version = config.load()["extract"]["prompt_version"]
    assert PROMPT_RAW == (ROOT / "eval" / "prompts" / f"{version}.md").read_text(encoding="utf-8")


def test_the_kept_h_prompt_is_the_clean_c_prompt_plus_one_evidence_section():
    """H + grounder is kept as the quality benchmark and backup (user, 23 Nov 2026). Its prompt must stay the clean-C
    prompt plus the evidence-last section, the schema name and the sample, so a switch changes only that. Both are
    compared as committed copies, so this holds whichever one is active."""
    import difflib
    h = (ROOT / "eval" / "prompts" / "extract-v8h.md").read_text(encoding="utf-8")
    c = (ROOT / "eval" / "prompts" / "extract-v8c.md").read_text(encoding="utf-8")
    section = h[h.index("## Evidence for each signal, last"):h.index("## Output")].splitlines()
    diff = list(difflib.ndiff(c.splitlines(), h.splitlines()))
    removed = [d[2:] for d in diff if d.startswith("- ")]
    added = [d[2:] for d in diff if d.startswith("+ ")]
    assert len(removed) == 2 and "schemas/extract-v6.schema.json" in removed[0] and removed[1].endswith('"limitations": ""}]}')
    outside = [a for a in added if a not in section]
    assert outside[0].replace("extract-v8h", "extract-v6") == removed[0]
    assert outside[1] == removed[1][:-1] + "," and outside[2] == ' "signal_evidence": []}'
    assert all("signal_evidence" in a or "obs_id" in a or not a.strip() or a.startswith("When an observation") for a in outside[3:])
    from src import schema
    sample = json.loads(re.findall(r"```json\n(.*?)```", h, re.S)[0])
    assert schema.validate(sample, schema.load("extract-v8h.schema.json")) == []
