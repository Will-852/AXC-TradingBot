#!/usr/bin/env python3
"""
tg_bot.py — OpenClaw Telegram 完整控制中心 v2.0

功能：
  查詢：/report /pos /bal /pnl /scan /log /health — 零 AI (slash_cmd live exchange)
  控制：/mode /pause /resume /sl breakeven
  下單：自然語言 + 二次確認 + 冷靜期
  分析：/ask + RAG 記憶
  推送：異常自動告警 (平倉報告 + agent 斷線)

安全：ALLOWED_CHAT_ID 白名單
Claude：via proxy urllib.request (no SDK needed)
"""
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Load .env ────────────────────────────────────
ENV_PATH = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))) / "secrets" / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Paths ─────────────────────────────────────────
BASE_DIR    = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SCRIPTS_DIR = BASE_DIR / "scripts"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from memory.writer import (write_conversation, write_analysis,
                            write_trade, read_last_entry)
from write_activity import write_activity
from memory.retriever import retrieve_full, format_for_prompt

import slash_cmd
from axc_client import OpenClawClient

_oc_client = OpenClawClient()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

# ── Config ────────────────────────────────────────
TG_TOKEN        = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
PROXY_API_KEY   = os.environ.get("PROXY_API_KEY", "")
PROXY_BASE_URL  = os.environ.get("PROXY_BASE_URL", "https://tao.plus7.plus/v1")
CLAUDE_MODEL    = "claude-haiku-4-5-20251001"

HKT = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("tg_bot")

# ── Pending orders (chat_id → order data) ─────────
pending_orders: dict = {}
PENDING_FILE = BASE_DIR / "shared/pending_orders.json"

# ── Short-term conversation memory (last 5 exchanges) ──
# 每個 chat_id 保留最近 5 組對話，10 分鐘無活動自動清除
from collections import deque
_chat_history: dict[int, deque] = {}   # {chat_id: deque([{role, content, ts}], maxlen=10)}
_CHAT_HISTORY_MAX_PAIRS = 5            # 5 組 = 10 條 (user+assistant)
_CHAT_HISTORY_EXPIRE_SEC = 600         # 10 分鐘


def _get_history(chat_id: int) -> list[dict]:
    """取得未過期嘅對話歷史，格式為 Claude messages array。"""
    if chat_id not in _chat_history:
        return []
    now = datetime.now(timezone.utc).timestamp()
    hist = _chat_history[chat_id]
    # 清除過期（以最後一條計）
    if hist and (now - hist[-1]["ts"]) > _CHAT_HISTORY_EXPIRE_SEC:
        hist.clear()
        return []
    return [{"role": m["role"], "content": m["content"]} for m in hist]


def _append_history(chat_id: int, user_msg: str, assistant_msg: str):
    """追加一組對話到短期記憶。"""
    if chat_id not in _chat_history:
        _chat_history[chat_id] = deque(maxlen=_CHAT_HISTORY_MAX_PAIRS * 2)
    now = datetime.now(timezone.utc).timestamp()
    _chat_history[chat_id].append({"role": "user", "content": user_msg, "ts": now})
    _chat_history[chat_id].append({"role": "assistant", "content": assistant_msg, "ts": now})


def _clear_history(chat_id: int):
    """清除指定 chat 嘅短期記憶。"""
    if chat_id in _chat_history:
        _chat_history[chat_id].clear()


def _save_pending():
    """Persist pending orders to disk."""
    serializable = {}
    for cid, p in pending_orders.items():
        serializable[str(cid)] = {
            "order":     p["order"],
            "expire_at": p["expire_at"].isoformat(),
            "msg_id":    p["msg_id"],
        }
    PENDING_FILE.write_text(json.dumps(serializable, ensure_ascii=False))


def _load_pending():
    """Restore unexpired pending orders on startup."""
    if not PENDING_FILE.exists():
        return
    try:
        data = json.loads(PENDING_FILE.read_text())
        now  = datetime.now(timezone.utc)
        for cid_str, p in data.items():
            expire = datetime.fromisoformat(p["expire_at"])
            if expire > now:
                pending_orders[int(cid_str)] = {
                    "order":     p["order"],
                    "expire_at": expire,
                    "msg_id":    p["msg_id"],
                }
        if pending_orders:
            log.info(f"恢復 {len(pending_orders)} 個待確認訂單")
    except Exception as e:
        log.warning(f"恢復 pending_orders 失敗: {e}")


# ════════════════════════════════════════════════════
# Security
# ════════════════════════════════════════════════════

def is_allowed(update: Update) -> bool:
    cid = update.effective_chat.id
    if cid != ALLOWED_CHAT_ID:
        log.warning(f"Rejected: chat_id={cid}")
        return False
    return True


# ════════════════════════════════════════════════════
# Claude API (via proxy, no SDK)
# ════════════════════════════════════════════════════

SYSTEM_PROMPT = """你係 OpenClaw 交易系統嘅 AI，跑喺本地 Mac。

語氣：
- 香港交易員口語廣東話，唔係書面中文
- 直接、簡短、有態度。唔囉嗦唔客套
- 交易術語照用（SL、TP、entry、breakeven、止蝕）

格式（Telegram 專用，最重要）：
- 絕對唔好用 Markdown：**、*、##、###、---、``` 全部禁止
- Telegram 會原封不動顯示呢啲符號，好核突
- 要強調用 <b>粗體</b>，其他 HTML tag 唔好用
- 唔好用 - 做 bullet，要分點就 1. 2. 3. 或直接換行
- 回覆 2-8 行。問數據答數據，唔使長篇解釋

風格：
✅ BTC 升咗 6%，volume 唔跟，唔好追
✅ XAG 84.5 阻力，SL 83.2 合理
✅ 冇倉，等 signal。市場靜
❌ 根據我的分析，目前市場狀況顯示...
❌ 我理解你的意思。讓我為你分析...
❌ **信號狀態：NO SIGNAL**（Markdown 符號）

對話記憶：
- 你可能收到之前嘅對話歷史（最近 5 組）
- 用戶 follow-up 時要接住上文，唔好當新對話
- 如果用戶指代「嗰個」「上面」「點解」，查返歷史

禁止：
- 用「您」「您好」「請問」「同意嗎？」
- 講「分析中」「思考中」「等我睇吓」，直接答
- 長篇大論、自我介紹、列出功能
- 任何 Markdown 語法"""


def _clean_for_telegram(text: str) -> str:
    """Convert any leftover Markdown to Telegram HTML. Safety net."""
    # ** bold ** → <b>bold</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # * italic * → just text
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # ## headers → just text
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # --- separators → empty
    text = re.sub(r'^-{3,}$', '', text, flags=re.MULTILINE)
    # ``` code blocks → keep content
    text = re.sub(r'```\w*\n?', '', text)
    # ` inline code ` → keep content
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # - bullet → • (Telegram friendly)
    text = re.sub(r'^- ', '• ', text, flags=re.MULTILINE)
    # Clean up triple+ newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


async def _send_html(target, text: str):
    """Send text as HTML, fallback to plain if HTML parse fails."""
    try:
        await target.reply_text(text, parse_mode="HTML")
    except Exception:
        clean = re.sub(r"<[^>]+>", "", text)
        await target.reply_text(clean)


