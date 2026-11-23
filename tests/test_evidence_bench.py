"""Evidence-support benchmark: one entry per positive stall label, passages resolved to raw offsets."""
import pytest

from src import bench, evidence_bench
from src.batch import make_batches

from conftest import BATCH_CFG, VERIFY_CFG, email

INTRO = "Following up on the open nomination items from last week's call."
A = "Please confirm the nominations for the Katy hub by noon tomorrow."
B = "I will send the corrected meter list to scheduling on Friday."


@pytest.fixture
def setup(ingested):
    st = ingested({"mb/inbox/1": email(subject="Katy", body=INTRO + "\r\n" + A + "\r\n" + B + "\r\n", message_id="<1@x>")})
    return st


def labels():
    act = lambda i, q, sig: {"id": f"mb/inbox/1#{i}", "quote": q, "activity": "x", "actors": [], "action_state": "requested",
                             "recurrence": "unclear", "recurrence_basis": "", "stall_signals": sig, "stall_basis": "text"}
    return {"messages": {"mb/inbox/1": {"outcome": "observations", "must_not": [],
                                        "activities": [act(1, A, ["follow_up"]), act(2, B, ["follow_up"])]}}}


def entry(i, sig, quotes, support="supports", note="", basis="own_text"):
    return {"id": f"mb/inbox/1#{i}", "signal": sig, "support": support, "note": note, "basis": basis,
            "spans": [{"quote": q, "role": "r"} for q in quotes]}


def run(st, entries, lab=None, keys=("mb/inbox/1",), negatives=None, full=False):
    lab = lab or labels()
    _, spans = bench.check_labels(lab, st, list(keys), VERIFY_CFG)
    out, neg, problems = evidence_bench.resolve({"entries": entries}, lab, spans, st, VERIFY_CFG, negatives)
    return (out, neg, problems) if full else (out, problems)


def test_every_positive_label_has_exactly_one_entry_and_no_entry_invents_a_signal(setup):
    out, problems = run(setup, [entry(1, "follow_up", [A]), entry(1, "follow_up", [A]), entry(1, "waiting", [A])])
    assert any("mb/inbox/1#2 follow_up: positive label with no evidence entry" in p for p in problems)
    assert any("#1 follow_up: more than one evidence entry" in p for p in problems)
    assert any("#1 waiting: evidence entry for a signal the label does not carry" in p for p in problems)


def test_passages_get_offsets_author_scope_and_sharing_and_doubt_needs_a_reason(setup):
    out, problems = run(setup, [entry(1, "follow_up", [INTRO, A]),
                                entry(2, "follow_up", [INTRO], "uncertain", "why", "same_message_context")])
    assert problems == []
    intro, own = out["entries"][0]["spans"]
    assert own["scope"] == "activity" and intro["scope"] == "context"      # the shared intro is not #1's own span
    assert setup.messages["mb/inbox/1"].text[own["start"]:own["end"]] == A and own["region"] == "new_text"
    assert intro["shared_with"] == ["mb/inbox/1#2 follow_up"] and own["shared_with"] == []
    _, problems = run(setup, [entry(1, "follow_up", [A]), entry(2, "follow_up", [B], "uncertain")])
    assert any("uncertain entry needs a note" in p for p in problems)
    _, problems = run(setup, [entry(1, "follow_up", [A]), entry(2, "follow_up", [B], basis="same_message_context")])
    assert any("needs a passage with scope context" in p for p in problems)          # basis must match the passages
    assert any("context-dependent entry needs a note" in p for p in problems)
    _, problems = run(setup, [entry(1, "follow_up", ["Please confirm the nominations for Waha today."]), entry(2, "follow_up", [B])])
    assert any("does not verify" in p for p in problems)


LONG = "Please confirm the nominations for the Katy hub by noon tomorrow so scheduling can release the volumes."
REPLY = ("Done, the volumes are released for the Katy hub as asked.\r\n\r\n -----Original Message-----\r\n"
         "From: \ta@enron.com\r\nSent:\tMonday, February 04, 2002 1:47 PM\r\nTo:\tb@enron.com\r\nSubject:\tKaty"
         "\r\n\r\n" + LONG + "\r\n")


