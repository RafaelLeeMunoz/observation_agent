"""Reading the raw evidence. Every stage that needs file text or offsets must use this module.

Coordinate system: every offset stored anywhere is a **byte offset into the raw file**. Files are
read as bytes and decoded without newline translation (never open them in text mode: CRLF -> LF
would shift offsets in 3,231 of the 3,240 files). The corpus is all ASCII, so character offsets
equal byte offsets; ingest checks that for every file and fails reconciliation if it ever isn't.

Windows can't open a name ending in '.' (the original corpus style, `20.`) through normal paths,
and `pathlib` doesn't even list such files there. Listing and reading go through the
extended-length prefix `\\\\?\\` on Windows, which keeps the name exactly as written.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

_EXT = "\\\\?\\"


def os_path(path: str | os.PathLike) -> str:
    """A path string that reaches files named like '20.' on Windows. Elsewhere, unchanged."""
    s = os.fspath(path)
    if sys.platform != "win32" or s.startswith(_EXT):
        return s
    head, tail = os.path.split(s)
    head = os.path.abspath(head or ".")          # directories never end in '.', so this is safe
    return _EXT + os.path.join(head, tail)


def read_bytes(path: str | os.PathLike) -> bytes:
    with open(os_path(path), "rb") as f:
        return f.read()


def decode(raw: bytes) -> tuple[str, str]:
    """(text, status). UTF-8 when valid, else Latin-1 (one char per byte, so offsets still match)."""
    try:
        return raw.decode("utf-8"), "ok"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "decoded_latin1"


def read_raw(path: str | os.PathLike) -> str:
    """The file's text exactly as stored (no newline translation). Use this for quote checks."""
    return decode(read_bytes(path))[0]


def sha256_file(path: str | os.PathLike) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


def walk_files(root: Path) -> list[tuple[tuple[str, ...], str]]:
    """Every file under `root` as (relative parts, os path), names kept exactly as written."""
    base = os_path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, base)
        parts = () if rel_dir == "." else tuple(Path(rel_dir).parts)
        for name in sorted(filenames):
            out.append((parts + (name,), os.path.join(dirpath, name)))
    return out
