<!--
title: 10 個 Agents
section: 機械體架構
order: 11
audience: human,claude,github
-->

# 10 個 Agents

| Agent | 職責 | 模型 |
|-------|------|------|
| main | 主控，協調所有 agent | Sonnet |
| aster_scanner | Aster DEX 掃描 | Haiku |
| aster_trader | Aster DEX 下單 | Sonnet |
| binance_scanner | Binance 掃描 | Haiku |
| binance_trader | Binance 下單 | Sonnet |
| analyst | 技術分析 | Sonnet |
| decision | 入場決策 + 風控 | Sonnet |
| heartbeat | 系統健康監察 | — |
| haiku_filter | 快速預篩信號 | Haiku |
| news_agent | 新聞 + 情緒分析 | Haiku |

每個 Agent 都有自己嘅 `SOUL.md`（性格設定），位於 `agents/<name>/SOUL.md`。

## 推理模型

| Tier | 模型 | 用途 |
|------|------|------|
| tier1 | claude-sonnet-4-6 | 決策 + 交易 |
| tier2 | claude-haiku-4-5 | 掃描 + Telegram |
| tier3 | gpt-5-mini | 報告 / 默認 |

API proxy: `tao.plus7.plus/v1`
