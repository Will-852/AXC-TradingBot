"""
trade_journal.py — WriteTradeJournalStep: 寫平倉記錄到 data_analysis
Pipeline Step 14.5 (WriteTradeLog 之後, WriteMemory 之前)

將 ctx.closed_positions 寫入 ~/.opencode/trading/data_analysis/raw/trades_YYYYMMDD.json
用於離線分析。原子寫入（tempfile + os.replace）。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime

from ..core.context import CycleContext
from ..config.settings import HKT

logger = logging.getLogger(__name__)

JOURNAL_DIR = os.path.expanduser("~/.opencode/trading/data_analysis/raw")


class WriteTradeJournalStep:
    """
    Step 14.5: Write closed positions to data_analysis for offline analysis.
    Appends to trades_YYYYMMDD.json (one file per day).
    """
    name = "write_trade_journal"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not ctx.closed_positions:
            return ctx

        today = datetime.now(HKT).strftime("%Y%m%d")
        path = os.path.join(JOURNAL_DIR, f"trades_{today}.json")

        # Build records
        records = []
        for cp in ctx.closed_positions:
            # Net PnL = gross PnL minus commission
            net_pnl = cp.pnl - cp.commission if cp.commission else cp.pnl
            record = {
                "pair": cp.pair,
                "direction": cp.direction,
                "entry_price": cp.entry_price,
                "exit_price": cp.exit_price,
                "size": cp.size,
                "pnl": cp.pnl,
                "commission": cp.commission,
                "net_pnl": net_pnl,
                "reason": cp.reason,
                "timestamp": cp.timestamp or ctx.timestamp_str,
                "dry_run": ctx.dry_run,
            }
            # Add entry slippage if available from order_result
            if ctx.order_result and ctx.order_result.slippage_pct != 0:
                record["entry_slippage_pct"] = ctx.order_result.slippage_pct
            records.append(record)

        try:
            os.makedirs(JOURNAL_DIR, exist_ok=True)

            # Read existing entries if file exists
            existing = []
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)

            existing.extend(records)

            # Atomic write: tempfile + os.replace
            fd, tmp_path = tempfile.mkstemp(
                dir=JOURNAL_DIR, suffix=".tmp", prefix="trades_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, path)
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            if ctx.verbose:
                print(f"    Trade journal: {len(records)} records → {path}")

        except Exception as e:
            ctx.warnings.append(f"Trade journal write failed: {e}")
            logger.warning(f"Trade journal write failed: {e}")

        return ctx
