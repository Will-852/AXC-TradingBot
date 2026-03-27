"""
mm/validate.py — Startup validation for MM bot constants, params, and state.

Created: 2026-03-28 (BMD fix P0).
Catches typos, renames, and schema drift BEFORE any trading logic executes.

Design rules:
  - All imports inside function bodies (config.params depends on sys.path).
  - Use module references (constants.X), not from-import caching.
  - Idempotent: safe to call multiple times.
  - No side effects: only raises or returns.
"""

import logging
import os

log = logging.getLogger(__name__)

# ─── Known valid coin identifiers ───
# 💀 FATAL: adding a coin here without updating _LIVE_TRADE_COINS / _BET_PCT_BY_COIN
#    in constants.py will silently allow observe-only trading.
_KNOWN_COINS = {"btc", "eth", "sol", "xrp"}

# ─── Required TIMEFRAME_PARAMS structure ───
_REQUIRED_TIMEFRAMES = {"3m", "15m", "1h", "4h"}
_REQUIRED_SUB_KEYS = {
    "bb_length", "bb_mult", "rsi_period", "adx_period",
    "ema_fast", "ema_slow", "atr_period",
    "rsi_long", "rsi_short", "adx_range_max",
    "bb_touch_tol", "bb_width_squeeze", "lookback_support",
}


def validate_constants() -> list[str]:
    """Validate mm/constants.py trading-critical values.

    Returns list of error strings (empty = pass).

    💀 FATAL checks:
      - _LIVE_TRADE_COINS must be all lowercase
      - _BET_PCT_BY_COIN keys must be superset of _LIVE_TRADE_COINS
      - All coins must be in _KNOWN_COINS
    """
    # ⚠️ 容易錯 #2: module ref, not from-import
    from polymarket.mm import constants

    errors = []

    # --- _LIVE_TRADE_COINS ---
    live_coins = constants._LIVE_TRADE_COINS
    if not isinstance(live_coins, set):
        errors.append(f"_LIVE_TRADE_COINS must be a set, got {type(live_coins).__name__}")
        return errors  # can't continue

    for c in live_coins:
        if c != c.lower():
            errors.append(f"_LIVE_TRADE_COINS contains non-lowercase '{c}' — "
                          "all comparisons assume lowercase")
        if c.lower() not in _KNOWN_COINS:
            errors.append(f"_LIVE_TRADE_COINS contains unknown coin '{c}' — "
                          f"known: {_KNOWN_COINS}")

    # --- _BET_PCT_BY_COIN keys must cover _LIVE_TRADE_COINS ---
    bet_pct = constants._BET_PCT_BY_COIN
    missing = live_coins - set(bet_pct.keys())
    if missing:
        errors.append(f"_BET_PCT_BY_COIN missing keys for live coins: {missing}")

    for k, v in bet_pct.items():
        if not isinstance(v, (int, float)):
            errors.append(f"_BET_PCT_BY_COIN['{k}'] must be numeric, got {type(v).__name__}")
        elif not (0 < v <= 1):
            errors.append(f"_BET_PCT_BY_COIN['{k}']={v} out of range (0, 1]")

    # --- _MIN_VIABLE_BUDGET sanity ---
    mvb = constants._MIN_VIABLE_BUDGET
    if not isinstance(mvb, (int, float)) or mvb <= 0:
        errors.append(f"_MIN_VIABLE_BUDGET must be positive number, got {mvb}")

    return errors


def validate_params() -> list[str]:
    """Validate config/params.py TIMEFRAME_PARAMS structure.

    Returns list of error strings (empty = pass).

    🔴 2CHECK: 20+ files depend on these keys via direct [] access.
    """
    # ⚠️ 容易錯 #1: function-level import (sys.path may not be set at module level)
    from config.params import TIMEFRAME_PARAMS

    errors = []

    if not isinstance(TIMEFRAME_PARAMS, dict):
        errors.append(f"TIMEFRAME_PARAMS must be dict, got {type(TIMEFRAME_PARAMS).__name__}")
        return errors

    # --- Required timeframes exist ---
    missing_tf = _REQUIRED_TIMEFRAMES - set(TIMEFRAME_PARAMS.keys())
    if missing_tf:
        errors.append(f"TIMEFRAME_PARAMS missing timeframes: {missing_tf}")

    # --- Each timeframe has all required sub-keys with numeric values ---
    for tf in _REQUIRED_TIMEFRAMES & set(TIMEFRAME_PARAMS.keys()):
        sub = TIMEFRAME_PARAMS[tf]
        if not isinstance(sub, dict):
            errors.append(f"TIMEFRAME_PARAMS['{tf}'] must be dict, got {type(sub).__name__}")
            continue

        # ⚠️ 容易錯 #3: sub-keys must exactly match params.py
        missing_keys = _REQUIRED_SUB_KEYS - set(sub.keys())
        if missing_keys:
            errors.append(f"TIMEFRAME_PARAMS['{tf}'] missing sub-keys: {missing_keys}")

        for k, v in sub.items():
            if k in _REQUIRED_SUB_KEYS and not isinstance(v, (int, float)):
                errors.append(f"TIMEFRAME_PARAMS['{tf}']['{k}'] must be numeric, "
                              f"got {type(v).__name__}")

    return errors


def validate_state_paths() -> list[str]:
    """Validate that required directories exist for state files.

    Returns list of error strings (empty = pass).
    """
    from polymarket.mm import constants

    errors = []
    log_dir = constants._LOG_DIR
    if not os.path.isdir(log_dir):
        errors.append(f"Log directory does not exist: {log_dir}")

    return errors


def validate_bankroll(state: dict) -> list[str]:
    """Validate loaded state bankroll sanity.

    💀 FATAL: state_io.load() silently returns default state (bankroll=100.0)
    on corruption. This catches unexpected resets.

    Returns list of warning strings (non-fatal, logged only).
    """
    from polymarket.mm import constants

    warnings = []
    bankroll = state.get("bankroll", 0)
    mvb = constants._MIN_VIABLE_BUDGET

    if not isinstance(bankroll, (int, float)):
        warnings.append(f"bankroll is not numeric: {bankroll}")
    elif bankroll < mvb:
        warnings.append(f"bankroll ${bankroll:.2f} < _MIN_VIABLE_BUDGET ${mvb:.2f} — "
                         "possible state corruption or depleted balance")
    # ⚠️ 容易錯 #5: 100.0 is the default — might be legit initial value
    elif bankroll == 100.0:
        warnings.append("bankroll is exactly $100.00 — this is the default value. "
                         "Verify this is your actual balance, not a state reset.")

    return warnings


def run_all() -> None:
    """Run all startup validations. Raises RuntimeError on fatal issues.

    Call this in main() AFTER logging.basicConfig() but BEFORE any trading logic.
    ⚠️ 容易錯 #8: position matters — see task_plan.md
    """
    all_errors = []

    all_errors.extend(validate_constants())
    all_errors.extend(validate_params())
    all_errors.extend(validate_state_paths())

    if all_errors:
        msg = "Startup validation FAILED:\n" + "\n".join(f"  - {e}" for e in all_errors)
        log.error(msg)
        raise RuntimeError(msg)

    log.info("Startup validation passed (%d constants, %d timeframes, paths OK)",
             len(_KNOWN_COINS), len(_REQUIRED_TIMEFRAMES))
