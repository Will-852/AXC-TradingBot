# AGENTS.md — OpenClaw 交易系統服務定義
# 版本: 2026-03-02 (Python-first)
# 注意: 所有服務已改為 100% Python，零 LLM 消耗

---

## 架構原則

> **100% 規則化嘅邏輯用 Python，唔用 LLM**

所有自動化服務（light-scan / trader-cycle / heartbeat）係 Python scripts，
由 macOS LaunchAgent 定時執行。LLM 只用於 on-demand 任務（用戶觸發）。

---

## 服務（Python, $0.00/日）

### light-scan（每 3 分鐘）
- **LaunchAgent:** `ai.openclaw.lightscan`
- **入口:** `tools/light_scan.py`
- **職責:** price/volume/S-R/funding 掃描 → 設 TRIGGER_PENDING flag
- **寫入:** SCAN_CONFIG.md（TRIGGER 欄位）, SCAN_LOG.md

### trader-cycle（每 30 分鐘）
- **LaunchAgent:** `ai.openclaw.tradercycle`
- **入口:** `tools/trader_cycle/main.py`
- **職責:** 16 步 pipeline — 分析 + 策略評估 + 落盤
- **模式:** `--dry-run`（default）/ `--live`（接 Aster DEX）
- **寫入:** TRADE_STATE.md, TRADE_LOG.md, SCAN_CONFIG.md, SCAN_LOG.md

### heartbeat（每 15 分鐘）
- **LaunchAgent:** `ai.openclaw.heartbeat`
- **入口:** `tools/heartbeat.py`
- **職責:** 倉位狀態監控、觸發檢查、log 清理

### gateway
- **LaunchAgent:** `ai.openclaw.gateway`
- **地址:** ws://127.0.0.1:18789

---

## Workspace 邊界

| 行為 | 誰可以做 |
|------|---------|
| 讀任何 MD | 任何服務 |
| 寫 TRADE_STATE.md | trader-cycle |
| 寫 TRADE_LOG.md | trader-cycle |
| 寫 SCAN_CONFIG.md（TRIGGER）| light-scan |
| 寫 SCAN_CONFIG.md（全部）| trader-cycle |
| 落盤（Aster DEX API）| trader-cycle（`--live` mode）|
| 發 Telegram | 任何服務，繁體中文 |

---

## 未來服務（待建立）

- **Monitor Agent** — 實時市場監控（目錄存在，設定未完成）
- **Analyst Agent** — 深度市場分析、勝率統計

---

## Telegram 規範

- **語言:** 繁體中文（必須）
- **時間戳:** YYYY-MM-DD HH:MM UTC+8
- **URGENT 標籤:** 緊急情況使用，觸發即時通知
