#!/usr/bin/env python3
"""
btc_15m_paper.py — Paper trading tracker for BTC Up/Down markets

設計決定：
- 輕量：只用 deterministic scoring，唔叫 Claude API（紙上交易唔值得花 API 錢）
- 複用 crypto_15m.py 嘅 parse/score 邏輯，唔重寫
- Resolution 用 Binance public klines API（免費，唔洗 auth）
- 單一 cron job (--cycle) 做 resolve + predict，簡化運維
- Dedup by condition_id + window_start，避免重複記錄
- 用 search_markets 搵 BTC markets（低流動性唔會出現喺 top-500 scan）
- Window 實際係 5 分鐘，但 strategy 叫 crypto_15m（歷史名稱）

Usage:
  PYTHONPATH=.:scripts python3 polymarket/run_btc_paper.py --cycle
  PYTHONPATH=.:scripts python3 polymarket/run_btc_paper.py --report
  PYTHONPATH=.:scripts python3 polymarket/run_btc_paper.py --predict --dry-run
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ─── Path Setup ───
_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if _AXC not in sys.path:
    sys.path.insert(0, _AXC)                          # for polymarket.*
_SCRIPTS = os.path.join(_AXC, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)                       # for shared_infra.*

from polymarket.strategy.crypto_15m import (
    parse_crypto_15m_market,
    _fetch_15m_indicators,
    _gather_btc_context,
    _score_direction,
)
from polymarket.exchange.gamma_client import GammaClient
from polymarket.config.categories import match_category

logger = logging.getLogger(__name__)

# ─── Constants ───
_ET = ZoneInfo("America/New_York")
_HKT = ZoneInfo("Asia/Hong_Kong")
_UTC = ZoneInfo("UTC")
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_PRED_LOG = os.path.join(_LOG_DIR, "btc_15m_predictions.jsonl")
_RESO_LOG = os.path.join(_LOG_DIR, "btc_15m_resolutions.jsonl")
_ENTRY_PRICE_CAP = 0.55  # trading knowledge: 15M crypto max entry


# ─── Helpers ───

def _append_jsonl(path: str, record: dict) -> None:
    """Append a JSON record to a JSONL file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: str) -> list[dict]:
    """Read all records from a JSONL file."""
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_resolved_keys() -> set[str]:
    """Load set of already-resolved prediction keys."""
    keys = set()
    for r in _read_jsonl(_RESO_LOG):
        keys.add(r["key"])
    return keys


def _load_predicted_keys() -> set[str]:
    """Load set of already-predicted keys for dedup."""
    keys = set()
    for p in _read_jsonl(_PRED_LOG):
        keys.add(f"{p['condition_id']}_{p['window_start']}")
    return keys


def _fetch_binance_kline(symbol: str, start_ms: int, interval: str = "5m") -> dict | None:
    """Fetch single kline from Binance public API.

    Uses startTime to get the exact kline at the given interval.
    Polymarket BTC windows are currently 5 minutes.
    Returns dict with open/close/high/low or None on failure.
    """
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval={interval}"
        f"&startTime={start_ms}&limit=1"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.warning("Binance kline fetch failed: %s", e)
        return None

    if not data:
        return None

    # kline format: [open_time, open, high, low, close, volume, ...]
    k = data[0]
    return {
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
    }


# ─── Resolve ───

