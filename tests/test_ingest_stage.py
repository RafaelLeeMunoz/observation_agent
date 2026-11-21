"""The ingest stage as a command: outputs, the fingerprint cache, and a read-only corpus."""
import hashlib
import sqlite3

from src import ingest

from conftest import email


def _hashes(corpus):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in corpus.rglob("*") if p.is_file()}


def test_stage_writes_outputs_caches_and_never_touches_the_corpus(make_corpus, tmp_path, capsys):
    corpus = make_corpus({"mb/inbox/1": email(), "mb/inbox/2": email(subject="RE: Hello", body="Yes.\r\n",
                                                                     message_id="<2@x>")})
    before = _hashes(corpus)
    db, led = tmp_path / "out" / "ingest.sqlite", tmp_path / "out" / "ledger.csv"
    args = ["--corpus", str(corpus), "--db", str(db), "--ledger", str(led)]

    assert ingest.main(args) == 0
    assert "wrote" in capsys.readouterr().out
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
    con.close()
    assert led.read_text().count("\n") == 3                       # header + 2 rows

    assert ingest.main(args) == 0                                  # unchanged corpus: skip
    assert "cache hit" in capsys.readouterr().out

    (corpus / "mb" / "inbox" / "3.txt").write_bytes(email(subject="New", message_id="<3@x>").encode())
    before[corpus / "mb" / "inbox" / "3.txt"] = hashlib.sha256((corpus / "mb" / "inbox" / "3.txt").read_bytes()).hexdigest()
    assert ingest.main(args) == 0                                  # changed corpus: rebuild
    assert "wrote" in capsys.readouterr().out
    assert _hashes(corpus) == before                               # ingest never wrote to corpus/


def _run(args, capsys):
    code = ingest.main(args)
    return code, "cache hit" in capsys.readouterr().out


def test_a_failed_run_is_never_reused_as_a_cache_hit(make_corpus, tmp_path, capsys):
    # Review defect D2: before the fix, the second run here was a cache hit and exited 0.
    corpus = make_corpus({"mb/inbox/1": email()})
    (corpus / "MANIFEST.csv").write_text("original_path,new_path,bytes,sha256\n"
                                         "corpus/mb/inbox/1.,corpus/mb/inbox/1.txt,1,bad\n")
    args = ["--corpus", str(corpus), "--db", str(tmp_path / "i.sqlite"), "--ledger", str(tmp_path / "l.csv")]
    assert _run(args, capsys) == (1, False)
    assert _run(args, capsys) == (1, False)                         # rebuilt, and still fails


def test_a_replaced_or_half_written_ledger_forces_a_rebuild(make_corpus, tmp_path, capsys):
    # Review defect D3: a stale ledger next to a fresh database used to be trusted.
    corpus = make_corpus({"mb/inbox/1": email()})
    led = tmp_path / "l.csv"
    args = ["--corpus", str(corpus), "--db", str(tmp_path / "i.sqlite"), "--ledger", str(led)]
    assert _run(args, capsys) == (0, False)
    good = led.read_bytes()
    led.write_text("key\nstale\n")
    assert _run(args, capsys) == (0, False)                         # rebuilt, not trusted
    assert led.read_bytes() == good
    assert _run(args, capsys) == (0, True)


def test_a_code_change_changes_the_fingerprint(make_corpus, tmp_path):
    # Review defect D1: the fingerprint used to ignore the code, so a rule change without a
    # version bump would have reused stale output.
    import shutil
    corpus = make_corpus({"mb/inbox/1": email()})
    src = tmp_path / "src_copy"
    shutil.copytree(ingest.SRC_DIR, src, ignore=shutil.ignore_patterns("__pycache__"))
    before = ingest.fingerprint(corpus, src)
    assert ingest.fingerprint(corpus, src) == before                # deterministic
    p = src / "dedupe.py"
    p.write_text(p.read_text(encoding="utf-8").replace("CROSS_MAILBOX_MAX_HOURS = 12",
                                                       "CROSS_MAILBOX_MAX_HOURS = 13"), encoding="utf-8")
    assert ingest.fingerprint(corpus, src) != before


def test_ignored_files_and_manifest_are_part_of_the_fingerprint(make_corpus):
    corpus = make_corpus({"mb/inbox/1": email()})
    before = ingest.fingerprint(corpus)
    (corpus / "stray.bin").write_bytes(b"x")
    assert ingest.fingerprint(corpus) != before


def test_manifest_mismatch_fails_reconciliation(make_corpus):
    corpus = make_corpus({"mb/inbox/1": email()})
    (corpus / "MANIFEST.csv").write_text("original_path,new_path,bytes,sha256\n"
                                         "corpus/mb/inbox/1.,corpus/mb/inbox/1.txt,1,deadbeef\n")
    r = ingest.build(corpus)
    assert r.manifest_problems and not r.reconciliation()["manifest matches"]
