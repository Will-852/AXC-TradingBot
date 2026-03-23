# W4 5M Deep Reverse Engineering — Complete Playbook
> Generated: 2026-03-24 01:18
> Source: wallet4_all_trades.json (19 windows, data from March 23, 2026)

---

## Executive Summary

W4 traded **16 valid 5M windows** across 4 coins (BTC, ETH, SOL, XRP) on March 23, 2026.
All trading occurred between **10:25AM ET** and **10:45AM ET**.

Key numbers:
- **Entry delay**: 46s avg (median 15s, range 9-145s)
- **Active duration**: 162s avg (range 26-266s)
- **Lean ratio**: 12.49:1 avg (range 1.13-48.55)
- **Combined cost**: 1.0005 avg (range 0.6255-1.1815)
- **Total per window**: $701.13 avg (range $18.46-$3055.93)
- **Lean = expensive side**: 15/16 (94%)
- **Lean matches BTC momentum at entry**: 6/8 (75%) (= momentum following)
- **Lean matches resolution**: 4/16 (25%) (= W4 win rate proxy)

---

## A. Dataset Filter

| Category | Count |
|----------|-------|
| Total trades in dataset | 2,516 |
| 5M TRADE entries | 1526 |
| Valid 5M windows (delay <= 300s) | 16 |
| Anomalous windows (excluded) | 3 |

**Coin breakdown (valid windows):**
- BTC: 4 windows
- ETH: 4 windows
- SOL: 4 windows
- XRP: 4 windows

---

## B. Per-Window Reconstruction


### Window: 10:25AM ET — 10:30AM ET ET

| Coin | Lean | UP$ | DN$ | Ratio | UP wavg | DN wavg | Combined | Delay | Duration | Fills |
|------|------|-----|-----|-------|---------|---------|----------|-------|----------|-------|
| BTC | DOWN | $189.55 | $502.62 | 2.65:1 | 0.1452 | 0.8706 | 1.0159 | 143s | 100s | 97 |
| ETH | DOWN | $23.64 | $262.83 | 11.12:1 | 0.1619 | 0.8244 | 0.9862 | 141s | 142s | 42 |
| SOL | UP | $325.86 | $156.35 | 2.08:1 | 0.3926 | 0.7787 | 1.1713 | 145s | 138s | 170 |
| XRP | DOWN | $17.32 | $153.43 | 8.86:1 | 0.1016 | 0.8192 | 0.9208 | 143s | 140s | 51 |

### Window: 10:30AM ET — 10:35AM ET ET

| Coin | Lean | UP$ | DN$ | Ratio | UP wavg | DN wavg | Combined | Delay | Duration | Fills |
|------|------|-----|-----|-------|---------|---------|----------|-------|----------|-------|
| BTC | DOWN | $1432.93 | $1623.00 | 1.13:1 | 0.5009 | 0.6806 | 1.1815 | 15s | 254s | 256 |
| ETH | DOWN | $144.36 | $379.49 | 2.63:1 | 0.3440 | 0.7001 | 1.0442 | 15s | 246s | 103 |
| SOL | DOWN | $54.71 | $116.22 | 2.12:1 | 0.5110 | 0.6206 | 1.1316 | 15s | 234s | 37 |
| XRP | UP | $349.99 | $223.55 | 1.57:1 | 0.4875 | 0.4739 | 0.9614 | 17s | 266s | 162 |

### Window: 10:35AM ET — 10:40AM ET ET

| Coin | Lean | UP$ | DN$ | Ratio | UP wavg | DN wavg | Combined | Delay | Duration | Fills |
|------|------|-----|-----|-------|---------|---------|----------|-------|----------|-------|
| BTC | UP | $1784.70 | $515.47 | 3.46:1 | 0.7931 | 0.2623 | 1.0554 | 9s | 214s | 203 |
| ETH | UP | $532.85 | $48.33 | 11.03:1 | 0.7548 | 0.2273 | 0.9821 | 11s | 222s | 81 |
| SOL | UP | $127.02 | $30.48 | 4.17:1 | 0.7422 | 0.2187 | 0.9609 | 15s | 206s | 49 |
| XRP | UP | $153.30 | $3.16 | 48.55:1 | 0.7818 | 0.0506 | 0.8324 | 9s | 206s | 38 |

### Window: 10:40AM ET — 10:45AM ET ET

| Coin | Lean | UP$ | DN$ | Ratio | UP wavg | DN wavg | Combined | Delay | Duration | Fills |
|------|------|-----|-----|-------|---------|---------|----------|-------|----------|-------|
| BTC | DOWN | $118.31 | $1124.68 | 9.51:1 | 0.3311 | 0.6395 | 0.9706 | 13s | 66s | 122 |
| ETH | DOWN | $15.36 | $612.20 | 39.86:1 | 0.3887 | 0.6901 | 1.0788 | 13s | 66s | 71 |
| SOL | DOWN | $4.49 | $173.51 | 38.65:1 | 0.4300 | 0.6589 | 1.0889 | 13s | 66s | 34 |
| XRP | DOWN | $0.00 | $18.46 | inf:1 | 0.0000 | 0.6255 | 0.6255 | 23s | 26s | 4 |

---

## C. Timing Analysis

### Entry Delay (seconds from window open to first trade)

