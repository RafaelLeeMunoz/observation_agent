"""The benchmark: human-reviewed labels, and code that scores an extraction against them.

Labels (eval/benchmark/labels.json) give, per benchmark message: its outcome, the activities it
shows (each with a quote span from the raw text, who, requested/done, recurrence evidence, stall
and its basis), and conclusions an extractor must NOT draw. Every label quote is checked by the
same verifier as model quotes, so labels point at real raw-byte spans.

The draft labels are frozen in labels_draft.json (its SHA-256 is recorded in labels.json). Review
never edits silently: every difference between the frozen draft and labels.json must be a
'review' entry saying who, when, why, and the label before and after. check_review enforces it.

Scoring is deterministic: an extracted observation matches a labelled activity when its verified
quote overlaps one of the activity's labelled spans in the same message. An activity's spans are
its `quote` plus any `other_quotes`: together they cover every sentence that describes that piece
of work. Spans of different activities never overlap, so one quote cannot credit two activities. Recall, outcome accuracy, stall and
requested/done agreement are computed; extracted observations with no labelled span are listed
for the user to judge (possible false positives), never auto-scored as wrong.

Run:  .venv\\Scripts\\python -m src.bench check
      .venv\\Scripts\\python -m src.bench score --verified data/extract/verified/b001.json
      .venv\\Scripts\\python -m src.bench blind-template      (the user's blind-label file)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from . import config, schema, store, verify

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "eval" / "benchmark" / "labels.json"
BLIND = ROOT / "eval" / "benchmark" / "blind_user.json"
OUTCOMES = ("observations", "business_no_process_signal", "not_business", "ambiguous", "cannot_interpret")
# Labels use the extractor's own vocabulary, read from its schema so the two cannot drift apart.
_OBS = schema.load("extract.schema.json")["properties"]["observations"]["items"]["properties"]
STATES = tuple(_OBS["action_state"]["enum"])
STALLS = tuple(_OBS["stall_signals"]["items"]["enum"])      # the signal types; none = an empty list


def signals_of(x: dict) -> frozenset:
    """An activity's or observation's stall signals as a set. Output from prompts before extract-v6
    carries one `stall_signal` ("none" or a type); it is read as a set of at most one, as written."""
    if "stall_signals" in x:
        return frozenset(x["stall_signals"])
    return frozenset() if x.get("stall_signal") in (None, "none") else frozenset([x["stall_signal"]])


def show(sig: frozenset) -> str:
    return "+".join(sorted(sig)) or "none"
BLIND_RESULTS = ("agree", "agree_missing_detail", "disagree")
# score-v2 (22 Nov 2026) adds complete-message recall, messages fully right, precision, duplicate hits,
# observations spanning several activities, overlapping observation quotes and state/stall confusion
# tables. The score-v1 measures keep their meaning. Every score records this version and the hash of
# this file, so the scoring used for the holdout can be frozen and checked.
# score-v3 (22 Nov 2026, the user chose "Several signals"): an activity may carry several stall
# signals. `stall_agreement` = the same SET of signals (identical to score-v2 when every set holds at
# most one); added: `stall_any_agreement` (stall vs no stall) and per-signal counts (labelled,
# extracted, both: recall = both/labelled, precision = both/extracted). Older output with one
# `stall_signal` is read as a set of at most one (signals_of).
SCORE_VERSION = "score-v3"


def check_labels(labels: dict, st: store.Store, keys: list[str], vcfg: dict) -> tuple[list[str], dict]:
    """Return (problems, spans) where spans maps activity id -> [(msg_key, start, end), ...]."""
    problems, spans = [], {}
    msgs = labels.get("messages", {})
    missing = [k for k in keys if k not in msgs]
    extra = [k for k in msgs if k not in keys]
    if missing:
        problems.append(f"no label for {len(missing)} benchmark message(s): {missing[:5]}")
    if extra:
        problems.append(f"labels for messages outside the benchmark: {extra[:5]}")
    for key, lab in msgs.items():
        if lab.get("outcome") not in OUTCOMES:
            problems.append(f"{key}: outcome {lab.get('outcome')!r} is not one of {OUTCOMES}")
        acts = lab.get("activities", [])
        if (lab.get("outcome") == "observations") != bool(acts):
            problems.append(f"{key}: outcome 'observations' must go with at least one activity, and only then")
        for a in acts:
            sig = a.get("stall_signals")
            if a.get("action_state") not in STATES or not isinstance(sig, list) or len(set(sig)) != len(sig) \
                    or any(s not in STALLS for s in sig) or "stall_signal" in a:
                problems.append(f"{a.get('id')}: bad action_state or stall_signals (a list of {STALLS}, no repeats)")
            if key not in st.messages:
                continue
            for q in [a.get("quote", ""), *a.get("other_quotes", [])]:
                chk = verify.check_quote(st.messages[key], q, vcfg["min_quote_chars"])
                if not chk.get("ok"):
                    problems.append(f"{a.get('id')}: label quote does not verify ({chk.get('reason')}): {q[:60]!r}")
                else:
                    spans.setdefault(a["id"], []).append((key, chk["start"], chk["end"]))
        own = [(s, e, a["id"]) for a in acts for (_, s, e) in spans.get(a["id"], [])]
        problems += [f"{x} and {y}: their label spans overlap, so one quote could credit both"
                     for i, (s1, e1, x) in enumerate(own) for (s2, e2, y) in own[i + 1:] if x != y and s1 < e2 and s2 < e1]
    return problems, spans


def check_review(labels: dict, draft_bytes: bytes) -> list[str]:
    """Every difference from the frozen draft must be a recorded review decision, applied in order."""
    problems = []
    if hashlib.sha256(draft_bytes).hexdigest() != labels.get("draft_sha256"):
        problems.append("the frozen draft does not match draft_sha256: it was edited or replaced")
    now = dict(json.loads(draft_bytes.decode("utf-8"))["messages"])
    for i, r in enumerate(labels.get("review", []), 1):
        key = r.get("msg_key")
        if not (r.get("by") and r.get("when") and r.get("reason")):
            problems.append(f"review entry {i} ({key}): needs who, when and why")
        if now.get(key) != r.get("before"):
            problems.append(f"review entry {i} ({key}): 'before' is not the label as it stood")
        now[key] = r.get("after")
    for key in sorted(set(now) | set(labels.get("messages", {}))):
        if now.get(key) != labels["messages"].get(key):
            problems.append(f"{key}: label differs from the frozen draft without a review entry")
    return problems


def blind_result(labels: dict, blind_keys: list[str]) -> tuple[list[str], Counter]:
    """The user's blind comparison: one recorded result per blind message, counted here by code."""
    res = labels.get("blind_comparison", {}).get("result")
    if res is None:
        return [], Counter()
    problems = []
    if sorted(res) != sorted(blind_keys):
        problems.append("the blind comparison needs one result for each blind message, and only those")
    problems += [f"{k}: blind result {v!r} is not one of {BLIND_RESULTS}" for k, v in res.items() if v not in BLIND_RESULTS]
    for r in labels.get("review", []):
        if r.get("source") == "blind_comparison" and res.get(r.get("msg_key")) == "agree":
            problems.append(f"review entry {r.get('n')}: a blind-comparison change on a message recorded as 'agree'")
    return problems, Counter(v for k, v in res.items() if k in blind_keys)


