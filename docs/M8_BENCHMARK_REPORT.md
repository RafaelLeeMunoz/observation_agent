# M8: official final cold benchmark (25 Nov 2026)

One invocation of `claude -p "/observe all"` in the verified clean copy `observation_agent_cold3` (0 differences from the tested tree, manifest sha256 `9580bde73edffd6a`, no `data\` or `output\`), 24 workers, `ANTHROPIC_API_KEY` unset, 5-hour window 0% at launch.
**No code, analysis or configuration change was made before or during the run.** Nothing is committed, V1 is not frozen, the working baseline and the earlier benchmark artifacts are preserved.
Evidence: `eval/results/m8_final/` (command times and driver output, `m8_timing.json`, `attempts.json`, `quality_final.json`, `finance_final.json`, `dashboard_final.json`, `precision_sample_final.md`, `evidence/` with a copy of the run's outputs, synthesis results and every session's fence log) and `eval/results/m6_repeat/*r4_*`.

## Verdict

**The benchmark passed. All mandatory V1 release-candidate gates passed, with a single invocation.** Two provisional repeatability lines failed (R5, R6): the synthesis step judged much more work as recurring in this run than before, and my precision reading is weaker. This does not fail a gate; it is the first thing an audit should examine.

## 1. Single-invocation status: PASS

Launched 00:53:17 UTC, the command ended 01:18:42 UTC with **exit 0**; run record `finished`, exit 0, one start (`already_running` false), no `stop.json`, no restart, no intervention. The driver printed the full SUMMARY block (3200/3200 read, central estimate and range, top ten, 5 artifacts, dashboard URL). The untrusted-workspace warning appeared (project allow entries ignored); the command ran on its own allow list.

## 2. Wall time and stages

**Total 1,525 s = 25.4 minutes** (pipeline 1,503 s). Margin to the 30-minute limit: 4.6 minutes.

| Stage | Window from launch (s) | Sessions (verified) | Longest (s) | Subagent tokens |
|---|---|---|---|---|
| ingest and batching | 0 to 14 | code | | |
| extraction (24 parallel) | 14 to 995 | 83 (81) | 507 | 17.18M |
| grounding (same pool) | 234 to 1,015 | 82 (81) | 47 | 3.59M |
| synthesis, 7 parts | 1,015 to 1,390 | 11 (7) | 255 | 3.55M |
| merge | 1,391 to 1,496 | 1 (1) | 106 | 0.17M |
| score (code) | 1,497 | | | |
| 5 artifacts in parallel | 1,497 to 1,515 | 5 (5) | 18 | 0.09M |
| report, dashboard | 1,516 to 1,525 | code | | |

## 3. Usage and tokens

5-hour window **0% to 76%** (78% after the analysis); weekly 48% to 58%. The meter includes this orchestrating session's own turns (its context is large), so the run's share cannot be isolated; the previous retest measured 48 points.
**Subagent tokens 24.58M** (previous: 23.85M, +3%). Extra usage disabled. A cold run fits one window but leaves little for anything else.

## 4. Processing completeness: PASS

3,240 of 3,240 corpus files accounted for (3,200 unique messages + 40 copies; ingest reconciliation passed); 3,200 of 3,200 messages read (dispositions: observations 2,263, not business 600, business without process signal 289, ambiguous 39, cannot interpret 9); 81 of 81 extraction and grounding batches verified; no unread batch; 7 of 7 synthesis parts and the merge verified; 4,599 observations, 116 processes, 96 scored, 1,301 events.

## 5. Validation and evidence integrity: PASS

- Every validator passed in the final state; 0 provenance chain problems; canary and sentinel checks passed.
- **2,806** counted observations' quotes re-read byte-exact from raw files (C1, 0 mismatches, 0 unknown or double-assigned ids).
- Full dashboard audit: **0 problems**: 6,168 quotes re-read, 850 claims, 2,276 pages, 3,902 quote links.
- Readiness D1 to D5 and D7 pass (ranked list and range, drill-through to highlighted quotes, original trailing-dot names, coverage page, report, escaping); D6 pass (median 64 ms, slowest 113 ms).
- 5 artifacts (top 5 by central estimate that pass the evidence rules; none skipped): P36, P1, P9, P4, P2 with 11, 22, 24, 20 and 16 verified citations from 10, 8, 6, 3 and 7 emails. Three carry a `numbers_lint` flag: the digits are section numbers, dates quoted from emails and one quoted '100%', none a computed figure.
- Cache (C3): an offline rerun in a disposable copy (every model call refused) took 2.2 s with 175 cache hits (81, 81, 7, 1, 5), 0 attempts, and a byte-identical `report.md`; the official run's files were not touched.
- Offline tests (C5): 796 passed.

## 6. Financial calculation: PASS (arithmetic verified; the assumptions are not)

F1 months, events per month, hours, dollars, allocation, total and range recomputed with a second implementation: exact. F2 independent recount 1,301 = 1,301 events. F3 every scored opportunity meets the evidence thresholds. F4 report equals data.
**Central estimate $1,355.30 per month, range $646.67 to $2,354.10** (the low and high minutes per event only; events and the $85 rate fixed). Provisional F5, F6 pass: overlap deduction $0.00 (the overlap rule remains vacuous); upper bound of thread-level double counting $37.29 (2.75%, higher than the earlier 1.3 to 1.6%); low-confidence share 0.2%, largest opportunity 8.5%, top five 24%.
These checks prove the arithmetic follows the stored inputs, not that the minutes, rate or savings are right.

## 7. Comparison with previous runs

| | Working baseline | Cold 1 | Cold 2 (retest) | **Official (this run)** |
|---|---|---|---|---|
| One invocation | n/a (many) | no (driver killed it) | no (fence denial, rerun) | **yes** |
| Wall time (min) | n/a | 20.2 (pipeline) | 22.3 (21.1 active) | **25.4** |
| Tokens (M) | n/a | 23.74 | 23.85 | **24.58** |
| Usage (5h points) | n/a | about 50 | 48 | **76** (includes this session) |
| Observations | 4,767 | 4,740 | 4,628 | **4,599** |
| Counted as recurring | 27.5% | 41.3% | 31.4% | **61.0%** |
| Processes / scored | 100 / 93 | 129 / 116 | 109 / 97 | **116 / 96** |
| Events | 744 | 1,078 | 954 | **1,301** |
| Central estimate ($/month) | 1,152.72 | 1,385.79 | 1,202.88 | **1,355.30** |
| Range ($/month) | 544.80 to 2,122.29 | 607.01 to 2,662.21 | 550.71 to 2,212.01 | **646.67 to 2,354.10** |
| First synthesis attempts failed | mixed | 3 of 8 | 3 of 7 | **4 of 7** |
| Critical stops | n/a | 0 | 1 | **0** |

Repeatability (provisional): R1 deterministic stages identical. R2 to R4 pass (extraction message agreement 97.3 to 97.9%, thread Jaccard 0.96 to 0.97, observation Jaccard 0.62 to 0.625).
**R5 fails** (retest against this run: farmer-d events +32%, williams-w3 events 144 to 276 (+92%), thread Jaccard 0.48; all events +36%, dollars +13%). **R6 fails** (share of events in matched processes 0.65 to 0.70 against 0.70).
Dollars moved far less than events (-2% against cold 1, +13% against cold 2) because the proposed minutes per event were lower (median 12 against 15).

## 8. Fence self-checks, retries, errors and unexpected behaviour

- Fence: 466 allowed calls, **2 denials, both the approved synthesizer read-back self-check** (parts s012 and s004; denied, logged, not stops), 0 unexpected denials, no instruction file loaded, 0 rate limits, 0 usage-limit events. **The new extractor/grounder scratch exception was not exercised in this run** (no scratch write happened): it remains proven only by tests.
- Retries, all verified on the second attempt: extraction 2 (b019 'observations' disposition without an observation; b059 missing `work_items`), grounding 1 (b002 invalid JSON), synthesis 4 first attempts (unknown observation id twice; an observation in two processes twice). No third or fourth attempt.
- Slow stragglers: extraction sessions of 437 s and up to 507 s (typical about 100 to 130 s); the merge took 106 s (58 to 60 s before). These make the 4.6-minute margin smaller than before (21.1 minutes on the retest).
- **Broader processes.** The synthesizer grouped much more coarsely: counted share 61% (31 to 41% before), largest processes 212, 180, 164, 157 and 120 observations (88 at most on the retest), each under the 25%-of-a-part guard. A spot check of the two largest showed mostly coherent members plus some generic ones.
- **Precision (C7, provisional): 27 of 30 by my reading** (pass at 85%), down from 30 of 30 twice: 3 weak (#9, #18, #23), 4 borderline (#3, #11, #12, #24); counting all seven as wrong gives 23 of 30 (77%), below the line. Please check `precision_sample_final.md`. With the jump in counted share, this points at over-inclusion.
- 10 `candidate_unmerged` flags (single-observation candidates nothing joined; normal). Missed-cluster proxy (C4) 3.5% (pass).

## 9. Mandatory V1 release-candidate gates: **all passed**

| Gate | Result |
|---|---|
| R1 every message read, ledger complete | pass |
| R2 processes with what, who, how often, stalls (116; 77 with a published stall signal) | pass |
| R3 ranked hours and dollars at $85 | pass |
| R4 verbatim quotes verified (C1; audit 0 problems) | pass |
| R5 artifacts with verified citations (top 5) | pass |
| R6 dashboard with drill-through (D1 to D5, D7) | pass |
| C1 no API key; C3 cache; C4 exact work in code; C5 tests without a model; C6 Python; C7 local | pass |
| C2 one unattended command under 30 minutes | **pass (25.4 minutes, one invocation)** |
| Hard criteria: quality C1, C2; finance F1 to F4; dashboard D1 to D5, D7; repeatability R1 | pass |
| Retest criteria (a) to (f) | pass |

Diagnostic (provisional) targets, reported separately: quality C3 pass (61.0%), C4 pass (3.5%), C5 pass (0 pairs), C6 pass, C7 pass marginally (27 of 30); finance F5, F6 pass; dashboard D6 pass; repeatability R2 to R4 pass, **R5 fail, R6 fail**.

## 10. Release-candidate status and the audit pack

**Eligible to be the V1 release candidate for the independent audit** (every mandatory gate passed). I have not frozen it. The audit should weigh section 7 and 8: the headline numbers are reproducible in arithmetic and evidence, but how much work counts as recurring varies widely between runs.

**Not in the GitHub copy** (the repository is public): the raw evidence copy (`eval/results/m8_final/evidence/`), raw run logs, driver output, `output/`, and the result files that hold local folder paths (among them `m8_timing.json`, `attempts.json`, `quality_final.json`). They stay on the project computer; hand them to the auditors directly. The numbers they hold are quoted in this report.

Give the auditors:
1. **The tested tree**: the files in `observation_agent_cold3` (identical to the working tree at launch; `tools\make_cold_copy.py --verify` checks it; documentation edited later is excluded from that check): `src/`, `tools/` (the security boundary `fence_hook.py`, `instructions_hook.py`; `headless.py`; `runctl.py`), `.claude/`, `config/`, `schemas/`, `tests/`, `eval/benchmark/`, `corpus/` with `MANIFEST.csv`, `requirements.txt`, `CLAUDE.md`, `PLAN.md`, `README.md`.
2. **This run's evidence**: `eval/results/m8_final/` including `evidence/` (report, dashboard data, artifacts, run log, run record, synthesis results, all fence logs; report sha256 `C11F3914670DF6E6`, data `F4A8ABF3FE6906DD`, run log `EE753BDE35164C8B`, first 16 hex).
3. **Earlier runs for comparison**: `eval/results/m6_baseline/`, `m6_cold/`, `m7_cold2/`, `m6_repeat/`.
4. **Documents**: this file, `docs/M7_FINAL_READINESS.md` (gate definitions, the correction, limitations, V1.1 backlog), `docs/M6_READINESS.md`, `docs/M5_SIZE_SPLIT.md`, `docs/M5_SPLIT_EXPERIMENT.md`, `docs/RUNNING.md`, `docs/OVERVIEW_FOR_REVIEW.md`.
5. **Deliberately, if you choose**: `private/DEVLOG.md` and `private/HANDOFF.md` (working notes, never committed).

Dashboards: working baseline http://127.0.0.1:8765/, cold 1 8766, cold 2 8767, official run 8768 (they stop when the computer sleeps).
