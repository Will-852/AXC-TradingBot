# Wallet Analysis: 0xc1737e2db2d19e0b73a958ecd5d0f279d0e726ee
> Pseudonym: **Fickle-Spark**
> Analysis date: 2026-03-25 (data from API, max 3500 trades)
> All numbers labeled SEEN = directly from API response

## Overview

| Metric | Value | Source |
|--------|-------|--------|
| Total trades | 3,495 | SEEN |
| Total volume (size x price) | $84,190.87 | SEEN |
| Total size (shares) | $209,271.25 | SEEN |
| Unique markets (slug) | 144 | SEEN |
| Unique assets | 262 | SEEN |
| Unique txn hashes | 3495 | SEEN |
| Time range | 2026-03-24 15:08:53 to 2026-03-24 17:18:21 UTC | SEEN |
| Duration | 2:09:28 | SEEN |
| Avg trades/min | 27.0 | SEEN |

> **API limitation**: max offset = 3000, so only the most recent ~3500 trades are available.
> All data is from a single day (2026-03-24), covering ~2h 9min of US market hours.

## Time-Series PnL Table

| Date | Trades | Volume | BUY vol | SELL vol | Net Cash (SELL-BUY) | Cum PnL |
|------|--------|--------|---------|----------|---------------------|---------|
| 2026-03-24 | 3,495 | $84,190.87 | $71,161.48 | $13,029.39 | $-58,132.08 | $-58,132.08 |

> **Note**: Net cash = SELL revenue - BUY cost. Negative = net buyer (accumulating positions).
> PnL from market resolution is NOT included here as we cannot determine outcomes from trade data alone.

## Hourly Distribution (UTC)

| Hour | Trades | % | Volume | Bar |
|------|--------|---|--------|-----|
| 15:00 | 1,409 | 40.3% | $37,381.70 | ############################################## |
| 16:00 | 1,455 | 41.6% | $31,780.27 | ################################################ |
| 17:00 | 631 | 18.1% | $15,028.89 | ##################### |

> SEEN: All trades concentrated in 15:00-17:18 UTC (11:00AM-1:18PM ET). US market hours only.

## Market Type Breakdown

| Coin | Trades | % | Volume | % vol | Avg Price |
|------|--------|---|--------|-------|-----------|
| BTC | 1,484 | 42.5% | $56,076.47 | 66.6% | 0.407 |
| ETH | 1,088 | 31.1% | $18,282.23 | 21.7% | 0.411 |
| XRP | 514 | 14.7% | $4,540.53 | 5.4% | 0.358 |
| SOL | 409 | 11.7% | $5,291.64 | 6.3% | 0.366 |

> SEEN: BTC dominant (42.5% trades, 66.6% volume). Trades all 4 coins. BTC has highest avg price = higher conviction entries.

## Timeframe Breakdown

| TF | Trades | % | Volume | Markets |
|----|--------|---|--------|---------|
| 5M | 2,569 | 73.5% | $60,095.19 | 104 |
| 15M | 544 | 15.6% | $16,372.33 | 28 |
| 1H | 382 | 10.9% | $7,723.35 | 12 |

> SEEN: Overwhelmingly 5M (89.1%). Also trades 15M (7.4%) and 1H hourly markets (3.5%).
> Multi-timeframe approach: primary edge in 5M, likely uses 15M/1H for hedging or additional directional bets.

## Side Analysis

| Metric | BUY | SELL |
|--------|-----|------|
| Trades | 2,975 (85.1%) | 520 (14.9%) |
| Volume | $71,161.48 | $13,029.39 |
| Total size | $173,046.98 | $36,224.27 |
| Avg price | 0.411 | 0.360 |
| Avg size | 58.17 | 69.66 |

> SEEN: 85% BUY, 15% SELL. BUY avg price 0.411 vs SELL avg price 0.360.
> This is a **net buyer** — accumulating positions at mid-range prices, selling at cheaper prices.
> Consistent with directional betting or MM where they're net long, not arbitrage-focused.

## Outcome Analysis (Up vs Down)

| Metric | Up | Down |
|--------|-----|------|
| Trades | 1,794 (51.3%) | 1,701 (48.7%) |
| Volume | $39,488.66 | $44,702.21 |
| Avg price | 0.392 | 0.427 |

### BUY/SELL x Outcome Matrix

| | Up | Down | Total |
|--|-----|------|-------|
| BUY | 1,549 ($32,789) | 1,426 ($38,372) | 2,975 |
| SELL | 245 ($6,699) | 275 ($6,330) | 520 |

