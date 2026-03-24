# Wallet Reverse Engineering — Entry Timing / Odds / Sizing
> Generated: 2026-03-24 18:02 UTC
> Wallets: Female-Billing, Unlawful-Shear
> Method: data-api.polymarket.com/trades (max 3500 per wallet)

---

## 1. Head-to-Head Comparison

| Metric | Female-Billing | Unlawful-Shear |
|--------|--------|--------|
| **Total trades** | 3,462 | 3,496 |
| **Unique windows** | 98 | 48 |
| **Volume ($)** | $91,444 | $96,160 |
| **Total shares** | 193,001 | 185,450 |
| **Time span** | 1.9h | 2.9h |
| **Both-side rate** | 94.9% | 100.0% |
| **Combined (mean)** | 0.9492 | 1.0222 |
| **Combined (median)** | 0.9508 | 1.0356 |
| **Combined < $1.00** | 68% | 23% |
| **Entry timing (median)** | 17.0s | 9.0s |
| **Entry timing (p25-p75)** | 13-65s | 7-13s |
| **Lean: Up%** | 43% | 33% |
| **Lean ratio (median)** | 1.34 | 1.36 |
| **Size (median shares)** | 38.0 | 29.1 |
| **Size (p25-p75)** | 18.6-66.0 | 20.0-58.5 |
| **Sells** | 0 | 0 |

## 2. Coin Breakdown

### Female-Billing
| Coin | Windows | Shares | Volume |
|------|---------|--------|--------|
| BTC | 25 | 123,485 | $57,659 |
| ETH | 25 | 49,417 | $23,975 |
| SOL | 24 | 12,802 | $6,175 |
| XRP | 24 | 7,297 | $3,634 |

### Unlawful-Shear
| Coin | Windows | Shares | Volume |
|------|---------|--------|--------|
| BTC | 48 | 185,450 | $96,160 |

## 3. Timeframe Breakdown

### Female-Billing
| TF | Windows | Shares |
|----|---------|--------|
| 5M | 66 | 96,177 |
| 15M | 32 | 96,824 |

### Unlawful-Shear
| TF | Windows | Shares |
|----|---------|--------|
| 5M | 36 | 131,066 |
| 15M | 12 | 54,384 |

## 4. Entry Price Distribution (BUY trades)

### Female-Billing
| Price Range | Count | Bar |
|-------------|-------|-----|
| 0.0-0.1 | 271 | ████████████████ |
| 0.1-0.2 | 278 | █████████████████ |
| 0.2-0.3 | 362 | ██████████████████████ |
| 0.3-0.4 | 424 | ██████████████████████████ |
| 0.4-0.5 | 446 | ███████████████████████████ |
| 0.5-0.6 | 476 | █████████████████████████████ |
| 0.6-0.7 | 482 | ██████████████████████████████ |
| 0.7-0.8 | 347 | █████████████████████ |
| 0.8-0.9 | 254 | ███████████████ |
| 0.9-1.0 | 122 | ███████ |

### Unlawful-Shear
| Price Range | Count | Bar |
|-------------|-------|-----|
| 0.0-0.1 | 11 | █ |
| 0.1-0.2 | 17 | █ |
| 0.2-0.3 | 50 | █ |
| 0.3-0.4 | 315 | ███████ |
| 0.4-0.5 | 1044 | ████████████████████████ |
| 0.5-0.6 | 1295 | ██████████████████████████████ |
| 0.6-0.7 | 598 | █████████████ |
| 0.7-0.8 | 108 | ██ |
| 0.8-0.9 | 42 | █ |
| 0.9-1.0 | 16 | █ |

## 5. Entry Timing (seconds after window open)

### Female-Billing
- Min: 11s | P25: 13s | Median: 17.0s | P75: 65s | Max: 611s

### Unlawful-Shear
- Min: 7s | P25: 7s | Median: 9.0s | P75: 13s | Max: 221s

## 6. Combined Price Analysis (both-side windows only)

