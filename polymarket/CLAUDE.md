# Polymarket — Claude Code 入口
> ⚠️ 此文件上限 150 行。Claude Code 自動載入。
> 最後更新：2026-03-28

## 身份
獨立預測市場交易子系統，寄生於 AXC shared_infra 但邏輯完全獨立。
詳細業務規則 → `polymarket/CORE.md`（必讀）

## Current Phase: 🟢 Live（1H primary）
| 系統 | 狀態 | 入口 |
|------|------|------|
| 1H Conviction | 🟢 **LIVE — PRIMARY** | `run_1h_live.py`（BTC+ETH+SOL+XRP live, ETH UP gated） |
| 4H Conviction | 📝 DRY-RUN（2026-03-28 bankroll merge） | `run_4h_live.py` |
| Daily (24H) | 📝 DRY-RUN | `run_daily_live.py` |
| MM 15M (v15) | ❌ STOPPED（2026-03-28） | `run_mm_live.py` |
| 5M W4 | ❌ STOPPED（2026-03-28） | `run_5m_live.py` |
| Research Cycle | 🟢 ACTIVE（6h） | `research_cycle.py` |
| General Pipeline | 🟡 DORMANT | `pipeline.py`（last run 2026-03-20） |

## 業務範圍（紅線 — 2026-03-28 更新）
- **自動化只限：BTC+ETH+SOL+XRP 1H Conviction（primary）**
- **ETH 1H：只允許 DOWN direction**（UP+ETH = 20% WR → gate blocked）
- **XRP**: live but same daily cap (2 entries/day shared across all coins)
- 4H/Daily = dry-run only，MM/5M = stopped
- 其他市場唔准自動操作。用戶手動落嘅注 = 只讀監控，唔准 exit/sell
- 詳細 → `CORE.md` §2 + `memory/rules/polymarket_redline.md`

## 交易系統

### 1. 1H Conviction Bot（`run_1h_live.py`）★ 主力
- **BTC+SOL live | ETH DOWN-only**（UP+ETH gated off — 20% WR）
- Brownian Bridge fair-value + **taker flow gate** + volume imbalance filter + ToD gate
- **Direction bias**: DOWN ×1.3 / UP ×0.7（91.7% vs 53.8% WR, N=25）
- **Taker flow gate**: first 15min Binance buy ratio, agree/disagree filter（z=3.7, N=329）
- **Bankroll**: 50% of wallet（2026-03-28: was 30%, increased after 4H merge）
- 共用 `market_maker.py`（MMMarketState + resolve_market）
- 獨立 state：`mm_state_1h.json`, `mm_trades_1h.jsonl`
- One-order-per-market guard（唔會重複入同一 market）
- Signal tape：`logs/signal_tape_1h.jsonl`（real Poly mid snapshots every 20s）

### 2. 4H / Daily / MM / 5M — DRY-RUN or STOPPED
- 4H: dry-run（plist changed 2026-03-28, bankroll $28→1H）
- Daily: dry-run（paper data collection, 4/4 correct）
- MM 15M: STOPPED（no edge, momentum paradox）
- 5M: STOPPED（latency disadvantage）

### 3. General Pipeline（`pipeline.py`）— DORMANT
- 14-step pipeline，覆蓋 crypto / logical arb（天氣已清除）
- LaunchAgent plist 存在但未 load
- edge_finder.py 有 Claude AI fallback（`_call_claude()`），但 pipeline 冇跑
- 2026-03-22 重構：execution logic 抽到 `exchange/executor.py`

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
數據：data/market_data.py（6 exchanges, 22+ sources, parallel fetch）
交易所：polymarket_client.py | gamma_client.py | hl_hedge_client.py | executor.py
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
- `crypto_15m.py` direct import `indicator_calc`（2026-03-28 BMD fix: was subprocess）
- `cvd_strategy.py` lazy import `backtest.fetch_agg_trades`（頂層 backtest/）
- 所有入口用 `sys.path` hack 注入 `scripts/`
- `hl_hedge_client.py` 直接用 `hyperliquid-python-sdk`

