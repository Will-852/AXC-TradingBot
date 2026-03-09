"""
metrics.py — 策略表現計算器

讀取 trades.jsonl，計算核心利潤指標。
設計決定：獨立於 dashboard.py，可從 tg_bot / trader_cycle / CLI 調用。
"""
import json
import logging
import os
from pathlib import Path
from collections import defaultdict

log = logging.getLogger(__name__)

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
TRADES_FILE = BASE_DIR / "memory" / "store" / "trades.jsonl"

# 過濾條件：entry < $10 嘅 USDT pair = 測試數據
_MIN_ENTRY_THRESHOLD = 10.0


def _load_trades() -> list[dict]:
    """讀取 trades.jsonl，merge entry + exit records。

    trades.jsonl 有兩種 record：
    - entry: exit=null（開倉）
    - exit: exit!=null（平倉，side 可能係 CLOSED 或原方向）

    merge key: symbol|side|entry_price（同 dashboard.py 一致）
    """
    if not TRADES_FILE.exists():
        return []

    raw = []
    with open(TRADES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    entries = []
    exits = {}

    for rec in raw:
        symbol = rec.get("symbol", "?")
        side = rec.get("side", "?")
        entry_val = float(rec.get("entry", 0))
        exit_price = rec.get("exit")

        if exit_price is not None:
            key = f"{symbol}|{side}|{entry_val}"
            exits[key] = rec
        else:
            entries.append(rec)

    merged = []
    for rec in entries:
        symbol = rec.get("symbol", "?")
        side = rec.get("side", "?")
        entry_val = float(rec.get("entry", 0))

        # 過濾測試 / 垃圾數據
        if entry_val < _MIN_ENTRY_THRESHOLD and symbol.endswith("USDT"):
            continue

        key = f"{symbol}|{side}|{entry_val}"
        exit_rec = exits.pop(key, None)

        if exit_rec:
            exit_price = float(exit_rec.get("exit", 0))
            pnl = float(exit_rec.get("pnl", 0))
            sl_price = exit_rec.get("sl_price") or rec.get("sl_price")
            merged.append({
                "symbol": symbol,
                "side": side,
                "entry": entry_val,
                "exit": exit_price,
                "pnl": pnl,
                "sl_price": float(sl_price) if sl_price else None,
                "ts": rec.get("ts", ""),
                "closed": True,
            })
        else:
            merged.append({
                "symbol": symbol,
                "side": side,
                "entry": entry_val,
                "exit": None,
                "pnl": 0.0,
                "sl_price": float(rec.get("sl_price")) if rec.get("sl_price") else None,
                "ts": rec.get("ts", ""),
                "closed": False,
            })

    # Orphan exit records（有 exit 冇 entry）
    for key, rec in exits.items():
        entry_val = float(rec.get("entry", 0))
        exit_val = float(rec.get("exit", 0))
        pnl = float(rec.get("pnl", 0))
        symbol = rec.get("symbol", "?")

        # 過濾 entry=0 嘅垃圾 CLOSED records
        if entry_val < _MIN_ENTRY_THRESHOLD and symbol.endswith("USDT"):
            continue
        if pnl == 0.0 and exit_val == 0:
            continue

        merged.append({
            "symbol": symbol,
            "side": rec.get("side", "?"),
            "entry": entry_val,
            "exit": exit_val,
            "pnl": pnl,
            "sl_price": float(rec.get("sl_price")) if rec.get("sl_price") else None,
            "ts": rec.get("ts", ""),
            "closed": True,
        })

    return merged


def _calc_r_multiple(trade: dict) -> float | None:
    """計算單筆交易嘅 R-Multiple。

    R = PnL / risk_per_unit
    冇 sl_price 就計唔到，返回 None。
    """
    sl = trade.get("sl_price")
    if sl is None or trade["entry"] == 0:
        return None
    risk_per_unit = abs(trade["entry"] - sl)
    if risk_per_unit == 0:
        return None
    # PnL 已經係絕對值（唔係 per unit），所以直接用
    # 但如果我哋冇 size 資訊，就用 price-based R
    entry = trade["entry"]
    exit_p = trade["exit"]
    if exit_p is None:
        return None
    if trade["side"] in ("LONG", "BUY"):
        price_pnl = exit_p - entry
    else:
        price_pnl = entry - exit_p
    return price_pnl / risk_per_unit


def calculate_metrics() -> dict:
    """計算策略表現指標。

    返回 dict：
    - expectancy_r: 平均每筆 R-Multiple（如有 SL 數據）
    - expectancy_usd: 平均每筆 PnL（USD）
    - profit_factor: 總利潤 / 總虧損
    - max_drawdown_pct: 最大回撤 %
    - total / winners / losers: 交易數
    - avg_win / avg_loss: 平均贏蝕（USD）
    - best_r / worst_r: 最好最差 R
    - best_usd / worst_usd: 最好最差 USD
    - streak: 最近 N 筆嘅贏蝕序列
    - per_pair: 每個 pair 嘅 W/L/PF
    """
    all_trades = _load_trades()
    closed = [t for t in all_trades if t["closed"] and t["pnl"] != 0.0]

    result = {
        "total": len(closed),
        "winners": 0,
        "losers": 0,
        "expectancy_r": None,
        "expectancy_usd": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_pct": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "best_r": None,
        "worst_r": None,
        "best_usd": 0.0,
        "worst_usd": 0.0,
        "streak": [],
        "per_pair": {},
        "open_count": len([t for t in all_trades if not t["closed"]]),
    }

    if not closed:
        return result

    # ── 基本分類 ──
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] < 0]
    result["winners"] = len(wins)
    result["losers"] = len(losses)

    # ── Expectancy (USD) ──
    pnls = [t["pnl"] for t in closed]
    result["expectancy_usd"] = round(sum(pnls) / len(pnls), 2)

    # ── Avg Win / Avg Loss ──
    if wins:
        result["avg_win"] = round(sum(t["pnl"] for t in wins) / len(wins), 2)
    if losses:
        result["avg_loss"] = round(sum(t["pnl"] for t in losses) / len(losses), 2)

    # ── Best / Worst (USD) ──
    result["best_usd"] = round(max(pnls), 2)
    result["worst_usd"] = round(min(pnls), 2)

    # ── Profit Factor ──
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    if gross_loss > 0:
        result["profit_factor"] = round(gross_profit / gross_loss, 2)
    else:
        result["profit_factor"] = float("inf") if gross_profit > 0 else 0.0

    # ── Max Drawdown ──
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in closed:
        running += t["pnl"]
        if running > peak:
            peak = running
        dd = (peak - running) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    result["max_drawdown_pct"] = round(max_dd * 100, 1)

    # ── R-Multiple 計算（只有有 sl_price 嘅 trades）──
    r_values = []
    for t in closed:
        r = _calc_r_multiple(t)
        if r is not None:
            r_values.append(r)

    if r_values:
        result["expectancy_r"] = round(sum(r_values) / len(r_values), 2)
        result["best_r"] = round(max(r_values), 2)
        result["worst_r"] = round(min(r_values), 2)

    # ── Streak（最近 10 筆）──
    result["streak"] = ["W" if t["pnl"] > 0 else "L" for t in closed[-10:]]

    # ── Per-pair breakdown ──
    pair_data = defaultdict(lambda: {"wins": 0, "losses": 0, "gross_profit": 0.0, "gross_loss": 0.0})
    for t in closed:
        sym = t["symbol"]
        if t["pnl"] > 0:
            pair_data[sym]["wins"] += 1
            pair_data[sym]["gross_profit"] += t["pnl"]
        else:
            pair_data[sym]["losses"] += 1
            pair_data[sym]["gross_loss"] += abs(t["pnl"])

    for sym, d in pair_data.items():
        pf = d["gross_profit"] / d["gross_loss"] if d["gross_loss"] > 0 else float("inf")
        result["per_pair"][sym] = {
            "wins": d["wins"],
            "losses": d["losses"],
            "pf": round(pf, 1),
            "net": round(d["gross_profit"] - d["gross_loss"], 2),
        }

    return result


