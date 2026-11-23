"""Grounding packets: the source text a grounder may cite, cut into numbered sentences with raw offsets.

Design I (and J) checks each claimed stall signal with a separate, fenced grounder. The grounder never
copies text: it points at sentence ids, and code cuts the evidence out of the raw file at the sentence's
raw-byte offsets. So a citation cannot be mistyped, and every published quote is literally in the file.

A packet covers the claims of one extraction batch. For every message holding a claim it shows a
SECTION with the message's new text and each quoted block exactly as the batch showed it. A quoted block
the batcher hid (because its text is shown in another message of the thread, PLAN 3.1 R3) is replaced by
a pointer, and the message that holds the text is added as its own section, marked as linked: that is
the context design I may use and design H may not (eval/benchmark/evidence_spans.json `access`).

Sentences (deterministic): a region is split into paragraphs at blank lines; a line that starts a list
item or a header-like field ("From:", "- ", "2) ") starts a new piece; a piece is split after . ? or !
followed by whitespace and a capital, digit, quote or bracket; a piece over 600 characters is split at
line ends. Cuts are made in the raw text, so every sentence's (start, end) indexes the raw file. The
grounder is shown each sentence with its whitespace collapsed (wrapped lines joined into one line); that
is a reading of the text (CLAUDE.md rule 10), logged per sentence in the packet manifest.

Run:  .venv\\Scripts\\python -m src.packet --run data/extract/bench_runs/haiku_v8c_b001.json --out data/ground/packets/<name>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from . import store, verify
from .batch import CLOSE, OPEN, mark
from .textutil import norm_ws

ROOT = Path(__file__).resolve().parent.parent
PACKET_VERSION = "packet-v2"     # v2 (23 Nov 2026): a table message shows its prose lines only, as its batch does
MAX_PIECE = 600
_PARA = re.compile(r"\r?\n[ \t]*\r?\n")
_LINE = re.compile(r"\r?\n")
_STARTS_ITEM = re.compile(r"^\s*(?:[-*•>]+\s|\d{1,2}[.)]\s|[A-Z][A-Za-z ]{0,18}:\s)")
_SENT_END = re.compile(r"(?<=[.?!])[\"')\]]?\s+(?=[A-Z0-9\"'(\[])")


def _strip(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def sentences(raw: str, start: int, end: int) -> list[tuple[int, int]]:
    """Sentence spans (raw offsets) covering the text of raw[start:end] that has any letter or digit."""
    out: list[tuple[int, int]] = []
    paras, pos = [], start
    for m in _PARA.finditer(raw, start, end):
        paras.append((pos, m.start()))
        pos = m.end()
    paras.append((pos, end))
    for ps, pe in paras:
        pieces, cur, lpos = [], ps, ps                     # pieces: new list items / header lines
        for m in list(_LINE.finditer(raw, ps, pe)) + [None]:
            ls, le = lpos, (m.start() if m else pe)
            if ls > cur and _STARTS_ITEM.match(raw[ls:le]):
                pieces.append((cur, ls))
                cur = ls
            if m:
                lpos = m.end()
        pieces.append((cur, pe))
        for qs, qe in pieces:
            cuts, last = [], qs
            for m in _SENT_END.finditer(raw, qs, qe):
                cuts.append((last, m.start() + len(m.group(0).rstrip())))   # keep a closing quote/bracket
                last = m.end()
            cuts.append((last, qe))
            for cs, ce in cuts:
                cs, ce = _strip(raw, cs, ce)
                if ce - cs > MAX_PIECE:                      # a long run with no sentence end: cut at line ends
                    sub, ss = [], cs
                    for m in _LINE.finditer(raw, cs, ce):
                        if m.start() - ss >= MAX_PIECE // 2:
                            sub.append((ss, m.start()))
                            ss = m.end()
                    sub.append((ss, ce))
                else:
                    sub = [(cs, ce)]
                for a, b in sub:
                    a, b = _strip(raw, a, b)
                    if b > a and re.search(r"[A-Za-z0-9]", raw[a:b]):
                        out.append((a, b))
    return out


def _regions(m: store.StoredMessage) -> list[dict]:
    """The message as a batch shows it: new text, then each quoted block (shown, or hidden + where it is).

    A table message (PLAN 3.1 R4) is shown as the batch shows it: its prose lines only, each found in the
    raw new text in order, and a note that the rows are not shown; the rows are code's data, never evidence
    a model may cite (fixed 23 Nov 2026: packet-v1 first showed all 3,600 rows of 3221)."""
    if m.table_summary is not None:
        out, pos = [], m.body_start
        for line in m.table_prose or []:
            at = m.text.find(line, pos, m.new_text_end)
            if at < 0:
                raise ValueError(f"{m.key}: table prose line not found in its new text in order: {line[:40]!r}")
            out.append({"kind": "new_text", "start": at, "end": at + len(line), "block": None})
            pos = at + len(line)
        out.append({"kind": "table_note", "rows": m.table_summary.get("row_count")})
        return out
    out = [{"kind": "new_text", "start": m.body_start, "end": m.new_text_end, "block": None}]
    for b in m.blocks:
        if b.skip_reason:
            why, other = b.skip_reason.split(":", 1)
            out.append({"kind": "hidden", "block": b.index, "reason": why, "points_to": other,
                        "start": b.start, "end": b.end})
        else:
            out.append({"kind": "block", "start": b.start, "end": b.end, "block": b.index,
                        "author": b.from_raw, "date": b.date_text})
    return out


def build(run: dict, st: store.Store, packet_id: str, run_name: str, claim_filter=None) -> dict:
    """One packet for the claimed signals of one verified extraction batch. Pure (no files written).

    `claim_filter(obs, signal) -> bool` keeps only some claims (design J); default: every claim."""
    claims = []
    for o in run["observations"]:
        for s in o.get("stall_signals_claimed", o.get("stall_signals", [])):
            if claim_filter is None or claim_filter(o, s):
                claims.append((o, s))
    keys = list(dict.fromkeys(o["msg_key"] for o, _ in claims))
    sections, order = {}, []                     # msg_key -> section id; order of sections

    def add(key: str, linked_from: str | None):
        if key in sections:
            return
        sections[key] = f"M{len(sections) + 1}"
        order.append((key, linked_from))

    for k in keys:
        add(k, None)
        for r in _regions(st.messages[k]):
            if r["kind"] == "hidden":
                add(r["points_to"], k)
    lines = [mark(f"GROUNDING PACKET {packet_id} | {len(claims)} claim(s) | {len(order)} section(s). Everything "
                  f"between {OPEN} and {CLOSE} was written by code, not by the emails. Read on until the line "
                  f"{OPEN}END PACKET {packet_id}{CLOSE}.")]
    sent_index, readings, permitted = {}, [], {}
    for key, linked_from in order:
        m, sid = st.messages[key], sections[key]
        head = f"SECTION {sid}: message {key} | file {m.original_path} | Date: {m.date_raw or 'none'} | " \
               f"From: {m.sender} | Subject: {m.subject}"
        if linked_from:
            head += f" | shown because message {linked_from} (section {sections[linked_from]}) quotes it"
        lines.append(mark(head))
        n, prev = 0, None
        for r in _regions(m):
            if r["kind"] == "hidden":
                lines.append(mark(f"quoted block {r['block']} of this message is a copy of text in message "
                                  f"{r['points_to']}, shown in section {sections.get(r['points_to'], '?')}"))
                prev = None
                continue
            if r["kind"] == "table_note":
                lines.append(mark(f"the message's {r['rows']:,} table rows are not shown: they are data read by "
                                  "code, never evidence to cite"))
                continue
            label = "NEW TEXT" if r["kind"] == "new_text" else \
                f"QUOTED BLOCK {r['block']} (its author: {r.get('author') or 'not in its header'})"
            if label != prev:                    # a table message's prose lines share one NEW TEXT label
                lines.append(mark(label))
            prev = label
            for a, b in sentences(m.text, r["start"], r["end"]):
                n += 1
                sent = f"{sid}.S{n}"
                shown = norm_ws(m.text[a:b])
                region, block = verify.region_of(m, a, b)
                sent_index[sent] = {"msg_key": key, "start": a, "end": b, "region": region,
                                    "block": block.index if block else None, "section": sid}
                if shown != m.text[a:b]:
                    readings.append({"sentence": sent, "msg_key": key, "start": a, "end": b,
                                     "rule": "packet_line_join", "certainty": "exact", "outcome": "read",
                                     "why": "whitespace collapsed (wrapped lines joined) for display; evidence is "
                                            "cut from the raw bytes at these offsets, never from the shown text"})
                lines.append(f"[{sent}] {shown}")
        lines.append(mark(f"END SECTION {sid}"))
    lines.append(mark("CLAIMS (written by code from the extractor's output; the task wording is the extractor's, "
                      "not email text)"))
    out_claims = []
    for i, (o, s) in enumerate(claims, start=1):
        cid = f"C{i}"
        key = o["msg_key"]
        own = [x for x, v in sent_index.items() if v["msg_key"] == key and v["section"] == sections[key]
               and v["start"] < o["end"] and o["start"] < v["end"]]
        allowed = [sections[key]] + [sections[r["points_to"]] for r in _regions(st.messages[key]) if r["kind"] == "hidden"]
        permitted[cid] = sorted(set(allowed))
        lines.append(mark(f"{cid} | section {sections[key]} | activity: {norm_ws(o.get('task', ''))!r} | "
                          f"state: {o.get('action_state')} | its own text: {', '.join(own) or 'none'} | "
                          f"claimed signal: {s} | you may cite sentences from section(s) {', '.join(permitted[cid])}"))
        out_claims.append({"claim_id": cid, "obs_id": o["obs_id"], "msg_key": key, "signal": s,
                           "own_sentences": own, "permitted_sections": permitted[cid]})
    lines.append(mark(f"END PACKET {packet_id}"))
    text = "\n".join(lines) + "\n"
    return {"packet_id": packet_id, "version": PACKET_VERSION, "run": run_name,
            "source_attempt": run.get("attempt_id"), "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "claims": out_claims, "sections": {sid: k for k, sid in sections.items()},
            "sentences": sent_index, "readings": readings, "est_tokens": round(len(text) / 4)}


def write(packet: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    pid = packet["packet_id"]
    (out_dir / f"{pid}.txt").write_text(packet["text"], encoding="utf-8", newline="")
    (out_dir / f"{pid}.manifest.json").write_text(
        json.dumps({k: v for k, v in packet.items() if k != "text"}, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a grounding packet from a verified extraction batch.")
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--packet-id", default="g001")
    args = ap.parse_args(argv)
    run = json.loads(args.run.read_text(encoding="utf-8"))
    pk = build(run, store.load(), args.packet_id, args.run.stem)
    write(pk, args.out)
    print(f"packet {pk['packet_id']}: {len(pk['claims'])} claims, {len(pk['sections'])} sections, "
          f"{len(pk['sentences'])} sentences, ~{pk['est_tokens']:,} tokens, {len(pk['readings'])} line-join readings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
