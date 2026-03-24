# Wallet Analysis: 0x2eb5714ff6f20f5f9f7662c556dbef5e1c9bf4d4
**Pseudonym**: Realistic-Swivel [SEEN]
**Analysis date**: 2026-03-24 17:29 UTC
**Data source**: Polymarket Data API (`/trades?user=...`), no auth needed
**Data completeness**: API max offset=3000 (hard limit). Retrieved 3,470 unique trades. All within a single session (~2.8h).
**All fields marked [SEEN] are directly from the raw API JSON response.**

## Critical Observation
**ALL 3470 trades are BUY orders.** Zero SELL orders. [SEEN]
This wallet operates a **pure BUY-both-sides arb strategy**: buy Up + Down on the same market, collect $1 at resolution, profit = $1 - combined_cost.
PnL is therefore estimated using the arb model, not traditional BUY-SELL round-trips.

## Summary
| Metric | Value |
|--------|-------|
| Total trades [SEEN] | 3470 |
| Total volume (price x size) [SEEN] | $130,069.06 |
| Total shares traded [SEEN] | 253,496 |
| Unique markets (conditionId) [SEEN] | 183 |
| Unique slugs [SEEN] | 183 |
| First trade [SEEN] | 2026-03-24 14:33:01 UTC |
| Last trade [SEEN] | 2026-03-24 17:18:55 UTC |
| Active days [SEEN] | 1 |
| Duration [SEEN] | 2.8 hours |
| Trades per hour | 1255.0 |
| Side distribution [SEEN] | BUY: 3470, SELL: 0 |
| Outcome distribution [SEEN] | Up: 1579, Down: 1849, Yes: 20, No: 22 |

## Arb PnL Model
Strategy: BUY both sides of binary market. At resolution, one side pays $1, other pays $0.
Arb PnL (gross) = matched_shares x (1 - avg_up_price - avg_down_price)

| Metric | Value |
|--------|-------|
| Both-side markets | 136 |
| Single-side markets | 47 |
| Total matched shares | 101,693 |
| Total cost (both sides) | $127,474.54 |
| Arb PnL (gross, before fees) | $-1,234.77 |
| Est. fees (2% on winnings) | -$2,033.85 |
| Arb PnL (net, after fees) | $-3,268.63 |
| ROI on cost | -0.97% gross, -2.56% net |

### Single-Side (Directional) Exposure
- Markets: 47
- Total cost: $2,594.52
- Total shares: 4,474
- Up/Yes only: 9 markets
- Down/No only: 38 markets

### Directional Excess in Both-Side Markets
- Excess Up/Yes shares: 19,244
- Excess Down/No shares: 26,394
- Balanced (excess=0) markets: 0

## Raw Trade Samples

### First 10 trades (oldest) [SEEN]
| # | Timestamp (UTC) | Side | Outcome | Price | Size | Vol ($) | Slug |
|---|----------------|------|---------|-------|------|---------|------|
| 1 | 2026-03-24 14:33:01 | BUY | Up | 0.58 | 36 | $21.01 | `sol-updown-15m-1774362600` |
| 2 | 2026-03-24 14:33:01 | BUY | Up | 0.26 | 19 | $5.04 | `xrp-updown-5m-1774362600` |
| 3 | 2026-03-24 14:33:03 | BUY | Up | 0.25 | 36 | $9.00 | `solana-up-or-down-march-24-2026-10am-et` |
| 4 | 2026-03-24 14:33:03 | BUY | Up | 0.39 | 113 | $44.08 | `bitcoin-up-or-down-march-24-2026-10am-et` |
| 5 | 2026-03-24 14:33:05 | BUY | Up | 0.41 | 56 | $22.96 | `bitcoin-up-or-down-march-24-2026-10am-et` |
| 6 | 2026-03-24 14:33:05 | BUY | Down | 0.36 | 50 | $17.86 | `btc-updown-5m-1774362600` |
| 7 | 2026-03-24 14:33:07 | BUY | Up | 0.25 | 108 | $27.00 | `solana-up-or-down-march-24-2026-10am-et` |
| 8 | 2026-03-24 14:33:07 | BUY | Up | 0.44 | 49 | $21.56 | `xrp-up-or-down-march-24-2026-10am-et` |
| 9 | 2026-03-24 14:33:07 | BUY | Down | 0.39 | 32 | $12.40 | `btc-updown-5m-1774362600` |
| 10 | 2026-03-24 14:33:11 | BUY | Up | 0.37 | 221 | $81.77 | `ethereum-up-or-down-march-24-2026-10am-et` |