def score(labels: dict, spans: dict, results: list[dict], st: store.Store, vcfg: dict) -> dict:
    """Compare verified extraction results with the labels (deterministic)."""
    disp = {x["msg_key"]: x["disposition"] for r in results if r["status"] == "verified" for x in r["messages"]}
    obs = [o for r in results if r["status"] == "verified" for o in r["observations"]]
    rejected = [x for r in results for x in r.get("rejected", [])]
    keys = list(labels["messages"])
    exact = sum(disp.get(k) == labels["messages"][k]["outcome"] for k in keys)
    collapsed = sum((disp.get(k) == "observations") == (labels["messages"][k]["outcome"] == "observations") for k in keys)
    confusion = Counter((labels["messages"][k]["outcome"], disp.get(k, "missing")) for k in keys)

    def overlaps(o, act_spans):
        return any(o["msg_key"] == key and o["start"] < e and s < o["end"] for key, s, e in act_spans)

    found, state_agree, stall_agree, missed, disagreements = [], 0, 0, [], []
    hits_by_act, all_right = {}, {}
    state_conf, stall_conf = Counter(), Counter()
    any_stall_agree = 0
    sig_counts = {s: Counter() for s in STALLS}
    for k, lab in labels["messages"].items():
        for a in lab.get("activities", []):
            hits = [o for o in obs if overlaps(o, spans.get(a["id"], []))]
            hits_by_act[a["id"]] = hits
            if not hits:
                missed.append(a["id"])
                all_right[a["id"]] = False
                continue
            found.append(a["id"])
            h = hits[0]                     # the first observation whose quote overlaps the labelled span
            ls, hs = signals_of(a), signals_of(h)
            state_agree += h["action_state"] == a["action_state"]
            stall_agree += hs == ls
            any_stall_agree += bool(hs) == bool(ls)
            for s in STALLS:
                sig_counts[s]["labelled"] += s in ls
                sig_counts[s]["extracted"] += s in hs
                sig_counts[s]["both"] += s in ls and s in hs
            state_conf[(a["action_state"], h["action_state"])] += 1
            stall_conf[(show(ls), show(hs))] += 1
            all_right[a["id"]] = (h["action_state"], hs) == (a["action_state"], ls)
            if not all_right[a["id"]]:
                disagreements.append({"id": a["id"], "obs_id": h["obs_id"],
                                      "label": f"{a['action_state']} / {show(ls)}",
                                      "extracted": f"{h['action_state']} / {show(hs)}"})
    n_acts = sum(len(l.get("activities", [])) for l in labels["messages"].values())
    unmatched = [{"obs_id": o["obs_id"], "msg_key": o["msg_key"], "task": o["task"], "quote": o["quote"]}
                 for o in obs if not any(overlaps(o, sp) for sp in spans.values())]

    # Added in score-v2, beside the measures above (which keep their meaning):
    with_acts = [k for k, lab in labels["messages"].items() if lab.get("activities")]
    incomplete = [k for k in with_acts if any(not hits_by_act[a["id"]] for a in labels["messages"][k]["activities"])]
    not_fully_right = [k for k in keys if disp.get(k) != labels["messages"][k]["outcome"]
                       or not all(all_right[a["id"]] for a in labels["messages"][k].get("activities", []))]
    matched_obs = [o for o in obs if any(overlaps(o, sp) for sp in spans.values())]
    duplicates = [{"id": aid, "obs_ids": [o["obs_id"] for o in hs]} for aid, hs in hits_by_act.items() if len(hs) > 1]
    acts_of = {o["obs_id"]: [aid for aid, sp in spans.items() if overlaps(o, sp)] for o in obs}
    spanning = [{"obs_id": oid, "ids": ids} for oid, ids in acts_of.items() if len(ids) > 1]
    overlapping = [[a["obs_id"], b["obs_id"]] for i, a in enumerate(obs) for b in obs[i + 1:]
                   if a["msg_key"] == b["msg_key"] and a["start"] < b["end"] and b["start"] < a["end"]]
    invalid = [o["obs_id"] for o in obs if not verify.reverify(o, st, vcfg)]
    return {
        "messages": len(keys),
        "outcome_exact": exact, "outcome_exact_rate": round(exact / len(keys), 3),
        "outcome_observation_vs_not": collapsed, "outcome_observation_vs_not_rate": round(collapsed / len(keys), 3),
        "confusion": {f"{a} -> {b}": n for (a, b), n in sorted(confusion.items())},
        "activities": n_acts, "activities_found": len(found),
        "recall": round(len(found) / n_acts, 3) if n_acts else None,
        "action_state_agreement": f"{state_agree}/{len(found)}", "stall_agreement": f"{stall_agree}/{len(found)}",
        "missed": missed, "disagreements": disagreements,
        "extracted": len(obs), "unmatched_for_review": unmatched,
        "rejected_quotes": len(rejected),
        "complete_messages": f"{len(with_acts) - len(incomplete)}/{len(with_acts)}", "incomplete_messages": incomplete,
        "messages_fully_right": f"{len(keys) - len(not_fully_right)}/{len(keys)}", "not_fully_right": not_fully_right,
        "precision": f"{len(matched_obs)}/{len(obs)}",
        "duplicate_hits": duplicates, "observations_spanning_activities": spanning,
        "overlapping_observation_quotes": overlapping,
        "extractor_flags": [f for r in results if r["status"] == "verified" for f in r.get("flags", [])],
        "state_confusion": {f"{a} -> {b}": n for (a, b), n in sorted(state_conf.items())},
        "stall_confusion": {f"{a} -> {b}": n for (a, b), n in sorted(stall_conf.items())},
        "stall_any_agreement": f"{any_stall_agree}/{len(found)}",
        "stall_signals": {s: {"labelled": c["labelled"], "extracted": c["extracted"], "both": c["both"]}
                          for s, c in sig_counts.items()},
        "invariants": {"no invalid citation among verified observations": not invalid,
                       "every benchmark message has an outcome": all(k in disp for k in keys)},
    }


