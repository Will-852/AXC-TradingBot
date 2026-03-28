# AXC Trading — 架構判斷 + 教學筆記
> 手動維護 — 放機器生成唔到嘅嘢（危險位、設計決定、比喻、教學）
> 事實/數據 → 見 `docs/ARCHITECTURE_AUTO.md`（自動生成）
> 最後更新：2026-03-29

---

## 1. 系統心智模型

AXC = 一間 24 小時運作嘅交易工廠，兩間公司租同一棟大廈（完全獨立）。

```
🏭 工廠大門
├── 👷 員工部門（agents/）  — 平做簡單、貴做重要
├── 🔧 工具間（scripts/）    — 交易引擎 + 掃描器 + Dashboard
├── 🎰 預測市場（polymarket/）— 做莊 + 信念策略
├── 🧠 記憶（memory/）       — AI 嘅海馬體
└── 🔐 保險箱（secrets/）    — 永遠唔上雲
```

---

## 2. 危險位登記冊

### polymarket/mm/

| 風險 | 涉及文件 | 原因 |
|------|---------|------|
| 🔴 entry_logic 太大 | entry_logic.py (1,055 行) | God function — 入場 + 分層 + T2 + 重入場 + 模擬全部混埋 |
| 🔴 全域狀態 | data_feeds / order_lifecycle / exit_logic | 6 個 module-level 變數（`_cache`, `_holder_cache`, `post_fill_checks`, `endgame_mid_buf` 等），crash 後記憶體歸零但檔案狀態留低 → 不一致 |
| 🟡 Paper mock 混 live | entry_logic.py | PaperClient 同 T2PaperClient 嵌喺真錢 code 入面 |
| ⚠️ 50+ constant 分散引用 | 全部 → constants.py | 改一個 constant 名要改 5-6 個文件 |

### scripts/trader_cycle/

| 風險 | 涉及文件 | 原因 |
|------|---------|------|
| 🔴 adjust_positions.py (822 行) | risk/adjust_positions.py | 止損 + 止賺 + 提早走 + 重入場混埋，改一個影響其他 |
| 🟡 trade_state.py (546 行) | state/trade_state.py | 同時支援 JSON + MD 兩種格式 + 遷移邏輯，格式錯 = 丟持倉 |
| 🟡 position_sizer.py (511 行) | risk/position_sizer.py | Kelly + ATR + sizing 混埋，算錯 = 注碼失控 |
| 🟡 hyperliquid_client.py (493 行) | exchange/hyperliquid_client.py | 獨立實作唔繼承 base — 行為可能同其他交易所唔一致 |
| 🟡 mode_detector.py (451 行) | strategies/mode_detector.py | HMM 判錯 = 用錯策略 = 全部訊號偏 |
| ⚠️ SCAN_CONFIG.md 用 regex 解析 | state/scan_config.py | Markdown 格式稍變 = 解析失敗 |

---

## 3. 系統邊界安全判定

**結論：trader_cycle ↔ polymarket 幾乎完全獨立 ✅**

| 資源 | trader_cycle | polymarket/mm | 會撞？ |
|------|-------------|---------------|--------|
| 狀態文件 | TRADE_STATE.json | POLYMARKET_STATE.json | ❌ |
| 鎖文件 | .pipeline.lock | .poly_pipeline.lock | ❌ |
| WAL | .wal.jsonl | .poly_wal.jsonl | ❌ |
| 設定 | config/settings.py | mm/constants.py | ❌ |
| Code import | 零 | 零 | ❌ |

### 唯一交叉點（低風險）

`polymarket/strategy/crypto_15m.py` **只讀** trader_cycle 嘅 3 個文件：
- `shared/SCAN_CONFIG.md`（BTC 價格/ATR）
- `shared/TRADE_STATE.json`（持倉）
- `shared/news_sentiment.json`（情緒）

有 `try/except` 保護，讀唔到就跳過，唔會 crash。

---

## 4. Trade Flow（追蹤線路圖）

### polymarket/mm/

```
validate → data_feeds → signal_pipeline → risk_guards →
entry_logic → order_lifecycle → state_io → ... →
exit_logic → state_io
```

依賴層級（無循環 ✅）：
```
Level 0: constants.py（地基）
Level 1: data_feeds / state_io / risk_guards / validate
Level 2: signal_pipeline / order_lifecycle
Level 3: entry_logic / exit_logic
```

### trader_cycle 16-Step Pipeline

```
手術前：  read_state → replay_wal → safety_check
診斷：    fetch_market → indicators → sentiment → liq → vol
判斷：    detect_mode → risk_profile → no_trade → sync → manage → adjust
新交易：  evaluate → filter → select → size → validate → 🔴 EXECUTE
收尾：    write_state → log → journal → memory → telegram
```

---

## 5. 設計決定

| 決定 | 點解 |
|------|------|
| AI 分三級（Haiku/Sonnet/Opus） | 慳錢 — 簡單嘢唔需要用貴嘅 AI |
| 兩套系統完全隔離 | 改一邊唔會影響另一邊，降低 blast radius |
| shared/ 用 file-based IPC | 簡單、可 debug、crash recovery 容易（WAL） |
| atomic write（tempfile + os.replace） | 防 crash 寫到一半 = 損壞文件 |
| fcntl.flock 防並發 | scanner 同 tradercycle 唔可以同時寫 |

---

## 6. 學到嘅技能（教學用）

| 技能 | 做咩 | 點用 |
|------|------|------|
| **Trace** | 追蹤一個 trade 由頭到尾經過邊啲文件 | Debug 時跟住條線搵 bug |
| **Boundary Detection** | 搵系統之間嘅邊界：邊度連住、邊度獨立 | 改 code 前知道 blast radius |

> 「如果呢度出 bug，我跟住條線，就知道問題可能喺邊。」
> 好似偵探查案 — 最長嗰個文件 = 最大嫌疑人。

---

## 7. Agent 比喻表

| Agent | AI 級別 | 比喻 | 點解 |
|-------|---------|------|------|
| haiku_filter | Haiku（平） | 分信員 | 快手揀出重要信件 |
| analyst | Sonnet（中） | 偵探 | 將線索拼成故事 |
| decision | Opus（貴） | 法官 | 一錘定音 |
| binance_scanner | 純 Python | 雷達 | 不停轉圈偵測 |
| heartbeat | 純 Python | 校醫 | 定期幫大家量體溫 |
| news_agent | — | 報紙佬 | 每日送報 |
