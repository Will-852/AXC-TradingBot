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

## 依賴清單（shared_infra only）
| import | 用途 |
|--------|------|
| `shared_infra.exchange.exceptions` | Error hierarchy（7 classes） |
| `shared_infra.exchange.retry` | retry_quadratic decorator |
| `shared_infra.pipeline` | Pipeline + Step framework |
| `shared_infra.file_lock` | FileLock (fcntl) |
| `shared_infra.wal` | WriteAheadLog |
| `shared_infra.telegram` | send_telegram |
