"""The run: every stage in order, cached, restartable, timed (PLAN 3, 3.8, 3.9, 5.1).

    ingest   (code)   src.ingest, cached by its own fingerprint (corpus bytes, names, code)
    batch    (code)   threads -> batches; fingerprint = batch text + prompt version
    extract  (model)  one fenced extractor per batch, in parallel; cached per batch
    ground   (model)  one fenced grounder per batch with claims (design I), started as soon as that batch's
                      extraction is verified, in the same pool of sessions; cached per packet
    synth    (model)  one fenced synthesizer over the compact list; cached by input
    events, score     (code)
    act      (model)  one fenced artifact-writer per qualifying opportunity; cached by packet
    report   (code)   output/<slice>/ dashboard data, report.md, run log

Model stages run as headless Claude Code sessions (src/headless.py: Read and Write only, no MCP, Pro
subscription, refuses if ANTHROPIC_API_KEY is set). Each session's fence hook is given exactly its one
input and one output file, and logs to the session's own folder, so a denial is seen here. A stage's cache
entry is its input fingerprint plus the prompt file's sha256, the model and the schema: change any of them
and only that stage (and what depends on it) runs again.

Run safety (M4 step 1, PLAN 5.1):
  - Every slice has its own folders, so dev, bench, production and holdout runs never displace each other.
  - Quality checks while extraction fans out: the CANARY is the first fresh batch of the run to finish with at
    least `canary_min_quotes` checked quotes; SENTINEL batches, chosen by code before the run (the first batch
    holding each mailbox, and the batches a quarter, half and three quarters through), are held to the same
    rule; so is the CUMULATIVE rejection rate after every batch. A trip, a fence denial or a model mismatch is
    a critical failure: nothing more is scheduled, sessions in flight are stopped, nothing downstream runs,
    exit 2.
  - A usage limit, or `breaker_consecutive` sessions in a row ending in an error with almost no output
    (whatever the CLI's text says), stops the run cleanly: no new session, no retry spent, unfinished batches
    stay unread (never `failed`), nothing downstream runs, exit 4. Run the same command again to resume: every
    finished batch is a cache hit.
  - A temporary refusal (rate limited, overloaded) is retried after a back-off and does not spend a retry; a bad
    output or a timeout spends one of the `retries` (PLAN R8). A failed batch is never marked read.

Run:  .venv\\Scripts\\python -m src.pipeline run --slice dev
      .venv\\Scripts\\python -m src.pipeline run --slice all --batches b001-b008 --stop-after ground --workers 8
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import threading
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from . import act, batch, config, events, extract, ground, headless, runmodel, score, store, synth, verify, windows

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "pipeline-m4-v1"

# Exit codes of `pipeline run` (observe.md reports them)
EXIT_OK, EXIT_INCOMPLETE, EXIT_CRITICAL, EXIT_STOPPED = 0, 1, 2, 4
NO_READBACK = headless.NO_READBACK              # see src/headless.py: added to the synthesizer, merger and artifact-writer run instructions


def _kind(error: str) -> str:
    """The requirement an error belongs to: the same kind of error in two attempts is one standing requirement."""
    import re
    e = str(error)
    if e.startswith("coverage:"):
        return "coverage"
    if e.startswith("positional coverage:"):          # one requirement per segment: two blind thirds stay two requirements
        return e.split(" of the input")[0]
    if "catch-all process" in e:
        return "catch-all"
    return re.sub(r"\d+", "#", re.sub(r"\b[A-Za-z]+\d*-o\d+\b|\bP\d+\b|\bs\d{3}\.P\d+\b", "#", e))[:120]


def note_standing(standing: dict, errors: list[str]) -> None:
    """Every kind of error an attempt hit stays a requirement for the attempts after it, with its latest example (user, 24 Nov
    2026: a structural error must not make the model forget the coverage floor)."""
    for e in errors:
        standing[_kind(e)] = e


def retry_note(errors: list[str], standing: dict | None = None, max_items: int = 40, max_chars: int = 3000) -> str:
    """Option A (user, 24 Nov 2026): the next attempt of a synthesizer or merger is told what the pipeline's independent
    validation found wrong with the last one, AND every requirement an earlier attempt failed that still applies (`standing`:
    kind -> latest example). Run-instruction text only: the analytical prompt, the schema and the input are unchanged, and the
    note is in no cache key. The errors are built by code from observation and process ids; anything outside a small safe
    character set is dropped anyway, so no email text and no instruction can ride in on it."""
    import re
    clean = lambda e: re.sub(r"[^A-Za-z0-9_\-:,.'\[\] ()/]", "", str(e))[:300]
    last = {_kind(e) for e in errors}
    carry = [v for k, v in (standing or {}).items() if k not in last]
    used, out = 0, []
    for title, items in (("Your previous attempt was rejected by the pipeline's independent validation of your output. Fix exactly "
                          "these problems in this attempt and do not repeat them: ", errors),
                         (("These requirements also failed in earlier attempts on this input and still apply, so satisfy them as well: "
                           if errors else "Earlier attempts on this input were rejected by the pipeline's independent validation. "
                                          "These requirements failed and still apply, so satisfy them: "), carry)):
        lines = []
        for e in items[:max_items]:
            line = clean(e)
            if used + len(line) > max_chars:
                break
            lines.append(f"- {line}")
            used += len(line)
        more = len(items) - len(lines)
        if lines:
            out.append(title + " ".join(lines) + (f" (and {more} more of the same kinds)" if more > 0 else ""))
    return " ".join(out)


def _attempt_instruction(base: str, prev_errors: list[str], standing: dict | None = None) -> str:
    return base + (" " + retry_note(prev_errors, standing) if prev_errors or standing else "")


def _note_sha(prev_errors: list[str], standing: dict | None = None) -> str | None:
    import hashlib
    return (hashlib.sha256(retry_note(prev_errors, standing).encode("utf-8")).hexdigest()[:12]
            if prev_errors or standing else None)


def _floor(r: "Run") -> dict:
    c = r.cfg["synth"]
    p = c.get("positional")
    return {"floor": float(c.get("min_assigned_share", 0.0)), "catch_all": float(c.get("catch_all_share", 1.0)),
            "min_obs": int(c.get("coverage_min_observations", 40)),
            "pos": None if not p else {"segments": int(p["segments"]), "warn": float(p["warn_share"]), "fail": float(p["fail_share"]),
                                       "min_segment": int(p["min_segment_observations"]), "decay": float(p["decay_ratio"])}}


def _max_attempts(r: "Run") -> int:
    return int(r.cfg["synth"].get("max_attempts") or 1 + r.cfg["run"]["retries"])


def _keep_rejected(r: "Run", out_file: Path, attempt_id: str) -> None:
    """A failed attempt's output is kept (data/, never published) so the errors can be read against what was written."""
    if out_file.exists():
        (r.synth_staging / "rejected").mkdir(parents=True, exist_ok=True)
        shutil.copy(out_file, r.synth_staging / "rejected" / f"{attempt_id}.json")
