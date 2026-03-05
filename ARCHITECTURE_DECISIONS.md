# OpenClaw AI Stack 架構決策
# 記錄日期：2026-03-05
# 狀態：已確認，長期有效

---

## 核心技術選型

| 層次 | 選擇 | 原因 |
|------|------|------|
| 推理層 | Claude API | 質量最高，遠勝本地 LLM |
| 向量層 | voyage-3 | 真正語義理解，免費 200M tokens |
| 搜尋層 | numpy cosine | 夠用至 10 萬條記憶，零依賴 |
| 記憶層 | jsonl + npy | 簡單可靠，人類可讀 |

---

## 明確拒絕

### ❌ 本地 LLM（Llama / Mistral）
- 26GB 磁碟 + 36GB RAM
- 質量 ≈ Claude Haiku 2年前水平
- 速度 2-10秒，唔比 API 快
- 結論：花更多資源換更差結果

### ❌ Faiss 向量資料庫
- 優勢在 1億條以上記憶
- 每日 50條對話，5年 = 9萬條
- numpy cosine 處理 10萬條 < 1秒
- 結論：過度設計，徒增複雜度

---

## 待實現：方案C 策略規則歸納

```python
# 每週執行一次
# scripts/weekly_strategy_review.py

def weekly_strategy_review():
    """
    讀取所有歷史交易記憶
    → Claude Sonnet 分析模式
    → 歸納個人交易規則
    → 輸出 STRATEGY_RULES.md
    → 下次 /ask 自動納入 context
    """
```

輸出格式（STRATEGY_RULES.md）：
```
## 我的成功入場模式
  1. ...

## 我的失敗模式
  1. ...

## 個人規則（具體條件）
  1. ...

最後更新：YYYY-MM-DD
```

---

## 為何唔用 sentence-transformers

```
sentence-transformers 係本地 embedding
voyage-3 係雲端 API embedding

voyage-3 優勢：
  - 語義理解更強（專門訓練）
  - 免費額度大（200M tokens）
  - 唔需要本地 GPU/CPU 資源
  - 自動更新模型
```

---

*呢份文件係 OpenClaw 架構決策記錄，供日後參考。*
*唔好因為「新方案」而偏離呢個選型。*