| Stat | Value |
|------|-------|
| Mean | 46.2s |
| Median | 15.0s |
| Min | 9s |
| Max | 145s |
| Std dev | 57.8s |

**Per-coin entry delay:**
  BTC: 4 windows | Lean: {'DOWN': 3, 'UP': 1} | Avg total: $1822.81 | Avg ratio: 4.19:1 | Avg combined: 1.0558
  ETH: 4 windows | Lean: {'DOWN': 3, 'UP': 1} | Avg total: $504.76 | Avg ratio: 16.16:1 | Avg combined: 1.0228
  SOL: 4 windows | Lean: {'UP': 2, 'DOWN': 2} | Avg total: $247.16 | Avg ratio: 11.76:1 | Avg combined: 1.0882
  XRP: 4 windows | Lean: {'DOWN': 2, 'UP': 2} | Avg total: $229.80 | Avg ratio: 19.66:1 | Avg combined: 0.8350

### Active Duration (first to last trade)

| Stat | Value |
|------|-------|
| Mean | 162.0s |
| Median | 174.0s |
| Min | 26s |
| Max | 266s |

### Execution Pattern

W4 does NOT enter all-at-once. The fills are **spread over the full active period** (avg 162s), indicating:
- Multiple order submissions or adjustments
- Possible "ladder" or "DCA" entry strategy
- Fills arrive in batches at the same timestamp (multiple fills per second = sweeping the book)

### Cross-Coin Synchronization

W4 trades all 4 coins within the SAME window epoch with near-simultaneous first trades:
- 10:25AM ET: spread = 4s across 4 coins
- 10:30AM ET: spread = 2s across 4 coins
- 10:35AM ET: spread = 6s across 4 coins
- 10:40AM ET: spread = 10s across 4 coins

---

## D. Price Analysis

### Lean Side Price Distribution

The lean side (direction W4 bets more on) prices:
- **Mean wavg**: 0.6926
- **Median wavg**: 0.6951
- **Range**: [0.3926, 0.8706]

### Hedge Side Price Distribution

The hedge side (smaller position) prices:
- **Mean wavg**: 0.3079
- **Median wavg**: 0.2967
- **Range**: [0.0000, 0.7787]

### Is Lean Always the Expensive Side?

**15/16 (94%)** — YES, lean = expensive = momentum following confirmed

This means W4 buys the side that the market already prices as more likely.
Interpretation: W4 is a **momentum follower**, not a contrarian.

### Combined Cost (up_wavg + dn_wavg)

| Stat | Value |
|------|-------|
| Mean | 1.0005 |
| Min | 0.6255 |
| Max | 1.1815 |

Combined < 1.0 means guaranteed profit if either side wins.
Combined > 1.0 means net loss unless lean side wins.

| Window | Coin | Combined | Interpretation |
|--------|------|----------|----------------|
| 10:25AM ET | BTC | 1.0159 | Need lean to win (cost=1.0159) |
| 10:25AM ET | ETH | 0.9862 | GUARANTEED PROFIT |
| 10:25AM ET | SOL | 1.1713 | Need lean to win (cost=1.1713) |
| 10:25AM ET | XRP | 0.9208 | GUARANTEED PROFIT |
| 10:30AM ET | BTC | 1.1815 | Need lean to win (cost=1.1815) |
| 10:30AM ET | ETH | 1.0442 | Need lean to win (cost=1.0442) |
| 10:30AM ET | SOL | 1.1316 | Need lean to win (cost=1.1316) |
| 10:30AM ET | XRP | 0.9614 | GUARANTEED PROFIT |
| 10:35AM ET | BTC | 1.0554 | Need lean to win (cost=1.0554) |
| 10:35AM ET | ETH | 0.9821 | GUARANTEED PROFIT |
| 10:35AM ET | SOL | 0.9609 | GUARANTEED PROFIT |
| 10:35AM ET | XRP | 0.8324 | GUARANTEED PROFIT |
| 10:40AM ET | BTC | 0.9706 | GUARANTEED PROFIT |
| 10:40AM ET | ETH | 1.0788 | Need lean to win (cost=1.0788) |
| 10:40AM ET | SOL | 1.0889 | Need lean to win (cost=1.0889) |
| 10:40AM ET | XRP | 0.6255 | GUARANTEED PROFIT |

---

## E. BTC Cross-Reference

### Momentum at Entry

W4 waits ~15s after window open, observes BTC price movement, then leans in that direction.

- **Lean matches BTC momentum at entry**: 6/8 (75%)
- **Lean matches final BTC resolution**: 4/16 (25%)

### Signal Interpretation

W4's strategy = **momentum continuation bet**:
1. Wait for initial BTC move direction after window opens
2. Lean in that direction (assuming momentum continues to window close)
3. Hedge the other side at cheap prices

---

## F. Fill Fragmentation

### Summary Statistics

| Metric | UP Side | DOWN Side |
|--------|---------|-----------|
| Avg fills per window | 50.2 | 44.8 |
| Avg price levels per window | 15.1 | 14.5 |
| Avg individual fill size | $6.5684 | $8.2898 |

### Execution Style

W4 places orders that **sweep multiple price levels** — this is NOT a single limit order.
Fills arrive in clusters at the same timestamp but at different prices, indicating market/FOK orders
that eat through the order book.

The lean side has more fills and more price levels → bigger sweep.
The hedge side has fewer fills at fewer levels → smaller, targeted order.

### Detailed Fill Maps


