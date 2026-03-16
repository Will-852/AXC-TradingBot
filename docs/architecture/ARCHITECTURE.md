# AI Stack 架構決策
> 記錄日期：2026-03-05 | 長期有效

## 核心技術選型

| 層次 | 選擇 | 原因 |
|------|------|------|
| 推理層 | Claude API | 質量最高，遠勝本地 LLM |
| 向量層 | voyage-3 | 真正語義理解，免費 200M tokens |
| 搜尋層 | numpy cosine | 夠用至 10 萬條記憶，零依賴 |
| 記憶層 | jsonl + npy | 簡單可靠，人類可讀 |

## Model Tiers

| Tier | Model | API | 用途 |
|------|-------|-----|------|
| tier1 | claude-sonnet-4-6 | anthropic-messages | 決策 + 交易 |
| tier2 | claude-haiku-4-5 | anthropic-messages | 掃描 + tg_bot chat |
| tier3 | gpt-5-mini | openai-completions | 日常 / agent default |

All via proxy `https://tao.plus7.plus/v1`

## 明確拒絕

### 本地 LLM（Llama / Mistral）
- 測試於 2026-03-05：Llama2 7B via Ollama + Mistral-7B via MLX
- 佔用 4-14.5GB RAM
- 質量遠低於 Claude Haiku（尤其廣東話、JSON parsing）
- 速度 2-10 秒，唔比 API 快
- 結論：花更多資源換更差結果

### Faiss 向量資料庫
- 優勢在 1 億條以上
- 每日 50 條 x 365 x 20 年 = 365,000 條
- numpy cosine 10 萬條以下 < 1 秒
- 結論：過度設計，徒增複雜度

### sentence-transformers
- 本地 embedding，需要 GPU/CPU 資源
- voyage-3 語義理解更強，免費額度大（200M tokens）
- 結論：唔值得本地跑

## 容量估算

```
每日對話 50 條 x 365 x 20 年 = 365,000 條
numpy cosine 10 萬條以下 < 1 秒
voyage-3 免費額度 = 約 40 萬條
= 20 年唔需要升級
```

## 已實現

### 方案C：weekly_strategy_review.py ✅
每週自動分析歷史交易 → Claude Sonnet 歸納個人規則 → ai/STRATEGY.md
排程：每週一 10:00 HKT via LaunchAgent

*唔好因為「新方案」而偏離呢個選型。*

---

## 系統架構圖 System Architecture Diagrams
> 更新：2026-03-16 | 中英雙語

所有圖存放於 `docs/architecture/`，可用瀏覽器直接開 SVG。

| # | 檔案 | 類型 | 內容 |
|---|------|------|------|
| 1 | `1-system-overview.svg` | Draw.io | 全系統鳥瞰 — 7 大區塊：數據入口、狀態層、交易引擎、交易所、輸出、AI、回測 |
| 1 | `1-system-overview.drawio` | Draw.io 原始檔 | 可用 [draw.io](https://app.diagrams.net) 開啟再編輯 |
| 2 | `2-pipeline-16steps.svg` | Mermaid | 交易引擎 16 步流程 + 決策分支（SafetyCheck → Execute → Report） |
| 3 | `3-mindmap.svg` | Mermaid | 概念樹 — 一眼睇晒數據/信號/執行/AI/回測/狀態 |
| 4 | `4-sequence-diagram.svg` | Mermaid | 時序圖 — 一個 30min cycle 內各模組互動順序 |
| 5 | `5-strategy-flow.svg` | Mermaid | 策略層 — 6票投票偵測 → 3 大策略入場邏輯 → 倉位計算 |

### 顏色標準
| 顏色 | 區塊 |
|------|------|
| 藍 `#dae8fc` | 數據 Data |
| 綠 `#d5e8d4` | 引擎 Engine |
| 橙 `#ffe6cc` | 交易所 Exchange |
| 紫 `#e1d5e7` | 輸出 Output |
| 灰 `#f5f5f5` | 狀態 State |
| 紅 `#f8cecc` | 風控/執行 Risk/Exec |
| 淺藍 `#b3cde3` | AI 層 |
| 白底灰邊 | 回測 Backtest |

### 更新指引
- Mermaid 圖：叫 Claude 重新 `mermaid_preview` → `mermaid_save` 覆蓋 SVG
- Draw.io 圖：用 draw.io 編輯 `.drawio` → Export 覆蓋 SVG，**同時保留 .drawio 原始檔**
- 架構有重大改動時更新圖表，小改動唔需要
