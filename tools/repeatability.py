r"""Repeatability between two runs of the same pipeline (user, 24 Nov 2026, milestone 4). Code only; no model call; nothing is changed.

Usage:
  .venv\Scripts\python tools\repeatability.py deterministic --a ROOT_A --b ROOT_B [--slice all]
  .venv\Scripts\python tools\repeatability.py extraction    --a ROOT_A --b ROOT_B [--slice all]
  .venv\Scripts\python tools\repeatability.py synthesis     --a ROOT_A --a-synth PATH --b ROOT_B --b-synth PATH [--mailbox NAME ...] [--label x]

A run is a repository root with its `data/` (the cold copy is one, the working tree is another). Results go to eval/results/m6_repeat/ of the root this
file lives in.

deterministic: the stages that are code (ingest ledger, batches) must be byte-identical between independent runs (hard).
extraction:   the same messages read twice by the model. Observations are matched within a message by quote span (intersection over union of the byte
              ranges, at least 0.5); reports message-level and thread-level agreement and the threads one run found and the other missed.
synthesis:    the same observations (or the same mailbox read twice) grouped into processes twice. Compares the thread sets counted toward a recurring
              process, matches processes one to one by the threads they share, and rescored events and dollars per mailbox.

Acceptance criteria (PROVISIONAL, set here for review):
  R1 deterministic stages identical (hard);   R2 message-level agreement on 'has a verified observation' >= 90 percent;
  R3 thread-level Jaccard of threads with an observation >= 0.80;   R4 observation-level Jaccard >= 0.60;
  R5 per mailbox, thread-level Jaccard of threads counted toward a process >= 0.60 and the events within 25 percent;
  R6 at least 70 percent of the events sit in processes with a counterpart (>= 0.30 thread Jaccard) in the other run.
"""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "eval" / "results" / "m6_repeat"


def jac(a, b):
    return len(a & b) / len(a | b) if (a or b) else 1.0


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_obs(root: Path, slice_name: str) -> dict[str, dict]:
    out = {}
    for f in sorted((root / "data" / "runs" / slice_name / "ground").glob("b*.json")):
        for o in json.loads(f.read_text(encoding="utf-8"))["observations"]:
            out[o["obs_id"]] = o
    return out


def thread_map(root: Path) -> dict[str, tuple[str, str]]:
    sys.path.insert(0, str(root))
    for m in [m for m in sys.modules if m == "src" or m.startswith("src.")]:
        del sys.modules[m]
    from src import store
    st = store.load()
    return {k: (m.thread_id, m.mailbox) for k, m in st.messages.items()}


def deterministic(a: Path, b: Path, slice_name: str) -> dict:
    files = {"ledger": ("data/ledger.csv",), "batch manifest": (f"data/batches/{slice_name}/manifest.json",)}
    rep = {}
    for name, (rel,) in files.items():
        pa, pb = a / rel, b / rel
        rep[name] = {"a": sha(pa)[:16] if pa.exists() else None, "b": sha(pb)[:16] if pb.exists() else None,
                     "identical": pa.exists() and pb.exists() and sha(pa) == sha(pb)}
    import sqlite3
    h = []
    for r in (a, b):
        con = sqlite3.connect(r / "data" / "ingest.sqlite")
        rows = con.execute("SELECT key, thread_id, mailbox FROM messages ORDER BY key").fetchall()
        con.close()
        h.append((hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16], len(rows)))
    rep["ingest messages (key, thread, mailbox)"] = {"a": h[0], "b": h[1], "identical": h[0] == h[1]}
    rep["verdict"] = "pass" if all(v["identical"] for v in rep.values() if isinstance(v, dict)) else "FAIL"
    return rep


