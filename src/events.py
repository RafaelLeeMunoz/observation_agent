"""Events: distinct occurrences of a process, built by code from verified observations (PLAN 3.1 R6).

The chain is file -> unique message -> thread -> event -> process. A model groups observations into
processes (src/synth.py); this module decides what counts as one occurrence:

  * duplicates never reach here: ingest merged the 40 copies and the batcher sends each unique message once;
  * one event per process per thread: replies, forwards and re-sends in a thread are one occurrence;
  * quoted copies count once: an observation whose quote sits in a quoted block with a `first_seen` anchor
    is attributed to the anchor message's thread, so the same quoted text seen in several threads (and the
    anchor's own new text) adds up to ONE event, not one per thread;
  * an event's date is the date of its earliest dated evidence (a quoted block's own date, to the day;
    a quoted block with no readable date gives the message date as an upper bound, logged by verify);
    an event with no dated evidence is counted but left out of date windows.

Every event lists its observations, messages, threads, the mailboxes its messages were seen in (copies
included, for the R7 denominator) and the published stall signals with their evidence.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import store

ROOT = Path(__file__).resolve().parent.parent
EVENTS_VERSION = "events-v1"


def seen_in_mailboxes(db: Path = store.DEFAULT_DB) -> dict[str, list[str]]:
    con = sqlite3.connect(db)
    out = {k: json.loads(v or "[]") for k, v in con.execute("SELECT key, seen_in_mailboxes_json FROM messages")}
    con.close()
    return out


def event_key(o: dict, st: store.Store) -> tuple[str, str]:
    """(key, kind): the thread this observation's occurrence belongs to."""
    anchor = o.get("anchor")
    if anchor:
        return st.messages[anchor.split("#", 1)[0]].thread_id, "quoted_anchor"
    return st.messages[o["msg_key"]].thread_id, "thread"


def build(observations: list[dict], assignment: dict[str, str], st: store.Store,
          seen: dict[str, list[str]]) -> list[dict]:
    """Events for every observation assigned to a process. Pure and deterministic."""
    groups: dict[tuple[str, str], list[dict]] = {}
    kinds: dict[tuple[str, str], set] = {}
    for o in observations:
        pid = assignment.get(o["obs_id"])
        if pid is None:
            continue
        key, kind = event_key(o, st)
        groups.setdefault((pid, key), []).append(o)
        kinds.setdefault((pid, key), set()).add(kind)
    events = []
    for n, ((pid, key), obs) in enumerate(sorted(groups.items()), start=1):
        dated = sorted((o["evidence_date"], o["obs_id"], o.get("date_basis")) for o in obs if o.get("evidence_date"))
        msgs = sorted({o["msg_key"] for o in obs})
        signals: dict[str, list[dict]] = {}
        for o in obs:
            for sg in o.get("stall_signals", []):
                ev = [e for e in o.get("published_evidence", []) if e["signal"] == sg]
                signals.setdefault(sg, []).extend({"obs_id": o["obs_id"], "msg_key": e["msg_key"], "start": e["start"],
                                                   "end": e["end"]} for e in ev)
        events.append({
            "event_id": f"E{n:04d}", "process_id": pid, "thread_key": key,
            "merged_by": sorted(kinds[(pid, key)]),
            "obs_ids": sorted(o["obs_id"] for o in obs), "msg_keys": msgs,
            "threads": sorted({st.messages[k].thread_id for k in msgs}),
            "mailboxes": sorted({mb for k in msgs for mb in (seen.get(k) or [st.messages[k].mailbox])}),
            "date": dated[0][0] if dated else None,
            "date_basis": dated[0][2] if dated else "undated",
            "date_from_obs": dated[0][1] if dated else None,
            "actors": sorted({a for o in obs for a in o.get("actors", [])}),
            "stall_signals": {sg: v for sg, v in sorted(signals.items())},
        })
    return events


def check(events: list[dict], assignment: dict[str, str]) -> list[str]:
    """Invariants: every assigned observation in exactly one event; at most one event per process per thread key."""
    problems = []
    seen_obs = [o for e in events for o in e["obs_ids"]]
    if sorted(seen_obs) != sorted(o for o in assignment):
        problems.append("assigned observations and event members differ")
    if len(seen_obs) != len(set(seen_obs)):
        problems.append("an observation is in more than one event")
    pairs = [(e["process_id"], e["thread_key"]) for e in events]
    if len(pairs) != len(set(pairs)):
        problems.append("two events of one process share a thread key")
    return problems
