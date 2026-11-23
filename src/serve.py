"""Stage 10, serve: the local dashboard (Flask, 127.0.0.1 only), drill-through on every number (PLAN 3.7).

$ figure -> its claim (formula, every input labelled observed / counted / assumption / estimated) -> the
counted events -> their messages -> the raw email, escaped, with the verified quote highlighted by its
raw-byte offsets and the original file name shown. Every page reads output/<slice>/dashboard/data.json,
written by code; raw email text is read through src/rawio.py and never trusted as HTML.

Run:  .venv\\Scripts\\python -m src.serve --slice dev                (foreground)
      .venv\\Scripts\\python -m src.serve --slice dev --detach       (keeps running after the caller exits)
      .venv\\Scripts\\python -m src.serve --stop
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, request
from markupsafe import Markup, escape

from . import rawio

ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PIDFILE = ROOT / "output" / "server.json"
KIND_HELP = {"observed": "a verified quote says it", "counted": "exact code over observed items",
             "assumption": "a stated estimate with a range and a reason", "estimated": "a formula over counted values and assumptions"}

CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d2433;--muted:#5d6678;--line:#e3e5ea;--accent:#1f5f8b;--accent-soft:#e6f0f7;
--mark:#fff1a8;--mark-ink:#3b3000;--obs:#2e7d32;--cnt:#1f5f8b;--asm:#9a5b00;--est:#6a3fa0;--warn:#a33}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14171c;--panel:#1c2027;--ink:#e6e8ec;--muted:#9aa3b2;
--line:#2c323c;--accent:#7fb7e0;--accent-soft:#1d2a36;--mark:#5c4e00;--mark-ink:#fff6c8;--obs:#81c784;--cnt:#7fb7e0;--asm:#f0b45a;--est:#b79be0;--warn:#f08a8a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--panel);border-bottom:1px solid var(--line);padding:14px 24px;display:flex;gap:24px;align-items:baseline;flex-wrap:wrap}
header .brand{font-weight:650;font-size:17px}header nav a{margin-right:16px;color:var(--muted)}
main{max-width:1120px;margin:0 auto;padding:24px 16px 64px}
.banner{background:var(--accent-soft);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin-bottom:18px;color:var(--ink)}
.hero{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:22px}.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.hero .card{flex:1 1 220px}.big{font-size:30px;font-weight:650;letter-spacing:-.01em}.label{color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;background:var(--bg)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}.pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;border:1px solid var(--line)}
.k-observed{color:var(--obs);border-color:var(--obs)}.k-counted{color:var(--cnt);border-color:var(--cnt)}.k-assumption{color:var(--asm);border-color:var(--asm)}.k-estimated{color:var(--est);border-color:var(--est)}
.muted{color:var(--muted)}.formula{font-family:ui-monospace,Consolas,monospace;font-size:13px;background:var(--bg);border-radius:6px;padding:8px 10px;margin:8px 0}
pre.raw{white-space:pre-wrap;word-break:break-word;font:13px/1.55 ui-monospace,Consolas,monospace;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0}
mark{background:var(--mark);color:var(--mark-ink);border-radius:3px;padding:0 1px}mark.focus{outline:2px solid var(--accent)}
.quoted{color:var(--muted)}.hidden-block{opacity:.6}
h1{font-size:24px;margin:0 0 6px}h2{font-size:18px;margin:26px 0 10px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:760px){.grid2{grid-template-columns:1fr}header{padding:12px 16px}table{display:block;overflow-x:auto}}
.q{border-left:3px solid var(--mark);padding:4px 10px;margin:8px 0;background:var(--panel)}
.artifact h1,.artifact h2,.artifact h3{margin-top:18px}.artifact li{margin:3px 0}
input.filter{padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);width:280px;max-width:100%}
"""


def page(title: str, body: str, data: dict) -> str:
    banner = f'<div class="banner">{escape(data["label"].capitalize())}. Every figure is computed by code; ' \
             'click any number to see how.</div>' if data.get("label") else ""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
            f"initial-scale=1'><title>{escape(title)}</title><style>{CSS}</style></head><body><header>"
            f"<span class=brand>Observation Agent</span><nav><a href='/'>Opportunities</a><a href='/coverage'>Coverage</a>"
            f"<a href='/artifacts'>Artifacts</a><a href='/method'>Method</a></nav></header><main>{banner}{body}</main></body></html>")


def kind(k: str) -> str:
    return f'<span class="pill k-{k}" title="{escape(KIND_HELP.get(k, ""))}">{escape(k)}</span>'


