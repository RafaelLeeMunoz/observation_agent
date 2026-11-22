"""R3 quoted text: markers, offsets, block dates, and the two skip rules."""
from datetime import datetime

import pytest

from src import ingest, quoted
from src.quoted import first_marker, mark_originals, mark_thread_repeats, split_body

from conftest import email

LONG = ("Please confirm the nominations for the Katy hub by noon tomorrow so scheduling can "
        "release the volumes before the pipeline cutoff. ")

CASES = {
    "original_message": "Reply text.\r\n\r\n -----Original Message-----\r\nFrom: \tGiron, Darron  \r\n"
                        "Sent:\tFriday, May 04, 2001 9:36 AM\r\nTo:\tVelasco, Jennifer\r\nSubject:\tRE: Books\r\n\r\nThanks.\r\n",
    "forwarded_by":     "FYI\r\n---------------------- Forwarded by Darron C Giron/HOU/ECT on 12/21/2000 03:20 PM "
                        "---------------------------\r\n\r\nText.\r\n",
    "lotus_from_on":    "ok\r\n\r\nFrom: Jennifer Velasco/ENRON@enronXgate on 05/04/2001 10:13 AM\r\nTo: Darron\r\n\r\nText.\r\n",
    "outlook_block":    "ok\r\n\r\nFrom: Bob Smith\r\nSent: Friday, May 04, 2001 9:36 AM\r\nTo: Darron\r\n\r\nText.\r\n",
    "lotus_name_date":  "ok\r\n\r\nJane S Saladino\r\n01/31/2001 07:24 AM\r\nTo: Darron C Giron\r\n\r\nText.\r\n",
    "gt_quote":         "ok\r\n> quoted line\r\n> more\r\n",
}


@pytest.mark.parametrize("kind", list(CASES))
def test_each_marker_is_found_and_offsets_map_back(kind):
    text = "Subject: x\r\n\r\n" + CASES[kind]
    body_start = text.index("\r\n\r\n") + 4
    assert first_marker(text[body_start:])[1] == kind
    end, blocks = split_body(text, body_start)
    assert blocks and blocks[0].kind == kind
    assert blocks[0].start == end
    assert "".join(text[b.start:b.end] for b in blocks) == text[end:]     # nothing lost or doubled
    assert text[body_start:end].startswith(CASES[kind][:2])


def test_lotus_reply_header_with_a_two_digit_year_is_a_marker():
    # farmer-d/logistics/593: the only marker is "Howard B Camp / 12/16/99 12:27 PM / To:".
    body = "Sounds good.\r\n\r\n\r\n\r\nHoward B Camp\r\n12/16/99 12:27 PM\r\nTo: Daren J Farmer/HOU/ECT@ECT\r\n\r\nText.\r\n"
    text = "S: x\r\n\r\n" + body
    end, blocks = split_body(text, 8)
    assert text[8:end].strip() == "Sounds good."
    assert blocks[0].kind == "lotus_name_date"
    assert blocks[0].quoted_date == datetime(1999, 12, 16, 12, 27)
    assert text[slice(*blocks[0].date_offsets)] == "12/16/99 12:27 PM"


def test_lotus_internet_reply_header_with_seconds_is_a_marker():
    # giron-d/sent/692: quoted text used to stay in "new text" because the time has seconds.
    body = ("I think we can.\r\n\r\nDG\r\n\r\n\r\n\"KRISTI GIRON\" <KRISTI.GIRON@cfisd.net> on 08/23/2000 01:40:40 PM\r\n"
            "To: <Darron.C.Giron@enron.com>\r\ncc:  \r\nSubject: Re: baseball\r\n\r\n\r\nLET'S TALK ABOUT BASEBALL.\r\n")
    text = "S: x\r\n\r\n" + body
    end, blocks = split_body(text, 8)
    assert text[8:end].strip() == "I think we can.\r\n\r\nDG"
    b = blocks[0]
    assert b.kind == "lotus_name_date" and b.from_raw == '"KRISTI GIRON" <KRISTI.GIRON@cfisd.net>'
    assert b.quoted_date == datetime(2000, 8, 23, 13, 40, 40)
    assert b.content == "LET'S TALK ABOUT BASEBALL."


