#!/usr/bin/env python3
"""
binance_feed.py — Binance 市場數據模組
無需 API Key（使用公開端點）
"""
from binance.spot import Spot
from datetime import datetime, timezone
from pathlib import Path
import json
import os

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
OUT_FILE = BASE_DIR / "shared/binance_market.json"

SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
}

_public_client = Spot()


def get_binance_prices() -> list:
    results = []
    for display, symbol in SYMBOLS.items():
        try:
            t = _public_client.ticker_24hr(symbol)
            k = _public_client.klines(symbol, "5m", limit=24)
            results.append({
                "symbol":  display,
                "price":   float(t["lastPrice"]),
                "change":  float(t["priceChangePercent"]),
                "high":    float(t["highPrice"]),
                "low":     float(t["lowPrice"]),
                "volume":  float(t["volume"]),
                "history": [float(c[4]) for c in k],
                "source":  "binance",
                "updated": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            print(f"[binance_feed] {symbol} fail: {e}")
    return results


def write_cache(prices: list) -> None:
    OUT_FILE.write_text(json.dumps({
        "prices":  prices,
        "count":   len(prices),
        "updated": datetime.now(timezone.utc).isoformat(),
        "source":  "binance-connector",
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    prices = get_binance_prices()
    write_cache(prices)
    print(f"[binance_feed] fetched {len(prices)} pairs")
    for p in prices:
        d = "+" if p["change"] >= 0 else ""
        print(f"  {p['symbol']:4s} ${p['price']:>12,.4f} "
              f"{d}{p['change']:.2f}%")