def extraction(a: Path, b: Path, slice_name: str) -> dict:
    oa, ob = load_obs(a, slice_name), load_obs(b, slice_name)
    tm = thread_map(a)
    by_msg = lambda obs: defaultdict(list, {}) if not obs else None  # noqa: E731
    ma, mb_ = defaultdict(list), defaultdict(list)
    for o in oa.values():
        ma[o["msg_key"]].append(o)
    for o in ob.values():
        mb_[o["msg_key"]].append(o)
    matched_pairs = 0
    for k in set(ma) & set(mb_):
        used = set()
        for x in ma[k]:
            best, bi = 0.0, None
            for i, y in enumerate(mb_[k]):
                if i in used:
                    continue
                inter = max(0, min(x["end"], y["end"]) - max(x["start"], y["start"]))
                union = (x["end"] - x["start"]) + (y["end"] - y["start"]) - inter
                iou = inter / union if union else 0
                if iou > best:
                    best, bi = iou, i
            if best >= 0.5:
                used.add(bi)
                matched_pairs += 1
    msgs_all = set(tm)
    A, B = set(ma), set(mb_)
    ta = {tm[k][0] for k in A if k in tm}
    tb = {tm[k][0] for k in B if k in tm}
    per_mb = {}
    for mbx in sorted({v[1] for v in tm.values()}):
        ka = {k for k in A if tm.get(k, ("", ""))[1] == mbx}
        kb = {k for k in B if tm.get(k, ("", ""))[1] == mbx}
        tha = {tm[k][0] for k in ka}
        thb = {tm[k][0] for k in kb}
        per_mb[mbx] = {"messages_with_obs_a": len(ka), "messages_with_obs_b": len(kb), "message_jaccard": round(jac(ka, kb), 3),
                       "thread_jaccard": round(jac(tha, thb), 3), "threads_only_a": len(tha - thb), "threads_only_b": len(thb - tha)}
    agree = len(msgs_all) - len(A ^ B)
    rep = {"observations_a": len(oa), "observations_b": len(ob), "matched_observation_pairs": matched_pairs,
           "observation_jaccard": round(matched_pairs / (len(oa) + len(ob) - matched_pairs), 3) if oa or ob else 1.0,
           "messages_with_obs_a": len(A), "messages_with_obs_b": len(B), "messages_both": len(A & B),
           "message_level_agreement": round(agree / len(msgs_all), 3), "message_jaccard": round(jac(A, B), 3),
           "threads_with_obs_a": len(ta), "threads_with_obs_b": len(tb), "thread_jaccard": round(jac(ta, tb), 3),
           "threads_found_only_by_a": len(ta - tb), "threads_found_only_by_b": len(tb - ta), "per_mailbox": per_mb,
           "examples_only_a": sorted(ta - tb)[:8], "examples_only_b": sorted(tb - ta)[:8]}
    rep["criteria"] = {"R2 message-level agreement >= 0.90": "pass" if rep["message_level_agreement"] >= 0.90 else "fail",
                       "R3 thread Jaccard >= 0.80": "pass" if rep["thread_jaccard"] >= 0.80 else "fail",
                       "R4 observation Jaccard >= 0.60": "pass" if rep["observation_jaccard"] >= 0.60 else "fail"}
    return rep


def run_scores(root: Path, slice_name: str, synth_path: Path, mailboxes: list[str] | None) -> dict:
    sys.path.insert(0, str(root))
    for m in [m for m in sys.modules if m == "src" or m.startswith("src.")]:
        del sys.modules[m]
    from src import events, pipeline, score, store
    st = store.load()
    res = json.loads(synth_path.read_text(encoding="utf-8"))
    obs = [o for o in load_obs(root, slice_name).values() if not mailboxes or st.messages[o["msg_key"]].mailbox in mailboxes]
    ids = {o["obs_id"] for o in obs}
    assign = {k: v for k, v in res["assignment"].items() if k in ids}
    procs = [dict(p, obs_ids=[o for o in p["obs_ids"] if o in ids]) for p in res["processes"]]
    procs = [p for p in procs if p["obs_ids"]]
    r = pipeline.Run(slice_name, root=root)
    seen = events.seen_in_mailboxes(root / "data" / "ingest.sqlite")
    evs = events.build(obs, assign, st, seen)
    win = {m: (datetime.fromisoformat(x), datetime.fromisoformat(y)) for m, x, y in pipeline._windows()}
    qids, _ = pipeline.quote_ids(obs)
    sc = score.score(procs, evs, win, r.cfg["score"], qids)
    scored = [o for o in sc["opportunities"] if o.get("status") == "scored"]
    pth = defaultdict(set)
    for e in evs:
        pth[e["process_id"]].update(e["threads"])
    return {"events": len(evs), "processes": len(procs), "scored": len(scored), "dollars": Decimal(sc["claims"]["total"]["value"]),
            "threads": {t for v in pth.values() for t in v}, "proc_threads": dict(pth), "names": {p["process_id"]: p["name"] for p in procs},
            "events_by_process": {pid: len([e for e in evs if e["process_id"] == pid]) for pid in pth}}


