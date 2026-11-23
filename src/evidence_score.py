"""Evidence-support scoring: does each PUBLISHED stall signal carry evidence that supports it?

The yardstick is frozen and approved: labels.json (bench-v1 r89) for which signals are right,
eval/benchmark/evidence_spans.json (evidence-v1) for which passages show each right signal, and
eval/benchmark/negative_claims.json (negative-v1) for plausible signals that are wrong. Activities are
matched to observations by the frozen benchmark scorer (src/bench.py score-v6, with the person
adjudications); this module never re-implements or changes that matching. Nothing here is shown to a
model: only code reads these files (the fence denies them to every agent).

For each run it separates six things the user asked to keep apart:
  1. valid citation     the published evidence is literally in the raw file at its stored offsets
  2. supports signal    the evidence overlaps an approved passage for that signal in that message
                        (of any activity)
  3. supports activity  ... an approved passage of THIS activity and signal
  4. context-dependent  the approved entry's basis is same_message_context or cross_message_context
  5. not accessible     the entry's needed passage is one the architecture cannot see (H sees the
                        batch only; I and J see the batch plus linked originals)
  6. unsupported / unresolved claims, and negative claims published

Per labelled (activity, signal), one category:
  published_supported      published, evidence overlaps an approved passage of this activity+signal
  published_other_passage  published, valid evidence, but not an approved passage (signal right, the
                           evidence does not show it by the reference)
  correct_unsupported      claimed but the grounder said unsupported
  correct_rejected         claimed but its evidence failed (bad quote, bad citation, missing entry)
  correct_unresolved       claimed but left unresolved / unsure
  correct_not_grounded     claimed, never checked (clean-C, or a J claim not sent and not evidenced)
  signal_missed            the activity was found but the signal was not claimed
  activity_missed          the activity was not found at all
End-to-end published-signal recall = published_supported / all labelled signals, whatever the
architecture could see: a system may not hide a recall failure behind its access rule. The
accessibility split is reported beside it, never instead of it.

An activity's signals count as published when ANY observation credited to it (the judged one or a
duplicate) publishes them, because downstream events merge duplicates; every published signal of
every observation counts toward precision.

Run:  .venv\\Scripts\\python -m src.evidence_score --arch I --run data/ground/verified/I_v8c.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from . import bench, config, evidence_bench, store

SCORER_VERSION = "evidence-score-v1.1"   # v1.1 (23 Nov 2026): counting units written out (published_counts); no metric changed
VISIBLE = {"clean-C": set(), "H": {"batch"}, "I": {"batch", "linked_original"}, "J": {"batch", "linked_original"}}
CATEGORIES = ("published_supported", "published_other_passage", "correct_unsupported", "correct_rejected",
              "correct_unresolved", "correct_not_grounded", "signal_missed", "activity_missed")


def load_yardstick() -> dict:
    vcfg = config.load()["verify"]
    st = store.load()
    sl = json.loads((bench.ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))
    labels = json.loads(bench.LABELS.read_text(encoding="utf-8"))
    problems, spans = bench.check_labels(labels, st, sl["benchmark_keys"], vcfg)
    if problems or labels.get("status") != "approved":
        raise RuntimeError(f"labels not usable as the yardstick: {problems[:3]} status {labels.get('status')}")
    ev = json.loads(evidence_bench.EVIDENCE.read_text(encoding="utf-8"))
    neg = json.loads(evidence_bench.NEGATIVE.read_text(encoding="utf-8"))
    adj = json.loads(bench.ADJUDICATIONS.read_text(encoding="utf-8"))["entries"]
    fz = json.loads((bench.ROOT / "eval" / "benchmark" / "evidence_freeze.json").read_text(encoding="utf-8"))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    ev_freeze = {"evidence_matches": sha(evidence_bench.EVIDENCE) == fz["evidence_spans_sha256"],
                 "negatives_match": sha(evidence_bench.NEGATIVE) == fz["negative_claims_sha256"],
                 "labels_freeze": bench.freeze_status()}
    return {"st": st, "vcfg": vcfg, "labels": labels, "spans": spans, "ev": ev, "neg": neg, "adj": adj,
            "freeze": ev_freeze}


def _overlap(a: dict, b: dict) -> bool:
    return a["msg_key"] == b["msg_key"] and a["start"] < b["end"] and b["start"] < a["end"]


def accessible(entry: dict, arch: str) -> bool:
    """Can this architecture see the passage the entry's basis needs?"""
    need = evidence_bench.NEEDS[entry["basis"]]
    return any(sp["scope"] == need and sp["visibility"] in VISIBLE[arch] for sp in entry["spans"])