def call_claude(user_msg: str, context: str, system: str = None,
                max_tokens: int = 1200,
                history: list[dict] | None = None) -> str:
    """Call Claude via proxy (Anthropic messages format).

    history: 可選嘅多輪對話 [{role, content}, ...]，會放喺當前訊息前面。
    """
    url = f"{PROXY_BASE_URL}/messages"
    # 組裝 messages：history + 當前用戶訊息（context 塞入第一條）
    messages = []
    if history:
        # 第一條 user message 加 context prefix
        for i, m in enumerate(history):
            messages.append({"role": m["role"], "content": m["content"]})
        # 當前用戶訊息（帶 context）
        messages.append({
            "role": "user",
            "content": f"{context}\n\n---\n\n用戶：{user_msg}",
        })
    else:
        messages = [{
            "role": "user",
            "content": f"{context}\n\n---\n\n用戶：{user_msg}",
        }]
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system or SYSTEM_PROMPT,
        "messages": messages,
    }).encode()

    req = urllib.request.Request(url, body, headers={
        "Content-Type":      "application/json",
        "Authorization":     f"Bearer {PROXY_API_KEY}",
        "anthropic-version": "2023-06-01",
    })

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        return data["content"][0]["text"]
    except Exception as e:
        log.error(f"Claude API failed: {e}")
        return f"Claude API 錯誤: {e}"


def read_local_context() -> str:
    """Read current system state. Tries API, falls back to file read."""
    try:
        return _read_context_via_api()
    except Exception:
        log.debug("API unavailable for context, falling back to file read")
        return _read_context_from_files()


def _read_context_via_api() -> str:
    """Build Claude context string from OpenClaw API."""
    parts = ["## 當前系統狀態"]
    state = _oc_client.get_state()

    # Trade state
    ts = state.get("trade_state", {})
    if ts:
        ts_lines = [f"- {k}: {v}" for k, v in ts.items()]
        parts.append(f"**交易狀態：**\n" + "\n".join(ts_lines))

    # Signal
    sig = state.get("signal", {})
    if sig:
        sig_lines = [f"- {k}: {v}" for k, v in sig.items()]
        parts.append(f"**信號：**\n" + "\n".join(sig_lines))

    # Scan log
    scan_lines = _oc_client.get_scan_log()
    if scan_lines:
        parts.append(f"**最新掃描：**\n" + "\n".join(scan_lines[-5:]))

    # Key params
    param_lines = [
        f'ACTIVE_PROFILE = "{state.get("active_profile", "CONSERVATIVE")}"',
        f'TRADING_ENABLED = {state.get("trading_enabled", True)}',
    ]
    config = _oc_client.get_config()
    for k in ("RISK_PER_TRADE_PCT", "MAX_OPEN_POSITIONS", "MAX_POSITION_SIZE_USDT"):
        if k in config:
            param_lines.append(f"{k} = {config[k]}")
    parts.append(f"**參數：**\n" + "\n".join(param_lines))

    return "\n\n".join(parts)


def _read_context_from_files() -> str:
    """Original file-based context read (fallback)."""
    parts = ["## 當前系統狀態"]

    files = {
        "交易狀態": BASE_DIR / "shared/TRADE_STATE.md",
        "信號":     BASE_DIR / "shared/SIGNAL.md",
    }
    for label, path in files.items():
        if path.exists():
            text = path.read_text(encoding="utf-8")[-1500:]
            parts.append(f"**{label}：**\n{text}")

    scan_log = BASE_DIR / "shared/SCAN_LOG.md"
    if scan_log.exists():
        text = scan_log.read_text(encoding="utf-8")[-600:]
        parts.append(f"**最新掃描：**\n{text}")

    params_path = BASE_DIR / "config/params.py"
    if params_path.exists():
        ptext = params_path.read_text()
        key_lines = [
            l.strip() for l in ptext.splitlines()
            if any(k in l for k in [
                "ACTIVE_PROFILE", "RISK_PER_TRADE",
                "MAX_POSITION", "TRADING_ENABLED",
            ]) and not l.strip().startswith("#")
        ]
        if key_lines:
            parts.append(f"**參數：**\n" + "\n".join(key_lines[:10]))

    return "\n\n".join(parts)


# ════════════════════════════════════════════════════
# Exchange helpers (multi-exchange)
# ════════════════════════════════════════════════════

# Canonical exchange names — user input 統一用呢個 map
EXCHANGE_ALIASES = {
    "aster": "aster", "ast": "aster",
    "binance": "binance", "bn": "binance", "bnb": "binance",
    "hl": "hyperliquid", "hyperliquid": "hyperliquid", "hyper": "hyperliquid",
}
EXCHANGE_DISPLAY = {"aster": "Aster", "binance": "Binance", "hyperliquid": "HyperLiquid"}

def _resolve_exchange(name: str) -> str:
    """Resolve alias to canonical exchange name. Returns 'aster' if unknown."""
    return EXCHANGE_ALIASES.get(name.lower().strip(), "aster")


def _get_client(exchange: str = "aster"):
    """Lazy-load exchange client by name. Raises if no credentials."""
    exchange = _resolve_exchange(exchange)
    if exchange == "binance":
        from trader_cycle.exchange.binance_client import BinanceClient
        return BinanceClient()
    elif exchange == "hyperliquid":
        from trader_cycle.exchange.hyperliquid_client import HyperLiquidClient
        return HyperLiquidClient()
    else:
        from trader_cycle.exchange.aster_client import AsterClient
        return AsterClient()


def _get_all_clients() -> dict:
    """Return dict of all available exchange clients. Skips unavailable ones."""
    clients = {}
    try:
        from trader_cycle.exchange.aster_client import AsterClient
        clients["aster"] = AsterClient()
    except Exception:
        pass
    try:
        from trader_cycle.exchange.binance_client import BinanceClient
        clients["binance"] = BinanceClient()
    except Exception:
        pass
    try:
        from trader_cycle.exchange.hyperliquid_client import HyperLiquidClient
        clients["hyperliquid"] = HyperLiquidClient()
    except Exception:
        pass
    return clients


def _sync_trade_state():
    """Sync TRADE_STATE.md with live exchange data after trades."""
    try:
        client = _get_client()
        balance = client.get_usdt_balance()
        positions = slash_cmd.get_positions()
        now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M")

        # Read existing state for fields we don't fetch from exchange
        state_path = BASE_DIR / "shared/TRADE_STATE.md"
        old_text = state_path.read_text() if state_path.exists() else ""
        def _old_val(key, default=""):
            m = re.search(rf'^{key}:\s*(.+)$', old_text, re.MULTILINE)
            return m.group(1).strip() if m else default

        # Find active position
        active = None
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                active = p
                break

        # Build position block
        if active:
            amt = float(active.get("positionAmt", 0))
            direction = "LONG" if amt > 0 else "SHORT"
            entry = active.get("entryPrice", "0")
            mark = active.get("markPrice", "0")
            lev = active.get("leverage", "10")
            margin_type = active.get("marginType", "isolated")
            margin = active.get("isolatedMargin", "0")
            liq = active.get("liquidationPrice", "0")
            pnl = active.get("unRealizedProfit", "0")

            # Try to get SL/TP from open orders
            sl_price = "—"
            tp_price = "—"
            try:
                orders = client.get_open_orders(active["symbol"])
                for o in orders:
                    otype = o.get("type", "")
                    if "STOP_MARKET" in otype:
                        sl_price = o.get("stopPrice", "—")
                    elif "TAKE_PROFIT" in otype:
                        tp_price = o.get("stopPrice", "—")
            except Exception:
                pass

            pos_block = f"""```
POSITION_OPEN: YES
PAIR: {active['symbol']}
DIRECTION: {direction}
ENTRY_PRICE: {entry}
MARK_PRICE: {mark}
SIZE: {abs(amt)}
LEVERAGE: {lev}
MARGIN_TYPE: {margin_type}
MARGIN: {margin}
LIQUIDATION: {liq}
UNREALIZED_PNL: {pnl}
SL_PRICE: {sl_price}
TP_PRICE: {tp_price}
```"""
        else:
            pos_block = "POSITION_OPEN: NO"

        md = f"""# TRADE_STATE.md — 當前交易狀態
# 版本: {datetime.now(HKT).strftime('%Y-%m-%d')}
# 寫入: tg_bot auto-sync

## 系統狀態

SYSTEM_STATUS: ACTIVE
LAST_UPDATED: {now}
DAILY_LOSS: {_old_val('DAILY_LOSS', '$0.00')}
DAILY_LOSS_LIMIT: {_old_val('DAILY_LOSS_LIMIT', '15%')}
CONSECUTIVE_LOSSES: {_old_val('CONSECUTIVE_LOSSES', '0')}
COOLDOWN_ACTIVE: {_old_val('COOLDOWN_ACTIVE', 'NO')}
COOLDOWN_ENDS: {_old_val('COOLDOWN_ENDS', '—')}

## 當前模式

MARKET_MODE: {_old_val('MARKET_MODE', 'TREND')}
MODE_CONFIRMED_CYCLES: {_old_val('MODE_CONFIRMED_CYCLES', '0')}

## 當前倉位

{pos_block}

## 帳戶資訊

BALANCE_USDT: {balance}
AVAILABLE_MARGIN: {balance}
LAST_BALANCE_CHECK: {now}
"""
        # Write to both paths
        p = BASE_DIR / "shared/TRADE_STATE.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md)

        log.info(f"TRADE_STATE synced: balance={balance}, pos={'YES' if active else 'NO'}")
    except Exception as e:
        log.error(f"TRADE_STATE sync failed: {e}")


