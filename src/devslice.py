"""The development slice: ~200 unique messages in whole threads, chosen by fixed rules.

Every pick is a whole thread with a stated reason, so the slice is reproducible and reviewable:
known hard cases first, then negatives (mail that should yield no business process), then ordinary
threads filled in a fixed hash order, spread across the four mailboxes. A ~40-message benchmark is
a fixed subset of whole threads from the slice, for the user to label.

Run:  .venv\\Scripts\\python -m src.devslice     (writes config/devslice.json)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from . import config, ingest

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "devslice.json"
SLICE_VERSION = "devslice-v1"

_QP = re.compile(r"^Content-Transfer-Encoding:\s*quoted-printable", re.I | re.M)
BROADCAST_SENDERS = ("announcements.enron@enron.com", "no.address@enron.com", "coo.jeff@enron.com",
                     "enron.announcements@enron.com", "chairman.ken@enron.com")


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def select(r: ingest.IngestResult, target: int = 200, bench_target: int = 40, hold_target: int = 30) -> dict:
    uniq = {m.key: m for m in r.unique}
    by_tid = {t.thread_id: t for t in r.threads}
    tid_of = {k: t.thread_id for t in r.threads for k in t.keys}
    picked: dict[str, str] = {}                 # thread_id -> reason (first reason wins)

    def take(tid: str, reason: str) -> None:
        picked.setdefault(tid, reason)

    def size(tids) -> int:
        return sum(len(by_tid[t].keys) for t in tids)

    ordered = sorted(r.threads, key=lambda t: _h(t.thread_id))      # the fixed "random" order
    multi = [t for t in ordered if len(t.keys) > 1]

    # 1. Known hard cases.
    cal = sorted((t for t in r.threads if t.subject_norm == "calpine daily gas nomination"),
                 key=lambda t: (t.first_utc is None, t.first_utc, t.thread_id))
    for t in cal[:3]:
        take(t.thread_id, "calpine daily notice (recurring, same body on different days)")
    take(tid_of["farmer-d/logistics/3221"], "giant table message (sent as prose + code summary)")
    for t in [t for t in multi if any(_QP.search(uniq[k].text[:uniq[k].body_start]) for k in t.keys)][:2]:
        take(t.thread_id, "quoted-printable encoded message")
    for t in [t for t in multi if any(uniq[k].clock_check == "shifted" for k in t.keys)][:2]:
        take(t.thread_id, "clock-shifted Lotus mail (header 7-8 h behind)")
    anchored = sorted({b.first_seen for m in uniq.values() for b in m.blocks
                       if b.first_seen and "#" not in b.first_seen}, key=_h)
    for anchor in anchored[:1]:
        take(tid_of[anchor], "original of a quote kept and linked in another thread")
        linking = sorted({m.thread_id for m in uniq.values() for b in m.blocks if b.first_seen == anchor})
        for tid in linking[:1]:
            take(tid, "quote linked to an original in another thread (one event, two threads)")
    with_copies = sorted({g.canonical for g in r.dedupe.merges if r.messages[g.copy].mailbox != uniq[g.canonical].mailbox})
    for k in with_copies[:1]:
        take(tid_of[k], "message with a duplicate copy in another mailbox")
    for k in ("giron-d/sent/795", "williams-w3/inbox/142", "lokay-m/inbox/74", "lokay-m/inbox/50"):
        take(tid_of[k], f"date problem ({r.messages[k].parsed_date.status})")
    for rule in ("two_digit_year", "address_recovered", "date_not_read"):
        keys = sorted({x.key for x in r.readings if x.rule == rule and x.key in tid_of}, key=_h)
        if keys:
            take(tid_of[keys[0]], f"logged reading: {rule}")
    longest = max(multi, key=lambda t: sum(len(uniq[k].body) for k in t.keys))
    take(longest.thread_id, "longest multi-message thread (batch budget)")

    # 2. Negatives: mail where no business process should be found.
    broadcasts = [t for t in ordered if uniq[t.keys[0]].sender in BROADCAST_SENDERS]
    personal = [t for t in ordered if uniq[t.keys[0]].subject.lower().startswith("fwd:")
                and not uniq[t.keys[0]].sender.endswith("@enron.com")]
    for t in broadcasts[:3]:
        take(t.thread_id, "negative: company-wide broadcast")
    for t in personal[:3]:
        take(t.thread_id, "negative: personal forward from outside Enron")

    # 3. Ordinary threads, in hash order, spread across mailboxes by size.
    mb_total = Counter(m.mailbox for m in uniq.values())
    want = {mb: max(1, round(target * n / len(uniq))) for mb, n in mb_total.items()}
    have = Counter()
    for tid in picked:
        for k in by_tid[tid].keys:
            have[uniq[k].mailbox] += 1
    for t in multi:
        if size(picked) >= target:
            break
        mb = uniq[t.keys[0]].mailbox
        if t.thread_id not in picked and have[mb] < want[mb]:
            take(t.thread_id, "ordinary thread")
            have[mb] += len(t.keys)

    threads = sorted(picked, key=lambda t: (by_tid[t].first_utc is None, by_tid[t].first_utc, t))
    keys = [k for t in threads for k in by_tid[t].keys]

    # Benchmark: whole threads from the slice, one per hard case where possible, then ordinary.
    bench, seen = [], set()
    for t in threads:
        reason = picked[t].split(" (")[0]
        if reason not in seen and size(bench + [t]) <= bench_target:
            bench.append(t)
            seen.add(reason)
    for t in threads:
        if size(bench) >= bench_target:
            break
        if t not in bench and size(bench + [t]) <= bench_target + 5:
            bench.append(t)
    bench = [t for t in threads if t in bench]

    # Blind set: whole benchmark threads (~10 messages) the user labels before seeing any draft.
    blind = []
    for t in sorted(bench, key=lambda t: _h("blind:" + t)):
        if size(blind + [t]) <= 12:
            blind.append(t)
        if size(blind) >= 10:
            break

    # Holdout: ~30 messages in whole threads from OUTSIDE the slice, in a different fixed order,
    # spread over the mailboxes. Never batched or shown while the prompt is being developed;
    # labelled and run once (M4) to check we didn't tune to the benchmark.
    holdout, hold_have = [], Counter()
    hold_want = {mb: max(2, round(hold_target * n / len(uniq))) for mb, n in mb_total.items()}
    for t in sorted(r.threads, key=lambda t: _h("holdout:" + t.thread_id)):
        if size(holdout) >= hold_target:
            break
        mb = uniq[t.keys[0]].mailbox
        if t.thread_id in picked or len(t.keys) > 4 or hold_have[mb] >= hold_want[mb]:
            continue
        holdout.append(t.thread_id)
        hold_have[mb] += len(t.keys)
    return {
        "version": SLICE_VERSION,
        "rule": "whole threads; hard cases, then negatives, then ordinary threads in sha256(thread_id) order",
        "messages": len(keys),
        "threads": [{"thread_id": t, "reason": picked[t], "keys": by_tid[t].keys, "benchmark": t in bench}
                    for t in threads],
        "keys": keys,
        "benchmark_keys": [k for t in bench for k in by_tid[t].keys],
        "blind_keys": [k for t in threads if t in blind for k in by_tid[t].keys],
        "holdout_threads": holdout,
        "holdout_keys": [k for t in holdout for k in by_tid[t].keys],
    }


def main() -> int:
    s = config.load()["devslice"]
    sl = select(ingest.build(), s["target_messages"], s["benchmark_messages"], s["holdout_messages"])
    OUT.write_text(json.dumps(sl, indent=1) + "\n", encoding="utf-8")
    reasons = Counter(t["reason"] for t in sl["threads"])
    print(f"devslice: {sl['messages']} messages in {len(sl['threads'])} threads; "
          f"benchmark {len(sl['benchmark_keys'])} messages (blind {len(sl['blind_keys'])}); "
          f"holdout {len(sl['holdout_keys'])} messages outside the slice")
    for reason, n in sorted(reasons.items()):
        print(f"  {n:3d} x {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
