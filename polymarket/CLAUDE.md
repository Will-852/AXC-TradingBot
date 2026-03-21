# Polymarket — Claude Code 入口
> ⚠️ 此文件上限 150 行。Claude Code 自動載入。
> 最後更新：2026-03-21

## 身份
獨立預測市場交易子系統，寄生於 AXC shared_infra 但邏輯完全獨立。
詳細業務規則 → `polymarket/CORE.md`（必讀）

## Current Phase: 🟢 Live（部分）
| 系統 | 狀態 | 入口 |
|------|------|------|
| MM 15M (v15) | 🟢 LIVE | `run_mm_live.py`（BTC+ETH） |
| 1H Conviction (v15) | 🟢 LIVE | `run_1h_live.py` |
| Research Cycle | 🟢 ACTIVE（6h） | `research_cycle.py` |
| General Pipeline | 🟡 DORMANT | `pipeline.py`（last run 2026-03-20） |
| Weather | ❌ 廢棄（冇 edge） | 代碼仍 entangled in edge_finder |

## 業務範圍（紅線 — 2026-03-19 事故後確立，2026-03-21 擴大）
- **自動化只限：BTC+ETH 15M（MM bot）+ BTC+ETH 1H（Conviction bot）**
- 其他市場唔准自動操作。用戶手動落嘅注 = 只讀監控，唔准 exit/sell
- 詳細 → `CORE.md` §2 + `memory/rules/polymarket_redline.md`

## 三個交易系統

### 1. MM 15M Bot（`run_mm_live.py`）★ 主力
- Dual-Layer market maker：Zone 1/2/3 hedge + directional
- 5s fast loop + **10s** heavy cycle + 300s discovery
- Bridge: **Student-t(ν=5)** + OB adj（assess_edge 已移除，fair = bridge + OB）
- Cancel: window-2min / adverse BTC **0.5%** ETH **0.7%** / **dynamic TTL** 60s-600s
- Exit: Profit Lock (mid≥95¢) + Cost Recovery (mid≥64¢) + Stop Loss (-25%)
- Forced hold: **last 5 min**（唔係 2 min）
- 詳細（含 2-rung ladder / scalp re-entry / CVD disagree / per-order log 等）→ `docs/mm_v15_pipeline.md`

### 2. 1H Conviction Bot（`run_1h_live.py`）
- Brownian Bridge fair-value + OB conviction model
- BTC + ETH 1H candles，slug-based discovery
- 共用 `market_maker.py`（MMMarketState + resolve_market）
- 獨立 state：`mm_state_1h.json`, `mm_trades_1h.jsonl`

### 3. General Pipeline（`pipeline.py`）— DORMANT
- 14-step pipeline，覆蓋 crypto / weather / logical arb
- LaunchAgent plist 存在但未 load
- edge_finder.py 仲有 Claude AI fallback（`_call_claude()`），但 pipeline 冇跑

## Pipeline 步驟（順序已修正 — gotcha fix）
```
pipeline.py（DORMANT）
1   ReadState        → POLYMARKET_STATE.json
2   ReplayWAL        → crash recovery
3   ScanMarkets      → Gamma API → match categories（原 step 4）
4   CheckPositions   → sync 持倉 + PnL（原 step 5）
5   SafetyCheck      → circuit breaker（原 step 3，需 positions 先 load）
5.5 MergeCheck       → detect mergeable pairs（report only）
6   ManagePositions  → exit triggers
6.5 CloseHedge       → close HL hedge
6.7 ExecuteExits     → sell flagged positions（WAL-safe）
7   FindEdge         → triple signal + AI fallback
7.3 LogicalArb       → pricing contradictions（零 AI）
7.5 GTOFilter        → adverse selection + Nash eq（零 AI）
8   GenerateSignals  → edge > threshold → PolySignal
9   SizePositions    → binary Kelly
10  ExecuteTrades    → Poly order + HL hedge
11  WriteState       → atomic write
12  SendReports      → Telegram
```

## 文件索引 → `polymarket/FILEMAP.md`
```
核心：market_maker.py | hourly_engine.py | edge_finder.py | gto.py
設定：config/settings.py | config/params.py | config/categories.py
風控：risk_manager.py | circuit_breaker.py | binary_kelly.py
交易所：polymarket_client.py | gamma_client.py | hl_hedge_client.py
工具：tools/（5 files）| analysis/（1 file）| backtest/（10 files）
```

## 依賴關係（隔離規則）
```
polymarket → shared_infra   ✅（pipeline, retry, WAL, telegram, file_lock）
polymarket → shared/ files  ✅ READ-ONLY（SCAN_CONFIG.md, news_sentiment.json）
polymarket → trader_cycle   ❌ 禁止
AXC → polymarket            ❌ 禁止（唯一例外：dashboard tab）
```

**Hard coupling（已知，暫時接受）：**
- `crypto_15m.py:154` subprocess → `scripts/indicator_calc.py`（硬編碼 python3.11）
- `cvd_strategy.py` lazy import `backtest.fetch_agg_trades`（頂層 backtest/）
- 所有入口用 `sys.path` hack 注入 `scripts/`
- `hl_hedge_client.py` 直接用 `hyperliquid-python-sdk`

## 落注規則速查
- Bankroll: **live balance** | Per bet: **1%** | Per market: **10%** | Max exposure: **30%**
- Kelly: half Kelly × confidence × GTO × capped at 1%
- Daily loss > 15% → CB（6h cooldown）
- MM kill switch: -20% daily / -20% total / 5 consecutive / WR<48%

## 跑法
```bash
cd ~/projects/axc-trading
# MM 15M
PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --live      # 或 --dry-run
# 1H Conviction
PYTHONPATH=.:scripts python3 polymarket/run_1h_live.py --live      # 或 --dry-run
# Pipeline (dormant)
PYTHONPATH=.:scripts python3 polymarket/pipeline.py --dry-run --verbose
# BTC paper
PYTHONPATH=.:scripts python3 polymarket/run_btc_paper.py --predict
# Position watcher (manual daemon)
PYTHONPATH=.:scripts python3 polymarket/position_watcher.py --live
```

## ⚠️ Known Issues
1. ~~Trade log 路徑~~ ✅ | ~~Weather scope~~ ✅ | ~~Exit signals~~ ✅（全部 2026-03-18 已修）
2. **indicator_calc.py 硬編碼**：`/opt/homebrew/bin/python3.11`（換機要改）
3. **HL credentials 未填**：`secrets/.env` HL_PRIVATE_KEY 係空
4. **Position Merger Phase 2**：on-chain merge execution 未做
5. ~~mm_v9 doc 過時~~ ✅ 已修（2026-03-21）：新建 `docs/mm_v15_pipeline.md`
6. ~~紅線 scope 不符~~ ✅ 已修（2026-03-21）：紅線擴大至 BTC+ETH 15M + 1H
7. **Weather 代碼未清理**：edge_finder.py top-level import weather_tracker（entangled）

## Gotchas
- GTO live_event = 永遠 BLOCK（場內有人睇住比分）
- `_SHORT_KEYWORD_LEN=4`：短 keyword 用 word boundary regex
- State file 喺 `shared/POLYMARKET_STATE.json`，唔係 polymarket/ 內
- MM bot 同 1H bot 用獨立 state files，唔共用

## Proxy
- AI model: `claude-sonnet-4-6` via `PROXY_BASE_URL`（同 AXC 共用 proxy）
- Temperature: 0.3（低 = 穩定概率估計）
