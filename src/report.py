"""Stage 10, report: dashboard data, report.md and the coverage ledger view, all written by code (PLAN 3.7).

Every number shown comes from the score's claim records; model prose (process names, descriptions,
assumption reasons, artifacts) is slotted in and labelled. Messages are served as raw text by the
dashboard, escaped there, with verified byte offsets highlighted.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import score, store

REPORT_VERSION = "report-m3-v1"
# Any slice but the full corpus is a pipeline test: its figures are never findings about the company.
SLICE_LABEL = "pipeline test on a development slice, not a finding"
SLICE_NOTE = ("These outputs test the pipeline on a ~200-message development slice of the corpus. Its counts, rates, "
              "hours and dollars are NOT validated findings about the company: rates are divided by each mailbox's whole "
              "date window while the slice holds only a small share of its messages, and no reviewer has checked the "
              "claims. The quotes and citations are real and verified against the raw files.")


def _coverage(r, st: store.Store, b: dict, extracted: dict, missing: list[str]) -> dict:
    # R8: a message is read when the verified output of its batch, as used by this run, lists it (its read receipt).
    # Coverage is taken from those receipts, never from a side file: after the M4 per-slice move the slice's
    # status.json did not exist yet, and an offline rerun reported 0 of 202 read (found 23 Nov 2026).
    status = json.loads(r.ex.status.read_text(encoding="utf-8")) if r.ex.status.exists() else {}   # this slice's own
    keys = b["keys"]
    per_batch = {k: x["batch_id"] for x in b["batches"] for k in x["keys"]}
    disp, receipts = Counter(), set()
    for bid, p in sorted(extracted.items()):
        for m in json.loads(Path(p).read_text(encoding="utf-8"))["messages"]:
            disp[m["disposition"]] += 1
            if per_batch.get(m["msg_key"]) == bid:
                receipts.add(m["msg_key"])
    read = [k for k in keys if k in receipts]
    read_set = set(read)

    def analysis(k):
        if k in read_set:
            return "read"
        if k in per_batch:
            return "failed" if status.get(k) == "failed" else "batched"
        return "not_in_slice"
    ledger = []
    with (r.root / "data" / "ledger.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ledger.append(row)
    in_slice = set(keys)
    return {"messages_in_slice": len(keys), "read": len(read), "unread": sorted(set(keys) - set(read)),
            "complete": not missing and len(read) == len(keys), "dispositions": dict(sorted(disp.items())),
            "corpus_files": len(ledger), "corpus_unique": sum(1 for x in ledger if x.get("ingest_status") == "unique"),
            "corpus_copies": sum(1 for x in ledger if (x.get("ingest_status") or "").startswith("duplicate_of")),
            "ledger": [{**{k: x.get(k) for k in ("key", "original_path", "ingest_status", "date_status", "thread_id",
                                                  "special_handling", "readings")},
                        "in_slice": x.get("key") in in_slice, "analysis": analysis(x.get("key"))}
                       for x in ledger]}


def build(r, st: store.Store, b: dict, extracted: dict, grounded: dict, obs: list[dict], synth_res: dict,
          evs: list[dict], sc: dict, qindex: dict, artifacts: list[dict], missing: list[str], selection: dict | None = None) -> dict:
    out = r.out
    procs = {p["process_id"]: p for p in synth_res["processes"]}
    cov = _coverage(r, st, b, extracted, missing)
    by_obs = {o["obs_id"]: o for o in obs}
    msgs_needed = sorted({q["msg_key"] for q in qindex.values()})
    messages = {}
    for k in msgs_needed:
        m = st.messages[k]
        hl = [{"id": qid, "start": q["start"], "end": q["end"], "kind": q["kind"]} for qid, q in qindex.items()
              if q["msg_key"] == k]
        messages[k] = {"key": k, "original_path": m.original_path, "date_raw": m.date_raw, "sender": m.sender,
                       "subject": m.subject, "thread_id": m.thread_id, "mailbox": m.mailbox, "highlights": hl,
                       "blocks": [{"index": bl.index, "start": bl.start, "end": bl.end, "author": bl.from_raw,
                                   "hidden": bool(bl.skip_reason)} for bl in m.blocks],
                       "body_start": m.body_start, "new_text_end": m.new_text_end}
    opps = []
    for o in sc["opportunities"]:
        p = procs[o["process_id"]]
        opps.append({**o, "description": p["description"], "trigger": p["trigger"], "output": p["output"],
                     "roles": p["roles"], "automation": p["automation"], "minutes_per_event": p["minutes_per_event"],
                     "obs_ids": p["obs_ids"], "threads_seen": p["threads_seen"],
                     # only what the dashboard links to: never the absolute local path of the file
                     "artifact": next(({"name": a["name"], "artifact_type": a["artifact_type"], "file": Path(a["path"]).name,
                                        "cited": a.get("cited")}
                                       for a in artifacts if a.get("process_id") == o["process_id"] and a.get("path")), None)})
    data = {"version": REPORT_VERSION, "slice": r.slice, "label": SLICE_LABEL if r.slice != "all" else "",
            "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "total": sc["claims"]["total"], "total_range": sc.get("total_range"), "claims": sc["claims"], "opportunities": opps,
            "artifact_selection": ({k: selection[k] for k in ("mode", "top_n", "selected", "skipped") if k in selection} if selection else None),
            "events": evs, "observations": {k: {f: v.get(f) for f in ("obs_id", "msg_key", "task", "action_state", "actors",
                                                                      "quote_raw", "start", "end", "evidence_kind",
                                                                      "evidence_author", "evidence_date", "date_basis",
                                                                      "stall_signals", "stall_signals_claimed",
                                                                      "signal_outcomes", "original_path")}
                                            for k, v in by_obs.items()},
            "quotes": qindex, "messages": messages, "coverage": cov,
            "artifacts": [{k: a.get(k) for k in ("process_id", "artifact_type", "name", "cited", "flags", "status")}
                          | {"file": Path(a["path"]).name if a.get("path") else None} for a in artifacts],
            "synthesis": {"unassigned": synth_res.get("unassigned", []), "flags": synth_res.get("flags", []),
                          "model_observed": synth_res.get("model_observed")}}
    (out / "dashboard").mkdir(parents=True, exist_ok=True)
    (out / "dashboard" / "data.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    lines = [f"# Observation report ({r.slice})", ""]
    if data["label"]:
        lines += [f"> **{data['label'][0].upper() + data['label'][1:]}.** {SLICE_NOTE}", ""]
        (out / "README.md").write_text(f"# Output of the `{r.slice}` slice: a pipeline test\n\n**{data['label'][0].upper() + data['label'][1:]}.** "
                                       f"{SLICE_NOTE}\n\nWritten by `src/report.py` on every run of the slice.\n", encoding="utf-8")
    tr = sc.get("total_range")
    rng = (f" Range **{sc['claims'][tr['low']]['display']} to {sc['claims'][tr['high']]['display']}** per month at the low and high end "
           f"of each minutes-per-event assumption (the events counted and the $85/hour rate are not varied)." if tr else "")
    lines += [f"Running total (overlap-adjusted), central estimate: **{sc['claims']['total']['display']}** per month "
              f"(estimated: events observed x minutes assumed x $85/hour).{rng}", "",
              "| Rank | Process | Events | Emails | Threads | Months observed | Minutes/event (assumed) | $/month (central) | $/month range | Confidence |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for o in opps:
        c = sc["claims"]
        if o["status"] == "scored":
            lines.append(f"| {o['rank']} | {o['name']} | {o['events']} | {o['emails']} | {o['threads']} | "
                         f"{c[o['claims']['months']]['display']} | {c[o['claims']['minutes']]['display']} | "
                         f"{c[o['claims']['dollars']]['display']} | {score.money(Decimal(o['dollars_low']))} to "
                         f"{score.money(Decimal(o['dollars_high']))} | {o['confidence']} |")
        else:
            lines.append(f"| - | {o['name']} | {o['events']} | {o['emails']} | {o['threads']} | - | - | "
                         f"no rate: {o['status'].replace('_', ' ')} | - | {o['confidence']} |")
    lines += ["", "## Observed, counted, assumed", "",
              "- **Observed:** each event rests on verified quotes (exact raw bytes, original file names).",
              "- **Counted by code:** events (one per process per thread, quoted copies once), months observed "
              "(union of mailbox windows), events per month.",
              "- **Assumed:** minutes saved per event (proposed by the synthesizer with a reason; low-high range shown) "
              "and the $85/hour rate.", "- **Estimated:** hours and dollars = counted x assumed. The central estimate uses the base "
              "minutes; the range uses the low and the high minutes of each process and nothing else (no bound is invented for the "
              "events counted or the rate).", "",
              f"Coverage: {cov['read']}/{cov['messages_in_slice']} slice messages read; dispositions {cov['dispositions']}.", ""]
    for a in artifacts:
        if a.get("path"):
            lines.append(f"- Artifact for {a['process_id']}: `{Path(a['path']).name}` ({a['artifact_type']}), "
                         f"{len(a['cited'])} citations verified.")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"coverage": {k: cov[k] for k in ("messages_in_slice", "read", "complete", "dispositions")},
            "total": sc["claims"]["total"]["display"],
            "total_range": ({"low": sc["claims"][tr["low"]]["display"], "high": sc["claims"][tr["high"]]["display"],
                             "basis": "low and high minutes per event only"} if tr else None),
            "opportunities_ranked": [(o.get("rank"), o["name"], o["status"],
                                      sc["claims"].get(o["claims"].get("dollars", ""), {}).get("display")) for o in opps],
            "artifacts": [a.get("name") for a in artifacts]}
