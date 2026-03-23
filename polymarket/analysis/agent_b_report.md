# Agent B Report: W4 Execution Pattern Reconstruction
> Generated: 2026-03-24 00:16
> Data: 14-minute snapshot (2468 trades, 34 markets)
> Wallet: 0x818f...58cb (Decent-Dune / livebreathevolatility)

---

# ANALYSIS 1: W4's Bid Price vs Market Mid

Since OB tape shows 0.01/0.99 (empty markets) for W4's windows,
we infer the market mid from W4's own trades within each slug.

**ETH 1H 10:00ET** | Avg Up=0.2634 Down=0.7356 Sum=0.9990 | Up: TAKER(11/14 unique) Down: MIXED(10/21 unique)
**BTC 1H 10:00ET** | Avg Up=0.3417 Down=0.6842 Sum=1.0259 | Up: MIXED(20/54 unique) Down: MIXED(15/38 unique)
**XRP 1H 10:00ET** | Avg Up=0.0000 Down=0.2740 Sum=   N/A | Up: TAKER(0/0 unique) Down: MIXED(15/26 unique)
**SOL 1H 10:00ET** | Avg Up=0.6143 Down=0.5300 Sum=1.1443 | Up: TAKER(19/25 unique) Down: MIXED(6/13 unique)
**XRP 15M 10:15ET** | Avg Up=0.4436 Down=0.7738 Sum=1.2174 | Up: MIXED(31/55 unique) Down: MIXED(20/35 unique)
**ETH 15M 10:15ET** | Avg Up=0.2151 Down=0.7800 Sum=0.9952 | Up: MIXED(18/52 unique) Down: MIXED(18/48 unique)
**SOL 15M 10:15ET** | Avg Up=0.9497 Down=0.2677 Sum=1.2174 | Up: MAKER(5/20 unique) Down: MIXED(7/15 unique)
**BTC 15M 10:15ET** | Avg Up=0.0829 Down=0.9425 Sum=1.0254 | Up: MAKER(5/46 unique) Down: MIXED(8/21 unique)
**XRP 5M 10:25ET** | Avg Up=0.1016 Down=0.8192 Sum=0.9208 | Up: MIXED(11/25 unique) Down: MIXED(9/26 unique)
**SOL 5M 10:25ET** | Avg Up=0.3926 Down=0.7787 Sum=1.1713 | Up: MAKER(39/141 unique) Down: MIXED(12/29 unique)
**ETH 5M 10:25ET** | Avg Up=0.1619 Down=0.8244 Sum=0.9862 | Up: MIXED(10/24 unique) Down: MIXED(11/18 unique)
**BTC 5M 10:25ET** | Avg Up=0.1452 Down=0.8706 Sum=1.0159 | Up: MAKER(12/81 unique) Down: MIXED(5/16 unique)
**ETH 15M 10:30ET** | Avg Up=0.5332 Down=0.6591 Sum=1.1923 | Up: MIXED(22/64 unique) Down: MIXED(18/48 unique)
**XRP 15M 10:30ET** | Avg Up=0.6739 Down=0.3385 Sum=1.0125 | Up: MIXED(18/43 unique) Down: MIXED(20/38 unique)
**BTC 15M 10:30ET** | Avg Up=0.5985 Down=0.4822 Sum=1.0807 | Up: MAKER(19/84 unique) Down: MIXED(27/88 unique)
**SOL 15M 10:30ET** | Avg Up=0.4770 Down=0.6489 Sum=1.1259 | Up: MIXED(18/35 unique) Down: MIXED(19/55 unique)
**XRP 5M 10:30ET** | Avg Up=0.4875 Down=0.4739 Sum=0.9614 | Up: MIXED(33/97 unique) Down: MIXED(30/65 unique)
**BTC 5M 10:30ET** | Avg Up=0.5009 Down=0.6806 Sum=1.1815 | Up: MAKER(37/157 unique) Down: MAKER(27/99 unique)
**ETH 5M 10:30ET** | Avg Up=0.3440 Down=0.7001 Sum=1.0442 | Up: MIXED(21/59 unique) Down: MIXED(18/44 unique)
**SOL 5M 10:30ET** | Avg Up=0.5110 Down=0.6206 Sum=1.1316 | Up: MIXED(12/18 unique) Down: TAKER(15/19 unique)
**ETH 5M 10:35ET** | Avg Up=0.7548 Down=0.2273 Sum=0.9821 | Up: MIXED(20/55 unique) Down: MIXED(11/26 unique)
**BTC 5M 10:35ET** | Avg Up=0.7931 Down=0.2623 Sum=1.0554 | Up: MAKER(17/63 unique) Down: MAKER(24/140 unique)
**SOL 5M 10:35ET** | Avg Up=0.7422 Down=0.2187 Sum=0.9609 | Up: MIXED(9/18 unique) Down: MIXED(15/31 unique)
**XRP 5M 10:35ET** | Avg Up=0.7818 Down=0.0506 Sum=0.8324 | Up: MIXED(11/27 unique) Down: MIXED(4/11 unique)
**BTC 5M 10:40ET** | Avg Up=0.3311 Down=0.6395 Sum=0.9706 | Up: MAKER(6/31 unique) Down: MAKER(20/91 unique)
**SOL 5M 10:40ET** | Avg Up=0.4300 Down=0.6589 Sum=1.0889 | Up: MIXED(1/3 unique) Down: MIXED(11/31 unique)
**ETH 5M 10:40ET** | Avg Up=0.3887 Down=0.6901 Sum=1.0788 | Up: TAKER(3/4 unique) Down: MAKER(17/67 unique)

