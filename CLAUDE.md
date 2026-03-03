# CLAUDE.md — 人類地圖
# 閱讀對象: 你（人類）

## 🔴 出事？

| 問題 | 指令 |
|---|---|
| Gateway | openclaw gateway health |
| Telegram | openclaw channels status --probe |
| 交易冇執行 | tail -20 logs/lightscan.log |
| Skill問題 | openclaw skills list |
| 改動歷史 | cat agents/main/workspace/EVOLUTION_LOG.md |

## 架構（按變化頻率）

🔴 常常改：
config/params.py          ← 所有數字參數
config/modes/             ← RANGE / TREND / VOLATILE

🟡 偶爾改：
agents/*/workspace/SOUL.md ← AI行為原則
agents/main/workspace/skills/ ← Skills

🟢 唔常改：
scripts/                  ← Python執行層
openclaw.json             ← OpenClaw設定

⚫ 即時變（唔需要改）：
shared/                   ← Agent狀態
logs/                     ← 日誌

## 四個 Agents
main      → agents/main/workspace/
trader    → agents/trader/workspace/
scanner   → agents/scanner/workspace/
heartbeat → agents/heartbeat/workspace/

## 切換模式
只改 config/modes/ 入面嘅active mode
其他唔需要動

## Gotchas [R]
[R] tier2 Haiku 唔夠強處理 >10K system prompt
[R] Skill description 空白 = 靜默失敗
[R] fcntl.flock 防止 scanner 同 tradercycle 同時執行
[R] 改參數只改 config/params.py，唔改scripts

## 備份指令
cd ~/.openclaw && \
git add -A && \
git commit -m "[$(date +%Y-%m-%d)] backup" && \
zip -r backups/backup-$(date +%Y-%m-%d-%H%M).zip \
  openclaw.json config/ agents/ shared/ scripts/ \
  ~/Library/LaunchAgents/ai.openclaw.*.plist
