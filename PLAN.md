# Observation Agent: build plan

Status: agreed plan, written before any code (20 Nov 2026). M1 done 21 Nov 2026: section 2 re-measured in code and the section 3.1 ingest rules approved. Work through the milestones in order. Each one ends with a "done when" check, and you report back at each check.

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

## 2. Facts about the corpus (measured 20 Nov 2026; re-measured in code 21 Nov 2026)

Every number here is computed in `notebooks/01_explore.ipynb` (section numbers like §14f refer to it). M1 re-confirmed 14 of the 18 earlier figures exactly, corrected the quoted-text share and the date counts, and added the duplicate, clock-shift, FLOW_DATE, header and mailbox findings.

- **3,240 files, 6.85 MB, roughly 1.7 million tokens.** The median email is 1.4 KB. Every file is plain ASCII, has a header/body break and has a non-empty body. **Headers are 38.9% of the bytes:** `giron-d/inbox/20.`, `williams-w3/inbox/45.` and `giron-d/inbox/390.` are 30–50 KB of To/Cc lists with bodies of about 3 KB.
- **Folders:** farmer-d/logistics 1,194 · giron-d/inbox 380 · giron-d/sent 789 · lokay-m/inbox 57 · lokay-m/sent_items 161 · williams-w3/inbox 139 · williams-w3/sent_items 520.
- **File names:** the original files end in a period (`20.`), which Windows can't handle. Our copy renames them `20.txt` with the content unchanged. `corpus/MANIFEST.csv` maps each new name to its original name with a SHA-256 hash, and all 3,240 hashes match. `corpus/.gitattributes` stops Git from changing line endings, which matters because 3,135 files have mixed line endings.
- **No threading headers:** no message has `In-Reply-To` or `References`. Threads have to be inferred (§10, §14c).
- **Duplicates (§5):** Message-ID finds 1 duplicate. Matching whitespace-normalized bodies finds 49 extra copies in 42 groups, but **only 40 are real copies**. The 9-copy group is the daily "Calpine Daily Gas Nomination" notice, the same template sent on 9 different days between Jul 2000 and Apr 2001: 9 events, not copies. The real copies are an export artifact 10 or 11 hours apart (28), the same email in two mailboxes (6), re-sends within an hour (5), or a shared Message-ID (1).
- **Unusable dates (§4):** `giron-d/sent/795.` has no Date header. `williams-w3/inbox/142.` reads "Tues, 32 Febuary 2001 25:61:00". `lokay-m/inbox/74.` is dated 2031. `lokay-m/inbox/50.` reads "Sat, 26 Nov 0001": year 1, and the weekday is wrong for both 0001 and 2001. Python's `parsedate_tz` silently turns year 0001 into 2001, which is how the first count missed it. The other 3,236 are good: 1999 (54), 2000 (1,219), 2001 (1,496), 2002 (467), running from 13 Dec 1999 to 25 Mar 2002. Every year is written with 4 digits, and every good date's weekday name matches its date. Every good date has an explicit offset, either -0800 or -0700.
- **Clock shift (§14f):** older Date headers run 7–8 hours behind the sender's clock. In 572 of 613 testable messages (93.3%), the header is exactly its own Pacific offset behind the time on the Lotus "Forwarded by … on" line. The testable messages are fresh forwards, where the forward line shows the send time. This covers farmer-d from Jan 2000 to Mar 2001 and giron-d from Jun 2000 to May 2001; later mail matches. It looks as if the export stored the sender's local time as UTC and then printed it as Pacific. **On these messages the UTC calendar date is the sender's real date, but the hour is not the real hour.**
- **The giant message (§6):** `farmer-d/logistics/3221.` is 350 KB (about 88,000 tokens, 5.1% of the corpus). It holds two sentences of prose plus **3,600 rows** of a gas position table. **FLOW_DATE is day/month for the whole column:** all 3,600 values are valid as day/month, 2,160 are impossible as month/day, and 720 could be read either way. SCHEDULED − ACTUAL = VARIANCE in every row. It is the only message whose body is over 64,000 bytes; the next largest body is 26.5 KB.
- **Replies and forwards (§7):** 1,276 subjects start with Re:/Fw:. **1,640 messages (59.2% of body text) contain quoted or forwarded text** once Lotus Notes and Outlook reply headers are counted (M1 notebook: 1,630; M2 added 3 Lotus replies with two-digit-year headers; the review added 7 forwards with `-----Original Appointment-----` or a Sent-less `From:`/`To:`/`Subject:` block). The earlier 1,437 messages and 53% counted only "Original Message" and "Forwarded by" lines. Only 7.5% of quoted text is a copy of a message already in the corpus, and re-quoted thread history adds 6.0% of body text (M1 estimates; the block-level figures the code actually skips are in 3.1 R3). Most quoted blocks carry their own readable date (95.1% of blocks, measured in M2). **54 messages are quoted-printable encoded** (`Content-Transfer-Encoding: quoted-printable`), so their raw bodies hold `=20`, `=3D` and `=` soft line breaks; they are kept raw (see 3.3). Quoted Lotus lines print two-digit years (`12/15/99`): 99 appears 115 times, 00 seven times and 01 21 times, all inside the corpus window.
- **Main senders:** darron.giron 789, bill.williams 528, michelle.lokay 166. Each sent folder holds only its owner's mail.
- **Mailboxes barely overlap (§14e):** at most 0.9% of one mailbox's mail involves another mailbox's owner. Date windows are farmer-d 24.06 months, giron-d 20.00, lokay-m 5.92 and williams-w3 9.05; all four together cover 27.39.
- A code search found **no planted AI instructions** in the emails (§9), but we still treat all email text as untrusted.

