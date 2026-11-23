# Observation report (dev)

> **Pipeline test on a development slice, not a finding.** These outputs test the pipeline on a ~200-message development slice of the corpus. Its counts, rates, hours and dollars are NOT validated findings about the company: rates are divided by each mailbox's whole date window while the slice holds only a small share of its messages, and no reviewer has checked the claims. The quotes and citations are real and verified against the raw files.

Running total (overlap-adjusted): **$29.25** per month (estimated: events observed x minutes assumed x $85/hour).

| Rank | Process | Events | Emails | Threads | Months observed | Minutes/event (assumed) | $/month | Confidence |
|---|---|---|---|---|---|---|---|---|
| 1 | Unallocated or unmatched meter volume resolution | 6 | 11 | 5 | 24.06 months | 30 min (range 15-50) | $10.60 | high |
| 2 | Extending an expired deal to cover flowing gas | 4 | 7 | 3 | 24.06 months | 30 min (range 15-45) | $7.07 | medium |
| 3 | Monthly transport usage ticket entry | 2 | 4 | 2 | 24.06 months | 25 min (range 10-40) | $2.94 | low |
| 4 | Sitara deal price correction | 2 | 6 | 2 | 25.84 months | 20 min (range 10-30) | $2.19 | low |
| 5 | Calpine daily gas nomination file | 4 | 4 | 4 | 25.86 months | 10 min (range 5-15) | $2.19 | medium |
| 6 | Booking correction for Canadian and cross-counterparty deals | 2 | 4 | 2 | 20.00 months | 15 min (range 5-25) | $2.13 | low |
| 7 | Gas price quote for a customer | 2 | 4 | 2 | 20.00 months | 15 min (range 5-25) | $2.13 | low |

## Observed, counted, assumed

- **Observed:** each event rests on verified quotes (exact raw bytes, original file names).
- **Counted by code:** events (one per process per thread, quoted copies once), months observed (union of mailbox windows), events per month.
- **Assumed:** minutes saved per event (proposed by the synthesizer with a reason; low-high range shown) and the $85/hour rate.
- **Estimated:** hours and dollars = counted x assumed.

Coverage: 202/202 slice messages read; dispositions {'ambiguous': 2, 'business_no_process_signal': 8, 'not_business': 46, 'observations': 146}.

- Artifact for P4: `P4-checklist.md` (checklist), 23 citations verified.