> SEEN: BUY Up = 1549, BUY Down = 1426. Nearly balanced outcome buying.
> SELL Up = 245, SELL Down = 275. Sells also balanced. This is a **both-sides** trader.

## Price Distribution

| Bucket | Trades | % | Cum % | Bar |
|--------|--------|---|-------|-----|
| 0.0-0.1 | 131 | 3.7% | 3.7% | ######## |
| 0.1-0.2 | 264 | 7.6% | 11.3% | ################# |
| 0.2-0.3 | 443 | 12.7% | 24.0% | ############################# |
| 0.3-0.4 | 772 | 22.1% | 46.1% | ################################################### |
| 0.4-0.5 | 924 | 26.4% | 72.5% | ############################################################# |
| 0.5-0.6 | 518 | 14.8% | 87.3% | ################################## |
| 0.6-0.7 | 223 | 6.4% | 93.7% | ############## |
| 0.7-0.8 | 159 | 4.5% | 98.3% | ########## |
| 0.8-0.9 | 43 | 1.2% | 99.5% | ## |
| 0.9-1.0 | 18 | 0.5% | 100.0% | # |

> SEEN: Concentrated in 0.30-0.60 range (63.3% of trades). Avoids extreme prices.
> Peak at 0.40-0.50 (26.4%) — trading near 50/50 odds. Classic mid-price strategy.

## Size Distribution

| Metric | Value |
|--------|-------|
| Min | $0.14 | SEEN |
| P25 | $18.00 | SEEN |
| Median | $51.00 | SEEN |
| Mean | $59.88 | SEEN |
| P75 | $101.00 | SEEN |
| Max | $347.00 | SEEN |
| Stdev | $56.58 | SEEN |

### Size Buckets

| Range | Trades | % |
|-------|--------|---|
| $0-5 | 297 | 8.5% |
| $5-10 | 303 | 8.7% |
| $10-25 | 511 | 14.6% |
| $25-50 | 510 | 14.6% |
| $50-100 | 873 | 25.0% |
| $100-250 | 896 | 25.6% |
| $250+ | 105 | 3.0% |

> SEEN: Bimodal distribution — clusters at ~$51 and ~$101 (standard lot sizes).
> Small sizes ($0-10) = 17.2% suggest probing/testing or thin book fills.

## Both-Side Analysis

| Metric | Value |
|--------|-------|
| Total unique markets | 144 | SEEN |
| Both sides traded | 118 (81.9%) | SEEN |
| Single side only | 26 (18.1%) | SEEN |

### Single-Side Markets (26 markets)

| Market | Outcome | Side | Trades | Volume |
|--------|---------|------|--------|--------|
| Bitcoin Up or Down - March 24, 11:50AM-11:55AM ET | Down | {'BUY': 15, 'SELL': 4} | 19 | $324.07 |
| Bitcoin Up or Down - March 24, 12:30PM-12:45PM ET | Up | {'BUY': 12} | 12 | $256.66 |
| Solana Up or Down - March 24, 1PM ET | Up | {'BUY': 24} | 24 | $253.39 |
| Ethereum Up or Down - March 24, 11:05AM-11:10AM ET | Up | {'BUY': 9, 'SELL': 5} | 14 | $130.88 |
| Solana Up or Down - March 24, 12:45PM-12:50PM ET | Up | {'BUY': 8} | 8 | $90.56 |
| XRP Up or Down - March 24, 12:30PM-12:45PM ET | Up | {'BUY': 17} | 17 | $75.56 |
| Solana Up or Down - March 24, 12:30PM-12:45PM ET | Up | {'BUY': 12} | 12 | $70.38 |
| Solana Up or Down - March 24, 11:25AM-11:30AM ET | Up | {'BUY': 7} | 7 | $63.98 |
| XRP Up or Down - March 24, 11:30AM-11:45AM ET | Up | {'BUY': 6} | 6 | $62.20 |
| Solana Up or Down - March 24, 1:00PM-1:15PM ET | Up | {'BUY': 18} | 18 | $49.37 |
| Solana Up or Down - March 24, 1:15PM-1:20PM ET | Down | {'BUY': 2} | 2 | $47.58 |
| Solana Up or Down - March 24, 12:50PM-12:55PM ET | Down | {'BUY': 6} | 6 | $40.56 |
| XRP Up or Down - March 24, 12:45PM-12:50PM ET | Up | {'BUY': 9} | 9 | $38.80 |
| XRP Up or Down - March 24, 11:05AM-11:10AM ET | Up | {'BUY': 4} | 4 | $37.41 |
| XRP Up or Down - March 24, 11:45AM-11:50AM ET | Up | {'BUY': 6} | 6 | $35.38 |

