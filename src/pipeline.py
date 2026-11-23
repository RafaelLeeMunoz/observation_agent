"""The run: every stage in order, cached, restartable, timed (PLAN 3, 3.8, 3.9).

    ingest   (code)   src.ingest, cached by its own fingerprint (corpus bytes, names, code)
    batch    (code)   threads -> batches; fingerprint = batch text + prompt version
    extract  (model)  one fenced extractor per batch, in parallel; cached per batch
    ground   (model)  one fenced grounder per batch with claims (design I); cached per packet
    synth    (model)  one fenced synthesizer over the compact list; cached by input
    events, score     (code)
    act      (model)  one fenced artifact-writer per qualifying opportunity; cached by packet
    report   (code)   output/<slice>/ dashboard data, report.md, run log

Model stages run as headless Claude Code sessions (src/headless.py: Read and Write only, the fence hook
passed explicitly, no MCP, Pro subscription, refuses if ANTHROPIC_API_KEY is set). A stage's cache entry
is its input fingerprint plus the prompt file's sha256, the model and the schema: change any of them and
only that stage (and what depends on it) runs again. A failed batch is retried up to `retries` times and
never marked read; a run with any unread message fails its coverage check and says which.

Run:  .venv\\Scripts\\python -m src.pipeline run --slice dev
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from . import act, batch, config, events, extract, ground, headless, runmodel, score, store, synth, verify, windows

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "pipeline-m3-v1"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Run:
    def __init__(self, slice_name: str, root: Path = ROOT):
        self.slice, self.root = slice_name, root
        self.data = root / "data" / "runs" / slice_name
        self.out = root / "output" / slice_name
        self.cache = root / "data" / "cache" / slice_name
        for d in (self.data, self.out, self.cache):
            d.mkdir(parents=True, exist_ok=True)
        self.log_path = self.out / "logs" / "runlog.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.t0 = time.time()
        self.cfg = config.load()

    def log(self, **rec):
        rec = {"at": _now(), "t": round(time.time() - self.t0, 1), "slice": self.slice, **rec}
        with extract.shared_lock(self.log_path.parent), self.log_path.open("a", encoding="utf-8") as f:   # parallel workers
            f.write(json.dumps(rec) + "\n")
        print(json.dumps(rec)[:300], flush=True)

    def cached(self, stage: str, name: str, key: dict) -> Path | None:
        c = self.cache / stage / f"{name}.json"
        if c.exists():
            rec = json.loads(c.read_text(encoding="utf-8"))
            if rec["key"] == key and Path(rec["output"]).exists():
                return Path(rec["output"])
        return None

    def remember(self, stage: str, name: str, key: dict, output: Path):
        (self.cache / stage).mkdir(parents=True, exist_ok=True)
        (self.cache / stage / f"{name}.json").write_text(json.dumps({"key": key, "output": str(output),
                                                                     "at": _now()}, indent=1), encoding="utf-8")


def _usage(rep: dict) -> dict:
    return {"subagent_tokens": headless.total_tokens(rep), "duration_ms": rep["duration_ms"],
            "wall_seconds": rep["wall_seconds"], "num_turns": rep["num_turns"], "tokens": rep["tokens"],
            "models_observed": rep["models_observed"], "notional_cost_usd": rep["notional_cost_usd"],
            "source": "claude -p JSON report (headless)"}


# --- stages ---

def stage_batch(r: Run, st: store.Store) -> dict:
    cfg = r.cfg
    if r.slice in ("dev", "bench"):
        sl = json.loads((r.root / "config" / "devslice.json").read_text(encoding="utf-8"))
        keys = sl["keys"] if r.slice == "dev" else sl["benchmark_keys"]
    else:
        keys = [m.key for m in st.unique()]
    batches = batch.make_batches(st, keys, cfg["batch"], cfg["extract"]["prompt_version"])
    checks = batch.check_batches(batches, st, keys)
    if not all(checks.values()):
        raise RuntimeError(f"batch checks failed: {[k for k, v in checks.items() if not v]}")
    batch.write(batches, r.root / "data" / "batches" / r.slice)
    r.log(stage="batch", status="ok", batches=len(batches), messages=len(keys),
          est_tokens=sum(b["est_tokens"] for b in batches))
    return {"batches": batches, "keys": keys}


def stage_extract(r: Run, st: store.Store, batches: list[dict], workers: int) -> dict[str, Path]:
    cfg = r.cfg["extract"]
    prompt = r.root / ".claude" / "agents" / "extractor.md"
    keyfor = lambda b: {"fingerprint": b["fingerprint"], "prompt_sha256": _sha(prompt), "model": cfg["model"],
                        "schema": cfg["schema"], "prompt_version": cfg["prompt_version"]}
    done, todo = {}, []
    for b in batches:
        hit = r.cached("extract", b["batch_id"], keyfor(b))
        adopt = r.root / "data" / "extract" / "verified" / f"{b['batch_id']}.json"
        if not hit and adopt.exists():       # verified by an earlier (or in-session) attempt for this exact batch and prompt
            v = json.loads(adopt.read_text(encoding="utf-8"))
            att = json.loads((r.root / "data" / "extract" / "attempts" / f"{b['batch_id']}.json").read_text(encoding="utf-8"))
            if (v.get("status") == "verified" and v["fingerprint"] == b["fingerprint"] and att["attempt_id"] == v["attempt_id"]
                    and v.get("prompt_sha256") == keyfor(b)["prompt_sha256"] and v.get("schema") == cfg["schema"]
                    and v["model_observed"]["status"] == runmodel.CONFIRMED):
                dest = r.data / "extract" / f"{b['batch_id']}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(adopt, dest)
                r.remember("extract", b["batch_id"], keyfor(b), dest)
                hit = dest
        if hit:
            done[b["batch_id"]] = hit
            r.log(stage="extract", batch=b["batch_id"], status="cache_hit")
        else:
            todo.append(b)
    p = extract.Paths(r.root)

    def one(b):
        for attempt in range(1 + r.cfg["run"]["retries"]):
            rec = extract.prepare(p, r.slice, b["batch_id"], cfg["model"], cfg["prompt_version"], cfg["schema"])
            t = time.time()
            rep = headless.run("extractor", extract.instruction(rec), cfg["model"], timeout=r.cfg["run"]["timeout_s"])
            res = extract.finalize(p, r.slice, b["batch_id"], st, r.cfg["verify"], first=False, usage=_usage(rep))
            r.log(stage="extract", batch=b["batch_id"], attempt=rec["attempt_id"], status=res["status"],
                  errors=res["errors"][:3], quotes=res.get("quotes"), seconds=round(time.time() - t, 1),
                  model=res["model_observed"]["status"], usage=_usage(rep))
            if res["status"] == "verified" and res["model_observed"]["status"] != runmodel.MISMATCH:
                dest = r.data / "extract" / f"{b['batch_id']}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(p.verified / f"{b['batch_id']}.json", dest)
                r.remember("extract", b["batch_id"], keyfor(b), dest)
                return b["batch_id"], dest
        return b["batch_id"], None
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for bid, path in ex.map(one, todo):
            if path:
                done[bid] = path
    return done


def stage_ground(r: Run, st: store.Store, extracted: dict[str, Path], workers: int) -> dict[str, Path]:
    prompt = r.root / ".claude" / "agents" / "grounder.md"
    out, todo = {}, []
    for bid, path in sorted(extracted.items()):
        run = json.loads(path.read_text(encoding="utf-8"))
        pid = f"g{100 + int(bid[1:]):03d}"          # g101, g102, ...: never the bench packets' ids
        if not any(o.get("stall_signals_claimed") for o in run["observations"]):
            g = json.loads(json.dumps(run))
            for o in g["observations"]:
                o.update(stall_signals=[], signal_outcomes=[], published_evidence=[], signal_grounding="grounder")
            dest = r.data / "ground" / f"{bid}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(g, indent=1), encoding="utf-8")
            out[bid] = dest
            r.log(stage="ground", batch=bid, status="no_claims")
            continue
        from . import packet
        pk = packet.build(run, st, pid, f"{r.slice}")
        key = {"packet_sha256": pk["sha256"], "prompt_sha256": _sha(prompt), "packet_version": pk["version"]}
        hit = r.cached("ground", bid, key)
        adopt = ground.GROUND / "verified" / f"{r.slice}_{bid}.json"
        if not hit and adopt.exists():             # finished by an earlier interrupted run: same packet, same prompt
            g = json.loads(adopt.read_text(encoding="utf-8"))["grounding"]
            if g["packet_sha256"] == pk["sha256"] and g["prompt_sha256"] == key["prompt_sha256"]:
                dest = r.data / "ground" / f"{bid}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(adopt, dest)
                r.remember("ground", bid, key, dest)
                hit = dest
        if hit:
            out[bid] = hit
            r.log(stage="ground", batch=bid, status="cache_hit")
        else:
            todo.append((bid, path, pid, key))

    def one(item):
        bid, path, pid, key = item
        name = f"{r.slice}_{bid}"
        for attempt in range(1 + r.cfg["run"]["retries"]):
            rec = ground.prepare(path, name, pid, st)
            t = time.time()
            rep = headless.run("grounder", ground.instruction(rec), "haiku", timeout=r.cfg["run"]["timeout_s"])
            res = ground.finalize(name, pid, st, _usage(rep))
            r.log(stage="ground", batch=bid, attempt=rec["attempt_id"], status=res["status"], outcomes=res.get("outcomes"),
                  errors=res["errors"][:3], seconds=round(time.time() - t, 1), usage=_usage(rep))
            if res["status"] == "verified":
                dest = r.data / "ground" / f"{bid}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ground.GROUND / "verified" / f"{name}.json", dest)
                r.remember("ground", bid, key, dest)
                return bid, dest
        return bid, None
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for bid, path in ex.map(one, todo):
            if path:
                out[bid] = path
    return out


def stage_synth(r: Run, st: store.Store, observations: list[dict]) -> dict:
    prompt = r.root / ".claude" / "agents" / "synthesizer.md"
    inp = synth.build_input(observations, st, "s001", r.slice)
    in_dir = r.root / "data" / "synth" / "inputs" / r.slice
    in_dir.mkdir(parents=True, exist_ok=True)
    (in_dir / "s001.txt").write_text(inp["text"], encoding="utf-8", newline="")
    key = {"input_sha256": inp["sha256"], "prompt_sha256": _sha(prompt), "model": r.cfg["synth"]["model"]}
    hit = r.cached("synth", "s001", key)
    if hit:
        r.log(stage="synth", status="cache_hit")
        return json.loads(hit.read_text(encoding="utf-8"))
    staging = r.root / "data" / "synth" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 2 + r.cfg["run"]["retries"]):
        attempt_id = f"s001-{inp['sha256'][:8]}-{attempt}"
        (staging / "s001.json").unlink(missing_ok=True)
        instr = (f"Read the input file `data/synth/inputs/{r.slice}/s001.txt`. input_id is `s001`, attempt_id is "
                 f"`{attempt_id}`. Follow your instructions and write your JSON to `data/synth/staging/s001.json`.")
        t = time.time()
        rep = headless.run("synthesizer", instr, r.cfg["synth"]["model"], timeout=r.cfg["run"]["timeout_s"])
        text = (staging / "s001.json").read_text(encoding="utf-8") if (staging / "s001.json").exists() else ""
        res = synth.check(text, inp, attempt_id)
        mo = runmodel.from_headless(r.cfg["synth"]["model"], rep["models_observed"])
        r.log(stage="synth", attempt=attempt_id, status=res["status"], errors=res["errors"][:5], flags=len(res["flags"]),
              processes=len(res["processes"]), seconds=round(time.time() - t, 1), model=mo["status"], usage=_usage(rep))
        if res["status"] == "verified" and mo["status"] != runmodel.MISMATCH:
            res.update(input_id="s001", attempt_id=attempt_id, input_sha256=inp["sha256"], prompt_sha256=key["prompt_sha256"],
                       model_observed=mo, usage=_usage(rep))
            dest = r.data / "synth.json"
            dest.write_text(json.dumps(res, indent=1), encoding="utf-8")
            r.remember("synth", "s001", key, dest)
            return res
    raise RuntimeError("synthesis failed after retries")


def stage_act(r: Run, st: store.Store, opp: dict, process: dict, observations: dict[str, dict]) -> dict:
    prompt = r.root / ".claude" / "agents" / "artifact-writer.md"
    n = opp["rank"]
    pid = f"a{n:03d}"
    atype = process["automation"]["artifact_type"]
    quotes = act.quotes_for(process, observations, st)
    pk = act.build_packet(process, atype, quotes, pid)
    pdir = r.root / "data" / "act" / "packets" / r.slice
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{pid}.txt").write_text(pk["text"], encoding="utf-8", newline="")
    key = {"packet_sha256": pk["sha256"], "prompt_sha256": _sha(prompt), "model": r.cfg["act"]["model"]}
    name = f"{process['process_id']}-{atype}"
    hit = r.cached("act", pid, key)
    if hit:
        r.log(stage="act", packet=pid, status="cache_hit")
        return json.loads(hit.read_text(encoding="utf-8"))
    staging = r.root / "data" / "act" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 2 + r.cfg["run"]["retries"]):
        (staging / f"{pid}.md").unlink(missing_ok=True)
        instr = (f"Read the packet file `data/act/packets/{r.slice}/{pid}.txt`. packet_id is `{pid}`. Follow your "
                 f"instructions and write your Markdown to `data/act/staging/{pid}.md`.")
        t = time.time()
        rep = headless.run("artifact-writer", instr, r.cfg["act"]["model"], timeout=r.cfg["run"]["timeout_s"])
        draft = (staging / f"{pid}.md").read_text(encoding="utf-8") if (staging / f"{pid}.md").exists() else ""
        chk = act.check(draft, pk, r.cfg["act"])
        mo = runmodel.from_headless(r.cfg["act"]["model"], rep["models_observed"])
        r.log(stage="act", packet=pid, status=chk["status"], errors=chk["errors"], flags=chk["flags"],
              seconds=round(time.time() - t, 1), model=mo["status"], usage=_usage(rep))
        if chk["status"] == "verified" and mo["status"] != runmodel.MISMATCH:
            path = act.publish(draft, pk, chk, st, r.out / "artifacts", name)
            rec = {"packet_id": pid, "process_id": process["process_id"], "artifact_type": atype, "path": str(path),
                   "name": name, "cited": chk["cited"], "flags": chk["flags"], "quotes": quotes,
                   "packet_sha256": pk["sha256"], "prompt_sha256": key["prompt_sha256"], "model_observed": mo}
            dest = r.data / "act" / f"{pid}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(rec, indent=1), encoding="utf-8")
            r.remember("act", pid, key, dest)
            return rec
        failed = r.root / "data" / "act" / "failed"
        failed.mkdir(parents=True, exist_ok=True)
        (failed / f"{pid}-{attempt}.md").write_text(draft, encoding="utf-8")
    return {"packet_id": pid, "process_id": process["process_id"], "status": "failed"}


def quote_ids(observations: list[dict]) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """Verified quote ids: q:<obs_id> for an observation's own quote, ev:<obs>:<msg>:<s>-<e> for evidence."""
    ids, index = {}, {}
    for o in observations:
        q = f"q:{o['obs_id']}"
        ids.setdefault(o["obs_id"], []).append(q)
        index[q] = {"msg_key": o["msg_key"], "start": o["start"], "end": o["end"], "quote_raw": o["quote_raw"],
                    "obs_id": o["obs_id"], "kind": "observation_quote", "evidence_kind": o.get("evidence_kind"),
                    "evidence_author": o.get("evidence_author"), "evidence_date": o.get("evidence_date")}
        for e in o.get("published_evidence", []):
            k = f"ev:{o['obs_id']}:{e['msg_key']}:{e['start']}-{e['end']}"
            ids[o["obs_id"]].append(k)
            index[k] = {"msg_key": e["msg_key"], "start": e["start"], "end": e["end"], "quote_raw": e["quote_raw"],
                        "obs_id": o["obs_id"], "kind": f"{e['signal']}_evidence", "signal": e["signal"],
                        "evidence_kind": e.get("evidence_kind"), "evidence_author": e.get("evidence_author")}
    return ids, index


