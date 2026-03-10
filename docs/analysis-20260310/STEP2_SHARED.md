# Step 2: `shared/` — 系統公告板
> talk12 風格分析 | 2026-03-10

所有部門（scanner、trader、dashboard、telegram bot）都靠讀寫 `shared/` 嘅文件嚟溝通。

---

## 白板 1: `TRADE_STATE.md` — 「而家有冇打緊仗？」

**比喻：** 運動員嘅即時戰績板 — 教練（trader cycle）同觀眾（dashboard）都睇呢塊板。

```
SYSTEM_STATUS: ACTIVE
MARKET_MODE: TREND              ← 而家係趨勢市
MODE_CONFIRMED_CYCLES: 9       ← 已確認 9 個 cycle

POSITION_OPEN: YES              ← 有倉
PAIR: BTCUSDT                   ← 買緊 BTC
DIRECTION: SHORT                ← 賭佢跌
ENTRY_PRICE: 70470.6            ← 喺呢個價入嘅
SL_PRICE: 72239.4               ← 升到呢度就認輸止損（+2.5%）
TP_PRICE: 67658.4               ← 跌到呢度就收錢（-4.0%）

BALANCE_USDT: 80.29             ← 戶口淨值
CONSECUTIVE_LOSSES: 1           ← 連輸 1 次
COOLDOWN_ACTIVE: NO             ← 冇暫停
```

### 寫入者
- `trader_cycle/state/trade_state.py` — 主要寫入者
- `tg_bot.py` — auto-sync 時更新

### 讀取者
- `dashboard.py` — 顯示持倉狀態
- `tg_bot.py` — /pos 指令查詢
- `trader_cycle` — 檢查有冇倉、冷卻期

---

## 白板 2: `SCAN_CONFIG.md` — 「市場而家點？」

**比喻：** 天氣報告板 — 溫度（價格）、風速（ATR）、天氣預測（S/R）。

### 6 個區域（2026-03-10 快照）

| 區域 | 內容 | BTC | ETH | XRP | XAG |
|------|------|-----|-----|-----|-----|
| PRICES | 最新價格 | $70,514 | $2,058 | $1.385 | $88.40 |
| ATR | 4H 波動幅度 | $1,131 | $40.7 | $0.023 | $1.66 |
| SR support | 支撐位 | $65,614 | $1,911 | $1.322 | $79.7 |
| SR resistance | 阻力位 | $71,613 | $2,099 | $1.423 | $90.0 |
| FUNDING | 資金費率 | 0.009% | -0.0005% | 0.004% | -0.0004% |
| SR_ZONES | ±0.3×ATR 範圍 | 65275-65953 | 1899-1923 | 1.31-1.33 | 79.2-80.2 |

### State Flags
```
CONFIG_VALID: true              ← 數據有效
SILENT_MODE: ON                 ← 靜默模式（冇出 Telegram 報告）
SILENT_MODE_CYCLES: 1117        ← 已靜默 1117 cycles（≈23 日）
TRIGGER_PENDING: OFF            ← 冇待處理觸發
```

### 寫入規則（重要！）
| 寫入者 | 可以寫嘅欄位 |
|--------|------------|
| trader-cycle | 所有欄位（PRICES, ATR, SR, FUNDING, CONFIG_VALID, SILENT_MODE） |
| light-scan | 只可以寫 TRIGGER_PENDING, TRIGGER_PAIR, TRIGGER_REASON, LIGHT_SCAN_COUNT |

### Protection Rules
- `last_updated` 超過 60 分鐘 → `CONFIG_VALID: false` → 跳過 S/R + funding 檢查
- ATR = 0 → 跳過所有 S/R 計算
- 避免用過期數據做決策

### ⚠️ 觀察到嘅問題
1. **SOL + XAU 冇出現** — TRADER_OWNED_FIELDS 已加，但 trader cycle 未跑新 cycle
2. **SILENT_MODE_CYCLES: 1117** — 23 日冇出報告。可能因為長期冇 STRONG 信號觸發退出靜默

---

## 白板 3: `SIGNAL.md` — 「而家有冇信號？」