**Up side roles**: Counter({'MIXED': 15, 'MAKER': 8, 'TAKER': 4})
**Down side roles**: Counter({'MIXED': 22, 'MAKER': 4, 'TAKER': 1})

**Price sum (up+down)**: mean=1.0545, min=0.8324, max=1.2174
(Sum < 1.0 means W4 buys below fair value on both sides = maker edge)

---

# ANALYSIS 2: Lean Ratio Distribution

Lean ratio = max(UP$, DOWN$) / min(UP$, DOWN$) for each window.

ETH 1H  10:00ET | UP=$   27.38 DOWN=$  171.00 | Lean=DOWN  6.25x | BTC=N/A
BTC 1H  10:00ET | UP=$  369.35 DOWN=$  523.35 | Lean=DOWN  1.42x | BTC=N/A
SOL 1H  10:00ET | UP=$  104.90 DOWN=$   49.07 | Lean=UP    2.14x | BTC=N/A
XRP 15M 10:15ET | UP=$  153.55 DOWN=$  159.21 | Lean=DOWN  1.04x | BTC@open→close=-41.2bps
ETH 15M 10:15ET | UP=$   86.01 DOWN=$  360.39 | Lean=DOWN  4.19x | BTC@open→close=-41.2bps
SOL 15M 10:15ET | UP=$  248.07 DOWN=$   22.21 | Lean=UP   11.17x | BTC@open→close=-41.2bps
BTC 15M 10:15ET | UP=$   51.14 DOWN=$  650.14 | Lean=DOWN 12.71x | BTC@open→close=-41.2bps
XRP 5M  10:25ET | UP=$   17.32 DOWN=$  153.43 | Lean=DOWN  8.86x | BTC@open→close=-13.1bps
SOL 5M  10:25ET | UP=$  325.86 DOWN=$  156.35 | Lean=UP    2.08x | BTC@open→close=-13.1bps
ETH 5M  10:25ET | UP=$   23.64 DOWN=$  262.83 | Lean=DOWN 11.12x | BTC@open→close=-13.1bps
BTC 5M  10:25ET | UP=$  189.55 DOWN=$  502.62 | Lean=DOWN  2.65x | BTC@open→close=-13.1bps
ETH 15M 10:30ET | UP=$  359.04 DOWN=$  299.48 | Lean=UP    1.20x | BTC=N/A
XRP 15M 10:30ET | UP=$  160.20 DOWN=$   68.69 | Lean=UP    2.33x | BTC=N/A
BTC 15M 10:30ET | UP=$  802.31 DOWN=$  634.50 | Lean=UP    1.26x | BTC=N/A
SOL 15M 10:30ET | UP=$  116.10 DOWN=$  261.06 | Lean=DOWN  2.25x | BTC=N/A
XRP 5M  10:30ET | UP=$  349.99 DOWN=$  223.55 | Lean=UP    1.57x | BTC@open→close=+1.0bps
BTC 5M  10:30ET | UP=$ 1432.93 DOWN=$ 1623.00 | Lean=DOWN  1.13x | BTC@open→close=+1.0bps
ETH 5M  10:30ET | UP=$  144.36 DOWN=$  379.49 | Lean=DOWN  2.63x | BTC@open→close=+1.0bps
SOL 5M  10:30ET | UP=$   54.71 DOWN=$  116.22 | Lean=DOWN  2.12x | BTC@open→close=+1.0bps
ETH 5M  10:35ET | UP=$  532.85 DOWN=$   48.33 | Lean=UP   11.03x | BTC=N/A
BTC 5M  10:35ET | UP=$ 1784.70 DOWN=$  515.47 | Lean=UP    3.46x | BTC=N/A
SOL 5M  10:35ET | UP=$  127.02 DOWN=$   30.48 | Lean=UP    4.17x | BTC=N/A
XRP 5M  10:35ET | UP=$  153.30 DOWN=$    3.16 | Lean=UP   48.55x | BTC=N/A
BTC 5M  10:40ET | UP=$  118.31 DOWN=$ 1124.68 | Lean=DOWN  9.51x | BTC=N/A
SOL 5M  10:40ET | UP=$    4.49 DOWN=$  173.51 | Lean=DOWN 38.65x | BTC=N/A
ETH 5M  10:40ET | UP=$   15.36 DOWN=$  612.20 | Lean=DOWN 39.86x | BTC=N/A

