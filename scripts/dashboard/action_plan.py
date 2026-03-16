"""action_plan.py — Action plan calculation + trade enrichment."""

import json
import os
import time

from scripts.dashboard.constants import HOME, PRICES_CACHE_PATH, parse_md
from scripts.dashboard.market_data import get_multi_interval_changes, _fetch_missing_atrs, _atr_fallback_cache

_action_cache = {"data": [], "ts": 0}


def get_action_plan(scan_config, trade_state):
    """計算每個幣種嘅行動部署。零額外 API call。30 秒 cache 防並發讀寫。"""
    global _action_cache
    now = time.time()
    if now - _action_cache["ts"] < 30:
        return _action_cache["data"]

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "params_ap", os.path.join(HOME, "config/params.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        all_symbols = sorted(set(
            getattr(mod, "ASTER_SYMBOLS", []) + getattr(mod, "BINANCE_SYMBOLS", [])
        ))
        active_profile = getattr(mod, "ACTIVE_PROFILE", "BALANCED")
        try:
            from config.profiles.loader import load_profile as _lp
            profiles = {active_profile: _lp(active_profile)}
        except Exception:
            profiles = {}
        try:
            spec_tc = importlib.util.spec_from_file_location(
                "settings_ap", os.path.join(HOME, "scripts/trader_cycle/config/settings.py")
            )
            mod_tc = importlib.util.module_from_spec(spec_tc)
            spec_tc.loader.exec_module(mod_tc)
            trader_pairs = set(getattr(mod_tc, "PAIRS", []))
        except Exception:
            trader_pairs = set()
    except Exception:
        return _action_cache["data"]

    profile = profiles.get(active_profile, {})
    threshold = profile.get("trigger_pct", 0.025) * 100
    sl_mult_range = profile.get("sl_atr_mult_range", 1.2)

    cache = {}
    try:
        with open(PRICES_CACHE_PATH) as f:
            cache = json.load(f)
    except Exception:
        return _action_cache["data"]

    consecutive = int(trade_state.get("consecutive_losses", 0))

    interval_changes = get_multi_interval_changes(all_symbols)

    _atr_from_config = {}
    missing_atr = []
    for s in all_symbols:
        short = s.replace("USDT", "")
        val = float(scan_config.get(f"{short}_ATR", 0))
        _atr_from_config[s] = val
        if val <= 0:
            missing_atr.append(s)
    if missing_atr:
        _fetch_missing_atrs(missing_atr)

    plans = []
    for sym in all_symbols:
        short = sym.replace("USDT", "")
        data = cache.get(sym, {})
        price = float(data.get("price", 0))
        if price <= 0:
            continue

        change = abs(float(data.get("change", 0)))
        atr = _atr_from_config.get(sym, 0)
        if atr <= 0:
            fb = _atr_fallback_cache.get(sym)
            if fb:
                atr = fb["atr"]
        support = float(scan_config.get(f"{short}_support", 0))
        resistance = float(scan_config.get(f"{short}_resistance", 0))

        if change >= threshold:
            status = "ready"
        elif change >= threshold * 0.7:
            status = "near"
        else:
            status = "far"

        sl_dist = atr * sl_mult_range if atr > 0 else 0
        tp_dist = sl_dist * 2.0 if sl_dist > 0 else 0

        blocker = f"連虧 {consecutive}" if consecutive >= 2 else None
        is_tradeable = sym in trader_pairs

        high_24h = float(data.get("high", 0))
        low_24h = float(data.get("low", 0))
        volume_24h = float(data.get("volume", 0))
        volume_ratio = float(scan_config.get(f"{short}_volume_ratio", 0))

        plans.append({
            "symbol": sym, "price": price,
            "change_pct": round(change, 2),
            "threshold_pct": round(threshold, 2),
            "distance": round(max(0, threshold - change), 2),
            "status": status,
            "blocker": blocker,
            "atr": round(atr, 6),
            "support": support, "resistance": resistance,
            "sl_long": round(price - sl_dist, 6) if sl_dist else None,
            "sl_short": round(price + sl_dist, 6) if sl_dist else None,
            "tp_long": round(price + tp_dist, 6) if tp_dist else None,
            "tp_short": round(price - tp_dist, 6) if tp_dist else None,
            "sl_pct": round(sl_dist / price * 100, 2) if sl_dist else None,
            "tp_pct": round(tp_dist / price * 100, 2) if tp_dist else None,
            "changes": interval_changes.get(sym, {}),
            "tradeable": is_tradeable,
            "high_24h": high_24h,
            "low_24h": low_24h,
            "volume_24h": volume_24h,
            "volume_ratio": volume_ratio,
        })
    _action_cache["data"] = plans
    _action_cache["ts"] = now
    return plans
