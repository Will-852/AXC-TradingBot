# Step 6: `scripts/` 根目錄 — 獨立工具（後勤部門）
> talk12 風格分析 | 2026-03-10

## 點樣搵到
```
axc-trading → scripts/
├── 🔵 掃描系統（眼睛 — 監視市場）
│   ├── async_scanner.py        ← 9 交易所輪轉掃描 18KB
│   ├── public_feeds.py         ← 9 路 API fetcher 12KB
│   ├── light_scan.py           ← 輕量觸發偵測 13KB
│   └── scanner_runner.py       ← 協調器：light_scan → trader_cycle 10KB
│
├── 🟢 指標工具（計數機）
│   └── indicator_calc.py       ← RSI/BB/MACD/ATR 計算器 14KB
│
├── 🟡 介面（嘴巴 — 同你溝通）
│   ├── tg_bot.py               ← Telegram 完整控制中心 69KB ⭐最大
│   ├── slash_cmd.py            ← /pos /bal /pnl 指令 19KB
│   ├── dashboard.py            ← Web Dashboard :5555 105KB ⭐⭐最大
│   └── telegram_sender.py      ← 簡單 TG 發送工具 3KB
│
├── 🔴 監控（保安 — 巡邏系統健康）
│   ├── heartbeat.py            ← 15 分鐘健康監控 13KB
│   ├── health_check.sh         ← 7 類別系統檢查 6KB
│   └── write_activity.py       ← 活動日誌寫入器 2KB
│
├── 🟣 新聞系統（耳朵 — 聽市場消息）
│   ├── news_scraper.py         ← RSS 收集（CoinTelegraph + CoinDesk）7KB
│   └── news_sentiment.py       ← Claude Haiku 情緒分析 8KB
│
├── ⚪ 基建（水電工 — 基礎設施）
│   ├── openclaw_bridge.py      ← Gateway 橋接器 2KB
│   ├── axc_client.py           ← OpenClaw API 客戶端 3KB
│   ├── memory_init.py          ← 記憶系統初始化 3KB
│   ├── binance_feed.py         ← Binance 專用 feed 2KB
│   └── weekly_strategy_review.py ← 每週策略回顧 9KB
│
├── 🛠️ Shell 工具
│   ├── load_env.sh             ← 載入環境變數 1KB
│   ├── backup_agent.sh         ← 自動備份 2KB
│   ├── build_axc_zip.sh        ← 打包分享 2KB
│   └── integration_test.sh     ← 每月整合測試 4KB
│
└── trader_cycle/               ← Step 3-5 已講過（核心引擎）
```

**共 23 個文件**（唔計 trader_cycle/）。按功能分 7 組。

---

## 1. 掃描系統（眼睛）⭐

**比喻：** 你有 9 個偵探輪流去唔同嘅交易所「蒐集情報」。每次只派 1 個去，20 秒後換下一個。

### `async_scanner.py` — 總指揮

```
9 路輪轉：
  Aster → Binance → HyperLiquid → Bybit → OKX →
  KuCoin → Gate.io → MEXC → Bitget → (回到 Aster)

每輪 20 秒 → 每個交易所每 180 秒被查一次
```

功能：
- 每 10 輪自動重讀 `params.py`（hot-reload，唔使重啟）
- 每 30 輪寫心跳到 activity_log
- Thread 數量監控（超過 24 個就告警）
- 磁碟空間監控（<500MB 警告，<100MB 危急）

寫入 3 個文件：
| 文件 | 模式 | 內容 |
|------|------|------|
| `SCAN_LOG.md` | append + rotation | 每輪掃描結果（最多 500 行） |
| `SIGNAL.md` | 原子覆寫 | 最新信號狀態 |
| `prices_cache.json` | merge 模式 | 所有幣種最新價格 |

**Merge 模式**：因為每輪只掃 1 個交易所，所以保留舊數據 + 更新新的。全部 fail → 標 `stale: true`。

### `public_feeds.py` — 9 路 API 適配器

每個交易所嘅 API 格式唔同，呢個文件統一變成：
```python
{symbol: {price, change, high, low, volume}}
```

| 交易所 | API 特點 |
|--------|---------|
| Aster/Binance | 標準 futures ticker |
| HyperLiquid | POST body，冇 24h high/low |
| Bybit/OKX/KuCoin | 各自格式，需要轉換 symbol 名 |
| Gate/MEXC/Bitget | 各自格式 |

