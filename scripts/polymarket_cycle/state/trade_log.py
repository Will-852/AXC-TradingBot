"""
trade_log.py — poly_trades.jsonl 追加寫入

JSONL 格式（每行一筆交易），唔用 JSON array 因為 append 更安全。
每筆記錄包含完整 trade context 用於後續分析 + AI calibration。
"""

import json
import logging
import os
from datetime import datetime

from ..config.settings import LOG_DIR, HKT

logger = logging.getLogger(__name__)

_TRADE_LOG_PATH = os.path.join(LOG_DIR, "poly_trades.jsonl")


def log_trade(
    *,
    condition_id: str,
    title: str,
    category: str,
    side: str,
    action: str,              # "buy" / "sell" / "exit"
    shares: float,
    price: float,
    amount_usdc: float,
    edge: float = 0.0,
    confidence: float = 0.0,
    kelly_fraction: float = 0.0,
    reasoning: str = "",
    order_id: str = "",
    dry_run: bool = True,
    pnl: float | None = None,
    path: str = _TRADE_LOG_PATH,
) -> bool:
    """Append one trade record to JSONL log.

    Returns True on success, False on error.
    """
    now = datetime.now(HKT)

    record = {
        "timestamp": now.isoformat(),
        "condition_id": condition_id,
        "title": title,
        "category": category,
        "side": side,
        "action": action,
        "shares": shares,
        "price": price,
        "amount_usdc": round(amount_usdc, 2),
        "edge": round(edge, 4),
        "confidence": round(confidence, 3),
        "kelly_fraction": round(kelly_fraction, 4),
        "reasoning": reasoning[:200],  # truncate for log size
        "order_id": order_id,
        "dry_run": dry_run,
    }

    if pnl is not None:
        record["pnl"] = round(pnl, 2)

    os.makedirs(os.path.dirname(path), exist_ok=True)

    try:
        with open(path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except (IOError, OSError) as e:
        logger.error("Trade log write error: %s", e)
        return False


def read_trades(
    path: str = _TRADE_LOG_PATH,
    last_n: int | None = None,
) -> list[dict]:
    """Read trade records from JSONL log.

    Args:
        path: Log file path
        last_n: Return only last N records (None = all)
    """
    if not os.path.exists(path):
        return []

    records = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # skip corrupt lines
    except IOError as e:
        logger.warning("Trade log read error: %s", e)
        return []

    if last_n is not None:
        records = records[-last_n:]

    return records


def get_daily_trades(date: str | None = None, path: str = _TRADE_LOG_PATH) -> list[dict]:
    """Get trades for a specific date (YYYY-MM-DD). Defaults to today."""
    if date is None:
        date = datetime.now(HKT).strftime("%Y-%m-%d")

    return [
        r for r in read_trades(path=path)
        if r.get("timestamp", "").startswith(date)
    ]


def get_calibration_data(path: str = _TRADE_LOG_PATH) -> list[dict]:
    """Extract (predicted_prob, edge, confidence, resolved_outcome) for calibration.

    Only returns records that have a resolved pnl (buy trades that later resolved).
    Useful for tracking AI calibration over time.
    """
    # Buy trades with PnL recorded = resolved outcomes
    trades = read_trades(path=path)
    return [
        {
            "condition_id": t["condition_id"],
            "side": t["side"],
            "edge": t.get("edge", 0),
            "confidence": t.get("confidence", 0),
            "price": t["price"],
            "pnl": t.get("pnl"),
            "won": t.get("pnl", 0) > 0,
        }
        for t in trades
        if t.get("action") == "buy" and t.get("pnl") is not None
    ]
