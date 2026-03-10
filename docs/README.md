# OpenClaw

> 本地優先的智能交易監控系統

本地運行，AI 驅動，你擁有所有數據。

## 系統概覽

| 組件 | 說明 | 必要 |
|------|------|------|
| Dashboard | 本地網頁監控介面 | 必要 |
| AI Agents | 9個智能代理 | 必要 |
| RAG 記憶 | voyage-3 語義搜尋歷史記錄 | 必要 |
| Telegram Bot | 手機控制介面 | 選填 |
| Aster DEX | 交易執行 | 選填 |
| Binance | 交易執行 | 選填 |

## 快速開始

```bash
git clone https://github.com/Will-852/AXC-TradingBot.git ~/projects/axc-trading
cd ~/projects/axc-trading
pip3 install -r requirements.txt --break-system-packages
cp docs/friends/.env.example secrets/.env
# 填入 API keys
python3 scripts/dashboard.py
# 打開 http://127.0.0.1:5555
```

## 文件索引

| 類型 | 文件 | 用途 |
|------|------|------|
| AI | [ai/CONTEXT.md](../ai/CONTEXT.md) | 系統概覽（Agent 讀） |
| AI | [ai/MEMORY.md](../ai/MEMORY.md) | 記憶快照（backup 自動） |
| AI | [ai/RULES.md](../ai/RULES.md) | 行為規則 |
| AI | [ai/STRATEGY.md](../ai/STRATEGY.md) | 交易策略（weekly 自動） |
| 安裝 | [setup/INSTALL.md](setup/INSTALL.md) | 完整安裝步驟 |
| 安裝 | [setup/ENV_SETUP.md](setup/ENV_SETUP.md) | API keys 設定 |
| 安裝 | [setup/RECOVERY.md](setup/RECOVERY.md) | 換電腦/文件遺失 |
| 操作 | [guides/OPS.md](guides/OPS.md) | Proxy 切換/Key Rotate |
| 操作 | [guides/BACKUP.md](guides/BACKUP.md) | 備份說明 |
| 操作 | [guides/SYMBOLS.md](guides/SYMBOLS.md) | 加幣種操作 |
| 操作 | [guides/TELEGRAM.md](guides/TELEGRAM.md) | Bot 完整指令 |
| 架構 | [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | AI stack 選型 |
| 架構 | [architecture/AGENTS.md](architecture/AGENTS.md) | 各 agent 職責 |
| 架構 | [architecture/ROADMAP.md](architecture/ROADMAP.md) | 未來計劃 |
| 架構 | [architecture/TAXONOMY.md](architecture/TAXONOMY.md) | 文件分類判斷樹 |
| 朋友 | [friends/INSTALL.md](friends/INSTALL.md) | 評測用指南 |

## 架構

```
推理層：Claude API（tier1 Sonnet / tier2 Haiku）
向量層：voyage-3
搜尋層：numpy cosine similarity
記憶層：jsonl + npy
```

## License

Private — 待開源
