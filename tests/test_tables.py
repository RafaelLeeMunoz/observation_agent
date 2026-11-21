"""R4 giant or tabular messages, on text in the real row format."""
from src import ingest, tables

from conftest import email

HEADER = ("  NOM_ID   PIPE  COUNTERPARTY  FLOW_DATE   SCHEDULED    ACTUAL  VARIANCE  STATUS     METER\r\n"
          "--------------------------------------------------------------------------------------------------------\r\n")
ROWS = [
    "  1000000  HPL   TXU-0000    1/ 1/2001          0          0         +0  PENDING    METER-000000",
    "  1000001  HPL   TXU-0001    8/ 2/2001        911        331       +580  SCHEDULED  METER-000007",
    "  1000002  HPL   TXU-0002   15/ 3/2001      1,822        662     +1,160  SCHEDULED  METER-000014",
    "  1000003  HPL   TXU-0003   22/ 4/2001      2,733        993     +1,740  PENDING    METER-000021",
]
BODY = "Team,\r\n\r\nPasting the position below.\r\n\r\n" + HEADER + "\r\n".join(ROWS) + "\r\n\r\nDaren\r\n"


def parse(body):
    text = "Subject: x\r\n\r\n" + body
    return tables.parse_table(text, 14), text


def test_rows_parse_from_the_real_text_format_with_offsets():
    t, text = parse(BODY)
    assert len(t.rows) == 4 and t.bad_rows == []
    r = t.rows[2]
    assert r.row_no == 3
    assert text[r.start:r.end] == ROWS[2]                       # offsets point at the raw line
    assert r.values == {"NOM_ID": 1000002, "PIPE": "HPL", "COUNTERPARTY": "TXU-0002", "FLOW_DATE": "2001-03-15",
                        "SCHEDULED": 1822, "ACTUAL": 662, "VARIANCE": 1160, "STATUS": "SCHEDULED",
                        "METER": "METER-000014"}
    assert t.prose == ["Team,", "Pasting the position below.", "Daren"]


def test_date_format_is_decided_for_the_whole_column():
    t, _ = parse(BODY)
    assert t.column_types["FLOW_DATE"] == "date day/month"     # 15/3 and 22/4 can't be month/day
    assert t.rows[1].values["FLOW_DATE"] == "2001-02-08"       # so 8/ 2 is 8 Feb, not 2 Aug
    info = t.summary["columns"]["FLOW_DATE"]
    assert (info["invalid_as_day_month"], info["invalid_as_month_day"]) == (0, 2)


def test_ambiguous_and_month_day_columns():
    amb = HEADER + "\r\n".join(r.replace("15/ 3", " 5/ 3").replace("22/ 4", " 2/ 4") for r in ROWS) + "\r\n"
    t, _ = parse(amb)
    assert t.column_types["FLOW_DATE"] == "date ambiguous"
    assert t.rows[1].values["FLOW_DATE"] == "8/ 2/2001"         # kept as written, never guessed
    md = HEADER + "\r\n".join(r.replace("15/ 3", " 3/15").replace("22/ 4", " 4/22") for r in ROWS) + "\r\n"
    assert parse(md)[0].column_types["FLOW_DATE"] == "date month/day"


def test_variance_check_passes_and_catches_a_bad_row():
    t, _ = parse(BODY)
    assert t.summary["variance_check"] == {"rows": 4, "pass": 4, "failed_rows": []}
    broken = BODY.replace("+1,160", "+1,161")
    assert parse(broken)[0].summary["variance_check"]["failed_rows"] == [3]


def test_a_malformed_row_is_reported_not_dropped():
    body = BODY.replace("  PENDING    METER-000021", "")        # row 4 loses two columns
    t, _ = parse(body)
    assert len(t.rows) == 3 and len(t.bad_rows) == 1


def test_size_limit_uses_the_body_and_is_exact():
    assert not tables.needs_special_handling("x" * 64_000)
    assert tables.needs_special_handling("x" * 64_001)


def test_chunks_cover_the_body_exactly_and_cut_at_paragraphs():
    para = "The pipeline will be down for maintenance this weekend, so please move volumes. " * 12
    body = ("\r\n\r\n".join(f"Paragraph {i}. {para}" for i in range(120))) + "\r\n"
    text = "Subject: x\r\n\r\n" + body
    chunks = tables.chunk_body(text, 14, limit=32_000)
    assert len(chunks) > 1
    assert "".join(text[a:b] for a, b in chunks) == body                 # nothing lost or repeated
    assert all(b - a <= 32_000 for a, b in chunks)
    assert all(text[b - 4:b] == "\r\n\r\n" for a, b in chunks[:-1])      # cut at blank lines
    assert [a for a, _ in chunks[1:]] == [b for _, b in chunks[:-1]]     # consecutive


def test_chunking_falls_back_to_a_hard_cut_without_line_breaks():
    text = "S: x\r\n\r\n" + "x" * 70_000
    chunks = tables.chunk_body(text, 8, limit=32_000)
    assert [b - a for a, b in chunks] == [32_000, 32_000, 6_000]


def test_oversized_prose_message_is_chunked_by_ingest(make_corpus):
    para = "Please confirm the meter volumes for the month and send the corrected nominations. " * 10
    corpus = make_corpus({"mb/inbox/1": email(body="\r\n\r\n".join([para] * 100))})
    r = ingest.build(corpus)
    m = r.messages["mb/inbox/1"]
    assert m.body_bytes > 64_000 and m.special_handling == "chunked" and len(m.chunks) >= 3
    assert all(r.reconciliation().values())


def test_huge_headers_with_a_small_body_are_standard(make_corpus):
    many = ", ".join(f"person{i:04d}@enron.com" for i in range(3000))          # ~66 KB of To:
    corpus = make_corpus({"mb/inbox/1": email(to=many, body="Short note.\r\n" * 200)})
    m = ingest.build(corpus).messages["mb/inbox/1"]
    assert m.size_bytes > 64_000 and m.body_bytes < 64_000
    assert m.special_handling is None


def test_a_giant_table_is_one_message_not_one_event_per_row(make_corpus):
    rows = "\r\n".join(f"  {1000000 + i}  HPL   TXU-{i:04d}   {1 + i % 28}/{1 + i % 12:2d}/2001   "
                       f"   {i * 3:,}   {i:,}   +{i * 2:,}  PENDING    METER-{i:06d}" for i in range(1000))
    corpus = make_corpus({"mb/logistics/1": email(body="Team,\r\n\r\n" + HEADER + rows + "\r\n\r\nDaren\r\n")})
    r = ingest.build(corpus)
    m = r.messages["mb/logistics/1"]
    assert m.special_handling == "table_parsed"
    assert len(m.table.rows) == 1000 and m.table.summary["variance_check"]["pass"] == 1000
    assert len(r.ledger_rows()) == 1 and len(r.threads) == 1
