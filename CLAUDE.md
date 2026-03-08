# OpenClaw — Claude Code 入口
> ⚠️ 此文件上限200行。Claude Code 自動載入（唔可移動）。
> 最後更新：2026-03-08

## 如果你係新 Clone 嘅 Collaborator
→ 睇 README.md「共同開發指南」section 就夠。
→ 下面全部係 system owner 嘅操作指引，唔關你事。
→ 改交易參數：`config/user_params.py`（唔好改 `params.py`）

## 新 Session 必讀（Owner only）
1. ~/.openclaw/ai/CONTEXT.md   — 系統完整上下文
2. ~/.openclaw/ai/MEMORY.md    — 近期狀態（backup自動更新）
3. ~/.openclaw/ai/RULES.md     — 行為規則

## 系統概覽
本地智能交易監控系統。9 agents + dashboard + Telegram bot。
推理：Claude API | 向量：voyage-3 | 記憶：jsonl + npy

## 搵舊記憶
python3 ~/.openclaw/memory/retriever.py "問題"

## 系統健康檢查
bash ~/.openclaw/scripts/health_check.sh

## 緊急操作
| 問題 | 指令 |
|---|---|
| Gateway | openclaw gateway health |
| Telegram | openclaw channels status --probe |
| Scanner | tail -20 logs/scanner.log |
| 全部服務 | launchctl list \| grep openclaw |

## 新增文件判斷樹
→ docs/architecture/TAXONOMY.md

## 完整文件索引
→ docs/README.md

## 架構速查（按變化頻率）
🔴 常改：config/params.py, config/modes/
🟡 偶改：agents/*/SOUL.md, agents/main/workspace/skills/
🟢 少改：scripts/, openclaw.json
⚫ 自動：shared/, logs/

## Gotchas
- 改參數：owner 改 config/params.py，collaborator 改 config/user_params.py
- tier2 Haiku 處理唔到 >10K system prompt
- Skill description 空白 = 靜默失敗
- fcntl.flock 防止 scanner 同 tradercycle 同時執行
- **重啟 tg_bot 前必須先 `launchctl bootout` 停 LaunchAgent，否則多 instance 撞 409**
  - 詳見 docs/guides/OPS.md「TG Bot 重複 Instance」