def highlight(text: str, spans: list[dict], focus: str | None, blocks: list[dict]) -> Markup:
    """Escape raw text and wrap verified spans in <mark>; nested/overlapping spans are flattened."""
    cuts = sorted({0, len(text)} | {s["start"] for s in spans} | {s["end"] for s in spans}
                  | {b["start"] for b in blocks} | {b["end"] for b in blocks})
    out, first = [], True
    for a, b in zip(cuts, cuts[1:]):
        seg = escape(text[a:b])
        on = [s for s in spans if s["start"] <= a and b <= s["end"]]
        blk = next((x for x in blocks if x["start"] <= a and b <= x["end"]), None)
        if blk:
            seg = Markup(f'<span class="quoted{" hidden-block" if blk["hidden"] else ""}">') + seg + Markup("</span>")
        if on:
            is_focus = bool(focus) and any(s["id"] == focus for s in on)
            anchor = ' id="focus"' if is_focus and first else ""
            first = first and not is_focus
            seg = (Markup(f'<mark class="{"focus" if is_focus else ""}" title="{escape(" ".join(s["id"] for s in on))}"'
                          f'{anchor}>') + seg + Markup("</mark>"))
        out.append(seg)
    return Markup("").join(out)


def md(text: str, quote_link) -> Markup:
    """A small, safe Markdown renderer for the artifacts (escape first, then structure)."""
    html, in_list = [], None
    for raw in text.splitlines():
        line = str(escape(raw))
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        line = re.sub(r"\[Q(\d+)\]", lambda m: quote_link(m.group(1)), line)
        m = re.match(r"^(#{1,3}) (.*)", line)
        lm = re.match(r"^\s*(?:- \[( |x)\] |[-*] |(\d+)\. )(.*)", line)
        if lm:
            tag = "ol" if lm.group(2) else "ul"
            if in_list != tag:
                if in_list:
                    html.append(f"</{in_list}>")
                html.append(f"<{tag}>")
                in_list = tag
            box = "☐ " if lm.group(1) == " " else ("☑ " if lm.group(1) == "x" else "")
            html.append(f"<li>{box}{lm.group(3)}</li>")
            continue
        if in_list:
            html.append(f"</{in_list}>")
            in_list = None
        if m:
            html.append(f"<h{len(m.group(1))}>{m.group(2)}</h{len(m.group(1))}>")
        elif line.strip() == "---":
            html.append("<hr>")
        elif line.strip():
            html.append(f"<p>{line}</p>")
    if in_list:
        html.append(f"</{in_list}>")
    return Markup("\n".join(html))


