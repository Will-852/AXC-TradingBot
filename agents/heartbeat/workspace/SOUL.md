# SOUL.md — Heartbeat Agent
# 版本: 2026-03-03

## 身份

我係 OpenClaw Heartbeat Monitor。純 Python 健康檢查，唔需要 LLM。

## 功能

每 15 分鐘自動執行：
1. 讀 TRADE_STATE.md → 檢查倉位 + SL/TP 確認狀態
2. 讀 SCAN_CONFIG.md → 檢查 TRIGGER_PENDING 超時
3. 讀 COST_TRACKER.md → 檢查日成本異常
4. SCAN_LOG.md → 超過 180 行就 trim
5. 有異常 → 發 Telegram 警報（繁中）

## 執行方式

```bash
python3 /Users/wai/.openclaw/workspace/tools/heartbeat.py
```

## 退出碼

- 0 = HEARTBEAT_OK（無警報）
- 1 = ALERT sent（有警報已發送）
- 2 = ERROR

## 警報條件

- URGENT: 倉位開啟但止損未確認
- URGENT: 倉位開啟但無止損設定
- WARNING: 止盈未確認
- WARNING: TRIGGER_PENDING 超過 25 分鐘
- WARNING: 日 API 成本超過 $0.50

## 靜音模式

23:00-08:00 UTC+8 只發 URGENT，WARNING 靜音。

## 共享狀態路徑

- TRADE_STATE: ~/.openclaw/shared/TRADE_STATE.md
- SCAN_CONFIG: ~/.openclaw/workspace/agents/aster_trader/config/SCAN_CONFIG.md
- COST_TRACKER: ~/.openclaw/workspace/routing/COST_TRACKER.md
- SCAN_LOG: ~/.openclaw/workspace/agents/aster_trader/logs/SCAN_LOG.md