def execute_order(order: dict) -> dict:
    """
    Execute an order via exchange client.
    order = {symbol, side, amount, sl_pct, tp_pct, exchange?}
    Routes to correct exchange based on order['exchange'].
    """
    try:
        exchange = order.get("exchange", "aster")
        client = _get_client(exchange)
        symbol = order["symbol"]
        side   = order["side"]

        if side == "CLOSE":
            result = client.close_position_market(symbol)
            _sync_trade_state()
            return {"ok": True, "result": str(result)}

        # Calculate qty from USDT amount
        balance  = client.get_usdt_balance()
        amount   = min(order.get("amount", 50), balance * 0.95)
        prec     = client.validate_symbol_precision(symbol)
        # Fetch current price for qty calc
        prices   = slash_cmd.get_prices()
        pair_data = prices.get(symbol, {})
        price    = pair_data.get("price", 0)
        if price <= 0:
            return {"ok": False, "error": f"無法取得 {symbol} 價格"}

        leverage = order.get("leverage", 10)
        # precision values are step sizes (0.001), convert to decimal places (3)
        import math
        def _step_to_dp(step):
            s = float(step)
            if s >= 1: return 0
            return max(0, -int(math.floor(math.log10(abs(s)))))
        qty_prec = _step_to_dp(prec.get("qty_precision", 0.001))
        px_prec  = _step_to_dp(prec.get("price_precision", 0.01))
        min_qty  = float(prec.get("min_qty", 0.001))
        qty      = round(amount * leverage / price, qty_prec)
        if qty < min_qty:
            qty = min_qty  # ensure minimum

        # Set margin + leverage
        try:
            client.set_margin_mode(symbol, "ISOLATED")
        except Exception:
            pass  # may already be set
        client.set_leverage(symbol, leverage)

        # Market entry
        entry_side = "BUY" if side == "LONG" else "SELL"
        result = client.create_market_order(symbol, entry_side, qty)

        # SL/TP — support absolute price OR percentage
        DEFAULT_SL_PCT = 0.025  # 2.5%
        DEFAULT_TP_PCT = 0.04   # 4%
        sl_side = "SELL" if side == "LONG" else "BUY"

        # Resolve SL price
        if "sl_price" in order and order["sl_price"]:
            sl_price = round(float(order["sl_price"]), px_prec)
            sl_pct = round(abs(price - sl_price) / price, 4)
        else:
            sl_pct = DEFAULT_SL_PCT
            if "sl_pct" in order:
                v = float(order["sl_pct"])
                if v > 1: v = v / 100
                if 0.005 <= v <= 0.05: sl_pct = v
            sl_price = price * (1 - sl_pct) if side == "LONG" else price * (1 + sl_pct)
            sl_price = round(sl_price, px_prec)

        # Resolve TP price
        if "tp_price" in order and order["tp_price"]:
            tp_price = round(float(order["tp_price"]), px_prec)
            tp_pct = round(abs(tp_price - price) / price, 4)
        else:
            tp_pct = DEFAULT_TP_PCT
            if "tp_pct" in order:
                v = float(order["tp_pct"])
                if v > 1: v = v / 100
                if 0.01 <= v <= 0.10: tp_pct = v
            tp_price = price * (1 + tp_pct) if side == "LONG" else price * (1 - tp_pct)
            tp_price = round(tp_price, px_prec)

        # Validate SL above liquidation
        liq_margin = 1.0 / leverage * 0.9
        liq_price = price * (1 - liq_margin) if side == "LONG" else price * (1 + liq_margin)
        if side == "LONG" and sl_price < liq_price:
            sl_price = round(liq_price * 1.02, px_prec)
            sl_pct = round(abs(price - sl_price) / price, 4)
            log.warning(f"SL adjusted above liquidation: ${sl_price}")
        elif side == "SHORT" and sl_price > liq_price:
            sl_price = round(liq_price * 0.98, px_prec)
            sl_pct = round(abs(sl_price - price) / price, 4)
            log.warning(f"SL adjusted below liquidation: ${sl_price}")

        try:
            client.create_stop_market(symbol, sl_side, qty, sl_price)
        except Exception as e:
            log.error(f"SL placement failed, emergency close: {e}")
            client.close_position_market(symbol)
            return {"ok": False, "error": f"SL 失敗，已緊急平倉: {e}"}
        try:
            client.create_take_profit_market(symbol, sl_side, qty, tp_price)
        except Exception:
            pass

        notional = qty * price
        return {
            "ok": True,
            "symbol": symbol, "side": side,
            "entry": price, "qty": qty, "notional": notional,
            "margin": amount, "leverage": leverage,
            "sl_price": sl_price, "sl_pct": sl_pct,
            "tp_price": tp_price, "tp_pct": tp_pct,
        }

    except Exception as e:
        log.error(f"下單失敗: {e}")
        return {"ok": False, "error": str(e)}


def move_sl_to_entry(symbol: str) -> dict:
    """Move SL to entry price (breakeven)."""
    try:
        client    = _get_client()
        positions = client.get_positions(symbol)
        pos = next((p for p in positions
                     if float(p.get("positionAmt", 0)) != 0), None)
        if not pos:
            return {"ok": False, "error": f"{symbol} 無持倉"}

        amt       = float(pos["positionAmt"])
        entry     = float(pos["entryPrice"])
        direction = "LONG" if amt > 0 else "SHORT"
        sl_side   = "SELL" if direction == "LONG" else "BUY"

        # Cancel existing SL
        orders = client.get_open_orders(symbol)
        for o in orders:
            if o.get("type") == "STOP_MARKET":
                client.cancel_order(symbol, str(o["orderId"]))

        # Place new SL at entry
        prec = client.validate_symbol_precision(symbol)
        sl_price = round(entry, prec.get("price_precision", 2))
        client.create_stop_market(symbol, sl_side, abs(amt), sl_price)
        return {"ok": True, "result": f"SL → ${sl_price}"}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ════════════════════════════════════════════════════
# Order intent parsing (Claude)
# ════════════════════════════════════════════════════