def create_app(slice_name: str = "dev", root: Path = ROOT) -> Flask:
    app = Flask(__name__)
    out = root / "output" / slice_name

    def load() -> dict:
        p = out / "dashboard" / "data.json"
        if not p.exists():
            abort(503, "no dashboard data yet: run the pipeline first")
        return json.loads(p.read_text(encoding="utf-8"))

    def claim_link(d: dict, cid: str, text: str | None = None) -> str:
        c = d["claims"][cid]
        return f'<a href="/claim/{escape(cid)}" title="{escape(c["formula"])}">{escape(text or c["display"])}</a>'

    @app.get("/")
    def overview():
        d = load()
        cov = d["coverage"]
        scored = [o for o in d["opportunities"] if o["status"] == "scored"]
        rows = []
        for o in d["opportunities"]:
            c = o["claims"]
            if o["status"] == "scored":
                money = claim_link(d, c["dollars"])
                hrs = claim_link(d, c["hours"])
                rng = f'<div class=muted>range {escape(_money(o["dollars_low"]))}–{escape(_money(o["dollars_high"]))}</div>'
            else:
                money, hrs, rng = f'<span class=muted>no rate: {escape(o["status"].replace("_", " "))}</span>', "", ""
            rows.append(f"<tr><td>{o.get('rank') or '–'}</td><td><a href='/opportunity/{escape(o['process_id'])}'>"
                        f"<strong>{escape(o['name'])}</strong></a><div class=muted>{escape(o['description'])}</div></td>"
                        f"<td class=num>{claim_link(d, c['events'])}</td><td class=num>{o['emails']}</td><td class=num>{hrs}</td>"
                        f"<td class=num>{money}{rng}</td><td>{escape(o['confidence'])}</td>"
                        f"<td>{_artifact_link(o, 'draft')}</td></tr>")
        body = (f"<h1>Where the time goes</h1><p class=muted>Recurring processes found in the email, ranked by estimated "
                f"monthly savings at a blended $85/hour.</p><div class=hero>"
                f"<div class=card><div class=label>Running total (events counted once)</div><div class=big>"
                f"{claim_link(d, 'total')}</div><div class=muted>per month, estimated</div></div>"
                f"<div class=card><div class=label>Opportunities with a rate</div><div class=big>{len(scored)}</div>"
                f"<div class=muted>of {len(d['opportunities'])} processes</div></div>"
                f"<div class=card><div class=label>Messages read</div><div class=big><a href='/coverage'>{cov['read']}/"
                f"{cov['messages_in_slice']}</a></div><div class=muted>every one with a status</div></div></div>"
                f"<table><tr><th>#</th><th>Process</th><th class=num>Events</th><th class=num>Emails</th>"
                f"<th class=num>Hours/mo</th><th class=num>$/month</th><th>Confidence</th><th>Artifact</th></tr>"
                f"{''.join(rows)}</table>")
        return page("Opportunities", body, d)

    @app.get("/opportunity/<pid>")
    def opportunity(pid):
        d = load()
        o = next((x for x in d["opportunities"] if x["process_id"] == pid), None) or abort(404)
        evs = [e for e in d["events"] if e["process_id"] == pid]
        ev_rows = "".join(f"<tr><td><a href='/event/{e['event_id']}'>{e['event_id']}</a></td><td>{escape(e['date'] or 'undated')}"
                          f"</td><td>{', '.join(_msg_link(d, k) for k in e['msg_keys'])}</td><td>{escape(', '.join(e['stall_signals']) or '–')}"
                          f"</td></tr>" for e in evs)
        mn = o["minutes_per_event"]
        chain = "".join(f"<li>{escape(d['claims'][c]['label'])}: {claim_link(d, c)} {kind(d['claims'][c]['kind'])}</li>"
                        for c in o["claims"].values())
        body = (f"<p><a href='/'>← Opportunities</a></p><h1>{escape(o['name'])}</h1><p>{escape(o['description'])}</p>"
                f"<div class=grid2><div class=card><div class=label>How it works today</div><p><strong>Trigger:</strong> "
                f"{escape(o['trigger'])}<br><strong>Output:</strong> {escape(o['output'])}<br><strong>Roles:</strong> "
                f"{escape(', '.join(o['roles']))}<br><strong>Mailboxes:</strong> {escape(', '.join(o['mailboxes']))}</p></div>"
                f"<div class=card><div class=label>Automation</div><p>{escape(o['automation']['idea'])}</p><p class=muted>"
                f"{escape(o['automation']['why'])}</p>{('<p>' + _artifact_link(o, 'Open the drafted ' + o['artifact']['artifact_type'] + ' →') + '</p>') if o.get('artifact') else ''}</div></div>"
                f"<h2>The numbers</h2><ul>{chain}</ul><p class=muted>Minutes saved per event are an <strong>assumption</strong> "
                f"({mn['low']}–{mn['high']} min, base {mn['base']}): {escape(mn['rationale'])}</p>"
                f"{'<p class=muted>' + escape(o.get('why', '')) + '</p>' if o.get('why') else ''}"
                f"<h2>Events ({len(evs)})</h2><table><tr><th>Event</th><th>Date</th><th>Messages</th><th>Stall signals</th></tr>{ev_rows}</table>")
        return page(o["name"], body, d)

    @app.get("/claim/<path:cid>")
    def claim(cid):
        d = load()
        c = d["claims"].get(cid) or abort(404)
        ins = "".join(f"<li>{escape(d['claims'][i]['label'])}: {claim_link(d, i)} {kind(d['claims'][i]['kind'])}</li>"
                      for i in c["inputs"])
        evq = "".join(_quote_block(d, q) for q in c["evidence"][:60])
        extra = ""
        if c["kind"] == "assumption":
            extra = (f"<p><strong>Range:</strong> {escape(str(c.get('low')))} – {escape(str(c.get('high')))}<br>"
                     f"<strong>Reason:</strong> {escape(c.get('reason', ''))}</p>")
        if c.get("event_ids"):
            extra += "<p><strong>Events:</strong> " + ", ".join(f"<a href='/event/{e}'>{e}</a>" for e in c["event_ids"]) + "</p>"
        if c.get("first_utc"):
            extra += f"<p class=muted>From {escape(c['first_utc'])} to {escape(c['last_utc'])}</p>"
        body = (f"<p><a href='/'>← Opportunities</a></p><h1>{escape(c['label'])}: {escape(c['display'])}</h1>"
                f"<p>{kind(c['kind'])} <span class=muted>{escape(KIND_HELP.get(c['kind'], ''))}</span></p>"
                f"<div class=formula>{escape(c['formula'])}</div>{extra}"
                f"{'<h2>Inputs</h2><ul>' + ins + '</ul>' if ins else ''}"
                f"{'<h2>Evidence (' + str(len(c['evidence'])) + ' verified quotes)</h2>' + evq if evq else ''}")
        return page(c["label"], body, d)

    @app.get("/event/<eid>")
    def event(eid):
        d = load()
        e = next((x for x in d["events"] if x["event_id"] == eid), None) or abort(404)
        obs = [d["observations"][o] for o in e["obs_ids"] if o in d["observations"]]
        rows = "".join(f"<div class=card style='margin:10px 0'><strong>{escape(o['task'])}</strong> <span class=muted>"
                       f"({escape(o['action_state'])}; {escape(o.get('evidence_kind') or '')} by {escape(o.get('evidence_author') or '')})"
                       f"</span>{_quote_block(d, 'q:' + o['obs_id'])}"
                       + "".join(f"<div class=muted>{escape(x['signal'])}: {escape(x['outcome'])}"
                                 f"{' — ' + escape(x.get('reason', '')) if x.get('reason') else ''}</div>"
                                 for x in o.get("signal_outcomes") or [])
                       + "</div>" for o in obs)
        body = (f"<p><a href='/opportunity/{escape(e['process_id'])}'>← Process</a></p><h1>Event {escape(eid)}</h1>"
                f"<p class=muted>Date {escape(e['date'] or 'undated')} ({escape(e['date_basis'])}); one event per process per "
                f"thread; merged by {escape(', '.join(e['merged_by']))}; threads {escape(', '.join(e['threads']))}</p>{rows}")
        return page(eid, body, d)

    @app.get("/message/<path:key>")
    def message(key):
        d = load()
        m = d["messages"].get(key) or abort(404)
        text = rawio.read_raw(root / "corpus" / f"{key}.txt")
        focus = request.args.get("q")
        body = (f"<p><a href='javascript:history.back()'>← Back</a></p><h1>{escape(m['subject'] or '(no subject)')}</h1>"
                f"<p class=muted>Original file <code>{escape(m['original_path'])}</code> · from {escape(m['sender'])} · "
                f"{escape(m['date_raw'] or 'no date')} · thread {escape(m['thread_id'])}</p>"
                f"<p class=muted>Highlighted text is a verified quote at its raw-byte offsets; grey text is quoted from earlier "
                f"messages (faded: not shown to the model in this message's batch).</p>"
                f"<pre class=raw>{highlight(text, m['highlights'], focus, m['blocks'])}</pre>"
                f"<script>var f=document.querySelector('mark.focus');if(f)f.scrollIntoView({{block:'center'}});</script>")
        return page(key, body, d)

    @app.get("/coverage")
    def coverage():
        d = load()
        cov = d["coverage"]
        rows = "".join(f"<tr><td><code>{escape(x['original_path'] or '')}</code></td><td>{escape(x['ingest_status'] or '')}</td>"
                       f"<td>{escape(x['date_status'] or '')}</td><td>{escape(x['analysis'])}</td>"
                       f"<td>{escape(x['special_handling'] or '')}</td><td class=num>{escape(x['readings'] or '0')}</td></tr>"
                       for x in cov["ledger"])
        body = (f"<h1>Coverage</h1><p>The ledger lists every file in the corpus ({cov['corpus_files']:,}: {cov['corpus_unique']:,} "
                f"unique, {cov['corpus_copies']} copies merged at ingest). This run read {cov['read']} of the "
                f"{cov['messages_in_slice']} messages in its slice. Dispositions: "
                f"{escape(', '.join(f'{k} {v}' for k, v in sorted(cov['dispositions'].items())))}.</p>"
                f"<p><input class=filter placeholder='Filter rows…' oninput=\"var v=this.value.toLowerCase();"
                f"document.querySelectorAll('#led tr+tr').forEach(r=>r.style.display=r.textContent.toLowerCase().includes(v)?'':'none')\"></p>"
                f"<table id=led><tr><th>Original file</th><th>Ingest</th><th>Date</th><th>Analysis</th><th>Special</th>"
                f"<th class=num>Readings</th></tr>{rows}</table>")
        return page("Coverage", body, d)

    @app.get("/artifacts")
    def artifacts():
        d = load()
        items = "".join(f"<li><a href='/artifact/{escape(a['name'])}'>{escape(a['name'])}</a> ({escape(a['artifact_type'])}, "
                        f"{len(a['cited'] or [])} verified citations)</li>" for a in d["artifacts"] if a.get("file"))
        return page("Artifacts", f"<h1>Drafted artifacts</h1><p class=muted>Drafts only: nothing is ever sent.</p><ul>{items}</ul>", d)

    @app.get("/artifact/<name>")
    def artifact(name):
        d = load()
        a = next((x for x in d["artifacts"] if x.get("name") == name and x.get("file")), None) or abort(404)
        text = (out / "artifacts" / a["file"]).read_text(encoding="utf-8")
        qmap = {}
        for line in text.splitlines():
            m = re.match(r"- \*\*\[Q(\d+)\]\*\* `([^`]+)` bytes (\d+)-(\d+)", line)
            if m:
                qmap[m.group(1)] = (m.group(2), m.group(3), m.group(4))

        def ql(n):
            if n not in qmap:
                return f"[Q{n}]"
            path, s, e = qmap[n]
            key = path.removeprefix("corpus/").rstrip(".")
            qid = next((k for k, v in d["quotes"].items() if v["msg_key"] == key and str(v["start"]) == s and str(v["end"]) == e), "")
            return f"<a href='/message/{escape(key)}?q={escape(qid)}' title='{escape(path)}'>[Q{n}]</a>"
        return page(name, f"<p><a href='/artifacts'>← Artifacts</a></p><div class='card artifact'>{md(text, ql)}</div>", d)

    @app.get("/method")
    def method():
        d = load()
        body = ("<h1>Method</h1><ul><li><strong>Observed</strong>: a verified quote, exact bytes of the original file.</li>"
                "<li><strong>Counted</strong>: exact code over observed items (events, months observed, events per month).</li>"
                "<li><strong>Assumption</strong>: minutes saved per event (proposed with a reason and a range) and the $85/hour rate."
                "</li><li><strong>Estimated</strong>: hours and dollars, a formula over counted values and assumptions.</li></ul>"
                "<p>Events: one per process per thread; copies merged at ingest; a quoted text seen in several threads counts once. "
                "Rates are per month observed in the mailboxes where the events were seen; they are not company-wide facts. "
                "Waiting time is never counted as work time. Missing replies in a partial archive are never read as work not done.</p>")
        return page("Method", body, d)

    @app.get("/api/data.json")
    def api():
        return jsonify(load())

    return app


