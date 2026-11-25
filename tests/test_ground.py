"""Design I/J grounding: packets of numbered sentences with raw offsets, and code-checked verdicts."""
import json

import pytest

from src import ground, packet, verify
from src.batch import make_batches

from conftest import BATCH_CFG, VERIFY_CFG, email
from test_verify import LONG, REPLY, corpus


@pytest.fixture
def setup(ingested):
    st = ingested(corpus())
    (b,) = make_batches(st, [m.key for m in st.unique()], BATCH_CFG, "p1")
    return st, b


def run_with(st, b, observations):
    """A verified clean-C style run: observations carry claims only."""
    msgs = [{"msg_key": k, "disposition": "observations" if any(o["msg_key"] == k for o in observations)
             else "business_no_process_signal", "reason": "x", "work_items": 0} for k in b["keys"]]
    out = []
    for o in observations:
        chk = verify.check_quote(st.messages[o["msg_key"]], o["quote"], 25)
        assert chk.pop("ok"), chk
        out.append({**o, **chk, "stall_signals_claimed": o["stall_signals"], "stall_signals": [],
                    "stall_evidence": [], "signal_grounding": "none"})
    return {"status": "verified", "attempt_id": "b001-x-1", "messages": msgs, "observations": out, "rejected": []}


def obs(n, key, quote, signals, task="confirm nominations", state="requested"):
    return {"obs_id": f"b001-o{n}", "msg_key": key, "task": task, "action_state": state, "quote": quote,
            "stall_signals": signals}


# --- sentences ---

def test_sentences_index_the_raw_file_and_join_wrapped_lines():
    raw = "Hi team,\r\nThe volume on meter 4 is wrong. Please fix\r\nit today!  Thanks.\r\n\r\n- item one\r\n- item two"
    got = [raw[s:e] for s, e in packet.sentences(raw, 0, len(raw))]
    assert got == ["Hi team,\r\nThe volume on meter 4 is wrong.", "Please fix\r\nit today!", "Thanks.",
                   "- item one", "- item two"]
    for s, e in packet.sentences(raw, 0, len(raw)):
        assert raw[s:e] == raw[s:e].strip() and raw[s:e]          # cut from the raw text, trimmed, never empty


def test_a_long_run_without_sentence_ends_is_cut_at_line_ends():
    raw = "\r\n".join(["row %03d value 1,234 more text here" % i for i in range(60)])
    cuts = packet.sentences(raw, 0, len(raw))
    assert len(cuts) > 1 and all(e - s <= packet.MAX_PIECE + 60 for s, e in cuts)
    assert "".join(raw[s:e] for s, e in cuts).replace("\r\n", "") == raw.replace("\r\n", "")   # nothing lost


def test_punctuation_only_lines_are_not_sentences():
    raw = "Done.\r\n\r\n-----\r\n\r\n***"
    assert [raw[s:e] for s, e in packet.sentences(raw, 0, len(raw))] == ["Done."]


# --- packets ---

def test_a_hidden_quoted_block_becomes_a_pointer_and_its_original_a_linked_section(setup):
    st, b = setup
    hidden = [bl for bl in st.messages["mb/inbox/2"].blocks if bl.skip_reason]
    assert hidden and hidden[0].skip_reason == "original_in_thread:mb/inbox/1"     # the fixture really hides it
    run = run_with(st, b, [obs(1, "mb/inbox/2", "sent the corrected\r\nnominations to scheduling", ["correction"])])
    pk = packet.build(run, st, "g001", "t")
    assert pk["sections"] == {"M1": "mb/inbox/2", "M2": "mb/inbox/1"}
    assert "shown because message mb/inbox/2 (section M1) quotes it" in pk["text"]
    assert "copy of text in message mb/inbox/1, shown in section M2" in pk["text"]
    (c,) = pk["claims"]
    assert c["permitted_sections"] == ["M1", "M2"] and c["own_sentences"] and c["signal"] == "correction"
    for sid, s in pk["sentences"].items():                           # every id points at real raw text
        m = st.messages[s["msg_key"]]
        assert f"[{sid}] " in pk["text"] and m.text[s["start"]:s["end"]].strip()
    # the wrapped reply line is shown joined, and that reading is logged with its offsets
    assert any(r["rule"] == "packet_line_join" and r["msg_key"] == "mb/inbox/2" for r in pk["readings"])