def published_evidence(o: dict, signal: str) -> list[dict]:
    """The evidence a published signal carries, as (msg_key, start, end, quote_raw) records."""
    ev = [e for e in o.get("published_evidence", []) if e["signal"] == signal]
    if ev:
        return ev
    return [{"signal": signal, "msg_key": o["msg_key"], "start": e["start"], "end": e["end"],
             "quote_raw": e["quote_raw"]} for e in o.get("stall_evidence", []) if e["signal"] == signal]


def outcome_of(o: dict, signal: str) -> str:
    for x in o.get("signal_outcomes", []):
        if x["signal"] == signal:
            return x["outcome"]
    return "published" if signal in o.get("stall_signals", []) else "not_grounded"


def evaluate(run: dict, arch: str, y: dict) -> dict:
    """Score one run (a verified extraction, or a grounded copy of one). Deterministic, no model."""
    st, labels = y["st"], y["labels"]
    sc = bench.score(labels, y["spans"], [run], st, y["vcfg"], y["adj"])
    obs = {o["obs_id"]: o for o in run["observations"]}
    assigned: dict[str, list[str]] = {}
    for aid, oid in sc["credited_obs"].items():
        assigned.setdefault(aid, []).append(oid)
    for d in sc["duplicate_observations"]:
        assigned.setdefault(d["activity"], []).append(d["obs_id"])
    activity_of = {oid: aid for aid, oids in assigned.items() for oid in oids}
    acts = {a["id"]: a for m in labels["messages"].values() for a in m.get("activities", [])}
    entries = {(e["id"], e["signal"]): e for e in y["ev"]["entries"]}
    negatives = {(c["id"], c["signal"]): c for c in y["neg"]["claims"]}
    same_signal = {}                                  # (msg_key, signal) -> approved passages of any activity
    for e in y["ev"]["entries"]:
        for sp in e["spans"]:
            same_signal.setdefault(sp["msg_key"], {}).setdefault(e["signal"], []).append(sp)

    rows = []
    for (aid, sig), e in entries.items():
        oids = assigned.get(aid, [])
        row = {"id": aid, "signal": sig, "basis": e["basis"], "context_dependent": e["basis"] != "own_text",
               "accessible": {a: accessible(e, a) for a in ("H", "I")}, "obs_ids": oids}
        if not oids:
            row["category"] = "activity_missed"
            rows.append(row)
            continue
        pubs = [obs[i] for i in oids if sig in obs[i].get("stall_signals", [])]
        claims = [obs[i] for i in oids if sig in obs[i].get("stall_signals_claimed", obs[i].get("stall_signals", []))]
        if pubs:
            ev = [x for o in pubs for x in published_evidence(o, sig)]
            row["evidence"] = [{k: x.get(k) for k in ("msg_key", "start", "end", "sentence_id", "scope")} for x in ev]
            act_hit = any(_overlap(x, sp) for x in ev for sp in e["spans"])
            sig_hit = any(_overlap(x, sp) for x in ev for sp in same_signal.get(x["msg_key"], {}).get(sig, []))
            row["supports_activity"], row["supports_signal"] = act_hit, act_hit or sig_hit
            row["category"] = "published_supported" if act_hit else "published_other_passage"
        elif claims:
            outs = {outcome_of(o, sig) for o in claims}
            row["outcomes"] = sorted(outs)
            row["category"] = ("correct_unsupported" if "unsupported" in outs else
                               "correct_rejected" if "rejected" in outs else
                               "correct_unresolved" if "unresolved" in outs else "correct_not_grounded")
        else:
            row["category"] = "signal_missed"
        rows.append(row)

    # every published signal, for precision and the false positives
    false_pub, unlabelled_pub, negative_pub, citations = [], [], [], {"checked": 0, "valid": 0, "invalid": []}
    withheld = []
    pub_pairs = set()
    for o in run["observations"]:
        aid = activity_of.get(o["obs_id"])
        for sig in o.get("stall_signals", []):
            for x in published_evidence(o, sig):
                citations["checked"] += 1
                m = st.messages.get(x["msg_key"])
                ok = m is not None and m.text[x["start"]:x["end"]] == x["quote_raw"] and x["end"] > x["start"]
                citations["valid"] += ok
                if not ok:
                    citations["invalid"].append({"obs_id": o["obs_id"], "signal": sig, "msg_key": x["msg_key"]})
            if not published_evidence(o, sig):
                citations["invalid"].append({"obs_id": o["obs_id"], "signal": sig, "problem": "published without evidence"})
            if aid is None:
                unlabelled_pub.append({"obs_id": o["obs_id"], "msg_key": o["msg_key"], "signal": sig, "task": o.get("task")})
                continue
            pub_pairs.add((aid, sig))
            if sig not in acts[aid]["stall_signals"]:
                rec = {"id": aid, "obs_id": o["obs_id"], "signal": sig, "negative_claim": (aid, sig) in negatives}
                false_pub.append(rec)
                if rec["negative_claim"]:
                    negative_pub.append(rec)
        for sig in o.get("stall_signals_claimed", []):
            if sig not in o.get("stall_signals", []) and aid is not None and sig not in acts[aid]["stall_signals"]:
                withheld.append({"id": aid, "obs_id": o["obs_id"], "signal": sig, "outcome": outcome_of(o, sig),
                                 "negative_claim": (aid, sig) in negatives})
    neg_rows = []
    for (aid, sig), c in negatives.items():
        oids = assigned.get(aid, [])
        claimed = any(sig in obs[i].get("stall_signals_claimed", obs[i].get("stall_signals", [])) for i in oids)
        published = any(sig in obs[i].get("stall_signals", []) for i in oids)
        neg_rows.append({"id": aid, "signal": sig, "obs_ids": oids, "activity_found": bool(oids), "claimed": claimed,
                         "published": published,
                         "outcome": sorted({outcome_of(obs[i], sig) for i in oids
                                            if sig in obs[i].get("stall_signals_claimed", [])}) or None})

    cat = Counter(r["category"] for r in rows)
    n = len(rows)
    by_sig = {}
    for sig in bench.STALLS:
        lab = [r for r in rows if r["signal"] == sig]
        pub_true = sum(r["category"] in ("published_supported", "published_other_passage") for r in lab)
        pub_all = sum(1 for (a, s) in pub_pairs if s == sig) + sum(u["signal"] == sig for u in unlabelled_pub)
        by_sig[sig] = {"labelled": len(lab), "published_correct": pub_true,
                       "published_supported": sum(r["category"] == "published_supported" for r in lab),
                       "published_total": pub_all, "published_false": sum(f["signal"] == sig for f in false_pub),
                       "published_on_unlabelled_obs": sum(u["signal"] == sig for u in unlabelled_pub)}
    acc = {a: [r for r in rows if r["accessible"][a]] for a in ("H", "I")}
    own_arch = arch if arch in ("H", "I") else ("I" if arch == "J" else None)
    published_total = len(pub_pairs) + len(unlabelled_pub)
    return {
        "scorer": SCORER_VERSION, "architecture": arch, "freeze": y["freeze"],
        "extraction": {k: sc[k] for k in ("activities_found", "activities", "precision", "precision_one_to_one",
                                          "action_state_agreement", "stall_agreement", "stall_any_agreement",
                                          "stall_signals", "unadjudicated_low_meaning", "missed")},
        "labelled_signals": n,
        "categories": {c: cat.get(c, 0) for c in CATEGORIES},
        "end_to_end_published_signal_recall": f"{cat.get('published_supported', 0)}/{n}",
        "published_correct_any_evidence": f"{cat.get('published_supported', 0) + cat.get('published_other_passage', 0)}/{n}",
        "accessibility": {a: {"accessible_entries": len(acc[a]),
                              "published_supported_among_accessible": sum(r["category"] == "published_supported" for r in acc[a]),
                              "inaccessible": [f"{r['id']} {r['signal']}" for r in rows if not r["accessible"][a]]}
                          for a in ("H", "I")},
        "own_access": own_arch,
        "context_dependent": [{"id": r["id"], "signal": r["signal"], "basis": r["basis"], "category": r["category"],
                               "accessible": r["accessible"]} for r in rows if r["context_dependent"]],
        "published_total": published_total,
        # v1.1: the counting units written out. The grounder counts claims (observation x signal); this scorer
        # counts each (labelled activity, signal) once, because duplicate observations of one activity merge
        # into one event downstream, plus each signal on an observation that matches no labelled activity.
        "published_counts": {"claims": sum(len(o.get("stall_signals", [])) for o in run["observations"]),
                             "activity_signal_pairs": len(pub_pairs),
                             "on_unlabelled_observations": len(unlabelled_pub),
                             "counted": published_total,
                             "correct": published_total - len(false_pub) - len(unlabelled_pub),
                             "false_on_labelled_activities": len(false_pub)},
        "published_false": false_pub, "published_false_count": len(false_pub),
        "negative_claims_published": negative_pub,
        "published_on_unlabelled_observations": unlabelled_pub,
        "published_precision": f"{published_total - len(false_pub) - len(unlabelled_pub)}/{published_total}"
                               + f" ({len(pub_pairs)} activity-signal pairs + {len(unlabelled_pub)} on observations "
                                 "matching no label; unlabelled ones count as not correct)",
        "withheld_correctly": withheld,
        "negative_claims": neg_rows,
        "citations": {**citations, "all_valid": citations["checked"] == citations["valid"] and not citations["invalid"]},
        "by_signal": by_sig,
        "rows": rows,
    }


