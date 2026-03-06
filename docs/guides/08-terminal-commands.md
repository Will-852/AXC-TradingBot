<!--
title: Terminal 指令參考
section: 操作指南
order: 8
audience: human,claude,github
-->

# Terminal 指令參考

以下指令需要喺 Mac Terminal 執行。唔熟悉嘅話，Telegram 指令已經夠用。

## 服務管理

```bash
# 查看所有服務
launchctl list | grep openclaw

# 停止指定服務
launchctl stop ai.openclaw.scanner

# 啟動指定服務
launchctl start ai.openclaw.scanner

# 重啟（逐個 stop → sleep 3 → 逐個 start）
launchctl stop ai.openclaw.scanner
launchctl stop ai.openclaw.telegram
launchctl stop ai.openclaw.gateway
sleep 3
launchctl start ai.openclaw.scanner
launchctl start ai.openclaw.telegram
launchctl start ai.openclaw.gateway
```

## 診斷

```bash
# 完整健康檢查
bash ~/.openclaw/scripts/health_check.sh

# 查看掃描日誌
tail -20 ~/.openclaw/logs/scanner.log

# 查看掃描心跳
cat ~/.openclaw/logs/scanner_heartbeat.txt

# 清除掃描鎖（掃描器卡住時用）
rm ~/.openclaw/shared/scanner_runner.lock

# 集成測試
bash ~/.openclaw/scripts/integration_test.sh
```

## 備份

```bash
# 手動備份（推送到 GitHub）
bash ~/.openclaw/scripts/backup_agent.sh

# 自動備份：crontab 每日 03:00 自動執行
```

## 儀表板

```bash
# 啟動儀表板
cd ~/.openclaw && python3 scripts/dashboard.py &

# 然後瀏覽器開
open http://localhost:5555
```

## 手動交易

```bash
cd ~/.openclaw/workspace/tools && python3 -m trader_cycle.main --live --verbose
```

## 策略回顧

```bash
python3 ~/.openclaw/scripts/weekly_strategy_review.py
```

## 加幣種

```bash
# 1. 編輯 config/params.py，加 "XXXUSDT" 到 SYMBOLS list
# 2. 重啟掃描器
launchctl stop ai.openclaw.scanner && sleep 2 && launchctl start ai.openclaw.scanner
```

## RAG 記憶

```bash
# 查詢記憶
python3 ~/.openclaw/memory/retriever.py "BTC 上次入場點解輸？"

# 重建索引
python3 ~/.openclaw/scripts/memory_init.py
```

## OpenClaw CLI

```bash
openclaw config get models.providers.tier1.apiKey  # 查 API key
openclaw config set <path> "<value>"               # 改設定
openclaw config file                               # 設定文件路徑
openclaw channels status --probe                   # Telegram 狀態
openclaw gateway health                            # Gateway 健康
openclaw status                                    # 頻道健康 + 最近 session
```

## 出事排查

| 症狀 | 檢查 |
|------|------|
| Telegram 冇反應 | `launchctl list ai.openclaw.telegram`，睇 PID |
| 409 Conflict | 確認 tg_bot.py 同 gateway 用唔同 token |
| 下單失敗 | `tail -50 ~/.openclaw/logs/telegram.err.log` |
| Scanner 卡住 | `rm ~/.openclaw/shared/scanner_runner.lock` |
| TRADE_STATE 過期 | 通過 Telegram 下單觸發自動同步 |
| Dashboard 冇數據 | `python3 ~/.openclaw/scripts/dashboard.py` |
