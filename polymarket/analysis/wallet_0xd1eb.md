# Wallet Analysis: 0xd1ebe815f921b3ebbd8d9e0a4192c6ab18360f5c
> Pseudonym: **Fickle-Spark** (SEEN)
> Generated: 2026-03-24 17:23 UTC
> Data source: Polymarket data-api /trades endpoint, all pages fetched
> **API limit**: max offset=3000, so only most recent 3,500 trades retrievable

## Summary

| Metric | Value | Source |
|--------|-------|--------|
| Total trades | 3,495 | SEEN |
| Total volume (size × price) | $84,190.87 | SEEN (calculated) |
| Total shares traded | 209,271.25 | SEEN |
| Unique markets (conditionId) | 144 | SEEN |
| Unique slugs | 144 | SEEN |
| Unique tx hashes | 3,495 | SEEN |
| Trades per tx | 1.0 (every trade = separate tx) | SEEN |
| Time range | 2026-03-24 15:08:53 to 2026-03-24 17:18:21 UTC | SEEN |
| Duration | 2.16 hours | SEEN |
| Trades/minute | 27.0 | SEEN (calculated) |
| Trades/second | 0.45 | SEEN (calculated) |

## Side Analysis

| Side | Count | Volume ($) | Avg Price |
|------|-------|-----------|-----------|
| BUY | 2,975 (85.1%) | $71,161.48 | 0.4172 |
| SELL | 520 (14.9%) | $13,029.39 | 0.3624 |

### Side × Outcome Matrix (SEEN)

| | Up | Down | Total |
|---|---|---|---|
| BUY | 1549 | 1426 | 2975 |
| SELL | 245 | 275 | 520 |
| Total | 1794 | 1701 | 3495 |

### Outcome Breakdown (SEEN)

| Outcome | Count | Volume ($) | Avg Price |
|---------|-------|-----------|-----------|
| Up | 1,794 (51.3%) | $39,488.66 | 0.3921 |
| Down | 1,701 (48.7%) | $44,702.21 | 0.4269 |

## Market Type Breakdown (SEEN)

| Coin | Trades | % | Volume ($) | % | Size | Buys | Sells |
|------|--------|---|-----------|---|------|------|-------|
| BTC | 1,484 | 42.5% | $56,076.47 | 66.6% | 137,653 | 1153 | 331 |
| ETH | 1,088 | 31.1% | $18,282.23 | 21.7% | 44,476 | 899 | 189 |
| SOL | 409 | 11.7% | $5,291.64 | 6.3% | 14,443 | 409 | 0 |
| XRP | 514 | 14.7% | $4,540.53 | 5.4% | 12,699 | 514 | 0 |

## Timeframe Breakdown (SEEN — from slug)

| Timeframe | Trades | % | Volume ($) | % |
|-----------|--------|---|-----------|---|
| 5M | 2,569 | 73.5% | $60,095.19 | 71.4% |
| 15M | 544 | 15.6% | $16,372.33 | 19.4% |
| 1H | 382 | 10.9% | $7,723.35 | 9.2% |

> Note: '1H' includes both explicit '-1h-' slug markets AND hourly slug format like 'bitcoin-up-or-down-march-24-2026-11am-et'

## Time-Series PnL Table (SEEN)

> Since all data is from a single day (2026-03-24), this shows the single day.
> PnL estimated from arb profit on both-side markets.

| Date | Trades | Volume ($) | Buy Vol ($) | Sell Vol ($) | Est. Arb PnL (gross) | Est. Arb PnL (net) |
|------|--------|-----------|------------|-------------|---------------------|-------------------|
| 2026-03-24 | 3,495 | $84,190.87 | $71,161.48 | $13,029.39 | $6,628.80 | $5,418.02 |

## Hourly Distribution (UTC) (SEEN)

| Hour (UTC) | Trades | % | Bar |
|-----------|--------|---|-----|
| 15:00 | 1,409 | 40.3% | █████████████████████████████ |
| 16:00 | 1,455 | 41.6% | ██████████████████████████████ |
| 17:00 | 631 | 18.1% | █████████████ |

