"""
signal_journal.py — Log every signal with full market context snapshot.

每個 signal fire 時記錄：
  1. Signal 本身（strategy, direction, confidence, score）
  2. 所有 indicator 數據（BB, ADX, volume, OBV, RSI, MACD...）
  3. 其他 context（funding, liq_events, vol_triggers, mode, regime）
  4. 最終結果（selected? executed? pnl?）→ 平倉後回填

用途：50-100 trades 後分析邊個 signal 真正 independent、
哪些 counterfactual signals 會改善決策。

Output: shared/signal_journal.jsonl（一行一個 signal snapshot）
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from ..core.context import CycleContext

log = logging.getLogger(__name__)

_BASE = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
JOURNAL_PATH = _BASE / "shared" / "signal_journal.jsonl"


def _safe_float(v, decimals=6):
    """Convert to float, handle None/NaN."""
    if v is None:
        return None
    try:
        import math
        f = float(v)
        return round(f, decimals) if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def log_signal_snapshot(signal, ctx: CycleContext) -> None:
    """Log a signal with full market context for counterfactual analysis."""
    try:
        symbol = signal.pair

        # Get all indicators for this symbol
        ind_1h = ctx.indicators.get(symbol, {}).get("1h", {})
        ind_4h = ctx.indicators.get(symbol, {}).get("4h", {})

        # Get market data
        market = ctx.market_data.get(symbol)

        snapshot = {
            "ts": int(time.time()),
            "cycle_id": ctx.cycle_id,

            # ── Signal ──
            "symbol": symbol,
            "strategy": signal.strategy,
            "direction": signal.direction,
            "strength": signal.strength,
            "confidence": _safe_float(signal.confidence, 4),
            "score": _safe_float(signal.score, 4),
            "original_score": _safe_float(signal.original_score, 4),
            "entry_price": _safe_float(signal.entry_price, 2),
            "reasons": signal.reasons[:5],  # cap to avoid bloat

            # ── Was this signal selected + executed? ──
            "selected": False,  # updated later by SelectSignalStep
            "executed": False,  # updated later by ExecuteTradeStep

            # ── Context: mode + regime ──
            "market_mode": ctx.market_mode,
            "coin_mode": ctx.coin_market_mode.get(symbol, "UNKNOWN"),
            "volatility_regime": ctx.volatility_regime,
            "session_tag": ctx.session_tag,
            "dry_run": ctx.dry_run,

            # ── 1H Indicators (the full picture) ──
            "price": _safe_float(ind_1h.get("price"), 2),
            "bb_upper": _safe_float(ind_1h.get("bb_upper"), 2),
            "bb_lower": _safe_float(ind_1h.get("bb_lower"), 2),
            "bb_width": _safe_float(ind_1h.get("bb_width")),
            "bb_width_pctl": _safe_float(ind_1h.get("bb_width_pctl"), 1),
            "rsi": _safe_float(ind_1h.get("rsi"), 1),
            "adx": _safe_float(ind_1h.get("adx"), 1),
            "atr": _safe_float(ind_1h.get("atr"), 2),
            "volume_ratio": _safe_float(ind_1h.get("volume_ratio"), 3),
            "obv": _safe_float(ind_1h.get("obv"), 0),
            "obv_ema": _safe_float(ind_1h.get("obv_ema"), 0),
            "macd_hist": _safe_float(ind_1h.get("macd_hist")),
            "ema_fast": _safe_float(ind_1h.get("ema_fast"), 2),
            "ema_slow": _safe_float(ind_1h.get("ema_slow"), 2),
            "stoch_k": _safe_float(ind_1h.get("stoch_k"), 1),
            "vwap": _safe_float(ind_1h.get("vwap"), 2),
            "prev_close": _safe_float(ind_1h.get("prev_close"), 2),

            # ── 4H confirmation ──
            "bb_width_pctl_4h": _safe_float(ind_4h.get("bb_width_pctl"), 1),
            "adx_4h": _safe_float(ind_4h.get("adx"), 1),
            "rsi_4h": _safe_float(ind_4h.get("rsi"), 1),

            # ── Market data (independent from price indicators) ──
            "funding_rate": _safe_float(market.funding_rate, 6) if market else None,
            "price_change_24h": _safe_float(market.price_change_24h_pct, 2) if market else None,

            # ── Liquidation events ──
            "liq_events_count": len(ctx.liq_events),

            # ── Volume triggers ──
            "vol_trigger_active": any(
                vt.get("symbol") == symbol for vt in ctx.vol_triggers
            ) if ctx.vol_triggers else False,

            # ── Positions ──
            "open_positions": len(ctx.open_positions),
            "balance": _safe_float(ctx.account_balance, 2),
        }

        # Atomic append
        with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, default=str) + "\n")

    except Exception as e:
        log.warning("Signal journal write failed: %s", e)
