r"""Dashboard and reporting readiness (user, 24 Nov 2026, milestone 3). Code only; no model call; nothing is changed.

Usage: .venv\Scripts\python tools\dashboard_readiness.py [--root DIR] [--slice all] [--tag baseline] [--out DIR]

Drives the dashboard through Flask's test client (the same app `src.serve` runs) and the report file, and checks what the README (R2, R3, R4, R6) and
the plan (M8) ask of a client-ready deliverable. The deeper per-claim audit is `tools/audit_dashboard.py` (every quote re-read from the raw bytes,
every chain, every page); this tool adds the product checks around it.

  D1 ranked list (hard): the home page shows the running total and the scored opportunities in rank order, every row linked, first row the largest.
  D2 drill-through (hard): from every scored opportunity, its dollar claim, its formula inputs, its events and the quotes behind them are reachable
     by links alone, and each quote link opens a message page that highlights the quote (sampled: the top 10 opportunities and 20 more at random).
  D3 original names (hard): a message page shows the corpus file's original name, including a trailing dot (`578.`), never `578.txt`.
  D4 coverage (hard): the coverage page states the message count of the slice, the unique and duplicate-copy counts, and every disposition.
  D5 report (hard): the report states the total, labels every figure as estimated, lists minutes per event as assumed with a range, and names
     the method and limits; it has the ranked table the README asks for (events, emails, threads, months, minutes, dollars, confidence).
  D6 responsiveness (provisional): the median page answers in 100 ms or less and the slowest sampled page in 2 s or less (after the data is loaded);
     the data file loads in 15 s or less.
  D7 safety (hard): pages carry no unescaped angle bracket from message text (a sampled message page is compared with its raw text).
"""
import argparse
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

