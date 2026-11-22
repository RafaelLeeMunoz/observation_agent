"""Stage 1, ingest: raw files -> SQLite tables + coverage ledger (PLAN 3.1, rules R1-R8).

Run it with:   .venv\\Scripts\\python -m src.ingest            (skips if nothing changed)
               .venv\\Scripts\\python -m src.ingest --force    (rebuild)

The corpus is read, never written. Files may be named `20.txt` (our Windows copy) or `20.`
(the original); both resolve to the key `mailbox/folder/20`, and citations always use the
original name `corpus/mailbox/folder/20.`.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.parser import HeaderParser
from email.utils import getaddresses
from pathlib import Path

from . import dates, dedupe, ledger, quoted, rawio, tables, threads, windows
from .textutil import has_reply_prefix, norm_ws, sort_key, subject_norm

# The cache fingerprint covers the corpus bytes, this version string AND the source code of every
# module in src/, so a code change can never reuse stale output even if nobody bumps the version.
INGEST_VERSION = "m2.4"
SRC_DIR = Path(__file__).resolve().parent

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus"
DEFAULT_DB = ROOT / "data" / "ingest.sqlite"
DEFAULT_LEDGER = ROOT / "data" / "ledger.csv"

_NAME = re.compile(r"^(\d+)\.(txt)?$")
_SPLIT = re.compile(r"\r?\n\r?\n")


# --- file names ---

def key_from_parts(mailbox: str, folder: str, filename: str) -> str | None:
    """'20.txt' and '20.' both give 'mailbox/folder/20'. Anything else is not an email."""
    m = _NAME.match(filename)
    return f"{mailbox}/{folder}/{int(m.group(1))}" if m else None


def original_path(key: str) -> str:
    """The citation name: the original trailing-dot file, e.g. 'corpus/giron-d/inbox/20.'."""
    return f"corpus/{key}."


@dataclass(frozen=True)
class Found:
    key: str
    local_path: str      # e.g. 'corpus/giron-d/inbox/20.txt', relative to the corpus's parent
    os_path: str         # what open() needs (extended-length on Windows, so '20.' works)


def discover(corpus: Path) -> tuple[list[Found], list[str]]:
    """Return ([Found] sorted by key, [ignored relative paths]). Lists with os.walk, not pathlib:
    on Windows pathlib silently misses names ending in '.'."""
    found, ignored = [], []
    for parts, osp in rawio.walk_files(corpus):
        key = key_from_parts(*parts) if len(parts) == 3 else None
        if key is None:
            ignored.append("/".join(parts))
        else:
            found.append(Found(key, "/".join((corpus.name, *parts)), osp))
    keys = [f.key for f in found]
    if len(keys) != len(set(keys)):
        dupes = [k for k, n in Counter(keys).items() if n > 1]
        raise ValueError(f"both name styles exist for the same message: {dupes[:5]}")
    found.sort(key=lambda f: sort_key(f.key))
    return found, ignored


def load_manifest(corpus: Path) -> dict[str, dict]:
    """MANIFEST.csv rows by key, when the corpus has one (the grader's copy may not)."""
    path = corpus / "MANIFEST.csv"
    if not path.exists():
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            parts = row["new_path"].split("/")[-3:]
            key = key_from_parts(*parts)
            if key:
                out[key] = row
    return out


# --- one message ---

@dataclass
class Message:
    key: str
    mailbox: str
    folder: str
    local_path: str
    original_path: str
    size_bytes: int
    sha256: str
    text: str                      # whole file; offsets everywhere index into this
    body_start: int
    parse_status: str
    message_id: str | None
    date_raw: str | None
    sender: str
    to: list[str]
    cc: list[str]
    subject: str
    subject_norm: str
    parsed_date: dates.ParsedDate
    body_hash: str = ""
    new_text_end: int = 0
    blocks: list[quoted.QuotedBlock] = field(default_factory=list)
    is_root: bool = True
    clock_check: str = dates.UNTESTABLE
    duplicate_of: str | None = None
    duplicate_reason: str | None = None
    seen_in_mailboxes: list[str] = field(default_factory=list)
    thread_id: str | None = None
    special_handling: str | None = None
    table: tables.TableResult | None = None
    chunks: list[tuple[int, int]] = field(default_factory=list)    # file offsets, oversized prose only
    header_readings: list["Reading"] = field(default_factory=list)  # e.g. recovered addresses

    @property
    def body(self) -> str:
        return self.text[self.body_start:]

    @property
    def body_bytes(self) -> int:
        return len(self.body.encode("utf-8"))

    @property
    def new_text(self) -> str:
        return self.text[self.body_start:self.new_text_end]

    @property
    def date_utc(self) -> datetime | None:
        return self.parsed_date.utc

    @property
    def people(self) -> set[str]:
        return {a for a in [self.sender, *self.to, *self.cc] if a}


_EMAIL = re.compile(r"[A-Za-z0-9._%'+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _addresses(key: str, text: str, head_end: int, field_name: str, values: list[str] | None,
               readings: list) -> list[str]:
    """Addresses from one header field. Python's parser drops malformed entries (the export left
    some, e.g. `".'"wehner@enron.com>` or X.400 '/o=enron/.../cn=...@enron.com'). Any address the
    raw field visibly contains but the parser dropped is kept, and logged as a reading."""
    parsed = [a.lower() for _, a in getaddresses(values or []) if a]
    seen = set(parsed)
    raw = " ".join(values or [])
    for token in _EMAIL.findall(raw):
        low = token.lower()
        if low in seen:
            continue
        seen.add(low)
        parsed.append(low)
        start = text.find(token, 0, head_end)
        ctx = norm_ws(raw[max(0, raw.find(token) - 25):raw.find(token) + len(token) + 2])
        readings.append(Reading(
            key, f"header {field_name}", start, start + len(token), token, low, "address_recovered",
            f"malformed {field_name} entry (...{ctx}...) is dropped by Python's address parser; "
            f"the address it visibly contains is kept", certainty="inferred"))
    return parsed


def parse_file(found: Found) -> Message:
    raw = rawio.read_bytes(found.os_path)
    text, status = rawio.decode(raw)
    m = _SPLIT.search(text)
    if m:
        head, body_start = text[:m.start()], m.end()
    else:
        head, body_start, status = text, len(text), "no_header_break"
    h = HeaderParser().parsestr(head)
    key = found.key
    readings: list[Reading] = []
    sender = _addresses(key, text, len(head), "From", h.get_all("From"), readings)
    subject = re.sub(r"\s+", " ", h.get("Subject") or "").strip()
    mailbox, folder, _ = key.split("/")
    return Message(
        key=key, mailbox=mailbox, folder=folder,
        local_path=found.local_path, original_path=original_path(key),
        size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), text=text,
        body_start=body_start, parse_status=status,
        message_id=(h.get("Message-ID") or "").strip() or None, date_raw=h.get("Date"),
        sender=sender[0] if sender else (h.get("From") or "").strip().lower(),
        to=_addresses(key, text, len(head), "To", h.get_all("To"), readings),
        cc=_addresses(key, text, len(head), "Cc", h.get_all("Cc"), readings),
        subject=subject, subject_norm=subject_norm(subject),
        parsed_date=dates.parse_header_date(h.get("Date")),
        header_readings=readings,
    )


