# AXC Trading — Claude Code 入口
> ⚠️ 此文件上限200行。Claude Code 自動載入（唔可移動）。
> 最後更新：2026-03-13

## Collaborator
→ 睇 README.md「共同開發指南」| 改參數：`config/user_params.py`

## 新 Session 必讀（Owner only）
1. ~/projects/axc-trading/ai/CONTEXT.md   — 系統完整上下文
2. ~/projects/axc-trading/ai/MEMORY.md    — 近期狀態（backup自動更新）
3. ~/projects/axc-trading/ai/RULES.md     — 行為規則
4. ~/projects/axc-trading/ai/STRATEGY.md  — 交易策略

## 系統概覽
本地智能交易監控系統。9 agents + dashboard + Telegram bot。
推理：Claude API | 向量：voyage-3 | 記憶：jsonl + npy

## 搵記憶
- AXC 交易記憶：`python3 ~/projects/axc-trading/memory/retriever.py "問題"`
- 全局 gotchas/lessons：`python3 ~/.claude/scripts/query_knowledge.py "關鍵詞"`

## 系統健康檢查
`bash ~/projects/axc-trading/scripts/health_check.sh`

## 緊急操作
| 問題 | 指令 |
|---|---|
| Gateway | openclaw gateway health |
| Telegram | openclaw channels status --probe |
| Scanner | tail -20 logs/scanner.log |
| 全部服務 | launchctl list \| grep openclaw |

## 文件索引
→ 判斷樹：docs/architecture/TAXONOMY.md | 完整索引：docs/README.md

## 架構速查（按變化頻率）
🔴 常改：config/params.py, config/modes/
🟡 偶改：agents/*/SOUL.md, agents/main/workspace/skills/
🟢 少改：scripts/
⚫ 自動：shared/, logs/

## Gotchas
- 改參數：owner 改 config/params.py，collaborator 改 config/user_params.py
- tier2 Haiku 處理唔到 >10K system prompt
- Skill description 空白 = 靜默失敗
- fcntl.flock 防止 scanner 同 tradercycle 同時執行
- **重啟 tg_bot 前必須先 `launchctl bootout` 停 LaunchAgent，否則多 instance 撞 409**
  - 詳見 docs/guides/OPS.md「TG Bot 重複 Instance」
- STRATEGY.md 係自動生成（weekly_strategy_review.py），手改會被覆蓋

## 路徑
- Env: `AXC_HOME=~/projects/axc-trading`
- Trader: `cd $AXC_HOME && python3 scripts/trader_cycle/main.py --live --verbose`
- Gateway: `~/.openclaw/`（獨立，唔好動）

## Model Tiers
- tier1: claude-sonnet-4-6 — decisions + trading
- tier2: claude-haiku-4-5 — scanning + tg_bot
- tier3: gpt-5.4 — daily/agent default
- Proxy: `https://tao.plus7.plus/v1`, key = PROXY_API_KEY
- Proxy2 (GPT failover): `https://yinli.one/v1`, key = PROXY2_API_KEY

## Telegram
- @AXCTradingBot → tg_bot.py | @axccommandbot → gateway
- Chat ID: 2060972655 | HTML parse_mode | 廣東話口語

## 額外約束
- ai/ 只引用 docs/，唔複製內容
- agents/*/SOUL.md 全部原位

## 服務啟停
| 動作 | 指令 |
|------|------|
| 啟動 | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.{服務}.plist` |
| 停止 | `launchctl bootout gui/$(id -u)/ai.openclaw.{服務}` |
| 服務名 | scanner, telegram, tradercycle, dashboard, newsbot, heartbeat, lightscan, report 等 |

## 維護
- 每月1號：`bash scripts/integration_test.sh`
- Thread 告警 → 重啟 scanner
- 每日 03:00 crontab backup