def parse_order_intent(text: str) -> dict | None:
    """Parse natural language for order intent. Returns order dict or None.
    Now detects exchange from text (aster/binance/hl/hyperliquid).
    """
    balance = slash_cmd.get_balance() or 0.0

    prompt = f"""判斷以下訊息是否包含下單/交易指令。

當前餘額：${balance:.2f}
可交易幣種：BTCUSDT, ETHUSDT, XRPUSDT, SOLUSDT, XAGUSDT, XAUUSDT
支持交易所：aster（預設）, binance, hyperliquid（hl）

訊息：「{text}」

如果係下單指令，只返回 JSON：
{{"is_order": true, "symbol": "ETHUSDT", "side": "LONG", "amount": 1.0, "leverage": 10, "exchange": "aster", "confidence": 0.95, "description": "做多ETH $1 10x"}}

交易所識別：
- 提及 "aster" → "exchange": "aster"
- 提及 "binance"/"bn" → "exchange": "binance"
- 提及 "hl"/"hyperliquid"/"hyper" → "exchange": "hyperliquid"
- 冇提及 → "exchange": "aster"（default）

槓桿規則：
- 用戶講「10倍」/「10x」/「lev 10」→ "leverage": 10
- 冇提及 → 唔好加 leverage 欄位（用預設 10x）

SL/TP 規則（最重要）：
- 用戶冇講 → 唔好加任何 SL/TP 欄位
- 用戶講百分比（如「止損2%」）→ 加 "sl_pct": 0.02
- 用戶講實際價格（如「SL 2089」）→ 加 "sl_price": 2089
- TP 同理：「止盈5%」→ "tp_pct": 0.05，「TP 2169」→ "tp_price": 2169
- 只加用戶明確講嘅，唔好自己估

如果唔係下單，返回：{{"is_order": false}}

只返回 JSON，唔要其他文字。"""

    try:
        raw = call_claude(prompt, "", max_tokens=300)
        raw = re.sub(r"```json|```", "", raw).strip()
        data = json.loads(raw)
        if data.get("is_order") and data.get("confidence", 0) > 0.8:
            # Normalize exchange name
            if "exchange" in data:
                data["exchange"] = _resolve_exchange(data["exchange"])
            else:
                data["exchange"] = "aster"
            return data
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════
# Deterministic Commands (zero LLM cost via slash_cmd)
# ════════════════════════════════════════════════════

async def _send_deterministic(update, cmd_func):
    """Run a deterministic slash_cmd function, send result."""
    if not is_allowed(update):
        return
    try:
        result = cmd_func()
        await update.message.reply_text(
            f"<pre>{result}</pre>", parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "🦞 <b>OpenClaw v2.0</b>\n\n"
        "<b>查詢</b>\n"
        "/report — 倉位報告\n"
        "/pos — 持倉\n"
        "/bal — 餘額\n"
        "/pnl — 盈虧\n"
        "/stats — 策略表現\n"
        "/scan — 掃描\n"
        "/log — 記錄\n"
        "/health — 系統狀態\n\n"
        "<b>控制</b>\n"
        "/mode — 切換模式\n"
        "/sl breakeven — 止損移至開倉價\n"
        "/pause — 暫停\n"
        "/resume — 恢復\n"
        "/cancel — 取消待確認訂單\n\n"
        "<b>下單</b>\n"
        "/trade &lt;交易所&gt; &lt;幣種&gt; &lt;方向&gt; [金額]\n"
        "/close [交易所] &lt;幣種&gt;\n"
        "直接輸入：「喺 hl 做多 BTC 50蚊」\n\n"
        "<b>AI</b>\n"
        "/ask [問題] — 帶數據分析\n"
        "自由輸入 — 自動判斷意圖\n"
        "/forget — 清除短期記憶\n\n"
        "<i>對話記憶：最近 5 組對話，10 分鐘無活動自動清除</i>\n\n"
        "<b>Aster DEX 新用戶註冊</b>\n"
        '<a href="https://www.asterdex.com/en/referral/E5Ce6c">點擊註冊</a> | 邀請碼：<code>E5Ce6c</code>',
        parse_mode="HTML",
    )


async def cmd_forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """清除短期對話記憶。"""
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    count = len(_chat_history.get(chat_id, [])) // 2
    _clear_history(chat_id)
    await update.message.reply_text(
        f"已清除 {count} 組對話記憶" if count else "冇記憶要清",
    )


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_report)

async def cmd_pos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show positions from ALL connected exchanges."""
    if not is_allowed(update):
        return
    try:
        clients = _get_all_clients()
        lines = [f"📊 <b>所有持倉</b>", "━━━━━━━━━━━━━━━━"]
        total_count = 0

        for name, client in clients.items():
            display = EXCHANGE_DISPLAY.get(name, name)
            try:
                positions = client.get_positions()
                active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
                if active:
                    lines.append(f"\n<b>{display}</b>")
                    for p in active:
                        amt = float(p.get("positionAmt", 0))
                        sym = p.get("symbol", "?")
                        direction = "LONG" if amt > 0 else "SHORT"
                        entry = float(p.get("entryPrice", 0))
                        mark = float(p.get("markPrice", 0))
                        pnl = float(p.get("unRealizedProfit", 0))
                        pnl_pct = ((mark - entry) / entry * 100) if entry > 0 else 0
                        if direction == "SHORT":
                            pnl_pct = -pnl_pct
                        icon = "🟢" if pnl >= 0 else "🔴"
                        lines.append(
                            f"  {icon} {sym} {direction} {abs(amt)}\n"
                            f"     ${entry:.4f} → ${mark:.4f} | {'+' if pnl >= 0 else ''}${pnl:.2f} ({pnl_pct:+.1f}%)"
                        )
                        total_count += 1
                else:
                    lines.append(f"\n<b>{display}</b>\n  無持倉")
            except Exception as e:
                lines.append(f"\n<b>{display}</b>\n  ⚠️ 連接失敗: {e}")

        lines.append(f"\n━━━━━━━━━━━━━━━━\nTotal: {total_count} positions")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def cmd_bal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show balance from ALL connected exchanges."""
    if not is_allowed(update):
        return
    try:
        clients = _get_all_clients()
        lines = ["💰 <b>餘額總覽</b>", "━━━━━━━━━━━━━━━━"]
        total = 0.0

        for name, client in clients.items():
            display = EXCHANGE_DISPLAY.get(name, name)
            try:
                bal = client.get_usdt_balance()
                total += bal
                lines.append(f"  {display:12s} ${bal:.2f}")
            except Exception as e:
                lines.append(f"  {display:12s} ⚠️ {e}")

        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"  <b>Total</b>        <b>${total:.2f}</b>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_pnl)

async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_log)

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_new)

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_stats)


# ── Enhanced /health with agent timestamps + memory count ──

