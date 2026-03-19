# Polymarket — Claude Code 入口
> ⚠️ 此文件上限 150 行。Claude Code 自動載入。
> 最後更新：2026-03-18

## 身份
獨立預測市場交易子系統，寄生於 AXC shared_infra 但邏輯完全獨立。
詳細業務規則 → `polymarket/CORE.md`（必讀）

## Current Phase: 🟡 Paper Only
- 所有策略仲係 paper trade，未有 live
- Paper gate: 需要 48h dry-run 先可以上 live
- BTC 15M + Weather 兩條 paper tracker 獨立運行中

## 業務範圍
| 策略 | 狀態 | AI 成本 |
|------|------|---------|
| Crypto 15M | Paper | 低（triple signal: indicator + CVD + microstructure，AI fallback） |
| Weather | Paper | 零（ensemble forecast + CDF，無 AI） |
| Crypto（一般） | Paper | 有（Claude sonnet 估概率） |

**Weather 正式範圍：亞洲**（Tokyo, HK, Shanghai 為主）。
categories.py 有 21 城市含 US — US 係 paper testing，唔係正式 scope。

## 架構：14-Step Pipeline
```
pipeline.py — 主循環入口
1   ReadState        → POLYMARKET_STATE.json
2   ReplayWAL        → crash recovery
3   SafetyCheck      → circuit breaker / daily loss / cooldown
4    ScanMarkets      → Gamma API → match categories
5    CheckPositions   → sync 持倉 + PnL
5.5  MergeCheck       → detect mergeable YES+NO pairs（report only, Phase 2 執行）
6    ManagePositions  → exit triggers (drift/profit/loss/expiry)
6.5  CloseHedge       → close HL hedge for resolved/exited positions
6.7  ExecuteExits     → sell positions flagged by exit triggers（WAL-safe）
7    FindEdge         → triple signal: indicator + CVD + microstructure → AI fallback
7.3  LogicalArb       → detect pricing contradictions across related markets（零 AI）
7.5  GTOFilter        → adverse selection + Nash eq（零 AI，skip arb signals）
8    GenerateSignals  → edge > threshold → PolySignal
9    SizePositions    → binary Kelly (half Kelly × confidence × GTO)
10   ExecuteTrades    → Poly order + HL hedge (crypto_15m)
11   WriteState       → atomic write state
12   SendReports      → Telegram
```

## 獨立入口（唔經 pipeline）
| Script | 用途 | 跑法 |
|--------|------|------|
| `run_btc_paper.py` | BTC 15M paper tracker | `--predict` / `--resolve` / `--report` |
| `run_weather_paper.py` | Weather paper tracker | `--predict` / `--resolve` / `--report` / `--calibrate` |

## 文件索引
```
polymarket/
├── CORE.md              ← 業務規則（GTO、落注、架構原則）★ 必讀
├── CLAUDE.md            ← 你而家睇緊嘅嘢
├── pipeline.py          ← 主入口
├── config/
│   ├── settings.py      ← 所有常數、路徑、閾值
│   ├── params.py        ← $100 bankroll override（獨立於 AXC params.py）
│   └── categories.py    ← 市場分類 + weather cities + blocklist
├── core/context.py      ← dataclasses: PolyMarket, EdgeAssessment, PolySignal...
├── exchange/
│   ├── gamma_client.py      ← Gamma API（公開，免 auth）
│   ├── polymarket_client.py ← CLOB SDK（需 POLY_PRIVATE_KEY）
│   └── hl_hedge_client.py   ← Hyperliquid hedge（需 HL_PRIVATE_KEY）
├── strategy/
│   ├── market_scanner.py    ← scan + filter
│   ├── edge_finder.py       ← 核心 edge 偵測（triple: indicator + CVD + microstructure）
│   ├── crypto_15m.py        ← BTC 15M 指標 pipeline
│   ├── cvd_strategy.py      ← CVD divergence signal source
│   ├── microstructure_strategy.py ← volume spike mean reversion（零 AI，1 API call）
│   ├── weather_tracker.py   ← multi-model ensemble paper
│   ├── gto.py               ← GTO filter（純數學）
│   ├── logical_arb.py       ← logical arbitrage detection（negRisk + ordering）
│   └── spread_analyzer.py   ← order book 分析
├── risk/
│   ├── risk_manager.py      ← risk rules + protected_call() wrapper
│   ├── circuit_breaker.py   ← 3-state CB（CLOSED/OPEN/HALF_OPEN）per service
│   ├── position_manager.py  ← exit triggers
│   ├── position_merger.py   ← mergeable position detection（Phase 1: detect only）
│   └── binary_kelly.py      ← Kelly sizing
├── state/
│   ├── poly_state.py        ← POLYMARKET_STATE.json（atomic write）
│   └── trade_log.py         ← poly_trades.jsonl（canonical: polymarket/logs/）
├── notify/telegram.py       ← Telegram reports（HTML, 廣東話）
└── logs/                    ← 所有 log + paper trade records
```