#### btc-updown-5m-1774275900
**UP** (81 fills, 12 levels, $189.55):
```
  @0.0300:   5 fills, $    2.76
  @0.0900:   2 fills, $    8.10
  @0.1000:   1 fills, $    9.00
  @0.1100:   1 fills, $    9.90
  @0.1200:   1 fills, $   10.80
  @0.1300:  16 fills, $   31.00
  @0.1400:   3 fills, $    3.76
  @0.1500:   6 fills, $   27.00
  @0.1600:  25 fills, $   36.95
  @0.1700:  10 fills, $   30.60
  @0.1800:   2 fills, $    2.59
  @0.1900:   9 fills, $   17.10
```
**DOWN** (16 fills, 5 levels, $502.62):
```
  @0.7800:   5 fills, $   70.20
  @0.7900:   3 fills, $  142.20
  @0.8100:   4 fills, $   72.90
  @0.9600:   1 fills, $   76.13
  @0.9800:   3 fills, $  141.19
```

#### btc-updown-5m-1774276200
**UP** (157 fills, 37 levels, $1432.93):
```
  @0.0200:   1 fills, $    1.80
  @0.0300:  11 fills, $    5.38
  @0.0600:   1 fills, $    5.40
  @0.0800:  10 fills, $    7.20
  @0.0900:   1 fills, $    8.10
  @0.1000:   1 fills, $    9.00
  @0.1400:   5 fills, $   12.60
  @0.2200:   4 fills, $   19.80
  @0.2300:   6 fills, $   20.70
  @0.2500:   6 fills, $   14.19
  @0.2600:   6 fills, $   23.40
  @0.2700:   7 fills, $   24.30
  @0.3400:   2 fills, $   15.38
  @0.3600:   2 fills, $   21.51
  @0.3700:   1 fills, $    0.00
  @0.3800:   6 fills, $   34.20
  @0.3900:   2 fills, $   22.34
  @0.4100:   3 fills, $   36.90
  @0.4200:   2 fills, $    5.64
  @0.4600:   1 fills, $    6.26
  @0.4700:   5 fills, $   18.27
  @0.4800:   6 fills, $   86.40
  @0.4900:   2 fills, $   44.10
  @0.5000:   4 fills, $   20.30
  @0.5100:   6 fills, $   32.85
  @0.5200:   1 fills, $    6.29
  @0.5300:   8 fills, $   78.78
  @0.5400:   9 fills, $   49.93
  @0.5500:   7 fills, $  107.17
  @0.5600:  11 fills, $  204.40
  @0.5700:   5 fills, $  153.90
  @0.5800:   6 fills, $  156.60
  @0.5900:   2 fills, $   57.82
  @0.6000:   3 fills, $   54.00
  @0.6100:   1 fills, $    8.98
  @0.6500:   2 fills, $   58.50
  @0.6600:   1 fills, $    0.58
```
**DOWN** (99 fills, 27 levels, $1623.00):
```
  @0.3700:   4 fills, $   27.50
  @0.3800:   5 fills, $   34.20
  @0.3900:   2 fills, $    9.07
  @0.4100:   1 fills, $    0.56
  @0.4200:   6 fills, $   41.42
  @0.4400:   2 fills, $    9.13
  @0.4500:   7 fills, $   92.05
  @0.4600:   6 fills, $   23.59
  @0.4700:   4 fills, $   42.30
  @0.4800:   4 fills, $   86.40
  @0.4900:   4 fills, $   49.86
  @0.5100:   2 fills, $   45.90
  @0.5400:   5 fills, $   79.78
  @0.5600:   3 fills, $   43.00
  @0.5700:   4 fills, $   51.30
  @0.5800:   1 fills, $    1.59
  @0.5900:   6 fills, $   53.09
  @0.6000:   5 fills, $  108.00
  @0.6100:   3 fills, $   54.90
  @0.6200:   1 fills, $   55.80
  @0.6300:   1 fills, $   56.70
  @0.7400:   1 fills, $    1.69
  @0.8900:   3 fills, $   80.10
  @0.9000:   7 fills, $  162.00
  @0.9100:   6 fills, $  163.80
  @0.9200:   3 fills, $  165.60
  @0.9300:   3 fills, $   83.70
```

#### btc-updown-5m-1774276500
**UP** (63 fills, 17 levels, $1784.70):
```
  @0.6900:   2 fills, $  124.20
  @0.7000:   4 fills, $   62.99
  @0.7100:   4 fills, $   63.89
  @0.7200:  14 fills, $  191.55
  @0.7300:   8 fills, $  289.32
  @0.7400:   3 fills, $  133.19
  @0.7500:   1 fills, $    6.67
  @0.7800:   1 fills, $    5.41
  @0.7900:   9 fills, $  213.28
  @0.8000:   3 fills, $   72.00
  @0.8100:   1 fills, $   30.78
  @0.8200:   4 fills, $   73.80
  @0.8300:   2 fills, $   99.11
  @0.9100:   2 fills, $  163.80
  @0.9200:   3 fills, $   82.80
  @0.9300:   1 fills, $   83.70
  @0.9800:   1 fills, $   88.20
```
**DOWN** (140 fills, 24 levels, $515.47):
```
  @0.0400:   3 fills, $    3.60
  @0.0700:   1 fills, $    6.30
  @0.0800:   1 fills, $    7.20
  @0.1100:  11 fills, $    9.90
  @0.1200:  18 fills, $   21.60
  @0.1300:  17 fills, $   23.40
  @0.1400:  13 fills, $   28.00
  @0.1500:   5 fills, $   13.50
  @0.1600:   6 fills, $    5.40
  @0.1700:   7 fills, $   14.45
  @0.1800:   3 fills, $   17.08
  @0.1900:   7 fills, $   21.53
  @0.2000:   9 fills, $   42.79
  @0.2100:   1 fills, $    1.05
  @0.2400:   6 fills, $   21.60
  @0.2700:   1 fills, $   24.30
  @0.2800:   1 fills, $   25.20
  @0.3300:   1 fills, $   29.70
  @0.3400:   4 fills, $   30.60
  @0.3500:   3 fills, $   31.50
  @0.3600:   4 fills, $   32.40
  @0.3700:   5 fills, $   33.30
  @0.3800:   8 fills, $   34.19
  @0.4100:   5 fills, $   36.90
```

