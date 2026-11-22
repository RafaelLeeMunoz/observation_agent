"""Stage 4, verify: staged extractor output -> verified observations + rejection log (PLAN 3.3).

Trusted code writes the trusted output: the extractor writes only to data/extract/staging/; this
module validates that file and writes data/extract/verified/. Order of checks:

  1. parse the JSON            -> otherwise the whole batch fails
  2. schema                    -> otherwise the whole batch fails
  3. read receipt              -> exactly the manifest's keys, each once, else the batch fails
  4. dispositions              -> 'observations' iff the message has observations; reasons given
  5. each quote, against the raw file (rawio, raw-byte offsets):
       exact -> whitespace-normalized -> quoted-printable decoded (QP messages only);
       at least 25 characters; one region only (header / new text / one quoted block), else
       ambiguous; never only inside a block Claude wasn't shown
  6. evidence date and anchor from the block the quote sits in

A batch that fails steps 1-4 marks every message in it `failed` (retried, never `read`).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import ledger, schema, store
from .textutil import norm_ws

ROOT = Path(__file__).resolve().parent.parent
EXACT, WHITESPACE, QP_DECODED = "exact", "whitespace", "qp_decoded"
_QP_HEADER = re.compile(r"^Content-Transfer-Encoding:\s*quoted-printable", re.I | re.M)
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Match:
    start: int          # raw-byte offsets in the file
    end: int
    kind: str


# --- matching with offset maps back to the raw text ---

def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space; out[i] is the raw offset of normalized char i."""
    out, idx = [], []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            out.append(" ")
            idx.append(i)
            i = j
        else:
            out.append(text[i])
            idx.append(i)
            i += 1
    return "".join(out), idx


def _qp_decode_with_map(text: str) -> tuple[str, list[int]]:
    """Undo quoted-printable soft line breaks and =XX codes; out[i] is the raw offset."""
    out, idx = [], []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "=":
            if text.startswith("=\r\n", i):
                i += 3
                continue
            if text.startswith("=\n", i):
                i += 2
                continue
            h = text[i + 1:i + 3]
            if len(h) == 2 and all(ch in "0123456789ABCDEFabcdef" for ch in h):
                out.append(chr(int(h, 16)))
                idx.append(i)
                i += 3
                continue
        out.append(c)
        idx.append(i)
        i += 1
    return "".join(out), idx


def _find_all(hay: str, needle: str) -> list[int]:
    found, pos = [], hay.find(needle)
    while pos != -1:
        found.append(pos)
        pos = hay.find(needle, pos + 1)
    return found


_HEX = set("0123456789ABCDEFabcdef")


def _mapped(hay: str, idx: list[int], needle: str, kind: str, raw: str) -> list[Match]:
    """Map matches in a transformed text back to raw spans. A last character decoded from a
    quoted-printable '=XX' code ends after all three raw characters."""
    out = []
    for p in _find_all(hay, needle):
        start = idx[p]
        last = idx[p + len(needle) - 1]
        end = last + (3 if kind == QP_DECODED and raw[last] == "=" and set(raw[last + 1:last + 3]) <= _HEX
                      and len(raw[last + 1:last + 3]) == 2 else 1)
        out.append(Match(start, end, kind))
    return out


def find_matches(raw: str, quote: str, is_qp: bool) -> list[Match]:
    """Every place `quote` occurs in `raw`, trying exact, then whitespace-normalized, then QP-decoded."""
    exact = [Match(p, p + len(quote), EXACT) for p in _find_all(raw, quote)]
    if exact:
        return exact
    q = norm_ws(quote)
    if not q:
        return []
    norm, idx = _normalize_with_map(raw)
    ws = _mapped(norm, idx, q, WHITESPACE, raw)
    if ws:
        return ws
    if is_qp:
        dec, didx = _qp_decode_with_map(raw)
        dnorm, nidx = _normalize_with_map(dec)
        composed = [didx[i] for i in nidx]
        return _mapped(dnorm, composed, q, QP_DECODED, raw)
    return []


# --- where a match sits ---

def region_of(m: store.StoredMessage, start: int, end: int) -> tuple[str, store.Block | None]:
    """('header' | 'new_text' | 'block:<i>' | 'spans_regions', block)."""
    if end <= m.body_start:
        return "header", None
    if start >= m.body_start and end <= m.new_text_end:
        return "new_text", None
    for b in m.blocks:
        if start >= b.start and end <= b.end:
            return f"block:{b.index}", b
    return "spans_regions", None


FORWARD_KINDS = {"forwarded_by", "header_block"}


