"""Stage 4, verify: quotes against raw bytes, match types, regions, dates, and every partial failure."""
import json

import pytest

from src import ledger, verify
from src.batch import make_batches

from conftest import BATCH_CFG, VERIFY_CFG, email

LONG = ("Please confirm the nominations for the Katy hub by noon tomorrow so scheduling can "
        "release the volumes before the pipeline cutoff. ")
REPLY = ("Done, sent the corrected\r\nnominations to scheduling before the cutoff.\r\n\r\n -----Original Message-----\r\n"
         "From: \ta@enron.com\r\nSent:\tMonday, February 04, 2002 1:47 PM\r\nTo:\tb@enron.com\r\nSubject:\tKaty noms"
         "\r\n\r\n" + LONG + "\r\nDaren\r\n")
MIXED = ("I will update the meter list this week.\r\n\r\n"
         "> The meter list for Katy needs the new numbers added before month end.\r\n\r\n"
         " -----Original Message-----\r\nFrom: \tc@enron.com\r\nSent:\tMonday, February 04, 2002 1:47 PM\r\n"
         "To:\ta@enron.com\r\nSubject:\tWaha\r\n\r\nCan you confirm the volumes for the Waha hub by Friday afternoon?\r\n")
QP_BODY = "Please review the attached contract=\r\n and send comments to legal by Monday.=20\r\nThanks=3D Bob\r\n"
AMBIG = ("We need the signed confirmation from Calpine today.\r\n\r\n"
         "> We need the signed confirmation from Calpine today.\r\n")


def corpus():
    qp = email(subject="Contract", body=QP_BODY, message_id="<4@x>", date="Thu, 7 Feb 2002 09:00:00 -0800")
    return {
        "mb/inbox/1": email(subject="Katy noms", body=LONG + "\r\nDaren\r\n", message_id="<1@x>"),
        "mb/inbox/2": email(subject="RE: Katy noms", body=REPLY, message_id="<2@x>", sender="b@enron.com",
                            to="a@enron.com", date="Tue, 5 Feb 2002 09:00:00 -0800"),
        "mb/inbox/3": email(subject="Meter list", body=MIXED, message_id="<3@x>", date="Wed, 6 Feb 2002 09:00:00 -0800"),
        "mb/inbox/4": qp.replace("Content-Transfer-Encoding: 7bit", "Content-Transfer-Encoding: quoted-printable"),
        "mb/inbox/5": email(subject="Calpine", body=AMBIG, message_id="<5@x>", date="Fri, 8 Feb 2002 09:00:00 -0800"),
        "mb/inbox/6": email(subject="Rates", body="The rate is 5=20 percent of the scheduled volume for March.\r\n",
                            message_id="<6@x>", date="Mon, 11 Feb 2002 09:00:00 -0800"),
    }


@pytest.fixture
def setup(ingested):
    st = ingested(corpus())
    (b,) = make_batches(st, [m.key for m in st.unique()], BATCH_CFG, "p1")
    return st, b


def obs(n, key, quote, **kw):
    base = {"obs_id": f"b001-o{n}", "msg_key": key, "task": "confirm nominations", "process_hint": "daily noms",
            "actors": ["a@enron.com"], "action_state": "requested", "trigger": "", "output": "",
            "stall_signals": [], "stall_evidence": [], "quote": quote, "confidence": "high", "limitations": ""}
    return {**base, **kw}


def output(b, observations, dispositions=None, attempt_id="b001-test-1", **kw):
    have = {o["msg_key"] for o in observations}
    msgs = [{"msg_key": k, "disposition": "observations" if k in have else "business_no_process_signal",
             "reason": "" if k in have else "no work shown",
             "work_items": sum(o["msg_key"] == k for o in observations)} for k in b["keys"]]
    if dispositions is not None:
        msgs = dispositions
    return json.dumps({"batch_id": b["batch_id"], "attempt_id": attempt_id, "messages": msgs,
                       "observations": observations, **kw})


def run(setup, observations, **kw):
    st, b = setup
    return verify.verify_batch(output(b, observations, **kw), b, st, VERIFY_CFG, attempt_id="b001-test-1")


