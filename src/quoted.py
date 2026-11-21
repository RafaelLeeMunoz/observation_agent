"""R3 quoted text: split each body into new text and quoted blocks, with offsets.

Nothing is ever deleted. Blocks carry a `skip_reason` only when Claude doesn't need to read
them, because the same text is already in front of Claude in the same thread (batches keep a
thread together): (a) the block's text is the new text of another message in the same thread,
or (b) the same block was already shown earlier in the same thread.

Everything else is kept, because quoted text is often the only record of the message being
answered, and a reply can't be understood without the text it answers. A kept block whose text
also appears somewhere else (another message's new text, or a kept quote in another thread)
carries `links` to every other occurrence and a `first_seen` anchor, so code can count the event
behind it once (R6).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .dates import QuotedDate, parse_quoted_date
from .textutil import norm_ws

# The six markers that start quoted or forwarded text (notebook section 7).
MARKERS = {
    # '-----Original Message-----', and Outlook's '-----Original Appointment-----' for meeting forwards.
    "original_message": r"^[ \t>]*-{2,}\s*Original (Message|Appointment)\s*-{2,}",
    "forwarded_by":     r"^[ \t>]*-{2,}\s*Forwarded by\b",
    "lotus_from_on":    r"^[ \t>]*From:[^\n]*\bon \d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}",
    "outlook_block":    r"^[ \t>]*From:[^\n]*\r?\n[ \t>]*(Sent|Date):",
    # Lotus reply header: a name, then 'MM/DD/YYYY HH:MM AM' (or 'MM/DD/YY', as 1999 mail prints it), then To:.
    # Spotting the header needs no reading of the year.
    "lotus_name_date":  r"^[ \t]*[^\n]*?\s\d{2}/\d{2}/(\d{4}|\d{2})\s+\d{2}:\d{2}\s*(AM|PM)\s*\r?\n(\s*\r?\n){0,3}[ \t]*To:",
    "gt_quote":         r"^[ \t]*>",
    # An internet-mail header block with no 'Sent:' line ('Fwd:' forwards, bounce notices):
    # a From: line, then To/Cc/Sent/Date or folded continuation lines, then a Subject: line.
    "header_block":     r"^[ \t>]*From:[^\n]*\r?\n(?:(?:[ \t>]*(?:To|Cc|CC|Sent|Date|Bcc):|[ \t]+\S)[^\n]*\r?\n){1,12}?[ \t>]*Subject:",
}
MARK_RE = {name: re.compile(p, re.M | re.I) for name, p in MARKERS.items()}

MIN_SKIP_CHARS = 80        # shorter blocks are never skipped
INDEX_PREFIX = 64          # candidate lookup uses the first 64 normalized chars

_HDR_LINE = re.compile(r"^[ \t>]*(From|Sent|To|Cc|Bcc|Subject|Date|Importance)\s*:.*$", re.I | re.M)
_MARKER_LINE = re.compile(r"^.*(Original Message|Forwarded by).*$", re.I | re.M)
_NAME_DATE_LINE = re.compile(r"^[^\n]*\d{2}/\d{2}/(\d{4}|\d{2})\s+\d{2}:\d{2}\s*(AM|PM)\s*$", re.I | re.M)
_GT = re.compile(r"^[ \t]*>+ ?", re.M)
_FROM = re.compile(r"^[ \t>]*From:[ \t]*(.+?)\s*$", re.I | re.M)
_FROM_TAIL = re.compile(r"(\s+on\s+\d{1,2}/\d{1,2}/\d{2,4}.*|\s{2,}\d{1,2}/\d{1,2}/\d{4}.*)$", re.I)


AS_NEW_TEXT = "new_text"      # the same text is that message's own new text (its original)
AS_QUOTED = "quoted"          # the same text is a kept quoted block in that message


@dataclass(frozen=True)
class Link:
    key: str                  # the other message
    kind: str                 # AS_NEW_TEXT or AS_QUOTED
    thread_id: str            # its thread (always a different thread from the block's own)
    block_index: int | None = None   # for AS_QUOTED: which block in that message


@dataclass
class QuotedBlock:
    index: int
    kind: str                         # which marker starts it
    start: int                        # offsets into the raw file text
    end: int
    from_raw: str | None
    date_source: QuotedDate | None    # the block's own date, with what was written and any readings
    content: str                      # normalized text without header and marker lines
    skip_reason: str | None = None
    links: list[Link] = field(default_factory=list)   # other occurrences of this text, other threads
    first_seen: str | None = None     # "key" or "key#block": the occurrence an event is anchored to

    @property
    def quoted_date(self) -> datetime | None:
        """The block's own date (naive, used to the day)."""
        return self.date_source.value if self.date_source else None

    @property
    def date_offsets(self) -> tuple[int, int] | None:
        """File offsets of the date text exactly as written."""
        if self.date_source is None:
            return None
        return self.start + self.date_source.start, self.start + self.date_source.end


