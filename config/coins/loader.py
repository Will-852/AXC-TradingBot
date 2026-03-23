"""
loader.py — Unified per-coin config loader。

用法：
    from config.coins import get_coin, get_all_coins, get_active_symbols

    coin = get_coin("BTCUSDT")
    coin["vol_mult"]                    # 1.0
    coin["strategies"]["range"]["enabled"]  # True
    coin["indicators"]["vwap"]          # False

設計：
    1. 每個 coin subfolder 有 config.py → COIN dict
    2. Loader merge COIN_DEFAULTS + coin-specific overrides (deep merge)
    3. 舊 pairs.py PairConfig 保持 backward compat (adapter)
    4. 一次 import，之後 cache — 唔使每次重新讀文件

遷移計劃：
    Phase 1: loader + 舊 pairs.py 並存 (adapter pattern)
    Phase 2: consumers 逐步改用 get_coin()
    Phase 3: pairs.py deprecated
"""

import copy
import importlib
import logging
import os
import threading

from config.coins._defaults import COIN_DEFAULTS

log = logging.getLogger(__name__)

# ─── Discover coin subfolders ───
_COINS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKIP = {"__pycache__", "__init__", "_defaults", "loader"}

# Thread-safe cache
_lock = threading.Lock()
_cache: dict[str, dict] = {}
_prefix_map: dict[str, str] = {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base. Override wins for leaf values."""
    merged = copy.deepcopy(base)
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def _load_all():
    """Scan config/coins/*/config.py and load all COIN dicts."""
    if _cache:
        return  # Already loaded — fast path (no lock needed)

    with _lock:
        if _cache:
            return  # Double-check inside lock

        for entry in sorted(os.listdir(_COINS_DIR)):
            if entry.startswith(("_", ".")) or entry in _SKIP:
                continue
            coin_dir = os.path.join(_COINS_DIR, entry)
            if not os.path.isdir(coin_dir):
                continue
            config_path = os.path.join(coin_dir, "config.py")
            if not os.path.isfile(config_path):
                log.warning("Coin dir '%s' has no config.py — skipping", entry)
                continue

            try:
                mod = importlib.import_module(f"config.coins.{entry}.config")
                coin_raw = getattr(mod, "COIN", None)
                if not coin_raw or not isinstance(coin_raw, dict):
                    log.warning("config.coins.%s.config has no COIN dict — skipping", entry)
                    continue

                # Deep merge defaults + coin-specific
                merged = _deep_merge(COIN_DEFAULTS, coin_raw)
                symbol = merged["symbol"]
                if not symbol:
                    log.warning("config.coins.%s has empty symbol — skipping", entry)
                    continue

                _cache[symbol] = merged
                _prefix_map[merged["prefix"]] = symbol
                log.debug("Loaded coin config: %s (%s)", symbol, entry)

            except Exception as e:
                log.error("Failed to load config.coins.%s: %s", entry, e)

        log.info("Loaded %d coin configs: %s", len(_cache), list(_cache.keys()))


def get_coin(symbol: str) -> dict:
    """Get merged coin config by symbol (e.g. 'BTCUSDT').

    Raises KeyError if symbol not found.
    """
    _load_all()
    if symbol not in _cache:
        raise KeyError(f"Unknown coin: {symbol}. Available: {list(_cache.keys())}")
    return _cache[symbol]


def get_coin_by_prefix(prefix: str) -> dict:
    """Get coin config by prefix (e.g. 'BTC')."""
    _load_all()
    symbol = _prefix_map.get(prefix)
    if not symbol:
        raise KeyError(f"Unknown prefix: {prefix}. Available: {list(_prefix_map.keys())}")
    return _cache[symbol]


def get_all_coins() -> dict[str, dict]:
    """Get all loaded coin configs. Returns {symbol: config_dict}."""
    _load_all()
    return dict(_cache)


def get_active_symbols() -> list[str]:
    """Get symbols that have at least one enabled strategy."""
    _load_all()
    result = []
    for symbol, cfg in _cache.items():
        strategies = cfg.get("strategies", {})
        if any(s.get("enabled", False) for s in strategies.values()):
            result.append(symbol)
    return result


def get_group_peers(symbol: str) -> list[str]:
    """Get all symbols in the same position group as the given symbol."""
    _load_all()
    coin = _cache.get(symbol)
    if not coin:
        return []
    group = coin["group"]
    return [s for s, c in _cache.items() if c["group"] == group]


def get_exchange_symbols(exchange: str) -> list[str]:
    """Get all symbols available on a given exchange."""
    _load_all()
    return [s for s, c in _cache.items()
            if exchange in c.get("exchange", [])]


def get_position_groups() -> dict[str, list[str]]:
    """Build POSITION_GROUPS dict from coin configs.

    Returns: {"crypto_correlated": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], ...}
    Replaces hardcoded POSITION_GROUPS in settings.py.
    """
    _load_all()
    groups: dict[str, list[str]] = {}
    for symbol, cfg in _cache.items():
        group = cfg["group"]
        groups.setdefault(group, []).append(symbol)
    return groups


def get_correlation_set() -> set[str]:
    """Get symbols that participate in cross-pair correlation boost.

    Replaces hardcoded _CRYPTO_PAIRS in evaluate.py.
    """
    _load_all()
    return {s for s, c in _cache.items() if c.get("correlation_tracked", False)}


def get_liq_coins() -> list[str]:
    """Get coin prefixes for liquidation monitoring.

    Replaces hardcoded LIQ_COINS in liq_params.py.
    """
    _load_all()
    return [c["prefix"] for c in _cache.values() if c.get("liq_enabled", False)]


def get_regime_anchor() -> str:
    """Get the symbol designated as regime/mode detection primary.

    Replaces hardcoded 'BTCUSDT' in mode_detector.py.
    """
    _load_all()
    for symbol, cfg in _cache.items():
        if cfg.get("regime_anchor", False):
            return symbol
    return "BTCUSDT"  # Ultimate fallback


def get_prefix_map() -> dict[str, str]:
    """Get {symbol: prefix} mapping. Replaces PAIR_PREFIX in settings.py."""
    _load_all()
    return {s: c["prefix"] for s, c in _cache.items()}


def get_conf_gate(symbol: str, strategy: str) -> float:
    """Get the confidence gate for a specific (symbol, strategy) combo.

    Replaces SIGNAL_CONF_GATE_PER_SYMBOL lookups in signal_filter.py.
    Falls back to strategy default from _defaults.py.
    """
    _load_all()
    coin = _cache.get(symbol)
    if not coin:
        return 0.50  # Safe default
    strategies = coin.get("strategies", {})
    strat_cfg = strategies.get(strategy, {})
    return strat_cfg.get("conf_gate", 0.50)


def is_strategy_enabled(symbol: str, strategy: str) -> bool:
    """Check if a specific strategy is enabled for a coin."""
    _load_all()
    coin = _cache.get(symbol)
    if not coin:
        log.warning("is_strategy_enabled: unknown symbol %s — defaulting to disabled", symbol)
        return False
    strategies = coin.get("strategies", {})
    strat_cfg = strategies.get(strategy, {})
    return strat_cfg.get("enabled", True)


def reload():
    """Force reload all coin configs (e.g. after hot-editing a coin file)."""
    import sys
    with _lock:
        _cache.clear()
        _prefix_map.clear()
        # Clear importlib cache for coin modules
        to_remove = [k for k in sys.modules
                     if k.startswith("config.coins.") and k != "config.coins.loader"]
        for k in to_remove:
            del sys.modules[k]
    _load_all()
