# Wallet Analysis: 0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82
> Pseudonym: **Unlawful-Shear**
> Generated: 2026-03-24 17:25 UTC
> Data source: Polymarket data-api (trades + activity endpoints)
> All numbers labeled SEEN from raw API data

## Summary
| Metric | Value | Label |
|--------|-------|-------|
| Total BUY+SELL trades | 6,757 | SEEN |
| Total MERGE events | 109 | SEEN |
| Total REDEEM events | 7 | SEEN |
| Total events | 6,873 | SEEN |
| Total volume (price*size) | $116,732.09 | SEEN |
| Total contracts | 277,405.12 | SEEN |
| Unique markets (slugs) | 145 | SEEN |
| First trade | 2026-03-24 15:08:53 UTC | SEEN |
| Last trade | 2026-03-24 17:21:29 UTC | SEEN |
| Active duration | 2.21 hours | SEEN |
| Active days | 1 (March 24, 2026) | SEEN |

## PnL Estimate
| Flow | Amount | Label |
|------|--------|-------|
| BUY cost | $103,702.70 | SEEN |
| SELL revenue | $13,029.39 | SEEN |
| MERGE revenue | $35,975.58 | SEEN |
| REDEEM revenue | $443.06 | SEEN |
| **Net PnL (sell+merge+redeem - buy)** | **$-54,254.66** | SEEN |

> **CAVEAT**: API has offset cap of 3000. Trades endpoint returned 3500 trades, activity returned 3500 events.
> Combined after dedup = 6757 unique trades. Merge/redeem data only available from activity endpoint
> (covers last ~25min of activity). True PnL likely different -- many markets missing merge/redeem data.

## Side Analysis
| Side | Count | Volume | Contracts | Label |
|------|-------|--------|-----------|-------|
| BUY | 6,237 | $103,702.70 | 241,180.85 | SEEN |
| SELL | 520 | $13,029.39 | 36,224.27 | SEEN |

| Outcome | Count | Volume | Contracts | Label |
|---------|-------|--------|-----------|-------|
| Up | 3,528 | $51,211.17 | 137,649.77 | SEEN |
| Down | 3,229 | $65,520.92 | 139,755.35 | SEEN |

## Market Type Breakdown (Coin)
| Coin | Trades | Volume | Contracts | BUY | SELL | Label |
|------|--------|--------|-----------|-----|------|-------|
| BTC | 4,746 (70.2%) | $88,617.70 | 205,786.98 | 4,415 | 331 | SEEN |
| ETH | 1,088 (16.1%) | $18,282.23 | 44,476.27 | 899 | 189 | SEEN |
| SOL | 409 (6.1%) | $5,291.64 | 14,442.87 | 409 | 0 | SEEN |
| XRP | 514 (7.6%) | $4,540.53 | 12,699.00 | 514 | 0 | SEEN |

## Timeframe Breakdown
| Timeframe | Trades | Volume | % of Total | Label |
|-----------|--------|--------|------------|-------|
| 5M | 6,375 | $109,008.74 | 94.3% | SEEN |
| 1H | 382 | $7,723.35 | 5.7% | SEEN |

> Note: '1H' includes named slugs like 'bitcoin-up-or-down-march-24-2026-1pm-et'. No 15M trades found in slug format.

## Price Distribution
| Bucket | Count | % | Histogram | Label |
|--------|-------|---|-----------|-------|
| 00-10c | 522 | 7.7% | ####### | SEEN |
| 10-20c | 864 | 12.8% | ############ | SEEN |
| 20-30c | 855 | 12.7% | ############ | SEEN |
| 30-40c | 950 | 14.1% | ############## | SEEN |
| 40-50c | 1,146 | 17.0% | ################ | SEEN |
| 50-60c | 746 | 11.0% | ########### | SEEN |
| 60-70c | 488 | 7.2% | ####### | SEEN |
| 70-80c | 559 | 8.3% | ######## | SEEN |
| 80-90c | 434 | 6.4% | ###### | SEEN |
| 90-100c | 193 | 2.9% | ## | SEEN |

## Size Distribution
| Stat | Value | Label |
|------|-------|-------|
| min | 0.02 | SEEN |
| p25 | 8.86 | SEEN |
| median | 23.08 | SEEN |
| mean | 41.05 | SEEN |
| p75 | 51.00 | SEEN |
| max | 1834.61 | SEEN |

## Hourly Distribution (UTC)
| Hour | Trades | Histogram | Label |
|------|--------|-----------|-------|
| 15:00 | 1,409 | ############################################## | SEEN |
| 16:00 | 1,780 | ########################################################### | SEEN |
| 17:00 | 3,568 | ###################################################################################################################### | SEEN |

## Daily Time-Series PnL
> Only 1 active day. All trades on 2026-03-24.

| Date | Trades | Volume | BUY Cost | SELL Rev | Merge Rev | Redeem Rev | Est PnL | Label |
|------|--------|--------|----------|----------|-----------|------------|---------|-------|
| 2026-03-24 | 6,757 | $116,732.09 | $103,702.70 | $13,029.39 | $35,975.58 | $443.06 | $-54,254.66 | SEEN |

## Both-Side Analysis
| Metric | Value | Label |
|--------|-------|-------|
| Total unique markets | 145 | SEEN |
| Markets with BOTH Up+Down | 119 (82.1%) | SEEN |
| Single-side markets | 26 (17.9%) | SEEN |

