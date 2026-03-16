"""collectors.py — collect_data() aggregator (facade)."""

import copy
import logging
import os
import time
from datetime import datetime

from scripts.dashboard.constants import (
    HOME, HKT, PARAMS_DISPLAY, parse_md,
)
from scripts.dashboard.exchange_auth import _is_demo_mode, _get_demo_data
from scripts.dashboard.live_data import (
    get_all_exchange_data, get_live_balance, get_live_positions,
    get_live_trade_history, get_live_today_pnl, get_funding_rates,
    get_news_sentiment,
    _exchange_cache, _exchange_cache_lock,
)
from scripts.dashboard.analytics import (
    get_trade_history, get_trade_stats, get_risk_status,
    calc_drawdown, get_balance_baseline, update_pnl_history_verified,
    _enrich_trades,
)
from scripts.dashboard.scoring import _score_position, _get_macro_state
from scripts.dashboard.action_plan import get_action_plan
from scripts.dashboard.pending_sltp import (
    _pending_sltp, _check_pending_sltp, _extract_open_orders,
)
from scripts.dashboard.services import (
    get_agent_info, get_scan_log, get_file_tree, get_agent_activity,
    get_uptime, get_git_info, get_telegram_status, get_trigger_summary,
    get_trading_params, get_trade_state, get_activity_log,
)

# ── Cache ────────────────────────────────────────────────────────────
_collect_cache = {"data": None, "ts": 0}
_COLLECT_CACHE_TTL = 5  # seconds — aligned with frontend's 5s polling interval


