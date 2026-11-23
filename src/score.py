"""Scoring: events -> rates -> hours and dollars, every number a provenance claim (PLAN 3.3, 3.4, R7).

hours/month = events/month x minutes saved per event / 60;  $/month = hours/month x $85.
All arithmetic is Decimal; values are stored unrounded and rounded only for display (dollars to the
cent, hours to 0.1, half-up). Totals are summed from unrounded values, then rounded once.

Every published number is a claim: {id, label, value, display, formula, inputs (claim ids), evidence
(verified quote ids), kind}. Kinds: observed (a verified quote says it), counted (exact code over observed
items), assumption (a stated estimate with low/high and a reason citing observations), estimated (a
formula over counted values and assumptions). A claim chain must end in verified quotes or documented
assumptions; `check_chains` proves it (no cycles, no dangling inputs).

Rules (all in config/settings.yaml `score`):
  * months observed = union of the date windows of the mailboxes the process's events were seen in
    (R7); a denominator under `min_months` is refused, never divided;
  * a rate is published only with minimum evidence: at least `min_emails` verified emails from at least
    `min_threads` threads; below that the process is listed with "insufficient evidence", no rate;
  * waiting time is never work time: stall counts are reported as stall metrics only;
  * overlap: an event counted by a higher-ranked opportunity is not counted again in the running total;
  * confidence (code rule): high when events >= 6, threads >= 4 and people >= 3; medium when events >= 3
    and threads >= 2; low otherwise.
"""
from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from . import windows

SCORE_VERSION = "score-m3-v1"
CENT, TENTH = Decimal("0.01"), Decimal("0.1")


def money(x: Decimal) -> str:
    return f"${x.quantize(CENT, rounding=ROUND_HALF_UP):,}"


def hours(x: Decimal) -> str:
    return f"{x.quantize(TENTH, rounding=ROUND_HALF_UP):,} h"


def confidence(events: int, threads: int, people: int) -> str:
    if events >= 6 and threads >= 4 and people >= 3:
        return "high"
    if events >= 3 and threads >= 2:
        return "medium"
    return "low"


def _claim(claims: dict, cid: str, label: str, value, display: str, formula: str, kind: str,
           inputs=(), evidence=(), **extra) -> str:
    claims[cid] = {"id": cid, "label": label, "value": str(value), "display": display, "formula": formula,
                   "kind": kind, "inputs": list(inputs), "evidence": list(evidence), **extra}
    return cid


