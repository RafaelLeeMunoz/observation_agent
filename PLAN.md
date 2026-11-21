# Observation Agent: build plan

Status: agreed plan, written before any code (20 Nov 2026). Work through the milestones in order. Each one ends with a "done when" check, and you report back at each check.

---

## 1. What the README requires (acceptance checklist)

One command (`/observe`) and the system:

| # | Requirement | How we prove it |
|---|---|---|
| R1 | Reads every message, not a sample | Coverage ledger: 3,240/3,240 with a status for each |
| R2 | Finds recurring processes: what, who, how often, where it stalls | Process table with participants, events/month, stall metrics, evidence |
| R3 | Ranks automation opportunities with hours/month and $/month at $85/hour | Code-computed scores with an assumptions table |
| R4 | Cites everything with verbatim quotes | Every claim links to verified quotes; a self-audit script mimics the grader |
| R5 | Acts: drafts artifacts above a threshold we choose and defend | Artifacts in `output/artifacts/`; threshold in config and NOTES.md |
| R6 | Dashboard on localhost with ranked list, running total, drill-through on every claim | Every number clicks through to the raw email with the quote highlighted |

Constraints:

| # | Constraint | How we meet it |
|---|---|---|
| C1 | No paid API keys or API spend | Runs on the Claude Pro subscription; `ANTHROPIC_API_KEY` unset |
| C2 | Full unattended run in under 30 minutes | Trimming, parallel subagents, pre-approved permissions; timed in M9 |
| C3 | Unchanged corpus → no repeated work | Fingerprint cache at every stage |
| C4 | Exact work is code: parsing, dedupe, arithmetic, citation checks, server | Python modules in `src/` |
| C5 | Tests pass without a model | `pytest` uses fixtures only |
| C6 | Python or TypeScript | Python |
| C7 | Everything runs locally | Local files, SQLite, local server |

Deliverables: the repo, the `.claude/` directory as used, `NOTES.md` (2 pages max), and the outputs of one real run (report, dashboard, run logs, artifacts). Push all of it to `RafaelLeeMunoz/observation_agent`.

---

## 2. Facts about the corpus (measured 20 Nov 2026)

- **3,240 files, 6.85 MB, roughly 1.7 million tokens.** The median email is 1.4 KB.
- **Folders:** farmer-d/logistics 1,194 · giron-d/inbox 380 · giron-d/sent 789 · lokay-m/inbox 57 · lokay-m/sent_items 161 · williams-w3/inbox 139 · williams-w3/sent_items 520.
- **File names:** the original files end in a period (`20.`), which Windows can't handle. Our copy renames them `20.txt` with the content unchanged. `corpus/MANIFEST.csv` maps each new name to its original name with a SHA-256 hash. `corpus/.gitattributes` stops Git from changing line endings, which matters because 3,135 files have mixed line endings.
- **Duplicates:** Message-ID finds only 1 duplicate, but comparing body text finds **49 extra copies**. Dedupe on normalized body text.
- **Unusable dates:** `giron-d/sent/795.` has no Date header. `williams-w3/inbox/142.` reads "Tues, 32 Febuary 2001 25:61:00". `lokay-m/inbox/74.` is dated 2031. The good dates fall in 1999 (54), 2000 (1,219), 2001 (1,497) and 2002 (467).
- **The giant message:** `farmer-d/logistics/3221.` is 350 KB (about 88,000 tokens). It holds two sentences of prose plus **3,600 rows** of a gas position table. Some FLOW_DATE values, such as `17/11/2001`, can't be month/day dates.
- **Replies and forwards:** 1,276 subjects start with Re:/Fw:. About **53% of all body text** comes after a reply or forward marker, in 1,437 messages.
- **Main senders:** darron.giron 789, bill.williams 528, michelle.lokay 166.
- A quick search found **no planted AI instructions** in the emails, but we still treat all email text as untrusted.

Milestone 1 re-confirms these facts in code. Update this section if anything changes.

---

## 3. Architecture: code counts, Claude reads, code checks