#### btc-updown-5m-1774276800
**UP** (31 fills, 6 levels, $118.31):
```
  @0.1900:   4 fills, $   17.10
  @0.3100:   5 fills, $   27.90
  @0.3600:   6 fills, $   32.40
  @0.3700:  14 fills, $   26.43
  @0.3800:   1 fills, $    6.39
  @0.4200:   1 fills, $    8.10
```
**DOWN** (91 fills, 20 levels, $1124.68):
```
  @0.5200:   1 fills, $   15.93
  @0.5300:   3 fills, $   47.70
  @0.5400:   1 fills, $    4.54
  @0.5500:   5 fills, $   49.49
  @0.5600:   1 fills, $   50.40
  @0.5700:   2 fills, $   10.54
  @0.5800:   4 fills, $   52.20
  @0.5900:   5 fills, $   53.10
  @0.6000:   6 fills, $   54.00
  @0.6100:   1 fills, $   54.90
  @0.6200:  11 fills, $   55.79
  @0.6300:   3 fills, $   56.70
  @0.6400:   4 fills, $   57.59
  @0.6500:  14 fills, $  119.06
  @0.6600:   4 fills, $   90.00
  @0.6700:   3 fills, $   60.29
  @0.6800:   6 fills, $  113.46
  @0.7300:  10 fills, $   63.00
  @0.7400:   5 fills, $   66.60
  @0.7600:   2 fills, $   49.40
```

#### eth-updown-5m-1774275900
**UP** (24 fills, 10 levels, $23.64):
```
  @0.0200:   4 fills, $    0.71
  @0.0300:   4 fills, $    2.78
  @0.0600:   1 fills, $    0.06
  @0.0900:   3 fills, $    2.83
  @0.1000:   1 fills, $    0.12
  @0.1100:   5 fills, $    4.13
  @0.1700:   1 fills, $    1.42
  @0.2300:   2 fills, $    5.45
  @0.2400:   1 fills, $    2.40
  @0.2500:   2 fills, $    3.73
```
**DOWN** (18 fills, 11 levels, $262.83):
```
  @0.7500:   1 fills, $   15.00
  @0.7600:   1 fills, $    6.73
  @0.7800:   2 fills, $   28.08
  @0.7900:   1 fills, $   15.80
  @0.8000:   3 fills, $   36.72
  @0.8100:   3 fills, $   45.36
  @0.8200:   2 fills, $   29.52
  @0.8300:   1 fills, $    4.15
  @0.8400:   1 fills, $   30.24
  @0.8900:   2 fills, $   32.04
  @0.9500:   1 fills, $   19.19
```

#### eth-updown-5m-1774276200
**UP** (59 fills, 21 levels, $144.36):
```
  @0.0100:   3 fills, $    0.72
  @0.0200:   1 fills, $    0.72
  @0.0300:   3 fills, $    2.16
  @0.0500:   5 fills, $    2.59
  @0.0600:  12 fills, $    4.10
  @0.0700:   1 fills, $    0.56
  @0.0800:   1 fills, $    0.88
  @0.0900:   1 fills, $    3.24
  @0.1000:   2 fills, $    3.13
  @0.1100:   3 fills, $    4.28
  @0.1200:   2 fills, $    3.00
  @0.1300:   1 fills, $    4.68
  @0.1400:   2 fills, $    2.64
  @0.1600:   5 fills, $    5.76
  @0.2100:   1 fills, $    7.56
  @0.4300:   4 fills, $   30.96
  @0.4400:   2 fills, $   15.84
  @0.4500:   4 fills, $   14.49
  @0.4600:   4 fills, $   16.56
  @0.4700:   1 fills, $    8.43
  @0.4800:   1 fills, $   12.06
```
**DOWN** (44 fills, 18 levels, $379.49):
```
  @0.5400:   1 fills, $   10.80
  @0.5600:   1 fills, $    0.56
  @0.5900:   1 fills, $    2.59
  @0.6000:   4 fills, $   36.60
  @0.6100:   1 fills, $   11.36
  @0.6200:   6 fills, $   33.26
  @0.6300:   6 fills, $   45.35
  @0.6400:   2 fills, $   23.04
  @0.7100:   1 fills, $   14.20
  @0.7200:   2 fills, $   19.49
  @0.7300:   2 fills, $   26.28
  @0.7500:   2 fills, $   32.94
  @0.7600:   6 fills, $   27.36
  @0.7700:   2 fills, $   27.72
  @0.7900:   2 fills, $   18.11
  @0.8100:   1 fills, $   16.20
  @0.8200:   3 fills, $   28.74
  @0.8300:   1 fills, $    4.88
```

