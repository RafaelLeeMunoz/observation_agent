"""File names: our `20.txt` copy and the original `20.` resolve to the same message."""
import os

import pytest

from src import ingest, rawio

from conftest import email


def test_both_name_styles_give_the_same_key():
    assert ingest.key_from_parts("giron-d", "inbox", "20.") == "giron-d/inbox/20"
    assert ingest.key_from_parts("giron-d", "inbox", "20.txt") == "giron-d/inbox/20"


def test_non_email_names_are_ignored():
    for name in ("ABOUT.md", "MANIFEST.csv", "20", "20.txt.bak", ".gitattributes", "x20.txt"):
        assert ingest.key_from_parts("giron-d", "inbox", name) is None


def test_citations_use_the_original_trailing_dot_name():
    assert ingest.original_path("giron-d/inbox/20") == "corpus/giron-d/inbox/20."


def test_discover_skips_non_email_files(make_corpus):
    corpus = make_corpus({"mb/inbox/2": email(), "mb/inbox/10": email()})
    (corpus / "ABOUT.md").write_text("about")
    (corpus / "mb" / "inbox" / "deep").mkdir()
    (corpus / "mb" / "inbox" / "deep" / "3.txt").write_text("too deep")
    found, ignored = ingest.discover(corpus)
    assert [f.key for f in found] == ["mb/inbox/2", "mb/inbox/10"]       # numeric order
    assert ignored == ["ABOUT.md", "mb/inbox/deep/3.txt"]                # reported, not silent


def _write(path, data: bytes):
    with open(rawio.os_path(path), "wb") as f:      # works for '20.' on Windows too
        f.write(data)


def test_original_trailing_dot_files_are_found_and_read(tmp_path):
    # Runs on every platform. On Windows, pathlib can't even list '20.' (it silently skipped such
    # files before the review); rawio lists and opens it through the extended-length prefix.
    folder = tmp_path / "corpus" / "mb" / "inbox"
    folder.mkdir(parents=True)
    dot = folder / "20."
    _write(dot, email(subject="Trailing dot").encode())
    try:
        found, _ = ingest.discover(tmp_path / "corpus")
        assert [(f.key, f.local_path) for f in found] == [("mb/inbox/20", "corpus/mb/inbox/20.")]
        r = ingest.build(tmp_path / "corpus")
        assert r.messages["mb/inbox/20"].subject == "Trailing dot"
        assert r.messages["mb/inbox/20"].original_path == "corpus/mb/inbox/20."
        _write(folder / "20.txt", email().encode())
        with pytest.raises(ValueError):                 # both styles for one message: refuse
            ingest.discover(tmp_path / "corpus")
    finally:
        os.remove(rawio.os_path(dot))
