r"""M4 step 2b: concurrency on real full-corpus batches, and the farmer-d synthesis comparison (PLAN 5.1, 2b).

    .venv\Scripts\python tools\concurrency_2b.py sets                    the production layout (soft cap) and the
                                                                         8 / 12 / 16 batch sets, written to
                                                                         eval/results/m4_concurrency/sets.json
    .venv\Scripts\python tools\concurrency_2b.py run A --workers 8       one set through the pipeline (extract and
                                                                         ground, never synthesis), measured
    .venv\Scripts\python tools\concurrency_2b.py compare-synth           farmer-d synthesized whole and as 2 date
                                                                         parts + merge, on the 2b observations

Every measured batch goes through the normal pipeline, so it is cached and reused by the full run (M5). A run is a
full-corpus model run: it needs the user's go-ahead and a fresh 5-hour window (PLAN 5.1); the plan meter is read by
the orchestrating session before and after and recorded in the DEVLOG. Spend: `run` one Haiku extractor and at most
one Haiku grounder per batch; `compare-synth` four Sonnet sessions. Results: eval/results/m4_concurrency/.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import events, pipeline, runmodel, store, synth  # noqa: E402

OUT = ROOT / "eval" / "results" / "m4_concurrency"
SIZES = {"A": 8, "B": 12, "C": 16}


# --- pure helpers (tested offline) ---

def pick_sets(batch_ids: list[str], sizes: dict[str, int] = SIZES) -> dict[str, list[str]]:
    """Disjoint sets of the given sizes, spread evenly over the whole id order (which follows date and mailbox), so
    each set mixes mailboxes and periods. Deterministic."""
    ids = sorted(batch_ids)
    total = sum(sizes.values())
    if total > len(ids):
        raise ValueError(f"{total} batches wanted, {len(ids)} exist")
    chosen = [ids[i * len(ids) // total] for i in range(total)]
    out = {name: [] for name in sizes}
    for j, bid in enumerate(chosen):                           # each position goes to the set furthest behind its
        name = max(sizes, key=lambda k: (sizes[k] * (j + 1) / total - len(out[k]), -list(sizes).index(k)))
        out[name].append(bid)                                  # even share so far: every set spans the whole corpus
    return {k: sorted(v) for k, v in out.items()}


def session_report(sessions: list[dict], messages: dict[str, int], model: dict, workers: int) -> dict:
    """Measured times per stage against the 2a time model (seconds = a + b * messages), start stagger, failure kinds."""
    def iso(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    out = {}
    for stage in ("extract", "ground"):
        ss = [s for s in sessions if s["stage"] == stage]
        if not ss:
            continue
        starts = sorted(iso(s["started_at_utc"]) for s in ss if s.get("started_at_utc"))
        first_wave = starts[:workers]
        rows = [{"batch": s["name"], "seconds": s["wall_seconds"], "messages": messages.get(s["name"]),
                 "kind": s["kind"], "output_tokens": s.get("output_tokens"), "self_checks": s.get("self_checks", 0)}
                for s in ss]
        if stage == "extract":
            for r in rows:
                if r["messages"] is not None:
                    r["predicted_s"] = round(model["a"] + model["b"] * r["messages"], 1)
        secs = [r["seconds"] for r in rows if r["seconds"] is not None]
        ratio = [r["seconds"] / r["predicted_s"] for r in rows if r.get("predicted_s") and r["seconds"]]
        out[stage] = {"sessions": len(ss), "kinds": dict(Counter(s["kind"] for s in ss)),
                      "seconds": {"min": min(secs), "max": max(secs), "mean": round(sum(secs) / len(secs), 1),
                                  "sum": round(sum(secs), 1)} if secs else None,
                      "first_wave_start_spread_s": (first_wave[-1] - first_wave[0]).total_seconds() if len(first_wave) > 1 else 0,
                      "measured_over_predicted": {"mean": round(sum(ratio) / len(ratio), 2), "max": round(max(ratio), 2)}
                      if ratio else None,
                      "self_checks": sum(r["self_checks"] for r in rows), "rows": rows}
    return out


def compare_processes(whole: dict, split: dict, observations: list[dict], st, seen: dict) -> dict:
    """Whole-mailbox vs split synthesis: process identity (best observation overlap), distinct events, evidence kept
    (verified quotes behind each process) and where each whole-mailbox process's observations went."""
    by_obs = {o["obs_id"]: o for o in observations}
    qids, _ = pipeline.quote_ids(observations)
    ev_w = events.build(observations, whole["assignment"], st, seen)
    ev_s = events.build(observations, split["assignment"], st, seen)
    n_events = lambda evs, pid: sum(e["process_id"] == pid for e in evs)
    quotes = lambda obs: sum(len(qids.get(o, [])) for o in obs)
    t_procs = {p["process_id"]: set(p["obs_ids"]) for p in split["processes"]}
    rows, matched = [], set()
    for p in whole["processes"]:
        w = set(p["obs_ids"])
        best = max(t_procs.items(), key=lambda kv: (len(w & kv[1]) / len(w | kv[1]), kv[0]), default=(None, set()))
        jac = len(w & best[1]) / len(w | best[1]) if best[0] else 0.0
        went = Counter(split["assignment"].get(o, "unassigned") for o in w)
        if jac >= 0.5:
            matched.add(best[0])
        rows.append({"whole": p["process_id"], "name": p["name"], "observations": len(w), "events": n_events(ev_w, p["process_id"]),
                     "quotes": quotes(w), "split": best[0], "jaccard": round(jac, 3),
                     "split_events": n_events(ev_s, best[0]) if best[0] else 0,
                     "split_quotes_shared": quotes(w & best[1]), "observations_went": dict(went.most_common()),
                     "recurring_whole": n_events(ev_w, p["process_id"]) >= 2,
                     "recurring_split": bool(best[0]) and n_events(ev_s, best[0]) >= 2})
    return {"whole_processes": len(whole["processes"]), "split_processes": len(split["processes"]),
            "recovered_at_half_overlap": sum(r["jaccard"] >= 0.5 for r in rows),
            "lost": [r for r in rows if r["jaccard"] < 0.5],
            "split_only": sorted(set(t_procs) - matched),
            "assigned": {"whole": len(whole["assignment"]), "split": len(split["assignment"])},
            "events_in_recurring_processes": {"whole": sum(r["events"] for r in rows if r["recurring_whole"]),
                                              "split": sum(n_events(ev_s, pid) for pid in t_procs if n_events(ev_s, pid) >= 2)},
            "rows": rows, "unknown_obs": sorted(o for o in whole["assignment"] if o not in by_obs)}


