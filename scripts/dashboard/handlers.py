"""handlers.py — Trading actions (place_order, close, modify SL/TP, etc.)."""

import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from scripts.dashboard.constants import (
    HOME, SCRIPTS_DIR, HKT,
    PRICES_CACHE_PATH, CONNECT_TIMEOUT_SEC,
    parse_md,
)
from scripts.dashboard.exchange_clients import (
    _get_aster_client, _get_binance_client, _get_hl_client,
)
from scripts.dashboard.pending_sltp import (
    _pending_sltp, _pending_sltp_lock, _save_pending_sltp,
)
from scripts.dashboard.action_plan import _action_cache
from scripts.dashboard.services import (
    get_trade_state, get_trading_params, get_scan_log,
    get_agent_info, get_uptime,
)

# ── Orderbook cache ──────────────────────────────────────────────────
_orderbook_cache: dict = {}  # {symbol: {"data": ..., "ts": float}}
_ORDERBOOK_CACHE_TTL = 10  # seconds


def _extract_hl_order_info(result: dict) -> dict:
    """Parse HyperLiquid SDK nested order response into flat dict.

    HL SDK returns: {status:"ok", response:{type:"order",
      data:{statuses:[{resting:{oid:N}} | {filled:{totalSz,avgPx,oid}}]}}}
    Returns: {orderId, avgPrice, executedQty, filled} or empty dict.
    """
    try:
        resp = result.get("response", {})
        if isinstance(resp, str):
            return {}
        data = resp.get("data", {})
        statuses = data.get("statuses", [])
        if not statuses:
            return {}
        s = statuses[0]
        if "filled" in s:
            f = s["filled"]
            return {
                "orderId": str(f.get("oid", "")),
                "avgPrice": float(f.get("avgPx", 0)),
                "executedQty": float(f.get("totalSz", 0)),
                "filled": True,
            }
        if "resting" in s:
            r = s["resting"]
            return {
                "orderId": str(r.get("oid", "")),
                "avgPrice": 0,
                "executedQty": 0,
                "filled": False,
            }
    except (KeyError, TypeError, ValueError, IndexError) as e:
        logging.warning("Failed to parse HL order response: %s → %s", result, e)
    return {}


def _invalidate_caches():
    """Invalidate collect + action caches after trading actions."""
    _action_cache["ts"] = 0
    try:
        from scripts.dashboard.collectors import _collect_cache
        _collect_cache["ts"] = 0
    except ImportError:
        pass


# ── Mode / Regime / Config ───────────────────────────────────────────

def handle_set_mode(body):
    """POST /api/set_mode — switch trading profile."""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}
    mode = data.get("mode", "").upper()
    valid = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]
    if mode not in valid:
        return 400, {"error": f"Invalid mode. Use: {valid}"}
    params_path = os.path.join(HOME, "config/params.py")
    try:
        with open(params_path) as f:
            content = f.read()
        content = re.sub(r'ACTIVE_PROFILE\s*=\s*"[^"]*"', f'ACTIVE_PROFILE = "{mode}"', content)
        with open(params_path, "w") as f:
            f.write(content)
        _invalidate_caches()
        return 200, {"ok": True, "mode": mode, "message": f"已切換至 {mode} 模式"}
    except Exception as e:
        return 500, {"error": str(e)}


def handle_set_regime(body):
    """POST /api/set_regime — switch regime preset."""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}
    preset = data.get("preset", "").lower()
    valid = ["classic", "classic_cp", "bocpd", "full"]
    if preset not in valid:
        return 400, {"error": f"Invalid preset. Use: {valid}"}
    params_path = os.path.join(HOME, "config/params.py")
    try:
        with open(params_path) as f:
            content = f.read()
        content = re.sub(
            r'ACTIVE_REGIME_PRESET\s*=\s*"[^"]*"',
            f'ACTIVE_REGIME_PRESET = "{preset}"',
            content,
        )
        with open(params_path, "w") as f:
            f.write(content)
        _invalidate_caches()
        return 200, {"ok": True, "preset": preset, "message": f"Regime → {preset}"}
    except Exception as e:
        return 500, {"error": str(e)}