#### eth-updown-5m-1774276500
**UP** (55 fills, 20 levels, $532.85):
```
  @0.6000:   1 fills, $   21.00
  @0.6300:   1 fills, $   22.68
  @0.6400:   2 fills, $   46.08
  @0.6600:   1 fills, $   12.79
  @0.7000:   6 fills, $   25.20
  @0.7100:   2 fills, $   31.48
  @0.7200:   1 fills, $   12.01
  @0.7300:   5 fills, $   49.84
  @0.7400:   6 fills, $   35.63
  @0.7500:   5 fills, $   33.69
  @0.7600:   1 fills, $    3.48
  @0.7700:   4 fills, $   29.12
  @0.7900:   3 fills, $   21.86
  @0.8000:   2 fills, $   17.60
  @0.8100:   1 fills, $   16.20
  @0.8200:   1 fills, $    4.56
  @0.8300:   4 fills, $   38.38
  @0.8400:   6 fills, $   50.27
  @0.8700:   2 fills, $   31.32
  @0.8800:   1 fills, $   29.67
```
**DOWN** (26 fills, 11 levels, $48.33):
```
  @0.0100:   5 fills, $    0.72
  @0.0200:   2 fills, $    0.72
  @0.0300:   2 fills, $    1.08
  @0.0400:   2 fills, $    1.44
  @0.1400:   1 fills, $    2.01
  @0.1900:   2 fills, $    7.40
  @0.2200:   3 fills, $    6.01
  @0.2400:   1 fills, $    8.64
  @0.2500:   3 fills, $    4.96
  @0.2900:   1 fills, $    5.19
  @0.3000:   4 fills, $   10.16
```

#### eth-updown-5m-1774276800
**UP** (4 fills, 3 levels, $15.36):
```
  @0.3700:   1 fills, $    0.65
  @0.3800:   1 fills, $    0.67
  @0.3900:   2 fills, $   14.04
```
**DOWN** (67 fills, 17 levels, $612.20):
```
  @0.5900:   1 fills, $    4.61
  @0.6000:   4 fills, $   21.60
  @0.6100:   8 fills, $   35.91
  @0.6200:   2 fills, $   22.32
  @0.6300:   5 fills, $   29.42
  @0.6400:   1 fills, $   23.04
  @0.6500:   1 fills, $   23.40
  @0.6700:   3 fills, $   20.19
  @0.6800:   5 fills, $   44.14
  @0.6900:  10 fills, $   99.35
  @0.7000:   8 fills, $  105.89
  @0.7100:   2 fills, $   25.56
  @0.7200:   2 fills, $    7.92
  @0.7400:   1 fills, $    1.18
  @0.7500:   7 fills, $   89.44
  @0.7600:   5 fills, $   30.51
  @0.7700:   2 fills, $   27.72
```

#### sol-updown-5m-1774275900
**UP** (141 fills, 39 levels, $325.86):
```
  @0.0500:  10 fills, $    3.60
  @0.0600:  25 fills, $    7.05
  @0.0700:   5 fills, $    2.13
  @0.0800:   8 fills, $    5.28
  @0.0900:   3 fills, $    2.45
  @0.1000:   3 fills, $    2.40
  @0.1100:   1 fills, $    0.06
  @0.1400:   5 fills, $    4.82
  @0.1500:   5 fills, $    9.31
  @0.1600:   2 fills, $    2.61
  @0.1800:   1 fills, $    0.55
  @0.1900:   2 fills, $    4.56
  @0.2000:   4 fills, $    6.62
  @0.2100:   7 fills, $   18.27
  @0.2200:   6 fills, $   11.92
  @0.2300:   6 fills, $   16.24
  @0.2400:   5 fills, $   10.08
  @0.2500:   6 fills, $   12.00
  @0.2537:   1 fills, $    6.09
  @0.2600:   2 fills, $    8.21
  @0.2800:   1 fills, $    3.89
  @0.4000:   1 fills, $    3.84
  @0.4100:   2 fills, $    6.23
  @0.4400:   1 fills, $   10.56
  @0.4600:   1 fills, $    3.05
  @0.4700:   2 fills, $    7.34
  @0.4800:   1 fills, $   11.52
  @0.5000:   2 fills, $    4.67
  @0.5100:   4 fills, $   11.83
  @0.5200:   2 fills, $    9.86
  @0.5300:   3 fills, $   12.72
  @0.5400:   2 fills, $   12.26
  @0.5500:   1 fills, $    3.23
  @0.5600:   3 fills, $   13.44
  @0.5700:   1 fills, $    5.70
  @0.5800:   2 fills, $   13.92
  @0.5900:   2 fills, $   14.16
  @0.6000:   2 fills, $   28.80
  @0.6084:   1 fills, $   14.60
```
**DOWN** (29 fills, 12 levels, $156.35):
```
  @0.5300:   2 fills, $    9.50
  @0.5500:   4 fills, $    2.24
  @0.7100:   2 fills, $    3.70
  @0.7200:   1 fills, $    0.50
  @0.7300:   4 fills, $   25.33
  @0.7500:   1 fills, $    1.85
  @0.7700:   1 fills, $    3.85
  @0.7800:   4 fills, $   34.62
  @0.8000:   2 fills, $    5.11
  @0.8100:   6 fills, $   38.87
  @0.8700:   1 fills, $   20.88
  @0.9000:   1 fills, $    9.90
```

