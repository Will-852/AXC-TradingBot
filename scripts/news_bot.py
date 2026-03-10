#!/usr/bin/env python3
"""
news_bot.py — @AXCnews_bot 獨立新聞 Telegram Bot

功能：
  /start   — 歡迎訊息
  /news    — 查詢當前新聞情緒
  /submit <文字> — 手動提交新聞畀 AI 分析
  自動推送：情緒方向變化時通知（1 小時 cooldown）

Token: TELEGRAM_NEWS_BOT_TOKEN（secrets/.env）
Chat ID: 同主 bot 共用 TELEGRAM_CHAT_ID
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Load .env ──
BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
ENV_PATH = BASE_DIR / "secrets" / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TG_TOKEN = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN", "")
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

# ── Paths ──
SHARED_DIR = BASE_DIR / "shared"
NEWS_SENTIMENT_PATH = SHARED_DIR / "news_sentiment.json"
NEWS_MANUAL_PATH = SHARED_DIR / "news_manual.json"

# ── Constants ──
MAX_SUBMIT_LEN = 500
MAX_MANUAL_ENTRIES = 20
PUSH_COOLDOWN_SEC = 3600  # 1 hour
POLL_INTERVAL_SEC = 300   # check sentiment every 5 min for auto-push
HKT = timezone(timedelta(hours=8))

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NEWS_BOT] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("news_bot")

# ── Telegram imports ──
try:
    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder, CommandHandler, ContextTypes,
        MessageHandler, filters,
    )
except ImportError:
    log.error("python-telegram-bot not installed")
    sys.exit(1)


# ═══════════════════════════════════════
# Auth
# ═══════════════════════════════════════
def is_allowed(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id == ALLOWED_CHAT_ID


# ═══════════════════════════════════════
# Helpers
# ═══════════════════════════════════════
def read_sentiment() -> dict | None:
    """Read news_sentiment.json, return dict or None."""
    if not NEWS_SENTIMENT_PATH.exists():
        return None
    try:
        return json.loads(NEWS_SENTIMENT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def format_sentiment(data: dict) -> str:
    """Format sentiment data as Telegram HTML message."""
    sent = data.get("overall_sentiment", "neutral")
    conf = round(data.get("confidence", 0) * 100)
    impact = data.get("overall_impact")
    icons = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "mixed": "🟡"}
    icon = icons.get(sent, "⚪")
    articles = data.get("articles_analyzed", 0)
    updated = data.get("updated_at", "")
    time_str = ""
    if updated:
        try:
            dt = datetime.fromisoformat(updated)
            hkt = dt.astimezone(HKT)
            time_str = hkt.strftime("%H:%M")
        except (ValueError, TypeError):
            pass

    # Per-symbol (backward compat: value may be string or dict)
    syms = data.get("sentiment_by_symbol", {})
    sym_parts = []
    for s, v in syms.items():
        short = s.replace("USDT", "")
        if isinstance(v, str):
            sym_parts.append(f"{short} {icons.get(v, '⚪')}")
        elif isinstance(v, dict):
            s_icon = icons.get(v.get("sentiment", ""), "⚪")
            s_impact = v.get("impact")
            imp_str = f" {s_impact}" if s_impact is not None else ""
            sym_parts.append(f"{short} {s_icon}{imp_str}")
        else:
            sym_parts.append(f"{short} ⚪")
    sym_line = " | ".join(sym_parts) if sym_parts else "—"

    # Narratives + risks
    narratives = data.get("key_narratives", [])
    narr_line = "、".join(narratives[:3]) if narratives else "—"
    risks = data.get("risk_events", [])
    risk_line = "、".join(risks[:3]) if risks else "無"
    summary = data.get("summary", "")

    stale = data.get("stale", False)
    stale_tag = " ⚠️過期" if stale else ""

    impact_str = f" | 影響力 {impact}/100" if impact is not None else ""

    msg = (
        f"📰 <b>新聞情緒分析</b>{stale_tag}\n"
        f"━━━━━━━━━━━━━━\n"
        f"整體：{icon} <b>{sent.capitalize()}</b> ({conf}%){impact_str}\n"
        f"分析：{articles} 篇 | {time_str} 更新\n\n"
        f"幣種：{sym_line}\n\n"
        f"敘事：{narr_line}\n"
        f"風險：{risk_line}\n"
    )
    if summary:
        msg += f"\n{summary}"
    return msg


def atomic_write_json(path: Path, data):
    """Atomic JSON write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ═══════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "📰 <b>AXC News Bot</b>\n\n"
        "/news — 查詢當前新聞情緒\n"
        "/submit &lt;文字&gt; — 手動提交新聞\n\n"
        "自動推送：情緒方向變化時會通知你。",
        parse_mode="HTML",
    )


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show current news sentiment."""
    if not is_allowed(update):
        return
    data = read_sentiment()
    if not data:
        await update.message.reply_text("暫無新聞數據。等 news agent 下次運行。")
        return
    await update.message.reply_text(format_sentiment(data), parse_mode="HTML")


async def cmd_submit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Submit manual news for next sentiment analysis."""
    if not is_allowed(update):
        return
    text = " ".join(ctx.args).strip() if ctx.args else ""
    if not text:
        await update.message.reply_text("用法：/submit BTC ETF 獲批准")
        return

    if len(text) > MAX_SUBMIT_LEN:
        text = text[:MAX_SUBMIT_LEN]
        await update.message.reply_text(f"⚠️ 內容已截斷至 {MAX_SUBMIT_LEN} 字")

    entry = {
        "text": text,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "source": "telegram",
    }

    # Read existing
    entries = []
    if NEWS_MANUAL_PATH.exists():
        try:
            raw = json.loads(NEWS_MANUAL_PATH.read_text(encoding="utf-8"))
            entries = raw.get("entries", [])
        except (json.JSONDecodeError, OSError):
            pass

    entries.append(entry)
    entries = entries[-MAX_MANUAL_ENTRIES:]

    atomic_write_json(NEWS_MANUAL_PATH, {"entries": entries})
    await update.message.reply_text(
        f"✅ 已提交新聞（{len(text)} 字）\n"
        f"下次 sentiment 分析時會納入。"
    )