# --- single quotes ---

def test_exact_quote_in_new_text(setup):
    st, b = setup
    r = run(setup, [obs(1, "mb/inbox/1", LONG.strip())])
    (o,) = r["observations"]
    assert r["status"] == "verified" and o["match_kind"] == "exact" and o["region"] == "new_text"
    m = st.messages["mb/inbox/1"]
    assert m.text[o["start"]:o["end"]] == o["quote_raw"] == LONG.strip()
    assert (o["evidence_date"], o["date_basis"]) == ("2002-02-04", "message_date")
    assert o["original_path"] == "corpus/mb/inbox/1."


def test_quote_across_a_line_break_is_a_whitespace_match_and_logged(setup):
    st, _ = setup
    r = run(setup, [obs(1, "mb/inbox/2", "Done, sent the corrected nominations to scheduling before the cutoff.")])
    (o,) = r["observations"]
    assert o["match_kind"] == "whitespace" and "\r\n" in o["quote_raw"]
    assert st.messages["mb/inbox/2"].text[o["start"]:o["end"]] == o["quote_raw"]
    assert [x["rule"] for x in o["readings"]] == ["quote_match_whitespace"]


def test_quoted_printable_message_matches_after_decoding(setup):
    st, _ = setup
    r = run(setup, [obs(1, "mb/inbox/4", "Please review the attached contract and send comments to legal by Monday.")])
    (o,) = r["observations"]
    assert o["match_kind"] == "qp_decoded"
    raw = st.messages["mb/inbox/4"].text[o["start"]:o["end"]]
    assert raw == o["quote_raw"] and "=\r\n" in raw                  # the raw span, soft break included


def test_quoted_printable_decoding_is_only_tried_on_qp_messages(setup):
    # mb/inbox/6 is plain 7bit mail whose text literally contains "=20". Decoding it would turn
    # that into a space and wrongly accept a quote that isn't in the file.
    r = run(setup, [obs(1, "mb/inbox/6", "The rate is 5 percent of the scheduled volume for March.")])
    assert r["rejected"][0]["reason"] == "not found in the raw file"
    r = run(setup, [obs(1, "mb/inbox/6", "The rate is 5=20 percent of the scheduled volume for March.")])
    assert r["observations"][0]["match_kind"] == "exact"


def test_dated_quoted_block_dates_the_evidence(setup):
    r = run(setup, [obs(1, "mb/inbox/3", "Can you confirm the volumes for the Waha hub by Friday afternoon?")])
    (o,) = r["observations"]
    assert o["region"].startswith("block:") and (o["evidence_date"], o["date_basis"]) == ("2002-02-04", "quoted_header")


def test_undated_quoted_block_uses_the_container_date_as_an_upper_bound(setup):
    r = run(setup, [obs(1, "mb/inbox/3", "The meter list for Katy needs the new numbers added before month end.")])
    (o,) = r["observations"]
    assert (o["evidence_date"], o["date_basis"]) == ("2002-02-06", "container_upper_bound")
    assert any(x["rule"] == "date_from_container" and x["certainty"] == "inferred" for x in o["readings"])


@pytest.mark.parametrize("key,quote,reason", [
    ("mb/inbox/1", "Please confirm the nominations for the Katy hub by noon today", "not found in the raw file"),
    ("mb/inbox/3", LONG.strip(), "not found in the raw file"),                                  # wrong message
    ("mb/inbox/1", "Katy hub by noon", "too short (under 25 characters)"),
    ("mb/inbox/5", "We need the signed confirmation from Calpine today.", "ambiguous"),
    ("mb/inbox/2", LONG.strip(), "found only in quoted text that was not shown in this batch"),
    ("mb/inbox/2", "quoted block 0 omitted; the same text is in message", "not found in the raw file"),  # a pointer
])
def test_rejected_quotes_say_why(setup, key, quote, reason):
    r = run(setup, [obs(1, key, quote)])
    assert r["status"] == "verified" and r["observations"] == []
    assert r["rejected"][0]["reason"].startswith(reason)


# --- attribution: whose words the evidence is ---