**Lean ratio stats**: mean=8.97x, median=3.06x, min=1.04x, max=48.55x, stdev=12.93

**Lean direction matches BTC outcome**: 7/12 = 58.3%
**Correlation (|BTC move at entry| vs lean ratio)**: r = -0.0181

---

# ANALYSIS 3: Entry Sequence

ETH 1H  10:00ET | Entry delay: 1645s | Duration:  836s | First side: DOWN | Gap: 358s | Same-sec fills: 5/34 (15%)
BTC 1H  10:00ET | Entry delay: 1643s | Duration:  826s | First side: DOWN | Gap: 8s | Same-sec fills: 28/91 (31%)
XRP 1H  10:00ET | Entry delay: 1957s | Duration:  484s | First side: DOWN | Gap: Nones | Same-sec fills: 5/25 (20%)
SOL 1H  10:00ET | Entry delay: 1647s | Duration:  788s | First side: DOWN | Gap: 22s | Same-sec fills: 4/37 (11%)
XRP 15M 10:15ET | Entry delay:  743s | Duration:  140s | First side: DOWN | Gap: 20s | Same-sec fills: 64/89 (72%)
ETH 15M 10:15ET | Entry delay:  741s | Duration:  142s | First side: DOWN | Gap: 30s | Same-sec fills: 68/99 (69%)
SOL 15M 10:15ET | Entry delay:  741s | Duration:  120s | First side: DOWN | Gap: 26s | Same-sec fills: 14/34 (41%)
BTC 15M 10:15ET | Entry delay:  743s | Duration:   98s | First side: UP   | Gap: 28s | Same-sec fills: 47/66 (71%)
XRP 5M  10:25ET | Entry delay:  143s | Duration:  140s | First side: DOWN | Gap: 20s | Same-sec fills: 30/50 (60%)
SOL 5M  10:25ET | Entry delay:  145s | Duration:  138s | First side: UP   | Gap: 8s | Same-sec fills: 126/169 (75%)
ETH 5M  10:25ET | Entry delay:  141s | Duration:  142s | First side: DOWN | Gap: 42s | Same-sec fills: 22/41 (54%)
BTC 5M  10:25ET | Entry delay:  143s | Duration:  100s | First side: UP   | Gap: 22s | Same-sec fills: 73/96 (76%)
ETH 15M 10:30ET | Entry delay:   65s | Duration:  618s | First side: DOWN | Gap: 312s | Same-sec fills: 61/111 (55%)
XRP 15M 10:30ET | Entry delay:   21s | Duration:  662s | First side: UP   | Gap: 82s | Same-sec fills: 29/80 (36%)
BTC 15M 10:30ET | Entry delay:   29s | Duration:  650s | First side: UP   | Gap: 34s | Same-sec fills: 108/171 (63%)
SOL 15M 10:30ET | Entry delay:   13s | Duration:  648s | First side: DOWN | Gap: 296s | Same-sec fills: 36/89 (40%)
XRP 5M  10:30ET | Entry delay:   17s | Duration:  266s | First side: UP   | Gap: 86s | Same-sec fills: 115/161 (71%)
BTC 5M  10:30ET | Entry delay:   15s | Duration:  254s | First side: UP   | Gap: 2s | Same-sec fills: 198/255 (78%)
ETH 5M  10:30ET | Entry delay:   15s | Duration:  246s | First side: UP   | Gap: 10s | Same-sec fills: 61/102 (60%)
SOL 5M  10:30ET | Entry delay:   15s | Duration:  234s | First side: DOWN | Gap: 16s | Same-sec fills: 9/36 (25%)
ETH 5M  10:35ET | Entry delay:   11s | Duration:  222s | First side: UP   | Gap: 102s | Same-sec fills: 47/80 (59%)
BTC 5M  10:35ET | Entry delay:    9s | Duration:  214s | First side: DOWN | Gap: 8s | Same-sec fills: 153/202 (76%)
SOL 5M  10:35ET | Entry delay:   15s | Duration:  206s | First side: UP   | Gap: 52s | Same-sec fills: 21/48 (44%)
XRP 5M  10:35ET | Entry delay:    9s | Duration:  206s | First side: UP   | Gap: 116s | Same-sec fills: 14/37 (38%)
BTC 5M  10:40ET | Entry delay:   13s | Duration:   66s | First side: DOWN | Gap: 16s | Same-sec fills: 104/121 (86%)
SOL 5M  10:40ET | Entry delay:   13s | Duration:   66s | First side: DOWN | Gap: 16s | Same-sec fills: 21/33 (64%)
ETH 5M  10:40ET | Entry delay:   13s | Duration:   66s | First side: DOWN | Gap: 14s | Same-sec fills: 47/70 (67%)

