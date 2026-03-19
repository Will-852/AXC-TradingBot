# Task Plan — BMD Fix: 15M Pipeline 安全修正

## Goal
修正 BMD 發現嘅 5 個問題（P0 × 2 + P1 × 1 + P2 × 2），確保 15M trade 路徑安全。

## Phases

### Phase 1: Stale Data Freshness Check (P0) `status: complete`
防止基於過時 Binance 數據落注。

**改動：**
| File | Change |
|------|--------|
| `polymarket/strategy/microstructure_strategy.py` | `_fetch_5m_klines()` 加 freshness check: 最新 candle open_time vs now > 2min → return None |
| `polymarket/strategy/cvd_strategy.py` | `_fetch_data()` 加 freshness check on latest aggTrade/kline timestamp |
| `polymarket/strategy/crypto_15m.py` | `_fetch_15m_indicators()` 加 freshness check on returned indicator timestamp |

**驗證：** Mock stale data → 確認 signal 返回 None

### Phase 2: AI Fallback Outcome Check (P0) `status: complete`
防止 AI fallback 喺 outcome 順序反轉時買錯方向。

**改動：**
| File | Change |
|------|--------|
| `polymarket/strategy/edge_finder.py` | AI fallback path (~line 797) 加 outcome[0] validation: must be "up" or "yes" |

**驗證：** Mock reversed outcomes → 確認 skip

### Phase 3: Pre-exec Liquidity Re-check (P1) `status: complete`
防止 scan 後 liquidity 歸零仲落注。

**改動：**
| File | Change |
|------|--------|
| `polymarket/pipeline.py` | `ExecuteTradesStep` 加 pre-exec liquidity check via Gamma API single market fetch |

**驗證：** Dry-run pipeline 確認 re-check log

### Phase 4: Signal Conflict Warning (P2) `status: complete`
3 個 signal 互相矛盾時 log warning。

**改動：**
| File | Change |
|------|--------|
| `polymarket/strategy/edge_finder.py` | 三個 candidate 有不同 side → log warning（唔 block） |

### Phase 5: Resolution Datetime Fix (P2) `status: complete`
15M 市場 resolution check 用 datetime 唔用 date。

**改動：**
| File | Change |
|------|--------|
| `polymarket/risk/position_manager.py` | resolution check 用 `end_date` 包含時間 → 改用 `datetime.fromisoformat()` 或 parse event end time |

## Errors
| # | Phase | Error | Resolution |
|---|-------|-------|------------|
| — | — | — | — |

## Decisions
| # | Decision | Reason |
|---|----------|--------|
| 1 | Freshness threshold = 2 min | 15M 市場 lead time 15-50 min，2 min stale 已經 material |
| 2 | Signal conflict = warn only | 唔 block，因為最大 edge 策略 backtest 已驗證有效 |
| 3 | Pre-exec re-check 只查 crypto_15m | Weather 流動性本來就低（$200-$1500），re-check 意義唔大 |