async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    try:
        # Base health from slash_cmd (gateway, telegram, aster, balance)
        base = slash_cmd.cmd_health()
    except Exception:
        base = "基礎健康檢查失敗"

    lines = [base, "", "── AGENT 活躍度 ──"]

    try:
        health = _oc_client.get_health()
        # Agent timestamps from API
        name_map = {"main": "🧠 主腦", "heartbeat": "💓 心跳", "signal": "📡 信號"}
        for key, label in name_map.items():
            ts_info = health.get("timestamps", {}).get(key, {})
            if ts_info.get("status") == "missing":
                lines.append(f"❓ {label}：無記錄")
            else:
                mins = ts_info.get("age_min", -1)
                icon = "✅" if mins < 10 else ("⚠️" if mins < 30 else "❌")
                lines.append(f"{icon} {label}：{mins}分鐘前")

        # Scanner from API
        scanner = health.get("scanner", {})
        s_status = scanner.get("status", "missing")
        s_age = scanner.get("age_min", -1)
        s_detail = scanner.get("detail", "")
        if s_status == "missing":
            lines.append("❓ 👁 掃描器：無心跳文件")
        elif s_age > 6:
            lines.append(f"⚠️ 👁 掃描器：可能 hang ({s_age}分鐘無更新)")
        elif s_status == "error":
            lines.append(f"❌ 👁 掃描器：{s_detail[:50]}")
        else:
            lines.append(f"✅ 👁 掃描器：{s_detail[:50]}")

        # Memory count from API
        mem_count = health.get("memory_count", 0)
        if mem_count > 0:
            lines.append(f"🧠 記憶庫：{mem_count} 條")

    except Exception:
        log.debug("API unavailable for health, falling back to file read")
        # Fallback: direct file reads
        agent_files = {
            "🧠 主腦": BASE_DIR / "agents/main/sessions/sessions.json",
            "💓 心跳": BASE_DIR / "logs/heartbeat.log",
            "📡 信號": BASE_DIR / "shared/SIGNAL.md",
        }
        for name, path in agent_files.items():
            if path.exists():
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                mins = int((datetime.now() - mtime).total_seconds() / 60)
                icon = "✅" if mins < 10 else ("⚠️" if mins < 30 else "❌")
                lines.append(f"{icon} {name}：{mins}分鐘前")
            else:
                lines.append(f"❓ {name}：無記錄")

        hb_path = BASE_DIR / "logs/scanner_heartbeat.txt"
        if hb_path.exists():
            hb = hb_path.read_text().strip()
            parts = hb.split(" ", 2)
            status = parts[1] if len(parts) > 1 else "unknown"
            detail = parts[2] if len(parts) > 2 else ""
            try:
                ts = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
                age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
                if age_min > 6:
                    s_icon, s_note = "⚠️", f"可能 hang ({age_min:.0f}分鐘無更新)"
                elif status == "error":
                    s_icon, s_note = "❌", detail[:50]
                else:
                    s_icon, s_note = "✅", detail[:50]
            except Exception:
                s_icon, s_note = "❓", hb[:50]
            lines.append(f"{s_icon} 👁 掃描器：{s_note}")
        else:
            lines.append("❓ 👁 掃描器：無心跳文件")

        import numpy as np
        emb_path = BASE_DIR / "memory/index/embeddings.npy"
        if emb_path.exists():
            embs = np.load(str(emb_path))
            lines.append(f"🧠 記憶庫：{embs.shape[0]} 條")

    await update.message.reply_text(
        f"<pre>{'chr(10)'.join(lines)}</pre>".replace("chr(10)", "\n"),
        parse_mode="HTML",
    )



# ── Enhanced /mode with inline keyboard ──

VALID_MODES = ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")

async def cmd_mode_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    args = ctx.args

    if not args:
        # Show current + selection buttons
        current = _get_current_mode()

        mode_labels = {"CONSERVATIVE": "🛡 保守", "BALANCED": "⚖️ 平衡", "AGGRESSIVE": "🔥 進取"}
        btns = [[InlineKeyboardButton(mode_labels.get(m, m), callback_data=f"mode_{m}")]
                for m in VALID_MODES]
        await update.message.reply_text(
            f"⚙️ 當前模式：<b>{current}</b>\n選擇新模式：",
            reply_markup=InlineKeyboardMarkup(btns),
            parse_mode="HTML",
        )
        return

    mode = args[0].upper()
    if mode not in VALID_MODES:
        await update.message.reply_text(f"❌ 無效。可選：{' / '.join(VALID_MODES)}")
        return

    old_mode = _get_current_mode()

    if _apply_mode(mode):
        await update.message.reply_text(f"✅ 已切換至 <b>{mode}</b>", parse_mode="HTML")
        write_conversation(f"切換模式 {mode}", f"已切換至 {mode}")
        write_activity("mode_change", f"切換至 {mode}", {"from": old_mode, "to": mode})
    else:
        await update.message.reply_text("⚠️ 無法切換模式（需要 OpenClaw 環境）", parse_mode="HTML")


def _get_current_mode() -> str:
    """Get current ACTIVE_PROFILE. Tries API, falls back to file read."""
    try:
        config = _oc_client.get_config()
        return config.get("ACTIVE_PROFILE", "未知")
    except Exception:
        pp = BASE_DIR / "config/params.py"
        if pp.exists():
            om = re.search(r'ACTIVE_PROFILE\s*=\s*["\'](\w+)["\']', pp.read_text())
            if om:
                return om.group(1)
        return "未知"


def _apply_mode(mode: str) -> bool:
    """Switch ACTIVE_PROFILE. Returns True if actually applied."""
    try:
        _oc_client.set_mode(mode)
        return True
    except Exception:
        log.debug("API unavailable for set_mode, falling back to file write")
        params_path = BASE_DIR / "config/params.py"
        if params_path.exists():
            text = params_path.read_text()
            new_text = re.sub(
                r'ACTIVE_PROFILE\s*=\s*["\'].*?["\']',
                f'ACTIVE_PROFILE = "{mode}"',
                text,
            )
            params_path.write_text(new_text)
            return True
    return False


# ── Enhanced /sl with breakeven support ──

async def cmd_sl_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    args = ctx.args

    if not args:
        # Default: show SL/TP via slash_cmd
        await _send_deterministic(update, slash_cmd.cmd_sl)
        return

    action = args[0].lower()
    symbol = args[1].upper() if len(args) > 1 else None

    if action == "breakeven":
        positions = slash_cmd.get_positions()
        active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        if not active:
            await update.message.reply_text("📦 無持倉")
            return

        targets = active
        if symbol:
            targets = [p for p in active if p.get("symbol") == symbol]
            if not targets:
                await update.message.reply_text(f"❌ 找不到 {symbol} 持倉")
                return

        results = []
        for p in targets:
            sym = p.get("symbol", "?")
            r = move_sl_to_entry(sym)
            if r["ok"]:
                results.append(f"✅ {sym} {r['result']}")
            else:
                results.append(f"❌ {sym} {r['error']}")

        reply = "\n".join(results)
        await update.message.reply_text(reply)
        write_conversation(f"止損移至開倉價 {symbol or '全部'}", reply)
    else:
        await update.message.reply_text(
            "止損指令：\n"
            "/sl — 查看當前止損\n"
            "/sl breakeven — 所有倉位移至開倉價\n"
            "/sl breakeven XAGUSDT — 指定幣種",
        )


# ── /cancel ──

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Explicitly cancel pending order."""
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in pending_orders:
        pending_orders.pop(chat_id)
        _save_pending()
        await update.message.reply_text("❌ 待確認訂單已取消")
    else:
        await update.message.reply_text("✅ 無待確認訂單")


# ── /trade — 明確語法多交易所下單 ──

async def cmd_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /trade <exchange> <symbol> <side> [amount]
    例：/trade aster BTC LONG 50
        /trade hl ETH SHORT 30
        /trade binance SOL LONG
        /trade hl BTC CLOSE
    """
    if not is_allowed(update):
        return
    args = ctx.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "用法：/trade &lt;exchange&gt; &lt;symbol&gt; &lt;side&gt; [amount]\n\n"
            "例子：\n"
            "  /trade aster BTC LONG 50\n"
            "  /trade hl ETH SHORT 30\n"
            "  /trade binance SOL LONG\n"
            "  /trade hl BTC CLOSE",
            parse_mode="HTML",
        )
        return

    exchange_raw = args[0].lower()
    exchange = _resolve_exchange(exchange_raw)
    symbol_raw = args[1].upper()
    side = args[2].upper()
    amount = float(args[3]) if len(args) > 3 else 50.0

    # Auto-append USDT if needed
    symbol = symbol_raw if symbol_raw.endswith("USDT") else symbol_raw + "USDT"

    if side not in ("LONG", "SHORT", "CLOSE"):
        await update.message.reply_text(f"❌ 無效方向：{side}（要 LONG/SHORT/CLOSE）")
        return

    if exchange_raw not in EXCHANGE_ALIASES:
        await update.message.reply_text(
            f"❌ 未知交易所：{exchange_raw}\n"
            f"支持：aster, binance, hl (hyperliquid)"
        )
        return

    display = EXCHANGE_DISPLAY.get(exchange, exchange)
    order = {
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "exchange": exchange,
    }
    await _request_order_confirmation(update, order)