def test_own_words_are_attributed_to_the_sender(setup):
    (o,) = run(setup, [obs(1, "mb/inbox/1", LONG.strip())])["observations"]
    assert (o["evidence_kind"], o["evidence_author"], o["original"]) == ("own_words", "a@enron.com", None)


def test_quoted_evidence_is_attributed_to_its_own_author(setup):
    # mb/inbox/3 is sent by a@enron.com; the Waha request inside it was written by c@enron.com.
    (o,) = run(setup, [obs(1, "mb/inbox/3", "Can you confirm the volumes for the Waha hub by Friday afternoon?")])["observations"]
    assert o["evidence_kind"] == "quoted" and o["evidence_author"] == "c@enron.com"
    assert o["original_status"] == "not found in the corpus: known only from this quote"


def test_quote_without_an_author_line_says_so(setup):
    (o,) = run(setup, [obs(1, "mb/inbox/3", "The meter list for Katy needs the new numbers added before month end.")])["observations"]
    assert o["evidence_kind"] == "quoted" and o["evidence_author"].startswith("unknown")


# --- boundary cases for the three match methods ---

def test_whitespace_match_that_takes_in_the_next_line_stays_in_its_region(setup):
    st, _ = setup
    q = "nominations to scheduling before the cutoff."           # crosses the line break in mb/inbox/2
    (o,) = run(setup, [obs(1, "mb/inbox/2", "Done, sent the corrected " + q)])["observations"]
    m = st.messages["mb/inbox/2"]
    assert o["region"] == "new_text" and m.body_start <= o["start"] < o["end"] <= m.new_text_end
    assert " ".join(m.text[o["start"]:o["end"]].split()) == "Done, sent the corrected " + q


def test_quoted_printable_match_inside_a_quoted_block(ingested):
    body = ("Forwarding below.\r\n\r\n -----Original Message-----\r\nFrom: \tlegal@enron.com\r\n"
            "Sent:\tMonday, February 04, 2002 1:47 PM\r\nTo:\ta@enron.com\r\nSubject:\tContract\r\n\r\n"
            "Please return the signed contract=\r\n to legal by Monday morning.\r\n")
    raw = email(subject="FW: Contract", body=body, message_id="<9@x>").replace(
        "Content-Transfer-Encoding: 7bit", "Content-Transfer-Encoding: quoted-printable")
    st = ingested({"mb/inbox/9": raw})
    (b,) = make_batches(st, ["mb/inbox/9"], BATCH_CFG, "p1")
    text = output(b, [obs(1, "mb/inbox/9", "Please return the signed contract to legal by Monday morning.")])
    (o,) = verify.verify_batch(text, b, st, VERIFY_CFG, attempt_id="b001-test-1")["observations"]
    m = st.messages["mb/inbox/9"]
    assert o["match_kind"] == "qp_decoded" and o["region"] == "block:0"
    assert m.text[o["start"]:o["end"]] == o["quote_raw"] and "=\r\n" in o["quote_raw"]
    assert o["evidence_author"] == "legal@enron.com" and o["evidence_date"] == "2002-02-04"


# --- whole-batch failures: nothing is read ---

def _failed(setup, text):
    st, b = setup
    r = verify.verify_batch(text, b, st, VERIFY_CFG, attempt_id="b001-test-1")
    status = {k: ledger.BATCHED for k in b["keys"]}
    unread = verify.apply_to_status(r, b, status)
    assert r["status"] == "failed" and r["errors"]
    assert sorted(unread) == sorted(b["keys"]) and set(status.values()) == {ledger.FAILED}
    return r["errors"]


