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
git clone https://github.com/Will-852/openclaw ~/.openclaw
cd ~/.openclaw
pip3 install -r requirements.txt --break-system-packages
cp docs/friends/.env.example secrets/.env
# 填入 API keys
python3 scripts/dashboard.py
# 打開 http://127.0.0.1:5555
```

## 文件索引

| 文件 | 用途 |
|------|------|
| [安裝指南](setup/INSTALL.md) | 完整安裝步驟 |
| [環境變數](setup/ENV_SETUP.md) | API keys 設定 |
| [災難恢復](setup/RECOVERY.md) | 換電腦/文件遺失 |
| [維運指南](ops/OPS_GUIDE.md) | Proxy 切換/Key Rotate |
| [備份機制](ops/BACKUP.md) | 備份說明 |
| [架構決策](architecture/ARCHITECTURE.md) | AI stack 選型 |
| [Agent 說明](architecture/AGENTS.md) | 各 agent 職責 |
| [發展路線](architecture/ROADMAP.md) | 未來計劃 |
| [Telegram 指令](telegram/TELEGRAM.md) | Bot 完整指令 |
| [朋友安裝](friends/INSTALL.md) | 評測用指南 |

## 架構

```
推理層：Claude API（tier1 Sonnet / tier2 Haiku）
向量層：voyage-3
搜尋層：numpy cosine similarity
記憶層：jsonl + npy
```

## License

Private — 待開源
