<!--
title: Dashboard API（28 端點）
section: 附錄
order: 15
audience: human,claude,github
-->

# Dashboard API（28 端點）

Dashboard 跑喺 `http://localhost:5555`，只接受本機連線（127.0.0.1）。

## GET 端點（讀取數據）

| 端點 | 功能 | 返回 |
|------|------|------|
| `/api/data` | 主數據：餘額、PnL、持倉、agents、掃描記錄、行動計劃 | JSON |
| `/api/state` | 當前交易狀態：信號、方向、策略、分數、profile | JSON |
| `/api/config` | 所有交易參數（from params.py） | JSON |
| `/api/scan-log` | 最近 20 行掃描日誌 | JSON |
| `/api/health` | 系統健康：服務狀態、文件年齡、心跳、記憶數量、uptime | JSON |
| `/api/debug` | Debug：TRADE_STATE / SIGNAL / SCAN_CONFIG 原始內容 | JSON |
| `/api/suggest_mode` | 根據 BTC 24h 波動建議交易模式 | JSON |
| `/api/binance/status` | Binance 連接狀態 + 餘額 + key 預覽 | JSON |
| `/api/aster/status` | Aster 連接狀態 + 餘額 + key 預覽 | JSON |
| `/api/hl/status` | HyperLiquid 連接狀態 + 餘額 | JSON |
| `/api/docs-list` | docs/ 下所有 .md 文件列表 | JSON |
| `/api/doc/<name>` | 讀取指定文件內容 | text |
| `/api/file?path=...` | 讀取 docs/ 範圍內嘅文件（白名單） | text |
| `/details` | 系統說明頁面（你而家睇緊） | HTML |
| `/share` | 分享頁面（畀朋友下載 setup package） | HTML |
| `/share/windows` | Windows 版分享頁 | HTML |
| `/api/share/package` | 生成 + 下載安裝包 .zip | ZIP |
| `/api/open_folder?path=...` | 喺 Finder 打開指定資料夾 | JSON |

## POST 端點（執行操作）

| 端點 | Body | 功能 |
|------|------|------|
| `/api/set_mode` | `{"mode": "AGGRESSIVE"}` | 切換交易 profile（直接改 params.py） |
| `/api/config/mode` | `{"mode": "BALANCED"}` | 同上（alias） |
| `/api/config/trading` | `{"enabled": true}` | 開/關交易功能 |
| `/api/binance/connect` | `{"api_key": "...", "api_secret": "..."}` | 連接 Binance（存 key 到 .env） |
| `/api/binance/disconnect` | （空） | 斷開 Binance（清 key） |
| `/api/aster/connect` | `{"api_key": "...", "api_secret": "..."}` | 連接 Aster |
| `/api/aster/disconnect` | （空） | 斷開 Aster |
| `/api/hl/connect` | `{"private_key": "...", "account_address": "..."}` | 連接 HyperLiquid |
| `/api/hl/disconnect` | （空） | 斷開 HyperLiquid |
| `/api/close-position` | `{"symbol": "BTCUSDT", "platform": "aster"}` | ⚠️ 即時市價平倉 |
| `/api/modify-sltp` | `{"symbol": "...", "sl_price": 94000, "tp_price": 100000}` | 修改 SL/TP |

## 安全

- 只接受 127.0.0.1（本機）連線
- API key 永遠唔會完整顯示（只 preview 頭尾幾個字）
- 平倉操作前端有確認彈窗
- 敏感設定存喺 `secrets/.env`（gitignored）
- Demo 模式：冇連接任何交易所 → 顯示假數據

## 冇連接交易所嘅 Demo 模式

如果 Aster、Binance、HyperLiquid 都冇連接，`/api/data` 會返回模擬數據（sine-wave PnL），方便你睇 Dashboard 點運作。連接任何一個交易所後自動切換到實時數據。
