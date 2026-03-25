# Progress — 4H Conviction Bot

## 2026-03-25 Session

### Done
- [x] 4H indicator backtest completed (365d, 2550 candles, 10 indicators)
  - Fixed pagination bug (Binance spot limit=1000, not 1500)
  - Results: Bridge 77.5% + Momentum 63.6%, all others <51%
- [x] Scout completed: 1H architecture, 4H slug format, constraints
- [x] Live 4H market screenshot analyzed: confirmed window timing + UI
- [x] Planning files created (task_plan.md, findings.md, progress.md)

### In Progress
- [x] Phase 0: Verify slug + discovery via Gamma API ✅
  - Slug confirmed: `{coin}-updown-4h-{unix_ts}`
  - 6 coins: btc, eth, sol, doge, xrp, bnb
  - Resolution: Chainlink (data.chain.link)
  - Volume ~$97K, Liquidity ~$7.5K per market
- [x] Phase 1: Write run_4h_live.py ✅
  - ~580 lines, single file, clean architecture
  - 2-signal engine: Bridge + Momentum only
  - Paper fill simulation (same pattern as 1H)
  - Smoke test passed: 12 markets discovered, 6 entries (all DOWN = correct)
- [ ] Phase 2: Paper test deploy (next)

### Decisions
- 2-signal only design (bridge + momentum) based on backtest evidence
- Entry at T+60min observation window
- Profit Lock at 96¢ (confirmed by live trade math)
