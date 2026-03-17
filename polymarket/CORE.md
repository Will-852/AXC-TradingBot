# Polymarket — 核心原則

## 業務範圍
- **Crypto 15M**: BTC Up/Down 5-min window markets (24/7)
- **Weather**: 亞洲每日最高溫 — Japan (Tokyo) + Shanghai, HK later

## 交易決策流程
1. **方向 > Edge** — 確定啱邊先落注，big edge + wrong side = 0
2. **信心閾值** — P(direction) < 55% = SKIP, ranging market 要 60%
3. **Lead confirmation** — 15 分鐘 lead 期間 BTC 走勢 confirm/contradict model
4. **選擇性入場** — 唔係每局都玩，100 個 window 可能只有 5-10 個值得

## 落注規則
- Bankroll: $100 USDC
- Max per bet: $10
- Max total exposure: 30%
- 0.45 ≤ P(Up) ≤ 0.55 → SKIP
- Lead period contradicts model → SKIP
- Entry price > 0.55 → SKIP (price cap)

## 架構原則
- 寄生於 AXC shared_infra（retry, exceptions, telegram, pipeline）
- 唔 import AXC trader_cycle 任何嘢
- 唔被 AXC import
- 獨立 config、logs、state
- shared/ 入面嘅 SCAN_CONFIG.md + news_sentiment.json 係共用讀取（read-only）

## GTO（Game Theory Optimal）Rules

### 核心認知：預測市場係零和博弈
- 每筆 fill 都有對手盤。問「點解佢肯賣俾我？」
- 如果冇答案 → 你就係 dumb money

### 市場分類 + Adverse Selection 風險
| Type | Base Risk | 策略 | 例子 |
|------|-----------|------|------|
| live_event | 0.95 | BLOCK | NBA score, match result |
| news_driven | 0.75 | LIMIT near mid (3%) | Fed rate, CEO fired |
| quantifiable | 0.15 | LIMIT aggressive (10%) | Temperature, gas fee |
| crypto_15m | 0.40 | MARKET (FOK) | BTC Up/Down |
| crypto | 0.50 | LIMIT near mid (5%) | Default |

### GTO Decision Rules
1. `live_event` → 永遠 BLOCK（場內有人睇住比分）
2. `fill_quality == "bad"` on non-quantifiable → BLOCK
3. `adverse_selection > 0.80` → BLOCK
4. `nash_eq > 0.90` AND `edge < 10%` → SKIP（市場已 efficient）
5. `is_dominant_strategy` → APPROVE + full Kelly
6. 其餘 → APPROVE，Kelly scaled by unexploitability

### Nash Equilibrium 原則
- 高 Nash score = 市場接近均衡 = 冇 edge = skip
- 低 Nash score = 市場失衡 = 有機會
- Price near 50% + tight spread + deep liquidity → 最高 Nash score

## 依賴清單（shared_infra only）
| import | 用途 |
|--------|------|
| `shared_infra.exchange.exceptions` | Error hierarchy（7 classes） |
| `shared_infra.exchange.retry` | retry_quadratic decorator |
| `shared_infra.pipeline` | Pipeline + Step framework |
| `shared_infra.file_lock` | FileLock (fcntl) |
| `shared_infra.wal` | WriteAheadLog |
| `shared_infra.telegram` | send_telegram |
