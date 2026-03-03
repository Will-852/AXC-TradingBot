"""
telegram.py — Template-based Telegram 報告（繁體中文）
Wraps light_scan.send_telegram() for sending
"""

from __future__ import annotations
import os
import sys

# Import send_telegram from light_scan.py
_tools_dir = os.path.join(
    os.environ.get("OPENCLAW_WORKSPACE", "/Users/wai/.openclaw/workspace"),
    "tools"
)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from light_scan import send_telegram as _send

from ..core.context import CycleContext, Signal


def send_telegram(text: str) -> dict:
    """Send Telegram message (HTML format)."""
    return _send(text)


def format_cycle_report(ctx: CycleContext) -> str:
    """
    Format the main cycle report for Telegram.
    繁體中文 + HTML 格式
    """
    ts = ctx.timestamp_str
    mode = ctx.market_mode
    dry = " | DRY_RUN" if ctx.dry_run else ""

    # Header
    lines = [f"<b>📊 Cycle #{ctx.cycle_id} | {ts}{dry}</b>"]
    lines.append(f"模式: <b>{mode}</b> ({'已確認' if ctx.mode_confirmed else '待確認'})")
    lines.append(f"分析模式: {ctx.mode}")
    lines.append("")

    # Prices
    lines.append("<b>📈 即時價格</b>")
    for sym, snap in sorted(ctx.market_data.items()):
        prefix = sym.replace("USDT", "")
        if snap.price > 100:
            price_str = f"${snap.price:,.1f}"
        elif snap.price > 1:
            price_str = f"${snap.price:.4f}"
        else:
            price_str = f"${snap.price:.6f}"
        lines.append(f"  {prefix}: {price_str} ({snap.price_change_24h_pct:+.1f}%)")
    lines.append("")

    # Mode votes
    if ctx.mode_votes:
        lines.append("<b>🗳️ 模式投票</b>")
        vote_parts = []
        for k, v in ctx.mode_votes.items():
            emoji = "📈" if v == "TREND" else "📊" if v == "RANGE" else "❓"
            vote_parts.append(f"{k}:{emoji}{v}")
        lines.append("  " + " | ".join(vote_parts))
        lines.append("")

    # Key indicators (BTC primary)
    btc_ind = ctx.indicators.get("BTCUSDT", {}).get("4h", {})
    if btc_ind:
        lines.append("<b>📉 BTC 4H 指標</b>")
        rsi = btc_ind.get("rsi")
        adx = btc_ind.get("adx")
        macd_h = btc_ind.get("macd_hist")
        atr = btc_ind.get("atr")
        bb_w = btc_ind.get("bb_width")
        parts = []
        if rsi is not None:
            parts.append(f"RSI:{rsi:.1f}")
        if adx is not None:
            parts.append(f"ADX:{adx:.1f}")
        if macd_h is not None:
            parts.append(f"MACD_H:{macd_h:.2f}")
        if atr is not None:
            parts.append(f"ATR:{atr:.1f}")
        if bb_w is not None:
            parts.append(f"BB_W:{bb_w:.4f}")
        if parts:
            lines.append("  " + " | ".join(parts))
        lines.append("")

    # Signals
    if ctx.signals:
        lines.append("<b>🚨 信號</b>")
        for sig in ctx.signals:
            lines.append(
                f"  {sig.pair} {sig.direction} ({sig.strategy}/{sig.strength})"
            )
            lines.append(f"  原因: {', '.join(sig.reasons)}")
        lines.append("")
    else:
        lines.append("信號: 無")
        lines.append("")

    # Risk status
    if ctx.risk_blocked:
        lines.append(f"⛔ 風控阻止: {', '.join(ctx.risk_reasons)}")
    if ctx.no_trade_reasons:
        lines.append(f"⚠️ No-Trade: {', '.join(ctx.no_trade_reasons)}")

    # Errors/warnings
    if ctx.errors:
        lines.append(f"❌ 錯誤: {len(ctx.errors)}")
    if ctx.warnings:
        lines.append(f"⚠️ 警告: {len(ctx.warnings)}")

    return "\n".join(lines)