def test_the_packet_holds_only_messages_with_claims_and_their_linked_originals(setup):
    st, b = setup
    run = run_with(st, b, [obs(1, "mb/inbox/3", "I will update the meter list this week.", []),
                           obs(2, "mb/inbox/6", "percent of the scheduled volume for March.", ["waiting"])])
    pk = packet.build(run, st, "g001", "t")
    assert list(pk["sections"].values()) == ["mb/inbox/6"] and len(pk["claims"]) == 1


def test_a_claim_filter_sends_only_the_selected_claims(setup):
    st, b = setup
    run = run_with(st, b, [obs(1, "mb/inbox/6", "percent of the scheduled volume for March.",
                               ["waiting", "follow_up"])])
    pk = packet.build(run, st, "g001", "t", claim_filter=lambda o, s: s == "waiting")
    assert [c["signal"] for c in pk["claims"]] == ["waiting"]


def test_the_packet_never_contains_the_batcher_marks_inside_a_sentence(setup):
    st, b = setup
    run = run_with(st, b, [obs(1, "mb/inbox/2", "sent the corrected\r\nnominations to scheduling", ["correction"])])
    pk = packet.build(run, st, "g001", "t")
    for line in pk["text"].splitlines():
        if line.startswith("[M"):
            assert "⟦" not in line and "⟧" not in line


# --- verdicts ---

@pytest.fixture
def grounded(setup):
    st, b = setup
    run = run_with(st, b, [obs(1, "mb/inbox/2", "sent the corrected\r\nnominations to scheduling", ["correction", "waiting"])])
    pk = packet.build(run, st, "g001", "t")
    manifest = {k: v for k, v in pk.items() if k != "text"}
    return st, run, manifest


def verdicts(manifest, items, attempt="g001-a-1"):
    return json.dumps({"packet_id": manifest["packet_id"], "attempt_id": attempt,
                       "verdicts": [{"claim_id": c, "verdict": v, "sentence_ids": ids, "reason": "r"}
                                    for c, v, ids in items]})


def test_malformed_or_incomplete_verdicts_fail_the_attempt(grounded):
    _, _, m = grounded
    assert ground.check_verdicts("{", m, "g001-a-1")["status"] == "failed"
    assert ground.check_verdicts(verdicts(m, [("C1", "supported", [])]), m, "g001-a-1")["status"] == "failed"   # C2 missing
    assert ground.check_verdicts(verdicts(m, [("C1", "unsure", []), ("C2", "unsure", []), ("C3", "unsure", [])]),
                                 m, "g001-a-1")["status"] == "failed"                                           # extra claim
    assert ground.check_verdicts(verdicts(m, [("C1", "maybe", []), ("C2", "unsure", [])]), m, "g001-a-1")["status"] == "failed"
    stale = ground.check_verdicts(verdicts(m, [("C1", "unsure", []), ("C2", "unsure", [])], attempt="g001-old-1"), m, "g001-a-1")
    assert stale["status"] == "failed" and "attempt_id" in stale["errors"][0]
    assert ground.check_verdicts(verdicts(m, [("C1", "unsure", []), ("C2", "unsure", [])]), m, "g001-a-1")["status"] == "verified"