# --- live steps ---

def sampler(stop: threading.Event, out: list):
    """Every 5 s: the number of claude.exe processes and their working sets (tasklist, no third-party package)."""
    while not stop.is_set():
        try:
            lines = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV", "/NH"], capture_output=True,
                                   text=True, timeout=20).stdout.splitlines()
            rows = [l.split('","') for l in lines if l.startswith('"claude.exe"')]
            kb = sum(int(r[4].replace(" K", "").replace(",", "").replace('"', "").replace(".", "")) for r in rows)
            out.append({"t": round(time.time(), 1), "processes": len(rows), "working_set_mb": round(kb / 1024)})
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
        stop.wait(5)


def cmd_sets():
    r = pipeline.Run("all")
    st = store.load()
    b = pipeline.stage_batch(r, st)
    msgs = {x["batch_id"]: len(x["keys"]) for x in b["batches"]}
    sets = pick_sets(list(msgs))
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "batches": len(msgs),
           "max_messages": r.cfg["batch"].get("max_messages_by_slice", {}).get("all"),
           "fingerprints": {x["batch_id"]: x["fingerprint"][:12] for x in b["batches"]},
           "sets": {k: {"batches": v, "messages": sum(msgs[i] for i in v),
                        "mailboxes": dict(Counter(k2.split("/")[0] for x in b["batches"] if x["batch_id"] in v for k2 in x["keys"]))}
                    for k, v in sets.items()},
           "cap_exceptions": [x.get("cap_exception") for x in b["batches"] if x.get("cap_exception")]}
    (OUT / "sets.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    for k, v in rec["sets"].items():
        print(k, f"{len(v['batches'])} batches, {v['messages']} messages, mailboxes {v['mailboxes']}:", ",".join(v["batches"]))
    return 0


def pick_extra_set(batch_ids: list[str], used: list[str], size: int) -> list[str]:
    """`size` batches spread evenly over the batches no earlier set used (deterministic): every earlier set keeps its
    batches, and the new one still spans the whole corpus (dates and mailboxes)."""
    free = sorted(set(batch_ids) - set(used))
    if size > len(free):
        raise ValueError(f"{size} batches wanted, {len(free)} unused")
    return sorted(free[i * len(free) // size] for i in range(size))


def cmd_add_set(name: str, size: int):
    """Add a further disjoint set (step 2b, 24 workers) without touching sets.json, so A-C stay exactly as measured."""
    sets = json.loads((OUT / "sets.json").read_text(encoding="utf-8"))
    out_file = OUT / f"set_{name}.json"
    if out_file.exists():
        raise SystemExit(f"{out_file} exists: sets are never overwritten")
    r = pipeline.Run("all")
    b = pipeline.stage_batch(r, store.load())
    now = {x["batch_id"]: x["fingerprint"][:12] for x in b["batches"]}
    if now != sets["fingerprints"]:
        raise SystemExit("the batch layout differs from the one sets A-C were measured on: stop and look")
    cached = [p.stem for p in (r.cache / "extract").glob("*.json")]       # a cached batch would not be a live session
    ids = pick_extra_set(list(now), [i for v in sets["sets"].values() for i in v["batches"]] + cached, size)
    msgs = {x["batch_id"]: len(x["keys"]) for x in b["batches"]}
    rec = {"name": name, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "batches": ids,
           "messages": sum(msgs[i] for i in ids),
           "mailboxes": dict(Counter(k.split("/")[0] for x in b["batches"] if x["batch_id"] in ids for k in x["keys"])),
           "fingerprints": {i: now[i] for i in ids}}
    out_file.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    print(name, f"{len(ids)} batches, {rec['messages']} messages, mailboxes {rec['mailboxes']}:", ",".join(ids))
    return 0


def cmd_run(name: str, workers: int, tag: str = ""):
    sets = json.loads((OUT / "sets.json").read_text(encoding="utf-8"))
    out_file = OUT / f"run_{name}{tag}.json"
    if out_file.exists():
        raise SystemExit(f"{out_file} exists: results are never overwritten")
    ids = (sets["sets"][name] if name in sets["sets"] else json.loads((OUT / f"set_{name}.json").read_text(encoding="utf-8")))["batches"]
    stop, samples = threading.Event(), []
    th = threading.Thread(target=sampler, args=(stop, samples), daemon=True)
    th.start()
    t0 = time.time()
    rep = pipeline.run("all", workers=workers, batch_ids=ids, stop_after="ground")
    wall = time.time() - t0
    stop.set()
    th.join(timeout=30)
    r = pipeline.Run("all")
    sessions = json.loads((r.data / "sessions.json").read_text(encoding="utf-8"))
    shutil.copy(r.data / "sessions.json", OUT / f"sessions_{name}{tag}.json")
    manifest = json.loads((ROOT / "data" / "batches" / "all" / "manifest.json").read_text(encoding="utf-8"))
    msgs = {x["batch_id"]: len(x["keys"]) for x in manifest["batches"]}
    model = json.loads((ROOT / "eval" / "results" / "m4_levers" / "batch_time_model.json").read_text(encoding="utf-8"))["model"]
    log = [json.loads(l) for l in r.log_path.read_text(encoding="utf-8").splitlines()]
    start = max(i for i, x in enumerate(log) if x.get("stage") == "start")
    ms = next((x for x in log[start:] if x.get("stage") == "model_stages"), {})
    res = {"name": name, "workers": workers, "batches": ids, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "wall_s": round(wall, 1), "exit_code": rep.get("exit_code"), "stopped": rep.get("stopped"),
           "complete": rep.get("complete"), "model_stages": {k: ms.get(k) for k in (
               "n_extracted", "n_grounded", "failed_extract", "failed_ground", "unfinished", "ground_unfinished",
               "peak_in_flight", "session_kinds", "fence_self_checks", "fence_self_check_flags", "canary",
               "sentinels_checked", "cumulative", "flags")},
           "cache_hits": sum(1 for x in log[start:] if x.get("status") == "cache_hit"),
           "sessions": session_report(sessions, msgs, model, workers),
           "processes": {"samples": len(samples), "peak_processes": max((s["processes"] for s in samples), default=None),
                         "peak_working_set_mb": max((s["working_set_mb"] for s in samples), default=None)}}
    OUT.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    ex = res["sessions"].get("extract") or {}
    print(json.dumps({"wall_s": res["wall_s"], "exit_code": res["exit_code"], "stopped": res["stopped"],
                      "extract": {k: ex.get(k) for k in ("sessions", "kinds", "seconds", "first_wave_start_spread_s",
                                                         "measured_over_predicted", "self_checks")},
                      "ground": {k: (res["sessions"].get("ground") or {}).get(k) for k in ("sessions", "kinds", "seconds")},
                      "processes": res["processes"], "model_stages": res["model_stages"]}, indent=1, default=str))
    return 0 if rep.get("exit_code") == 0 else 1


def _synth_session(r, st, obs, sid, part):
    """One synthesizer session outside the pipeline cache (input ids s1NN never used by production)."""
    inp = synth.build_input(obs, st, sid, r.slice, part=part)
    r.synth_inputs.mkdir(parents=True, exist_ok=True)
    r.synth_staging.mkdir(parents=True, exist_ok=True)
    (r.synth_inputs / f"{sid}.txt").write_text(inp["text"], encoding="utf-8", newline="")
    for attempt in range(1, 2 + r.cfg["run"]["retries"]):
        att = f"{sid}-{inp['sha256'][:8]}-{attempt}"
        out = r.synth_staging / f"{sid}.json"
        out.unlink(missing_ok=True)
        rd, wr = r.rel(r.synth_inputs / f"{sid}.txt"), r.rel(out)
        t = time.time()
        rep = pipeline._single_session(r, "synthesizer", f"Read the input file `{rd}`. input_id is `{sid}`, attempt_id is "
                                       f"`{att}`. Follow your instructions and write your JSON to `{wr}`. {pipeline.NO_READBACK}",
                                       r.cfg["synth"]["model"], rd, wr, att)
        res = synth.check(out.read_text(encoding="utf-8") if out.exists() else "", inp, att)
        res["model_observed"] = runmodel.from_headless(r.cfg["synth"]["model"], rep["models_observed"])
        res["seconds"], res["attempt_id"], res["tokens"] = round(time.time() - t, 1), att, rep["tokens"]
        if res["status"] == "verified":
            return inp, res
    raise SystemExit(f"synthesis {sid} failed: {res['errors'][:3]}")


def cmd_compare_synth(mailbox: str):
    out_file = OUT / f"synth_{mailbox}.json"
    if out_file.exists():
        raise SystemExit(f"{out_file} exists: results are never overwritten")
    r = pipeline.Run("all")
    st = store.load()
    obs = [o for f in sorted((r.data / "ground").glob("b*.json"))
           for o in json.loads(f.read_text(encoding="utf-8"))["observations"] if st.messages[o["msg_key"]].mailbox == mailbox]
    if not obs:
        raise SystemExit(f"no grounded {mailbox} observations under {r.data / 'ground'}: run the 2b sets first")
    total = sum(synth.est_tokens(o, st) for o in obs)
    budget = math.ceil(total / 2)
    parts = synth.split(obs, st, budget, date_split=True)
    while len(parts) > 2:                                       # whole threads: widen until exactly two parts
        budget = math.ceil(budget * 1.05)
        parts = synth.split(obs, st, budget, date_split=True)
    t0 = time.time()
    w_inp, whole = _synth_session(r, st, obs, "s101", None)
    t_whole = time.time() - t0
    from concurrent.futures import ThreadPoolExecutor
    t1 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        pairs = list(ex.map(lambda kp: _synth_session(r, st, kp[1], f"s{102 + kp[0]}", (kp[0] + 1, 2)), enumerate(parts)))
    t_parts = time.time() - t1
    minp = synth.build_merge_input(pairs, st, {o["obs_id"]: o for o in obs}, "m101", r.slice)
    (r.synth_inputs / "m101.txt").write_text(minp["text"], encoding="utf-8", newline="")
    t2 = time.time()
    split = None
    for attempt in range(1, 2 + r.cfg["run"]["retries"]):
        att = f"m101-{minp['sha256'][:8]}-{attempt}"
        out = r.synth_staging / "m101.json"
        out.unlink(missing_ok=True)
        rd, wr = r.rel(r.synth_inputs / "m101.txt"), r.rel(out)
        rep = pipeline._single_session(r, "merger", f"Read the input file `{rd}`. input_id is `m101`, attempt_id is `{att}`. "
                                       f"Follow your instructions and write your JSON to `{wr}`. {pipeline.NO_READBACK}",
                                       r.cfg["synth"]["merge_model"], rd, wr, att)
        split = synth.merge_check(out.read_text(encoding="utf-8") if out.exists() else "", minp, pairs, att)
        if split["status"] == "verified":
            break
    t_merge = time.time() - t2
    if not split or split["status"] != "verified":
        raise SystemExit(f"merge failed: {split['errors'][:3] if split else 'no attempt'}")
    cmp_ = compare_processes(whole, split, obs, st, events.seen_in_mailboxes())
    res = {"mailbox": mailbox, "observations": len(obs), "est_tokens": total, "parts": [len(p) for p in parts],
           "seconds": {"whole": round(t_whole, 1), "parts_parallel": round(t_parts, 1), "merge": round(t_merge, 1),
                       "split_total": round(t_parts + t_merge, 1)},
           "models": {"whole": whole["model_observed"]["status"], "parts": [p[1]["model_observed"]["status"] for p in pairs]},
           "merge_flags": split["flags"], "merged": [p["process_id"] for p in split["processes"] if p.get("made_by") == "merged"],
           "comparison": cmp_, "whole": {k: whole[k] for k in ("processes", "assignment")},
           "split": {k: split[k] for k in ("processes", "assignment")}}
    OUT.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("observations", "est_tokens", "parts", "seconds", "merged")}, indent=1))
    print(json.dumps({k: cmp_[k] for k in ("whole_processes", "split_processes", "recovered_at_half_overlap", "split_only",
                                           "assigned", "events_in_recurring_processes")}, indent=1))
    for row in cmp_["rows"]:
        print(f"  {row['whole']} {row['name'][:48]!r}: {row['observations']} obs, {row['events']} events -> {row['split']} "
              f"jaccard {row['jaccard']}, {row['split_events']} events; went {row['observations_went']}")
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sets")
    r = sub.add_parser("run")
    r.add_argument("name", choices=sorted(SIZES) + ["D"])
    r.add_argument("--workers", type=int, required=True)
    r.add_argument("--tag", default="", help="suffix for the result file, e.g. -r2 for a rerun after a stop")
    d = sub.add_parser("add-set")
    d.add_argument("name", choices=["D"])
    d.add_argument("--size", type=int, default=24)
    c = sub.add_parser("compare-synth")
    c.add_argument("--mailbox", default="farmer-d")
    a = ap.parse_args(argv)
    if a.cmd == "sets":
        return cmd_sets()
    if a.cmd == "add-set":
        return cmd_add_set(a.name, a.size)
    if a.cmd == "run":
        return cmd_run(a.name, a.workers, a.tag)
    return cmd_compare_synth(a.mailbox)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
