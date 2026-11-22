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
    ("M3 benchmark matches without overlap", "src/bench.py",
     'return any(o["msg_key"] == key and o["start"] < e and s < o["end"] for key, s, e in act_spans)',
     'return any(o["msg_key"] == key for key, s, e in act_spans)',
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
     'if _same(path.parent.parent, batches) and _BATCH.match(path.name):', "if True:",
     T + "test_fence.py::test_the_extractor_reads_only_batch_files_and_writes_only_its_output"),
    ("M3 fence lets the extractor write any file", "tools/fence_hook.py",
     'if _same(path.parent, staging) and _OUTPUT.match(path.name):', "if True:",
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
