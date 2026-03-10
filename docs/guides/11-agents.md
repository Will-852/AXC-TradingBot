<!--
title: 10 個 Agents
section: 機械體架構
order: 11
audience: human,claude,github
-->

# 10 個 Agents

⚠️ trader_cycle 16 步 pipeline 已取代原始 Agent Pipeline 做交易決策。以下標注實際狀態。

| Agent | 職責 | 模型 | 實際狀態 |
|-------|------|------|----------|
| main | Telegram 介面 + 指令路由 | Haiku | 活躍 |
| aster_scanner | Aster DEX 掃描 | Python | 被 async_scanner 取代 |
| aster_trader | Aster DEX 下單 | Python | 被 trader_cycle 取代 |
| binance_scanner | Binance 掃描 | — | 整合入 async_scanner |
| binance_trader | Binance 下單 | — | 整合入 trader_cycle |
| heartbeat | 系統健康監察 | Python | 活躍 |
| haiku_filter | 快速預篩信號 | Haiku | 原始設計，被 trader_cycle 取代 |
| analyst | 技術分析 | Sonnet | 原始設計，被 trader_cycle 取代 |
| decision | 入場決策 + 風控 | Opus | 原始設計，被 trader_cycle 取代 |
| news_agent | 新聞 + 情緒分析 | Haiku | 活躍 |

每個 Agent 都有自己嘅 `SOUL.md`（性格設定），位於 `agents/<name>/SOUL.md`。

## Model 成本分級

| Tier | 模型 | 用途 |
|------|------|------|
| Opus | claude-opus-4-6 | decision agent（原始設計） |
| Sonnet | claude-sonnet-4-6 | analyst agent（原始設計） |
| Haiku | claude-haiku-4-5 | main, haiku_filter, news（高頻互動） |
| Python | — | scanner, trader_cycle, heartbeat（確定性 + 零 AI cost） |

API proxy: `tao.plus7.plus/v1`