```
觸發：4 個
- STRONG BTCUSDT $70528  24H_3.6pct     ← 24小時升 3.6%，超過 2% threshold
- LIGHT  ETHUSDT $2059   24H_2.7pct     ← 有波動但介乎邊界
- LIGHT  XRPUSDT $1.39   24H_2.3pct
- STRONG SOLUSDT $86.60  24H_3.2pct     ← SOL 都有 STRONG 信號
```

### STRONG vs LIGHT
- **STRONG** = 24H 變動 > `TRIGGER_PCT`（AGGRESSIVE profile = 2%）
- **LIGHT** = 有波動但未夠強，或者只係微超閾值

### 寫入者
- `async_scanner.py` — 每 20 秒掃描後更新

---

## 價格箱: `prices_cache.json` — 「各交易所最新報價」

| Pair | 價格 | 24h 變動 | 信號 | 來源交易所 | stale? |
|------|------|---------|------|-----------|--------|
| BTC | $70,528 | +3.59% | STRONG | KuCoin | No |
| ETH | $2,059 | +2.68% | LIGHT | KuCoin | No |
| XRP | $1.386 | +2.30% | LIGHT | KuCoin | No |
| XAG | $88.39 | +5.67% | STRONG | OKX | No |
| XAU | $5,153 | +1.32% | NO_SIGNAL | OKX | No |
| SOL | $86.60 | +3.18% | STRONG | KuCoin | No |

### stale 欄位
- `false` = 數據新鮮
- `true` = 超過 5 分鐘冇更新 → dashboard 顯示警告

---

## 其他文件速查

| 文件 | 大小 | 做咩 | 幾時睇 |
|------|------|------|--------|
| `pnl_history.json` | 15KB | 每日 PnL 記錄 | 想睇歷史表現 |
| `news_feed.json` | 47KB | RSS 新聞原文 | debug 新聞 agent |
| `news_sentiment.json` | 5KB | 新聞情緒分數 | 想知點解 block LONG |
| `SCAN_LOG.md` | 14KB | 掃描記錄（最新 200 行） | debug scanner |
| `TRADE_LOG.md` | 2KB | 交易記錄 | 想睇過往交易 |
| `activity_log.jsonl` | 18KB | 系統活動日誌 | debug 用 |
| `PROTOCOL.md` | 2KB | 讀寫權限規則 | 想理解邊個寫邊個讀 |
| `balance_baseline.json` | <1KB | 帳戶基準線 | PnL 計算用 |
| `pending_orders.json` | <1KB | 待處理訂單 | debug 落單 |
| `price_history.json` | 2KB | 歷史價格（短期） | 趨勢追蹤 |
| `SYSTEM_STATUS.md` | <1KB | 系統開關 | 維護時用 |
| `binance_market.json` | 2KB | Binance 市場數據快照 | debug 用 |

---

## 數據流圖

```
Scanner ──寫──→ prices_cache.json ──讀──→ Dashboard
         ──寫──→ SIGNAL.md         ──讀──→ Telegram Bot

Trader  ──寫──→ SCAN_CONFIG.md     ──讀──→ Light Scan
Cycle   ──寫──→ TRADE_STATE.md     ──讀──→ Dashboard + TG Bot
        ──寫──→ TRADE_LOG.md       ──讀──→ Dashboard

News    ──寫──→ news_feed.json     ──讀──→ Trader Cycle
Agent   ──寫──→ news_sentiment.json ──讀──→ (sentiment block)
```

---

## 自檢問題

1. **SILENT_MODE 1117 cycles 正常嗎？** → 如果你唔想收 Telegram 通知就正常，但可能錯過重要信號
2. **SOL + XAU 點解冇 ATR/SR？** → settings.py 已加，等下次 trader cycle 跑就會寫入
3. **BTC SHORT 倉 SL $72,239** → 而家 BTC $70,514，距 SL $1,725（2.4%）。留意。
4. **CONSECUTIVE_LOSSES: 1** → 再輸一次就觸發 30 分鐘冷卻期（COOLDOWN_2_LOSSES_MIN = 30）