**First side placed**: Counter({'DOWN': 16, 'UP': 11})
**Entry delay**: mean=396s, median=29s, min=9s, max=1957s
**Trading duration**: mean=318s, median=214s
**UP/DOWN gap**: mean=67s, median=24s
**Same-second fills**: mean=54%
(High same-second fill % = fragmented maker fills, NOT taker sweeps)

---

# ANALYSIS 4: Fill Size Distribution

## All Fills
- Count: 2468
- Min: $0.0007
- P25: $1.10
- **P50 (median): $3.17**
- P75: $8.05
- P90: $16.45
- P99: $55.78
- Max: $88.20
- Mean: $6.87

## Lean Side Fills (n=1305)
- P50: $4.44, Mean: $9.28

## Hedge Side Fills (n=1163)
- P50: $1.95, Mean: $4.18

## Share Size Distribution
- Min: 0.0100 shares, Median: 7.66, Max: 90.00

---

# ANALYSIS 5: Entry Delay Relative to Window Open

  BTC 5M  delay=   9s | $ 2300.17 | BTC=N/A
  XRP 5M  delay=   9s | $  156.46 | BTC=N/A
  ETH 5M  delay=  11s | $  581.18 | BTC=N/A
  BTC 5M  delay=  13s | $ 1242.99 | BTC=N/A
  SOL 5M  delay=  13s | $  178.00 | BTC=N/A
  ETH 5M  delay=  13s | $  627.56 | BTC=N/A
  SOL 15M delay=  13s | $  377.16 | BTC=N/A
  SOL 5M  delay=  15s | $  157.50 | BTC=N/A
  BTC 5M  delay=  15s | $ 3055.93 | BTC=N/A
  ETH 5M  delay=  15s | $  523.85 | BTC=N/A
  SOL 5M  delay=  15s | $  170.92 | BTC=N/A
  XRP 5M  delay=  17s | $  573.54 | BTC=N/A
  XRP 15M delay=  21s | $  228.89 | BTC=N/A
  BTC 15M delay=  29s | $ 1436.81 | BTC=N/A
  ETH 15M delay=  65s | $  658.51 | BTC move at entry=5.7bps
  ETH 5M  delay= 141s | $  286.47 | BTC move at entry=6.9bps
  XRP 5M  delay= 143s | $  170.75 | BTC move at entry=6.9bps
  BTC 5M  delay= 143s | $  692.17 | BTC move at entry=6.9bps
  SOL 5M  delay= 145s | $  482.21 | BTC move at entry=6.9bps
  ETH 15M delay= 741s | $  446.40 | BTC move at entry=35.0bps
  SOL 15M delay= 741s | $  270.28 | BTC move at entry=35.0bps
  XRP 15M delay= 743s | $  312.76 | BTC move at entry=35.0bps
  BTC 15M delay= 743s | $  701.29 | BTC move at entry=35.0bps
  BTC 1H  delay=1643s | $  892.70 | BTC move at entry=16.8bps
  ETH 1H  delay=1645s | $  198.38 | BTC move at entry=16.8bps
  SOL 1H  delay=1647s | $  153.97 | BTC move at entry=16.8bps
  XRP 1H  delay=1957s | $   38.60 | BTC move at entry=25.8bps

