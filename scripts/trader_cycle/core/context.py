"""
context.py — CycleContext: 貫穿整個 pipeline 嘅數據容器
每個 step 讀取 + 寫入 context，避免 global state
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class MarketSnapshot:
    """Single pair's market data snapshot."""
    symbol: str
    price: float = 0.0
    price_change_24h_pct: float = 0.0
    volume_24h: float = 0.0
    funding_rate: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0


@dataclass
class Signal:
    """Trading signal from strategy evaluation."""
    pair: str                       # "BTCUSDT"
    direction: str                  # "LONG" or "SHORT"
    strategy: str                   # "range" or "trend"
    strength: str                   # "STRONG" or "WEAK"
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float | None = None
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0             # numeric strength for ranking (may include boosts)
    original_score: float = 0.0    # pre-boost score for position sizing
    # Phase 3 — populated by SizePositionStep for ExecuteTradeStep
    position_size_qty: float = 0.0  # quantity in base asset (e.g., 0.003 BTC)
    position_notional: float = 0.0  # notional value in USDT
    margin_required: float = 0.0    # margin = notional / leverage
    leverage: int = 0               # leverage used for this signal
    platform: str = "aster"         # "aster" or "binance"


@dataclass
class Position:
    """Current open position."""
    pair: str
    direction: str                  # "LONG" / "SHORT"
    entry_price: float = 0.0
    mark_price: float = 0.0
    size: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    entry_time: datetime | None = None
    unrealized_pnl: float = 0.0
    funding_cost: float = 0.0
    platform: str = "aster"         # "aster", "binance", or "hyperliquid"


@dataclass
class ClosedPosition:
    """Record of a position closed during this cycle."""
    pair: str
    direction: str
    entry_price: float = 0.0
    exit_price: float = 0.0
    size: float = 0.0
    pnl: float = 0.0
    reason: str = ""
    timestamp: str = ""
    commission: float = 0.0        # fee paid on close


@dataclass
class OrderResult:
    """Result from order execution."""
    success: bool = False
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    price: float = 0.0
    quantity: float = 0.0
    error: str = ""
    # Fee & slippage tracking (Sprint 1B)
    commission: float = 0.0        # total fee paid (USDT)
    commission_asset: str = "USDT"
    slippage_pct: float = 0.0      # direction-aware: positive = unfavourable
    signal_price: float = 0.0      # intended entry price from signal


@dataclass
class CycleContext:
    """
    Central data object flowing through the pipeline.
    Created at cycle start, mutated by each step, finalized at end.
    """
    # ─── Meta ───
    cycle_id: int = 0
    timestamp: datetime | None = None
    timestamp_str: str = ""
    mode: str = "FULL"              # "FULL" or "FAST"
    dry_run: bool = True
    verbose: bool = False

    # ─── State (read from files at start) ───
    scan_config: dict = field(default_factory=dict)
    trade_state: dict = field(default_factory=dict)

    # ─── Market Data ───
    market_data: dict[str, MarketSnapshot] = field(default_factory=dict)
    indicators: dict[str, dict] = field(default_factory=dict)
    news_sentiment: dict = field(default_factory=dict)  # from shared/news_sentiment.json
    # indicators = {"BTCUSDT": {"4h": {...}, "1h": {...}}, ...}

    # ─── Mode Detection ───
    market_mode: str = "UNKNOWN"     # "RANGE", "TREND", "UNKNOWN"
    mode_votes: dict[str, str] = field(default_factory=dict)
    # mode_votes = {"RSI": "RANGE", "MACD": "TREND", ...}
    mode_confirmed: bool = False
    prev_mode: str = "UNKNOWN"
    prev_mode_cycles: int = 0

    # ─── Risk ───
    risk_blocked: bool = False
    risk_reasons: list[str] = field(default_factory=list)
    no_trade_reasons: list[str] = field(default_factory=list)
    cooldown_active: bool = False
    cooldown_ends: datetime | None = None

    # ─── Positions (from exchange) ───
    open_positions: list[Position] = field(default_factory=list)
    account_balance: float = 0.0
    available_margin: float = 0.0

    # ─── Strategy ───
    signals: list[Signal] = field(default_factory=list)
    selected_signal: Signal | None = None

    # ─── Execution (Phase 3) ───
    exchange_client: Any = None      # AsterClient instance (injected in --live mode)
    exchange_clients: dict = field(default_factory=dict)  # {"aster": ..., "binance": ..., "hyperliquid": ...}
    order_result: OrderResult | None = None
    entry_order_id: str = ""
    sl_order_id: str = ""
    tp_order_id: str = ""
    tp2_order_id: str = ""

    # ─── Outputs (written at end) ───
    scan_config_updates: dict = field(default_factory=dict)
    trade_state_updates: dict = field(default_factory=dict)
    trade_log_entry: str | None = None
    trade_log_entries: list[str] = field(default_factory=list)
    closed_positions: list[ClosedPosition] = field(default_factory=list)
    scan_log_entry: str = ""
    telegram_messages: list[str] = field(default_factory=list)

    # ─── Re-entry (from AdjustPositionsStep early exit) ───
    reentry_eligible: bool = False
    reentry_pair: str = ""
    reentry_direction: str = ""

    # ─── Error Tracking ───
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