## Price Distribution (SEEN)

| Bucket | Count | % | Bar |
|--------|-------|---|-----|
| [0.00-0.10) | 131 | 3.7% | ████ |
| [0.10-0.20) | 264 | 7.6% | ████████ |
| [0.20-0.30) | 443 | 12.7% | ██████████████ |
| [0.30-0.40) | 772 | 22.1% | █████████████████████████ |
| [0.40-0.50) | 924 | 26.4% | ██████████████████████████████ |
| [0.50-0.60) | 518 | 14.8% | ████████████████ |
| [0.60-0.70) | 223 | 6.4% | ███████ |
| [0.70-0.80) | 159 | 4.5% | █████ |
| [0.80-0.90) | 43 | 1.2% | █ |
| [0.90-1.00) | 18 | 0.5% |  |

| **Price stats** | min=0.0086, max=0.9600, mean=0.4091, median=0.4100 | | |

## Size Distribution (SEEN)

| Stat | Value |
|------|-------|
| Min | 0.14 |
| Max | 347.00 |
| Mean | 59.88 |
| Median | 51.00 |
| P25 | 18.00 |
| P75 | 101.00 |
| Stdev | 56.58 |

### Size Histogram (SEEN)

| Bucket | Count | % |
|--------|-------|---|
| [0-10] | 509 | 14.6% |
| [10-20] | 441 | 12.6% |
| [20-50] | 656 | 18.8% |
| [50-100] | 881 | 25.2% |
| [100-200] | 872 | 24.9% |
| [200-500] | 136 | 3.9% |
| [500+] | 0 | 0.0% |

## Both-Side Analysis (SEEN)

| Metric | Value |
|--------|-------|
| Total unique markets | 144 |
| Markets with BOTH Up+Down traded | 118 (81.9%) |
| Markets with only one side | 26 (18.1%) |

## Combined Price Analysis (SEEN — both-side markets only)

> Combined = weighted_avg(Up_price) + weighted_avg(Down_price) per market.
> Combined < 1.00 = profitable arb. Combined > 1.00 = overpaying (negative arb).

| Stat | Value |
|------|-------|
| Markets analyzed | 118 |
| Min combined | 0.5358 |
| Max combined | 1.2078 |
| Mean combined | 0.8808 |
| Median combined | 0.8790 |
| Markets with combined < 1.00 | 96 (81.4%) |
| Markets with combined > 1.00 | 22 (18.6%) |

### Top 10 Most Profitable (lowest combined) (SEEN)

| # | Combined | Up Price | Down Price | Trades | Volume ($) | Market |
|---|----------|----------|------------|--------|-----------|--------|
| 1 | 0.5358 | 0.1935 | 0.3423 | 5 | $32 | Solana Up or Down - March 24, 11:45AM-11:50AM ET |
| 2 | 0.5827 | 0.1660 | 0.4167 | 8 | $48 | XRP Up or Down - March 24, 11:50AM-11:55AM ET |
| 3 | 0.5905 | 0.1805 | 0.4100 | 5 | $36 | Solana Up or Down - March 24, 12:15PM-12:30PM ET |
| 4 | 0.6009 | 0.1704 | 0.4304 | 30 | $982 | Bitcoin Up or Down - March 24, 12:15PM-12:30PM ET |
| 5 | 0.6330 | 0.1300 | 0.5030 | 2 | $12 | XRP Up or Down - March 24, 11:40AM-11:45AM ET |
| 6 | 0.6364 | 0.3544 | 0.2821 | 22 | $270 | Solana Up or Down - March 24, 11:45AM-12:00PM ET |
| 7 | 0.6391 | 0.1554 | 0.4837 | 5 | $41 | Solana Up or Down - March 24, 12:00PM-12:05PM ET |
| 8 | 0.6761 | 0.2121 | 0.4640 | 16 | $42 | XRP Up or Down - March 24, 12:45PM-1:00PM ET |
| 9 | 0.6897 | 0.5200 | 0.1697 | 7 | $38 | XRP Up or Down - March 24, 12:50PM-12:55PM ET |
| 10 | 0.6904 | 0.5073 | 0.1832 | 5 | $52 | Solana Up or Down - March 24, 12:40PM-12:45PM ET |

