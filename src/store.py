"""Read the ingest stage's output (data/ingest.sqlite) for later stages.

Later stages never re-parse email: they use ingest's stored offsets and fields, and fetch raw text
through `rawio`, so every stage agrees on the same raw-byte coordinates.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import rawio

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "ingest.sqlite"


@dataclass
class Block:
    index: int
    kind: str
    start: int
    end: int
    from_raw: str | None           # the quoted message's own author, as written in its header
    quoted_date: str | None        # ISO, naive (the writer's clock; used to the day)
    date_text: str | None
    skip_reason: str | None
    links: list[dict]
    first_seen: str | None


@dataclass
class StoredMessage:
    key: str
    local_path: str
    original_path: str
    mailbox: str
    thread_id: str
    duplicate_of: str | None
    date_raw: str | None
    date_status: str
    date_utc: str | None
    sender: str
    to: list[str]
    cc: list[str]
    subject: str
    body_start: int
    new_text_end: int
    special_handling: str | None
    sha256: str
    blocks: list[Block] = field(default_factory=list)
    chunks: list[tuple[int, int]] = field(default_factory=list)
    table_prose: list[str] | None = None
    table_summary: dict | None = None
    root: Path = ROOT               # the folder that holds `corpus/`
    _text: str | None = None

    @property
    def text(self) -> str:
        """The whole raw file, exactly as stored (raw-byte offsets index into this)."""
        if self._text is None:
            self._text = rawio.read_raw(self.root / self.local_path)
        return self._text

    @property
    def new_text(self) -> str:
        return self.text[self.body_start:self.new_text_end]

    @property
    def date(self) -> datetime | None:
        return datetime.fromisoformat(self.date_utc) if self.date_utc else None


@dataclass
class Store:
    messages: dict[str, StoredMessage]
    threads: dict[str, list[str]]          # thread_id -> keys in reading order
    meta: dict[str, str]

    def unique(self) -> list[StoredMessage]:
        return [m for m in self.messages.values() if m.duplicate_of is None]


def load(db: Path = DEFAULT_DB, root: Path = ROOT) -> Store:
    if not db.exists():
        raise FileNotFoundError(f"{db} not found: run `python -m src.ingest` first")
    con = sqlite3.connect(db)
    meta = dict(con.execute("SELECT name, value FROM meta").fetchall())
    if meta.get("reconciliation_ok") != "1":
        raise RuntimeError("the ingest database is marked as failed reconciliation; rebuild it first")
    msgs: dict[str, StoredMessage] = {}
    for row in con.execute(
            "SELECT key, local_path, original_path, mailbox, thread_id, duplicate_of, date_raw, date_status, "
            "date_utc, sender, to_json, cc_json, subject, body_start, new_text_end, special_handling, sha256 "
            "FROM messages"):
        (key, lp, op, mb, tid, dup, draw, dst, dutc, snd, to_j, cc_j, subj, bs, nte, sh, sha) = row
        msgs[key] = StoredMessage(key, lp, op, mb, tid, dup, draw, dst, dutc, snd, json.loads(to_j),
                                  json.loads(cc_j), subj, bs, nte, sh, sha, root=root)
    for key, idx, kind, s, e, frm, qd, dt, skip, links, fs in con.execute(
            "SELECT key, idx, kind, start, end, from_raw, quoted_date, date_text, skip_reason, links_json, first_seen "
            "FROM quoted_blocks ORDER BY key, idx"):
        msgs[key].blocks.append(Block(idx, kind, s, e, frm, qd, dt, skip, json.loads(links or "[]"), fs))
    for key, idx, s, e in con.execute("SELECT key, idx, start, end FROM chunks ORDER BY key, idx"):
        msgs[key].chunks.append((s, e))
    for key, prose, summary in con.execute("SELECT key, prose_json, summary_json FROM table_summary"):
        msgs[key].table_prose, msgs[key].table_summary = json.loads(prose), json.loads(summary)
    threads = {tid: json.loads(keys) for tid, keys in con.execute("SELECT thread_id, keys_json FROM threads")}
    con.close()
    return Store(msgs, threads, meta)
