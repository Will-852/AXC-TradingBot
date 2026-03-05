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

## 待實現

### 方案C：weekly_strategy_review.py
每週自動分析歷史交易 -> Claude Sonnet 歸納個人規則 -> STRATEGY_RULES.md

*唔好因為「新方案」而偏離呢個選型。*