def attribution(m: store.StoredMessage, region: str, block: store.Block | None) -> dict:
    """Whose words the evidence is. A quote from a quoted or forwarded block belongs to that block's
    author and date, never to the person who replied or forwarded it."""
    if block is None:
        return {"evidence_kind": "header" if region == "header" else "own_words",
                "evidence_author": m.sender, "original": None, "original_status": "this message"}
    originals = [l["key"] for l in block.links if l.get("as") == "new_text"]
    return {"evidence_kind": "forwarded" if block.kind in FORWARD_KINDS else "quoted",
            "evidence_author": block.from_raw or "unknown (not in the quoted header)",
            "original": originals[0] if originals else None,
            "original_status": "in the corpus (linked)" if originals else
                               "not found in the corpus: known only from this quote"}


def evidence_date(m: store.StoredMessage, block: store.Block | None) -> tuple[str | None, str]:
    """(ISO date, basis). Basis: message_date | quoted_header | container_upper_bound | undated."""
    if block is not None and block.quoted_date:
        return block.quoted_date[:10], "quoted_header"
    if m.date_utc is None:
        return None, "undated"
    if block is not None:
        return m.date_utc[:10], "container_upper_bound"
    return m.date_utc[:10], "message_date"


def check_quote(m: store.StoredMessage, quote: str, min_chars: int) -> dict:
    """Verify one quote against its message. Returns {'ok': True, ...evidence} or {'ok': False, 'reason'}."""
    if len(norm_ws(quote)) < min_chars:
        return {"ok": False, "reason": f"too short (under {min_chars} characters)"}
    raw = m.text
    is_qp = bool(_QP_HEADER.search(raw[:m.body_start]))
    matches = find_matches(raw, quote, is_qp)
    if not matches:
        return {"ok": False, "reason": "not found in the raw file"}
    placed = [(mt, *region_of(m, mt.start, mt.end)) for mt in matches]
    shown = [(mt, r, b) for mt, r, b in placed if not (b is not None and b.skip_reason)]
    if not shown:
        return {"ok": False, "reason": "found only in quoted text that was not shown in this batch"}
    regions = {r for _, r, _ in shown}
    if "spans_regions" in regions:
        return {"ok": False, "reason": "quote runs across a region boundary (new text / quoted block)"}
    if len(regions) > 1:
        return {"ok": False, "reason": f"ambiguous: occurs in {len(regions)} different regions {sorted(regions)}"}
    mt, region, block = shown[0]
    date, basis = evidence_date(m, block)
    out = {"ok": True, "start": mt.start, "end": mt.end, "quote_raw": raw[mt.start:mt.end],
           "match_kind": mt.kind, "occurrences": len(shown), "region": region,
           "block": block.index if block else None, "evidence_date": date, "date_basis": basis,
           "anchor": block.first_seen if block else None, **attribution(m, region, block), "readings": []}
    if mt.kind != EXACT:
        out["readings"].append({"rule": f"quote_match_{mt.kind}", "certainty": "exact",
                                "why": "the quote matches the raw text only after "
                                       + ("collapsing whitespace" if mt.kind == WHITESPACE else
                                          "decoding quoted-printable and collapsing whitespace")})
    if basis == "container_upper_bound":
        out["readings"].append({"rule": "date_from_container", "certainty": "inferred",
                                "why": "the quoted block has no readable date; the date of the message "
                                       "holding it is used as an upper bound ('on or before')"})
    return out


# --- a whole batch ---