def test_lotus_author_line_is_not_part_of_the_block_text():
    # farmer-d/logistics/675 quoting 673: the name line "Mandy MacKinnon@ECT" used to block the match.
    body = ("Please act.\r\n\r\nMandy MacKinnon@ECT\r\n01/11/2000 07:38 PM\r\nTo: Pat Clynes/Corp/Enron@ENRON\r\n"
            "cc:  \r\n\r\nSubject: Unresolved Issues\r\n\r\n" + LONG + "\r\n")
    text = "S: x\r\n\r\n" + body
    _, blocks = split_body(text, 8)
    assert blocks[0].content == quoted.norm_ws(LONG)


def test_wrapped_header_lines_are_not_part_of_the_block_text():
    # farmer-d/logistics/917 quoting 916: a cc: list wrapped onto a second line used to stay in.
    body = ("Thanks all.\r\n\r\nHoward B Camp\r\n03/20/2000 08:46 AM\r\nTo: Karry Kendall/HOU/ECT@ECT, Clem \r\n"
            "Cernosek/HOU/ECT@ECT\r\ncc: Stella L Morris/HOU/ECT@ECT, Rita \r\nWynne/HOU/ECT@ECT \r\n"
            "Subject: HPLR Liquids\r\n\r\n" + LONG + "\r\n")
    text = "S: x\r\n\r\n" + body
    _, blocks = split_body(text, 8)
    assert blocks[0].content == quoted.norm_ws(LONG)


def test_a_long_wrapped_to_list_still_counts_as_header():
    # farmer-d/logistics/1077: the To: list wraps over ~50 lines before Subject:.
    to_lines = "".join(f"Person{i:02d} Name/HOU/ECT@ECT, \r\n" for i in range(40))
    body = ("FYI\r\n\r\nMario Parra Cardoso@ENRON\r\n04/27/2000 11:42 AM\r\nTo: " + to_lines +
            "cc:  \r\nSubject: Press release\r\n\r\n" + LONG + "\r\n")
    text = "S: x\r\n\r\n" + body
    _, blocks = split_body(text, 8)
    assert blocks[0].content == quoted.norm_ws(LONG)


def test_same_thread_original_with_a_lotus_header_is_skipped_end_to_end(make_corpus):
    corpus = make_corpus({
        "mb/inbox/1": email(subject="Unresolved Issues", body=LONG + "\r\n", message_id="<1@x>"),
        "mb/inbox/2": email(subject="Re: Unresolved Issues", message_id="<2@x>", sender="b@enron.com", to="a@enron.com",
                            date="Tue, 5 Feb 2002 09:00:00 -0800",
                            body="Please act.\r\n\r\nMandy MacKinnon@ECT\r\n02/04/2002 01:47 PM\r\nTo: Pat Clynes, Daren \r\n"
                                 "Farmer\r\ncc:  \r\n\r\nSubject: Unresolved Issues\r\n\r\n" + LONG + "\r\n"),
    })
    r = ingest.build(corpus)
    (b,) = r.messages["mb/inbox/2"].blocks
    assert b.skip_reason == "original_in_thread:mb/inbox/1"


def test_outlook_original_appointment_is_a_marker():
    # williams-w3/inbox/73: a forwarded meeting request.
    body = "FYI\r\n\r\n -----Original Appointment-----\r\nFrom: \tBill\r\nSent:\tMon 1/7/2002 9:00 AM\r\nText.\r\n"
    text = "S: x\r\n\r\n" + body
    end, blocks = split_body(text, 8)
    assert text[8:end].strip() == "FYI" and blocks[0].kind == "original_message"


def test_header_block_without_a_sent_line_is_a_marker():
    # giron-d/inbox/174 ("Fwd: snake"): From / To / Subject, no Sent: line, To: folded.
    body = ("Check this out.\r\n\r\nFrom: \"Bishop Keller\" <BKeller@psiusa.com>\r\nTo: Carlos.Giron@psiusa.com,\r\n"
            "        Clint.McDowell@psiusa.com\r\nSubject:  snake\r\n\r\nForwarded text.\r\n")
    text = "S: x\r\n\r\n" + body
    end, blocks = split_body(text, 8)
    assert text[8:end].strip() == "Check this out." and blocks[0].kind == "header_block"


def test_prose_mentioning_from_or_subject_is_not_a_marker():
    for body in ("From: the desk's view, the deal is fine.\r\nWe should talk.\r\n",
                 "Subject: to approval, we go ahead.\r\nFrom: here on, use the new form.\r\n",
                 "From: Bob\r\nThanks for the update.\r\nSubject: noted\r\n"):
        text = "S: x\r\n\r\n" + body
        assert split_body(text, 8) == (len(text), [])