### Last 10 trades (newest) [SEEN]
| # | Timestamp (UTC) | Side | Outcome | Price | Size | Vol ($) | Slug |
|---|----------------|------|---------|-------|------|---------|------|
| 3461 | 2026-03-24 17:18:11 | BUY | Up | 0.37 | 28 | $10.36 | `eth-updown-15m-1774372500` |
| 3462 | 2026-03-24 17:18:11 | BUY | Up | 0.36 | 45 | $16.20 | `btc-updown-15m-1774372500` |
| 3463 | 2026-03-24 17:18:13 | BUY | Down | 0.82 | 275 | $225.25 | `eth-updown-5m-1774372500` |
| 3464 | 2026-03-24 17:18:15 | BUY | Down | 0.77 | 126 | $97.90 | `eth-updown-5m-1774372500` |
| 3465 | 2026-03-24 17:18:33 | BUY | Down | 0.44 | 33 | $14.52 | `sol-updown-5m-1774372500` |
| 3466 | 2026-03-24 17:18:33 | BUY | Down | 0.45 | 17 | $7.52 | `xrp-updown-5m-1774372500` |
| 3467 | 2026-03-24 17:18:39 | BUY | Down | 0.55 | 64 | $35.22 | `sol-updown-5m-1774372500` |
| 3468 | 2026-03-24 17:18:39 | BUY | Up | 0.08 | 33 | $2.64 | `btc-updown-5m-1774372500` |
| 3469 | 2026-03-24 17:18:49 | BUY | Up | 0.09 | 30 | $2.67 | `eth-updown-5m-1774372500` |
| 3470 | 2026-03-24 17:18:55 | BUY | Down | 0.68 | 47 | $31.96 | `btc-updown-15m-1774372500` |

## Daily Time-Series [SEEN]
| Date | Trades | Volume ($) | Shares |
|------|--------|-----------|--------|
| 2026-03-24 | 3470 | $130,069.06 | 253,496 |

*Note: All trades fall on a single day. The wallet appears to have been active for only ~2.8 hours.*

## Hourly Distribution (UTC) [SEEN]
| Hour | Trades | % | Volume ($) | Visual |
|------|--------|---|-----------|--------|
| 00:00 | 0 | 0.0% | $0.00 |  |
| 01:00 | 0 | 0.0% | $0.00 |  |
| 02:00 | 0 | 0.0% | $0.00 |  |
| 03:00 | 0 | 0.0% | $0.00 |  |
| 04:00 | 0 | 0.0% | $0.00 |  |
| 05:00 | 0 | 0.0% | $0.00 |  |
| 06:00 | 0 | 0.0% | $0.00 |  |
| 07:00 | 0 | 0.0% | $0.00 |  |
| 08:00 | 0 | 0.0% | $0.00 |  |
| 09:00 | 0 | 0.0% | $0.00 |  |
| 10:00 | 0 | 0.0% | $0.00 |  |
| 11:00 | 0 | 0.0% | $0.00 |  |
| 12:00 | 0 | 0.0% | $0.00 |  |
| 13:00 | 0 | 0.0% | $0.00 |  |
| 14:00 | 1013 | 29.2% | $38,454.85 | ############## |
| 15:00 | 1278 | 36.8% | $51,955.54 | ################## |
| 16:00 | 852 | 24.6% | $29,050.90 | ############ |
| 17:00 | 327 | 9.4% | $10,607.77 | #### |
| 18:00 | 0 | 0.0% | $0.00 |  |
| 19:00 | 0 | 0.0% | $0.00 |  |
| 20:00 | 0 | 0.0% | $0.00 |  |
| 21:00 | 0 | 0.0% | $0.00 |  |
| 22:00 | 0 | 0.0% | $0.00 |  |
| 23:00 | 0 | 0.0% | $0.00 |  |

## Market Type Breakdown [SEEN]
| Coin | Trades | % | Volume ($) | % Vol | Shares |
|------|--------|---|-----------|-------|--------|
| BTC | 1780 | 51.3% | $83,738.91 | 64.4% | 164,215 |
| ETH | 1206 | 34.8% | $34,510.92 | 26.5% | 66,706 |
| SOL | 242 | 7.0% | $5,980.91 | 4.6% | 11,669 |
| XRP | 242 | 7.0% | $5,838.33 | 4.5% | 10,907 |

## Timeframe Breakdown [SEEN]
| Timeframe | Trades | % | Volume ($) | % Vol | Shares |
|-----------|--------|---|-----------|-------|--------|
| 5M | 2519 | 72.6% | $84,779.06 | 65.2% | 175,252 |
| Other | 951 | 27.4% | $45,290.00 | 34.8% | 78,244 |

## Coin x Timeframe Matrix (trade count) [SEEN]
| Coin | 5M | Other | Total |
|------|------|------|-------|
| BTC | 1268 | 512 | 1780 |
| ETH | 908 | 298 | 1206 |
| SOL | 178 | 64 | 242 |
| XRP | 165 | 77 | 242 |

## Coin x Timeframe Matrix (volume $) [SEEN]
| Coin | 5M | Other | Total |
|------|------|------|-------|
| BTC | $52,768 | $30,971 | $83,739 |
| ETH | $25,068 | $9,443 | $34,511 |
| SOL | $4,205 | $1,775 | $5,981 |
| XRP | $2,737 | $3,101 | $5,838 |

## Outcome Analysis [SEEN]
| Outcome | Trades | % | Volume ($) | Shares | VWAP |
|---------|--------|---|-----------|--------|------|
| Down | 1849 | 53.3% | $67,029.61 | 130,723 | 0.5128 |
| Up | 1579 | 45.5% | $61,480.24 | 120,255 | 0.5112 |
| No | 22 | 0.6% | $1,191.01 | 1,315 | 0.9055 |
| Yes | 20 | 0.6% | $368.21 | 1,203 | 0.3062 |

