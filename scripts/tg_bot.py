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
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Load .env ────────────────────────────────────
ENV_PATH = Path.home() / ".openclaw/secrets/.env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Paths ─────────────────────────────────────────
BASE_DIR    = Path.home() / ".openclaw"
SCRIPTS_DIR = BASE_DIR / "scripts"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from memory.writer import write_conversation, write_analysis, write_trade
from memory.retriever import retrieve_full, format_for_prompt

import slash_cmd

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

SYSTEM_PROMPT = """你係 OpenClaw 智能交易助手，跑喺用戶本地 Mac。

你可以存取本地交易狀態、掃描記錄、歷史記憶。
用廣東話回覆，簡潔直接，數字具體。
如果係下單指令，用結構化格式解析。
報告用 emoji 標示重要資訊。
保持在 1000 字以內。"""


def call_claude(user_msg: str, context: str, system: str = None,
                max_tokens: int = 1200) -> str:
    """Call Claude via proxy (Anthropic messages format)."""
    url = f"{PROXY_BASE_URL}/messages"
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system or SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"{context}\n\n---\n\n用戶：{user_msg}",
        }],
    }).encode()

    req = urllib.request.Request(url, body, headers={
        "Content-Type":      "application/json",
        "x-api-key":         PROXY_API_KEY,
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
    """Read current system state files for Claude context."""
    parts = ["## 當前系統狀態"]

    files = {
        "交易狀態": BASE_DIR / "shared/TRADE_STATE.md",
        "信號":     BASE_DIR / "shared/SIGNAL.md",
    }
    for label, path in files.items():
        if path.exists():
            text = path.read_text(encoding="utf-8")[-1500:]
            parts.append(f"**{label}：**\n{text}")

    scan_log = BASE_DIR / "workspace/agents/aster_trader/logs/SCAN_LOG.md"
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
# Exchange helpers (via AsterClient)
# ════════════════════════════════════════════════════

def _get_client():
    """Lazy-load AsterClient."""
    from trader_cycle.exchange.aster_client import AsterClient
    return AsterClient()


def execute_order(order: dict) -> dict:
    """
    Execute an order via AsterClient.
    order = {symbol, side, amount, sl_pct, tp_pct}
    """
    try:
        client = _get_client()
        symbol = order["symbol"]
        side   = order["side"]

        if side == "CLOSE":
            result = client.close_position_market(symbol)
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

        leverage = 10  # default
        qty      = round(amount * leverage / price, prec.get("qty_precision", 3))

        # Set margin + leverage
        try:
            client.set_margin_mode(symbol, "ISOLATED")
        except Exception:
            pass  # may already be set
        client.set_leverage(symbol, leverage)

        # Market entry
        entry_side = "BUY" if side == "LONG" else "SELL"
        result = client.create_market_order(symbol, entry_side, qty)

        # SL
        sl_pct   = order.get("sl_pct", 0.02)
        sl_side  = "SELL" if side == "LONG" else "BUY"
        sl_price = price * (1 - sl_pct) if side == "LONG" else price * (1 + sl_pct)
        sl_price = round(sl_price, prec.get("price_precision", 2))
        try:
            client.create_stop_market(symbol, sl_side, qty, sl_price)
        except Exception as e:
            # SL failed → emergency close
            log.error(f"SL placement failed, emergency close: {e}")
            client.close_position_market(symbol)
            return {"ok": False, "error": f"SL 失敗，已緊急平倉: {e}"}

        # TP (optional, best-effort)
        tp_pct = order.get("tp_pct", 0.04)
        if tp_pct > 0:
            tp_price = price * (1 + tp_pct) if side == "LONG" else price * (1 - tp_pct)
            tp_price = round(tp_price, prec.get("price_precision", 2))
            try:
                client.create_take_profit_market(symbol, sl_side, qty, tp_price)
            except Exception:
                pass

        return {"ok": True, "result": f"入場 {symbol} {side} qty={qty} @~${price:.2f}"}

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
    """Parse natural language for order intent. Returns order dict or None."""
    balance = slash_cmd.get_balance() or 0.0

    prompt = f"""判斷以下訊息是否包含下單/交易指令。

當前餘額：${balance:.2f}
可交易幣種：BTCUSDT, ETHUSDT, XRPUSDT, XAGUSDT

訊息：「{text}」

如果係下單指令，只返回 JSON：
{{"is_order": true, "symbol": "XAGUSDT", "side": "LONG 或 SHORT 或 CLOSE", "amount": 50.0, "sl_pct": 0.02, "tp_pct": 0.04, "confidence": 0.95, "description": "買入XAG $50，止損2%"}}

如果唔係下單，返回：{{"is_order": false}}

只返回 JSON，唔要其他文字。"""

    try:
        raw = call_claude(prompt, "", max_tokens=300)
        raw = re.sub(r"```json|```", "", raw).strip()
        data = json.loads(raw)
        if data.get("is_order") and data.get("confidence", 0) > 0.8:
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
        "🦞 *OpenClaw v2.0*\n\n"
        "*查詢（免費）*\n"
        "/report — 完整倉位報告\n"
        "/pos — 當前持倉\n"
        "/bal — 餘額\n"
        "/pnl — 今日盈虧\n"
        "/scan — 最新掃描\n"
        "/log — 最近記錄\n"
        "/health — 系統狀態\n\n"
        "*控制*\n"
        "/mode — 切換模式\n"
        "/sl breakeven — 移止損至開倉價\n"
        "/pause — 暫停交易\n"
        "/resume — 恢復交易\n\n"
        "*下單*\n"
        "直接輸入：「買入 XAG 50蚊」\n"
        "或：「平倉 BTC」\n\n"
        "*AI 分析*\n"
        "/ask \\[問題\\] — 帶本地數據分析\n"
        "自由輸入 — 自動判斷意圖",
        parse_mode="Markdown",
    )


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_report)

