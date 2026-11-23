"""Evidence-support benchmark: the passages that show each positive stall label, and claims that are NOT supported.

eval/benchmark/evidence_spans.json holds one entry per (activity, signal) that labels.json marks
positive. Each entry names its passages (verbatim quotes), whether they `supports` the signal for that
activity or leave it `uncertain` (with a note), and its declared `basis`:
  own_text               the activity's own labelled text shows the signal;
  same_message_context   it needs other text of the same message (e.g. a memo's opening line);
  cross_message_context  it needs a passage the batcher hides in this message because the original is
                         shown in another message of the thread (`in_message`), linked in the store.
Code fills in, per passage: the message, raw-byte offsets, region and block, the author (the quoted
block's author for quoted text), `scope` (activity / context / other_message), `visibility` (batch: shown
in this message's batch text; linked_original: shown only in the linked message's batch), `via_block`
(the hidden block in the activity's message whose original it is) and `shared_with`.

eval/benchmark/negative_claims.json holds plausible signals the labels do NOT carry, each with the
passage a model might cite: the reference for whether a grounder rejects unsupported claims.

A verified quote only proves the text exists; these files are the reference for SUPPORT. Neither is
ever shown to a model: the fence denies every Read outside the batch files (tests/test_fence.py).

Run:  .venv\\Scripts\\python -m src.evidence_bench resolve     (fill in the computed fields)
      .venv\\Scripts\\python -m src.evidence_bench check       (0 problems, or the list)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import bench, config, store, verify
from .textutil import norm_ws

EVIDENCE = bench.ROOT / "eval" / "benchmark" / "evidence_spans.json"
NEGATIVE = bench.ROOT / "eval" / "benchmark" / "negative_claims.json"
SUPPORT = ("supports", "uncertain")
BASIS = ("own_text", "same_message_context", "cross_message_context")
NEEDS = {"own_text": "activity", "same_message_context": "context", "cross_message_context": "other_message"}
COMPUTED = ("msg_key", "start", "end", "region", "block", "author", "author_named_in_label", "scope", "visibility",
            "via_block", "shared_with")


def _name(s: str) -> set[str]:
    """Comparable name tokens of an author as labels or headers write it (address or display name)."""
    s = re.sub(r"\(.*?\)", " ", s.lower()).split("@")[0]
    return {w for w in re.split(r"[^a-z]+", s) if len(w) > 1}


def _passage(sp: dict, key: str, act_spans: list, st: store.Store, vcfg: dict) -> str | None:
    """Fill one passage's computed fields; return a problem or None."""
    src = sp.get("in_message") or key
    if src not in st.messages:
        return f"in_message {src!r} is not a stored message"
    via = None
    if src != key:      # a hidden copy in this message must be linked, in the store, to the shown original
        via = next((b.index for b in st.messages[key].blocks if b.skip_reason == f"original_in_thread:{src}"), None)
        if via is None:
            return f"{key} has no hidden quoted block whose original is {src}: no stored link"
        if st.messages[src].thread_id != st.messages[key].thread_id:
            return f"{src} is not in the thread of {key}"
    chk = verify.check_quote(st.messages[src], sp["quote"], vcfg["min_quote_chars"])
    if not chk.get("ok"):
        return f"passage does not verify in {src} ({chk.get('reason')}): {sp['quote'][:60]!r}"
    if norm_ws(st.messages[src].text[chk["start"]:chk["end"]]) != norm_ws(sp["quote"]):
        return f"the raw bytes at {chk['start']}-{chk['end']} are not the passage as written: {sp['quote'][:60]!r}"
    inside = src == key and any(k == key and chk["start"] < b and a < chk["end"] for k, a, b in act_spans)
    sp.update(msg_key=src, start=chk["start"], end=chk["end"], region=chk["region"], block=chk["block"],
              author=chk["evidence_author"],
              scope="activity" if inside else ("context" if src == key else "other_message"),
              visibility="batch" if src == key else "linked_original", via_block=via)
    return None


