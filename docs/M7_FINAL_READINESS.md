# M7: final readiness review after the cold retest (24 Nov 2026)

The build under test is unchanged since the clean copy was made (a file-by-file comparison showed the copy equal to the working tree before the launch; the only file added later is the launcher script). Nothing is committed and the working baseline (`output/all`, `data/`) is not replaced.
Results: `eval/results/m7_cold2/` (this retest), `eval/results/m6_cold/` and `m6_baseline/` (the first cold candidate and the working run), `eval/results/m6_repeat/` (repeatability). Judgments (precision sample, which near-duplicates are real) are mine, with the files named so you can check them.

> **25 Nov 2026: the official benchmark has since run and passed every mandatory gate in one invocation (25.4 minutes). See `docs/M8_BENCHMARK_REPORT.md`; it supersedes the "not run" status below.**

## Update after your approval of the narrow fence correction (24 Nov 2026, 18:00 UTC)

**Status.** The correction is implemented and tested (796 tests, no model calls; 12 new mutants, all caught). **The official cold benchmark was not run: the 5-hour window stood at 64% and a cold run costs about 48 points (it resets at 21:50 UTC); you asked me to stop in that case.**
The tree is benchmark-ready (below). V1 is not frozen, the working baseline and every earlier artifact are untouched, nothing is committed. Analytical prompts, schemas, holdout, worker count, batch size, financial assumptions, the splitting threshold and the synthesis logic are unchanged.

### 1. Files changed and why

| File | Change | Reason |
|---|---|---|
| `tools/fence_hook.py` | `SCRATCH_ONCE`, `_SCRATCH_SUFFIX`, `_scratch_ok`, one branch in `denial_kind` | the narrow exception (section 2); nothing else in the fence changed |
| `src/pipeline.py` | extract and ground attempt records now carry `fence_self_checks` | audit: the denied scratch write is in the run log, not only in the session's fence folder |
| `tests/test_scratch_selfcheck.py` (new), `tests/test_runsafety.py` (+3) | positive, negative, security and end-to-end tests | prove the exception is as narrow as specified and that validation still gates the output |
| `tools/mutation_check.py` | +12 mutants (one per narrowing) | each narrowing must be caught by its test |
| `tools/make_cold_copy.py`, `tests/test_m6_tools.py` (+1), `tools/run_cold_retest.ps1` | `--verify` (the copy equals the tree and holds no run state; docs may change after) and an output-folder parameter | the benchmark must run on exactly the tested code and configuration |
| docs | this file, `DEVLOG`, `HANDOFF` | the requested record |

### 2. The exception, exactly, and its security implications

A denied `Write` is classified `self_check` (still denied, logged with its path, not a stop) only if **all** of these hold, else it stays an unexpected, critical denial:
1. the agent is the **extractor or the grounder** (never the synthesizer, merger, artifact-writer, an unknown agent or none) and the tool is **Write** (not Read, Edit, Bash, anything else);
2. the path is **the session's own assigned output path plus `.tmp-` and 1 to 12 lowercase letters**, in the very same folder (so not another folder or slice, another session's output, a subfolder, a longer or different suffix, a digit, dot, capital, `.tmp`, `.bak`, `.json`; path traversal is resolved first, so `../` cannot reach another file);
3. the session's own log holds an **allowed Write of its exact output file** and that file is on disk and not empty (a denied write, a missing file or an empty file does not count);
4. it is the **first** such write in the session (a second one is unexpected, so a repeat stops the run);
5. **every** record in the session's log can be read (an unreadable log, or no log, fails closed).

What it does not do: the write is denied (nothing is created: an end-to-end test checks the file does not exist); no path, tool or agent gains access; the fence's own decision is unchanged (`decide` still says deny); every other denial, including any other write the same session makes, stops the session and the run as before.
Python validates the legitimate output on its own exactly as for any session: a test gives a session a self-check and a garbage output, and the batch is rejected, not accepted. The denied write is audited in two places: the session's fence log (path, size of the attempted content, reason, kind) and the run log (`fence_self_checks` on the attempt).
**Residual risk:** a prompt injection in an email could ask the worker to write such a file; the effect is one denied, logged probe per session that no longer stops the run, and no data, file or access is gained. The injection would still need a second, different action to do anything, and that action stops the run.