async def cmd_pos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_pos)

async def cmd_bal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_bal)

async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_pnl)

async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_log)

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_deterministic(update, slash_cmd.cmd_new)


# ── Enhanced /health with agent timestamps + memory count ──

async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    try:
        # Base health from slash_cmd (gateway, telegram, aster, balance)
        base = slash_cmd.cmd_health()
    except Exception:
        base = "基礎健康檢查失敗"

    # Agent activity timestamps
    agents = {
        "🧠 主腦":    BASE_DIR / "agents/main/workspace/MEMORY.md",
        "👁 掃描器":  BASE_DIR / "workspace/agents/aster_trader/logs/SCAN_LOG.md",
        "💓 心跳":    BASE_DIR / "agents/heartbeat/workspace/MEMORY.md",
    }
    lines = [base, "", "── AGENT 活躍度 ──"]
    for name, path in agents.items():
        if path.exists():
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            mins  = int((datetime.now() - mtime).total_seconds() / 60)
            icon  = "✅" if mins < 10 else ("⚠️" if mins < 30 else "❌")
            lines.append(f"{icon} {name}：{mins}分鐘前")
        else:
            lines.append(f"❓ {name}：無記錄")

    # Memory count
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
        params_path = BASE_DIR / "config/params.py"
        current = "未知"
        if params_path.exists():
            m = re.search(r'ACTIVE_PROFILE\s*=\s*["\'](\w+)["\']',
                          params_path.read_text())
            if m:
                current = m.group(1)

        mode_labels = {"CONSERVATIVE": "🛡 保守", "BALANCED": "⚖️ 平衡", "AGGRESSIVE": "🔥 進取"}
        btns = [[InlineKeyboardButton(mode_labels.get(m, m), callback_data=f"mode_{m}")]
                for m in VALID_MODES]
        await update.message.reply_text(
            f"⚙️ 當前模式：*{current}*\n選擇新模式：",
            reply_markup=InlineKeyboardMarkup(btns),
            parse_mode="Markdown",
        )
        return

    mode = args[0].upper()
    if mode not in VALID_MODES:
        await update.message.reply_text(f"❌ 無效。可選：{' / '.join(VALID_MODES)}")
        return

    _apply_mode(mode)
    await update.message.reply_text(f"✅ 已切換至 *{mode}*", parse_mode="Markdown")
    write_conversation(f"切換模式 {mode}", f"已切換至 {mode}")