## 依賴關係（隔離規則）
```
polymarket → shared_infra   ✅ 允許（pipeline, retry, WAL, telegram, file_lock）
polymarket → shared/ files  ✅ 允許 READ-ONLY（SCAN_CONFIG.md, news_sentiment.json, TRADE_STATE.json）
polymarket → trader_cycle   ❌ 禁止
AXC → polymarket            ❌ 禁止（唯一例外：dashboard tab，try/except lazy import）
```

**Hard coupling（已知，暫時接受）：**
- `crypto_15m.py:154` subprocess 呼叫 `scripts/indicator_calc.py`
- `cvd_strategy.py` lazy import `backtest.fetch_agg_trades`（頂層 backtest/ package，唔係 polymarket/backtest/）
- 所有入口用 `sys.path` hack 注入 `scripts/` 目錄
- `hl_hedge_client.py` 直接用 `hyperliquid-python-sdk`（唔經 trader_cycle）

## 落注規則速查
- Bankroll: **live balance** | Per bet: **1%** | Per market: **10%** | Max exposure: **30%**
- Kelly: half Kelly × confidence × GTO × **capped at 1% bankroll**
- Weather edge threshold: dynamic（tail ≤10¢ = 3%, peak >35¢ = 8%）
- Daily loss > 15% → circuit breaker（6h cooldown）
- 3 consecutive losses → circuit breaker

## 跑法
```bash
cd ~/projects/axc-trading
# Pipeline (dry-run)
PYTHONPATH=.:scripts python3 polymarket/pipeline.py --dry-run --verbose
# BTC paper
PYTHONPATH=.:scripts python3 polymarket/run_btc_paper.py --predict
PYTHONPATH=.:scripts python3 polymarket/run_btc_paper.py --resolve --report
# Weather paper
PYTHONPATH=.:scripts python3 polymarket/run_weather_paper.py --predict
PYTHONPATH=.:scripts python3 polymarket/run_weather_paper.py --resolve --report --calibrate
```

## ⚠️ Known Issues
1. ~~Trade log 路徑~~ — ✅ 已修（2026-03-18）：settings.py + trade_log.py 統一指向 `polymarket/logs/`
2. ~~CORE.md weather scope 過時~~ — ✅ 已修（2026-03-18）：加 US = paper testing only 註記
3. **indicator_calc.py 硬編碼**：`/opt/homebrew/bin/python3.11` subprocess（換機要改）
4. **HL credentials 未填**：`secrets/.env` 嘅 HL_PRIVATE_KEY + HL_ACCOUNT_ADDRESS 係空，填好先開 HEDGE_ENABLED
5. ~~Exit signals 冇執行~~ — ✅ 已修（2026-03-18）：加 ExecuteExitStep (step 6.7)
6. **Position Merger Phase 2**：on-chain CTF merge execution 未做，目前只有 detection + report

## ⛅ Weather Critical Rules（每次觸及天氣前必讀）
- **ROUND rule**：Wunderground 用四捨五入（唔係截斷）→ bucket "13°C" = actual [12.5, 13.5)
- **Resolution**: Wunderground airport station, "highest temp for ALL TIMES on this day" = 24h max
- **Trading cutoff**: 當日 local time ~21:00（endDate = noon UTC）
- **Discovery**: event slug `highest-temperature-in-{city}-on-{month}-{day}-{year}`（唔好用 tag filter）
- **Dedup**: per-cycle 同一 condition_id 只買 1 次 + per-market cap 扣減已有持倉
- **3 份城市列表**: 加新城市要改 `categories.py` + `market_scanner.py` + `weather_tracker.py`
- 詳細 rules → `~/.claude/projects/-Users-wai/memory/trading/weather_market_rules.md`

## Gotchas
- Weather ensemble 用 Open-Meteo（GFS+ECMWF+ICON = 122 members）→ ensemble counting（唔係 CDF）
- GTO live_event = 永遠 BLOCK（場內有人睇住比分，你永遠係 dumb money）
- `_SHORT_KEYWORD_LEN=4`：短 keyword 用 word boundary regex（防 "sol" match "resolve"）
- State file 喺 `shared/POLYMARKET_STATE.json`，唔係 polymarket/ 內（dashboard 要讀）

## Proxy
- AI model: `claude-sonnet-4-6` via `PROXY_BASE_URL`（同 AXC 共用 proxy）
- Temperature: 0.3（低 = 穩定概率估計）
