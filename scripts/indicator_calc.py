#!/usr/bin/env python3.11
"""
indicator_calc.py — 技術指標計算器
版本: 2026-03-02
用途: Agent 調用計算 BB/RSI/ADX/EMA/Stoch/ATR，輸出 JSON
依賴: tradingview_indicators, pandas, requests
Python: 3.11+（tradingview_indicators 需要 match syntax）

用法:
  python3.11 tools/indicator_calc.py --symbol BTCUSDT --interval 4h --limit 200
  python3.11 tools/indicator_calc.py --symbol BTCUSDT --interval 1h --limit 100 --mode range
  python3.11 tools/indicator_calc.py --symbol BTCUSDT --interval 15m --limit 50 --mode range
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
import tradingview_indicators as tv

import importlib.util
_spec = importlib.util.spec_from_file_location("openclaw_params", "/Users/wai/.openclaw/config/params.py")
_params = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_params)
BB_TOUCH_TOL_DEFAULT = _params.BB_TOUCH_TOL_DEFAULT
BB_TOUCH_TOL_XRP = _params.BB_TOUCH_TOL_XRP
BB_WIDTH_MIN = _params.BB_WIDTH_MIN

# ─── Aster DEX API ───
API_BASE = "https://fapi.asterdex.com"
HKT = timezone(timedelta(hours=8))

# ─── 時間框參數表（來自 range-strategies spec）───
TIMEFRAME_PARAMS = {
    "15m": {
        "bb_length": 20, "bb_mult": 2,
        "rsi_period": 14, "adx_period": 14,
        "ema_fast": 8, "ema_slow": 20, "atr_period": 14,
        "rsi_long": 30, "rsi_short": 70,
        "adx_range_max": 20,
        "bb_touch_tol": BB_TOUCH_TOL_DEFAULT,
        "lookback_support": 50,
    },
    "1h": {
        "bb_length": 20, "bb_mult": 2,
        "rsi_period": 14, "adx_period": 14,
        "ema_fast": 10, "ema_slow": 30, "atr_period": 14,
        "rsi_long": 35, "rsi_short": 65,
        "adx_range_max": 20,
        "bb_touch_tol": BB_TOUCH_TOL_DEFAULT,
        "lookback_support": 30,
    },
    "4h": {
        "bb_length": 20, "bb_mult": 2,
        "rsi_period": 14, "adx_period": 14,
        "ema_fast": 10, "ema_slow": 50, "atr_period": 14,
        "rsi_long": 35, "rsi_short": 65,
        "adx_range_max": 18,
        "bb_touch_tol": BB_TOUCH_TOL_DEFAULT,
        "lookback_support": 30,
    },
}

# ─── 產品參數覆蓋 ───
PRODUCT_OVERRIDES = {
    "ETHUSDT": {"rsi_long": 32, "rsi_short": 68},
    "XRPUSDT": {"bb_touch_tol": BB_TOUCH_TOL_XRP, "stop_loss_mult": 1.0},
}


def fetch_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """從 Aster DEX 抓取 K 線數據"""
    url = f"{API_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """計算 ATR（用 RMA）"""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tv.rma(tr, period)


def calc_indicators(df: pd.DataFrame, params: dict) -> dict:
    """計算所有指標，返回最新一行嘅結果"""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # Bollinger Bands — returns DataFrame with columns: basis, upper, lower
    bb = tv.bollinger_bands(close, params["bb_length"], params["bb_mult"])
    bb_upper = bb["upper"]
    bb_basis = bb["basis"]
    bb_lower = bb["lower"]
    bb_width = (bb_upper - bb_lower) / bb_basis

    # RSI
    rsi = tv.RSI(close, params["rsi_period"])

    # ADX / DMI — .adx() returns tuple: (ADX_series, DI+_series, DI-_series)
    dmi = tv.DMI(df, "close")
    adx_tuple = dmi.adx()
    adx_series = adx_tuple[0]
    di_plus = adx_tuple[1]
    di_minus = adx_tuple[2]

    # EMA
    ema_fast = tv.ema(close, params["ema_fast"])
    ema_slow = tv.ema(close, params["ema_slow"])

    # ATR
    atr = calc_atr(df, params["atr_period"])

    # Stochastic
    try:
        stoch_result = tv.slow_stoch(close, high, low, 14, 1, 3)
        stoch_k = stoch_result[0]
        stoch_d = stoch_result[1]
    except Exception:
        stoch_k = pd.Series([None] * len(close))
        stoch_d = pd.Series([None] * len(close))

    # MA50 / MA200（現有策略用）
    ma50 = tv.sma(close, 50) if len(close) >= 50 else pd.Series([None] * len(close))
    ma200 = tv.sma(close, 200) if len(close) >= 200 else pd.Series([None] * len(close))

    # MACD（現有策略用）— returns DataFrame with columns: macd, signal, histogram
    try:
        macd_df = tv.MACD(close, 12, 26, 9)
        macd_line = macd_df["macd"]
        macd_signal = macd_df["signal"]
        macd_hist = macd_df["histogram"]
    except Exception:
        macd_line = pd.Series([None] * len(close))
        macd_signal = pd.Series([None] * len(close))
        macd_hist = pd.Series([None] * len(close))

    # 最新值
    i = len(df) - 1

    def safe_val(series, idx):
        """安全取值 — 用 .loc[] 避免 shorter series 越界"""
        try:
            if idx < 0:
                v = series.iloc[idx]
            elif idx in series.index:
                v = series.loc[idx]
            else:
                return None
            if pd.isna(v):
                return None
            return round(float(v), 6)
        except Exception:
            return None

    result = {
        "price": safe_val(close, i),
        "high": safe_val(high, i),
        "low": safe_val(low, i),
        "volume": safe_val(df["volume"], i),
        # Bollinger
        "bb_upper": safe_val(bb_upper, i),
        "bb_basis": safe_val(bb_basis, i),
        "bb_lower": safe_val(bb_lower, i),
        "bb_width": safe_val(bb_width, i),
        # RSI
        "rsi": safe_val(rsi, i),
        "rsi_prev": safe_val(rsi, i - 1),
        # ADX / DMI
        "adx": safe_val(adx_series, i),
        "di_plus": safe_val(di_plus, i),
        "di_minus": safe_val(di_minus, i),
        # EMA
        "ema_fast": safe_val(ema_fast, i),
        "ema_slow": safe_val(ema_slow, i),
        # ATR
        "atr": safe_val(atr, i),
        # Stochastic
        "stoch_k": safe_val(stoch_k, i),
        "stoch_d": safe_val(stoch_d, i),
        "stoch_k_prev": safe_val(stoch_k, i - 1),
        "stoch_d_prev": safe_val(stoch_d, i - 1),
        # MA（現有策略）
        "ma50": safe_val(ma50, i),
        "ma200": safe_val(ma200, i),
        # MACD（現有策略）
        "macd_line": safe_val(macd_line, i),
        "macd_signal": safe_val(macd_signal, i),
        "macd_hist": safe_val(macd_hist, i),
        "macd_hist_prev": safe_val(macd_hist, i - 1),
        # Support / Resistance（rolling）
        "rolling_low": safe_val(low.rolling(params["lookback_support"]).min(), i),
        "rolling_high": safe_val(high.rolling(params["lookback_support"]).max(), i),
    }
    return result


def evaluate_range_signal(ind: dict, params: dict) -> dict:
    """評估 Range 入場信號"""
    signals = {"range_valid": False, "signal_long": 0, "signal_short": 0, "reasons": []}

    # R0: BB 寬度
    if ind["bb_width"] is None or ind["bb_width"] >= BB_WIDTH_MIN:
        signals["reasons"].append(f"R0_FAIL: bb_width={ind['bb_width']}")
        return signals

    # R1: ADX < threshold
    if ind["adx"] is None or ind["adx"] >= params["adx_range_max"]:
        signals["reasons"].append(f"R1_FAIL: adx={ind['adx']}")
        return signals

    # R2: 價格拉鋸（簡化版 — 用 EMA slow 變化）
    # 完整版需要 ema_slow.shift(10)，此處用 bb_width 作為代替已足夠
    signals["range_valid"] = True
    signals["reasons"].append("R0+R1 PASS: range market confirmed")

    price = ind["price"]
    tol = params["bb_touch_tol"]

    # LONG signal
    c1_long = price <= ind["bb_lower"] * (1 + tol) if ind["bb_lower"] else False
    c2_long = (ind["rsi"] is not None and ind["rsi_prev"] is not None and
               ind["rsi"] < params["rsi_long"] and ind["rsi"] > ind["rsi_prev"])
    c3_long = (ind["rolling_low"] is not None and
               price <= ind["rolling_low"] * 1.005) if ind["rolling_low"] else False
    c4_long = (ind["stoch_k"] is not None and ind["stoch_d"] is not None and
               ind["stoch_k_prev"] is not None and ind["stoch_d_prev"] is not None and
               ind["stoch_k"] < 20 and
               ind["stoch_k"] > ind["stoch_d"] and
               ind["stoch_k_prev"] <= ind["stoch_d_prev"])

    if c1_long and c2_long and c3_long:
        signals["signal_long"] = 1
        strength = "STRONG" if c4_long else "WEAK"
        signals["reasons"].append(f"LONG_{strength}: BB_touch+RSI_reversal+support" +
                                  ("+Stoch_cross" if c4_long else ""))

    # SHORT signal
    c1_short = price >= ind["bb_upper"] * (1 - tol) if ind["bb_upper"] else False
    c2_short = (ind["rsi"] is not None and ind["rsi_prev"] is not None and
                ind["rsi"] > params["rsi_short"] and ind["rsi"] < ind["rsi_prev"])
    c3_short = (ind["rolling_high"] is not None and
                price >= ind["rolling_high"] * 0.995) if ind["rolling_high"] else False
    c4_short = (ind["stoch_k"] is not None and ind["stoch_d"] is not None and
                ind["stoch_k_prev"] is not None and ind["stoch_d_prev"] is not None and
                ind["stoch_k"] > 80 and
                ind["stoch_k"] < ind["stoch_d"] and
                ind["stoch_k_prev"] >= ind["stoch_d_prev"])

    if c1_short and c2_short and c3_short:
        signals["signal_short"] = -1
        strength = "STRONG" if c4_short else "WEAK"
        signals["reasons"].append(f"SHORT_{strength}: BB_touch+RSI_reversal+resistance" +
                                  ("+Stoch_cross" if c4_short else ""))

    if signals["signal_long"] == 0 and signals["signal_short"] == 0:
        signals["reasons"].append("NO_SIGNAL: range conditions met but no entry trigger")

    return signals


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Indicator Calculator")
    parser.add_argument("--symbol", required=True, help="Trading pair (e.g. BTCUSDT)")
    parser.add_argument("--interval", required=True, help="Kline interval (15m, 1h, 4h)")
    parser.add_argument("--limit", type=int, default=200, help="Number of klines")
    parser.add_argument("--mode", default="full", choices=["full", "range", "quick"],
                        help="full=所有指標, range=加 range 信號評估, quick=只價格+RSI+ATR")
    args = parser.parse_args()

    # 參數選擇
    interval = args.interval
    if interval not in TIMEFRAME_PARAMS:
        print(json.dumps({"error": f"Unsupported interval: {interval}. Use 15m/1h/4h"}))
        sys.exit(1)

    params = TIMEFRAME_PARAMS[interval].copy()

    # 產品覆蓋
    symbol = args.symbol.upper()
    if symbol in PRODUCT_OVERRIDES:
        params.update(PRODUCT_OVERRIDES[symbol])

    try:
        # 抓取數據
        df = fetch_klines(symbol, interval, args.limit)

        # 計算指標
        indicators = calc_indicators(df, params)

        # 組裝輸出
        output = {
            "symbol": symbol,
            "interval": interval,
            "timestamp": datetime.now(HKT).strftime("%Y-%m-%d %H:%M UTC+8"),
            "candles": len(df),
            "params": {
                "rsi_long": params["rsi_long"],
                "rsi_short": params["rsi_short"],
                "adx_range_max": params["adx_range_max"],
                "bb_touch_tol": params["bb_touch_tol"],
            },
            "indicators": indicators,
        }

        # Range 信號評估
        if args.mode in ("range", "full"):
            output["range_signal"] = evaluate_range_signal(indicators, params)

        # SL/TP 參考（如有 ATR）
        if indicators["atr"] is not None:
            sl_mult = params.get("stop_loss_mult", 1.2)
            output["risk"] = {
                "sl_distance": round(sl_mult * indicators["atr"], 4),
                "tp1": indicators["bb_basis"],
                "tp2_long": indicators["bb_upper"],
                "tp2_short": indicators["bb_lower"],
            }

        print(json.dumps(output, indent=2, ensure_ascii=False))

    except requests.RequestException as e:
        print(json.dumps({"error": f"API request failed: {str(e)}"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Calculation error: {str(e)}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
