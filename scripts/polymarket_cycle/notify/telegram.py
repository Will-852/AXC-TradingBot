"""
telegram.py — Polymarket-specific Telegram report templates

Uses shared_infra.telegram.send_telegram() for actual delivery.
HTML parse_mode, 廣東話口語。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def format_cycle_report(ctx: Any) -> str:
    """Full cycle report — sent every cycle with signals or errors."""
    dry = " DRY" if ctx.dry_run else ""
    ts = ctx.timestamp_str

    lines = [f"<b>🔮 Poly Cycle{dry} | {ts}</b>"]
    lines.append(
        f"💰 ${ctx.usdc_balance:,.0f} | "
        f"曝險 {ctx.exposure_pct:.0%} | "
        f"持倉 {len(ctx.open_positions)}"
    )

    if ctx.risk_blocked:
        lines.append(f"⛔ 風控攔截: {'; '.join(ctx.risk_reasons[:3])}")

    # Market scan summary
    lines.append(
        f"📡 掃描 {len(ctx.scanned_markets)} → "
        f"過濾 {len(ctx.filtered_markets)} → "
        f"AI評估 {len(ctx.edge_assessments)}"
    )

    # Signals
    if ctx.signals:
        lines.append("")
        lines.append("<b>📊 信號</b>")
        for s in ctx.signals:
            lines.append(
                f"  {s.side} {s.title[:35]} "
                f"edge:{s.edge:+.1%} conf:{s.confidence:.2f} "
                f"${s.bet_size_usdc:.0f}"
            )

    # Executed trades
    if ctx.executed_trades:
        lines.append("")
        lines.append(f"<b>✅ 落盤 {len(ctx.executed_trades)}</b>")
        for t in ctx.executed_trades:
            dry_tag = " [模擬]" if t.get("dry_run") else ""
            lines.append(
                f"  {t['side']} {t['title'][:30]}{dry_tag} "
                f"${t['amount']:.0f} @{t['price']:.3f}"
            )

    # Exit signals
    if hasattr(ctx, 'exit_signals') and ctx.exit_signals:
        lines.append("")
        lines.append("<b>⚠️ 退出觸發</b>")
        for sig in ctx.exit_signals:
            lines.append(
                f"  {sig.action.upper()} {sig.position.title[:30]} "
                f"({sig.urgency}): {'; '.join(sig.reasons[:2])}"
            )

    # Positions
    if ctx.open_positions:
        lines.append("")
        lines.append("<b>📋 持倉</b>")
        for p in ctx.open_positions:
            pnl_emoji = "🟢" if p.unrealized_pnl >= 0 else "🔴"
            lines.append(
                f"  {pnl_emoji} {p.side} {p.title[:30]} "
                f"{p.unrealized_pnl_pct:+.1%} (${p.cost_basis:.0f})"
            )

    # Errors
    if ctx.errors:
        lines.append(f"\n🚨 錯誤: {len(ctx.errors)}")
        for e in ctx.errors[:3]:
            lines.append(f"  {e[:80]}")

    if ctx.warnings:
        lines.append(f"⚠️ 警告: {len(ctx.warnings)}")

    return "\n".join(lines)


def format_mini_report(ctx: Any) -> str:
    """One-liner for quiet cycles (no signals, no errors)."""
    dry = " DRY" if ctx.dry_run else ""
    return (
        f"🔮 Poly{dry} | "
        f"${ctx.usdc_balance:,.0f} | "
        f"掃描{len(ctx.scanned_markets)} | "
        f"持倉{len(ctx.open_positions)} | "
        f"曝險{ctx.exposure_pct:.0%}"
    )


def format_trade_alert(signal: Any, dry_run: bool = True) -> str:
    """Alert for a new trade execution."""
    dry = " [模擬]" if dry_run else ""
    return (
        f"<b>🔮 新交易{dry}</b>\n"
        f"{signal.side} {signal.title[:40]}\n"
        f"邊際: {signal.edge:+.1%} | 信心: {signal.confidence:.2f}\n"
        f"落注: ${signal.bet_size_usdc:.0f} @{signal.price:.3f}\n"
        f"Kelly: {signal.kelly_fraction:.3f}"
    )


def format_exit_alert(exit_signal: Any) -> str:
    """Alert for position exit trigger."""
    pos = exit_signal.position
    return (
        f"<b>⚠️ 退出觸發</b>\n"
        f"{exit_signal.action.upper()} {pos.title[:40]}\n"
        f"原因: {'; '.join(exit_signal.reasons)}\n"
        f"緊急: {exit_signal.urgency} | PnL: {pos.unrealized_pnl_pct:+.1%}"
    )


def send_poly_report(ctx: Any, no_telegram: bool = False) -> None:
    """Send appropriate Telegram report based on cycle outcome.

    Decides what to send:
    - Errors → full report (always)
    - Signals/trades → full report
    - Exit triggers → full report
    - Quiet cycle → mini report (or skip)
    """
    if no_telegram:
        return

    try:
        from shared_infra.telegram import send_telegram
    except ImportError:
        logger.debug("Telegram module not available")
        return

    # Always send on errors, signals, or exit triggers
    has_content = (
        ctx.errors
        or ctx.signals
        or ctx.executed_trades
        or (hasattr(ctx, 'exit_signals') and ctx.exit_signals)
    )

    if has_content:
        report = format_cycle_report(ctx)
    else:
        # Quiet cycle — send mini report
        report = format_mini_report(ctx)

    try:
        send_telegram(report)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)

    # Send queued messages
    for msg in ctx.telegram_messages:
        try:
            send_telegram(msg)
        except Exception:
            pass