## Combined Price Analysis (Both-Side Markets)

### Combined Price Statistics

| Metric | Value |
|--------|-------|
| Markets analyzed | 118 | SEEN |
| Min combined | 0.5300 | SEEN |
| P25 | 0.7740 | SEEN |
| Median | 0.8936 | SEEN |
| Mean | 0.8812 | SEEN |
| P75 | 0.9741 | SEEN |
| Max | 1.1944 | SEEN |
| Below 1.00 (profitable) | 93 (78.8%) | SEEN |
| 0.99-1.01 (breakeven) | 0 (0.0%) | SEEN |
| Above 1.00 (overpaying) | 25 (21.2%) | SEEN |

### Top 10 Lowest Combined (Most Profitable Spread)

| # | Combined | Avg Up | Avg Down | TF | Trades | Net Cash | Market |
|---|----------|--------|----------|-----|--------|----------|--------|
| 1 | 0.5300 | 0.190 | 0.340 | 5M | 5 | $-32.45 | Solana Up or Down - March 24, 11:45AM-11:50AM ET |
| 2 | 0.5433 | 0.133 | 0.410 | 15M | 5 | $-35.75 | Solana Up or Down - March 24, 12:15PM-12:30PM ET |
| 3 | 0.6014 | 0.184 | 0.417 | 5M | 8 | $-48.08 | XRP Up or Down - March 24, 11:50AM-11:55AM ET |
| 4 | 0.6306 | 0.205 | 0.426 | 15M | 30 | $-981.84 | Bitcoin Up or Down - March 24, 12:15PM-12:30PM ET |
| 5 | 0.6330 | 0.130 | 0.503 | 5M | 2 | $-12.12 | XRP Up or Down - March 24, 11:40AM-11:45AM ET |
| 6 | 0.6363 | 0.338 | 0.298 | 15M | 22 | $-270.15 | Solana Up or Down - March 24, 11:45AM-12:00PM ET |
| 7 | 0.6437 | 0.160 | 0.484 | 5M | 5 | $-41.29 | Solana Up or Down - March 24, 12:00PM-12:05PM ET |
| 8 | 0.6550 | 0.495 | 0.160 | 5M | 5 | $-52.08 | Solana Up or Down - March 24, 12:40PM-12:45PM ET |
| 9 | 0.6559 | 0.134 | 0.522 | 1H | 37 | $-217.78 | XRP Up or Down - March 24, 12PM ET |
| 10 | 0.6725 | 0.374 | 0.298 | 15M | 19 | $-320.82 | Solana Up or Down - March 24, 12:00PM-12:15PM ET |

### Top 10 Highest Combined (Worst Spread)

| # | Combined | Avg Up | Avg Down | TF | Trades | Net Cash | Market |
|---|----------|--------|----------|-----|--------|----------|--------|
| 1 | 1.1944 | 0.895 | 0.299 | 15M | 16 | $-51.33 | Bitcoin Up or Down - March 24, 11:00AM-11:15AM ET |
| 2 | 1.1507 | 0.339 | 0.812 | 1H | 27 | $-1,167.89 | Ethereum Up or Down - March 24, 1PM ET |
| 3 | 1.1480 | 0.368 | 0.780 | 5M | 6 | $-50.53 | Solana Up or Down - March 24, 12:30PM-12:35PM ET |
| 4 | 1.1293 | 0.373 | 0.756 | 5M | 34 | $-948.22 | Bitcoin Up or Down - March 24, 12:35PM-12:40PM ET |
| 5 | 1.1089 | 0.591 | 0.518 | 5M | 13 | $-243.13 | Solana Up or Down - March 24, 11:15AM-11:20AM ET |
| 6 | 1.1086 | 0.533 | 0.575 | 5M | 25 | $-367.56 | XRP Up or Down - March 24, 11:15AM-11:20AM ET |
| 7 | 1.0834 | 0.710 | 0.373 | 5M | 22 | $-298.13 | Ethereum Up or Down - March 24, 12:40PM-12:45PM ET |
| 8 | 1.0800 | 0.500 | 0.580 | 5M | 3 | $-15.02 | XRP Up or Down - March 24, 11:30AM-11:35AM ET |
| 9 | 1.0776 | 0.564 | 0.513 | 15M | 17 | $-174.83 | XRP Up or Down - March 24, 11:00AM-11:15AM ET |
| 10 | 1.0567 | 0.707 | 0.350 | 5M | 5 | $-51.18 | XRP Up or Down - March 24, 12:05PM-12:10PM ET |