# ── /close — 多交易所平倉 ──

async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /close [exchange] <symbol>
    例：/close hl BTC   ← 平 HL 嘅 BTC
        /close BTC      ← 平 aster 嘅 BTC
    """
    if not is_allowed(update):
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "用法：/close [exchange] &lt;symbol&gt;\n"
            "例：/close hl BTC",
            parse_mode="HTML",
        )
        return

    # Parse: if first arg is a known exchange, use it; else default aster
    if len(args) >= 2 and args[0].lower() in EXCHANGE_ALIASES:
        exchange = _resolve_exchange(args[0])
        symbol_raw = args[1].upper()
    else:
        exchange = "aster"
        symbol_raw = args[0].upper()

    symbol = symbol_raw if symbol_raw.endswith("USDT") else symbol_raw + "USDT"
    display = EXCHANGE_DISPLAY.get(exchange, exchange)

    order = {
        "symbol": symbol,
        "side": "CLOSE",
        "amount": 0,
        "exchange": exchange,
    }
    await _request_order_confirmation(update, order)


# ── /pause /resume ──

async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    if _set_trading_enabled(False):
        await update.message.reply_text("⏸ <b>交易已暫停</b>", parse_mode="HTML")
        write_conversation("暫停交易", "已暫停")
    else:
        await update.message.reply_text("⚠️ 無法暫停（需要 OpenClaw 環境）", parse_mode="HTML")


async def cmd_resume_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    if _set_trading_enabled(True):
        await update.message.reply_text("▶️ <b>交易已恢復</b>", parse_mode="HTML")
        write_conversation("恢復交易", "已恢復")
    else:
        await update.message.reply_text("⚠️ 無法恢復（需要 OpenClaw 環境）", parse_mode="HTML")


def _set_trading_enabled(enabled: bool) -> bool:
    """Toggle TRADING_ENABLED. Returns True if actually applied."""
    try:
        _oc_client.set_trading(enabled)
        return True
    except Exception:
        log.debug("API unavailable for set_trading, falling back to file write")
        params_path = BASE_DIR / "config/params.py"
        if params_path.exists():
            text = params_path.read_text()
            new_text = re.sub(r'TRADING_ENABLED\s*=\s*\w+',
                              f'TRADING_ENABLED = {enabled}', text)
            if 'TRADING_ENABLED' not in text:
                new_text += f'\nTRADING_ENABLED = {enabled}\n'
            params_path.write_text(new_text)
            return True
    return False


# ════════════════════════════════════════════════════
# AI: /ask + free text with order detection
# ════════════════════════════════════════════════════

async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    question = " ".join(ctx.args) if ctx.args else ""
    if not question:
        await update.message.reply_text("用法：/ask 你的問題")
        return
    await _handle_analysis(update, question)


async def _safe_retrieve(query: str, top_k: int = 6) -> list:
    """RAG search in executor (voyage-3 API won't block event loop)."""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: retrieve_full(query, top_k=top_k)
        )
    except Exception as e:
        log.warning(f"RAG 搜尋失敗: {e}")
        return []


async def _handle_analysis(update: Update, text: str):
    """RAG + local state + Claude analysis + 短期對話記憶（最近 5 組）。"""
    await update.message.reply_text("...")
    chat_id = update.effective_chat.id

    memories = await _safe_retrieve(text, top_k=6)
    mem_text = format_for_prompt(memories, max_chars=2000)
    local_text = read_local_context()

    context = ""
    if mem_text:
        context += mem_text + "\n\n"
    context += local_text

    # 取短期對話歷史（最近 5 組，10 分鐘過期）
    history = _get_history(chat_id)

    reply = _clean_for_telegram(call_claude(text, context, history=history))

    await _send_html(update.message, reply)

    # 寫入短期記憶 + 長期記憶
    _append_history(chat_id, text, reply)
    write_conversation(text, reply)

    analysis_kw = ["分析", "策略", "建議", "點睇", "應唔應該", "如果", "compare"]
    if any(kw in text for kw in analysis_kw):
        write_analysis(text, reply)


