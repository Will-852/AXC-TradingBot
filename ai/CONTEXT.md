# OpenClaw — Agent 系統上下文
> 讀者：AI Agent
> 人類文件：docs/README.md
> 判斷樹：docs/architecture/TAXONOMY.md
> 深度分析：docs/analysis-20260310/（10 步架構分析）
> 最後更新：2026-03-10
> ⚠️ 此文件只引用 docs/，不複製內容

## 立即讀取
1. ai/MEMORY.md    — 近期狀態
2. ai/RULES.md     — 行為規則
3. ai/STRATEGY.md  — 交易策略

## 需要細節時
架構決策  → docs/architecture/ARCHITECTURE.md
Agent職責 → docs/architecture/AGENTS.md
操作指南  → docs/guides/
加幣種    → docs/guides/SYMBOLS.md（7 步 checklist）
10 步分析 → docs/analysis-20260310/FOLDER_GUIDE.md

## 系統概覽

本地智能交易監控系統。10 agents + dashboard + Telegram bot。
推理：Claude API（tier1 Sonnet / tier2 Haiku / Opus for decisions）
向量：voyage-3 | 搜尋：numpy cosine | 記憶：jsonl + npy
Proxy：https://tao.plus7.plus/v1（PROXY_API_KEY）

## 核心路徑
```
~/projects/axc-trading/
├── CLAUDE.md              ← Claude Code 自動載入（唔可移動）
├── DEV_LOG.md             ← 開發日誌
├── ai/                    ← AI Agent 上下文（你而家讀緊）
├── docs/                  ← 人類文檔（唯一真相）
│   ├── setup/             INSTALL + ENV_SETUP + RECOVERY
│   ├── guides/            OPS + BACKUP + SYMBOLS + TELEGRAM（20 個文件）
│   ├── architecture/      ARCHITECTURE + AGENTS + ROADMAP + TAXONOMY
│   ├── indicators/        指標研究 + Yunis Collection（14 個文件）
│   ├── friends/           INSTALL + .env.example（外部評測）
│   └── analysis-20260310/ 10 步架構分析（本次）
├── agents/                ← 10 agents，各自 SOUL.md
├── scripts/               ← Python/Bash 執行層（23 root + trader_cycle）
│   └── trader_cycle/      ← ⭐ 自動交易引擎（16 步 pipeline）
│       ├── strategies/    Range + Trend + Mode Detector + Evaluate
│       ├── exchange/      Aster / Binance / HyperLiquid + market_data
│       ├── risk/          風控 + 移動止損 + 倉位管理
│       ├── state/         狀態管理 + SCAN_CONFIG writer
│       └── config/        pairs.py + settings.py
├── backtest/              ← 回測系統（engine + compare_configs）
├── config/                ← params.py + modes/ + user_params.py
├── secrets/.env           ← 9 API keys（+Binance）
├── shared/                ← Agent 間通信（SIGNAL.md, TRADE_STATE.md, prices_cache.json）
├── memory/                ← RAG 記憶系統
├── logs/                  ← 日誌 + 心跳
└── backups/               ← auto zip（keep 10）
```

## 交易對（7 pairs, 3 groups）

| Pair | Aster | Binance | HL | Group | Priority |
|------|-------|---------|----|-------|----------|
| BTCUSDT | ✅ | ✅ | ✅ | crypto_correlated | 4 |
| ETHUSDT | ✅ | ✅ | ✅ | crypto_correlated | 3 |
| SOLUSDT | - | ✅ | ✅ | crypto_correlated | 3 |
| XRPUSDT | ✅ | - | - | crypto_independent | 2 |
| POLUSDT | - | ✅ | - | crypto_independent | 2 |
| XAGUSDT | ✅ | - | - | commodity | 1 |
| XAUUSDT | ✅ | - | - | commodity | 1 |

Position groups: 每組 max 1 倉，最多 3 倉同時。
market_data.py 自動路由：Aster pairs → Aster API，Binance pairs → Binance API。

## 兩層掃描系統

```
Layer 1: async_scanner.py（常駐 daemon）
  9 exchanges × 20s each = 180s full rotation
  讀 params.py ASTER_SYMBOLS / BINANCE_SYMBOLS / HL_SYMBOLS
  寫 shared/prices_cache.json + shared/SCAN_CONFIG.md

Layer 2: light_scan.py（3 min cron）
  Aster only（5 pairs: BTC/ETH/XRP/XAG/XAU）
  4 triggers: PRICE(>0.6%) / VOLUME(>175%) / SR_ZONE / FUNDING(>0.18%)
  寫 shared/SCAN_CONFIG.md

兩層獨立。async_scanner 係主力，light_scan 係補充。
```

