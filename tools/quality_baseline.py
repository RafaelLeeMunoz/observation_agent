r"""Process-discovery quality baseline (user, 24 Nov 2026, milestone 1). Code only; no model call; nothing is changed.

Usage: .venv\Scripts\python tools\quality_baseline.py [--root DIR] [--slice all] [--synth PATH] [--tag baseline] [--out DIR] [--precision-fit N/M]

Measures one synthesis result (default `data/runs/<slice>/synth.json`) against the verified observations behind it (`data/runs/<slice>/ground/`)
and the raw corpus. Every number is computed here from stored files; the judgments (are two near-duplicate processes really one, is a missed
cluster real work, does a sampled observation fit its process) are read by a person from the lists written beside the numbers and entered with
--precision-fit. Acceptance criteria are the CRITERIA below: HARD ones must pass; PROVISIONAL ones are this tool's proposal, to be reviewed.

  C1 evidence integrity (hard): every observation behind a process exists, is assigned once, and its quote re-reads byte-exact at its offsets.
  C2 positional completeness (hard): in every mailbox every third of its date-ordered observations has at least 5 percent counted.
  C3 recurring-work capture (provisional): at least 25 percent of all observations are counted toward a process of two or more observations.
  C4 missed clusters (provisional): unassigned observations that form hint clusters of 8 or more observations in 4 or more threads
     are at most 10 percent of the unassigned observations.
  C5 fragmentation (provisional): process pairs whose names or descriptions are near-identical are at most 5 percent of the processes.
  C6 over-merge (provisional): no process holds more than 25 percent of a mailbox's observations.
  C7 precision sample (provisional): at least 85 percent of a seeded sample of assigned observations fit their process (read by a person).
"""
import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT0 = Path(__file__).resolve().parent.parent
STOP = set("the and for with from that this into about after before onto over under each their there been have will would should when where which "
           "what your you our are was were not but its his her any all per via too out off may can".split())


def toks(s: str) -> frozenset:
    out = set()
    for w in re.findall(r"[a-z]{3,}", (s or "").lower()):
        if w in STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.add(w)
    return frozenset(out)


