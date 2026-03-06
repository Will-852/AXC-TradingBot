<!--
title: 10 個 LaunchAgents
section: 機械體架構
order: 13
audience: human,claude,github
-->

# 10 個 LaunchAgents

| Agent | 類型 | 間隔 |
|-------|------|------|
| ai.openclaw.scanner | 常駐 | — |
| ai.openclaw.telegram | 常駐 | — |
| ai.openclaw.gateway | 常駐 | — |
| ai.openclaw.lightscan | 排程 | 每 3 分鐘 |
| ai.openclaw.tradercycle | 排程 | 每 30 分鐘 |
| ai.openclaw.heartbeat | 排程 | 每 25 分鐘 |
| ai.openclaw.report | 排程 | 每 30 分鐘 |
| ai.openclaw.newsagent | 排程 | 每 15 分鐘 |
| ai.openclaw.strategyreview | 排程 | 每週一 |
| ai.openclaw.newsmonitor | 常駐 | —（stale） |

## 管理指令

```bash
# 查看全部
launchctl list | grep openclaw

# 重啟指定服務
launchctl stop ai.openclaw.scanner && launchctl start ai.openclaw.scanner

# plist 位置
ls ~/Library/LaunchAgents/ai.openclaw.*.plist
```