async def handle_free_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Treat any free text as news submission."""
    if not is_allowed(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return

    if len(text) > MAX_SUBMIT_LEN:
        text = text[:MAX_SUBMIT_LEN]

    entry = {
        "text": text,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "source": "telegram",
    }

    entries = []
    if NEWS_MANUAL_PATH.exists():
        try:
            raw = json.loads(NEWS_MANUAL_PATH.read_text(encoding="utf-8"))
            entries = raw.get("entries", [])
        except (json.JSONDecodeError, OSError):
            pass

    entries.append(entry)
    entries = entries[-MAX_MANUAL_ENTRIES:]
    atomic_write_json(NEWS_MANUAL_PATH, {"entries": entries})

    await update.message.reply_text(f"📝 已收錄（{len(text)} 字）")


# ═══════════════════════════════════════
# Auto-push: sentiment change detection
# ═══════════════════════════════════════
_last_sentiment = None
_last_push_ts = 0


async def sentiment_watcher(app):
    """Background task: poll sentiment file, push on direction change."""
    global _last_sentiment, _last_push_ts
    log.info("Sentiment watcher started")

    # Initialize without pushing
    data = read_sentiment()
    if data:
        _last_sentiment = data.get("overall_sentiment")

    while True:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            data = read_sentiment()
            if not data:
                continue

            sent = data.get("overall_sentiment")
            conf = data.get("confidence", 0)
            now = asyncio.get_event_loop().time()

            if (
                _last_sentiment is not None
                and sent != _last_sentiment
                and conf >= 0.6
                and now - _last_push_ts > PUSH_COOLDOWN_SEC
            ):
                icons = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "mixed": "🟡"}
                msg = (
                    f"📰 <b>情緒轉變</b>\n"
                    f"{icons.get(_last_sentiment, '⚪')} {_last_sentiment} → "
                    f"{icons.get(sent, '⚪')} <b>{sent}</b>\n"
                    f"信心度：{round(conf * 100)}%"
                )
                try:
                    await app.bot.send_message(
                        chat_id=ALLOWED_CHAT_ID,
                        text=msg,
                        parse_mode="HTML",
                    )
                    _last_push_ts = now
                    log.info(f"Pushed sentiment change: {_last_sentiment} → {sent}")
                except Exception as e:
                    log.warning(f"Failed to push notification: {e}")

            _last_sentiment = sent

        except Exception as e:
            log.warning(f"Sentiment watcher error: {e}")


# ═══════════════════════════════════════
# Main
# ═══════════════════════════════════════
def main():
    if not TG_TOKEN:
        log.error("TELEGRAM_NEWS_BOT_TOKEN not set")
        sys.exit(1)
    if not ALLOWED_CHAT_ID:
        log.error("TELEGRAM_CHAT_ID not set")
        sys.exit(1)

    log.info(f"Starting @AXCnews_bot (chat_id={ALLOWED_CHAT_ID})")

    app = ApplicationBuilder().token(TG_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("submit", cmd_submit))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_free_text,
    ))

    # Start sentiment watcher as background task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run():
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            log.info("Bot polling started")

            # Run sentiment watcher alongside polling
            watcher = asyncio.create_task(sentiment_watcher(app))
            try:
                await asyncio.Event().wait()  # run forever
            finally:
                watcher.cancel()
                await app.updater.stop()
                await app.stop()

    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