## Trader Cycle（16 步 pipeline）⭐

```
Step  1: LoadState        — 讀 TRADE_STATE.md + open positions
Step  2: SafetyCheck      — circuit breaker + cooldown
Step  3: NoTradeCheck     — volume/funding filter + position group limits
Step  4: FetchMarketData  — ticker + funding（Aster/Binance per pair）
Step  5: CalcIndicators   — calc_indicators() for 4H + 1H
Step  6: DetectMode       — 5 票制 mode detection（RANGE/TREND/UNKNOWN）
Step  7: RangeStrategy    — BB/RSI/STOCH/MACD/ADX entry signals
Step  8: TrendStrategy    — EMA cross/RSI/ADX/MACD entry signals
Step  9: NewsFilter       — sentiment from shared/news_sentiment.json
Step 10: EvaluateSignals  — score + rank + select best signal
Step 11: PositionSizer    — ATR-based SL/TP + Kelly-inspired sizing
Step 12: AdjustPositions  — trailing SL, TP extension, early exit
Step 13: ExecuteTrade     — 7-step order sequence
Step 14: ManagePositions  — max hold, funding cost check
Step 15: WriteState       — update TRADE_STATE.md + SCAN_CONFIG.md
Step 16: SendAlerts       — Telegram notifications
```

觸發：LaunchAgent interval。每個 cycle 處理所有 7 pairs。
鎖機制：fcntl.flock 防止同 scanner 同時跑。

## 十個 Agents

| Agent | Model | Role | 實際狀態 |
|-------|-------|------|----------|
| main | Haiku | Telegram 介面 + 指令路由 | 活躍（73 sessions） |
| aster_scanner | Python | Aster DEX 掃描 | 被 async_scanner 取代 |
| aster_trader | Python | Aster DEX 交易 | 被 trader_cycle 取代 |
| binance_scanner | — | Binance 掃描 | 整合入 async_scanner |
| binance_trader | — | Binance 交易 | 整合入 trader_cycle |
| heartbeat | Python | 15 min 健康檢查 | 活躍 |
| haiku_filter | Haiku | 信號壓縮 <300 字 | 原始設計，trader_cycle 取代 |
| analyst | Sonnet | 深度分析 | 原始設計，trader_cycle 取代 |
| decision | Opus | 最終 GO/HOLD/ABORT | 原始設計，trader_cycle 取代 |
| news_agent | Haiku | RSS + 情緒分析 | 活躍 |

⚠️ SOUL.md 描述嘅 Agent Pipeline（scanner→filter→analyst→decision→trader）
同實際 trader_cycle 16-step pipeline 係兩套系統。trader_cycle 已取代 agent pipeline
做交易決策。Agent pipeline 係原始設計願景。

## Model 成本分級
```
Opus     → decision agent（最終決策，原始設計）
Sonnet   → analyst agent（深度分析，原始設計）
Haiku    → main, haiku_filter, news_sentiment（高頻互動）
Python   → scanner, trader_cycle, heartbeat（確定性 + 零 AI cost）
```

## Scripts（關鍵）

### 根目錄（23 個）
| Script | 用途 |
|--------|------|
| async_scanner.py | v7 九路輪轉掃描器（9 exchanges × 20s） |
| light_scan.py | 3 min Aster 輕量掃描（5 pairs） |
| indicator_calc.py | 技術指標計算（25+ indicators, 支持 aster/binance） |
| tg_bot.py | Telegram Bot（69KB，自然語言 + 14 slash commands） |
| slash_cmd.py | 14 個 slash commands（零 AI，純 Python） |
| dashboard.py | ICU Dashboard（port 5555，105KB 最大文件） |
| heartbeat.py | 15 min 健康檢查 |
| news_scraper.py | RSS 新聞收集（CoinTelegraph + CoinDesk） |
| news_sentiment.py | Claude Haiku 情緒分析 → shared/news_sentiment.json |
| public_feeds.py | 9 exchange API adapters |
| weekly_strategy_review.py | 每週回顧 → ai/STRATEGY.md |
| load_env.sh | LaunchAgent .env wrapper |
| backup_agent.sh | git + push + zip backup |
| health_check.sh | 7 類別系統診斷 |