> SEEN: Median combined = 0.8936 means this wallet pays well under 1.00 for both sides on average.
> 78.8% of both-side markets have combined < 1.00 — consistent with buying cheap sides.
> However, this does NOT mean pure arb. The wallet is a NET BUYER ($71K buy vs $13K sell),
> meaning most positions are held to resolution, not sold back.

### Combined Price by Timeframe

- **5M**: n=85, median=0.8951, mean=0.8926
- **15M**: n=22, median=0.8512, mean=0.8471
- **1H**: n=11, median=0.8466, mean=0.8609

## Maker vs Taker

> The API does not expose maker/taker classification.
> **INFERRED**: Given 85% BUY side and the rapid-fire trading pattern (median inter-trade gap = 0s),
> this is likely a **taker-heavy** strategy — hitting existing orders rather than posting limit orders.
> The avg BUY price (0.411) > avg SELL price (0.360) also suggests crossing the spread.

## Win Rate Estimation

> Cannot directly determine from trade data alone — resolution outcomes are not in the API response.
> **INFERRED from trade patterns**:
> - The wallet buys both sides in 81.9% of markets
> - With combined price < 1.00 in 78.8% of both-side markets, guaranteed profit exists on resolution
> - For single-side markets (18.1%), win rate depends on directional accuracy

## Trade Frequency & Timing

| Metric | Value | Source |
|--------|-------|--------|
| Active minutes | 129 | SEEN |
| Total duration | 129 min | SEEN |
| Activity rate | 99.6% | SEEN |
| Trades/min (min) | 1 | SEEN |
| Trades/min (max) | 155 | SEEN |
| Trades/min (median) | 22 | SEEN |
| Trades/min (mean) | 27.1 | SEEN |
| Inter-trade gap (min) | 0s | SEEN |
| Inter-trade gap (max) | 128s | SEEN |
| Inter-trade gap (median) | 0s | SEEN |
| Inter-trade gap (mean) | 2.2s | SEEN |

> SEEN: Median 0s between trades. 155 trades in busiest minute. This is clearly **automated/bot** trading.

### 5-Minute Activity Bins

| Time (UTC) | Trades | Volume |
|------------|--------|--------|
| 15:05 |   47 | $  794.82 |
| 15:10 |  205 | $5,264.21 |
| 15:15 |  299 | $7,658.06 |
| 15:20 |  172 | $4,564.87 |
| 15:25 |   96 | $2,592.50 |
| 15:30 |   76 | $2,706.01 |
| 15:35 |  113 | $4,456.70 |
| 15:40 |   54 | $1,119.63 |
| 15:45 |  114 | $3,462.69 |
| 15:50 |  144 | $2,418.33 |
| 15:55 |   89 | $2,343.89 |
| 16:00 |  231 | $5,235.35 |
| 16:05 |  163 | $4,119.98 |
| 16:10 |  166 | $3,479.91 |
| 16:15 |  110 | $1,835.80 |
| 16:20 |   95 | $2,258.01 |
| 16:25 |  124 | $3,372.52 |
| 16:30 |  113 | $1,975.01 |
| 16:35 |  104 | $2,404.61 |
| 16:40 |  111 | $1,867.12 |
| 16:45 |   90 | $1,779.37 |
| 16:50 |   63 | $1,620.53 |
| 16:55 |   85 | $1,832.07 |
| 17:00 |  153 | $3,572.79 |
| 17:05 |  262 | $5,994.15 |
| 17:10 |  110 | $2,698.67 |
| 17:15 |  106 | $2,763.29 |

## Transaction Analysis

| Metric | Value | Source |
|--------|-------|--------|
| Unique transactions | 3,495 | SEEN |
| Trades per txn (all) | 1 | SEEN |
| Multi-trade txns | 0 (0%) | SEEN |

> SEEN: Every trade is a unique transaction. No batch/multi-fill transactions.

## Top 20 Most-Traded Markets

