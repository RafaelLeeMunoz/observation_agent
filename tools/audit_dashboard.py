r"""Audit a slice's published dashboard data against the raw files (a forerunner of the M10 self-audit).

Checks, all deterministic:
  1. every quote the dashboard can show re-reads byte-exact from its raw file at its stored offsets;
  2. every claim chain ends in verified quotes or documented assumptions (score.check_chains);
  3. every $ figure, claim, event, opportunity and message page returns 200, and every message page highlights the
     quotes it is linked with;
  4. arithmetic: each scored opportunity's dollars = hours x rate and hours = events/month x minutes / 60, recomputed
     in Decimal from the stored claim values; the running total = the sum of the allocated dollars.
Usage: .venv\Scripts\python tools\audit_dashboard.py dev
"""
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import rawio, score, serve  # noqa: E402


def main(argv):
    slice_name = argv[0] if argv else "dev"
    d = json.loads((ROOT / "output" / slice_name / "dashboard" / "data.json").read_text(encoding="utf-8"))
    problems = []
    raw = {}
    for qid, q in d["quotes"].items():
        k = q["msg_key"]
        raw.setdefault(k, rawio.read_raw(ROOT / "corpus" / f"{k}.txt"))
        if raw[k][q["start"]:q["end"]] != q["quote_raw"]:
            problems.append(f"quote {qid}: raw bytes at {q['start']}-{q['end']} differ")
    problems += [f"chain: {p}" for p in score.check_chains(d["claims"], set(d["quotes"]))]
    c = d["claims"]
    total = Decimal(0)
    for o in d["opportunities"]:
        if o["status"] != "scored":
            continue
        cl = o["claims"]
        epm, mins = Decimal(c[cl["epm"]]["value"]), Decimal(c[cl["minutes"]]["value"])
        h, dollars = Decimal(c[cl["hours"]]["value"]), Decimal(c[cl["dollars"]]["value"])
        if epm != Decimal(c[cl["events"]]["value"]) / Decimal(c[cl["months"]]["value"]):
            problems.append(f"{o['process_id']}: events/month is not events / months")
        if h != epm * mins / Decimal(60):
            problems.append(f"{o['process_id']}: hours is not events/month x minutes / 60")
        if dollars != h * Decimal(c["rate"]["value"]):
            problems.append(f"{o['process_id']}: dollars is not hours x rate")
        total += Decimal(c[cl["allocated"]]["value"])
    if total != Decimal(c["total"]["value"]):
        problems.append("the running total is not the sum of the allocated dollars")
    app = serve.create_app(slice_name).test_client()
    pages = ["/", "/coverage", "/artifacts", "/method"] + [f"/claim/{k}" for k in c] + \
            [f"/event/{e['event_id']}" for e in d["events"]] + [f"/opportunity/{o['process_id']}" for o in d["opportunities"]] + \
            [f"/artifact/{a['name']}" for a in d["artifacts"] if a.get("file")]
    for p in pages:
        if app.get(p).status_code != 200:
            problems.append(f"page {p} does not load")
    linked = set()
    for p in pages:
        linked |= set(re.findall(r"href='/message/([^?']+)\?q=([^']+)'", app.get(p).get_data(as_text=True)))
    for key, qid in sorted(linked):
        html = app.get(f"/message/{key}?q={qid}").get_data(as_text=True)
        if '<mark class="focus"' not in html:
            problems.append(f"message {key} does not highlight {qid}")
    print(f"quotes re-read: {len(d['quotes'])}; claims: {len(c)}; pages: {len(pages)}; quote links opened: {len(linked)}; "
          f"problems: {len(problems)}")
    for p in problems[:30]:
        print("  ", p)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