# --- no silent readings ---

EXACT, INFERRED = "exact", "inferred"
READ, NOT_READ = "read", "not_read"


@dataclass(frozen=True)
class Reading:
    """One place where code read the text as something other than exactly what is written, or
    found date-like text it could not read. Every one is stored with raw-byte file offsets, so
    the dashboard can show the original text and say why.

    certainty: `exact` = a lossless decoding (e.g. quoted-printable '=09' is a tab code);
               `inferred` = a judgment that could in principle be wrong (a two-digit year's
               century, a column's day/month order, an address inside a malformed entry).
    outcome:   `read` = a value was produced; `not_read` = no value, and `why` says why.
    """
    key: str
    where: str          # e.g. "quoted block 2 date" or "table column FLOW_DATE"
    start: int          # raw-byte offsets of the text as written
    end: int
    written: str
    read_as: str
    rule: str
    why: str
    certainty: str = EXACT
    outcome: str = READ


_CERTAINTY = {dates.TWO_DIGIT_YEAR: INFERRED, dates.QUOTED_PRINTABLE: EXACT}


def collect_readings(m: Message) -> list[Reading]:
    out = list(m.header_readings)
    for b in m.blocks:
        ds = b.date_source
        if ds is None:
            unread = dates.explain_unread_date(m.text[b.start:b.end])
            if unread:
                text, s, e, why = unread
                out.append(Reading(m.key, f"quoted block {b.index} date", b.start + s, b.start + e, text,
                                   "", "date_not_read", why, certainty="none", outcome=NOT_READ))
            continue
        start, end = b.date_offsets
        for rd in ds.readings:
            out.append(Reading(m.key, f"quoted block {b.index} date", start, end, ds.text.strip(),
                               ds.value.isoformat(sep=" ", timespec="minutes"), rd.rule, rd.why,
                               certainty=_CERTAINTY.get(rd.rule, INFERRED)))
    if m.table:
        for col, why in m.table.column_readings.items():
            kind = m.table.column_types[col]
            out.append(Reading(m.key, f"table column {col}", m.table.header_start, m.table.header_end,
                               col, kind, "column_format", why,
                               certainty=INFERRED if kind.startswith("date") else EXACT))
    return out


