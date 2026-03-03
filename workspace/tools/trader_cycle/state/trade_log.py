"""
trade_log.py — WriteTradeLogStep: 寫入結構化交易記錄
Pipeline Step 14 (WriteState 之後)

Appends trade entries to TRADE_LOG.md for audit trail.
"""

from __future__ import annotations

import os
import logging

from ..core.context import CycleContext
from ..config.settings import TRADE_LOG_PATH

logger = logging.getLogger(__name__)


class WriteTradeLogStep:
    """
    Step 14: Write trade log entries to TRADE_LOG.md.
    Appends entries from ctx.trade_log_entries.
    """
    name = "write_trade_log"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not ctx.trade_log_entries:
            return ctx

        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(TRADE_LOG_PATH), exist_ok=True)

            # Append entries
            with open(TRADE_LOG_PATH, "a", encoding="utf-8") as f:
                for entry in ctx.trade_log_entries:
                    f.write(entry + "\n")

            if ctx.verbose:
                print(f"    Trade log: {len(ctx.trade_log_entries)} entries written")
                for entry in ctx.trade_log_entries:
                    print(f"      {entry}")

        except Exception as e:
            ctx.warnings.append(f"Trade log write failed: {e}")
            logger.warning(f"Trade log write failed: {e}")

        return ctx
