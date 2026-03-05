# OpenClaw — Agent 系統上下文
> 讀者：AI Agent
> 人類文件：docs/README.md
> 判斷樹：docs/architecture/TAXONOMY.md
> 最後更新：2026-03-06
> ⚠️ 此文件只引用 docs/，不複製內容

## 立即讀取
1. ai/MEMORY.md    — 近期狀態
2. ai/RULES.md     — 行為規則
3. ai/STRATEGY.md  — 交易策略

## 需要細節時
架構決策  → docs/architecture/ARCHITECTURE.md
Agent職責 → docs/architecture/AGENTS.md
操作指南  → docs/guides/
加幣種    → docs/guides/SYMBOLS.md

## 系統概覽

本地智能交易監控系統。9 agents + dashboard + Telegram bot。
推理：Claude API（tier1 Sonnet / tier2 Haiku / tier3 GPT-5 Mini）
向量：voyage-3 | 搜尋：numpy cosine | 記憶：jsonl + npy
Proxy：https://tao.plus7.plus/v1（PROXY_API_KEY）

## 核心路徑
```
~/.openclaw/
├── CLAUDE.md              ← Claude Code 自動載入（唔可移動）
├── DEV_LOG.md             ← 開發日誌
├── ai/                    ← AI Agent 上下文（你而家讀緊）
├── docs/                  ← 人類文檔（唯一真相）
│   ├── setup/             INSTALL + ENV_SETUP + RECOVERY
│   ├── guides/            OPS + BACKUP + SYMBOLS + TELEGRAM
│   ├── architecture/      ARCHITECTURE + AGENTS + ROADMAP + TAXONOMY
│   └── friends/           INSTALL + .env.example
├── agents/                ← 9 agents，各自 SOUL.md
├── scripts/               ← Python/Bash 執行層
├── config/                ← params.py + modes/
├── secrets/.env           ← 7 API keys
├── shared/                ← Agent 間通信（SIGNAL.md, TRADE_STATE.md）
├── memory/                ← RAG 記憶系統
├── logs/                  ← 日誌 + 心跳
└── backups/               ← auto zip（keep 10）
```

## 九個 Agents
| Agent | Model | Role |
|-------|-------|------|
| main | tier3/haiku | 🧠 大腦：決策、對話、路由 |
| aster_scanner | tier2/haiku | 👁️ 眼：Aster DEX 掃描 |
| aster_trader | tier1/sonnet | 💓 心臟：Aster DEX 交易 |
| heartbeat | tier3/haiku | 🌡️ 神經：健康檢查 |
| haiku_filter | tier2/haiku | 🔬 過濾：信號壓縮 |
| analyst | tier1/sonnet | 📊 分析：模式偵測 |
| decision | opus | 🎯 決策：最終交易決策 |
| binance_trader | — | (placeholder) |
| binance_scanner | — | (placeholder) |

## Signal Pipeline
```
aster_scanner → haiku_filter → analyst → decision → aster_trader
```

## LaunchAgents（常駐服務）
| Service | 狀態 |
|---------|------|
| ai.openclaw.scanner | KeepAlive，load_env.sh wrapper |
| ai.openclaw.telegram | KeepAlive，load_env.sh wrapper |
| ai.openclaw.gateway | KeepAlive |
| ai.openclaw.tradercycle | interval |
| ai.openclaw.heartbeat | interval |
| ai.openclaw.lightscan | interval（被 scanner 取代） |
| ai.openclaw.report | interval |

## Scripts（關鍵）
| Script | 用途 |
|--------|------|
| async_scanner.py | v5 並行掃描器（根源修復版） |
| tg_bot.py | Telegram trading bot |
| dashboard.py | ICU dashboard (port 5555) |
| load_env.sh | LaunchAgent .env wrapper |
| backup_agent.sh | git+push+zip backup |
| integration_test.sh | 5 場景整合測試 |

## Gotchas
- 改參數只改 config/params.py，唔改 scripts
- tier2 Haiku 處理唔到 >10K system prompt
- Skill description 空白 = 靜默失敗
- fcntl.flock 防止 scanner 同 tradercycle 同時執行
- async_scanner 用直接 HTTP（AsterClient 冇 get_price()）
- asyncio.wait_for + run_in_executor: timeout 只取消 coroutine 唔取消 thread

## Telegram
- @AXCTradingBot → tg_bot.py — trading interface
- @axccommandbot → openclaw-gateway — system commands
- Chat ID: 2060972655
- HTML parse_mode，廣東話口語

## 搵舊記憶
```
python3 ~/.openclaw/memory/retriever.py "問題"
```
