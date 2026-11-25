r"""Analysis of the farmer-d three-chunk date-split experiment against the baseline whole-mailbox result (code only, no model).

Usage: .venv\Scripts\python tools\split_experiment_report.py

Reads data/runs/all/synth/{s001 (baseline), s201-s203 (chunks), m201 (merge)}.json and eval/results/m5_split_experiment/experiment.json;
writes eval/results/m5_split_experiment/report.json. Changes nothing else. Joins use the baseline processes as the reference identity (the
only independent one available): a baseline process whose observations sit, 3 or more each (and 30 percent of that group), in groups of
two or more chunks is a 'recurring process spanning chunks'; a join is correct when the merger put two or more of those groups in one final
process. Whether a join is right in meaning, and whether a new process is a real discovery, is read by a person from the lists it prints.
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import events, pipeline, score, store  # noqa: E402

OUT = ROOT / "eval" / "results" / "m5_split_experiment"
r, st = pipeline.Run("all"), store.load()
obs = {}
for f in sorted((r.data / "ground").glob("b*.json")):
    for o in json.loads(f.read_text(encoding="utf-8"))["observations"]:
        if st.messages[o["msg_key"]].mailbox == "farmer-d":
            obs[o["obs_id"]] = o
fd = list(obs.values())
J = lambda p: json.loads((r.data / "synth" / p).read_text(encoding="utf-8"))
base, m, chunks = J("s001.json"), J("m201.json"), {k: J(f"s20{k}.json") for k in (1, 2, 3)}
exp = json.loads((OUT / "experiment.json").read_text(encoding="utf-8"))
thread = lambda o: st.messages[obs[o]["msg_key"]].thread_id
seen = events.seen_in_mailboxes()
rep = {"baseline_processes": len(base["processes"]), "experiment_processes": len(m["processes"])}


def summarise(res):
    evs = events.build(fd, res["assignment"], st, seen)
    thr = defaultdict(set)
    for o, p in res["assignment"].items():
        thr[p].add(thread(o))
    win = {mb: (datetime.fromisoformat(a), datetime.fromisoformat(z)) for mb, a, z in pipeline._windows()}
    qids, _ = pipeline.quote_ids(fd)
    sc = score.score(res["processes"], evs, win, r.cfg["score"], qids)
    scored = [o for o in sc["opportunities"] if o.get("status") == "scored"]
    return {"processes": len(res["processes"]), "assigned_obs": len(res["assignment"]), "events_build": len(evs), "events_sum_of_process_threads": sum(len(v) for v in thr.values()),
            "scored_processes": len(scored), "events_in_scored_processes": sum(o["events"] for o in scored), "threads_covered": len({t for v in thr.values() for t in v}),
            "total_dollars_per_month": str(sc["claims"]["total"]["value"])[:10]}, evs, thr


bs, b_ev, b_thr = summarise(base)
es, e_ev, e_thr = summarise(m)
rep["baseline"], rep["experiment"] = bs, es
rep["criterion_1_events"] = {k: {"baseline": bs[k], "experiment": es[k], "change_percent": round(100 * (es[k] - bs[k]) / bs[k], 1)}
                             for k in ("events_build", "events_sum_of_process_threads", "events_in_scored_processes", "assigned_obs")}
print("BASELINE", bs)
print("EXPERIMENT", es)
print("C1", rep["criterion_1_events"])

# ---- joins (reference: the baseline processes)
base_of = base["assignment"]
groups = {}                                                   # group id -> (chunk, name, obs set)
for k, res in chunks.items():
    for p in res["processes"]:
        groups[f"s20{k}.{p['process_id']}"] = (k, p["name"], set(p["obs_ids"]))
final_of, final_name = {}, {}
for p in m["processes"]:
    final_name[p["process_id"]] = p["name"]
    for g in p.get("parts", []):
        final_of[g] = p["process_id"]
bproc = {p["process_id"]: set(p["obs_ids"]) for p in base["processes"]}
bname = {p["process_id"]: p["name"] for p in base["processes"]}
den, joined, full, nonj, seps = [], [], [], [], 0
for b, bo in bproc.items():
    gb = [g for g, (k, n, o) in groups.items() if len(o & bo) >= 3 and len(o & bo) >= 0.3 * len(o)]
    if len({groups[g][0] for g in gb}) < 2:
        continue
    den.append(b)
    fin = Counter(final_of[g] for g in gb)
    cross = [f for f, c in fin.items() if len({groups[g][0] for g in gb if final_of[g] == f}) >= 2]
    seps += len(fin) - 1
    (joined if cross else nonj).append(b)
    if len(fin) == 1:
        full.append(b)
rep["criterion_2_joins"] = {"denominator_baseline_processes_spanning_2plus_chunks": len(den), "joined_at_least_once": len(joined), "fully_joined": len(full),
                            "not_joined": len(nonj), "join_rate_percent": round(100 * len(joined) / max(1, len(den)), 1),
                            "missed_joins_separations": seps,
                            "limitation": "The baseline's processes lie entirely in chunk 1 (it assigned nothing from the other two thirds), so this baseline-identity denominator is 0 "
                                          "and carries no information. The join counts that are reported (docs/M5_SPLIT_EXPERIMENT.md) are a reader's judgment of the merged "
                                          "processes: 17 same-theme joins of 18 (1 missed, 0 clear incorrect and 2 borderline, 0 clear duplicates)."}
print("C2", rep["criterion_2_joins"])
print("   denominator:", [(b, bname[b][:40]) for b in den])
print("   not joined:", [(b, bname[b][:40]) for b in nonj])
# duplicates: more than one final process whose dominant baseline process is the same one
dom = {}
for p in m["processes"]:
    c = Counter(base_of.get(o) for o in p["obs_ids"] if base_of.get(o))
    if c:
        b, n = c.most_common(1)[0]
        if n >= 0.4 * len(p["obs_ids"]):
            dom[p["process_id"]] = b
dups = Counter(dom.values())
rep["duplicates_same_dominant_baseline_process"] = {bname[b][:40]: n for b, n in dups.items() if n > 1}
print("   duplicates (final processes sharing a dominant baseline process):", rep["duplicates_same_dominant_baseline_process"])
# merged processes: the members, for a human to judge
print("== MERGED PROCESSES (final | members: group name [chunk, obs]) and the baseline processes their members map to")
for p in m["processes"]:
    if p.get("made_by") == "merged":
        mem = [f"{groups[g][1][:42]} [c{groups[g][0]}, {len(groups[g][2])}]" for g in p["parts"] if g in groups]
        dm = [bname.get(Counter(base_of.get(o) for o in groups[g][2] if base_of.get(o)).most_common(1)[0][0], "-")[:22] if any(base_of.get(o) for o in groups[g][2]) else "-" for g in p["parts"] if g in groups]
        print(f"  {p['name'][:46]:46s} | " + " + ".join(mem) + f" | baseline: {dm}")

# ---- new versus continuation, and duplicates by name
fin_obs = {p["process_id"]: p["obs_ids"] for p in m["processes"]}
cont, new, mixed = [], [], []
for p in m["processes"]:
    o = p["obs_ids"]
    un = sum(1 for x in o if x not in base_of) / len(o)
    c = Counter(base_of.get(x) for x in o if base_of.get(x))
    top = c.most_common(1)[0][1] / len(o) if c else 0
    (cont if top >= 0.5 else new if un >= 0.7 else mixed).append(p)
ev_by = Counter(e["process_id"] for e in e_ev)
rep["criterion_3_classes"] = {"continuation_of_a_baseline_process": len(cont), "mostly_new_observations": len(new), "mixed": len(mixed),
                              "events_in_new": sum(ev_by[p["process_id"]] for p in new), "events_in_continuation": sum(ev_by[p["process_id"]] for p in cont),
                              "events_in_mixed": sum(ev_by[p["process_id"]] for p in mixed)}
print("C3", rep["criterion_3_classes"], "| new threads vs baseline:", len(set().union(*e_thr.values()) - set().union(*b_thr.values())))
print("== MOSTLY-NEW PROCESSES (events | obs | name)")
for p in sorted(new, key=lambda p: -ev_by[p["process_id"]])[:30]:
    print(f"  {ev_by[p['process_id']]:3d} ev {len(p['obs_ids']):3d} obs | {p['name'][:80]}")
words = lambda n: {w for w in n.lower().replace("-", " ").split() if len(w) > 3}
pairs = [(a["name"][:40], b["name"][:40]) for i, a in enumerate(m["processes"]) for b in m["processes"][i + 1:]
         if len(words(a["name"]) & words(b["name"])) >= 3 and len(words(a["name"]) & words(b["name"])) / len(words(a["name"]) | words(b["name"])) >= 0.5]
rep["near_duplicate_name_pairs"] = pairs
print("== NEAR-DUPLICATE NAME PAIRS among final processes:", pairs)

# ---- runtime, tokens
sess = exp["sessions"]
rep["runtime_tokens"] = {"wall_s": exp["wall_s"], "chunk_stage_wall_s": exp["chunk_stage_wall_s"], "merge_wall_s": exp.get("merge_wall_s"), "sessions": len(sess),
                         "failed_attempts": sum(1 for s in sess if s["status"] != "verified"), "subagent_tokens": sum(s["subagent_tokens"] or 0 for s in sess),
                         "output_tokens": sum((s["tokens"] or {}).get("output_tokens") or 0 for s in sess)}
print("RUNTIME/TOKENS", rep["runtime_tokens"], "| baseline: 149.6 s, 315,980 subagent tokens, 22,803 output tokens (one seeded attempt)")
rep["chunk_coverage"] = [(s["input"], s["status"], (s["coverage"] or {}).get("share"), s["processes"]) for s in sess]
(OUT / "report.json").write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
