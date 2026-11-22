"""Stage 2, batch: coverage, whole threads, token budget, pointers, manifest, safety."""
import pytest

from src import batch
from src.batch import CLOSE, OPEN, check_batches, make_batches

from conftest import BATCH_CFG, email

LONG = ("Please confirm the nominations for the Katy hub by noon tomorrow so scheduling can "
        "release the volumes before the pipeline cutoff. ")
REPLY = ("Done.\r\n\r\n -----Original Message-----\r\nFrom: \ta@enron.com\r\nSent:\tMonday, February 04, 2002 1:47 PM"
         "\r\nTo:\tb@enron.com\r\nSubject:\tKaty noms\r\n\r\n" + LONG + "\r\nDaren\r\n")


def _corpus():
    return {
        "mb/inbox/1": email(subject="Katy noms", body=LONG + "\r\nDaren\r\n", message_id="<1@x>"),
        "mb/inbox/2": email(subject="RE: Katy noms", body=REPLY, message_id="<2@x>", sender="b@enron.com",
                            to="a@enron.com", date="Tue, 5 Feb 2002 09:00:00 -0800"),
        "mb/inbox/3": email(subject="Meter list", body="Please add the new meters to the list.\r\n", message_id="<3@x>",
                            to=", ".join(f"p{i}@enron.com" for i in range(15)), date="Wed, 6 Feb 2002 09:00:00 -0800"),
    }


def test_every_message_once_threads_whole_and_deterministic(ingested):
    st = ingested(_corpus())
    keys = [m.key for m in st.unique()]
    b1 = make_batches(st, keys, BATCH_CFG, "p1")
    b2 = make_batches(st, keys, BATCH_CFG, "p1")
    assert [b["fingerprint"] for b in b1] == [b["fingerprint"] for b in b2]
    assert all(check_batches(b1, st, keys).values())
    assert sorted(k for b in b1 for k in b["keys"]) == sorted(keys)
    # a prompt-version change changes the cache key, not the batch contents
    assert make_batches(st, keys, BATCH_CFG, "p2")[0]["fingerprint"] != b1[0]["fingerprint"]


def test_a_slice_must_hold_whole_threads(ingested):
    st = ingested(_corpus())
    with pytest.raises(ValueError, match="not whole"):
        make_batches(st, ["mb/inbox/2"], BATCH_CFG, "p1")          # thread also holds mb/inbox/1


def test_skipped_quote_becomes_a_marked_pointer_recorded_in_the_manifest(ingested):
    st = ingested(_corpus())
    (b,) = make_batches(st, [m.key for m in st.unique()], BATCH_CFG, "p1")
    assert b["pointers"] == [{"key": "mb/inbox/2", "block": 0, "points_to": "mb/inbox/1",
                              "reason": "original_in_thread"}]
    assert f"{OPEN}BATCHER: quoted block 0 omitted; the same text is in message mb/inbox/1" in b["text"]
    assert b["text"].count(LONG.strip()) == 1                       # the text is shown once, in mb/inbox/1


def test_headers_are_trimmed_and_new_text_is_exact(ingested):
    st = ingested(_corpus())
    (b,) = make_batches(st, [m.key for m in st.unique()], BATCH_CFG, "p1")
    assert "p9@enron.com (+5 more)" in b["text"] and "p10@enron.com" not in b["text"]
    m = st.messages["mb/inbox/3"]
    assert m.new_text in b["text"]                                  # raw text, unchanged
    assert b["render"]["mb/inbox/3"] == "full"


def test_token_budget_packs_batches_and_keeps_threads_together(ingested):
    st = ingested(_corpus())
    keys = [m.key for m in st.unique()]
    small = dict(BATCH_CFG, token_budget=150)
    out = make_batches(st, keys, small, "p1")
    assert len(out) >= 2
    for b in out:                                                   # a thread never straddles batches
        assert not ({"mb/inbox/1", "mb/inbox/2"} & set(b["keys"])) or {"mb/inbox/1", "mb/inbox/2"} <= set(b["keys"])


def test_thread_over_the_hard_cap_is_split_at_message_boundaries(ingested):
    st = ingested(_corpus())
    keys = [m.key for m in st.unique()]
    tiny = dict(BATCH_CFG, token_budget=100, hard_cap_tokens=120)
    out = make_batches(st, keys, tiny, "p1")
    text = "".join(b["text"] for b in out)
    assert "(part 1 of 2)" in text and "(part 2 of 2; continued from the previous part)" in text
    assert all(check_batches(out, st, keys).values())
    # the manifest records the split explicitly, in every batch that holds a part
    tid = st.messages["mb/inbox/1"].thread_id
    holding = [b["batch_id"] for b in out if tid in b["threads"]]
    assert len(holding) == 2
    for b in out:
        if tid in b["threads"]:
            assert b["split_threads"] == {tid: holding}
        else:
            assert tid not in b["split_threads"]