def verify_batch(staged_text: str, batch: dict, st: store.Store, vcfg: dict, attempt_id: str | None = None) -> dict:
    """Return the verification result for one batch (pure: no files written).

    `attempt_id` is the id handed to the extractor for this attempt; output carrying any other id
    is a stale or foreign file and fails the batch."""
    res = {"batch_id": batch["batch_id"], "attempt_id": attempt_id, "fingerprint": batch["fingerprint"],
           "status": "failed", "errors": [], "messages": [], "observations": [], "rejected": [],
           "verified_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        data = json.loads(staged_text)
    except json.JSONDecodeError as e:
        res["errors"].append(f"invalid JSON: {e}")
        return res
    errs = schema.validate(data, schema.load("extract.schema.json"))
    if errs:
        res["errors"] += [f"schema: {e}" for e in errs[:20]]
        return res
    if data["batch_id"] != batch["batch_id"]:
        res["errors"].append(f"batch_id {data['batch_id']!r} does not match {batch['batch_id']!r}")
    if attempt_id is not None and data["attempt_id"] != attempt_id:
        res["errors"].append(f"attempt_id {data['attempt_id']!r} is not the current attempt {attempt_id!r}: "
                             "a stale or foreign staging file")
    receipt = [x["msg_key"] for x in data["messages"]]
    want = batch["keys"]
    missing = sorted(set(want) - set(receipt))
    extra = sorted(set(receipt) - set(want))
    dupes = sorted({k for k in receipt if receipt.count(k) > 1})
    if missing:
        res["errors"].append(f"read receipt is missing {len(missing)} message(s): {missing[:5]}")
    if extra:
        res["errors"].append(f"read receipt lists {len(extra)} message(s) not in this batch: {extra[:5]}")
    if dupes:
        res["errors"].append(f"read receipt lists message(s) twice: {dupes[:5]}")
    obs_keys = [o["msg_key"] for o in data["observations"]]
    ids = [o["obs_id"] for o in data["observations"]]
    if len(ids) != len(set(ids)):
        res["errors"].append("observation ids are not unique")
    stray = sorted(set(obs_keys) - set(want))
    if stray:
        res["errors"].append(f"observations for message(s) not in this batch: {stray[:5]}")
    for x in data["messages"]:
        has = x["msg_key"] in obs_keys
        if x["disposition"] == "observations" and not has:
            res["errors"].append(f"{x['msg_key']}: disposition 'observations' but no observation")
        if x["disposition"] != "observations" and has:
            res["errors"].append(f"{x['msg_key']}: has observations but disposition {x['disposition']!r}")
        if x["disposition"] != "observations" and not x["reason"].strip():
            res["errors"].append(f"{x['msg_key']}: disposition {x['disposition']!r} needs a reason")
    if res["errors"]:
        return res

    res["messages"] = data["messages"]
    for o in data["observations"]:
        m = st.messages[o["msg_key"]]
        chk = check_quote(m, o["quote"], vcfg["min_quote_chars"])
        if chk.pop("ok"):
            res["observations"].append({**o, **chk, "original_path": m.original_path})
        else:
            res["rejected"].append({"obs_id": o["obs_id"], "msg_key": o["msg_key"], "quote": o["quote"],
                                    "reason": chk["reason"]})
    checked = len(res["observations"]) + len(res["rejected"])
    res["quotes"] = {"checked": checked, "verified": len(res["observations"]), "rejected": len(res["rejected"])}
    res["flags"] = review_flags(data, res["observations"])
    res["status"] = "verified"
    return res


def review_flags(data: dict, verified: list[dict]) -> list[dict]:
    """Listed for review, never failures: a message whose stated item count differs from the
    observations written for it, and two observations of one message whose quotes overlap."""
    per_msg: dict[str, int] = {}
    for o in data["observations"]:
        per_msg[o["msg_key"]] = per_msg.get(o["msg_key"], 0) + 1
    flags = [{"kind": "work_items_mismatch", "msg_key": x["msg_key"], "work_items": x["work_items"],
              "observations": per_msg.get(x["msg_key"], 0)}
             for x in data["messages"] if "work_items" in x and x["work_items"] != per_msg.get(x["msg_key"], 0)]
    flags += [{"kind": "overlapping_quotes", "msg_key": a["msg_key"], "obs_ids": [a["obs_id"], b["obs_id"]]}
              for i, a in enumerate(verified) for b in verified[i + 1:]
              if a["msg_key"] == b["msg_key"] and a["start"] < b["end"] and b["start"] < a["end"]]
    return flags


def canary_tripped(res: dict, vcfg: dict) -> bool:
    """First batch of a run: stop if at least `canary_min_quotes` were checked and too many failed."""
    q = res.get("quotes", {"checked": 0, "rejected": 0})
    return q["checked"] >= vcfg["canary_min_quotes"] and q["rejected"] / q["checked"] > vcfg["canary_max_fail_rate"]


def reverify(obs: dict, st: store.Store, vcfg: dict) -> bool:
    """Publish gate: re-check a verified observation against the raw file (zero tolerance)."""
    m = st.messages[obs["msg_key"]]
    chk = check_quote(m, obs["quote"], vcfg["min_quote_chars"])
    return bool(chk.get("ok")) and chk["start"] == obs["start"] and chk["end"] == obs["end"] \
        and m.text[obs["start"]:obs["end"]] == obs["quote_raw"]


def apply_to_status(res: dict, batch: dict, status: dict[str, str]) -> list[str]:
    """Ledger analysis status (R8): only a verified batch's receipted messages become `read`."""
    ok = res["status"] == "verified"
    receipt = [x["msg_key"] for x in res["messages"]] if ok else []
    return ledger.apply_batch_result(status, batch["keys"], receipt, ok)