# --- the whole stage ---

@dataclass
class IngestResult:
    messages: dict[str, Message]
    ignored_files: list[str]
    manifest_problems: list[str]
    dedupe: dedupe.DedupeResult
    threads: list[threads.Thread]
    windows: dict[str, windows.Window]
    skipped_originals: int           # R3(a): original's new text is in the same thread
    skipped_repeats: int             # R3(b): shown earlier in the same thread
    readings: list[Reading] = field(default_factory=list)
    linked_to_original: int = 0      # kept blocks whose original is in another thread
    linked_copies: int = 0           # kept blocks whose text is also kept in another thread

    @property
    def unique(self) -> list[Message]:
        return [m for m in self.messages.values() if m.duplicate_of is None]

    def ledger_rows(self) -> list[ledger.LedgerRow]:
        per_key = Counter(rd.key for rd in self.readings)
        rows = []
        for m in self.messages.values():
            rows.append(ledger.LedgerRow(
                key=m.key, original_path=m.original_path,
                ingest_status=f"duplicate_of:{m.duplicate_of}" if m.duplicate_of else "unique",
                duplicate_reason=m.duplicate_reason or "",
                special_handling=m.special_handling or "",
                parse_status=m.parse_status, date_status=m.parsed_date.status,
                clock_check=m.clock_check, thread_id=m.thread_id or "",
                readings=per_key.get(m.key, 0),
                analysis_status=ledger.NOT_APPLICABLE if m.duplicate_of else ledger.PENDING))
        return rows

    def reconciliation(self) -> dict[str, bool]:
        """R6/R8 invariants. Every value must be True."""
        uniq = self.unique
        member = Counter(k for t in self.threads for k in t.keys)
        return {
            "every file has one ledger row": len(self.ledger_rows()) == len(self.messages),
            "files = unique + copies": len(self.messages) == len(uniq) + len(self.dedupe.merges),
            "every unique message in exactly one thread":
                set(member) == {m.key for m in uniq} and all(n == 1 for n in member.values()),
            "unique - threads = messages that joined":
                len(uniq) - len(self.threads) == sum(len(t.keys) - 1 for t in self.threads),
            "no copy points at another copy (no chains)":
                all(self.messages[g.canonical].duplicate_of is None for g in self.dedupe.merges),
            "manifest matches": not self.manifest_problems,
            "every reading points at its text as written":
                all(rd.written in self.messages[rd.key].text[rd.start:rd.end] for rd in self.readings),
            "offsets are raw-byte offsets (one character per byte in every file)":
                all(len(m.text) == m.size_bytes for m in self.messages.values()),
            "oversized prose is fully covered by its chunks":
                all(not m.chunks or ("".join(m.text[a:b] for a, b in m.chunks) == m.body)
                    for m in self.messages.values()),
            "no oversized message left without handling":
                all(not tables.needs_special_handling(m.body) or m.table or m.chunks for m in uniq),
            **self._quoted_link_checks(),
        }

    def _quoted_link_checks(self) -> dict[str, bool]:
        """R3: a block is skipped only for text already in its own thread; every link is real,
        crosses threads, is two-way for copies, and every copy of one text shares one anchor."""
        msgs = self.messages
        skips_ok = links_ok = anchors_ok = True
        anchor_of: dict[str, set] = {}
        for m in self.unique:
            for b in m.blocks:
                if b.skip_reason:
                    other = b.skip_reason.split(":", 1)[1]
                    skips_ok &= msgs[other].thread_id == m.thread_id and not b.links
                for l in b.links:
                    t = msgs[l.key]
                    links_ok &= t.thread_id == l.thread_id != m.thread_id
                    if l.kind == quoted.AS_NEW_TEXT:
                        links_ok &= b.content in norm_ws(t.new_text)
                    else:
                        tb = t.blocks[l.block_index]
                        links_ok &= tb.content == b.content and any(
                            x.key == m.key and x.block_index == b.index for x in tb.links)
                if b.links:
                    anchors_ok &= b.first_seen is not None
                    anchor_of.setdefault(b.content, set()).add(b.first_seen)
                else:
                    anchors_ok &= b.first_seen is None
        anchors_ok &= all(len(a) == 1 for a in anchor_of.values())
        return {"quoted blocks are skipped only for text already in their own thread": skips_ok,
                "every quoted link is real, crosses threads and is two-way": links_ok,
                "every copy of one quoted text shares one anchor": anchors_ok}


