# AXC v1 → v2 搬遷指南
> 最後更新: 2026-03-13
> 搬遷前必讀。每一項都有「點解要做」同「唔做會點」。

---

## 搬遷前檢查

- [ ] **確認冇 open position** — 最安全係等冇倉位先搬。如果有倉，要用 dual-write（見下）
- [ ] v1 所有 launchd 服務已停（`launchctl list | grep openclaw`）
- [ ] v2 dry-run 跑過至少 48h（≥96 cycles），0 errors

---

## 1. State 格式切換（最高風險）

### 改咗咩
v2 trade state 由 `TRADE_STATE.md`（regex parse）改為 `TRADE_STATE.json`（結構化）。

### 注意事項

| 問題 | 影響 | 修正 |
|------|------|------|
| Dashboard 直接 regex parse MD | v2 寫 JSON 後 dashboard 數據凍結 | ✅ 已修（`get_trade_state()` 先讀 JSON） |
| `parse_md()` fallback 喺多處 | balance/SL/TP fallback 仲讀 MD | 🟡 低風險：只有 exchange API fail 時先行到 |
| Scanner 讀 TRADE_STATE | 如果 scanner 直接 parse MD，會讀到舊數據 | ⚠️ 確認 scanner 用 `read_trade_state()` 定直接 parse |
| tg_bot `slash_cmd` | 可能直接 parse MD | ⚠️ 搬遷前 grep `TRADE_STATE.md` 確認 |

### Rollback
```bash
# 即刻切返 MD 格式（唔使改 code）
export STATE_FORMAT=md
# 重啟所有服務
```

### 搬遷步驟
1. 確認 `TRADE_STATE.json` 已存在（v2 首次 cycle 自動 migrate）
2. 比對 JSON 同 MD 內容一致
3. 搬遷後保留 MD 檔 14 日先刪

---

## 2. Exchange Client 重構

### 改咗咩
AsterClient + BinanceClient 由 430 行獨立代碼 → 30 行 config subclass（繼承 `HmacExchangeClient`）。

### 注意事項

| 問題 | 影響 | 修正 |
|------|------|------|
| 外部 import 路徑 | 如果有代碼 `from aster_client import AsterClient`，舊 class path 可能斷 | grep 所有 `import aster_client` / `import binance_client` 確認 |
| API 行為一致性 | 共用 HMAC 邏輯理論上行為一樣，但未經 live 驗證 | 搬遷後跑 1-2 單小額 live trade 驗證 |
| HyperLiquid `retry_quadratic` | 改用共用 decorator，retry 行為應該相同 | 低風險 |

---

## 3. Pipeline 新步驟

### 改咗咩
Pipeline 由 18 步 → 20 步（加咗 `ValidateOrderStep` + 改咗步驟號碼）。

### 注意事項

| 問題 | 影響 | 修正 |
|------|------|------|
| ValidateOrderStep 太嚴 | 正常 signal 被 block（false positive） | 設 `USE_VALIDATION_PIPELINE=false` 關掉 |
| DataFreshness 2% 閾值 | 高波動市場可能誤殺 | 搬遷初期觀察，需要時調高閾值 |
| DuplicateValidator | 阻止同 pair 開第二倉 | 如果策略需要加倉，要改 validator 邏輯 |

---

## 4. Margin 監控

### 改咗咩
Position 加咗 `liquidation_price`, `maint_margin`, `margin_ratio` 欄位。ManagePositionsStep 加咗 margin alert。

### 注意事項

| 問題 | 影響 | 修正 |
|------|------|------|
| Alert-only（唔會自動平倉） | Phase A 只發 Telegram alert，唔會 auto-close | 預期行為 |
| 閾值：ratio < 1.5, liq < 2% | 可能太鬆或太緊 | 觀察 2 週後調整 |
| HL `marginUsed` vs `isolatedWallet` | HL API 返嘅 field name 可能唔同 | 已做 fallback（`isolatedWallet or marginUsed`） |

---

## 5. Fee + Slippage 追蹤

### 改咗咩
ExecuteTradeStep 加咗 `_extract_commission()` 同 `_calc_slippage()`。TradeJournal 加咗 `net_pnl`, `commission`, `entry_slippage_pct`。

