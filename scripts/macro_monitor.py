#!/usr/bin/env python3
"""
macro_monitor.py — 宏觀市場流動性監察

監察 DXY、原油、黃金、白銀、VIX、2 年國債、日經等宏觀指標。
偵測顯著單日變動及多日趨勢，寫入 shared/news_manual.json。

設計：Group A（Direct API 為主）+ Group B（yfinance 為主），互為 fallback。
每個 symbol 之間加 delay 錯開，減少 Yahoo 負荷。

排程：每 4 小時（配合 4H 交易框架）
手動：python3 ~/projects/axc-trading/scripts/macro_monitor.py
"""

import hashlib
import json
import logging
import os
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SHARED_DIR = BASE_DIR / "shared"
NEWS_MANUAL_FILE = SHARED_DIR / "news_manual.json"
MACRO_STATE_FILE = SHARED_DIR / "macro_state.json"

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
USER_AGENT = "macro_monitor/1.0"
FETCH_DELAY = 2  # 每個 symbol 之間等 2 秒

# ── Symbol 配置 ──
# group: A = Direct API 為主, B = yfinance 為主
# alert_pct: 單日變動 ≥ 此值觸發警報（%）
# trend_days: 連續 N 日同方向 = 趨勢確認
# rise_impact / fall_impact: 該 symbol 升/跌對 crypto 嘅影響描述
# weight: 1-5，對 crypto 嘅影響權重（5 = 最重要）
# silent: True = 只記錄唔出警報（用於交叉驗證，如 ETF 對照期貨）
SYMBOLS = {
    "DX-Y.NYB": {
        "name": "美元指數 DXY",
        "group": "A",
        "alert_pct": 0.5,
        "trend_days": 3,
        "rise_impact": "美元走強→資金回流美元→crypto利空",
        "fall_impact": "美元走弱→流動性外溢→crypto利好",
        "weight": 5,
    },
    "ZT=F": {
        "name": "2年期國債期貨",
        "group": "B",
        "alert_pct": 0.3,
        "trend_days": 3,
        "rise_impact": "債價升(息跌)→寬鬆預期→crypto利好",
        "fall_impact": "債價跌(息升)→緊縮預期→crypto利空",
        "weight": 4,
    },
    "^VIX": {
        "name": "恐慌指數 VIX",
        "group": "A",
        "alert_pct": 15.0,
        "trend_days": 2,
        "rise_impact": "VIX飆升→市場恐慌→crypto利空",
        "fall_impact": "VIX回落→恐慌消退→crypto利好",
        "weight": 4,
        "alert_absolute": 30,
    },
    "VXX": {
        "name": "VXX 波動率ETN",
        "group": "A",
        "alert_pct": 10.0,
        "trend_days": 2,
        "rise_impact": "VXX升→波動率資金流入→避險增加",
        "fall_impact": "VXX跌→波動率資金流出→市場穩定",
        "weight": 2,
        "track_volume": True,
    },
    "CL=F": {
        "name": "WTI 原油",
        "group": "B",
        "alert_pct": 3.0,
        "trend_days": 3,
        "rise_impact": "油升→通脹壓力+避險→crypto利空",
        "fall_impact": "油跌→通脹緩解→crypto中性偏好",
        "weight": 3,
    },
    "GC=F": {
        "name": "黃金期貨",
        "group": "B",
        "alert_pct": 1.5,
        "trend_days": 3,
        "rise_impact": "金升→避險需求→BTC或跟升",
        "fall_impact": "金跌→risk-on→crypto中性",
        "weight": 3,
    },
    "SI=F": {
        "name": "白銀期貨",
        "group": "B",
        "alert_pct": 2.5,
        "trend_days": 3,
        "rise_impact": "銀升→通脹對沖需求→crypto中性偏好",
        "fall_impact": "銀跌→需求轉弱→crypto中性偏淡",
        "weight": 2,
    },
    "GLD": {
        "name": "黃金現價(ETF)",
        "group": "B",
        "alert_pct": 1.5,
        "trend_days": 3,
        "rise_impact": "金現價升→避險需求確認",
        "fall_impact": "金現價跌→risk-on確認",
        "weight": 2,
        "silent": True,
    },
    "SLV": {
        "name": "白銀現價(ETF)",
        "group": "B",
        "alert_pct": 2.5,
        "trend_days": 3,
        "rise_impact": "銀現價升→金屬需求確認",
        "fall_impact": "銀現價跌→需求轉弱確認",
        "weight": 1,
        "silent": True,
    },
    "^N225": {
        "name": "日經225",
        "group": "A",
        "alert_pct": 2.0,
        "trend_days": 3,
        "rise_impact": "日經升→亞洲資金活躍→crypto中性偏好",
        "fall_impact": "日經跌→carry trade平倉風險→crypto利空",
        "weight": 3,
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MACRO] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("macro_monitor")


# ── Fetch: Direct Yahoo API (stdlib only) ──

def fetch_direct(symbol: str) -> dict | None:
    """Fetch via Yahoo Finance direct chart API. Zero dependencies."""
    url = f"{YAHOO_BASE}/{symbol}?interval=1d&range=10d"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        log.warning(f"[Direct] {symbol}: {e}")
        return None

    result = data.get("chart", {}).get("result", [{}])[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp", [])
    quotes = result.get("indicators", {}).get("quote", [{}])[0]
    closes = quotes.get("close", [])
    volumes = quotes.get("volume", [])

    padded_vols = volumes if volumes else [None] * len(closes)
    valid = [(t, c, v) for t, c, v in zip(timestamps, closes, padded_vols) if c is not None]
    if len(valid) < 2:
        log.warning(f"[Direct] {symbol}: not enough data ({len(valid)} points)")
        return None

    current_price = meta.get("regularMarketPrice", valid[-1][1])
    daily_changes = []
    for i in range(1, len(valid)):
        pct = (valid[i][1] - valid[i - 1][1]) / valid[i - 1][1] * 100
        daily_changes.append(pct)

    out = {
        "price": round(current_price, 4),
        "prev_close": round(valid[-2][1], 4),
        "daily_change_pct": round(daily_changes[-1], 4) if daily_changes else 0,
        "daily_changes": [round(c, 4) for c in daily_changes[-5:]],
        "data_points": len(valid),
        "method": "direct",
    }

    valid_vols = [v for _, _, v in valid if v is not None]
    if valid_vols:
        out["volume"] = int(valid_vols[-1])
        out["avg_volume"] = int(sum(valid_vols) / len(valid_vols))

    return out


# ── Fetch: yfinance ──

def fetch_yfinance(symbol: str) -> dict | None:
    """Fetch via yfinance library. Needs pip install yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning(f"[yfinance] not installed, cannot fetch {symbol}")
        return None

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="10d", interval="1d")
    except Exception as e:
        log.warning(f"[yfinance] {symbol}: {e}")
        return None

    if hist.empty or len(hist) < 2:
        log.warning(f"[yfinance] {symbol}: not enough data")
        return None

    closes = hist["Close"].dropna().tolist()
    if len(closes) < 2:
        return None

    daily_changes = []
    for i in range(1, len(closes)):
        pct = (closes[i] - closes[i - 1]) / closes[i - 1] * 100
        daily_changes.append(pct)

    out = {
        "price": round(closes[-1], 4),
        "prev_close": round(closes[-2], 4),
        "daily_change_pct": round(daily_changes[-1], 4) if daily_changes else 0,
        "daily_changes": [round(c, 4) for c in daily_changes[-5:]],
        "data_points": len(closes),
        "method": "yfinance",
    }

    if "Volume" in hist.columns:
        vols = hist["Volume"].dropna().tolist()
        if vols and any(v > 0 for v in vols):
            out["volume"] = int(vols[-1])
            out["avg_volume"] = int(sum(vols) / len(vols)) if vols else 0

    return out


# ── Fetch 調度：primary + fallback ──

def fetch_symbol(symbol: str, group: str) -> dict | None:
    """Group A → direct first; Group B → yfinance first. 失敗自動切換。"""
    if group == "A":
        primary, fallback = fetch_direct, fetch_yfinance
    else:
        primary, fallback = fetch_yfinance, fetch_direct

    data = primary(symbol)
    if data is not None:
        return data

    log.info(f"{symbol}: primary failed, trying fallback...")
    data = fallback(symbol)
    if data is not None:
        log.info(f"{symbol}: fallback OK ({data['method']})")
    return data


# ── 趨勢偵測 ──

def detect_trend(daily_changes: list[float], n_days: int) -> str | None:
    """連續 N 日同方向 → 'up' / 'down'。否則 None。"""
    if len(daily_changes) < n_days:
        return None
    recent = daily_changes[-n_days:]
    if all(c < 0 for c in recent):
        return "down"
    if all(c > 0 for c in recent):
        return "up"
    return None


# ── 分析 + 產生警報 ──

def analyze_symbol(symbol: str, cfg: dict, data: dict, prev_state: dict) -> list[str]:
    """對單個 symbol 產生警報。silent symbol 唔出警報。"""
    if cfg.get("silent"):
        return []

    alerts = []
    name = cfg["name"]
    price = data["price"]
    change = data["daily_change_pct"]

    # 1) 顯著單日變動
    if abs(change) >= cfg["alert_pct"]:
        if change > 0:
            direction, impact = "升", cfg["rise_impact"]
        else:
            direction, impact = "跌", cfg["fall_impact"]
        alerts.append(
            f"[{symbol}] {name}顯著{direction} {change:+.2f}% "
            f"(現價 {price}) — {impact}"
        )

    # 2) VIX 絕對值警戒
    if cfg.get("alert_absolute") and price > cfg["alert_absolute"]:
        alerts.append(
            f"[{symbol}] {name}處於極端水平 {price:.1f} "
            f"(閾值>{cfg['alert_absolute']}) — crypto利空警告"
        )

    # 3) VXX 成交量異常（>2x 平均）
    if cfg.get("track_volume") and data.get("volume") and data.get("avg_volume"):
        avg = data["avg_volume"]
        if avg > 0:
            ratio = data["volume"] / avg
            if ratio > 2.0:
                alerts.append(
                    f"[{symbol}] {name}成交量異常 {ratio:.1f}x均量 "
                    f"(現價 {price}) — 波動率資金湧入"
                )

    # 4) 趨勢變化（同方向連續 N 日，且同上次唔同先報）
    trend = detect_trend(data["daily_changes"], cfg["trend_days"])
    prev_trend = prev_state.get(f"{symbol}_trend")
    n = cfg["trend_days"]

    if trend and trend != prev_trend:
        if trend == "up":
            alerts.append(
                f"[{symbol}] {name}連續{n}日升勢確認 "
                f"(現價 {price}) — {cfg['rise_impact']}"
            )
        else:
            alerts.append(
                f"[{symbol}] {name}連續{n}日跌勢確認 "
                f"(現價 {price}) — {cfg['fall_impact']}"
            )

    return alerts


# ── State / IO ──

def load_macro_state() -> dict:
    if not MACRO_STATE_FILE.exists():
        return {}
    try:
        return json.loads(MACRO_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def atomic_write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_to_news_manual(text: str) -> bool:
    existing = {"entries": [], "processed_before": ""}
    if NEWS_MANUAL_FILE.exists():
        try:
            existing = json.loads(NEWS_MANUAL_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    seen = {hashlib.md5(e.get("text", "").encode()).hexdigest() for e in existing.get("entries", [])}
    text_hash = hashlib.md5(text.encode()).hexdigest()
    if text_hash in seen:
        return False

    existing["entries"].append({
        "text": text,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "source": "macro-monitor",
    })
    existing["entries"] = existing["entries"][-100:]
    atomic_write_json(NEWS_MANUAL_FILE, existing)
    return True


# ── Main ──

def main():
    log.info(f"Macro monitor starting ({len(SYMBOLS)} symbols)...")

    prev_state = load_macro_state()
    new_state = {"updated_at": datetime.now(timezone.utc).isoformat()}
    all_alerts = []

    # 分組錯開：先 Group A (Direct)，再 Group B (yfinance)
    group_a = [(s, c) for s, c in SYMBOLS.items() if c["group"] == "A"]
    group_b = [(s, c) for s, c in SYMBOLS.items() if c["group"] == "B"]

    for label, group in [("A/Direct", group_a), ("B/yfinance", group_b)]:
        log.info(f"--- Group {label} ({len(group)} symbols) ---")

        for symbol, cfg in group:
            data = fetch_symbol(symbol, cfg["group"])
            if not data:
                log.warning(f"{symbol}: FAILED (both methods)")
                continue

            tag = "🔇" if cfg.get("silent") else ""
            log.info(
                f"  {symbol:12s} {data['price']:>12} ({data['daily_change_pct']:+.2f}%) "
                f"via {data['method']} {tag}"
            )

            # Save state
            new_state[f"{symbol}_price"] = data["price"]
            new_state[f"{symbol}_change"] = data["daily_change_pct"]
            new_state[f"{symbol}_trend"] = detect_trend(
                data["daily_changes"], cfg["trend_days"]
            )
            new_state[f"{symbol}_changes"] = data["daily_changes"]
            if data.get("volume"):
                new_state[f"{symbol}_volume"] = data["volume"]
                new_state[f"{symbol}_avg_volume"] = data.get("avg_volume", 0)

            # Analyze
            alerts = analyze_symbol(symbol, cfg, data, prev_state)
            all_alerts.extend(alerts)

            time.sleep(FETCH_DELAY)

    # Save state
    atomic_write_json(MACRO_STATE_FILE, new_state)

    # Write alerts
    if not all_alerts:
        log.info("No significant macro events")
        return

    added_count = 0
    for alert in all_alerts:
        added = append_to_news_manual(alert)
        if added:
            log.info(f"  ALERT: {alert}")
            added_count += 1
        else:
            log.info(f"  dedup: {alert}")

    log.info(f"Done: {len(all_alerts)} alerts, {added_count} new")


if __name__ == "__main__":
    main()