def test_truncated_json_fails_the_batch(setup):
    st, b = setup
    text = output(b, [obs(1, "mb/inbox/1", LONG.strip())])
    assert "invalid JSON" in _failed(setup, text[: len(text) // 2])[0]


def test_empty_output_fails_the_batch(setup):
    assert "invalid JSON" in _failed(setup, "")[0]


@pytest.mark.parametrize("mutate,expect", [
    (lambda o: o["observations"][0].pop("quote"), "missing 'quote'"),
    (lambda o: o["observations"][0].update(stall_signals=["late"]), "is not one of"),
    (lambda o: o["observations"][0].update(stall_signals=["waiting", "waiting"]), "must be unique"),
    (lambda o: o["observations"][0].update(stall_signals="waiting"), "expected array"),
    (lambda o: o["observations"][0].update(extra=1), "unexpected 'extra'"),
    (lambda o: o.update(batch_id="b999"), "does not match"),
    (lambda o: o.update(attempt_id="b001-old-1"), "stale or foreign staging file"),
    (lambda o: o["messages"][0].update(disposition="no_business_content"), "is not one of"),   # retired value
    (lambda o: o["observations"][0].pop("action_state"), "missing 'action_state'"),
    (lambda o: o["messages"][0].pop("work_items"), "missing 'work_items'"),
    (lambda o: o["messages"][0].update(work_items=-1), "below the minimum"),
    (lambda o: o["messages"][0].update(work_items="2"), "expected integer"),
    (lambda o: o["messages"][0].update(work_items=True), "expected integer"),
])
def test_schema_and_id_problems_fail_the_batch(setup, mutate, expect):
    st, b = setup
    data = json.loads(output(b, [obs(1, "mb/inbox/1", LONG.strip())]))
    mutate(data)
    assert any(expect in e for e in _failed(setup, json.dumps(data)))


def test_read_receipt_must_match_the_manifest_exactly(setup):
    st, b = setup
    base = json.loads(output(b, []))
    missing = dict(base, messages=base["messages"][1:])
    assert any("missing 1" in e for e in _failed(setup, json.dumps(missing)))
    extra = dict(base, messages=base["messages"] + [{"msg_key": "mb/inbox/99", "disposition": "not_business",
                                                      "reason": "x", "work_items": 0}])
    assert any("not in this batch" in e for e in _failed(setup, json.dumps(extra)))
    twice = dict(base, messages=base["messages"] + base["messages"][:1])
    assert any("twice" in e for e in _failed(setup, json.dumps(twice)))


def test_dispositions_must_agree_with_observations(setup):
    st, b = setup
    data = json.loads(output(b, [obs(1, "mb/inbox/1", LONG.strip())]))
    for x in data["messages"]:
        if x["msg_key"] == "mb/inbox/1":
            x["disposition"], x["reason"] = "business_no_process_signal", "x"
    assert any("has observations but disposition" in e for e in _failed(setup, json.dumps(data)))
    data = json.loads(output(b, []))
    data["messages"][0]["reason"] = " "
    assert any("needs a reason" in e for e in _failed(setup, json.dumps(data)))


def test_item_count_mismatch_and_overlapping_quotes_are_flagged_not_failed(setup):
    st, b = setup
    q = LONG.strip()
    data = json.loads(output(b, [obs(1, "mb/inbox/1", q), obs(2, "mb/inbox/1", q)]))   # two quotes, same text
    for x in data["messages"]:
        if x["msg_key"] == "mb/inbox/1":
            x["work_items"] = 3                                                     # says 3, wrote 2
    r = verify.verify_batch(json.dumps(data), b, st, VERIFY_CFG, attempt_id="b001-test-1")
    assert r["status"] == "verified"
    assert {"kind": "work_items_mismatch", "msg_key": "mb/inbox/1", "work_items": 3, "observations": 2} in r["flags"]
    assert {"kind": "overlapping_quotes", "msg_key": "mb/inbox/1", "obs_ids": ["b001-o1", "b001-o2"]} in r["flags"]
    assert run(setup, [obs(1, "mb/inbox/1", q)])["flags"] == []                  # counts agree: no flag


@pytest.mark.parametrize("task", ["", "   ", "\t\r\n"])
def test_an_empty_task_is_flagged_not_failed(setup, task):
    q = LONG.strip()
    r = run(setup, [obs(1, "mb/inbox/1", q, task=task)])
    assert r["status"] == "verified" and len(r["observations"]) == 1           # the quote still stands
    assert r["flags"] == [{"kind": "empty_task", "msg_key": "mb/inbox/1", "obs_id": "b001-o1"}]
    assert run(setup, [obs(1, "mb/inbox/1", q, task="confirm noms")])["flags"] == []


def test_a_verified_batch_marks_its_messages_read(setup):
    st, b = setup
    r = run(setup, [obs(1, "mb/inbox/1", LONG.strip())])
    status = {k: ledger.BATCHED for k in b["keys"]}
    assert verify.apply_to_status(r, b, status) == [] and set(status.values()) == {ledger.READ}


# --- canary and publish gate ---

@pytest.mark.parametrize("checked,rejected,tripped", [(4, 4, False), (5, 2, True), (10, 2, False), (10, 3, True)])
def test_canary_needs_a_minimum_sample(checked, rejected, tripped):
    assert verify.canary_tripped({"quotes": {"checked": checked, "rejected": rejected}}, VERIFY_CFG) is tripped


def test_publish_gate_reverifies_against_the_raw_file(setup):
    st, _ = setup
    (o,) = run(setup, [obs(1, "mb/inbox/1", LONG.strip())])["observations"]
    assert verify.reverify(o, st, VERIFY_CFG)
    assert not verify.reverify(dict(o, start=o["start"] + 1), st, VERIFY_CFG)
    assert not verify.reverify(dict(o, quote="Please confirm the nominations for Waha tomorrow"), st, VERIFY_CFG)


# --- stall evidence (schema extract-v7) ---

KATY = LONG.strip()
CUTOFF = "so scheduling can release the volumes before the pipeline cutoff."       # inside KATY
METER = "The meter list for Katy needs the new numbers added before month end."  # a '>' block in 3
WAHA = "Can you confirm the volumes for the Waha hub by Friday afternoon?"       # c@enron.com's quoted message in 3


def ev(*pairs):
    return [{"signal": s, "quote": q} for s, q in pairs]


def test_a_signal_is_published_only_with_one_verified_evidence_quote(setup):
    o = obs(1, "mb/inbox/1", KATY, stall_signals=["waiting", "follow_up"], stall_evidence=ev(("waiting", CUTOFF)))
    r = run(setup, [o])
    assert r["status"] == "verified"                                              # never a batch failure
    (v,) = r["observations"]
    assert v["stall_signals"] == ["waiting"] and v["stall_signals_claimed"] == ["waiting", "follow_up"]
    (e,) = v["stall_evidence"]
    assert setup[0].messages["mb/inbox/1"].text[e["start"]:e["end"]] == CUTOFF and e["inside_own_quote"] is True
    assert r["rejected_evidence"] == [{"obs_id": "b001-o1", "msg_key": "mb/inbox/1", "signal": "follow_up",
                                       "quote": None, "problem": "mismatch", "reason": "no evidence quote for this signal"}]
    assert {"kind": "stall_evidence_mismatch", "obs_id": "b001-o1", "msg_key": "mb/inbox/1"} in r["flags"]


def test_signal_and_evidence_sets_must_match_one_to_one(setup):
    dup = obs(1, "mb/inbox/1", KATY, stall_signals=["waiting"], stall_evidence=ev(("waiting", CUTOFF), ("waiting", KATY)))
    r = run(setup, [dup])
    assert r["observations"][0]["stall_signals"] == [] and "2 evidence quotes for one signal" in r["rejected_evidence"][0]["reason"]
    extra = obs(1, "mb/inbox/1", KATY, stall_signals=["waiting"], stall_evidence=ev(("waiting", CUTOFF), ("correction", KATY)))
    r = run(setup, [extra])
    assert r["observations"][0]["stall_signals"] == ["waiting"]                  # the listed signal stands
    assert [(x["signal"], x["problem"]) for x in r["rejected_evidence"]] == [("correction", "unlisted")]
    assert r["evidence"]["evidence_for_unlisted_signals"] == 1 and r["evidence"]["signals_dropped"] == 0


def test_evidence_that_fails_verification_drops_its_signal_and_is_logged(setup):
    o = obs(1, "mb/inbox/1", KATY, stall_signals=["waiting", "correction"],
            stall_evidence=ev(("waiting", "the pipeline cutoff was moved to Friday by legal"), ("correction", "cutoff.")))
    r = run(setup, [o])
    assert r["observations"][0]["stall_signals"] == [] and len(r["observations"]) == 1   # the work itself stands
    assert [(x["signal"], x["problem"], x["reason"]) for x in r["rejected_evidence"]] == [
        ("waiting", "unverified", "not found in the raw file"),
        ("correction", "unverified", "too short (under 25 characters)")]
    assert not [f for f in r["flags"] if f["kind"] == "stall_evidence_mismatch"]   # a failed quote, not a mismatch


def test_evidence_may_be_shared_and_overlap_and_keeps_its_own_author(setup):
    a = obs(1, "mb/inbox/3", "I will update the meter list this week.", stall_signals=["waiting"],
            stall_evidence=ev(("waiting", METER)))
    b = obs(2, "mb/inbox/3", WAHA, stall_signals=["follow_up", "waiting"], stall_evidence=ev(("follow_up", METER), ("waiting", WAHA)))
    r = run(setup, [a, b])
    assert [v["stall_signals"] for v in r["observations"]] == [["waiting"], ["follow_up", "waiting"]]
    assert r["evidence"] == {"signals_claimed": 3, "signals_verified": 3, "signals_ungrounded": 0, "signals_dropped": 0,
                             "evidence_for_unlisted_signals": 0, "evidence_inside_own_quote": 1, "evidence_spans_shared": 1}
    assert not [f for f in r["flags"] if f["kind"] == "overlapping_quotes"]       # the no-overlap rule is for `quote`
    waha = r["observations"][1]["stall_evidence"][1]
    assert waha["evidence_kind"] == "quoted" and "c@enron.com" in waha["evidence_author"]


@pytest.mark.parametrize("found,flagged", [(2, True), (4, False)])
def test_a_batch_losing_too_much_evidence_is_flagged_for_review(setup, found, flagged):
    sigs = ["follow_up", "waiting", "correction", "rework", "escalation"]        # 5 claimed: the minimum sample
    o = obs(1, "mb/inbox/1", KATY, stall_signals=sigs, stall_evidence=ev(*[(s, CUTOFF) for s in sigs[:found]]))
    r = run(setup, [o])
    assert r["status"] == "verified"
    assert any(f["kind"] == "stall_evidence_rejection_rate" for f in r["flags"]) is flagged   # 3/5 > 20%; 1/5 is not


def test_the_publish_gate_rechecks_evidence_quotes(setup):
    st, _ = setup
    r = run(setup, [obs(1, "mb/inbox/1", KATY, stall_signals=["waiting"], stall_evidence=ev(("waiting", CUTOFF)))])
    v = r["observations"][0]
    assert verify.reverify(v, st, VERIFY_CFG)
    v["stall_evidence"][0]["start"] += 1                                         # a stored offset gone wrong
    assert not verify.reverify(v, st, VERIFY_CFG)


def test_clean_c_output_is_verified_against_its_own_schema_and_its_signals_stay_claims(setup):
    """extract-v6 (clean-C) has no per-signal evidence: its signals are kept as claims, never published."""
    st, b = setup
    o = obs(1, "mb/inbox/1", LONG.strip(), stall_signals=["waiting", "follow_up"])
    del o["stall_evidence"]
    text = output(b, [o])
    r = verify.verify_batch(text, b, st, VERIFY_CFG, attempt_id="b001-test-1", schema_name="extract-v6")
    assert r["status"] == "verified" and r["schema"] == "extract-v6"
    (v,) = r["observations"]
    assert v["stall_signals"] == [] and v["stall_signals_claimed"] == ["waiting", "follow_up"]
    assert v["signal_grounding"] == "none" and v["stall_evidence"] == []
    assert r["evidence"]["signals_claimed"] == 2 and r["evidence"]["signals_ungrounded"] == 2
    assert r["evidence"]["signals_verified"] == 0 and r["rejected_evidence"] == []
    # versions are never mixed: the same output fails the evidence schema, which requires stall_evidence
    r7 = verify.verify_batch(text, b, st, VERIFY_CFG, attempt_id="b001-test-1", schema_name="extract-v7")
    assert r7["status"] == "failed" and any("stall_evidence" in e for e in r7["errors"])


# --- design H: evidence last (schema extract-v8h) ---

def h_obs(n, key, quote, signals):
    o = obs(n, key, quote, stall_signals=signals)
    o.pop("stall_evidence")
    return o


def h_run(setup, observations, evidence):
    st, b = setup
    return verify.verify_batch(output(b, observations, signal_evidence=evidence), b, st, VERIFY_CFG,
                               attempt_id="b001-test-1", schema_name="extract-v8h")


def hev(oid, sig, quote="", status="quoted", reason="r"):
    return {"obs_id": oid, "signal": sig, "status": status, "quote": quote, "reason": reason}


def test_h_publishes_a_signal_only_with_a_verified_quote_and_keeps_unresolved_claims(setup):
    q = LONG.strip()
    r = h_run(setup, [h_obs(1, "mb/inbox/1", q, ["waiting", "follow_up", "correction"])],
              [hev("b001-o1", "waiting", "release the volumes before the pipeline cutoff"),
               hev("b001-o1", "follow_up", status="unresolved", reason="nothing chases an earlier request"),
               hev("b001-o1", "correction", "release the volumes before the pipeline cutoff tomorrow")])   # not in the file
    (o,) = r["observations"]
    assert r["status"] == "verified" and o["stall_signals"] == ["waiting"]
    assert o["stall_signals_claimed"] == ["waiting", "follow_up", "correction"]
    assert {x["signal"]: x["outcome"] for x in o["signal_outcomes"]} == \
        {"waiting": "published", "follow_up": "unresolved", "correction": "rejected"}
    (pe,) = o["published_evidence"]
    st, _ = setup
    assert st.messages["mb/inbox/1"].text[pe["start"]:pe["end"]] == pe["quote_raw"]
    assert {x["signal"]: x["problem"] for x in r["rejected_evidence"]} == {"follow_up": "unresolved", "correction": "unverified"}
    assert r["evidence"]["signals_unresolved"] == 1 and r["evidence"]["signals_dropped"] == 1


def test_h_a_claimed_signal_with_no_or_two_entries_is_rejected_and_stray_entries_are_logged(setup):
    q = LONG.strip()
    r = h_run(setup, [h_obs(1, "mb/inbox/1", q, ["waiting", "follow_up"])],
              [hev("b001-o1", "follow_up", "release the volumes before the pipeline cutoff"),
               hev("b001-o1", "follow_up", "Please confirm the nominations for the Katy hub"),
               hev("b001-o1", "rework", "Please confirm the nominations for the Katy hub"),        # not claimed
               hev("b001-o9", "waiting", "Please confirm the nominations for the Katy hub")])      # no such observation
    (o,) = r["observations"]
    assert o["stall_signals"] == [] and {x["outcome"] for x in o["signal_outcomes"]} == {"rejected"}
    assert sorted(x["problem"] for x in r["rejected_evidence"]) == ["mismatch", "mismatch", "unlisted", "unlisted"]


def test_h_evidence_must_be_in_the_observations_own_message_as_batched(setup):
    # mb/inbox/2 quotes mb/inbox/1, but its copy is hidden from the batch: H cannot cite it there
    r = h_run(setup, [h_obs(1, "mb/inbox/2", "sent the corrected\r\nnominations to scheduling", ["correction"])],
              [hev("b001-o1", "correction", "release the volumes before the pipeline cutoff")])
    (o,) = r["observations"]
    assert o["stall_signals"] == [] and o["signal_outcomes"][0]["outcome"] == "rejected"
    assert "not shown" in o["signal_outcomes"][0]["reason"]


def test_h_output_without_signal_evidence_fails_its_schema(setup):
    st, b = setup
    r = verify.verify_batch(output(b, [h_obs(1, "mb/inbox/1", LONG.strip(), [])]), b, st, VERIFY_CFG,
                            attempt_id="b001-test-1", schema_name="extract-v8h")
    assert r["status"] == "failed" and any("signal_evidence" in e for e in r["errors"])
