r"""M4 step 2a: one per-batch lever run on the benchmark, scored with the frozen scorers (PLAN 5.1, 2a).

    .venv\Scripts\python tools\lever_run.py run <name> [--budget 24000] [--transcript] [--env K=V ...]
                                                       [--settings-json "{...}"] [--no-ground]
    .venv\Scripts\python tools\lever_run.py table      eval/results/m4_levers/levers.md, beside the frozen baseline
    .venv\Scripts\python tools\lever_run.py restore    rebuild the bench batch at the production budget

A run rebuilds the bench batches at `--budget` (threads are never split; the context report records every change
in what a message is shown with), runs one fenced headless extractor per batch in parallel (held to its own two
files), grounds each batch (design I), and scores the combined result with the frozen benchmark and evidence
scorers (src/bench.py, src/evidence_score.py: the yardstick freeze is checked and recorded). Hard requirements
are checked and recorded: every batch verified against its schema, every benchmark message dispositioned, every
model confirmed, every fence record an allow, every published citation re-read byte-exact.

`--transcript` keeps each extraction session's stream-json transcript under data/logs/transcripts/ (it holds
email text, so it stays in data/); code reads only the kinds and sizes of the model's output blocks and the token
counts, never their text. `--env` and `--settings-json` set the session's configuration (the thinking lever); the
effective configuration (CLI version, the model and tools the session reported, what was passed) is recorded.
Results go to eval/results/m4_levers/<name>.json and are never overwritten. Spend: one Haiku session per batch,
plus one grounder per batch with claims. Never the full corpus.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import batch, config, evidence_score, extract, ground, headless, pipeline, runmodel, store  # noqa: E402

OUT = ROOT / "eval" / "results" / "m4_levers"
ARCH = ROOT / "eval" / "results" / "architecture_comparison.json"
BENCH_DIR = ROOT / "data" / "batches" / "bench"
BASELINE = ("C1", "C2", "C3h", "I1", "I2", "I3")


def _usage(rep: dict) -> dict:
    return {"duration_ms": rep.get("duration_ms"), "wall_seconds": rep.get("wall_seconds"), "num_turns": rep.get("num_turns"),
            "tokens": rep.get("tokens"), "thinking_tokens": rep.get("thinking_tokens"), "subagent_tokens": headless.total_tokens(rep), "models_observed": rep.get("models_observed"),
            "notional_cost_usd": rep.get("notional_cost_usd"), "kind": rep.get("kind"),
            "started_at_utc": rep.get("started_at_utc"), "finished_at_utc": rep.get("finished_at_utc"),
            "source": "claude -p report (headless)"}


# --- pure helpers (tested offline) ---

def context_report(batches: list[dict]) -> dict:
    """What each message is shown with: threads kept whole, quoted-text pointers inside their own batch, and the
    neighbours (other threads) each batch holds. A lever that changes these changes context, not only size."""
    where = {k: b["batch_id"] for b in batches for k in b["keys"]}
    crossing = [{"batch": b["batch_id"], "key": p["key"], "points_to": p["points_to"], "in_batch": where.get(p["points_to"])}
                for b in batches for p in b["pointers"] if where.get(p["points_to"]) != b["batch_id"]]
    return {"batches": len(batches),
            "per_batch": [{"batch": b["batch_id"], "messages": len(b["keys"]), "threads": len(b["threads"]),
                           "est_tokens": b["est_tokens"], "pointers": len(b["pointers"]),
                           "mailboxes": sorted({k.split("/")[0] for k in b["keys"]})} for b in batches],
            "threads_split": sorted(t for b in batches for t in b["split_threads"]),
            "pointers_crossing_batches": crossing,
            "messages_whose_text_changed": 0 if not crossing and not any(b["split_threads"] for b in batches) else None,
            "note": "with no split thread and no crossing pointer, every message is shown exactly as in a one-batch "
                    "layout; only the other threads sharing its batch (its neighbours) differ"}


def transcript_breakdown(text: str) -> dict:
    """Kinds and sizes of the model's output blocks in a stream-json transcript, and its token counts. Reads block
    types, lengths and tool names only; never keeps any text."""
    msgs: dict[str, dict] = {}
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant" or not isinstance(ev.get("message"), dict):
            continue
        m = ev["message"]
        rec = msgs.setdefault(m.get("id") or f"anon{len(msgs)}", {"blocks": {}, "output_tokens": 0, "model": m.get("model")})
        rec["output_tokens"] = max(rec["output_tokens"], ((m.get("usage") or {}).get("output_tokens") or 0))
        for blk in m.get("content") or []:
            t = blk.get("type")
            if t == "thinking":
                key, size = "thinking", len(blk.get("thinking") or "")
            elif t == "redacted_thinking":
                key, size = "redacted_thinking", len(blk.get("data") or "")
            elif t == "text":
                key, size = "text", len(blk.get("text") or "")
            elif t == "tool_use":
                key, size = f"tool_use:{blk.get('name')}", len(json.dumps(blk.get("input") or {}))
            else:
                key, size = f"other:{t}", 0
            seen = rec["blocks"].setdefault(key, {"count": 0, "chars": 0})
            seen["count"] += 1
            seen["chars"] += size
    total: dict[str, dict] = {}
    for rec in msgs.values():
        for k, v in rec["blocks"].items():
            t = total.setdefault(k, {"count": 0, "chars": 0})
            t["count"] += v["count"]
            t["chars"] += v["chars"]
    chars = sum(v["chars"] for v in total.values())
    return {"assistant_messages": len(msgs), "output_tokens_summed": sum(r["output_tokens"] for r in msgs.values()),
            "models": sorted({r["model"] for r in msgs.values() if r["model"]}), "blocks": dict(sorted(total.items())),
            "share_of_output_chars": {k: round(v["chars"] / chars, 3) if chars else 0 for k, v in sorted(total.items())},
            "thinking_present": any(k in total for k in ("thinking", "redacted_thinking")),
            "write_calls": total.get("tool_use:Write", {}).get("count", 0)}


def thinking_from_transcript(path: Path) -> int | None:
    """The CLI's own thinking-token count from a kept transcript's result event (None when absent)."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            return ((ev.get("usage") or {}).get("output_tokens_details") or {}).get("thinking_tokens")
    return None