## Combined Price Analysis (Both-Side Markets)
- Markets with BUY on both sides: **119** SEEN
- Average combined price: **0.8927** SEEN
- Total theoretical arb profit: **$6,198.97** SEEN
- Combined < 1.00 (profitable arb): **96** markets
- Combined > 1.00 (losing arb): **23** markets

### Top 10 Most Profitable (Lowest Combined)
| Slug | Combined | Up Price | Down Price | Min Size | Arb Profit | Label |
|------|----------|----------|------------|----------|------------|-------|
| sol-updown-5m-1774367100 | 0.5358 | 0.1935 | 0.3423 | 31.0 | $14.39 | SEEN |
| xrp-updown-5m-1774367400 | 0.5827 | 0.1660 | 0.4167 | 66.0 | $27.54 | SEEN |
| sol-updown-15m-1774368900 | 0.5905 | 0.1805 | 0.4100 | 22.0 | $9.03 | SEEN |
| btc-updown-15m-1774368900 | 0.6009 | 0.1704 | 0.4304 | 636.9 | $254.22 | SEEN |
| xrp-updown-5m-1774366800 | 0.6330 | 0.1300 | 0.5030 | 16.7 | $6.12 | SEEN |
| sol-updown-15m-1774367100 | 0.6364 | 0.3544 | 0.2821 | 400.3 | $145.55 | SEEN |
| sol-updown-5m-1774368000 | 0.6391 | 0.1554 | 0.4837 | 15.0 | $5.41 | SEEN |
| xrp-updown-15m-1774370700 | 0.6761 | 0.2121 | 0.4640 | 12.2 | $3.97 | SEEN |
| xrp-updown-5m-1774371000 | 0.6897 | 0.5200 | 0.1697 | 10.0 | $3.10 | SEEN |
| sol-updown-5m-1774370400 | 0.6904 | 0.5073 | 0.1832 | 55.0 | $17.03 | SEEN |

### Top 10 Worst (Highest Combined)
| Slug | Combined | Up Price | Down Price | Min Size | Arb Profit | Label |
|------|----------|----------|------------|----------|------------|-------|
| sol-updown-5m-1774369800 | 1.0935 | 0.3135 | 0.7800 | 45.1 | $-4.22 | SEEN |
| eth-updown-5m-1774370400 | 1.0960 | 0.7100 | 0.3860 | 153.0 | $-14.68 | SEEN |
| btc-updown-15m-1774365300 | 1.1086 | 0.7002 | 0.4084 | 881.2 | $-95.66 | SEEN |
| eth-updown-5m-1774370100 | 1.1172 | 0.3834 | 0.7338 | 376.0 | $-44.08 | SEEN |
| btc-updown-5m-1774367700 | 1.1411 | 0.3659 | 0.7751 | 146.1 | $-20.61 | SEEN |
| btc-updown-5m-1774370100 | 1.1419 | 0.3699 | 0.7719 | 949.0 | $-134.64 | SEEN |
| xrp-updown-5m-1774365300 | 1.1448 | 0.5693 | 0.5755 | 110.3 | $-15.97 | SEEN |
| ethereum-up-or-down-march-24-2026-1pm-et | 1.1498 | 0.3375 | 0.8122 | 473.0 | $-70.84 | SEEN |
| eth-updown-5m-1774367700 | 1.1578 | 0.2982 | 0.8596 | 200.7 | $-31.68 | SEEN |
| btc-updown-15m-1774364400 | 1.3209 | 0.8925 | 0.4284 | 164.9 | $-52.90 | SEEN |

## Trade Timing
| Stat | Value | Label |
|------|-------|-------|
| Inter-trade gap min | 0s | SEEN |
| Inter-trade gap median | 0.0s | SEEN |
| Inter-trade gap mean | 1.2s | SEEN |
| Inter-trade gap p75 | 2s | SEEN |
| Inter-trade gap max | 128s | SEEN |
| Trades/second (avg) | 0.85 | SEEN |

### Busiest Minutes (UTC)
| Time | Trades | Label |
|------|--------|-------|
| 17:05 | 383 | SEEN |
| 17:08 | 379 | SEEN |
| 17:06 | 357 | SEEN |
| 16:58 | 194 | SEEN |
| 17:07 | 177 | SEEN |
| 17:10 | 177 | SEEN |
| 17:18 | 169 | SEEN |
| 17:16 | 166 | SEEN |
| 17:02 | 156 | SEEN |
| 17:13 | 149 | SEEN |

## Merge Analysis
| Stat | Value | Label |
|------|-------|-------|
| Total merges | 109 | SEEN |
| Total merged amount | $35,975.58 | SEEN |
| Min merge | $14.48 | SEEN |
| Max merge | $2,720.31 | SEEN |
| Mean merge | $330.05 | SEEN |
| Median merge | $150.00 | SEEN |

## Maker vs Taker
- **No maker/taker field** available in API response. SEEN
- Available fields: asset, conditionId, side, size, price, timestamp, title, slug, outcome, outcomeIndex, transactionHash, pseudonym, proxyWallet

## Win Rate / Consecutive Streaks
- **Cannot determine** from API data alone. SEEN
- API does not provide resolution outcome per market.
- MERGE events indicate profitable arb completion (buy both sides < $1, merge for $1).
- Of 145 unique markets, only 9 have merge/redeem data (API offset limit).