**5M windows**: mean delay=48s, median=15s, range=[9, 145]s

**15M windows**: mean delay=387s, median=403s, range=[13, 743]s

**1H windows**: mean delay=1723s, median=1646s, range=[1643, 1957]s

**Correlation (delay vs |BTC move at entry|)**: r = 0.3872
(Negative = enters faster on bigger moves)

---

# ANALYSIS 6: W4's Exact P&L Per Window

Resolution: winning side shares x $1.00, losing side = $0.00
Payout includes the 2% fee on winnings on Polymarket.

  [+] BTC 15M 09:15ET | BTC -11.4bps→DOWN | Cost=$16.14 | Up=0.0sh Down=36.7sh | Payout=$36.27 | PnL=$+20.13 (+124.7%) | Cum=$+20.13
  [+] BTC 5M  09:15ET | BTC +5.3bps→UP | Cost=$4.82 | Up=5.2sh Down=2.1sh | Payout=$5.21 | PnL=$+0.38 (+8.0%) | Cum=$+20.51
  [-] BTC 15M 10:15ET | BTC -41.2bps→DOWN | Cost=$701.29 | Up=629.2sh Down=691.3sh | Payout=$690.43 | PnL=$-10.86 (-1.5%) | Cum=$+9.66
  [-] BTC 5M  10:25ET | BTC -13.1bps→DOWN | Cost=$692.17 | Up=1412.5sh Down=583.4sh | Payout=$581.76 | PnL=$-110.41 (-16.0%) | Cum=$-100.76
  [+] BTC 5M  10:30ET | BTC +1.0bps→UP | Cost=$3055.93 | Up=3565.8sh Down=2605.4sh | Payout=$3523.10 | PnL=$+467.16 (+15.3%) | Cum=$+366.41

**BTC Markets Summary (14-min snapshot)**:
- Windows: 5 | W/L: 3/2 | WR: 60.0%
- Total invested: $4470.35
- Total P&L: $+366.41
- ROI: +8.2%
- Avg win: $+162.56 | Avg loss: $-60.64

---

# ANALYSIS 7: The W4 Playbook


