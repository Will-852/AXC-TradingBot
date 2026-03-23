# Findings: W4 Signal Validation

## F1: Momentum signal — 7-day backtest
| Delay | Threshold | WR | Trades |
|-------|-----------|-----|--------|
| 120s | 5bps | 84.0% | 1,097 |
| 240s | 10bps | 98.0% | 710 |
Source: w4_signal_detection.py on 7-day BTC klines

## F2: W4 API limitation
Only 14 min visible. Need on-chain for 162 days.

## F3: W4 is directional conviction, not arb
Combined > $1.00 in 58% of markets. Profit = direction accuracy.

## F4: 13-MONTH BACKTEST CONFIRMS (103,680 windows, 359 days)
| Delay | 0bps | 5bps | 10bps | 15bps | 20bps |
|-------|------|------|-------|-------|-------|
| 60s | 65.6% | 76.4% | 81.5% | 85.2% | 86.7% |
| 120s | 73.1% | **84.1%** | 89.1% | 91.7% | 92.9% |
| 180s | 79.5% | 90.5% | **94.5%** | 96.4% | 97.5% |

- Monthly stability: ZERO degradation (worst month 96.3%)
- Regime: works in trending, normal AND choppy
- PnL sim (180s/20bps, 2:1 lean): $3,340 profit, Sharpe 19.18, every day profitable
- Max drawdown $1.32, max losing streak 3

**⚠️ CRITICAL CAVEAT**: WR measures BTC autocorrelation. By T+120s, Polymarket prices already reflect momentum. Need real OB snapshot to confirm exploitable pricing still exists.

## F5: W4 On-Chain History (287 PnL data points)
- Started: Oct 13, 2025 with $507
- EXPONENTIAL phase: Nov 12 - Dec 4 ($6K → $109K, 17.4x in 22 days)
- This was BEFORE 5M launch (Feb 12, 2026) → initial edge on 15M!
- 5M migration happened Feb 2026 onwards
- 3,016,846 on-chain token transfers total
- 7 distinct growth phases, including 2 dormant periods

## F6: KEY INSIGHT — W4's edge predates 5M
W4 found the momentum edge on 15M first (Nov 2025), then migrated to 5M (Feb 2026) for more volume. The signal works on both timeframes. 5M = more windows = faster compounding.

## F7: ✅ RESOLVED — Polymarket pricing IS exploitable (8,023 OB snapshots)

**Polymarket 極度遲鈍。** BTC 動咗之後，Poly 價格要 10+ 分鐘先收斂到 fair value。

| BTC Move | T+120s Poly Mid | Backtest WR | Fair Value | **Discount** | **EV/share** |
|----------|----------------|-------------|------------|------------|-------------|
| >5bps | $0.571 | 84% | $0.841 | **$0.270** | **$0.320** |
| >10bps | $0.648 | 89% | $0.891 | **$0.243** | **$0.247** |
| >20bps | $0.807 | 93% | $0.929 | **$0.122** | **$0.083** |

T+120s after >10bps move: Poly mid = $0.648, fair = $0.891 → 24¢ discount per share。

## F8: Nov-Dec analysis — high vol = more opportunities, not better signal
- Nov 12 - Dec 4: BTC DOWN 10.7%, NOT up. Correction from $103K.
- Vol 55% annualized (double normal)
- Signal WR was NOT higher in Nov (80.2% vs avg 79%). More TRADES passed threshold.
- W4 grew 17.4x because: more windows × same edge × compound reinvestment

## F9: 5M crushes 15M by 12-17pp
| Threshold | 5M WR | 15M WR | Gap |
|-----------|-------|--------|-----|
| 0bps | 78.9% | 66.2% | -12.7% |
| 10bps | 93.8% | 76.7% | -17.2% |
| 20bps | 97.4% | 81.5% | -15.9% |

5M has 3 min remaining after entry → less reversion risk. 15M has 13 min → more reversion.

## F10: Per-coin strategy differs
- BTC/ETH: lean with momentum (78%/67% follow BTC)
- XRP: 100% follow BTC (proxy play)
- SOL: 78% CONTRARIAN to BTC (mean-reversion play, SOL has lowest persistence 44.6%)
- Fill size scales: BTC $10.21 > ETH $6.08 > SOL $3.94 > XRP $3.32

## F11: Threshold effect is THE KEY
- Raw persistence (0bps filter): 47.9% = mean-reverting
- With 5bps filter: 84.1% = strong persistence
- The filter separates SIGNAL from NOISE. Moves <5bps = 50/50 random. Moves >5bps = 84%+ persistence.

## RESOLVED
- [x] Polymarket OB exploitable? → YES, 24¢ discount at T+120s
- [x] Signal decay over time? → NO, zero degradation across 13 months
- [x] Regime dependent? → NO, works in trending/normal/choppy
- [x] Per-coin differences? → YES, SOL contrarian, XRP proxy, BTC/ETH momentum