### 3. Test and mutation results

- Complete suite: **796 passed** (745 before: 47 scratch tests with parametrization, 3 run-safety integration tests, 1 copy-verification test), no model call.
- The 12 new mutants (any agent, any tool, no output written first, a denied write counted as written, repeats allowed, unreadable log fails open, any suffix, a long suffix, another folder, another session's output, no audit in the run log, a bad output accepted after a self-check): **12 of 12 caught** by their intended tests.
- Full mutation run over all 268 mutants: 266 caught by their intended test at once; the other 2 were not applied only because the changed source text no longer matched their pattern (a log field added to the extract attempt record, and a line now shared by the read-back and scratch checks); they were re-pointed and are caught: **268 of 268**. None was missed.

### 4. The outstanding quality findings, stated precisely (nothing was fixed)

- **williams-w3 and the splitting threshold.** The mailbox has 999 (working run), 1,022 (cold 1) and 989 (cold 2) observations against a trigger at 1,000: within 2.3% of it, so it ran whole, split, whole. Events were 223 (whole), 203 (split), 144 (whole): the **29% fall between cold 1 and cold 2** is large, but whole against whole (working run against cold 2) is also -35%, so the architecture flip does **not** by itself explain it:
  the same mailbox, synthesized the same way from two different extractions, counted 34% of its observations once and 17.5% once. Moving the trigger to about 800 is a **hypothesis** to test in the next phase, not an established fix; the variation looks partly intrinsic to the model's judgment of what is recurring.
- **17.1% missed clusters against the 10% line.** The line was my own proposal, set before the data, with no external basis. The measure is a proxy: unassigned observations whose extractor hint text clusters (8 or more observations in 4 or more threads), as a share of the unassigned. It is built from the extractor's own wording, not adjudicated as real recurring work, and it is not a recall measurement (there is no labelled process set).
  Values: 18.1% (working run), 9.8% (cold 1), 17.1% (cold 2). It is a diagnostic of where recall may be lost, not a pass/fail correctness test.
- **13.2% between the two split runs ($1,385.79 against $1,202.88).** Two runs only. The difference mixes extraction variation (4,740 against 4,628 observations), the share counted as recurring (41.3% against 31.4%), the minutes the model assumed and williams-w3 changing architecture; I did not decompose it. It is far smaller than the stated range (the low and high minutes alone span -54% to +84% of the central estimate), so the two runs agree within the range, but **the central estimate is not a stable point estimate**.
- **Verified arithmetic is not valid assumptions.** F1 to F4 prove that every figure follows from the stored inputs by the documented formulas (counts, months, hours, dollars, allocation, report equals data). They say nothing about whether the minutes per event are right, whether an event is a true recurrence, whether $85 per hour is the right rate, whether the time would actually be saved by automation, or whether the mailboxes represent the company.
  Evidence integrity (quotes re-read byte-exact) shows a quote exists, not that it means what the process claims. The dollars are estimates by construction.
- **The 30-assignment precision sample.** 30 of about 1,450 counted observations (2%), one seeded draw per run, **read by me**, not blind, not double-labelled, no agreement measure. It judges whether an observation fits its process *name*, which is lenient, and it samples uniformly across observations, so large processes dominate and small ones are barely tested; it says nothing about recall or about over-merging inside a big process.
  Statistically, 30 of 30 only supports a true precision of at least about 90% (one-sided 95% bound, 0.05^(1/30)); counting the 2 borderline items as wrong gives 28 of 30. Please check `eval/results/m7_cold2/precision_sample_cold2.md` yourself.
- **The 40% cold-run failure estimate.** It is one event in about 341 worker sessions since the run instruction was added (0 in the 167 of cold 1, 1 in the 174 of the retest): 0.29% per session, so 1 - (1 - 0.0029)^175 = 40% for a run of 175 sessions.
  **The uncertainty is very large**: the exact 95% interval for 1 event in 341 is 0.007% to 1.6% per session, which is **1.3% to 94% per run**. Assumptions: sessions are independent and identical and the rate is constant. The one event happened on a *retry* after an invalid-JSON failure, so the rate may belong to retries (about 9 retries per cold run), not to sessions in general, which would change the estimate in either direction. Treat '40%' as 'a real, unquantified risk', not as a measurement.

### 5. Which criteria are mandatory V1 gates, and which are diagnostic

This follows the labels already attached ('hard' and 'provisional' when I defined them) and the README and plan requirements; I have not redefined or relaxed anything. **Promoting any provisional criterion to a gate, or changing one, is your decision.**

| Mandatory V1 release gates | Result on the retest |
|---|---|
| README R1 every message read, ledger 3,240 of 3,240 | pass |
| R2 processes with what, who, how often, where they stall; R3 ranked hours and dollars at $85; R4 every claim cites verbatim quotes that re-read exactly (quality C1, audit 0 problems); R5 artifacts with verified citations; R6 dashboard with drill-through (D1 to D5, D7) | pass |
| Constraints C1 no API key; C2 one unattended command under 30 minutes; C3 cache; C4 exact work in code; C5 tests without a model; C6 Python; C7 local | C2: **not met in one invocation** (the blocker; fixed in code, **pending the official benchmark**), otherwise pass |
| Hard criteria: quality C1 and C2 (every third of every mailbox covered); finance F1 to F4 (exact recomputation, recount, thresholds, report equals data); dashboard D1 to D5 and D7; repeatability R1 (deterministic stages identical) | pass |
| Retest criteria (a) one command finishes unattended, (b) under 30 minutes, (c) ledger, (d) validators, (e) outputs, (f) usage in one window | (a) pending the benchmark, (b) to (f) pass |

| Diagnostic or future optimization targets (provisional by definition) | Result on the retest |
|---|---|
| Quality C3 counted share at least 25%; C4 missed clusters at most 10%; C5 near-duplicate pairs at most 5%; C6 no process over 25% of a mailbox; C7 precision sample at least 85% | pass, **fail (17.1%)**, pass, pass, pass |
| Finance F5 overlap and duplicates; F6 concentration and assumption dependence | pass, pass |
| Dashboard D6 speed | pass |
| Repeatability R2 to R6 (extraction agreement, thread and observation Jaccard, per-mailbox synthesis lines) | pass, pass, pass (0.63, at its line), **fail for williams-w3**, pass |
| Attempt statistics (`max_attempts` 4) | no third or fourth attempt needed |

### 6. The official benchmark: ready, not run

To run it (needs a 5-hour window with at least about 50 points free, the computer awake and online for about 25 minutes, `ANTHROPIC_API_KEY` unset, port 8765 free):
`.venv\Scripts\python tools\make_cold_copy.py C:\Users\Xigua\GitHub\observation_agent_cold3`, then
`powershell -NoProfile -ExecutionPolicy Bypass -File tools\run_cold_retest.ps1 -Copy C:\Users\Xigua\GitHub\observation_agent_cold3 -OutName m8_final`.
The script refuses unless the copy equals the working tree (code, configuration, prompts, schemas, tests, corpus) and holds no run state; it runs exactly one `claude -p "/observe all"` and writes the start, end and wall seconds to `eval/results/m8_final/`. Nothing is retried or restarted by the tooling; if the run stops, the evidence is preserved and nothing further starts.
Pass lines: exit 0 with the full SUMMARY block, all stages including 5 artifacts and the dashboard, wall time under 30 minutes, every validator passing, 0 audit problems, no manual restart. Retries, nonfatal fence denials, stage timings, usage and tokens come from the run log with `tools/attempt_report.py` and the timing script.

### 7. Is the project a V1 release candidate?

**For an independent code audit: yes, now.** The code, tests and documentation are stable, every hard gate that does not need a full run passes, and the audit does not depend on the benchmark. **As a V1 release candidate (the thing that may be frozen): conditional on the official benchmark** passing in one invocation, because the one-command gate (C2) is the only gate not yet demonstrated on the final code.
I am not freezing anything. If the benchmark fails, the evidence is preserved and I will report the specific blocker only.

### 8. Recommended V1.1 backlog (not started)

1. Test the williams-w3 trigger hypothesis (about 800 observations) against the intrinsic variation: needs 2 to 3 more full runs, so plan it with the next window.
2. A residual pass over unassigned clusters (the 17% proxy), after adjudicating whether the clusters are real work.
3. Stabilise the minutes assumption (a fixed rubric or the median of two proposals) and report run-to-run variation alongside the range.
4. A labelled process set for a real recall and precision measurement (blind, two labellers).
5. Start each mailbox's synthesis as grounding completes (3 to 4 minutes of headroom).
6. A thread-aware overlap rule (up to about $15 to $20 per month, 1.3 to 1.6%); report polish (limitations section, 'files, unique, copies' coverage line, mobile table).
7. Reassess `max_attempts` 4 against 3 after more runs.

### 9. Files and documents for an independent multi-AI code audit

Read first: `CLAUDE.md` (the hard rules), `PLAN.md`, `docs/RUNNING.md`, this file, `docs/M6_READINESS.md`, `docs/M5_SIZE_SPLIT.md`, `docs/M5_SPLIT_EXPERIMENT.md`, `docs/M4_SYNTHESIS_OPTIONS.md`, `private/HANDOFF.md` and `private/DEVLOG.md` (working notes, not committed; give them to the auditors deliberately).
Code under audit: `src/` (all), `tools/fence_hook.py` and `tools/instructions_hook.py` (the security boundary), `src/headless.py`, `src/pipeline.py`, `src/runctl.py`, `src/verify.py`, `src/synth.py`, `src/score.py`, `src/act.py`, `src/serve.py`; `.claude/commands/observe.md`, `.claude/settings.json`, `.claude/agents/*.md`; `config/settings.yaml`; `schemas/`.
Evidence: `tests/` (796 tests, offline), `tools/mutation_check.py` (the mutants), `tools/audit_dashboard.py`, `tools/finance_validation.py`, `tools/quality_baseline.py`, `tools/dashboard_readiness.py`, `tools/repeatability.py`, `tools/attempt_report.py`; results in `eval/results/` (m6_baseline, m6_cold, m6_repeat, m7_cold2, m5_*), the frozen benchmark in `eval/benchmark/`; `corpus/` and `corpus/MANIFEST.csv` (never modified).
Suggested audit questions: can any path let a worker read or write outside its two files; can the scratch exception be widened by a crafted path or log; can a number reach the report without code computing it; does every quote re-read from raw bytes; is the one command safe in an untrusted clone.

## Verdict as of the retest (before the correction)

**The retest passes every criterion except one: the first invocation of the command was stopped by the fence, and the second finished the job.** The specific blocker is small and the correction is small (below). Recommendation: **do not freeze V1 yet; approve the narrow correction, then freeze.** Everything else is ready.

| Area | Status |
|---|---|
| One command, single invocation, no prompt | **FAIL (once):** stopped at 12.6 minutes by a critical fence denial; the same command, run again, resumed and finished |
| Performance (30 minutes) | **PASS:** 22.3 minutes wall clock from launch to the final report, 21.1 minutes of active run |
| Functional correctness | **PASS** (all stages, validators, 3,240 of 3,240 files accounted for, 5 artifacts, dashboard up) |
| Financial calculation correctness | **PASS** (recomputed with a second implementation to the last digit; report equals data) |
| Evidence integrity | **PASS** (1,452 counted quotes and 6,180 dashboard quotes re-read byte-exact from the raw files, 0 audit problems) |
| Repeatability | **PASS, provisional**, with one mailbox failing its line (williams-w3) |
| Estimated business savings | **$1,202.88 per month central estimate, range $550.71 to $2,212.01** (estimates, not measurements) |

## 1. What ran and how long it took

One command, `claude -p "/observe all"`, in a clean untrusted copy (nothing cached, `data\` and `output\` absent), 24 workers, launched at 16:50:55 UTC.

| Segment | UTC | Seconds | What happened |
|---|---|---|---|
| Launch to critical stop | 16:50:55 to 17:03:32 | 757 | ingest 10 s; 77 of 81 extractions and 74 of 81 groundings done; **stopped (exit 2): fence denial on batch b081** |
| Gap | 17:03:32 to 17:04:44 | 72 | my relaunch of the same command (in real use: the user running it again) |
| Same command again (resume) | 17:04:44 to 17:13:13 | 509.5 | remaining batches, synthesis, merge, scoring, 5 artifacts, report, dashboard; run record: finished, exit 0 |
| **Launch to final report** | 16:50:55 to 17:13:13 | **1,338 (22.3 min)** | the driver's report carried the full SUMMARY block (coverage, central estimate, range, top ten, artifacts) |
| Active run (without the gap) | | **1,266 (21.1 min)** | the runtime projection said 20.7 minutes mean at 24 workers |

Stage timeline from launch (the 72 s gap is inside the extraction line): extraction 8 to 1,028 s (90 sessions for 81 batches: 5 failed validation and were retried, 3 were stopped in flight at the denial, 1 was the denial), grounding 161 to 1,047 s (84 sessions, 3 stopped in flight),
synthesis 1,047 to 1,251 s (10 attempts for 7 parts: 3 first attempts failed, all second attempts verified), merge 1,251 to 1,308 s (1 attempt), scoring 1,309 s, report 1,326 s. Peak 24 sessions in flight. 23.85M subagent tokens (extraction 16.97M, grounding 3.51M, synthesis 3.23M, merge 0.14M; the 5 artifact sessions are not in this sum).
Usage: the 5-hour window went from 8% to 56% (**48 points**), the weekly meter from 40% to 46%. `ANTHROPIC_API_KEY` unset, extra usage off.

Attempts (`tools/attempt_report.py`, `eval/results/m7_cold2/attempts.json`): no fourth attempt and no third attempt was needed; the only structural failure was 'basis_obs_ids must be observations of this process' (3 first attempts); the merge passed first time.
Over the two cold runs and the three synthesis reruns: 24 synthesis attempts for 18 parts, the first attempt failed for 6 of 18, every second attempt verified. `max_attempts` 4 was never used beyond 2; 3 would have sufficed.

## The blocker, and the smallest correction

**What happened.** At 17:03:29 the extractor for batch b081 wrote its exact output file (allowed), and at 17:03:32 it tried to write `data\extract\staging\all\b081.json.tmp-unused`, a leftover scratch file next to its output. The fence denied the write (nothing was created) and, by design, any unexpected denial is a critical stop for the whole run (exit 2), which also stopped the six sessions then in flight.
This is the same pattern as the `b013.json.tmp-check` event that led to the run instruction 'do not create scratch files'; that instruction was in the prompt and the model ignored it. Observed rate since the instruction: 1 in about 340 sessions (0 in the 167 of the first cold run), so a cold run of about 175 sessions has roughly a 40% chance of meeting it.

**Why it is not a safety problem.** The denied path is the worker's own output path plus a suffix, in the one folder it may write to; the pipeline only ever reads the exact output name, and Python validates that output on its own. Nothing outside the fence was touched.

**Smallest correction (needs your approval, because it is a fence exception; nothing was changed):** treat exactly this event as a documented, still-denied self-check instead of a stop, in the same way the approved read-back self-check works: for the extractor and the grounder, a denied Write whose path is the session's own output path followed by a suffix beginning `.tmp`, after the exact output file has been written and is non-empty, is classified `self_check`:
still denied, logged with the session, counted, flagged when repeated, never a stop. Any other denial, a write elsewhere, or such a write before the real output exists stays critical. About 20 lines in `tools/fence_hook.py` and `src/pipeline.py`, 6 tests and 2 mutants like the read-back ones, no model usage.
**The alternative without an exception** is to leave the design as it is and accept that about 4 cold runs in 10 need the same command run a second time (which works and costs about 9 extra minutes); that is the behaviour measured here.

## 2. Functional correctness: PASS

- Ledger 3,240 of 3,240 files accounted for: 3,200 unique messages read (3200/3200) and 40 copies. The driver noted the 3,200 against 3,240 itself; the one-line coverage should say 'files, unique, copies' (noncritical).
- Every stage's validator passed in the final state: 81 of 81 extractions and groundings verified, 7 synthesis parts and the merge verified (floor, catch-all guard and positional check included), 0 provenance chain problems.
- The detached run survived its driver; the exit code was recorded; the second invocation was safe (`already_running` is refused; finished stages came from cache); the driver reported the full figures from the SUMMARY block.
- Fresh untrusted clone: the project's allow entries were ignored and the command still ran on its own allow list; every worker session was fenced (the denial above is the fence working, not failing).
- 5 artifacts were drafted (the top 5 by central estimate that pass the evidence rules; none skipped): P30, P37, P33, P1, P17; each cites verified quotes.

## 3. Financial calculation correctness: PASS (`tools/finance_validation.py`)

F1 months, events per month, hours, dollars, allocation, total and the new range recomputed with a second implementation: exact. F2 an independent recount of (process, thread) pairs equals the events (954 = 954). F3 every scored opportunity has at least 2 events, 3 emails and 2 threads. F4 the report's total, range and all 97 rows equal the data.
Provisional F5 and F6 pass: no near-duplicate scored pair; overlap deduction $0.00 (the overlap rule remains vacuous: events are keyed per process); at most $15.32 (1.3%) of thread-level double counting; low-confidence share 1.0%, largest opportunity 6.4%, top five 21.8%.

## 4. Estimated business savings (estimates: events counted by code, minutes assumed by the model)

| Run | Processes / scored | Events | Central estimate per month | Range (low to high minutes) |
|---|---|---|---|---|
| Working run (whole mailboxes; blind to two thirds of farmer-d) | 100 / 93 | 744 | $1,152.72 | $544.80 to $2,122.29 |
| Cold run 1 (size split) | 129 / 116 | 1,078 | $1,385.79 | $607.01 to $2,662.21 |
| **Cold run 2 (this retest)** | **109 / 97** | **954** | **$1,202.88** | **$550.71 to $2,212.01** |

The central estimate uses the base minutes proposed per process; the range varies only the low and high minutes proposed with a reason (the events and the $85 rate are fixed, no other bound is invented). The two split runs differ by 13%: what is recurring work, and the minutes, change from run to run (next section).
A defensible way to state it: **about $1,200 to $1,400 per month at the central estimate, within roughly $550 to $2,700 on the minutes assumption alone.**

## 5. Repeatability: PASS, provisional (3 full runs; `tools/repeatability.py`)

- Deterministic stages (ledger, batch manifest, ingest rows): identical between the working run and this retest.
- Extraction, run against run: message agreement 97.6% (working against retest), 97.3% (cold 1 against retest), 97.8% (working against cold 1); thread Jaccard 0.966, 0.961, 0.969; observation Jaccard 0.63 each time (at its line of 0.60). About 35 to 40 threads of about 1,930 differ per pair. Stable.
- Synthesis, cold 1 against cold 2 per mailbox (extraction variation included): events farmer-d 554 to 509 (-8%), giron-d 246 to 221 (-10%), lokay-m 75 to 80 (+7%), **williams-w3 203 to 144 (-29%)**; dollars -12.5%, -13.9%, +7.5%, -28.5%; thread Jaccard 0.72, 0.69, 0.84, **0.50**; all four together events -11.5%, dollars -13.2%, 80 to 85% of events in matched processes.
  The criteria (thread Jaccard at least 0.60 and events within 25%) pass for three mailboxes and **fail for williams-w3**.
- **williams-w3:** it is just under the size trigger (989 observations this time, 1,022 in the first cold run), so this run synthesized it whole and counted only 17.5% of its observations (thirds 20, 16, 17), the first cold run split it in two and counted 28%. The mailbox sits where the architecture flips, but **that is not shown to be the cause**: the working run synthesized it whole too (999 observations) and counted 34%, so whole against whole also differs by -35% in events (see the update at the top).
- Earlier synthesis-only reruns on identical inputs: pairwise grouping agreement 0.93 to 0.996, but 15 to 32% of the observations that count as recurring differ (`docs/M6_READINESS.md`).

## 6. Process-discovery quality (`tools/quality_baseline.py`): evidence passes, recall varies

| | Working run | Cold 1 | Cold 2 |
|---|---|---|---|
| Observations / counted share | 4,767 / 27.5% | 4,740 / 41.3% | 4,628 / 31.4% |
| C1 evidence (counted quotes re-read byte-exact) | pass (1,312) | pass (1,958) | **pass (1,452)** |
| C2 every third covered | FAIL (farmer-d 59 / 0 / 0) | pass | **pass** (farmer-d 25 / 30 / 39; giron-d 43 / 47 / 24; williams-w3 20 / 16 / 17) |
| C4 missed clusters at most 10% of unassigned | FAIL 18.1% | pass 9.8% | **fail 17.1%** (26 clusters, 542 observations) |
| C5 near-duplicate pairs | 0 | 0 | 1 of 109 ('Sitara and Tagg deal number lookups' / 'Linking Tagg, Sitara and EOL deal numbers'); 4 hint clusters spread over 3 to 6 processes (nominations 52 observations over 6) |
| C6 no process over 25% of a mailbox | pass | pass | pass |
| C7 precision sample | n/a | 30 of 30 | **30 of 30** (2 borderline: #10, #24; worst case 28 of 30 = 93%; `precision_sample_cold2.md` for you to check) |

So what is counted is right (evidence and precision), but how much recurring work is found changes from run to run (31% to 41% counted; 10% to 17% missed clusters). That is the accuracy phase's job, not a V1 blocker.

## 7. Dashboard and reporting readiness: PASS

D1 to D7 pass (97 scored opportunities in rank order, every dollar figure drills to a highlighted quote, original names with trailing dots shown as written, coverage page complete, report has the ranked table with the central estimate and the range per row, escaped text; median page 48 ms, slowest 77 ms, data 10.2 MB loading in 0.05 s). `tools/audit_dashboard.py`: **0 problems**: 6,180 quotes re-read from the raw bytes, 804 claims, 1,876 pages, 1,953 quote links opened.
Visual check: the home page shows 'central estimate' and 'range $550.71 to $2,212.01 at the low and high minutes assumptions'; an opportunity page shows its central estimate with its own range and labels every number counted, assumption or estimated.

## 8. V1 freeze recommendation (as written after the retest; superseded by section 7 of the update at the top: the correction is implemented, the freeze is not authorized and waits for the official benchmark)

**Freeze V1 after the one narrow correction, not before.** Reasoning: functional, financial, evidence and performance criteria all pass on a true cold run; the only failure is an operational one that will recur in roughly 4 of 10 cold runs and has a 20-line, no-model-cost fix. Once you approve it and the tests pass, freeze the working tree as V1; the official final run (M11) doubles as its confirmation. Until then the tree is as tested and nothing was changed.
Keep for the freeze: the rollback switches (`synth.size_split.enabled`, `synth.positional.fail_share`, `synth.max_attempts`, `act.selection`), the three preserved result sets, and the two clean copies until you decide. If you prefer to freeze today, freeze with the single known limitation written into NOTES.md ('about 4 cold runs in 10 stop once on a harmless scratch-file denial; run the same command again').

## 9. Remaining noncritical improvements for the next phase (value in the DEVLOG row 'Deferred improvements')

1. Test, not apply, a size trigger near 800 observations: williams-w3 flips architecture around 1,000, but its variation also appears whole against whole, so this is a hypothesis for the next phase (see the update at the top).
2. A residual pass over unassigned clusters (17% of unassigned in this run): perhaps +5 to 10% events, about +$60 to $140 per month.
3. Stabilise the minutes assumption (dollars moved -14% to +7% for three mailboxes between runs): a fixed rubric or the median of two proposals.
4. Start each mailbox's synthesis when its batches are grounded: 3 to 4 minutes of headroom.
5. Thread-aware overlap rule (up to $15 to $20, 1.3 to 1.6%); merge misses between chunks; extraction span variance; report polish (a limitations section, 'files, unique, copies' coverage line, mobile table); reassess `max_attempts` 4 against 3 (no third attempt was needed in 18 parts).

## Usage and state

Retest 48 meter points; 5-hour meter 56% at the end of the analysis; weekly 46%. Dashboards: working baseline, cold 1 and cold 2 are served on 8765, 8766 and 8767 respectively (they stop when the computer sleeps). Clean copies kept: `observation_agent_cold` (cold 1), `observation_agent_cold2` (this retest), `observation_agent_smoke` (dev smoke; can be deleted). Analytical prompts, schemas, labels and holdout unchanged; the holdout was not run.