CRITICAL = ("canary", "sentinel", "cumulative", "fence_denial", "fence_unobserved", "model_mismatch", "instructions_loaded", "synthesis_failed")
CLEAN_STOPS = ("usage_limit", "systemic_failure")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunStopped(Exception):
    """A stage stopped the run (a critical failure or a clean stop); `reason` is one of CRITICAL or CLEAN_STOPS."""

    def __init__(self, reason: str, **detail):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


class Run:
    def __init__(self, slice_name: str, root: Path = ROOT):
        self.slice, self.root = slice_name, root
        self.ex = extract.Paths(root, slice_name)          # refuses a slice the fence does not admit
        self.gp = ground.Paths(root, slice_name)
        self.synth_inputs = root / "data" / "synth" / "inputs" / slice_name
        self.synth_staging = root / "data" / "synth" / "staging" / slice_name
        self.act_packets = root / "data" / "act" / "packets" / slice_name
        self.act_staging = root / "data" / "act" / "staging" / slice_name
        self.act_failed = root / "data" / "act" / slice_name / "failed"
        self.fence_sessions = root / "data" / "logs" / "fence_sessions" / slice_name
        self.data = root / "data" / "runs" / slice_name
        self.out = root / "output" / slice_name
        self.cache = root / "data" / "cache" / slice_name
        for d in (self.data, self.out, self.cache):
            d.mkdir(parents=True, exist_ok=True)
        self.log_path = self.out / "logs" / "runlog.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.t0 = time.time()
        self.cfg = config.load()

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def log(self, **rec):
        rec = {"at": _now(), "t": round(time.time() - self.t0, 1), "slice": self.slice, **rec}
        with extract.shared_lock(self.log_path.parent), self.log_path.open("a", encoding="utf-8") as f:   # parallel workers
            f.write(json.dumps(rec, default=str) + "\n")
        print(json.dumps(rec, default=str)[:300], flush=True)

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
            "kind": rep.get("kind"), "thinking_tokens": rep.get("thinking_tokens"), "started_at_utc": rep.get("started_at_utc"),
            "session_env": (rep.get("config") or {}).get("session_env", headless.SESSION_ENV_VERSION),
            "finished_at_utc": rep.get("finished_at_utc"), "source": "claude -p JSON report (headless)"}


# --- sessions ---

def fence_summary(log_dir: Path) -> dict:
    """What the session's own fence registration decided: every record that is not an allow counts as denied."""
    recs = []
    if log_dir.is_dir():
        for f in sorted(log_dir.glob("*.json")):
            try:
                recs.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, UnicodeDecodeError):
                recs.append({"decision": "unreadable", "path": f.name})
    denied = [{k: x.get(k) for k in ("tool", "path", "decision", "reason", "denial_kind")} for x in recs
              if x.get("decision") != "allow"]
    # a documented self-check (tools/fence_hook.py SELF_CHECKS) was denied and is counted, never a stop; any other
    # denial, or a record without a kind, is unexpected
    self_checks = [d for d in denied if d.get("denial_kind") == "self_check"]
    unexpected = [d for d in denied if d.get("denial_kind") != "self_check"]
    return {"records": len(recs), "denied": unexpected, "self_checks": self_checks}


def session(r: Run, agent: str, prompt: str, model: str, read: str, write: str, attempt_id: str) -> dict:
    """One fenced headless session, held to exactly `read` and `write`; its fence decisions are summarised."""
    log_dir = r.fence_sessions / attempt_id
    if log_dir.exists():                 # the same attempt id again (a rerun of the same input): its old records are history
        log_dir.rename(log_dir.with_name(f"{attempt_id}.prior-{time.time_ns()}"))     # never counted against this session
    rep = headless.run(agent, prompt, model, timeout=r.cfg["run"]["timeout_s"], cwd=r.root, read=read, write=write,
                       log_dir=str(log_dir))
    rep["kind"], rep["reset_text"] = headless.classify(rep)      # from the report itself, never assumed
    rep["fence"] = fence_summary(log_dir)
    rep["instructions"] = {"loaded": headless.instructions_loaded(log_dir)}      # metadata only; a worker has none
    rep["output_exists"] = (r.root / write).exists()
    return rep


class Monitor:
    """Shared by the worker threads: the stop flag, the breaker streak and a record of every session."""

    def __init__(self, run_cfg: dict):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.stop: dict | None = None
        self.k, self.max_out = run_cfg["breaker_consecutive"], run_cfg["breaker_max_output_tokens"]
        self.streak, self.sessions = 0, []
        self.self_checks, self.self_check_flags = 0, []

    def request_stop(self, reason: str, **detail) -> bool:
        """True for the first stop request (later ones are recorded by their callers, not here)."""
        with self.lock:
            first = self.stop is None
            if first:
                self.stop = {"reason": reason, "critical": reason in CRITICAL, **detail}
        self.stop_event.set()
        return first

    def session_done(self, stage: str, name: str, attempt_id: str, rep: dict) -> bool:
        """Record a finished session; True when it completes a streak of `k` empty failures (the breaker)."""
        out_tok = (rep.get("tokens") or {}).get("output_tokens") or 0
        empty = rep["kind"] not in (headless.OK, headless.TERMINATED) and out_tok <= self.max_out
        with self.lock:
            sc = len(rep.get("fence", {}).get("self_checks") or [])
            if sc:
                self.self_checks += sc
                if sc > 1:                                # repeated in one session: flagged (user, 23 Nov 2026)
                    self.self_check_flags.append({"stage": stage, "name": name, "attempt": attempt_id, "self_checks": sc})
            self.sessions.append({"stage": stage, "name": name, "attempt": attempt_id, "kind": rep["kind"],
                                  "self_checks": sc, "instructions_loaded": len((rep.get("instructions") or {}).get("loaded") or []),
                                  "started_at_utc": rep.get("started_at_utc"), "finished_at_utc": rep.get("finished_at_utc"),
                                  "wall_seconds": rep.get("wall_seconds"), "output_tokens": out_tok,
                                  "fence_records": rep.get("fence", {}).get("records")})
            if rep["kind"] == headless.TERMINATED:
                return False
            self.streak = self.streak + 1 if empty else 0
            return self.streak >= self.k


def _session_outcome(rep: dict, tripped: bool) -> str | None:
    """The outcome a finished session forces before its output is looked at, or None to go on to finalize."""
    if rep["fence"]["denied"]:
        return "fence_denial"
    if (rep.get("instructions") or {}).get("loaded"):
        return "instructions_loaded"        # project instructions reached a worker: the launch isolation failed
    if rep["kind"] == headless.TERMINATED:
        return "terminated"
    if rep["kind"] == headless.USAGE_LIMIT:
        return "usage_limit"
    if tripped:
        return "systemic_failure"
    if rep["output_exists"] and rep["fence"]["records"] == 0:
        return "fence_unobserved"          # an output was written, but the session's fence never saw a call
    return None


