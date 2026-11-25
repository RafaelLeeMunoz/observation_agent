# M6: consolidated readiness report (five milestones, 24 Nov 2026)

Scope the user set on 24 Nov 2026: process-discovery quality baseline, financial estimate validation, dashboard and reporting readiness, repeatability, and a true cold
end-to-end 30-minute run, on the size-triggered splitting build (`docs/M5_SIZE_SPLIT.md`) with `synth.max_attempts` 4. Not committed. The working baseline
(`output/all`, `data/`, the dashboard on port 8765) is untouched; the cold run lives in a clean copy (`C:\Users\Xigua\GitHub\observation_agent_cold`, made by
`tools/make_cold_copy.py`, its dashboard on port 8766). Every acceptance criterion below was written by me before looking at the cold run's results; the numeric ones marked
provisional are proposals for your review. All judgments (is a sample observation right, is a cluster real work) are mine and are listed as such; the files to check them are named.
Results: `eval/results/m6_baseline/` (the working run), `eval/results/m6_cold/` (the cold run), `eval/results/m6_repeat/` (repeatability).

## Verdict

| # | Milestone | Status | One line |
|---|---|---|---|
| 1 | Process-discovery quality baseline | **PASS (replacement); working baseline FAILS 2 criteria** | Cold run: 41.3% of observations in processes (baseline 27.5%), every third of every mailbox covered, 19 missed-cluster candidates (9.8% of unassigned), no duplicate process pairs, precision sample 30 of 30 (my reading) |
| 2 | Financial estimate validation | **PASS** (4 hard checks, 2 provisional) | $1,385.79 per month recomputed to the last digit with a second implementation; the dollars rest on model-assumed minutes (0.44 to 1.92 times the base) |
| 3 | Dashboard and reporting readiness | **PASS with 2 gaps** | All 7 checks pass on both runs; 0 problems in the full audit (6,303 quotes, 2,191 pages); gaps: 1 artifact drafted of the 3 to 6 planned (M7), mobile table scrolls sideways |
| 4 | Repeatability | **PASS (provisional)** | Deterministic stages identical; extraction 97.8% message agreement; the same input grouped twice agrees on 93 to 99.6% of pairs; but the dollars of a mailbox vary from -25% to +12% between runs |
| 5 | True cold end-to-end 30-minute run | **FAIL on the one command; PASS on speed** | The pipeline took 20.2 minutes cold; `claude -p "/observe all"` did not finish it: the driver ended its turn at 20.5 minutes and killed the pipeline before the artifact and report stages |

**The critical finding is milestone 5's one-command failure**, not a correctness problem in any number. It needs a decision (see Improvements, item 1). Everything else can be adopted as it is.

## 5. Cold end-to-end run (the speed was measured; the one command failed)

**Acceptance criteria.** (a) `claude -p "/observe all"` in a clean copy finishes unattended, with no prompt, exit code 0, all stages; (b) the whole run under 30 minutes (README C2);
(c) ledger 3,240 of 3,240 with a status for each; (d) every validator passes; (e) report, dashboard, artifact and run logs exist; (f) the usage fits one 5-hour window.

