"""
mm/risk_guards.py — Risk monitoring and kill switches for MM 15M bot.

Split from run_mm_live.py (2026-03-25).
🔴 2CHECK: These functions determine when to stop trading and cancel all orders.
Incorrect thresholds = bot doesn't stop when it should.
"""

import logging
from datetime import datetime, timedelta

from polymarket.mm.constants import _HKT

logger = logging.getLogger(__name__)


def get_rolling_wr(state: dict, window: int = 30) -> tuple[float, int]:
    """Rolling win rate over last N resolved markets that actually filled.

    Only counts markets with entry_cost > 0 (i.e., orders were filled).
    Unfilled markets (entry_cost=0, PnL=0) are excluded — no fill = no play.
    Returns (wr, count). If count < 5, returns (0.68, count) = assume baseline.
    """
    resolved = [m for m in state["markets"].values() if m["phase"] == "RESOLVED" and not m.get("paper")]
    filled = [m for m in resolved if m.get("entry_cost", 0) > 0 or m.get("realized_pnl", 0) != 0]
    recent = filled[-window:] if len(filled) > window else filled
    if len(recent) < 5:
        return 0.68, len(recent)
    wins = sum(1 for m in recent if m.get("realized_pnl", 0) > 0)
    return wins / len(recent), len(recent)


def get_risk_mode(state: dict, tg_alert_fn=None) -> str:
    """Determine risk mode based on rolling WR.

    🔴 Thresholds:
    NORMAL (WR >= 58%):  full dual-layer
    DEFENSIVE (30-58%):  shift budget toward hedge
    HEDGE_ONLY (28-30%): no directional, pure hedge
    STOPPED (<28%):      stop trading completely

    tg_alert_fn: callback for Telegram alerts (optional).
    """
    wr, count = get_rolling_wr(state, window=30)

    if count < 10:
        return "NORMAL"

    if wr < 0.28:
        logger.warning("RISK MODE: STOPPED — rolling WR %.1f%% (%d trades) < 28%%", wr*100, count)
        if tg_alert_fn:
            tg_alert_fn(f"🛑 MM STOPPED: WR {wr*100:.0f}% < 28% ({count} trades). Manual review needed.")
        return "STOPPED"
    elif wr < 0.30:
        logger.warning("RISK MODE: HEDGE_ONLY — rolling WR %.1f%% (%d trades) < 30%%",
                        wr*100, count)
        return "HEDGE_ONLY"
    elif wr < 0.58:
        logger.info("RISK MODE: DEFENSIVE — rolling WR %.1f%% (%d trades) < 58%%", wr*100, count)
        return "DEFENSIVE"
    else:
        return "NORMAL"


def check_hard_stop(state: dict, client, dry_run: bool,
                    tg_alert_fn=None) -> bool:
    """Check if total PnL has breached hard stop (-20% of HWM).

    🔴 DZ-6: Cancels ALL wallet orders (includes other bots).
    Returns True if bot should halt.
    """
    br = state.get("bankroll", 100.0)
    _total_pnl = state.get("total_pnl", 0.0)
    _hwm = state.get("high_water_mark", 0)

    # Auto-update HWM (deposit detection)
    if br > _hwm and br > 10:
        if _hwm > 0 and br > _hwm * 1.3:
            logger.info("DEPOSIT DETECTED: balance $%.2f >> HWM $%.2f → updating", br, _hwm)
        state["high_water_mark"] = br
        _hwm = br
    if _hwm <= 0:
        _hwm = br if br > 10 else 100.0

    if _total_pnl < -_hwm * 0.20:
        logger.critical("💀 HARD STOP: total PnL $%.2f = %.0f%% of HWM $%.0f. Manual restart required.",
                        _total_pnl, _total_pnl / _hwm * 100, _hwm)
        if tg_alert_fn:
            tg_alert_fn(f"💀 HARD STOP: PnL ${_total_pnl:.2f} ({_total_pnl/_hwm*100:.0f}% of ${_hwm:.0f}). Manual restart needed.")
        state["hard_stopped"] = True
        # Cancel all open orders before halting
        if client and hasattr(client, "get_orders") and not dry_run:
            try:
                _all_orders = client.get_orders()
                for _o in (_all_orders or []):
                    try:
                        client.client.cancel(order_id=_o.get("id", ""))
                    except Exception:
                        pass
                logger.info("HARD STOP: cancelled %d open orders", len(_all_orders or []))
            except Exception:
                pass
        return True

    if state.get("hard_stopped"):
        logger.warning("💀 HARD STOPPED. Clear 'hard_stopped' from state to resume.")
        return True

    return False


