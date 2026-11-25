"""Stage 2, batch: unique messages -> batch files for the extractor, plus a manifest (PLAN 3.2).

Each message is shown with its key, original file name, trimmed headers, its new text exactly as
in the raw file, and each quoted block either exactly as in the raw file or, when R3 skipped it,
as a pointer. Every batcher-written line is wrapped in the non-ASCII marks ⟦ ⟧, which can never
occur in the ASCII corpus, so nothing the batcher wrote can pass the quote check.

Threads stay whole and in reading order; batches are packed by estimated tokens. The manifest
records, for every batch, its exact keys, how each message was sent, every pointer, and a content
hash (the cache key for extraction).

Run:  .venv\\Scripts\\python -m src.batch --slice dev      (or --slice all)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import config, store

ROOT = Path(__file__).resolve().parent.parent
OPEN, CLOSE = "⟦", "⟧"          # ⟦ ⟧ : never in the ASCII corpus
BATCH_VERSION = "batch-v2"
# The extractor reads its batch with Claude Code's Read tool, which returns about 2,000 lines per
# call: a batch says how many lines it has and ends with an END BATCH line, so the extractor knows
# to read on, and no line is longer than this (the table summary is spread over several lines).
LINE_LIMIT = 2000


def mark(text: str) -> str:
    return f"{OPEN}{text}{CLOSE}"


@dataclass
class Rendered:
    key: str
    text: str
    mode: str                                     # full | table_summary | chunk i/n
    pointers: list[dict] = field(default_factory=list)


def _addresses(addrs: list[str], shown: int) -> str:
    head = ", ".join(addrs[:shown])
    return head + (f" (+{len(addrs) - shown} more)" if len(addrs) > shown else "")


def render_message(m: store.StoredMessage, shown: int = 10, part: tuple[int, int, int, int] | None = None) -> Rendered:
    """One message as Claude sees it. `part` = (i, n, start, end) renders one chunk of an oversized body."""
    lines = [mark(f"MESSAGE {m.key} | file {m.original_path}"),
             f"Date: {m.date_raw or mark('no Date header')}",
             f"From: {m.sender}",
             f"To: {_addresses(m.to, shown) or mark('none')}"]
    if m.cc:
        lines.append(f"Cc: {_addresses(m.cc, shown)}")
    lines.append(f"Subject: {m.subject}")
    pointers: list[dict] = []
    if m.table_summary is not None:
        mode = "table_summary"
        summary = json.dumps(m.table_summary, indent=1)
        lines += [mark("NEW TEXT (the message's prose lines; its table rows are summarized below)"),
                  *m.table_prose,
                  mark(f"BATCHER: the {m.table_summary['row_count']:,} table rows are not shown. "
                       f"Code summary, not email text, cannot be quoted:\n{summary}")]
    elif part is not None:
        i, n, start, end = part
        mode = f"chunk {i}/{n}"
        lines += [mark(f"NEW TEXT, part {i} of {n} of an oversized message"), m.text[start:end]]
    else:
        mode = "full"
        lines += [mark("NEW TEXT"), m.new_text]
        for b in m.blocks:
            if b.skip_reason:
                why, other = b.skip_reason.split(":", 1)
                lines.append(mark(f"BATCHER: quoted block {b.index} omitted; the same text is in message "
                                  f"{other} earlier in this thread"))
                pointers.append({"key": m.key, "block": b.index, "points_to": other, "reason": why})
            else:
                lines += [mark(f"QUOTED BLOCK {b.index}"), m.text[b.start:b.end]]
    lines.append(mark(f"END MESSAGE {m.key}"))
    return Rendered(m.key, "\n".join(lines) + "\n", mode, pointers)


@dataclass
class Unit:
    """What packing works with: a whole thread, or one part of a thread over the hard cap."""
    thread_id: str
    parts: list[Rendered]
    label: str = ""

    @property
    def text(self) -> str:
        head = mark(f"THREAD {self.thread_id}{self.label}") + "\n"
        return head + "".join(p.text for p in self.parts)


def thread_units(tid: str, keys: list[str], st: store.Store, cfg: dict) -> list[Unit]:
    cpt, cap, shown = cfg["chars_per_token"], cfg["hard_cap_tokens"], cfg["recipients_shown"]
    rendered = []
    for k in keys:
        m = st.messages[k]
        if m.chunks:
            n = len(m.chunks)
            rendered += [render_message(m, shown, (i + 1, n, s, e)) for i, (s, e) in enumerate(m.chunks)]
        else:
            rendered.append(render_message(m, shown))
    whole = Unit(tid, rendered)
    if len(whole.text) / cpt <= cap:
        return [whole]
    units, cur = [], []
    for r in rendered:                            # split at message boundaries, in order
        if cur and len(Unit(tid, cur + [r]).text) / cpt > cap:
            units.append(cur)
            cur = []
        cur.append(r)
    units.append(cur)
    return [Unit(tid, u, f" (part {i + 1} of {len(units)}; continued from the previous part)" if i else
                 f" (part 1 of {len(units)})") for i, u in enumerate(units)]


def make_batches(st: store.Store, keys: list[str], cfg: dict, prompt_version: str) -> list[dict]:
    """Deterministic batches covering every given unique key exactly once, threads kept whole."""
    wanted = set(keys)
    tids = []
    for k in keys:
        tid = st.messages[k].thread_id
        if tid not in tids:
            tids.append(tid)
    for tid in tids:                              # a slice must hold whole threads
        missing = [k for k in st.threads[tid] if k not in wanted]
        if missing:
            raise ValueError(f"thread {tid} is not whole in this slice: missing {missing[:3]}")
    first = lambda tid: min((st.messages[k].date_utc or "9999", k) for k in st.threads[tid])
    tids.sort(key=lambda t: (first(t), t))
    budget, cpt = cfg["token_budget"], cfg["chars_per_token"]
    cap = cfg.get("max_messages")                # M4 2a: optional; extraction time follows the work in a batch
    batches, cur = [], []
    for tid in tids:
        for u in thread_units(tid, st.threads[tid], st, cfg):
            size = len(u.text) / cpt
            over_cap = cap is not None and sum(len(x.parts) for x in cur) + len(u.parts) > cap   # never splits a thread
            if cur and (sum(len(x.text) for x in cur) / cpt + size > budget or over_cap):
                batches.append(cur)
                cur = []
            cur.append(u)
    if cur:
        batches.append(cur)
    split: dict[str, list[str]] = {}            # thread_id -> batch ids of its parts (over the hard cap)
    for i, units in enumerate(batches, start=1):
        for u in units:
            if u.label:
                split.setdefault(u.thread_id, []).append(f"b{i:03d}")
    out = []
    for i, units in enumerate(batches, start=1):
        bid = f"b{i:03d}"
        body = "".join(u.text for u in units)
        n_parts = sum(len(u.parts) for u in units)
        n_lines = body.count("\n") + 2                     # + this header line and the END BATCH line
        header = mark(f"BATCH {bid}: {n_parts} message parts in {len(units)} thread units, {n_lines:,} lines. "
                      f"It ends with the line {OPEN}END BATCH {bid}{CLOSE}: read on until you reach it. "
                      f"Everything between {OPEN} and {CLOSE} was written by the batcher, not by the emails.") + "\n"
        text = header + body + mark(f"END BATCH {bid}") + "\n"
        parts = [p for u in units for p in u.parts]
        out.append({
            "batch_id": bid,
            "text": text,
            "keys": list(dict.fromkeys(p.key for p in parts)),
            "threads": list(dict.fromkeys(u.thread_id for u in units)),
            "render": {p.key: p.mode for p in parts},
            "pointers": [ptr for p in parts for ptr in p.pointers],
            "split_threads": {t: split[t] for t in dict.fromkeys(u.thread_id for u in units) if t in split},
            "est_tokens": round(len(text) / cpt),
            "chars": len(text),
            "fingerprint": hashlib.sha256((BATCH_VERSION + prompt_version + text).encode("utf-8")).hexdigest(),
        })
    return out


def check_batches(batches: list[dict], st: store.Store, keys: list[str]) -> dict[str, bool]:
    """Coverage and safety checks the batch stage must pass before anything is sent to a model."""
    seen = [k for b in batches for k in b["keys"]]
    ptr_ok = True
    for b in batches:
        here = set(b["keys"])
        for p in b["pointers"]:
            ptr_ok &= p["points_to"] in here or any(p["points_to"] in x["keys"] for x in batches)
    return {
        "every unique message in exactly one batch": sorted(seen) == sorted(keys) and len(seen) == len(set(seen)),
        "no copies sent": all(st.messages[k].duplicate_of is None for k in seen),
        "pointers name a message that is in a batch": ptr_ok,
        "batcher marks never occur in the corpus": not any(OPEN in st.messages[k].text or CLOSE in st.messages[k].text
                                                           for k in keys),
        "every batch states its line count and ends with its END BATCH line": all(
            b["text"].endswith(mark(f"END BATCH {b['batch_id']}") + "\n")
            and f", {b['text'].count(chr(10)):,} lines." in b["text"].split("\n", 1)[0] for b in batches),
        f"no line longer than {LINE_LIMIT:,} characters (one Read returns it whole)": all(
            len(line) <= LINE_LIMIT for b in batches for line in b["text"].splitlines()),
    }


def write(batches: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for b in batches:
        (out_dir / f"{b['batch_id']}.txt").write_text(b["text"], encoding="utf-8", newline="")
    manifest = {"version": BATCH_VERSION,
                "batches": [{k: v for k, v in b.items() if k != "text"} for b in batches]}
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Make extractor batches and their manifest.")
    ap.add_argument("--slice", choices=["dev", "bench", "all"], default="dev")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    cfg = config.load()
    st = store.load()
    if args.slice in ("dev", "bench"):
        sl = json.loads((ROOT / "config" / "devslice.json").read_text(encoding="utf-8"))
        keys = sl["keys"] if args.slice == "dev" else sl["benchmark_keys"]
    else:
        keys = [m.key for m in st.unique()]
    batches = make_batches(st, keys, cfg["batch"], cfg["extract"]["prompt_version"])
    checks = check_batches(batches, st, keys)
    out = args.out or ROOT / "data" / "batches" / args.slice
    path = write(batches, out)
    toks = [b["est_tokens"] for b in batches]
    print(f"batch: {len(keys)} messages -> {len(batches)} batches (est. tokens min {min(toks)}, "
          f"max {max(toks)}, total {sum(toks):,}); wrote {path}")
    for name, ok in checks.items():
        print(f"batch: [{'ok' if ok else 'FAIL'}] {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