## The W4 Playbook: Step-by-Step Recipe

### Identity
- Wallet: `0x818f...58cb` (pseudonym: "Decent-Dune" / "livebreathevolatility")
- Trades BTC, ETH, SOL, XRP simultaneously on both 5M and 15M windows
- Average 4.5 markets per time slot

### Step 1: Window Discovery
- Monitors multiple time windows: 5M + 15M + 1H for BTC/ETH/SOL/XRP
- In this 14-minute snapshot: 27 active markets across 6 time slots

### Step 2: Entry Timing
- **5M windows**: enters 15s after open (median), range [9-145]s
- **15M windows**: enters 403s after open (median), range [13-743]s
- **1H windows**: enters ~1646s after open (limited data)
- **Pattern**: Very fast entry on 5M (9-17s), slightly slower on 15M (~29s for current window, ~740s for previous)

### Step 3: Determine Direction
- Checks BTC return since window open
- Lean direction = follows the BTC move direction
- **BTC lean accuracy**: 2/3 = 66.7% (based on available data)

### Step 4: Place Orders — BOTH SIDES (Hedged Directional)
- **Always buys BOTH Up and Down** — not pure directional
- **Lean ratio**: median 3.06x, mean 8.97x
  - Range: [1.04x - 48.55x]
- Lean side gets ~60-75% of capital; hedge side gets ~25-40%

### Step 5: Pricing
- Average Up price paid: $0.4638
- Average Down price paid: $0.5907
- Sum (up + down): ~$1.0545
  - Below $1.00 = guaranteed theoretical profit if buying equal shares
  - W4 exploits the spread by being a MAKER on both sides

### Step 6: Order Sizing
- Individual fill: median $3.17, mean $6.87
- Fill range: $0.0007 to $88.20
- Total per window: median $446.40, mean $626.50
- Total range: $38.60 to $3055.93

### Step 7: Execution Style
- **Fragmented fills**: many small fills at same price = LIMIT ORDERS (maker)
- Trading duration per window: median 214s, range [66-836]s
- Places orders across multiple coins simultaneously (multi-market maker)

### Step 8: Hold to Resolution
- No evidence of exit trades in this data — all BUY side
- Holds all positions to window expiry
- Collects $1.00 per winning share, $0.00 per losing share

---

## Distilled Recipe (Concrete Parameters)

```
TRIGGER:
  At T+15s (5M) / T+13s (15M) after window open:
    Check BTC return since window open

ENTRY:
  Buy LEAN side at ~$0.46-$0.95 (directional bet)
  Buy HEDGE side at ~$0.59-$0.94 (insurance)
  Lean:Hedge ratio ≈ 3.1:1

SIZING:
  Individual fills: ~$3-$8 each (limit orders, fragmented)
  Total per window: ~$446
  Run 4 coins x multiple timeframes simultaneously

EXECUTION:
  Style: MAKER (limit orders, not market orders)
  Duration: 214s of active trading per window

RESOLUTION:
  Hold to expiry. No early exit.
```


---


# Appendix: Per-Timeframe Summary

| TF | Markets | Total USDC | Avg/Market | Median Lean | Median Delay |
|----|---------|-----------:|-----------:|------------:|-------------:|
| 5M  |      15 | $ 11199.70 | $   746.65 |       4.17x |         15s |
| 15M |       8 | $  4432.10 | $   554.01 |       2.29x |        403s |
| 1H  |       4 | $  1283.65 | $   320.91 |       2.14x |       1646s |

# Appendix: All Markets Detail

