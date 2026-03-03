# HEARTBEAT.md — 交易系統心跳協議（僅供 systemEvent / launchd 使用）
# 版本: 2026-03-03
# 執行方式: Python script (tools/heartbeat.py) via macOS launchd
# LaunchAgent: ai.openclaw.heartbeat（每 15 分鐘）

## ⚠️ 重要：呢個檔案只適用於 heartbeat systemEvent

**呢個檔案唔適用於用戶訊息。**
當用戶透過 Telegram 發送訊息（包括 /report、/pos、/bal 等任何文字），
請參照 SOUL.md 嘅「Telegram Slash Commands」section 處理，唔好行心跳流程。

只有當訊息來源係 systemEvent 或 launchd heartbeat 時，先用以下心跳邏輯。

---

## 心跳執行順序（僅 systemEvent）

1. 讀 agents/trader/TRADE_STATE.md — 確認當前倉位
2. 讀 agents/trader/config/SCAN_CONFIG.md — 確認 TRIGGER_PENDING 狀態
3. 讀 routing/COST_TRACKER.md — 確認今日成本

## 警報觸發條件

立即發 Telegram（繁體中文）：
- TRADE_STATE 顯示倉位但 SL/TP 未確認
- SCAN_CONFIG.TRIGGER_PENDING = ON 超過 25 分鐘無人處理
- COST_TRACKER.DAILY_TOTAL > $0.50（Python-first 架構下超過代表異常）
- TRADE_STATE.POSITION_OPEN 但無 SL 記錄
- 任何 URGENT 標籤

## 回報格式

無警報時：
```
HEARTBEAT_OK | [YYYY-MM-DD HH:MM UTC+8] | 倉位:[有/無] | 成本:$[X] | Trigger:[ON/OFF]
```

有警報時：
```
⚠️ HEARTBEAT ALERT | [時間]
原因：[具體原因]
需要：[用戶行動]
```

## 靜默規則

- 深夜 23:00-08:00 UTC+8：只有 URGENT 才發 Telegram
- 無警報：唔發 Telegram，只輸出 HEARTBEAT_OK

## 實現

Python script: `tools/heartbeat.py`（~200 行）
Exit codes: 0 = OK, 1 = ALERT sent, 2 = ERROR