def format_signal_alert(signal: Signal, dry_run: bool = True) -> str:
    """Format a signal alert for Telegram."""
    dry = " [DRY_RUN]" if dry_run else ""
    return (
        f"<b>🚨 交易信號{dry}</b>\n"
        f"交易對: <b>{signal.pair}</b>\n"
        f"方向: <b>{signal.direction}</b>\n"
        f"策略: {signal.strategy} ({signal.strength})\n"
        f"入場: ${signal.entry_price:.4f}\n"
        f"止損: ${signal.sl_price:.4f}\n"
        f"止盈: ${signal.tp1_price:.4f}\n"
        f"原因: {', '.join(signal.reasons)}"
    )


def format_order_confirmation(ctx: CycleContext) -> str:
    """Format live order confirmation for Telegram."""
    signal = ctx.selected_signal
    result = ctx.order_result
    if not signal or not result:
        return ""

    status = "成功" if result.success else "失敗"
    emoji = "✅" if result.success else "❌"
    lines = [
        f"<b>{emoji} 落盤確認 — {status}</b>",
        f"交易對: <b>{result.symbol}</b>",
        f"方向: <b>{signal.direction}</b> ({result.side})",
        f"數量: {result.quantity}",
        f"成交價: ${result.price:.4f}",
        f"訂單號: {result.order_id}",
        "",
        f"止損: ${signal.sl_price:.4f} ({ctx.sl_order_id or 'N/A'})",
        f"止盈: ${signal.tp1_price:.4f} ({ctx.tp_order_id or 'N/A'})",
        f"槓桿: {signal.leverage}x | 保證金: ${signal.margin_required:.2f}",
    ]

    if result.error:
        lines.append(f"\n⚠️ {result.error}")

    return "\n".join(lines)


def format_position_close(pair: str, direction: str, pnl: float, reason: str) -> str:
    """Format position close notification."""
    emoji = "💰" if pnl >= 0 else "💸"
    return (
        f"<b>{emoji} 平倉通知</b>\n"
        f"交易對: <b>{pair}</b>\n"
        f"方向: {direction}\n"
        f"PnL: ${pnl:+.2f}\n"
        f"原因: {reason}"
    )


def format_urgent_alert(title: str, body: str) -> str:
    """Format an urgent alert."""
    return f"<b>🚨 URGENT: {title}</b>\n{body}"


class SendReportsStep:
    """Step 16: Send Telegram reports."""
    name = "send_reports"

    def run(self, ctx: CycleContext) -> CycleContext:
        # Check silent mode
        silent_mode = ctx.scan_config.get("SILENT_MODE", "OFF")

        # Always send: errors, signals, trades
        if ctx.errors:
            for err in ctx.errors:
                msg = format_urgent_alert("Trader Cycle Error", err[:500])
                result = send_telegram(msg)
                if ctx.verbose:
                    print(f"    Telegram URGENT: {'sent' if 'error' not in result else result}")

        # Live order confirmation (Phase 3)
        if ctx.order_result and not ctx.dry_run:
            msg = format_order_confirmation(ctx)
            if msg:
                result = send_telegram(msg)
                ctx.telegram_messages.append(msg)
                if ctx.verbose:
                    print(f"    Telegram order confirmation: sent")

        if ctx.selected_signal:
            msg = format_signal_alert(ctx.selected_signal, ctx.dry_run)
            result = send_telegram(msg)
            ctx.telegram_messages.append(msg)

        # Routine report (skip in silent mode)
        if silent_mode == "ON" and not ctx.signals and not ctx.errors:
            if ctx.verbose:
                print("    Telegram: skipped (silent mode)")
            return ctx

        # Send cycle report
        report = format_cycle_report(ctx)
        result = send_telegram(report)
        ctx.telegram_messages.append(report)

        if ctx.verbose:
            status = "sent" if "error" not in result else result.get("error", "unknown")
            print(f"    Telegram report: {status}")

        return ctx