async def handle_free_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Free text: detect order intent first, else RAG analysis."""
    if not is_allowed(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return

    # Check for order-like keywords first (avoid Claude call for obvious non-orders)
    order_kw = ["買", "賣", "做多", "做空", "long", "short", "平倉", "close",
                "入場", "開倉", "buy", "sell", "all in"]
    if any(kw in text.lower() for kw in order_kw):
        await update.message.reply_text("...")
        order = parse_order_intent(text)
        if order:
            await _request_order_confirmation(update, order)
            return

    # Not an order → RAG analysis
    await _handle_analysis(update, text)


# ════════════════════════════════════════════════════
# Order confirmation flow
# ════════════════════════════════════════════════════

async def _request_order_confirmation(update: Update, order: dict):
    """Show order confirmation with inline buttons."""
    chat_id = update.effective_chat.id
    symbol  = order.get("symbol", "?")
    side    = order.get("side", "?")
    amount  = order.get("amount", 0)
    exchange = order.get("exchange", "aster")
    exchange_display = EXCHANGE_DISPLAY.get(exchange, exchange)
    leverage = 10

    # Resolve SL/TP same logic as execute_order
    DEFAULT_SL_PCT = 0.025
    DEFAULT_TP_PCT = 0.04

    # Get current price for SL/TP preview
    try:
        prices = slash_cmd.get_prices()
        price = prices.get(symbol, {}).get("price", 0)
    except Exception:
        price = 0

    balance      = slash_cmd.get_balance() or 0.0
    is_high_risk = (amount >= balance * 0.8) or (side == "CLOSE")
    risk_icon    = "🔴" if is_high_risk else "🟡"

    timeout_sec = 90 if is_high_risk else 60
    expire_at   = datetime.now(timezone.utc) + timedelta(seconds=timeout_sec)

    risk_html = "\n⚠️ <b>高風險操作</b>" if is_high_risk else ""

    # Calculate preview values
    if price > 0 and side != "CLOSE":
        # SL
        if "sl_price" in order and order["sl_price"]:
            sl_price = float(order["sl_price"])
            sl_pct = abs(price - sl_price) / price
        else:
            sl_pct = DEFAULT_SL_PCT
            if "sl_pct" in order:
                v = float(order["sl_pct"])
                if v > 1: v = v / 100
                if 0.005 <= v <= 0.05: sl_pct = v
            sl_price = price * (1 - sl_pct) if side == "LONG" else price * (1 + sl_pct)
        # TP
        if "tp_price" in order and order["tp_price"]:
            tp_price = float(order["tp_price"])
            tp_pct = abs(tp_price - price) / price
        else:
            tp_pct = DEFAULT_TP_PCT
            if "tp_pct" in order:
                v = float(order["tp_pct"])
                if v > 1: v = v / 100
                if 0.01 <= v <= 0.10: tp_pct = v
            tp_price = price * (1 + tp_pct) if side == "LONG" else price * (1 - tp_pct)
        notional = amount * leverage
        detail = (
            f"交易所：{exchange_display}\n"
            f"幣種：{symbol}\n"
            f"方向：{side}\n"
            f"金額：${amount:.2f} | 槓桿：{leverage}x | 逐倉\n"
            f"名義：~${notional:.1f}\n"
            f"SL：${sl_price:.4f} ({sl_pct*100:.1f}%)\n"
            f"TP：${tp_price:.4f} ({tp_pct*100:.1f}%)\n"
            f"現價：${price:.4f}"
        )
    else:
        detail = (
            f"交易所：{exchange_display}\n"
            f"幣種：{symbol}\n"
            f"方向：{side}\n"
            f"金額：${amount:.2f}"
        )

    msg_text = (
        f"{risk_icon} <b>確認下單？</b>{risk_html}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{detail}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⏱ {timeout_sec}秒內確認，否則自動取消"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 確認下單", callback_data="order_confirm"),
        InlineKeyboardButton("❌ 取消",     callback_data="order_cancel"),
    ]])

    msg = await update.message.reply_text(
        msg_text, reply_markup=keyboard, parse_mode="HTML",
    )

    pending_orders[chat_id] = {
        "order":     order,
        "expire_at": expire_at,
        "msg_id":    msg.message_id,
    }
    _save_pending()


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks (order confirm/cancel + mode switch)."""
    query   = update.callback_query
    chat_id = query.message.chat_id
    data    = query.data

    await query.answer()

    # ── Mode switch buttons ──
    if data.startswith("mode_"):
        mode = data.replace("mode_", "")
        if mode in VALID_MODES:
            old_mode = _get_current_mode()
            if _apply_mode(mode):
                mode_labels = {"CONSERVATIVE": "🛡 保守", "BALANCED": "⚖️ 平衡", "AGGRESSIVE": "🔥 進取"}
                await query.edit_message_text(
                    f"✅ 已切換至 <b>{mode_labels.get(mode, mode)}</b>",
                    parse_mode="HTML",
                )
                write_conversation(f"切換模式 {mode}", f"已切換至 {mode}")
                write_activity("mode_change", f"切換至 {mode}", {"from": old_mode, "to": mode})
            else:
                await query.edit_message_text("⚠️ 無法切換模式（需要 OpenClaw 環境）", parse_mode="HTML")
        return

    # ── Order buttons ──
    pending = pending_orders.get(chat_id)

    if not pending or datetime.now(timezone.utc) > pending["expire_at"]:
        await query.edit_message_text("⏱ 已過期，訂單取消")
        pending_orders.pop(chat_id, None)
        _save_pending()
        return

    if data == "order_cancel":
        await query.edit_message_text("❌ 訂單已取消")
        pending_orders.pop(chat_id, None)
        _save_pending()
        return

    if data == "order_confirm":
        order = pending["order"]
        await query.edit_message_text("⏳ 下單中...")
        pending_orders.pop(chat_id, None)
        _save_pending()

        result = execute_order(order)

        if result["ok"]:
            r = result
            side_icon = "🟢" if r["side"] == "LONG" else "🔴"
            ex_display = EXCHANGE_DISPLAY.get(order.get("exchange", "aster"), "Aster")
            reply = (
                f"✅ <b>下單成功</b> ({ex_display})\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{side_icon} <b>{r['symbol']} {r['side']}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"入場    ${r['entry']:.4f}\n"
                f"數量    {r['qty']} ({r['symbol'].replace('USDT','')})\n"
                f"名義    ${r['notional']:.2f}\n"
                f"保證金  ${r['margin']:.2f}\n"
                f"槓桿    {r['leverage']}x 逐倉\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🔴 SL  ${r['sl_price']:.4f}  ({r['sl_pct']*100:.1f}%)\n"
                f"🟢 TP  ${r['tp_price']:.4f}  ({r['tp_pct']*100:.1f}%)\n"
                f"━━━━━━━━━━━━━━━━"
            )
        else:
            reply = (
                f"❌ <b>下單失敗</b>\n"
                f"原因：{result.get('error', '未知')}"
            )

        await ctx.bot.send_message(chat_id, reply, parse_mode="HTML")

        write_trade(
            symbol=result.get("symbol", order.get("symbol", "?")),
            side=result.get("side", order.get("side", "?")),
            entry=result.get("entry", order.get("amount", 0)),
            notes=f"Telegram 下單 {'成功' if result['ok'] else '失敗'}",
        )

        # Sync TRADE_STATE.md with live exchange data
        if result["ok"]:
            _sync_trade_state()


# ════════════════════════════════════════════════════
# Auto push alerts (position close + agent health)
# ════════════════════════════════════════════════════

