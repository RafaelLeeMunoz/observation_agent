# M5: farmer-d three-chunk date-split experiment (result and recommendation)

Run 24 Nov 2026 (04:10-04:17 UTC), approved by the user. Isolated: chunks s201-s203 and the merge m201 have their own inputs, staging, caches, result files and run log
(`eval/results/m5_split_experiment/`); the working baseline (`s001`, `synth.json`, `output/all`, the dashboard) was not touched. Analytical prompts, schemas,
labels and holdout unchanged. Not committed. Scripts: `tools/split_experiment.py`, `tools/split_experiment_report.py`. Judgments below are mine, read from the printed lists.

## Headline

The whole-mailbox baseline was **positionally blind**: of its 482 assigned observations, 482 come from the first date third of the input and **0 from the other two thirds**
(assigned per tenth of the input: 51, 64, 63, 20 percent, then 0 for the last six tenths). The 15 percent floor passed it because the first third alone gave 19.8 percent of the whole.
The three-chunk split covers every part of the timeline (61, 28 and 42 percent of the three chunks) and lifts farmer-d from 221 to 560 events.
My earlier estimate (a gap worth about 4 to 10 percent of the total) was wrong: it assumed the missing observations were scattered, when two thirds of the timeline had not been read at all.

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Distinct, evidence-supported events up 10 percent or more against 221 | **Met: 221 to 560 (+153%)** by `events.build`; sum of process threads 221 to 564; events in scored processes 221 to 534 (+142%); processes 31 to 71 (scored 31 to 58); assigned observations 482 to 1,064 (+121%); threads covered 211 to 547. Scored alone on farmer-d: **$214.11 to $452.98 per month (+112%)**, so the running total would rise by about $240 per month (to about $1,390, +21%; an estimate, cross-mailbox overlaps not modelled) |
| 2 | At least 50 percent of recurring processes spanning chunks rejoined; denominator, correct, missed, incorrect, duplicates | **Met on my reading: 17 of 18 (94 percent).** An independent reference does not exist: the baseline's processes lie entirely in chunk 1 (denominator 0 by that definition), so the denominator is my judgment. The merger joined 41 of the 95 chunk groups (38 + 29 + 28) into 17 processes; all 17 read as same-theme joins (two borderline: the stranger's-gas family and the counterparty nomination intake); **missed joins: 1** (c1 'Unpathed deal assignment and pathing cleanup' vs c3 'Pipeline deal pathing for invoice volumes'); **incorrect joins: 0 clear, 2 borderline**; **duplicates: 0 clear** (four baseline families came out as two specific sub-processes inside chunk 1, which are different sub-processes; 16 of 543 threads, 3 percent, sit in two processes). For comparison the merger joined 14 percent (4 of 29) on the earlier two-chunk split of the healthy 1,178-observation subset |
| 3 | Added events and processes are discoveries, not duplicates or fragments | **Met on a sample.** 25 processes continue a baseline process (160 events), 39 are mostly new observations (184 events), 7 are mixed (216 events); 355 of the 547 threads covered are new. The largest 'new' ones are genuine: counterparty price discrepancy resolution 26 events (the invoice-discrepancy family the whole run missed), performance review and PRC preparation 9, weekend on-call changes 8, imbalance payback resolution 7, meter set-up and assignment 6, feedback error and variance clearing 6, pipeline supply cut reporting 5. Others are the same families as the baseline's, now seen over the whole timeline (strangers' gas volume 32 events). Precision spot check, 30 of the 642 newly assigned observations: about 27 fit their process, 3 are weak (about 90 percent, like the baseline's 88) |
| 4 | Every validator, the 15 percent floor and the 25 percent catch-all guard pass | **Met.** All three chunks and the merge verified; chunk coverage 61.4, 27.7 and 41.8 percent (floors 128, 127 and 110); no catch-all. Four of eight sessions failed an attempt and were retried with the standing requirements (chunk 1 needed 3 attempts, chunks 2 and 3 needed 2; the merge passed first time); 3 non-fatal `numbers_lint` flags on one process |
| 5 | Compare discovery, coverage, runtime, tokens, financial estimate | Below |

## Comparison with the baseline

| | Whole mailbox (baseline) | Three-chunk split + merge |
|---|---|---|
| Counted observations | 482 (19.8%), all in the first third | 1,064 (43.7%), spread over the timeline |
| Events (`events.build`) | 221 | 560 |
| Processes (scored) | 31 (31) | 71 (58) |
| farmer-d dollars per month (standalone scoring) | $214.11 | $452.98 |
| Wall time of the synthesis step | 149.6 s for the one attempt that passed after 7 that failed (2.5 to 7.5 min expected) | 401 s: chunks 361 s (the slowest chunk three attempts: 104.5, 109.9, 146.8 s), merge 40 s |
| Subagent tokens | 315,980 for one attempt | 2,882,214 for 8 sessions (output 116,572); 9 times one baseline attempt and 3.5 times my estimate |
| Meter | about 3 points per attempt | about 16 points (43% to 62% including about 3 for this session's turns) |

## Whether cross-chunk joining is the weakness

No. It performed well (94 percent on my reading, 17 joins), so the code-built hint index is **not justified**. The weak points are the cost (tokens and meter) and the retries.

## Recommendation: adopt size-triggered splitting (needs your approval; nothing is changed)

1. **Trigger and size.** A part above about 1,000 observations is cut into date chunks of whole threads of at most about 850 observations, synthesized in parallel, joined by the existing merger. The evidence for the limit: williams-w3 (999 observations) is uniform over its input, but giron-d (1,085) already assigns 41, 35, 44, 41, 41, 48, 26, **8, 15, 7** percent over its last tenths, and farmer-d at 2,433 goes blind after the first third. So the trigger would also cut giron-d into two chunks (a change to a mailbox whose result is currently in the dashboard).
2. **A positional completeness check** (cheap, independent of the split): a part fails an attempt if any third of its input has fewer than about 5 percent of its observations counted. The 15 percent floor alone cannot see this blindness.
3. **Cost to accept:** about 8 to 13 more meter points per cold run (about 75 to 95 of a 5-hour window in total) and about 4 minutes more synthesis wall; the assembled cold-run estimate (about 18 minutes) rises to about 21 to 22 minutes. **Corrected 24 Nov 2026 after the giron-d test (`docs/M5_SIZE_SPLIT.md`):** that compared the split with one optimistic baseline attempt. The working run's own synthesis log holds 5.78M tokens (eight farmer-d attempts), the split costs about 5.9M, so the extra is about 0 to 2 points and the synthesis chain is shorter, not longer (361 s against 688 s for farmer-d); the merge over about 190 groups adds 1 to 2 minutes.
4. Keep the current results as the provisional baseline until the change is approved, built, and its cold run measured. The 30-minute target is still not verified.

Future milestones, not executed here: process-discovery accuracy, financial validation, repeatability, dashboard readiness, the 30-minute cold end-to-end benchmark.
