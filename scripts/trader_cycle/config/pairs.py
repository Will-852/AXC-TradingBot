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
    # ─── 2-Zone SL scaling (2026-03-23) ───
    # vol_mult: 相對 BTC 嘅 2H volatility 比例。SL = profile.sl_pct_base × vol_mult。
    # BTC=1.0, ETH=1.5, XRP=2.0 etc. 數字來自歷史 2H realized vol 比較。
    vol_mult: float = 1.0
    # max_leverage: per-coin 硬上限（override zone 嘅 leverage）
    max_leverage: int = 20
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
        vol_mult=1.0, max_leverage=20,
        price_precision=1, qty_precision=3,
        notes="Baseline vol。最可靠，優先分析",
    ),
    "ETHUSDT": PairConfig(
        symbol="ETHUSDT", prefix="ETH",
        group="crypto_correlated",
        vol_mult=1.5, max_leverage=20,
        rsi_long=32, rsi_short=68,
        price_precision=2, qty_precision=2,
        notes="1.5x BTC vol。跟隨 BTC",
    ),
    "XRPUSDT": PairConfig(
        symbol="XRPUSDT", prefix="XRP",
        group="crypto_independent",
        vol_mult=2.0, max_leverage=15,
        bb_touch_tol=0.008, sl_mult_override=1.0,
        price_precision=4, qty_precision=0,
        notes="2x BTC vol → max 15x lev。獨立走勢",
    ),
    "SOLUSDT": PairConfig(
        symbol="SOLUSDT", prefix="SOL",
        group="crypto_correlated",
        vol_mult=1.9, max_leverage=15,
        price_precision=2, qty_precision=0,
        notes="1.9x BTC vol。Binance only",
    ),
    "POLUSDT": PairConfig(
        symbol="POLUSDT", prefix="POL",
        group="crypto_independent",
        vol_mult=2.0, max_leverage=15,
        price_precision=7, qty_precision=0,
        notes="2x BTC vol。Binance only; 同 XRP 一組",
    ),
    "XAGUSDT": PairConfig(
        symbol="XAGUSDT", prefix="XAG",
        group="commodity",
        vol_mult=0.47, max_leverage=10,
        price_precision=2, qty_precision=3,
        notes="0.47x BTC vol → Zone A only（max_lev 10x < Zone B 下限 11x）。Asia+London only",
    ),
    "XAUUSDT": PairConfig(
        symbol="XAUUSDT", prefix="XAU",
        group="commodity",
        vol_mult=0.31, max_leverage=10,
        price_precision=2, qty_precision=2,
        notes="0.31x BTC vol → Zone A only（max_lev 10x < Zone B 下限 11x）。Aster only",
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