def _run_resolve() -> int:
    """Resolve pending predictions by checking Binance klines."""
    predictions = _read_jsonl(_PRED_LOG)
    if not predictions:
        print("  No predictions to resolve")
        return 0

    resolved_keys = _load_resolved_keys()
    now = datetime.now(tz=_ET)
    count = 0

    for pred in predictions:
        key = f"{pred['condition_id']}_{pred['window_start']}"
        if key in resolved_keys:
            continue

        # Window must have ended (+ 2 min buffer for kline close)
        try:
            window_end = datetime.fromisoformat(pred["window_end"])
        except (ValueError, KeyError):
            continue

        if now < window_end + timedelta(minutes=2):
            continue

        # Convert window start to UTC ms for Binance
        start_dt = datetime.fromisoformat(pred["window_start"])
        start_ms = int(start_dt.astimezone(_UTC).timestamp() * 1000)

        # Determine Binance kline interval from window duration
        window_min = pred.get("window_duration_min", 5)
        if window_min <= 5:
            interval = "5m"
        elif window_min <= 15:
            interval = "15m"
        else:
            interval = "1h"

        kline = _fetch_binance_kline("BTCUSDT", start_ms, interval=interval)
        if not kline:
            continue

        went_up = kline["close"] > kline["open"]
        outcome = 1 if went_up else 0  # 1 = Up (YES wins), 0 = Down

        # Lead period analysis: BTC 喺預測到開窗之間動咗幾多
        btc_at_predict = pred.get("btc_price_at_predict", 0)
        lead_change = 0.0
        lead_change_pct = 0.0
        if btc_at_predict > 0:
            lead_change = kline["open"] - btc_at_predict
            lead_change_pct = lead_change / btc_at_predict

        resolution = {
            "key": key,
            "condition_id": pred["condition_id"],
            "window_start": pred["window_start"],
            "window_end": pred["window_end"],
            "btc_open": kline["open"],
            "btc_close": kline["close"],
            "btc_high": kline["high"],
            "btc_low": kline["low"],
            "btc_at_predict": btc_at_predict,
            "lead_change": round(lead_change, 2),
            "lead_change_pct": round(lead_change_pct, 6),
            "went_up": went_up,
            "outcome": outcome,
            "resolved_at": datetime.now(tz=_HKT).isoformat(),
        }
        _append_jsonl(_RESO_LOG, resolution)
        resolved_keys.add(key)
        count += 1

        direction = "UP" if went_up else "DOWN"
        change = kline["close"] - kline["open"]
        print(
            f"  Resolved: {pred['window_start'][-8:]} BTC {direction} "
            f"(${kline['open']:,.0f} → ${kline['close']:,.0f}, {change:+.0f})"
        )

    return count


# ─── Predict ───

def _run_predict(dry_run: bool = False) -> list[dict]:
    """Search Gamma for BTC Up/Down markets, score, and log predictions.

    Uses search_markets instead of get_markets because these low-liquidity
    markets (~$15K) don't appear in the top-500 by liquidity.
    """
    gamma = GammaClient()

    # Search specifically for BTC Up/Down markets
    raw_markets = gamma.search_markets("Bitcoin Up or Down", limit=20)
    if not raw_markets:
        return []

    predicted_keys = set() if dry_run else _load_predicted_keys()
    predictions = []

    for raw in raw_markets:
        parsed_market = gamma.parse_market(raw)
        cat = match_category(parsed_market["title"])
        if cat != "crypto_15m":
            continue

        # Parse 15m specifics (checks lead time 15-50 min window)
        info = parse_crypto_15m_market(parsed_market["title"])
        if not info:
            continue

        # Dedup
        dedup_key = f"{parsed_market['condition_id']}_{info['start_time']}"
        if dedup_key in predicted_keys:
            continue

        yes_price = parsed_market.get("yes_price", 0.5)
        liquidity = parsed_market.get("liquidity", 0)
        volume_24h = parsed_market.get("volume_24h", 0)

        # Compute window duration (currently 5 min on Polymarket)
        start_dt = datetime.fromisoformat(info["start_time"])
        end_dt = datetime.fromisoformat(info["end_time"])
        window_min = (end_dt - start_dt).total_seconds() / 60

        # Fetch 15m indicators via indicator_calc.py subprocess
        indicators = _fetch_15m_indicators(info["symbol"])
        if not indicators:
            print(f"  Skip (no indicators): {parsed_market['title'][:50]}")
            continue

        # BTC context (SCAN_CONFIG + sentiment + TRADE_STATE)
        btc_ctx = _gather_btc_context()

        # Deterministic scoring → P(Up)
        p_up, reasons = _score_direction(indicators, btc_ctx)

        # Edge calculation
        raw_edge = p_up - yes_price
        if raw_edge > 0:
            side = "YES"
            edge_pct = raw_edge
        else:
            side = "NO"
            edge_pct = -raw_edge

        # Entry price cap check
        entry_price = yes_price if side == "YES" else (1 - yes_price)
        price_capped = entry_price > _ENTRY_PRICE_CAP

        # Snapshot BTC spot price at prediction time
        # → resolve 時比較 lead period 入面 BTC 動咗幾多
        btc_spot = indicators.get("price", 0)

        record = {
            "ts": datetime.now(tz=_HKT).isoformat(),
            "condition_id": parsed_market["condition_id"],
            "title": parsed_market["title"],
            "coin": info["coin"],
            "window_start": info["start_time"],
            "window_end": info["end_time"],
            "lead_minutes": info["lead_minutes"],
            "window_duration_min": window_min,
            "btc_price_at_predict": round(btc_spot, 2),
            "p_up": round(p_up, 4),
            "side": side,
            "market_price": round(yes_price, 4),
            "edge_pct": round(edge_pct, 4),
            "entry_price": round(entry_price, 4),
            "price_capped": price_capped,
            "poly_liquidity": round(liquidity, 0),
            "poly_volume_24h": round(volume_24h, 2),
            "indicators": {
                k: round(v, 2) if isinstance(v, float) else v
                for k, v in (indicators or {}).items()
                if k in ("rsi", "macd_hist", "macd_hist_prev", "bb_upper",
                         "bb_lower", "stoch_k", "stoch_d", "vwap", "price",
                         "ema_fast", "ema_slow")
            },
            "market_mode": btc_ctx.get("market_mode"),
            "reasons": reasons[:5],
        }

        if not dry_run:
            _append_jsonl(_PRED_LOG, record)
            predicted_keys.add(dedup_key)

        predictions.append(record)

        cap_str = " [CAPPED]" if price_capped else ""
        print(
            f"  {side} P(Up)={p_up:.3f} vs mkt={yes_price:.3f} "
            f"edge={edge_pct:+.1%} lead={info['lead_minutes']:.0f}m{cap_str}"
        )
        print(f"    {parsed_market['title'][:65]}")

    return predictions


