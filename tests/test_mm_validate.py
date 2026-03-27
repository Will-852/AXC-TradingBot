"""
Tests for polymarket/mm/validate.py — startup validation.

BMD P0 fix (2026-03-28): catches typos in constants/params before trading.
"""

import pytest


# ═══════════════════════════════════════
# validate_constants
# ═══════════════════════════════════════

def test_validate_constants_happy_path():
    """Current constants.py should pass validation."""
    from polymarket.mm.validate import validate_constants
    errors = validate_constants()
    assert errors == [], f"Unexpected errors: {errors}"


def test_validate_constants_uppercase_coin(monkeypatch):
    """Uppercase coin in _LIVE_TRADE_COINS should be caught."""
    from polymarket.mm import constants
    from polymarket.mm.validate import validate_constants

    monkeypatch.setattr(constants, "_LIVE_TRADE_COINS", {"BTC", "sol"})
    errors = validate_constants()
    assert any("non-lowercase" in e for e in errors), f"Expected uppercase error: {errors}"


def test_validate_constants_unknown_coin(monkeypatch):
    """Unknown coin should be caught."""
    from polymarket.mm import constants
    from polymarket.mm.validate import validate_constants

    monkeypatch.setattr(constants, "_LIVE_TRADE_COINS", {"btc", "doge"})
    errors = validate_constants()
    assert any("unknown coin" in e for e in errors), f"Expected unknown coin error: {errors}"


def test_validate_constants_bet_pct_mismatch(monkeypatch):
    """Missing key in _BET_PCT_BY_COIN should be caught."""
    from polymarket.mm import constants
    from polymarket.mm.validate import validate_constants

    monkeypatch.setattr(constants, "_LIVE_TRADE_COINS", {"btc", "sol"})
    monkeypatch.setattr(constants, "_BET_PCT_BY_COIN", {"btc": 0.03})  # sol missing
    errors = validate_constants()
    assert any("missing keys" in e for e in errors), f"Expected missing key error: {errors}"


def test_validate_constants_bet_pct_out_of_range(monkeypatch):
    """Bet pct > 1 should be caught."""
    from polymarket.mm import constants
    from polymarket.mm.validate import validate_constants

    monkeypatch.setattr(constants, "_BET_PCT_BY_COIN", {"btc": 1.5, "sol": 0.01})
    errors = validate_constants()
    assert any("out of range" in e for e in errors), f"Expected range error: {errors}"


# ═══════════════════════════════════════
# validate_params
# ═══════════════════════════════════════

def test_validate_params_happy_path():
    """Current params.py should pass validation."""
    from polymarket.mm.validate import validate_params
    errors = validate_params()
    assert errors == [], f"Unexpected errors: {errors}"


def test_validate_params_missing_timeframe(monkeypatch):
    """Missing timeframe key should be caught."""
    from config import params
    from polymarket.mm.validate import validate_params

    original = params.TIMEFRAME_PARAMS.copy()
    monkeypatch.setattr(params, "TIMEFRAME_PARAMS", {"15m": original["15m"]})
    errors = validate_params()
    assert any("missing timeframes" in e for e in errors), f"Expected missing TF: {errors}"


def test_validate_params_missing_sub_key(monkeypatch):
    """Missing sub-key should be caught."""
    from config import params
    from polymarket.mm.validate import validate_params

    patched = {k: v.copy() for k, v in params.TIMEFRAME_PARAMS.items()}
    del patched["1h"]["bb_length"]
    monkeypatch.setattr(params, "TIMEFRAME_PARAMS", patched)
    errors = validate_params()
    assert any("missing sub-keys" in e for e in errors), f"Expected missing sub-key: {errors}"


def test_validate_params_non_numeric_value(monkeypatch):
    """Non-numeric sub-key value should be caught."""
    from config import params
    from polymarket.mm.validate import validate_params

    patched = {k: v.copy() for k, v in params.TIMEFRAME_PARAMS.items()}
    patched["1h"]["rsi_period"] = "fourteen"
    monkeypatch.setattr(params, "TIMEFRAME_PARAMS", patched)
    errors = validate_params()
    assert any("numeric" in e for e in errors), f"Expected numeric error: {errors}"


# ═══════════════════════════════════════
# validate_bankroll
# ═══════════════════════════════════════

def test_validate_bankroll_healthy():
    """Normal bankroll should have no warnings."""
    from polymarket.mm.validate import validate_bankroll
    warnings = validate_bankroll({"bankroll": 50.0})
    assert warnings == [], f"Unexpected warnings: {warnings}"


def test_validate_bankroll_below_minimum():
    """Bankroll below minimum should warn."""
    from polymarket.mm.validate import validate_bankroll
    warnings = validate_bankroll({"bankroll": 1.0})
    assert any("min viable" in w.lower() or "MIN_VIABLE" in w for w in warnings)


def test_validate_bankroll_default_value():
    """Bankroll exactly 100.0 (default) should warn about possible reset."""
    from polymarket.mm.validate import validate_bankroll
    warnings = validate_bankroll({"bankroll": 100.0})
    assert any("default" in w.lower() for w in warnings)


# ═══════════════════════════════════════
# run_all
# ═══════════════════════════════════════

def test_run_all_happy_path():
    """Full validation should pass with current config."""
    from polymarket.mm.validate import run_all
    run_all()  # should not raise


def test_run_all_raises_on_bad_constants(monkeypatch):
    """run_all should raise RuntimeError on validation failure."""
    from polymarket.mm import constants
    from polymarket.mm.validate import run_all

    monkeypatch.setattr(constants, "_LIVE_TRADE_COINS", {"INVALID_COIN"})
    with pytest.raises(RuntimeError, match="Startup validation FAILED"):
        run_all()
