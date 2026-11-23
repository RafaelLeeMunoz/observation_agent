"""A lock that holds across threads AND processes, for the files every attempt shares.

The pipeline finalizes batches from parallel worker threads, and an orchestrator may also run `finalize` as
separate processes at the same time. Both read-modify-write `data/extract/status.json` and append to the shared
logs. A thread lock alone covers only one process, so this combines one with an OS lock on a lock file next to the
shared files: `msvcrt.locking` on Windows, `fcntl.flock` elsewhere. Waiting is bounded (`timeout`), and a timeout
raises instead of writing unprotected. Standard library only.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


class SharedFileLock:
    def __init__(self, lock_path: Path, timeout: float = 120.0):
        self.path = Path(lock_path)
        self.timeout = timeout
        with _GUARD:
            self._thread_lock = _THREAD_LOCKS.setdefault(os.path.normcase(str(self.path.resolve())), threading.Lock())
        self._fd = None

    def __enter__(self):
        if not self._thread_lock.acquire(timeout=self.timeout):
            raise TimeoutError(f"thread lock on {self.path} not acquired in {self.timeout} s")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    if os.name == "nt":
                        os.lseek(self._fd, 0, os.SEEK_SET)
                        msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"file lock {self.path} not acquired in {self.timeout} s")
                    time.sleep(0.01)
        except BaseException:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._thread_lock.release()
            raise

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
            self._thread_lock.release()
        return False


def write_atomic(path: Path, text: str, attempts: int = 50) -> None:
    """Write a whole file so a reader sees the old or the new version, never a truncated one. On Windows a replace
    can fail briefly while another process has the target open for reading; it is retried, never skipped."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02)