def handle_api_state():
    """GET /api/state — AXC state endpoint. Returns trade state + signal + key params."""
    trade = get_trade_state()
    signal = parse_md(os.path.join(HOME, "shared/SIGNAL.md"))
    params = get_trading_params()
    return {
        "trade_state": trade,
        "signal": {
            "active": signal.get("SIGNAL_ACTIVE", "NO"),
            "pair": signal.get("PAIR", "—"),
            "direction": signal.get("DIRECTION", "—"),
            "strategy": signal.get("STRATEGY", "—"),
            "strength": signal.get("STRENGTH", "—"),
            "score": signal.get("SCORE", "0"),
            "entry_price": signal.get("ENTRY_PRICE", "0"),
            "timestamp": signal.get("TIMESTAMP", "—"),
            "reasons": signal.get("REASONS", "—"),
            "trigger_count": signal.get("TRIGGER_COUNT", "0"),
            "scan_status": signal.get("SCAN_STATUS", "—"),
        },
        "active_profile": params.get("ACTIVE_PROFILE", "CONSERVATIVE"),
        "trading_enabled": params.get("TRADING_ENABLED", True),
    }


def handle_api_config():
    """GET /api/config — AXC config endpoint. Returns all trading params."""
    return get_trading_params()


def handle_set_trading(body):
    """POST /api/config/trading — toggle TRADING_ENABLED in params.py."""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return 400, {"error": "Field 'enabled' must be boolean"}
    params_path = os.path.join(HOME, "config/params.py")
    try:
        with open(params_path) as f:
            content = f.read()
        if "TRADING_ENABLED" in content:
            content = re.sub(
                r'TRADING_ENABLED\s*=\s*\w+',
                f'TRADING_ENABLED = {enabled}',
                content,
            )
        else:
            content += f'\nTRADING_ENABLED = {enabled}\n'
        with open(params_path, "w") as f:
            f.write(content)
        return 200, {"ok": True, "enabled": enabled}
    except Exception as e:
        return 500, {"error": str(e)}


# ── Position / Order Actions ─────────────────────────────────────────