def test_table_and_chunked_messages_are_rendered_and_recorded(ingested):
    header = ("  NOM_ID   PIPE  COUNTERPARTY  FLOW_DATE   SCHEDULED    ACTUAL  VARIANCE  STATUS     METER\r\n"
              "-----------------------------------------------------------------------------------------\r\n")
    rows = "\r\n".join(f"  {1000000 + i}  HPL   TXU-{i:04d}   {1 + i % 28}/{1 + i % 12:2d}/2001   "
                       f"   {i * 3:,}   {i:,}   +{i * 2:,}  PENDING    METER-{i:06d}" for i in range(1000))
    para = "Please confirm the meter volumes for the month and send the corrected nominations. " * 10
    st = ingested({
        "mb/logistics/1": email(subject="Position", body="Team,\r\n\r\n" + header + rows + "\r\n\r\nDaren\r\n"),
        "mb/inbox/2": email(subject="Long note", body="\r\n\r\n".join([para] * 100), message_id="<2@x>"),
    })
    out = make_batches(st, [m.key for m in st.unique()], BATCH_CFG, "p1")
    render = {k: v for b in out for k, v in b["render"].items()}
    assert render["mb/logistics/1"] == "table_summary"
    assert {v for k, v in render.items() if k == "mb/inbox/2"} and render["mb/inbox/2"].startswith("chunk ")
    text = "".join(b["text"] for b in out)
    assert "1,000 table rows are not shown" in text and "METER-000500" not in text
    assert "part 1 of" in text
    assert max(len(l) for l in text.splitlines()) <= batch.LINE_LIMIT          # the summary spans several lines


def test_a_batch_says_how_long_it_is_and_where_it_ends(ingested):
    st = ingested(_corpus())
    out = make_batches(st, [m.key for m in st.unique()], BATCH_CFG, "p1")
    for b in out:
        first, *_, last = b["text"].splitlines()
        assert last == f"{OPEN}END BATCH {b['batch_id']}{CLOSE}"
        assert f", {len(b['text'].splitlines()):,} lines." in first and f"END BATCH {b['batch_id']}" in first
    checks = check_batches(out, st, [m.key for m in st.unique()])
    assert all(checks.values())
    out[0]["text"] = out[0]["text"].rsplit(OPEN + "END BATCH", 1)[0]          # a batch cut short
    out[0]["text"] += "x" * (batch.LINE_LIMIT + 1) + "\n"                    # and an over-long line
    checks = check_batches(out, st, [m.key for m in st.unique()])
    assert not checks["every batch states its line count and ends with its END BATCH line"]
    assert not checks[f"no line longer than {batch.LINE_LIMIT:,} characters (one Read returns it whole)"]


def test_batcher_marks_are_detected_if_they_ever_appear_in_the_corpus(ingested, make_corpus, tmp_path):
    from src import ingest
    # Ingest itself refuses such a file (non-ASCII breaks the raw-byte offset check) ...
    corpus = make_corpus({"mb/inbox/9": email(body=f"odd {OPEN}text{CLOSE} here\r\n")})
    assert ingest.main(["--corpus", str(corpus), "--db", str(tmp_path / "x.sqlite"),
                        "--ledger", str(tmp_path / "x.csv")]) == 1
    (corpus / "mb" / "inbox" / "9.txt").unlink()
    # ... and the batcher's own check catches it too (defence in depth).
    st = ingested({"mb/inbox/1": email(body="plain text\r\n")})
    st.messages["mb/inbox/1"]._text = st.messages["mb/inbox/1"].text + f"{OPEN}x{CLOSE}"
    checks = check_batches(make_batches(st, ["mb/inbox/1"], BATCH_CFG, "p1"), st, ["mb/inbox/1"])
    assert checks["batcher marks never occur in the corpus"] is False


def test_write_puts_text_and_manifest_on_disk(ingested, tmp_path):
    import json
    st = ingested(_corpus())
    out = make_batches(st, [m.key for m in st.unique()], BATCH_CFG, "p1")
    path = batch.write(out, tmp_path / "batches")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert [b["batch_id"] for b in manifest["batches"]] == ["b001"] and "text" not in manifest["batches"][0]
    # read raw bytes: text mode would turn the emails' CRLF into LF
    assert (tmp_path / "batches" / "b001.txt").read_bytes().decode("utf-8") == out[0]["text"]