## Per-Market Detail
| # | Slug | Coin | TF | Up# | Dn# | BuyCost | SellRev | Mrg# | MrgRev | Rdm# | RdmRev | PnL | Label |
|---|------|------|----|-----|-----|---------|---------|------|--------|------|--------|-----|-------|
| 1 | bitcoin-up-or-down-march-24-2026-11am-et | BTC | 1H | 2 | 29 | $677 | $0 | 0 | $0 | 0 | $0 | $-677 | SEEN |
| 2 | bitcoin-up-or-down-march-24-2026-12pm-et | BTC | 1H | 31 | 14 | $1,570 | $0 | 0 | $0 | 0 | $0 | $-1,570 | SEEN |
| 3 | bitcoin-up-or-down-march-24-2026-1pm-et | BTC | 1H | 15 | 4 | $844 | $0 | 0 | $0 | 0 | $0 | $-844 | SEEN |
| 4 | btc-updown-15m-1774364400 | BTC | 5M | 4 | 12 | $473 | $421 | 0 | $0 | 0 | $0 | $-51 | SEEN |
| 5 | btc-updown-15m-1774365300 | BTC | 5M | 11 | 17 | $1,287 | $116 | 0 | $0 | 0 | $0 | $-1,171 | SEEN |
| 6 | btc-updown-15m-1774366200 | BTC | 5M | 30 | 18 | $3,155 | $790 | 0 | $0 | 0 | $0 | $-2,365 | SEEN |
| 7 | btc-updown-15m-1774367100 | BTC | 5M | 17 | 22 | $2,306 | $293 | 0 | $0 | 0 | $0 | $-2,013 | SEEN |
| 8 | btc-updown-15m-1774368000 | BTC | 5M | 11 | 20 | $1,000 | $0 | 0 | $0 | 0 | $0 | $-1,000 | SEEN |
| 9 | btc-updown-15m-1774368900 | BTC | 5M | 21 | 9 | $982 | $0 | 0 | $0 | 0 | $0 | $-982 | SEEN |
| 10 | btc-updown-15m-1774369800 | BTC | 5M | 12 | 0 | $257 | $0 | 0 | $0 | 0 | $0 | $-257 | SEEN |
| 11 | btc-updown-15m-1774370700 | BTC | 5M | 34 | 19 | $1,503 | $0 | 5 | $571 | 1 | $37 | $-895 | SEEN |
| 12 | btc-updown-15m-1774371600 | BTC | 5M | 227 | 204 | $3,647 | $0 | 24 | $3,145 | 1 | $3 | $-499 | SEEN |
| 13 | btc-updown-15m-1774372500 | BTC | 5M | 137 | 131 | $2,788 | $26 | 18 | $2,041 | 0 | $0 | $-722 | SEEN |
| 14 | btc-updown-5m-1774364700 | BTC | 5M | 10 | 3 | $230 | $208 | 0 | $0 | 0 | $0 | $-22 | SEEN |
| 15 | btc-updown-5m-1774365000 | BTC | 5M | 28 | 49 | $1,978 | $661 | 0 | $0 | 0 | $0 | $-1,317 | SEEN |
| 16 | btc-updown-5m-1774365300 | BTC | 5M | 48 | 70 | $2,780 | $1,512 | 0 | $0 | 0 | $0 | $-1,268 | SEEN |
| 17 | btc-updown-5m-1774365600 | BTC | 5M | 18 | 29 | $1,514 | $295 | 0 | $0 | 0 | $0 | $-1,219 | SEEN |
| 18 | btc-updown-5m-1774365900 | BTC | 5M | 27 | 31 | $1,540 | $455 | 0 | $0 | 0 | $0 | $-1,085 | SEEN |
| 19 | btc-updown-5m-1774366200 | BTC | 5M | 11 | 8 | $528 | $100 | 0 | $0 | 0 | $0 | $-429 | SEEN |
| 20 | btc-updown-5m-1774366500 | BTC | 5M | 4 | 33 | $978 | $376 | 0 | $0 | 0 | $0 | $-602 | SEEN |
| 21 | btc-updown-5m-1774366800 | BTC | 5M | 14 | 9 | $751 | $14 | 0 | $0 | 0 | $0 | $-737 | SEEN |
| 22 | btc-updown-5m-1774367100 | BTC | 5M | 27 | 19 | $1,466 | $291 | 0 | $0 | 0 | $0 | $-1,175 | SEEN |
| 23 | btc-updown-5m-1774367400 | BTC | 5M | 0 | 19 | $243 | $81 | 0 | $0 | 0 | $0 | $-163 | SEEN |
| 24 | btc-updown-5m-1774367700 | BTC | 5M | 2 | 8 | $407 | $0 | 0 | $0 | 0 | $0 | $-407 | SEEN |
| 25 | btc-updown-5m-1774368000 | BTC | 5M | 72 | 14 | $1,599 | $1,261 | 0 | $0 | 0 | $0 | $-338 | SEEN |
| 26 | btc-updown-5m-1774368300 | BTC | 5M | 24 | 38 | $1,872 | $290 | 0 | $0 | 0 | $0 | $-1,582 | SEEN |
| 27 | btc-updown-5m-1774368600 | BTC | 5M | 24 | 37 | $1,618 | $358 | 0 | $0 | 0 | $0 | $-1,260 | SEEN |
| 28 | btc-updown-5m-1774368900 | BTC | 5M | 8 | 27 | $545 | $355 | 0 | $0 | 0 | $0 | $-189 | SEEN |
| 29 | btc-updown-5m-1774369200 | BTC | 5M | 21 | 11 | $839 | $156 | 0 | $0 | 0 | $0 | $-683 | SEEN |
| 30 | btc-updown-5m-1774369500 | BTC | 5M | 38 | 20 | $1,614 | $656 | 0 | $0 | 0 | $0 | $-958 | SEEN |
| 31 | btc-updown-5m-1774369800 | BTC | 5M | 21 | 7 | $741 | $159 | 0 | $0 | 0 | $0 | $-582 | SEEN |
| 32 | btc-updown-5m-1774370100 | BTC | 5M | 23 | 11 | $1,115 | $166 | 0 | $0 | 0 | $0 | $-948 | SEEN |
| 33 | btc-updown-5m-1774370400 | BTC | 5M | 12 | 19 | $868 | $91 | 0 | $0 | 0 | $0 | $-777 | SEEN |
| 34 | btc-updown-5m-1774370700 | BTC | 5M | 25 | 2 | $649 | $164 | 0 | $0 | 0 | $0 | $-485 | SEEN |
| 35 | btc-updown-5m-1774371000 | BTC | 5M | 12 | 15 | $705 | $0 | 0 | $0 | 0 | $0 | $-705 | SEEN |
| 36 | btc-updown-5m-1774371300 | BTC | 5M | 187 | 141 | $3,428 | $54 | 9 | $3,549 | 1 | $40 | $215 | SEEN |
| 37 | btc-updown-5m-1774371600 | BTC | 5M | 200 | 161 | $4,173 | $130 | 6 | $3,989 | 1 | $0 | $-54 | SEEN |
| 38 | btc-updown-5m-1774371900 | BTC | 5M | 536 | 416 | $10,863 | $174 | 11 | $10,339 | 2 | $363 | $12 | SEEN |
| 39 | btc-updown-5m-1774372200 | BTC | 5M | 230 | 315 | $7,303 | $694 | 21 | $6,917 | 1 | $0 | $307 | SEEN |
| 40 | btc-updown-5m-1774372500 | BTC | 5M | 246 | 202 | $6,284 | $139 | 14 | $5,175 | 0 | $0 | $-970 | SEEN |
| 41 | btc-updown-5m-1774372800 | BTC | 5M | 54 | 59 | $1,010 | $0 | 1 | $250 | 0 | $0 | $-760 | SEEN |
| 42 | eth-updown-5m-1774364700 | ETH | 5M | 14 | 0 | $98 | $33 | 0 | $0 | 0 | $0 | $-65 | SEEN |
| 43 | eth-updown-5m-1774365000 | ETH | 5M | 23 | 69 | $1,180 | $300 | 0 | $0 | 0 | $0 | $-880 | SEEN |
| 44 | eth-updown-5m-1774365300 | ETH | 5M | 34 | 83 | $1,447 | $622 | 0 | $0 | 0 | $0 | $-825 | SEEN |
| 45 | eth-updown-5m-1774365600 | ETH | 5M | 21 | 20 | $623 | $123 | 0 | $0 | 0 | $0 | $-500 | SEEN |
| 46 | eth-updown-5m-1774365900 | ETH | 5M | 16 | 9 | $450 | $8 | 0 | $0 | 0 | $0 | $-442 | SEEN |
| 47 | eth-updown-5m-1774366200 | ETH | 5M | 7 | 7 | $239 | $21 | 0 | $0 | 0 | $0 | $-219 | SEEN |
| 48 | eth-updown-5m-1774366500 | ETH | 5M | 10 | 10 | $293 | $12 | 0 | $0 | 0 | $0 | $-281 | SEEN |
| 49 | eth-updown-5m-1774366800 | ETH | 5M | 12 | 5 | $181 | $41 | 0 | $0 | 0 | $0 | $-140 | SEEN |
| 50 | eth-updown-5m-1774367100 | ETH | 5M | 17 | 11 | $334 | $97 | 0 | $0 | 0 | $0 | $-238 | SEEN |
| 51 | eth-updown-5m-1774367400 | ETH | 5M | 10 | 43 | $586 | $20 | 0 | $0 | 0 | $0 | $-566 | SEEN |
| 52 | eth-updown-5m-1774367700 | ETH | 5M | 8 | 8 | $237 | $0 | 0 | $0 | 0 | $0 | $-237 | SEEN |
| 53 | eth-updown-5m-1774368000 | ETH | 5M | 35 | 41 | $752 | $409 | 0 | $0 | 0 | $0 | $-343 | SEEN |
| 54 | eth-updown-5m-1774368300 | ETH | 5M | 25 | 35 | $641 | $128 | 0 | $0 | 0 | $0 | $-513 | SEEN |
| 55 | eth-updown-5m-1774368600 | ETH | 5M | 32 | 39 | $649 | $275 | 0 | $0 | 0 | $0 | $-374 | SEEN |
| 56 | eth-updown-5m-1774368900 | ETH | 5M | 17 | 26 | $581 | $57 | 0 | $0 | 0 | $0 | $-524 | SEEN |
| 57 | eth-updown-5m-1774369200 | ETH | 5M | 12 | 12 | $312 | $53 | 0 | $0 | 0 | $0 | $-260 | SEEN |
| 58 | eth-updown-5m-1774369500 | ETH | 5M | 9 | 10 | $309 | $0 | 0 | $0 | 0 | $0 | $-309 | SEEN |
| 59 | eth-updown-5m-1774369800 | ETH | 5M | 13 | 9 | $324 | $20 | 0 | $0 | 0 | $0 | $-304 | SEEN |
| 60 | eth-updown-5m-1774370100 | ETH | 5M | 13 | 9 | $439 | $21 | 0 | $0 | 0 | $0 | $-418 | SEEN |
| 61 | eth-updown-5m-1774370400 | ETH | 5M | 3 | 19 | $362 | $64 | 0 | $0 | 0 | $0 | $-298 | SEEN |
| 62 | eth-updown-5m-1774370700 | ETH | 5M | 8 | 5 | $204 | $19 | 0 | $0 | 0 | $0 | $-185 | SEEN |
| 63 | eth-updown-5m-1774371000 | ETH | 5M | 1 | 2 | $20 | $0 | 0 | $0 | 0 | $0 | $-20 | SEEN |
| 64 | eth-updown-5m-1774371300 | ETH | 5M | 20 | 15 | $466 | $49 | 0 | $0 | 0 | $0 | $-417 | SEEN |
| 65 | eth-updown-5m-1774371600 | ETH | 5M | 16 | 13 | $482 | $39 | 0 | $0 | 0 | $0 | $-443 | SEEN |
| 66 | eth-updown-5m-1774371900 | ETH | 5M | 15 | 25 | $1,112 | $0 | 0 | $0 | 0 | $0 | $-1,112 | SEEN |
| 67 | eth-updown-5m-1774372200 | ETH | 5M | 5 | 19 | $317 | $35 | 0 | $0 | 0 | $0 | $-282 | SEEN |
| 68 | eth-updown-5m-1774372500 | ETH | 5M | 21 | 15 | $447 | $99 | 0 | $0 | 0 | $0 | $-348 | SEEN |
| 69 | ethereum-up-or-down-march-24-2026-11am-et | ETH | 1H | 9 | 38 | $915 | $0 | 0 | $0 | 0 | $0 | $-915 | SEEN |
| 70 | ethereum-up-or-down-march-24-2026-12pm-et | ETH | 1H | 34 | 4 | $568 | $0 | 0 | $0 | 0 | $0 | $-568 | SEEN |
| 71 | ethereum-up-or-down-march-24-2026-1pm-et | ETH | 1H | 24 | 3 | $1,168 | $0 | 0 | $0 | 0 | $0 | $-1,168 | SEEN |
| 72 | sol-updown-15m-1774365300 | SOL | 5M | 11 | 6 | $178 | $0 | 0 | $0 | 0 | $0 | $-178 | SEEN |
| 73 | sol-updown-15m-1774366200 | SOL | 5M | 4 | 12 | $161 | $0 | 0 | $0 | 0 | $0 | $-161 | SEEN |
| 74 | sol-updown-15m-1774367100 | SOL | 5M | 12 | 10 | $270 | $0 | 0 | $0 | 0 | $0 | $-270 | SEEN |
| 75 | sol-updown-15m-1774368000 | SOL | 5M | 7 | 12 | $321 | $0 | 0 | $0 | 0 | $0 | $-321 | SEEN |
| 76 | sol-updown-15m-1774368900 | SOL | 5M | 3 | 2 | $36 | $0 | 0 | $0 | 0 | $0 | $-36 | SEEN |
| 77 | sol-updown-15m-1774369800 | SOL | 5M | 12 | 0 | $70 | $0 | 0 | $0 | 0 | $0 | $-70 | SEEN |
| 78 | sol-updown-15m-1774370700 | SOL | 5M | 17 | 1 | $143 | $0 | 0 | $0 | 0 | $0 | $-143 | SEEN |
| 79 | sol-updown-15m-1774371600 | SOL | 5M | 18 | 0 | $49 | $0 | 0 | $0 | 0 | $0 | $-49 | SEEN |
| 80 | sol-updown-5m-1774365000 | SOL | 5M | 4 | 1 | $56 | $0 | 0 | $0 | 0 | $0 | $-56 | SEEN |
| 81 | sol-updown-5m-1774365300 | SOL | 5M | 6 | 7 | $243 | $0 | 0 | $0 | 0 | $0 | $-243 | SEEN |
| 82 | sol-updown-5m-1774365600 | SOL | 5M | 11 | 5 | $352 | $0 | 0 | $0 | 0 | $0 | $-352 | SEEN |
| 83 | sol-updown-5m-1774365900 | SOL | 5M | 7 | 0 | $64 | $0 | 0 | $0 | 0 | $0 | $-64 | SEEN |
| 84 | sol-updown-5m-1774366200 | SOL | 5M | 2 | 0 | $10 | $0 | 0 | $0 | 0 | $0 | $-10 | SEEN |
| 85 | sol-updown-5m-1774367100 | SOL | 5M | 3 | 2 | $32 | $0 | 0 | $0 | 0 | $0 | $-32 | SEEN |
| 86 | sol-updown-5m-1774367400 | SOL | 5M | 7 | 7 | $193 | $0 | 0 | $0 | 0 | $0 | $-193 | SEEN |
| 87 | sol-updown-5m-1774367700 | SOL | 5M | 0 | 1 | $7 | $0 | 0 | $0 | 0 | $0 | $-7 | SEEN |
| 88 | sol-updown-5m-1774368000 | SOL | 5M | 4 | 1 | $41 | $0 | 0 | $0 | 0 | $0 | $-41 | SEEN |
| 89 | sol-updown-5m-1774368300 | SOL | 5M | 8 | 3 | $101 | $0 | 0 | $0 | 0 | $0 | $-101 | SEEN |
| 90 | sol-updown-5m-1774368600 | SOL | 5M | 1 | 3 | $119 | $0 | 0 | $0 | 0 | $0 | $-119 | SEEN |
| 91 | sol-updown-5m-1774368900 | SOL | 5M | 3 | 2 | $42 | $0 | 0 | $0 | 0 | $0 | $-42 | SEEN |
| 92 | sol-updown-5m-1774369200 | SOL | 5M | 2 | 1 | $54 | $0 | 0 | $0 | 0 | $0 | $-54 | SEEN |
| 93 | sol-updown-5m-1774369500 | SOL | 5M | 4 | 2 | $114 | $0 | 0 | $0 | 0 | $0 | $-114 | SEEN |
| 94 | sol-updown-5m-1774369800 | SOL | 5M | 5 | 1 | $51 | $0 | 0 | $0 | 0 | $0 | $-51 | SEEN |
| 95 | sol-updown-5m-1774370100 | SOL | 5M | 3 | 0 | $18 | $0 | 0 | $0 | 0 | $0 | $-18 | SEEN |
| 96 | sol-updown-5m-1774370400 | SOL | 5M | 2 | 3 | $52 | $0 | 0 | $0 | 0 | $0 | $-52 | SEEN |
| 97 | sol-updown-5m-1774370700 | SOL | 5M | 8 | 0 | $91 | $0 | 0 | $0 | 0 | $0 | $-91 | SEEN |
| 98 | sol-updown-5m-1774371000 | SOL | 5M | 0 | 6 | $41 | $0 | 0 | $0 | 0 | $0 | $-41 | SEEN |
| 99 | sol-updown-5m-1774371300 | SOL | 5M | 5 | 2 | $72 | $0 | 0 | $0 | 0 | $0 | $-72 | SEEN |
| 100 | sol-updown-5m-1774371600 | SOL | 5M | 0 | 4 | $32 | $0 | 0 | $0 | 0 | $0 | $-32 | SEEN |
| 101 | sol-updown-5m-1774371900 | SOL | 5M | 27 | 36 | $1,214 | $0 | 0 | $0 | 0 | $0 | $-1,214 | SEEN |
| 102 | sol-updown-5m-1774372200 | SOL | 5M | 1 | 5 | $17 | $0 | 0 | $0 | 0 | $0 | $-17 | SEEN |
| 103 | sol-updown-5m-1774372500 | SOL | 5M | 0 | 2 | $48 | $0 | 0 | $0 | 0 | $0 | $-48 | SEEN |
| 104 | solana-up-or-down-march-24-2026-11am-et | SOL | 1H | 5 | 10 | $221 | $0 | 0 | $0 | 0 | $0 | $-221 | SEEN |
| 105 | solana-up-or-down-march-24-2026-12pm-et | SOL | 1H | 29 | 7 | $525 | $0 | 0 | $0 | 0 | $0 | $-525 | SEEN |
| 106 | solana-up-or-down-march-24-2026-1pm-et | SOL | 1H | 24 | 0 | $253 | $0 | 0 | $0 | 0 | $0 | $-253 | SEEN |
| 107 | xrp-up-or-down-march-24-2026-11am-et | XRP | 1H | 11 | 22 | $358 | $0 | 0 | $0 | 0 | $0 | $-358 | SEEN |
| 108 | xrp-up-or-down-march-24-2026-12pm-et | XRP | 1H | 31 | 6 | $218 | $0 | 0 | $0 | 0 | $0 | $-218 | SEEN |
| 109 | xrp-up-or-down-march-24-2026-1pm-et | XRP | 1H | 23 | 7 | $406 | $0 | 0 | $0 | 0 | $0 | $-406 | SEEN |
| 110 | xrp-updown-15m-1774364400 | XRP | 5M | 7 | 10 | $175 | $0 | 0 | $0 | 0 | $0 | $-175 | SEEN |
| 111 | xrp-updown-15m-1774365300 | XRP | 5M | 16 | 4 | $303 | $0 | 0 | $0 | 0 | $0 | $-303 | SEEN |
| 112 | xrp-updown-15m-1774366200 | XRP | 5M | 6 | 0 | $62 | $0 | 0 | $0 | 0 | $0 | $-62 | SEEN |
| 113 | xrp-updown-15m-1774367100 | XRP | 5M | 10 | 3 | $139 | $0 | 0 | $0 | 0 | $0 | $-139 | SEEN |
| 114 | xrp-updown-15m-1774368000 | XRP | 5M | 12 | 15 | $156 | $0 | 0 | $0 | 0 | $0 | $-156 | SEEN |
| 115 | xrp-updown-15m-1774368900 | XRP | 5M | 1 | 6 | $30 | $0 | 0 | $0 | 0 | $0 | $-30 | SEEN |
| 116 | xrp-updown-15m-1774369800 | XRP | 5M | 17 | 0 | $76 | $0 | 0 | $0 | 0 | $0 | $-76 | SEEN |
| 117 | xrp-updown-15m-1774370700 | XRP | 5M | 13 | 3 | $42 | $0 | 0 | $0 | 0 | $0 | $-42 | SEEN |
| 118 | xrp-updown-15m-1774371600 | XRP | 5M | 28 | 15 | $245 | $0 | 0 | $0 | 0 | $0 | $-245 | SEEN |
| 119 | xrp-updown-15m-1774372500 | XRP | 5M | 3 | 0 | $8 | $0 | 0 | $0 | 0 | $0 | $-8 | SEEN |
| 120 | xrp-updown-5m-1774364700 | XRP | 5M | 4 | 0 | $37 | $0 | 0 | $0 | 0 | $0 | $-37 | SEEN |
| 121 | xrp-updown-5m-1774365000 | XRP | 5M | 3 | 4 | $90 | $0 | 0 | $0 | 0 | $0 | $-90 | SEEN |
| 122 | xrp-updown-5m-1774365300 | XRP | 5M | 7 | 18 | $368 | $0 | 0 | $0 | 0 | $0 | $-368 | SEEN |
| 123 | xrp-updown-5m-1774365600 | XRP | 5M | 10 | 4 | $144 | $0 | 0 | $0 | 0 | $0 | $-144 | SEEN |
| 124 | xrp-updown-5m-1774365900 | XRP | 5M | 1 | 0 | $9 | $0 | 0 | $0 | 0 | $0 | $-9 | SEEN |
| 125 | xrp-updown-5m-1774366200 | XRP | 5M | 1 | 2 | $15 | $0 | 0 | $0 | 0 | $0 | $-15 | SEEN |
| 126 | xrp-updown-5m-1774366500 | XRP | 5M | 0 | 2 | $3 | $0 | 0 | $0 | 0 | $0 | $-3 | SEEN |
| 127 | xrp-updown-5m-1774366800 | XRP | 5M | 1 | 1 | $12 | $0 | 0 | $0 | 0 | $0 | $-12 | SEEN |
| 128 | xrp-updown-5m-1774367100 | XRP | 5M | 6 | 0 | $35 | $0 | 0 | $0 | 0 | $0 | $-35 | SEEN |
| 129 | xrp-updown-5m-1774367400 | XRP | 5M | 6 | 2 | $48 | $0 | 0 | $0 | 0 | $0 | $-48 | SEEN |
| 130 | xrp-updown-5m-1774367700 | XRP | 5M | 2 | 0 | $10 | $0 | 0 | $0 | 0 | $0 | $-10 | SEEN |
| 131 | xrp-updown-5m-1774368000 | XRP | 5M | 2 | 7 | $118 | $0 | 0 | $0 | 0 | $0 | $-118 | SEEN |
| 132 | xrp-updown-5m-1774368300 | XRP | 5M | 3 | 2 | $51 | $0 | 0 | $0 | 0 | $0 | $-51 | SEEN |
| 133 | xrp-updown-5m-1774368600 | XRP | 5M | 5 | 1 | $26 | $0 | 0 | $0 | 0 | $0 | $-26 | SEEN |
| 134 | xrp-updown-5m-1774368900 | XRP | 5M | 2 | 6 | $61 | $0 | 0 | $0 | 0 | $0 | $-61 | SEEN |
| 135 | xrp-updown-5m-1774369200 | XRP | 5M | 7 | 3 | $71 | $0 | 0 | $0 | 0 | $0 | $-71 | SEEN |
| 136 | xrp-updown-5m-1774369500 | XRP | 5M | 10 | 0 | $31 | $0 | 0 | $0 | 0 | $0 | $-31 | SEEN |
| 137 | xrp-updown-5m-1774369800 | XRP | 5M | 6 | 1 | $42 | $0 | 0 | $0 | 0 | $0 | $-42 | SEEN |
| 138 | xrp-updown-5m-1774370100 | XRP | 5M | 5 | 0 | $29 | $0 | 0 | $0 | 0 | $0 | $-29 | SEEN |
| 139 | xrp-updown-5m-1774370400 | XRP | 5M | 1 | 12 | $96 | $0 | 0 | $0 | 0 | $0 | $-96 | SEEN |
| 140 | xrp-updown-5m-1774370700 | XRP | 5M | 9 | 0 | $39 | $0 | 0 | $0 | 0 | $0 | $-39 | SEEN |
| 141 | xrp-updown-5m-1774371000 | XRP | 5M | 1 | 6 | $38 | $0 | 0 | $0 | 0 | $0 | $-38 | SEEN |
| 142 | xrp-updown-5m-1774371600 | XRP | 5M | 6 | 9 | $150 | $0 | 0 | $0 | 0 | $0 | $-150 | SEEN |
| 143 | xrp-updown-5m-1774371900 | XRP | 5M | 33 | 25 | $737 | $0 | 0 | $0 | 0 | $0 | $-737 | SEEN |
| 144 | xrp-updown-5m-1774372200 | XRP | 5M | 2 | 3 | $31 | $0 | 0 | $0 | 0 | $0 | $-31 | SEEN |
| 145 | xrp-updown-5m-1774372500 | XRP | 5M | 4 | 0 | $31 | $0 | 0 | $0 | 0 | $0 | $-31 | SEEN |

