# M5: size-triggered synthesis splitting and positional coverage (implementation and giron-d test)

Approved by the user on 24 Nov 2026 after the farmer-d experiment (`docs/M5_SPLIT_EXPERIMENT.md`). Not committed. The dashboard, `output/all`, `data/runs/all/synth.json` and
the whole-mailbox results (`s001`-`s004`, `m001`) are untouched; no revised financial estimate is published. Analytical prompts, schemas, labels and holdout unchanged.
Judgments below (joins, genuine discoveries, precision) are mine, read from printed lists; the code only counts.

## What was built

| Piece | Where | Behaviour |
|---|---|---|
| Size trigger | `synth.size_chunks`, config `synth.size_split` | A mailbox over `over_observations` (1,000) is cut into `ceil(n / 850)` chunks (never fewer than 2). 999 stays whole, 1,001 becomes 2, 2,433 becomes 3 |
| Chunker | `synth.fit_chunks`, `synth.plan` | Date-ordered whole threads, balanced by estimated tokens (budget total / k, widened 5 percent until at most k chunks): the algorithm of the experiment. It reproduces the experiment's farmer-d chunks exactly (850 / 849 / 734, same order); giron-d (1,085) gives 559 / 526 |
| Part ids | `synth.part_id` | Whole mailbox `s001`; chunks `s011 s012 s013` (mailbox 1), `s021 s022` (mailbox 2). Digits only, because the synthesizer's fence admits exactly `sNNN` files; **the fence was not changed**. A collision raises `ValueError` |
| Stage | `pipeline.stage_synth` | With `size_split.enabled`, parts run in parallel (existing pool), then ONE merge over every part's groups (the existing merger, unchanged), cross-mailbox as before. A whole mailbox keeps its `PART i OF n` header and id, so lokay-m (`s003`) and williams-w3 (`s004`) are cache hits; chunks say `PART j OF k` |
| Fallback | `synth.size_split.enabled: false` | The old path, byte for byte (own ids, caches, results). Both modes can coexist in `data/` |
| Positional check | `synth.positional`, config `synth.positional` | Each part's input (date order) is cut into thirds; per third of at least 40 observations: counted share below `fail_share` (2 percent) fails the attempt; below `warn_share` (5 percent) is a `positional_gap` warning; below 25 percent of the best third is a `positional_decay` warning. Warnings are logged per attempt, saved in the part's `coverage`, and carried as report flags (`positional_gap`, `positional_decay`) on the merged result. A failure is fed back to the next attempt like the 15 percent floor (one standing requirement per third), measured even when the structure failed, and re-checked on a cache hit |

Tests: 719 passing (12 new), no model calls. 8 split and 10 positional mutants added to `tools/mutation_check.py`, each caught by its intended test (the full run is recorded in DEVLOG).

## Positional thresholds: warn or fail?

Counted share per third (recurring, non-candidate processes of two or more observations), measured on every existing result:

| Result | Thirds counted | Reading |
|---|---|---|
| farmer-d whole (2,433) | 59.4, **0**, **0** | blind to two thirds |
| giron-d whole (1,085) | 41.0, 41.2, **9.7** | decays at the end |
| farmer-d subset (1,178), williams-w3 (999), lokay-m (250) | 33 / 30 / 25, 41 / 33 / 29, 65 / 63 / 62 | uniform |
| farmer-d chunks (850, 849, 734) | 60 / 58 / 66, 31 / 35 / 18, 45 / 41 / 40 | healthy, lowest 17.7 |
| giron-d chunks (559, 526) | 49 / 51 / 41, 53 / 57 / 47 | healthy, lowest 41.2 |
| dev slice (303) | 49 / 51 / 23 | healthy |

