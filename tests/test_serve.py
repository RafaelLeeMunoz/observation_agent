"""Dashboard: 127.0.0.1 only, escaped raw text, byte-offset highlighting, drill-through from every $ figure."""
import json
import re

import pytest

from src import serve

RAW = "Subject: x\r\n\r\nPlease fix meter 4 <b>now</b> & confirm.\r\nThanks\r\n"
Q_START = RAW.index("Please")
Q_END = RAW.index("confirm.") + len("confirm.")


@pytest.fixture
def client(tmp_path):
    (tmp_path / "corpus" / "mb" / "inbox").mkdir(parents=True)
    (tmp_path / "corpus" / "mb" / "inbox" / "1.txt").write_bytes(RAW.encode())
    claims = {
        "rate": {"id": "rate", "label": "Rate", "value": "85", "display": "$85.00/hour", "formula": "given", "kind": "assumption",
                 "inputs": [], "evidence": [], "reason": "README", "low": "85", "high": "85"},
        "P1:events": {"id": "P1:events", "label": "Events", "value": "2", "display": "2", "formula": "count", "kind": "counted",
                      "inputs": [], "evidence": ["q:o1"], "event_ids": ["E0001"]},
        "P1:months": {"id": "P1:months", "label": "Months", "value": "6", "display": "6.00 months", "formula": "union",
                      "kind": "counted", "inputs": [], "evidence": ["q:o1"]},
        "P1:epm": {"id": "P1:epm", "label": "EPM", "value": "0.3", "display": "0.33/month", "formula": "events / months",
                   "kind": "counted", "inputs": ["P1:events", "P1:months"], "evidence": []},
        "P1:minutes": {"id": "P1:minutes", "label": "Minutes", "value": "30", "display": "30 min", "formula": "assumption",
                       "kind": "assumption", "inputs": [], "evidence": ["q:o1"], "reason": "r", "low": "10", "high": "40"},
        "P1:hours": {"id": "P1:hours", "label": "Hours", "value": "0.17", "display": "0.2 h", "formula": "epm x min / 60",
                     "kind": "estimated", "inputs": ["P1:epm", "P1:minutes"], "evidence": []},
        "P1:dollars": {"id": "P1:dollars", "label": "Dollars", "value": "14.17", "display": "$14.17", "formula": "hours x rate",
                       "kind": "estimated", "inputs": ["P1:hours", "rate"], "evidence": []},
        "total": {"id": "total", "label": "Total", "value": "14.17", "display": "$14.17", "formula": "sum", "kind": "estimated",
                  "inputs": ["P1:dollars"], "evidence": []}}
    data = {"label": "pipeline test, not a finding", "claims": claims, "total": claims["total"],
            "opportunities": [{"process_id": "P1", "name": "Meter fixes", "description": "d", "trigger": "t", "output": "o",
                               "roles": ["r"], "mailboxes": ["mb"], "automation": {"idea": "i", "why": "w", "artifact_type": "sop"},
                               "minutes_per_event": {"low": 10, "base": 30, "high": 40, "rationale": "r"}, "status": "scored",
                               "rank": 1, "events": 2, "emails": 3, "confidence": "low", "dollars_low": "4", "dollars_high": "19",
                               "claims": {"events": "P1:events", "months": "P1:months", "epm": "P1:epm", "minutes": "P1:minutes",
                                          "hours": "P1:hours", "dollars": "P1:dollars"}, "artifact": None}],
            "events": [{"event_id": "E0001", "process_id": "P1", "date": "2001-01-02", "date_basis": "message_date",
                        "msg_keys": ["mb/inbox/1"], "obs_ids": ["o1"], "stall_signals": {}, "merged_by": ["thread"], "threads": ["t1"]}],
            "observations": {"o1": {"obs_id": "o1", "task": "fix meter", "action_state": "requested", "evidence_kind": "own_words",
                                    "evidence_author": "a@x.com", "signal_outcomes": []}},
            "quotes": {"q:o1": {"msg_key": "mb/inbox/1", "start": Q_START, "end": Q_END, "quote_raw": RAW[Q_START:Q_END],
                                "kind": "observation_quote", "evidence_author": "a@x.com"}},
            "messages": {"mb/inbox/1": {"original_path": "corpus/mb/inbox/1.", "subject": "x", "sender": "a@x.com",
                                        "date_raw": "Tue", "thread_id": "t1",
                                        "highlights": [{"id": "q:o1", "start": Q_START, "end": Q_END, "kind": "observation_quote"}],
                                        "blocks": []}},
            "coverage": {"read": 1, "messages_in_slice": 1, "dispositions": {"observations": 1}, "corpus_files": 1,
                         "corpus_unique": 1, "corpus_copies": 0, "ledger": []},
            "artifacts": []}
    (tmp_path / "output" / "dev" / "dashboard").mkdir(parents=True)
    (tmp_path / "output" / "dev" / "dashboard" / "data.json").write_text(json.dumps(data))
    return serve.create_app("dev", root=tmp_path).test_client()