## Market Entry Speed (Top 20 Busiest)
| Slug | Trades | Duration | Rate | Label |
|------|--------|----------|------|-------|
| xrp-updown-5m-1774364700 | 4 | 0s | 4.00/s | SEEN |
| btc-updown-5m-1774371900 | 952 | 270s | 3.53/s | SEEN |
| eth-updown-5m-1774371900 | 40 | 20s | 2.00/s | SEEN |
| sol-updown-5m-1774372500 | 2 | 0s | 2.00/s | SEEN |
| btc-updown-5m-1774372200 | 545 | 274s | 1.99/s | SEEN |
| btc-updown-5m-1774372500 | 448 | 266s | 1.68/s | SEEN |
| btc-updown-5m-1774371600 | 361 | 250s | 1.44/s | SEEN |
| btc-updown-5m-1774372800 | 113 | 80s | 1.41/s | SEEN |
| btc-updown-5m-1774371300 | 328 | 286s | 1.15/s | SEEN |
| sol-updown-5m-1774366200 | 2 | 2s | 1.00/s | SEEN |
| sol-updown-5m-1774370100 | 3 | 4s | 0.75/s | SEEN |
| btc-updown-15m-1774372500 | 268 | 380s | 0.71/s | SEEN |
| btc-updown-15m-1774371600 | 431 | 684s | 0.63/s | SEEN |
| btc-updown-5m-1774365600 | 47 | 90s | 0.52/s | SEEN |
| btc-updown-5m-1774365300 | 118 | 272s | 0.43/s | SEEN |
| eth-updown-5m-1774365300 | 117 | 270s | 0.43/s | SEEN |
| eth-updown-5m-1774364700 | 14 | 34s | 0.41/s | SEEN |
| sol-updown-5m-1774368600 | 4 | 10s | 0.40/s | SEEN |
| btc-updown-5m-1774364700 | 13 | 36s | 0.36/s | SEEN |
| eth-updown-5m-1774365600 | 41 | 122s | 0.34/s | SEEN |