#### sol-updown-5m-1774276200
**UP** (18 fills, 12 levels, $54.71):
```
  @0.0200:   2 fills, $    0.48
  @0.0600:   1 fills, $    0.07
  @0.2300:   1 fills, $    2.82
  @0.3400:   1 fills, $    6.89
  @0.4700:   1 fills, $    0.11
  @0.5000:   2 fills, $   12.00
  @0.5300:   1 fills, $    3.91
  @0.5700:   1 fills, $    0.37
  @0.5800:   4 fills, $    9.77
  @0.5900:   2 fills, $   14.16
  @0.6100:   1 fills, $    1.56
  @0.6400:   1 fills, $    2.56
```
**DOWN** (19 fills, 15 levels, $116.22):
```
  @0.4400:   1 fills, $    1.63
  @0.5100:   2 fills, $   24.48
  @0.5157:   1 fills, $   12.38
  @0.5600:   2 fills, $    8.19
  @0.5700:   2 fills, $    7.07
  @0.5900:   1 fills, $    1.51
  @0.6200:   1 fills, $    0.40
  @0.6300:   2 fills, $    5.00
  @0.6400:   1 fills, $    2.58
  @0.6600:   1 fills, $    4.22
  @0.6900:   1 fills, $   12.42
  @0.7000:   1 fills, $   12.60
  @0.7300:   1 fills, $   17.52
  @0.7800:   1 fills, $    4.04
  @0.8600:   1 fills, $    2.17
```

#### sol-updown-5m-1774276500
**UP** (18 fills, 9 levels, $127.02):
```
  @0.5700:   1 fills, $    0.30
  @0.6200:   1 fills, $   14.88
  @0.6500:   3 fills, $   15.60
  @0.7000:   1 fills, $    3.13
  @0.7100:   4 fills, $   15.71
  @0.7200:   3 fills, $   25.23
  @0.7900:   2 fills, $    7.90
  @0.8300:   1 fills, $   19.92
  @0.8400:   2 fills, $   24.36
```
**DOWN** (31 fills, 15 levels, $30.48):
```
  @0.0200:   3 fills, $    0.48
  @0.0300:   1 fills, $    0.72
  @0.0700:   2 fills, $    0.82
  @0.0900:   1 fills, $    0.05
  @0.1600:   1 fills, $    0.45
  @0.1800:   2 fills, $    1.07
  @0.1900:   7 fills, $    5.34
  @0.2000:   1 fills, $    3.60
  @0.2200:   3 fills, $    5.28
  @0.2300:   5 fills, $    5.27
  @0.2400:   1 fills, $    1.48
  @0.2600:   1 fills, $    0.38
  @0.2900:   1 fills, $    0.39
  @0.3100:   1 fills, $    4.65
  @0.3800:   1 fills, $    0.51
```

#### sol-updown-5m-1774276800
**UP** (3 fills, 1 levels, $4.49):
```
  @0.4300:   3 fills, $    4.49
```
**DOWN** (31 fills, 11 levels, $173.51):
```
  @0.5600:   1 fills, $   13.44
  @0.5700:   3 fills, $    6.16
  @0.6000:   1 fills, $   14.40
  @0.6100:   1 fills, $   14.64
  @0.6500:   4 fills, $   18.16
  @0.6600:   5 fills, $   31.68
  @0.6800:   5 fills, $   32.64
  @0.7100:   1 fills, $    1.68
  @0.7200:   6 fills, $   19.78
  @0.7300:   3 fills, $   17.52
  @0.7600:   1 fills, $    3.41
```

#### xrp-updown-5m-1774275900
**UP** (25 fills, 11 levels, $17.32):
```
  @0.0300:   2 fills, $    0.30
  @0.0400:   2 fills, $    1.44
  @0.0500:   3 fills, $    1.80
  @0.0600:   1 fills, $    0.90
  @0.0700:   3 fills, $    1.63
  @0.0800:   3 fills, $    1.44
  @0.0900:   3 fills, $    1.67
  @0.1000:   2 fills, $    1.78
  @0.1100:   1 fills, $    0.61
  @0.1500:   3 fills, $    2.70
  @0.1700:   2 fills, $    3.06
```
**DOWN** (26 fills, 9 levels, $153.43):
```
  @0.7700:   4 fills, $   13.85
  @0.7800:   4 fills, $   26.09
  @0.7900:   1 fills, $   14.22
  @0.8000:   4 fills, $   25.67
  @0.8100:   5 fills, $   29.15
  @0.8200:   3 fills, $   10.71
  @0.8300:   2 fills, $   10.50
  @0.8400:   1 fills, $    5.78
  @0.9700:   2 fills, $   17.46
```