def synthesis(a: Path, asy: Path, b: Path, bsy: Path, slice_name: str, mailboxes: list[str] | None) -> dict:
    A = run_scores(a, slice_name, asy, mailboxes)
    B = run_scores(b, slice_name, bsy, mailboxes)
    # one-to-one process matching by shared threads (greedy by overlap size)
    cand = sorted(((len(A["proc_threads"][x] & B["proc_threads"][y]), x, y) for x in A["proc_threads"] for y in B["proc_threads"]), reverse=True)
    used_a, used_b, match = set(), set(), []
    for n, x, y in cand:
        if n == 0:
            break
        if x in used_a or y in used_b:
            continue
        j = jac(A["proc_threads"][x], B["proc_threads"][y])
        if j >= 0.30:
            used_a.add(x), used_b.add(y)
            match.append((x, y, n, round(j, 2)))
    ev_a = sum(A["events_by_process"][x] for x in used_a)
    ev_b = sum(B["events_by_process"][y] for y in used_b)
    tot_a, tot_b = sum(A["events_by_process"].values()), sum(B["events_by_process"].values())
    rep = {"mailboxes": mailboxes, "a": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in A.items() if k in ("events", "processes", "scored", "dollars")},
           "b": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in B.items() if k in ("events", "processes", "scored", "dollars")},
           "threads_counted_a": len(A["threads"]), "threads_counted_b": len(B["threads"]), "thread_jaccard": round(jac(A["threads"], B["threads"]), 3),
           "events_ratio_b_over_a": round(B["events"] / A["events"], 3) if A["events"] else None,
           "dollars_ratio_b_over_a": round(float(B["dollars"] / A["dollars"]), 3) if A["dollars"] else None,
           "matched_processes": len(match), "processes_a_unmatched": len(A["proc_threads"]) - len(match), "processes_b_unmatched": len(B["proc_threads"]) - len(match),
           "share_of_a_events_in_matched_processes": round(ev_a / tot_a, 3) if tot_a else None, "share_of_b_events_in_matched_processes": round(ev_b / tot_b, 3) if tot_b else None,
           "matches": [{"a": A["names"][x][:50], "b": B["names"][y][:50], "shared_threads": n, "jaccard": j} for x, y, n, j in sorted(match, key=lambda m: -m[2])[:12]],
           "unmatched_a_largest": sorted(((A["events_by_process"][x], A["names"][x][:56]) for x in A["proc_threads"] if x not in used_a), reverse=True)[:8],
           "unmatched_b_largest": sorted(((B["events_by_process"][y], B["names"][y][:56]) for y in B["proc_threads"] if y not in used_b), reverse=True)[:8]}
    ratio = rep["events_ratio_b_over_a"]
    rep["criteria"] = {"R5 thread Jaccard >= 0.60 and events within 25 percent": "pass" if rep["thread_jaccard"] >= 0.60 and ratio and abs(ratio - 1) <= 0.25 else "fail",
                       "R6 >= 70 percent of events in matched processes (both runs)": "pass" if (rep["share_of_a_events_in_matched_processes"] or 0) >= 0.70
                       and (rep["share_of_b_events_in_matched_processes"] or 0) >= 0.70 else "fail"}
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["deterministic", "extraction", "synthesis"])
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--slice", default="all")
    ap.add_argument("--a-synth")
    ap.add_argument("--b-synth")
    ap.add_argument("--mailbox", nargs="*")
    ap.add_argument("--label", default="")
    x = ap.parse_args(argv)
    a, b = Path(x.a).resolve(), Path(x.b).resolve()
    if x.what == "deterministic":
        rep = deterministic(a, b, x.slice)
    elif x.what == "extraction":
        rep = extraction(a, b, x.slice)
    else:
        rep = synthesis(a, Path(x.a_synth), b, Path(x.b_synth), x.slice, x.mailbox)
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"{x.what}{('_' + x.label) if x.label else ''}.json"
    (OUT / name).write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k not in ("matches", "unmatched_a_largest", "unmatched_b_largest", "per_mailbox", "examples_only_a", "examples_only_b")}, default=str, indent=1))
    for k in ("matches", "unmatched_a_largest", "unmatched_b_largest", "per_mailbox"):
        if k in rep:
            print(k, json.dumps(rep[k], default=str)[:1800])
    print("written", OUT / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
