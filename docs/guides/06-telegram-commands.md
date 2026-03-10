<!--
title: Telegram 指令完整版
section: 操作指南
order: 6
audience: human,claude,github
-->

# Telegram 指令完整版

Bot：@AXCTradingBot

## 查詢類（零 AI cost）

| 指令 | 功能 |
|------|------|
| `/start` | 顯示指令列表 |
| `/health` | 系統狀態 + agent 心跳 + 記憶數量 |
| `/report` | 完整持倉報告 |
| `/pos` | 當前持倉 |
| `/bal` | 帳戶餘額 |
| `/pnl` | 今日 + 累計盈虧 |
| `/scan` | 最新掃描結果 |
| `/log` | 最近日誌 |
| `/sl` | 查看當前止蝕位 |

## 操控類（零 AI cost）

| 指令 | 功能 |
|------|------|
| `/mode` | 查看/切換模式（彈出按鈕選擇） |
| `/mode BALANCED` | 直接切換到平衡模式 |
| `/mode CONSERVATIVE` | 保守模式 |
| `/mode AGGRESSIVE` | 進取模式 |
| `/sl breakeven` | 全部持倉止蝕移到入場價 |
| `/sl breakeven XAGUSDT` | 指定幣種止蝕移到入場價 |
| `/pause` | 暫停交易（保留持倉） |
| `/resume` | 恢復交易 |
| `/cancel` | 取消掛單確認 |

## AI 分析類（調用 Haiku）

| 指令 | 功能 |
|------|------|
| `/ask BTC 走勢如何？` | AI 分析 + RAG 記憶查詢 |
| 直接打字 | 自動偵測：下單意圖 or 分析請求 |

## 自然語言下單

```
你說：「做多 ETH $5 10倍 SL 2089 TP 2169」
Bot：彈出確認按鈕（顯示完整倉位細節）
你：確認
Bot：執行下單 + 顯示結果
```

支援：
- 絕對價格 SL/TP（如 SL 2089）
- 百分比 SL/TP（如 SL 2.5%）
- 唔指定則用預設（SL 2.5%, TP 4%）

高風險訂單（>80% 餘額）：90 秒冷靜期

## 主動推送

| 事件 | 推送內容 |
|------|----------|
| 倉位平倉（SL/TP 觸發） | 自動生成廣東話交易報告 |
| Agent 停止 >15 分鐘 | 系統告警 |

## 安全機制

- 只接受白名單 chat_id（TELEGRAM_CHAT_ID）
- 陌生人發指令 → 靜默忽略
- 所有下單需二次確認
- SL 強制設定（唔設定會自動用 2.5%）
- SL 低於清算價自動調整

## 三個 Bot

| Bot | Script | 用途 | 必須？ |
|-----|--------|------|--------|
| @AXCTradingBot | `tg_bot.py` | 交易控制（查詢/下單/風控） | ✅ 核心 |
| @AXCnews_bot | `news_bot.py` | 新聞情緒（查詢/提交/自動推送） | 選填 |
| @axccommandbot | openclaw-gateway | 系統指令（需要 OpenClaw） | 選填 |

每個 bot 用獨立 token，避免 409 Conflict。

### @AXCnews_bot 指令

| 指令 | 功能 |
|------|------|
| `/start` | 歡迎訊息 |
| `/news` | 查詢當前新聞情緒 |
| `/submit BTC ETF 獲批` | 手動提交新聞 |
| 直接打字 | 自動收錄為新聞 |

自動推送：情緒方向變化（bullish→bearish 等）時主動通知，1 小時 cooldown。
