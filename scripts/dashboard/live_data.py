"""live_data.py — Live exchange data: positions, balance, trade history, funding."""

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from scripts.dashboard.constants import HOME, HKT, NEWS_SENTIMENT_PATH, parse_md
from scripts.dashboard.exchange_clients import (
    _get_aster_client, _get_binance_client, _get_hl_client,
    _reset_aster_client, _reset_binance_client, _reset_hl_client,
)
from scripts.dashboard.exchange_auth import (
    _get_aster_credentials, _get_binance_credentials, _get_hl_credentials,
)

# ── Position normalisation ──────────────────────────────────────────


def _normalize_positions(raw, orders, platform):
    """Normalize raw positions + open orders → dashboard format with platform tag.
    Works for Aster/Binance (native format) and HL (pre-normalized by client).
    """
    positions = []
    for p in raw:
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        symbol = p.get("symbol", "")
        entry = float(p.get("entryPrice", 0))
        mark = float(p.get("markPrice", 0))
        leverage = int(float(p.get("leverage", 1)))
        size = abs(amt)
        notional = size * mark
        upnl = float(p.get("unRealizedProfit", 0))
        upnl_pct = round(upnl / (notional / leverage) * 100, 2) if notional > 0 else 0

        # SL/TP from open orders — format differs by exchange
        sl_price = 0
        tp_price = 0
        for o in orders:
            # Aster/Binance format: type + stopPrice
            otype = o.get("type", "")
            if otype == "STOP_MARKET":
                if not symbol or o.get("symbol") == symbol:
                    sl_price = float(o.get("stopPrice", 0))
            elif otype == "TAKE_PROFIT_MARKET":
                if not symbol or o.get("symbol") == symbol:
                    tp_price = float(o.get("stopPrice", 0))
            # HL format: coin + orderType contains "Stop" / "Take"
            if not otype and o.get("coin"):
                hl_type = o.get("orderType", "")
                hl_sym = o.get("coin", "") + "USDT"
                if hl_sym == symbol or not symbol:
                    if "stop" in hl_type.lower():
                        sl_price = float(o.get("triggerPx", o.get("limitPx", 0)))
                    elif "take" in hl_type.lower():
                        tp_price = float(o.get("triggerPx", o.get("limitPx", 0)))

        positions.append({
            "pair": symbol,
            "direction": "LONG" if amt > 0 else "SHORT",
            "entry_price": entry,
            "mark_price": mark,
            "size": size,
            "notional": round(notional, 2),
            "leverage": leverage,
            "margin_type": p.get("marginType", "cross"),
            "margin": round(float(p.get("isolatedWallet", p.get("marginUsed", 0))), 2),
            "liq_price": float(p.get("liquidationPrice", p.get("liquidationPx", 0)) or 0),
            "unrealized_pnl": upnl,
            "unrealized_pct": upnl_pct,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "platform": platform,
        })
    return positions


# ── Multi-exchange query ────────────────────────────────────────────


def _query_single_exchange(name, client_fn, cred_check):
    """Query one exchange — called inside thread pool."""
    c1, c2 = cred_check()
    if not c1 or not c2:
        return None
    try:
        client = client_fn()
        orders = []
        try:
            orders = client.get_open_orders()
        except Exception as e:
            logging.warning("get_open_orders failed (%s): %s", name, e)
        return {
            "balance": client.get_usdt_balance(),
            "positions": _normalize_positions(client.get_positions(), orders, name),
            "orders": orders,
        }
    except Exception as e:
        logging.warning("exchange query %s error: %s", name, e)
        if name == "aster":
            _reset_aster_client()
        elif name == "binance":
            _reset_binance_client()
        elif name == "hyperliquid":
            _reset_hl_client()
        return None


_exchange_cache = {"data": {}, "ts": 0}
_exchange_cache_lock = threading.Lock()
_EXCHANGE_CACHE_TTL = 10  # 10s — positions update every 10s, not every 5s


def get_all_exchange_data():
    """Query all connected exchanges in parallel → per-exchange balance + positions.
    10s cache to avoid 429 rate limit (~18 calls/min instead of ~60).
    Thread-safe: lock protects cache read/write from concurrent requests."""
    now = time.time()
    with _exchange_cache_lock:
        if _exchange_cache["data"] and now - _exchange_cache["ts"] < _EXCHANGE_CACHE_TTL:
            return _exchange_cache["data"]

    exchanges = [
        ("aster", _get_aster_client, _get_aster_credentials),
        ("binance", _get_binance_client, _get_binance_credentials),
        ("hyperliquid", _get_hl_client, _get_hl_credentials),
    ]
    result = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_query_single_exchange, name, cfn, cred): name
            for name, cfn, cred in exchanges
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                data = fut.result(timeout=10)
                if data is not None:
                    result[name] = data
            except Exception as e:
                logging.warning("exchange query %s timeout/error: %s", name, e)

    with _exchange_cache_lock:
        if result:
            _exchange_cache["data"] = result
            _exchange_cache["ts"] = now
        elif _exchange_cache["data"]:
            return _exchange_cache["data"]  # keep stale data on total failure
    return result


