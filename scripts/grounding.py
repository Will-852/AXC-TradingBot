"""
grounding.py — 結構化實時市場數據注入 + inline citation 支持

設計決定：
- from_files() 讀 shared/ JSON/MD，用於 tg_bot（file-based，零 CycleContext 依賴）
- from_context() 直接從 CycleContext 提取，零 I/O，用於 trader_cycle pipeline
- Key 命名：{PAIR}_{IND}_{TF} 格式，方便 LLM 引用
- format_grounding_prompt() 將 snapshot 格式化為 LLM prompt 注入文字
- citation_instruction() 獨立函數，fit Haiku 10K system prompt 限制
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trader_cycle.core.context import CycleContext

log = logging.getLogger(__name__)

SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"

# ── Key 命名 ────────────────────────────────────────────
# 價格：{PAIR}_PRICE          → BTC_PRICE=71715.5
# 指標：{PAIR}_{IND}_{TF}    → BTC_RSI_4H=45.2
# 制度：REGIME                → REGIME=RANGE
# 倉位：{PAIR}_POS            → BTC_POS=LONG@68500
# 風控：RISK_OK / COOLDOWN    → RISK_OK=true
# 情緒：NEWS_SENTIMENT        → NEWS_SENTIMENT=mixed

_PAIR_STRIP = re.compile(r"USDT$", re.IGNORECASE)

# SCAN_CONFIG.md 指標 key → grounding key 映射
_SCAN_INDICATOR_MAP = {
    "ATR": "ATR",
    "support": "SUPPORT",
    "resistance": "RESISTANCE",
}

# from_context() 提取嘅指標 keys
_IND_KEYS = ("rsi", "adx", "macd_hist", "atr", "bb_width")


def _pair_prefix(symbol: str) -> str:
    """BTCUSDT → BTC, ETHUSDT → ETH"""
    return _PAIR_STRIP.sub("", symbol).upper()


def _safe_float(val, default=None):
    """Graceful float conversion — SCAN_CONFIG values 可能係 string。"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _read_json(path: Path) -> dict:
    """Read JSON file, return empty dict on error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        log.warning("grounding: cannot read %s: %s", path, e)
        return {}


def _parse_scan_config(path: Path) -> dict:
    """Parse SCAN_CONFIG.md key-value pairs (format: key: value)."""
    result = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as e:
        log.warning("grounding: cannot read %s: %s", path, e)
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ": " in line:
            key, _, val = line.partition(": ")
            result[key.strip()] = val.strip()
    return result


# ─────────────────────────────────────────────────────────
# from_files() — tg_bot 用，讀 shared/ 文件
# ─────────────────────────────────────────────────────────

def from_files() -> dict[str, str | float]:
    """
    從 shared/ 文件讀取實時數據，輸出結構化 key-value snapshot。
    用於 tg_bot（file-based，唔需要 CycleContext）。
    """
    snap: dict[str, str | float] = {}

    # 1. prices_cache.json — 價格 + 24h 變幅
    prices = _read_json(SHARED_DIR / "prices_cache.json")
    for symbol, data in prices.items():
        prefix = _pair_prefix(symbol)
        price = _safe_float(data.get("price"))
        if price is not None:
            snap[f"{prefix}_PRICE"] = price
        chg = _safe_float(data.get("change"))
        if chg is not None:
            snap[f"{prefix}_CHG24H"] = round(chg, 2)

    # 2. TRADE_STATE.json — 制度 + 倉位 + 風控
    state = _read_json(SHARED_DIR / "TRADE_STATE.json")
    sys_block = state.get("system", {})
    snap["REGIME"] = sys_block.get("market_mode", "UNKNOWN")

    # 倉位
    for pos in state.get("positions", []):
        pair = pos.get("pair", "")
        prefix = _pair_prefix(pair)
        direction = pos.get("direction", "?")
        entry = _safe_float(pos.get("entry_price"), 0)
        snap[f"{prefix}_POS"] = f"{direction}@{entry}"

    # 風控
    risk = state.get("risk", {})
    snap["RISK_OK"] = not risk.get("cooldown_active", False)
    if risk.get("cooldown_active"):
        snap["COOLDOWN"] = risk.get("cooldown_until", risk.get("cooldown_ends", "—"))

    # 3. SCAN_CONFIG.md — ATR + S/R levels（從 key-value 解析）
    scan = _parse_scan_config(SHARED_DIR / "SCAN_CONFIG.md")
    for symbol in prices:
        prefix = _pair_prefix(symbol)
        # ATR
        atr_val = _safe_float(scan.get(f"{prefix}_ATR"))
        if atr_val is not None:
            snap[f"{prefix}_ATR_4H"] = atr_val
        # Support / Resistance
        sup_val = _safe_float(scan.get(f"{prefix}_support"))
        if sup_val is not None:
            snap[f"{prefix}_SUPPORT"] = sup_val
        res_val = _safe_float(scan.get(f"{prefix}_resistance"))
        if res_val is not None:
            snap[f"{prefix}_RESISTANCE"] = res_val

    # 4. news_sentiment.json — 整體情緒
    news = _read_json(SHARED_DIR / "news_sentiment.json")
    overall = news.get("overall_sentiment", "unknown")
    snap["NEWS_SENTIMENT"] = overall

    return snap


# ─────────────────────────────────────────────────────────
# from_context() — trader_cycle pipeline 用，零 I/O
# ─────────────────────────────────────────────────────────

def from_context(ctx: CycleContext) -> dict[str, str | float]:
    """
    從 CycleContext 提取結構化 key-value snapshot。
    零 I/O，直接讀取 ctx 屬性。
    """
    snap: dict[str, str | float] = {}

    # 價格
    for symbol, ms in ctx.market_data.items():
        prefix = _pair_prefix(symbol)
        if ms.price is not None:
            snap[f"{prefix}_PRICE"] = ms.price
        if ms.price_change_24h_pct is not None:
            snap[f"{prefix}_CHG24H"] = round(ms.price_change_24h_pct, 2)

    # 指標（4h + 1h）
    for symbol, tf_data in ctx.indicators.items():
        prefix = _pair_prefix(symbol)
        for tf_label, tf_key in [("4H", "4h"), ("1H", "1h")]:
            ind = tf_data.get(tf_key, {})
            for k in _IND_KEYS:
                val = _safe_float(ind.get(k))
                if val is not None:
                    key_name = k.upper().replace("MACD_HIST", "MACD_H")
                    snap[f"{prefix}_{key_name}_{tf_label}"] = round(val, 4)

    # 制度
    snap["REGIME"] = ctx.market_mode or "UNKNOWN"

    # 倉位
    for pos in ctx.open_positions:
        prefix = _pair_prefix(pos.pair)
        snap[f"{prefix}_POS"] = f"{pos.direction}@{pos.entry_price}"

    # 風控
    snap["RISK_OK"] = not ctx.risk_blocked
    if ctx.cooldown_active:
        snap["COOLDOWN"] = str(ctx.cooldown_ends or "active")

    # 情緒
    overall = ctx.news_sentiment.get("overall_sentiment", "unknown")
    snap["NEWS_SENTIMENT"] = overall

    return snap


# ─────────────────────────────────────────────────────────
# format_grounding_prompt() — LLM prompt 注入
# ─────────────────────────────────────────────────────────

_CATEGORY_ORDER = [
    ("價格", lambda k: k.endswith("_PRICE")),
    ("24H變幅", lambda k: k.endswith("_CHG24H")),
    ("指標(4H)", lambda k: "_4H" in k),
    ("指標(1H)", lambda k: "_1H" in k),
    ("支撐/阻力", lambda k: k.endswith("_SUPPORT") or k.endswith("_RESISTANCE")),
    ("制度", lambda k: k == "REGIME"),
    ("倉位", lambda k: k.endswith("_POS")),
    ("風控", lambda k: k in ("RISK_OK", "COOLDOWN")),
    ("情緒", lambda k: k == "NEWS_SENTIMENT"),
]


def format_grounding_prompt(snapshot: dict[str, str | float],
                            max_chars: int = 1200) -> str:
    """
    將 snapshot dict 格式化為 LLM prompt 注入文字。
    分類排列，方便 LLM 快速定位。超過 max_chars 截斷低優先度類別。
    """
    lines = ["## 實時市場數據（引用格式: [KEY=VALUE]）"]
    used_keys: set[str] = set()
    total_len = len(lines[0])

    for cat_name, matcher in _CATEGORY_ORDER:
        cat_keys = [k for k in snapshot if matcher(k) and k not in used_keys]
        if not cat_keys:
            continue
        cat_line = f"【{cat_name}】" + " ".join(
            f"{k}={snapshot[k]}" for k in sorted(cat_keys)
        )
        if total_len + len(cat_line) + 1 > max_chars:
            break
        lines.append(cat_line)
        total_len += len(cat_line) + 1
        used_keys.update(cat_keys)

    return "\n".join(lines)


def citation_instruction() -> str:
    """
    Citation 指令，注入 system prompt。
    <300 字元，fit Haiku 10K system prompt 限制。
    """
    return (
        "引用規則：回覆時必須引用實時數據，格式 [KEY=VALUE]。"
        "例如 [BTC_PRICE=71715.5]。"
        "唔好虛構數字，只用上方提供嘅數據。"
    )
