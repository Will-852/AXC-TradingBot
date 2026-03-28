# Step 8: `agents/` — OpenClaw Agents（員工團隊）
> talk12 風格分析 | 2026-03-10

## 點樣搵到
```
axc-trading → agents/
├── main/               ← 老闆（最複雜，73 個 session，130 個文件）⭐⭐
│   ├── workspace/
│   │   ├── SOUL.md         ← 性格 + 系統架構
│   │   ├── core/
│   │   │   ├── SOUL.md     ← TG 指令路由
│   │   │   ├── STRATEGY.md
│   │   │   └── RISK_PROTOCOL.md
│   │   ├── memory/         ← MEMORY.md, EMOTION_BIN.md, 月份存檔
│   │   ├── skills/         ← Telegram skills（new2, slash-commands, system-memory）
│   │   ├── docs/           ← 內部文檔
│   │   └── shared/         ← TRADE_STATE.md（agent 自己嘅副本）
│   ├── sessions/           ← 73 個對話記錄 (.jsonl)
│   └── agent/models.json
│
├── aster_scanner/      ← Aster 偵探
│   └── workspace/SOUL.md + skills/
├── aster_trader/       ← Aster 交易員
│   └── workspace/SOUL.md
├── binance_scanner/    ← Binance 偵探
│   └── SOUL.md
├── binance_trader/     ← Binance 交易員
│   └── SOUL.md
├── heartbeat/          ← 護士
│   └── workspace/SOUL.md
├── haiku_filter/       ← 秘書
│   └── SOUL.md
├── analyst/            ← 分析師
│   └── SOUL.md
├── decision/           ← 顧問
│   └── SOUL.md
└── news_agent/         ← 記者
    └── workspace/SOUL.md
```

**10 個 Agent，130 個文件。** 每個 agent 嘅核心係 `SOUL.md` = 性格設定。

---

## 比喻

想像一間公司有 10 個員工。每個人有自己嘅 job description（SOUL.md）。

老闆（main）最忙，有 73 次會議記錄（sessions）。
其他員工比較單一 — 做好自己嗰份就夠。

---

## 信號流水線（誰做咩）

```
偵探 A/B           秘書            分析師         顧問           交易員
(scanner)   →   (haiku_filter)  →  (analyst)  →  (decision)  →  (trader)
  │                  │                │              │              │
  │ 收集原始         │ 壓縮到          │ 加市場        │ 模擬 3 種     │ 7 步
  │ 價格數據         │ <300 字         │ 上下文        │ 情景 → GO/   │ 落單
  │                  │                │              │ HOLD/ABORT   │ 序列
  │                  │                │              │              │
  │ 零 LLM cost     │ tier2 Haiku    │ tier1 Sonnet │ Opus         │ 零 LLM
  │ (純 Python)     │ (平)           │ (中)         │ (貴)         │ (純 Python)

       ↑                                                    ↑
   news_agent                                          heartbeat
   (記者，tier2)                                       (護士，純 Python)
```

---

## 10 個 Agent 詳細

### 1. main — 老闆 ⭐⭐

**角色：** Telegram 介面 + 指令路由 + 同你溝通。

**性格：**
- 直接、有態度、香港交易員口語廣東話
- 唔好企業腔、唔好拍馬屁
- 先搵再問，有意見