Healthy thirds are never below 17.7 percent; the blind ones are 0 and 9.7. **Decision: keep the failure at 2 percent and the warning at 5 percent.** The failure needs a third to have almost nothing counted
(at most 16 of 811 observations), which no healthy third approaches, and the error text tells the model to leave out anything that does not belong to a recurring process, so nothing is forced.
The 5 percent warning alone would have missed giron-d's decay (9.7 percent), which the decay warning (under 25 percent of the best third: 10.3) does catch, by a small margin.
If a quiet third is ever legitimate, the cost is one retry per attempt and a stop after three, the same as every other validator; setting `fail_share: 0` makes the check warning-only.
Evidence is 9 healthy parts and 2 blind ones: thin, so the thresholds are provisional and reported as such.

## The giron-d test (isolated; `tools/split_mailbox_test.py`, `tools/split_mailbox_report.py`, `eval/results/m5_giron_d_split/`)

The production planner and production `_synth_one` / `_merge` ran on giron-d only (chunks `s021`, `s022`, merge `m210`, its own log). Every validator passed: structure, the 15 percent floor (47 and 53 percent), the 25 percent catch-all guard, the positional check (no warning), the merge check.

| | Whole mailbox (`s002`) | Two chunks + merge |
|---|---|---|
| Processes (scored) | 30 (28) | 43 (39) |
| Observations counted | 332 | 538 (+62%) |
| Events (`events.build`) | 216 | 280 (+29.6%) |
| Events in scored processes | 212 | 272 (+28%) |
| Dollars per month, giron-d scored alone | $288.57 | $420.90 (+46%) |
| Thirds counted | 41.0, 41.2, 9.7 | 49.0, 49.2, 50.6 |
| Last four tenths counted | 25.9, 8.3, 13.9, 8.3 | 35.2, 50.5, 62.0, 49.5 |
| Wall | 254 s (2 attempts) | 281 s (chunks 263 s, merge 18 s) |
| Subagent tokens | 1,287,283 (2 attempts) | 1,297,744 (6 sessions: 3 failed attempts, merge 39,621) |
| Meter | not measured separately | about 9 to 10 points (73 to 85 percent; about 2 to 3 of the 12 were this session's own turns) |

**Discoveries.** 26 final processes continue a whole-mailbox process (188 events), 13 are mostly new (60 events), 4 mixed (32). 78 threads are new and 17 threads of the baseline's 216 are no longer covered (net +61; run-to-run variation, to be measured
under repeatability). The new processes are genuine late-period work that the whole synthesis could not see in its last four tenths: bankruptcy-book moves (18 events, 45 observations; the baseline had a smaller version of the same), Netco curve and book setup, UBS transition onboarding, month-end NSS and EMW clean-up, training enrolment, P&L file maintenance.
Some are generic ('Meeting scheduling and attendance', 'Risk team event coordination'). Precision spot check, 30 of the 240 newly assigned observations (seeded sample): 28 fit their process, 2 are weak (a generic meeting line, an ID lookup): about 93 percent, in line with the baseline's 88 and farmer-d's 90.

**Joins.** The merger joined 7 of 47 groups into 3 processes; all 3 read as correct (settlement-book moves; Sitara price and rate corrections; daily position report). By the baseline identity the denominator is 4 (processes spanning both chunks), 2 joined, 2 not: 50 percent, **the weakest of the two tests** (farmer-d: 17 of 18 on my reading).
By my reading there are 2 probable missed joins (monthly P&L top sheet / auditor testing document requests; new trading book setup / book admin and trading group setup) and 2 borderline (curve setup / Netco curve file setup; nightly post ID run / post ID and report access lookup); 0 clear incorrect joins.
Missed joins fragment one process into two: they do not double count events (an event is a distinct process-thread pair; 2 threads sit in two processes) and the dollars are a sum, so the cost is a cleaner process list, not a wrong total.

## Expected runtime and token implications

Measured synthesis sessions (subagent tokens, seconds; a first attempt failed validation in 9 of 9 parts, always with one to seven structural slips, never a floor failure):

