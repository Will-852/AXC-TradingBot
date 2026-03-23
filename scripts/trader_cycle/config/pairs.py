"""
pairs.py — 交易對定義 + 產品覆蓋

2026-03-23: 改為從 config/coins/ 讀取。PairConfig dataclass 保留 backward compat。
所有 per-coin 數據嘅 source of truth 係 config/coins/{symbol}/config.py。
要改幣嘅設定 → 改嗰個 coin folder，唔好直接改呢度。
"""

from dataclasses import dataclass

from config.coins.loader import get_coin, get_all_coins, get_group_peers


@dataclass
class PairConfig:
    """Single trading pair configuration.

    Backward-compatible interface — all consumers (position_sizer, range_strategy,
    risk_manager etc.) continue using pair_cfg.vol_mult etc. without changes.
    Data sourced from config/coins/ loader.
    """
    symbol: str
    prefix: str
    group: str
    vol_mult: float = 1.0
    max_leverage: int = 20
    rsi_long: float | None = None
    rsi_short: float | None = None
    bb_touch_tol: float | None = None
    sl_mult_override: float | None = None
    price_precision: int = 2
    qty_precision: int = 3
    notes: str = ""


def _coin_to_pair_config(coin: dict) -> PairConfig:
    """Convert a coin config dict → PairConfig dataclass."""
    indicator_params = coin.get("indicator_params", {})
    return PairConfig(
        symbol=coin["symbol"],
        prefix=coin["prefix"],
        group=coin["group"],
        vol_mult=coin.get("vol_mult", 1.0),
        max_leverage=coin.get("max_leverage", 20),
        rsi_long=indicator_params.get("rsi_long"),
        rsi_short=indicator_params.get("rsi_short"),
        bb_touch_tol=indicator_params.get("bb_touch_tol"),
        sl_mult_override=coin.get("sl_mult_override"),
        price_precision=coin.get("price_precision", 2),
        qty_precision=coin.get("qty_precision", 3),
        notes=coin.get("notes", ""),
    )


# ─── Build PAIR_CONFIGS from coin loader ───
PAIR_CONFIGS: dict[str, PairConfig] = {
    symbol: _coin_to_pair_config(cfg)
    for symbol, cfg in get_all_coins().items()
}


def get_pair(symbol: str) -> PairConfig:
    """Get pair config by symbol. Raises KeyError if not found."""
    return PAIR_CONFIGS[symbol]


def get_all_symbols() -> list[str]:
    """Get all active trading pair symbols."""
    return list(PAIR_CONFIGS.keys())


def get_group_symbols(group: str) -> list[str]:
    """Get all symbols in a position group."""
    return [s for s, p in PAIR_CONFIGS.items() if p.group == group]
