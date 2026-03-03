# NEWS_PROTOCOL.md — 人手觸發新聞分析協議
# 版本: 2026-03-02
# 規則: 新聞係人手提供，唔自動爬取，結果唔儲存

## 觸發方式

用戶發送帶 `NEWS:` 前綴的訊息即觸發：
```
NEWS: [新聞內容]
```

## 執行模型

- 分析：TIER_2（haiku45）
- 如需落盤：TIER_1（sonnet）

## 分析流程

### Step 1 — News Score（0-3）

| 分數 | 類型 |
|------|------|
| 3 | Fed 決定 / 戰爭 / 交易所爆雷 / ETF 裁決 / 清算 >$100M |
| 2 | 監管動態 / 大型合作 / 鯨魚移動 |
| 1 | 一般市場評論 |
| 0 | 無關或噪音 |

### Step 2 — Technical Score（0-5）

逐項確認，每項 +1：

```
RSI trending with news direction: +1
MACD aligning with news direction: +1
MA position confirms bias: +1
Volume >50% of 30d average: +1
Price near S/R zone: +1
```

### Step 3 — 決策

| 總分 | 行動 |
|------|------|
| 6-8 | 立即執行 → TIER_1 落盤 |
| 4-5 | 等下一個 trader-cycle 確認 |
| 0-3 | 記錄（僅 Telegram 通知），不行動 |

## 輸出格式（繁體中文 Telegram）

```
📰 [YYYY-MM-DD HH:MM UTC+8] NEWS 分析
━━━━━━━━━━━━━━
新聞：[內容摘要]
新聞評分：[X]/3 — [類型]
技術評分：[X]/5
  RSI: [✅/❌] | MACD: [✅/❌] | MA: [✅/❌]
  Volume: [✅/❌] | S/R: [✅/❌]
總分：[X]/8
━━━━━━━━━━━━━━
決策：[執行/等待/忽略] — [原因]
```

## 重要規則

- 結果唔儲存到任何 MD 檔案
- 唔主動爬取新聞，只分析用戶提供的內容
- 分析結果只發 Telegram，唔寫入 TRADE_LOG（除非實際落盤）
- 1小時內主要新聞 → NO-TRADE 條件激活
