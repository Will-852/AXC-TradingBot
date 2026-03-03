"""
scan_config.py — SCAN_CONFIG.md 讀寫
Wraps light_scan.py functions + adds file locking
"""

from __future__ import annotations
import os
import sys

# Import from light_scan.py
_tools_dir = os.path.join(
    os.environ.get("OPENCLAW_WORKSPACE", "/Users/wai/.openclaw/workspace"),
    "tools"
)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from light_scan import parse_scan_config as _parse, update_scan_config as _update

from ..config.settings import SCAN_CONFIG_PATH
from .file_lock import FileLock


def read_scan_config(path: str | None = None) -> dict:
    """Read and parse SCAN_CONFIG.md. Returns dict of key-value pairs."""
    p = path or SCAN_CONFIG_PATH
    return _parse(p)


def write_scan_config(updates: dict, path: str | None = None) -> bool:
    """
    Update specific fields in SCAN_CONFIG.md with file locking.
    Uses fcntl lock to prevent race with light_scan.py.
    """
    p = path or SCAN_CONFIG_PATH
    with FileLock(p):
        return _update(p, updates)
