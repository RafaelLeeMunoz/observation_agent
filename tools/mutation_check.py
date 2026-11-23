"""Mutation check: break one rule at a time and confirm the test written for it catches it.

A mutant counts as CAUGHT only when all of these hold:
  1. the unmodified suite passes (checked once, first);
  2. the change is really applied: the original text occurs exactly once and the file changes;
  3. the *intended* test fails with a test failure (pytest exit code 1), not a collection or
     setup error (exit code 2+), which is reported as SETUP-ERROR and never counted.
Every result records the intended test, the exit code and the first error line. A JSON report is
written to data/logs/mutation_report.json.

Run:  .venv\\Scripts\\python tools\\mutation_check.py              (all mutants)
      .venv\\Scripts\\python tools\\mutation_check.py "M3 "        (names containing "M3 ")
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "Scripts" / "python.exe") if sys.platform == "win32" else sys.executable
COPY = ("src", "tests", "tools", "pytest.ini", "corpus", "config", "schemas", "eval", ".claude/agents")

T = "tests/"
MUTANTS = [
    # (name, file, original text, replacement, intended test node)
    ("R1 year taken from parsedate_tz", "src/dates.py", "year = int(head.group(4))", "year = t[0]",
     T + "test_dates.py::test_year_is_taken_as_written_never_repaired"),
    ("R1 weekday check removed", "src/dates.py", "if weekday is not None and", "if False and",
     T + "test_dates.py::test_weekday_that_contradicts_the_date_is_invalid"),
    ("R1 zone token not required", "src/dates.py", "if t[9] is None or not _ZONE.search(raw):", "if t[9] is None:",
     T + "test_dates.py::test_missing_offset_is_never_assumed_utc"),
    ("R1 mktime_tz-style rollover", "src/dates.py", "local = datetime(year, t[1], t[2], t[3], t[4], t[5])",
     "local = datetime(1970, 1, 1) + __import__('datetime').timedelta(seconds=__import__('calendar').timegm("
     "(year,) + tuple(t[1:6]) + (0, 0, 0)))",
     T + "test_dates.py::test_impossible_date_is_invalid_not_rolled_forward"),
    ("R2 any gap <= 12 h counts as export copy", "src/dedupe.py", "if gap in EXPORT_COPY_SECONDS:", "if gap <= 12 * 3600:",
     T + "test_dedupe.py::test_not_exactly_10_or_11_hours_is_ambiguous_not_merged"),
    ("R2 subject not compared", "src/dedupe.py",
     "if subject_norm(canonical.subject) != subject_norm(cand.subject):\n        return None", "if False:\n        return None",
     T + "test_dedupe.py::test_same_body_different_subject_stays_separate"),
    ("R2 copies compared with copies (chains)", "src/dedupe.py", "                kept.append(cand)",
     "                pass\n            kept.append(cand)",
     T + "test_dedupe.py::test_no_chains_a_copy_is_only_compared_with_canonicals"),
    ("R3 block may match its own message", "src/quoted.py", "if c != key and b.content in new_texts[c]]",
     "if b.content in new_texts[c]]", T + "test_quoted.py::test_a_block_never_matches_its_own_message"),
    ("R3 (a) skips across threads", "src/quoted.py", "same = [c for c in matches if thread_of[c] == thread_of[key]]",
     "same = matches", T + "test_quoted.py::test_original_in_another_thread_is_kept_for_context_and_linked"),
    ("R3 skipped repeat keeps its links", "src/quoted.py", "                b.links.clear()", "                pass",
     T + "test_quoted.py::test_a_skipped_repeat_drops_its_links"),
    ("R3 cross-thread copies not linked", "src/quoted.py",
     "        if len({thread_of[k] for k, _ in occurrences}) < 2:", "        if True:",
     T + "test_quoted.py::test_copies_kept_in_different_threads_link_both_ways_and_share_one_anchor"),
    ("R3 anchor ignores the original", "src/quoted.py", "        if originals:\n            anchor",
     "        if False:\n            anchor", T + "test_quoted.py::test_anchor_prefers_an_original_message_over_quoted_copies"),
    ("R3 gt lines one block each", "src/quoted.py", "                continue                   # a run of '>' lines",
     "                pass                       # a run of '>' lines", T + "test_quoted.py::test_a_run_of_gt_lines_is_one_block"),
    ("R3 two-digit Lotus header not a marker", "src/quoted.py",
     r'"lotus_name_date":  r"^[ \t]*[^\n]*?\s\d{2}/\d{2}/(\d{4}|\d{2})\s',
     r'"lotus_name_date":  r"^[ \t]*[^\n]*?\s\d{2}/\d{2}/(\d{4})\s',
     T + "test_quoted.py::test_lotus_reply_header_with_a_two_digit_year_is_a_marker"),
    ("R3 quoted header kept in block text (author line)", "src/quoted.py", "    s = _drop_quoted_header(s)\n",
     "    s = s\n", T + "test_quoted.py::test_lotus_author_line_is_not_part_of_the_block_text"),
    ("R3 quoted header kept in block text (wrapped cc)", "src/quoted.py", "    s = _drop_quoted_header(s)\n",
     "    s = s\n", T + "test_quoted.py::test_wrapped_header_lines_are_not_part_of_the_block_text"),
    ("R3 long Lotus headers not dropped", "src/quoted.py", "HEADER_MAX_LINES = 60", "HEADER_MAX_LINES = 25",
     T + "test_quoted.py::test_a_long_wrapped_to_list_still_counts_as_header"),
    ("R3 Lotus header with seconds not a marker", "src/quoted.py",
     r"\s+\d{2}:\d{2}(:\d{2})?\s*(AM|PM)\s*\r?\n(\s*\r?\n){0,3}[ \t]*To:",
     r"\s+\d{2}:\d{2}\s*(AM|PM)\s*\r?\n(\s*\r?\n){0,3}[ \t]*To:",
     T + "test_quoted.py::test_lotus_internet_reply_header_with_seconds_is_a_marker"),
    ("R3 Original Appointment not a marker", "src/quoted.py", "Original (Message|Appointment)", "Original (Message)",
     T + "test_quoted.py::test_outlook_original_appointment_is_a_marker"),
    ("R3 header block not a marker", "src/quoted.py", '"header_block":     r"^', '"header_block":     r"^NEVER',
     T + "test_quoted.py::test_header_block_without_a_sent_line_is_a_marker"),
    ("R4 limit on the file, not the body", "src/ingest.py", "if tables.needs_special_handling(m.body):",
     "if tables.needs_special_handling(m.text):", T + "test_tables.py::test_huge_headers_with_a_small_body_are_standard"),
    ("R4 date format per value", "src/tables.py", "if dm_bad == 0 and md_bad > 0:", "if True:",
     T + "test_tables.py::test_ambiguous_and_month_day_columns"),
    ("R4 oversized prose not chunked", "src/ingest.py", "                m.chunks = tables.chunk_body(m.text, m.body_start)",
     "                m.chunks = []", T + "test_tables.py::test_oversized_prose_message_is_chunked_by_ingest"),
    ("R4 table distribution dropped", "src/tables.py", "                        largest_abs=[", "                        _dropped=[",
     T + "test_tables.py::test_summary_shows_the_distribution_and_cites_rows"),
    ("R5 roots may join", "src/threads.py", "            if subject and not m.is_root:\n                cands",
     "            if subject:\n                cands", T + "test_threads.py::test_a_root_always_starts_a_new_thread"),
    ("R5 no shared address needed", "src/threads.py", "<= max_gap_days and t.people & m.people]", "<= max_gap_days]",
     T + "test_threads.py::test_reply_without_a_shared_address_starts_a_new_thread"),
    ("R7 windows added, not merged", "src/windows.py", "        if a <= cur_b:", "        if False:",
     T + "test_windows.py::test_overlapping_windows_are_merged_not_added"),
    ("R8 receipt ignored", "src/ledger.py", "listed = set(receipt_keys or ()) if output_valid else set()",
     "listed = set(batch_keys) if output_valid else set()", T + "test_ledger.py::test_only_keys_on_a_valid_receipt_become_read"),
    ("Readings: two-digit year read silently", "src/dates.py", "        readings = (Reading(TWO_DIGIT_YEAR",
     "        _unused = (Reading(TWO_DIGIT_YEAR", T + "test_dates.py::test_two_digit_year_is_read_and_flagged_with_why"),
    ("Readings: any two-digit year accepted", "src/dates.py",
     "        if yy not in TWO_DIGIT_YEARS:\n            return None\n        year = TWO_DIGIT_YEARS[yy]",
     "        year = 1900 + int(yy) if int(yy) > 50 else 2000 + int(yy)",
     T + "test_dates.py::test_two_digit_year_outside_the_window_gives_no_date"),
    ("Readings: QP read silently", "src/dates.py", "for q in _QP_SPACE.finditer(raw))", "for q in _QP_SPACE.finditer(''))",
     T + "test_dates.py::test_quoted_printable_is_read_as_a_space_and_flagged"),
    ("Readings: table column decision not logged", "src/ingest.py",
     "        for col, why in m.table.column_readings.items():", "        for col, why in {}.items():",
     T + "test_corpus.py::test_no_silent_readings"),
    ("Readings: dropped addresses not recovered", "src/ingest.py", "    for token in _EMAIL.findall(raw):",
     "    for token in []:", T + "test_headers.py::test_malformed_addresses_are_recovered_and_logged"),
    ("Readings: unread dates not logged", "src/ingest.py",
     "            unread = dates.explain_unread_date(m.text[b.start:b.end])", "            unread = None",
     T + "test_corpus.py::test_no_silent_readings"),
    ("Stage: code not in the fingerprint", "src/ingest.py", "    h.update(code_hash(src_dir).encode())", "    pass",
     T + "test_ingest_stage.py::test_a_code_change_changes_the_fingerprint"),
    ("Stage: failed run accepted as cache", "src/ingest.py", 'meta.get("reconciliation_ok") == "1"', "True",
     T + "test_ingest_stage.py::test_a_failed_run_is_never_reused_as_a_cache_hit"),
    ("Stage: ledger hash not checked", "src/ingest.py", 'rawio.sha256_file(led) == meta.get("ledger_sha256")', "True",
     T + "test_ingest_stage.py::test_a_replaced_or_half_written_ledger_forces_a_rebuild"),
    ("Stage: no extended path on Windows", "src/rawio.py", 'if sys.platform != "win32" or s.startswith(_EXT):', "if True:",
     T + "test_names.py::test_original_trailing_dot_files_are_found_and_read"),
    ("Offsets: text-mode newline translation", "src/ingest.py", "    text, status = rawio.decode(raw)",
     "    text, status = rawio.decode(raw)[0].replace('\\r\\n', '\\n'), 'ok'",
     T + "test_corpus.py::test_every_stored_offset_resolves_in_the_raw_bytes_on_disk"),
    # --- M3 ---
    ("M3 ambiguous quotes accepted", "src/verify.py", "    if len(regions) > 1:", "    if False:",
     T + "test_verify.py::test_rejected_quotes_say_why"),
    ("M3 quotes from unshown blocks accepted", "src/verify.py",
     "    shown = [(mt, r, b) for mt, r, b in placed if not (b is not None and b.skip_reason)]", "    shown = placed",
     T + "test_verify.py::test_rejected_quotes_say_why"),
    ("M3 short quotes accepted", "src/verify.py", "    if len(norm_ws(quote)) < min_chars:", "    if False:",
     T + "test_verify.py::test_rejected_quotes_say_why"),
    ("M3 missing receipt keys ignored", "src/verify.py", "    if missing:\n        res", "    if False:\n        res",
     T + "test_verify.py::test_read_receipt_must_match_the_manifest_exactly"),
    ("M3 canary without minimum sample", "src/verify.py", 'q["checked"] >= vcfg["canary_min_quotes"] and ', "",
     T + "test_verify.py::test_canary_needs_a_minimum_sample"),
    ("M3 QP decoding on every message", "src/verify.py", "    if is_qp:", "    if True:",
     T + "test_verify.py::test_quoted_printable_decoding_is_only_tried_on_qp_messages"),
    ("M3 disposition mismatch ignored", "src/verify.py", '        if x["disposition"] != "observations" and has:',
     "        if False:", T + "test_verify.py::test_dispositions_must_agree_with_observations"),
    ("M3 stale attempt accepted", "src/verify.py", '    if attempt_id is not None and data["attempt_id"] != attempt_id:',
     "    if False:", T + "test_extract.py::test_a_stale_staging_file_from_an_earlier_attempt_is_refused"),
    ("M3 quoted evidence attributed to the forwarder", "src/verify.py",
     '            "evidence_author": block.from_raw or', '            "evidence_author": m.sender or',
     T + "test_verify.py::test_quoted_evidence_is_attributed_to_its_own_author"),
    ("M3 earlier output not moved aside", "src/extract.py",
     '    _move_aside(p.staging / f"{batch_id}.json", p.staging / "rejected"',
     '    (lambda *a: None)(p.staging / f"{batch_id}.json", p.staging / "rejected"',
     T + "test_extract.py::test_prepare_moves_earlier_output_aside"),
    ("M3 failed attempt lands in verified", "src/extract.py", "    out_dir = p.verified if ok else p.failed",
     "    out_dir = p.verified", T + "test_extract.py::test_a_failed_attempt_never_lands_in_verified"),
    ("M3 partial threads allowed", "src/batch.py", "        if missing:\n            raise", "        if False:\n            raise",
     T + "test_batch.py::test_a_slice_must_hold_whole_threads"),
    ("M3 skipped quotes sent in full", "src/batch.py", "            if b.skip_reason:", "            if False:",
     T + "test_batch.py::test_skipped_quote_becomes_a_marked_pointer_recorded_in_the_manifest"),
    ("M3 all recipients shown", "src/batch.py", '    head = ", ".join(addrs[:shown])', '    head = ", ".join(addrs)',
     T + "test_batch.py::test_headers_are_trimmed_and_new_text_is_exact"),
    ("M3 thread splits not recorded", "src/batch.py", "            if u.label:", "            if False:",
     T + "test_batch.py::test_thread_over_the_hard_cap_is_split_at_message_boundaries"),
    ("M3 holdout may overlap the slice", "src/devslice.py", "        if t.thread_id in picked or len(t.keys) > 4",
     "        if len(t.keys) > 4", T + "test_devslice.py::test_holdout_is_outside_the_slice_and_spread"),
    ("M3 benchmark matches without overlap", "src/bench.py",     # score-v4: crediting uses overlap_chars
     'return sum(max(0, min(e, o["end"]) - max(s, o["start"])) for key, s, e in act_spans if key == o["msg_key"])',
     'return sum(1 for key, s, e in act_spans if key == o["msg_key"])',
     T + "test_bench.py::test_recall_counts_only_quotes_that_overlap_the_labelled_span"),
    ("M3 silent label edits accepted", "src/bench.py",
     '        if now.get(key) != labels["messages"].get(key):', "        if False:",
     T + "test_bench.py::test_changes_from_the_frozen_draft_must_be_recorded_review_decisions"),
    ("M3 review order not checked", "src/bench.py", '        if now.get(key) != r.get("before"):', "        if False:",
     T + "test_bench.py::test_changes_from_the_frozen_draft_must_be_recorded_review_decisions"),
    ("M3 frozen draft hash not checked", "src/bench.py",
     '    if hashlib.sha256(draft_bytes).hexdigest() != labels.get("draft_sha256"):', "    if False:",
     T + "test_bench.py::test_changes_from_the_frozen_draft_must_be_recorded_review_decisions"),
    ("M3 blind comparison may skip messages", "src/bench.py", "    if sorted(res) != sorted(blind_keys):",
     "    if False:", T + "test_bench.py::test_blind_comparison_needs_one_result_per_blind_message_and_code_counts_it"),
    ("M3 blind change on an agree accepted", "src/bench.py",
     '        if r.get("source") == "blind_comparison" and res.get(r.get("msg_key")) == "agree":', "        if False:",
     T + "test_bench.py::test_blind_comparison_needs_one_result_per_blind_message_and_code_counts_it"),
    ("R4 rows on the send day count as after it", "src/tables.py",
     "later = [r.row_no for r in t.rows if r.values[c] > sent_on.isoformat()]",
     "later = [r.row_no for r in t.rows if r.values[c] >= sent_on.isoformat()]",
     T + "test_tables.py::test_summary_counts_months_and_rows_dated_after_the_message_was_sent"),
    ("R4 table dates compared with the UTC day", "src/ingest.py", "local = m.parsed_date.local\n",
     "local = m.parsed_date.utc\n",
     T + "test_tables.py::test_ingest_counts_table_dates_against_the_senders_own_calendar_day"),
    ("M3 label states not taken from the extractor schema", "src/bench.py",
     'STATES = tuple(_OBS["action_state"]["enum"])', 'STATES = ("requested", "done", "unclear")',
     T + "test_bench.py::test_label_states_are_the_extractor_schema_states"),
    ("M3 batch end line not checked", "src/batch.py",
     '            b["text"].endswith(mark(f"END BATCH {b[\'batch_id\']}") + "\\n")', "            True",
     T + "test_batch.py::test_a_batch_says_how_long_it_is_and_where_it_ends"),
    ("M3 over-long batch lines accepted", "src/batch.py",
     "            len(line) <= LINE_LIMIT for b in batches", "            True or len(line) <= LINE_LIMIT for b in batches",
     T + "test_batch.py::test_a_batch_says_how_long_it_is_and_where_it_ends"),
    ("M3 fence lets the extractor read any file", "tools/fence_hook.py",
     'if _same(path.parent.parent, root.joinpath(*read_dir).resolve()) and read_name.match(path.name):', "if True:",  # re-pointed 23 Nov (fence refactor)
     T + "test_fence.py::test_the_extractor_reads_only_batch_files_and_writes_only_its_output"),
    ("M3 fence lets the extractor write any file", "tools/fence_hook.py",
     'if _same(path.parent, root.joinpath(*write_dir).resolve()) and write_name.match(path.name):', "if True:",  # re-pointed 23 Nov
     T + "test_fence.py::test_the_extractor_reads_only_batch_files_and_writes_only_its_output"),
    ("M3 overlapping label spans accepted", "src/bench.py",
     "if x != y and s1 < e2 and s2 < e1]", "if False]",
     T + "test_bench.py::test_an_activity_may_have_several_spans_but_two_activities_never_share_text"),
    ("M3 other quotes not checked or scored", "src/bench.py",
     'for q in [a.get("quote", ""), *a.get("other_quotes", [])]:', 'for q in [a.get("quote", "")]:',
     T + "test_bench.py::test_an_activity_may_have_several_spans_but_two_activities_never_share_text"),
    ("M3 labels in review score as final", "src/bench.py",
     '    if provisional and labels.get("status") == "in_review":', '    if labels.get("status") == "in_review":',
     T + "test_bench.py::test_only_approved_labels_score_unless_the_score_is_marked_provisional"),
    ("M3 complete-message recall ignores misses", "src/bench.py",
     'incomplete = [k for k in with_acts if any(not hits_by_act[a["id"]]', 'incomplete = [k for k in with_acts if any(False and not hits_by_act[a["id"]]',
     T + "test_bench.py::test_score_v2_measures_complete_messages_precision_duplicates_and_confusion"),
    ("M3 duplicate hits not reported", "src/bench.py",
     "for aid, hs in hits_by_act.items() if len(hs) > 1]", "for aid, hs in hits_by_act.items() if len(hs) > 9]",
     T + "test_bench.py::test_score_v2_measures_complete_messages_precision_duplicates_and_confusion"),
    ("M3 benchmark sentence leaked into the prompt", ".claude/agents/extractor.md",
     "These are made up to show the rules; they are not from any batch.",
     "These are made up to show the rules; they are not from any batch. Please zero out the volumes until further notice.",
     T + "test_prompt.py::test_the_extractor_prompt_holds_no_benchmark_or_holdout_text"),
    ("M3 item count mismatch not flagged", "src/verify.py",
     'for x in data["messages"] if "work_items" in x and x["work_items"] != per_msg.get(x["msg_key"], 0)]',
     'for x in data["messages"] if False]',
     T + "test_verify.py::test_item_count_mismatch_and_overlapping_quotes_are_flagged_not_failed"),
    ("M3 stall sets agree on any shared signal", "src/bench.py",
     "            stall_agree += hs == ls", "            stall_agree += bool(hs & ls) or hs == ls",
     T + "test_bench.py::test_stall_signals_are_scored_as_sets_with_per_signal_counts"),
    ("M3 repeated label signals accepted", "src/bench.py",
     "not isinstance(sig, list) or len(set(sig)) != len(sig)", "not isinstance(sig, list)",
     T + "test_bench.py::test_stall_signals_are_scored_as_sets_with_per_signal_counts"),
    ("M3 stale approval accepted", "src/bench.py",
     '    if a.get("after_review_entry") != len(labels.get("review", [])):', "    if False:",
     T + "test_bench.py::test_approval_needs_a_record_and_goes_stale_after_a_later_review_entry"),
    ("M3 empty task not flagged", "src/verify.py",
     'for o in data["observations"] if not o["task"].strip()]', 'for o in data["observations"] if False]',
     T + "test_verify.py::test_an_empty_task_is_flagged_not_failed"),
    ("M3 prompt sample drifts from the schema", ".claude/agents/extractor.md",
     '"stall_signals": [],', '"stall_signal": "none",',
     T + "test_prompt.py::test_the_prompts_sample_output_matches_the_schema"),
    ("M3 model mismatch accepted", "src/runmodel.py",
     'other = sorted(m for m in rec["models"] if requested.lower() not in m.lower())', "other = []",
     T + "test_runmodel.py::test_a_turn_by_another_model_is_a_mismatch"),
    ("M3 model taken from a subagent that read another batch", "src/runmodel.py",
     'and r.get("agent_id") and r.get("path") and _norm(r["path"], root) == want):', 'and r.get("agent_id")):',
     T + "test_runmodel.py::test_without_proof_the_model_is_unknown_never_assumed"),
    ("M3 fence log drops the transcript path", "tools/fence_hook.py",
     '"transcript_path": payload.get("transcript_path")}', '"transcript_path": None}',
     T + "test_fence.py::test_each_decision_records_where_the_run_transcript_is"),
    ("M3 one quote credits every activity it overlaps", "src/bench.py",
     'hits = [o for i, o in enumerate(obs) if credited.get(i) == a["id"]]',
     'hits = [o for o in obs if overlaps(o, spans.get(a["id"], []))]',
     T + "test_bench.py::test_one_quote_credits_only_the_activity_it_overlaps_most"),
    ("M3 duplicate judged on output order", "src/bench.py",          # score-v5 step 3
     'h = max(hits, key=lambda o: (overlap_chars(', 'h = hits[0] or max(hits, key=lambda o: (overlap_chars(',
     T + "test_bench.py::test_a_duplicate_is_judged_on_the_copy_with_the_most_evidence_never_on_order_or_answer"),
    ("M3 duplicate judged on its answer", "src/bench.py",
     'h = max(hits, key=lambda o: (overlap_chars(',
     'h = max(hits, key=lambda o: (o["action_state"] == a["action_state"], overlap_chars(',
     T + "test_bench.py::test_a_duplicate_is_judged_on_the_copy_with_the_most_evidence_never_on_order_or_answer"),
    ("M3 clean-C claims published without evidence", "src/verify.py",
     'ev, ev_rejected = {"stall_signals": [], "stall_signals_claimed"',
     'ev, ev_rejected = {"stall_signals": list(o["stall_signals"]), "stall_signals_claimed"',
     T + "test_verify.py::test_clean_c_output_is_verified_against_its_own_schema_and_its_signals_stay_claims"),
    ("M3 benchmark scores published signals as the claims", "src/bench.py",
     "ls, hs, ps = signals_of(a), claimed_of(h), signals_of(h)", "ls, hs, ps = signals_of(a), signals_of(h), signals_of(h)",
     T + "test_bench.py::test_stall_classification_scores_claims_and_published_signals_are_counted_apart"),
    ("M3 benchmark cue phrase back in the prompt", ".claude/agents/extractor.md",
     '"the upload keeps failing"', '"the attachment keeps bouncing"',
     T + "test_prompt.py::test_no_cue_phrase_in_the_prompt_comes_from_the_benchmark_or_dev_slice"),
    ("M3 prompt drifts from the audited escalation rule", ".claude/agents/extractor.md",
     "communicating expectations are not escalation.", "communicating expectations can be escalation.",
     T + "test_prompt.py::test_the_prompt_states_the_audited_stall_rules_and_their_history_leads_there"),
    ("M3 doubtful matches not listed for review", "src/bench.py",
     '           if meaning(h.get("task", ""), acts_by_id[aid].get("activity", "")) < MEANING_REVIEW]',
     '           if False]',
     T + "test_bench.py::test_matches_whose_task_shares_few_words_are_listed_for_review_and_still_scored"),
    ("M3 no-credit judgment ignored", "src/bench.py",
     'if adj and adj["decision"] == "no_credit":', 'if False:',
     T + "test_bench.py::test_a_person_judged_wrong_match_earns_no_credit_and_unjudged_doubts_are_counted"),
    ("M3 freeze ignores label edits", "src/bench.py",
     '    differs = [k for k, v in now.items() if fz.get(k) != v]',
     '    differs = [k for k, v in now.items() if fz.get(k) != v and k != "labels_sha256"]',
     T + "test_bench.py::test_a_score_says_whether_it_was_made_with_the_frozen_benchmark"),
    ("M3 evidence set may skip a positive label", "src/evidence_bench.py",
     'problems += [f"{i} {s}: positive label with no evidence entry" for i, s in sorted(want - set(have))]',
     'problems += []',
     T + "test_evidence_bench.py::test_every_positive_label_has_exactly_one_entry_and_no_entry_invents_a_signal"),
    ("M3 evidence scope ignores the activity's span", "src/evidence_bench.py",
     'scope="activity" if inside else', 'scope="activity" if True else',
     T + "test_evidence_bench.py::test_passages_get_offsets_author_scope_and_sharing_and_doubt_needs_a_reason"),
    ("M3 cross-message evidence accepted without a stored link", "src/evidence_bench.py",
     '        if via is None:\n            return f"{key} has no hidden quoted block', '        if False:\n            return f"{key} has no hidden quoted block',
     T + "test_evidence_bench.py::test_a_passage_from_another_message_needs_the_stored_link_of_a_hidden_block"),
    ("M3 negative claim may contradict the labels", "src/evidence_bench.py",
     '            if c["signal"] in a["stall_signals"]:', '            if False:',
     T + "test_evidence_bench.py::test_negative_claims_must_not_contradict_the_labels_and_cite_real_text"),
    ("M3 tied duplicates broken by output order", "src/bench.py",
     '-o["start"], -o["end"], -pos[id(o)]))', '-pos[id(o)]))',
     T + "test_bench.py::test_tied_duplicates_are_broken_by_source_position_not_output_order"),
    ("M3 broad quote ignores the task's meaning", "src/bench.py",    # score-v5 step 2
     'cands = [(meaning(o.get("task", ""), acts_by_id[aid].get("activity", "")),', 'cands = [(0,',
     T + "test_bench.py::test_a_broad_quote_credits_one_activity_the_one_its_task_describes"),
    ("M3 signal published without verified evidence", "src/verify.py",
     'return ({"stall_signals": [e["signal"] for e in verified], "stall_signals_claimed": claimed,\n'
     '             "stall_evidence": verified}, rejected)',
     'return ({"stall_signals": claimed, "stall_signals_claimed": claimed,\n'
     '             "stall_evidence": verified}, rejected)',  # made unique 23 Nov (the H path repeats the first line)
     T + "test_verify.py::test_a_signal_is_published_only_with_one_verified_evidence_quote"),
    ("M3 several evidence quotes for one signal accepted", "src/verify.py",
     "        if len(mine) > 1:", "        if False:",
     T + "test_verify.py::test_signal_and_evidence_sets_must_match_one_to_one"),
    ("M3 evidence for an unlisted signal ignored", "src/verify.py",
     '    for s in sorted({e["signal"] for e in entries} - set(claimed)):', "    for s in []:",
     T + "test_verify.py::test_signal_and_evidence_sets_must_match_one_to_one"),
    ("M3 evidence flag on any dropped signal", "src/verify.py",
     'st["signals_dropped"] / st["signals_claimed"] > vcfg["evidence_flag_max_fail_rate"]', 'st["signals_dropped"] > 0',
     T + "test_verify.py::test_a_batch_losing_too_much_evidence_is_flagged_for_review"),
    ("M3 publish gate skips evidence quotes", "src/verify.py",
     'return same(obs) and all(same(e) for e in obs.get("stall_evidence", []))', "return same(obs)",
     T + "test_verify.py::test_the_publish_gate_rechecks_evidence_quotes"),
    # --- M3 Phase 3-6 (23 Nov 2026): evidence architectures, grounding, events, scoring, artifacts, dashboard ---
    ("M3 H publishes an unresolved claim", "src/verify.py",
     '        if e["status"] == "unresolved":\n', '        if False:\n',
     T + "test_verify.py::test_h_publishes_a_signal_only_with_a_verified_quote_and_keeps_unresolved_claims"),
    ("M3 H accepts two entries for one signal", "src/verify.py", "        if len(mine) != 1:\n", "        if not mine:\n",
     T + "test_verify.py::test_h_a_claimed_signal_with_no_or_two_entries_is_rejected_and_stray_entries_are_logged"),
    ("M3 grounder may cite outside its sections", "src/ground.py", "    if outside:\n        return REJECTED",
     "    if False:\n        return REJECTED", T + "test_ground.py::test_a_citation_outside_the_claims_permitted_sections_is_rejected"),
    ("M3 grounder evidence copied, not cut from raw", "src/ground.py", '"quote_raw": m.text[s["start"]:s["end"]]',
     '"quote_raw": m.text[s["start"]:s["end"]].strip() + " "',
     T + "test_ground.py::test_only_supported_claims_with_valid_citations_are_published_and_evidence_is_cut_by_code"),
    ("M3 unsure verdict published", "src/ground.py", '    if v == "unsure":\n        return UNRESOLVED, [], verdict["reason"]',
     '    if v == "unsure":\n        v = "supported"',
     T + "test_ground.py::test_a_supported_verdict_without_a_valid_citation_is_rejected_not_published"),
    ("M3 missing verdict accepted", "src/ground.py", '        res["errors"].append(f"no verdict for claim(s)',
     '        pass  # res["errors"].append(f"no verdict for claim(s)',
     T + "test_ground.py::test_malformed_or_incomplete_verdicts_fail_the_attempt"),
    ("M3 packet shows table rows", "src/packet.py", "    if m.table_summary is not None:\n        out, pos",
     "    if False:\n        out, pos", T + "test_ground.py::test_a_table_message_shows_only_its_prose_lines_as_its_batch_does"),
    ("M3 packet hides no linked original", "src/packet.py", "            if r[\"kind\"] == \"hidden\":\n                add(",
     "            if False:\n                add(",
     T + "test_ground.py::test_a_hidden_quoted_block_becomes_a_pointer_and_its_original_a_linked_section"),
    ("M3 fence lets the grounder read batches", "tools/fence_hook.py", '("data", "ground", "packets"), re.compile(r"^g\\d{3}\\.txt$")',
     '("data", "batches"), re.compile(r"^[bg]\\d{3}\\.txt$")',
     T + "test_fence.py::test_the_grounder_reads_only_packet_files_and_writes_only_its_output"),
    ("M3 evidence scorer credits any passage", "src/evidence_score.py",
     'row["category"] = "published_supported" if act_hit else "published_other_passage"',
     'row["category"] = "published_supported"',
     T + "test_evidence_score.py::test_right_signal_with_evidence_that_is_not_an_approved_passage_is_kept_apart"),
    ("M3 evidence scorer skips the raw re-read", "src/evidence_score.py",
     'ok = m is not None and m.text[x["start"]:x["end"]] == x["quote_raw"] and x["end"] > x["start"]', "ok = True",
     T + "test_evidence_score.py::test_evidence_not_literally_in_the_raw_file_fails_the_citation_check"),
    ("M3 events ignore the quoted-copy anchor", "src/events.py", "    if anchor:\n", "    if False:\n",
     T + "test_events_score.py::test_quoted_copies_in_other_threads_merge_into_their_anchors_event"),
    ("M3 rate published below minimum evidence", "src/score.py",
     'if len(msgs) < cfg["min_emails"] or len(threads) < cfg["min_threads"]:', "if False:",
     T + "test_events_score.py::test_minimum_evidence_and_short_windows_publish_no_rate"),
    ("M3 overlap counted twice in the total", "src/score.py", "        new = [e for e in o[\"event_ids\"] if e not in counted]",
     "        new = list(o[\"event_ids\"])", T + "test_events_score.py::test_overlapping_windows_are_merged_and_shared_events_count_once_in_the_total"),
    ("M3 float money rounding", "src/score.py", "    return f\"${x.quantize(CENT, rounding=ROUND_HALF_UP):,}\"",
     "    return f\"${round(float(x), 2):,.2f}\"", T + "test_events_score.py::test_decimal_half_up_rounding_where_float_rounding_is_a_cent_off"),
    ("M3 chain check ignores fake quotes", "src/score.py", "        bad = [q for q in c[\"evidence\"] if q not in verified_quote_ids]",
     "        bad = []", T + "test_events_score.py::test_the_chain_check_catches_a_fake_quote_a_missing_input_and_a_cycle"),
    ("M3 synthesis reuses an observation", "src/synth.py", "            if o in used:\n", "            if False:\n",
     T + "test_synth_act.py::test_bad_synthesis_output_fails"),
    ("M3 artifact cites an unknown quote", "src/act.py", "    if unknown:\n        errors.append", "    if False:\n        errors.append",
     T + "test_synth_act.py::test_bad_drafts_fail"),
    ("M3 dashboard trusts raw email as HTML", "src/serve.py", "        seg = escape(text[a:b])", "        seg = Markup(text[a:b])",
     T + "test_serve.py::test_raw_email_is_escaped_and_the_quote_is_highlighted_at_its_byte_offsets"),
    ("M3 pipeline never retries", "src/pipeline.py", '        for attempt in range(1 + r.cfg["run"]["retries"]):\n            rec = extract.prepare',
     '        for attempt in range(1):\n            rec = extract.prepare',
     T + "test_pipeline.py::test_a_failed_attempt_is_retried_and_a_batch_failing_every_attempt_is_never_read"),
    ("M3 extract cache ignores the prompt", "src/pipeline.py", 'keyfor = lambda b: {"fingerprint": b["fingerprint"], "prompt_sha256": _sha(prompt),',
     'keyfor = lambda b: {"fingerprint": b["fingerprint"], "prompt_sha256": "x",',
     T + "test_pipeline.py::test_a_rerun_skips_finished_batches_and_a_prompt_change_reruns_them"),
    ("M3 scorer counts claims as activity-signal pairs", "src/evidence_score.py",
     '"published_counts": {"claims": sum(len(o.get("stall_signals", [])) for o in run["observations"]),',
     '"published_counts": {"claims": len(pub_pairs),',
     T + "test_evidence_score.py::test_the_counting_units_are_explicit_claims_versus_activity_signal_pairs"),
    ("M3 offline run calls the model anyway", "src/pipeline.py", "    raise OfflineCacheMiss(f\"offline run:",
     "    return OfflineCacheMiss(f\"offline run:",
     T + "test_pipeline.py::test_an_offline_run_refuses_any_model_call_for_an_uncached_stage"),
    # The same change ("finalize unlocked") was first checked against the concurrent-threads test, which catches it only
    # when threads happen to collide (1 run in 3 after the cross-process lock); it is checked deterministically here.
    ("M3 finalize writes shared files outside the lock", "src/extract.py",
     "    with shared_lock(p.logs):      # parallel finalizes",
     "    if True:      # parallel finalizes",
     T + "test_filelock.py::test_finalize_writes_every_shared_file_only_while_holding_the_cross_process_lock"),
    ("M3 lock holds threads only, not processes", "src/filelock.py",
     "                        msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)\n",
     "                        pass\n",
     T + "test_filelock.py::test_separate_processes_never_lose_an_update_under_the_lock"),
    ("M3 atomic write replaced by a plain write", "src/filelock.py",
     "    tmp.write_text(text, encoding=\"utf-8\")\n    for i in range(attempts):",
     "    path.write_text(text, encoding=\"utf-8\")\n    return\n    for i in range(attempts):",
     T + "test_filelock.py::test_a_reader_in_another_process_never_sees_a_torn_status_file"),
    ("M3 headless runner ignores the API key", "src/headless.py", '    if os.environ.get("ANTHROPIC_API_KEY"):', "    if False:",
     T + "test_pipeline.py::test_the_headless_runner_refuses_to_start_with_an_api_key"),
]


def run_pytest(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *args],
                          cwd=cwd, capture_output=True, text=True)


def first_error(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("E ") or "Error" in line[:40]:
            return line.strip()[:160]
    return ""


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    todo = [m for m in MUTANTS if only in m[0]]
    base = run_pytest(REPO)
    if base.returncode != 0:
        print("BASELINE FAILS: fix the suite before checking mutants\n" + base.stdout[-2000:])
        return 2
    print(f"baseline passes: {base.stdout.strip().splitlines()[-1]}")
    report, caught = [], 0
    for name, rel, old, new, node in todo:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for part in COPY:
                src = REPO / part
                if src.exists():
                    (shutil.copytree if src.is_dir() else shutil.copy)(src, d / part)
            f = d / rel
            text = f.read_text(encoding="utf-8")
            n = text.count(old)
            if n != 1:
                verdict, err, code = "NOT-APPLIED", f"original text occurs {n} times (must be exactly 1)", None
            else:
                f.write_text(text.replace(old, new), encoding="utf-8")
                assert f.read_text(encoding="utf-8") != text
                r = run_pytest(d, node)
                code = r.returncode
                err = first_error(r.stdout)
                verdict = "CAUGHT" if code == 1 else ("MISSED" if code == 0 else "SETUP-ERROR")
        caught += verdict == "CAUGHT"
        report.append({"mutant": name, "file": rel, "intended_test": node, "verdict": verdict,
                       "pytest_exit_code": code, "error": err})
        print(f"{verdict:12s} {name:46s} {node.split('::')[-1]}" + (f"\n             {err}" if verdict != "CAUGHT" else ""))
    out = REPO / "data" / "logs" / "mutation_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"\n{caught}/{len(todo)} caught by their intended test; report: {out}")
    return 0 if caught == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
