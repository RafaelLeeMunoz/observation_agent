"""The raw-evidence reader: one coordinate system (raw-byte offsets) for every stage."""
from src import rawio


def test_read_raw_keeps_line_endings_exactly(tmp_path):
    p = tmp_path / "m.txt"
    data = b"Subject: x\r\n\r\nline one\nline two\r\nend"       # mixed endings, as in the corpus
    p.write_bytes(data)
    text = rawio.read_raw(p)
    assert text.encode() == data and len(text) == len(data)
    i = data.index(b"line two")
    assert text[i:i + 8] == "line two"                             # char offset == byte offset
    # The trap: text mode translates CRLF and shifts every later offset.
    assert len(p.read_text(encoding="utf-8")) == len(data) - data.count(b"\r\n")


def test_non_utf8_bytes_fall_back_one_char_per_byte(tmp_path):
    p = tmp_path / "m.txt"
    p.write_bytes(b"caf\xe9 \r\n")
    text, status = rawio.decode(p.read_bytes())
    assert status == "decoded_latin1" and len(text) == 7


def test_walk_files_reports_relative_parts(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "1.txt").write_bytes(b"x")
    (tmp_path / "top.md").write_bytes(b"y")
    assert [parts for parts, _ in rawio.walk_files(tmp_path)] == [("top.md",), ("a", "b", "1.txt")]