### BUY Price by Outcome [SEEN]
- **Up**: mean=0.4859, median=0.4900, std=0.2593, n=1579
- **Down**: mean=0.5190, median=0.5200, std=0.2298, n=1849
- **Yes**: mean=0.2915, median=0.3661, std=0.1264, n=20
- **No**: mean=0.8784, median=0.9200, std=0.0840, n=22

## Price Distribution [SEEN]
| Bucket | Trades | % | Visual |
|--------|--------|---|--------|
| 0.00-0.10 | 197 | 5.7% | ## |
| 0.10-0.20 | 247 | 7.1% | ### |
| 0.20-0.30 | 347 | 10.0% | ##### |
| 0.30-0.40 | 371 | 10.7% | ##### |
| 0.40-0.50 | 488 | 14.1% | ####### |
| 0.50-0.60 | 504 | 14.5% | ####### |
| 0.60-0.70 | 481 | 13.9% | ###### |
| 0.70-0.80 | 376 | 10.8% | ##### |
| 0.80-0.90 | 243 | 7.0% | ### |
| 0.90-1.00 | 216 | 6.2% | ### |

## Size Distribution [SEEN]
| Stat | Value |
|------|-------|
| Min | 0.0 |
| P25 | 23.0 |
| Median | 45.0 |
| Mean | 73.1 |
| P75 | 83.0 |
| Max | 1058.5 |

| Size Bucket | Trades | % |
|-------------|--------|---|
| 1-10 | 364 | 10.5% |
| 11-25 | 609 | 17.6% |
| 26-50 | 931 | 26.8% |
| 51-100 | 888 | 25.6% |
| 101-250 | 519 | 15.0% |
| 251-500 | 118 | 3.4% |
| 500+ | 41 | 1.2% |

## Both-Side Rate [SEEN]
| Metric | Value |
|--------|-------|
| Total unique markets | 183 |
| Markets with BOTH sides | 136 |
| Both-side rate | 74.3% |
| Single-side only | 47 |

## Combined Price Analysis (Both-Side Markets)
combined = avg(side_A_price) + avg(side_B_price). If < 1.00, arb is profitable before fees.

| Stat | Value |
|------|-------|
| Count | 136 |
| Mean | 1.0023 |
| Median | 0.9947 |
| Min | 0.5900 |
| Max | 1.3856 |
| Std Dev | 0.1401 |
| Combined < 1.00 (gross profit) | 70 (51.5%) |
| Combined < 0.98 (profit after ~2% fee) | 62 (45.6%) |

### Top 10 Most Profitable (lowest combined) [SEEN]
| # | Combined | Avg A | Avg B | A Sh | B Sh | Matched | Arb PnL | Title |
|---|----------|-------|-------|------|------|---------|---------|-------|
| 1 | 0.5900 | 0.5500 | 0.0400 | 8 | 25 | 8 | $+3.28 | Solana Up or Down - March 24, 11:15AM-11:20AM |
| 2 | 0.6347 | 0.1390 | 0.4957 | 546 | 178 | 178 | $+65.08 | Bitcoin Up or Down - March 24, 11:30AM-11:35A |
| 3 | 0.6577 | 0.0914 | 0.5663 | 769 | 150 | 150 | $+51.28 | Bitcoin Up or Down - March 24, 12:30PM-12:35P |
| 4 | 0.6898 | 0.2600 | 0.4298 | 19 | 51 | 19 | $+6.01 | XRP Up or Down - March 24, 10:30AM-10:35AM ET |
| 5 | 0.7381 | 0.0868 | 0.6513 | 145 | 428 | 145 | $+37.98 | Solana Up or Down - March 24, 12PM ET |
| 6 | 0.7717 | 0.4932 | 0.2786 | 102 | 147 | 102 | $+23.31 | Solana Up or Down - March 24, 12:40PM-12:45PM |
| 7 | 0.7968 | 0.2438 | 0.5530 | 842 | 727 | 727 | $+147.64 | Bitcoin Up or Down - March 24, 11:40AM-11:45A |
| 8 | 0.8264 | 0.6882 | 0.1382 | 207 | 563 | 207 | $+35.86 | Bitcoin Up or Down - March 24, 11:00AM-11:05A |
| 9 | 0.8342 | 0.3631 | 0.4711 | 1958 | 1480 | 1480 | $+245.34 | Bitcoin Up or Down - March 24, 10:55AM-11:00A |
| 10 | 0.8412 | 0.6498 | 0.1914 | 117 | 69 | 69 | $+10.96 | Solana Up or Down - March 24, 11:00AM-11:15AM |

