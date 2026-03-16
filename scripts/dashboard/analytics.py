"""analytics.py — Trade stats, risk status, drawdown, PnL history, balance baseline."""

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime

from scripts.dashboard.constants import (
    HOME, HKT, PNL_HISTORY_PATH, BALANCE_BASELINE_PATH, parse_md,
)
from scripts.dashboard.live_data import _bootstrap_all_time_pnl

# ── Trade history (from local JSONL) ────────────────────────────────

_trade_history_cache = {"data": [], "ts": 0}


def get_trade_history():
    """Read trade history from trades.jsonl, merge entry + exit records."""
    jsonl_path = os.path.join(HOME, "memory/store/trades.jsonl")
    if not os.path.exists(jsonl_path):
        return []

    raw = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

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

    trades = []
    for rec in entries:
        symbol = rec.get("symbol", "?")
        side = rec.get("side", "?")
        entry_val = float(rec.get("entry", 0))
        key = f"{symbol}|{side}|{entry_val}"

        exit_rec = exits.pop(key, None)
        if exit_rec:
            exit_price = float(exit_rec["exit"])
            pnl_val = float(exit_rec.get("pnl", 0))
            is_open = False
        else:
            exit_price = None
            pnl_val = 0.0
            is_open = True

        if is_open:
            status = "open"
        elif pnl_val > 0:
            status = "win"
        elif pnl_val < 0:
            status = "loss"
        else:
            status = "closed"

        suspicious = entry_val < 10 and symbol.endswith("USDT")
        trades.append({
            "dir": side,
            "asset": symbol,
            "entry": entry_val,
            "exit": exit_price,
            "pnl": pnl_val,
            "time": rec.get("ts", ""),
            "open": is_open,
            "size": 0,
            "status": status,
            "suspicious": suspicious,
        })

    for key, rec in exits.items():
        pnl_val = float(rec.get("pnl", 0))
        if pnl_val > 0:
            status = "win"
        elif pnl_val < 0:
            status = "loss"
        else:
            status = "closed"
        entry_val = float(rec.get("entry", 0))
        symbol = rec.get("symbol", "?")
        trades.append({
            "dir": rec.get("side", "?"),
            "asset": symbol,
            "entry": entry_val,
            "exit": float(rec["exit"]),
            "pnl": pnl_val,
            "time": rec.get("ts", ""),
            "open": False,
            "size": 0,
            "status": status,
            "suspicious": entry_val < 10 and symbol.endswith("USDT"),
        })

    return trades[-10:]


# ── Trade stats (from exchange fills) ───────────────────────────────


def get_trade_stats(exchange_trades=None):
    """Aggregate win/loss stats from REAL exchange fills (API data)."""
    empty = {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
             "avg_win": 0, "avg_loss": 0, "profit_factor": 0, "source": "exchange_api"}

    if not exchange_trades:
        return empty

    closed_pnls = []
    for t in exchange_trades:
        rpnl = float(t.get("realizedPnl", 0))
        if rpnl != 0:
            closed_pnls.append(rpnl)

    if not closed_pnls:
        return empty

    wins = [p for p in closed_pnls if p > 0]
    losses = [p for p in closed_pnls if p < 0]
    total = len(closed_pnls)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = round(win_count / total * 100, 1) if total > 0 else 0
    avg_win = round(sum(wins) / win_count, 2) if wins else 0
    avg_loss = round(sum(losses) / loss_count, 2) if losses else 0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0

    return {
        "total": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "source": "exchange_api",
    }


# ── Risk status ─────────────────────────────────────────────────────