def _apply_mode(mode: str):
    params_path = BASE_DIR / "config/params.py"
    if params_path.exists():
        text = params_path.read_text()
        new_text = re.sub(
            r'ACTIVE_PROFILE\s*=\s*["\'].*?["\']',
            f'ACTIVE_PROFILE = "{mode}"',
            text,
        )
        params_path.write_text(new_text)


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
            "`/sl` — 查看當前止損\n"
            "`/sl breakeven` — 所有倉位移至開倉價\n"
            "`/sl breakeven XAGUSDT` — 指定幣種",
            parse_mode="Markdown",
        )


# ── /pause /resume ──

async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    _set_trading_enabled(False)
    await update.message.reply_text("⏸ *交易已暫停*", parse_mode="Markdown")
    write_conversation("暫停交易", "已暫停")


async def cmd_resume_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    _set_trading_enabled(True)
    await update.message.reply_text("▶️ *交易已恢復*", parse_mode="Markdown")
    write_conversation("恢復交易", "已恢復")


def _set_trading_enabled(enabled: bool):
    params_path = BASE_DIR / "config/params.py"
    if params_path.exists():
        text = params_path.read_text()
        new_text = re.sub(r'TRADING_ENABLED\s*=\s*\w+',
                          f'TRADING_ENABLED = {enabled}', text)
        if 'TRADING_ENABLED' not in text:
            new_text += f'\nTRADING_ENABLED = {enabled}\n'
        params_path.write_text(new_text)


# ════════════════════════════════════════════════════
# AI: /ask + free text with order detection
# ════════════════════════════════════════════════════

async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    question = " ".join(ctx.args) if ctx.args else ""
    if not question:
        await update.message.reply_text("用法：`/ask 你的問題`", parse_mode="Markdown")
        return
    await _handle_analysis(update, question)


async def _handle_analysis(update: Update, text: str):
    """RAG + local state + Claude analysis."""
    await update.message.reply_text("🤔 思考中...")

    memories = retrieve_full(text, top_k=6)
    mem_text = format_for_prompt(memories, max_chars=2000)
    local_text = read_local_context()

    context = ""
    if mem_text:
        context += mem_text + "\n\n"
    context += local_text

    reply = call_claude(text, context)

    try:
        await update.message.reply_text(reply, parse_mode="HTML")
    except Exception:
        try:
            await update.message.reply_text(reply, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(reply)

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
        await update.message.reply_text("🤔 分析中...")
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
    sl_pct  = order.get("sl_pct", 0.02) * 100
    tp_pct  = order.get("tp_pct", 0.04) * 100
    desc    = order.get("description", "")

    balance      = slash_cmd.get_balance() or 0.0
    is_high_risk = (amount >= balance * 0.8) or (side == "CLOSE")
    risk_icon    = "🔴" if is_high_risk else "🟡"
    risk_note    = "\n⚠️ *高風險操作*" if is_high_risk else ""

    timeout_sec = 90 if is_high_risk else 60
    expire_at   = datetime.now(timezone.utc) + timedelta(seconds=timeout_sec)

    msg_text = (
        f"{risk_icon} *確認下單？*{risk_note}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"幣種：`{symbol}`\n"
        f"方向：`{side}`\n"
        f"金額：`${amount:.2f}`\n"
        f"止損：`{sl_pct:.1f}%`\n"
        f"止盈：`{tp_pct:.1f}%`\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⏱ {timeout_sec}秒內確認，否則自動取消"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 確認下單", callback_data="order_confirm"),
        InlineKeyboardButton("❌ 取消",     callback_data="order_cancel"),
    ]])

    msg = await update.message.reply_text(
        msg_text, reply_markup=keyboard, parse_mode="Markdown",
    )

    pending_orders[chat_id] = {
        "order":     order,
        "expire_at": expire_at,
        "msg_id":    msg.message_id,
    }


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
            _apply_mode(mode)
            mode_labels = {"CONSERVATIVE": "🛡 保守", "BALANCED": "⚖️ 平衡", "AGGRESSIVE": "🔥 進取"}
            await query.edit_message_text(
                f"✅ 已切換至 *{mode_labels.get(mode, mode)}*",
                parse_mode="Markdown",
            )
            write_conversation(f"切換模式 {mode}", f"已切換至 {mode}")
        return

    # ── Order buttons ──
    pending = pending_orders.get(chat_id)

    if not pending or datetime.now(timezone.utc) > pending["expire_at"]:
        await query.edit_message_text("⏱ 已過期，訂單取消")
        pending_orders.pop(chat_id, None)
        return

    if data == "order_cancel":
        await query.edit_message_text("❌ 訂單已取消")
        pending_orders.pop(chat_id, None)
        return

    if data == "order_confirm":
        order = pending["order"]
        await query.edit_message_text("⏳ 下單中...")
        pending_orders.pop(chat_id, None)

        result = execute_order(order)

        if result["ok"]:
            reply = (
                f"✅ *下單成功*\n"
                f"━━━━━━━━━━\n"
                f"{order['symbol']} {order['side']}\n"
                f"金額：${order.get('amount', 0):.2f}\n"
                f"結果：{str(result.get('result', ''))[:200]}"
            )
        else:
            reply = (
                f"❌ *下單失敗*\n"
                f"原因：{result.get('error', '未知')}"
            )

        await ctx.bot.send_message(chat_id, reply, parse_mode="Markdown")

        write_trade(
            symbol=order.get("symbol", "?"),
            side=order.get("side", "?"),
            entry=order.get("amount", 0),
            notes=f"Telegram 下單 {'成功' if result['ok'] else '失敗'}",
        )