def test_header_only_cut_merges_into_its_block():
    # "-----Original Message-----" and its From:/Sent: lines form one block, not two.
    text = "S: x\r\n\r\n" + CASES["original_message"]
    _, blocks = split_body(text, 8)
    assert len(blocks) == 1 and blocks[0].kind == "original_message"
    assert blocks[0].content == "Thanks."


def test_a_run_of_gt_lines_is_one_block():
    text = "S: x\r\n\r\nok\r\n> one\r\n> two\r\n> three\r\n\r\nmy reply\r\n> later quote\r\n"
    _, blocks = split_body(text, 8)
    assert [b.content for b in blocks] == ["one two three my reply", "later quote"]


def test_no_marker_means_all_new_text():
    text = "Subject: x\r\n\r\nJust a note.\r\n"
    end, blocks = split_body(text, 14)
    assert end == len(text) and blocks == []


def test_blocks_carry_their_own_sender_and_date():
    def first_block(kind):
        text = "S: x\r\n\r\n" + CASES[kind]
        return split_body(text, 8)[1][0]
    b = first_block("original_message")
    assert (b.from_raw, b.quoted_date) == ("Giron, Darron", datetime(2001, 5, 4, 9, 36))
    b = first_block("lotus_from_on")
    assert (b.from_raw, b.quoted_date) == ("Jennifer Velasco/ENRON@enronXgate", datetime(2001, 5, 4, 10, 13))
    b = first_block("lotus_name_date")
    assert (b.from_raw, b.quoted_date) == ("Jane S Saladino", datetime(2001, 1, 31, 7, 24))


def _block(content, key="x"):
    return quoted.QuotedBlock(0, "gt_quote", 0, 0, None, None, content)


def _block_at(index, content):
    return quoted.QuotedBlock(index, "gt_quote", 0, 0, None, None, content)


def test_block_is_skipped_only_when_its_original_is_in_the_same_thread():
    original = quoted.norm_ws(LONG + "Thanks, Daren")
    kept = _block(quoted.norm_ws("Something nobody else wrote. " * 5))
    copy = _block(quoted.norm_ws(LONG))
    short = _block("Thanks")
    same_thread = {"a": "t1", "b": "t1"}
    assert mark_originals({"a": original, "b": "reply"}, {"b": [copy, kept, short]}, same_thread) == (1, 0)
    assert copy.skip_reason == "original_in_thread:a" and copy.links == []
    assert kept.skip_reason is None and short.skip_reason is None


def test_original_in_another_thread_is_kept_for_context_and_linked():
    # The reply needs the text it answers; skipping it here would leave Claude reading "Yes, done."
    copy = _block(quoted.norm_ws(LONG))
    assert mark_originals({"a": quoted.norm_ws(LONG), "b": "Yes, done."}, {"b": [copy]},
                          {"a": "t1", "b": "t2"}) == (0, 1)
    assert copy.skip_reason is None
    assert copy.links == [quoted.Link("a", quoted.AS_NEW_TEXT, "t1")]


def test_a_block_never_matches_its_own_message():
    b = _block(quoted.norm_ws(LONG))
    assert mark_originals({"a": quoted.norm_ws(LONG)}, {"a": [b]}, {"a": "t1"}) == (0, 0)
    assert b.skip_reason is None and b.links == []


def test_repeat_in_thread_skips_later_copies_only():
    first, again, other = _block(quoted.norm_ws(LONG)), _block(quoted.norm_ws(LONG)), _block(quoted.norm_ws(LONG))
    assert mark_thread_repeats(["m1", "m2"], {"m1": [first], "m2": [again]}) == 1
    assert first.skip_reason is None and again.skip_reason == "repeat_in_thread:m1"
    # A block already skipped was never shown, so it can't make a later one a repeat.
    hidden = _block(quoted.norm_ws(LONG)); hidden.skip_reason = "original_in_thread:z"
    assert mark_thread_repeats(["m1", "m2"], {"m1": [hidden], "m2": [other]}) == 0
    assert other.skip_reason is None


def test_a_skipped_repeat_drops_its_links():
    # The copy shown earlier in the same thread carries the links; a hidden copy must not.
    first, again = _block(quoted.norm_ws(LONG)), _block(quoted.norm_ws(LONG))
    for b in (first, again):
        b.links.append(quoted.Link("z", quoted.AS_NEW_TEXT, "t9"))
    mark_thread_repeats(["m1", "m2"], {"m1": [first], "m2": [again]})
    assert first.links and again.links == [] and again.skip_reason == "repeat_in_thread:m1"