## Raw Trade Samples
### First 10 Trades
| # | Timestamp | Side | Outcome | Price | Size | Title |
|---|-----------|------|---------|-------|------|-------|
| 1 | 15:08:53 | BUY | Down | 0.5700 | 40.50 | XRP Up or Down - March 24, 11:00AM-11:15AM ET |
| 2 | 15:08:59 | BUY | Up | 0.3600 | 67.00 | Bitcoin Up or Down - March 24, 11:05AM-11:10AM ET |
| 3 | 15:08:59 | SELL | Down | 0.2600 | 7.56 | Bitcoin Up or Down - March 24, 11:00AM-11:15AM ET |
| 4 | 15:08:59 | BUY | Up | 0.5500 | 1.36 | XRP Up or Down - March 24, 11:00AM-11:15AM ET |
| 5 | 15:09:05 | BUY | Up | 0.2900 | 29.62 | Ethereum Up or Down - March 24, 11:05AM-11:10AM ET |
| 6 | 15:09:09 | SELL | Up | 0.2500 | 51.00 | Ethereum Up or Down - March 24, 11:05AM-11:10AM ET |
| 7 | 15:09:13 | BUY | Up | 0.4243 | 51.00 | Ethereum Up or Down - March 24, 11:05AM-11:10AM ET |
| 8 | 15:09:13 | SELL | Down | 0.2100 | 92.99 | Bitcoin Up or Down - March 24, 11:00AM-11:15AM ET |
| 9 | 15:09:13 | BUY | Up | 0.4900 | 101.00 | Bitcoin Up or Down - March 24, 11:05AM-11:10AM ET |
| 10 | 15:09:13 | BUY | Up | 0.5000 | 101.00 | Bitcoin Up or Down - March 24, 11:05AM-11:10AM ET |