def first_marker(body: str) -> tuple[int, str] | None:
    best = None
    for name, rx in MARK_RE.items():
        m = rx.search(body)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best


def _block_content(raw: str) -> str:
    s = _GT.sub("", raw)
    s = _HDR_LINE.sub("", s)
    s = _MARKER_LINE.sub("", s)
    s = _NAME_DATE_LINE.sub("", s)
    return norm_ws(s)


def _block_from(raw: str, kind: str) -> str | None:
    m = _FROM.search(raw[:600])
    if m:
        return _FROM_TAIL.sub("", m.group(1)).strip() or None
    if kind == "lotus_name_date":
        for line in raw.splitlines():
            line = line.strip()
            if line:
                return _FROM_TAIL.sub("", line).strip() or None
    return None


_GT_LINE = re.compile(r"^[ \t]*>")


def _previous_line(text: str, pos: int) -> str:
    """The line just before the one starting at `pos` ('' at the start)."""
    head = text[:pos].rstrip("\r\n")
    if len(head) == len(text[:pos]):       # pos is not at a line start
        return ""
    return head[head.rfind("\n") + 1:] if head else ""


def split_body(text: str, body_start: int) -> tuple[int, list[QuotedBlock]]:
    """Return (new_text_end, blocks). `new_text_end` is the file offset where quoted text
    starts (end of the body when there is none). Block offsets are file offsets."""
    body = text[body_start:]
    first = first_marker(body)
    if first is None:
        return len(text), []
    q_start = body_start + first[0]
    quoted = text[q_start:]
    kinds = {}
    for name, rx in MARK_RE.items():
        for m in rx.finditer(quoted):
            if name == "gt_quote" and _GT_LINE.match(_previous_line(quoted, m.start())):
                continue                   # a run of '>' lines is one block, not one per line
            kinds.setdefault(m.start(), name)
    cuts = sorted(set(kinds) | {0})
    # A cut whose block would hold only marker/header lines (e.g. "-----Original Message-----"
    # right before its From:/Sent: lines) is merged into the next block, so every block keeps
    # its own header together with its text.
    merged = [cuts[0]]
    for c in cuts[1:]:
        if _block_content(quoted[merged[-1]:c]) == "":
            continue
        merged.append(c)
    cuts = merged
    blocks = []
    for i, (a, b) in enumerate(zip(cuts, cuts[1:] + [len(quoted)])):
        raw = quoted[a:b]
        blocks.append(QuotedBlock(
            index=i, kind=kinds.get(a, first[1]), start=q_start + a, end=q_start + b,
            from_raw=_block_from(raw, kinds.get(a, "")), date_source=parse_quoted_date(raw),
            content=_block_content(raw)))
    return q_start, blocks


