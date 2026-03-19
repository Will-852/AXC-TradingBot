# Task Plan: Polymarket Dashboard — BMD 修正

## Goal
修正 bmd 發現嘅 5 個安全/邏輯漏洞（3 critical + 2 medium）

## Current Phase
Phase 1

## Phases

### Phase 1: 修正 5 個問題
- [x] **💀 #1 run_cycle race** — `_cycle_lock` + running=True 喺 main thread
- [x] **🔴 #2 XSS via title** — `esc()` function, 13 個 call sites
- [x] **🔴 #3 CB panel 空** — `_CB_SERVICES` pre-init 4 services
- [x] **🟡 #4 force_scan mutex** — `_scan_running` flag
- [x] **🟡 #5 PnL series sort** — `sorted(trades, key=timestamp)`
- **Status:** complete

### Phase 2: 驗證
- [x] Unit test 每個 fix — 5/5 passed
- [ ] 2check
- **Status:** in_progress

### Phase 3: 交付
- [ ] Commit
- **Status:** pending

## Decisions
| Decision | Rationale |
|----------|-----------|
| run_cycle 用 threading.Lock（唔用 asyncio） | 同 existing pattern 一致，server 係 threading model |
| XSS fix 用 JS escape function | 唔改 backend，frontend 負責 sanitize display |
| CB pre-init 4 services：polymarket/gamma/claude/binance | 同 pipeline 用嘅 service 一致 |

## Errors
| Error | Attempt | Resolution |
|-------|---------|------------|
