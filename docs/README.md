# AXC Trading System

> 本地優先的智能交易監控系統

本地運行，AI 驅動，你擁有所有數據。唔需要 OpenClaw 或任何外部平台。

## 系統概覽

| 組件 | 說明 | 必要？ |
|------|------|--------|
| Dashboard | 本地網頁監控介面（:5555） | ✅ 核心 |
| Trader Cycle | 自動掃描 + 策略 + 下單 | ✅ 核心 |
| Scanner | 多交易所市場掃描 | ✅ 核心 |
| Heartbeat | 倉位 + 止損監控 + 告警 | ✅ 核心 |
| RAG 記憶 | voyage-3 語義搜尋歷史記錄 | ✅ 核心 |
| @AXCTradingBot | Telegram 交易控制 | 選填 |
| @AXCnews_bot | Telegram 新聞情緒 | 選填 |
| News Agent | RSS 抓取 + AI 情緒分析 | 選填 |
| Aster DEX | 交易執行 | 選填 |
| Binance | 交易執行 | 選填 |
| OpenClaw | Gateway（Agent sessions） | 選填 |

## 快速開始

```bash
git clone https://github.com/Will-852/AXC-TradingBot.git ~/projects/axc-trading
cd ~/projects/axc-trading
pip3 install -r requirements.txt --break-system-packages
cp docs/friends/.env.example secrets/.env
nano secrets/.env    # 填入 API keys
python3 scripts/dashboard.py
# 打開 http://127.0.0.1:5555
```

## 三層架構

```
OpenClaw Gateway（可選）     ← @axccommandbot、Agent sessions
        │
AXC 交易系統（核心）         ← 掃描、交易、監控、Telegram、新聞
        │
Proxy API（AI 功能需要）     ← 任何 Claude/OpenAI 兼容 endpoint
```

三層完全獨立。詳見 [18-how-axc-runs.md](guides/18-how-axc-runs.md)。

```
推理層：Claude API（tier1 Sonnet / tier2 Haiku / tier3 GPT-5-mini）
向量層：voyage-3
搜尋層：numpy cosine similarity
記憶層：jsonl + npy
排程層：macOS LaunchAgents（鬧鐘模式）
通訊層：shared/ JSON + MD 文件
```

## 文件索引

### 入門
| 文件 | 用途 |
|------|------|
| [guides/01-what-is-axc.md](guides/01-what-is-axc.md) | AXC 係咩 + 功能一覽 |
| [guides/02-how-it-works.md](guides/02-how-it-works.md) | 運作流程 |
| [guides/18-how-axc-runs.md](guides/18-how-axc-runs.md) | 鬧鐘 vs 班長架構（必讀） |
| [friends/INSTALL.md](friends/INSTALL.md) | Collaborator 評測指南 |

### 安裝 + 設定
| 文件 | 用途 |
|------|------|
| [setup/INSTALL.md](setup/INSTALL.md) | 完整安裝步驟 |
| [setup/ENV_SETUP.md](setup/ENV_SETUP.md) | 環境變數設定 |
| [setup/RECOVERY.md](setup/RECOVERY.md) | 換電腦 / 文件遺失 |
| [guides/07-api-key-setup.md](guides/07-api-key-setup.md) | API Key 完整指南（10 個 key） |
| [guides/00-install.md](guides/00-install.md) | 快速安裝 |

### 操作
| 文件 | 用途 |
|------|------|
| [guides/03-dashboard-guide.md](guides/03-dashboard-guide.md) | Dashboard 使用 |
| [guides/06-telegram-commands.md](guides/06-telegram-commands.md) | Telegram 三個 Bot 指令 |
| [guides/04-trading-modes.md](guides/04-trading-modes.md) | 交易模式 |
| [guides/05-risk-control.md](guides/05-risk-control.md) | 風控設定 |
| [guides/16-parameter-guide.md](guides/16-parameter-guide.md) | 參數完整說明 |
| [guides/OPS.md](guides/OPS.md) | Proxy 切換 / Key Rotate |
| [guides/BACKUP.md](guides/BACKUP.md) | 備份說明 |
| [guides/SYMBOLS.md](guides/SYMBOLS.md) | 加幣種操作 |
| [guides/08-terminal-commands.md](guides/08-terminal-commands.md) | Terminal 指令 |
| [guides/13-launchagents.md](guides/13-launchagents.md) | LaunchAgent 管理 |

### 架構
| 文件 | 用途 |
|------|------|
| [architecture/BOUNDARY.md](architecture/BOUNDARY.md) | AXC ↔ OpenClaw 邊界 + 三層圖 |
| [architecture/TAXONOMY.md](architecture/TAXONOMY.md) | 文件分類判斷樹 |
| [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | AI stack 選型 |
| [architecture/AGENTS.md](architecture/AGENTS.md) | 各 Agent 職責 |
| [architecture/ROADMAP.md](architecture/ROADMAP.md) | 未來計劃 |
| [guides/10-layers-explained.md](guides/10-layers-explained.md) | 人體架構（六層） |
| [guides/11-agents.md](guides/11-agents.md) | Agent 詳解 |
| [guides/12-scripts.md](guides/12-scripts.md) | Script 一覽 |
| [guides/14-data-flow.md](guides/14-data-flow.md) | 資料流 |
| [guides/17-connections.md](guides/17-connections.md) | 交易所連接 |

### AI Context（Agent 專用）
| 文件 | 用途 |
|------|------|
| [ai/CONTEXT.md](../ai/CONTEXT.md) | 系統概覽（Agent 讀） |
| [ai/MEMORY.md](../ai/MEMORY.md) | 記憶快照（backup 自動） |
| [ai/RULES.md](../ai/RULES.md) | 行為規則 |
| [ai/STRATEGY.md](../ai/STRATEGY.md) | 交易策略（weekly 自動） |

## License

Private — 待開源
