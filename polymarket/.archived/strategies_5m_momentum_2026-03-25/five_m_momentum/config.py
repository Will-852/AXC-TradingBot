"""
5M Momentum Strategy — All tuneable parameters in one place.

Change parameters here. Code reads from here. Never hardcode in logic files.

Three-tier decision tree (from 12h live data + 30-day backtest + BMD):
  < 8bps  → SKIP (WR too low, edge < break-even)
  8-15bps → MAKER_ARB (both sides, combined < $0.98, guaranteed arb)
  > 15bps → TAKER_DIRECTIONAL (aggressive limit lean + optional maker hedge)

Data sources:
  - 30-day momentum WR: analysis/data/momentum_wr_results.json
  - 12h live data: logs/mm_trades_5m.jsonl (191 trades, WR 35.1%, lean acc 58.1%)
  - Wallet RE: analysis/wallet_0x{b27b,d1eb,910e,2eb5,c173}.md
  - Fee structure: Polymarket docs (current + 2026-03-30 change)
  - 5M spread: Uncommon-Oat data (combined 0.80-0.98, wider than 15M 0.84-0.96)
"""

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Entry timing
# ---------------------------------------------------------------------------

ENTRY_DELAY_S = 45          # Seconds after window open to read signal
                            # Data: T+45s 8bps = 76% WR (n=1432)
                            # Trade-off: faster = worse WR but better fill price
WINDOW_DURATION_S = 300     # 5M = 300s
ENTRY_CYCLE_S = 2           # Main loop cycle (was 5s, reduced for taker speed)
SCAN_INTERVAL_S = 60        # Market discovery interval
WINDOW_GIVE_UP_PCT = 0.80   # Skip if > 80% of window elapsed


# ---------------------------------------------------------------------------
# Mode thresholds (momentum in bps)
#
# Three tiers based on 30-day data + 12h live validation:
#   T+45s 8bps  = 76.0% WR (n=1432) → maker arb viable if combined < $0.98
#   T+45s 15bps = 83.2% WR (n=340)  → taker directional viable (BE ~56%)
#   T+45s 20bps = 83.6% WR (n=165)  → stronger signal, same mode
#
# 5M σ ≈ 0.16% = 16bps. So 8bps = 0.5σ, 15bps ≈ 1σ.
# ---------------------------------------------------------------------------

SKIP_BELOW_BPS = 8          # |momentum| < 8bps → SKIP
ARB_UPPER_BPS = 15          # 8-15bps → MAKER_ARB (both sides)
                            # > 15bps → TAKER_DIRECTIONAL (lean + optional hedge)


# ---------------------------------------------------------------------------
# Mode A: MAKER_ARB — both sides, guaranteed arb
#
# 5M spreads wider than 15M (Uncommon-Oat: 5M combined 0.80-0.98).
# At low momentum (8-15bps), CLOB hasn't fully repriced → maker bids
# can fill at combined < $1.00 → guaranteed profit regardless of direction.
#
# 5+5 shares = zero directional edge (BMD proven).
# Edge comes purely from combined < $1.00, not from direction.
# ---------------------------------------------------------------------------

ARB_MAX_COMBINED = 0.98     # Hard cap. combined ≥ this → skip arb mode.
                            # Data: 5M combined 0.80-0.98 range (Uncommon-Oat)
                            # Tighter than old 0.95 — capture wider 5M spreads.
ARB_SPREAD_FROM_MID = 0.025 # Bid at mid - 2.5¢ (inside Polymarket rewards zone ±4.5¢)
ARB_MAX_PRICE = 0.55        # Don't bid above this for either side
ARB_MIN_SHARES = 5          # Polymarket CLOB minimum per side


# ---------------------------------------------------------------------------
# Mode B: TAKER_DIRECTIONAL — aggressive limit lean + optional maker hedge
#
# For strong signals (>15bps), maker lean doesn't work (momentum paradox:
# lean bid doesn't fill because everyone wants same side).
# Fix: aggressive GTC limit at ask + buffer = near-instant fill.
#
# Hedge is OPTIONAL bonus:
#   Lean fills + hedge fills → combined ~$0.97 → arb + direction
#   Lean fills + hedge no fill → single-side → 58% WR > 55.8% BE ✅
#
# FOK trap: SDK create_market_order(price=0) is NOT a true market order.
#   It fetches book via REST, calculates worst price, submits GTC+FOK.
#   Thin book + 200ms lag = "no match" kill.
# Fix: aggressive GTC limit at ask + 2¢ (stays on book if not instant fill).
# ---------------------------------------------------------------------------

TAKER_ASK_BUFFER = 0.02     # Bid at ask + 2¢ (absorb 200ms book movement)
TAKER_ASK_CAP = 0.55        # Hard cap. ask > this → skip (CLOB already repriced)
                            # Data: at $0.55 fill, 58% lean acc → EV +$0.03/share
                            # at $0.60 fill → EV ≈ 0 (no edge)
TAKER_MIN_SHARES = 5        # Polymarket CLOB minimum

