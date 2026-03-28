#!/usr/bin/env python3
"""
query_metrics.py — CLI to query signal_journal.jsonl with optional trades.jsonl join.

Usage:
    python3 scripts/query_metrics.py                          # all signals summary
    python3 scripts/query_metrics.py --executed               # only executed signals
    python3 scripts/query_metrics.py --symbol BTCUSDT         # filter by symbol
    python3 scripts/query_metrics.py --direction LONG         # filter by direction
    python3 scripts/query_metrics.py --strategy bb_bounce     # filter by strategy
    python3 scripts/query_metrics.py --session ASIA           # filter by session_tag
    python3 scripts/query_metrics.py --since 2026-03-01       # filter by date
    python3 scripts/query_metrics.py --live                   # exclude dry_run
    python3 scripts/query_metrics.py --breakdown strategy     # group-by breakdown
    python3 scripts/query_metrics.py --breakdown session_tag  # group-by breakdown
    python3 scripts/query_metrics.py --with-pnl               # join trades.jsonl for PnL

Design decision: queries signal_journal.jsonl directly rather than creating a new JSONL,
because signal_journal already has ~40 fields per cycle including indicators and regime.
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SIGNAL_FILE = BASE_DIR / "shared" / "signal_journal.jsonl"
TRADES_FILE = BASE_DIR / "memory" / "store" / "trades.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def parse_ts(ts_val) -> datetime:
    """Parse timestamp — handles both epoch int and ISO string."""
    if isinstance(ts_val, (int, float)):
        return datetime.fromtimestamp(ts_val, tz=timezone.utc)
    if isinstance(ts_val, str):
        # ISO 8601
        return datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
    return datetime.min.replace(tzinfo=timezone.utc)


def filter_signals(signals: list[dict], args) -> list[dict]:
    result = []
    since_dt = None
    if args.since:
        since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    for s in signals:
        if args.executed and not s.get("executed"):
            continue
        if args.selected and not s.get("selected"):
            continue
        if args.symbol and s.get("symbol") != args.symbol:
            continue
        if args.direction and s.get("direction") != args.direction.upper():
            continue
        if args.strategy and s.get("strategy") != args.strategy:
            continue
        if args.session and s.get("session_tag") != args.session.upper():
            continue
        if args.live and s.get("dry_run", True):
            continue
        if since_dt:
            sig_dt = parse_ts(s.get("ts", 0))
            if sig_dt < since_dt:
                continue
        result.append(s)
    return result


_SIDE_TO_DIRECTION = {"BUY": "LONG", "SELL": "SHORT"}


def load_trades_for_join() -> dict:
    """Load trades.jsonl, return lookup by (symbol, direction, date).

    Normalizes side values: BUY→LONG, SELL→SHORT to match signal_journal's direction field.
    """
    raw = load_jsonl(TRADES_FILE)
    lookup = defaultdict(list)
    for rec in raw:
        pnl = rec.get("pnl")
        if pnl is None:
            continue
        symbol = rec.get("symbol", "")
        side = rec.get("side", "")
        direction = _SIDE_TO_DIRECTION.get(side, side)  # BUY→LONG, SELL→SHORT, LONG→LONG
        ts = parse_ts(rec.get("ts", 0))
        date_key = ts.strftime("%Y-%m-%d")
        lookup[(symbol, direction, date_key)].append(float(pnl))
    return lookup


def compute_stats(signals: list[dict], trades_lookup: dict | None = None) -> dict:
    """Compute aggregate stats for a set of signals."""
    n = len(signals)
    if n == 0:
        return {"count": 0}

    executed = [s for s in signals if s.get("executed")]
    selected = [s for s in signals if s.get("selected")]
    confidences = [s["confidence"] for s in signals if "confidence" in s]
    scores = [s["score"] for s in signals if "score" in s]

    stats = {
        "count": n,
        "selected": len(selected),
        "executed": len(executed),
        "select_rate": f"{len(selected)/n*100:.1f}%" if n else "0%",
        "exec_rate": f"{len(executed)/n*100:.1f}%" if n else "0%",
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
    }

    # PnL join
    if trades_lookup is not None and executed:
        matched_pnls = []
        for s in executed:
            symbol = s.get("symbol", "")
            direction = s.get("direction", "")
            ts = parse_ts(s.get("ts", 0))
            date_key = ts.strftime("%Y-%m-%d")
            pnls = trades_lookup.get((symbol, direction, date_key), [])
            matched_pnls.extend(pnls)

        if matched_pnls:
            wins = [p for p in matched_pnls if p > 0]
            stats["trades_matched"] = len(matched_pnls)
            stats["total_pnl"] = round(sum(matched_pnls), 2)
            stats["win_rate"] = f"{len(wins)/len(matched_pnls)*100:.1f}%"
            stats["avg_pnl"] = round(sum(matched_pnls) / len(matched_pnls), 2)
        else:
            stats["trades_matched"] = 0

    return stats


def print_stats(label: str, stats: dict):
    if stats["count"] == 0:
        print(f"\n{label}: no records")
        return

    print(f"\n{'─'*50}")
    print(f"  {label}")
    print(f"{'─'*50}")
    print(f"  Signals:     {stats['count']}")
    print(f"  Selected:    {stats['selected']} ({stats['select_rate']})")
    print(f"  Executed:    {stats['executed']} ({stats['exec_rate']})")
    if stats.get("avg_confidence") is not None:
        print(f"  Avg conf:    {stats['avg_confidence']}")
    if stats.get("avg_score") is not None:
        print(f"  Avg score:   {stats['avg_score']}")
    if "trades_matched" in stats:
        print(f"  Trades:      {stats['trades_matched']}")
        if stats["trades_matched"] > 0:
            print(f"  Win rate:    {stats['win_rate']}")
            print(f"  Total PnL:   ${stats['total_pnl']:+.2f}")
            print(f"  Avg PnL:     ${stats['avg_pnl']:+.2f}")


def main():
    parser = argparse.ArgumentParser(description="Query AXC signal journal")
    parser.add_argument("--executed", action="store_true", help="Only executed signals")
    parser.add_argument("--selected", action="store_true", help="Only selected signals")
    parser.add_argument("--symbol", help="Filter by symbol (e.g. BTCUSDT)")
    parser.add_argument("--direction", help="Filter by direction (LONG/SHORT)")
    parser.add_argument("--strategy", help="Filter by strategy name")
    parser.add_argument("--session", help="Filter by session_tag (ASIA/EU/US)")
    parser.add_argument("--since", help="Only signals after date (YYYY-MM-DD)")
    parser.add_argument("--live", action="store_true", help="Exclude dry_run signals")
    parser.add_argument("--with-pnl", action="store_true", help="Join trades.jsonl for PnL")
    parser.add_argument("--breakdown", help="Group by field (strategy/direction/session_tag/symbol/market_mode)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    if not SIGNAL_FILE.exists():
        log.error("Signal journal not found: %s", SIGNAL_FILE)
        sys.exit(1)

    signals = load_jsonl(SIGNAL_FILE)
    filtered = filter_signals(signals, args)

    trades_lookup = load_trades_for_join() if args.with_pnl else None

    if args.breakdown:
        groups = defaultdict(list)
        for s in filtered:
            key = s.get(args.breakdown, "unknown")
            groups[key].append(s)

        print(f"\nBreakdown by: {args.breakdown} ({len(filtered)} signals)")
        for key in sorted(groups.keys(), key=lambda k: len(groups[k]), reverse=True):
            stats = compute_stats(groups[key], trades_lookup)
            print_stats(str(key), stats)
    else:
        # Build filter description
        filters = []
        if args.executed:
            filters.append("executed")
        if args.selected:
            filters.append("selected")
        if args.symbol:
            filters.append(f"symbol={args.symbol}")
        if args.direction:
            filters.append(f"dir={args.direction}")
        if args.strategy:
            filters.append(f"strategy={args.strategy}")
        if args.session:
            filters.append(f"session={args.session}")
        if args.live:
            filters.append("live")
        if args.since:
            filters.append(f"since={args.since}")

        label = "All signals" if not filters else f"Filtered: {', '.join(filters)}"
        stats = compute_stats(filtered, trades_lookup)
        print_stats(label, stats)


if __name__ == "__main__":
    main()
