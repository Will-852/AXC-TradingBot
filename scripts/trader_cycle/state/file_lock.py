"""
file_lock.py — Re-export from shared_infra.file_lock

Canonical implementation lives in shared_infra.file_lock.
"""

from shared_infra.file_lock import FileLock  # noqa: F401

__all__ = ["FileLock"]
