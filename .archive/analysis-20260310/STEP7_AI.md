# Step 7: `ai/` — 策略文檔（AI 嘅大腦記憶卡）
> talk12 風格分析 | 2026-03-10

## 點樣搵到
```
axc-trading → ai/
├── CONTEXT.md    ← 系統完整描述（AI 嘅「我係邊個」）
├── STRATEGY.md   ← 交易策略 + 每週自動回顧
├── RULES.md      ← 行為規則（AI 嘅「做人原則」）
└── MEMORY.md     ← 近期狀態快照（自動更新）
```

**4 個文件，全部係 AI Agent 讀嘅。** 人類文件喺 `docs/`。

---

## 比喻

想像你每朝醒返嚟乜都唔記得（失憶症）。呢 4 個文件就係你瞓覺前寫嘅紙條：
- CONTEXT.md = 「我係邊個、住喺邊、屋企有咩設備」
- STRATEGY.md = 「我做開咩工作、表現點」
- RULES.md = 「我嘅做人原則」
- MEMORY.md = 「尋晚發生咗咩事」

AI Agent 每次啟動都係全新嘅。靠呢啲文件延續記憶。

---

## 1. `CONTEXT.md` — 系統地圖（4.4KB）

### 內容
- 系統概覽：9 agents + dashboard + Telegram bot
- 核心路徑：`~/projects/axc-trading/` 嘅目錄結構
- 十個 Agents 列表 + 各自角色
- Signal Pipeline 流程
- LaunchAgents 服務列表
- 關鍵 Scripts 清單
- Gotchas（已知坑位）

### 十個 Agents

| Agent | Model | 角色 | 比喻 |
|-------|-------|------|------|
| main | tier3/haiku | 大腦：決策、對話、路由 | 老闆 |
| aster_scanner | tier2/haiku | Aster 掃描 | 偵探 A |
| aster_trader | tier1/sonnet | Aster 交易 | 交易員 A |
| binance_scanner | — | Binance 掃描 | 偵探 B |
| binance_trader | — | Binance 交易 | 交易員 B |
| heartbeat | tier3/haiku | 健康檢查 | 護士 |
| haiku_filter | tier2/haiku | 信號壓縮 | 秘書 |
| analyst | tier1/sonnet | 模式偵測 | 分析師 |
| decision | opus | 最終決策 | 顧問 |
| news_agent | tier2/haiku | 新聞情緒 | 記者 |

### Signal Pipeline（信號流水線）
```
aster_scanner  ─┐
binance_scanner─┤→ haiku_filter → analyst → decision → trader
news_agent     ─┘ (sentiment overlay)
```

### Gotchas（已知坑位）
- 改參數只改 `config/params.py`
- tier2 Haiku 處理唔到 >10K system prompt
- Skill description 空白 = 靜默失敗
- `fcntl.flock` 防 scanner 同 trader_cycle 同時執行

---

## 2. `STRATEGY.md` — 每週策略回顧（4.2KB）

### 由 `weekly_strategy_review.py` 自動生成
每週一 10:00 HKT 由 LaunchAgent 觸發，讀 `trades.jsonl` 生成報告。

### 當前狀態（2026-03-09）

**交易風格摘要：**
- 激進探索階段：9 筆記錄，多數未完成
- 多品種分散：BTC、ETH、XRP、XAG
- 全自動化傾向
- 頻繁調參

**統計：**
| 指標 | 數值 |
|------|------|
| 總交易數 | 9（含失敗） |
| 已平倉 | 2（數據不完整） |
| Win Rate | N/A |
| Total PnL | $0.00 |
| 入場成功率 | 78%（7/9） |

**核心問題：**
1. 平倉數據缺失（entry/exit 價格 = $0）
2. 多個未平倉無追蹤
3. 過度理論化，實際交易極少

**建議：**
- 修復數據記錄系統
- 至少累積 20 筆完整交易
- 先用單一品種測試 2-3 週

⚠️ 呢份報告反映嘅係 **系統剛上線階段**（3月4-9日）。之後嘅 trader_cycle live 交易（例如你而家嘅 BTC SHORT）數據需要時間累積。

---

## 3. `RULES.md` — 行為規則（1.3KB）

### Core Truths
1. **真正有用** > 表面有用。跳過廢話。
2. **有自己嘅意見**。冇性格嘅助手只係搜索引擎。
3. **先搵再問**。讀文件、查上下文、搜索。搵唔到先問。
4. **用能力贏信任**。外部操作要小心，內部操作可以大膽。
5. **你係客人**。尊重 access 權限。

### Boundaries（邊界）
- 私隱嘢唔講。
- 唔肯定就問。
- 唔好發半成品訊息去 messaging。
- 你唔係用戶嘅聲音 — group chat 要小心。

### Telegram 格式
- **唔好用 Markdown**（**、*、##、```）
- 要強調用 `<b>粗體</b>`
- 回覆 2-8 行，簡短直接
- 語氣：香港交易員口語廣東話

---

## 4. `MEMORY.md` — 狀態快照（2.0KB）

### 自動更新
由 `backup_agent.sh` 每日 03:00 觸發。

### 當前內容（2026-03-10 03:00）
- 運行服務：scanner + telegram + gateway
- 近期重要決定（R1-R5 根源修復、架構決策）
- 已知待處理：memory RAG 系統、VOYAGE_API_KEY rotate
- 已知 Bug：`tp_atr_mult` 覆蓋 `MIN_RR`

---

## 數據流

```
backup_agent.sh (每日 03:00)
    └──→ 更新 ai/MEMORY.md

weekly_strategy_review.py (每週一 10:00)
    └──→ 讀 trades.jsonl → 更新 ai/STRATEGY.md

AI Agent 啟動
    └──→ 讀 ai/CONTEXT.md → MEMORY.md → RULES.md → STRATEGY.md
    └──→ 需要細節 → 讀 docs/ 下嘅具體文件
```

---

## ⚠️ 分析中觀察到嘅特點

### 🟡 CONTEXT.md 過時
- 寫住 `async_scanner.py v5`，實際已經係 v7（9 路輪轉版）
- Signal Pipeline 圖仲係舊版（冇 trader_cycle pipeline）
- 最後更新：2026-03-06（4 日前）

### 🟡 STRATEGY.md 數據極少
- 只有 9 筆記錄（多數不完整）
- 反映系統剛上線。隨住 trader_cycle 累積交易，呢個會改善。
- `weekly_strategy_review.py` 自動更新，唔使手動。

### 🟢 RULES.md 簡潔有效
- 清楚定義 Telegram 格式規範
- 核心原則合理（先搵再問、有意見、尊重權限）

### 🟢 架構設計：引用唔複製
- `ai/` 只引用 `docs/`，唔複製內容
- 避免多個版本 → 唯一真相喺 `docs/`

---

## 自檢問題

1. **CONTEXT.md 幾時更新？** → 手動更新。而家過時。建議 backup_agent.sh 加入自動更新。
2. **STRATEGY.md 可靠嗎？** → 而家唔可靠（數據不足）。20+ 筆交易後先有意義。
3. **RULES.md 夠唔夠？** → 夠用。Telegram 格式規範特別重要（唔好用 Markdown）。
4. **MEMORY.md 自動更新？** → 係，每日 03:00 by backup_agent.sh。
