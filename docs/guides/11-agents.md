<!--
title: 10 個 Agents + OpenClaw
section: 機械體架構
order: 11
audience: human,claude,github
-->

# 10 個 Agents + OpenClaw

## 咩係 Agent？咩係 OpenClaw？

**Agent** = 一個有特定職責嘅程式。好似公司入面唔同部門嘅員工。

**OpenClaw** = 管理呢啲 agent 嘅平台。好似 HR 系統 — 負責：
- 分配 AI model 畀每個 agent
- 提供 Telegram 橋接（Gateway）
- 管理 API key + 通訊頻道

**AXC** = 你嘅交易系統。用 OpenClaw 平台跑，但核心交易邏輯係自己嘅。

## 用唔用 OpenClaw？

| 模式 | 你需要 | 有咩功能 |
|------|--------|---------|
| **用 OpenClaw** | 安裝 `openclaw` CLI + `~/.openclaw/openclaw.json` | Telegram Gateway + Agent sessions + AI 對話 |
| **唔用 OpenClaw** | 只跑 Python scripts | 自動交易 + Dashboard + 掃描，冇 Telegram AI 對話 |

大部分交易功能唔需要 OpenClaw。如果你只想自動交易 + 睇 Dashboard，直接跑 Python scripts 就夠。

## 10 個 Agent 一覽

⚠️ trader_cycle 16 步 pipeline 已取代原始 Agent Pipeline 做交易決策。

| Agent | 做咩 | 點跑 | LLM？ | 而家狀態 |
|-------|------|------|-------|---------|
| main | Telegram 介面、指令路由 | OpenClaw session | 🤖 Haiku | 活躍 |
| news_agent | 新聞情緒分析 | Python script | 🤖 Haiku | 活躍 |
| heartbeat | 系統健康監察 | Python script | ❌ 純 Python | 活躍 |
| aster_scanner | Aster DEX 掃描 | Python script | ❌ 純 Python | 被 async_scanner 取代 |
| aster_trader | Aster DEX 下單 | Python script | ❌ 純 Python | 被 trader_cycle 取代 |
| binance_scanner | Binance 掃描 | — | — | 整合入 async_scanner |
| binance_trader | Binance 下單 | — | — | 整合入 trader_cycle |
| haiku_filter | 快速預篩信號 | OpenClaw session | 🤖 Haiku | 原始設計（唔用） |
| analyst | 技術分析 | OpenClaw session | 🤖 Sonnet | 原始設計（唔用） |
| decision | 入場決策 + 風控 | OpenClaw session | 🤖 Opus | 原始設計（唔用） |

**活躍嘅 3 個**：main（Telegram）、news_agent、heartbeat
**核心交易**：全部由 trader_cycle（純 Python，零 AI cost）處理
**原始設計嘅 4 個**（haiku_filter / analyst / decision / binance）：SOUL.md 保留做參考，但功能已被 trader_cycle 取代

## OpenClaw 設定

如果你用 OpenClaw，設定文件在 `~/.openclaw/openclaw.json`。

### Agent 模型設定

```json
{
  "models": {
    "providers": {
      "tier1": { "model": "claude-sonnet-4-6", "baseUrl": "你的proxy/v1", "apiKey": "sk-xxx" },
      "tier2": { "model": "claude-haiku-4-5", "baseUrl": "你的proxy/v1", "apiKey": "sk-xxx" },
      "tier3": { "model": "gpt-5-mini", "baseUrl": "你的proxy/v1", "apiKey": "sk-xxx" }
    }
  }
}
```

### 改 AI model 或 API key

```bash
# 查看當前設定
openclaw config get models.providers.tier1.apiKey

# 改 API key（所有 tier）
openclaw config set models.providers.tier1.apiKey "sk-新key"
openclaw config set models.providers.tier2.apiKey "sk-新key"

# 改 proxy 地址
openclaw config set models.providers.tier1.baseUrl "https://你的proxy/v1"

# 改完重啟
launchctl stop ai.openclaw.telegram && launchctl start ai.openclaw.telegram
```

### 唔用 OpenClaw 嘅話

Python scripts 嘅 AI 呼叫（news_sentiment.py、tg_bot.py、weekly_strategy_review.py）用 `secrets/.env` 入面嘅 `PROXY_API_KEY` + `PROXY_BASE_URL`。同 OpenClaw 設定分開。

```bash
# 編輯 Python scripts 嘅 API key
nano ~/projects/axc-trading/secrets/.env

# 入面填：
PROXY_API_KEY=你的key
PROXY_BASE_URL=https://你的proxy/v1
```

## SOUL.md

每個 Agent 都有自己嘅性格設定，位於 `agents/<name>/SOUL.md`。

SOUL.md 描述嘅 Agent Pipeline（scanner → filter → analyst → decision → trader）同實際嘅 trader_cycle 16 步 pipeline 係**兩套系統**。trader_cycle 為主，Agent Pipeline 係原始設計願景。