ROOT0 = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT0))
    ap.add_argument("--slice", default="all")
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    sys.path.insert(0, str(root))
    from src import serve  # noqa: E402
    t0 = time.time()
    d = json.loads((root / "output" / a.slice / "dashboard" / "data.json").read_text(encoding="utf-8"))
    load_s = time.time() - t0
    t0 = time.time()
    app = serve.create_app(a.slice).test_client()
    app_s = time.time() - t0
    times, problems = [], {k: [] for k in ("D1", "D2", "D3", "D4", "D5", "D7")}

    def get(path):
        t = time.time()
        r = app.get(path)
        times.append((time.time() - t, path))
        return r.status_code, r.get_data(as_text=True)

    scored = [o for o in d["opportunities"] if o["status"] == "scored"]
    scored.sort(key=lambda o: o["rank"])
    # D1
    code, home = get("/")
    total_txt = d["claims"]["total"]["display"]
    if code != 200 or total_txt not in home:
        problems["D1"].append(f"home page does not show the running total {total_txt}")
    pos = [home.find(f"/opportunity/{o['process_id']}'") for o in scored]
    if any(p < 0 for p in pos) or pos != sorted(pos):
        problems["D1"].append("scored opportunities are not all linked in rank order on the home page")
    if [o["rank"] for o in scored] != list(range(1, len(scored) + 1)) or any(Decimal_(scored[i]["dollars_per_month"]) < Decimal_(scored[i + 1]["dollars_per_month"]) for i in range(len(scored) - 1)):
        problems["D1"].append("ranks are not 1..n by descending dollars")
    # D2
    rnd = random.Random(24112026)
    sample = scored[:10] + rnd.sample(scored[10:], min(20, max(0, len(scored) - 10)))
    quote_links = set()
    for o in sample:
        code, page = get(f"/opportunity/{o['process_id']}")
        if code != 200:
            problems["D2"].append(f"opportunity page {o['process_id']} -> {code}")
            continue
        for c in (o["claims"]["dollars"], o["claims"]["hours"], o["claims"]["epm"], o["claims"]["minutes"], o["claims"]["events"]):
            if f'href="/claim/{c}"' not in page and f"href='/claim/{c}'" not in page:
                problems["D2"].append(f"{o['process_id']}: no link to claim {c}")
        code, cp = get(f"/claim/{o['claims']['dollars']}")
        if code != 200 or "formula" not in cp.lower():
            problems["D2"].append(f"{o['process_id']}: dollars claim page shows no formula")
        if not re.search(r"/event/", page):
            problems["D2"].append(f"{o['process_id']}: no event links")
        else:
            ev = re.search(r"/event/([^'\"?]+)", page).group(1)
            code, ep = get(f"/event/{ev}")
            quote_links |= set(re.findall(r"href='/message/([^?']+)\?q=([^']+)'", ep))
        quote_links |= set(re.findall(r"href='/message/([^?']+)\?q=([^']+)'", page))
    opened = 0
    for key, qid in sorted(quote_links)[:80]:
        code, mp = get(f"/message/{key}?q={qid}")
        opened += 1
        if code != 200 or '<mark class="focus"' not in mp:
            problems["D2"].append(f"message {key} does not highlight {qid}")
    # D3 original names (a corpus file whose original name ends with a dot)
    dotted = [k for k, m in d["messages"].items() if m["original_path"].endswith(".")][:5]
    for k in dotted:
        code, mp = get(f"/message/{k}")
        if code != 200 or d["messages"][k]["original_path"] + "<" not in mp or f"{k.split('/')[-1]}.txt" in mp:
            problems["D3"].append(f"{k}: original name not shown as written")
    if not dotted:
        problems["D3"].append("no sampled message with a trailing-dot original name")
    # D4 coverage
    code, cov = get("/coverage")
    c = d["coverage"]
    for needle in (f"{c['corpus_files']:,}", f"{c['corpus_unique']:,}", f"{c['corpus_copies']:,}", f"{c['messages_in_slice']:,}"):
        if needle not in cov:
            problems["D4"].append(f"coverage page lacks {needle}")
    for disp in c["dispositions"]:
        if str(disp) not in cov:
            problems["D4"].append(f"coverage page lacks disposition {disp}")
    # D5 report
    rep_text = (root / "output" / a.slice / "report.md").read_text(encoding="utf-8")
    for needle in ("Running total", "estimated", "assumed", "range", "| Rank | Process | Events | Emails | Threads | Months observed | Minutes/event (assumed) |"):
        if needle not in rep_text:
            problems["D5"].append(f"report lacks '{needle[:50]}'")
    if "total_low" in d["claims"]:                      # the financial range (final workflow, 24 Nov 2026)
        for needle in ("central estimate", "Range **", "$/month range"):
            if needle not in rep_text:
                problems["D5"].append(f"report lacks '{needle}'")
        if "central estimate" not in home or d["claims"]["total_low"]["display"] not in home or d["claims"]["total_high"]["display"] not in home:
            problems["D1"].append("the home page does not show the central estimate with its range")
    if not re.search(r"(?i)method|limit|assum", rep_text):
        problems["D5"].append("report names no method, limit or assumption section")
    for p in ("/method", "/artifacts"):
        code, _ = get(p)
        if code != 200:
            problems["D5"].append(f"{p} -> {code}")
    # D7 escaping: a message page must not contain an unescaped '<' from the raw text
    ex = 0
    for key in list(d["messages"])[:300]:
        raw = (root / "corpus" / f"{key}.txt")
        if not raw.exists():
            continue
        txt = raw.read_bytes().decode("utf-8", "replace")
        if re.search(r"<[A-Za-z/][^>]*>", txt):
            code, mp = get(f"/message/{key}")
            body = mp.split("<body", 1)[-1]
            leaked = [t for t in re.findall(r"<(?:script|iframe|img|a|b|i|font|html|table)\b[^>]*>", txt, re.I) if t in body]
            if leaked:
                problems["D7"].append(f"{key}: raw tag in the page: {leaked[0][:60]}")
            ex += 1
            if ex >= 12:
                break
    ts = sorted(t for t, _ in times)
    median, slowest = statistics.median(ts), ts[-1]
    rep = {"tag": a.tag, "slice": a.slice, "opportunities_scored": len(scored), "pages_requested": len(times), "quote_pages_opened": opened,
           "data_file_mb": round((root / "output" / a.slice / "dashboard" / "data.json").stat().st_size / 1e6, 1), "data_load_s": round(load_s, 2),
           "app_start_s": round(app_s, 2), "median_page_ms": round(1000 * median, 1), "p95_page_ms": round(1000 * ts[int(0.95 * (len(ts) - 1))], 1),
           "slowest_page_ms": round(1000 * slowest, 1), "slowest_page": max(times)[1], "message_pages_with_raw_tags_checked": ex,
           "problems": {k: v[:15] for k, v in problems.items()}}
    rep["criteria"] = {"D1 ranked list and running total (hard)": "pass" if not problems["D1"] else "FAIL",
                       "D2 every sampled claim drills through to a highlighted quote (hard)": "pass" if not problems["D2"] else "FAIL",
                       "D3 original corpus names shown as written (hard)": "pass" if not problems["D3"] else "FAIL",
                       "D4 coverage page states every count (hard)": "pass" if not problems["D4"] else "FAIL",
                       "D5 report has the ranked table, labels and method (hard)": "pass" if not problems["D5"] else "FAIL",
                       "D6 median page <= 100 ms, slowest <= 2 s, data load <= 15 s (provisional)":
                           "pass" if median <= 0.1 and slowest <= 2 and load_s <= 15 else "fail",
                       "D7 message text is escaped (hard)": "pass" if not problems["D7"] else "FAIL"}
    out = Path(a.out) if a.out else root / "eval" / "results" / "m6_baseline"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"dashboard_{a.tag}.json").write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k not in ("problems", "criteria")}))
    for k, v in problems.items():
        if v:
            print(k, v[:4])
    for k, v in rep["criteria"].items():
        print(f"  {v:>5s}  {k}")
    return 0


def Decimal_(x):
    from decimal import Decimal
    return Decimal(x)


if __name__ == "__main__":
    sys.exit(main())