def blind_template(keys: list[str], st: store.Store) -> dict:
    return {"instructions": "Label each message yourself BEFORE reading labels.json. outcome is one of "
                            + ", ".join(OUTCOMES) + ". For each activity copy a quote exactly from the email.",
            "messages": {k: {"original_path": st.messages[k].original_path, "subject": st.messages[k].subject,
                             "outcome": "", "activities": [], "notes": ""} for k in keys}}


def check_approval(labels: dict) -> list[str]:
    """Approved labels say who approved them, when, in their own words, and after which review entry.
    Any later review entry makes the approval stale until the user approves again."""
    status = labels.get("status")
    if status not in ("draft", "in_review", "approved"):
        return [f"status {status!r} is not draft, in_review or approved"]
    if status != "approved":
        return ["reviewed_by_user is true but the labels are not approved"] if labels.get("reviewed_by_user") else []
    a = labels.get("approval") or {}
    problems = []
    if not (a.get("by") == "user" and a.get("when") and a.get("user_words")):
        problems.append("approved labels need an approval record: by the user, when, and the user's words")
    if labels.get("reviewed_by_user") is not True:
        problems.append("approved labels must have reviewed_by_user true")
    if a.get("after_review_entry") != len(labels.get("review", [])):
        problems.append("the approval is stale: review entries were added after it; the user must approve again")
    return problems