@pytest.fixture
def thread(ingested):
    return ingested({"mb/inbox/1": email(subject="Katy", body=LONG + "\r\n", message_id="<1@x>"),
                     "mb/inbox/2": email(subject="RE: Katy", body=REPLY, message_id="<2@x>", sender="bob.smith@enron.com",
                                         to="a@enron.com", date="Tue, 5 Feb 2002 09:00:00 -0800")})


def test_a_passage_from_another_message_needs_the_stored_link_of_a_hidden_block(thread):
    act = {"id": "mb/inbox/2#1", "quote": "Done, the volumes are released for the Katy hub as asked.", "activity": "x",
           "actors": [], "author": "bob.smith@enron.com", "action_state": "done", "recurrence": "unclear", "recurrence_basis": "",
           "stall_signals": ["follow_up"], "stall_basis": "text"}
    lab = {"messages": {"mb/inbox/1": {"outcome": "not_business", "activities": [], "must_not": []},
                        "mb/inbox/2": {"outcome": "observations", "activities": [act], "must_not": []}}}
    e = {"id": "mb/inbox/2#1", "signal": "follow_up", "support": "supports", "basis": "cross_message_context", "note": "n",
         "spans": [{"quote": act["quote"], "role": "r"}, {"quote": LONG, "role": "r", "in_message": "mb/inbox/1"}]}
    out, _, problems = run(thread, [e], lab, ("mb/inbox/1", "mb/inbox/2"), full=True)
    assert problems == []
    own, linked = out["entries"][0]["spans"]
    assert (own["scope"], own["visibility"]) == ("activity", "batch")
    assert (linked["scope"], linked["visibility"], linked["msg_key"]) == ("other_message", "linked_original", "mb/inbox/1")
    assert linked["via_block"] is not None and own["author_named_in_label"] is True
    e["spans"][1]["in_message"] = "mb/inbox/2"                    # the hidden copy itself is not shown: refused
    assert any("does not verify" in p for p in run(thread, [e], lab, ("mb/inbox/1", "mb/inbox/2"), full=True)[2])
    e["spans"][1] = {"quote": act["quote"], "role": "r", "in_message": "mb/inbox/2"}
    lab["messages"]["mb/inbox/1"]["activities"] = []
    e2 = {**e, "spans": [{"quote": "Please confirm the nominations for the Katy hub by noon tomorrow", "role": "r",
                          "in_message": "mb/inbox/1"}], "id": "mb/inbox/2#1"}
    lab2 = {"messages": {"mb/inbox/1": lab["messages"]["mb/inbox/1"],
                         "mb/inbox/2": {"outcome": "observations", "must_not": [], "activities": [act]}}}
    st = thread
    st.messages["mb/inbox/2"].blocks = []                         # no hidden block linked to mb/inbox/1
    assert any("no stored link" in p for p in run(st, [e2], lab2, ("mb/inbox/1", "mb/inbox/2"), full=True)[2])


def test_negative_claims_must_not_contradict_the_labels_and_cite_real_text(setup):
    neg = {"claims": [{"id": "mb/inbox/1#1", "signal": "waiting", "why": "a request alone", "rule": "rules 5",
                       "spans": [{"quote": A, "role": "r"}]}]}
    _, out, problems = run(setup, [entry(1, "follow_up", [A]), entry(2, "follow_up", [B])], negatives=neg, full=True)
    assert problems == [] and out["claims"][0]["spans"][0]["scope"] == "activity"
    neg["claims"][0]["signal"] = "follow_up"                      # the approved label carries it: not unsupported
    _, _, problems = run(setup, [entry(1, "follow_up", [A]), entry(2, "follow_up", [B])], negatives=neg, full=True)
    assert any("the approved label carries this signal" in p for p in problems)


def test_a_passage_by_someone_the_label_does_not_name_is_flagged_for_review(setup):
    lab = labels()
    lab["messages"]["mb/inbox/1"]["activities"][0]["author"] = "someone.else@enron.com"
    out, problems = run(setup, [entry(1, "follow_up", [A]), entry(2, "follow_up", [B])], lab)
    assert problems == [] and out["entries"][0]["spans"][0]["author_named_in_label"] is False