### Female-Billing
- N = 93 windows
- Mean: 0.9492 | Median: 0.9508 | Stdev: 0.1569
- Min: 0.3767 | Max: 1.2989
- Below $1.00: 68%

#### Top 10 Best (lowest combined)
| Slug | Coin | TF | Combined | Up$ | Dn$ | Up# | Dn# | Lean | Entry |
|------|------|----|----------|-----|-----|-----|-----|------|-------|
| ...sol-updown-5m-1774371600 | SOL | 5M | **0.3767** | 0.1473 | 0.2294 | 177.0 | 195.6 | DOWN | 41s |
| ...xrp-updown-5m-1774370700 | XRP | 5M | **0.6253** | 0.1319 | 0.4934 | 42.4 | 118.7 | DOWN | 15s |
| ...sol-updown-15m-1774370700 | SOL | 15M | **0.6324** | 0.1745 | 0.4578 | 292.2 | 306.0 | DOWN | 11s |
| ...xrp-updown-15m-1774368900 | XRP | 15M | **0.6656** | 0.1756 | 0.49 | 190.4 | 20.0 | UP | 169s |
| ...xrp-updown-5m-1774372200 | XRP | 5M | **0.6771** | 0.6064 | 0.0708 | 170.2 | 71.6 | UP | 29s |
| ...sol-updown-5m-1774370700 | SOL | 5M | **0.7349** | 0.1814 | 0.5536 | 200.3 | 303.3 | DOWN | 11s |
| ...sol-updown-5m-1774373100 | SOL | 5M | **0.7378** | 0.04 | 0.6978 | 44.0 | 238.6 | DOWN | 19s |
| ...sol-updown-15m-1774371600 | SOL | 15M | **0.7392** | 0.0952 | 0.644 | 390.8 | 389.9 | UP | 13s |
| ...eth-updown-5m-1774370700 | ETH | 5M | **0.7441** | 0.2131 | 0.531 | 591.0 | 453.8 | UP | 11s |
| ...sol-updown-5m-1774369800 | SOL | 5M | **0.7461** | 0.1061 | 0.64 | 178.2 | 42.0 | UP | 65s |

#### Top 10 Worst (highest combined)
| Slug | Coin | TF | Combined | Up$ | Dn$ | Up# | Dn# | Lean | Entry |
|------|------|----|----------|-----|-----|-----|-----|------|-------|
| ...sol-updown-5m-1774371000 | SOL | 5M | **1.2989** | 0.96 | 0.3389 | 51.0 | 273.3 | DOWN | 11s |
| ...xrp-updown-15m-1774369800 | XRP | 15M | **1.2759** | 0.31 | 0.9659 | 18.0 | 3.7 | UP | 249s |
| ...sol-updown-5m-1774369500 | SOL | 5M | **1.2438** | 0.4062 | 0.8376 | 194.0 | 278.8 | DOWN | 11s |
| ...xrp-updown-5m-1774369500 | XRP | 5M | **1.2262** | 0.3575 | 0.8686 | 55.3 | 220.9 | DOWN | 11s |
| ...xrp-updown-5m-1774372800 | XRP | 5M | **1.2109** | 0.6095 | 0.6014 | 152.6 | 308.5 | DOWN | 117s |
| ...sol-updown-5m-1774373400 | SOL | 5M | **1.1881** | 0.6059 | 0.5822 | 88.6 | 153.3 | DOWN | 21s |
| ...xrp-updown-5m-1774371900 | XRP | 5M | **1.1778** | 0.4334 | 0.7444 | 443.2 | 465.7 | DOWN | 17s |
| ...eth-updown-5m-1774371000 | ETH | 5M | **1.1523** | 0.9364 | 0.2159 | 116.0 | 311.8 | DOWN | 11s |
| ...eth-updown-5m-1774374900 | ETH | 5M | **1.1514** | 0.5634 | 0.588 | 1888.0 | 1790.9 | UP | 11s |
| ...eth-updown-15m-1774374300 | ETH | 15M | **1.1501** | 0.8141 | 0.336 | 1914.2 | 2120.3 | DOWN | 75s |

