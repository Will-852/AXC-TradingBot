"""Tests for config/coins/ loader — per-coin config architecture."""

import pytest

from config.coins.loader import (
    get_coin,
    get_all_coins,
    get_active_symbols,
    get_group_peers,
    get_position_groups,
    get_correlation_set,
    get_liq_coins,
    get_regime_anchor,
    get_exchange_symbols,
    get_conf_gate,
    is_strategy_enabled,
    get_prefix_map,
    get_coin_by_prefix,
)
from scripts.trader_cycle.config.pairs import get_pair, PairConfig


class TestCoinLoading:
    """All 7 coins load correctly."""

    def test_all_7_coins_loaded(self):
        coins = get_all_coins()
        assert len(coins) == 7
        assert set(coins.keys()) == {
            "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT",
            "POLUSDT", "XAGUSDT", "XAUUSDT",
        }

    def test_btc_is_baseline(self):
        btc = get_coin("BTCUSDT")
        assert btc["vol_mult"] == 1.0
        assert btc["max_leverage"] == 20
        assert btc["regime_anchor"] is True
        assert btc["pair_priority"] == 4

    def test_eth_has_rsi_overrides(self):
        eth = get_coin("ETHUSDT")
        assert eth["indicator_params"]["rsi_long"] == 32
        assert eth["indicator_params"]["rsi_short"] == 68

    def test_xrp_has_bb_override(self):
        xrp = get_coin("XRPUSDT")
        assert xrp["indicator_params"]["bb_touch_tol"] == 0.008
        assert xrp["sl_mult_override"] == 1.0

    def test_unknown_coin_raises(self):
        with pytest.raises(KeyError, match="Unknown coin"):
            get_coin("DOGEUSDT")

    def test_defaults_inherited(self):
        """Coins without explicit indicator_params still get empty dict."""
        sol = get_coin("SOLUSDT")
        assert sol["indicator_params"] == {}


class TestIndicatorToggles:
    """Indicator enabled/disabled flags work."""

    def test_active_indicators_on(self):
        btc = get_coin("BTCUSDT")
        for ind in ["bb", "rsi", "adx", "atr", "macd_hist", "obv", "ma", "sr", "volume_ratio"]:
            assert btc["indicators"][ind] is True, f"{ind} should be enabled"

    def test_unused_indicators_off(self):
        btc = get_coin("BTCUSDT")
        for ind in ["di", "ema_legacy", "stoch", "macd_line_signal", "vwap", "vol_spike", "z_robust", "bb_width_pctl"]:
            assert btc["indicators"][ind] is False, f"{ind} should be disabled"


class TestStrategyConfig:
    """Per-coin strategy enabled/disabled and conf_gate."""

    def test_pol_all_disabled(self):
        for strat in ["range", "trend", "crash"]:
            assert not is_strategy_enabled("POLUSDT", strat)

    def test_xag_all_disabled(self):
        for strat in ["range", "trend", "crash"]:
            assert not is_strategy_enabled("XAGUSDT", strat)

    def test_btc_all_enabled(self):
        for strat in ["range", "trend", "crash"]:
            assert is_strategy_enabled("BTCUSDT", strat)

    def test_xau_still_enabled(self):
        """XAU is profitable — strategies should be ON."""
        for strat in ["range", "trend", "crash"]:
            assert is_strategy_enabled("XAUUSDT", strat)

    def test_eth_conf_gates_stricter(self):
        assert get_conf_gate("ETHUSDT", "range") == 0.50
        assert get_conf_gate("ETHUSDT", "trend") == 0.50
        assert get_conf_gate("ETHUSDT", "crash") == 0.50

    def test_btc_conf_gates_default(self):
        assert get_conf_gate("BTCUSDT", "range") == 0.40
        assert get_conf_gate("BTCUSDT", "trend") == 0.48

    def test_active_symbols_excludes_disabled(self):
        active = get_active_symbols()
        assert "POLUSDT" not in active
        assert "XAGUSDT" not in active
        assert "BTCUSDT" in active
        assert "XAUUSDT" in active


class TestDerivedData:
    """Derived lookups match previous hardcoded values."""

    def test_position_groups(self):
        groups = get_position_groups()
        assert set(groups["crypto_correlated"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
        assert set(groups["crypto_independent"]) == {"POLUSDT", "XRPUSDT"}
        assert set(groups["commodity"]) == {"XAGUSDT", "XAUUSDT"}

    def test_correlation_set(self):
        assert get_correlation_set() == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

    def test_liq_coins(self):
        assert set(get_liq_coins()) == {"BTC", "ETH", "SOL"}

    def test_regime_anchor(self):
        assert get_regime_anchor() == "BTCUSDT"

    def test_aster_symbols(self):
        aster = get_exchange_symbols("aster")
        assert "BTCUSDT" in aster
        assert "XAUUSDT" in aster
        assert "SOLUSDT" not in aster  # Binance only

    def test_binance_symbols(self):
        binance = get_exchange_symbols("binance")
        assert "SOLUSDT" in binance
        assert "POLUSDT" in binance

    def test_group_peers(self):
        peers = get_group_peers("BTCUSDT")
        assert set(peers) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

    def test_prefix_map(self):
        pm = get_prefix_map()
        assert pm["BTCUSDT"] == "BTC"
        assert pm["XAUUSDT"] == "XAU"

    def test_get_by_prefix(self):
        eth = get_coin_by_prefix("ETH")
        assert eth["symbol"] == "ETHUSDT"


class TestPairsBackwardCompat:
    """pairs.py PairConfig adapter matches original values exactly."""

    def test_btc_pair_config(self):
        p = get_pair("BTCUSDT")
        assert isinstance(p, PairConfig)
        assert p.vol_mult == 1.0
        assert p.max_leverage == 20
        assert p.price_precision == 1
        assert p.qty_precision == 3
        assert p.rsi_long is None
        assert p.bb_touch_tol is None

    def test_eth_pair_config(self):
        p = get_pair("ETHUSDT")
        assert p.rsi_long == 32
        assert p.rsi_short == 68
        assert p.vol_mult == 1.5

    def test_xrp_pair_config(self):
        p = get_pair("XRPUSDT")
        assert p.bb_touch_tol == 0.008
        assert p.sl_mult_override == 1.0
        assert p.vol_mult == 2.0
        assert p.max_leverage == 15

    def test_all_7_pairs_available(self):
        from scripts.trader_cycle.config.pairs import get_all_symbols
        assert len(get_all_symbols()) == 7
