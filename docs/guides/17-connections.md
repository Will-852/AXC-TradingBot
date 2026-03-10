<!--
title: AXC / OpenClaw / Telegram 點樣連動？
section: 機械體架構
order: 17
audience: human,github
-->

# AXC / OpenClaw / Telegram 點樣連動？

> 好多人第一個問題：「Telegram 係唔係用嚟自動交易？」
> 答案：**唔係**。自動交易唔經 Telegram，佢自己跑。

---

## 三樣嘢，三個角色

想像你開咗間餐廳：

| 角色 | 對應 | 做咩 |
|------|------|------|
| **廚房** | AXC（交易系統） | 煮嘢食 — 睇市場、計策略、落單、管風險 |
| **電話** | Telegram（通訊工具） | 你打嚟問「今日有咩食？」或者「幫我加個菜」 |
| **經理** | OpenClaw（平台） | 安排邊個廚師負責邊個崗位、管理排班 |

重點：**廚房唔需要電話就可以煮嘢食**。電話只係方便你遙控同收通知。

---

## 邊啲功能需要邊樣？

```
功能                        AXC    Telegram   OpenClaw   AI API Key
─────────────────────────────────────────────────────────────────────
自動掃描市場                  ✅       ❌         ❌         ❌
自動交易（入場 + SL/TP）      ✅       ❌         ❌         ❌
Dashboard 睇數據              ✅       ❌         ❌         ❌
手機 Telegram 收交易通知      ✅       ✅         ❌         ❌
手機 Telegram 手動下單        ✅       ✅         ❌         ✅
Telegram AI 對話分析          ✅       ✅         ❌         ✅
新聞情緒分析                  ✅       ❌         ❌         ✅
每週策略回顧                  ✅       ❌         ❌         ✅
OpenClaw Agent sessions      ✅       ❌         ✅         ✅
```

**結論**：只裝 AXC + 連接交易所 = 完整自動交易系統。其他全部係加分。

---

## 冇 Telegram 會點？

**乜都唔影響**：
- 自動交易照跑（每 30 分鐘 trader_cycle）
- 掃描照跑（每 3 分鐘 light_scan）
- Dashboard 照睇（http://localhost:5555）
- 風控照生效（SL/TP/每日限額）

**少咗咩**：
- 你唔會收到「開倉/平倉」通知（要自己上 Dashboard 睇）
- 唔可以用手機遙控（改模式、手動下單）
- 唔可以用自然語言問 AI 分析

---

## 有 Telegram 但冇 AI API Key 會點？

可以用嘅：
- `/pos`、`/bal`、`/report`、`/pnl` — 查持倉、餘額（零 AI 成本）
- `/mode`、`/pause`、`/resume` — 切模式、暫停交易（零 AI 成本）
- 收到開倉/平倉通知（零 AI 成本）

用唔到嘅：
- `/ask BTC 走勢？` — 需要 AI 回答
- 自然語言下單（「做多 ETH $5 10倍」）— 需要 AI 理解你講咩
- 平倉後嘅 AI 教練分析

---

## 信息點樣流？

### 路線 1：自動交易（唔經 Telegram）

```
你嘅 Mac（每 30 分鐘自動跑）
    │
    ▼
trader_cycle 16 步 pipeline
    │
    ├── 掃描 7 個交易對
    ├── 計算指標（BB、ATR、S/R）
    ├── 判斷市場模式（Range / Trend）
    ├── 策略評估（有冇信號？）
    ├── 風控檢查（有冇超限？）
    └── 有信號 → 直接向交易所落單
         │
         ▼
    交易所（Aster / Binance）
         │
         ▼
    （如果有 Telegram）發通知畀你
```

成條路線 **100% 本地 Python**，零 AI 成本。

### 路線 2：Telegram 手動下單