def scoring_refusal(labels: dict, problems: list[str], provisional: bool) -> str | None:
    """Why these labels may not be scored, or None. Labels in review score only as 'provisional'."""
    if problems:
        return "the benchmark labels must pass check"
    if labels.get("status") == "approved":
        return None
    if provisional and labels.get("status") == "in_review":
        return None
    return "the benchmark labels are not approved by the user (use --provisional to score labels in review)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark labels: check, score, blind template.")
    ap.add_argument("action", choices=["check", "score", "blind-template"])
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--verified", type=Path, nargs="*", default=[])
    ap.add_argument("--provisional", action="store_true",
                    help="score labels that are still in review; the output says the score is provisional")
    args = ap.parse_args(argv)
    vcfg = config.load()["verify"]
    st = store.load()
    sl = json.loads((ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))
    if args.action == "blind-template":
        BLIND.parent.mkdir(parents=True, exist_ok=True)
        if BLIND.exists():
            print(f"{BLIND} already exists; not overwriting")
            return 1
        BLIND.write_text(json.dumps(blind_template(sl["blind_keys"], st), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {BLIND}")
        return 0
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    problems, spans = check_labels(labels, st, sl["benchmark_keys"], vcfg)
    if labels.get("draft_file"):
        problems += check_review(labels, (args.labels.parent / labels["draft_file"]).read_bytes())
    elif labels.get("status") != "draft":
        problems.append("labels past the draft stage must name their frozen draft_file")
    blind_problems, blind = blind_result(labels, sl["blind_keys"])
    problems += blind_problems + check_approval(labels)
    for p in problems:
        print("label problem:", p)
    if args.action == "check":
        print(f"labels: {len(labels['messages'])} messages, {len(spans)} activities, "
              f"{sum(map(len, spans.values()))} label spans verified, "
              f"{len(labels.get('review', []))} review change(s), {len(problems)} problem(s); "
              f"status {labels.get('status')!r}")
        if blind:
            n = sum(blind.values())
            print(f"blind comparison ({n} messages): " + ", ".join(f"{blind[r]} {r}" for r in BLIND_RESULTS)
                  + f"; same outcome, state and stall: {blind['agree'] + blind['agree_missing_detail']}/{n}")
        return 0 if not problems else 1
    refusal = scoring_refusal(labels, problems, args.provisional)
    if refusal:
        print("not scoring:", refusal)
        return 1
    results = [json.loads(p.read_text(encoding="utf-8")) for p in args.verified]
    out = {"labels": {"status": labels.get("status"), "provisional": labels.get("status") != "approved",
                      "review_entries": len(labels.get("review", []))},
           "scoring": {"version": SCORE_VERSION,
                       "bench_py_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
           **score(labels, spans, results, st, vcfg)}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
