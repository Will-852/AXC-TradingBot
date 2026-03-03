# CLAUDE.md — 人類快速導航
# 閱讀對象: 你（人類）
# 概念說明: workspace/skills/system-memory/SKILL.md
# 架構藍圖: workspace/docs/AGENT_BLUEPRINT.md

## 出事快速指引
Gateway 唔回應     → openclaw gateway health
Telegram 收唔到    → openclaw channels status --probe
交易冇執行         → tail -20 ~/.openclaw/logs/lightscan.log
Skill 唔載入       → openclaw skills list
查改動歷史         → cat workspace/EVOLUTION_LOG.md

## 四個 Agents
main      tier1 Sonnet  → Telegram + slash commands
trader    tier1 Sonnet  → 交易執行
scanner   tier2 Haiku   → 市場掃描
heartbeat tier3 mini    → 健康檢查

## 自動化時間表
每3分鐘:  scanner_runner.py (訊號偵測)
每3小時:  trader_cycle/main.py (完整週期)
每25分鐘: heartbeat.py (健康檢查)
每30分鐘: slash_cmd.py report --send (定時報告)

## 重要路徑
設定:    ~/.openclaw/openclaw.json
Python:  workspace/tools/
日誌:    ~/.openclaw/logs/
備份:    ~/.openclaw/backups/
共享:    ~/.openclaw/shared/

## Gotchas [R]
[R] tier2 Haiku 唔夠強處理 >10K system prompt
[R] Skill description 空白 = 靜默失敗唔載入
[R] fcntl.flock 防止 scanner 同 tradercycle 同時執行

## 備份指令
cd ~/.openclaw && zip -r \
  backups/backup-$(date +%Y-%m-%d-%H%M).zip \
  openclaw.json workspace/core/ workspace/skills/ \
  workspace/memory/ agents/*/workspace/SOUL.md \
  shared/ ~/Library/LaunchAgents/ai.openclaw.*.plist