### Top 10 Worst (highest combined) [SEEN]
| # | Combined | Avg A | Avg B | A Sh | B Sh | Matched | Arb PnL | Title |
|---|----------|-------|-------|------|------|---------|---------|-------|
| 1 | 1.3856 | 0.9456 | 0.4400 | 54 | 1 | 1 | $-0.51 | Solana Up or Down - March 24, 11:15AM-11:30AM |
| 2 | 1.3770 | 0.7477 | 0.6293 | 27 | 54 | 27 | $-10.31 | Solana Up or Down - March 24, 12:10PM-12:15PM |
| 3 | 1.3562 | 0.7562 | 0.6000 | 77 | 20 | 20 | $-7.24 | XRP Up or Down - March 24, 11:15AM-11:30AM ET |
| 4 | 1.3292 | 0.5800 | 0.7492 | 36 | 631 | 36 | $-11.92 | Solana Up or Down - March 24, 10:30AM-10:45AM |
| 5 | 1.2672 | 0.8627 | 0.4046 | 574 | 335 | 335 | $-89.52 | Ethereum Up or Down - March 24, 12:05PM-12:10 |
| 6 | 1.2500 | 0.5000 | 0.7500 | 7 | 42 | 7 | $-1.75 | Solana Up or Down - March 24, 12:35PM-12:40PM |
| 7 | 1.2463 | 0.9063 | 0.3400 | 16178 | 8387 | 8387 | $-2065.61 | Bitcoin Up or Down - March 24, 11AM ET |
| 8 | 1.2438 | 0.6391 | 0.6047 | 10 | 23 | 10 | $-2.55 | Solana Up or Down - March 24, 12:00PM-12:15PM |
| 9 | 1.2419 | 0.8372 | 0.4046 | 148 | 74 | 74 | $-17.97 | XRP Up or Down - March 24, 11:10AM-11:15AM ET |
| 10 | 1.2400 | 0.3700 | 0.8700 | 28 | 18 | 18 | $-4.32 | XRP Up or Down - March 24, 12:25PM-12:30PM ET |

### Combined Price Distribution
| Bucket | Count | % |
|--------|-------|---|
| < 0.90 | 26 | 19.1% |
| 0.90-0.95 | 20 | 14.7% |
| 0.95-0.98 | 16 | 11.8% |
| 0.98-1.00 | 8 | 5.9% |
| 1.00-1.02 | 14 | 10.3% |
| 1.02-1.05 | 13 | 9.6% |
| 1.05+ | 39 | 28.7% |

## Maker vs Taker
**Not available** — API response does not include maker/taker field. [SEEN: field absent from raw JSON]
API fields present: `asset, bio, conditionId, eventSlug, icon, name, outcome, outcomeIndex, price, profileImage, profileImageOptimized, proxyWallet, pseudonym, side, size, slug, timestamp, title, transactionHash`

## Win Rate Estimate
Cannot determine from trade data alone — resolution outcomes not in API response.
However, for both-side arb markets where combined < 1.00, the arb is guaranteed profitable regardless of outcome.
- **Guaranteed profitable arb markets**: 70 / 136
- **Guaranteed profitable after 2% fee**: 62 / 136

## Consecutive Streaks (Arb Markets)
- Longest profitable arb streak: **9** markets
- Longest losing arb streak: **7** markets

## Activity Patterns
### Day of Week [SEEN]
| Day | Trades | % |
|-----|--------|---|
| Monday | 0 | 0.0% |
| Tuesday | 3470 | 100.0% |
| Wednesday | 0 | 0.0% |
| Thursday | 0 | 0.0% |
| Friday | 0 | 0.0% |
| Saturday | 0 | 0.0% |
| Sunday | 0 | 0.0% |

### Inter-trade Timing [SEEN]
| Stat | Value |
|------|-------|
| Mean gap | 2.9s (0.0min) |
| Median gap | 2.0s (0.0min) |
| Min gap | 0s |
| Max gap | 154s (0.0h) |

### Gap Distribution [SEEN]
| Bucket | Count | % |
|--------|-------|---|
| 0s (same sec) | 1546 | 44.6% |
| 1-2s | 1044 | 30.1% |
| 3-5s | 367 | 10.6% |
| 6-15s | 398 | 11.5% |
| 16-60s | 111 | 3.2% |
| 1-5min | 3 | 0.1% |
| 5-15min | 0 | 0.0% |
| 15min+ | 0 | 0.0% |