def build(corpus: Path = DEFAULT_CORPUS) -> IngestResult:
    found, ignored = discover(corpus)
    manifest = load_manifest(corpus)
    msgs = {f.key: parse_file(f) for f in found}

    problems = []
    if manifest:
        for k, m in msgs.items():
            row = manifest.get(k)
            if row is None:
                problems.append(f"{k}: not in MANIFEST.csv")
            elif row["sha256"] != m.sha256 or int(row["bytes"]) != m.size_bytes:
                problems.append(f"{k}: content differs from MANIFEST.csv")
        problems += [f"{k}: in MANIFEST.csv but missing" for k in manifest if k not in msgs]

    # R3 split, then the root flag (R5) and the clock check (R1), which both need it.
    for m in msgs.values():
        m.body_hash = dedupe.body_hash(m.body)
        m.new_text_end, m.blocks = quoted.split_body(m.text, m.body_start)
        m.is_root = not (has_reply_prefix(m.subject) or m.blocks)
        m.clock_check = dates.clock_check(m.parsed_date, m.new_text, m.text[m.new_text_end:])

    # R2 duplicates.
    dd = dedupe.find_duplicates(list(msgs.values()))
    for mg in dd.merges:
        msgs[mg.copy].duplicate_of = mg.canonical
        msgs[mg.copy].duplicate_reason = mg.reason
    for m in msgs.values():
        if m.duplicate_of is None:
            m.seen_in_mailboxes = sorted({m.mailbox} | {msgs[c.copy].mailbox for c in dd.merges if c.canonical == m.key})
    unique = [m for m in msgs.values() if m.duplicate_of is None]

    # R4 giant or tabular messages.
    for m in unique:
        if tables.needs_special_handling(m.body):
            if tables.table_share(m.body) >= tables.TABLE_SHARE:
                local = m.parsed_date.local
                m.table = tables.parse_table(m.text, m.body_start, local.date() if local else None)
            if m.table:
                m.special_handling = "table_parsed"
            else:
                m.chunks = tables.chunk_body(m.text, m.body_start)
                m.special_handling = "chunked"

    # R5 threads; copies point at their canonical's thread for display.
    th = threads.build_threads(unique)
    for t in th:
        for k in t.keys:
            msgs[k].thread_id = t.thread_id
    for m in msgs.values():
        if m.duplicate_of:
            m.thread_id = msgs[m.duplicate_of].thread_id

    # R3 skip marks, only where the same text is in front of Claude in the same thread:
    # (a) the original's new text is in this thread, (b) the block was shown earlier in it.
    # Then kept copies of text that also appears in other threads are linked, with one anchor.
    blocks_by_key = {m.key: m.blocks for m in unique if m.blocks}
    new_texts = {m.key: norm_ws(m.new_text) for m in unique}
    thread_of = {m.key: m.thread_id for m in unique}
    n_orig, _ = quoted.mark_originals(new_texts, blocks_by_key, thread_of)
    n_rep = sum(quoted.mark_thread_repeats(t.keys, blocks_by_key) for t in th)
    quoted.link_cross_thread_copies(blocks_by_key, thread_of)
    order = {m.key: (m.date_utc is None, m.date_utc or datetime.min.replace(tzinfo=timezone.utc), sort_key(m.key))
             for m in unique}
    quoted.assign_first_seen(blocks_by_key, order)
    shown = [b for bs in blocks_by_key.values() for b in bs if not b.skip_reason]
    n_linked_orig = sum(1 for b in shown if any(l.kind == quoted.AS_NEW_TEXT for l in b.links))
    n_linked_copies = sum(1 for b in shown if any(l.kind == quoted.AS_QUOTED for l in b.links))

    # R7 mailbox windows.
    win = windows.mailbox_windows((m.mailbox, m.date_utc) for m in unique if m.date_utc is not None)

    readings = [rd for m in msgs.values() for rd in collect_readings(m)]
    return IngestResult(msgs, ignored, problems, dd, th, win, n_orig, n_rep, readings,
                        linked_to_original=n_linked_orig, linked_copies=n_linked_copies)


