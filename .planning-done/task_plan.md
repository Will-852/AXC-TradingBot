# Task Plan: 1H Crypto Strategy — blue-walnut Pattern + 15M Boundary Exploitation

## Goal
建立 1H crypto prediction market 策略：
1. 分析 swisstony ($5.42M PnL) 做對比驗證
2. 1H signal recorder（15M 結算時刻 OB 變化）
3. 1H MM bot prototype（time-phased scaling + 15M boundary entry）

## Context
- blue-walnut 分析結果：post-15M density 1.58x baseline，post-boundary avg price 低 8-9¢
- 策略核心：early 探索 → 15M 結算後趁 liquidity gap 入場 → late 確認加碼
- 現有 v10 MM bot 係 15M market，呢個係新嘅 1H market bot
- 現有 infra：signal_recorder.py (15M)、gamma_client.py、polymarket_client.py

## Current Phase
Phase 2

## Phases

### Phase 1: 偵察 + swisstony 對比
- [ ] Fetch swisstony trades (0x204f72f35326db932158cba6addd6d74ca91e407b9)
- [ ] 用 whale_1h_timing.py 同一分析框架對比
- [ ] 確認 1H market 嘅 Gamma API 搜索方式（tag/slug pattern）
- [ ] 確認 1H market order book 結構同 15M 有冇分別
- [ ] 記錄 findings.md
- **Status:** complete

### Phase 2: 設計 + BMD
- [x] 1H signal recorder spec
- [x] 1H MM bot 架構
- [x] Entry/exit rules
- [x] BMD 攻擊 → 發現 15M boundary data confounded + 未驗證假設
- [x] 用戶確認修正方案：先驗證再寫 bot
- **Status:** complete

### Phase 3: 驗證性實作（BMD 修正後）
- [ ] 3A: 修正 whale_1h_timing.py — proper boundary vs non-boundary 同分鐘對比
- [ ] 3B: 實測 Gamma API 取得真實 1H slug format
- [ ] 3C: Signal recorder 加 1H market data + 15M boundary burst
- **Status:** in_progress

### Phase 4: 數據收集（7 日）
- [ ] 收集 1H OB data at 15M boundaries
- [ ] 分析 dislocation magnitude + duration
- [ ] 判定：dislocation ≥3¢ for ≥30s?
- **Status:** pending — 等 Phase 3 完成

### Phase 5: Bot（條件觸發）
- [ ] 條件：dislocation 驗證通過 AND v10 fill rate > 15%
- [ ] 寫 run_1h_live.py + hourly_engine.py
- [ ] Paper trade 1 週
- **Status:** blocked — 等 Phase 4 結果

## Decisions
| Decision | Rationale |
|----------|-----------|
| swisstony 先分析再寫 code | 結果：swisstony 做 5M (0.04% margin)，唔適合我哋 |
| 跟 blue-walnut pattern | 高 margin (0.48%)，bankroll 友好 |
| 1H bot 獨立於 v10 15M bot | 唔同 market、唔同 timeframe、唔同 sizing |
| Resolution 用 Binance OHLC | 1H oracle = Binance (唔係 Chainlink) |
| 1H slug 用全名 | `bitcoin-up-or-down-march-22-2026-6am-et` (唔係 timestamp) |
| Focus BTC + ETH only | 最液態，bankroll 唔夠做 4 幣 |
| Signal recorder extend | 共用 exchange data，加 1H discovery + 15M boundary burst |

## Errors
| Error | Attempt | Resolution |
|-------|---------|------------|