def check_daily_loss(state: dict, client, dry_run: bool,
                     is_heavy: bool, tg_alert_fn=None) -> tuple[bool, float]:
    """Check daily loss tiers and apply graduated response.

    🔴 Tiers:
    >$10 loss → sizing ×0.67
    >$20 loss → sizing ×0.33
    >$30 loss → 2h cooldown (cancel all orders)
    >$45 loss → halt until tomorrow (cancel all orders)

    Returns (should_halt, daily_budget_mult).
    should_halt=True means run_cycle should return immediately.
    """
    now = datetime.now(tz=_HKT)

    # Cooldown check (must be ABOVE daily loss tiers)
    cd = state.get("cooldown_until", "")
    if cd:
        try:
            if now < datetime.fromisoformat(cd):
                return True, 0.0  # still in cooldown
        except ValueError:
            pass
        state["consecutive_losses"] = 0
        state["cooldown_until"] = ""
        logger.info("COOLDOWN EXPIRED — resuming trading")

    _daily_loss = -state.get("daily_pnl", 0)  # positive = loss amount
    _daily_budget_mult = 1.0

    if _daily_loss > 45:
        logger.warning("DAILY KILL: loss $%.2f > $45. Halted until tomorrow.", _daily_loss)
        if tg_alert_fn:
            tg_alert_fn(f"🔴 DAILY KILL: loss ${_daily_loss:.2f} > $45. Halted until tomorrow.")
        if client and hasattr(client, "client") and not dry_run:
            try:
                _all = client.get_orders()
                for _o in (_all or []):
                    client.client.cancel(order_id=_o.get("id", ""))
                logger.info("DAILY KILL: cancelled %d orphan orders", len(_all or []))
            except Exception as e:
                logger.warning("DAILY KILL cancel failed: %s", e)
        return True, 0.0
    elif _daily_loss > 30:
        _cd_end = (now + timedelta(hours=2)).isoformat()
        if not state.get("cooldown_until"):
            state["cooldown_until"] = _cd_end
            logger.warning("DAILY COOLDOWN: loss $%.2f > $30. Pausing 2 hours.", _daily_loss)
            if tg_alert_fn:
                tg_alert_fn(f"🟡 DAILY COOLDOWN: loss ${_daily_loss:.2f} > $30. 2h pause.")
            if client and hasattr(client, "client") and not dry_run:
                try:
                    _all = client.get_orders()
                    for _o in (_all or []):
                        client.client.cancel(order_id=_o.get("id", ""))
                    logger.info("COOLDOWN: cancelled %d orphan orders", len(_all or []))
                except Exception as e:
                    logger.warning("COOLDOWN cancel failed: %s", e)
        return True, 0.0
    elif _daily_loss > 20:
        _daily_budget_mult = 0.33
        if is_heavy:
            logger.info("DAILY DEFENSIVE-2: loss $%.2f > $20, sizing ×0.33", _daily_loss)
    elif _daily_loss > 10:
        _daily_budget_mult = 0.67
        if is_heavy:
            logger.info("DAILY DEFENSIVE-1: loss $%.2f > $10, sizing ×0.67", _daily_loss)

    return False, _daily_budget_mult