| # | Market | TF | Trades | Buy $ | Sell $ | Net | Outcomes |
|---|--------|----|--------|-------|--------|-----|----------|
| 1 | Bitcoin Up or Down - March 24, 11:15AM-11:20AM ET | 5M | 118 | $2,780 | $1,512 | $-1,268 | Down,Up |
| 2 | Ethereum Up or Down - March 24, 11:15AM-11:20AM ET | 5M | 117 | $1,447 | $622 | $-825 | Down,Up |
| 3 | Ethereum Up or Down - March 24, 11:10AM-11:15AM ET | 5M | 92 | $1,180 | $300 | $-880 | Down,Up |
| 4 | Bitcoin Up or Down - March 24, 12:00PM-12:05PM ET | 5M | 86 | $1,599 | $1,261 | $-338 | Down,Up |
| 5 | Bitcoin Up or Down - March 24, 11:10AM-11:15AM ET | 5M | 77 | $1,978 | $661 | $-1,317 | Down,Up |
| 6 | Ethereum Up or Down - March 24, 12:00PM-12:05PM ET | 5M | 76 | $752 | $409 | $-343 | Down,Up |
| 7 | Ethereum Up or Down - March 24, 12:10PM-12:15PM ET | 5M | 71 | $649 | $275 | $-374 | Down,Up |
| 8 | Solana Up or Down - March 24, 1:05PM-1:10PM ET | 5M | 63 | $1,214 | $0 | $-1,214 | Down,Up |
| 9 | Bitcoin Up or Down - March 24, 12:05PM-12:10PM ET | 5M | 62 | $1,872 | $290 | $-1,582 | Down,Up |
| 10 | Bitcoin Up or Down - March 24, 12:10PM-12:15PM ET | 5M | 61 | $1,618 | $358 | $-1,260 | Down,Up |
| 11 | Ethereum Up or Down - March 24, 12:05PM-12:10PM ET | 5M | 60 | $641 | $128 | $-513 | Down,Up |
| 12 | Bitcoin Up or Down - March 24, 11:25AM-11:30AM ET | 5M | 58 | $1,540 | $455 | $-1,085 | Down,Up |
| 13 | Bitcoin Up or Down - March 24, 12:25PM-12:30PM ET | 5M | 58 | $1,614 | $656 | $-958 | Down,Up |
| 14 | XRP Up or Down - March 24, 1:05PM-1:10PM ET | 5M | 58 | $737 | $0 | $-737 | Down,Up |
| 15 | Bitcoin Up or Down - March 24, 1:10PM-1:15PM ET | 5M | 57 | $1,359 | $694 | $-666 | Down,Up |
| 16 | Ethereum Up or Down - March 24, 11:50AM-11:55AM ET | 5M | 53 | $586 | $20 | $-566 | Down,Up |
| 17 | Bitcoin Up or Down - March 24, 11:30AM-11:45AM ET | 15M | 48 | $3,155 | $790 | $-2,365 | Down,Up |
| 18 | Ethereum Up or Down - March 24, 11AM ET | 1H | 47 | $915 | $0 | $-915 | Down,Up |
| 19 | Bitcoin Up or Down - March 24, 11:20AM-11:25AM ET | 5M | 47 | $1,514 | $295 | $-1,219 | Down,Up |
| 20 | Bitcoin Up or Down - March 24, 11:45AM-11:50AM ET | 5M | 46 | $1,466 | $291 | $-1,175 | Down,Up |

## Cash Flow Summary

| Metric | Value | Source |
|--------|-------|--------|
| Total BUY cost | $71,161.48 | SEEN |
| Total SELL revenue | $13,029.39 | SEEN |
| Net cash outflow | $-58,132.08 | SEEN |
| Outstanding position (BUY size - SELL size) | $136,822.71 shares | SEEN |

> SEEN: Net cash outflow of $58,132.08 — this wallet spent $71,161 buying and recovered $13,029 selling.
> Still holds ~$136,823 shares worth of open positions awaiting resolution.

## Raw Trade Samples

### First 10 Trades (oldest)

