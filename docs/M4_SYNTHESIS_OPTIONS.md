# M4: size-based synthesis options for the farmer-d part (comparison, no implementation)

Status: **analysis only**, 24 Nov 2026. Nothing was implemented, no prompt, schema, label or holdout changed, and the working pipeline
and dashboard (100 processes, 744 events, $1,152.72 per month, audit with 0 problems) are untouched. Everything below comes from code
run over artifacts that already exist (`data/runs/all/`, `eval/results/m4_concurrency/`, the run logs); no model was called for
this document. Figures marked *estimate* are extrapolations from five measured parts and are labelled as such.

## 1. What was measured

| Part (one synthesis session each) | Observations | Input | Assigned and counted | Attempts (seconds) | Output tokens of the kept result |
|---|---|---|---|---|---|
| lokay-m | 250 | 68 KB | 158 = 63.2% | 49.8 failed, 51.9 verified | 7.5k |
| williams-w3 | 999 | 275 KB | 342 = 34.2% | 145.8 failed, 122.5 verified | 17.9k |
| giron-d | 1,085 | 299 KB | 332 = 30.6% | 125.7 failed, 128.8 verified | 19.2k |
| farmer-d subset (the earlier comparison) | 1,178 | about 340 KB | 345 = 29.3% | 105.4 verified | n/a |
| **farmer-d full (baseline)** | **2,433** | **683 KB** | **482 = 19.8%** | 7 of 8 attempts over two runs failed or collapsed (2.8%, 3.4%, 6.5% assigned, a duplicate id, three invalid); the one seeded attempt verified in 149.6 s | 22.8k |

The assigned share falls steadily with part size (63% at 250, 34% at 999, 29% at 1,178, 20% at 2,433). A part of about 830 observations is
interpolated at about 38%, of about 610 at about 44%, of about 1,216 at about 29% (*estimates*, log-linear between the measured points).

**What the missing observations are worth is a question about events, not observations.** The dollars are built on events (one per process per thread),
and the named missed clusters are bursts inside very few threads:

| Missed cluster (hint) | Observations | Threads | When | In the earlier subset run |
|---|---|---|---|---|
| HPL/TETCO discrepancy resolution | 36 | 3 | Dec 2000 only | not in its input |
| meter allocation and feedback error clearing | 26 | 2 | Aug-Sep 2000 | in its input, 0 assigned |
| invoice discrepancy check against deal records | 19 | 2 | Mar 2001 only | in its input, 2 assigned |
| Duke Energy 1999 meter allocation issues | 15 | 2 | May-Jun 2000 | not in its input |
| path manager rewrite review | 15 | 2 | Oct 2001 only | in its input, 0 assigned |

These are two- and three-thread bursts, so even if they were perfect processes they would add 2-3 events each, and they are probably projects
more than recurring processes. A cluster survives a date split intact: with 4 date chunks each of the five sits wholly in one chunk, with 3 chunks four of
five do (HPL/TETCO splits 14 and 22). The earlier subset run's 'counterparty invoice and price discrepancy check' (22 observations) is a different set
of observations from the cluster above and now has none placed.

The events view of the baseline (code, by hint match; an exact-hint match under-counts label variants, so these are floors):

| Part | Events now | Extra events hidden in unassigned observations that match an existing process | Unmatched hint clusters spanning 3+ threads |
|---|---|---|---|
| farmer-d (2,433) | 221 | 28 (+13%) | 15 clusters, 67 threads (an upper bound for new events; several are label variants of existing processes: 'unattached meter volume resolution', 'unallocated volume resolution', 'desk coverage during absence', 'opm hours survey') |
| giron-d (1,085) | 216 | 8 (+4%) | 0 |
| williams-w3 (999) | 224 | 8 (+4%) | 1 (3 threads) |

So farmer-d is short by roughly 13% of its events plus a bounded share of the 67; the parts of about 1,000 observations are short by about 4%. farmer-d
carries about 30% of the 744 events, so closing the gap is worth roughly +4% to +10% on the running total (*estimate*: about $45-120 per month on $1,153), not more.

## 2. The three options

**Option 1, baseline: whole-mailbox synthesis (current, with the completeness floor and the standing retry requirements).**
- Completeness: 19.8% counted; events 13% short inside existing processes, up to about 43% short counting every unmatched multi-thread cluster.
- Evidence quality: unchanged (byte-exact quotes, ids validated, no duplicates, basis ids inside the process, floor, catch-all guard); about 88% of 25 sampled assigned observations fit their process.
- Runtime: 150 s per attempt; one to three attempts, so 2.5 to 7.5 minutes of the synthesis stage; reliability is the weak point (7 of 8 earlier attempts failed; 1 of 1 seeded attempt passed).
- Tokens and usage: about 316k subagent tokens and 22.8k output tokens per attempt, about 3 meter points per attempt, about 5-8 points expected.
- Complexity: none. Risk of losing recurring processes across segments: none, but a part this size sits at the edge of what one session handles.