## Top 20 Markets by Volume [SEEN]
| # | Slug | Trades | Volume ($) | Shares | Title |
|---|------|--------|-----------|--------|-------|
| 1 | `bitcoin-up-or-down-march-24-2026-11am-et` | 221 | $17,512.90 | 24,565 | Bitcoin Up or Down - March 24, 11AM ET |
| 2 | `btc-updown-15m-1774363500` | 96 | $6,614.86 | 13,005 | Bitcoin Up or Down - March 24, 10:45AM-11:00A |
| 3 | `bitcoin-up-or-down-march-24-2026-10am-et` | 142 | $6,124.63 | 12,308 | Bitcoin Up or Down - March 24, 10AM ET |
| 4 | `btc-updown-5m-1774364700` | 107 | $4,453.60 | 9,167 | Bitcoin Up or Down - March 24, 11:05AM-11:10A |
| 5 | `ethereum-up-or-down-march-24-2026-10am-e` | 130 | $4,212.18 | 8,500 | Ethereum Up or Down - March 24, 10AM ET |
| 6 | `ethereum-up-or-down-march-24-2026-11am-e` | 119 | $4,041.25 | 7,155 | Ethereum Up or Down - March 24, 11AM ET |
| 7 | `bitcoin-up-or-down-march-24-2026-12pm-et` | 72 | $3,814.79 | 8,302 | Bitcoin Up or Down - March 24, 12PM ET |
| 8 | `eth-updown-15m-1774363500` | 79 | $3,476.98 | 6,031 | Ethereum Up or Down - March 24, 10:45AM-11:00 |
| 9 | `btc-updown-5m-1774368600` | 54 | $3,024.12 | 6,251 | Bitcoin Up or Down - March 24, 12:10PM-12:15P |
| 10 | `xrp-up-or-down-march-24-2026-10am-et` | 54 | $2,373.11 | 4,397 | XRP Up or Down - March 24, 10AM ET |
| 11 | `btc-updown-5m-1774365000` | 63 | $2,304.94 | 4,519 | Bitcoin Up or Down - March 24, 11:10AM-11:15A |
| 12 | `btc-updown-5m-1774363200` | 56 | $2,259.06 | 4,460 | Bitcoin Up or Down - March 24, 10:40AM-10:45A |
| 13 | `btc-updown-5m-1774368000` | 36 | $2,164.91 | 4,708 | Bitcoin Up or Down - March 24, 12:00PM-12:05P |
| 14 | `bitcoin-up-or-down-march-24-2026-1pm-et` | 41 | $2,085.72 | 3,637 | Bitcoin Up or Down - March 24, 1PM ET |
| 15 | `btc-updown-5m-1774368300` | 48 | $2,076.88 | 4,708 | Bitcoin Up or Down - March 24, 12:05PM-12:10P |
| 16 | `btc-updown-5m-1774367100` | 40 | $2,060.00 | 4,552 | Bitcoin Up or Down - March 24, 11:45AM-11:50A |
| 17 | `eth-updown-5m-1774368600` | 62 | $1,864.53 | 3,777 | Ethereum Up or Down - March 24, 12:10PM-12:15 |
| 18 | `eth-updown-5m-1774365000` | 73 | $1,751.44 | 3,403 | Ethereum Up or Down - March 24, 11:10AM-11:15 |
| 19 | `eth-updown-15m-1774362600` | 34 | $1,478.96 | 2,565 | Ethereum Up or Down - March 24, 10:30AM-10:45 |
| 20 | `btc-updown-15m-1774366200` | 37 | $1,469.93 | 2,816 | Bitcoin Up or Down - March 24, 11:30AM-11:45A |