| Part | Attempts | Tokens | Seconds (summed) |
|---|---|---|---|
| Working run, whole mailboxes (all attempts in the run log) | farmer-d 8, giron-d 2, lokay-m 2, williams-w3 2, merge 2 | 5.78M | 1,362 |
| farmer-d, three chunks + merge (experiment) | 7 + 1 | 2.88M | 401 wall |
| giron-d, two chunks + merge | 5 + 1 | 1.30M | 281 wall |
| Projected cold run with the split | farmer-d 2.88M + giron-d 1.30M + lokay-m 0.23M + williams-w3 1.19M + final merge 0.1 to 0.25M | about 5.9M to 6.0M | |

**Against what actually happened, the split costs about the same** (5.9M against 5.8M tokens): the whole-mailbox flow needed eight farmer-d attempts. Against an idealised single successful attempt per mailbox (about 1.8M) it is three times higher, but that ideal was never achieved.
My earlier estimate of '+8 to 13 meter points per cold run' compared the split with one optimistic baseline attempt and overstated the difference; **the honest figure is about +0 to +2 points over the working run, and a cold run stays at about 65 to 85 points (92 in the projection's worst case) of the 5-hour window.** A cold run needs a nearly empty window.
The farmer-d and giron-d chunk results are not reusable from the experiment: the first full run pays for the farmer-d chunks (about 13 points) once; giron-d's chunks (`s021`, `s022`), lokay-m and williams-w3 are already cached under the production ids.

**Time.** Chunks run in parallel and each chunk attempt is 55 to 147 s (mean about 95 s over 12), so a mailbox's synthesis chain is attempts times about 95 s: farmer-d 361 s (3 attempts) against 688 s of sequential whole-mailbox attempts in the working run. The merge grows with the group count
(18 s for 47 groups, 40 s for 95); the production merge sees about 190 groups, so about 75 s per attempt. In the runtime projection (24 workers: mean 20.7, slow 30.1, worst 44.5 minutes) the synthesis chain is unchanged in the mean and the merge adds about 1 to 2 minutes:
**mean about 22, slow about 31, worst about 46 minutes. The 30-minute target holds in the mean (about 8 minutes of margin) and is missed in the slow and worst cases. It is still NOT verified: only a cold end-to-end run measures it.**

## Open risk for your decision (not changed)

**Three attempts may not be enough.** Every first attempt of every synthesis part failed a structural check (9 of 9), and 2 of the 5 chunks failed their second attempt as well (a third attempt was needed twice and passed both times). With 7 parts in a cold run, a part failing three times stops the run (`synthesis_failed`, exit 2); from this thin evidence I estimate the chance of a stop at roughly 20 to 40 percent per cold run (about 10 percent per part, assuming a third attempt fails one time in four).
Options: (a) raise `synth.max_attempts` to 4 for parts (costs about 2 extra points only when needed; cheapest); (b) leave it at 3 and accept resume (every finished part is a cache hit); (c) a targeted repair of only the failing process, which is new design and not proposed. **Recommendation: (a)**, as a one-line config change, if you approve.
Also worth a look later, not now: the three errors seen are the same slips each time ('basis_obs_ids must be observations of this process', 'observation is already in P10', 'unknown observation ids'); a one-line reminder in the run instruction (not the analytical prompt) might cut the first-attempt failure rate, at no cost in tokens.

## Files

`src/synth.py` (`size_chunks`, `fit_chunks`, `plan`, `part_id`, `positional`, `coverage`), `src/pipeline.py` (`stage_synth`, `_floor`, `_kind`, `_positional_flags`, `_merge`), `config/settings.yaml` (`synth.size_split`, `synth.positional`), `tests/test_synth_merge.py`,
`tools/split_mailbox_test.py`, `tools/split_mailbox_report.py`, `tools/mutation_check.py`, `PLAN.md` (one sentence), this file. Results: `eval/results/m5_giron_d_split/` (`experiment.json`, `runlog.jsonl`, `report.json`).