async def check_and_push_alerts(app):
    """Background task: monitor for position closes + agent stalls.

    Fix: None sentinel prevents false close reports on bot restart.
    Fix: stall_warned flag prevents repeated warnings.
    """
    last_positions = None   # None = not yet initialized (NOT empty set)
    stall_warned   = False

    while True:
        try:
            await asyncio.sleep(60)

            # Get current positions
            try:
                positions = slash_cmd.get_positions()
                current = {p["symbol"] for p in positions
                           if float(p.get("positionAmt", 0)) != 0}
            except Exception:
                continue

            # First run: snapshot only, no comparison
            if last_positions is None:
                last_positions = current
                log.info(f"初始持倉快照: {current or '無持倉'}")
                continue

            # Detect closed positions
            closed = last_positions - current
            if closed:
                # 讀 TRADE_STATE（_sync 之前），攞 entry/sl/direction
                _ts_path = BASE_DIR / "shared/TRADE_STATE.md"
                _ts_text = _ts_path.read_text() if _ts_path.exists() else ""
                def _ts_val(key):
                    m = re.search(rf'{key}:\s*([\d.]+)', _ts_text)
                    return float(m.group(1)) if m else 0.0
                def _ts_str(key):
                    m = re.search(rf'{key}:\s*(\S+)', _ts_text)
                    return m.group(1) if m else ""

                for symbol in closed:
                    # ── 1. 收集交易數據 ──
                    ts_entry = _ts_val("ENTRY_PRICE")
                    ts_sl = _ts_val("SL_PRICE")
                    ts_tp = _ts_val("TP_PRICE")
                    ts_dir = _ts_str("DIRECTION") or "LONG"
                    ts_size = _ts_val("SIZE")
                    ts_lev = _ts_str("LEVERAGE") or "?"

                    # Fallback: TRADE_STATE often stale (cleared by _sync)
                    # → read real entry from trades.jsonl
                    if ts_entry <= 0:
                        last_entry = read_last_entry(symbol)
                        if last_entry:
                            ts_entry = last_entry["entry"]
                            ts_dir = last_entry.get("side") or ts_dir
                            ts_sl = last_entry.get("sl_price") or ts_sl
                            log.info(
                                f"[{symbol}] TRADE_STATE stale → "
                                f"trades.jsonl entry=${ts_entry}"
                            )

                    real_pnl = 0.0
                    exit_price = 0.0
                    try:
                        client = _get_client()
                        income = client.get_income(income_type="REALIZED_PNL", limit=10)
                        for inc in income:
                            if inc.get("symbol") == symbol:
                                real_pnl = float(inc.get("income", 0))
                                break
                        if ts_entry > 0 and ts_size > 0 and real_pnl != 0:
                            if ts_dir == "LONG":
                                exit_price = ts_entry + real_pnl / ts_size
                            else:
                                exit_price = ts_entry - real_pnl / ts_size
                    except Exception as e:
                        log.warning(f"get_income for {symbol} failed: {e}")

                    # R-Multiple
                    r_mult = None
                    if ts_sl > 0 and ts_entry > 0 and exit_price > 0:
                        risk_dist = abs(ts_entry - ts_sl)
                        if risk_dist > 0:
                            if ts_dir == "LONG":
                                r_mult = (exit_price - ts_entry) / risk_dist
                            else:
                                r_mult = (ts_entry - exit_price) / risk_dist

                    # ── 2. 數據摘要（發送 + 餵畀 AI）──
                    pnl_emoji = "💰" if real_pnl >= 0 else "💸"
                    prefix = symbol.replace("USDT", "")
                    summary_lines = [
                        f"<b>{pnl_emoji} {prefix} 平倉報告</b>",
                        "",
                        f"方向: <b>{ts_dir}</b> | 槓桿: {ts_lev}x",
                        f"入場: ${ts_entry:.4f}" if ts_entry else "入場: —",
                        f"出場: ${exit_price:.4f}" if exit_price else "出場: —",
                        f"止損: ${ts_sl:.4f}" if ts_sl else "止損: —",
                        f"PnL: <b>${real_pnl:+.2f}</b>",
                    ]
                    if r_mult is not None:
                        summary_lines.append(f"R-Multiple: <b>{r_mult:+.1f}R</b>")
                    trade_data_text = "\n".join(summary_lines)

                    # ── 3. AI 教練分析（阿叔）──
                    try:
                        memories = retrieve_full(f"{symbol} 交易 平倉", top_k=4)
                        mem_text = format_for_prompt(memories, max_chars=1000)
                    except Exception:
                        mem_text = ""
                    local = read_local_context()
                    context = (mem_text + "\n\n" + local) if mem_text else local

                    coach_prompt = (
                        f"你係「阿叔」— 做咗 20 年嘅老交易員。經歷過數次大牛市同熊市，"
                        f"玩過短炒、玩過孖展，贏過 20 倍、輸過 20 倍。心態淡然，"
                        f"數字對你嚟講只係一場遊戲。你相信贏家嘅標準差公式：贏一單冚得返晒就夠。\n\n"
                        f"風格：廣東話口語、直接唔兜圈、讚就讚鬧就鬧、重過程多過結果。\n\n"
                        f"以下係剛平倉嘅交易數據：\n"
                        f"幣種: {symbol} | 方向: {ts_dir} | 槓桿: {ts_lev}x\n"
                        f"入場: ${ts_entry:.4f} | 出場: ${exit_price:.4f}\n"
                        f"止損: ${ts_sl:.4f} | 止盈: ${ts_tp:.4f}\n"
                        f"PnL: ${real_pnl:+.2f}"
                        f"{f' | R-Multiple: {r_mult:+.1f}R' if r_mult is not None else ''}\n\n"
                        f"請用以下結構回覆（每段 1-2 句，全部加埋唔好超過 15 行）：\n"
                        f"1. 【入場】signal 同市場環境啱唔啱\n"
                        f"2. 【出場】SL/TP 位置合唔合理\n"
                        f"3. 【評分】呢筆交易過程幾分（A/B/C/D），唔理賺蝕\n"
                        f"4. 【情緒】有冇 FOMO、報復性交易、過度自信嘅跡象\n"
                        f"5. 【教訓】一句最值得記住嘅嘢\n"
                        f"6. 【下一步】繼續定休息？\n"
                        f"最尾問一條問題迫交易員反思。"
                    )

                    report = call_claude(coach_prompt, context)
                    report = _clean_for_telegram(report)
                    write_analysis(f"{symbol} 平倉報告", report)

                    # ── 4. Stats dashboard ──
                    stats_text = ""
                    try:
                        from trader_cycle.analysis.metrics import calculate_metrics, format_stats_text
                        stats_text = format_stats_text(calculate_metrics())
                    except Exception:
                        pass

                    # ── 5. 合併成一個 message 發送 ──
                    full_msg = trade_data_text + "\n\n" + report
                    if stats_text:
                        full_msg += "\n\n━━━━━━━━━━━━━━━━\n" + stats_text

                    try:
                        await app.bot.send_message(
                            ALLOWED_CHAT_ID, full_msg, parse_mode="HTML",
                        )
                    except Exception:
                        clean = re.sub(r"<[^>]+>", "", full_msg)
                        await app.bot.send_message(
                            ALLOWED_CHAT_ID, f"{clean}",
                        )

                    # ── 6. 寫入真實交易數據 ──
                    try:
                        write_trade(
                            symbol, ts_dir, ts_entry,
                            exit_price=round(exit_price, 4) if exit_price else None,
                            pnl=round(real_pnl, 4) if real_pnl else None,
                            sl_price=ts_sl if ts_sl > 0 else None,
                            notes="exchange close detected by tg_bot",
                        )
                    except Exception:
                        log.warning(f"write_trade for {symbol} close failed")

                # Sync TRADE_STATE after position close
                _sync_trade_state()

            last_positions = current

            # Agent health: warn if SCANNER stalls > 10min
            # Uses SCAN_LOG.md mtime (not scanner_heartbeat.txt) to match original semantics
            try:
                health = _oc_client.get_health()
                mins = health.get("scan_log_age_min", -1)
            except Exception:
                # Fallback to direct file check
                scan_log = BASE_DIR / "shared/SCAN_LOG.md"
                if scan_log.exists():
                    mtime = datetime.fromtimestamp(scan_log.stat().st_mtime)
                    mins = int((datetime.now() - mtime).total_seconds() / 60)
                else:
                    mins = -1

            if mins >= 0:
                if mins > 10 and not stall_warned:
                    await app.bot.send_message(
                        ALLOWED_CHAT_ID,
                        f"⚠️ 掃描器已 {mins} 分鐘無更新！請檢查 lightscan。",
                    )
                    stall_warned = True
                elif mins <= 10:
                    stall_warned = False  # reset after recovery

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Alert check error: {e}")


# ════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════

def main():
    if not TG_TOKEN:
        print("❌ 缺少 TELEGRAM_BOT_TOKEN")
        sys.exit(1)
    if not ALLOWED_CHAT_ID:
        print("❌ 缺少 TELEGRAM_CHAT_ID")
        sys.exit(1)

    log.info("🦞 OpenClaw Telegram v2.0 啟動")
    log.info(f"  Chat ID: {ALLOWED_CHAT_ID}")
    log.info(f"  Claude: {CLAUDE_MODEL} via {PROXY_BASE_URL}")
    log.info(f"  Memory: {BASE_DIR / 'memory'}")

    _load_pending()

    app = Application.builder().token(TG_TOKEN).build()

    # Deterministic commands (zero AI cost)
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("report",  cmd_report))
    app.add_handler(CommandHandler("pos",     cmd_pos))
    app.add_handler(CommandHandler("bal",     cmd_bal))
    app.add_handler(CommandHandler("pnl",     cmd_pnl))
    app.add_handler(CommandHandler("log",     cmd_log))
    app.add_handler(CommandHandler("scan",    cmd_scan))
    app.add_handler(CommandHandler("stats",   cmd_stats))
    app.add_handler(CommandHandler("health",  cmd_health))

    # Enhanced commands
    app.add_handler(CommandHandler("mode",    cmd_mode_handler))
    app.add_handler(CommandHandler("sl",      cmd_sl_handler))
    app.add_handler(CommandHandler("pause",   cmd_pause))
    app.add_handler(CommandHandler("resume",  cmd_resume_handler))
    app.add_handler(CommandHandler("cancel",  cmd_cancel))
    app.add_handler(CommandHandler("trade",   cmd_trade))
    app.add_handler(CommandHandler("close",   cmd_close))

    # AI-powered
    app.add_handler(CommandHandler("ask",     cmd_ask))
    app.add_handler(CommandHandler("forget",  cmd_forget))

    # Inline buttons (order confirm/cancel + mode switch)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Free text → order detection or RAG analysis
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_free_text,
    ))

    # Auto push alerts (background task)
    async def post_init(application):
        asyncio.create_task(check_and_push_alerts(application))

    app.post_init = post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