def format_stats_text(m: dict) -> str:
    """格式化 Telegram 報告（廣東話 + HTML）。"""
    if m["total"] == 0:
        return "<b>📊 策略表現</b>\n\n未有已平倉交易記錄。"

    lines = ["<b>📊 策略表現</b>", ""]

    # 核心指標
    lines.append("<b>核心指標</b>")
    if m["expectancy_r"] is not None:
        lines.append(f"  期望值:     <b>{m['expectancy_r']:+.2f}R</b> (${m['expectancy_usd']:+.2f})")
    else:
        lines.append(f"  期望值:     <b>${m['expectancy_usd']:+.2f}</b>/筆")
    pf_str = f"{m['profit_factor']:.1f}" if m["profit_factor"] != float("inf") else "∞"
    lines.append(f"  利潤因子:   <b>{pf_str}</b>")
    lines.append(f"  最大回撤:   <b>{m['max_drawdown_pct']:.1f}%</b>")
    lines.append("")

    # 勝負分析
    lines.append("<b>勝負分析</b>")
    if m["avg_win"]:
        lines.append(f"  平均贏:  ${m['avg_win']:+.2f}")
    if m["avg_loss"]:
        lines.append(f"  平均蝕:  ${m['avg_loss']:+.2f}")

    best_str = f"{m['best_r']:+.1f}R" if m["best_r"] is not None else f"${m['best_usd']:+.2f}"
    worst_str = f"{m['worst_r']:+.1f}R" if m["worst_r"] is not None else f"${m['worst_usd']:+.2f}"
    lines.append(f"  最好一筆:  {best_str}")
    lines.append(f"  最差一筆:  {worst_str}")
    lines.append("")

    # Per-pair
    if m["per_pair"]:
        lines.append("<b>交易對表現</b>")
        for sym, d in sorted(m["per_pair"].items()):
            pf_s = f"{d['pf']:.1f}" if d["pf"] != float("inf") else "∞"
            prefix = sym.replace("USDT", "")
            lines.append(f"  {prefix}: {d['wins']}W/{d['losses']}L  PF {pf_s}  ${d['net']:+.2f}")
        lines.append("")

    # Streak + 總數
    if m["streak"]:
        streak_icons = "".join("🟢" if s == "W" else "🔴" for s in m["streak"])
        lines.append(f"近期: {streak_icons}")
    lines.append(f"總數: {m['total']} | 贏: {m['winners']} | 蝕: {m['losers']}")

    if m["open_count"]:
        lines.append(f"未平倉: {m['open_count']}")

    return "\n".join(lines)
