<!--
title: LaunchAgents（服務管理）
section: 機械體架構
order: 13
audience: human,claude,github
-->

# LaunchAgents（服務管理）

macOS 用 LaunchAgent 管理常駐服務。好似 Windows 嘅「系統服務」或 Linux 嘅 systemd。

## 點解叫 ai.openclaw.*？

LaunchAgent 嘅名用 `ai.openclaw.` 開頭係因為底層用 OpenClaw 平台。呢啲係系統識別名（改唔到），唔影響 AXC 運作。

## 13 個服務（+ 2 已停用）

| 服務 | 類型 | 間隔 | 做咩 |
|------|------|------|------|
| ai.openclaw.scanner | 常駐 | — | 9 交易所輪轉掃描（async_scanner.py） |
| ai.openclaw.telegram | 常駐 | — | Telegram 交易 bot（tg_bot.py） |
| ai.openclaw.gateway | 常駐 | — | OpenClaw Gateway + @axccommandbot |
| ai.openclaw.newsbot | 常駐 | — | @AXCnews_bot 獨立新聞 Telegram Bot（news_bot.py） |
| ai.openclaw.lightscan | 排程 | 每 3 分鐘 | Aster 輕量掃描（light_scan.py） |
| ai.openclaw.tradercycle | 排程 | 每 30 分鐘 | 交易引擎 16 步（trader_cycle/main.py） |
| ai.openclaw.heartbeat | 排程 | 每 15 分鐘 | 系統健康檢查 |
| ai.openclaw.report | 排程 | 每 30 分鐘 | 定時報告 |
| ai.openclaw.newsscraper | 排程 | 每 5 分鐘 | RSS 新聞收集（news_scraper.py） |
| ai.openclaw.newssentiment | 排程 | 每 15 分鐘 | AI 情緒分析（news_sentiment.py） |
| ai.openclaw.macromonitor | 排程 | 每 4 小時 | 宏觀市場流動性監察（macro_monitor.py） |
| ai.openclaw.xmonitor | 排程 | 每 1 小時 | X 帳號推文監察（x_monitor.py） |
| ai.openclaw.strategyreview | 排程 | 每週一 10:00 | 每週策略回顧 |
| ~~ai.openclaw.newsagent~~ | 已停用 | — | 舊版新聞 agent（已拆分為 newsscraper + newssentiment） |
| ~~ai.openclaw.newsmonitor~~ | 已停用 | — | 舊版新聞監察 |

## 管理指令

```bash
# 查看全部服務
launchctl list | grep openclaw

# 重啟指定服務
launchctl stop ai.openclaw.scanner && launchctl start ai.openclaw.scanner

# ⚠️ 重啟 Telegram bot（必須用 bootout，唔係 stop）
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.telegram.plist
sleep 2
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.telegram.plist

# plist 文件位置
ls ~/Library/LaunchAgents/ai.openclaw.*.plist
```

## 常駐 vs 排程

- **常駐（KeepAlive）**：crash 後自動重啟。scanner / telegram / gateway 係常駐。
- **排程（StartInterval）**：固定間隔執行一次。tradercycle / heartbeat 係排程。

## plist 文件格式

每個服務嘅設定喺 `~/Library/LaunchAgents/ai.openclaw.*.plist`。關鍵欄位：

```xml
<key>ProgramArguments</key>
<array>
    <string>/bin/bash</string>
    <string>/Users/你/projects/axc-trading/scripts/load_env.sh</string>
    <string>python3</string>
    <string>scripts/async_scanner.py</string>
</array>
```

⚠️ 所有 plist 都要用 `load_env.sh` wrapper — 唔可以直接 call `python3 xxx.py`（因為 LaunchAgent 唔會載入你嘅 shell 環境變數）。

## 日誌位置

| 服務 | 日誌 |
|------|------|
| scanner | `logs/scanner.log` |
| telegram | `logs/telegram.log` + `logs/telegram.err.log` |
| tradercycle | `logs/cycles/` |
| heartbeat | `logs/heartbeat.log` |

## Gotchas

- Telegram bot 如果用 `launchctl stop` → `start` 可能出 409 Conflict（因為舊 polling 未斷）。必須用 `bootout` → `bootstrap`
- scanner 用 `fcntl.flock` 防止同 trader_cycle 同時執行
- 心跳 23:00-08:00 HKT 靜音（只發 URGENT 告警）