def consistency(a: dict, b: dict, run_a: dict, run_b: dict, y: dict) -> dict:
    """Repeat-run agreement per labelled activity: found in both, same state, same published signals."""
    sa = bench.score(y["labels"], y["spans"], [run_a], y["st"], y["vcfg"], y["adj"])["credited_obs"]
    sb = bench.score(y["labels"], y["spans"], [run_b], y["st"], y["vcfg"], y["adj"])["credited_obs"]
    oa = {o["obs_id"]: o for o in run_a["observations"]}
    ob = {o["obs_id"]: o for o in run_b["observations"]}
    acts = [x["id"] for m in y["labels"]["messages"].values() for x in m.get("activities", [])]
    both = [x for x in acts if x in sa and x in sb]
    state = sum(oa[sa[x]]["action_state"] == ob[sb[x]]["action_state"] for x in both)
    pub = sum(set(oa[sa[x]].get("stall_signals", [])) == set(ob[sb[x]].get("stall_signals", [])) for x in both)
    claimed = sum(set(oa[sa[x]].get("stall_signals_claimed", [])) == set(ob[sb[x]].get("stall_signals_claimed", []))
                  for x in both)
    cats = {(r["id"], r["signal"]): r["category"] for r in a["rows"]}
    same_cat = sum(cats[(r["id"], r["signal"])] == r["category"] for r in b["rows"])
    return {"activities": len(acts), "found_in_both": len(both),
            "found_in_one_only": sorted(set(sa) ^ set(sb)),
            "same_state": f"{state}/{len(both)}", "same_published_signals": f"{pub}/{len(both)}",
            "same_claimed_signals": f"{claimed}/{len(both)}",
            "same_evidence_category": f"{same_cat}/{len(b['rows'])}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score published stall signals against the evidence benchmark.")
    ap.add_argument("--arch", required=True, choices=sorted(VISIBLE))
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    y = load_yardstick()
    res = evaluate(json.loads(args.run.read_text(encoding="utf-8")), args.arch, y)
    res["run_file"] = str(args.run)
    text = json.dumps(res, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("architecture", "end_to_end_published_signal_recall",
                                          "published_correct_any_evidence", "categories", "published_precision",
                                          "published_false_count")}, indent=1))
    print("negative claims published:", [f"{r['id']} {r['signal']}" for r in res["negative_claims_published"]])
    print("citations:", {k: res["citations"][k] for k in ("checked", "valid", "all_valid")})
    print("freeze:", res["freeze"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