| Criterion | Result |
|---|---|
| (a) one command finishes | **FAIL.** Started 11:16:52 UTC, `claude -p` ended 11:37:22 (1,230 s). The haiku driver ran the pipeline as the command's step 2; at 10 minutes the Bash tool moved it to the background, the driver ended its turn while the pipeline was still running (it was in the synthesis step), and the pipeline died with the session after the score stage, before the artifact-writer, the report and the dashboard. The driver also printed that the workspace was not trusted and 7 `permissions.allow` entries were ignored (the command's own `allowed-tools` still worked), and its attempt to resume asked for an approval that nobody could give |
| (b) under 30 minutes | **PASS for the pipeline: about 1,212 s = 20.2 minutes** (1,196 s to the score stage, then a 17 s resume for the artifact and report; the stage timings below). The one command did not complete, so the 30-minute target is met by the pipeline and **not demonstrated by the command** |
| (c) ledger | PASS: 3,240 of 3,240, 3,200 unique messages, 40 copies; 3200/3200 read |
| (d) validators | PASS: all 81 extractions and 81 groundings verified; 8 synthesis parts and the merge verified; 0 chain problems |
| (e) outputs | PASS after the resume (one artifact, report, dashboard started in 0.4 s) |
| (f) usage | PASS: the 5-hour window went from 4% to 62% across the run and the analysis that followed (this session's own turns included, so the run alone used at most 58 points, about 50); weekly 28% to 36%. 23.74M subagent tokens: extract 16.76M, ground 3.62M, synthesis 3.21M, merge 0.15M |

Stage timeline (24 workers, peak 24 sessions in flight, 167 model sessions, none lost): extraction 0 to 847 s (85 sessions, 4 failed attempts, longest 379 s), grounding 142 to 869 s, synthesis 870 to 1,135 s (11 sessions, 3 failed attempts, longest chain s011 at 266 s), merge 60 s (167 groups to 129 processes),
score 0.4 s, artifact 14 s, report about 2 s. The tail after the last grounding is 326 s (5.4 minutes). The runtime projection said 20.7 minutes mean at 24 workers; measured 20.2 (extract and ground 14.5 against 14.2 projected).
Synthesis starts only after all grounding is done, so the overlap the projection allowed for is not used (Improvements, item 6).

## 1. Process-discovery quality baseline

**Acceptance criteria** (`tools/quality_baseline.py`; HARD must pass, PROVISIONAL are proposals): C1 every counted observation re-reads byte-exact from the raw file, once, and exists (hard); C2 every third of every mailbox has at least 5% of its observations counted (hard);
C3 at least 25% of all observations counted (provisional); C4 unassigned observations that form hint clusters of 8 or more observations in 4 or more threads are at most 10% of the unassigned (provisional); C5 near-identical process pairs at most 5% of the processes (provisional);
C6 no process holds over 25% of a mailbox (provisional); C7 a seeded sample of 30 assigned observations fits at least 85% (provisional, read by a person).

| | Working baseline (whole mailboxes, extraction run 1) | Cold run (size split, extraction run 2) |
|---|---|---|
| Observations / processes / scored / events | 4,767 / 100 / 93 / 744 | 4,740 / 129 / 116 / 1,078 |
| C1 evidence | **pass** (1,312 quotes re-read) | **pass** (1,958) |
| C2 positional (thirds counted) | **FAIL**: farmer-d 59 / 0 / 0; giron-d 41 / 41 / 9.7 | **pass**: farmer-d 69 / 32 / 32; giron-d 50 / 52 / 33; lokay-m 41 / 51 / 55; williams-w3 20 / 26 / 39 |
| C3 counted share | pass 27.5% | pass 41.3% |
| C4 missed clusters | **FAIL**: 33 clusters, 625 observations, 18.1% of unassigned | pass (marginal): 19 clusters, 272 observations, 9.8% |
| C5 near-duplicate pairs | pass: 0 (1 hint cluster spread over 3 processes) | pass: 0 (1: Calpine nominations, 42 observations over 4 processes) |
| C6 over-merge | pass (largest process 61 observations) | pass (largest 99, 4% of farmer-d) |
| C7 precision sample | not re-read (88% in the earlier farmer-d reading) | **pass: 30 of 30** (4 borderline: #13, #16, #17, #27; worst case 26 of 30 = 87%) |
| Structure | 100 processes of 5+ or 2-4 observations: 88 / 12; 6 cross-mailbox; 62 with a stall signal | 129: 117 / 12; 6 cross-mailbox; 79 with a published stall signal |

**Limitations.** C4 to C7 are my proposals and C4's clusters are built from the extractor's own hint text (independent of the synthesizer, but crude). The missed clusters in the cold run are real candidates, not noise: daily P&L and prudency reporting (32 observations, 19 threads), system access requests (30 / 13), meter flow with no deal (24 / 11), real-time desk equipment setup (18 / 5), Sitara book setup (17 / 11), PG&E Texas volume confirmation (11 / 6).
The precision sample is read by me, not independent: please check `eval/results/m6_cold/precision_sample_cold_run.md` (30 lines, each with its original file name). The cold run's counted share varies by mailbox (williams-w3 28%, its first chunk 17.6%, just above the 15% floor).
Fragmentation: the only visible split is the Calpine/customer nomination family; in the giron-d test there were 2 probable and 2 borderline missed joins between chunks (`docs/M5_SIZE_SPLIT.md`).

## 2. Financial estimate validation

**Acceptance criteria** (`tools/finance_validation.py`, a second implementation of the arithmetic): F1 months observed, events per month, hours, dollars, the allocation and the running total match the published claims to 1e-18 (hard); F2 an independent recount of distinct (process, thread) pairs equals the events (hard); F3 every scored opportunity has at least 2 events, 3 emails and 2 threads (hard);
F4 the report's total and every row equal the data (hard); F5 overlap deduction at most 10% and no near-duplicate scored pair (provisional); F6 low-confidence dollars at most 15%, the largest opportunity at most 15%, the top five at most 50% (provisional).

| | Working baseline | Cold run |
|---|---|---|
| F1 / F2 / F3 / F4 | pass x4 (93 opportunities; 744 pairs = 744 events; 93 report rows) | pass x4 (116; 1,078 = 1,078; 116 rows) |
| Running total | $1,152.72 per month | **$1,385.79 per month** |
| Total at low / high minutes | $544.80 / $2,122.29 | $607.01 / $2,662.21 |
| Low-confidence share, largest, top five | 1.3%, 10.0%, 26.0% | 2.2%, 5.5%, 21.3% |
| Minutes per event (base): min, median, max | 2, 15, 45 | 3, 15, 45 |

**Findings and limits.** (1) The dollars are labelled estimates and they rest on the model's minutes-per-event assumption: the range is 0.44 to 1.92 times the base. The counted part (events, months) is exact and verified. (2) **The overlap rule is vacuous.** Events are keyed per process, so an event can never be 'already counted by a higher-ranked opportunity': the overlap deduction is $0.00 in both runs.
Threads that two scored processes both count: 19 threads (19 events) in the baseline and 17 threads (18 events) in the cold run; the upper bound of what that double counts is $18.39 (1.6%) and $20.73 (1.5%) of the total. Small, not a correctness failure of the arithmetic, but the documented rule does not do what its description says. (3) No near-duplicate scored pair by name; semantic duplicates (the Calpine nominations family) are not detected by names. (4) **The dollars change between runs** (milestone 4): per mailbox -25% to +12%.
(5) Adoption: the new total is +20% over the baseline for reasons that are measured (the baseline did not look at two thirds of farmer-d, `docs/M5_SPLIT_EXPERIMENT.md`), plus run-to-run variation; the single figure to quote with an honest range is $1,386 per month, low-high $607 to $2,662 on the minutes assumption alone.

## 3. Dashboard and reporting readiness

**Acceptance criteria** (`tools/dashboard_readiness.py` plus the existing `tools/audit_dashboard.py` and a visual check): D1 home shows the running total and every scored opportunity in rank order, linked (hard); D2 from an opportunity its dollar claim, formula inputs, events and quotes are reachable by links and each quote opens a message page that highlights it (sampled: top 10 and 20 random) (hard);
D3 original corpus names, including a trailing dot, are shown as written (hard); D4 the coverage page states every count (hard); D5 the report has the ranked table the README asks for, labels everything estimated or assumed, and states the method (hard); D6 median page at most 100 ms, slowest at most 2 s, data load at most 15 s (provisional); D7 message text is escaped (hard); the audit finds 0 problems.

Result: **D1 to D7 pass on both runs.** Median page 55 ms (baseline) and 48 ms (cold), slowest 85 and 79 ms, data file 10.2 and 10.6 MB loading in 0.05 s. The full audit: 0 problems; baseline 6,367 quotes re-read byte-exact, 772 claims, 1,621 pages, 1,806 quote links opened; cold 6,303 quotes, 979 claims, 2,191 pages, 2,683 quote links.
Visual check in the browser pane on the cold dashboard: the home page is clear (total $1,385.79, 116 opportunities of 129 processes, 3200/3200 messages), an opportunity page labels every number as counted, assumption or estimated with its formula, a message page shows the quote highlighted at its raw offsets, and the mobile layout works (the main table scrolls sideways).
**Gaps.** (a) **Only one artifact** was drafted (`P29-template.md`): the threshold in `config/settings.yaml` is $1,000 per month, the largest opportunity is $76.52, so nothing qualifies and the `at_least_one` rule produced the single artifact; the plan's M7 (3 to 6 artifacts, README R5) is not done and the threshold needs your decision. (b) The report lists method and assumptions but no separate limitations section. (c) The dashboard has not been checked in other browsers.

## 4. Repeatability

**Acceptance criteria** (`tools/repeatability.py`, `tools/synth_rerun.py`; provisional except R1): R1 the code stages are byte-identical (hard); R2 message-level agreement on 'has a verified observation' at least 90%; R3 thread-level Jaccard at least 0.80; R4 observation-level Jaccard (quote spans, IoU 0.5) at least 0.60;
R5 per mailbox, thread Jaccard of threads counted toward a process at least 0.60 and events within 25%; R6 at least 70% of the events in processes with a counterpart in the other run (thread Jaccard 0.30).

- **R1 pass.** Ledger, batch manifest and ingest rows identical between the working run and the cold copy.
- **Extraction (R2 to R4 pass).** 3,200 messages read twice (4,767 against 4,740 observations): message agreement 97.8%, thread Jaccard 0.969 (31 threads found only by the first run and 30 only by the second, of about 1,930), observation Jaccard 0.632 (the weakest and closest to its line; quote spans differ). By mailbox: farmer-d 0.988, lokay-m 0.976, williams-w3 0.975, giron-d 0.927 (17 and 19 threads).
- **Synthesis, across the two runs (R5, R6 pass).** Events farmer-d 560 to 554 (-1%), giron-d 280 to 246 (-12%), lokay-m 86 to 75 (-13%), williams-w3 223 to 203 (-9%); thread Jaccard 0.77, 0.69, 0.79, 0.61 (williams-w3 at the line); processes with a counterpart 47 of 71, 30 of 43, 14 of 17, 22 of 35, holding 75 to 97% of the events. **Dollars per mailbox: +12%, -24%, -25%, -3%.**
  The end-to-end working baseline against the cold run fails R5 and R6 (thread Jaccard 0.51) as it must: it compares two architectures and is not a repeatability measure.
- **Synthesis alone, the same input twice** (3 parts re-run in a clean cache, 575k tokens, about 6 meter points, each verified on its first attempt): the observations that both runs counted are grouped almost identically (pairwise agreement F1 0.996, 0.934, 0.959), but **which observations count differs** (counted-set Jaccard 0.752, 0.844, 0.677); processes 15 to 15, 22 to 24, 17 to 17, with 14, 20 and 13 matched.
  So the model's grouping is stable; its decision on what is recurring work moves by 15 to 32% of the observations, and the minutes it assumes move the dollars.
- **Missed threads between runs:** 1.6% of the threads with observations differ per run at extraction, 12 to 38% of the counted threads differ per mailbox at synthesis.

**Limitations.** One comparison of two extraction runs and three synthesis reruns: a small sample, so every threshold is provisional. Nothing here measures drift over time or across prompt versions.

## Particular attention (your list)

- **Process fragmentation:** low. No near-duplicate process names in either run; one family (Calpine nominations) spread over four processes; 2 probable and 2 borderline missed joins in the giron-d chunk test. Merges that were made were correct (farmer-d 17 of 18 on my reading, giron-d 3 of 3).
- **Missed threads between runs:** 1.6% of threads at extraction; 12 to 38% of counted threads per mailbox at synthesis (above). The mailbox that flips architecture (williams-w3 has 999 observations in one extraction and 1,022 in the other, against the trigger at 1,000) changed from whole to two chunks between the runs: a fragile threshold (Improvements, item 7).
- **Duplicate financial opportunities:** none by name; up to $20 (1.5%) of thread-level double counting because the overlap rule is vacuous; semantic duplicates possible within the nominations family.
- **Repeated structural validation failures:** the same three slips cause nearly every failed synthesis attempt: an observation placed in two processes, a basis observation that is not in its process, an unknown observation id; plus the 15% floor twice in earlier runs, never the positional check. First-attempt failures: 9 of 9 in the earlier experiments, **3 of 8 in the cold run**, 0 of 3 in the reruns; every second attempt verified.
  **Fourth attempts: none needed** in the cold run (8 parts, 11 attempts) or the reruns; a third attempt was needed twice before (two chunk parts in the experiments). The merge never failed (5 of 5 first attempts). `tools/attempt_report.py` tabulates every attempt with its reason, seconds and tokens (`eval/results/m6_cold/attempts_cold_run.json`, `eval/results/m6_baseline/attempts_working_run.json`).
  Reading: 3 attempts would have sufficed in all 8 cold parts; 4 stays as cheap insurance until another cold run confirms it.

## Improvements, ranked by business value (nothing below was built)

| # | Opportunity | Benefit | Cost | Recommendation |
|---|---|---|---|---|
| 1 | **Make the one command finish a 20-minute run.** A time-sliced `src.pipeline run`: stop scheduling at about 8.5 minutes, drain the sessions in flight, exit with a resumable code; the command already says 'run it again, at most 4 times'. Or raise the Bash timeout with an `env` setting (does not apply in an untrusted clone) | Without it the headline requirement (one command, unattended) fails in a fresh clone; the pipeline itself is fast enough | About 100 lines, tests and a line in `observe.md`; no model usage | **Do first.** It is the only critical item; needs your approval (pipeline exit codes and the command text) |
| 2 | Document and test the untrusted fresh clone (M10): project permissions are ignored until the workspace is trusted; the command's own `allowed-tools` kept working | A grader cloning the repo hits it | A README paragraph; a fresh-clone test | Do with item 1 |
| 3 | Artifacts: choose a threshold that yields 3 to 6 artifacts (about $30 per month gives about 8 candidates) | Meets R5 and M7 | About 1 to 2 meter points per artifact | Needs your threshold decision |
| 4 | Dollar stability: the minutes per event move the per-mailbox dollars by up to 25% between runs | Credibility of the headline number | Design choice (fixed rubric, or the median of two runs, or lead with the range) | Lead with the range now; decide after you review |
| 5 | A residual pass over the unassigned clusters (272 observations in 19 clusters) | Recall: perhaps 5 to 10% more events | A new synthesis stage, about 3 meter points | Later optimisation phase |
| 6 | Start each mailbox's synthesis as soon as its last batch is grounded (mailbox-ordered batching) | About 3 to 4 minutes of the 20 | Batch ordering change | Only if 20 minutes proves too tight |
| 7 | Move the size trigger away from the mailbox sizes (williams-w3 sits at 999 to 1,022) or add hysteresis | Stops a mailbox changing architecture between runs | One config value | Cheap; do with item 4 |
| 8 | Make the overlap rule thread-aware | Removes up to 1.5% double counting and makes the documented rule true | Small code and tests | Low value; later |
| 9 | Report: a limitations section; mobile table | Client polish | Small | Later |

## Provisional and not verified

All numeric criteria except the hard ones; the 30-of-30 precision reading (mine); the cold run is one run (n = 1) and was completed by a resume, so a clean one-command run is not demonstrated until item 1 is fixed; repeatability rests on 1 extraction pair and 3 synthesis reruns; dollars are estimates by design.

## Usage record

Cold run about 50 points of the 5-hour window (at most 58 with this session's turns), 23.74M subagent tokens; the three synthesis reruns about 6 points; weekly meter 28% to 36% by the end of the cold run. `ANTHROPIC_API_KEY` unset, extra usage disabled. Analytical prompts, schemas, labels and holdout unchanged; the holdout was not run.