def _artifact_link(o: dict, text: str) -> str:
    """A plain-str link (str + Markup would escape the HTML itself)."""
    if not o.get("artifact"):
        return ""
    return f"<a href='/artifact/{escape(o['artifact']['name'])}'>{escape(text)}</a>"


def _money(v: str) -> str:
    from decimal import Decimal
    from . import score
    return score.money(Decimal(v))


def _msg_link(d: dict, key: str) -> str:
    m = d["messages"].get(key)
    name = m["original_path"].removeprefix("corpus/") if m else key
    return f"<a href='/message/{escape(key)}'>{escape(name)}</a>"


def _quote_block(d: dict, qid: str) -> str:
    q = d["quotes"].get(qid)
    if not q:
        return ""
    m = d["messages"].get(q["msg_key"], {})
    return (f"<div class=q>“{escape(' '.join(q['quote_raw'].split()))}”<div class=muted>"
            f"<a href='/message/{escape(q['msg_key'])}?q={escape(qid)}'>{escape(m.get('original_path', q['msg_key']))} · bytes "
            f"{q['start']}–{q['end']}</a> · {escape(q.get('kind', '').replace('_', ' '))} · {escape(q.get('evidence_author') or '')}</div></div>")


def detach(slice_name: str, port: int) -> dict:
    """Start the server as a detached process that outlives the caller (a headless run ends; the server stays)."""
    log = ROOT / "output" / "logs" / "server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", "src.serve", "--slice", slice_name, "--port", str(port)]
    kw = {"cwd": str(ROOT), "stdout": open(log, "ab"), "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(args, **kw)
    rec = {"pid": p.pid, "url": f"http://{HOST}:{port}/", "slice": slice_name}
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def stop() -> bool:
    if not PIDFILE.exists():
        return False
    rec = json.loads(PIDFILE.read_text(encoding="utf-8"))
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(rec["pid"]), "/T", "/F"], capture_output=True)
        else:
            os.kill(rec["pid"], 15)
    finally:
        PIDFILE.unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Serve the dashboard on 127.0.0.1.")
    ap.add_argument("--slice", default="dev")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--detach", action="store_true")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args(argv)
    if args.stop:
        print("stopped" if stop() else "no server recorded")
        return 0
    if args.detach:
        stop()
        print(json.dumps(detach(args.slice, args.port)))
        return 0
    create_app(args.slice).run(host=HOST, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
