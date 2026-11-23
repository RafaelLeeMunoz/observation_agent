"""The shared-files lock holds across separate PROCESSES, not only threads (race found 23 Nov 2026)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src import batch, extract, filelock

from conftest import email

ROOT = Path(__file__).resolve().parent.parent

COUNTER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from src.filelock import SharedFileLock, write_atomic
lock, data, n = Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
for _ in range(n):
    with SharedFileLock(lock):
        v = json.loads(data.read_text(encoding="utf-8"))
        v["count"] += 1
        write_atomic(data, json.dumps(v))
"""


def spawn(code, *args):
    return subprocess.Popen([sys.executable, "-c", code, str(ROOT), *map(str, args)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_separate_processes_never_lose_an_update_under_the_lock(tmp_path):
    data, lock = tmp_path / "counter.json", tmp_path / ".shared.lock"
    data.write_text(json.dumps({"count": 0}), encoding="utf-8")
    procs = [spawn(COUNTER, lock, data, 40) for _ in range(6)]
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, err.decode()[-800:]
    assert json.loads(data.read_text(encoding="utf-8"))["count"] == 6 * 40      # every read-modify-write kept
    assert not list(tmp_path.glob("*.tmp"))


HOLD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from src.filelock import SharedFileLock
with SharedFileLock(Path(sys.argv[2])):
    print("held", flush=True)
    time.sleep(float(sys.argv[3]))
"""


def test_a_lock_held_by_another_process_blocks_until_it_is_released(tmp_path):
    lock = tmp_path / ".shared.lock"
    p = spawn(HOLD, lock, 3)
    assert p.stdout.readline().strip() == b"held"
    with pytest.raises(TimeoutError):
        with filelock.SharedFileLock(lock, timeout=0.5):
            pass
    p.communicate(timeout=60)
    with filelock.SharedFileLock(lock, timeout=5):                        # free again once the holder exits
        pass


FINALIZE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from src import extract, store
root, db, corpus_root, bid, rounds = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5], int(sys.argv[6])
st = store.load(db, root=corpus_root)
p = extract.Paths(root)
b = extract.load_batch(p, "dev", bid)
vcfg = {"min_quote_chars": 25, "canary_min_quotes": 5, "canary_max_fail_rate": 0.2,
        "evidence_flag_min_signals": 5, "evidence_flag_max_fail_rate": 0.2}
for _ in range(rounds):
    rec = extract.prepare(p, "dev", bid, "haiku", "extract-v8c", "extract-v6")
    out = {"batch_id": bid, "attempt_id": rec["attempt_id"], "observations": [],
           "messages": [{"msg_key": k, "disposition": "business_no_process_signal", "reason": "fyi", "work_items": 0}
                        for k in b["keys"]]}
    (p.staging / f"{bid}.json").write_text(json.dumps(out), encoding="utf-8")
    res = extract.finalize(p, "dev", bid, st, vcfg, usage={"models_observed": ["claude-haiku-5-5"]})
    assert res["status"] == "verified", res["errors"]
"""


def test_finalize_run_as_separate_processes_keeps_the_shared_files_whole(ingested, tmp_path):
    dates = ["Mon, 4 Feb 2002 09:00:00 -0800", "Tue, 5 Feb 2002 09:00:00 -0800", "Wed, 6 Feb 2002 09:00:00 -0800",
             "Thu, 7 Feb 2002 09:00:00 -0800"]
    st = ingested({f"mb/inbox/{i}": email(subject=f"Topic {i}", body=f"Please confirm the meter {i} volumes today.\r\n",
                                          message_id=f"<{i}@x>", date=dates[i - 1]) for i in range(1, 5)})
    root = tmp_path / "repo"
    bs = batch.make_batches(st, [m.key for m in st.unique()], {"chars_per_token": 4, "token_budget": 1,
                                                              "hard_cap_tokens": 40000, "recipients_shown": 10}, "p")
    batch.write(bs, root / "data" / "batches" / "dev")
    assert len(bs) == 4
    rounds = 6
    procs = [spawn(FINALIZE, root, tmp_path / "ingest.sqlite", tmp_path, b["batch_id"], rounds) for b in bs]
    for p in procs:
        out, err = p.communicate(timeout=300)
        assert p.returncode == 0, err.decode()[-1500:]
    status = json.loads((root / "data" / "extract" / "status.json").read_text(encoding="utf-8"))
    assert all(status[k] == "read" for b in bs for k in b["keys"])          # no process lost another's marks
    log = (root / "data" / "logs" / "runlog.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == len(bs) * rounds and all(json.loads(line)["status"] == "verified" for line in log)
    assert not list((root / "data" / "extract").glob("*.tmp"))


def test_finalize_writes_every_shared_file_only_while_holding_the_cross_process_lock(ingested, tmp_path, monkeypatch):
    """Wiring, deterministically: the mutual exclusion is proven above; here, finalize must do all its shared-file
    work (status.json, run log, rejections log) inside that lock."""
    st = ingested({"mb/inbox/1": email(subject="Topic", body="Please confirm the meter volumes today, thanks.\r\n",
                                       message_id="<1@x>")})
    root = tmp_path / "repo"
    (b,) = batch.make_batches(st, ["mb/inbox/1"], {"chars_per_token": 4, "token_budget": 24000, "hard_cap_tokens": 40000,
                                                   "recipients_shown": 10}, "p")
    batch.write([b], root / "data" / "batches" / "dev")
    held, seen = [], []
    real_lock, real_finish = extract.shared_lock, extract._finish_shared

    class Spy:
        def __init__(self, d):
            self.inner = real_lock(d)
            assert isinstance(self.inner, filelock.SharedFileLock)               # the cross-process kind

        def __enter__(self):
            self.inner.__enter__()
            held.append(1)
            return self

        def __exit__(self, *a):
            held.pop()
            return self.inner.__exit__(*a)
    monkeypatch.setattr(extract, "shared_lock", Spy)
    monkeypatch.setattr(extract, "_finish_shared", lambda *a: (seen.append(bool(held)), real_finish(*a)))
    p = extract.Paths(root)
    rec = extract.prepare(p, "dev", b["batch_id"], "haiku", "extract-v8c", "extract-v6")
    (p.staging / f"{b['batch_id']}.json").write_text(json.dumps({
        "batch_id": b["batch_id"], "attempt_id": rec["attempt_id"], "observations": [],
        "messages": [{"msg_key": "mb/inbox/1", "disposition": "business_no_process_signal", "reason": "fyi", "work_items": 0}]}))
    extract.finalize(p, "dev", b["batch_id"], st, {"min_quote_chars": 25, "canary_min_quotes": 5, "canary_max_fail_rate": 0.2,
                                                   "evidence_flag_min_signals": 5, "evidence_flag_max_fail_rate": 0.2},
                     usage={"models_observed": ["claude-haiku-5-5"]})
    assert seen == [True]


WRITER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from src.filelock import write_atomic
target, n = Path(sys.argv[2]), int(sys.argv[3])
for i in range(n):
    write_atomic(target, json.dumps({"i": i, "pad": "x" * 400000}))
"""


def test_a_reader_in_another_process_never_sees_a_torn_status_file(tmp_path):
    """Readers outside the lock (the report, the dashboard) must see the old or the new file, never a partial one."""
    target = tmp_path / "status.json"
    target.write_text(json.dumps({"i": -1}), encoding="utf-8")
    w = spawn(WRITER, target, 150)
    reads = 0
    while w.poll() is None:
        try:
            text = target.read_text(encoding="utf-8")
        except (PermissionError, FileNotFoundError):        # Windows: the instant of a replace; a reader just retries
            continue
        json.loads(text)                                     # a torn file would raise here
        reads += 1
    out, err = w.communicate(timeout=120)
    assert w.returncode == 0, err.decode()[-800:]
    assert reads > 10 and json.loads(target.read_text(encoding="utf-8"))["i"] == 149