def mark_originals(new_texts: dict[str, str], blocks_by_key: dict[str, list[QuotedBlock]],
                   thread_of: dict[str, str]) -> tuple[int, int]:
    """Rule R3(a): a block whose whole text is inside another message's new text.

    Skipped only when that original is in the **same thread** (so Claude reads it in the same
    batch). When the original is in another thread, the block is kept, because the reply needs
    it for context, and linked to the original so its event is counted once.

    `new_texts` maps each unique message key to its normalized new text; `thread_of` maps keys
    to thread ids. Returns (skipped, linked). The lowest matching key is cited, so the result
    is deterministic.
    """
    index: dict[str, list[str]] = defaultdict(list)
    for key in sorted(new_texts):
        n = new_texts[key]
        if len(n) >= MIN_SKIP_CHARS:
            index[n[:INDEX_PREFIX]].append(key)
    skipped = linked = 0
    for key, blocks in blocks_by_key.items():
        for b in blocks:
            if b.skip_reason or len(b.content) < MIN_SKIP_CHARS:
                continue
            matches = [c for c in index.get(b.content[:INDEX_PREFIX], ())
                       if c != key and b.content in new_texts[c]]
            same = [c for c in matches if thread_of[c] == thread_of[key]]
            if same:
                b.skip_reason = f"original_in_thread:{same[0]}"
                skipped += 1
            elif matches:
                b.links.extend(Link(c, AS_NEW_TEXT, thread_of[c]) for c in matches)
                linked += 1
    return skipped, linked


def mark_thread_repeats(thread_order: list[str], blocks_by_key: dict[str, list[QuotedBlock]]) -> int:
    """Rule R3(b): within one thread (keys in reading order), skip a block whose text was
    already shown in an earlier message of that thread. Returns the number marked."""
    seen: dict[str, str] = {}          # content -> key of the message where it was shown
    marked = 0
    for key in thread_order:
        for b in blocks_by_key.get(key, ()):
            if b.skip_reason or len(b.content) < MIN_SKIP_CHARS:
                continue                   # already skipped blocks were never shown
            prior = seen.get(b.content)
            if prior is None:
                seen[b.content] = key
            elif prior != key:
                b.skip_reason = f"repeat_in_thread:{prior}"
                b.links.clear()            # the copy shown earlier in this thread carries the links
                marked += 1
    return marked


def link_cross_thread_copies(blocks_by_key: dict[str, list[QuotedBlock]], thread_of: dict[str, str]) -> int:
    """Kept blocks (80+ chars) with the same text in different threads link to each other, so an
    event read from any copy can be matched to the others. Run after both skip rules.
    Returns the number of blocks that gained a link."""
    where: dict[str, list[tuple[str, QuotedBlock]]] = defaultdict(list)
    for key in sorted(blocks_by_key):
        for b in blocks_by_key[key]:
            if not b.skip_reason and len(b.content) >= MIN_SKIP_CHARS:
                where[b.content].append((key, b))
    gained = 0
    for occurrences in where.values():
        if len({thread_of[k] for k, _ in occurrences}) < 2:
            continue
        for key, b in occurrences:
            before = len(b.links)
            b.links.extend(Link(k2, AS_QUOTED, thread_of[k2], b2.index) for k2, b2 in occurrences
                           if thread_of[k2] != thread_of[key])
            gained += len(b.links) > before
    return gained


def assign_first_seen(blocks_by_key: dict[str, list[QuotedBlock]], order: dict[str, tuple]) -> int:
    """Give every linked block the occurrence its event is anchored to, the same for every copy of
    the same text: the earliest original (a message whose new text holds it) when any copy found
    one, else the earliest quoted copy. `order` sorts messages, e.g. (date, key).
    Returns the number of blocks given an anchor."""
    groups: dict[str, list[tuple[str, QuotedBlock]]] = defaultdict(list)
    for key in sorted(blocks_by_key):
        for b in blocks_by_key[key]:
            if b.links:
                groups[b.content].append((key, b))
    n = 0
    for members in groups.values():
        originals = {l.key for _, b in members for l in b.links if l.kind == AS_NEW_TEXT}
        if originals:
            anchor = min(originals, key=lambda k: order[k])
        else:
            copies = {(key, b.index) for key, b in members} | \
                     {(l.key, l.block_index) for _, b in members for l in b.links if l.kind == AS_QUOTED}
            k, i = min(copies, key=lambda c: (order[c[0]], c[1]))
            anchor = f"{k}#{i}"
        for _, b in members:
            b.first_seen = anchor
            n += 1
    return n
