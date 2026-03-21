"""
context.py — PolyContext: 貫穿 Polymarket pipeline 嘅數據容器

同 trader_cycle CycleContext 同樣 pattern，但 field 針對預測市場：
- 冇 leverage/margin/SL/TP
- 有 edge assessment、概率、market metadata
- 持倉模型唔同：shares of outcome tokens，唔係 perp positions
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PolyMarket:
    """Single Polymarket market snapshot (from Gamma API)."""
    condition_id: str = ""
    title: str = ""
    description: str = ""
    category: str = ""              # "crypto" / "crypto_15m"
    end_date: str = ""
    yes_token_id: str = ""
    no_token_id: str = ""
    yes_price: float = 0.0         # current market probability
    no_price: float = 0.0
    volume: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    spread: float = 0.0            # bid-ask spread
    book_depth: float = 0.0        # total book depth (USDC)
    slug: str = ""
    outcomes: list[str] = field(default_factory=list)
    outcome_prices: dict[str, float] = field(default_factory=dict)
    outcome_tokens: dict[str, str] = field(default_factory=dict)
    neg_risk: bool = False
    event_id: str = ""               # parent event (for logical arb grouping)
    event_slug: str = ""
    tick_size: float = 0.01
    min_order_size: float = 5.0


@dataclass
class EdgeAssessment:
    """AI probability assessment for a market."""
    condition_id: str = ""
    title: str = ""
    category: str = ""
    market_price: float = 0.0       # current Yes price (market probability)
    ai_probability: float = 0.0     # AI estimated probability
    edge: float = 0.0               # ai_probability - market_price (positive = buy Yes)
    edge_pct: float = 0.0           # |edge| as percentage
    confidence: float = 0.0         # 0-1, AI self-assessed confidence
    side: str = ""                  # "YES" or "NO"
    reasoning: str = ""             # AI 解釋
    data_sources: list[str] = field(default_factory=list)  # what data informed the assessment
    signal_source: str = ""  # "indicator" / "cvd" / "ai" — which strategy produced this
    # ─── GTO fields (populated by GTOFilterStep) ───
    gto_type: str = ""
    adverse_selection_score: float = 0.0
    nash_equilibrium_score: float = 0.0
    unexploitability_score: float = 0.0
    fill_quality: str = ""
    gto_approved: bool = True
    gto_order_type: str = "LIMIT"
    gto_limit_offset: float = 0.0
    gto_reasoning: str = ""
    is_dominant_strategy: bool = False


@dataclass
class PolySignal:
    """Trading signal for a Polymarket position."""
    condition_id: str = ""
    title: str = ""
    category: str = ""
    side: str = ""                  # "YES" or "NO"
    token_id: str = ""              # token to buy
    price: float = 0.0              # current price of the token
    edge: float = 0.0               # expected edge
    confidence: float = 0.0         # AI confidence
    bet_size_usdc: float = 0.0      # Kelly-sized bet in USDC
    kelly_fraction: float = 0.0     # raw Kelly fraction before sizing
    reasoning: str = ""
    signal_source: str = ""  # "indicator" / "cvd" / "ai"
    # ─── GTO fields (populated by GTOFilterStep) ───
    gto_type: str = ""
    adverse_selection_score: float = 0.0
    unexploitability_score: float = 0.0
    gto_order_type: str = "LIMIT"
    gto_limit_offset: float = 0.0
    is_dominant_strategy: bool = False


@dataclass
class PolyPosition:
    """Current open position in a Polymarket market."""
    condition_id: str = ""
    title: str = ""
    category: str = ""
    side: str = ""                  # "YES" or "NO"
    token_id: str = ""
    shares: float = 0.0             # number of shares held
    avg_price: float = 0.0          # average entry price
    current_price: float = 0.0      # current market price
    cost_basis: float = 0.0         # total USDC spent
    market_value: float = 0.0       # shares × current_price
    unrealized_pnl: float = 0.0     # market_value - cost_basis
    unrealized_pnl_pct: float = 0.0
    entry_time: str = ""
    end_date: str = ""              # market resolution date
    # ─── Hyperliquid Hedge ───
    hedge_side: str = ""            # "LONG" or "SHORT" (empty = no hedge)
    hedge_size: float = 0.0         # HL position qty (coins)
    hedge_entry_px: float = 0.0     # HL entry price

    @property
    def probability_drift(self) -> float:
        """How much has probability moved since entry."""
        return self.current_price - self.avg_price


@dataclass
class PolyContext:
    """Central data object for Polymarket pipeline.

    Created at cycle start, mutated by each step, finalized at end.
    Same pattern as trader_cycle CycleContext.
    """
    # ─── Meta ───
    cycle_id: int = 0
    timestamp: datetime | None = None
    timestamp_str: str = ""
    dry_run: bool = True
    verbose: bool = False

    # ─── State (from POLYMARKET_STATE.json) ───
    state: dict = field(default_factory=dict)

    # ─── Account ───
    usdc_balance: float = 0.0
    total_exposure: float = 0.0      # sum of all position cost_basis
    exposure_pct: float = 0.0        # total_exposure / usdc_balance

    # ─── Markets (from Gamma API scan) ───
    scanned_markets: list[PolyMarket] = field(default_factory=list)
    filtered_markets: list[PolyMarket] = field(default_factory=list)  # after category + quality filter

    # ─── Positions ───
    open_positions: list[PolyPosition] = field(default_factory=list)

    # ─── Logical Arbitrage ───
    arb_opportunities: list = field(default_factory=list)  # list[ArbOpportunity]

    # ─── Edge Finding (AI) ───
    edge_assessments: list[EdgeAssessment] = field(default_factory=list)

    # ─── Signals ───
    signals: list[PolySignal] = field(default_factory=list)

    # ─── Position Management ───
    exit_signals: list = field(default_factory=list)  # list[ExitSignal] from position_manager

    # ─── Risk ───
    risk_blocked: bool = False
    entry_blocked: bool = False       # soft block: allow exits but no new entries (cooldown)
    risk_reasons: list[str] = field(default_factory=list)
    daily_pnl: float = 0.0
    circuit_breaker_active: bool = False
    cooldown_until: datetime | None = None

    # ─── Execution ───
    exchange_client: Any = None       # PolymarketClient instance
    gamma_client: Any = None          # GammaClient instance
    hl_hedge_client: Any = None       # HLHedgeClient instance (Phase 3)
    executed_trades: list[dict] = field(default_factory=list)

    # ─── Outputs ───
    state_updates: dict = field(default_factory=dict)
    telegram_messages: list[str] = field(default_factory=list)

    # ─── WAL ───
    wal: Any = None                   # WriteAheadLog instance

    # ─── GTO ───
    gto_assessments: dict = field(default_factory=dict)  # condition_id → GTOAssessment
    gto_blocked_count: int = 0

    # ─── Error Tracking ───
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
