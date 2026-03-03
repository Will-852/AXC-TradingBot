"""
file_lock.py — fcntl-based file locking for concurrent safety
光 scan (每 3 分鐘) 同 trader-cycle (每 30 分鐘) 都寫 SCAN_CONFIG.md
雖然寫唔同 fields，但 read-modify-write 需要原子性
"""

import fcntl
import os
import time


class FileLock:
    """
    Advisory file lock using fcntl.flock().
    用法:
        with FileLock("/path/to/file.md"):
            # read-modify-write operations
    """

    def __init__(self, path: str, timeout: float = 5.0):
        self._path = path
        self._lock_path = path + ".lock"
        self._timeout = timeout
        self._fd = None

    def __enter__(self) -> "FileLock":
        self._fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (IOError, OSError):
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise TimeoutError(
                        f"Could not acquire lock on {self._path} "
                        f"within {self._timeout}s"
                    )
                time.sleep(0.05)

    def __exit__(self, *args) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None
            # Clean up lock file (best effort)
            try:
                os.unlink(self._lock_path)
            except FileNotFoundError:
                pass
