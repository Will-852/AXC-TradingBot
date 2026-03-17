"""
wal.py — Write-Ahead Log for crash recovery.

Append-only JSONL: each operation writes a "pending" line before execution,
then a "done"/"failed" line after. Recovery = find pending without completion.

Design decisions:
- JSONL (not JSON) because append is crash-safe; partial last line = skip on parse.
- Completion records (not in-place update) avoid corruption on crash mid-write.
- One file, not per-operation, because cycle frequency is low (~2/hour).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

__all__ = ["HKT", "WriteAheadLog"]

logger = logging.getLogger(__name__)

HKT = timezone(timedelta(hours=8))


class WriteAheadLog:
    """Append-only JSONL log for crash recovery."""

    def __init__(self, path: str) -> None:
        self._path = path
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)

    @property
    def path(self) -> str:
        return self._path

    def log_intent(self, op: str, pair: str, direction: str,
                   qty: float, price: float, sl_price: float,
                   platform: str) -> str:
        """Write pending intent, return intent_id."""
        ts = int(time.time())
        intent_id = f"{op}_{pair}_{ts}"
        entry = {
            "id": intent_id,
            "ts": datetime.now(HKT).isoformat(timespec="seconds"),
            "op": op,
            "pair": pair,
            "direction": direction,
            "qty": qty,
            "price": price,
            "sl_price": sl_price,
            "platform": platform,
            "status": "pending",
        }
        self._append(entry)
        return intent_id

    def log_done(self, intent_id: str, order_id: str = "") -> None:
        """Mark intent as completed."""
        entry = {
            "id": intent_id,
            "ts": datetime.now(HKT).isoformat(timespec="seconds"),
            "status": "done",
            "order_id": order_id,
        }
        self._append(entry)

    def log_failed(self, intent_id: str, error: str) -> None:
        """Mark intent as failed."""
        entry = {
            "id": intent_id,
            "ts": datetime.now(HKT).isoformat(timespec="seconds"),
            "status": "failed",
            "error": error[:500],
        }
        self._append(entry)

    def get_pending(self) -> list[dict]:
        """Return intents still in pending state (no done/failed record)."""
        if not os.path.exists(self._path):
            return []

        # Build map: id → latest status + original intent data
        intents: dict[str, dict] = {}
        resolved: set[str] = set()

        try:
            with open(self._path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        # Partial line from crash — skip
                        continue

                    rid = record.get("id", "")
                    status = record.get("status", "")

                    if status == "pending":
                        intents[rid] = record
                    elif status in ("done", "failed"):
                        resolved.add(rid)
        except OSError as e:
            logger.warning(f"WAL read error: {e}")
            return []

        return [v for k, v in intents.items() if k not in resolved]

    def prune(self, keep_days: int = 7) -> None:
        """Remove entries older than N days. Rewrites file atomically."""
        if not os.path.exists(self._path):
            return

        cutoff = datetime.now(HKT) - timedelta(days=keep_days)
        kept: list[str] = []

        try:
            with open(self._path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ts_str = record.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts >= cutoff:
                            kept.append(json.dumps(record, ensure_ascii=False))
                    except (ValueError, TypeError):
                        # Can't parse timestamp — keep it to be safe
                        kept.append(json.dumps(record, ensure_ascii=False))
        except OSError as e:
            logger.warning(f"WAL prune read error: {e}")
            return

        # Atomic rewrite
        dir_name = os.path.dirname(self._path)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".wal.tmp")
            with os.fdopen(fd, "w") as f:
                for entry in kept:
                    f.write(entry + "\n")
            os.replace(tmp_path, self._path)
        except OSError as e:
            logger.warning(f"WAL prune write error: {e}")
            if 'tmp_path' in locals():
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _append(self, entry: dict) -> None:
        """Append a single JSON line. Flush immediately for crash safety."""
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            logger.error(f"WAL write failed: {e}")
