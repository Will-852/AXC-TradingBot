# Findings: Polymarket 5M Momentum Strategy

## 1. 12h Live Data (2026-03-24 to 2026-03-25)
- 191 trades (7 live, 185 paper), PnL -$182.07
- Trade WR 35.1%, Lean accuracy 58.1%
- Combined median $1.02 (85% > $1.00)
- Root cause: momentum paradox (maker lean doesn't fill, hedge adverse-fills)
- Source: `polymarket/logs/mm_trades_5m.jsonl`, `mm_w4_5m.jsonl`

## 2. Momentum WR Backtest (30 days, 8,639 windows)
- T+45s: 8bps=76.0%(n=1432), 15bps=83.2%(n=340), 20bps=83.6%(n=165)
- DOWN baseline: 50.2% (no directional bias)
- Regime: low-vol WR=78.7% at 8bps (n=61, small sample)
- Source: `analysis/momentum_wr_calculator.py`, `analysis/data/momentum_wr_results.json`

## 3. 5M vs 15M Spread (from Uncommon-Oat wallet)
- 5M combined: 0.80-0.98 (wider spread = more arb edge for maker)
- 15M combined: 0.84-0.96 (narrower)
- BTC σ_5m ≈ 0.16% vs σ_15m ≈ 0.28% (√3 factor)
- Wider 5M spread = maker arb mode more viable than 15M

## 4. FOK Trap (SDK reverse engineering)
- `create_market_order(price=0)` = REST fetch book → calc worst price → submit GTC+FOK limit
- Thin book + 200ms lag between fetch and submit → "no match" kill
- Fix: aggressive GTC limit at ask+2¢ (guaranteed on-book, near-instant fill)
- Source: `py_clob_client/client.py:554-560`, `order_builder/builder.py:197-215`

## 5. Fee Structure (current + upcoming change)
- Current: `C * p * 0.25 * (p*(1-p))^2` → p=0.55 → 1.53%
- After 2026-03-30: `C * p * 0.072 * (p*(1-p))^1` → p=0.55 → 1.78%
- Maker fee = 0%, rebate = 20% of taker fees
- Source: Polymarket docs, `analysis/ultimate_reality_check.md`

## 6. 5+5 Shares = Zero Directional Edge (BMD finding)
- $5 budget + min 5 shares + price ~$0.50 → always 5:5 regardless of R
- 5+5 at combined $1.00 = EV zero at ANY WR
- Tier system (T1/T2/T3) is cosmetic at this budget
- Fix: taker single-side for directional edge, maker arb only for combined < $1.00

## 7. Wallet Reverse Engineering Summary
- Unlawful-Shear: combined $1.04, T+9s entry, 67% DOWN, lean 1.36, +$136K
- Female-Billing: combined $0.95, T+17s, 57% DOWN, lean 1.34, multi-coin
- All top wallets: accept combined > $1.00, profit from directional accuracy
- Source: `analysis/wallet_0x{b27b,d1eb,910e,2eb5,c173}.md`

## 8. Break-Even Analysis
- Maker arb (combined < $1.00): guaranteed profit, no WR needed
- Taker single-side at $0.55 fill: BE = 55% WR
- Taker single-side at $0.50 fill: BE = 50% WR (+ fee)
- With 1.53% fee at $0.55: effective BE ≈ 55.8%
- Lean accuracy 58.1% > 55.8% → marginal but positive edge