def jac(a, b) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def clusters(items: dict[str, frozenset], thr: float, min_tokens: int = 2, max_post: int = 250) -> list[list[str]]:
    """Single-link clusters of items whose token sets have Jaccard >= thr (candidates found through rare shared tokens)."""
    keys = [k for k, t in items.items() if len(t) >= min_tokens]
    post = defaultdict(list)
    for k in keys:
        for t in items[k]:
            post[t].append(k)
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for k in keys:
        cand = Counter(o for t in items[k] if len(post[t]) <= max_post for o in post[t] if o > k)
        for o, shared in cand.items():
            if shared >= 2 and jac(items[k], items[o]) >= thr:
                parent[find(o)] = find(k)
    groups = defaultdict(list)
    for k in keys:
        groups[find(k)].append(k)
    return sorted(groups.values(), key=len, reverse=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT0))
    ap.add_argument("--slice", default="all")
    ap.add_argument("--synth")
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--out")
    ap.add_argument("--precision-fit", help="N/M from a person's reading of precision_sample.md")
    ap.add_argument("--sample", type=int, default=30)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    sys.path.insert(0, str(root))
    from src import rawio, store  # noqa: E402
    st = store.load()
    synth_path = Path(a.synth) if a.synth else root / "data" / "runs" / a.slice / "synth.json"
    res = json.loads(synth_path.read_text(encoding="utf-8"))
    obs = {}
    for f in sorted((root / "data" / "runs" / a.slice / "ground").glob("b*.json")):
        for o in json.loads(f.read_text(encoding="utf-8"))["observations"]:
            obs[o["obs_id"]] = o
    mb = {k: st.messages[o["msg_key"]].mailbox for k, o in obs.items()}
    th = {k: st.messages[o["msg_key"]].thread_id for k, o in obs.items()}
    procs = res["processes"]
    assign = {}
    for p in procs:
        for o in p["obs_ids"]:
            assign.setdefault(o, []).append(p["process_id"])
    counted = {o for p in procs if len(set(p["obs_ids"])) >= 2 and not p.get("candidate") for o in p["obs_ids"]}
    rep = {"synth": str(synth_path), "tag": a.tag, "observations": len(obs), "processes": len(procs), "assigned": len(assign),
           "counted": len(counted), "counted_share": round(len(counted) / max(1, len(obs)), 4)}

    # C1 evidence integrity
    bad = {"unknown_ids": [o for o in assign if o not in obs], "assigned_twice": [o for o, v in assign.items() if len(v) > 1], "quote_mismatch": []}
    raw = {}
    for o in assign:
        if o not in obs:
            continue
        x = obs[o]
        text = raw.setdefault(x["msg_key"], rawio.read_raw(root / "corpus" / f"{x['msg_key']}.txt"))
        if text[x["start"]:x["end"]] != x["quote_raw"]:
            bad["quote_mismatch"].append(o)
    rep["C1_evidence"] = {k: len(v) for k, v in bad.items()} | {"quotes_reread": len(assign)}

    # C2 positional completeness, per mailbox, thirds of its date-ordered observations
    order = lambda o: (obs[o].get("evidence_date") or "9999", o)
    pos = {}
    for m in sorted(set(mb.values())):
        ids = sorted((o for o in obs if mb[o] == m), key=order)
        n = len(ids)
        th3 = [round(100 * sum(o in counted for o in ids[j * n // 3:(j + 1) * n // 3]) / max(1, len(ids[j * n // 3:(j + 1) * n // 3])), 1) for j in range(3)]
        pos[m] = {"observations": n, "counted_percent": round(100 * sum(o in counted for o in ids) / max(1, n), 1), "thirds_counted_percent": th3,
                  "decay_warning": min(th3) < 0.25 * max(th3), "gap": min(th3) < 5}
    rep["C2_positional"] = pos

    # C4 missed clusters among the unassigned (by the extractor's own process hint, independent of the synthesizer)
    un = [o for o in obs if o not in counted]
    hint = {o: toks(obs[o].get("process_hint", "")) for o in un}
    cl = []
    for g in clusters(hint, 0.5):
        t = {th[o] for o in g}
        if len(g) >= 8 and len(t) >= 4:
            cl.append({"observations": len(g), "threads": len(t), "mailboxes": sorted({mb[o] for o in g}),
                       "hints": [obs[o]["process_hint"] for o in g[:3]], "tasks": [obs[o]["task"][:90] for o in g[:2]], "obs_ids": g[:12]})
    rep["C4_missed_clusters"] = {"unassigned": len(un), "clusters": len(cl), "observations_in_clusters": sum(c["observations"] for c in cl),
                                 "share_of_unassigned": round(sum(c["observations"] for c in cl) / max(1, len(un)), 4), "top": cl[:15]}

    # C5 fragmentation: near-identical process pairs, and hint clusters spread over several processes
    text = {p["process_id"]: toks(p["name"]) for p in procs}
    full = {p["process_id"]: toks(" ".join([p["name"], p.get("description", ""), p.get("trigger", ""), p.get("output", "")])) for p in procs}
    pairs = []
    ids = [p["process_id"] for p in procs]
    pname = {p["process_id"]: p["name"] for p in procs}
    pmb = {p["process_id"]: sorted({mb[o] for o in p["obs_ids"] if o in mb}) for p in procs}
    for i, x in enumerate(ids):
        for y in ids[i + 1:]:
            jn, jf = jac(text[x], text[y]), jac(full[x], full[y])
            if jn >= 0.5 or jf >= 0.4:
                pairs.append({"a": pname[x], "b": pname[y], "name_jaccard": round(jn, 2), "full_jaccard": round(jf, 2), "mailboxes": [pmb[x], pmb[y]]})
    pairs.sort(key=lambda r: -max(r["name_jaccard"], r["full_jaccard"]))
    allhint = {o: toks(obs[o].get("process_hint", "")) for o in obs}
    spread = []
    for g in clusters(allhint, 0.6):
        t = {th[o] for o in g}
        if len(g) < 8 or len(t) < 4:
            continue
        c = Counter(assign[o][0] for o in g if o in assign)
        if len(c) >= 3 and c.most_common(1)[0][1] < 0.6 * sum(c.values()):
            spread.append({"observations": len(g), "processes": len(c), "hint": obs[g[0]]["process_hint"][:70],
                           "split": [(pname[p][:44], n) for p, n in c.most_common(4)]})
    rep["C5_fragmentation"] = {"near_duplicate_pairs": len(pairs), "share_of_processes": round(len(pairs) / max(1, len(procs)), 4), "pairs": pairs[:20],
                               "hint_clusters_spread_over_3_plus_processes": len(spread), "spread": spread[:10]}

    # C6 over-merge: share of a mailbox held by one process, and low-purity processes
    big = []
    for p in procs:
        c = Counter(mb[o] for o in p["obs_ids"] if o in mb)
        for m, n in c.items():
            share = n / pos[m]["observations"]
            if share > 0.25:
                big.append({"process": p["name"], "mailbox": m, "share": round(share, 3)})
    pur = []
    for p in procs:
        if len(p["obs_ids"]) < 8:
            continue
        sets = [toks(obs[o]["task"]) for o in p["obs_ids"] if o in obs]
        cen = Counter(t for s in sets for t in s)
        top = {t for t, n in cen.most_common(8)}
        pur.append((round(sum(len(s & top) / max(1, len(s)) for s in sets) / len(sets), 3), p["name"], len(sets)))
    pur.sort()
    rep["C6_overmerge"] = {"processes_over_25_percent_of_a_mailbox": big, "lowest_purity": [{"purity": x, "process": n, "observations": k} for x, n, k in pur[:8]]}

    # structure and stalls
    size = sorted((len(set(p["obs_ids"])) for p in procs), reverse=True)
    stalled = sum(1 for p in procs if any(obs[o]["stall_signals"] for o in p["obs_ids"] if o in obs))
    rep["structure"] = {"largest_processes": size[:5], "processes_of_2_to_4": sum(2 <= s <= 4 for s in size), "processes_of_5_plus": sum(s >= 5 for s in size),
                        "single_observation_processes": sum(s < 2 for s in size), "cross_mailbox_processes": sum(len(v) > 1 for v in pmb.values()),
                        "processes_with_a_published_stall_signal": stalled, "threads_in_processes": len({th[o] for o in assign if o in th})}

    # C7 precision sample, seeded, for a person to read
    sample = sorted(o for o in counted if o in obs)
    random.Random(24112026).shuffle(sample)
    out = Path(a.out) if a.out else root / "eval" / "results" / "m6_baseline"
    out.mkdir(parents=True, exist_ok=True)
    md = ["# Precision sample (seeded; judge whether the observation belongs to the process it was assigned to)", "",
          "| # | observation | process | task | file |", "|---|---|---|---|---|"]
    pid_name = {p["process_id"]: p["name"] for p in procs}
    for i, o in enumerate(sample[:a.sample], start=1):
        md.append(f"| {i} | {o} | {pid_name[assign[o][0]][:60]} | {obs[o]['task'][:100]} | {obs[o]['original_path'].removeprefix('corpus/')} |")
    (out / f"precision_sample_{a.tag}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    rep["C7_precision"] = {"sample": min(a.sample, len(sample)), "file": f"precision_sample_{a.tag}.md", "fit": a.precision_fit}

    # verdicts
    hard1 = all(v == 0 for k, v in rep["C1_evidence"].items() if k != "quotes_reread")
    fit = tuple(int(x) for x in a.precision_fit.split("/")) if a.precision_fit else None
    rep["criteria"] = {
        "C1 evidence integrity (hard)": "pass" if hard1 else "FAIL",
        "C2 positional completeness (hard)": "pass" if not any(v["gap"] for v in pos.values()) else "FAIL",
        "C3 counted share >= 25 percent (provisional)": "pass" if rep["counted_share"] >= 0.25 else "fail",
        "C4 missed clusters <= 10 percent of unassigned (provisional)": "pass" if rep["C4_missed_clusters"]["share_of_unassigned"] <= 0.10 else "fail",
        "C5 near-duplicate pairs <= 5 percent of processes (provisional)": "pass" if rep["C5_fragmentation"]["share_of_processes"] <= 0.05 else "fail",
        "C6 no process over 25 percent of a mailbox (provisional)": "pass" if not big else "fail",
        "C7 precision sample >= 85 percent (provisional)": (("pass" if fit[0] / fit[1] >= 0.85 else "fail") + f" ({a.precision_fit})") if fit else "not read yet"}
    (out / f"quality_{a.tag}.json").write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("observations", "processes", "assigned", "counted", "counted_share")}))
    print("C1", rep["C1_evidence"])
    for m, v in pos.items():
        print("C2", m, v)
    c4 = rep["C4_missed_clusters"]
    print("C4", {k: c4[k] for k in ("unassigned", "clusters", "observations_in_clusters", "share_of_unassigned")})
    for c in c4["top"][:10]:
        print(f"   {c['observations']:3d} obs / {c['threads']:3d} threads {c['mailboxes']} | {c['hints'][0][:60]} | {c['tasks'][0][:70]}")
    c5 = rep["C5_fragmentation"]
    print("C5", {k: c5[k] for k in ("near_duplicate_pairs", "share_of_processes", "hint_clusters_spread_over_3_plus_processes")})
    for r in c5["pairs"][:12]:
        print(f"   J {r['name_jaccard']}/{r['full_jaccard']} | {r['a'][:50]} || {r['b'][:50]}")
    for r in c5["spread"][:6]:
        print(f"   spread {r['observations']} obs over {r['processes']} processes | {r['hint']} | {r['split']}")
    print("C6", rep["C6_overmerge"]["processes_over_25_percent_of_a_mailbox"], [(x["purity"], x["process"][:40]) for x in rep["C6_overmerge"]["lowest_purity"][:5]])
    print("structure", rep["structure"])
    for k, v in rep["criteria"].items():
        print(f"  {v:>22s}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