### `light_scan.py` — 輕量觸發偵測

**比喻：** 值更員。每 3 分鐘喺門口望一眼，有可疑先叫總指揮。

只查 Aster DEX，4 個觸發：
| 觸發類型 | 條件 | 意思 |
|----------|------|------|
| PRICE | 變動 > 0.38% | 價格大變 |
| VOLUME | 24H 變動 > 3% | 成交異常 |
| SR_ZONE | 價格入 S/R zone | 到關鍵位 |
| FUNDING | 費率變動 > 0.18% | 資金費率異常 |

Exit code: `0` = 冇事 / `1` = 觸發 / `2` = 錯誤

### `scanner_runner.py` — 協調器

**比喻：** 交通燈。確保 light_scan 同 trader_cycle 唔會撞車。

```
flow:
  1. 攞 fcntl 鎖（防重複執行）
  2. 跑 light_scan.py
  3. 有觸發？→ 跑 trader_cycle/main.py
  4. 有信號？→ 發 Telegram
  5. 寫 SIGNAL.md
  6. 放鎖
```

⚠️ 用 `fcntl.flock` 文件鎖，macOS/Linux 才有效。

---

## 2. 指標工具（計數機）

### `indicator_calc.py` — 指標計算器

**比喻：** 萬能計數機。你話邊個幣、邊個時間框，佢就計哂所有指標。

```bash
# 用法
python3.11 indicator_calc.py --symbol BTCUSDT --interval 4h --limit 200
python3.11 indicator_calc.py --symbol BTCUSDT --interval 1h --mode range
```

計算嘅指標（全部喺一個 function `calc_indicators()`）：
| 指標 | 用途 |
|------|------|
| BB (Bollinger Bands) | Range 策略嘅牆壁 |
| RSI | 超買超賣 |
| ADX / DI+/DI- | 趨勢強度 |
| MACD (line/signal/histogram) | 動能 |
| ATR | 止損計算 |
| Stochastic K/D | Range 入場確認 |
| OBV + OBV EMA | 資金流向（Yunis Collection） |
| MA50 / MA200 | Trend 策略嘅火車軌道 |
| Rolling High/Low | S/R level |

**重要：** 呢個文件被 3 個地方 reuse：
1. `market_data.py`（trader_cycle 計指標）
2. `backtest/engine.py`（回測引擎）
3. Agent CLI 直接調用

---

## 3. 介面（嘴巴）

### `tg_bot.py` — Telegram 控制中心 ⭐⭐

**比喻：** 你嘅私人秘書。你喺 Telegram 講嘢，佢識判斷你想做咩。

```
69KB — 整個系統最複雜嘅文件之一

功能：
1. Slash 指令 → slash_cmd.py（零 AI cost）
2. 自然語言 → Claude Haiku 理解 → 執行
3. 落單 → 二次確認 + 冷靜期
4. /ask → RAG 記憶搜索
5. 異常推送（平倉報告 + agent 斷線）
```

安全：
- `ALLOWED_CHAT_ID` 白名單（只有你嘅 chat ID）
- 落單需要 InlineKeyboard 二次確認
- 短期記憶：最近 5 組對話，10 分鐘無活動自動清除

### `slash_cmd.py` — 指令執行器

**比喻：** 自動販賣機。你投幣（打指令），佢出嘢。完全確定性，唔使 AI。

| 指令 | 做咩 | 數據來源 |
|------|------|---------|
| `/report` | 完整報告（結餘+持倉+行情） | Aster API live |
| `/pos` | 持倉一覽 | Aster API live |
| `/bal` | 結餘 | Aster API live |
| `/pnl` | 盈虧概覽（已實現+浮動+資金費） | Aster income API |
| `/sl` | 止損止盈狀態 | Aster open orders |
| `/mode` | 市場模式 + 5 票投票結果 | TRADE_STATE + SCAN_CONFIG |
| `/health` | 系統健康（Gateway+TG+Aster+結餘） | 多源 |
| `/log` | 最近 10 條交易記錄 | TRADE_LOG.md |
| `/stats` | 策略統計（勝率、PF、期望值） | trades.jsonl |
| `/run` | 觸發實盤 cycle | trader_cycle main.py |
| `/dryrun` | 觸發模擬 cycle | trader_cycle main.py |
| `/new` | 信號掃描 | SCAN_CONFIG |
| `/stop` | 暫停交易 | SCAN_CONFIG |
| `/resume` | 恢復交易 | SCAN_CONFIG |
| `/reset` | 清除觸發狀態 | SCAN_CONFIG |

