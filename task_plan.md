# Task Plan: 1H Conviction Pricing Bot

## Goal
基於 backtest 驗證嘅 1H conviction strategy 寫 bot：觀察 25-40 min → 方向確認 → $0.30-0.40 入場 → hold to resolution

## Backtest Results (30 days, 720 windows)
- Wait 40m + 0.3σ: WR 90.2%, $10.92/day, MaxDD $0.60
- Wait 40m + 0.8σ: WR 97.6%, $5.67/day, MaxDD $0.40 ← sweet spot
- Wait 40m + 1.5σ: WR 100%, $1.60/day, MaxDD $0.00

## Infra Audit Summary
- 90% reusable from run_mm_live.py (15M bot)
- Reuse: compute_fair_up, plan_opening, _execute, _check_fills, _check_resolutions, state mgmt
- Modify: discovery (1H slug), entry gates (25-40min wait), timing constants
- New: ~460 lines (run_1h_live.py + config)

## Current Phase
Phase 1

## Phases

### Phase 1: 實作
- [ ] 1A: settings.py 加 CRYPTO_1H_* constants
- [ ] 1B: run_1h_live.py — 1H bot 主入口
  - [ ] _discover_1h() — slug-based + tag fallback
  - [ ] Entry conviction logic (wait 25-40min, threshold 0.3-0.8σ)
  - [ ] Brownian Bridge fair value (reuse compute_fair_up)
  - [ ] Order execution (reuse plan_opening + _execute)
  - [ ] Resolution (Binance 1H OHLC, auto-detect)
  - [ ] State mgmt (mm_state_1h.json)
- **Status:** in_progress

### Phase 2: Dry-run 驗證
- [ ] --dry-run mode 跑 2-3 個 window
- [ ] 確認 discovery 正確（slug format）
- [ ] 確認 fair value + conviction signal 合理
- [ ] 確認 order placement 邏輯
- [ ] 2check
- **Status:** pending

### Phase 3: Paper trade
- [ ] --live mode but 極小注 (1% = $1.38)
- [ ] 跑 24h 收集 fill rate data
- [ ] 同 backtest 對比 WR
- **Status:** pending

## Decisions
| Decision | Rationale |
|----------|-----------|
| Wait 40min default | Backtest: WR 90%+ at 40min, 最高 $/day |
| Threshold 0.5σ default | Balance: 61% entry rate × 94% WR = $7.93/day |
| Entry $0.40 cap | 1.5x win/loss ratio, same as 15M bot |
| Skip M1 signal | Too noisy for 1H; Brownian Bridge sufficient |
| Single file (run_1h_live.py) | Import shared components from market_maker.py |
| Binance OHLC resolution | 1H oracle = Binance (confirmed) |

## Errors
| Error | Attempt | Resolution |
|-------|---------|------------|
