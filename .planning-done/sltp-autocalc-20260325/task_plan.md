# Task Plan: 長線策略 — 鬥分析唔鬥快

## Goal
由「5M 鬥快」轉向「1H/4H/24H 鬥分析」。15M 只改一行 config (reprice 30s→5s)。核心精力投放喺 1H+ 嘅信息優勢。

## Strategic Shift
```
5M:   Speed competition → 我哋輸（300ms HK vs 1ms Amsterdam）
1H+:  Information competition → 我哋贏（Claude AI + human judgment + news + on-chain）
```

用戶原話：「鬥分析、鬥賠率靚、鬥穩陣、鬥長命」

## Current Phase
Phase 1

## Discovery Summary

### 15M（已建成，改一行）
- Reprice cooldown: `run_mm_live.py:439` → `_REPRICE_COOLDOWN_S = 30` 改做 5
- Heavy cycle 已經 5s → 5s cooldown = 每個 heavy cycle 都可以 reprice
- `_REPRICE_MAX_PER_ORDER = 3` lifetime cap 仲係 backstop
- 風險：API rate limit（更多 call/min）

### 1H（已 live，有基礎，需要加 repricing + 增強 signal）
- BTC live, ETH+SOL observe-only
- **72% WR** on paper (25 trades), total PnL $71.52
- Bankroll: $21.57（depleted）
- 4 signal sources: Brownian Bridge + OB quality + volume imbalance + holder imbalance
- **零 repricing** — one-shot set-and-forget
- Entry price: $0.20-$0.39
- 3,600s window, 5s cycle = 720 iterations possible（目前用 0 iterations）

### 4H（未建，需要偵察）
- Slug format 未知（只知 `-4h-` substring）
- 6 windows/day
- Multi-TF cascade (15M+1H+4H) 曾顯示 77.8% WR (n=9)
- 需要先 fetch Gamma API 發現 slug 格式

### 24H（未建，需要偵察）
- Daily market 存在但 slug 未確認
- ~1 window/day per coin
- 長時間分析 = 新聞、macro、on-chain 全部可用
- 用戶表示可以提供新聞 + 鏈上錢包動向

## Phases

### Phase 1: 15M Quick Win（改一行）
- [ ] 改 `_REPRICE_COOLDOWN_S = 30` → `5` at run_mm_live.py:439
- [ ] 記錄改動前 fill rate baseline（from logs）
- [ ] 監控 24h：fill rate、reprice count、API errors
- [ ] 比較前後
- **Status:** in_progress

### Phase 2: 1H Mild Repricing（conviction-driven, cheap zone only）
- [x] **2A: Design review + BMD** ✅
- [ ] **2B: Add config constants** — 5 lines top of run_1h_live.py
- [ ] **2C: Add `_reprice_1h()` function** — ~80 lines (bigger than original 60 due to BMD fixes)
  - ⚠️ [CANCEL-SAFETY] REST double-check pattern (get_trades → 500ms → get_trades)
  - ⚠️ [REPRICE-LOCK] `_repricing_in_progress` flag blocks new ENTER during cycle
  - ⚠️ [WR-ASSUMPTION] Log repriced fills SEPARATELY for WR tracking
- [ ] **2D: Wire into heavy cycle** — 3 lines after _check_fills
  - ⚠️ [REPRICE-LOCK] Check flag before normal signal evaluation
- [ ] **2E: Budget + guard adjustment** — 2 lines
  - ⚠️ [REPRICE-LOCK] Guard must respect lock flag
- [ ] **2F: Paper test 48h minimum** (BMD requirement: was 20 trades, now 48h)
  - Track: fill rate BY price bucket, WR of repriced vs one-shot fills
  - ⚠️ [EV-ESTIMATE] Only go live if repriced-fill WR > 55%
- **Status:** 2B-2E complete ✅, 2F (paper test) pending
- **2check fixes applied:** lock release in buy-fail path, REPRICE+FILL state update, vol_1m from cache, direction flip guard

### BMD Mandatory Pre-conditions (from Opus Challenger) — ALL ADDRESSED ✅
1. ✅ [CANCEL-SAFETY] REST double-check (500ms gap) — run_1h_live.py:1057-1120
2. ✅ [REPRICE-LOCK] `_repricing_cid` flag — run_1h_live.py:940, 1055, 1583
3. ⚠️ [WR-ASSUMPTION] 唔好用 72% 做 baseline — range 55-72%（paper test 驗證）
4. ⚠️ [DATA-SOURCE] 唔好引用 15M data 做 1H 證據（獨立追蹤）
5. ⚠️ [EV-ESTIMATE] EV improvement range -15% to +32%（paper test 驗證）

### Phase 3: 4H Strategy — Discovery ✅ + Design ✅ + Implementation
- [x] **3A: Discovery** ✅ — slug = `{coin}-updown-4h-{unix}`, 7 coins, 6 windows/day
- [x] **3B: Design** ✅ — Hybrid: early conviction + cross-TF cascade size-up
- [ ] **3C: Implementation** — `run_4h_live.py`
  - [ ] Slug builder (timestamp-based, increment 14400s)
  - [ ] HourlyConfig for 4H (recalibrate vol, wider spread)
  - [ ] Cross-TF cascade module (read 15M + 1H state files)
    - ⚠️ [VERIFY-LATER] 15M state file format + path
    - ⚠️ [VERIFY-LATER] Cross-TF correlation validity
  - [ ] Repricing (reuse 1H pattern, adjust caps $0.30-$0.45)
  - [ ] State: `mm_state_4h.json`
  - [ ] Entry: 2-phase (small early → ADD on cascade confirm)