**Report 發送頻率控制：**
- 有持倉 → 每 30 分鐘發
- 冇持倉 → 每 3 小時發（慳電）

### `dashboard.py` — Web Dashboard

**比喻：** 醫院 ICU 嘅監控大屏幕。一眼睇哂所有生命指數。

```
105KB — 整個系統最大嘅文件

Port 5555：http://localhost:5555
前端：canvas/index.html
後端：/api/data JSON endpoint
```

功能包括：
- 實時價格 + 持倉 + 盈虧
- 參數面板（profile-aware）
- PnL 歷史圖
- 系統健康狀態
- 生成分享 zip 包

### `telegram_sender.py` — 簡單發送工具

**比喻：** 信差。只識送信，唔識讀信。

畀 agent 或腳本直接調用發 Telegram 訊息。有 3 個便捷方法：
- `send_heartbeat_ok()` — 心跳正常
- `send_alert(title, body)` — 警報
- `send_trade_report(report)` — 交易報告

---

## 4. 監控（保安）

### `heartbeat.py` — 15 分鐘健康巡邏

**比喻：** 醫院護士每 15 分鐘巡房。有異常就拉警報。

檢查項目：
| 檢查 | 條件 | 動作 |
|------|------|------|
| 倉位 + SL/TP 狀態 | 有倉但冇 SL | 發 TG 警報 |
| TRIGGER_PENDING 超時 | 觸發但長時間未處理 | 發 TG 警報 |
| 日成本異常 | COST_TRACKER 超標 | 發 TG 警報 |
| SCAN_LOG 肥大 | > 180 行 | 自動 trim |

靜音模式：23:00-08:00 UTC+8 只發 URGENT 級別。

### `health_check.sh` — 7 類別系統診斷

**比喻：** 年度體檢。唔係自動跑嘅，你手動跑。

```bash
bash scripts/health_check.sh
```

7 類別：
1. 路徑完整性（舊路徑引用？關鍵文件存在？）
2. 環境變數
3. Python 依賴
4. LaunchAgent 狀態
5. 文件權限
6. API 連接
7. 磁碟空間

結果寫入 `logs/health_check.log`，用 ✅ / ❌ / ⚠️ 標記。

### `write_activity.py` — 活動日誌

**比喻：** CCTV 錄影機。乜嘢事件都記一筆。

```python
write_activity("trade_entry", "BTC SHORT 7x")
write_activity("heartbeat", "Scanner 第 300 輪")
write_activity("error", "Aster API timeout")
```

格式：`shared/activity_log.jsonl`（每行一條 JSON）
超過 1MB 自動 trim 到最新 500 條。

---

## 5. 新聞系統（耳朵）

### `news_scraper.py` — RSS 收集器

**比喻：** 剪報員。每 15 分鐘去兩間報社收新聞。

來源：CoinTelegraph + CoinDesk（RSS feed）

流程：
```
RSS XML → 解析標題+摘要 → 按 symbol 關鍵詞分類
→ 保留最近 6 小時 → 原子寫入 shared/news_feed.json
```

關鍵詞：
- BTC: "bitcoin", "btc"
- ETH: "ethereum", "eth"
- SOL: "solana", "sol"
- XRP: "xrp", "ripple"
- XAG: "silver", "xag"

### `news_sentiment.py` — 情緒分析

**比喻：** 翻譯官。將新聞標題翻譯成「利好/利淡/中性」。

```
讀 news_feed.json（最近 1 小時嘅新聞）
→ 每條送 Claude Haiku 分析
→ 返回 {sentiment: bullish/bearish/neutral, confidence: 0-100}
→ 原子寫入 shared/news_sentiment.json
```

- 用 tier2 Claude Haiku（平價，夠用）
- 已分析嘅文章 URL hash 記住，下次跳過
- 輸出被 `evaluate.py` 讀取做 sentiment filter

---

## 6. 基建（水電工）

### `openclaw_bridge.py` — Gateway 橋接器

偵測 OpenClaw gateway 是否存在。如果冇裝 → 返回 `n/a`，永遠唔 crash。

### `axc_client.py` — OpenClaw API 客戶端

同 OpenClaw gateway 溝通嘅 wrapper。

### `memory_init.py` — 記憶系統初始化

初始化 RAG 記憶（jsonl + npy 文件）。