# ── Single-exchange balance/positions (Aster primary) ──────────────


def get_live_balance():
    """Get USDT balance from Aster DEX. Falls back to TRADE_STATE.md."""
    try:
        return _get_aster_client().get_usdt_balance()
    except Exception:
        ts = parse_md(os.path.join(HOME, "shared/TRADE_STATE.md"))
        try:
            return float(ts.get("BALANCE_USDT", 0))
        except (ValueError, TypeError):
            return 0.0


def get_live_positions():
    """Get open positions from Aster DEX with full details."""
    try:
        client = _get_aster_client()
        raw = client.get_positions()
        positions = []
        for p in raw:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            symbol = p.get("symbol", "")
            entry = float(p.get("entryPrice", 0))
            mark = float(p.get("markPrice", 0))
            leverage = int(p.get("leverage", 1))
            size = abs(amt)
            notional = size * mark
            upnl = float(p.get("unRealizedProfit", 0))
            upnl_pct = round(upnl / (notional / leverage) * 100, 2) if notional > 0 else 0

            # Fetch SL/TP from open orders
            sl_price = 0
            tp_price = 0
            try:
                orders = client.get_open_orders(symbol)
                for o in orders:
                    if o.get("type") == "STOP_MARKET":
                        sl_price = float(o.get("stopPrice", 0))
                    elif o.get("type") == "TAKE_PROFIT_MARKET":
                        tp_price = float(o.get("stopPrice", 0))
            except Exception as e:
                logging.warning("get_open_orders failed (%s): %s", symbol, e)
                ts = parse_md(os.path.join(HOME, "shared/TRADE_STATE.md"))
                try:
                    sl_price = float(ts.get("SL_PRICE", 0))
                except (ValueError, TypeError):
                    sl_price = 0
                try:
                    tp_price = float(ts.get("TP_PRICE", 0))
                except (ValueError, TypeError):
                    tp_price = 0

            positions.append({
                "pair": symbol,
                "direction": "LONG" if amt > 0 else "SHORT",
                "entry_price": entry,
                "mark_price": mark,
                "size": size,
                "notional": round(notional, 2),
                "leverage": leverage,
                "margin_type": p.get("marginType", "isolated"),
                "margin": round(float(p.get("isolatedWallet", 0)), 2),
                "liq_price": float(p.get("liquidationPrice", 0)),
                "unrealized_pnl": upnl,
                "unrealized_pct": upnl_pct,
                "sl_price": sl_price,
                "tp_price": tp_price,
            })
        return positions
    except Exception:
        return []


# ── Trade history ───────────────────────────────────────────────────

_trade_history_cache = {"data": [], "ts": 0}
TRADE_HISTORY_CACHE_TTL = 60  # seconds


def get_live_trade_history():
    """Get last 30 trades from Aster DEX. 60s cache to avoid 429."""
    now = time.time()
    if now - _trade_history_cache["ts"] < TRADE_HISTORY_CACHE_TTL:
        return _trade_history_cache["data"]
    try:
        client = _get_aster_client()
        raw = client._private_request("GET", "/fapi/v1/userTrades", {"limit": 30})
        trades = []
        for t in raw:
            ts_ms = int(t.get("time", 0))
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=HKT)
            trades.append({
                "time": dt.strftime("%m-%d %H:%M"),
                "symbol": t.get("symbol", ""),
                "side": t.get("side", ""),
                "price": float(t.get("price", 0)),
                "qty": float(t.get("qty", 0)),
                "realizedPnl": float(t.get("realizedPnl", 0)),
                "commission": float(t.get("commission", 0)),
            })
        trades.reverse()  # 最新在前
        _trade_history_cache["data"] = trades
        _trade_history_cache["ts"] = now
        return trades
    except Exception:
        return _trade_history_cache["data"] or []


# ── Exchange income (PnL) ──────────────────────────────────────────


def _get_exchange_income(start_time=None, end_time=None, limit=100):
    """Query income from all connected exchanges. Returns summed totals.
    Any single exchange failure logs warning but doesn't block others."""
    exchanges = []
    ak, asec = _get_aster_credentials()
    if ak and asec:
        exchanges.append(("Aster", _get_aster_client))
    bk, bsec = _get_binance_credentials()
    if bk and bsec:
        exchanges.append(("Binance", _get_binance_client))
    hpk, haddr = _get_hl_credentials()
    if hpk and haddr:
        exchanges.append(("HL", _get_hl_client))

    if not exchanges:
        return None

    total = {"realized": 0.0, "funding": 0.0, "commission": 0.0, "insurance": 0.0}
    any_success = False
    for name, get_client in exchanges:
        try:
            client = get_client()
            kwargs = {"limit": limit}
            if start_time is not None:
                kwargs["start_time"] = start_time
            if end_time is not None:
                kwargs["end_time"] = end_time
            income = client.get_income(**kwargs)
            total["realized"] += sum(float(e["income"]) for e in income if e["incomeType"] == "REALIZED_PNL")
            total["funding"] += sum(float(e["income"]) for e in income if e["incomeType"] == "FUNDING_FEE")
            total["commission"] += sum(float(e["income"]) for e in income if e["incomeType"] == "COMMISSION")
            total["insurance"] += sum(float(e["income"]) for e in income if e["incomeType"] == "INSURANCE_CLEAR")
            any_success = True
        except Exception:
            logging.warning("Failed to get income from %s", name)

    if not any_success:
        return None
    total["net"] = total["realized"] + total["funding"] + total["commission"] + total["insurance"]
    return total