### trader_cycle/（5 子目錄，~30 個文件）
| 子目錄 | 關鍵文件 | 用途 |
|--------|---------|------|
| config/ | pairs.py, settings.py | 7 pairs 定義 + 所有常數 |
| strategies/ | range_strategy.py, trend_strategy.py, mode_detector.py, evaluate.py | 策略邏輯 |
| exchange/ | market_data.py, aster_client.py, execute_trade.py, position_sync.py | 交易所接口 |
| risk/ | risk_manager.py, position_sizer.py | 風控 + 倉位計算 |
| state/ | state_manager.py, scan_config_writer.py | 狀態讀寫 |

## Backtest 系統
> 完整指引（CLI + gotchas + pass/fail 標準）：docs/guides/BACKTEST.md
> ⚠️ **Model 路由**：數據分析用 Sonnet subagent，代碼/決策用 Opus main context

**標準流程（每次必跟）：**
```
Step 1  run_backtest.py     確認 baseline（單次回測）
Step 2  grid_search.py      搵最佳參數（1-2 params sweep）
Step 3  validate.py         驗證結果（must-use: monte-carlo + walk-forward + heatmap）
Step 4  validate.py         可選驗證（noise / delay / dsr）
Step 5  validate.py all    一次跑全部 must-use
Step 6  改 config/params.py 全部 PASS + 記錄 DEV_LOG + git commit
```

**文件：**
```
backtest/
├── engine.py             — 核心模擬器（1H+4H MTF, signal_delay support）
├── fetch_historical.py   — Binance klines + CSV cache
├── grid_search.py        — 參數優化（8 params, ProcessPoolExecutor）
├── validate.py           — 6 驗證工具（monte-carlo/walk-forward/heatmap/noise/delay/dsr）
├── optimizer.py          — LHS 優化器
├── run_backtest.py       — 單 pair CLI
├── compare_configs.py    — A/B configs 對比
└── data/                 — CSV cache + grid search JSON + heatmap PNG
```
特性：No look-ahead bias, SL slippage 0.02%, cluster-adjusted win rate。

## LaunchAgents（常駐服務）
| Service | 狀態 |
|---------|------|
| ai.openclaw.scanner | KeepAlive，load_env.sh wrapper |
| ai.openclaw.telegram | KeepAlive，load_env.sh wrapper |
| ai.openclaw.gateway | KeepAlive |
| ai.openclaw.tradercycle | interval |
| ai.openclaw.heartbeat | interval |
| ai.openclaw.lightscan | interval（Aster 輕量掃描） |
| ai.openclaw.report | interval |
| ai.openclaw.strategyreview | 每週一 10:00 HKT |
| ai.openclaw.newsagent | 每 15 分鐘 |

## Config 架構
```
config/params.py          ← 共用參數（ASTER_SYMBOLS, BINANCE_SYMBOLS, 指標參數, profiles）
config/user_params.py     ← collaborator override（gitignored）
config/modes/             ← RANGE.md, TREND.md, VOLATILE.md（人類可讀規則）
trader_cycle/config/
├── settings.py           ← trader_cycle 常數（PAIRS, POSITION_GROUPS, 風控閾值）
└── pairs.py              ← 7 pairs 定義（group, precision, overrides）
```
params.py 嘅 ACTIVE_PROFILE 透過 settings.py 覆蓋策略常數。

## Gotchas
- 改參數只改 config/params.py，唔改 scripts
- 加幣種要改 7 個位（見 docs/guides/SYMBOLS.md）
- tier2 Haiku 處理唔到 >10K system prompt
- Skill description 空白 = 靜默失敗
- fcntl.flock 防止 scanner 同 tradercycle 同時執行
- async_scanner 用直接 HTTP（AsterClient 冇 get_price()）
- asyncio.wait_for + run_in_executor: timeout 只取消 coroutine 唔取消 thread
- market_data.py 根據 ASTER_SYMBOLS 路由 API（Aster pair → Aster, 其他 → Binance）
- MAX_CRYPTO_POSITIONS / MAX_XAG_POSITIONS 係 dead code（實際由 POSITION_GROUPS 控制）
- SOUL.md Agent Pipeline 同 trader_cycle pipeline 係兩套系統（trader_cycle 為主）

## Telegram
- @AXCTradingBot → tg_bot.py — trading interface
- @axccommandbot → openclaw-gateway — system commands
- Chat ID: 2060972655
- HTML parse_mode，廣東話口語

## 搵舊記憶
```
python3 ~/projects/axc-trading/memory/retriever.py "問題"
```