class OfflineCacheMiss(RuntimeError):
    """--offline: a stage needed a model call because its cache entry is missing or stale."""


def _refuse(agent, prompt, model, timeout=0, cwd=None):
    raise OfflineCacheMiss(f"offline run: the {agent} would be called ({prompt[:80]!r}); its stage is not cached")


def run(slice_name: str, workers: int | None = None, offline: bool = False) -> dict:
    """`offline`: every model stage must be a cache hit; any model call raises instead (no spend, ever)."""
    if offline:
        headless.run = _refuse
    r = Run(slice_name)
    workers = workers or r.cfg["run"]["workers"]
    r.log(stage="start", version=PIPELINE_VERSION, workers=workers, offline=offline)
    st = store.load()
    b = stage_batch(r, st)
    extracted = stage_extract(r, st, b["batches"], workers)
    missing = [x["batch_id"] for x in b["batches"] if x["batch_id"] not in extracted]
    grounded = stage_ground(r, st, extracted, workers)
    obs = [o for bid in sorted(grounded) for o in json.loads(grounded[bid].read_text(encoding="utf-8"))["observations"]]
    res = stage_synth(r, st, obs)
    seen = events.seen_in_mailboxes()
    evs = events.build(obs, res["assignment"], st, seen)
    probs = events.check(evs, {o: p for o, p in res["assignment"].items() if o in {x["obs_id"] for x in obs}})
    if probs:
        raise RuntimeError(f"event invariants failed: {probs}")
    win = {mb: (datetime.fromisoformat(a), datetime.fromisoformat(z))
           for mb, a, z in _windows()}
    qids, qindex = quote_ids(obs)
    sc = score.score(res["processes"], evs, win, r.cfg["score"], qids)
    chain = score.check_chains(sc["claims"], set(qindex))
    r.log(stage="score", status="ok" if not chain else "failed", opportunities=len(sc["opportunities"]),
          total=sc["claims"]["total"]["display"], chain_problems=chain[:5])
    if chain:
        raise RuntimeError(f"provenance chains broken: {chain[:5]}")
    by_obs = {o["obs_id"]: o for o in obs}
    procs = {p["process_id"]: p for p in res["processes"]}
    artifacts = []
    thr = r.cfg["act"]
    for opp in sc["opportunities"]:
        if opp["status"] != "scored" or len(artifacts) >= thr["max_artifacts"]:
            continue
        if Decimal(opp["dollars_per_month"]) >= Decimal(str(thr["min_dollars_per_month"])) or (
                thr.get("at_least_one") and not artifacts):
            artifacts.append(stage_act(r, st, opp, procs[opp["process_id"]], by_obs))
    from . import report
    rep = report.build(r, st, b, extracted, grounded, obs, res, evs, sc, qindex, artifacts, missing)
    r.log(stage="done", status="ok" if not missing else "incomplete", seconds=round(time.time() - r.t0, 1),
          unread_batches=missing)
    return rep


def _windows():
    import sqlite3
    con = sqlite3.connect(store.DEFAULT_DB)
    rows = list(con.execute("SELECT mailbox, first_utc, last_utc FROM windows"))
    con.close()
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the observation pipeline on a slice.")
    ap.add_argument("action", choices=["run"])
    ap.add_argument("--slice", default="dev", choices=["dev", "bench", "all"])
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--offline", action="store_true", help="refuse every model call: proves the cache is complete")
    args = ap.parse_args(argv)
    rep = run(args.slice, args.workers, offline=args.offline)
    print(json.dumps({k: rep[k] for k in ("coverage", "total", "opportunities_ranked", "artifacts")}, indent=1)[:2000])
    return 0 if rep["coverage"]["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