Update this section if anything changes.

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

### 3.1 Ingest rules (Milestone 2; approved 21 Nov 2026 from the M1 measurements)

The numbers after each rule are what it produces on this corpus (measured by the M2 code). M2 tests assert them.

- **One coordinate system: raw-byte offsets.** Every offset stored anywhere is a byte offset into the original file. All stages read evidence through `src/rawio.py`: raw bytes, no newline translation (text mode would turn CRLF into LF and shift offsets in 3,231 files, by up to 771 characters), names like `20.` reachable on Windows through the `\\?\` prefix. The corpus is all ASCII, so character offsets equal byte offsets; reconciliation fails if that ever stops being true.
- **No silent readings (applies to every rule).** Whenever code reads text as something other than exactly what is written, or finds date-like text it can't read, it logs a reading: the message, the raw-byte offsets of the text, the text as written, what it was read as, the rule, why, a **certainty** (`exact` for a lossless decoding such as `=09` → tab; `inferred` for a judgment that could be wrong, such as a two-digit year's century, a column's day/month order or an address inside a malformed entry) and an **outcome** (`read`, or `not_read` with the reason). Readings go in the `readings` table, each ledger row counts its message's readings, and the dashboard shows them on the message and coverage pages. The raw text is never changed. Expected: 185 readings in 80 messages: 127 two-digit years, 31 quoted-printable leftovers, 12 recovered addresses, 4 table-column decisions, and 11 quoted dates not read. Values that are only re-expressed with every input stored aren't readings: a header date converted to UTC beside its raw string and offset, lower-cased addresses, normalized subjects and whitespace used only as matching keys, and a quoted date used to the day exactly as its writer printed it (41 quoted RFC `Date:` lines carry a zone, which is not applied).
- **Stable key** for each message: `mailbox/folder/number`, such as `giron-d/inbox/20`. Store both the original path (`corpus/giron-d/inbox/20.`) and the local path, and accept both file-name styles. The raw files and the stored body are never changed or trimmed.
- **Fields:** key, original_path, local_path, mailbox, folder, message_id, date_raw, tz_offset, date_utc, date_status, clock_check, from, to, cc (full lists), subject, subject_norm, is_root, body_raw, new_text, quoted_blocks (each with start/end offsets into the raw file, its own from and date when present, the date text as written with its offsets, and any readings), readings, body_hash, duplicate_of, duplicate_reason, seen_in_mailboxes, thread_id, size_bytes, body_bytes, special_handling, chunks, parse_status, analysis_status. Bodies are not copied into the database: every text field is an offset range into the raw file.
- **Addresses:** From/To/Cc are parsed with Python's `getaddresses`, which silently drops malformed entries left by the export (`".'"wehner@enron.com>`, X.400 `/o=enron/…/cn=…@enron.com`). Any address the raw field visibly contains but the parser dropped is kept and logged as an `address_recovered` reading. Expected: 12 in 4 messages.

**R1. Dates**
- Parse with `email.utils.parsedate_tz`, then build a `datetime` from the parts, which raises on impossible values such as day 32 or 25:61. Python's date helpers repair bad input in three ways, and code must block all of them (`src/dates.py`):
  - **Never use `mktime_tz`:** it silently rolls "32 Feb 2001 25:61" forward to 5 Mar.
  - **Take the year exactly as written** in the header, never `parsedate_tz`'s year: it treats any year below 100 as two-digit, so "26 Nov 0001" becomes 2001.
  - **Read the zone from the header text:** `parsedate_tz` reports a missing or unknown zone as offset 0, the same as UTC. Only a written `+hhmm`/`-hhmm` or a standard name (UT, GMT, EST, …) counts as an offset (found in M2).
- **The weekday name must match the date**, or the date is `invalid`. Only `lokay-m/inbox/50.` fails this check, so it adds no false positives.
- `date_status` is `ok`, `missing`, `invalid`, `out_of_range` (a written year outside 1999–2002) or `no_timezone`. `ok` requires an explicit offset; code never assumes UTC. Store `date_raw`, `tz_offset` and `date_utc`. Expected: 3,236 ok, 1 missing, 1 invalid, 2 out of range (`lokay-m/inbox/74.` 2031, `lokay-m/inbox/50.` 0001), 0 no_timezone.
- Dates inside quoted headers (R3) go through the same strict parser: Outlook `Sent:` lines (weekday checked), RFC `Date:` lines, and Lotus `MM/DD/YYYY HH:MM AM` lines. Two readings are allowed there, and each is logged:
  - **Two-digit years in quoted Lotus lines** (`12/15/99 08:40 AM`): Lotus Notes prints years this way. Only 99, 00, 01 and 02 are read (as 1999–2002, the corpus window); any other two-digit year gives no date. Expected: 127 readings, of years 99, 00 and 01 only. The message's own Date header never gets this reading: there the year is still taken as written.
  - **Quoted-printable leftovers** (`Sent:=09Wednesday, …`): `=09` and `=20` are read as spaces. Expected: 31.
  - **Dates that can't be read are logged, not dropped**, with the reason (no time, Spanish weekday, `20019:42 PM`, wrong weekday, two-digit year outside 99–02). Expected: 11, each `not_read`. Nothing is guessed.
- **Months are counted by UTC date.** Only 11 messages (0.3%) would land in a different month under header-local time, and on clock-shifted mail the UTC date is the sender's real date.
- **Timestamps are never corrected.** `clock_check` records `shifted` (the message's own fresh Lotus forward line shows the 7–8 hour shift), `matches`, or `untestable`. Expected: 572 shifted.
- **Durations are measured in whole calendar days.** Anything under a day is "same day"; no published number uses hours between messages.
- A message without an `ok` date still counts as an event, but it is left out of date windows and durations.

**R2. Duplicates**
- Candidates: identical whitespace-normalized body (42 groups, 49 extra copies).
- A candidate is merged only if it matches the group's **canonical** message, never another copy (so no chains), has the same subject after Re:/Fw: is stripped, and fits one named `duplicate_reason`:
  - `same_message_id` (1)
  - `export_timezone_copy`: same mailbox and sender, exactly 10 or 11 hours apart (28)
  - `same_mail_in_two_mailboxes`: at most 12 hours apart (6)
  - `resend_within_1h`: same sender (5)
- Anything else stays a separate message, and the merge log says why: `ambiguous_duplicate` when the subject matches and the two are at most 12 hours apart yet no reason fits (0 today), `separate_send` when they are further apart (9 today), `different_subject`, or `undated`. Expected: **40 copies**. The 8 Calpine notices and `williams-w3/inbox/37.` ("Ken Lay Resigns", sent again 18 hours later from another address) stay separate.
- Recipient overlap is recorded, not required: 7 real copies share no recipients (broadcasts to different list aliases or with no To/Cc at all, and a re-send to a corrected address).
- The canonical is the lowest key. Each copy records its reason, the gap in hours and both dates. The canonical's `seen_in_mailboxes` lists every mailbox the email appeared in (copies add a mailbox for 7 canonicals), and R7 uses it.
- Copies stay in the ledger and the merge log (shown on the coverage page) but never count as separate events.

**R3. Quoted text**
- Split each body at the first of seven markers: `-----Original Message-----` (or `-----Original Appointment-----`), `Forwarded by`, Lotus `From: … on <date>`, Lotus `<name> <date>` + `To:` header, Outlook `From:`/`Sent:` block, `>` lines, and an internet header block with no `Sent:` line (`From:`, then To/Cc/Date or folded lines, then `Subject:`). The Lotus header marker accepts two-digit years too (`Howard B Camp / 12/16/99 12:27 PM / To:`); spotting a header needs no reading of the year. Prose that merely starts a line with "From:" or "Subject:" is not a marker. Expected: 1,640 messages with quoted text, 59.2% of body text (M1's 1,630 missed three 1999 Lotus replies and seven forwards in the two formats added by the review). Each quoted block keeps its start and end offsets in the raw file, and the blocks together cover the quoted text exactly.
- **Block boundaries:** a new block starts at every marker, except that a run of consecutive `>` lines is one block, and a marker line with nothing after it but header lines (such as `-----Original Message-----` before its `From:`/`Sent:` lines) joins the next block, so each block keeps its own header. Expected: 3,968 blocks in unique messages.
- **What Claude reads** leaves out a quoted block (80+ normalized characters) only when the same text is already in front of Claude **in the same thread** (batches keep a thread together): (a) its whole text is inside the new text of another message in the same thread (`original_in_thread`), or (b) the same block was already shown, not skipped, earlier in the same thread (`repeat_in_thread`). Expected: (a) 143 blocks, 3.6% of quoted text; (b) 345 blocks, 5.8% of body text. A whole block must match, which is the safe direction. Rule (a) finds an original only when the block starts where the original's new text starts (a 64-character prefix index), so it under-detects; that keeps more text, never less. Everything else is kept: 1,317 of 2,736 threads start with a reply whose original is not in the corpus, so quoted text is often the only record.
- **Quoted text that also appears in another thread is kept and linked** (approved 21 Nov 2026 after the review). When a block's original is in a *different* thread, skipping it would leave Claude reading the reply without the text it answers (27 such replies have under 200 characters of their own), so it is kept; this adds about 28,000 characters. Each kept block whose text also appears elsewhere carries `links` to every other occurrence in other threads (the original's new text, or a kept quoted copy) and a `first_seen` anchor shared by every copy of that text: the earliest original when any copy found one, else the earliest quoted copy. A skipped repeat drops its links, because the copy shown earlier in its thread carries them. Expected: 193 linked blocks under 90 anchors; 38 kept because their original is in another thread, 162 with copies kept in other threads. Reconciliation checks that every skip refers to its own thread, every link is real, crosses threads and (for copies) goes both ways, and every copy of one text shares one anchor.
- Each quoted block carries its own From and date when its header has them, stored with the date text exactly as written and its file offsets: 95.1% of blocks with 80+ characters do (M1's 96.6% counted line fragments, not blocks). Nearly all of the undated ones are `>` quotes, which carry no header. **An event found only in quoted text takes the quoted date, to the day**, not the date of the message holding it (179 blocks are more than 4 weeks older than their message, mostly 1999 Lotus threads forwarded in 2000). Quoted header times have no offset, so they are used only to the day.
- Citations always name the file that actually contains the quote, with offsets into it.
- **Header trimming for Claude:** Claude sees Date, From, Subject and the first 10 To/Cc addresses plus a total count. The database keeps everything (headers are 38.9% of corpus bytes).

**R4. Giant or tabular messages**
- A message gets special handling if its **body** (not the file) is over **64,000 bytes**. Expected: 1 (`farmer-d/logistics/3221.`, body 349,596 bytes; the next largest body is 26,506).
- It is treated as a table if at least 50% of its non-blank lines are table-shaped (this one: 99.9%). Code parses every row and keeps the row number and character offsets. Claude reads only the prose plus a code-written summary (row count, status counts, date range, invalid values).
- Each column's format is decided once for the whole column, never per value, and every decision is logged as a reading with its reason. FLOW_DATE is day/month ("2,160 of 3,600 values are impossible as month/day and none as day/month"). SCHEDULED, ACTUAL and VARIANCE read commas as thousands separators and `+`/`-` as the sign. A column that is valid both ways is kept as written, never guessed.
- **A table row is a data record, never an event.** The message itself is one event.
- Cross-checks run in code: 3,600 rows; SCHEDULED − ACTUAL = VARIANCE in all of them.
- A body over the limit that isn't a table is split into consecutive chunks of at most 32,000 characters, cut at a blank line when possible, else at a line end, else hard; the chunks cover the body exactly (checked in reconciliation). None in this corpus, but tested.
- Only the first header-plus-dashes table in a message is parsed; a second table would be read as prose (none in this corpus).

**R5. Threads (conversations)**
- A **root** is a message with no Re:/Fw: prefix and no quoted text. **A root always starts a new thread.**
- A reply or forward joins the most recent open thread when the subject matches (Re:/Fw: stripped, lowercased, spaces collapsed), it shares at least one From/To/Cc address with the thread, and the thread's last message is no more than 14 days earlier (whole days elapsed, rounded down: 14 days 23 hours still joins, 15 days does not). Otherwise it starts its own thread. Counting calendar days instead would give 2,737 threads; this gap only groups messages and is never a published duration.
- Messages with an empty subject (107) never join a thread. An undated reply joins only a same-subject thread that shares an address; an undated root starts its own.
- Expected: **2,736 threads**, 348 of them with more than one message; the 18 Calpine notices make 18 threads. Sensitivity: a 3-day limit gives 2,801 and a 30-day limit 2,717. **History:** the first M1 draft rule had no root rule and gave 2,667 threads; it chained 137 separate sends (such as daily notices) into 49 threads. Re-running that draft rule on the current data reproduces 2,667 exactly, so the 69-thread difference is entirely the root rule.
- Why 14 days is defensible: 81.6% of consecutive same-subject gaps are 14 days or less, and moving the limit changes the count only a little: 3 days +2.4%, 7 days +1.0%, 30 days −0.7% (re-measured with the M2 code). Every thread is checked directly in a test: one subject, no root after the first message, every joiner shares an address with earlier members, no gap over 14 days.
- Every thread is marked `inferred`, since the corpus has no threading headers.

**R6. Files, messages, threads, events, processes**
- The chain is file (3,240) → unique message (3,200) → thread (2,736) → event → process. Each level has a stable ID, and none is used in place of another.
- Claude records observations; **code builds events**. An event is one occurrence of one process: by default a thread gives at most one event per process. Its date is the date of its earliest evidence (the quoted date when the evidence is quoted text).
- Reconciliation invariants (tested in M2): 3,240 = 3,200 + 40; every unique message belongs to exactly one thread; 3,200 − 2,736 = 464 messages joined an earlier thread.
- **Quoted copies count once.** When an observation's quote lies in a quoted block that has a `first_seen` anchor (R3), code attributes it to that anchor, so every copy of the same quoted text in different threads adds up to one event, not one per thread. Observations from the anchor message's own new text and from its linked copies merge into that event.
- Known limit (for NOTES.md): one piece of work that continues under a new subject counts twice *unless* the later message quotes the earlier one (then the R3 links tie them together).

**R7. Months observed (the events/month denominator)**
- Divide by the **union** of the date windows (first to last `ok` date) of the mailboxes in the process's events' `seen_in_mailboxes`, in days ÷ 30.4375. Overlapping windows are merged, never added. Windows: farmer-d 24.06, giron-d 20.00, lokay-m 5.92, williams-w3 9.05, all four 27.39.
- Why per mailbox: at most 0.9% of one mailbox's mail involves another mailbox's owner, so each mailbox shows one person's mail during its own window. Dividing by the whole archive would understate a lokay-only rate 4.6 times and a williams-only rate 3.0 times.
- Selection-bias guard: no rate is published for a process below the minimum evidence (by default the artifact rule in 3.6: at least 3 verified emails from at least 2 threads). Every rate is shown with its mailboxes and months, for example "12 events in lokay-m over 5.92 months".
- Rates are labelled "observed in these mailboxes". Extending them to the whole company is an assumption, never an observed fact.
- A window can be zero or tiny (a mailbox with one dated message). Rate code (M6) must refuse a denominator under 1 month and flag it instead of dividing (none in this corpus; the smallest window is 5.92 months).

**R8. Coverage ledger**
- **Ingest status**, one per file (3,240): `unique` (3,200) or `duplicate_of:X` with its reason (40); `excluded:reason` only if a parse fails (none expected). The giant message is `unique` with `special_handling = table_parsed`, and its prose is still read.
- **Analysis status**, one per unique message (3,200): `batched` → `read` or `failed`. A message is `read` only when its batch output passes the schema check and lists that message's key (see the read receipt in 3.2).
- A failed batch is retried at most 2 times and is never counted as read. A run whose ledger has any unique message not `read` fails its coverage check and says which messages.
- **The ingest stage can't look complete when it isn't.** Its cache fingerprint covers the corpus bytes, every file name, `MANIFEST.csv`, `INGEST_VERSION` *and the source of every module in `src/`*, so a code change always rebuilds. The database records whether reconciliation passed and the hash of the ledger it wrote. A cache hit needs all three to match. A failed run writes its outputs marked failed, exits 1, and is rebuilt (and fails again) next time. The ledger and database are each written atomically, in an order where a crash leaves a hash mismatch that forces a rebuild.

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
- **Read receipt:** each batch output also lists every `msg_key` in the batch, including messages with no observations. Code checks it against the batch manifest before marking any message `read` (3.1 R8).
- **What counts as a process:** a repeated task with a trigger, the people involved and an output, seen in at least 2 threads. "Emails about gas" is not a process.
- **Synthesizer context budget:** it reads a compact list, one line per observation, never the raw emails. If that list is too big for one context, synthesize per mailbox first, then merge.

### 3.3 Verification (code, Milestones 3–5)

- **Schema check first:** every model output is validated against its JSON schema before anything else uses it. Invalid output fails the batch, which is retried under 3.1 R8.
- **Quote check:** the quote must appear in its message's raw file, read through `src/rawio.py` (raw bytes, never text mode). Matching ignores whitespace differences, then code stores the **exact raw text** with its raw-byte start and end offsets, so the stored quote is literally in the file. Quotes that fail are dropped and logged in `output/logs/rejections.jsonl`.
- **Quoted-printable fallback (54 encoded messages):** Claude reads raw text, so a faithful quote matches. If a quote fails both the exact and the whitespace match in a quoted-printable message, code tries once more against the decoded text (`=20`, `=3D`, soft line breaks undone) with a decoded-to-raw offset map, stores the raw span, and logs the match as a reading. Nothing else is decoded.
- **Where the quote sits:** code uses the quote's offsets to tell whether it is in new text or a quoted block, and dates the evidence by that block's own date (3.1 R3).
- **Canary:** if more than 20% of quotes in the first batch of a run fail, stop the run and log why instead of burning the rest of the usage.
- **Publish gate:** nothing reaches the report or dashboard without verified evidence.
- **Numbers lint:** model-written prose is flagged if it contains numbers. All published numbers come from code.
- **Evidence labels:** every published statement is *observed* (a verified quote says it), *inferred* (a pattern across verified observations, such as a process, which the reviewer checks), or *estimated* (an assumption plus a code formula).

### 3.4 Scoring (code, Milestone 6)

- **Formula:** hours/month = events/month × minutes saved per event ÷ 60, and $/month = hours/month × $85.
  Worked example (made-up numbers): 40 events/month × 30 min = 1,200 min ÷ 60 = 20 h; 20 h × $85 = **$1,700/month**.
- **Exact arithmetic:** all money and hours math uses Python `Decimal`, never floats, and rounds only for display: dollars to the cent (half-up), hours to 0.1 (half-up). Float `round()` lands a cent off in 115 of 10,080 realistic cases; for example, 1 event × 30 min over 20 months gives $2.125, which should show as $2.13 but float rounding gives $2.12. Totals are summed from unrounded values, then rounded once.
- **Events:** built by code under 3.1 R6, never raw emails: at most one event per process per thread.
- **Months observed:** the union of mailbox date windows under 3.1 R7, shown with every rate.
- **Waiting time is not work time:** stall durations (whole days, 3.1 R1) are reported only as stall metrics. Hours and dollars come only from minutes-of-effort assumptions × events, never from elapsed time. A stall threshold is at least 1 day, because shorter durations can't be measured on this corpus.
- **Assumptions table** (`data/assumptions.csv`): the minutes saved per event, a low/high range and the rationale. The synthesizer proposes these values and you can override them in `config/assumption_overrides.csv`. Each assumption is labeled *assumed*, while counts are labeled *observed*.
- **Confidence:** high, medium or low, set by a code rule based on the number of events, threads, people and date coverage.
- **No double counting:** if two opportunities share events, each event counts toward the running total only once, assigned to the higher-ranked opportunity.

### 3.5 Review (subagent, Milestone 6)

The reviewer runs **once per run, after scoring and before artifacts**, so no artifact gets drafted for an unsupported claim. It reviews only candidate claims, usually 10–20, never every observation. Code drops anything marked "not supported" and logs it.

### 3.6 Artifacts (Milestone 7)

- **Threshold (to set after the first ranked list):** a starting idea is **≥ $1,000/month AND ≥ 3 verified emails from ≥ 2 threads**. Choose the value so it yields about 3–6 artifacts. It lives in `config/settings.yaml` and NOTES.md defends it.
- The writer picks the artifact type (SOP, checklist, draft email or template), says why, and cites messages in a fixed format that code verifies.
- Artifacts are drafts only. The system never sends email, posts anything or changes any outside system.

### 3.7 Report and dashboard (Milestone 8)

- `output/report.md` is generated by code from a template. Model-written narrative is slotted in, but code inserts every number.
- The dashboard is a Flask server plus HTML/CSS/JS, serving JSON from `output/`. It binds to 127.0.0.1 only.
- The overview shows each opportunity's own estimate and, separately labelled, the overlap-adjusted running total (3.4).
- **Drill-through chain:** $ figure → calculation with its inputs → counted events → messages → verbatim quotes → raw email with the quote highlighted and its original file name.
- Pages: overview (running total, ranked list), opportunity detail, process detail, message view, coverage (3,240 ledger with ingest and analysis status, the duplicate merge log with reasons, date problems and clock checks, table-parsed messages), artifacts.
- Quality bar: something you'd put in front of a client's president.

### 3.8 One command and unattended running (Milestone 9)

- `.claude/commands/observe.md` is the recipe: run each stage in order, skip cached work, write a timed run log to `output/logs/`, then serve the dashboard.
- It works both as `/observe` inside Claude Code and as `claude -p "/observe"` from a terminal.
- `.claude/settings.json` pre-approves every command the run needs and denies web and shell access to the subagents, so nothing stops to ask permission.
- **Open issue:** a server started during a headless run may stop when the run ends. Test this in Milestone 3 and pick one approach: either `/observe` starts a detached server, or a tiny `run.ps1` / `run.sh` runs `claude -p "/observe"` and then the server.

### 3.9 Caching (Milestone 5)

Every stage saves its output under a fingerprint of its inputs plus the prompt version, model and config. A rerun on an unchanged corpus skips every cached stage, and the run log shows the cache hits. If a prompt or setting changes, only the affected stages rerun. Extraction is cached **per batch**, keyed by the hashes of the batch's messages plus the prompt version and model, so changing one email reruns only its batch.

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
| M2 | **Ingest + ledger + tests** (`src/ingest.py`, `dates.py`, `dedupe.py`, `quoted.py`, `tables.py`, `threads.py`, `windows.py`, `ledger.py`, `rawio.py`, `textutil.py`; run with `.venv\Scripts\python -m src.ingest`) | The ledger shows 3,240/3,240; `pytest` passes; the giant message's 3,600 rows are parsed |
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

Small fixtures test each rule; corpus-level tests (still offline) assert the expected numbers in 3.1.

- **Tests import the real `src/` modules.** A test file never defines its own copy of the code it tests.
- **Each fixture must fail for the reason it names.** Example: a "next-day notice" fixture keeps the same subject, so only the time check can reject it. Where it's cheap, a test also confirms that removing the rule makes it fail.

- **Independent checks on the real corpus**, not just pinned counts: a second date parser (strptime with `%z`) agrees on all 3,236 good dates; every stored offset resolves to the same text in the raw bytes on disk; every thread is re-checked against the R5 rule definition; every merge is re-derived from raw fields; two builds are identical; the database, ledger CSV and in-memory result reconcile.
- **Stage safety:** a failed run is never reused as a cache hit; a replaced or half-written ledger forces a rebuild; a code change changes the fingerprint; stray files and the manifest are in the fingerprint.
- Boundaries: duplicates at exactly 12 h vs 12 h + 1 s, re-send at 59:59 vs 1:00:00, export copy at exactly 10 h vs 10 h + 1 s; thread gap at 14 d 23 h vs 15 d; two-digit years 98 vs 02; window edges by written year; leap days; full weekday names.
- Readings: recovered addresses point at the raw header text; clean headers log nothing; unread dates are explained; certainty and outcome are set on every reading.
- File discovery: a real `20.` file is found and read on every platform, including Windows; files at the wrong depth are reported as ignored.
- Parser: headers; missing date; impossible date (Feb 32, 25:61 → `invalid`, never rolled forward to March); out-of-range dates (2031, and year 0001 never read as 2001); weekday name that contradicts the date → `invalid`; no offset → `no_timezone`; valid date converts to UTC (e.g. "Mon, 13 Dec 1999 08:00:00 -0800" → 16:00 UTC); the real raw strings of the 4 problem messages; corpus: 3,236 ok
- Clock check: a fresh Lotus forward line 8 h ahead of its header → `shifted`; equal → `matches`; no forward line → `untestable`
- No silent readings: a quoted `12/16/99` date is read as 1999 *and* logged with the text as written, its offsets, and why; `12/16/87` and `12/16/03` give no date; a two-digit year in the message's own Date header stays `out_of_range`; a quoted-printable `=09` is logged; every table-column decision is logged; every logged reading's offsets point at its written text; ledger reading counts add up to the log
- Dedupe: each of the four merge reasons, with the export copy given *different* Message-IDs so the 10/11-hour path runs; a same-subject, same-body notice 24.08 h later stays separate (Calpine); an 18.2 h re-send from another address stays separate; a cross-mailbox broadcast 4 h apart from a different sender alias merges; A~B and B~C never merges A with C; an unexplained candidate stays separate and is logged; corpus: 49 candidates → 40 copies
- Quoted-text links: an original in the same thread is skipped, one in another thread is kept and linked; a block never matches its own message; copies in different threads link both ways and share one anchor; the anchor prefers an original over quoted copies; a skipped repeat drops its links; a forward under a new subject keeps and links its quote end to end
- Quoted text: each of the six markers; a block is left out only when its original exists or it repeats earlier in the thread; a quoted block keeps its own date; offsets map back into the raw file
- Table parser: the giant message gives 3,600 rows; rows parsed from the real text format (`1,822`, `+1,160`) with integer arithmetic; FLOW_DATE is decided as day/month for the column; SCHEDULED − ACTUAL = VARIANCE; a broken row is reported; rows never become events
- Size limit: a body of exactly 64,000 bytes is standard and 64,001 is special; a message with ~50 KB of headers and a 3 KB body is standard (the limit uses the body, not the file)
- Threads: a root always starts a new thread, even with the same subject, a shared address and a 1-day gap; a reply joins only with a matching subject, a shared address and a gap of 14 days or less; a 15-day gap starts a new thread; an empty subject never joins; corpus: 2,736 threads
- Reconciliation: 3,240 = 3,200 + 40; every unique message is in exactly one thread
- Months observed: overlapping windows are merged, not added; per-mailbox values match 3.1 R7
- Batcher: every non-duplicate message lands in exactly one batch
- Ledger: totals 3,240; a read receipt missing a key leaves that message unread; a failed batch is never `read`
- Verifier: exact match; a quote that spans a line break or CRLF matches with whitespace normalization and returns the exact raw text and offsets; fabricated quote; one changed word or number (e.g. 10:00 vs 11:00) fails; quote from the wrong message; invalid schema
- Scoring: the worked examples (including 12 events over 6 months × 90 min = 3 h/month = $255.00); events vs emails; overlap allocation; threshold rule; stall days never turned into hours saved; months_observed ≤ 0 raises; `Decimal` half-up rounding (1 event × 30 min over 20 months → $2.13, where float rounding gives $2.12)
- File names: `20.` and `20.txt` resolve to the same message
- Server: binds to 127.0.0.1; drill-through endpoints return the message with the quote offsets

---

## 7. NOTES.md outline (2 pages max, written from `private/DEVLOG.md`)

1. How Claude Code drove the system (the `/observe` flow)
2. What went to subagents and why; context budgets; what ran in parallel
3. Where Claude Code got it wrong and how each mistake was caught (from `private/DEVLOG.md`)
4. Threshold choice and defense
5. Where the system is most likely wrong (minutes-saved assumptions, process grouping, stall detection, months observed, work that continues under a new subject counting twice)
6. What it took on faith (for example, that email volume reflects the real workload, that undated messages belong to the same period, that the clock shift holds beyond the 613 testable messages, and that four people's mailboxes say something about the company)
7. What we'd build next with another week

---

## 8. Risks

| Risk | Handling |
|---|---|
| Full run doesn't fit one Pro window or 30 minutes | Measured in the M2 review: after header trimming and quoted-text skips, Claude would still read about 4.4 M characters (~1.1 M tokens), dominated by genuine message text (repeated boilerplate is only ~61 k characters). Trimming, smaller extraction model, measure in M3, caching to resume |
| Offsets shift because a later stage reads files in text mode | One reader for every stage (`src/rawio.py`); reconciliation checks char offsets equal byte offsets; tests resolve every stored offset in the raw bytes |
| A cached or failed ingest run looks complete | Fingerprint includes the code; cache hit requires passed reconciliation and the matching ledger hash (3.1 R8) |
| Made-up or altered quotes | Code verifier, canary, publish gate |
| Counting emails instead of events | Code builds events (3.1 R6); fresh emails start new threads; reconciliation tests |
| Recurring notices merged away as duplicates | Merge only for a named reason (3.1 R2); Calpine test |
| Clock shift distorts stall times | Durations in whole days; stall thresholds of at least 1 day |
| A batch silently goes unread | Read receipts; the coverage check fails the run |
| Hours estimates are guesses | Visible assumptions with ranges and confidence labels |
| Vague processes | Process definition in 3.2; reviewer |
| Double-counted savings | Overlap allocation rule |
| Instructions hidden inside emails | Untrusted text; minimal subagent tools |
| Dashboard dies after a headless run | Tested in M3; decided in M9 |
| Grader's file names differ from ours | Parser accepts both; citations use original names |

## 9. Open decisions

- The artifact threshold value (after the first ranked list)
- Event date when the evidence sits in a quoted block with no readable date (M3/M6): proposal is the containing message's date, labelled as an upper bound- The extraction model, batch size and concurrency (from M3–M4)
- How the server stays up (from the M3 test)
