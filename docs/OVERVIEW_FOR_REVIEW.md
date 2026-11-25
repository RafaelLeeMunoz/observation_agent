# Observation agent: a one-page overview for a methods review

## What it does
Reads **all 3,240 Enron emails** (4 mailboxes; 3,200 unique messages + 40 exact copies), finds the **recurring work processes**, where they **stall**, and estimates **hours and dollars saved** ($85/hour) if each were automated. Every claim carries a **verbatim quote** that is re-read from the raw bytes. It drafts artifacts (SOP, checklist, template) for the top 5 opportunities and serves a **dashboard** where every number drills through to the raw email with the quote highlighted. One command: `claude -p "/observe all"`. Runs on a Claude Pro subscription (no API), about 21 minutes cold, about 48 points of a 5-hour usage window.

## The rule that shapes everything
**Code does anything that must be exact** (parsing, dedup, dates, counting, arithmetic, quote checking, the report). **The model only reads and judges text** (what is a task, which tasks are the same process, a quote, a minutes estimate). The model never computes a published number. Email text is treated as untrusted data.

## Pipeline
| Stage | Who | Output |
|---|---|---|
| 1 Ingest | code | raw bytes parsed (no newline translation), 40 copies merged, 2,736 threads, dates, a ledger of all 3,240 files with a status each |
| 2 Batch | code | 81 batches of at most 40 messages, whole threads, token budget |
| 3 Extract | cheaper model, 24 in parallel | per message: observations (task, process hint, actors, trigger, output, stall signals, quote). Code verifies each quote exists in the raw file; fails are dropped and logged |
| 4 Ground | model | does a claimed stall signal have support in numbered source sentences? unsupported ones are not published |
| 5 Synthesize | stronger model + merger | groups about 4,700 observations into recurring processes with low/base/high minutes per event. A mailbox over 1,000 observations is cut into date chunks (whole threads) and a merger joins the chunks |
| 6 Events and score | code | event = one (process, thread) pair, quoted copies counted once; events per month over the months observed; hours = events/month x minutes / 60; dollars = hours x 85; a range from the low and high minutes |
| 7 Artifacts | model | top 5 by dollars that pass evidence rules; code checks every citation |
| 8 Report and dashboard | code | report.md, dashboard data, Flask app on localhost |

## File types (and why)
- **Raw emails**: `corpus/<mailbox>/<folder>/<n>.` (no extension; some names end with a dot, which Windows hides). Never modified; read only through one byte-exact reader, because every stored offset is a raw-byte offset.
- **SQLite** (`ingest.sqlite`: messages, threads, mailbox date windows) and **CSV** (`ledger.csv`, `MANIFEST.csv`): the exact, queryable facts.
- **Batch text + manifest JSON**: what each worker reads (code-written, quotes marked as untrusted).
- **JSON per stage** (extraction, grounding, synthesis, merge): validated against **JSON Schemas** by our own validator; observations carry quote start/end byte offsets, match type and date basis.
- **Cache entries (JSON)**: input fingerprint + prompt hash + model -> output, so an unchanged corpus repeats no work.
- **`runlog.jsonl`**: one record per model attempt (status, errors, seconds, tokens), plus a **fence log per session**.
- **`dashboard/data.json`** (10 MB): a graph of **claims**, each with kind (observed / counted / assumption / estimated), formula, inputs and evidence quote ids. The dashboard only renders this.
- **Markdown** outputs (`report.md`, artifacts); **YAML** config (every tunable number in one file); user-labelled **benchmark JSON** (about 40 messages) for the extraction stage.

## Decisions and tradeoffs
1. **Code vs model split.** Exactness and auditability, at the price of plumbing and of not letting the model "just summarize".
2. **Evidence first.** A claim without a verifiable quote is dropped. Cost: lost recall (about 20 quotes and 175 stall signals dropped per run).
3. **Untrusted text, minimal tools.** Each worker is a headless session allowed to Read one input file and Write one output file, enforced by a hook; any other action stops the run. Cost: rare harmless model habits (a stray scratch file) can stop a run, so we added one narrow, logged exception.
4. **Read everything, never sample.** 100% coverage ledger. Cost: 81 batches and about 23M tokens per cold run.
5. **Synthesis in one pass was silently blind.** One call over a mailbox of 2,433 observations assigned nothing from the last two thirds of its input yet passed our 15% completeness floor. Fixes: date chunks above 1,000 observations plus a merger, and a positional check (every third of the input must be looked at). Cost: more sessions, an LLM merge step, and a size threshold that one mailbox (about 1,000 observations) sits right on.
6. **Validators feed back.** Structural, coverage floor, catch-all guard, positional check; a failed attempt gets its errors in the next attempt (up to 4). First attempts fail about 1 in 3.
7. **Dollars are estimates by design.** Arithmetic is verified with a second, independent implementation; the minutes per event are model-proposed assumptions with a low/base/high range. We do not claim the assumptions are right.
8. **Cheap model for bulk, stronger model for grouping.** Cost and speed against grouping quality; 24 parallel sessions did not slow each other in our test.

## What we measured (3 full runs)
Extraction is stable (97% of messages agree between runs, 0.97 Jaccard on threads). Synthesis is less so: the same input grouped twice agrees on 93-99% of observation pairs, but **which observations count as recurring work varies by 15-30%**, and the central estimate moved 13% between two runs ($1,386 vs $1,203 per month; range about $550-$2,700 from the minutes assumption alone).

## Where feedback would help most
1. **Measuring recall without labels** (we only have precision-style spot checks and a small extraction benchmark; no labelled process set).
2. **Grouping method:** an LLM groups a text list. Would embeddings + clustering (or hint-based pre-clusters) give more stable, cheaper and testable grouping?
3. **Run-to-run stability:** consensus across two runs, calibration of the minutes assumption, or reporting intervals instead of a point.
4. **Event definition and dedup** (one event per process per thread; quoted copies merged by anchor): is that a sound unit to count?
5. **Sampling plan** for precision estimates with a proper confidence interval (our 30-item read only supports about 90% as a lower bound).
