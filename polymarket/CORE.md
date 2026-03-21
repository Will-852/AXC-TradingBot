# Polymarket — 核心原則

## 1. 根基：零和博弈
- 預測市場每筆 fill 都有對手盤
- **第一問：「點解佢肯賣俾我？」**
- 答唔到 → 你係 dumb money，對面有 edge 你冇
- 呢條問題貫穿所有決策：入場、加注、持倉、離場

## 2. 業務範圍（紅線 — 禁止踩過界）

**自動化只限以下市場，其他一律唔准自動操作（2026-03-21 更新）：**

| 系統 | 市場 | 時間框架 | 狀態 |
|------|------|---------|------|
| MM 15M Bot | BTC+ETH Up/Down | 15 分鐘 window (24/7) | 🟢 LIVE |
| 1H Conviction Bot | BTC+ETH Up/Down | 1 小時 window (24/7) | 🟢 LIVE |
| ~~Weather~~ | ~~全球最高溫~~ | ~~24 小時~~ | ❌ 廢棄 + 代碼已清除（2026-03-22） |

> **嚴禁**：
> - 自動操作用戶手動落嘅注（sports、general crypto、任何非上面嘅市場）
> - Bot 只管自己 create 嘅 positions
> - 用戶手動加入 state 嘅 position = 只讀監控，唔准 exit/sell
> - 信號指向上面以外嘅市場 → SKIP，唔落單
>
> **違規案例（2026-03-19）**：Pipeline 自動賣出用戶手動落嘅 NBA Nets 注，
> 虧損 $1.82。原因：position 被加入 state 後被 drift exit rule 觸發。
> 教訓：非自動化範圍嘅 position 唔應該被 exit rules 管。

## 3. 交易決策流程
1. **方向 > Edge** — 確定啱邊先落注，big edge + wrong side = 0
2. **信心閾值** — P(direction) < 55% = SKIP，ranging market 要 60%
3. **Lead confirmation** — 15 分鐘 lead 期間 BTC 走勢 confirm/contradict model
4. **選擇性入場** — 唔係每局都玩，100 個 window 可能只有 5-10 個值得

## 4. GTO Framework

### 4a. Defensive GTO — 避開陷阱

**市場分類 + Adverse Selection 風險**
| Type | Base Risk | 策略 | 例子 |
|------|-----------|------|------|
| live_event | 0.95 | BLOCK | NBA score, match result |
| news_driven | 0.75 | LIMIT near mid (3%) | Fed rate, CEO fired |
| quantifiable | 0.15 | LIMIT aggressive (10%) | Temperature, gas fee |
| crypto_15m | 0.40 | MARKET (FOK) | BTC Up/Down |
| crypto | 0.50 | LIMIT near mid (5%) | Default |

**GTO Decision Rules（對應 gto.py:343-368）**
1. `live_event` → 永遠 BLOCK（場內有人睇住比分）
2. `fill_quality == "bad"` on non-quantifiable → BLOCK
3. `adverse_selection > 0.80` → BLOCK
4. `nash_eq > 0.90` AND `edge < 10%` → SKIP（市場已 efficient）
5. `is_dominant_strategy` → APPROVE + full Kelly
6. `unexploitability < 0.30` → BLOCK（order 太容易被 exploit）
7. 其餘 → APPROVE，Kelly scaled by unexploitability

**Nash Equilibrium 原則**
- 高 Nash score = 市場接近均衡 = 冇 edge = skip
- 低 Nash score = 市場失衡 = 有機會
- Price near 50% + tight spread + deep liquidity → 最高 Nash score

### 4b. Offensive GTO — 逆推對手方向（概念階段）
- 對手每步行動 = information leak
- 觀察行為 → 推演方向 → 反制或跟隨
- 類比象棋：唔係猜對手係邊個，而係睇佢做咗咩
- 實作框架：CVD / order flow 重新 frame 為「對手資訊逆推」
- **核心修正**：focus net flow result，唔猜身份
  - ✅ 「賣壓有冇被吸收」「買盤 net flow 係正定負」
  - ❌ 「大戶在賣」「散戶在追」

## 5. BTC 15M — Exchange Signal 原則
> 適用於 pipeline 嘅 edge_finder + crypto_15m 路徑（DORMANT）。
> MM live bot (v15) 已移除 assess_edge，改用 Student-t Bridge + OB only → 見 `docs/mm_v15_pipeline.md`

- **PRIMARY signal**：交易所數據（BTC 價格、成交量、訂單簿）
- **SECONDARY reference**：Polymarket 成交量（流動性低，noise 大）
- 觀察重點：15 分鐘內有冇大型 net flow 傾斜
- **Spoofing 防範**：只信 executed volume，唔信 resting orders
  - 大掛單可以隨時撤，成交量造唔到假
- **Time decay**：越接近 window 結束，signal 要越強
  - 開頭 5 分鐘嘅 signal ≠ 最後 2 分鐘嘅 signal
- **唔推演身份**：唔猜「邊個在賣」→ 只睇「賣壓有冇被吸收」

## 6. 兩個 Live 系統嘅差異

| | MM 15M Bot | 1H Conviction Bot |
|--|-----------|-------------------|
| 入口 | `run_mm_live.py` | `run_1h_live.py` |
| 時間框架 | 15 分鐘 | 1 小時 |
| 幣種 | BTC + ETH | BTC + ETH |
| 定價模型 | Student-t(ν=5) Bridge + OB | Brownian Bridge + OB Conviction |
| 落注方式 | Dual-Layer（hedge + directional） | Conviction-based directional |
| 共用 | `market_maker.py`（MMMarketState, resolve_market） | 同左 |
| 獨立 state | `mm_state.json` | `mm_state_1h.json` |

### MM 15M Exit Rule（v15，3-Layer）
```
Layer 1 — Profit Lock:  mid ≥ 95¢ → sell 90%, keep 10% + greed hedge
Layer 2 — Cost Recovery: mid ≥ 64¢ → sell enough to recover entry cost, keep rest
Layer 3 — Stop Loss:    pnl_pct < -25% → sell all @ mid × 0.97
Scalp re-entry:         after SL, up to 3 rounds (R2×0.90, R3×0.80)
Forced hold:            last 5 min = 唔可以 sell
```
> 詳細 → `docs/mm_v15_pipeline.md`

## 7. 落注規則
- Bankroll: **live balance** | Per bet: **1%** | Per market: **10%** | Max exposure: **30%**
- Kelly: half Kelly × confidence × GTO × capped at 1% bankroll
- Daily loss > 15% → circuit breaker（6h cooldown）
- Consecutive loss CB：Pipeline = **3** (`risk_manager.py`) / MM bot = **5** (`run_mm_live.py`)
- MM kill switch: -20% daily / -20% total / WR<48% (rolling 30) → STOPPED

## 8. 架構原則
- 寄生於 AXC shared_infra（retry, exceptions, telegram, pipeline）
- 唔 import AXC trader_cycle 任何嘢
- 唔被 AXC import
- 獨立 config、logs、state
- shared/ 入面嘅 SCAN_CONFIG.md + news_sentiment.json 係共用讀取（read-only）

## 9. 依賴清單（shared_infra only）
| import | 用途 |
|--------|------|
| `shared_infra.exchange.exceptions` | Error hierarchy（7 classes） |
| `shared_infra.exchange.retry` | retry_quadratic decorator |
| `shared_infra.pipeline` | Pipeline + Step framework |
| `shared_infra.file_lock` | FileLock (fcntl) |
| `shared_infra.wal` | WriteAheadLog |
| `shared_infra.telegram` | send_telegram |
