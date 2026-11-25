r"""Financial estimate validation (user, 24 Nov 2026, milestone 2). Code only; no model call; nothing is changed.

Usage: .venv\Scripts\python tools\finance_validation.py [--root DIR] [--slice all] [--tag baseline] [--out DIR]

Re-derives every published dollar figure from the stored inputs with a SECOND implementation (plain Decimal and datetime arithmetic written here, not
`src/score.py`) and compares it with the published claims, then looks for the ways a financial estimate can be wrong without any arithmetic error:
double counting, duplicated opportunities, assumptions carrying the total, and a report that disagrees with the data behind it.

  F1 arithmetic (hard): months observed, events per month, hours, dollars, the overlap allocation and the running total match to the last digit.
  F2 event recount (hard): the events in the data equal an independent recount of distinct (process, thread) pairs from the stored assignment.
  F3 evidence thresholds (hard): every scored opportunity has at least 2 events, 3 emails and 2 threads.
  F4 report consistency (hard): the total and every row of the report's ranked table equal the data's figures.
  F5 double counting (provisional): the overlap deduction (sum of opportunity dollars minus the running total) is at most 10 percent of the sum,
     and no scored opportunity pair is a near-duplicate (name Jaccard 0.5 or more).
  F6 assumption dependence (reported, provisional limit): the total at the low and the high end of every minutes assumption, the dollars carried by
     low-confidence opportunities (at most 15 percent of the total), the largest single opportunity (at most 15 percent) and the top five (at most 50).
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT0 = Path(__file__).resolve().parent.parent
D30 = Decimal("30.4375")
CENT = Decimal("0.01")


def toks(s):
    return {w for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def union_days(windows: list[tuple[datetime, datetime]]) -> Decimal:
    """Length of the union of date windows in days (own implementation), then /30.4375 months by the caller."""
    spans = sorted((a, b) for a, b in windows)
    total, cur_a, cur_b = Decimal(0), None, None
    for a, b in spans:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += Decimal((cur_b - cur_a).total_seconds()) / Decimal(86400)
            cur_a, cur_b = a, b
        elif b > cur_b:
            cur_b = b
    if cur_b is not None:
        total += Decimal((cur_b - cur_a).total_seconds()) / Decimal(86400)
    return total


def money(x: Decimal) -> str:
    return f"${x.quantize(CENT, rounding=ROUND_HALF_UP):,}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT0))
    ap.add_argument("--slice", default="all")
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--out")
    ap.add_argument("--synth")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    d = json.loads((root / "output" / a.slice / "dashboard" / "data.json").read_text(encoding="utf-8"))
    synth = json.loads(Path(a.synth or root / "data" / "runs" / a.slice / "synth.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(root))
    from src import store  # noqa: E402
    st = store.load()
    claims = d["claims"]
    rate = Decimal(claims["rate"]["value"])
    rep = {"tag": a.tag, "slice": a.slice, "opportunities": len(d["opportunities"]), "events": len(d["events"])}
    problems = defaultdict(list)

    # F1 arithmetic, second implementation
    win = {c["id"].split(":", 1)[1]: (datetime.fromisoformat(c["first_utc"]), datetime.fromisoformat(c["last_utc"]))
           for c in claims.values() if c["id"].startswith("window:")}
    proc = {p["process_id"]: p for p in synth["processes"]}
    ev_by = defaultdict(list)
    for e in d["events"]:
        ev_by[e["process_id"]].append(e)
    scored = sorted((o for o in d["opportunities"] if o["status"] == "scored"), key=lambda o: (-Decimal(o["dollars_per_month"]), o["process_id"]))
    raw_sum, counted, total = Decimal(0), set(), Decimal(0)
    low_total = high_total = Decimal(0)
    rows = []
    for rank, o in enumerate(scored, start=1):
        pid = o["process_id"]
        evs = ev_by[pid]
        mbs = sorted({m for e in evs for m in e["mailboxes"]})
        months = union_days([win[m] for m in mbs]) / D30
        epm = Decimal(len(evs)) / months
        mins = proc[pid]["minutes_per_event"]
        hours = {k: epm * Decimal(str(mins[k])) / Decimal(60) for k in ("low", "base", "high")}
        dollars = {k: v * rate for k, v in hours.items()}
        if o["rank"] != rank:
            problems["F1"].append(f"{pid}: rank {o['rank']} != recomputed {rank}")
        for what, mine, theirs in (("months", months, Decimal(o["months"])), ("events/month", epm, Decimal(o["events_per_month"])),
                                   ("hours", hours["base"], Decimal(o["hours_per_month"])), ("dollars", dollars["base"], Decimal(o["dollars_per_month"])),
                                   ("dollars low", dollars["low"], Decimal(o["dollars_low"])), ("dollars high", dollars["high"], Decimal(o["dollars_high"]))):
            if abs(mine - theirs) > Decimal("1e-18") * max(Decimal(1), abs(theirs)):
                problems["F1"].append(f"{pid}: {what} {mine} != published {theirs}")
        ids = [e["event_id"] for e in evs]
        new = [x for x in ids if x not in counted]
        share = Decimal(len(new)) / Decimal(len(ids))
        alloc = dollars["base"] * share
        if abs(alloc - Decimal(o["allocated_dollars"])) > Decimal("1e-18") * max(Decimal(1), alloc):
            problems["F1"].append(f"{pid}: allocated {alloc} != published {o['allocated_dollars']}")
        counted |= set(ids)
        total += alloc
        low_total += dollars["low"] * share
        high_total += dollars["high"] * share
        raw_sum += dollars["base"]
        rows.append((pid, o["name"], dollars["base"], alloc, o["confidence"], int(mins["base"]), len(evs), o["emails"], o["threads"], dollars["low"], dollars["high"]))
    pub_total = Decimal(claims[d["total"]["claim"]]["value"]) if isinstance(d["total"], dict) and "claim" in d["total"] else Decimal(claims["total"]["value"])
    if abs(total - pub_total) > Decimal("1e-12"):
        problems["F1"].append(f"running total {total} != published {pub_total}")
    if "total_low" in claims:                                   # the range claims exist from the final workflow on (24 Nov 2026)
        for k, mine in (("total_low", low_total), ("total_high", high_total)):
            if abs(mine - Decimal(claims[k]["value"])) > Decimal("1e-12"):
                problems["F1"].append(f"{k} {mine} != published {claims[k]['value']}")
        if not (low_total <= total <= high_total):
            problems["F1"].append("the central estimate is outside its own range")
    rep["F1_arithmetic"] = {"scored_opportunities": len(scored), "recomputed_total": str(total), "published_total": str(pub_total), "mismatches": len(problems["F1"])}

    # F2 event recount: distinct (process, thread) pairs from the assignment, thread of the anchor message for a quoted copy
    obs = {}
    for f in sorted((root / "data" / "runs" / a.slice / "ground").glob("b*.json")):
        for o in json.loads(f.read_text(encoding="utf-8"))["observations"]:
            obs[o["obs_id"]] = o
    pairs = set()
    for oid, pid in synth["assignment"].items():
        x = obs[oid]
        key = st.messages[x["anchor"].split("#", 1)[0]].thread_id if x.get("anchor") else st.messages[x["msg_key"]].thread_id
        pairs.add((pid, key))
    rep["F2_recount"] = {"independent_pairs": len(pairs), "events_in_data": len(d["events"])}
    if len(pairs) != len(d["events"]):
        problems["F2"].append(f"independent recount {len(pairs)} != events in the data {len(d['events'])}")

    # F3 thresholds
    for o in scored:
        if o["events"] < 2 or o["emails"] < 3 or o["threads"] < 2:
            problems["F3"].append(f"{o['process_id']}: {o['events']} events, {o['emails']} emails, {o['threads']} threads")
    rep["F3_thresholds"] = {"violations": len(problems["F3"])}

    # F4 report consistency
    text = (root / "output" / a.slice / "report.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*(\$[\d,]+\.\d\d)\*\*", text)
    if not m or m.group(1) != money(total):
        problems["F4"].append(f"report total {m.group(1) if m else None} != {money(total)}")
    if "total_low" in claims and f"**{money(low_total)} to {money(high_total)}**" not in text:
        problems["F4"].append(f"report does not state the range {money(low_total)} to {money(high_total)}")
    row_rx = (r"^\| (\d+) \| (.+?) \| (\d+) \| (\d+) \| (\d+) \| ([\d.]+) months \| (\d+) min \(range (\d+)-(\d+)\) \| (\$[\d,]+\.\d\d) \|"
              r"(?: (\$[\d,]+\.\d\d) to (\$[\d,]+\.\d\d) \|)? (\w+) \|$")
    table = {int(r[0]): r for r in re.findall(row_rx, text, re.M)}
    for i, (pid, name, dol, alloc, conf, base, ne, em, tn, dlo, dhi) in enumerate(rows, start=1):
        r = table.get(i)
        if not r:
            problems["F4"].append(f"{pid}: not in the report table")
            continue
        if (r[2], r[3], r[4], r[6], r[9], r[12]) != (str(ne), str(em), str(tn), str(base), money(dol), conf) or r[1] != name:
            problems["F4"].append(f"{pid}: report row differs from the data {r[1:]}")
        if r[10] and (r[10], r[11]) != (money(dlo), money(dhi)):
            problems["F4"].append(f"{pid}: report range {r[10]} to {r[11]} != {money(dlo)} to {money(dhi)}")
    rep["F4_report"] = {"table_rows_found": len(table), "scored": len(rows), "mismatches": len(problems["F4"])}

    # F5 double counting
    deduction = raw_sum - total
    th_proc = defaultdict(set)
    for e in d["events"]:
        for t in e["threads"]:
            th_proc[t].add(e["process_id"])
    scored_ids = {o["process_id"] for o in scored}
    multi = {t: ps & scored_ids for t, ps in th_proc.items() if len(ps & scored_ids) > 1}
    dup = []
    for i, x in enumerate(rows):
        for y in rows[i + 1:]:
            tx, ty = toks(x[1]), toks(y[1])
            if tx and ty and len(tx & ty) / len(tx | ty) >= 0.5:
                dup.append((x[1], y[1], str(x[2].quantize(CENT)), str(y[2].quantize(CENT))))
    # an upper bound on thread-level double counting: in a thread counted by two scored processes, every process after the highest-ranked one adds
    # its events of that thread at its own dollars per event (events are keyed per process, so the overlap rule above never removes them)
    per_event = {r[0]: r[2] / Decimal(r[6]) for r in rows}
    rank_of = {r[0]: i for i, r in enumerate(rows)}
    extra_events, extra_dollars = 0, Decimal(0)
    n_ev = Counter((e["process_id"], t) for e in d["events"] for t in e["threads"])
    for t, ps in multi.items():
        for p in sorted(ps, key=rank_of.get)[1:]:
            extra_events += n_ev[(p, t)]
            extra_dollars += per_event[p] * n_ev[(p, t)]
    rep["F5_double_counting"] = {"sum_of_opportunity_dollars": str(raw_sum.quantize(CENT)), "running_total": str(total.quantize(CENT)),
                                 "thread_overlap_upper_bound_events": extra_events, "thread_overlap_upper_bound_dollars": str(extra_dollars.quantize(CENT)),
                                 "thread_overlap_upper_bound_share_of_total": round(float(extra_dollars / total), 4) if total else 0,
                                 "overlap_deduction": str(deduction.quantize(CENT)), "deduction_share": round(float(deduction / raw_sum), 4) if raw_sum else 0,
                                 "threads_in_two_or_more_scored_processes": len(multi), "events_sharing_such_threads": sum(len(v) for v in multi.values()),
                                 "near_duplicate_scored_pairs": dup[:10], "n_near_duplicate_pairs": len(dup)}

    # F6 assumption dependence
    by_conf = Counter()
    for pid, name, dol, alloc, conf, base, ne, em, tn, dlo, dhi in rows:
        by_conf[conf] += alloc
    mins_all = sorted(r[5] for r in rows)
    top = sorted((r[3] for r in rows), reverse=True)
    rep["F6_assumptions"] = {"total_at_low_minutes": str(low_total.quantize(CENT)), "total_at_base": str(total.quantize(CENT)),
                             "total_at_high_minutes": str(high_total.quantize(CENT)),
                             "dollars_by_confidence": {k: str(v.quantize(CENT)) for k, v in by_conf.items()},
                             "low_confidence_share": round(float(by_conf["low"] / total), 4) if total else 0,
                             "largest_opportunity_share": round(float(top[0] / total), 4) if total else 0, "top5_share": round(float(sum(top[:5]) / total), 4) if total else 0,
                             "minutes_per_event_base": {"min": mins_all[0], "median": mins_all[len(mins_all) // 2], "max": mins_all[-1]},
                             "opportunities_with_base_minutes_over_60": [r[1] for r in rows if r[5] > 60],
                             "total_events_counted_once": len(counted)}

    rep["criteria"] = {
        "F1 arithmetic matches an independent recomputation (hard)": "pass" if not problems["F1"] else "FAIL",
        "F2 event recount equals the events in the data (hard)": "pass" if not problems["F2"] else "FAIL",
        "F3 evidence thresholds hold for every scored opportunity (hard)": "pass" if not problems["F3"] else "FAIL",
        "F4 report equals the data (hard)": "pass" if not problems["F4"] else "FAIL",
        "F5 overlap deduction <= 10 percent and no near-duplicate pair (provisional)":
            "pass" if rep["F5_double_counting"]["deduction_share"] <= 0.10 and not dup else "fail",
        "F6 low-confidence <= 15, largest <= 15, top five <= 50 percent (provisional)":
            "pass" if rep["F6_assumptions"]["low_confidence_share"] <= 0.15 and rep["F6_assumptions"]["largest_opportunity_share"] <= 0.15
            and rep["F6_assumptions"]["top5_share"] <= 0.50 else "fail"}
    rep["problems"] = {k: v[:20] for k, v in problems.items()}
    out = Path(a.out) if a.out else root / "eval" / "results" / "m6_baseline"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"finance_{a.tag}.json").write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    for k in ("F1_arithmetic", "F2_recount", "F3_thresholds", "F4_report", "F5_double_counting", "F6_assumptions"):
        print(k, rep[k])
    for k, v in problems.items():
        print(k, v[:5])
    for k, v in rep["criteria"].items():
        print(f"  {v:>6s}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
