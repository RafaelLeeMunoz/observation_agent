"""Design J: selective grounding. Code picks which of H's claimed signals go to the grounder.

J starts from an H run (one extraction call with evidence last). A claim H could not evidence, or whose
evidence is the kind H handles worst, is sent to the grounder; every other claim keeps H's own verified
evidence. The selection reads only H's output and the store (never labels), so it can run in production.

Strategies (each a superset of the one before, except `none` and `all`):
  none          send nothing: J = H
  unpublished   claims H left unresolved or whose evidence failed (rescue only)
  context       + claims whose H evidence lies outside the observation's own quote, and claims on a
                message whose batch hid a quoted block (its context lives in another message)
  risky         + every waiting and escalation claim (the two signal types with the most label disputes)
  all           every claim: J = I applied to H's claims

Offline evaluation (`evaluate`): given one grounding of ALL of H's claims, each strategy's J run is
assembled without new model calls (selected claims take the grounder's verdict, the rest keep H's
outcome). The grounder's verdicts are NOT treated as truth: every J run is scored against the approved
evidence and negative claims (src/evidence_score.py). The risk of skipping is measured directly: false or
weakly evidenced signals that a strategy left unselected and so published on H's word alone.

Run:  .venv\\Scripts\\python -m src.selective --h data/extract/bench_runs/haiku_v8h_b001.json \\
          --grounded data/ground/verified/IH_v8h.json --manifest data/ground/packets/IH_v8h/g003.manifest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import evidence_score, ground, store

STRATEGIES = ("none", "unpublished", "context", "risky", "all")


def _h_outcome(o: dict, sig: str) -> dict:
    return next((x for x in o.get("signal_outcomes", []) if x["signal"] == sig), {"outcome": "not_grounded"})


def selector(strategy: str, st: store.Store):
    """(obs, signal) -> True when the claim goes to the grounder."""
    if strategy not in STRATEGIES:
        raise ValueError(strategy)

    def hidden(o):
        return any(b.skip_reason for b in st.messages[o["msg_key"]].blocks)

    def outside_own(o, sig):
        return any(e["signal"] == sig and not e.get("inside_own_quote", False) for e in o.get("stall_evidence", []))

    def pick(o: dict, sig: str) -> bool:
        if strategy == "none":
            return False
        if strategy == "all":
            return True
        unpub = _h_outcome(o, sig)["outcome"] != "published"
        if strategy == "unpublished":
            return unpub
        ctx = unpub or outside_own(o, sig) or hidden(o)
        if strategy == "context":
            return ctx
        return ctx or sig in ("waiting", "escalation")
    return pick


def assemble(h_run: dict, full_manifest: dict, full_verdicts: dict, strategy: str, st: store.Store) -> dict:
    """The J run for one strategy, from one grounding of all of H's claims (no model call)."""
    pick = selector(strategy, st)
    obs = {o["obs_id"]: o for o in h_run["observations"]}
    keep = [c for c in full_manifest["claims"] if pick(obs[c["obs_id"]], c["signal"])]
    manifest = {**full_manifest, "claims": keep}
    out = ground.apply(h_run, manifest, {c["claim_id"]: full_verdicts[c["claim_id"]] for c in keep}, st,
                       only_selected=True)
    out["selection"] = {"strategy": strategy, "claims_sent": len(keep), "claims_total": len(full_manifest["claims"]),
                        "messages_sent": len({c["msg_key"] for c in keep}),
                        "sections_needed": len({s for c in keep for s in c["permitted_sections"]})}
    return out


def evaluate(h_run: dict, full_manifest: dict, full_verdicts: dict, y: dict) -> dict:
    st = y["st"]
    rows = {}
    for s in STRATEGIES:
        j = assemble(h_run, full_manifest, full_verdicts, s, st)
        res = evidence_score.evaluate(j, "J", y)
        pick = selector(s, st)
        obs = {o["obs_id"]: o for o in h_run["observations"]}
        # the risk of skipping: what was published on H's word alone, and was wrong or weakly evidenced
        skipped_false = [f for f in res["published_false"] if not pick(obs[f["obs_id"]], f["signal"])]
        skipped_unlab = [u for u in res["published_on_unlabelled_observations"] if not pick(obs[u["obs_id"]], u["signal"])]
        skipped_weak = [r for r in res["rows"] if r["category"] == "published_other_passage"
                        and not any(pick(obs[i], r["signal"]) for i in r["obs_ids"] if r["signal"] in
                                    obs[i].get("stall_signals_claimed", []))]
        hard = [r for r in res["rows"] if r["context_dependent"] or not r["accessible"]["H"]]
        hard_sel = [r for r in hard if any(pick(obs[i], r["signal"]) for i in r["obs_ids"]
                                           if r["signal"] in obs[i].get("stall_signals_claimed", []))]
        neg_sent = [n for n in res["negative_claims"]
                    if any(n["signal"] in obs[i].get("stall_signals_claimed", []) and pick(obs[i], n["signal"])
                           for i in n["obs_ids"])]
        rows[s] = {"selection": j["selection"],
                   "end_to_end_published_signal_recall": res["end_to_end_published_signal_recall"],
                   "published_correct_any_evidence": res["published_correct_any_evidence"],
                   "categories": res["categories"], "published_precision": res["published_precision"],
                   "published_false": len(res["published_false"]),
                   "negative_claims_published": len(res["negative_claims_published"]),
                   "published_on_unlabelled": len(res["published_on_unlabelled_observations"]),
                   "skipped_and_published_false": skipped_false, "skipped_and_published_unlabelled": skipped_unlab,
                   "skipped_and_weakly_evidenced": [f"{r['id']} {r['signal']}" for r in skipped_weak],
                   "hard_cases_claimed": len([r for r in hard if any(r["signal"] in obs[i].get("stall_signals_claimed", [])
                                                                     for i in r["obs_ids"])]),
                   "hard_cases_selected": len(hard_sel),
                   "negative_claims_claimed": sum(n["claimed"] for n in res["negative_claims"]),
                   "negative_claims_sent": len(neg_sent),
                   "citations_valid": res["citations"]["all_valid"]}
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate J's selection strategies offline from one full grounding of H.")
    ap.add_argument("--h", type=Path, required=True)
    ap.add_argument("--result", type=Path, required=True, help="data/ground/results/<name>__<attempt>.json")
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    y = evidence_score.load_yardstick()
    res = json.loads(args.result.read_text(encoding="utf-8"))
    rows = evaluate(json.loads(args.h.read_text(encoding="utf-8")), json.loads(args.manifest.read_text(encoding="utf-8")),
                    res["verdicts"], y)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    for s, r in rows.items():
        print(f"{s:12s} sent {r['selection']['claims_sent']:2d}/{r['selection']['claims_total']}  "
              f"e2e {r['end_to_end_published_signal_recall']:6s} any-ev {r['published_correct_any_evidence']:6s} "
              f"false {r['published_false']} neg {r['negative_claims_published']} unlab {r['published_on_unlabelled']} "
              f"| skipped&false {len(r['skipped_and_published_false'])} skipped&weak {len(r['skipped_and_weakly_evidenced'])} "
              f"| hard sel {r['hard_cases_selected']}/{r['hard_cases_claimed']} neg sent {r['negative_claims_sent']}/{r['negative_claims_claimed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
