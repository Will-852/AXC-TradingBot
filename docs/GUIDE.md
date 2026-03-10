# OpenClaw 系統指南

> 此文件為 Claude Code 及 GitHub README 雙用途。
> 詳細分章節文件見 `docs/guides/` 目錄。

## 系統概覽

OpenClaw 係一個自動化加密貨幣交易系統，10 個 AI Agents 分工協作。
24 小時掃描市場、分析走勢、自動下單、Telegram 通知。

## 快速入門

- [AXC 係咩？](guides/01-what-is-axc.md)
- [系統運作流程](guides/02-how-it-works.md)
- [儀表板各區域](guides/03-dashboard-guide.md)

## 操作指南

- [交易模式](guides/04-trading-modes.md)
- [風控機制](guides/05-risk-control.md)
- [Telegram 指令](guides/06-telegram-commands.md)
- [換 API Key](guides/07-api-key-setup.md)
- [Terminal 指令](guides/08-terminal-commands.md)
- [常見問題](guides/09-faq.md)

## 架構

- [人體架構（LAYERS）](guides/10-layers-explained.md)
- [10 個 Agents](guides/11-agents.md)
- [15 個 Scripts](guides/12-scripts.md)
- [10 個 LaunchAgents](guides/13-launchagents.md)
- [數據流 + 文件結構](guides/14-data-flow.md)
- [Dashboard API](guides/15-dashboard-api.md)

## 文件維護說明（for Claude Code）

- 想改指南內容 → 改 `docs/guides/XX.md`
- `details.html` 自動讀取 guides/ 文件，唔需要改 HTML
- 新增指南 → 加 frontmatter（`<!-- title/section/order -->`），TOC 自動更新
- 刪除指南 → 刪除 `docs/guides/XX.md`，TOC 自動移除
- 修改 `details.html` 只需要喺改版面設計時