### `binance_feed.py` — Binance 專用 Feed

Binance 價格 feed，被 `public_feeds.py` 引用。

### `weekly_strategy_review.py` — 每週回顧

自動生成每週策略表現報告。

---

## 7. Shell 工具

| 腳本 | 做咩 | 幾時用 |
|------|------|--------|
| `load_env.sh` | 載入 `secrets/.env` 環境變數 | LaunchAgent wrapper |
| `backup_agent.sh` | 自動備份到 `backups/` | 每日 03:00 cron |
| `build_axc_zip.sh` | 打包系統（排除 secrets/logs/memory） | 分享畀其他人 |
| `integration_test.sh` | 月度整合測試 | 每月 1 號手動跑 |

---

## 數據流總覽

```
┌─────────────────────────────────────────────────────────┐
│                    掃描系統（眼睛）                        │
│                                                         │
│  async_scanner ──→ public_feeds ──→ 9 交易所             │
│       │                                                 │
│       ├──→ SCAN_LOG.md                                  │
│       ├──→ SIGNAL.md                                    │
│       └──→ prices_cache.json                            │
│                                                         │
│  scanner_runner ──→ light_scan ──→ Aster API            │
│       │                  │                              │
│       │                  └──→ SCAN_CONFIG.md            │
│       │                                                 │
│       └──→ (觸發) → trader_cycle/main.py                │
└─────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────┐
│                    介面（嘴巴）                           │
│                                                         │
│  tg_bot ──→ slash_cmd ──→ Aster API (live data)         │
│    │            │                                       │
│    │            └──→ trader_cycle (run/dryrun)           │
│    │                                                    │
│    └──→ Claude Haiku (自然語言理解)                       │
│    └──→ RAG memory (retriever.py)                       │
│                                                         │
│  dashboard ──→ :5555 ──→ canvas/index.html              │
└─────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────┐
│                    監控（保安）                           │
│                                                         │
│  heartbeat ──→ 讀 TRADE_STATE + SCAN_CONFIG             │
│       │                                                 │
│       └──→ Telegram 警報                                │
│                                                         │
│  health_check.sh ──→ 7 類別診斷                          │
└─────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────┐
│                    新聞（耳朵）                           │
│                                                         │
│  news_scraper ──→ RSS feeds ──→ news_feed.json          │
│  news_sentiment ──→ Claude Haiku ──→ news_sentiment.json│
│       │                                                 │
│       └──→ evaluate.py sentiment filter                 │
└─────────────────────────────────────────────────────────┘
```

---

## ⚠️ 分析中觀察到嘅特點

### 🟡 light_scan.py 只掃 4 個 pair
```python
PAIRS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT"]
```
缺 SOLUSDT + XAUUSDT。同 Step 3 發現嘅 PAIR_PRIORITY 問題一致。

### 🟡 slash_cmd.py 嘅 get_prices() 只查 4 個 pair
```python
for pair in ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT"]:
```
SOL + XAU 唔會出現喺 `/report` 行情部分。

### 🟡 TG bot token 硬編碼
`light_scan.py` 同 `settings.py` 都有 hardcode TG_BOT_TOKEN + TG_CHAT_ID。
`slash_cmd.py` 用 env var（正確做法）。應統一用 env var。

### 🟢 scanner_runner.py 用 fcntl 鎖
防止 scanner 同 trader_cycle 同時執行。正確。

### 🟢 原子寫入到處用
`async_scanner.py` 嘅 `atomic_write()` 確保唔會寫到一半 crash 導致損壞。

### 🟢 hot-reload 設計
`async_scanner.py` 每 10 輪自動重讀 `params.py`，改參數唔使重啟 scanner。

---

## 自檢問題

1. **掃描系統有幾多層？** → 2 層。async_scanner（9 路輪轉）+ light_scan（3 分鐘輕量）+ scanner_runner 協調
2. **slash_cmd 點解唔使 AI？** → 所有數據直接讀 API 或文件。確定性 = 零成本 + 快
3. **新聞幾耐更新？** → 每 15 分鐘。scraper 收集 → sentiment 分析（串行）
4. **tg_bot 點解最大？** → 因為佢係控制中心。整合所有功能：指令 + AI + 落單 + 推送
5. **SOL + XAU 漏咗幾多位？** → light_scan（4 pair）、slash_cmd（4 pair）、PAIR_PRIORITY、POSITION_GROUPS — 共 4 個位要修
