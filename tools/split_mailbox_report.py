r"""Analysis of an isolated size-split test on one mailbox against its whole-mailbox result (code only, no model).

Usage: .venv\Scripts\python tools\split_mailbox_report.py --mailbox giron-d --baseline s002 --merge m210 --out eval/results/m5_giron_d_split

Reads data/runs/all/synth/{baseline, chunk ids from <out>/experiment.json, merge}.json; writes <out>/report.json. Changes nothing else.
Joins use the baseline processes as the reference identity: a baseline process whose observations sit, 3 or more each (and 30 percent of
that group), in groups of two or more chunks is a 'recurring process spanning chunks' (the denominator); a join is correct when the merger
put two or more of those groups in one final process. Whether a join is right in meaning and whether a new process is a real discovery is
read by a person from the lists printed here (the model never judges its own result).
"""
import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import events, pipeline, score, store, synth  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mailbox", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--merge", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sample", type=int, default=30)
    a = ap.parse_args(argv)
    OUT = ROOT / a.out
    r, st = pipeline.Run("all"), store.load()
    obs = {o["obs_id"]: o for f in sorted((r.data / "ground").glob("b*.json")) for o in json.loads(f.read_text(encoding="utf-8"))["observations"]
           if st.messages[o["msg_key"]].mailbox == a.mailbox}
    mb = list(obs.values())
    J = lambda p: json.loads((r.data / "synth" / f"{p}.json").read_text(encoding="utf-8"))
    exp = json.loads((OUT / "experiment.json").read_text(encoding="utf-8"))
    cids = [c["id"] for c in exp["chunks"]]
    base, m, chunks = J(a.baseline), J(a.merge), {c: J(c) for c in cids}
    thread = lambda o: st.messages[obs[o]["msg_key"]].thread_id
    seen = events.seen_in_mailboxes()
    win = {x: (datetime.fromisoformat(s), datetime.fromisoformat(e)) for x, s, e in pipeline._windows()}
    qids, _ = pipeline.quote_ids(mb)
    rep = {"mailbox": a.mailbox, "observations": len(mb)}

    def summarise(res):
        evs = events.build(mb, res["assignment"], st, seen)
        thr = defaultdict(set)
        for o, p in res["assignment"].items():
            thr[p].add(thread(o))
        sc = score.score(res["processes"], evs, win, r.cfg["score"], qids)
        scored = [o for o in sc["opportunities"] if o.get("status") == "scored"]
        return {"processes": len(res["processes"]), "assigned_obs": len(res["assignment"]), "events_build": len(evs),
                "events_sum_of_process_threads": sum(len(v) for v in thr.values()), "scored_processes": len(scored),
                "events_in_scored_processes": sum(o["events"] for o in scored), "threads_covered": len({t for v in thr.values() for t in v}),
                "dollars_per_month": str(sc["claims"]["total"]["value"])[:10]}, evs, thr

    bs, b_ev, b_thr = summarise(base)
    es, e_ev, e_thr = summarise(m)
    rep["baseline"], rep["split"] = bs, es
    rep["change_percent"] = {k: round(100 * (es[k] - bs[k]) / bs[k], 1) for k in ("events_build", "events_in_scored_processes", "assigned_obs", "processes")}
    print("BASELINE", bs)
    print("SPLIT   ", es)
    print("CHANGE %", rep["change_percent"])

    # ---- positional distribution (input order of the whole mailbox), baseline vs split
    order = [o["obs_id"] for o in sorted(mb, key=synth._order)]

    def shares(res, k):
        counted = set()
        for p in res["processes"]:
            own = list(dict.fromkeys(p["obs_ids"]))
            if not p.get("candidate") and len(own) >= 2:
                counted.update(own)
        return [round(100 * sum(1 for o in order[j * len(order) // k:(j + 1) * len(order) // k] if o in counted)
                      / max(1, len(order[j * len(order) // k:(j + 1) * len(order) // k])), 1) for j in range(k)]
    rep["positional_percent_counted"] = {"baseline_thirds": shares(base, 3), "split_thirds": shares(m, 3), "baseline_tenths": shares(base, 10),
                                         "split_tenths": shares(m, 10)}
    print("POSITIONAL baseline thirds", rep["positional_percent_counted"]["baseline_thirds"], "tenths", rep["positional_percent_counted"]["baseline_tenths"])
    print("POSITIONAL split    thirds", rep["positional_percent_counted"]["split_thirds"], "tenths", rep["positional_percent_counted"]["split_tenths"])

    # ---- joins (reference: the baseline processes)
    base_of = base["assignment"]
    groups = {}
    for cid, res in chunks.items():
        for p in res["processes"]:
            groups[f"{cid}.{p['process_id']}"] = (cid, p["name"], set(p["obs_ids"]))
    final_of = {g: p["process_id"] for p in m["processes"] for g in p.get("parts", [])}
    bproc = {p["process_id"]: set(p["obs_ids"]) for p in base["processes"]}
    bname = {p["process_id"]: p["name"] for p in base["processes"]}
    den, joined, full, nonj, seps = [], [], [], [], 0
    for b, bo in bproc.items():
        gb = [g for g, (c, n, o) in groups.items() if len(o & bo) >= 3 and len(o & bo) >= 0.3 * len(o)]
        if len({groups[g][0] for g in gb}) < 2:
            continue
        den.append(b)
        fin = Counter(final_of[g] for g in gb if g in final_of)
        cross = [f for f in fin if len({groups[g][0] for g in gb if final_of.get(g) == f}) >= 2]
        seps += max(0, len(fin) - 1)
        (joined if cross else nonj).append(b)
        full.append(b) if len(fin) == 1 else None
    rep["joins"] = {"denominator_baseline_processes_spanning_chunks": len(den), "joined_at_least_once": len(joined), "fully_joined": len(full),
                    "not_joined": len(nonj), "join_rate_percent": round(100 * len(joined) / max(1, len(den)), 1), "separations": seps,
                    "merger_joined_groups": sum(len(p.get("parts", [])) for p in m["processes"] if p.get("made_by") == "merged"),
                    "merged_processes": sum(p.get("made_by") == "merged" for p in m["processes"]), "chunk_groups": len(groups)}
    print("JOINS", rep["joins"])
    print("   denominator:", [(b, bname[b][:44]) for b in den])
    print("   not joined :", [(b, bname[b][:44]) for b in nonj])
    dom = {}
    for p in m["processes"]:
        c = Counter(base_of.get(o) for o in p["obs_ids"] if base_of.get(o))
        if c and c.most_common(1)[0][1] >= 0.4 * len(p["obs_ids"]):
            dom[p["process_id"]] = c.most_common(1)[0][0]
    rep["duplicates_same_dominant_baseline_process"] = {bname[b][:44]: n for b, n in Counter(dom.values()).items() if n > 1}
    print("DUPLICATES (final processes sharing a dominant baseline process):", rep["duplicates_same_dominant_baseline_process"])
    print("== MERGED PROCESSES (final | members: group name [chunk, observations])")
    for p in m["processes"]:
        if p.get("made_by") == "merged":
            print(f"  {p['name'][:52]:52s} | " + " + ".join(f"{groups[g][1][:42]} [{groups[g][0]}, {len(groups[g][2])}]" for g in p["parts"] if g in groups))

    # ---- new versus continuation
    cont, new, mixed = [], [], []
    for p in m["processes"]:
        o = p["obs_ids"]
        c = Counter(base_of.get(x) for x in o if base_of.get(x))
        top = c.most_common(1)[0][1] / len(o) if c else 0
        (cont if top >= 0.5 else new if sum(1 for x in o if x not in base_of) / len(o) >= 0.7 else mixed).append(p)
    ev_by = Counter(e["process_id"] for e in e_ev)
    rep["classes"] = {"continuation_of_a_baseline_process": len(cont), "mostly_new_observations": len(new), "mixed": len(mixed),
                      "events_in_new": sum(ev_by[p["process_id"]] for p in new), "events_in_continuation": sum(ev_by[p["process_id"]] for p in cont),
                      "events_in_mixed": sum(ev_by[p["process_id"]] for p in mixed),
                      "new_threads_vs_baseline": len(set().union(*e_thr.values()) - set().union(*b_thr.values())),
                      "threads_in_two_or_more_processes": sum(1 for t, n in Counter(t for v in e_thr.values() for t in v).items() if n > 1)}
    print("CLASSES", rep["classes"])
    print("== MOSTLY-NEW AND MIXED PROCESSES (events | obs | name)")
    for p in sorted(new + mixed, key=lambda p: -ev_by[p["process_id"]])[:30]:
        print(f"  {ev_by[p['process_id']]:3d} ev {len(p['obs_ids']):3d} obs | {p['name'][:90]}")
    words = lambda n: {w for w in n.lower().replace("-", " ").split() if len(w) > 3}
    rep["near_duplicate_name_pairs"] = [(x["name"][:44], y["name"][:44]) for i, x in enumerate(m["processes"]) for y in m["processes"][i + 1:]
                                        if len(words(x["name"]) & words(y["name"])) >= 3
                                        and len(words(x["name"]) & words(y["name"])) / len(words(x["name"]) | words(y["name"])) >= 0.5]
    print("NEAR-DUPLICATE NAME PAIRS:", rep["near_duplicate_name_pairs"])

    # ---- a reproducible sample of newly assigned observations, for a person to judge the fit (extractor wording, not email text)
    newly = sorted(o for o in m["assignment"] if o not in base_of)
    pname = {p["process_id"]: p["name"] for p in m["processes"]}
    random.Random(24112026).shuffle(newly)
    rep["sample_newly_assigned"] = [{"obs_id": o, "process": pname[m["assignment"][o]], "task": synth._clip(obs[o]["task"], 130)} for o in newly[:a.sample]]
    rep["newly_assigned_total"] = len(newly)
    print(f"== SAMPLE of {len(rep['sample_newly_assigned'])} of {len(newly)} newly assigned observations (process | task)")
    for s in rep["sample_newly_assigned"]:
        print(f"  {s['process'][:44]:44s} | {s['task']}")

    # ---- runtime, tokens
    sess = exp["sessions"]
    rep["runtime_tokens"] = {"wall_s": exp["wall_s"], "chunk_stage_wall_s": exp["chunk_stage_wall_s"], "merge_wall_s": exp.get("merge_wall_s"),
                             "sessions": len(sess), "failed_attempts": sum(1 for s in sess if s["status"] != "verified"),
                             "subagent_tokens": sum(s["subagent_tokens"] or 0 for s in sess),
                             "output_tokens": sum((s["tokens"] or {}).get("output_tokens") or 0 for s in sess),
                             "seconds_per_session": [(s["input"], s["status"], s["seconds"]) for s in sess]}
    print("RUNTIME/TOKENS", rep["runtime_tokens"])
    rep["chunk_coverage"] = [(s["input"], s["status"], (s["coverage"] or {}).get("share"), s["processes"]) for s in sess]
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