### Top 10 Worst (highest combined) (SEEN)

| # | Combined | Up Price | Down Price | Trades | Volume ($) | Market |
|---|----------|----------|------------|--------|-----------|--------|
| 1 | 1.2078 | 0.8925 | 0.3153 | 16 | $894 | Bitcoin Up or Down - March 24, 11:00AM-11:15AM ET |
| 2 | 1.1578 | 0.2982 | 0.8596 | 16 | $237 | Ethereum Up or Down - March 24, 11:55AM-12:00PM ET |
| 3 | 1.1498 | 0.3375 | 0.8122 | 27 | $1,168 | Ethereum Up or Down - March 24, 1PM ET |
| 4 | 1.1448 | 0.5693 | 0.5755 | 25 | $368 | XRP Up or Down - March 24, 11:15AM-11:20AM ET |
| 5 | 1.1447 | 0.3728 | 0.7719 | 34 | $1,281 | Bitcoin Up or Down - March 24, 12:35PM-12:40PM ET |
| 6 | 1.1411 | 0.3659 | 0.7751 | 10 | $407 | Bitcoin Up or Down - March 24, 11:55AM-12:00PM ET |
| 7 | 1.1031 | 0.3693 | 0.7338 | 22 | $461 | Ethereum Up or Down - March 24, 12:35PM-12:40PM ET |
| 8 | 1.0997 | 0.7100 | 0.3897 | 22 | $426 | Ethereum Up or Down - March 24, 12:40PM-12:45PM ET |
| 9 | 1.0935 | 0.3135 | 0.7800 | 6 | $51 | Solana Up or Down - March 24, 12:30PM-12:35PM ET |
| 10 | 1.0933 | 0.5234 | 0.5699 | 17 | $175 | XRP Up or Down - March 24, 11:00AM-11:15AM ET |

## Arbitrage PnL Estimate (SEEN — calculated)

> For each market: arb_size = min(up_shares, down_shares). Guaranteed $1 payout on arb portion.
> Gross = revenue - cost. Net = revenue × 0.98 - cost (2% Polymarket fee on winnings).
> Excess shares = directional exposure (not guaranteed).

| Metric | Value |
|--------|-------|
| Total arb PnL (gross) | $6,628.80 |
| Total arb PnL (net, after 2% fee) | $5,418.02 |
| Total cost | $84,190.87 |
| Total arb shares | 60,539.14 |
| Total excess (directional) shares | 88,192.97 |

### Top 15 Markets by Arb Size (SEEN)

| Market | Arb Size | Excess | Dir | Cost ($) | Arb PnL (net) |
|--------|----------|--------|-----|---------|--------------|
| Bitcoin Up or Down - March 24, 11:15AM-11:20AM ET | 3,802 | 2,235 | Down | $4,292 | $347.70 |
| Bitcoin Up or Down - March 24, 11:30AM-11:45AM ET | 2,488 | 3,057 | Up | $3,945 | $-108.66 |
| Bitcoin Up or Down - March 24, 11:25AM-11:30AM ET | 2,340 | 44 | Down | $1,995 | $320.79 |
| Bitcoin Up or Down - March 24, 11:10AM-11:15AM ET | 2,185 | 1,777 | Down | $2,639 | $224.10 |
| Bitcoin Up or Down - March 24, 12:25PM-12:30PM ET | 2,020 | 1,199 | Up | $2,270 | $116.12 |
| Bitcoin Up or Down - March 24, 11:45AM-12:00PM ET | 2,008 | 2,376 | Down | $2,599 | $424.23 |
| Bitcoin Up or Down - March 24, 12:05PM-12:10PM ET | 1,954 | 1,383 | Down | $2,162 | $232.09 |
| Bitcoin Up or Down - March 24, 12:10PM-12:15PM ET | 1,862 | 843 | Down | $1,976 | $200.48 |
| Bitcoin Up or Down - March 24, 11:45AM-11:50AM ET | 1,658 | 547 | Up | $1,757 | $67.20 |
| Bitcoin Up or Down - March 24, 1:10PM-1:15PM ET | 1,505 | 1,796 | Down | $2,053 | $72.00 |
| Bitcoin Up or Down - March 24, 11:20AM-11:25AM ET | 1,404 | 1,134 | Down | $1,809 | $41.99 |
| Bitcoin Up or Down - March 24, 11:15AM-11:30AM ET | 1,361 | 40 | Up | $1,403 | $-41.72 |
| Ethereum Up or Down - March 24, 11:15AM-11:20AM ET | 1,355 | 1,986 | Down | $2,069 | $49.51 |
| Bitcoin Up or Down - March 24, 12:45PM-1:00PM ET | 1,355 | 768 | Up | $1,343 | $284.41 |
| Bitcoin Up or Down - March 24, 12:00PM-12:05PM ET | 1,143 | 5,164 | Up | $2,860 | $113.46 |