# ─── Report ───

def _run_report() -> None:
    """Compute accuracy, Brier score, and simulated P&L."""
    predictions = _read_jsonl(_PRED_LOG)
    resolutions = {r["key"]: r for r in _read_jsonl(_RESO_LOG)}

    if not predictions or not resolutions:
        total_p = len(predictions)
        total_r = len(resolutions)
        print(f"Not enough data: {total_p} predictions, {total_r} resolutions")
        return

    # Join predictions with resolutions
    joined = []
    for pred in predictions:
        key = f"{pred['condition_id']}_{pred['window_start']}"
        if key in resolutions:
            joined.append((pred, resolutions[key]))

    if not joined:
        print(f"No resolved predictions yet "
              f"({len(predictions)} predictions, {len(resolutions)} resolutions)")
        return

    n = len(joined)
    brier_sum = 0.0
    correct = 0
    profit_sim = 0.0  # $10 per trade simulation
    correct_capped = 0
    total_capped = 0
    edge_bins = {}  # edge_bucket → [correct, total]
    # Lead period momentum analysis
    lead_momentum_match = 0  # lead period direction matches window outcome
    lead_momentum_total = 0
    lead_confirm_correct = 0  # model + lead agree → was it right?
    lead_confirm_total = 0

    for pred, reso in joined:
        p_up = pred["p_up"]
        outcome = reso["outcome"]  # 1=Up, 0=Down
        our_call = 1 if pred["side"] == "YES" else 0

        # Brier score: (forecast - outcome)²
        brier_sum += (p_up - outcome) ** 2

        # Accuracy
        hit = our_call == outcome
        if hit:
            correct += 1

        # Profit simulation ($10 per trade)
        entry = pred.get("entry_price", pred["market_price"])
        if entry > 0:
            if hit:
                profit_sim += 10 * (1.0 / entry - 1.0)  # win payout
            else:
                profit_sim -= 10  # lose bet

        # Price cap tracking
        if pred.get("price_capped", False):
            total_capped += 1
            if hit:
                correct_capped += 1

        # Edge bin tracking
        edge = pred["edge_pct"]
        if edge < 0.05:
            bucket = "<5%"
        elif edge < 0.10:
            bucket = "5-10%"
        elif edge < 0.15:
            bucket = "10-15%"
        else:
            bucket = ">15%"
        if bucket not in edge_bins:
            edge_bins[bucket] = [0, 0]
        edge_bins[bucket][1] += 1
        if hit:
            edge_bins[bucket][0] += 1

        # Lead period momentum: BTC direction during lead → matches outcome?
        lead_pct = reso.get("lead_change_pct", 0)
        if abs(lead_pct) > 0.0001:  # non-trivial move
            lead_momentum_total += 1
            lead_up = lead_pct > 0
            if lead_up == bool(outcome):
                lead_momentum_match += 1
            # Did model + lead momentum agree? If so, was it right?
            model_up = our_call == 1
            if model_up == lead_up:
                lead_confirm_total += 1
                if hit:
                    lead_confirm_correct += 1

    brier = brier_sum / n
    accuracy = correct / n

    print(f"\n{'='*55}")
    print(f"  15M BTC Paper Trading Report")
    print(f"{'='*55}")
    print(f"  Total predictions:  {len(predictions)}")
    print(f"  Total resolved:     {n}")
    print(f"  Accuracy:           {accuracy:.1%} ({correct}/{n})")
    print(f"  Brier Score:        {brier:.4f}")
    print(f"  Sim P&L ($10/trade): ${profit_sim:+.2f}")

    if total_capped:
        cap_acc = correct_capped / total_capped if total_capped else 0
        print(f"\n  Price-capped trades: {total_capped} "
              f"(accuracy: {cap_acc:.1%})")
        print(f"  → These would be SKIPPED in live trading")

    if edge_bins:
        print(f"\n  Accuracy by edge size:")
        for bucket in ["<5%", "5-10%", "10-15%", ">15%"]:
            if bucket in edge_bins:
                c, t = edge_bins[bucket]
                print(f"    {bucket:>6s}: {c}/{t} = {c/t:.1%}" if t else "")

    if lead_momentum_total:
        lm_acc = lead_momentum_match / lead_momentum_total
        print(f"\n  Lead period momentum (your insight):")
        print(f"    Lead direction → window outcome: "
              f"{lead_momentum_match}/{lead_momentum_total} = {lm_acc:.1%}")
        if lead_confirm_total:
            lc_acc = lead_confirm_correct / lead_confirm_total
            print(f"    Model + lead agree → accuracy: "
                  f"{lead_confirm_correct}/{lead_confirm_total} = {lc_acc:.1%}")
            print(f"    → If this > base accuracy, lead confirmation adds value")

    print(f"\n  Interpretation:")
    if brier < 0.20:
        print(f"    Brier {brier:.3f} = GOOD calibration")
    elif brier < 0.25:
        print(f"    Brier {brier:.3f} = OK calibration")
    else:
        print(f"    Brier {brier:.3f} = POOR (coin flip = 0.25)")

    if accuracy > 0.55:
        print(f"    Accuracy {accuracy:.1%} = promising signal")
    elif accuracy > 0.50:
        print(f"    Accuracy {accuracy:.1%} = marginal edge")
    else:
        print(f"    Accuracy {accuracy:.1%} = no edge detected")
    print()