def combine(runs: list[dict]) -> dict:
    """One run from several verified (or grounded) batch outputs, for the frozen scorers. Order is by batch id."""
    runs = sorted(runs, key=lambda r: r.get("batch_id", ""))
    return {"status": "verified" if runs and all(r.get("status") == "verified" for r in runs) else "failed",
            "batch_ids": [r.get("batch_id") for r in runs],
            "messages": [m for r in runs for m in r.get("messages", [])],
            "observations": [o for r in runs for o in r.get("observations", [])],
            "rejected": [x for r in runs for x in r.get("rejected", [])],
            "rejected_evidence": [x for r in runs for x in r.get("rejected_evidence", [])]}


def fence_check(log_dir: Path, read: str, write: str, root: Path = ROOT) -> dict:
    """Did the fence hold for one session: every ALLOWED call was on its own input or output file. Denied calls are
    listed as behaviour (the fence stopped them); a session whose fence logged nothing was not observed."""
    norm = lambda x: str(Path(x) if Path(x).is_absolute() else root / x).replace("\\", "/").lower()
    own = {norm(read), norm(write)}
    recs = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(Path(log_dir).glob("*.json"))] if Path(log_dir).is_dir() else []
    allowed_outside = [r.get("path") for r in recs if r.get("decision") == "allow" and r.get("path") and norm(r["path"]) not in own]
    denied = [{"tool": r.get("tool"), "path": r.get("path")} for r in recs if r.get("decision") != "allow"]
    return {"records": len(recs), "allowed_outside": allowed_outside, "denied": denied,
            "held": bool(recs) and not allowed_outside}