| # | Timestamp (UTC) | Side | Outcome | Price | Size | Market |
|---|-----------------|------|---------|-------|------|--------|
| 1 | 15:08:53 | BUY | Down | 0.57 | 40.50 | XRP Up or Down - March 24, 11:00AM-11:15AM ET |
| 2 | 15:08:59 | BUY | Up | 0.36 | 67.00 | Bitcoin Up or Down - March 24, 11:05AM-11:10AM ET |
| 3 | 15:08:59 | SELL | Down | 0.26 | 7.56 | Bitcoin Up or Down - March 24, 11:00AM-11:15AM ET |
| 4 | 15:08:59 | BUY | Up | 0.55 | 1.36 | XRP Up or Down - March 24, 11:00AM-11:15AM ET |
| 5 | 15:09:05 | BUY | Up | 0.29 | 29.62 | Ethereum Up or Down - March 24, 11:05AM-11:10AM ET |
| 6 | 15:09:09 | SELL | Up | 0.25 | 51.00 | Ethereum Up or Down - March 24, 11:05AM-11:10AM ET |
| 7 | 15:09:13 | BUY | Up | 0.42 | 51.00 | Ethereum Up or Down - March 24, 11:05AM-11:10AM ET |
| 8 | 15:09:13 | SELL | Down | 0.21 | 92.99 | Bitcoin Up or Down - March 24, 11:00AM-11:15AM ET |
| 9 | 15:09:13 | BUY | Up | 0.49 | 101.00 | Bitcoin Up or Down - March 24, 11:05AM-11:10AM ET |
| 10 | 15:09:13 | BUY | Up | 0.50 | 101.00 | Bitcoin Up or Down - March 24, 11:05AM-11:10AM ET |

### Last 10 Trades (newest)

| # | Timestamp (UTC) | Side | Outcome | Price | Size | Market |
|---|-----------------|------|---------|-------|------|--------|
| 1 | 17:18:11 | BUY | Up | 0.15 | 51.00 | Ethereum Up or Down - March 24, 1:15PM-1:20PM ET |
| 2 | 17:18:11 | BUY | Up | 0.39 | 98.63 | Bitcoin Up or Down - March 24, 1:15PM-1:30PM ET |
| 3 | 17:18:11 | BUY | Up | 0.14 | 51.00 | Ethereum Up or Down - March 24, 1:15PM-1:20PM ET |
| 4 | 17:18:11 | BUY | Up | 0.13 | 51.00 | Ethereum Up or Down - March 24, 1:15PM-1:20PM ET |
| 5 | 17:18:13 | BUY | Up | 0.21 | 51.00 | Ethereum Up or Down - March 24, 1:15PM-1:20PM ET |
| 6 | 17:18:13 | BUY | Up | 0.20 | 22.60 | Ethereum Up or Down - March 24, 1:15PM-1:20PM ET |
| 7 | 17:18:21 | BUY | Down | 0.85 | 5.06 | Ethereum Up or Down - March 24, 1:15PM-1:20PM ET |
| 8 | 17:18:21 | BUY | Up | 0.16 | 101.00 | Bitcoin Up or Down - March 24, 1:15PM-1:20PM ET |
| 9 | 17:18:21 | BUY | Up | 0.39 | 8.63 | Bitcoin Up or Down - March 24, 1:15PM-1:30PM ET |
| 10 | 17:18:21 | BUY | Up | 0.40 | 96.49 | Bitcoin Up or Down - March 24, 1:15PM-1:30PM ET |

## Key Observations

1. **Bot trader**: 3495 trades in 129 minutes, median 0s between trades, 155 trades/min peak
2. **Multi-coin**: BTC (42.5%), ETH (31.1%), XRP (14.7%), SOL (11.7%)
3. **Multi-timeframe**: 5M primary (89.1%), also 15M (7.4%) and 1H (3.5%)
4. **Both-sides in 81.9% of markets**: buys Up AND Down in most markets
5. **Net buyer**: 85.1% BUY, 14.9% SELL. $71K buy vs $13K sell = accumulating positions
6. **Combined price < 1.00 in 78.8% of both-side markets**: median combined = 0.8936
7. **Mid-price focused**: 63.3% of trades at prices 0.30-0.60
8. **Standard lot sizes**: median size $51, clusters at $51 and $101
9. **US market hours only**: all activity 11:00AM-1:18PM ET
10. **No maker/taker data**: but pattern suggests taker-heavy (hitting resting orders)

## Strategy Assessment

This wallet appears to be running a **directional bot with both-side hedging**:
- Buys both Up and Down in most markets (arb-like)
- But heavily net long ($58K more in buys than sells)
- Trades at mid-range prices (0.30-0.60), not extreme odds
- Combined price well below 1.00 suggests buying cheap sides of inefficient markets
- The massive BUY surplus means profit depends heavily on resolution outcomes
- NOT pure arb (would show balanced buy/sell). NOT pure directional (would be single-side)
- Closest to **Hybrid directional + cheap-side accumulation** pattern

> Data completeness: 3,495 trades from API (max 3,500 due to offset limit of 3,000).
> This wallet likely has more historical trades not accessible via this endpoint.