| Stage | Owner | Input → Output |
|---|---|---|
| 1. Ingest | Code | raw files → SQLite `messages` table + coverage ledger |
| 2. Batch | Code | messages → batch files sized for one subagent, threads kept together |
| 3. Extract | Subagents (parallel) | batch file → observations JSON with verbatim quotes |
| 4. Verify | Code (runs after every batch) | observations → verified observations + rejection log |
| 5. Synthesize | 1 subagent + code | compact observation list → process list; code computes counts, rates, stalls |
| 6. Score | Code | processes + assumptions → ranked opportunities in hours and $ |
| 7. Review | 1 subagent | each candidate claim + its quotes → supported / not supported |
| 8. Act | Subagent per artifact | opportunities above the threshold → SOP, checklist, email or template |
| 9. Verify artifacts | Code | artifact citations → pass/fail |
| 10. Report + dashboard | Code | data → `output/report.md`, dashboard JSON, local server |

### 3.1 Ingest rules (Milestone 2)

- **Stable key** for each message: `mailbox/folder/number`, such as `giron-d/inbox/20`. Store both the original path (`corpus/giron-d/inbox/20.`) and the local path, and accept both file-name styles.
- **Fields:** key, original_path, local_path, message_id, date_raw, date_utc, date_status (ok / missing / invalid / out_of_range), from, to, cc, subject, body_raw, new_text, quoted_blocks, body_hash, duplicate_of, thread_key, size_bytes, special_handling, parse_status.
- **Dates:** never invent one. A date outside the corpus window (1999–2002) is `out_of_range`. Undated messages still count as events but are left out of date spans.
- **Duplicates:** hash the whitespace-normalized body. Later copies get `duplicate_of = first key`. Duplicates stay in the ledger but never count as separate events.
- **Quoted text:** split each body into new text and quoted or forwarded blocks. If a quoted block's text already exists as its own message in the corpus, mark it `duplicate_of` that message and **leave it out of what Claude reads**. Otherwise keep it, because it may be the only copy. The raw files are never changed.
- **Giant or tabular messages:** if a message is over a size limit, code detects table rows, parses every row, and writes a summary (row count, status counts, date range, invalid values). Claude reads only the prose plus that summary. Any oversized message that is all prose gets split into chunks with character offsets.
- **Threads:** group messages by normalized subject (with Re:/Fw: removed), participants and time proximity. A thread is the default unit for an **event**.
- **Coverage ledger:** every one of the 3,240 files gets exactly one status: `sent_to_model`, `duplicate_of:X`, `table_parsed`, or `excluded:reason`.

### 3.2 Batching and subagents (Milestones 3–5)

- Batches are made by code, keep each thread together, and stay deterministic (the same input gives the same batches). Every non-duplicate message lands in exactly one batch.
- Batch size, model choice and concurrency are **set from the Milestone 3 measurement**, not guessed.
- Subagents, defined in `.claude/agents/`:

| Agent | Job | Model (to confirm in M4) | Tools |
|---|---|---|---|
| extractor | Reads one batch and writes observations JSON | smaller, faster model (Haiku or Sonnet) | Read; Write to `data/extract/` only |
| synthesizer | Groups observations into processes and proposes the minutes-saved assumptions with reasons | stronger model | Read; Write to `data/synth/` |
| reviewer | Checks that each candidate claim is really supported by its quotes | stronger model | Read; Write to `data/review/` |
| artifact-writer | Drafts one artifact per qualifying opportunity, with citations | stronger model | Read; Write to `output/artifacts/` |

- No subagent gets shell or web access. A hook blocks writes outside each agent's folder.
- **Observation schema** (extractor output, one per finding):
  `obs_id, msg_key, task (short verb phrase), process_hint, actors[], trigger, output, stall_signal (none | follow_up | waiting | correction | rework | escalation), quote, notes`
- **What counts as a process:** a repeated task with a trigger, the people involved and an output, seen in at least 2 threads. "Emails about gas" is not a process.
- **Synthesizer context budget:** it reads a compact list, one line per observation, never the raw emails. If that list is too big for one context, synthesize per mailbox first, then merge.

### 3.3 Verification (code, Milestones 3–5)

- **Quote check:** the quote must appear in its message's raw file. Matching ignores whitespace differences, then code stores the **exact raw text** with its start and end offsets, so the stored quote is literally in the file. Quotes that fail are dropped and logged in `output/logs/rejections.jsonl`.
- **Canary:** if more than 20% of quotes in the first batch of a run fail, stop the run and log why instead of burning the rest of the usage.
- **Publish gate:** nothing reaches the report or dashboard without verified evidence.
- **Numbers lint:** model-written prose is flagged if it contains numbers. All published numbers come from code.