### 注意事項

| 問題 | 影響 | 修正 |
|------|------|------|
| Slippage alert 0.5% | 高波動幣（POL）可能頻繁觸發 | 觀察後調整 `SLIPPAGE_ALERT_PCT` |
| Commission 提取依賴 `fills[]` | 如果 exchange 返嘅格式唔同，commission = 0 | 唔會 block 交易，只影響追蹤準確度 |

---

## 6. 備份系統

### 改咗咩
JSON 寫入前自動 backup 到 `shared/backups/`。保留 48 個最近 + 7 日 daily。

### 注意事項

| 問題 | 影響 | 修正 |
|------|------|------|
| Backup 唔係 atomic write | Crash 中途可能得半個 backup | 低風險，有其他 backup 補 |
| 48 個 × ~2KB = ~96KB | 磁碟空間唔會爆 | 正常 |
| `shared/backups/` 新目錄 | v1 冇呢個目錄，v2 自動建立 | 正常 |

---

## 7. Dashboard 端口

### 改咗咩
v2 dashboard port 改為 `DASHBOARD_PORT` env var，預設 5566。

### 注意事項
- v1 = `localhost:5555`，v2 = `localhost:5566`
- 搬遷後如果要用返 5555：`export DASHBOARD_PORT=5555`

---

## 8. LaunchAgent Plist 更新

### 搬遷時要改嘅 plist

每個 plist 要改 `AXC_HOME` 路徑：

```bash
# 搵出所有引用 axc-trading 嘅 plist
grep -l "axc-trading" ~/Library/LaunchAgents/ai.openclaw.*.plist

# 每個 plist 入面改：
# axc-trading → axc-trading-v2
# 或者設 AXC_HOME env var
```

**唔好忘記嘅 plist**：
- `ai.openclaw.tradercycle.plist` — 主要交易邏輯
- `ai.openclaw.dashboard.plist` — 加 `--port 5566` 或改返 5555
- `ai.openclaw.scanner.plist` — 掃描器
- `ai.openclaw.telegram.plist` — Telegram bot

---

## 9. 完整搬遷 Checklist

### Phase 1: 準備（搬遷前 1 日）
- [ ] v2 dry-run 48h 完成，0 errors
- [ ] `grep -rn "TRADE_STATE.md" scripts/` 確認所有 consumer 都經 `read_trade_state()` 或已修
- [ ] `grep -rn "parse_md.*TRADE_STATE" scripts/` 確認 fallback 路徑

### Phase 2: 搬遷（5 分鐘，最好冇 open position）
- [ ] `launchctl bootout` 停所有 v1 服務
- [ ] 確認 v2 `shared/TRADE_STATE.json` 內容正確
- [ ] 更新所有 plist 路徑指向 v2
- [ ] `launchctl bootstrap` 啟動所有 v2 服務
- [ ] 開 dashboard `localhost:5566` 確認數據正確

### Phase 3: 驗證（搬遷後 24h）
- [ ] Telegram `/status` 正常
- [ ] Dashboard 數據跟 live exchange 一致
- [ ] 至少 1 次 trade cycle 正常完成（可以用 dry-run）
- [ ] Backup 目錄有正常 snapshot
- [ ] 冇重複 Telegram bot instance（`ps aux | grep tg_bot`）

### Phase 4: 清理（搬遷後 14 日）
- [ ] 刪除 `TRADE_STATE.md`（JSON 已穩定 14 日）
- [ ] 移除 `_read_md()` 同 `_write_md()` 代碼（可選）
- [ ] 刪除 v1 目錄 `~/projects/axc-trading`（確認冇用到先刪）

---

## Rollback（萬一出事）

```bash
# 1. 停 v2
launchctl bootout gui/$(id -u)/ai.openclaw.tradercycle

# 2. 切 state format
export STATE_FORMAT=md

# 3. 或者直接改返 plist 指向 v1
# 4. 重啟
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.tradercycle.plist
```

全程 < 1 分鐘。`STATE_FORMAT=md` 會令 `read_trade_state()` 忽略 JSON 直接讀 MD。
