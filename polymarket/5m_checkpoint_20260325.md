# 5M Bot Checkpoint — 2026-03-25 12:40 HKT

## Current Mode: 方案 A（兩邊都買）
- PID: 27192
- BTC LIVE | ETH/SOL/XRP paper
- delay=60s, threshold=8bps, tiered lean, taker veto, profit lock @99¢

## Decision Gate: 20 BTC live trades
After 20 trades, compare:
- Actual WR vs 77% (backtest 8bps@T+60s)
- Actual fill rate (both sides)
- Actual combined price
- Actual PnL per trade

### If WR ≥ 70%: Stay 方案 A, consider increasing budget
### If WR 55-70%: Stay 方案 A but review signal quality
### If WR < 55%: Switch to 方案 B (skip hedge for strong signals) or pause

## 方案 B (pending evaluation)
- Skip hedge when signal > 20bps (T3)
- EV = $1.89/trade (5× higher than A)
- Risk = max loss $3.50/trade (2.7× higher than A)
- Only activate after WR verified > 70%

## Known Issues (this session)
1. Paper PnL was polluting bankroll → FIXED (live-only PnL)
2. First order fail → naked position → FIXED (abort second)
3. Daily loss fuse triggered by paper PnL → FIXED + hard reset
4. min_order_size=5 crushes all tiers to ~1.3-1.8:1 → KNOWN, accept at $72 bankroll
5. Profit lock bid discount 2¢→1¢ → DONE
6. Dead hours removed → DONE
7. lean_dir logging bug → FIXED

## Bankroll
- Wallet: ~$234
- Bot bankroll: $72 (30% fraction)
- Per trade: $5 (7%)
- Real BTC trades so far: 2 (1W 1L, net ~-$0.22)

## Unverified Assumptions
- T+60s WR = 73-77% (backtest, not validated live)
- Taker veto improves WR (data says +60bps agree, -500bps disagree)
- Combined price median = $1.02 (from OB analysis)
- Profit lock at 99¢ fills reliably (1 success so far)