## Per-Market Arb PnL (All Both-Side Markets)
| # | Combined | Matched | Arb PnL | Excess A | Excess B | Trades | Slug |
|---|----------|---------|---------|----------|----------|--------|------|
| 1 | 0.8342 | 1480 | $+245.34 | 479 | 0 | 48 | `btc-updown-5m-1774364100` |
| 2 | 0.9425 | 3930 | $+226.00 | 442 | 0 | 72 | `bitcoin-up-or-down-march-24-2026-12` |
| 3 | 0.9034 | 2163 | $+209.01 | 0 | 383 | 48 | `btc-updown-5m-1774368300` |
| 4 | 0.8531 | 1339 | $+196.70 | 0 | 383 | 32 | `btc-updown-5m-1774368900` |
| 5 | 0.9116 | 2183 | $+192.97 | 186 | 0 | 40 | `btc-updown-5m-1774367100` |
| 6 | 0.8835 | 1409 | $+164.12 | 0 | 3 | 46 | `btc-updown-5m-1774365300` |
| 7 | 0.7968 | 727 | $+147.64 | 115 | 0 | 20 | `btc-updown-5m-1774366800` |
| 8 | 0.9731 | 4436 | $+119.52 | 295 | 0 | 107 | `btc-updown-5m-1774364700` |
| 9 | 0.8683 | 896 | $+117.99 | 0 | 421 | 23 | `btc-updown-5m-1774363800` |
| 10 | 0.8799 | 826 | $+99.19 | 0 | 1133 | 16 | `btc-updown-5m-1774367400` |
| 11 | 0.9707 | 3047 | $+89.40 | 0 | 157 | 54 | `btc-updown-5m-1774368600` |
| 12 | 0.9601 | 1959 | $+78.26 | 790 | 0 | 36 | `btc-updown-5m-1774368000` |
| 13 | 0.9128 | 835 | $+72.80 | 90 | 0 | 24 | `btc-updown-5m-1774369200` |
| 14 | 0.8735 | 572 | $+72.35 | 0 | 23 | 25 | `sol-updown-5m-1774371900` |
| 15 | 0.9515 | 1468 | $+71.23 | 98 | 0 | 27 | `btc-updown-5m-1774372200` |
| 16 | 0.8654 | 516 | $+69.42 | 0 | 757 | 18 | `btc-updown-15m-1774368900` |
| 17 | 0.8945 | 645 | $+68.05 | 0 | 464 | 20 | `btc-updown-5m-1774371000` |
| 18 | 0.6347 | 178 | $+65.08 | 368 | 0 | 8 | `btc-updown-5m-1774366200` |
| 19 | 0.8920 | 597 | $+64.53 | 10 | 0 | 20 | `btc-updown-5m-1774370400` |
| 20 | 0.8898 | 509 | $+56.08 | 0 | 131 | 14 | `btc-updown-15m-1774371600` |
| 21 | 0.9259 | 723 | $+53.56 | 0 | 2191 | 41 | `bitcoin-up-or-down-march-24-2026-1p` |
| 22 | 0.9108 | 580 | $+51.76 | 256 | 0 | 26 | `btc-updown-5m-1774370700` |
| 23 | 0.6577 | 150 | $+51.28 | 619 | 0 | 12 | `btc-updown-5m-1774369800` |
| 24 | 0.8793 | 375 | $+45.29 | 354 | 0 | 13 | `btc-updown-5m-1774371600` |
| 25 | 0.9066 | 484 | $+45.20 | 0 | 121 | 22 | `xrp-updown-15m-1774363500` |
| 26 | 0.9087 | 424 | $+38.73 | 0 | 259 | 12 | `btc-updown-5m-1774367700` |
| 27 | 0.9264 | 524 | $+38.57 | 155 | 0 | 16 | `btc-updown-5m-1774370100` |
| 28 | 0.9277 | 528 | $+38.19 | 0 | 117 | 22 | `ethereum-up-or-down-march-24-2026-1` |
| 29 | 0.9530 | 811 | $+38.07 | 0 | 514 | 40 | `eth-updown-5m-1774368000` |
| 30 | 0.7381 | 145 | $+37.98 | 0 | 283 | 16 | `solana-up-or-down-march-24-2026-12p` |
| 31 | 0.8264 | 207 | $+35.86 | 0 | 357 | 17 | `btc-updown-5m-1774364400` |
| 32 | 0.9391 | 584 | $+35.61 | 0 | 35 | 22 | `eth-updown-5m-1774367100` |
| 33 | 0.8643 | 252 | $+34.21 | 107 | 0 | 11 | `eth-updown-5m-1774362900` |
| 34 | 0.9406 | 574 | $+34.11 | 84 | 0 | 8 | `btc-updown-5m-1774365600` |
| 35 | 0.8786 | 277 | $+33.65 | 62 | 0 | 15 | `btc-updown-5m-1774362900` |
| 36 | 0.9720 | 1180 | $+33.05 | 0 | 287 | 24 | `btc-updown-5m-1774365900` |
| 37 | 0.9149 | 376 | $+31.99 | 285 | 0 | 13 | `eth-updown-5m-1774369500` |
| 38 | 0.9699 | 899 | $+27.03 | 268 | 0 | 26 | `btc-updown-5m-1774371900` |
| 39 | 0.9494 | 496 | $+25.09 | 632 | 0 | 25 | `eth-updown-5m-1774363500` |
| 40 | 0.7717 | 102 | $+23.31 | 0 | 45 | 4 | `sol-updown-5m-1774370400` |
| 41 | 0.9023 | 226 | $+22.12 | 0 | 406 | 21 | `ethereum-up-or-down-march-24-2026-1` |
| 42 | 0.9537 | 380 | $+17.61 | 36 | 0 | 9 | `eth-updown-5m-1774370400` |
| 43 | 0.9120 | 200 | $+17.60 | 0 | 316 | 10 | `btc-updown-15m-1774370700` |
| 44 | 0.9753 | 611 | $+15.10 | 0 | 29 | 17 | `btc-updown-5m-1774371300` |
| 45 | 0.9739 | 566 | $+14.80 | 57 | 0 | 22 | `eth-updown-5m-1774371900` |
| 46 | 0.9880 | 1224 | $+14.71 | 252 | 0 | 37 | `btc-updown-5m-1774369500` |
| 47 | 0.9871 | 967 | $+12.49 | 345 | 0 | 24 | `btc-updown-5m-1774363500` |
| 48 | 0.8412 | 69 | $+10.96 | 48 | 0 | 5 | `sol-updown-15m-1774364400` |
| 49 | 0.9870 | 821 | $+10.70 | 0 | 549 | 35 | `eth-updown-5m-1774367400` |
| 50 | 0.8939 | 98 | $+10.35 | 0 | 16 | 5 | `xrp-updown-5m-1774370400` |
| 51 | 0.9941 | 1725 | $+10.18 | 0 | 328 | 62 | `eth-updown-5m-1774368600` |
| 52 | 0.9580 | 241 | $+10.10 | 0 | 224 | 13 | `sol-updown-5m-1774363200` |
| 53 | 0.9454 | 177 | $+9.65 | 0 | 66 | 13 | `xrp-updown-15m-1774371600` |
| 54 | 0.9518 | 192 | $+9.24 | 0 | 436 | 19 | `eth-updown-5m-1774371600` |
| 55 | 0.9855 | 539 | $+7.80 | 0 | 53 | 14 | `btc-updown-5m-1774366500` |
| 56 | 0.9358 | 114 | $+7.35 | 80 | 0 | 10 | `eth-updown-5m-1774370700` |
| 57 | 0.8432 | 44 | $+6.93 | 0 | 104 | 5 | `eth-updown-5m-1774366800` |
| 58 | 0.6898 | 19 | $+6.01 | 0 | 32 | 3 | `xrp-updown-5m-1774362600` |
| 59 | 0.9316 | 82 | $+5.61 | 0 | 86 | 11 | `eth-updown-5m-1774371300` |
| 60 | 0.9646 | 151 | $+5.33 | 0 | 371 | 11 | `eth-updown-5m-1774363800` |
| 61 | 0.8961 | 33 | $+3.42 | 86 | 0 | 3 | `sol-updown-5m-1774365000` |
| 62 | 0.5900 | 8 | $+3.28 | 0 | 17 | 2 | `sol-updown-5m-1774365300` |
| 63 | 0.8432 | 19 | $+3.05 | 0 | 197 | 5 | `xrp-updown-5m-1774371600` |
| 64 | 0.9514 | 40 | $+1.93 | 269 | 0 | 9 | `eth-updown-5m-1774364400` |
| 65 | 0.9571 | 45 | $+1.93 | 0 | 63 | 4 | `btc-updown-15m-1774372500` |
| 66 | 0.9276 | 18 | $+1.30 | 0 | 107 | 5 | `xrp-updown-5m-1774363500` |
| 67 | 0.9952 | 227 | $+1.09 | 0 | 53 | 25 | `xrp-updown-5m-1774371900` |
| 68 | 0.9989 | 910 | $+1.00 | 108 | 0 | 19 | `solana-up-or-down-march-24-2026-10a` |
| 69 | 0.9772 | 28 | $+0.64 | 0 | 46 | 2 | `eth-updown-15m-1774372500` |
| 70 | 0.9891 | 15 | $+0.16 | 12 | 0 | 3 | `sol-updown-5m-1774372200` |
| 71 | 1.0000 | 15 | $+0.00 | 0 | 21 | 2 | `xrp-updown-5m-1774371000` |
| 72 | 1.0041 | 9 | $-0.04 | 0 | 541 | 9 | `btc-updown-5m-1774362600` |
| 73 | 1.0112 | 21 | $-0.23 | 66 | 0 | 3 | `sol-updown-15m-1774370700` |
| 74 | 1.0012 | 237 | $-0.29 | 46 | 0 | 19 | `eth-updown-15m-1774368900` |
| 75 | 1.0010 | 325 | $-0.33 | 0 | 375 | 21 | `eth-updown-15m-1774368000` |
| 76 | 1.0214 | 17 | $-0.36 | 0 | 99 | 6 | `xrp-updown-5m-1774363200` |
| 77 | 1.3856 | 1 | $-0.51 | 52 | 0 | 3 | `sol-updown-15m-1774365300` |
| 78 | 1.0047 | 122 | $-0.58 | 30 | 0 | 11 | `xrp-updown-15m-1774364400` |
| 79 | 1.0333 | 18 | $-0.59 | 67 | 0 | 3 | `xrp-updown-15m-1774362600` |
| 80 | 1.0076 | 126 | $-0.96 | 0 | 252 | 14 | `sol-updown-5m-1774364700` |
| 81 | 1.0800 | 18 | $-1.44 | 39 | 0 | 2 | `xrp-updown-5m-1774368300` |
| 82 | 1.2500 | 7 | $-1.75 | 0 | 35 | 2 | `sol-updown-5m-1774370100` |
| 83 | 1.0600 | 30 | $-1.83 | 13 | 0 | 2 | `eth-updown-15m-1774370700` |
| 84 | 1.0063 | 333 | $-2.09 | 12 | 0 | 9 | `eth-updown-5m-1774366500` |
| 85 | 1.2438 | 10 | $-2.55 | 0 | 12 | 5 | `sol-updown-15m-1774368000` |
| 86 | 1.1914 | 15 | $-2.89 | 18 | 0 | 2 | `xrp-updown-5m-1774365600` |
| 87 | 1.1544 | 20 | $-3.09 | 0 | 129 | 4 | `btc-updown-15m-1774369800` |
| 88 | 1.0735 | 42 | $-3.09 | 397 | 0 | 7 | `eth-updown-5m-1774370100` |
| 89 | 1.2133 | 15 | $-3.25 | 373 | 0 | 7 | `sol-updown-5m-1774364100` |
| 90 | 1.0009 | 4117 | $-3.74 | 265 | 0 | 130 | `ethereum-up-or-down-march-24-2026-1` |
| 91 | 1.0317 | 120 | $-3.82 | 12 | 0 | 6 | `will-ethereum-reach-2400-in-march-2` |
| 92 | 1.0907 | 43 | $-3.92 | 0 | 150 | 6 | `solana-up-or-down-march-24-2026-1pm` |
| 93 | 1.0310 | 131 | $-4.06 | 0 | 165 | 13 | `xrp-updown-5m-1774364100` |
| 94 | 1.2400 | 18 | $-4.32 | 10 | 0 | 2 | `xrp-updown-5m-1774369500` |
| 95 | 1.0040 | 1103 | $-4.36 | 0 | 90 | 34 | `eth-updown-5m-1774363200` |
| 96 | 1.0769 | 58 | $-4.46 | 0 | 19 | 3 | `sol-updown-15m-1774366200` |
| 97 | 1.0616 | 76 | $-4.65 | 845 | 0 | 17 | `will-bitcoin-dip-to-65k-in-march-20` |
| 98 | 1.0240 | 204 | $-4.91 | 256 | 0 | 7 | `btc-updown-15m-1774365300` |
| 99 | 1.1000 | 52 | $-5.20 | 67 | 0 | 3 | `sol-updown-5m-1774362600` |
| 100 | 1.3562 | 20 | $-7.24 | 57 | 0 | 3 | `xrp-updown-15m-1774365300` |
| 101 | 1.0094 | 780 | $-7.34 | 0 | 1005 | 34 | `eth-updown-15m-1774362600` |
| 102 | 1.2300 | 34 | $-7.82 | 0 | 28 | 2 | `eth-updown-5m-1774365600` |
| 103 | 1.1838 | 46 | $-8.45 | 0 | 80 | 7 | `eth-updown-5m-1774365900` |
| 104 | 1.0728 | 125 | $-9.06 | 0 | 329 | 8 | `eth-updown-5m-1774372500` |
| 105 | 1.2294 | 42 | $-9.74 | 0 | 9 | 4 | `sol-updown-5m-1774363500` |
| 106 | 1.0737 | 136 | $-10.00 | 0 | 56 | 11 | `eth-updown-5m-1774372200` |
| 107 | 1.2259 | 45 | $-10.08 | 0 | 46 | 6 | `xrp-updown-5m-1774368600` |
| 108 | 1.0097 | 1047 | $-10.14 | 100 | 0 | 23 | `btc-updown-5m-1774372500` |
| 109 | 1.3770 | 27 | $-10.31 | 0 | 27 | 4 | `sol-updown-5m-1774368600` |
| 110 | 1.3292 | 36 | $-11.92 | 0 | 594 | 14 | `sol-updown-15m-1774362600` |
| 111 | 1.0302 | 477 | $-14.42 | 75 | 0 | 20 | `eth-updown-5m-1774369200` |
| 112 | 1.0189 | 801 | $-15.15 | 0 | 392 | 30 | `sol-updown-15m-1774363500` |
| 113 | 1.2419 | 74 | $-17.97 | 74 | 0 | 7 | `xrp-updown-5m-1774365000` |
| 114 | 1.0242 | 747 | $-18.06 | 0 | 1088 | 57 | `eth-updown-5m-1774364100` |
| 115 | 1.0338 | 549 | $-18.53 | 537 | 0 | 35 | `eth-updown-15m-1774364400` |
| 116 | 1.0034 | 6077 | $-20.74 | 155 | 0 | 142 | `bitcoin-up-or-down-march-24-2026-10` |
| 117 | 1.0597 | 422 | $-25.18 | 0 | 102 | 20 | `eth-updown-5m-1774368900` |
| 118 | 1.0882 | 354 | $-31.28 | 0 | 169 | 11 | `btc-updown-15m-1774367100` |
| 119 | 1.0968 | 340 | $-32.88 | 0 | 148 | 23 | `solana-up-or-down-march-24-2026-11a` |
| 120 | 1.0561 | 627 | $-35.18 | 0 | 441 | 45 | `eth-updown-15m-1774366200` |
| 121 | 1.0367 | 1247 | $-45.80 | 0 | 238 | 62 | `eth-updown-5m-1774364700` |
| 122 | 1.0211 | 2245 | $-47.30 | 30 | 0 | 63 | `btc-updown-5m-1774365000` |
| 123 | 1.1781 | 301 | $-53.62 | 0 | 369 | 17 | `xrp-up-or-down-march-24-2026-11am-e` |
| 124 | 1.0342 | 1655 | $-56.55 | 0 | 93 | 73 | `eth-updown-5m-1774365000` |
| 125 | 1.0894 | 662 | $-59.19 | 0 | 1353 | 41 | `btc-updown-15m-1774368000` |
| 126 | 1.0286 | 2093 | $-59.97 | 274 | 0 | 56 | `btc-updown-5m-1774363200` |
| 127 | 1.0619 | 1270 | $-78.58 | 0 | 276 | 37 | `btc-updown-15m-1774366200` |
| 128 | 1.1790 | 456 | $-81.70 | 0 | 144 | 22 | `eth-updown-5m-1774365300` |
| 129 | 1.0955 | 932 | $-88.98 | 210 | 0 | 25 | `btc-updown-15m-1774364400` |
| 130 | 1.2672 | 335 | $-89.52 | 239 | 0 | 17 | `eth-updown-5m-1774368300` |
| 131 | 1.2017 | 489 | $-98.73 | 149 | 0 | 20 | `btc-updown-15m-1774362600` |
| 132 | 1.1148 | 1761 | $-202.20 | 0 | 876 | 54 | `xrp-up-or-down-march-24-2026-10am-e` |
| 133 | 1.0438 | 5058 | $-221.51 | 0 | 2888 | 96 | `btc-updown-15m-1774363500` |
| 134 | 1.1505 | 2743 | $-412.88 | 0 | 545 | 79 | `eth-updown-15m-1774363500` |
| 135 | 1.2152 | 2753 | $-592.61 | 0 | 1648 | 119 | `ethereum-up-or-down-march-24-2026-1` |
| 136 | 1.2463 | 8387 | $-2065.61 | 7790 | 0 | 221 | `bitcoin-up-or-down-march-24-2026-11` |
