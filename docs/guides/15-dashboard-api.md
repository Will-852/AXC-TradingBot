<!--
title: Dashboard API
section: 附錄
order: 15
audience: human,claude,github
-->

# Dashboard API

## 13 個 API 端點

| 端點 | Method | 功能 |
|------|--------|------|
| /api/data | GET | 主數據（持倉、PnL、agents、掃描、風控、行動計劃） |
| /api/debug | GET | Debug 資訊 |
| /api/suggest_mode | GET | AI 建議交易模式 |
| /api/binance/status | GET | Binance 連接狀態 |
| /api/file?path=... | GET | 讀取文件（白名單） |
| /api/docs-list | GET | 文件列表 |
| /api/doc/\<name\> | GET | 讀取文件內容 |
| /details | GET | 系統說明頁面 |
| /api/set_mode | POST | 切換交易模式 |
| /api/binance/connect | POST | 連接 Binance（存 key） |
| /api/binance/disconnect | POST | 斷開 Binance（清 key） |

安全提示：API 密鑰、資金資訊不會在此頁面顯示。敏感設定：`secrets/.env` + `openclaw.json`