def handle_close_position(body):
    """POST /api/close-position — market close a position via dashboard."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON"}

    symbol = (data.get("symbol") or "").upper().strip()
    platform = (data.get("platform") or "").lower().strip()

    if not symbol or not symbol.endswith("USDT"):
        return 400, {"error": f"Invalid symbol: {symbol}"}
    if platform not in ("aster", "binance", "hyperliquid"):
        return 400, {"error": f"Invalid platform: {platform}"}

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    try:
        client = client_fns[platform]()
        result = client.close_position_market(symbol)
        logging.info("Dashboard close-position: %s %s → %s", platform, symbol, result)
        return 200, {"ok": True, "result": result}
    except Exception as e:
        logging.error("Dashboard close-position failed: %s %s → %s", platform, symbol, e)
        return 500, {"error": str(e)}


def handle_modify_sltp(body):
    """POST /api/modify-sltp — cancel+recreate SL/TP orders for a position."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON"}

    symbol = (data.get("symbol") or "").upper().strip()
    platform = (data.get("platform") or "").lower().strip()
    try:
        sl_price = float(data.get("sl_price") or 0)
        tp_price = float(data.get("tp_price") or 0)
    except (ValueError, TypeError):
        return 400, {"error": "SL/TP 價格必須為數字"}

    if not symbol or not symbol.endswith("USDT"):
        return 400, {"error": f"Invalid symbol: {symbol}"}
    if platform not in ("aster", "binance", "hyperliquid"):
        return 400, {"error": f"Invalid platform: {platform}"}
    if sl_price <= 0 and tp_price <= 0:
        return 400, {"error": "至少提供一個 SL 或 TP 價格"}

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    try:
        client = client_fns[platform]()

        # 1. Get position to determine side + qty
        positions = client.get_positions(symbol)
        pos = next((p for p in positions if float(p.get("positionAmt", 0)) != 0), None)
        if not pos:
            return 400, {"error": f"{symbol} 無持倉"}

        amt = float(pos["positionAmt"])
        direction = "LONG" if amt > 0 else "SHORT"
        close_side = "SELL" if direction == "LONG" else "BUY"
        qty = abs(amt)

        results = {}
        warnings = []

        # Helper: classify order as "sl"/"tp"/None across Aster/Binance + HL formats
        def _order_kind(o):
            otype = o.get("type", "")
            if otype == "STOP_MARKET":
                return "sl"
            if otype == "TAKE_PROFIT_MARKET":
                return "tp"
            # HL format: orderType contains "Stop Market" / "Take Profit"
            if not otype and o.get("coin"):
                hl_type = o.get("orderType", "").lower()
                if "stop" in hl_type:
                    return "sl"
                if "take" in hl_type:
                    return "tp"
            return None

        def _order_id(o):
            return str(o.get("orderId", o.get("oid", "")))

        # Fetch orders once if either SL or TP needs modification
        orders = client.get_open_orders(symbol) if (sl_price > 0 or tp_price > 0) else []

        # 2. Modify SL if provided
        if sl_price > 0:
            for o in orders:
                if _order_kind(o) == "sl":
                    try:
                        client.cancel_order(symbol, _order_id(o))
                    except Exception as e:
                        logging.warning("Cancel SL order %s failed: %s", _order_id(o), e)
            try:
                client.create_stop_market(symbol, close_side, qty, sl_price)
                results["sl"] = sl_price
                logging.info("Dashboard modify SL: %s %s → %s", platform, symbol, sl_price)
            except Exception as e:
                warnings.append(f"SL 設置失敗: {e}")
                logging.error("Dashboard create SL failed: %s %s → %s", platform, symbol, e)

        # 3. Modify TP if provided
        if tp_price > 0:
            for o in orders:
                if _order_kind(o) == "tp":
                    try:
                        client.cancel_order(symbol, _order_id(o))
                    except Exception as e:
                        logging.warning("Cancel TP order %s failed: %s", _order_id(o), e)
            try:
                client.create_take_profit_market(symbol, close_side, qty, tp_price)
                results["tp"] = tp_price
                logging.info("Dashboard modify TP: %s %s → %s", platform, symbol, tp_price)
            except Exception as e:
                warnings.append(f"TP 設置失敗: {e}")
                logging.error("Dashboard create TP failed: %s %s → %s", platform, symbol, e)

        if not results and warnings:
            return 500, {"error": "; ".join(warnings)}

        resp = {"ok": True, "results": results}
        if warnings:
            resp["warnings"] = warnings
        return 200, resp

    except Exception as e:
        logging.error("Dashboard modify-sltp failed: %s %s → %s", platform, symbol, e)
        return 500, {"error": str(e)}


