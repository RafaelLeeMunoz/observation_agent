"""Design I and J: ground claimed stall signals with a separate, fenced grounder (PLAN 3.3, HI_DESIGN).

    prepare  : build the packet for one verified extraction batch (src/packet.py), give the attempt an
               id, move earlier staging aside, print the grounder's instruction
    finalize : verify the staged verdicts against THIS attempt and the packet manifest, map every cited
               sentence id to raw-byte offsets, and write a grounded copy of the extraction where only
               supported claims are published (data/ground/verified/<run>.json)

What the grounder decides: supported / unsupported / unsure for each claim, and which sentences show
it. What code decides: whether the output is well formed and complete (one verdict per claim), whether
each cited id exists and lies in a section the claim may cite, the evidence text (cut from the raw
file at the sentence's offsets, never copied from model output), and what is published. The
grounder cannot add claims or change an activity, its state or its quote: those are copied from the
extraction unchanged, and finalize fails if the claims it grounded differ from the packet's.

Outcomes per claim: published (supported, 1-3 valid ids); unsupported (the grounder says the text does
not show it); unresolved (unsure); rejected (supported with no valid citation, or a malformed verdict).
Every claim keeps a `signal_outcomes` record, so nothing is dropped silently.

Run:  .venv\\Scripts\\python -m src.ground prepare  --run data/extract/bench_runs/haiku_v8c_b001.json --name I_v8c --packet g001
      .venv\\Scripts\\python -m src.ground finalize --name I_v8c --packet g001
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import packet, runmodel, schema, store, verify

ROOT = Path(__file__).resolve().parent.parent
GROUND = ROOT / "data" / "ground"
SCHEMA = "grounder-v1"
PROMPT = ROOT / ".claude" / "agents" / "grounder.md"
MAX_IDS = 3
PUBLISHED, UNSUPPORTED, UNRESOLVED, REJECTED = "published", "unsupported", "unresolved", "rejected"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _aside(path: Path, dest: Path, tag: str) -> None:
    if path.exists():
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest / f"{path.stem}.{tag}{path.suffix}"))


def prepare(run_file: Path, name: str, packet_id: str, st: store.Store, claim_filter=None,
            root: Path = GROUND, selection: dict | None = None) -> dict:
    run = json.loads(run_file.read_text(encoding="utf-8"))
    if run.get("status") != "verified":
        raise ValueError(f"{run_file} is not a verified extraction")
    pk = packet.build(run, st, packet_id, name, claim_filter)
    pk_dir = root / "packets" / name
    packet.write(pk, pk_dir)
    att_dir = root / "attempts"
    att_dir.mkdir(parents=True, exist_ok=True)
    rec_path = att_dir / f"{name}__{packet_id}.json"
    prev = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.exists() else None
    n = prev["attempt_no"] + 1 if prev else 1
    attempt_id = f"{packet_id}-{pk['sha256'][:8]}-{n}"
    _aside(root / "staging" / f"{packet_id}.json", root / "staging" / "rejected", f"superseded-before-{attempt_id}")
    (root / "staging").mkdir(parents=True, exist_ok=True)
    rec = {"name": name, "packet_id": packet_id, "attempt_id": attempt_id, "attempt_no": n,
           "run_file": str(run_file.relative_to(ROOT) if run_file.is_absolute() and ROOT in run_file.parents else run_file),
           "run_attempt": run.get("attempt_id"), "packet_sha256": pk["sha256"], "packet_version": pk["version"],
           "claims": len(pk["claims"]), "est_tokens": pk["est_tokens"], "selection": selection,
           "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest() if PROMPT.exists() else None,
           "packet_file": f"data/ground/packets/{name}/{packet_id}.txt",
           "output_file": f"data/ground/staging/{packet_id}.json",
           "started_at_utc": _now(), "started_monotonic": time.time()}
    rec_path.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return rec


def instruction(rec: dict) -> str:
    return (f"Read the packet file `{rec['packet_file']}`. packet_id is `{rec['packet_id']}`, attempt_id is "
            f"`{rec['attempt_id']}`. Follow your instructions and write your JSON to `{rec['output_file']}`.")


def check_verdicts(staged_text: str, manifest: dict, attempt_id: str) -> dict:
    """Validate grounder output against the packet (pure). Returns {status, errors, verdicts}."""
    res = {"status": "failed", "errors": [], "verdicts": {}}
    try:
        data = json.loads(staged_text)
    except json.JSONDecodeError as e:
        res["errors"].append(f"invalid JSON: {e}")
        return res
    errs = schema.validate(data, schema.load(f"{SCHEMA}.schema.json"))
    if errs:
        res["errors"] += [f"schema: {e}" for e in errs[:20]]
        return res
    if data["packet_id"] != manifest["packet_id"]:
        res["errors"].append(f"packet_id {data['packet_id']!r} is not {manifest['packet_id']!r}")
    if data["attempt_id"] != attempt_id:
        res["errors"].append(f"attempt_id {data['attempt_id']!r} is not the current attempt {attempt_id!r}")
    want = [c["claim_id"] for c in manifest["claims"]]
    got = [v["claim_id"] for v in data["verdicts"]]
    if sorted(set(want) - set(got)):
        res["errors"].append(f"no verdict for claim(s) {sorted(set(want) - set(got))[:8]}")
    if sorted(set(got) - set(want)):
        res["errors"].append(f"verdicts for claims not in the packet: {sorted(set(got) - set(want))[:8]}")
    if len(got) != len(set(got)):
        res["errors"].append("a claim has more than one verdict")
    if res["errors"]:
        return res
    res["verdicts"] = {v["claim_id"]: v for v in data["verdicts"]}
    res["status"] = "verified"
    return res


def evidence_for(claim: dict, verdict: dict, manifest: dict, st: store.Store) -> tuple[str, list[dict], str]:
    """(outcome, evidence records, reason) for one claim. Evidence is cut from the raw file by code."""
    v, ids = verdict["verdict"], verdict["sentence_ids"]
    if v == "unsupported":
        return UNSUPPORTED, [], verdict["reason"]
    if v == "unsure":
        return UNRESOLVED, [], verdict["reason"]
    if not ids:
        return REJECTED, [], "supported, but no sentence cited"
    if len(ids) > MAX_IDS:
        return REJECTED, [], f"supported, but {len(ids)} sentences cited (at most {MAX_IDS})"
    bad = [i for i in ids if i not in manifest["sentences"]]
    if bad:
        return REJECTED, [], f"cited sentence id(s) not in the packet: {bad}"
    outside = [i for i in ids if manifest["sentences"][i]["section"] not in claim["permitted_sections"]]
    if outside:
        return REJECTED, [], f"cited sentence(s) {outside} outside the sections this claim may cite"
    out = []
    for i in ids:
        s = manifest["sentences"][i]
        m = st.messages[s["msg_key"]]
        region, block = verify.region_of(m, s["start"], s["end"])
        date, basis = verify.evidence_date(m, block)
        scope = "own" if i in claim["own_sentences"] else ("context" if s["msg_key"] == claim["msg_key"] else "linked")
        out.append({"signal": claim["signal"], "sentence_id": i, "msg_key": s["msg_key"],
                    "original_path": m.original_path, "start": s["start"], "end": s["end"],
                    "quote_raw": m.text[s["start"]:s["end"]], "region": region,
                    "block": block.index if block else None, "evidence_date": date, "date_basis": basis,
                    **verify.attribution(m, region, block), "scope": scope, "source": "grounder_sentence"})
    return PUBLISHED, out, verdict["reason"]


def apply(run: dict, manifest: dict, verdicts: dict, st: store.Store, only_selected: bool = False) -> dict:
    """A copy of the extraction where each grounded claim is published only when supported.

    Claims not in the packet (design J leaves some out) keep the run's own outcome when
    `only_selected` is set, else are recorded as not grounded. Activities are never changed."""
    by_obs: dict[str, list] = {}
    for c in manifest["claims"]:
        by_obs.setdefault(c["obs_id"], []).append(c)
    out = json.loads(json.dumps(run))
    for o in out["observations"]:
        claimed = list(o.get("stall_signals_claimed", o["stall_signals"]))
        prior = {x["signal"]: x for x in o.get("signal_outcomes", [])}
        prior_ev = [e for e in o.get("published_evidence", [])]
        outcomes, evidence = [], []
        for sg in claimed:
            c = next((c for c in by_obs.get(o["obs_id"], []) if c["signal"] == sg), None)
            if c is None:
                if only_selected and sg in prior:
                    outcomes.append({**prior[sg], "grounded": False})
                    evidence += [e for e in prior_ev if e["signal"] == sg]
                else:
                    outcomes.append({"signal": sg, "outcome": "not_grounded", "reason": "not sent to the grounder",
                                     "grounded": False})
                continue
            outcome, ev, why = evidence_for(c, verdicts[c["claim_id"]], manifest, st)
            outcomes.append({"signal": sg, "outcome": outcome, "reason": why, "grounded": True,
                             "claim_id": c["claim_id"], "verdict": verdicts[c["claim_id"]]["verdict"],
                             "sentence_ids": verdicts[c["claim_id"]]["sentence_ids"]})
            evidence += ev
        o["stall_signals_claimed"] = claimed
        o["stall_signals"] = [x["signal"] for x in outcomes if x["outcome"] == PUBLISHED]
        o["signal_outcomes"] = outcomes
        o["published_evidence"] = evidence
        o["stall_evidence"] = [e for e in o.get("stall_evidence", []) if e["signal"] in o["stall_signals"]
                               and any(x["signal"] == e["signal"] and not x["grounded"] for x in outcomes)]
        o["signal_grounding"] = "grounder" if not only_selected else "selective"
    return out


def finalize(name: str, packet_id: str, st: store.Store, usage: dict | None = None, root: Path = GROUND,
             only_selected: bool = False) -> dict:
    rec = json.loads((root / "attempts" / f"{name}__{packet_id}.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "packets" / name / f"{packet_id}.manifest.json").read_text(encoding="utf-8"))
    staged = root / "staging" / f"{packet_id}.json"
    text = staged.read_text(encoding="utf-8") if staged.exists() else ""
    chk = check_verdicts(text, manifest, rec["attempt_id"])
    if not staged.exists():
        chk["errors"].insert(0, "the grounder wrote no output file for this attempt")
    run = json.loads((ROOT / rec["run_file"]).read_text(encoding="utf-8"))
    res = {"name": name, "packet_id": packet_id, "attempt_id": rec["attempt_id"], "status": chk["status"],
           "errors": chk["errors"], "packet_sha256": rec["packet_sha256"], "prompt_sha256": rec["prompt_sha256"],
           "schema": SCHEMA, "claims": rec["claims"], "selection": rec.get("selection"),
           "seconds": round(time.time() - rec["started_monotonic"], 1), "usage": usage, "finished_at_utc": _now(),
           "model_observed": runmodel.from_headless("haiku", (usage or {}).get("models_observed"))}
    if res["model_observed"]["status"] == runmodel.MISMATCH:
        res["status"], res["errors"] = "failed", ["model mismatch: " + res["model_observed"]["why"]] + chk["errors"]
        chk["status"] = "failed"
    if chk["status"] == "verified":
        grounded = apply(run, manifest, chk["verdicts"], st, only_selected)
        grounded["grounding"] = {k: res[k] for k in ("name", "packet_id", "attempt_id", "packet_sha256",
                                                     "prompt_sha256", "schema", "claims", "selection", "usage",
                                                     "model_observed")}
        outs = [x for o in grounded["observations"] for x in o["signal_outcomes"] if x.get("grounded")]
        res["outcomes"] = {k: sum(x["outcome"] == k for x in outs) for k in (PUBLISHED, UNSUPPORTED, UNRESOLVED, REJECTED)}
        (root / "verified").mkdir(parents=True, exist_ok=True)
        (root / "verified" / f"{name}.json").write_text(json.dumps(grounded, indent=1) + "\n", encoding="utf-8")
        res["verdicts"] = chk["verdicts"]
    _aside(staged, root / "staging" / ("accepted" if chk["status"] == "verified" else "rejected"), rec["attempt_id"])
    (root / "results").mkdir(parents=True, exist_ok=True)
    (root / "results" / f"{name}__{rec['attempt_id']}.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    logs = ROOT / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    from .extract import shared_lock          # the same run log as extraction, written by parallel workers
    with shared_lock(logs), (logs / "runlog.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"stage": "ground", **{k: v for k, v in res.items() if k != "verdicts"}}) + "\n")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Prepare or finalize one grounding attempt.")
    ap.add_argument("action", choices=["prepare", "finalize"])
    ap.add_argument("--run", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--packet", default="g001")
    args = ap.parse_args(argv)
    st = store.load()
    if args.action == "prepare":
        print(instruction(prepare(args.run, args.name, args.packet, st)))
        return 0
    res = finalize(args.name, args.packet, st)
    print(f"ground {args.name} {args.packet}: {res['status']}; {res.get('outcomes')}; errors {res['errors']}")
    return 0 if res["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
