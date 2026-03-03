# HEARTBEAT.md — Message Router
# 版本: 2026-03-03

## 訊息處理規則

- 如果訊息以 `/` 開頭 → 執行 SOUL.md 定義嘅 Slash Command
- 如果訊息係普通文字 → 正常回覆用戶問題
- 唔好預設行心跳。心跳由 heartbeat agent 獨立處理。

## Agent 分工

- 心跳監控 → heartbeat agent（每 15 分鐘，Python）
- 市場掃描 → scanner agent（每 3 分鐘，Python）
- 交易執行 → trader agent（信號觸發時）
- Telegram 介面 → main agent（本 agent）