def test_the_server_binds_to_localhost_only():
    assert serve.HOST == "127.0.0.1"


def test_raw_email_is_escaped_and_the_quote_is_highlighted_at_its_byte_offsets(client):
    html = client.get("/message/mb/inbox/1?q=q:o1").get_data(as_text=True)
    assert "<b>now</b>" not in html and "&lt;b&gt;now&lt;/b&gt;" in html            # never trusted as HTML
    marked = re.search(r'<mark class="focus"[^>]*>(.*?)</mark>', html, re.S).group(1)
    assert marked == "Please fix meter 4 &lt;b&gt;now&lt;/b&gt; &amp; confirm."
    assert "corpus/mb/inbox/1." in html                                              # the original file name


def test_every_dollar_figure_drills_through_to_a_highlighted_raw_email(client):
    """Crawler: from the overview follow every claim link; every chain must reach a verified quote that opens the raw
    email with a highlight, or end in a documented assumption."""
    home = client.get("/").get_data(as_text=True)
    todo, seen, reached_quote, assumptions = re.findall(r"href=\"/claim/([^\"]+)\"", home), set(), False, set()
    assert "P1:dollars" in todo and "total" in todo
    while todo:
        cid = todo.pop()
        if cid in seen:
            continue
        seen.add(cid)
        page = client.get(f"/claim/{cid}")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "k-" in html and "formula" in html                                    # kind and formula shown
        if "k-assumption" in html.split("</h1>")[1].split("<h2>")[0]:
            assumptions.add(cid)
            assert "Reason:" in html
        for q in re.findall(r"href='/message/([^?']+)\?q=([^']+)'", html):
            msg = client.get(f"/message/{q[0]}?q={q[1]}").get_data(as_text=True)
            assert '<mark class="focus"' in msg
            reached_quote = True
        todo += re.findall(r"href=\"/claim/([^\"]+)\"", html)
    assert reached_quote and {"rate", "P1:minutes"} <= assumptions
    assert {"P1:dollars", "P1:hours", "P1:epm", "P1:events", "P1:months", "total"} <= seen


def test_pages_render_and_unknown_items_404(client):
    for path in ("/", "/opportunity/P1", "/event/E0001", "/coverage", "/artifacts", "/method", "/api/data.json"):
        assert client.get(path).status_code == 200, path
    assert client.get("/claim/nope").status_code == 404 and client.get("/opportunity/P9").status_code == 404


def test_markdown_artifacts_are_escaped_before_formatting():
    html = str(serve.md("# Title\n- [ ] check <script>x</script> [Q1]\n1. step", lambda n: f"<a>Q{n}</a>"))
    assert "<script>" not in html and "&lt;script&gt;" in html and "<h1>Title</h1>" in html and "<a>Q1</a>" in html


def test_an_opportunity_with_an_artifact_links_to_it_as_html(client, tmp_path):
    p = tmp_path / "output" / "dev" / "dashboard" / "data.json"
    d = json.loads(p.read_text())
    d["opportunities"][0]["artifact"] = {"name": "P1-sop", "artifact_type": "sop"}
    p.write_text(json.dumps(d))
    for path in ("/", "/opportunity/P1"):
        html = client.get(path).get_data(as_text=True)
        assert "href='/artifact/P1-sop'" in html and "&lt;a href" not in html
