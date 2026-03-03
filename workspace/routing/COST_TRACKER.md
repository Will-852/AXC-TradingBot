# COST_TRACKER.md — 每日 API 成本追蹤
# 版本: 2026-03-02（100% Python 版）
# 每日 UTC 00:00 自動重設
# 注意: 所有 services 已改用 Python，零 LLM 自動消耗

## 今日成本（2026-03-02）

DATE: 2026-03-02
DAILY_TOTAL: $0.00
DAILY_LIMIT_SOFT: $0.50
DAILY_LIMIT_HARD: $1.00

## 成本預算（每日）

```
light-scan:     Python script（唔用 LLM）              = $0.00
trader-cycle:   Python script（唔用 LLM）              = $0.00
heartbeat:      Python script（唔用 LLM）              = $0.00
預計總計:       $0.00/日
```

## Sub-task 明細

| 時間 | Sub-task | 方式 | 成本 |
|------|----------|------|------|
| — | 全部 Python services | launchd | $0.00/日 |
| 按需 | news-analysis (手動) | TIER_2 Haiku | ~$0.01/次 |

## 歷史紀錄

| 日期 | 總成本 | 執行次數 | 備注 |
|------|--------|----------|------|
| 2026-03-02 | $0.00 | — | 100% Python: heartbeat 亦改用 Python |
| 2026-03-01 | — | — | Session 中斷 |
| 2026-02-28 | ~$0.92 | — | trader-cycle 用 Haiku |

## 規則

- 所有自動 service 用 Python（$0.00/日）
- LLM 只用於手動觸發（news-analysis 等）
- 軟性熔斷 $0.50 → Telegram 警告（超過代表有異常 LLM 調用）
- 硬性熔斷 $1.00 → 暫停所有 LLM 服務
- 每日 UTC 00:00 重設 DAILY_TOTAL，移入歷史紀錄