def collect_data():
    global _collect_cache
    now = time.time()
    if _collect_cache["data"] and now - _collect_cache["ts"] < _COLLECT_CACHE_TTL:
        return copy.copy(_collect_cache["data"])

    if _is_demo_mode():
        return _get_demo_data()

    ts = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    # All dynamic sources
    agents = get_agent_info()
    params = get_trading_params()
    trade = get_trade_state()

    # Multi-exchange breakdown — single pass, reuse for balance/positions
    exchange_data = get_all_exchange_data()

    # API health: which exchanges responded, how old is the data
    with _exchange_cache_lock:
        _exch_age = round(time.time() - _exchange_cache["ts"], 1) if _exchange_cache["ts"] else 999
    api_health = {
        "aster": "aster" in exchange_data,
        "binance": "binance" in exchange_data,
        "hyperliquid": "hyperliquid" in exchange_data,
        "exchange_data_age_s": _exch_age,
        "pnl_source": "api",  # updated below if fallback
    }

    # Check pending SL/TP for filled limit orders (zero extra API calls)
    if _pending_sltp:
        _check_pending_sltp(exchange_data)

    # Extract open orders (limit orders only, not SL/TP triggers) for dashboard display
    open_orders = _extract_open_orders(exchange_data)

    # Extract Aster data from exchange_data (avoid double API call)
    _aster_data = exchange_data.get("aster", {})
    live_bal = _aster_data.get("balance", 0.0) if _aster_data else get_live_balance()
    live_positions = _aster_data.get("positions", []) if _aster_data else get_live_positions()
    has_position = len(live_positions) > 0

    # Balance baseline for PnL
    # Today fee breakdown from exchange income API (verified source)
    fee_breakdown_raw = get_live_today_pnl()
    fee_breakdown = fee_breakdown_raw or {"realized": 0, "funding": 0, "commission": 0, "net": 0}

    # Balance baseline + cumulative fee tracking
    baseline = get_balance_baseline(live_bal, fee_breakdown if fee_breakdown_raw else None)

    # today_pnl: use verified source when API succeeded, fallback to balance delta when API failed
    if fee_breakdown_raw is None:
        api_health["pnl_source"] = "balance_delta"
    today_pnl = fee_breakdown["net"] if fee_breakdown_raw is not None else baseline["today_pnl"]
    pnl_history = update_pnl_history_verified(today_pnl, baseline["total_pnl"])

    # Unrealized PnL from live positions
    unrealized_pnl = round(sum(p["unrealized_pnl"] for p in live_positions), 4)
    unrealized_pct = round(unrealized_pnl / live_bal * 100, 2) if live_bal > 0 else 0.0

    # Position display from live exchange
    if has_position:
        pos = live_positions[0]
        position_str = pos["pair"]
        direction_str = pos["direction"]
    else:
        position_str = trade["position"]  # fallback "無"
        direction_str = trade["direction"]

    # Prices from scan config
    scan_config = parse_md(os.path.join(HOME, "shared/SCAN_CONFIG.md"))
    signal = parse_md(os.path.join(HOME, "shared/SIGNAL.md"))
    prices = {
        "BTC": scan_config.get("BTC_price", "0"),
        "ETH": scan_config.get("ETH_price", "0"),
        "XRP": scan_config.get("XRP_price", "0"),
        "XAG": scan_config.get("XAG_price", "0"),
    }
    last_scan_ts = scan_config.get("last_updated", signal.get("TIMESTAMP", "?"))

    # Build params_display from whitelist (profile-aware)
    params_display = []
    for key, label, unit in PARAMS_DISPLAY:
        val = params.get(key)
        if val is not None:
            if unit == "bool":
                display = "開" if val else "關"
            elif unit == "%" and isinstance(val, (int, float)):
                display = f"{val*100:.0f}{unit}" if val < 1 else f"{val:.0f}{unit}"
            elif unit == "$":
                display = f"{unit}{val}"
            else:
                display = f"{val}{unit}"
            params_display.append({"label": label, "value": display})

    # Trade history (for log display only)
    trades = _enrich_trades(get_trade_history(), prices, trade)

    # Exchange trade history (real fills from API)
    exchange_trades = get_live_trade_history()

    # New: trade stats (from real exchange fills), drawdown, signal heatmap
    trade_stats = get_trade_stats(exchange_trades)
    drawdown = calc_drawdown(pnl_history, baseline.get("all_time_start", 0), live_bal)
    signal_heatmap = []  # removed — scan_log rotation causes incomplete data
    funding_rates = get_funding_rates()

    # Pre-compute for position scoring (reused in result dict)
    action_plan = get_action_plan(scan_config, trade)
    news_sentiment = get_news_sentiment()
    risk_status = get_risk_status(live_bal)

    # Score each open position (pure formula, zero API calls)
    if live_positions:
        ap_by_sym = {p["symbol"]: p for p in action_plan} if action_plan else {}
        macro = _get_macro_state()
        for pos in live_positions:
            plan_entry = ap_by_sym.get(pos.get("pair"))
            pos["hold_score"] = _score_position(
                pos, plan_entry, news_sentiment, risk_status, funding_rates, macro
            )

    result = {
        "timestamp": ts,
        "balance": live_bal,
        "today_pnl": today_pnl,
        "total_pnl": baseline["total_pnl"],
        "mode": trade["market_mode"],
        "signal_active": signal.get("SIGNAL_ACTIVE", "NO"),
        "signal_pair": signal.get("PAIR", "---"),
        "position": position_str,
        "direction": direction_str,
        "in_position": has_position,
        "live_positions": live_positions,
        "open_orders": open_orders,
        "consecutive_losses": int(trade["consecutive_losses"]),
        "agents": agents,
        "params": params,
        "params_display": params_display,
        "scan_log": get_scan_log(),
        "file_tree": get_file_tree(),
        "prices": prices,
        "action_plan": action_plan,
        "trigger": scan_config.get("TRIGGER_PENDING", "OFF"),
        "scan_count": scan_config.get("LIGHT_SCAN_COUNT", "0"),
        "last_scan": last_scan_ts,
        "agent_activity": get_agent_activity(),
        "uptime": get_uptime(),
        "git": get_git_info(),
        "telegram": get_telegram_status(),
        "trigger_summary": get_trigger_summary(),
        "pnl_history": pnl_history,
        "trade_history": trades,
        "exchange_trades": exchange_trades,
        "risk_status": risk_status,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pct": unrealized_pct,
        "fee_breakdown": fee_breakdown,
        "cumulative_fees": baseline.get("cumulative_fees", {}),
        "active_profile": params.get("ACTIVE_PROFILE", "CONSERVATIVE"),
        "active_regime_preset": params.get("ACTIVE_REGIME_PRESET", "classic"),
        "regime_engine": params.get("REGIME_ENGINE", "votes_hmm"),
        "cp_enabled": params.get("CP_ENABLED", False),
        "activity_log": get_activity_log(50),
        "trade_stats": trade_stats,
        "drawdown": drawdown,
        "signal_heatmap": signal_heatmap,
        "funding_rates": funding_rates,
        "news_sentiment": news_sentiment,
        "demo_mode": False,
        "exchanges": exchange_data,
        "api_health": api_health,
    }
    _collect_cache["data"] = result
    _collect_cache["ts"] = time.time()
    return result
