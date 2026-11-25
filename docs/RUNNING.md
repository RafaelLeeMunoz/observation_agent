# Running the observation agent (one command, fresh clone, trust, rollback)

## The command

```powershell
claude -p "/observe all"        # the full corpus (3,240 messages); `/observe` alone runs the ~200-message dev slice
```

What it does, in order (`.claude/commands/observe.md`, a Haiku driver that only runs commands and reports):

1. `python -m src.ingest` (cached).
2. `python -m src.pipeline start --slice all`: starts the pipeline **detached** (it outlives the driver) and records it in `output/all/logs/run.json`. A second start while one is alive does nothing and says so.
3. `python -m src.pipeline wait --slice all --max-seconds 540`, repeated: each call returns before the driver's 10-minute shell limit. Exit code 5 means still running (call again); 0 done; 1 incomplete; 2 a critical check failed; 4 stopped cleanly (usage limit); 3 the run died or none was started (start again).
4. The pipeline starts the dashboard itself when it finishes cleanly; the driver makes sure it is up (`python -m src.serve --slice all --detach`, http://127.0.0.1:8765/).
5. The driver reports the coverage line, the central estimate and its range, the ranked opportunities, the artifacts and the log path.

If the driver session ends early, **no progress is lost**: the detached run keeps going to the artifacts, the report and the dashboard. Running the same command again later resumes: every finished stage is cached.
A person can also run it in the foreground: `.venv\Scripts\python -m src.pipeline run --slice all` (the same code; this is what `start` launches with `--record --then-serve`).

## A fresh clone: trust and permissions

Requirements: Windows, Python 3.12, `.venv` built from `requirements.txt` (`.venv\Scripts\python -m pip install -r requirements.txt`), Claude Code logged in with the Pro subscription, `ANTHROPIC_API_KEY` **unset** (the pipeline refuses to start model sessions if it is set).

Measured in a clean copy of the working tree with Claude Code 2.1.330 (24 Nov 2026), where the folder had never been trusted:

- Claude Code **ignores the project's `permissions.allow` entries and project settings hooks** until the folder is trusted ("this workspace has not been trusted"). The driver printed that warning and ran on.
- The `/observe` command still ran, because the command file carries its own `allowed-tools`: exactly the `src.ingest`, `src.pipeline` and `src.serve` module commands, nothing broader (a test checks that every command the file asks the driver to run is covered by its own allow list and that nothing wider is requested).
- The **model workers do not depend on trust**: every extractor, grounder, synthesizer, merger and artifact-writer session is started by the pipeline with its own `--settings` (fence hook, no project instruction files, no auto memory), Read and Write only. A session with no fence record stops the run (`fence_unobserved`), and a denial stops it (`fence_denial`). The first cold run in the untrusted copy ran 167 sessions with no denial and a fence record for every one.
- Recommended for an interactive checkout: run `claude` once in the folder and accept the trust dialog, so the project's allow list and hook also apply to the driver. Do not disable permission checks, do not use `--dangerously-skip-permissions`, and do not assume a folder you have trusted before behaves like a clean clone.

To test a fresh clone without a second install: `.venv\Scripts\python tools\make_cold_copy.py C:\path\to\copy` copies exactly what a checkout of the working tree would hold (uncommitted changes included, no run state) and junctions the virtual environment; then `cd` there and run the command.

## Settings that can be rolled back (`config/settings.yaml`)

| Setting | Now | Rollback |
|---|---|---|
| `synth.size_split.enabled` | true (mailboxes over 1,000 observations are synthesized as chunks) | false: whole-mailbox synthesis |
| `synth.positional.fail_share` | 0.02 | 0: the positional check only warns |
| `synth.max_attempts` | 4 | 3 |
| `act.selection` | `top_n` (the 5 most valuable opportunities that pass the evidence rules; fewer if fewer pass) | `threshold` with `min_dollars_per_month: 1000` and `at_least_one: true` (the earlier rule) |
| `act.top_n`, `act.min_confidence`, `act.min_events`, `act.min_dollars_per_month`, `act.parallel` | 5, medium, 3, 0, 5 | any value |

The artifact rules (`min_citations`, `min_messages`) and the citation check on every artifact are unchanged: an artifact whose quotes do not verify is not published.

## The financial figures

Each dollar figure is a **central estimate** (the base minutes per event proposed with a reason) with a **range** from the low and high minutes of that same proposal. The running total carries the same range (`total_low`, `total_high`, claims with their own formula and inputs). The range varies only the minutes assumption: the events counted and the $85/hour rate are not varied and no other bound is invented.
Run-to-run variation (the model proposes different minutes and counts different observations as recurring work) is measured separately: see `docs/M6_READINESS.md`.
