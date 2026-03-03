---
name: system-memory
description: System memory paths, write rules, and backup protocol for all agents
status: ACTIVE
last-updated: 2026-03-03
---

# system-memory
# Status: ACTIVE
# Last updated: 2026-03-03
# 載入時機: 每次對話（輕量，操作用）

## 文件路徑
靈魂:    agents/[name]/workspace/SOUL.md
狀態:    workspace/memory/MEMORY.md
共享:    shared/TRADE_STATE.md
         shared/SIGNAL.md
         shared/SYSTEM_STATUS.md

## 寫入規則（唔可以違反）
TRADE_STATE.md   → trader 獨寫
SIGNAL.md        → scanner 獨寫
SYSTEM_STATUS.md → heartbeat 獨寫
自己 SOUL.md     → 各自獨寫

## Model 失敗處理

IF Haiku 返回錯誤 OR 結果明顯唔完整：
  → 自動升級用 tier1 Sonnet 重試
  → 寫入 MEMORY.md:
    [日期] WARN Haiku失敗，升級Sonnet：[任務描述]

判斷「唔完整」：
- 回覆少於預期
- 回覆包含「I cannot」或「too long」
- Python執行出錯

## 備份觸發
改 SOUL.md / skill / openclaw.json / LaunchAgent → 執行：

cd ~/.openclaw && \
git add -A && \
git commit -m "[$(date +%Y-%m-%d)] auto-backup" && \
zip -r backups/backup-$(date +%Y-%m-%d-%H%M).zip \
  openclaw.json workspace/core/ workspace/skills/ \
  workspace/memory/ agents/*/workspace/SOUL.md \
  ~/Library/LaunchAgents/ai.openclaw.*.plist

(Git記錄改咗咩 + zip快照保留完整副本)

## 參考文件（按需讀取）
建立新 agent/skill: workspace/docs/AGENT_BLUEPRINT.md
系統進化規則:       workspace/docs/SYSTEM_EVOLUTION.md
Debug 教訓:        workspace/docs/OPENCLAW_DEBUG.md
