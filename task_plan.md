# Task Plan: AXC Edge Improvements — Trailing Stop + Slippage + Correlation + Noise MC

## Goal
4 項 quick-win 改進，按 ROI 排序：
1. **Trailing Stop** — re-enable for trend/crash only（range 保持 fixed TP）
2. **Configurable Slippage/Commission** — 移除 hardcode，加入 param
3. **Correlation Gate** — live trading 開倉前 check pair correlation，防集中虧損
4. **Noise Injection MC** — 對 price 加 random noise re-run backtest，測策略 robustness

## Current Phase
Phase 1

## Phases

### Phase 1: Trailing Stop（trend/crash only）
- [ ] 讀 engine.py trailing stop 現有 code（L483-487）+ BTPosition
- [ ] 設計：trend/crash 用 ATR trail，range 保持 fixed TP
- [ ] 實作 + test
- **Status:** pending

### Phase 2: Configurable Slippage/Commission
- [ ] 移除 engine.py L62-63 hardcode
- [ ] 加入 BacktestEngine constructor param
- [ ] Dashboard param panel 加 input
- **Status:** pending

### Phase 3: Correlation Gate（live trading）
- [ ] 讀 trader_cycle risk check 現有 code
- [ ] 加 rolling correlation check before entry
- [ ] 高 correlation + 同方向 → 減半 risk
- **Status:** pending

### Phase 4: Noise Injection MC
- [ ] 新 function：對 OHLC 加 ±0.2% noise → re-run engine
- [ ] 加入 metrics_ext 或獨立 script
- [ ] Dashboard 顯示 noise robustness score
- **Status:** pending

### Phase 5: 驗證 + 交付
- [ ] 2check 所有改動
- [ ] Backtest 跑一次驗證 trailing stop 效果
- [ ] Commit
- **Status:** pending

## Decisions
| Decision | Rationale |
|----------|-----------|
| Trailing 只對 trend/crash | Range 係 mean reversion，trail 會被 oscillation 掃出 |
| Trail activation at 1.5R | 太早（1R）breakeven 容易被 noise 掃；1.5R 有 buffer |
| Noise std = 0.2% | BTC 1H candle typical noise level，唔影響大 trend |
| Correlation threshold 0.85 | 經驗值：>0.85 = 近乎同一資產 |

## Errors
| Error | Attempt | Resolution |
|-------|---------|------------|
