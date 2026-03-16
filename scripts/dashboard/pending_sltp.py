"""pending_sltp.py — Pending SL/TP state for limit orders + auto-apply after fill."""

import json
import logging
import os
import tempfile
import threading
import time

from scripts.dashboard.constants import HOME
from scripts.dashboard.exchange_clients import (
    _get_aster_client, _get_binance_client, _get_hl_client,
)

_pending_sltp = {}  # {orderId: {symbol, platform, sl_price, tp_price, exit_side, qty, created_at}}
_PENDING_SLTP_FILE = os.path.join(HOME, "shared", "pending_sltp.json")
_pending_sltp_lock = threading.Lock()
_PENDING_SLTP_EXPIRY_SEC = 86400  # 24h


def _save_pending_sltp():
    """Atomic write pending SLTP state to JSON for crash recovery."""
    with _pending_sltp_lock:
        data = _pending_sltp.copy()
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.join(HOME, "shared"), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, _PENDING_SLTP_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logging.error("Failed to save pending SLTP: %s", e)


def _load_pending_sltp():
    """Load pending SLTP from JSON on startup, prune entries older than 24h."""
    global _pending_sltp
    if not os.path.exists(_PENDING_SLTP_FILE):
        return
    try:
        with open(_PENDING_SLTP_FILE, "r") as f:
            data = json.load(f)
        now = time.time()
        pruned = {
            oid: entry for oid, entry in data.items()
            if now - entry.get("created_at", 0) < _PENDING_SLTP_EXPIRY_SEC
        }
        with _pending_sltp_lock:
            _pending_sltp = pruned
        if len(pruned) < len(data):
            logging.info("Pending SLTP: pruned %d expired entries, %d remaining", len(data) - len(pruned), len(pruned))
            _save_pending_sltp()
        if pruned:
            logging.info("Pending SLTP: loaded %d entries from disk", len(pruned))
    except Exception as e:
        logging.warning("Failed to load pending SLTP: %s", e)


def _check_pending_sltp(exchange_data):
    """Check if pending limit orders have filled; auto-set SL/TP if so.
    Uses orders already fetched by get_all_exchange_data() — zero extra API calls
    for detection. Only creates new API calls when placing SL/TP."""
    with _pending_sltp_lock:
        pending_copy = dict(_pending_sltp)
    if not pending_copy:
        return

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    to_process = {}
    to_remove = []

    for order_id, entry in pending_copy.items():
        platform = entry["platform"]
        symbol = entry["symbol"]
        plat_data = exchange_data.get(platform)
        if plat_data is None:
            continue

        open_order_ids = set()
        for o in plat_data.get("orders", []):
            oid = str(o.get("orderId", o.get("oid", "")))
            if oid:
                open_order_ids.add(oid)

        if order_id in open_order_ids:
            continue

        has_position = False
        for pos in plat_data.get("positions", []):
            if pos.get("pair", "").upper() == symbol.upper():
                has_position = True
                break

        if not has_position:
            logging.info(
                "Pending SLTP: orderId=%s no position found for %s %s — removing (likely cancelled)",
                order_id, platform, symbol,
            )
            to_remove.append(order_id)
        else:
            to_process[order_id] = entry

    if to_process or to_remove:
        with _pending_sltp_lock:
            for oid in to_remove:
                _pending_sltp.pop(oid, None)
            for oid in to_process:
                _pending_sltp.pop(oid, None)
        _save_pending_sltp()

    re_queue = []
    for order_id, entry in to_process.items():
        platform = entry["platform"]
        symbol = entry["symbol"]
        exit_side = entry["exit_side"]
        sl_price = entry.get("sl_price", 0)
        tp_price = entry.get("tp_price", 0)
        order_qty = entry.get("qty", 0)
        if order_qty <= 0:
            logging.warning("Pending SLTP: orderId=%s has no stored qty, skipping", order_id)
            continue

        logging.info(
            "Pending SLTP: orderId=%s filled! Setting SL/TP for %s %s (qty=%s)",
            order_id, platform, symbol, order_qty,
        )

        try:
            client = client_fns[platform]()
        except Exception as e:
            logging.error("Pending SLTP: cannot connect to %s: %s", platform, e)
            re_queue.append((order_id, entry))
            continue

        sl_ok = True

        if sl_price > 0:
            try:
                client.create_stop_market(symbol, exit_side, order_qty, sl_price)
                logging.info("Pending SLTP: SL set %s %s @ %s qty=%s", platform, symbol, sl_price, order_qty)
            except Exception as e:
                sl_ok = False
                logging.error("Pending SLTP: SL failed %s %s @ %s → %s", platform, symbol, sl_price, e)

        if tp_price > 0:
            try:
                client.create_take_profit_market(symbol, exit_side, order_qty, tp_price)
                logging.info("Pending SLTP: TP set %s %s @ %s qty=%s", platform, symbol, tp_price, order_qty)
            except Exception as e:
                logging.warning("Pending SLTP: TP failed %s %s @ %s → %s", platform, symbol, tp_price, e)

        if not sl_ok:
            logging.warning("Pending SLTP: SL failed, will retry next cycle for %s %s", platform, symbol)
            re_queue.append((order_id, entry))

    if re_queue:
        with _pending_sltp_lock:
            for oid, entry in re_queue:
                _pending_sltp[oid] = entry
        _save_pending_sltp()

    resolved_count = len(to_remove) + len(to_process) - len(re_queue)
    if resolved_count > 0 or to_remove:
        logging.info("Pending SLTP: resolved %d, re-queued %d, remaining %d",
                     resolved_count, len(re_queue), len(_pending_sltp))


def _extract_open_orders(exchange_data):
    """Extract pending limit orders from exchange data for dashboard display."""
    result = []
    _TRIGGER_TYPES = {"STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT",
                      "TRAILING_STOP_MARKET"}
    for platform, pdata in exchange_data.items():
        if not pdata:
            continue
        for o in pdata.get("orders", []):
            otype = o.get("type", "")
            hl_type = o.get("orderType", "").lower()
            if otype in _TRIGGER_TYPES:
                continue
            if hl_type and ("stop" in hl_type or "take profit" in hl_type):
                continue

            oid = str(o.get("orderId", o.get("oid", "")))
            symbol = o.get("symbol", "")
            if not symbol and o.get("coin"):
                symbol = o["coin"] + "USDT"
            side = o.get("side", "").upper()
            if not side:
                side = o.get("side", "Buy").upper()
                if side == "B":
                    side = "BUY"
                elif side == "A":
                    side = "SELL"
            price = float(o.get("price", o.get("limitPx", 0)) or 0)
            qty = float(o.get("origQty", o.get("sz", 0)) or 0)
            filled = float(o.get("executedQty", 0) or 0)
            order_time = o.get("time", o.get("timestamp", 0))

            if not symbol or price <= 0:
                continue

            sltp_queued = False
            queued_sl = 0
            queued_tp = 0
            with _pending_sltp_lock:
                pentry = _pending_sltp.get(oid)
                if pentry:
                    sltp_queued = True
                    queued_sl = pentry.get("sl_price", 0)
                    queued_tp = pentry.get("tp_price", 0)

            result.append({
                "orderId": oid,
                "symbol": symbol.upper(),
                "side": side,
                "price": price,
                "qty": qty,
                "filled": filled,
                "platform": platform,
                "time": order_time,
                "sltp_queued": sltp_queued,
                "queued_sl": queued_sl,
                "queued_tp": queued_tp,
            })
    return result