def get_risk_status(live_balance=None):
    """Read risk parameters dynamically from settings.py + TRADE_STATE.md."""
    import importlib.util
    circuit_daily = 0
    circuit_single = 0
    cooldown_2 = 0
    cooldown_3 = 0
    max_hold = 0
    try:
        spec = importlib.util.spec_from_file_location(
            "settings", os.path.join(HOME, "scripts/trader_cycle/config/settings.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        circuit_daily = getattr(mod, "CIRCUIT_BREAKER_DAILY", 0)
        circuit_single = getattr(mod, "CIRCUIT_BREAKER_SINGLE", 0)
        cooldown_2 = getattr(mod, "COOLDOWN_2_LOSSES_MIN", 0)
        cooldown_3 = getattr(mod, "COOLDOWN_3_LOSSES_MIN", 0)
        max_hold = getattr(mod, "MAX_HOLD_HOURS", 0)
    except Exception:
        pass
    trade_state = parse_md(os.path.join(HOME, "shared/TRADE_STATE.md"))
    cons_losses = 0
    try:
        cons_losses = int(trade_state.get("CONSECUTIVE_LOSSES", "0"))
    except (ValueError, TypeError):
        pass
    if live_balance and live_balance > 0:
        balance = live_balance
    else:
        balance = 0.0
        try:
            balance = float(trade_state.get("BALANCE_USDT",
                            trade_state.get("ACCOUNT_BALANCE", "0")))
        except (ValueError, TypeError):
            pass
    max_daily_loss = round(balance * circuit_daily, 2) if circuit_daily else 0
    daily_loss = 0.0
    dl_str = trade_state.get("DAILY_LOSS", "0")
    m = re.search(r'[\$]?([\d.]+)', str(dl_str))
    if m:
        daily_loss = float(m.group(1))
    market_mode = trade_state.get("MARKET_MODE", "RANGE")
    cooldown_active = trade_state.get("COOLDOWN_ACTIVE", "NO") == "YES"
    max_cons = 3 if cooldown_3 > 0 else (2 if cooldown_2 > 0 else 1)

    scan_config = parse_md(os.path.join(HOME, "shared/SCAN_CONFIG.md"))
    hmm_regime = scan_config.get("HMM_REGIME", "")
    hmm_confidence = 0.0
    try:
        hmm_confidence = float(scan_config.get("HMM_CONFIDENCE", "0"))
    except (ValueError, TypeError):
        pass

    return {
        "consecutive_losses": cons_losses,
        "max_consecutive_losses": max_cons,
        "daily_loss": daily_loss,
        "max_daily_loss": max_daily_loss,
        "circuit_daily_pct": round(circuit_daily * 100),
        "circuit_single_pct": round(circuit_single * 100),
        "cooldown_2_min": cooldown_2,
        "cooldown_3_min": cooldown_3,
        "max_hold_hours": max_hold,
        "market_mode": market_mode,
        "trigger_cooldown": cooldown_active or cons_losses >= 2,
        "hmm_regime": hmm_regime,
        "hmm_confidence": hmm_confidence,
    }


# ── Balance baseline + PnL tracking ────────────────────────────────


def get_balance_baseline(current_balance, fee_breakdown=None):
    """Get or create balance baseline. Resets start_of_day on new day.
    total_pnl is realized-based — immune to deposits/withdrawals."""
    try:
        bal = float(current_balance)
    except (ValueError, TypeError):
        return {"today_pnl": 0, "total_pnl": 0, "start_of_day": 0, "all_time_start": 0,
                "cumulative_fees": {"realized": 0, "funding": 0, "commission": 0}}

    today = datetime.now(HKT).strftime("%Y-%m-%d")
    data = None
    if os.path.exists(BALANCE_BASELINE_PATH):
        try:
            with open(BALANCE_BASELINE_PATH) as f:
                data = json.load(f)
        except Exception:
            data = None

    dirty = False
    if data is None:
        bootstrapped = _bootstrap_all_time_pnl()
        data = {"start_of_day": bal, "date": today, "all_time_start": bal,
                "all_time_realized": bootstrapped["net"],
                "cumulative_fees": {
                    "realized": bootstrapped["realized"],
                    "funding": bootstrapped["funding"],
                    "commission": bootstrapped["commission"],
                    "insurance": bootstrapped["insurance"],
                },
                "yesterday_fees": {"realized": 0, "funding": 0, "commission": 0, "insurance": 0}}
        dirty = True
    else:
        if "all_time_realized" not in data:
            bootstrapped = _bootstrap_all_time_pnl()
            data["all_time_realized"] = bootstrapped["net"]
            data["cumulative_fees"] = {
                "realized": bootstrapped["realized"],
                "funding": bootstrapped["funding"],
                "commission": bootstrapped["commission"],
                "insurance": bootstrapped["insurance"],
            }
            dirty = True

        cum = data.get("cumulative_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
        cum_total = sum(cum.get(k, 0) for k in ("realized", "funding", "commission", "insurance"))
        atr = data.get("all_time_realized", 0)
        if abs(cum_total - atr) > 0.01:
            bootstrapped = _bootstrap_all_time_pnl()
            data["cumulative_fees"] = {
                "realized": bootstrapped["realized"],
                "funding": bootstrapped["funding"],
                "commission": bootstrapped["commission"],
                "insurance": bootstrapped["insurance"],
            }
            data["all_time_realized"] = bootstrapped["net"]
            data["yesterday_fees"] = {"realized": 0, "funding": 0, "commission": 0, "insurance": 0}
            logging.info("Re-bootstrapped cumulative_fees to match all_time_realized: %.4f", bootstrapped["net"])
            dirty = True

        if data.get("date") != today:
            cum = data.get("cumulative_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
            yest = data.get("yesterday_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
            yesterday_net = 0.0
            for k in ("realized", "funding", "commission", "insurance"):
                val = round(yest.get(k, 0), 4)
                cum[k] = round(cum.get(k, 0) + val, 4)
                yesterday_net += val
            data["cumulative_fees"] = cum
            data["all_time_realized"] = round(data.get("all_time_realized", 0) + yesterday_net, 4)
            data["yesterday_fees"] = {"realized": 0, "funding": 0, "commission": 0, "insurance": 0}
            data["start_of_day"] = bal
            data["date"] = today
            dirty = True

    if fee_breakdown:
        data["yesterday_fees"] = {
            "realized": fee_breakdown.get("realized", 0),
            "funding": fee_breakdown.get("funding", 0),
            "commission": fee_breakdown.get("commission", 0),
            "insurance": fee_breakdown.get("insurance", 0),
        }
        dirty = True

    if dirty:
        tmp = tempfile.NamedTemporaryFile(mode='w', dir=os.path.dirname(BALANCE_BASELINE_PATH),
                                          delete=False, suffix='.tmp')
        json.dump(data, tmp)
        tmp.close()
        os.replace(tmp.name, BALANCE_BASELINE_PATH)

    today_pnl = round(bal - data["start_of_day"], 2)

    today_net = 0.0
    if fee_breakdown:
        for k in ("realized", "funding", "commission", "insurance"):
            today_net += fee_breakdown.get(k, 0)
    total_pnl = round(data.get("all_time_realized", 0) + today_net, 2)

    cum = data.get("cumulative_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
    today_fees = data.get("yesterday_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
    total_fees = {}
    for k in ("realized", "funding", "commission", "insurance"):
        total_fees[k] = round(cum.get(k, 0) + today_fees.get(k, 0), 4)

    return {
        "today_pnl": today_pnl,
        "total_pnl": total_pnl,
        "start_of_day": data["start_of_day"],
        "all_time_start": data["all_time_start"],
        "cumulative_fees": total_fees,
    }


def update_pnl_history_verified(today_pnl, cumulative_pnl=None):
    """Track PnL history using verified today_pnl value."""
    data = {"history": []}
    if os.path.exists(PNL_HISTORY_PATH):
        try:
            with open(PNL_HISTORY_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {"history": []}
    now = int(time.time())
    pnl = round(today_pnl, 2)
    point = {"t": now, "v": pnl}
    if cumulative_pnl is not None:
        point["c"] = round(cumulative_pnl, 2)
    hist = data.get("history", [])
    if hist and now - hist[-1]["t"] < 30:
        hist[-1] = point
    else:
        hist.append(point)
    data["history"] = hist[-500:]
    try:
        with open(PNL_HISTORY_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass
    return data["history"]


def calc_drawdown(pnl_history, all_time_start, current_balance):
    """Calculate current and max drawdown."""
    empty = {"current_dd": 0, "current_dd_pct": 0, "max_dd": 0, "max_dd_pct": 0, "peak_value": 0}
    if all_time_start <= 0:
        return empty

    peak_pnl = 0.0
    if pnl_history:
        for point in pnl_history:
            v = point.get("v", 0)
            if v > peak_pnl:
                peak_pnl = v

    peak_value = round(all_time_start + peak_pnl, 2)

    current_dd = max(peak_value - current_balance, 0)
    intraday_max_dd = 0.0
    if pnl_history:
        running_peak = 0.0
        for point in pnl_history:
            v = point.get("v", 0)
            if v > running_peak:
                running_peak = v
            dd = running_peak - v
            if dd > intraday_max_dd:
                intraday_max_dd = dd
    max_dd = max(current_dd, intraday_max_dd)

    return {
        "current_dd": round(current_dd, 2),
        "current_dd_pct": round(current_dd / peak_value * 100, 2) if peak_value > 0 else 0,
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / peak_value * 100, 2) if peak_value > 0 else 0,
        "peak_value": peak_value,
    }


# ── Enrich trades ──────────────────────────────────────────────────


def _enrich_trades(trades, prices, trade_state):
    """Enrich trades: cross-reference TRADE_STATE for open/closed truth."""
    position_open = trade_state.get("in_position", False)
    for t in trades:
        if t.get("open"):
            if not position_open:
                t["open"] = False
                t["exit"] = "SL/TP"
                t["stale_open"] = True
                t["status"] = "closed"
            else:
                sym = t["asset"].replace("USDT", "")
                try:
                    t["current_price"] = float(prices.get(sym, 0))
                except (ValueError, TypeError):
                    t["current_price"] = 0
    return trades