def test_copies_kept_in_different_threads_link_both_ways_and_share_one_anchor():
    text = quoted.norm_ws(LONG)
    a0, b1, c0 = _block_at(0, text), _block_at(1, text), _block_at(0, text)
    other = _block_at(0, quoted.norm_ws("A different quote that only one thread has. " * 3))
    blocks = {"m1": [a0], "m2": [other, b1], "m3": [c0]}
    thread_of = {"m1": "t1", "m2": "t2", "m3": "t1"}
    assert quoted.link_cross_thread_copies(blocks, thread_of) == 3
    assert {(l.key, l.block_index) for l in b1.links} == {("m1", 0), ("m3", 0)}    # t2 sees both t1 copies
    assert {(l.key, l.block_index) for l in a0.links} == {("m2", 1)}               # same-thread copy not linked
    assert other.links == []
    order = {"m1": (2,), "m2": (1,), "m3": (3,)}                                    # m2 is the earliest
    quoted.assign_first_seen(blocks, order)
    assert a0.first_seen == b1.first_seen == c0.first_seen == "m2#1"
    assert other.first_seen is None


def test_anchor_prefers_an_original_message_over_quoted_copies():
    text = quoted.norm_ws(LONG)
    a0, b0 = _block_at(0, text), _block_at(0, text)
    a0.links.append(quoted.Link("orig", quoted.AS_NEW_TEXT, "t0"))    # only one copy found the original
    blocks = {"m1": [a0], "m2": [b0]}
    quoted.link_cross_thread_copies(blocks, {"m1": "t1", "m2": "t2"})
    quoted.assign_first_seen(blocks, {"orig": (5,), "m1": (1,), "m2": (2,)})
    assert a0.first_seen == b0.first_seen == "orig"                   # same anchor for every copy


def test_end_to_end_reply_quoting_a_corpus_message(make_corpus):
    corpus = make_corpus({
        "mb/inbox/1": email(subject="Katy noms", body=LONG + "\r\nDaren\r\n", message_id="<1@x>"),
        "mb/inbox/2": email(subject="RE: Katy noms", message_id="<2@x>", date="Tue, 5 Feb 2002 09:00:00 -0800",
                            body="Done.\r\n\r\n -----Original Message-----\r\nFrom: \ta@enron.com\r\n"
                                 "Sent:\tMonday, February 04, 2002 1:47 PM\r\nTo:\tb@enron.com\r\n"
                                 "Subject:\tKaty noms\r\n\r\n" + LONG + "\r\nDaren\r\n",
                            sender="b@enron.com", to="a@enron.com"),
    })
    r = ingest.build(corpus)
    reply = r.messages["mb/inbox/2"]
    assert reply.new_text.strip() == "Done."
    assert reply.thread_id == r.messages["mb/inbox/1"].thread_id        # one conversation
    assert reply.blocks[0].skip_reason == "original_in_thread:mb/inbox/1"
    assert reply.blocks[0].quoted_date == datetime(2002, 2, 4, 13, 47)


def test_end_to_end_forward_under_a_new_subject_keeps_and_links_the_quote(make_corpus):
    # A forward with a new subject lands in a different thread, so the quote is kept for context.
    corpus = make_corpus({
        "mb/inbox/1": email(subject="Katy noms", body=LONG + "\r\nDaren\r\n", message_id="<1@x>"),
        "mb/inbox/2": email(subject="FW: please handle", message_id="<2@x>", date="Tue, 5 Feb 2002 09:00:00 -0800",
                            body="Can you take this?\r\n\r\n -----Original Message-----\r\nFrom: \ta@enron.com\r\n"
                                 "Sent:\tMonday, February 04, 2002 1:47 PM\r\nTo:\tb@enron.com\r\n"
                                 "Subject:\tKaty noms\r\n\r\n" + LONG + "\r\nDaren\r\n",
                            sender="b@enron.com", to="c@enron.com"),
    })
    r = ingest.build(corpus)
    fwd, orig = r.messages["mb/inbox/2"], r.messages["mb/inbox/1"]
    assert fwd.thread_id != orig.thread_id
    b = fwd.blocks[0]
    assert b.skip_reason is None
    assert [(l.key, l.kind) for l in b.links] == [("mb/inbox/1", quoted.AS_NEW_TEXT)]
    assert b.first_seen == "mb/inbox/1"
    assert all(r.reconciliation().values())