def test_only_supported_claims_with_valid_citations_are_published_and_evidence_is_cut_by_code(grounded):
    st, run, m = grounded
    own = m["claims"][0]["own_sentences"][0]
    linked = next(i for i, s in m["sentences"].items() if s["msg_key"] == "mb/inbox/1")
    chk = ground.check_verdicts(verdicts(m, [("C1", "supported", [own, linked]), ("C2", "unsupported", [])]), m, "g001-a-1")
    out = ground.apply(run, m, chk["verdicts"], st)
    (o,) = out["observations"]
    assert o["stall_signals"] == ["correction"] and o["stall_signals_claimed"] == ["correction", "waiting"]
    assert {x["signal"]: x["outcome"] for x in o["signal_outcomes"]} == {"correction": "published", "waiting": "unsupported"}
    for e in o["published_evidence"]:
        assert e["quote_raw"] == st.messages[e["msg_key"]].text[e["start"]:e["end"]]   # literally in the raw file
    assert {e["scope"] for e in o["published_evidence"]} == {"own", "linked"}
    linked_ev = next(e for e in o["published_evidence"] if e["scope"] == "linked")
    assert linked_ev["msg_key"] == "mb/inbox/1" and linked_ev["evidence_author"] == "a@enron.com"
    # the activity is never changed by grounding
    src = run["observations"][0]
    assert all(o[k] == src[k] for k in ("task", "action_state", "quote", "start", "end", "msg_key"))


@pytest.mark.parametrize("ids, why", [
    ([], "no sentence cited"),
    (["M9.S1"], "not in the packet"),
    (["M1.S1", "M1.S2", "M2.S1", "M2.S2"], "at most 3"),
])
def test_a_supported_verdict_without_a_valid_citation_is_rejected_not_published(grounded, ids, why):
    st, run, m = grounded
    chk = ground.check_verdicts(verdicts(m, [("C1", "supported", ids), ("C2", "unsure", [])]), m, "g001-a-1")
    (o,) = ground.apply(run, m, chk["verdicts"], st)["observations"]
    out = {x["signal"]: x for x in o["signal_outcomes"]}
    assert o["stall_signals"] == [] and out["correction"]["outcome"] == "rejected" and why in out["correction"]["reason"]
    assert out["waiting"]["outcome"] == "unresolved"


def test_a_citation_outside_the_claims_permitted_sections_is_rejected(setup):
    st, b = setup
    run = run_with(st, b, [obs(1, "mb/inbox/6", "percent of the scheduled volume for March.", ["waiting"]),
                           obs(2, "mb/inbox/3", "I will update the meter list this week.", ["follow_up"])])
    pk = packet.build(run, st, "g001", "t")
    m = {k: v for k, v in pk.items() if k != "text"}
    other = next(i for i, s in m["sentences"].items() if s["msg_key"] == "mb/inbox/3")
    chk = ground.check_verdicts(verdicts(m, [("C1", "supported", [other]), ("C2", "unsure", [])]), m, "g001-a-1")
    o = ground.apply(run, m, chk["verdicts"], st)["observations"][0]
    assert o["signal_outcomes"][0]["outcome"] == "rejected" and "outside" in o["signal_outcomes"][0]["reason"]


def test_claims_left_out_of_a_selective_packet_keep_their_own_outcome(grounded):
    st, run, m = grounded
    run = json.loads(json.dumps(run))
    o = run["observations"][0]
    o["signal_outcomes"] = [{"signal": "correction", "outcome": "published"}, {"signal": "waiting", "outcome": "published"}]
    o["published_evidence"] = [{"signal": "waiting", "msg_key": o["msg_key"], "start": o["start"], "end": o["end"],
                                "quote_raw": o["quote_raw"]}]
    one = {**m, "claims": [c for c in m["claims"] if c["signal"] == "correction"]}
    chk = {"C1": {"claim_id": "C1", "verdict": "unsupported", "sentence_ids": [], "reason": "r"}}
    (g,) = ground.apply(run, one, chk, st, only_selected=True)["observations"]
    assert g["stall_signals"] == ["waiting"] and [e["signal"] for e in g["published_evidence"]] == ["waiting"]
    assert {x["signal"]: x["grounded"] for x in g["signal_outcomes"]} == {"correction": True, "waiting": False}