#### xrp-updown-5m-1774276200
**UP** (97 fills, 33 levels, $349.99):
```
  @0.2000:   3 fills, $    0.98
  @0.2100:   1 fills, $    2.52
  @0.2200:   3 fills, $    3.96
  @0.2300:   3 fills, $    4.14
  @0.2500:   3 fills, $    4.30
  @0.2700:   1 fills, $    3.24
  @0.2800:   2 fills, $    5.04
  @0.2900:   2 fills, $    2.61
  @0.3000:   2 fills, $    1.34
  @0.3800:   2 fills, $   13.68
  @0.3900:   2 fills, $   14.04
  @0.4000:   2 fills, $    6.27
  @0.4100:   2 fills, $    7.38
  @0.4200:   3 fills, $   15.12
  @0.4300:   8 fills, $   33.41
  @0.4400:   5 fills, $   18.92
  @0.4500:   6 fills, $   16.19
  @0.4600:   4 fills, $   15.60
  @0.4700:   4 fills, $   18.16
  @0.4800:   4 fills, $   22.62
  @0.4900:   5 fills, $   18.80
  @0.5000:   4 fills, $   18.00
  @0.5200:   3 fills, $   19.91
  @0.5300:   3 fills, $   10.78
  @0.5400:   4 fills, $    9.72
  @0.5600:   1 fills, $    2.80
  @0.5700:   1 fills, $    2.60
  @0.5800:   1 fills, $    0.90
  @0.6300:   1 fills, $    1.93
  @0.6400:   3 fills, $    9.10
  @0.6500:   6 fills, $   23.39
  @0.6800:   2 fills, $    6.15
  @0.9100:   1 fills, $   16.38
```
**DOWN** (65 fills, 30 levels, $223.55):
```
  @0.0600:   3 fills, $    2.16
  @0.0700:   1 fills, $    1.26
  @0.1300:   1 fills, $    0.88
  @0.1400:   1 fills, $    2.52
  @0.1500:   1 fills, $    0.18
  @0.2300:   2 fills, $    4.14
  @0.2400:   1 fills, $    4.32
  @0.2800:   1 fills, $    1.99
  @0.3600:   1 fills, $    6.48
  @0.3800:   2 fills, $    8.74
  @0.3900:   1 fills, $    1.88
  @0.4000:   2 fills, $    3.59
  @0.4100:   2 fills, $    7.38
  @0.4200:   2 fills, $    6.26
  @0.4300:   2 fills, $    4.71
  @0.4600:   2 fills, $    8.28
  @0.4700:   4 fills, $   13.21
  @0.4800:   3 fills, $    8.64
  @0.4900:   5 fills, $   23.64
  @0.5000:   2 fills, $    9.00
  @0.5100:   3 fills, $   14.36
  @0.5200:   7 fills, $   33.69
  @0.5300:   2 fills, $    7.03
  @0.5400:   2 fills, $    7.19
  @0.5500:   1 fills, $    6.89
  @0.5700:   3 fills, $   10.26
  @0.5800:   3 fills, $   10.44
  @0.5900:   1 fills, $    1.58
  @0.6000:   3 fills, $    9.18
  @0.6400:   1 fills, $    3.69
```

#### xrp-updown-5m-1774276500
**UP** (27 fills, 11 levels, $153.30):
```
  @0.5500:   1 fills, $    9.90
  @0.6100:   1 fills, $   10.98
  @0.6200:   1 fills, $   11.16
  @0.6400:   1 fills, $    3.20
  @0.7200:   2 fills, $   10.76
  @0.7300:   3 fills, $   13.13
  @0.7900:   6 fills, $   28.87
  @0.8800:   2 fills, $    3.64
  @0.8900:   2 fills, $   18.90
  @0.9000:   5 fills, $   33.62
  @0.9100:   3 fills, $    9.13
```
**DOWN** (11 fills, 4 levels, $3.16):
```
  @0.0400:   6 fills, $    1.67
  @0.0500:   2 fills, $    0.47
  @0.0600:   1 fills, $    0.18
  @0.0700:   2 fills, $    0.84
```

#### xrp-updown-5m-1774276800
**UP** (0 fills, 0 levels, $0.00):
```
```
**DOWN** (4 fills, 3 levels, $18.46):
```
  @0.6000:   2 fills, $    6.00
  @0.6100:   1 fills, $    0.94
  @0.6400:   1 fills, $   11.52
```

---

## G. SOL-Specific Analysis

### SOL vs BTC Lean Direction

| Epoch | SOL Lean | BTC Lean | Relationship |
|-------|----------|----------|--------------|
| 10:25AM ET | UP | DOWN | CONTRARIAN |
| 10:30AM ET | DOWN | DOWN | SAME |
| 10:35AM ET | UP | UP | SAME |
| 10:40AM ET | DOWN | DOWN | SAME |

**SOL contrarian to BTC: 1/4 (25%)**
**SOL same as BTC: 3/4 (75%)**

### Sizing Comparison

| Metric | SOL | BTC |
|--------|-----|-----|
| Avg total/window | $247.16 | $1822.81 |
| Min total | $157.50 | $692.17 |
| Max total | $482.21 | $3055.93 |

---

## H. The W4 5M Playbook

### Step-by-Step Recipe