# Optional maker hedge (placed after lean fill confirmed)
HEDGE_SPREAD_FROM_MID = 0.025  # Hedge bid at mid - 2.5¢
HEDGE_MAX_PRICE = 0.50         # Don't bid hedge above this
HEDGE_ENABLED = True           # Set False to run pure single-side


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------

BET_SIZE_USD = 5.0           # Per trade (all modes). Minimum viable at $200 bankroll.
MIN_ORDER_SHARES = 5         # Polymarket CLOB minimum (can't go lower)
BANKROLL_USD = 200.0         # Current (updated from $131 after deposit)
                            # ⚠️ RISK: 手動更新。SESSION_MAX_LOSS 唔會跟住 scale。
                            # 考慮改做 SESSION_MAX_LOSS = BANKROLL_USD * 0.30


# ---------------------------------------------------------------------------
# Risk / Session stops
#
# Max loss = 30% of bankroll = $60.
# At $5/trade worst case: 12 losing trades.
# At 42% loss rate (single-side): ~29 trades expected before $60 cap.
# ---------------------------------------------------------------------------

SESSION_MAX_LOSS = 60.0      # Stop ALL trading if cumulative loss ≥ this
MAX_CONSECUTIVE_LOSSES = 5   # Stop taker_directional after N consecutive losses
                            # Arb mode continues (arb = guaranteed profit if fills)
DAILY_LOSS_CAP = 40.0        # Max daily loss (reset at 00:00 UTC)
                            # ⚠️ RISK: 定義咗但冇 enforcement 邏輯。Phase 4 加 daily reset。


# ---------------------------------------------------------------------------
# Fee reference (DO NOT use in calculations — fetch from Polymarket)
#
# Current (2026-03-25): fee = C * p * 0.25 * (p*(1-p))^2
#   p=0.50 → 1.56%,  p=0.55 → 1.53%
#
# After 2026-03-30:   fee = C * p * 0.072 * (p*(1-p))^1
#   p=0.50 → 1.80%,  p=0.55 → 1.78%
#
# Maker fee = 0%. Maker rebate = 20% of taker fees.
# ---------------------------------------------------------------------------

TAKER_FEE_ESTIMATE = 0.0153  # For EV logging only. NOT used in order logic.
                            # ⚠️ RISK: 2026-03-30 後 fee 結構改變，呢個值會過時。
                            # 屆時改做 0.0178。


# ---------------------------------------------------------------------------
# Regime filter (Phase 2 — not yet wired)
#
# Low vol = momentum signals less reliable.
# When implemented: low vol → downgrade one tier (directional→arb, arb→skip).
# ---------------------------------------------------------------------------

VOL_LOOKBACK_HOURS = 24      # Trailing window for realized vol
VOL_LOW_PERCENTILE = 15      # Below P15 = low vol → downgrade
# NOT YET ACTIVE — marked for future implementation.


# ---------------------------------------------------------------------------
# Coins
# ---------------------------------------------------------------------------

@dataclass
class CoinConfig:
    symbol: str               # Binance symbol (BTCUSDT)
    slug_prefix: str          # Polymarket slug prefix (btc)
    live: bool = False        # True = execute orders, False = paper only
    entry_delay_s: int = ENTRY_DELAY_S

COINS = {
    "btc": CoinConfig(symbol="BTCUSDT", slug_prefix="btc", live=True),
    # ⚠️ RISK: live=True default。--live flag 即落真錢。冇 balance gate。
    "eth": CoinConfig(symbol="ETHUSDT", slug_prefix="eth", live=False),
    "sol": CoinConfig(symbol="SOLUSDT", slug_prefix="sol", live=False),
    "xrp": CoinConfig(symbol="XRPUSDT", slug_prefix="xrp", live=False),
}


# ---------------------------------------------------------------------------
# Experiment tracking
# ---------------------------------------------------------------------------

EXPERIMENT_TRADE_LIMIT = 20   # Stop after N trades (Phase 1 experiment)
                              # After 20 trades: analyze fill rate + WR → decide next step


# ---------------------------------------------------------------------------
# Backwards-compatible aliases (used by run.py until Phase 2-4 rewrite)
# DELETE after Phase 4 complete.
# ---------------------------------------------------------------------------

ARB_COMBINED_MAX = ARB_MAX_COMBINED        # old name → new name
MAX_COMBINED_COST = ARB_MAX_COMBINED       # old name → new name
SPREAD_FROM_MID = ARB_SPREAD_FROM_MID      # old name → new name
LEAN_RATIO = 1.0                           # arb = equal sides (old default)
SINGLE_SIDE_MAX_FILL = TAKER_ASK_CAP      # old name → new name
SINGLE_SIDE_ABOVE_BPS = ARB_UPPER_BPS     # old name → new name
SESSION_DRAWDOWN_STOP_ALL = SESSION_MAX_LOSS
SESSION_DRAWDOWN_STOP_SINGLE = SESSION_MAX_LOSS * 0.33  # $20 for directional
MAX_LEAN_FILL_PRICE = ARB_MAX_PRICE
MAX_HEDGE_FILL_PRICE = HEDGE_MAX_PRICE
EXPERIMENT_PHASE = 2                       # legacy
