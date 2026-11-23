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
    status = json.loads((r.root / "data" / "extract" / "status.json").read_text(encoding="utf-8")) \
        if (r.root / "data" / "extract" / "status.json").exists() else {}
    keys = b["keys"]
    per_batch = {k: x["batch_id"] for x in b["batches"] for k in x["keys"]}
    read = [k for k in keys if per_batch[k] in extracted and status.get(k) == "read"]
    disp = Counter()
    for bid, p in sorted(extracted.items()):
        for m in json.loads(Path(p).read_text(encoding="utf-8"))["messages"]:
            disp[m["disposition"]] += 1
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
                        "in_slice": x.get("key") in in_slice, "analysis": status.get(x.get("key"), "not_in_slice")}
                       for x in ledger]}


def build(r, st: store.Store, b: dict, extracted: dict, grounded: dict, obs: list[dict], synth_res: dict,
          evs: list[dict], sc: dict, qindex: dict, artifacts: list[dict], missing: list[str]) -> dict:
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
            "total": sc["claims"]["total"], "claims": sc["claims"], "opportunities": opps,
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
    lines += [f"Running total (overlap-adjusted): **{sc['claims']['total']['display']}** per month "
              f"(estimated: events observed x minutes assumed x $85/hour).", "",
              "| Rank | Process | Events | Emails | Threads | Months observed | Minutes/event (assumed) | $/month | Confidence |",
              "|---|---|---|---|---|---|---|---|---|"]
    for o in opps:
        c = sc["claims"]
        if o["status"] == "scored":
            lines.append(f"| {o['rank']} | {o['name']} | {o['events']} | {o['emails']} | {o['threads']} | "
                         f"{c[o['claims']['months']]['display']} | {c[o['claims']['minutes']]['display']} | "
                         f"{c[o['claims']['dollars']]['display']} | {o['confidence']} |")
        else:
            lines.append(f"| - | {o['name']} | {o['events']} | {o['emails']} | {o['threads']} | - | - | "
                         f"no rate: {o['status'].replace('_', ' ')} | {o['confidence']} |")
    lines += ["", "## Observed, counted, assumed", "",
              "- **Observed:** each event rests on verified quotes (exact raw bytes, original file names).",
              "- **Counted by code:** events (one per process per thread, quoted copies once), months observed "
              "(union of mailbox windows), events per month.",
              "- **Assumed:** minutes saved per event (proposed by the synthesizer with a reason; low-high range shown) "
              "and the $85/hour rate.", "- **Estimated:** hours and dollars = counted x assumed.", "",
              f"Coverage: {cov['read']}/{cov['messages_in_slice']} slice messages read; dispositions {cov['dispositions']}.", ""]
    for a in artifacts:
        if a.get("path"):
            lines.append(f"- Artifact for {a['process_id']}: `{Path(a['path']).name}` ({a['artifact_type']}), "
                         f"{len(a['cited'])} citations verified.")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"coverage": {k: cov[k] for k in ("messages_in_slice", "read", "complete", "dispositions")},
            "total": sc["claims"]["total"]["display"],
            "opportunities_ranked": [(o.get("rank"), o["name"], o["status"],
                                      sc["claims"].get(o["claims"].get("dollars", ""), {}).get("display")) for o in opps],
            "artifacts": [a.get("name") for a in artifacts]}