def test_the_grounder_prompt_uses_the_extractors_signal_definitions_word_for_word():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    ext = (root / "data" / "prompts" / "extract-v8c.md")
    ext = ext.read_text(encoding="utf-8") if ext.exists() else (root / ".claude" / "agents" / "extractor.md").read_text(encoding="utf-8")
    defs = ext[ext.index("- `waiting`: the work is explicitly"):ext.index("## Examples")].rstrip()
    g = (root / ".claude" / "agents" / "grounder.md").read_text(encoding="utf-8")
    assert defs in g
    sample = json.loads(g.split("```json\n", 1)[1].split("```", 1)[0])
    from src import schema
    assert schema.validate(sample, schema.load("grounder-v1.schema.json")) == []


def test_a_table_message_shows_only_its_prose_lines_as_its_batch_does():
    """packet-v1 showed all 3,600 rows of the giant table message to the grounder (caught 23 Nov 2026 from
    the packet sizes). The packet must show what the batch shows: prose lines only, rows never citable."""
    from src import store as store_mod
    rows = "".join(f"{i:04d}  31/01/2001  1,000  900  +100\r\n" for i in range(40))
    head = "From: a@enron.com\r\nSubject: Position\r\n\r\n"
    body = "Team,\r\nPlease confirm your meters by Thursday.\r\n\r\n" + rows + "Daren\r\n"
    text = head + body
    m = store_mod.StoredMessage("mb/logistics/9", "corpus/mb/logistics/9.txt", "corpus/mb/logistics/9.", "mb", "t:9",
                                None, None, "missing", None, "a@enron.com", [], [], "Position", len(head), len(text),
                                "table_parsed", "x", table_prose=["Team,", "Please confirm your meters by Thursday.", "Daren"],
                                table_summary={"row_count": 40}, _text=text)
    regs = packet._regions(m)
    shown = [text[r["start"]:r["end"]] for r in regs if r["kind"] == "new_text"]
    assert shown == ["Team,", "Please confirm your meters by Thursday.", "Daren"] and regs[-1]["kind"] == "table_note"
    st = store_mod.Store({m.key: m}, {"t:9": [m.key]}, {})
    o = {"obs_id": "b001-o1", "msg_key": m.key, "task": "confirm meters", "action_state": "requested",
         "start": text.index("Please"), "end": text.index("Thursday.") + 9, "stall_signals_claimed": ["waiting"]}
    pk = packet.build({"observations": [o]}, st, "g001", "t")
    assert "31/01/2001" not in pk["text"] and "40 table rows are not shown" in pk["text"]
    assert all(not text[s["start"]:s["end"]].startswith("00") for s in pk["sentences"].values())


def test_the_final_staged_file_is_what_is_validated_after_a_write_read_write_sequence(grounded, tmp_path):
    """Set D, b017: the grounder wrote, tried to read its file back (denied), and wrote again. Only the file on disk when
    the pipeline finalizes is checked, so a good first write cannot carry a bad rewrite, nor the reverse."""
    _, _, m = grounded
    f = tmp_path / "g001.json"
    good = verdicts(m, [("C1", "unsure", []), ("C2", "unsure", [])])
    bad = verdicts(m, [("C1", "unsure", [])])                                                       # C2 has no verdict
    f.write_text(good, encoding="utf-8")
    f.write_text(bad, encoding="utf-8")                                                             # the rewrite
    assert ground.check_verdicts(f.read_text(encoding="utf-8"), m, "g001-a-1")["status"] == "failed"
    f.write_text(bad, encoding="utf-8")
    f.write_text(good, encoding="utf-8")
    assert ground.check_verdicts(f.read_text(encoding="utf-8"), m, "g001-a-1")["status"] == "verified"


def test_the_grounder_run_instruction_asks_for_no_read_back_and_its_analytical_prompt_is_untouched():
    from src import headless
    rec = {"packet_file": "data/ground/packets/all/r/g001.txt", "packet_id": "g001", "attempt_id": "g001-a-1",
           "output_file": "data/ground/staging/all/g001.json"}
    text = ground.instruction(rec)
    assert text.endswith(headless.NO_READBACK) and text.count("`") == 8                              # the four fields it parses
    assert "read your output" not in ground.PROMPT.read_text(encoding="utf-8").lower()