def hard_checks(ext: list[dict], gr: list[dict], keys: list[str], ev: dict) -> dict:
    receipts = sorted(m["msg_key"] for r in ext for m in r["res"].get("messages", [])) if all(
        r["res"]["status"] == "verified" for r in ext) else []
    sessions = ext + [g for g in gr if g.get("usage") is not None]      # a batch with no claims runs no grounder
    out = {"all_batches_verified": all(r["res"]["status"] == "verified" for r in ext),
           "every_message_dispositioned_once": receipts == sorted(keys),
           "every_model_confirmed": all(r["model"] == runmodel.CONFIRMED for r in sessions),
           "fence_held": all(r["fence"]["held"] for r in sessions),
           "citations_all_valid": bool(ev.get("citations", {}).get("all_valid", True)),
           "grounding_verified": all(r["res"]["status"] == "verified" for r in gr)}
    out["all_hold"] = all(out.values())
    return out


# --- the run ---

def write_bench(st, keys, budget, prompt_version, max_messages=None) -> list[dict]:
    cfg = config.load()
    bs = batch.make_batches(st, keys, {**cfg["batch"], "token_budget": budget, "max_messages": max_messages}, prompt_version)
    checks = batch.check_batches(bs, st, keys)
    if not all(checks.values()):
        raise SystemExit(f"batch checks failed: {[k for k, v in checks.items() if not v]}")
    for f in BENCH_DIR.glob("b*.txt"):                     # no stale batch file from another layout
        f.unlink()
    batch.write(bs, BENCH_DIR)
    return bs