def score(processes: list[dict], events: list[dict], mailbox_windows: dict[str, tuple[datetime, datetime]],
          cfg: dict, quote_ids: dict[str, list[str]]) -> dict:
    """processes: validated synthesis output; events: src/events.py; quote_ids: obs_id -> verified quote ids.

    Returns {opportunities (ranked), claims, total}. Pure and deterministic."""
    rate = Decimal(str(cfg["hourly_rate"]))
    claims: dict[str, dict] = {}
    _claim(claims, "rate", "Blended hourly rate", rate, money(rate) + "/hour", "given in the assignment brief",
           "assumption", low=str(rate), high=str(rate), reason="README: a blended $85/hour")
    for mb, (a, b) in sorted(mailbox_windows.items()):
        m = windows.months_observed(mailbox_windows, [mb])
        _claim(claims, f"window:{mb}", f"{mb} mailbox window", m, f"{m.quantize(Decimal('0.01'))} months",
               "first to last ok-dated unique message, days / 30.4375", "counted",
               first_utc=a.isoformat(), last_utc=b.isoformat())
    opps = []
    by_proc: dict[str, list[dict]] = {}
    for e in events:
        by_proc.setdefault(e["process_id"], []).append(e)
    for p in processes:
        pid = p["process_id"]
        evs = by_proc.get(pid, [])
        obs_ids = [o for e in evs for o in e["obs_ids"]]
        msgs = sorted({k for e in evs for k in e["msg_keys"]})
        threads = sorted({t for e in evs for t in e["threads"]})
        people = sorted({a.lower() for e in evs for a in e["actors"]})
        mbs = sorted({mb for e in evs for mb in e["mailboxes"]})
        q = [qid for o in obs_ids for qid in quote_ids.get(o, [])]
        ev_ids = [e["event_id"] for e in evs]
        c_events = _claim(claims, f"{pid}:events", "Events (distinct occurrences)", len(evs), f"{len(evs)}",
                          "count of events: one per process per thread; quoted copies merged through their anchor",
                          "counted", evidence=q, event_ids=ev_ids)
        stalls = {}
        for e in evs:
            for sg in e["stall_signals"]:
                stalls[sg] = stalls.get(sg, 0) + 1
        stall_claims = {}
        for sg, n in sorted(stalls.items()):
            sq = [f"ev:{x['obs_id']}:{x['msg_key']}:{x['start']}-{x['end']}" for e in evs for x in e["stall_signals"].get(sg, [])]
            stall_claims[sg] = _claim(claims, f"{pid}:stall:{sg}", f"Events with a {sg.replace('_', ' ')} signal", n,
                                      f"{n} of {len(evs)}", "count of events with at least one published, grounded "
                                      "signal of this type (a stall metric, never work time)", "counted", evidence=sq)
        opp = {"process_id": pid, "name": p["name"], "events": len(evs), "emails": len(msgs), "threads": len(threads),
               "people": len(people), "mailboxes": mbs, "event_ids": ev_ids, "claims": {"events": c_events},
               "stall_claims": stall_claims, "confidence": confidence(len(evs), len(threads), len(people))}
        if len(msgs) < cfg["min_emails"] or len(threads) < cfg["min_threads"]:
            opp["status"] = "insufficient_evidence"
            opp["why"] = (f"{len(msgs)} verified email(s) in {len(threads)} thread(s); a rate needs at least "
                          f"{cfg['min_emails']} emails in {cfg['min_threads']} threads")
            opps.append(opp)
            continue
        months = windows.months_observed(mailbox_windows, mbs)
        c_months = _claim(claims, f"{pid}:months", "Months observed", months, f"{months.quantize(Decimal('0.01'))} months",
                          "union of the date windows of the mailboxes its events were seen in (overlaps merged)",
                          "counted", inputs=[f"window:{mb}" for mb in mbs])
        if months < Decimal(str(cfg["min_months"])):
            opp.update(status="window_too_short", why=f"months observed {months} is under {cfg['min_months']}")
            opps.append(opp)
            continue
        epm = Decimal(len(evs)) / months
        c_epm = _claim(claims, f"{pid}:epm", "Events per month (observed in these mailboxes)", epm,
                       f"{epm.quantize(Decimal('0.01'))}/month", "events / months observed", "counted",
                       inputs=[c_events, c_months])
        mins = p["minutes_per_event"]
        c_min = _claim(claims, f"{pid}:minutes", "Minutes saved per event", Decimal(str(mins["base"])),
                       f"{mins['base']} min (range {mins['low']}-{mins['high']})", "assumption proposed by the "
                       "synthesizer, with its reason", "assumption", low=str(mins["low"]), high=str(mins["high"]),
                       reason=mins["rationale"], evidence=[qid for o in mins.get("basis_obs_ids", [])
                                                           for qid in quote_ids.get(o, [])])
        out = {}
        for level in ("low", "base", "high"):
            m = Decimal(str(mins[level]))
            h = epm * m / Decimal(60)
            out[level] = (h, h * rate)
        h, d = out["base"]
        c_h = _claim(claims, f"{pid}:hours", "Hours saved per month", h, hours(h), "events per month x minutes per "
                     "event / 60", "estimated", inputs=[c_epm, c_min], low=str(out["low"][0]), high=str(out["high"][0]))
        c_d = _claim(claims, f"{pid}:dollars", "Dollars saved per month", d, money(d), "hours per month x hourly rate",
                     "estimated", inputs=[c_h, "rate"], low=str(out["low"][1]), high=str(out["high"][1]))
        opp.update(status="scored", months=str(months), events_per_month=str(epm), hours_per_month=str(h),
                   dollars_per_month=str(d), dollars_low=str(out["low"][1]), dollars_high=str(out["high"][1]),
                   claims={**opp["claims"], "months": c_months, "epm": c_epm, "minutes": c_min, "hours": c_h,
                           "dollars": c_d})
        opps.append(opp)
    scored = sorted((o for o in opps if o["status"] == "scored"), key=lambda o: (-Decimal(o["dollars_per_month"]), o["process_id"]))
    rest = sorted((o for o in opps if o["status"] != "scored"), key=lambda o: (-o["events"], o["process_id"]))
    # overlap: an event already counted by a higher-ranked opportunity is not counted again in the total
    counted, total, parts = set(), Decimal(0), []
    for rank, o in enumerate(scored, start=1):
        o["rank"] = rank
        new = [e for e in o["event_ids"] if e not in counted]
        share = Decimal(len(new)) / Decimal(len(o["event_ids"])) if o["event_ids"] else Decimal(0)
        alloc = Decimal(o["dollars_per_month"]) * share
        counted |= set(new)
        o["allocated_dollars"] = str(alloc)
        c_alloc = _claim(claims, f"{o['process_id']}:allocated", "Dollars counted in the running total", alloc,
                         money(alloc), "dollars per month x share of its events not counted by a higher-ranked "
                         "opportunity", "estimated", inputs=[o["claims"]["dollars"]], events_not_counted_before=len(new))
        o["claims"]["allocated"] = c_alloc
        parts.append(c_alloc)
        total += alloc
    c_total = _claim(claims, "total", "Running total, dollars per month", total, money(total),
                     "sum of each opportunity's allocated dollars (events counted once)", "estimated", inputs=parts)
    return {"version": SCORE_VERSION, "opportunities": scored + rest, "claims": claims, "total": c_total}


def check_chains(claims: dict, verified_quote_ids: set[str]) -> list[str]:
    """Every chain ends in verified quotes or documented assumptions; no cycles; no dangling inputs."""
    problems = []
    for cid, c in claims.items():
        if c["kind"] == "assumption" and not c.get("reason"):
            problems.append(f"{cid}: an assumption needs a reason")
        if c["kind"] in ("counted", "observed") and not c["inputs"] and not c["evidence"] and not cid.startswith("window:"):
            problems.append(f"{cid}: a counted claim needs evidence or inputs")
        bad = [q for q in c["evidence"] if q not in verified_quote_ids]
        if bad:
            problems.append(f"{cid}: evidence {bad[:3]} is not a verified quote")
        for i in c["inputs"]:
            if i not in claims:
                problems.append(f"{cid}: input {i} is not a claim")

    def walk(cid, path):
        if cid in path:
            problems.append(f"cycle: {' -> '.join(path + [cid])}")
            return
        for i in claims.get(cid, {}).get("inputs", []):
            walk(i, path + [cid])
    for cid in claims:
        walk(cid, [])
    return problems
