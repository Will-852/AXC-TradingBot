# 7 Profitable Wallets — Reverse Engineering Report
> Date: 2026-03-23 | All wallets confirmed +PnL

## Summary Table

| # | Address | PnL | Volume | ROI | Markets | Mkts/Day | Daily PnL | Timeframe | Strategy |
|---|---------|-----|--------|-----|---------|----------|-----------|-----------|----------|
| 1 | 0x63ce..9a9a | **$2,380,911** | $196.1M | 1.21% | 33,461 | 307 | $21,843 | 5M+15M+1H | Both-sides MM, multi-TF |
| 2 | 0x29bc..683c | **$357,920** | $52.7M | 0.68% | 18,700 | 170 | $3,254 | 5M+15M | Both-sides MM, aggressive spread |
| 3 | 0xd0d6..93aa | **$1,717,532** | $205.6M | 0.84% | 53,212 | 591 | $19,084 | 5M+15M+1H | Both-sides + active mgmt |
| 4 | 0x818f..58cb | **$433,363** | $58.5M | 0.74% | 30,482 | ~180 | $25,492* | 5M+15M+1H | Both-sides + directional lean |
| 5 | 0x2eb5..b5d4 | **$243,207** | $50.1M | 0.49% | 19,002 | ~210 | $2,702 | 5M+15M+Monthly | Both-sides MM + range bets |
| 6 | 0x70ec..b5d | **$390,038** | $30.9M | 1.26% | 29,215 | 790 | $10,541 | **5M only** | Pure 5M MM |
| 7 | 0x1f0e..f86 | **$323,462** | $74.1M | 0.44% | 15,474 | 645 | $13,477 | 15M+5M+4H | Binary arb + directional |

*Wallet 4 active 17 months, daily PnL is lifetime average.

## Key Patterns (ALL 7 wallets)

### 1. ALL are both-sides
Every single profitable wallet buys BOTH Up AND Down on the same market.
- 0/7 are pure directional
- Combined entry cost < $1.00 = structural edge

### 2. ROI is thin (0.44% - 1.26%)
Nobody makes big money per trade. Profit = volume × tiny edge.

### 3. Volume is the multiplier
| Metric | Min | Max | Median |
|--------|-----|-----|--------|
| Markets/day | 170 | 790 | 307 |
| Daily volume | ~$500K | ~$2.3M | ~$835K |
| Daily PnL | $2,702 | $21,843 | $10,541 |

### 4. Multi-timeframe is common (6/7 wallets)
Only Wallet 6 is pure 5M. All others play 2-3 timeframes:
- 5M: high frequency, small size ($200-$2K/market)
- 15M: medium frequency, medium size ($2K-$5K/market)
- 1H: low frequency, large size ($2K-$8K/market)

### 5. Entry price patterns
| Timeframe | Typical Combined | Edge/share |
|-----------|-----------------|------------|
| 5M | $0.82 - $0.99 | $0.01 - $0.18 |
| 15M | $0.90 - $0.96 | $0.04 - $0.10 |
| 1H | $0.97 - $1.00 | $0.00 - $0.03 |

5M has WIDER spreads (more edge per trade) but smaller position sizes.

### 6. Active management vs hold-to-resolution: MIXED
- Wallets 2, 5, 6: mostly hold to resolution (few sells)
- Wallets 1, 3: active management (buy + sell within market, flipping)
- Wallet 7: buy only, hold to resolution
- **BOTH approaches work** — contradicts our earlier "zero management" thesis

### 7. All are bots
Every wallet shows: sub-second trade clustering, 24/7 operation, dozens of fills per market.

## Wallet 6 (Pure 5M) — Our Best Blueprint

**Why this wallet matters most:**
- ONLY 5M, no 15M or 1H
- 790 markets/day = highest frequency
- 1.26% ROI = highest efficiency
- $390K in 37 days from ~$50K start
- $10.5K/day

**What Wallet 6 does:**
- BTC + ETH + SOL + XRP on 5M
- Both-sides on every market
- Buy at $0.03-0.40 (cheap side) + $0.60-0.97 (expensive side)
- Rapid-fire fills (29 buys + 12 sells in ~60 seconds)
- Average $1.06 per individual fill (very small trades)

## Wallet 7 (15M confirmed) — Comparison

| Metric | Wallet 6 (5M) | Wallet 7 (15M) |
|--------|--------------|----------------|
| PnL | $390K | $323K |
| Volume | $30.9M | $74.1M |
| ROI | **1.26%** | 0.44% |
| Markets | 29,215 | 15,474 |
| Mkts/day | **790** | 645 |
| Avg $/mkt | $1,058 | $4,793 |

**5M is more capital-efficient (2.9x better ROI) but lower per-market sizing.**
**15M allows bigger positions but thinner margins.**

## Implications for Us ($253 bankroll)

### The good news:
- Strategy is validated: both-sides works across 7 independent wallets
- 5M exists and has WIDER spreads than 15M (more edge per trade)
- Fees are identical (maker = 0, same rebates)
- No directional prediction needed

### The bad news:
- Minimum viable volume: ~170 markets/day (Wallet 2, lowest)
- Our current fill rate (~2.5% both-fill) would need to improve 30-50x
- All wallets use automated execution with sub-second latency
- $253 can deploy ~$5/market → need 40+ markets/day just for $1/day

### The critical unknown:
**Fill rate at our price levels.** All these wallets get filled because they have:
1. Queue priority (larger orders, longer history)
2. Speed (sub-100ms order management)
3. Volume (market makers get preferential fill rates)

We have none of these. Paper test running now to validate.