### Last 10 Trades
| # | Timestamp | Side | Outcome | Price | Size | Title |
|---|-----------|------|---------|-------|------|-------|
| 1 | 17:21:25 | BUY | Down | 0.1600 | 13.00 | Bitcoin Up or Down - March 24, 1:20PM-1:25PM ET |
| 2 | 17:21:25 | BUY | Down | 0.1600 | 10.00 | Bitcoin Up or Down - March 24, 1:20PM-1:25PM ET |
| 3 | 17:21:25 | BUY | Down | 0.5100 | 20.00 | Bitcoin Up or Down - March 24, 1:15PM-1:30PM ET |
| 4 | 17:21:25 | BUY | Up | 0.4700 | 25.60 | Bitcoin Up or Down - March 24, 1:15PM-1:30PM ET |
| 5 | 17:21:29 | BUY | Up | 0.8300 | 8.86 | Bitcoin Up or Down - March 24, 1:20PM-1:25PM ET |
| 6 | 17:21:29 | BUY | Up | 0.7700 | 20.87 | Bitcoin Up or Down - March 24, 1:20PM-1:25PM ET |
| 7 | 17:21:29 | BUY | Up | 0.7800 | 20.00 | Bitcoin Up or Down - March 24, 1:20PM-1:25PM ET |
| 8 | 17:21:29 | BUY | Up | 0.7800 | 15.00 | Bitcoin Up or Down - March 24, 1:20PM-1:25PM ET |
| 9 | 17:21:29 | BUY | Up | 0.7800 | 15.00 | Bitcoin Up or Down - March 24, 1:20PM-1:25PM ET |
| 10 | 17:21:29 | BUY | Up | 0.8300 | 25.00 | Bitcoin Up or Down - March 24, 1:20PM-1:25PM ET |

## Key Observations
1. **Single-day operation**: All 6,757 trades in 2.21 hours on March 24, 2026 (SEEN)
2. **Pure BTC-dominant**: BTC = 4,746 trades (70.2%), ETH = 1,088, SOL = 409, XRP = 514 (SEEN)
3. **5M timeframe dominant**: 6,375 trades (94.3%) on 5M markets (SEEN)
4. **100% both-side rate by slug**: 119/145 markets (82.1%) have both Up+Down trades (SEEN)
5. **BUY-heavy**: 6,237 BUY vs 520 SELL = 12.0x ratio (SEEN)
6. **Merge-based arb strategy**: 109 MERGE events = 35,975.58 revenue. Buys both sides, merges for $1 (SEEN)
7. **Average combined price**: 0.8927 across 119 both-side markets (SEEN)
8. **Extremely fast execution**: median inter-trade gap = 0s, up to 4.00 trades/sec peak (SEEN)
9. **API data is INCOMPLETE**: offset cap of 3000 means we see max ~7000 events. Merge/redeem data only from activity endpoint (last ~25min window). True PnL unknown. (SEEN)

---
*Data fetched from Polymarket data-api. API offset limit = 3000. Two endpoints combined (trades + activity) with dedup.*