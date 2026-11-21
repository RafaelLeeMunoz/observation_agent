"""Shared fixtures. No test ever calls a model."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import ingest

ROOT = Path(__file__).resolve().parent.parent


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def email(subject="Hello", body="Body text.\n", date="Mon, 4 Feb 2002 13:47:02 -0800 (PST)",
          sender="a@enron.com", to="b@enron.com", cc=None, message_id="<1.x@thyme>", extra="") -> str:
    """A raw email in the corpus format (CRLF headers, blank line, body)."""
    lines = [f"Message-ID: {message_id}"]
    if date is not None:
        lines.append(f"Date: {date}")
    lines += [f"From: {sender}", f"To: {to}", f"Subject: {subject}"]
    if cc:
        lines.append(f"Cc: {cc}")
    lines += ["Mime-Version: 1.0", "Content-Type: text/plain; charset=us-ascii",
              "Content-Transfer-Encoding: 7bit"]
    if extra:
        lines.append(extra)
    return "\r\n".join(lines) + "\r\n\r\n" + body


@pytest.fixture
def make_corpus(tmp_path):
    """make_corpus({"mb/folder/1": raw_text, ...}) writes files as N.txt and returns the dir."""
    def _make(files: dict[str, str]) -> Path:
        corpus = tmp_path / "corpus"
        for key, raw in files.items():
            mailbox, folder, n = key.split("/")
            p = corpus / mailbox / folder / f"{n}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw.encode("utf-8"))
        return corpus
    return _make


@pytest.fixture(scope="session")
def corpus_result():
    """The real corpus, ingested once per test session (offline)."""
    return ingest.build(ROOT / "corpus")
