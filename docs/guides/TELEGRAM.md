# Telegram Bot 指令完整說明

Bot：@AXCTradingBot

## 免費指令（讀本地文件，零 AI cost）

| 指令 | 說明 |
|------|------|
| `/start` | 顯示指令列表 |
| `/report` | 完整倉位報告 |
| `/pos` | 當前持倉 |
| `/bal` | 餘額 |
| `/pnl` | 今日/累計盈虧 |
| `/scan` | 最新掃描結果 |
| `/log` | 最近記錄 |
| `/health` | 系統狀態（agent 活躍度） |

## 控制指令

| 指令 | 說明 |
|------|------|
| `/mode` | 查看/切換模式（彈出按鈕） |
| `/mode CONSERVATIVE` | 保守模式 |
| `/mode BALANCED` | 平衡模式 |
| `/mode AGGRESSIVE` | 進取模式 |
| `/sl breakeven` | 止損移至開倉價 |
| `/sl breakeven XAGUSDT` | 指定幣種 |
| `/pause` | 暫停交易 |
| `/resume` | 恢復交易 |
| `/cancel` | 取消待確認訂單 |

## AI 分析指令（調用 Claude Haiku）

| 指令 | 說明 |
|------|------|
| `/ask [問題]` | 帶本地數據 + RAG 記憶分析 |
| 直接輸入文字 | 自動判斷：下單意圖或分析 |

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

高風險訂單（>80%餘額）：90秒冷靜期

## 主動推送

| 事件 | 推送內容 |
|------|----------|
| 倉位平倉（SL/TP觸發） | 自動生成廣東話交易報告 |
| Agent 停止 >15 分鐘 | 系統告警 |

## 安全

- 只接受白名單 chat_id（TELEGRAM_CHAT_ID）
- 陌生人發指令 -> 靜默忽略
- 所有下單需二次確認
- SL 強制設定（唔設定會自動用 2.5%）
- SL 低於清算價自動調整

## 兩個 Bot

| Bot | 用途 |
|-----|------|
| @AXCTradingBot | tg_bot.py — 交易控制介面 |
| @axccommandbot | openclaw-gateway — 系統指令 |

兩個 bot 用唔同 token，避免 409 Conflict。