## 落注規則速查
- Bankroll: **live balance** | Per bet: **3%** | Per market: **10%** | Max exposure: **30%**
- Kelly: half Kelly × confidence × GTO × capped at 3%
- Daily loss > 15% → CB（6h cooldown）— pipeline only
- MM daily cap（graduated）: >$10 sizing×0.67 / >$20 ×0.33 / >$30 2h cooldown / >$45 halt
- MM kill switch: 8 consecutive / WR<28%（rolling 30 filled trades）

## 跑法
```bash
cd ~/projects/axc-trading
# 1H Conviction (PRIMARY)
PYTHONPATH=.:scripts python3 polymarket/run_1h_live.py --live      # 或 --dry-run
# 4H Conviction (dry-run)
PYTHONPATH=.:scripts python3 polymarket/run_4h_live.py --dry-run
# Daily (dry-run)
PYTHONPATH=.:scripts python3 polymarket/run_daily_live.py --dry-run
# Pipeline (dormant)
PYTHONPATH=.:scripts python3 polymarket/pipeline.py --dry-run --verbose
```

## ⚠️ Known Issues
1. ~~Trade log 路徑~~ ✅ | ~~Weather scope~~ ✅ | ~~Exit signals~~ ✅（全部 2026-03-18 已修）
2. ~~indicator_calc.py 硬編碼~~ ✅（2026-03-28 BMD fix: subprocess→direct import）
3. **HL credentials 未填**：`secrets/.env` HL_PRIVATE_KEY 係空
4. **Position Merger Phase 2**：on-chain merge execution 未做
5. ~~mm_v9 doc 過時~~ ✅ 已修（2026-03-21）：新建 `docs/mm_v15_pipeline.md`
6. ~~紅線 scope 不符~~ ✅ 已修（2026-03-21）：紅線擴大至 BTC+ETH 15M + 1H
7. ~~Weather 代碼未清理~~ ✅ 已修（2026-03-22）：全部天氣代碼已移除（weather_tracker.py + run_weather_paper.py 刪除，12 個文件清理）

## Gotchas
- GTO live_event = 永遠 BLOCK（場內有人睇住比分）
- `_SHORT_KEYWORD_LEN=4`：短 keyword 用 word boundary regex
- State file 喺 `shared/POLYMARKET_STATE.json`，唔係 polymarket/ 內
- MM bot 同 1H bot 用獨立 state files，唔共用

## 💀 Real Money Safety Checklist（MANDATORY）
> $106 loss (2026-03-22 duplicate entry) + $35 loss (2026-03-24 phantom fill). 呢啲 step 唔可以 skip。
> **危險代碼位置登記冊 → `docs/DANGER_ZONES.md`**（9 個已知高危位，改前必讀）

**改 entry/order logic 後：**
```bash
# ORDER PATH AUDIT — 列出所有落單路徑，逐個解釋
grep -n "_execute\|buy_shares\|plan_opening\|PlannedOrder" polymarket/run_mm_live.py
```
每個 call site 要回答：「呢個 path 應唔應該存在？有冇舊 code 未 disable？」

**改完任何 code 後：**
- Save lesson to `gotchas.md` or `lesson_for_me.md`（涉及蝕錢 = MUST save）

**Restart 前：**
- Worst case trace：「如果所有嘢都出錯，最多蝕幾多？」
- 確認 answer < bankroll × 2%

**唔准 skip 嘅情況：** 涉及 `_execute`, `buy_shares`, `PlannedOrder`, pricing, sizing

## Proxy
- AI model: `claude-sonnet-4-6` via `PROXY_BASE_URL`（同 AXC 共用 proxy）
- Temperature: 0.3（低 = 穩定概率估計）