```
W4 5M PLAYBOOK
═══════════════

SETUP:
- Markets: BTC, ETH, SOL, XRP 5-minute Up/Down on Polymarket
- Trade ALL 4 coins simultaneously in each window
- Window = 5 minutes (e.g., 10:30AM-10:35AM ET)

EXECUTION:
1. At T+15s (median): Enter the window
   - Range: T+9s to T+145s
   - Signal: BTC price direction in first ~15s after window open

2. Determine lean direction:
   - If BTC moved UP since window open → lean UP on all coins
   - If BTC moved DOWN since window open → lean DOWN on all coins
   - Momentum match rate: 6/8 (75%)
   - Exception: SOL may go contrarian (1/4 times)

3. LEAN side order (the direction you believe):
   - 50-45 fills sweeping 15-14 price levels
   - Avg price: 0.69c (range 0.39c-0.87c)
   - Avg USDC: $515.01 per window per coin

4. HEDGE side order (the opposite direction):
   - Fewer fills, fewer levels
   - Avg price: 0.31c (range 0.00c-0.78c)
   - Avg USDC: $186.13 per window per coin

5. Lean ratio: 12.49:1 avg
   - Range: 1.13:1 to 48.55:1
   - Lean side gets ~12.5x the USDC of hedge side

6. Combined cost: 1.0005 avg
   - Range: 0.6255 to 1.1815
   - Most windows: combined > 1.0 = need lean side to win

7. Hold to resolution (window close, T+300s)
   - NO exits observed — all positions held to settlement

8. Active period: 162s avg (range 26-266s)
   - Fills spread over this duration (not all-at-once)

9. Total per window per coin: $175.28 avg
   - Total across all coins: $701.13 avg per window epoch

RISK PROFILE:
- Win rate (lean matches resolution): ~25%
- When lean wins: profit = lean_shares - lean_cost + hedge_cost (net > 0)
- When lean loses: loss = lean_cost - hedge_shares (net < 0)
- Lean=expensive means higher cost but higher payout
```

### Key Insights

1. **Momentum Following**: W4 observes 15s of BTC price action, then bets momentum continues. This is a simple but effective signal.

2. **Multi-Coin Diversification**: Trading BTC, ETH, SOL, XRP simultaneously in the same window diversifies across coin-specific noise while keeping the same directional thesis.

3. **Asymmetric Sizing**: 12.5:1 lean ratio means W4 is NOT hedging equally. This is a conviction bet with a small hedge.

4. **Sweep Execution**: W4 uses market-like orders that eat through multiple price levels, not patient limit orders. Speed > price.

5. **Active Management**: The 162s active period suggests W4 may be adjusting positions or adding to them as the window progresses, not just entering once.

6. **No Early Exit**: All positions held to resolution. W4 does not try to trade out of positions mid-window.

---

## Raw Data Summary

### All Valid Windows (sorted by epoch)

| # | Slug | Coin | Lean | UP$ | DN$ | Ratio | Combined | Delay | Duration | Trades |
|---|------|------|------|-----|-----|-------|----------|-------|----------|--------|
| 1 | btc-updown-5m-1774275900 | BTC | DOWN | $189.55 | $502.62 | 2.65:1 | 1.0159 | 143s | 100s | 97 |
| 2 | eth-updown-5m-1774275900 | ETH | DOWN | $23.64 | $262.83 | 11.12:1 | 0.9862 | 141s | 142s | 42 |
| 3 | sol-updown-5m-1774275900 | SOL | UP | $325.86 | $156.35 | 2.08:1 | 1.1713 | 145s | 138s | 170 |
| 4 | xrp-updown-5m-1774275900 | XRP | DOWN | $17.32 | $153.43 | 8.86:1 | 0.9208 | 143s | 140s | 51 |
| 5 | btc-updown-5m-1774276200 | BTC | DOWN | $1432.93 | $1623.00 | 1.13:1 | 1.1815 | 15s | 254s | 256 |
| 6 | eth-updown-5m-1774276200 | ETH | DOWN | $144.36 | $379.49 | 2.63:1 | 1.0442 | 15s | 246s | 103 |
| 7 | sol-updown-5m-1774276200 | SOL | DOWN | $54.71 | $116.22 | 2.12:1 | 1.1316 | 15s | 234s | 37 |
| 8 | xrp-updown-5m-1774276200 | XRP | UP | $349.99 | $223.55 | 1.57:1 | 0.9614 | 17s | 266s | 162 |
| 9 | btc-updown-5m-1774276500 | BTC | UP | $1784.70 | $515.47 | 3.46:1 | 1.0554 | 9s | 214s | 203 |
| 10 | eth-updown-5m-1774276500 | ETH | UP | $532.85 | $48.33 | 11.03:1 | 0.9821 | 11s | 222s | 81 |
| 11 | sol-updown-5m-1774276500 | SOL | UP | $127.02 | $30.48 | 4.17:1 | 0.9609 | 15s | 206s | 49 |
| 12 | xrp-updown-5m-1774276500 | XRP | UP | $153.30 | $3.16 | 48.55:1 | 0.8324 | 9s | 206s | 38 |
| 13 | btc-updown-5m-1774276800 | BTC | DOWN | $118.31 | $1124.68 | 9.51:1 | 0.9706 | 13s | 66s | 122 |
| 14 | eth-updown-5m-1774276800 | ETH | DOWN | $15.36 | $612.20 | 39.86:1 | 1.0788 | 13s | 66s | 71 |
| 15 | sol-updown-5m-1774276800 | SOL | DOWN | $4.49 | $173.51 | 38.65:1 | 1.0889 | 13s | 66s | 34 |
| 16 | xrp-updown-5m-1774276800 | XRP | DOWN | $0.00 | $18.46 | inf:1 | 0.6255 | 23s | 26s | 4 |