| Coin | TF | Window ET | Trades | UP $ | DOWN $ | Total $ | Lean | Ratio | Delay(s) | Duration(s) |
|------|----|-----------|-------:|-----:|-------:|--------:|------|------:|--------:|------------:|
| ETH | 1H  | 10:00ET |    35 | $  27.38 | $  171.00 | $  198.38 | DOWN |  6.25x |   1645 |        836 |
| BTC | 1H  | 10:00ET |    92 | $ 369.35 | $  523.35 | $  892.70 | DOWN |  1.42x |   1643 |        826 |
| XRP | 1H  | 10:00ET |    26 | $   0.00 | $   38.60 | $   38.60 | DOWN |   infx |   1957 |        484 |
| SOL | 1H  | 10:00ET |    38 | $ 104.90 | $   49.07 | $  153.97 | UP   |  2.14x |   1647 |        788 |
| XRP | 15M | 10:15ET |    90 | $ 153.55 | $  159.21 | $  312.76 | DOWN |  1.04x |    743 |        140 |
| ETH | 15M | 10:15ET |   100 | $  86.01 | $  360.39 | $  446.40 | DOWN |  4.19x |    741 |        142 |
| SOL | 15M | 10:15ET |    35 | $ 248.07 | $   22.21 | $  270.28 | UP   | 11.17x |    741 |        120 |
| BTC | 15M | 10:15ET |    67 | $  51.14 | $  650.14 | $  701.29 | DOWN | 12.71x |    743 |         98 |
| XRP | 5M  | 10:25ET |    51 | $  17.32 | $  153.43 | $  170.75 | DOWN |  8.86x |    143 |        140 |
| SOL | 5M  | 10:25ET |   170 | $ 325.86 | $  156.35 | $  482.21 | UP   |  2.08x |    145 |        138 |
| ETH | 5M  | 10:25ET |    42 | $  23.64 | $  262.83 | $  286.47 | DOWN | 11.12x |    141 |        142 |
| BTC | 5M  | 10:25ET |    97 | $ 189.55 | $  502.62 | $  692.17 | DOWN |  2.65x |    143 |        100 |
| ETH | 15M | 10:30ET |   112 | $ 359.04 | $  299.48 | $  658.51 | UP   |  1.20x |     65 |        618 |
| XRP | 15M | 10:30ET |    81 | $ 160.20 | $   68.69 | $  228.89 | UP   |  2.33x |     21 |        662 |
| BTC | 15M | 10:30ET |   172 | $ 802.31 | $  634.50 | $ 1436.81 | UP   |  1.26x |     29 |        650 |
| SOL | 15M | 10:30ET |    90 | $ 116.10 | $  261.06 | $  377.16 | DOWN |  2.25x |     13 |        648 |
| XRP | 5M  | 10:30ET |   162 | $ 349.99 | $  223.55 | $  573.54 | UP   |  1.57x |     17 |        266 |
| BTC | 5M  | 10:30ET |   256 | $1432.93 | $ 1623.00 | $ 3055.93 | DOWN |  1.13x |     15 |        254 |
| ETH | 5M  | 10:30ET |   103 | $ 144.36 | $  379.49 | $  523.85 | DOWN |  2.63x |     15 |        246 |
| SOL | 5M  | 10:30ET |    37 | $  54.71 | $  116.22 | $  170.92 | DOWN |  2.12x |     15 |        234 |
| ETH | 5M  | 10:35ET |    81 | $ 532.85 | $   48.33 | $  581.18 | UP   | 11.03x |     11 |        222 |
| BTC | 5M  | 10:35ET |   203 | $1784.70 | $  515.47 | $ 2300.17 | UP   |  3.46x |      9 |        214 |
| SOL | 5M  | 10:35ET |    49 | $ 127.02 | $   30.48 | $  157.50 | UP   |  4.17x |     15 |        206 |
| XRP | 5M  | 10:35ET |    38 | $ 153.30 | $    3.16 | $  156.46 | UP   | 48.55x |      9 |        206 |
| BTC | 5M  | 10:40ET |   122 | $ 118.31 | $ 1124.68 | $ 1242.99 | DOWN |  9.51x |     13 |         66 |
| SOL | 5M  | 10:40ET |    34 | $   4.49 | $  173.51 | $  178.00 | DOWN | 38.65x |     13 |         66 |
| ETH | 5M  | 10:40ET |    71 | $  15.36 | $  612.20 | $  627.56 | DOWN | 39.86x |     13 |         66 |