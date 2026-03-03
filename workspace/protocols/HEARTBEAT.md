# HEARTBEAT.md — 交易系統心跳協議
# 版本: 2026-03-02（Python 版）
# 執行方式: Python script (tools/heartbeat.py) via macOS launchd
# LaunchAgent: ai.openclaw.heartbeat（每 15 分鐘）
# 注意: 此為子目錄備份，主版本在根目錄 HEARTBEAT.md

## 每次心跳執行順序

1. 讀 {ROOT}/agents/trader/TRADE_STATE.md — 確認當前倉位
2. 讀 {ROOT}/agents/trader/config/SCAN_CONFIG.md — 確認 TRIGGER_PENDING 狀態
3. 讀 {ROOT}/routing/COST_TRACKER.md — 確認今日成本

## 警報觸發條件

立即發 Telegram（繁體中文）：
- TRADE_STATE 顯示倉位但 SL/TP 未確認
- SCAN_CONFIG.TRIGGER_PENDING = ON 超過 25 分鐘無人處理
- COST_TRACKER.DAILY_TOTAL > $0.50（Python-first 架構下超過代表異常）
- TRADE_STATE.POSITION_OPEN 但無 SL 記錄
- 任何 URGENT 標籤

## SCAN_LOG 監控（每24小時）

- 讀 {ROOT}/agents/trader/logs/SCAN_LOG.md
- 如果行數 > 180 → 觸發清理（保留最新 100 行）
- 記錄清理動作到 SCAN_LOG 尾部

## 例行心跳回報格式

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

## Telegram 格式（有警報）

```
⚠️ [YYYY-MM-DD HH:MM UTC+8] 心跳警報
━━━━━━━━━━━━━━
[警報內容]
需要用戶確認：[行動]
```

## 靜默規則

- 深夜 23:00-08:00 UTC+8：只有 URGENT 才發 Telegram
- 無警報：唔發 Telegram，只輸出 HEARTBEAT_OK

## 實現

Python script: `tools/heartbeat.py`（~200 行）
重用: `light_scan.py`（parse_scan_config, send_telegram）+ `trader_cycle/state/trade_state.py`（read_trade_state）
Exit codes: 0 = OK, 1 = ALERT sent, 2 = ERROR
Log: `~/.openclaw/logs/heartbeat.log`（JSON format）
