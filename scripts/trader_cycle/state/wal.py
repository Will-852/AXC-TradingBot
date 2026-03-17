"""
wal.py — Re-export from shared_infra.wal

Canonical implementation lives in shared_infra.wal.
"""

from shared_infra.wal import HKT, WriteAheadLog  # noqa: F401

__all__ = ["HKT", "WriteAheadLog"]