def handle_cancel_order(body):
    """POST /api/cancel-order — cancel a pending order + clean up queued SL/TP."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON"}

    symbol = (data.get("symbol") or "").upper().strip()
    platform = (data.get("platform") or "").lower().strip()
    order_id = str(data.get("orderId") or "")

    if not symbol or not platform or not order_id:
        return 400, {"error": "symbol, platform, orderId required"}
    if platform not in ("aster", "binance", "hyperliquid"):
        return 400, {"error": f"Invalid platform: {platform}"}

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    try:
        client = client_fns[platform]()
        client.cancel_order(symbol, order_id)
        logging.info("Dashboard cancel-order: %s %s orderId=%s", platform, symbol, order_id)

        # Clean up pending SL/TP if queued
        with _pending_sltp_lock:
            removed = _pending_sltp.pop(order_id, None)
        if removed:
            _save_pending_sltp()
            logging.info("Pending SLTP removed for cancelled order: %s", order_id)

        _invalidate_caches()
        return 200, {"ok": True}

    except Exception as e:
        logging.error("Dashboard cancel-order failed: %s %s %s → %s", platform, symbol, order_id, e)
        return 500, {"error": str(e)}


def handle_place_order(body):
    """POST /api/place-order — open a new position from dashboard trade modal.
    Execution sequence:
      ① set_margin_mode ISOLATED
      ② set_leverage
      ③ market/limit entry
      ④ SL (critical — failure triggers emergency close; skipped for pending limit)
      ⑤ TP (best-effort; skipped for pending limit)
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON"}

    symbol = (data.get("symbol") or "").upper().strip()
    platform = (data.get("platform") or "").lower().strip()
    side = (data.get("side") or "").upper().strip()       # BUY or SELL
    order_type = (data.get("order_type") or "MARKET").upper().strip()
    sl_price = data.get("sl_price")
    tp_price = data.get("tp_price")

    try:
        qty = float(data.get("qty", 0))
        leverage = int(data.get("leverage", 5))
        limit_price = float(data.get("limit_price") or 0)
    except (ValueError, TypeError):
        return 400, {"error": "qty/leverage/limit_price 必須為數字"}

    # Validation
    if not symbol or not symbol.endswith("USDT"):
        return 400, {"error": f"Invalid symbol: {symbol}"}
    if platform not in ("aster", "binance", "hyperliquid"):
        return 400, {"error": f"Invalid platform: {platform}"}
    if side not in ("BUY", "SELL"):
        return 400, {"error": f"Invalid side: {side}"}
    if qty <= 0:
        return 400, {"error": "數量必須大於 0"}
    if leverage < 1 or leverage > 125:
        return 400, {"error": f"Invalid leverage: {leverage}"}
    if order_type not in ("MARKET", "LIMIT"):
        return 400, {"error": f"Invalid order_type: {order_type}"}
    if order_type == "LIMIT" and limit_price <= 0:
        return 400, {"error": "限價單需要有效價格"}

    exit_side = "SELL" if side == "BUY" else "BUY"
    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }

    try:
        client = client_fns[platform]()
    except Exception as e:
        return 500, {"error": f"交易所未連接: {e}"}

    # Pre-validate qty against exchange precision rules
    try:
        precision = client.validate_symbol_precision(symbol)
        step = precision.get("qty_precision", 0.001)
        min_qty_ex = precision.get("min_qty", 0.001)
        rounded_qty = client._round_to_precision(qty, step)

        if rounded_qty <= 0:
            return 400, {
                "error": f"數量太小：{qty:.8f} 經精度調整後為 0。"
                         f"最小下單量 {min_qty_ex}，請增加 USDT 金額"
            }
        if rounded_qty < min_qty_ex:
            return 400, {
                "error": f"數量 {rounded_qty} 低於最小下單量 {min_qty_ex}，"
                         f"請增加 USDT 金額"
            }
    except Exception as prec_err:
        # Only skip for network/timeout — symbol-not-found should fail early
        if "not found" in str(prec_err).lower():
            return 400, {"error": f"交易所不支援 {symbol}: {prec_err}"}
        logging.warning("Pre-validation skipped (non-fatal): %s", prec_err)

    t_start = time.time()

    try:
        # ① Margin mode
        try:
            client.set_margin_mode(symbol, "ISOLATED")
        except Exception:
            pass  # may already be set

        # ② Leverage
        client.set_leverage(symbol, leverage)

        # ③ Entry (market or limit)
        t_entry = time.time()
        if order_type == "LIMIT":
            entry_result = client.create_limit_order(symbol, side, qty, limit_price)
        else:
            entry_result = client.create_market_order(symbol, side, qty)
        t_fill = time.time()

        is_limit = order_type == "LIMIT"

        # HL SDK returns nested structure
        hl_info = _extract_hl_order_info(entry_result) if platform == "hyperliquid" else {}
        fill_qty = float(hl_info.get("executedQty") or entry_result.get("executedQty", 0)) or qty
        fill_price = float(hl_info.get("avgPrice") or entry_result.get("avgPrice", 0))
        raw_order_id = str(hl_info.get("orderId") or entry_result.get("orderId", ""))

        # Limit order may fill immediately if price matches market
        actually_pending = is_limit and fill_price == 0 and not hl_info.get("filled")
        resp = {
            "ok": True,
            "pending": actually_pending,
            "entry": {
                "orderId": raw_order_id,
                "avgPrice": fill_price if fill_price > 0 else limit_price,
                "executedQty": fill_qty,
            },
        }
        logging.info(
            "Dashboard place-order: %s %s %s %s qty=%s lev=%sx%s → %s",
            platform, symbol, side, order_type, qty, leverage,
            f" @{limit_price}" if is_limit else "", entry_result
        )

        # ④a Queue SL/TP for pending limit orders — auto-set after fill
        if actually_pending and (sl_price or tp_price):
            if raw_order_id:
                pending_entry = {
                    "symbol": symbol,
                    "platform": platform,
                    "sl_price": float(sl_price) if sl_price else 0,
                    "tp_price": float(tp_price) if tp_price else 0,
                    "exit_side": exit_side,
                    "qty": float(qty),  # store order qty for SL/TP placement
                    "created_at": time.time(),
                }
                with _pending_sltp_lock:
                    _pending_sltp[raw_order_id] = pending_entry
                _save_pending_sltp()
                resp["sltp_queued"] = True
                logging.info(
                    "Pending SLTP queued: %s %s orderId=%s sl=%s tp=%s qty=%s",
                    platform, symbol, raw_order_id,
                    sl_price or "none", tp_price or "none", qty,
                )
            else:
                logging.warning(
                    "Pending SLTP: cannot queue — empty orderId from %s %s response: %s",
                    platform, symbol, entry_result,
                )

        # ④b SL (critical) — skip for pending limit orders (not yet filled)
        if not actually_pending and sl_price and float(sl_price) > 0:
            try:
                sl_result = client.create_stop_market(
                    symbol, exit_side, fill_qty, float(sl_price)
                )
                resp["sl"] = {"orderId": str(sl_result.get("orderId", ""))}
            except Exception as sl_err:
                logging.error("Dashboard SL failed, emergency close: %s %s → %s", platform, symbol, sl_err)
                try:
                    client.close_position_market(symbol)
                    return 500, {"error": f"SL 落單失敗，已緊急平倉: {sl_err}"}
                except Exception as close_err:
                    logging.error("Emergency close also failed: %s", close_err)
                    return 500, {"error": f"SL 落單失敗，緊急平倉也失敗！倉位仍開放，請手動處理: {sl_err}"}

        # ⑤ TP (best-effort) — skip for pending limit orders
        if not actually_pending and tp_price and float(tp_price) > 0:
            try:
                tp_result = client.create_take_profit_market(
                    symbol, exit_side, fill_qty, float(tp_price)
                )
                resp["tp"] = {"orderId": str(tp_result.get("orderId", ""))}
            except Exception as tp_err:
                logging.warning("Dashboard TP failed (SL active): %s %s → %s", platform, symbol, tp_err)
                resp["warnings"] = [f"TP 設置失敗 (SL 保護中): {tp_err}"]

        # Timing: total + entry fill
        t_end = time.time()
        resp["timing"] = {
            "total_ms": round((t_end - t_start) * 1000),
            "fill_ms": round((t_fill - t_entry) * 1000),
        }

        # Invalidate caches so next fetchData() shows updated positions + orders
        _invalidate_caches()

        return 200, resp

    except Exception as e:
        err_msg = str(e).lower()
        if "insufficient" in err_msg or "balance" in err_msg:
            return 400, {"error": f"餘額不足: {e}"}
        logging.error("Dashboard place-order failed: %s %s → %s", platform, symbol, e)
        return 500, {"error": str(e)}