def get_live_today_pnl():
    """Get today's realized PnL from all connected exchanges."""
    now = datetime.now(HKT)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(today_start.timestamp() * 1000)
    return _get_exchange_income(start_time=start_ms, limit=100)


def _bootstrap_all_time_pnl():
    """One-time pull of all historical income BEFORE today from all connected exchanges.
    Used to seed all_time_realized when baseline has no such field.
    Excludes today — today's PnL is added separately via today_net."""
    today_start = datetime.now(HKT).replace(hour=0, minute=0, second=0, microsecond=0)
    end_ms = int(today_start.timestamp() * 1000)
    result = _get_exchange_income(start_time=None, end_time=end_ms, limit=1000)
    if result is None:
        return {"net": 0.0, "realized": 0.0, "funding": 0.0, "commission": 0.0, "insurance": 0.0}
    logging.info("Bootstrapped all-time realized PnL (excl today): %.4f (r=%.4f f=%.4f c=%.4f)",
                 result["net"], result["realized"], result["funding"], result["commission"])
    return result


# ── Funding rates ───────────────────────────────────────────────────

_funding_cache = {"data": {}, "ts": 0}
_FUNDING_CACHE_TTL = 120  # 2 min — funding rates update every 8h


def get_funding_rates():
    """Fetch current funding rates for watched symbols. Public API, 2-min cache."""
    now = time.time()
    if now - _funding_cache["ts"] < _FUNDING_CACHE_TTL:
        return _funding_cache["data"]
    try:
        client = _get_aster_client()
        raw = client._public_request("GET", "/fapi/v1/premiumIndex")
        rates = {}
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "params_fr", os.path.join(HOME, "config/params.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        watched = set(getattr(mod, "ASTER_SYMBOLS", []) + getattr(mod, "BINANCE_SYMBOLS", []))
        for item in raw:
            sym = item.get("symbol", "")
            if sym in watched:
                rate = float(item.get("lastFundingRate", 0))
                next_ts = int(item.get("nextFundingTime", 0))
                rates[sym] = {
                    "rate": round(rate * 100, 4),
                    "next_time": datetime.fromtimestamp(next_ts / 1000, tz=HKT).strftime("%H:%M") if next_ts else "",
                }
        _funding_cache["data"] = rates
        _funding_cache["ts"] = now
        return rates
    except Exception:
        logging.warning("Failed to fetch funding rates")
        return _funding_cache["data"]


# ── News sentiment ──────────────────────────────────────────────────

_news_cache = {"data": None, "ts": 0}
_NEWS_CACHE_TTL = 120  # 2 min
NEWS_STALE_MINUTES = 30


def get_news_sentiment():
    """Read news sentiment from shared JSON. 2-min cache, staleness check."""
    now = time.time()
    if now - _news_cache["ts"] < _NEWS_CACHE_TTL:
        return _news_cache["data"]
    if not os.path.exists(NEWS_SENTIMENT_PATH):
        _news_cache["data"] = None
        _news_cache["ts"] = now
        return None
    try:
        with open(NEWS_SENTIMENT_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        updated = raw.get("updated_at", "")
        stale = False
        if updated:
            try:
                ut = datetime.fromisoformat(updated)
                age_min = (datetime.now(timezone.utc) - ut).total_seconds() / 60
                stale = age_min > NEWS_STALE_MINUTES
            except (ValueError, TypeError):
                stale = True
        result = {
            "overall_sentiment": raw.get("overall_sentiment", "neutral"),
            "overall_impact": raw.get("overall_impact"),
            "confidence": raw.get("confidence", 0.0),
            "sentiment_by_symbol": raw.get("sentiment_by_symbol", {}),
            "key_narratives": raw.get("key_narratives", []),
            "risk_events": raw.get("risk_events", []),
            "summary": raw.get("summary", ""),
            "stale": stale or raw.get("stale", False),
            "updated_at": updated,
            "articles_analyzed": raw.get("articles_analyzed", 0),
        }
        _news_cache["data"] = result
        _news_cache["ts"] = now
        return result
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("Failed to read news sentiment: %s", e)
        return _news_cache["data"]
