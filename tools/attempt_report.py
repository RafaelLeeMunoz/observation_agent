r"""Every model attempt of a run, with its reason, runtime and usage, and how often a later attempt was needed (user, 24 Nov 2026).

Usage: .venv\Scripts\python tools\attempt_report.py [--root DIR] [--slice all] [--log PATH ...] [--out PATH]

Reads the run log(s) the pipeline already writes (`output/<slice>/logs/runlog.jsonl`: one record per attempt with status, errors, seconds and
usage) and tabulates, per stage (extract, ground, synth, merge, act): attempts by attempt number (tried, verified, failed, other), the
reason of every failure (the error kind, so the same slip counts once per kind), seconds and subagent tokens spent on failed attempts, and for
each input its final outcome. For synthesis and the merge it answers the fourth-attempt question: how many inputs needed a fourth attempt,
and whether it resolved the errors. Code only; nothing is changed. Writes JSON next to the log unless --out is given.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STAGES = ("extract", "ground", "synth", "merge", "act")


def kind_of(error: str) -> str:
    """The requirement an error belongs to (the pipeline's own grouping: one kind per slip, numbers and ids removed)."""
    e = str(error)
    if e.startswith("coverage:"):
        return "coverage floor"
    if e.startswith("positional coverage:"):
        return "positional coverage: " + e.split(" of the input")[0].removeprefix("positional coverage: the ")
    if "catch-all process" in e:
        return "catch-all process"
    e = re.sub(r"\b[A-Za-z]+\d*-o\d+\b|\bP\d+\b|\bs\d{3}\.P\d+\b|\bb\d+-o\d+\b", "#", e)
    return re.sub(r"\d+", "#", e)[:110]


def attempt_no(attempt_id: str) -> int:
    m = re.search(r"-(\d+)$", str(attempt_id))
    return int(m.group(1)) if m else 0


def read_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def input_of(r: dict) -> str:
    return str(r.get("batch") or r.get("input") or r.get("packet") or str(r.get("attempt", "")).split("-")[0])


def build(rows: list[dict]) -> dict:
    atts = []
    for r in rows:
        if r.get("stage") in STAGES and r.get("attempt"):
            u = r.get("usage") or {}
            atts.append({"stage": r["stage"], "input": input_of(r), "n": attempt_no(r["attempt"]), "attempt": r["attempt"],
                         "status": r.get("status"), "session_kind": r.get("kind"), "seconds": r.get("seconds"),
                         "tokens": u.get("subagent_tokens") or 0, "errors": [str(e) for e in (r.get("errors") or [])],
                         "n_errors": r.get("n_errors", len(r.get("errors") or [])), "fed_back_errors": r.get("fed_back_errors", 0),
                         "coverage_share": (r.get("coverage") or {}).get("share") if isinstance(r.get("coverage"), dict) else None,
                         "at": r.get("at")})
    out = {"attempts": atts, "stages": {}}
    for stage in STAGES:
        sa = [a for a in atts if a["stage"] == stage]
        if not sa:
            continue
        by_n = defaultdict(list)
        for a in sa:
            by_n[a["n"]].append(a)
        per_input = defaultdict(list)
        for a in sa:
            per_input[a["input"]].append(a)
        final = {}
        for i, lst in per_input.items():
            lst.sort(key=lambda a: (a["n"], a["at"] or ""))
            final[i] = {"attempts": len(lst), "outcome": "verified" if any(a["status"] == "verified" for a in lst) else lst[-1]["status"],
                        "verified_on_attempt": next((a["n"] for a in lst if a["status"] == "verified"), None)}
        failed = [a for a in sa if a["status"] != "verified"]
        kinds = Counter(k for a in failed for k in {kind_of(e) for e in a["errors"]})
        by_status = Counter(a["status"] for a in failed)
        rec = {"attempts": len(sa), "inputs": len(per_input), "verified_attempts": len(sa) - len(failed), "failed_attempts": len(failed),
               "failed_by_status": dict(by_status),
               "by_attempt_number": {str(n): {"tried": len(v), "verified": sum(a["status"] == "verified" for a in v),
                                              "failed": sum(a["status"] != "verified" for a in v)} for n, v in sorted(by_n.items())},
               "inputs_needing_more_than_one_attempt": sum(f["attempts"] > 1 for f in final.values()),
               "inputs_never_verified": sorted(i for i, f in final.items() if f["outcome"] != "verified"),
               "seconds_all": round(sum(a["seconds"] or 0 for a in sa), 1), "seconds_failed": round(sum(a["seconds"] or 0 for a in failed), 1),
               "tokens_all": sum(a["tokens"] for a in sa), "tokens_failed": sum(a["tokens"] for a in failed),
               "failure_reasons": [{"kind": k, "attempts": n} for k, n in kinds.most_common(12)]}
        if stage in ("synth", "merge"):
            first = by_n.get(1, [])
            rec["first_attempt_failure_rate"] = round(sum(a["status"] != "verified" for a in first) / len(first), 3) if first else None
            fourth = by_n.get(4, [])
            rec["fourth_attempt"] = {"inputs_that_needed_one": len(fourth), "resolved_the_errors": sum(a["status"] == "verified" for a in fourth),
                                     "still_failing": sum(a["status"] != "verified" for a in fourth),
                                     "inputs": [{"input": a["input"], "status": a["status"], "seconds": a["seconds"], "tokens": a["tokens"],
                                                 "reasons": sorted({kind_of(e) for e in a["errors"]})} for a in fourth]}
        rec["inputs_detail"] = final
        out["stages"][stage] = rec
    return out


def markdown(rep: dict) -> str:
    lines = ["| stage | attempts | inputs | failed | failed seconds | failed tokens | first attempt fails | 4th attempts (resolved) |", "|---|---|---|---|---|---|---|---|"]
    for s, r in rep["stages"].items():
        f4 = r.get("fourth_attempt")
        lines.append(f"| {s} | {r['attempts']} | {r['inputs']} | {r['failed_attempts']} | {r['seconds_failed']} | {r['tokens_failed']:,} | "
                     f"{r.get('first_attempt_failure_rate', '')} | {f'{f4['inputs_that_needed_one']} ({f4['resolved_the_errors']})' if f4 else ''} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--slice", default="all")
    ap.add_argument("--log", nargs="*")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    paths = [Path(p) for p in a.log] if a.log else [Path(a.root) / "output" / a.slice / "logs" / "runlog.jsonl"]
    rep = build(read_rows(paths))
    rep["logs"] = [str(p) for p in paths]
    out = Path(a.out) if a.out else paths[0].with_name("attempts.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    print(markdown(rep))
    for s in ("synth", "merge"):
        r = rep["stages"].get(s)
        if r:
            print(f"\n{s}: by attempt number {r['by_attempt_number']}; fourth attempts {r['fourth_attempt']['inputs_that_needed_one']} "
                  f"(resolved {r['fourth_attempt']['resolved_the_errors']}); never verified {r['inputs_never_verified']}")
            print("  reasons:", [(x['kind'][:70], x['attempts']) for x in r["failure_reasons"][:6]])
    print("written", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