def claude_version() -> str:
    try:
        return subprocess.run([shutil.which("claude") or "claude", "--version"], capture_output=True, text=True,
                              timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"unknown ({e})"


def run_lever(name, budget, transcript, env, settings_extra, do_ground, effort=None, max_messages=None) -> dict:
    out_file = OUT / f"{name}.json"
    if out_file.exists():
        raise SystemExit(f"{out_file} exists: results are never overwritten")
    cfg = config.load()
    ecfg = cfg["extract"]
    st = store.load()
    keys = json.loads((ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))["benchmark_keys"]
    bs = write_bench(st, keys, budget, ecfg["prompt_version"], max_messages)
    ctx = context_report(bs)
    p = extract.Paths(ROOT, "bench")
    gp = ground.Paths(ROOT, "bench")
    for b in bs:
        if (ROOT / "data" / "extract" / "bench_runs" / f"{name}_{b['batch_id']}.json").exists():
            raise SystemExit(f"bench_runs/{name}_{b['batch_id']}.json exists: pick a new run name")
    t0 = time.time()

    def extract_one(b):
        bid = b["batch_id"]
        rec = extract.prepare(p, "bench", bid, ecfg["model"], ecfg["prompt_version"], ecfg["schema"])
        log_dir = ROOT / "data" / "logs" / "fence_sessions" / "bench" / rec["attempt_id"]
        tpath = ROOT / "data" / "logs" / "transcripts" / f"{name}_{bid}.jsonl" if transcript else None
        rep = headless.run("extractor", extract.instruction(rec), ecfg["model"], timeout=cfg["run"]["timeout_s"],
                           read=rec["batch_file"], write=rec["output_file"], log_dir=str(log_dir), env_extra=env,
                           extra_settings=settings_extra, transcript=str(tpath) if tpath else None, effort=effort)
        fence = fence_check(log_dir, rec["batch_file"], rec["output_file"])
        res = extract.finalize(p, "bench", bid, st, cfg["verify"], usage=_usage(rep))
        archived = None
        if res["status"] == "verified":
            archived = ROOT / "data" / "extract" / "bench_runs" / f"{name}_{bid}.json"
            shutil.copy(p.verified / f"{bid}.json", archived)
        bd = transcript_breakdown(tpath.read_text(encoding="utf-8")) if tpath and tpath.exists() else None
        return {"batch": bid, "attempt": rec["attempt_id"], "res": res, "archived": archived, "fence": fence,
                "model": res["model_observed"]["status"], "usage": _usage(rep), "config": rep.get("config"),
                "breakdown": bd, "est_tokens": b["est_tokens"]}
    with ThreadPoolExecutor(max_workers=len(bs)) as ex:
        ext = list(ex.map(extract_one, bs))
    t_ext = time.time() - t0
    gr, t_gr = [], 0.0
    if do_ground and all(r["archived"] for r in ext):
        t1 = time.time()

        def ground_one(item):
            k, r = item
            run = json.loads(r["archived"].read_text(encoding="utf-8"))
            gname, pid = f"{name}_{r['batch']}", f"g{k:03d}"
            if not any(o.get("stall_signals_claimed") for o in run["observations"]):
                g = json.loads(json.dumps(run))
                for o in g["observations"]:
                    o.update(stall_signals=[], signal_outcomes=[], published_evidence=[], signal_grounding="grounder")
                return {"batch": r["batch"], "grounded": g, "res": {"status": "verified"}, "model": runmodel.CONFIRMED,
                        "fence": {"records": 0, "denied": [], "allowed_outside": [], "held": True}, "usage": None,
                        "note": "no claims: no session"}
            rec = ground.prepare(r["archived"], gname, pid, st, paths=gp)
            log_dir = ROOT / "data" / "logs" / "fence_sessions" / "bench" / f"{gname}-{rec['attempt_id']}"
            rep = headless.run("grounder", ground.instruction(rec), cfg["ground"]["model"], timeout=cfg["run"]["timeout_s"],
                               read=rec["packet_file"], write=rec["output_file"], log_dir=str(log_dir))
            res = ground.finalize(gname, pid, st, _usage(rep), paths=gp)
            g = json.loads((gp.verified / f"{gname}.json").read_text(encoding="utf-8")) if res["status"] == "verified" else None
            return {"batch": r["batch"], "grounded": g, "res": res, "model": res["model_observed"]["status"],
                    "fence": fence_check(log_dir, rec["packet_file"], rec["output_file"]), "usage": _usage(rep),
                    "claims": res.get("claims"),
                    "outcomes": res.get("outcomes")}
        with ThreadPoolExecutor(max_workers=len(ext)) as ex:
            gr = list(ex.map(ground_one, enumerate(ext, start=1)))
        t_gr = time.time() - t1
    y = evidence_score.load_yardstick()
    if gr and all(g["grounded"] for g in gr):
        scored_run, arch = combine([dict(g["grounded"], batch_id=g["batch"]) for g in gr]), "I"
    else:
        scored_run, arch = combine([dict(r["res"], batch_id=r["batch"]) for r in ext]), "clean-C"
    ev = evidence_score.evaluate(scored_run, arch, y) if scored_run["status"] == "verified" else {}
    combined_path = ROOT / "data" / "extract" / "bench_runs" / f"{name}_{'grounded' if arch == 'I' else 'combined'}.json"
    combined_path.write_text(json.dumps(scored_run, indent=1), encoding="utf-8")
    result = {
        "name": name, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "architecture": arch,
        "lever": {"token_budget": budget, "max_messages": max_messages, "env": env, "settings": settings_extra,
                  "transcript": transcript, "effort": effort},
        "effective_config": {"claude_version": claude_version(), "extract_model": ecfg["model"],
                             "prompt_version": ecfg["prompt_version"], "schema": ecfg["schema"],
                             "prompt_sha256": ext[0]["res"].get("prompt_sha256"),
                             "sessions": [{"batch": r["batch"], **(r["config"] or {})} for r in ext]},
        "context": ctx,
        "hard_requirements": hard_checks(ext, gr, keys, ev),
        "fence_denials": [{"batch": r["batch"], "stage": "extract", **d} for r in ext for d in r["fence"]["denied"]]
                         + [{"batch": g["batch"], "stage": "ground", **d} for g in gr for d in g["fence"]["denied"]],
        "quality": {k: ev.get(k) for k in ("extraction", "end_to_end_published_signal_recall", "published_correct_any_evidence",
                                           "published_precision", "published_counts", "citations", "freeze", "by_signal")}
        if ev else None,
        "quality_detail": {"published_false": [f"{x['id']} {x['signal']}" for x in ev.get("published_false", [])],
                           "negatives_published": [f"{x['id']} {x['signal']}" for x in ev.get("negative_claims_published", [])],
                           "categories": ev.get("categories")} if ev else None,
        "runtime": {"extraction_wall_s": round(t_ext, 1), "grounding_wall_s": round(t_gr, 1),
                    "extraction_sessions_s": [round((r["usage"]["duration_ms"] or 0) / 1000, 1) for r in ext],
                    "grounding_sessions_s": [round((g["usage"]["duration_ms"] or 0) / 1000, 1) for g in gr if g.get("usage")]},
        "tokens": {"extraction": [r["usage"]["tokens"] for r in ext], "grounding": [g["usage"]["tokens"] for g in gr if g.get("usage")],
                   "extraction_output_total": sum((r["usage"]["tokens"] or {}).get("output_tokens") or 0 for r in ext),
                   "extraction_thinking_total": sum(r["usage"].get("thinking_tokens") or 0 for r in ext)},
        "breakdown": [{"batch": r["batch"], **r["breakdown"]} for r in ext if r["breakdown"]],
        "batches": [{"batch": r["batch"], "attempt": r["attempt"], "status": r["res"]["status"], "errors": r["res"]["errors"][:5],
                     "quotes": r["res"].get("quotes"), "model": r["model"], "fence": r["fence"],
                     "archived": str(r["archived"].relative_to(ROOT)) if r["archived"] else None} for r in ext],
        "grounding": [{"batch": g["batch"], "status": g["res"]["status"], "claims": g.get("claims"), "outcomes": g.get("outcomes"),
                       "model": g["model"], "fence": g["fence"], "note": g.get("note")} for g in gr],
        "scored_run": str(combined_path.relative_to(ROOT))}
    OUT.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return result


# --- the table ---

def table() -> str:
    arch = json.loads(ARCH.read_text(encoding="utf-8"))
    rows = {r["run"]: r for rs in arch["architectures"].values() for r in rs}
    md = ["<!-- generated by tools/lever_run.py table from eval/results; do not edit by hand -->", "",
          "| Run | Lever | Hard req. | Found /50 | State | Stall claimed exact | Stall or not | Published w/ approved evidence /30 "
          "| False on labelled | Negatives | Extraction s (sessions) | Grounding s | Extraction output tokens | Thinking tokens |",
          "|" + "---|" * 14]
    for tag in BASELINE[3:]:
        r = rows[tag]
        src = rows[r["extraction_from"]]
        ec = src["extraction_cost"]
        md.append(f"| {tag} (frozen baseline, from {r['extraction_from']}) | clean-C 24k, M3 | - | {r['activities_found']} "
                  f"| {r['action_state_agreement']} | {r['stall_agreement']} | {r['stall_any_agreement']} | {r['e2e'].split('/')[0]} "
                  f"| {r['published_counts']['false_on_labelled_activities']} | {len(r['negatives_published'])} "
                  f"| {ec['seconds']} ({ec['harness']}) | {(r['grounding_cost'] or {}).get('seconds', '-')} | - | - |")
    for f in sorted(OUT.glob("*.json")):
        x = json.loads(f.read_text(encoding="utf-8"))
        if "lever" not in x:                              # consistency.json, batch_time_model.json
            continue
        q = (x.get("quality") or {}).get("extraction") or {}
        lever = f"{x['lever']['token_budget'] // 1000}k" + (f" <= {x['lever']['max_messages']} msgs" if x["lever"].get("max_messages") else "") +                 (f" effort {x['lever']['effort']}" if x["lever"].get("effort") else "") + \
                (f" env {x['lever']['env']}" if x["lever"]["env"] else "") + \
                (f" settings {x['lever']['settings']}" if x["lever"]["settings"] else "")
        tk = x["tokens"]
        th = tk.get("extraction_thinking_total")
        if th is None:                                   # runs before thinking was recorded: from their transcripts
            th = sum(thinking_from_transcript(ROOT / "data" / "logs" / "transcripts" / f"{x['name']}_{b['batch']}.jsonl")
                     for b in x["batches"])
        th = f"{th} ({round(100 * th / tk['extraction_output_total'])}%)" if th is not None and tk["extraction_output_total"] else "-"
        pc = (x.get("quality") or {}).get("published_counts") or {}
        md.append(f"| {x['name']} | {lever} | {'pass' if x['hard_requirements']['all_hold'] else 'FAIL'} "
                  f"| {q.get('activities_found', '-')} | {q.get('action_state_agreement', '-')} | {q.get('stall_agreement', '-')} "
                  f"| {q.get('stall_any_agreement', '-')} | {str((x.get('quality') or {}).get('end_to_end_published_signal_recall', '-')).split('/')[0]} "
                  f"| {pc.get('false_on_labelled_activities', '-')} | {len((x.get('quality_detail') or {}).get('negatives_published') or [])} "
                  f"| {x['runtime']['extraction_wall_s']} ({', '.join(map(str, x['runtime']['extraction_sessions_s']))}) "
                  f"| {x['runtime']['grounding_wall_s']} | {x['tokens']['extraction_output_total']} | {th} |")
    md += ["", "Baseline rows are copied by code from eval/results/architecture_comparison.json (frozen M3 runs). "
               "Found, state and stall are unadjudicated where a run lists low-meaning matches "
               "(see each result file's quality.extraction.unadjudicated_low_meaning)."]
    text = "\n".join(md) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "levers.md").write_text(text, encoding="utf-8")
    return text


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--budget", type=int, default=None)
    r.add_argument("--transcript", action="store_true")
    r.add_argument("--env", action="append", default=[], help="K=V for the session environment (never an API key)")
    r.add_argument("--settings-json", default=None)
    r.add_argument("--no-ground", action="store_true")
    r.add_argument("--effort", choices=headless.EFFORTS, default=None, help="claude --effort for the extractor sessions")
    r.add_argument("--max-messages", type=int, default=None, help="also start a new batch past this many messages")
    sub.add_parser("table")
    sub.add_parser("restore")
    a = ap.parse_args(argv)
    cfg = config.load()
    if a.cmd == "restore":
        st = store.load()
        keys = json.loads((ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))["benchmark_keys"]
        bs = write_bench(st, keys, cfg["batch"]["token_budget"], cfg["extract"]["prompt_version"])
        print("bench batches restored:", [(b["batch_id"], b["est_tokens"], b["fingerprint"][:8]) for b in bs])
        return 0
    if a.cmd == "table":
        print(table())
        return 0
    env = dict(x.split("=", 1) for x in a.env)
    res = run_lever(a.name, a.budget or cfg["batch"]["token_budget"], a.transcript, env,
                    json.loads(a.settings_json) if a.settings_json else None, not a.no_ground, a.effort, a.max_messages)
    show = {k: res[k] for k in ("hard_requirements", "fence_denials", "runtime", "tokens")}
    show["quality"] = {k: (res.get("quality") or {}).get("extraction", {}).get(k) for k in
                       ("activities_found", "action_state_agreement", "stall_agreement", "stall_any_agreement",
                        "unadjudicated_low_meaning")}
    show["published"] = (res.get("quality") or {}).get("end_to_end_published_signal_recall")
    show["context"] = {k: res["context"][k] for k in ("batches", "threads_split", "pointers_crossing_batches")}
    show["breakdown"] = [{k: b[k] for k in ("output_tokens_summed", "share_of_output_chars", "thinking_present", "write_calls")}
                         for b in res["breakdown"]]
    print(json.dumps(show, indent=1, default=str))
    return 0 if res["hard_requirements"]["all_hold"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