# ─── Main ───

def main():
    parser = argparse.ArgumentParser(
        description="15M BTC Paper Tracker — deterministic scoring only"
    )
    parser.add_argument("--cycle", action="store_true",
                        help="Resolve pending + make new predictions (for cron)")
    parser.add_argument("--predict", action="store_true",
                        help="Predict only")
    parser.add_argument("--resolve", action="store_true",
                        help="Resolve only")
    parser.add_argument("--report", action="store_true",
                        help="Show accuracy + Brier report")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write to log files")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    now_str = datetime.now(tz=_HKT).strftime("%H:%M")

    if args.report:
        _run_report()
    elif args.cycle:
        print(f"[{now_str} HKT] 15M BTC Paper Cycle")
        print("── Resolve ──")
        n_resolved = _run_resolve()
        if n_resolved == 0:
            print("  Nothing to resolve")
        print("── Predict ──")
        preds = _run_predict(dry_run=args.dry_run)
        if not preds:
            print("  No 15m BTC markets in lead window")
        print(f"── Done: {n_resolved} resolved, {len(preds)} predicted ──")
    elif args.predict:
        preds = _run_predict(dry_run=args.dry_run)
        if not preds:
            print("No 15m BTC markets in lead window")
    elif args.resolve:
        n = _run_resolve()
        print(f"Resolved: {n}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
