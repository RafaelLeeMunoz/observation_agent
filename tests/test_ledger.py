"""R8 coverage ledger: every file has a row; only receipted, valid batches count as read."""
from src import ledger, ingest

from conftest import email


def test_only_keys_on_a_valid_receipt_become_read():
    status = {k: ledger.BATCHED for k in ("a", "b", "c")}
    unread = ledger.apply_batch_result(status, ["a", "b", "c"], receipt_keys=["a", "c"], output_valid=True)
    assert status == {"a": ledger.READ, "b": ledger.FAILED, "c": ledger.READ}
    assert unread == ["b"]
    assert ledger.coverage_problems(status) == ["b"]


def test_a_failed_batch_is_never_read_even_with_a_full_receipt():
    status = {"a": ledger.BATCHED, "b": ledger.BATCHED}
    unread = ledger.apply_batch_result(status, ["a", "b"], receipt_keys=["a", "b"], output_valid=False)
    assert unread == ["a", "b"] and set(status.values()) == {ledger.FAILED}


def test_every_file_gets_one_row_and_copies_are_never_sent(make_corpus):
    body = "Same announcement for everyone.\r\n"
    corpus = make_corpus({
        "giron-d/inbox/1": email(body=body, message_id="<1@x>"),
        "williams-w3/inbox/2": email(body=body, message_id="<2@x>", sender="no.address@enron.com",
                                     date="Mon, 4 Feb 2002 16:47:02 -0800"),
        "giron-d/inbox/3": email(subject="Other", body="Something else.\r\n", message_id="<3@x>"),
    })
    r = ingest.build(corpus)
    rows = {row.key: row for row in r.ledger_rows()}
    assert len(rows) == 3
    assert rows["williams-w3/inbox/2"].ingest_status == "duplicate_of:giron-d/inbox/1"
    assert rows["williams-w3/inbox/2"].duplicate_reason == "same_mail_in_two_mailboxes"
    assert rows["williams-w3/inbox/2"].analysis_status == ledger.NOT_APPLICABLE
    assert rows["giron-d/inbox/1"].analysis_status == ledger.PENDING
    assert r.messages["giron-d/inbox/1"].seen_in_mailboxes == ["giron-d", "williams-w3"]
    assert all(r.reconciliation().values())