### Unlawful-Shear
- N = 48 windows
- Mean: 1.0222 | Median: 1.0356 | Stdev: 0.0755
- Min: 0.7767 | Max: 1.2054
- Below $1.00: 23%

#### Top 10 Best (lowest combined)
| Slug | Coin | TF | Combined | Up$ | Dn$ | Up# | Dn# | Lean | Entry |
|------|------|----|----------|-----|-----|-----|-----|------|-------|
| ...btc-updown-5m-1774371000 | BTC | 5M | **0.7767** | 0.5736 | 0.2031 | 897.5 | 2047.0 | DOWN | 7s |
| ...btc-updown-5m-1774371900 | BTC | 5M | **0.7948** | 0.1366 | 0.6583 | 421.5 | 2091.2 | DOWN | 15s |
| ...btc-updown-5m-1774368900 | BTC | 5M | **0.8619** | 0.6069 | 0.2549 | 500.5 | 522.3 | DOWN | 33s |
| ...btc-updown-5m-1774365300 | BTC | 5M | **0.9072** | 0.4356 | 0.4716 | 4427.3 | 6060.5 | DOWN | 9s |
| ...btc-updown-5m-1774371600 | BTC | 5M | **0.9168** | 0.2965 | 0.6203 | 486.7 | 1078.0 | DOWN | 17s |
| ...btc-updown-15m-1774372500 | BTC | 15M | **0.9569** | 0.4449 | 0.512 | 2288.6 | 2470.7 | DOWN | 11s |
| ...btc-updown-5m-1774369500 | BTC | 5M | **0.9634** | 0.414 | 0.5494 | 764.4 | 974.9 | DOWN | 9s |
| ...btc-updown-5m-1774374000 | BTC | 5M | **0.9671** | 0.415 | 0.5521 | 432.0 | 622.8 | DOWN | 7s |
| ...btc-updown-5m-1774373100 | BTC | 5M | **0.9679** | 0.4522 | 0.5157 | 1114.8 | 828.6 | UP | 7s |
| ...btc-updown-5m-1774372800 | BTC | 5M | **0.9916** | 0.6404 | 0.3513 | 490.3 | 219.8 | UP | 21s |

#### Top 10 Worst (highest combined)
| Slug | Coin | TF | Combined | Up$ | Dn$ | Up# | Dn# | Lean | Entry |
|------|------|----|----------|-----|-----|-----|-----|------|-------|
| ...btc-updown-5m-1774367400 | BTC | 5M | **1.2054** | 0.8586 | 0.3468 | 2718.2 | 1350.0 | UP | 7s |
| ...btc-updown-15m-1774367100 | BTC | 15M | **1.1582** | 0.5929 | 0.5654 | 2221.1 | 1352.5 | UP | 7s |
| ...btc-updown-5m-1774366200 | BTC | 5M | **1.107** | 0.5328 | 0.5742 | 1011.2 | 1099.2 | DOWN | 7s |
| ...btc-updown-5m-1774368300 | BTC | 5M | **1.1002** | 0.633 | 0.4673 | 624.7 | 669.5 | DOWN | 15s |
| ...btc-updown-5m-1774369200 | BTC | 5M | **1.0895** | 0.4806 | 0.6089 | 378.8 | 557.2 | DOWN | 9s |
| ...btc-updown-5m-1774365600 | BTC | 5M | **1.0735** | 0.5062 | 0.5673 | 1098.3 | 471.3 | UP | 9s |
| ...btc-updown-5m-1774373700 | BTC | 5M | **1.0692** | 0.5777 | 0.4915 | 978.0 | 908.0 | UP | 13s |
| ...btc-updown-5m-1774369800 | BTC | 5M | **1.0686** | 0.5061 | 0.5625 | 1275.1 | 1250.2 | UP | 7s |
| ...btc-updown-5m-1774364700 | BTC | 5M | **1.0644** | 0.4959 | 0.5685 | 2584.1 | 3539.9 | DOWN | 221s |
| ...btc-updown-5m-1774367100 | BTC | 5M | **1.0582** | 0.4779 | 0.5803 | 1158.4 | 1849.0 | DOWN | 9s |