# --- stages ---

def stage_batch(r: Run, st: store.Store) -> dict:
    cfg = r.cfg
    if r.slice in ("dev", "bench"):
        sl = json.loads((r.root / "config" / "devslice.json").read_text(encoding="utf-8"))
        keys = sl["keys"] if r.slice == "dev" else sl["benchmark_keys"]
    else:
        keys = [m.key for m in st.unique()]
    cap = (cfg["batch"].get("max_messages_by_slice") or {}).get(r.slice, cfg["batch"].get("max_messages"))
    batches = batch.make_batches(st, keys, {**cfg["batch"], "max_messages": cap}, cfg["extract"]["prompt_version"])
    checks = batch.check_batches(batches, st, keys)
    if not all(checks.values()):
        raise RuntimeError(f"batch checks failed: {[k for k, v in checks.items() if not v]}")
    over = []                                         # documented exceptions to the soft cap: whole threads kept
    for b in batches:
        if cap is not None and len(b["keys"]) > cap:
            b["cap_exception"] = (f"one thread of {len(b['keys'])} messages kept whole" if len(b["threads"]) == 1
                                  else f"{len(b['keys'])} messages in {len(b['threads'])} threads")
            over.append({"batch": b["batch_id"], "messages": len(b["keys"]), "why": b["cap_exception"]})
    batch.write(batches, r.root / "data" / "batches" / r.slice)
    r.log(stage="batch", status="ok", batches=len(batches), messages=len(keys), max_messages=cap, cap_exceptions=over,
          est_tokens=sum(b["est_tokens"] for b in batches))
    return {"batches": batches, "keys": keys}