### 3.4 Scoring (code, Milestone 6)

- **Formula:** hours/month = events/month × minutes saved per event ÷ 60, and $/month = hours/month × $85.
  Worked example (made-up numbers): 40 events/month × 30 min = 1,200 min ÷ 60 = 20 h; 20 h × $85 = **$1,700/month**.
- **Events:** count distinct threads or instances, never raw emails. Replies within one thread are one event.
- **Months observed:** by default, the span of dated messages in the mailboxes where the process appears. Confirm this in M1 and document it.
- **Assumptions table** (`data/assumptions.csv`): the minutes saved per event, a low/high range and the rationale. The synthesizer proposes these values and you can override them in `config/assumption_overrides.csv`. Each assumption is labeled *assumed*, while counts are labeled *observed*.
- **Confidence:** high, medium or low, set by a code rule based on the number of events, threads, people and date coverage.
- **No double counting:** if two opportunities share events, each event counts toward the running total only once, assigned to the higher-ranked opportunity.

### 3.5 Review (subagent, Milestone 6)

The reviewer runs **once per run, after scoring and before artifacts**, so no artifact gets drafted for an unsupported claim. It reviews only candidate claims, usually 10–20, never every observation. Code drops anything marked "not supported" and logs it.

### 3.6 Artifacts (Milestone 7)

- **Threshold (to set after the first ranked list):** a starting idea is **≥ $1,000/month AND ≥ 3 verified emails from ≥ 2 threads**. Choose the value so it yields about 3–6 artifacts. It lives in `config/settings.yaml` and NOTES.md defends it.
- The writer picks the artifact type (SOP, checklist, draft email or template), says why, and cites messages in a fixed format that code verifies.

### 3.7 Report and dashboard (Milestone 8)

- `output/report.md` is generated by code from a template. Model-written narrative is slotted in, but code inserts every number.
- The dashboard is a Python server (Flask or FastAPI) plus HTML/CSS/JS, serving JSON from `output/`.
- **Drill-through chain:** $ figure → calculation with its inputs → counted events → messages → verbatim quotes → raw email with the quote highlighted and its original file name.
- Pages: overview (running total, ranked list), opportunity detail, process detail, message view, coverage (3,240 ledger, duplicates, date problems, table-parsed messages), artifacts.
- Quality bar: something you'd put in front of a client's president.

### 3.8 One command and unattended running (Milestone 9)

- `.claude/commands/observe.md` is the recipe: run each stage in order, skip cached work, write a timed run log to `output/logs/`, then serve the dashboard.
- It works both as `/observe` inside Claude Code and as `claude -p "/observe"` from a terminal.
- `.claude/settings.json` pre-approves every command the run needs and denies web and shell access to the subagents, so nothing stops to ask permission.
- **Open issue:** a server started during a headless run may stop when the run ends. Test this in Milestone 3 and pick one approach: either `/observe` starts a detached server, or a tiny `run.ps1` / `run.sh` runs `claude -p "/observe"` and then the server.

### 3.9 Caching (Milestone 5)

Every stage saves its output under a fingerprint of its inputs plus the prompt version, model and config. A rerun on an unchanged corpus skips every cached stage, and the run log shows the cache hits. If a prompt or setting changes, only the affected stages rerun.

---

## 4. Pro plan budget rules

- Pro has a **five-hour session limit and a weekly limit**, shared across claude.ai chats, Claude Code and the desktop app.
- The README says "wanting to buy tokens means the design went wrong", so the design must fit Pro.
- Develop on the dev slice (about 200 messages). Ask before any full-corpus model run.
- Measure usage with `/usage` after the one-batch test, then estimate the full run before scaling.
- Use the smaller model for bulk extraction; the code quote check catches its mistakes.
- Caching is the insurance: if a run hits the limit, finished batches are kept.
- Do the official run at the **start of a fresh five-hour window**.

---