def resolve(ev: dict, labels: dict, spans: dict, st: store.Store, vcfg: dict,
            negatives: dict | None = None) -> tuple[dict, dict | None, list[str]]:
    """Return (evidence file, negative-claims file, both with computed fields, problems). Pure."""
    out, problems = json.loads(json.dumps(ev)), []
    acts = {a["id"]: (k, a) for k, m in labels["messages"].items() for a in m.get("activities", [])}
    want = {(a["id"], s) for _, a in acts.values() for s in a["stall_signals"]}
    have = [(e["id"], e["signal"]) for e in out["entries"]]
    problems += [f"{i} {s}: positive label with no evidence entry" for i, s in sorted(want - set(have))]
    problems += [f"{i} {s}: evidence entry for a signal the label does not carry" for i, s in sorted(set(have) - want)]
    problems += [f"{i} {s}: more than one evidence entry" for i, s in sorted({x for x in have if have.count(x) > 1})]
    where = {}
    for e in out["entries"]:
        me = f"{e['id']} {e['signal']}"
        if e.get("support") not in SUPPORT:
            problems.append(f"{me}: support must be one of {SUPPORT}")
        if e.get("support") == "uncertain" and not e.get("note", "").strip():
            problems.append(f"{me}: an uncertain entry needs a note saying why")
        if e.get("basis") not in BASIS:
            problems.append(f"{me}: basis must be one of {BASIS}")
        if e["id"] not in acts:
            continue
        key, a = acts[e["id"]]
        if not e["spans"]:
            problems.append(f"{me}: no passage")
        for sp in e["spans"]:
            p = _passage(sp, key, spans.get(e["id"], []), st, vcfg)
            if p:
                problems.append(f"{me}: {p}")
                continue
            where.setdefault((sp["msg_key"], sp["start"], sp["end"]), []).append(me)
            # attribution: a passage inside the activity's span written by someone the label does not name as
            # author or actor is recorded for review (an activity's span may include a quoted reply)
            named = _name(a.get("author", "")).union(*(_name(x) for x in a.get("actors", [])))
            sp["author_named_in_label"] = sp["scope"] != "activity" or bool(_name(sp["author"]) & named)
        if e.get("basis") in NEEDS and not any(sp.get("scope") == NEEDS[e["basis"]] for sp in e["spans"]):
            problems.append(f"{me}: basis {e['basis']} needs a passage with scope {NEEDS[e['basis']]}")
        if e.get("basis") != "own_text" and not e.get("note", "").strip():
            problems.append(f"{me}: a context-dependent entry needs a note naming the context it relies on")
    for e in out["entries"]:
        for sp in e["spans"]:
            if "start" in sp:
                sp["shared_with"] = [x for x in where[(sp["msg_key"], sp["start"], sp["end"])] if x != f"{e['id']} {e['signal']}"]
    neg = None
    if negatives is not None:
        neg = json.loads(json.dumps(negatives))
        seen = []
        for c in neg["claims"]:
            me = f"negative {c['id']} {c['signal']}"
            seen.append((c["id"], c["signal"]))
            if c["id"] not in acts:
                problems.append(f"{me}: no such labelled activity")
                continue
            key, a = acts[c["id"]]
            if c["signal"] in a["stall_signals"]:
                problems.append(f"{me}: the approved label carries this signal, so the claim is not unsupported")
            if not c.get("why", "").strip() or not c.get("rule", "").strip():
                problems.append(f"{me}: needs a 'why' and the 'rule' that excludes it")
            for sp in c["spans"]:
                p = _passage(sp, key, spans.get(c["id"], []), st, vcfg)
                if p:
                    problems.append(f"{me}: {p}")
        problems += [f"negative {i} {s}: listed twice" for i, s in sorted({x for x in seen if seen.count(x) > 1})]
    return out, neg, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evidence-support benchmark: resolve offsets, check coverage.")
    ap.add_argument("action", choices=["resolve", "check"])
    args = ap.parse_args(argv)
    vcfg = config.load()["verify"]
    st = store.load()
    sl = json.loads((bench.ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))
    labels = json.loads(bench.LABELS.read_text(encoding="utf-8"))
    lab_problems, spans = bench.check_labels(labels, st, sl["benchmark_keys"], vcfg)
    ev = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    negs = json.loads(NEGATIVE.read_text(encoding="utf-8")) if NEGATIVE.exists() else None
    out, neg, problems = resolve(ev, labels, spans, st, vcfg, negs)
    problems = lab_problems + problems
    if args.action == "resolve" and not problems:
        EVIDENCE.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        if neg is not None:
            NEGATIVE.write_text(json.dumps(neg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    if args.action == "check":
        fields = lambda sps: [{k: s.get(k) for k in COMPUTED} for s in sps]
        problems += [f"{e['id']} {e['signal']}: computed fields are missing or out of date (run resolve)"
                     for e, f in zip(ev["entries"], out["entries"]) if fields(e["spans"]) != fields(f["spans"])]
        if negs is not None:
            problems += [f"negative {c['id']} {c['signal']}: computed fields are missing or out of date (run resolve)"
                         for c, f in zip(negs["claims"], neg["claims"]) if fields(c["spans"]) != fields(f["spans"])]
    for p in problems:
        print("evidence problem:", p)
    ents = out["entries"]
    sps = [s for e in ents for s in e["spans"]]
    count = lambda k, v: sum(s.get(k) == v for s in sps)
    print(f"evidence: {len(ents)} entries ({sum(e['support'] == 'uncertain' for e in ents)} uncertain; basis "
          + ", ".join(f"{b} {sum(e.get('basis') == b for e in ents)}" for b in BASIS)
          + f"), {len(sps)} passages (activity {count('scope', 'activity')}, context {count('scope', 'context')}, "
          f"other message {count('scope', 'other_message')}; shared {sum(bool(s.get('shared_with')) for s in sps)}); "
          f"status {ev.get('status')!r}")
    for e in ents:
        for s in e["spans"]:
            if s.get("author_named_in_label") is False:
                print(f"attribution to review: {e['id']} {e['signal']}: {s['author']!r} wrote {s['quote'][:50]!r} inside the "
                      f"activity's span but is not its labelled author or an actor")
    if neg is not None:
        print(f"negative claims: {len(neg['claims'])}; status {neg.get('status')!r}")
    print(f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