# ── Exchange Info ────────────────────────────────────────────────────

def handle_exchange_balance():
    """GET /api/exchange/balance — balances for all connected exchanges (parallel)."""
    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }

    def _query_balance(name, cfn):
        client = cfn()
        return name, client.get_usdt_balance()

    result = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_query_balance, name, cfn): name
            for name, cfn in client_fns.items()
        }
        for fut in as_completed(futures):
            try:
                name, bal = fut.result(timeout=CONNECT_TIMEOUT_SEC)
                result[name] = {"balance": bal}
            except Exception:
                pass  # exchange not connected
    return result


def handle_symbol_info(qs):
    """GET /api/exchange/symbol-info?symbol=BTCUSDT&platform=aster"""
    symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
    platform = (qs.get("platform", [""])[0] or "").lower().strip()

    if not symbol or not platform:
        return 400, {"error": "symbol and platform required"}

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    if platform not in client_fns:
        return 400, {"error": f"Unknown platform: {platform}"}

    try:
        client = client_fns[platform]()
        precision = client.validate_symbol_precision(symbol)
        step = precision.get("qty_precision", 0.001)
        min_qty = precision.get("min_qty", 0.001)
        min_notional = precision.get("min_notional", 5.0)
        tick_size = precision.get("price_precision", 0.01)

        return 200, {
            "symbol": symbol,
            "platform": platform,
            "step_size": step,
            "min_qty": min_qty,
            "min_notional": min_notional,
            "tick_size": tick_size,
            "order_types": ["MARKET"],
        }
    except Exception as e:
        return 500, {"error": str(e)}