## 5. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | **Setup.** Claude Code logged in with Pro and `ANTHROPIC_API_KEY` unset; Python environment; `requirements.txt`; CLAUDE.md and PLAN.md committed; `private/DEVLOG.md` kept locally (git-ignored, never committed) | `/status` shows your Pro login; the first `private/DEVLOG.md` entry exists |
| M1 | **Explore** (notebook, `notebooks/01_explore.ipynb`). Re-confirm the section 2 facts; decide the rules for dates, duplicates, quoted text, threads and months observed | Section 2 and the 3.1 rules are confirmed or updated in this file |
| M2 | **Ingest + ledger + tests** (`src/ingest.py`, `dedupe.py`, `quoted.py`, `tables.py`, `threads.py`, `ledger.py`) | The ledger shows 3,240/3,240; `pytest` passes; the giant message's 3,600 rows are parsed |
| M3 | **Thin slice, end to end, on one batch.** Batcher → 1 extractor run → verifier → mini synthesis → scoring → 1 artifact → basic dashboard with drill-through. **Measure usage with `/usage` and time it.** Test whether the server survives a headless run | One opportunity clicks through to a raw email; the usage and time per batch are written in `private/DEVLOG.md` |
| M4 | **Budget decision.** From the M3 numbers, set batch size, model per agent and concurrency; estimate the full run against the Pro window | The estimate and decision are written in `private/DEVLOG.md` (go or adjust) |
| M5 | **Full pipeline.** All batches in parallel, caching, canary, retries, verify hook | A cold run covers every batch; an immediate rerun skips them all |
| M6 | **Synthesis, scoring, review.** Processes, assumptions table, confidence, overlap rule, reviewer before artifacts | The ranked list is fully computed by code and every claim has verified evidence |
| M7 | **Artifacts.** Set the threshold and draft the artifacts | 3–6 artifacts exist and all of their citations pass |
| M8 | **Dashboard polish** | Every number drills through to a raw email, and it looks client-ready |
| M9 | **One command + unattended.** `/observe`, `claude -p "/observe"`, permissions, server, timing | A full run finishes in under 30 minutes with no prompts |
| M10 | **Self-audit + fresh checkout.** `src/audit.py` picks 20 random claims and re-checks them against the raw files; test a fresh clone, including the original trailing-dot names | The audit passes 20/20; a fresh clone runs |
| M11 | **Real run + NOTES.md + push.** Do the official run in a fresh five-hour window; commit outputs and logs | Everything in section 1 is pushed |

---

## 6. Tests (all offline, no model)

- Parser: headers, missing date, impossible date (Feb 32, 25:61), out-of-range date (2031)
- Dedupe: body-hash duplicates vs Message-ID duplicates
- Quoted text: split, matching against originals in the corpus, keeping blocks with no original
- Table parser: the giant message gives 3,600 rows; invalid flow dates are flagged
- Threads: Re:/Fw: normalization, grouping
- Batcher: every non-duplicate message lands in exactly one batch
- Verifier: exact match, whitespace-normalized match with recovered raw text and offsets, fabricated quote, quote from the wrong message
- Scoring: the worked examples, events vs emails, overlap allocation, threshold rule
- File names: `20.` and `20.txt` resolve to the same message
- Ledger: totals 3,240
- Server: drill-through endpoints return the message with the quote offsets

---

## 7. NOTES.md outline (2 pages max, written from `private/DEVLOG.md`)

1. How Claude Code drove the system (the `/observe` flow)
2. What went to subagents and why; context budgets; what ran in parallel
3. Where Claude Code got it wrong and how each mistake was caught (from `private/DEVLOG.md`)
4. Threshold choice and defense
5. Where the system is most likely wrong (minutes-saved assumptions, process grouping, stall detection, months observed)
6. What it took on faith (for example, that email volume reflects the real workload, and that undated messages belong to the same period)
7. What we'd build next with another week

---

## 8. Risks

| Risk | Handling |
|---|---|
| Full run doesn't fit one Pro window or 30 minutes | Trimming, smaller extraction model, measure in M3, caching to resume |
| Made-up or altered quotes | Code verifier, canary, publish gate |
| Counting emails instead of events | Threads as events; tests |
| Hours estimates are guesses | Visible assumptions with ranges and confidence labels |
| Vague processes | Process definition in 3.2; reviewer |
| Double-counted savings | Overlap allocation rule |
| Instructions hidden inside emails | Untrusted text; minimal subagent tools |
| Dashboard dies after a headless run | Tested in M3; decided in M9 |
| Grader's file names differ from ours | Parser accepts both; citations use original names |

## 9. Open decisions

- The artifact threshold value (after the first ranked list)
- The months-observed rule (confirm in M1)
- The extraction model, batch size and concurrency (from M3–M4)
- How the server stays up (from the M3 test)