def pick_sentinels(batches: list[dict], st: store.Store) -> list[str]:
    """Representative later batches, chosen before the run (deterministic): the first batch, by id, holding each
    mailbox's messages, and the batches a quarter, half and three quarters through the id order."""
    ordered = sorted(batches, key=lambda b: b["batch_id"])
    out, seen = set(), set()
    for b in ordered:
        for mb in sorted({st.messages[k].mailbox for k in b["keys"]}):
            if mb not in seen:
                seen.add(mb)
                out.add(b["batch_id"])
    n = len(ordered)
    for k in (1, 2, 3):
        if n:
            out.add(ordered[min(n - 1, n * k // 4)]["batch_id"])
    return sorted(out)


def _extract_key(r: Run, b: dict) -> dict:
    cfg = r.cfg["extract"]
    prompt = r.root / ".claude" / "agents" / "extractor.md"
    keyfor = lambda b: {"fingerprint": b["fingerprint"], "prompt_sha256": _sha(prompt), "model": cfg["model"],
                        "schema": cfg["schema"], "prompt_version": cfg["prompt_version"],
                        "session_env": headless.SESSION_ENV_VERSION}
    return keyfor(b)


def _extract_hit(r: Run, b: dict) -> Path | None:
    """A cache hit, or a batch verified by an earlier (interrupted) attempt for this exact batch and prompt."""
    key = _extract_key(r, b)
    hit = r.cached("extract", b["batch_id"], key)
    adopt = r.ex.verified / f"{b['batch_id']}.json"
    att_file = r.ex.attempts / f"{b['batch_id']}.json"
    if not hit and adopt.exists() and att_file.exists():
        v = json.loads(adopt.read_text(encoding="utf-8"))
        att = json.loads(att_file.read_text(encoding="utf-8"))
        if (v.get("status") == "verified" and v["fingerprint"] == b["fingerprint"] and att["attempt_id"] == v["attempt_id"]
                and v.get("prompt_sha256") == key["prompt_sha256"] and v.get("schema") == key["schema"]
                and (v.get("usage") or {}).get("session_env") == headless.SESSION_ENV_VERSION       # as grounding does (2b)
                and v["model_observed"]["status"] == runmodel.CONFIRMED):
            dest = r.data / "extract" / f"{b['batch_id']}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(adopt, dest)
            r.remember("extract", b["batch_id"], key, dest)
            hit = dest
    return hit


def _wait_backoff(mon: Monitor, r: Run, n: int) -> None:
    mon.stop_event.wait(r.cfg["run"]["rate_limit_backoff_s"] * 2 ** (n - 1))


def _extract_task(r: Run, st: store.Store, b: dict, mon: Monitor) -> dict:
    """All attempts for one batch (runs in a worker thread). Never starts a session once the run is stopping."""
    cfg, rc, bid = r.cfg["extract"], r.cfg["run"], b["batch_id"]
    bad = rate = 0
    while True:
        if mon.stop_event.is_set():
            return {"outcome": "not_started"}
        rec = extract.prepare(r.ex, r.slice, bid, cfg["model"], cfg["prompt_version"], cfg["schema"])
        t = time.time()
        rep = session(r, "extractor", extract.instruction(rec), cfg["model"], rec["batch_file"], rec["output_file"],
                      rec["attempt_id"])
        forced = _session_outcome(rep, mon.session_done("extract", bid, rec["attempt_id"], rep))
        if rep["kind"] == headless.RATE_LIMITED and forced is None and rate < rc["rate_limit_retries"]:
            rate += 1
            r.log(stage="extract", batch=bid, attempt=rec["attempt_id"], status="rate_limited", retry_in_s=
                  rc["rate_limit_backoff_s"] * 2 ** (rate - 1), usage=_usage(rep))
            _wait_backoff(mon, r, rate)
            continue
        if forced:
            r.log(stage="extract", batch=bid, attempt=rec["attempt_id"], status=forced, kind=rep["kind"],
                  reset_text=rep.get("reset_text"), fence=rep["fence"], instructions=rep.get("instructions"),
                  usage=_usage(rep))
            return {"outcome": forced, "attempt": rec["attempt_id"], "reset_text": rep.get("reset_text"),
                    "denied": rep["fence"]["denied"], "instructions": (rep.get("instructions") or {}).get("loaded")}
        res = extract.finalize(r.ex, r.slice, bid, st, r.cfg["verify"], first=False, usage=_usage(rep))
        r.log(stage="extract", batch=bid, attempt=rec["attempt_id"], status=res["status"], kind=rep["kind"],
              errors=res["errors"][:3], quotes=res.get("quotes"), seconds=round(time.time() - t, 1),
              model=res["model_observed"]["status"], fence_records=rep["fence"]["records"],
              fence_self_checks=rep["fence"]["self_checks"] or None, usage=_usage(rep))
        if res["model_observed"]["status"] == runmodel.MISMATCH:
            return {"outcome": "model_mismatch", "attempt": rec["attempt_id"], "why": res["model_observed"]["why"]}
        if res["status"] == "verified":
            dest = r.data / "extract" / f"{bid}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(r.ex.verified / f"{bid}.json", dest)
            r.remember("extract", bid, _extract_key(r, b), dest)
            return {"outcome": "verified", "path": dest, "quotes": res["quotes"], "attempt": rec["attempt_id"]}
        bad += 1
        if bad > rc["retries"]:
            return {"outcome": "failed", "attempt": rec["attempt_id"]}


def _ground_item(r: Run, st: store.Store, bid: str, path: Path) -> tuple[str, object]:
    """('done', path) when no session is needed (no claims, or a cache hit); else ('todo', the task's item)."""
    run = json.loads(path.read_text(encoding="utf-8"))
    pid = f"g{100 + int(bid[1:]):03d}"          # g101, g102, ...: never the bench packets' ids
    if not any(o.get("stall_signals_claimed") for o in run["observations"]):
        g = json.loads(json.dumps(run))
        for o in g["observations"]:
            o.update(stall_signals=[], signal_outcomes=[], published_evidence=[], signal_grounding="grounder")
        dest = r.data / "ground" / f"{bid}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(g, indent=1), encoding="utf-8")
        r.log(stage="ground", batch=bid, status="no_claims")
        return "done", dest
    from . import packet
    prompt = r.root / ".claude" / "agents" / "grounder.md"
    pk = packet.build(run, st, pid, f"{r.slice}")
    key = {"packet_sha256": pk["sha256"], "prompt_sha256": _sha(prompt), "packet_version": pk["version"],
           "session_env": headless.SESSION_ENV_VERSION}
    hit = r.cached("ground", bid, key)
    adopt = r.gp.verified / f"{r.slice}_{bid}.json"
    if not hit and adopt.exists():             # finished by an earlier interrupted run: same packet, same prompt
        g = json.loads(adopt.read_text(encoding="utf-8"))["grounding"]
        if (g["packet_sha256"] == pk["sha256"] and g["prompt_sha256"] == key["prompt_sha256"]
                and (g.get("usage") or {}).get("session_env") == headless.SESSION_ENV_VERSION):
            dest = r.data / "ground" / f"{bid}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(adopt, dest)
            r.remember("ground", bid, key, dest)
            hit = dest
    if hit:
        r.log(stage="ground", batch=bid, status="cache_hit")
        return "done", hit
    return "todo", (bid, path, pid, key)


def _ground_task(r: Run, st: store.Store, item: tuple, mon: Monitor) -> dict:
    bid, path, pid, key = item
    name, rc, model = f"{r.slice}_{bid}", r.cfg["run"], r.cfg["ground"]["model"]
    bad = rate = 0
    while True:
        if mon.stop_event.is_set():
            return {"outcome": "not_started"}
        rec = ground.prepare(path, name, pid, st, paths=r.gp)
        t = time.time()
        rep = session(r, "grounder", ground.instruction(rec), model, rec["packet_file"], rec["output_file"],
                      rec["attempt_id"])
        forced = _session_outcome(rep, mon.session_done("ground", bid, rec["attempt_id"], rep))
        if rep["kind"] == headless.RATE_LIMITED and forced is None and rate < rc["rate_limit_retries"]:
            rate += 1
            r.log(stage="ground", batch=bid, attempt=rec["attempt_id"], status="rate_limited", usage=_usage(rep))
            _wait_backoff(mon, r, rate)
            continue
        if forced:
            r.log(stage="ground", batch=bid, attempt=rec["attempt_id"], status=forced, kind=rep["kind"],
                  reset_text=rep.get("reset_text"), fence=rep["fence"], instructions=rep.get("instructions"),
                  usage=_usage(rep))
            return {"outcome": forced, "attempt": rec["attempt_id"], "reset_text": rep.get("reset_text"),
                    "denied": rep["fence"]["denied"], "instructions": (rep.get("instructions") or {}).get("loaded")}
        res = ground.finalize(name, pid, st, _usage(rep), paths=r.gp)
        r.log(stage="ground", batch=bid, attempt=rec["attempt_id"], status=res["status"], kind=rep["kind"],
              outcomes=res.get("outcomes"), errors=res["errors"][:3], seconds=round(time.time() - t, 1),
              fence_records=rep["fence"]["records"], fence_self_checks=rep["fence"]["self_checks"] or None, usage=_usage(rep))
        if res["model_observed"]["status"] == runmodel.MISMATCH:
            return {"outcome": "model_mismatch", "attempt": rec["attempt_id"], "why": res["model_observed"]["why"]}
        if res["status"] == "verified":
            dest = r.data / "ground" / f"{bid}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(r.gp.verified / f"{name}.json", dest)
            r.remember("ground", bid, key, dest)
            return {"outcome": "verified", "path": dest, "attempt": rec["attempt_id"]}
        bad += 1
        if bad > rc["retries"]:
            return {"outcome": "failed", "attempt": rec["attempt_id"]}


def stage_models(r: Run, st: store.Store, batches: list[dict], workers: int, stop_after: str | None = None) -> dict:
    """Extraction and grounding in one pool of `workers` sessions, with the run's quality checks and stops.

    `stop_after` = "extract" never grounds; "ground" (or None) grounds every verified batch with claims."""
    mon, vcfg = Monitor(r.cfg["run"]), r.cfg["verify"]
    sentinels = set(pick_sentinels(batches, st))
    r.log(stage="quality_plan", canary=f"first fresh batch to finish with at least {vcfg['canary_min_quotes']} checked quotes",
          max_fail_rate=vcfg["canary_max_fail_rate"], sentinels=sorted(sentinels))
    extracted, grounded, failed_ex, failed_gr = {}, {}, [], []
    quality = {"canary": None, "sentinels": {}, "cumulative": {"checked": 0, "rejected": 0}, "flags": [], "fresh": 0}
    pending: deque = deque()
    terminated = 0

    def stop(reason: str, **detail):
        nonlocal terminated
        if mon.request_stop(reason, **detail) and reason in CRITICAL:
            terminated += headless.terminate_all()          # sessions in flight are stopped, never finalized

    def schedule_ground(bid: str, path: Path):
        if stop_after == "extract" or mon.stop_event.is_set():
            return
        kind, val = _ground_item(r, st, bid, path)
        if kind == "done":
            grounded[bid] = val
        else:
            pending.appendleft(("ground", val))          # grounding first: it is short and keeps the tail short

    def check_quality(bid: str, q: dict):
        quality["fresh"] += 1
        cum = quality["cumulative"]
        cum["checked"] += q["checked"]
        cum["rejected"] += q["rejected"]
        tripped = verify.canary_tripped({"quotes": q}, vcfg)
        reasons = []
        if quality["canary"] is None and q["checked"] >= vcfg["canary_min_quotes"]:
            quality["canary"] = {"batch": bid, **q, "tripped": tripped}
            if tripped:
                reasons.append("canary")
        if bid in sentinels:
            quality["sentinels"][bid] = {**q, "qualifies": q["checked"] >= vcfg["canary_min_quotes"], "tripped": tripped}
            if tripped:
                reasons.append("sentinel")
        if tripped and not reasons:
            quality["flags"].append({"batch": bid, **q})
        if verify.canary_tripped({"quotes": cum}, vcfg):
            reasons.append("cumulative")
        if reasons:
            stop(reasons[0], batch=bid, quotes=q, cumulative=dict(cum))

    def handle(kind: str, item, out: dict):
        bid = item["batch_id"] if kind == "extract" else item[0]
        o = out["outcome"]
        if o == "verified":
            if kind == "extract":
                extracted[bid] = out["path"]
                check_quality(bid, out["quotes"])
                schedule_ground(bid, out["path"])
            else:
                grounded[bid] = out["path"]
        elif o == "failed":
            (failed_ex if kind == "extract" else failed_gr).append(bid)
        elif o in CRITICAL or o in CLEAN_STOPS:
            stop(o, stage=kind, batch=bid, attempt=out.get("attempt"), reset_text=out.get("reset_text"),
                 denied=out.get("denied"), why=out.get("why"), instructions=out.get("instructions"))
        # not_started / terminated: the batch stays unfinished (unread), to be resumed

    for b in batches:
        hit = _extract_hit(r, b)
        if hit:
            extracted[b["batch_id"]] = hit
            r.log(stage="extract", batch=b["batch_id"], status="cache_hit")
            schedule_ground(b["batch_id"], hit)
        else:
            pending.append(("extract", b))
    peak = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        running: dict = {}
        try:
            while pending or running:
                while pending and len(running) < workers and not mon.stop_event.is_set():
                    kind, item = pending.popleft()
                    fn = _extract_task if kind == "extract" else _ground_task
                    running[ex.submit(fn, r, st, item, mon)] = (kind, item)
                peak = max(peak, len(running))
                if not running:
                    break
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for fut in done:
                    kind, item = running.pop(fut)
                    handle(kind, item, fut.result())
                if mon.stop_event.is_set():
                    pending.clear()                             # nothing more is scheduled
        except BaseException:                                   # a crash or Ctrl+C: no session is left running
            mon.stop_event.set()
            headless.terminate_all()
            raise
    ids = [b["batch_id"] for b in batches]
    unfinished = [x for x in ids if x not in extracted and x not in failed_ex]
    ground_open = [] if stop_after == "extract" else [x for x in sorted(extracted) if x not in grounded and x not in failed_gr]
    summary = {"batches": len(ids), "n_extracted": len(extracted), "n_grounded": len(grounded),
               "failed_extract": sorted(failed_ex), "failed_ground": sorted(failed_gr), "unfinished": unfinished,
               "ground_unfinished": ground_open, "stop": mon.stop, "terminated": terminated,
               "quality": quality, "sessions": len(mon.sessions), "peak_in_flight": peak,
               "fence_self_checks": mon.self_checks, "fence_self_check_flags": mon.self_check_flags,
               "session_kinds": dict(Counter(s["kind"] for s in mon.sessions))}
    r.log(stage="model_stages", **{k: v for k, v in summary.items() if k != "quality"},
          canary=quality["canary"], sentinels_checked=quality["sentinels"], cumulative=quality["cumulative"],
          flags=quality["flags"])
    (r.data / "sessions.json").write_text(json.dumps(mon.sessions, indent=1), encoding="utf-8")
    return {"extracted": extracted, "grounded": grounded, **summary}


def stage_extract(r: Run, st: store.Store, batches: list[dict], workers: int) -> dict[str, Path]:
    """Extraction only (no grounding); the run's quality checks and stops apply."""
    return stage_models(r, st, batches, workers, stop_after="extract")["extracted"]


def _single_session(r: Run, agent: str, instr: str, model: str, read: str, write: str, attempt_id: str) -> dict:
    """A one-off session for synthesis or act: a fence denial, model trouble or usage limit stops the run."""
    rep = session(r, agent, instr, model, read, write, attempt_id)
    if rep["fence"]["denied"]:
        raise RunStopped("fence_denial", stage=agent, attempt=attempt_id, denied=rep["fence"]["denied"])
    if rep["instructions"]["loaded"]:
        raise RunStopped("instructions_loaded", stage=agent, attempt=attempt_id, instructions=rep["instructions"]["loaded"])
    if rep["fence"].get("self_checks"):           # a documented, still-denied self-check: counted, never a stop
        r.log(stage=agent, attempt=attempt_id, status="fence_self_check", self_checks=rep["fence"]["self_checks"])
    if rep["kind"] == headless.USAGE_LIMIT:
        raise RunStopped("usage_limit", stage=agent, attempt=attempt_id, reset_text=rep.get("reset_text"))
    if rep["output_exists"] and rep["fence"]["records"] == 0:
        raise RunStopped("fence_unobserved", stage=agent, attempt=attempt_id)
    return rep


def _positional_flags(cov: dict, part: str) -> list[dict]:
    """The positional warnings of one part's coverage summary, as report flags that name the part (never a failure)."""
    return [{"kind": w["kind"], "part": part, **{k: v for k, v in w.items() if k != "kind"}}
            for w in ((cov or {}).get("positional") or {}).get("warnings", [])]


def _synth_one(r: Run, st: store.Store, observations: list[dict], sid: str, part: tuple[int, int] | None,
               seed: list[str] | None = None) -> tuple[dict, dict]:
    """One synthesizer session over one list (the whole slice, or part k of n); cached by its input. `seed`: errors of earlier
    failed attempts on this same input (a diagnostic rerun), which become standing requirements from the first attempt on."""
    prompt = r.root / ".claude" / "agents" / "synthesizer.md"
    inp = synth.build_input(observations, st, sid, r.slice, part=part)
    r.synth_inputs.mkdir(parents=True, exist_ok=True)
    (r.synth_inputs / f"{sid}.txt").write_text(inp["text"], encoding="utf-8", newline="")
    key = {"input_sha256": inp["sha256"], "prompt_sha256": _sha(prompt), "model": r.cfg["synth"]["model"],
           "session_env": headless.SESSION_ENV_VERSION}
    hit = r.cached("synth", sid, key)
    if hit:
        cached = json.loads(hit.read_text(encoding="utf-8"))
        cov = synth.coverage(cached, inp, **_floor(r))
        if cov["ok"]:                                  # a cached part is reused only if it also meets the floor
            r.log(stage="synth", input=sid, status="cache_hit", coverage=cov["summary"])
            return inp, cached
        r.synth_staging.mkdir(parents=True, exist_ok=True)
        (r.synth_staging / "rejected").mkdir(parents=True, exist_ok=True)
        shutil.copy(hit, r.synth_staging / "rejected" / f"{sid}-{inp['sha256'][:8]}.cached-rejected.json")
        r.log(stage="synth", input=sid, status="cache_rejected", errors=cov["errors"][:60], coverage=cov["summary"])
    r.synth_staging.mkdir(parents=True, exist_ok=True)
    out_file = r.synth_staging / f"{sid}.json"
    history, prev, standing = [], [], {}       # every failed attempt's errors; the last one's; every kind still required
    note_standing(standing, seed or [])
    for attempt in range(1, _max_attempts(r) + 1):
        attempt_id = f"{sid}-{inp['sha256'][:8]}-{attempt}"
        out_file.unlink(missing_ok=True)
        read, write = r.rel(r.synth_inputs / f"{sid}.txt"), r.rel(out_file)
        instr = _attempt_instruction(f"Read the input file `{read}`. input_id is `{sid}`, attempt_id is "
                                     f"`{attempt_id}`. Follow your instructions and write your JSON to `{write}`. {NO_READBACK}", prev, standing)
        t = time.time()
        rep = _single_session(r, "synthesizer", instr, r.cfg["synth"]["model"], read, write, attempt_id)
        text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        res = synth.check(text, inp, attempt_id)
        cov = {"computed": False, "floor_share": _floor(r)["floor"], "why": "the output could not be read as a list of processes"}
        # the completeness floor (user, 24 Nov 2026): after the structural check, and measured even when that check failed
        c = (synth.coverage(res, inp, **_floor(r)) if res["status"] == "verified"
             else synth.coverage_from_text(text, inp, **_floor(r)))
        if c is not None:
            cov = c["summary"]
            if not c["ok"]:
                res["status"], res["errors"] = "failed", res["errors"] + c["errors"]
        mo = runmodel.from_headless(r.cfg["synth"]["model"], rep["models_observed"])
        r.log(stage="synth", input=sid, part=part, attempt=attempt_id, status=res["status"], coverage=cov, errors=res["errors"][:60],
              n_errors=len(res["errors"]), fed_back_errors=len(prev), standing_kinds=len(standing), feedback_sha256=_note_sha(prev, standing),
              flags=len(res["flags"]), processes=len(res["processes"]),
              candidates=sum(bool(p.get("candidate")) for p in res["processes"]), seconds=round(time.time() - t, 1),
              model=mo["status"], usage=_usage(rep))
        if mo["status"] == runmodel.MISMATCH:
            raise RunStopped("model_mismatch", stage="synth", attempt=attempt_id, why=mo["why"])
        if res["status"] != "verified":
            history.append({"attempt": attempt_id, "n_errors": len(res["errors"]), "errors": res["errors"][:60], "coverage": cov,
                            "fed_back_errors": len(prev), "seconds": round(time.time() - t, 1)})
            _keep_rejected(r, out_file, attempt_id)
            prev = res["errors"]
            note_standing(standing, prev)
        if res["status"] == "verified":
            res["flags"] = list(res["flags"]) + _positional_flags(cov, sid)
            res.update(coverage=cov, input_id=sid, attempt_id=attempt_id, input_sha256=inp["sha256"], prompt_sha256=key["prompt_sha256"],
                       run_note_sha256=headless.NO_READBACK_SHA256,
                       model_observed=mo, usage=_usage(rep))
            dest = r.data / "synth.json" if part is None else r.data / "synth" / f"{sid}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(res, indent=1), encoding="utf-8")
            r.remember("synth", sid, key, dest)
            return inp, res
    raise RunStopped("synthesis_failed", stage="synth", input=sid, part=part, attempts=history)


def _merge(r: Run, st: store.Store, pairs: list[tuple[dict, dict]], observations: list[dict], mid: str = "m001") -> dict:
    """The merger over every part's groups (M4 1d); code checks it and builds the final processes. `mid`: the input id; any id but the
    default keeps its result out of the working `synth.json` (an experiment, user, 24 Nov 2026)."""
    prompt = r.root / ".claude" / "agents" / "merger.md"
    model = r.cfg["synth"]["merge_model"]
    minp = synth.build_merge_input(pairs, st, {o["obs_id"]: o for o in observations}, mid, r.slice)
    r.synth_inputs.mkdir(parents=True, exist_ok=True)
    (r.synth_inputs / f"{mid}.txt").write_text(minp["text"], encoding="utf-8", newline="")
    key = {"input_sha256": minp["sha256"], "prompt_sha256": _sha(prompt), "model": model,
           "parts": [inp["sha256"] for inp, _ in pairs], "session_env": headless.SESSION_ENV_VERSION}
    hit = r.cached("synth", mid, key)
    if hit:
        r.log(stage="merge", status="cache_hit")
        return json.loads(hit.read_text(encoding="utf-8"))
    r.synth_staging.mkdir(parents=True, exist_ok=True)
    out_file = r.synth_staging / f"{mid}.json"
    history, prev, standing = [], [], {}
    for attempt in range(1, _max_attempts(r) + 1):
        attempt_id = f"{mid}-{minp['sha256'][:8]}-{attempt}"
        out_file.unlink(missing_ok=True)
        read, write = r.rel(r.synth_inputs / f"{mid}.txt"), r.rel(out_file)
        instr = _attempt_instruction(f"Read the input file `{read}`. input_id is `{mid}`, attempt_id is "
                                     f"`{attempt_id}`. Follow your instructions and write your JSON to `{write}`. {NO_READBACK}", prev, standing)
        t = time.time()
        rep = _single_session(r, "merger", instr, model, read, write, attempt_id)
        text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        res = synth.merge_check(text, minp, pairs, attempt_id)
        mo = runmodel.from_headless(model, rep["models_observed"])
        r.log(stage="merge", attempt=attempt_id, status=res["status"], errors=res["errors"][:60], n_errors=len(res["errors"]),
              fed_back_errors=len(prev), standing_kinds=len(standing), feedback_sha256=_note_sha(prev, standing), flags=res["flags"][:10],
              groups=len(minp["groups"]), processes=len(res["processes"]), seconds=round(time.time() - t, 1),
              model=mo["status"], usage=_usage(rep))
        if mo["status"] == runmodel.MISMATCH:
            raise RunStopped("model_mismatch", stage="merge", attempt=attempt_id, why=mo["why"])
        if res["status"] != "verified":
            history.append({"attempt": attempt_id, "n_errors": len(res["errors"]), "errors": res["errors"][:60],
                            "fed_back_errors": len(prev), "seconds": round(time.time() - t, 1)})
            _keep_rejected(r, out_file, attempt_id)
            prev = res["errors"]
            note_standing(standing, prev)
        if res["status"] == "verified":
            res["flags"] = list(res["flags"]) + [f for inp, pr in pairs for f in _positional_flags(pr.get("coverage"), inp["input_id"])]
            res.update(input_id=mid, attempt_id=attempt_id, input_sha256=minp["sha256"], prompt_sha256=key["prompt_sha256"],
                       run_note_sha256=headless.NO_READBACK_SHA256,
                       model_observed=mo, usage=_usage(rep),
                       parts=[{"input_id": inp["input_id"], "part": inp["part"], "attempt_id": pr["attempt_id"],
                               "input_sha256": inp["sha256"], "observations": len(inp["obs_ids"]),
                               "groups": len(pr["processes"]), "model_observed": pr.get("model_observed"),
                               "coverage": pr.get("coverage")}
                              for inp, pr in pairs])
            dest = r.data / "synth.json" if mid == "m001" else r.data / "synth" / f"{mid}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(res, indent=1), encoding="utf-8")
            r.remember("synth", mid, key, dest)
            return res
    raise RunStopped("synthesis_failed", stage="merge", input=mid, attempts=history)


def stage_synth(r: Run, st: store.Store, observations: list[dict]) -> dict:
    """One pass when the list fits `synth.chunk_token_budget`; else parts in parallel, then the merge (M4 1d)."""
    sc = r.cfg["synth"]
    if (sc.get("size_split") or {}).get("enabled"):            # size-triggered splitting (user, 24 Nov 2026)
        items = synth.plan(observations, st, sc["chunk_token_budget"], sc["size_split"])
        if len(items) <= 1:
            return _synth_one(r, st, observations, "s001", None)[1]
        mailboxes = max(i["index"] for i in items)
        jobs = [(i["obs"], synth.part_id(i), i["chunk"] or (i["index"], mailboxes)) for i in items]   # a whole mailbox keeps "PART i OF n"
        r.log(stage="synth", status="split", mode="size", parts=len(jobs), ids=[j[1] for j in jobs], observations=[len(j[0]) for j in jobs],
              chunks=sum(bool(i["chunk"]) for i in items), mailboxes=[[i["mailbox"]] for i in items])
    else:
        parts = synth.split(observations, st, sc["chunk_token_budget"], date_split=sc["date_split"])
        if len(parts) <= 1:
            return _synth_one(r, st, observations, "s001", None)[1]
        n = len(parts)
        jobs = [(p, f"s{k:03d}", (k, n)) for k, p in enumerate(parts, start=1)]
        r.log(stage="synth", status="split", parts=n, observations=[len(p) for p in parts],
              mailboxes=[sorted({st.messages[o["msg_key"]].mailbox for o in p}) for p in parts])
    with ThreadPoolExecutor(max_workers=max(1, min(len(jobs), r.cfg["run"]["workers"]))) as ex:
        futs = [ex.submit(_synth_one, r, st, p, sid, part) for p, sid, part in jobs]
        pairs = [f.result() for f in futs]
    return _merge(r, st, pairs, observations)


def stage_act(r: Run, st: store.Store, opp: dict, process: dict, observations: dict[str, dict]) -> dict:
    prompt = r.root / ".claude" / "agents" / "artifact-writer.md"
    n = opp["rank"]
    pid = f"a{n:03d}"
    atype = process["automation"]["artifact_type"]
    quotes = act.quotes_for(process, observations, st)
    pk = act.build_packet(process, atype, quotes, pid)
    r.act_packets.mkdir(parents=True, exist_ok=True)
    (r.act_packets / f"{pid}.txt").write_text(pk["text"], encoding="utf-8", newline="")
    key = {"packet_sha256": pk["sha256"], "prompt_sha256": _sha(prompt), "model": r.cfg["act"]["model"],
           "session_env": headless.SESSION_ENV_VERSION}
    name = f"{process['process_id']}-{atype}"
    hit = r.cached("act", pid, key)
    if hit:
        r.log(stage="act", packet=pid, status="cache_hit")
        return json.loads(hit.read_text(encoding="utf-8"))
    r.act_staging.mkdir(parents=True, exist_ok=True)
    out_file = r.act_staging / f"{pid}.md"
    for attempt in range(1, 2 + r.cfg["run"]["retries"]):
        out_file.unlink(missing_ok=True)
        read, write = r.rel(r.act_packets / f"{pid}.txt"), r.rel(out_file)
        instr = (f"Read the packet file `{read}`. packet_id is `{pid}`. Follow your "
                 f"instructions and write your Markdown to `{write}`. {NO_READBACK}")
        t = time.time()
        rep = _single_session(r, "artifact-writer", instr, r.cfg["act"]["model"], read, write,
                              f"{pid}-{pk['sha256'][:8]}-{attempt}")
        draft = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        chk = act.check(draft, pk, r.cfg["act"])
        mo = runmodel.from_headless(r.cfg["act"]["model"], rep["models_observed"])
        r.log(stage="act", packet=pid, status=chk["status"], errors=chk["errors"], flags=chk["flags"],
              seconds=round(time.time() - t, 1), model=mo["status"], usage=_usage(rep))
        if mo["status"] == runmodel.MISMATCH:
            raise RunStopped("model_mismatch", stage="act", packet=pid, why=mo["why"])
        if chk["status"] == "verified":
            path = act.publish(draft, pk, chk, st, r.out / "artifacts", name)
            rec = {"packet_id": pid, "process_id": process["process_id"], "artifact_type": atype, "path": str(path),
                   "name": name, "cited": chk["cited"], "flags": chk["flags"], "quotes": quotes,
                   "packet_sha256": pk["sha256"], "prompt_sha256": key["prompt_sha256"], "run_note_sha256": headless.NO_READBACK_SHA256,
                   "model_observed": mo}
            dest = r.data / "act" / f"{pid}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(rec, indent=1), encoding="utf-8")
            r.remember("act", pid, key, dest)
            return rec
        r.act_failed.mkdir(parents=True, exist_ok=True)
        (r.act_failed / f"{pid}-{attempt}.md").write_text(draft, encoding="utf-8")
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


def _refuse(agent, prompt, model, timeout=0, cwd=None, **kw):
    raise OfflineCacheMiss(f"offline run: the {agent} would be called ({prompt[:80]!r}); its stage is not cached")


def parse_batches(spec: str | None) -> list[str] | None:
    """'b001-b008,b012' -> ['b001', ..., 'b008', 'b012']."""
    if not spec:
        return None
    out = []
    for part in spec.split(","):
        m = re.fullmatch(r"b(\d{3})(?:-b(\d{3}))?", part.strip())
        if not m:
            raise ValueError(f"batch list {spec!r}: expected items like b001 or b001-b008")
        a, z = int(m.group(1)), int(m.group(2) or m.group(1))
        out += [f"b{i:03d}" for i in range(a, z + 1)]
    return out


def _stopped(r: Run, stop: dict, extra: dict) -> dict:
    code = EXIT_CRITICAL if stop["reason"] in CRITICAL else EXIT_STOPPED
    rec = {"stopped": stop, "exit_code": code, "at": _now(), **extra,
           "resume": "run the same command again: every finished batch and stage is cached" if code == EXIT_STOPPED
           else "a critical check failed: look at the run log before running again"}
    r.log(stage="stopped", **{k: v for k, v in rec.items() if k != "at"})
    (r.out / "logs" / "stop.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    return rec


def run(slice_name: str, workers: int | None = None, offline: bool = False, batch_ids: list[str] | None = None,
        stop_after: str | None = None) -> dict:
    """`offline`: every model stage must be a cache hit; any model call raises instead (no spend, ever).
    `batch_ids` (a measurement) needs `stop_after`: a subset is never synthesized."""
    if batch_ids and not stop_after:
        raise ValueError("a subset of batches is only run with --stop-after extract|ground (never synthesized)")
    if offline:
        headless.run = _refuse
    r = Run(slice_name)
    workers = workers or r.cfg["run"]["workers"]
    r.log(stage="start", version=PIPELINE_VERSION, workers=workers, offline=offline, batches=batch_ids, stop_after=stop_after)
    st = store.load()
    b = stage_batch(r, st)
    chosen = b["batches"]
    if batch_ids:
        known = {x["batch_id"] for x in b["batches"]}
        if set(batch_ids) - known:
            raise ValueError(f"unknown batches for slice {slice_name}: {sorted(set(batch_ids) - known)}")
        chosen = [x for x in b["batches"] if x["batch_id"] in set(batch_ids)]
    ms = stage_models(r, st, chosen, workers, stop_after)
    if ms["stop"]:
        return _stopped(r, ms["stop"], {"unfinished": ms["unfinished"], "ground_unfinished": ms["ground_unfinished"]})
    if stop_after:
        complete = not ms["unfinished"] and not ms["failed_extract"] and (stop_after == "extract" or (
            not ms["ground_unfinished"] and not ms["failed_ground"]))
        r.log(stage="done", status=f"stopped_after_{stop_after}", complete=complete, seconds=round(time.time() - r.t0, 1))
        return {"stop_after": stop_after, "complete": complete, "exit_code": EXIT_OK if complete else EXIT_INCOMPLETE,
                **{k: ms[k] for k in ("batches", "n_extracted", "n_grounded", "failed_extract", "failed_ground",
                                      "unfinished", "ground_unfinished")}}
    extracted, grounded = ms["extracted"], ms["grounded"]
    missing = [x["batch_id"] for x in b["batches"] if x["batch_id"] not in extracted]
    ground_missing = sorted(set(extracted) - set(grounded))      # read, but its observations would be left out
    if ground_missing:
        r.log(stage="ground", status="incomplete", batches=ground_missing)
        missing = sorted(set(missing) | set(ground_missing))
    obs = [o for bid in sorted(grounded) for o in json.loads(grounded[bid].read_text(encoding="utf-8"))["observations"]]
    try:
        res = stage_synth(r, st, obs)
    except RunStopped as e:
        return _stopped(r, {"reason": e.reason, "critical": e.reason in CRITICAL, **e.detail}, {})
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
    sel = act.select(sc["opportunities"], procs, by_obs, st, thr)
    sel_rec = {"mode": sel["mode"], "top_n": sel["top_n"], "skipped": sel["skipped"],
               "selected": [{"process_id": o["process_id"], "name": o["name"], "rank": o["rank"], "dollars_per_month": o["dollars_per_month"],
                             "confidence": o["confidence"]} for o in sel["selected"]]}
    r.log(stage="act_selection", mode=sel["mode"], top_n=sel["top_n"], selected=[x["process_id"] for x in sel_rec["selected"]],
          skipped=[(x["process_id"], x["reasons"]) for x in sel["skipped"][:10]])
    try:
        if sel["selected"]:                       # independent sessions: drafted side by side
            with ThreadPoolExecutor(max_workers=max(1, min(len(sel["selected"]), int(thr.get("parallel", 1))))) as ex:
                futs = [ex.submit(stage_act, r, st, o, procs[o["process_id"]], by_obs) for o in sel["selected"]]
                artifacts = [f.result() for f in futs]
    except RunStopped as e:
        return _stopped(r, {"reason": e.reason, "critical": e.reason in CRITICAL, **e.detail}, {})
    from . import report
    rep = report.build(r, st, b, extracted, grounded, obs, res, evs, sc, qindex, artifacts, missing, sel_rec)
    r.log(stage="done", status="ok" if not missing else "incomplete", seconds=round(time.time() - r.t0, 1),
          unread_batches=missing)
    rep["exit_code"] = EXIT_OK if rep["coverage"]["complete"] else EXIT_INCOMPLETE
    return rep


def _windows():
    import sqlite3
    con = sqlite3.connect(store.DEFAULT_DB)
    rows = list(con.execute("SELECT mailbox, first_utc, last_utc FROM windows"))
    con.close()
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the observation pipeline on a slice (run), start it detached (start), or wait for it (wait).")
    ap.add_argument("action", choices=["run", "start", "wait"])
    ap.add_argument("--slice", default="dev", choices=["dev", "bench", "all"])
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--max-seconds", type=float, default=540, help="wait: return exit code 5 (still running) after this long")
    ap.add_argument("--record", action="store_true", help="run: write the exit code to output/<slice>/logs/run.json (set by `start`)")
    ap.add_argument("--then-serve", action="store_true", help="run: after a clean finish, start the dashboard detached (set by `start`)")
    ap.add_argument("--offline", action="store_true", help="refuse every model call: proves the cache is complete")
    ap.add_argument("--batches", default=None, help="a subset, e.g. b001-b008,b012 (needs --stop-after)")
    ap.add_argument("--stop-after", choices=["extract", "ground"], default=None,
                    help="stop after this stage: a measurement never synthesizes partial data")
    args = ap.parse_args(argv)
    if args.batches and not args.stop_after:
        ap.error("--batches needs --stop-after extract|ground: a subset of batches is never synthesized")
    from . import runctl
    if args.action == "start":
        extra = (["--workers", str(args.workers)] if args.workers else []) + (["--offline"] if args.offline else [])
        rec = runctl.start(args.slice, extra)
        print(json.dumps({k: rec[k] for k in ("pid", "slice", "status", "started_at_utc", "already_running")}))
        return EXIT_OK
    if args.action == "wait":
        code, msg = runctl.wait(args.slice, args.max_seconds)
        print(msg)
        return code
    code = EXIT_INCOMPLETE                                        # what an uncaught exception ends with
    if args.record:
        runctl.attach(args.slice)
    try:
        rep = run(args.slice, args.workers, offline=args.offline, batch_ids=parse_batches(args.batches),
                  stop_after=args.stop_after)
        if "stopped" in rep:
            print(f"STOPPED ({rep['stopped']['reason']}): {rep['resume']}")
        elif "stop_after" in rep:
            print(json.dumps({k: rep[k] for k in ("stop_after", "complete", "failed_extract", "failed_ground", "unfinished")}))
        else:
            print(json.dumps({k: rep[k] for k in ("coverage", "total", "total_range", "opportunities_ranked", "artifacts")}, indent=1)[:2400])
        code = rep["exit_code"]
        if args.record and "stopped" not in rep and "stop_after" not in rep:
            runctl.write_summary(args.slice, rep)                  # what `wait` prints for the driver to report
        if args.then_serve and code == EXIT_OK:                   # the dashboard no longer depends on the driver
            from . import serve
            serve.stop()
            print(json.dumps(serve.detach(args.slice, 8765)))
        return code
    finally:
        if args.record:
            runctl.finish(args.slice, code)


if __name__ == "__main__":
    sys.exit(main())