**Option 2, size-triggered date split with overlapping context.** Only a part above a size limit (about 1,300 observations) is cut into 3 date chunks of whole threads
(measured splits: 850 / 849 / 734 observations; for 2: 1,277 / 1,156; for 4: 643 / 631 / 644 / 515), synthesized in parallel, then joined by the existing merger.
'Overlapping context' means each chunk also sees what the neighbouring chunks found: (a) the previous chunk's process names (sequential, slower), or (b) a code-built
index of the hint clusters that span chunks (parallel, deterministic).
- Completeness (*estimate*): about 33-40% counted (838-970 observations, +70% to +100%); events short by about 4-8%, like giron-d and williams-w3.
- Risk of losing recurring processes across segments: lower than it looks, because only 2-4 of the 50 hint clusters of 8+ observations are spread over 2 or more chunks with 4+ each, so the joining work is small. But the **measured** merger on the earlier date split joined only 4 of 29 processes (14%) and the split recovered 15 of 26 whole-run processes with 4 recurring processes lost; that was a healthy 1,178-observation part split in two, which is exactly where a split should not be used, and it shows that without context a recurring process comes out as duplicates per chunk.
- Evidence quality: same validators per chunk, same merge validation; context-only lines must never be assignable (an input change).
- Runtime (*estimate*): chunks about 100-130 s in parallel, each likely 2 attempts (every healthy part needed 2), plus a 25-35 s merge: 3.5 to 5 minutes, about the same as the baseline's expected value and a smaller worst case.
- Tokens and usage (*estimate*): about 750-900k subagent tokens with retries (+25% to +40%), about 8-10 points.
- Complexity: moderate. The date split and the merger already exist; the size trigger is small; the context (a) or (b) needs an input change and probably a prompt and schema change, that is a new versioned synthesis prompt that needs approval and a re-run of its tests, about 200-400 lines.

**Option 3, hierarchical synthesis with a global reconciliation stage.** Smaller chunks (4 date chunks of about 610 observations), the merger as the reconciliation, then a third pass that
places leftover observations into the merged processes (code proposes by hint, a model verifies).
- Completeness (*estimate*): about 44% from the chunks plus 5-10 points from the leftover pass, so 45-55%; events short by about 2-4%.
- Risk of losing recurring processes across segments: lowest on completeness, highest on precision: the reconciliation and the leftover pass can join distinct processes or place an observation in the wrong process, and only structure and ids are checkable by code, not relevance.
- Evidence quality: same validators plus the new stage's; relevance of a placement is a model judgment (about 12% of the sampled placements already fall short today).
- Runtime (*estimate*): chunks about 100 s x 2 attempts, merge 40 s, leftover pass 90-180 s in sequence: 5 to 8 minutes, +2 to +4 minutes over the baseline.
- Tokens and usage (*estimate*): about 1.3-1.6M tokens, about 14-18 points per cold run.
- Complexity: high: a new stage, new prompt and schema, validator, cache keys, tests and mutants, about 600-1,000 lines.

## 3. Side by side

| | Option 1 baseline | Option 2 size-triggered split | Option 3 hierarchical |
|---|---|---|---|
| Counted observations | 482 (19.8%, measured) | 840-970 (33-40%, estimate) | 1,100-1,340 (45-55%, estimate) |
| Events still missing (farmer-d) | 13% to 43% | 4-8% | 2-4% |
| Evidence quality | validators as now | validators as now | validators plus a model-judged placement |
| Synthesis stage wall | 2.5-7.5 min | 3.5-5 min | 5-8 min |
| Usage per cold run (this mailbox) | 5-8 points | 8-10 points | 14-18 points |
| Recurring processes lost across segments | none | small, depends on the merge join rate (measured 14% without context) | smallest, but false joins possible |
| New code and prompt work | none | moderate; a prompt change for the context | high; new stage |

## 4. The three `numbers_lint` flags

The farmer-d result carries three non-fatal flags, all on process P9, 'Unallocated volume (UA4) resolution': its name, its trigger ('UA4 or unallocated volume at a meter') and its
automation idea ('a research checklist and summary template for UA4 meters'). The lint (`src/synth.py`) only looks for digits in the model's text fields (name, description,
trigger, output, automation idea) and flags them because prose figures are not computed by code. Here the digits are the identifier UA4, a meter-volume category, not a figure.
**They do not affect the financial estimates.** The dollars are computed by code in Decimal from events (counted by code), the months observed (from dates), the hourly rate, and one
model-supplied number per process, the minutes per event, which has its own validation (whole numbers from 1 to 480, low <= base <= high, a stated reason, basis observations that belong
to the process) and is shown as an assumption with its range. The lint does not read those fields. A reviewer should still glance at P9's text; nothing else needs doing.

## 5. Recommendation

The expected accuracy gain is real but bounded: a size effect is visible in the coverage curve and in the events (farmer-d is about 13-43% short against about 4% for the parts of
about 1,000 observations), but the missed clusters the user named are mostly two- and three-thread bursts, and the whole gap is worth roughly 4-10% of the running total.
That does not justify a new architecture (Option 3).

It does justify one bounded experiment, which is a small variant of what already exists:
- **Option 2 in its minimal form** after approval: a size trigger on the existing date split (parts above about 1,300 observations only, 3 chunks of whole threads), the existing merger, no overlapping context at first, and the existing floor, standing requirements and three-attempt limit. Run it once on farmer-d (about 8-10 points) and compare it with the baseline on the same data.
- **Pre-registered acceptance:** keep it only if, against the baseline, farmer-d's counted events rise by at least 10% (about +22), at least 50% of recurring chunk processes are joined by the merger (the measured baseline for a context-free merge is 14%), no catch-all or floor failure occurs, and every validator passes. If the join rate is the weak point, add the code-built hint index (context option b) as the second step, with a versioned prompt change that needs approval.
- Until then the current farmer-d synthesis stays the provisional baseline, flagged as 19.8% counted.

Not done here, and needing approval before anything is built: the size trigger itself, any context input, any prompt or schema version, and any re-run.