**核心規則：**
- 收到 `/` 指令 → 即刻執行 `python3 slash_cmd.py [cmd] --send`（唔問、唔讀文件）
- Telegram 回覆最多 25 行，用 `<pre>` 格式
- 絕對唔好用 Markdown（**、*、```）
- URGENT 訊息即刻發，唔等

**Workspace：** 73 個 session 記錄 + 3 個 skills + 記憶系統 + 情緒回收站

**Model：** Claude Haiku（tier2）— 因為 TG 互動量大，用平價模型

---

### 2. aster_scanner — Aster 偵探

**角色：** Aster DEX 市場掃描（純 Python，零 AI cost）。

**實際執行：** `light_scan.py`（3 分鐘輕量掃描）+ `trader_cycle/main.py`（完整 pipeline）

**觸發門檻：**
- 價格變動 > 0.6%
- 成交量 > 175% baseline
- Funding delta > 0.18%

---

### 3. aster_trader — Aster 交易員

**角色：** 紀律性執行 Aster DEX 交易。

**核心哲學：** "歷史重演 — 模式重複嘅係節奏，唔係觸發器"

**原則：**
```
紀律 > 直覺
保本 > 獲利
數據 > 情緒
確認 > 速度
```

**實際執行：** `trader_cycle/main.py` → `execute_trade.py` 7 步落單

**Model：** 無（純 Python）

---

### 4-5. binance_scanner + binance_trader

同 Aster 版本一樣嘅 interface，只係連接 Binance Futures。

- Scanner 已整合入 `async_scanner.py`（9 路輪轉）
- Trader 用同一個 `trader_cycle/main.py`，透過 `signal.platform` 路由
- Env vars: `BINANCE_API_KEY`, `BINANCE_API_SECRET`

---

### 6. haiku_filter — 秘書

**角色：** 將原始信號壓縮到 <300 字嘅結構化摘要。

**比喻：** 你老闆日理萬機，秘書將 100 頁報告壓縮成 1 頁 summary。

**輸出格式：**
```
PLATFORM: aster/binance
TIMESTAMP: ...
SIGNALS: [list]
KEY_INDICATORS: [list]
ANOMALIES: [list]
CONFIDENCE: 0-100
SUMMARY: <300 words
```

**關鍵規則：** 永遠唔做交易決策，只壓縮同過濾。

**Model：** Claude Haiku（tier2）— 平、快

---

### 7. analyst — 分析師

**角色：** 將 haiku_filter 嘅摘要加上市場上下文。

**關鍵規則：**
- 輸入：只接受 haiku_filter 嘅 output（最多 300 字）
- 一定要列出矛盾信號（唔好只講好嘅）
- 有異常 → 升級 HALT 建議

**Model：** Claude Sonnet（tier1）— 需要深度分析

---

### 8. decision — 顧問

**角色：** 最終交易決策 — GO / HOLD / ABORT。

**比喻：** 法官。聽完所有證詞（analyst 報告），做最終判決。

**流程：**
1. 讀 analyst output（最多 400 字）
2. 查 ACTIVE_PROFILE（CONSERVATIVE/BALANCED/AGGRESSIVE）
3. 模擬 3 種情景：最好、基本、最差
4. Confidence < 60% → ABORT
5. 有異常 → ABORT
6. 輸出：GO_LONG / GO_SHORT / HOLD / ABORT

**核心原則：** "有懷疑就 HOLD — 保本第一"

**Model：** Claude Opus — 最貴但最重要嘅決策用

---

### 9. heartbeat — 護士

**角色：** 每 15 分鐘巡邏系統健康。

**檢查項目：**
| 級別 | 條件 | 動作 |
|------|------|------|
| URGENT | 有倉但冇 SL | 發 TG |
| URGENT | 有倉但 SL 未確認 | 發 TG |
| WARNING | TP 未確認 | 發 TG |
| WARNING | 觸發 >25 分鐘未處理 | 發 TG |
| WARNING | 日 API cost > $0.50 | 發 TG |

靜音：23:00-08:00 只發 URGENT。

**Model：** 無（純 Python）

---

### 10. news_agent — 記者

**角色：** RSS 新聞收集 + 情緒分析。

**兩部分：**
| 部分 | 工具 | 成本 |
|------|------|------|
| news_scraper.py | 純 Python（RSS parsing） | 零 |
| news_sentiment.py | Claude Haiku | ~$3-4/月 |

**輸出：** `shared/news_sentiment.json` — 被 `evaluate.py` 讀取做 sentiment filter。

---

## Model 成本分級

```
                  ┌─────────────┐
    最貴          │   Opus      │  decision（最終決策）
                  └──────┬──────┘
                  ┌──────┴──────┐
    中等          │   Sonnet    │  analyst（深度分析）
                  └──────┬──────┘
                  ┌──────┴──────┐
    平價          │   Haiku     │  main, haiku_filter, news_sentiment
                  └──────┬──────┘
                  ┌──────┴──────┐
    免費          │   Python    │  scanner, trader, heartbeat, news_scraper
                  └─────────────┘

設計原則：越接近「做決策」→ 用越貴嘅模型
         越接近「執行」→ 用純 Python（確定性 + 零成本）
```

---

## main Agent 嘅 Workspace 結構

Main 係最複雜嘅 agent，有自己嘅「辦公室」：

```
main/workspace/
├── core/
│   ├── SOUL.md           ← TG 指令路由規則
│   ├── STRATEGY.md       ← 交易策略（同 ai/STRATEGY.md 唔同版本）
│   └── RISK_PROTOCOL.md  ← 風控規則
├── memory/
│   ├── MEMORY.md         ← 短期記憶
│   ├── EMOTION_BIN.md    ← 情緒回收站（有趣）
│   └── MEMORY_ARCHIVE_2026-03.md ← 月份存檔
├── skills/
│   ├── new2/SKILL.md
│   ├── slash-commands/SKILL.md
│   └── system-memory/SKILL.md
├── docs/                 ← 內部文檔（5 個）
├── shared/TRADE_STATE.md
└── SOUL.md + IDENTITY.md + HEARTBEAT.md + USER.md + ...

sessions/                 ← 73 個對話 (.jsonl)
agent/models.json
```

---

## ⚠️ 分析中觀察到嘅特點

### 🟡 Agent Pipeline vs Trader Cycle Pipeline
SOUL.md 描述嘅 Signal Pipeline（scanner → haiku_filter → analyst → decision → trader）
同實際 `trader_cycle/main.py` 嘅 16-step pipeline 有分歧。

實際上 trader_cycle 唔經過 haiku_filter/analyst/decision — 佢自己有完整嘅策略評估（mode_detector + range/trend strategy + evaluate）。

呢啲 SOUL.md 更似係 **原始設計願景**，trader_cycle 係之後建嘅 **獨立系統**。

### 🟡 aster_scanner SOUL 寫 4 pairs
同 light_scan.py 一樣，只列 BTC/ETH/XRP/XAG。缺 SOL+XAU。

### 🟢 成本分級合理
決策用最貴（Opus），執行用免費（Python）。高頻互動用平價（Haiku）。

### 🟢 main agent 有 73 個 session
代表佢有豐富嘅對話歷史，可以用嚟訓練/改進。

---

## 自檢問題

1. **trader_cycle 同 agent pipeline 嘅關係？** → 兩套並行。trader_cycle 係獨立嘅自動交易系統，agent pipeline 係透過 OpenClaw gateway 嘅 AI 協調系統。
2. **邊啲 agent 而家真正跑緊？** → main（tg_bot）、scanner（async_scanner）、heartbeat、news_agent。其他 agent 嘅角色已被 trader_cycle 取代。
3. **SOUL.md 過時？** → 部分係。例如 aster_scanner SOUL 描述同實際 async_scanner v7 有差異。
4. **73 個 session 有用嗎？** → 有。代表同 main agent 嘅所有 Telegram 對話。如果要改善 AI 回覆質量，呢啲係重要數據。