- [ ] **3D: Paper test** — 48h minimum
  - ⚠️ [EV-ESTIMATE] All config values need paper validation
  - ⚠️ [VERIFY-LATER] Bridge model at 4H vol may need recalibration
- [ ] **3E: Daily market (research only)**
  - Daily slug confirmed: `bitcoin-up-or-down-{month}-{day}-{year}-9am-et`
  - Different paradigm: news/macro-driven, not bridge-driven
  - Defer to Phase 5
- **Status:** 3A+3B complete, 3C pending (after 1H paper test)

### Phase 4: Signal Enhancement — Goldsky + News + Cross-TF
- [ ] **4A: Goldsky holder imbalance upgrade**
  - Endpoints configured: `settings.py` GOLDSKY_*_URL + GOLDSKY_TRACKED_WHALES
  - ⚠️ [VERIFY-LATER] Endpoint versions may change (0.0.7, 0.0.14)
  - ⚠️ [VERIFY-LATER] Rate limits unknown — start 1 req/5s
  - ⚠️ [VERIFY-LATER] Whale addresses may rotate
  - Goal: distinguish spread-capture bots (WR ~50%, ignore) from directional holders (WR >60%, follow)
  - Query: Goldsky positions-subgraph → filter by GOLDSKY_TRACKED_WHALES → if whale holds UP token in 1H market → confidence boost
  - ⚠️ [VERIFY-LATER] Goldsky position data latency unknown — may be minutes behind CLOB
- [ ] **4B: News sentiment signal**
  - 用戶可提供 real-time headlines
  - Claude API 分析 sentiment → directional modifier
  - Only for 4H/24H（1H 太快做 news analysis）
  - ⚠️ [VERIFY-LATER] News → price impact lag 未知。可能 already priced in。
- [ ] **4C: Cross-TF cascade**
  - 15M signal confirms 1H direction → conviction boost
  - Multi-TF alignment (15M+1H+4H) showed 77.8% WR on n=9（⚠️ [DATA-SOURCE] 小樣本）
- [ ] **4D: ToD + session edge**
  - Current: skip HKT 09h, 19h
  - Expand with more data（48h paper test 結果）
- **Status:** pending — Goldsky endpoints configured in settings.py ✅

### Phase 5: 4H/24H Implementation
- [ ] Build 4H bot（reuse 1H architecture + repricing）
- [ ] Build 24H bot（可能唔需要 repricing — time 充裕）
- [ ] Paper test both
- **Status:** pending

## Decisions
| Decision | Rationale |
|----------|-----------|
| 15M 只改一行 | 已建成，唔好搞壞。測試 5s cooldown 效果 |
| 1H 係核心 focus | 72% WR + 零 repricing = 最大改進空間 |
| 4H/24H research first | Slug 未知 + market 特性未研究 |
| 鬥分析唔鬥快 | 長 timeframe = information advantage > speed advantage |
| News + on-chain = future edge | 用戶可提供，Claude 可分析 |

## Errors
| Error | Attempt | Resolution |
|-------|---------|------------|

## 紅線
- 15M 改完要監控 24h，唔好改第二樣
- 1H repricing 要 paper test 先，唔好直接 live
- Cancel 前 REST double-check（phantom fill protection, 500ms gap）
- 4H/24H research only，唔好急住 implement

## ⚠️ [VERIFY-LATER] Registry — 二次驗證清單
> 後續 session 檢查呢啲假設是否仲成立

### Code Level
| Marker | 位置 | 驗證方法 | 狀態 |
|--------|------|---------|------|
| [CANCEL-SAFETY] | run_1h_live.py:1057-1120 | 48h paper test 冇 phantom fill = OK | ⏳ |
| [REPRICE-LOCK] | run_1h_live.py:940,1055,1583 | Check logs for "DEDUP repricing in progress" | ⏳ |
| [WR-ASSUMPTION] | findings.md | Paper test: repriced fill WR > 55% | ⏳ |
| [BOOK-DEPTH] | run_1h_live.py:1043 | Paper test: reprices actually trigger (not all skipped) | ⏳ |
| [EV-ESTIMATE] | findings.md | Paper test: net PnL positive after 48h | ⏳ |

### Config Level
| Marker | 位置 | 驗證方法 | 狀態 |
|--------|------|---------|------|
| [VERIFY-LATER] Goldsky endpoint versions | settings.py:GOLDSKY_*_URL | Phase 4 開始前 curl test | ⏳ |
| [VERIFY-LATER] Goldsky rate limits | settings.py comment | First query: monitor 429 responses | ⏳ |
| [VERIFY-LATER] Whale addresses rotate | settings.py:GOLDSKY_TRACKED_WHALES | Monthly: re-check Goldsky PnL | ⏳ |
| [VERIFY-LATER] Goldsky position latency | Phase 4A | Test: place order → check position subgraph delay | ⏳ |
| [VERIFY-LATER] News impact already priced in | Phase 4B | Backtest: news time vs 1H market price reaction | ⏳ |
| [DATA-SOURCE] Multi-TF cascade n=9 | Phase 4C | Need 50+ windows to validate | ⏳ |

### Strategy Level
| Marker | 驗證方法 | 狀態 |
|--------|---------|------|
| 15M repricing 30s→5s 有效 | 24h 後比較 fill rate >41.6% | ⏳ |
| 1H repricing improves EV | 48h paper test: EV/signal > $0.168 (baseline) | ⏳ |
| Goldsky MB→TS pattern 對 1H 唔適用 | 確認 1H winners 係 hold-to-resolution 唔係 round-trip | ⏳ |
| Decent-Dune 真係 -$35K | Cross-check Goldsky PnL with on-chain transfers | ⏳ |
