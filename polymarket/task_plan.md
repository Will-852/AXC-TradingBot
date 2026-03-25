# Task Plan — 4H Conviction Bot (`run_4h_live.py`)
> Created: 2026-03-25 | Status: **Phase 1 in progress**

## Goal
Build `run_4h_live.py` — 4H conviction bot using ONLY bridge + momentum signals (backtest-proven edge). Reuse 1H architecture, paper-first.

## Backtest Results (ground truth for design)
```
Bridge:    77.5% accuracy, 610/750 windows, +25.9pp vs base
Momentum:  63.6% accuracy, 750/750 windows, +12.0pp vs base
Best combo: momentum+bridge → 72.1% (610 windows, +20.5pp)
All other indicators (RSI/MACD/EMA/OBV/ADX): <51%, NO edge
```

## Key Design Decisions
| # | Decision | Reason |
|---|----------|--------|
| D1 | Only 2 signals: Bridge + Momentum | Backtest: everything else is noise |
| D2 | Entry at T+60min (after 1H momentum read) | Bridge needs observation time; 77.5% at T+1H |
| D3 | Cheap zone $0.20-$0.40 (same as 1H) | Positive selection bias = feature |
| D4 | Profit Lock at 96¢ → sell | Confirmed by live trade: 98¢ sell > hold EV |
| D5 | BTC paper-only first | Gotcha: skip paper = $49 loss |
| D6 | Single file (~800 lines) | 1H is 2000+ lines = too big; 4H simpler = smaller |

## 4H Market Mechanics (confirmed from live screenshot)
- Windows: 12AM/4AM/8AM/12PM/4PM/8PM ET (6 per day)
- Slug: `btc-updown-4h-{unix_timestamp}` (UTC epoch, 14400s aligned)
- Resolution: close >= open → UP (Binance or Chainlink — to verify)
- 7 coins available (BTC + ETH confirmed, others TBD)

## Phases

### Phase 0: Verify slug + discovery ⬜
- [ ] curl Gamma API with real 4H slug → confirm format works
- [ ] Confirm all 7 coins slugs
- [ ] Confirm resolution source (Chainlink vs Binance)

### Phase 1: Core bot (`run_4h_live.py`) ⬜
**~600 lines. Structure mirrors 1H bot but dramatically simpler.**
- [ ] Slug builder + market discovery (6 windows/day, 14400s increments)
- [ ] State management (mm_state_4h.json, independent from 1H)
- [ ] Main loop: 10s fast + 60s heavy cycle
- [ ] Signal engine: Bridge + Momentum only
  - T+0→T+60min: WAIT (observe momentum)
  - T+60min: compute bridge + check momentum → ENTER/WAIT
  - T+60min→T+210min: monitor, optional ADD if conviction grows
  - T+210min→T+240min: hold, no new entries
- [ ] Order execution (dry_run hard block)
- [ ] Fill confirmation (paper sim + live CLOB)
- [ ] Profit Lock (mid ≥ 96¢ → sell)
- [ ] Resolution + PnL tracking
- [ ] Repricing (reuse paper reprice from 1H)

### Phase 2: Paper test deploy ⬜
- [ ] Run --dry-run 48h minimum
- [ ] Measure: fill rate, WR by signal type, avg entry price
- [ ] Go/no-go: WR > 55% on filled trades

### Phase 3: Integration ⬜
- [ ] Update CORE.md + polymarket_redline.md + CLAUDE.md
- [ ] Add to FILEMAP.md
- [ ] TG notifications (reuse 1H pattern)

## Dependencies
- `market_maker.py:compute_fair_up()` — bridge calculation
- `hourly_engine.py` — conviction model reference (fork key logic)
- `gamma_client.py` — Gamma API for market discovery
- `polymarket_client.py` — CLOB order execution

## Errors
| Error | Resolution |
|-------|-----------|
| (none yet) | |

## Anti-patterns to avoid
- DON'T add RSI/MACD/EMA signals (backtest = no edge)
- DON'T copy entire 1H bot (2000 lines of complexity we don't need)
- DON'T skip paper test
- DON'T add cross-TF cascade yet (Phase 4 future, needs data)