# --- output ---

SCHEMA = """
CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT);
CREATE TABLE messages (
  key TEXT PRIMARY KEY, mailbox TEXT, folder TEXT, original_path TEXT, local_path TEXT,
  size_bytes INT, body_bytes INT, sha256 TEXT, parse_status TEXT,
  message_id TEXT, date_raw TEXT, date_status TEXT, date_utc TEXT, date_local TEXT,
  tz_offset_min INT, clock_check TEXT, sender TEXT, to_json TEXT, cc_json TEXT,
  subject TEXT, subject_norm TEXT, is_root INT, body_start INT, new_text_end INT,
  body_hash TEXT, duplicate_of TEXT, duplicate_reason TEXT, seen_in_mailboxes_json TEXT,
  thread_id TEXT, special_handling TEXT, ingest_status TEXT, analysis_status TEXT);
CREATE TABLE quoted_blocks (
  key TEXT, idx INT, kind TEXT, start INT, end INT, from_raw TEXT, quoted_date TEXT,
  date_text TEXT, date_start INT, date_end INT, date_readings_json TEXT,
  skip_reason TEXT, links_json TEXT, first_seen TEXT, PRIMARY KEY (key, idx));
CREATE TABLE readings (
  key TEXT, location TEXT, start INT, end INT, written TEXT, read_as TEXT, rule TEXT, why TEXT,
  certainty TEXT, outcome TEXT);
CREATE TABLE chunks (key TEXT, idx INT, start INT, end INT, PRIMARY KEY (key, idx));
CREATE TABLE duplicates (
  copy_key TEXT PRIMARY KEY, canonical_key TEXT, reason TEXT, gap_hours REAL, recipients_overlap REAL);
CREATE TABLE same_body_separate (key TEXT PRIMARY KEY, compared_to TEXT, label TEXT, gap_hours REAL);
CREATE TABLE threads (
  thread_id TEXT PRIMARY KEY, subject_norm TEXT, n_messages INT, first_utc TEXT, last_utc TEXT,
  starts_with_root INT, inferred INT, keys_json TEXT, people_json TEXT);
CREATE TABLE table_rows (key TEXT, row_no INT, start INT, end INT, raw TEXT, values_json TEXT,
  PRIMARY KEY (key, row_no));
CREATE TABLE table_summary (key TEXT PRIMARY KEY, columns_json TEXT, prose_json TEXT, summary_json TEXT,
  bad_rows_json TEXT);
CREATE TABLE windows (mailbox TEXT PRIMARY KEY, first_utc TEXT, last_utc TEXT, months TEXT);
"""


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def write_db(r: IngestResult, path: Path, fingerprint: str, ledger_sha256: str) -> None:
    """Write atomically (temp file, then replace). meta records whether reconciliation passed and
    the ledger file's hash, so a failed or half-written run can never look like a valid cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")        # 'ingest.sqlite.tmp': never collides with the ledger's
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    rec = r.reconciliation()
    con.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("fingerprint", fingerprint), ("ingest_version", INGEST_VERSION),
        ("reconciliation_ok", "1" if all(rec.values()) else "0"),
        ("reconciliation_json", json.dumps(rec)), ("ledger_sha256", ledger_sha256),
        ("offsets", "raw-byte offsets into the original file (see src/rawio.py)"),
        ("built_at_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))])
    ledger_by_key = {row.key: row for row in r.ledger_rows()}
    for m in r.messages.values():
        p = m.parsed_date
        con.execute("INSERT INTO messages VALUES (" + ",".join("?" * 32) + ")", (
            m.key, m.mailbox, m.folder, m.original_path, m.local_path, m.size_bytes, m.body_bytes,
            m.sha256, m.parse_status, m.message_id, m.date_raw, p.status, _iso(p.utc), _iso(p.local),
            p.tz_offset_min, m.clock_check, m.sender, json.dumps(m.to), json.dumps(m.cc), m.subject,
            m.subject_norm, int(m.is_root), m.body_start, m.new_text_end, m.body_hash, m.duplicate_of,
            m.duplicate_reason, json.dumps(m.seen_in_mailboxes), m.thread_id, m.special_handling,
            ledger_by_key[m.key].ingest_status, ledger_by_key[m.key].analysis_status))
        con.executemany("INSERT INTO quoted_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (m.key, b.index, b.kind, b.start, b.end, b.from_raw, _iso(b.quoted_date),
             b.date_source.text if b.date_source else None,
             b.date_offsets[0] if b.date_source else None, b.date_offsets[1] if b.date_source else None,
             json.dumps([rd.rule for rd in b.date_source.readings]) if b.date_source else "[]",
             b.skip_reason,
             json.dumps([{"key": l.key, "as": l.kind, "thread_id": l.thread_id, "block": l.block_index}
                         for l in b.links]),
             b.first_seen)
            for b in m.blocks])
        if m.table:
            con.executemany("INSERT INTO table_rows VALUES (?,?,?,?,?,?)", [
                (m.key, row.row_no, row.start, row.end, row.raw, json.dumps(row.values)) for row in m.table.rows])
            con.execute("INSERT INTO table_summary VALUES (?,?,?,?,?)", (
                m.key, json.dumps(m.table.columns), json.dumps(m.table.prose),
                json.dumps(m.table.summary), json.dumps(m.table.bad_rows)))
        con.executemany("INSERT INTO chunks VALUES (?,?,?,?)", [
            (m.key, i, a, b) for i, (a, b) in enumerate(m.chunks)])
    con.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (rd.key, rd.where, rd.start, rd.end, rd.written, rd.read_as, rd.rule, rd.why, rd.certainty, rd.outcome)
        for rd in r.readings])
    con.executemany("INSERT INTO duplicates VALUES (?,?,?,?,?)", [
        (g.copy, g.canonical, g.reason, g.gap_hours, g.recipients_overlap) for g in r.dedupe.merges])
    con.executemany("INSERT INTO same_body_separate VALUES (?,?,?,?)", [
        (s.key, s.compared_to, s.label, s.gap_hours) for s in r.dedupe.separate])
    con.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)", [
        (t.thread_id, t.subject_norm, len(t.keys), _iso(t.first_utc), _iso(t.last_utc),
         int(t.starts_with_root), int(t.inferred), json.dumps(t.keys), json.dumps(sorted(t.people)))
        for t in r.threads])
    con.executemany("INSERT INTO windows VALUES (?,?,?,?)", [
        (mb, _iso(a), _iso(b), str(windows.months_observed(r.windows, [mb]))) for mb, (a, b) in sorted(r.windows.items())])
    con.commit()
    con.close()
    tmp.replace(path)


def code_hash(src_dir: Path = SRC_DIR) -> str:
    """Hash of every .py file in src/ (names and bytes). Any code change changes it."""
    h = hashlib.sha256()
    for p in sorted(src_dir.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def fingerprint(corpus: Path, src_dir: Path = SRC_DIR) -> str:
    """Corpus bytes + every file name + INGEST_VERSION + the code. Same inputs -> same value."""
    h = hashlib.sha256(INGEST_VERSION.encode())
    h.update(code_hash(src_dir).encode())
    found, ignored = discover(corpus)
    for f in found:
        h.update(f.key.encode())
        h.update(hashlib.sha256(rawio.read_bytes(f.os_path)).digest())
    manifest = corpus / "MANIFEST.csv"
    if manifest.exists():
        h.update(hashlib.sha256(manifest.read_bytes()).digest())
    h.update(json.dumps(ignored).encode())
    return h.hexdigest()


def _meta(db: Path) -> dict[str, str]:
    if not db.exists():
        return {}
    try:
        con = sqlite3.connect(db)
        rows = dict(con.execute("SELECT name, value FROM meta").fetchall())
        con.close()
        return rows
    except sqlite3.Error:
        return {}


def cache_is_valid(db: Path, led: Path, fp: str) -> bool:
    """A cache hit needs all three: the same fingerprint, a run whose reconciliation passed, and
    the very ledger file that run wrote (its hash is recorded in the database)."""
    meta = _meta(db)
    return (meta.get("fingerprint") == fp and meta.get("reconciliation_ok") == "1"
            and led.exists() and rawio.sha256_file(led) == meta.get("ledger_sha256"))


def summary_lines(r: IngestResult) -> list[str]:
    uniq = r.unique
    st = Counter(m.parsed_date.status for m in r.messages.values())
    clock = Counter(m.clock_check for m in uniq)
    reasons = Counter(g.reason for g in r.dedupe.merges)
    special = {m.key: m for m in uniq if m.special_handling}
    lines = [
        f"files: {len(r.messages)} (ignored non-email files: {len(r.ignored_files)})",
        f"ledger: {len(r.ledger_rows())}/{len(r.messages)} rows | unique {len(uniq)} | copies {len(r.dedupe.merges)}",
        f"dates: " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())),
        f"clock check (unique): " + ", ".join(f"{k} {v}" for k, v in sorted(clock.items())),
        f"duplicates: {sum(len(g) - 1 for g in r.dedupe.candidate_groups)} candidates -> {len(r.dedupe.merges)} copies "
        f"{dict(sorted(reasons.items()))}; kept separate {len(r.dedupe.separate)}; ambiguous {len(r.dedupe.ambiguous)}",
        f"quoted: {sum(1 for m in r.messages.values() if m.blocks)} messages with quoted text; "
        f"blocks skipped (same text already in the thread): original {r.skipped_originals}, "
        f"repeat {r.skipped_repeats}; kept and linked: original in another thread {r.linked_to_original}, "
        f"copy in another thread {r.linked_copies}",
        f"threads: {len(r.threads)} ({sum(1 for t in r.threads if len(t.keys) > 1)} with 2+ messages)",
        f"readings (text read as something other than written, or not read; each logged with why): "
        + ", ".join(f"{k} {v}" for k, v in sorted(Counter(rd.rule for rd in r.readings).items())),
    ]
    for k, m in special.items():
        s = m.table.summary if m.table else {}
        lines.append(f"special: {m.original_path} {m.special_handling}, rows {s.get('row_count')}, "
                     f"variance {s.get('variance_check', {}).get('pass')}/{s.get('variance_check', {}).get('rows')}")
    lines.append("months observed: " + ", ".join(
        f"{mb} {windows.months_observed(r.windows, [mb]):.2f}" for mb in sorted(r.windows)))
    rec = r.reconciliation()
    lines.append("reconciliation: " + ("all pass" if all(rec.values()) else
                                       "FAILED: " + "; ".join(k for k, v in rec.items() if not v)))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest the corpus into SQLite and write the coverage ledger.")
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--force", action="store_true", help="rebuild even if the fingerprint is unchanged")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    fp = fingerprint(args.corpus)
    if not args.force and cache_is_valid(args.db, args.ledger, fp):
        print(f"ingest: cache hit ({fp[:12]}), nothing to do [{time.perf_counter() - t0:.1f}s]")
        return 0
    r = build(args.corpus)
    rec = r.reconciliation()
    # Ledger first (to a temp file), then the database that records the ledger's hash, then the
    # ledger moves into place. A crash anywhere leaves a mismatch, so the next run rebuilds.
    tmp_ledger = args.ledger.with_name(args.ledger.name + ".tmp")
    ledger.write_csv(r.ledger_rows(), tmp_ledger)
    write_db(r, args.db, fp, rawio.sha256_file(tmp_ledger))
    tmp_ledger.replace(args.ledger)
    for line in summary_lines(r):
        print("ingest:", line)
    print(f"ingest: wrote {args.db} and {args.ledger} [{time.perf_counter() - t0:.1f}s]")
    if not all(rec.values()):
        print("ingest: FAILED reconciliation; outputs are marked failed and will be rebuilt next run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