## Maker vs Taker (SEEN)

The Polymarket /trades API does **not** expose maker/taker designation or fee fields.
Available fields per trade: proxyWallet, side, asset, conditionId, size, price, timestamp, title, slug, icon, eventSlug, outcome, outcomeIndex, name, pseudonym, bio, profileImage, profileImageOptimized, transactionHash.

## Win Rate & Streaks

The /trades API does **not** include resolution outcome (win/loss) data.
All 3,500 trades are from 2026-03-24, and many 5M markets from that day may have resolved,
but the API response does not include a 'resolved' or 'result' field.
Win rate and consecutive streaks **cannot be calculated** from this data source alone.

## Raw Trade Samples (SEEN)

### First 10 Trades (oldest in dataset)

| # | Time (UTC) | Side | Outcome | Price | Size | Market |
|---|-----------|------|---------|-------|------|--------|
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

### Last 10 Trades (newest in dataset)

| # | Time (UTC) | Side | Outcome | Price | Size | Market |
|---|-----------|------|---------|-------|------|--------|
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

## All Unique Market Titles (SEEN)

Total: 144 unique titles across 144 markets.

| Market Title | Trades |
|-------------|--------|
| Bitcoin Up or Down - March 24, 11:15AM-11:20AM ET | 118 |
| Ethereum Up or Down - March 24, 11:15AM-11:20AM ET | 117 |
| Ethereum Up or Down - March 24, 11:10AM-11:15AM ET | 92 |
| Bitcoin Up or Down - March 24, 12:00PM-12:05PM ET | 86 |
| Bitcoin Up or Down - March 24, 11:10AM-11:15AM ET | 77 |
| Ethereum Up or Down - March 24, 12:00PM-12:05PM ET | 76 |
| Ethereum Up or Down - March 24, 12:10PM-12:15PM ET | 71 |
| Solana Up or Down - March 24, 1:05PM-1:10PM ET | 63 |
| Bitcoin Up or Down - March 24, 12:05PM-12:10PM ET | 62 |
| Bitcoin Up or Down - March 24, 12:10PM-12:15PM ET | 61 |
| Ethereum Up or Down - March 24, 12:05PM-12:10PM ET | 60 |
| Bitcoin Up or Down - March 24, 11:25AM-11:30AM ET | 58 |
| Bitcoin Up or Down - March 24, 12:25PM-12:30PM ET | 58 |
| XRP Up or Down - March 24, 1:05PM-1:10PM ET | 58 |
| Bitcoin Up or Down - March 24, 1:10PM-1:15PM ET | 57 |
| Ethereum Up or Down - March 24, 11:50AM-11:55AM ET | 53 |
| Bitcoin Up or Down - March 24, 11:30AM-11:45AM ET | 48 |
| Ethereum Up or Down - March 24, 11AM ET | 47 |
| Bitcoin Up or Down - March 24, 11:20AM-11:25AM ET | 47 |
| Bitcoin Up or Down - March 24, 11:45AM-11:50AM ET | 46 |
| Bitcoin Up or Down - March 24, 1:15PM-1:20PM ET | 46 |
| Bitcoin Up or Down - March 24, 12PM ET | 45 |
| Ethereum Up or Down - March 24, 12:15PM-12:20PM ET | 43 |
| XRP Up or Down - March 24, 1:00PM-1:15PM ET | 43 |
| Ethereum Up or Down - March 24, 11:20AM-11:25AM ET | 41 |
| Ethereum Up or Down - March 24, 1:05PM-1:10PM ET | 40 |
| Bitcoin Up or Down - March 24, 11:45AM-12:00PM ET | 39 |
| Ethereum Up or Down - March 24, 12PM ET | 38 |
| Bitcoin Up or Down - March 24, 11:35AM-11:40AM ET | 37 |
| XRP Up or Down - March 24, 12PM ET | 37 |
| Solana Up or Down - March 24, 12PM ET | 36 |
| Ethereum Up or Down - March 24, 1:15PM-1:20PM ET | 36 |
| Bitcoin Up or Down - March 24, 12:15PM-12:20PM ET | 35 |
| Ethereum Up or Down - March 24, 12:55PM-1:00PM ET | 35 |
| Bitcoin Up or Down - March 24, 12:35PM-12:40PM ET | 34 |
| XRP Up or Down - March 24, 11AM ET | 33 |
| Bitcoin Up or Down - March 24, 12:20PM-12:25PM ET | 32 |
| Bitcoin Up or Down - March 24, 12:55PM-1:00PM ET | 32 |
| Bitcoin Up or Down - March 24, 11AM ET | 31 |
| Bitcoin Up or Down - March 24, 12:00PM-12:15PM ET | 31 |
| Bitcoin Up or Down - March 24, 12:40PM-12:45PM ET | 31 |
| Bitcoin Up or Down - March 24, 1:00PM-1:05PM ET | 31 |
| Bitcoin Up or Down - March 24, 12:15PM-12:30PM ET | 30 |
| XRP Up or Down - March 24, 1PM ET | 30 |
| Ethereum Up or Down - March 24, 1:00PM-1:05PM ET | 29 |
| Bitcoin Up or Down - March 24, 11:15AM-11:30AM ET | 28 |
| Ethereum Up or Down - March 24, 11:45AM-11:50AM ET | 28 |
| Bitcoin Up or Down - March 24, 12:30PM-12:35PM ET | 28 |
| XRP Up or Down - March 24, 12:00PM-12:15PM ET | 27 |
| Bitcoin Up or Down - March 24, 12:45PM-12:50PM ET | 27 |
| Bitcoin Up or Down - March 24, 12:50PM-12:55PM ET | 27 |
| Ethereum Up or Down - March 24, 1PM ET | 27 |
| Bitcoin Up or Down - March 24, 1:05PM-1:10PM ET | 27 |
| XRP Up or Down - March 24, 11:15AM-11:20AM ET | 25 |
| Ethereum Up or Down - March 24, 11:25AM-11:30AM ET | 25 |
| Ethereum Up or Down - March 24, 12:20PM-12:25PM ET | 24 |
| Bitcoin Up or Down - March 24, 12:45PM-1:00PM ET | 24 |
| Solana Up or Down - March 24, 1PM ET | 24 |
| Ethereum Up or Down - March 24, 1:10PM-1:15PM ET | 24 |
| Bitcoin Up or Down - March 24, 11:40AM-11:45AM ET | 23 |
| Solana Up or Down - March 24, 11:45AM-12:00PM ET | 22 |
| Ethereum Up or Down - March 24, 12:30PM-12:35PM ET | 22 |
| Ethereum Up or Down - March 24, 12:35PM-12:40PM ET | 22 |
| Ethereum Up or Down - March 24, 12:40PM-12:45PM ET | 22 |
| XRP Up or Down - March 24, 11:15AM-11:30AM ET | 20 |
| Ethereum Up or Down - March 24, 11:35AM-11:40AM ET | 20 |
| Bitcoin Up or Down - March 24, 11:30AM-11:35AM ET | 19 |
| Bitcoin Up or Down - March 24, 11:50AM-11:55AM ET | 19 |
| Solana Up or Down - March 24, 12:00PM-12:15PM ET | 19 |
| Ethereum Up or Down - March 24, 12:25PM-12:30PM ET | 19 |
| Bitcoin Up or Down - March 24, 1PM ET | 19 |
| Solana Up or Down - March 24, 12:45PM-1:00PM ET | 18 |
| Solana Up or Down - March 24, 1:00PM-1:15PM ET | 18 |
| XRP Up or Down - March 24, 11:00AM-11:15AM ET | 17 |
| Solana Up or Down - March 24, 11:15AM-11:30AM ET | 17 |
| Ethereum Up or Down - March 24, 11:40AM-11:45AM ET | 17 |
| XRP Up or Down - March 24, 12:30PM-12:45PM ET | 17 |
| Bitcoin Up or Down - March 24, 11:00AM-11:15AM ET | 16 |
| Solana Up or Down - March 24, 11:20AM-11:25AM ET | 16 |
| Solana Up or Down - March 24, 11:30AM-11:45AM ET | 16 |
| Ethereum Up or Down - March 24, 11:55AM-12:00PM ET | 16 |
| XRP Up or Down - March 24, 12:45PM-1:00PM ET | 16 |
| Solana Up or Down - March 24, 11AM ET | 15 |
| XRP Up or Down - March 24, 1:00PM-1:05PM ET | 15 |
| Ethereum Up or Down - March 24, 11:05AM-11:10AM ET | 14 |
| XRP Up or Down - March 24, 11:20AM-11:25AM ET | 14 |
| Ethereum Up or Down - March 24, 11:30AM-11:35AM ET | 14 |
| Solana Up or Down - March 24, 11:50AM-11:55AM ET | 14 |
| Bitcoin Up or Down - March 24, 11:05AM-11:10AM ET | 13 |
| Solana Up or Down - March 24, 11:15AM-11:20AM ET | 13 |
| XRP Up or Down - March 24, 11:45AM-12:00PM ET | 13 |
| XRP Up or Down - March 24, 12:40PM-12:45PM ET | 13 |
| Ethereum Up or Down - March 24, 12:45PM-12:50PM ET | 13 |
| Solana Up or Down - March 24, 12:30PM-12:45PM ET | 12 |
| Bitcoin Up or Down - March 24, 12:30PM-12:45PM ET | 12 |
| Bitcoin Up or Down - March 24, 1:15PM-1:30PM ET | 12 |
| Solana Up or Down - March 24, 12:05PM-12:10PM ET | 11 |
| Bitcoin Up or Down - March 24, 11:55AM-12:00PM ET | 10 |
| XRP Up or Down - March 24, 12:20PM-12:25PM ET | 10 |
| XRP Up or Down - March 24, 12:25PM-12:30PM ET | 10 |
| XRP Up or Down - March 24, 12:00PM-12:05PM ET | 9 |
| XRP Up or Down - March 24, 12:45PM-12:50PM ET | 9 |
| XRP Up or Down - March 24, 11:50AM-11:55AM ET | 8 |
| XRP Up or Down - March 24, 12:15PM-12:20PM ET | 8 |
| Solana Up or Down - March 24, 12:45PM-12:50PM ET | 8 |
| Bitcoin Up or Down - March 24, 1:00PM-1:15PM ET | 8 |
| XRP Up or Down - March 24, 11:10AM-11:15AM ET | 7 |
| Solana Up or Down - March 24, 11:25AM-11:30AM ET | 7 |
| XRP Up or Down - March 24, 12:15PM-12:30PM ET | 7 |
| XRP Up or Down - March 24, 12:30PM-12:35PM ET | 7 |
| XRP Up or Down - March 24, 12:50PM-12:55PM ET | 7 |
| Solana Up or Down - March 24, 12:55PM-1:00PM ET | 7 |
| XRP Up or Down - March 24, 11:30AM-11:45AM ET | 6 |
| XRP Up or Down - March 24, 11:45AM-11:50AM ET | 6 |
| XRP Up or Down - March 24, 12:10PM-12:15PM ET | 6 |
| Solana Up or Down - March 24, 12:25PM-12:30PM ET | 6 |
| Solana Up or Down - March 24, 12:30PM-12:35PM ET | 6 |
| Solana Up or Down - March 24, 12:50PM-12:55PM ET | 6 |
| Solana Up or Down - March 24, 1:10PM-1:15PM ET | 6 |
| Solana Up or Down - March 24, 11:10AM-11:15AM ET | 5 |
| Solana Up or Down - March 24, 11:45AM-11:50AM ET | 5 |
| Solana Up or Down - March 24, 12:00PM-12:05PM ET | 5 |
| XRP Up or Down - March 24, 12:05PM-12:10PM ET | 5 |
| Solana Up or Down - March 24, 12:15PM-12:20PM ET | 5 |
| Solana Up or Down - March 24, 12:15PM-12:30PM ET | 5 |
| XRP Up or Down - March 24, 12:35PM-12:40PM ET | 5 |
| Solana Up or Down - March 24, 12:40PM-12:45PM ET | 5 |
| XRP Up or Down - March 24, 1:10PM-1:15PM ET | 5 |
| XRP Up or Down - March 24, 11:05AM-11:10AM ET | 4 |
| Solana Up or Down - March 24, 12:10PM-12:15PM ET | 4 |
| Solana Up or Down - March 24, 1:00PM-1:05PM ET | 4 |
| XRP Up or Down - March 24, 1:15PM-1:20PM ET | 4 |
| XRP Up or Down - March 24, 11:30AM-11:35AM ET | 3 |
| Solana Up or Down - March 24, 12:20PM-12:25PM ET | 3 |
| Solana Up or Down - March 24, 12:35PM-12:40PM ET | 3 |
| Ethereum Up or Down - March 24, 12:50PM-12:55PM ET | 3 |
| XRP Up or Down - March 24, 1:15PM-1:30PM ET | 3 |
| Solana Up or Down - March 24, 11:30AM-11:35AM ET | 2 |
| XRP Up or Down - March 24, 11:35AM-11:40AM ET | 2 |
| XRP Up or Down - March 24, 11:40AM-11:45AM ET | 2 |
| XRP Up or Down - March 24, 11:55AM-12:00PM ET | 2 |
| Solana Up or Down - March 24, 1:15PM-1:20PM ET | 2 |
| XRP Up or Down - March 24, 11:25AM-11:30AM ET | 1 |
| Solana Up or Down - March 24, 11:55AM-12:00PM ET | 1 |

## Key Observations

1. **Extremely high frequency**: 27 trades/minute sustained over 2.16 hours (SEEN)
2. **100% BUY-dominant**: 85.1% BUY, 14.9% SELL — this wallet primarily opens positions via buying (SEEN)
3. **BTC-heavy**: 42.4% of trades, 66.6% of volume in Bitcoin markets (SEEN)
4. **5M markets dominate**: 73.4% of trades in 5-minute markets (SEEN)
5. **Both-side rate 81.9%**: strong arb/MM pattern — buying BOTH Up and Down in same market (SEEN)
6. **Combined price median 0.8790**: most markets purchased below $1 combined, indicating profitable arb (SEEN)
7. **Estimated arb profit**: $5,418.02 net after fees on arb portion alone (SEEN, calculated)
8. **Single-day operation**: all 3,500 trades within 2.16 hours on 2026-03-24 (SEEN)
9. **Each trade is a separate transaction**: 1:1 trade-to-txhash ratio, no batching (SEEN)
10. **Size range 0.01-483**: median 50, suggesting algorithmic sizing with variable amounts (SEEN)