def handle_orderbook(qs) -> tuple:
    """GET /api/orderbook?symbol=BTCUSDT — Order book depth with wall detection. 10s cache."""
    symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
    if not symbol:
        return 400, {"error": "symbol required"}

    now = time.time()
    cached = _orderbook_cache.get(symbol)
    if cached and now - cached["ts"] < _ORDERBOOK_CACHE_TTL:
        return 200, cached["data"]

    try:
        client = _get_aster_client()
        result = client.get_order_book(symbol, limit=20)
        _orderbook_cache[symbol] = {"data": result, "ts": now}
        return 200, result
    except Exception as e:
        logger.warning("Order book fetch failed for %s: %s", symbol, e)
        return 500, {"error": str(e)}


# ── API Endpoints ────────────────────────────────────────────────────

def handle_api_scan_log():
    """GET /api/scan-log — AXC scan log endpoint."""
    return {"lines": get_scan_log(n=20)}


def handle_api_health():
    """GET /api/health — AXC health endpoint. Agent status + timestamps + heartbeat."""
    agents = get_agent_info()

    # File mtime checks (same as tg_bot cmd_health)
    mtime_checks = {
        "main": os.path.join(HOME, "agents/main/sessions/sessions.json"),
        "heartbeat": os.path.join(HOME, "logs/heartbeat.log"),
        "signal": os.path.join(HOME, "shared/SIGNAL.md"),
    }
    timestamps = {}
    now = time.time()
    for key, path in mtime_checks.items():
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            age_min = int((now - mtime) / 60)
            timestamps[key] = {"age_min": age_min, "status": "ok" if age_min < 10 else ("warn" if age_min < 30 else "stale")}
        else:
            timestamps[key] = {"age_min": -1, "status": "missing"}

    # Scanner heartbeat
    scanner = {"status": "missing", "detail": "", "age_min": -1}
    hb_path = os.path.join(HOME, "logs/scanner_heartbeat.txt")
    if os.path.exists(hb_path):
        try:
            with open(hb_path) as f:
                hb = f.read().strip()
            parts = hb.split(" ", 2)
            ts = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
            age_min = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
            scanner = {
                "status": parts[1] if len(parts) > 1 else "unknown",
                "detail": parts[2] if len(parts) > 2 else "",
                "age_min": age_min,
            }
        except Exception:
            scanner = {"status": "error", "detail": "parse failed", "age_min": -1}

    # Memory count
    memory_count = 0
    emb_path = os.path.join(HOME, "memory/index/embeddings.npy")
    if os.path.exists(emb_path):
        try:
            import numpy as np
            embs = np.load(emb_path)
            memory_count = embs.shape[0]
        except Exception:
            pass

    # SCAN_LOG.md mtime
    scan_log_age_min = -1
    scan_log_path = os.path.join(HOME, "shared/SCAN_LOG.md")
    if os.path.exists(scan_log_path):
        scan_log_age_min = int((time.time() - os.path.getmtime(scan_log_path)) / 60)

    return {
        "agents": agents,
        "timestamps": timestamps,
        "scanner": scanner,
        "scan_log_age_min": scan_log_age_min,
        "memory_count": memory_count,
        "uptime": get_uptime(),
    }


def handle_suggest_mode():
    """GET /api/suggest_mode — suggest profile based on BTC 24h change."""
    change = 0.0
    try:
        with open(PRICES_CACHE_PATH) as f:
            cache = json.load(f)
        change = abs(float(cache.get("BTCUSDT", {}).get("change", 0)))
    except Exception:
        pass
    if change > 5.0:
        suggested = "AGGRESSIVE"
        reason = f"BTC 24H 變化 {change:.1f}% > 5%，市場波動大"
    elif change > 2.0:
        suggested = "BALANCED"
        reason = f"BTC 24H 變化 {change:.1f}%，中等波動"
    else:
        suggested = "CONSERVATIVE"
        reason = f"BTC 24H 變化 {change:.1f}%，市場平靜"
    return {"suggested": suggested, "reason": reason, "btc_change_24h": round(change, 2)}
