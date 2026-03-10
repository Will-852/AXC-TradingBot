"""
pairs.py — 交易對定義 + 產品覆蓋
加新 pair 只需加一個 entry
"""

from dataclasses import dataclass, field


@dataclass
class PairConfig:
    """Single trading pair configuration."""
    symbol: str                 # "BTCUSDT"
    prefix: str                 # "BTC"
    group: str                  # "crypto_correlated" / "crypto_independent" / "commodity"
    # Indicator overrides (None = use default from TIMEFRAME_PARAMS)
    rsi_long: float | None = None
    rsi_short: float | None = None
    bb_touch_tol: float | None = None
    sl_mult_override: float | None = None
    # Precision
    price_precision: int = 2     # decimal places for price
    qty_precision: int = 3       # decimal places for quantity
    # Notes
    notes: str = ""


# ─── Pair Registry ───
PAIR_CONFIGS: dict[str, PairConfig] = {
    "BTCUSDT": PairConfig(
        symbol="BTCUSDT", prefix="BTC",
        group="crypto_correlated",
        price_precision=1, qty_precision=3,
        notes="最可靠，優先分析",
    ),
    "ETHUSDT": PairConfig(
        symbol="ETHUSDT", prefix="ETH",
        group="crypto_correlated",
        rsi_long=32, rsi_short=68,
        price_precision=2, qty_precision=2,
        notes="跟隨 BTC",
    ),
    "XRPUSDT": PairConfig(
        symbol="XRPUSDT", prefix="XRP",
        group="crypto_independent",
        bb_touch_tol=0.008, sl_mult_override=1.0,
        price_precision=4, qty_precision=0,
        notes="獨立走勢",
    ),
    "SOLUSDT": PairConfig(
        symbol="SOLUSDT", prefix="SOL",
        group="crypto_correlated",
        price_precision=2, qty_precision=0,
        notes="Trend 5W/2L in 180d backtest; 已喺 Binance+HL scanner",
    ),
    "POLUSDT": PairConfig(
        symbol="POLUSDT", prefix="POL",
        group="crypto_independent",
        price_precision=7, qty_precision=0,
        notes="Binance only; 同 XRP 一組",
    ),
    "XAGUSDT": PairConfig(
        symbol="XAGUSDT", prefix="XAG",
        group="commodity",
        price_precision=2, qty_precision=3,
        notes="Silver, scalp 只限 Asia+London; 先查 XAUUSD 方向",
    ),
    "XAUUSDT": PairConfig(
        symbol="XAUUSDT", prefix="XAU",
        group="commodity",
        price_precision=2, qty_precision=2,
        notes="Gold, Aster only; XAG 參考方向用",
    ),
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