# ════════════════════════════════════════════════════
# Auto push alerts (position close + agent health)
# ════════════════════════════════════════════════════

async def check_and_push_alerts(app):
    """Background task: monitor for position closes + agent stalls."""
    last_positions = set()
    last_alert_time = {}

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

            # Detect closed positions
            closed = last_positions - current
            if closed and last_positions:  # skip first run
                for symbol in closed:
                    memories = retrieve_full(f"{symbol} 交易 平倉", top_k=4)
                    mem_text = format_for_prompt(memories, max_chars=1000)
                    local    = read_local_context()
                    context  = (mem_text + "\n\n" + local) if mem_text else local

                    report = call_claude(
                        f"{symbol} 剛剛平倉了。請生成交易報告：入場/出場分析、盈虧、下次建議",
                        context,
                    )

                    await app.bot.send_message(
                        ALLOWED_CHAT_ID,
                        f"📋 *{symbol} 平倉報告*\n\n{report}",
                        parse_mode="Markdown",
                    )
                    write_analysis(f"{symbol} 平倉報告", report)

            last_positions = current

            # Agent health: warn if main brain stalls > 15min (max once per hour)
            main_mem = BASE_DIR / "agents/main/workspace/MEMORY.md"
            if main_mem.exists():
                mtime = datetime.fromtimestamp(main_mem.stat().st_mtime)
                mins  = int((datetime.now() - mtime).total_seconds() / 60)
                now_ts = datetime.now(timezone.utc)
                last_main = last_alert_time.get("main_stall")

                if mins > 15 and (not last_main or
                        (now_ts - last_main).total_seconds() > 3600):
                    await app.bot.send_message(
                        ALLOWED_CHAT_ID,
                        f"⚠️ 主腦已 {mins} 分鐘無活動！請檢查系統。",
                    )
                    last_alert_time["main_stall"] = now_ts

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

    app = Application.builder().token(TG_TOKEN).build()

    # Deterministic commands (zero AI cost)
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("report",  cmd_report))
    app.add_handler(CommandHandler("pos",     cmd_pos))
    app.add_handler(CommandHandler("bal",     cmd_bal))
    app.add_handler(CommandHandler("pnl",     cmd_pnl))
    app.add_handler(CommandHandler("log",     cmd_log))
    app.add_handler(CommandHandler("scan",    cmd_scan))
    app.add_handler(CommandHandler("health",  cmd_health))

    # Enhanced commands
    app.add_handler(CommandHandler("mode",    cmd_mode_handler))
    app.add_handler(CommandHandler("sl",      cmd_sl_handler))
    app.add_handler(CommandHandler("pause",   cmd_pause))
    app.add_handler(CommandHandler("resume",  cmd_resume_handler))

    # AI-powered
    app.add_handler(CommandHandler("ask",     cmd_ask))

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