## 7. Directional Lean Analysis

### Female-Billing
- Up lean: 43% | Down lean: 57%
- Lean ratio — Mean: 1.88 | Median: 1.34 | Max: 13.72

### Unlawful-Shear
- Up lean: 33% | Down lean: 67%
- Lean ratio — Mean: 1.56 | Median: 1.36 | Max: 4.96

## 8. Daily Activity

### Female-Billing
| Date | Trades | Windows | Volume |
|------|--------|---------|--------|
| 2026-03-24 | 3462 | 98 | $91,444 |

### Unlawful-Shear
| Date | Trades | Windows | Volume |
|------|--------|---------|--------|
| 2026-03-24 | 3496 | 48 | $96,160 |

## 9. Hourly Distribution (UTC)

### Female-Billing
| Hour | Trades | Bar |
|------|--------|-----|
| 00:00 | 0 |  |
| 01:00 | 0 |  |
| 02:00 | 0 |  |
| 03:00 | 0 |  |
| 04:00 | 0 |  |
| 05:00 | 0 |  |
| 06:00 | 0 |  |
| 07:00 | 0 |  |
| 08:00 | 0 |  |
| 09:00 | 0 |  |
| 10:00 | 0 |  |
| 11:00 | 0 |  |
| 12:00 | 0 |  |
| 13:00 | 0 |  |
| 14:00 | 0 |  |
| 15:00 | 0 |  |
| 16:00 | 1521 | ███████████████████████ |
| 17:00 | 1941 | ██████████████████████████████ |
| 18:00 | 0 |  |
| 19:00 | 0 |  |
| 20:00 | 0 |  |
| 21:00 | 0 |  |
| 22:00 | 0 |  |
| 23:00 | 0 |  |

### Unlawful-Shear
| Hour | Trades | Bar |
|------|--------|-----|
| 00:00 | 0 |  |
| 01:00 | 0 |  |
| 02:00 | 0 |  |
| 03:00 | 0 |  |
| 04:00 | 0 |  |
| 05:00 | 0 |  |
| 06:00 | 0 |  |
| 07:00 | 0 |  |
| 08:00 | 0 |  |
| 09:00 | 0 |  |
| 10:00 | 0 |  |
| 11:00 | 0 |  |
| 12:00 | 0 |  |
| 13:00 | 0 |  |
| 14:00 | 0 |  |
| 15:00 | 1390 | ██████████████████████████████ |
| 16:00 | 1215 | ██████████████████████████ |
| 17:00 | 891 | ███████████████████ |
| 18:00 | 0 |  |
| 19:00 | 0 |  |
| 20:00 | 0 |  |
| 21:00 | 0 |  |
| 22:00 | 0 |  |
| 23:00 | 0 |  |

## 10. Position Sizing

### Female-Billing
- Mean: 55.7 | Median: 38.0 | Min: 0.0 | Max: 1300.0
- P25: 18.6 | P75: 66.0

### Unlawful-Shear
- Mean: 53.0 | Median: 29.1 | Min: 0.1 | Max: 1858.5
- P25: 20.0 | P75: 58.5

## 11. Actionable Patterns for Our Bot

### Entry Timing
- When do they enter relative to window start?
- Can we replicate this timing?

### Price/Odds Selection
- What price range do they target?
- Combined < $1.00 or paying spread for direction?

### Sizing Rules
- Fixed lot or variable?
- How does sizing relate to confidence/lean?

### Direction Selection
- Pure arb (50:50) or directional lean?
- Lean ratio correlate with combined price?