```
你（手機 / 電腦）
    │
    ▼ 「做多 BTC $10 5倍 SL 101000」
Telegram 雲端（中轉站，唔做任何事）
    │
    ▼
tg_bot.py（跑喺你 Mac 上面）
    │
    ├── 🤖 Claude AI 理解你嘅意思
    ├── 彈出確認按鈕（你要撳「確認」）
    └── 你確認後 → 向交易所落單
         │
         ▼
    交易所執行 → 結果傳返 Telegram
```

> **關鍵**：tg_bot.py 跑喺你自己嘅 Mac。Telegram 只係傳話，唔接觸你嘅錢。

### 路線 3：Telegram 查詢（零 AI）

```
你：/pos
    │
    ▼
Telegram → tg_bot.py（你嘅 Mac）
    │
    ├── 直接 call 交易所 API 攞持倉
    └── 格式化 → 傳返 Telegram
```

唔經 AI，直接查。

---

## OpenClaw 嘅角色

OpenClaw 係「經理」，主要管兩樣嘢：

### 1. Agent 框架

AXC 有 10 個 Agent（見指南 11）。其中大部分已經被 trader_cycle 取代，而家活躍嘅只有 3 個：

| Agent | 需要 OpenClaw？ | 點跑 |
|-------|----------------|------|
| main（Telegram 介面） | 需要 | OpenClaw session |
| news_agent（新聞分析） | 唔需要 | 獨立 Python script |
| heartbeat（系統監察） | 唔需要 | 獨立 Python script |

### 2. Gateway（@axccommandbot）

另一個 Telegram bot，用嚟做系統層面嘅嘢（唔係交易）：
- 查看 agent 狀態
- 系統診斷
- 同 @AXCTradingBot（交易 bot）係**兩個獨立嘅 bot**

### 唔裝 OpenClaw 會點？

| 有 OpenClaw | 冇 OpenClaw |
|-------------|-------------|
| @axccommandbot 可用 | @axccommandbot 唔可用 |
| Agent sessions 可跑 | Agent sessions 唔可跑 |
| 自動交易照跑 ✅ | 自動交易照跑 ✅ |
| Dashboard 照跑 ✅ | Dashboard 照跑 ✅ |
| tg_bot.py 照跑 ✅ | tg_bot.py 照跑 ✅ |

**簡單講**：唔裝 OpenClaw，你少咗 @axccommandbot 同 agent sessions。其他全部正常。

---

## 兩個 Telegram Bot 比較

| | @AXCTradingBot | @axccommandbot |
|---|---|---|
| 程式 | tg_bot.py | OpenClaw Gateway |
| 用途 | 交易控制 + 監察 | 系統管理 |
| 需要 OpenClaw？ | 唔需要 | 需要 |
| 需要 AI？ | 部分功能要 | 要 |
| 新用戶需要？ | 推薦 | 可以唔裝 |

---

## 安全問題

**「Telegram 會唔會偷我嘅錢？」**

唔會。原因：
1. Telegram 只係傳訊息，好似 WhatsApp。佢唔知你嘅交易所 API Key
2. tg_bot.py 跑喺你自己嘅 Mac，所有操作喺本地執行
3. 所有下單都要你手動確認（彈出「確認」按鈕，90 秒內要撳）
4. 只接受你嘅 chat ID，陌生人發訊息會被忽略

**「AI 會唔會亂下單？」**

唔會。AI 嘅角色只係：
- 理解你打嘅自然語言（「做多 BTC」→ 轉成具體參數）
- 分析市場數據（你問「BTC 走勢？」）
- 平倉後寫交易報告

AI **唔會**自己決定開倉。自動交易係 trader_cycle（純 Python 規則），唔用 AI。

---

## 新用戶建議路徑

```
第 1 日：裝 AXC + 連交易所 → 自動交易已經跑緊
         ↓
第 2 日：設定 Telegram → 手機收通知 + 遙控
         ↓
之後：   加 AI API Key → 解鎖 AI 對話 + 新聞分析
         ↓
進階：   裝 OpenClaw → 解鎖 Agent sessions + @axccommandbot
```

每一步都係獨立嘅，唔裝後面嘅唔影響前面嘅功能。
