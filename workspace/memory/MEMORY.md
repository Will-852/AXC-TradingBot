# MEMORY.md — OpenClaw 系統記憶
# 版本: 2026-03-02（修正版）
# 維護: memory-keeper agent
# 位置: {ROOT}/memory/MEMORY.md

---

## 系統總覽

- 平台: OpenClaw AI Trading System
- 交易所: Aster DEX (LIVE)
- API: https://fapi.asterdex.com
- Workspace: /Users/wai/.openclaw/workspace/
- Gateway: ws://127.0.0.1:18789
- Telegram chatId: 2060972655
- 時區: UTC+8 (HKT)

---

## 核心設計原則（2026-03-02 重大發現）

> **100% 規則化嘅邏輯用 Python，唔用 LLM**

- 交易策略、指標計算、風控規則全部係數學公式 + if-else
- LLM 只用於需要「理解」嘅任務（自然語言分析、非結構化判斷）
- light-scan + trader-cycle 全部改用 Python scripts
- 日成本由 ~$1.59 → $0.00（100% Python 自動化，零 LLM 消耗）
- 所有 Python 工具放 tools/ 目錄，模組化設計，strategy 可插拔

---

## 知識庫

| 檔案 | 位置 | 內容 |
|------|------|------|
| TRADING_BOT_PATTERNS.md | knowledge/ | freqtrade + TradingAgents 分析（10 章） |
| OPENCLAW_OPS_PATTERNS.md | knowledge/ | Agent 運維最佳實踐 + 知識管理架構 |

來源 1: freqtrade + TradingAgents（開源 trading bot 分析）
收穫: retry backoff、unfilled order management、4-layer stoploss、BM25 memory
詳見: PATH_KB_TRADING_PATTERNS

來源 2: 社群實戰分享（AI Agent 穩定運作指南）
收穫: Skill > 即興、持久記憶系統、PARA + Zettelkasten、免費工具生態
詳見: PATH_KB_OPS_PATTERNS

---

## 帳戶狀態

- 餘額: ~$99.87
- 起始資金: $60.00
- 總回報: +66.5%
- 開倉: XAG/USDT LONG @ $94.30 (SL $93.36, TP $103.00)

---

## 系統歷史

- 2026-02-28: 系統建立，$60 起始資金
- 2026-02-28: XRP/USDT LONG 開倉 $1.3473 → SL $1.3263，虧 $0.084
- 2026-02-28: XAG/USDT LONG 開倉 $94.30（SL $93.36，TP $103.00）
- 2026-02-28 22:04: 帳戶 $99.91（+66.5%），XAG 倉位輕微浮虧
- 2026-03-01: Session 記錄建立，架構設計完成
- 2026-03-01: light-scan STEP G 移除，trader-cycle 改用 Sonnet
- 2026-03-01: claude-3-haiku-20240307 確認 404，禁止使用
- 2026-03-02: 完整 workspace rebuild + recovery merge
- 2026-03-02: 根目錄檔案修復（HEARTBEAT/IDENTITY/USER/TOOLS 由空白→正確內容）
- 2026-03-02: 建立 skills/WORKSPACE_OPS.md — 工作區安全修改協議（OP-1~OP-7）
- 2026-03-02: 建立 tools/indicator_calc.py — BB/RSI/ADX/EMA/Stoch/ATR/MACD 計算器（Python 3.11）
- 2026-03-02: 整合 Range Strategy 改進（R0+R1+R2 前置 + BB/Stoch 信號）入 core/STRATEGY.md
- 2026-03-02: 加入粵語交付格式到 CRON_PAYLOADS.md
- 2026-03-02: 3 個 Cron Jobs 上線（heartbeat/light-scan/trader-cycle）
- 2026-03-02: Aster DEX 確認 XAG symbol = XAGUSDT（唔係 XAGUSD）
- 2026-03-02: tradingview_indicators 需要 python3.11（python3.9 語法唔支援 match）
- 2026-03-02: Telegram bot token 失效（需用戶重建）
- 2026-03-02: Provider = Tier 重構（sonnet-4-6-tier2 → tier1, haiku-45-tier2 → tier2）
- 2026-03-02: Light-scan 由 LLM agent 改為 Python script（解決 proxy 66s timeout 問題）
- 2026-03-02: Light-scan launchd service 上線 (ai.openclaw.lightscan)
- 2026-03-02: Trader-cycle 模型改為 tier1/claude-sonnet-4-6
- 2026-03-02: 重大發現 — 交易策略 100% 規則化，全部可用 Python 取代 LLM
- 2026-03-02: trader_cycle Python package Phase 1 完成（Foundation + Analysis）
- 2026-03-02: trader_cycle Python package Phase 2 完成（Strategy Evaluation）
- 2026-03-02: 建立 knowledge/TRADING_BOT_PATTERNS.md（freqtrade + TradingAgents 分析）
- 2026-03-02: SCAN_CONFIG.md CONFIG_VALID 首次由 false → true（S/R zones 啟用）
- 2026-03-02: 日成本由 ~$1.59 → ~$0.00
- 2026-03-02: Trader-cycle OpenClaw cron (f7486cbf) 正式停用
- 2026-03-02: Trader-cycle Python launchd 部署（ai.openclaw.tradercycle, DRY_RUN）
- 2026-03-02: Light-scan plist 修正 python3.9 → python3.11
- 2026-03-02: Phase 4 完成 — 全部 Python services 用 launchd 運行
- 2026-03-02: Heartbeat 由 LLM systemEvent 改為 Python script（零 LLM 成本）
- 2026-03-02: Heartbeat Python launchd 部署 (ai.openclaw.heartbeat)
- 2026-03-02: OpenClaw heartbeat cron (df0828ad) 正式停用
- 2026-03-02: Memory-keeper 加入 trader-cycle pipeline (WriteMemoryStep)
- 2026-03-02: 日成本由 ~$0.02 → $0.00（100% Python，零 LLM 自動消耗）

---

## Active Services

| 名稱 | 頻率 | 方式 | Service | 速度 |
|------|------|------|---------|------|
| **heartbeat** | **每15分鐘** | **Python 3.11 script** | **macOS launchd** | **~0.5s** |
| **light-scan** | **每3分鐘** | **Python 3.11 script** | **macOS launchd** | **~2s** |
| **trader-cycle** | **每30分鐘** | **Python 3.11 script** | **macOS launchd** | **~5s** |

### Python Services（全部唔用 LLM）
| Service | Script | LaunchAgent | Python |
|---------|--------|-------------|--------|
| heartbeat | `tools/heartbeat.py` | `ai.openclaw.heartbeat` | 3.11 |
| light-scan | `tools/light_scan.py` | `ai.openclaw.lightscan` | 3.11 |
| trader-cycle | `tools/trader_cycle/main.py` | `ai.openclaw.tradercycle` | 3.11 |

### Trader-Cycle Python Pipeline（Phase 2 完成）
```
read_state → safety_check → fetch_market → calc_indicators
→ detect_mode → no_trade_check → evaluate_signals
→ select_signal → size_position → write_state → write_memory → send_reports
```
- 12 步 pipeline，~5 秒完成（含 write_memory step）
- Mode detection: 5 指標投票（RSI/MACD/Volume/MA/Funding）
- Range strategy: R0+R1 前置 → C1-C4 entry（reuse indicator_calc.py）
- Trend strategy: 4 KEY 全中（MA+MACD+RSI+Price pullback）+ day-of-week bias
- Risk: circuit breaker 25% single / 15% daily, cooldown 2→30min / 3→2hr
- Position sizer: ATR-based SL, BB/SR-based TP, funding cost 調整

### 進度
- Phase 1: ✅ Foundation + Analysis（DRY_RUN）
- Phase 2: ✅ Strategy Evaluation（DRY_RUN）
- Phase 3: ⬜ Live Trading（aster_client.py + auth API）
- Phase 4: ✅ LaunchAgent 部署 + 移除舊 OpenClaw cron

已刪除: trader-cycle (old 10min, Sonnet cron f7486cbf), trading-8h-stop, silent-monitor

---

## 架構: Adaptive Sampling

- light-scan (3min): 純掃描 STEP A-F，觸發時設 TRIGGER_PENDING
- trader-cycle (30min): 深度分析，更新 SCAN_CONFIG.md，負責落盤決策
- FAST MODE: 早期觸發，跳過 SOUL/STRATEGY 讀取（~3k token 省）
- SILENT MODE: 連續 2 次 NO SIGNAL 後，暫停例行 Telegram

---

## 模型路由 — Python First 架構

### 核心原則
> 100% 規則化 → Python | 需要「理解」→ LLM

### 模型使用
| 角色 | 方式 | 模型 | 成本 |
|------|------|------|------|
| light-scan | Python script | N/A | $0.00 |
| trader-cycle | Python script | N/A | $0.00 |
| heartbeat | Python script | N/A | $0.00 |
| NEWS 分析（如啟用） | LLM | claude-haiku-4-5 | per-use |

### 禁用模型
- claude-3-haiku-20240307: ❌ 404 禁止使用
- Gemini Flash: 暫停（rate limit）
- Opus: 完全移除

### 每日成本估算
- 所有 Python services: $0.00
- heartbeat systemEvent: $0.00
- **總計: ~$0.00/日**（was ~$1.59/日）

---

## 已知決策

### 架構決策
- PATH_CONFIG.md = 唯一路徑定義，禁止寫死路徑
- MODEL_ROUTER.md = 唯一模型控制
- Agent 唔綁死模型，只管邏輯
- 所有外部 HTTP 由 Python scripts 處理，agent 只讀寫 MD
- 新聞係人手提供（NEWS: 前綴），唔自動爬取，結果唔儲存

### 已確認無效 Endpoints
- globalLongShortAccountRatio → 404
- takerlongshortRatio → 404
- topLongShortAccountRatio → 404

---

## 風控參數

- Capital per trade: Range/Trend 2%, Scalp 1%, BS P1 2.5%, BS P2 5%
- SL: 1.5×ATR (正常), 2% fixed (Black Swan)
- 槓桿: Range 8x, Trend 7x, Scalp 5x, BS 5x
- 日虧損限: 15% → 停止所有交易
- 連續虧損: 2→30min, 3→2h 暫停
- 最多倉位: 2 crypto (BTC+ETH = 1 group) + 1 XAG

---

## Light-scan 觸發閾值

1. PRICE: >0.6% move in 3min
2. VOLUME: >175% of 30d average
3. S/R ZONE: price inside ±0.3×ATR of support/resistance
4. FUNDING DELTA: |current - last| >0.18%

---

## Silent Mode

- 進入: 連續 2 次 NO SIGNAL trader-cycle
- 退出: 任何觸發條件，或 "exit silent mode"
- 期間: light-scan 每 20 次（60分鐘）發簡短報告
- trader-cycle: 照跑，但唔發例行 Telegram

---

## 用戶預批准

- XAG LONG: ✅ 已執行
- XRP SHORT at $1.35: ✅ 預批准（需監控 resistance）
- Black Swan Phase 1: 需用戶確認才入場
- Black Swan Phase 2: P1 條件達到後自動執行

---

## 已知限制

- 勝率未知（只有 1 筆已平倉 — 需 50 筆）
- XAG 週末: 極低流動性（ask ~10 XAG vs 1.059 position）
- Monitor agent: 未啟用（無 setup）
- Scalp mode: 未啟用（需 Monitor）
- Kelly Criterion: 使用保守 1.5-2% 直到 50-trade 數據

---

## 待確認事項

- XAG/USDT 倉位當前狀態（需從 Aster DEX 查詢）
- 帳戶實際餘額（需查詢）
- OpenClaw cron jobs 是否已啟動
- openclaw.json 是否已修復（重啟電腦後損壞）

---

## 重要規範

- Telegram 匯報一律繁體中文
- 時間戳格式: YYYY-MM-DD HH:MM UTC+8
- 落盤後 30 秒確認 SL/TP
- 每次 trader-cycle 發 Telegram 匯報（除非 SILENT MODE）

---

## 上次更新

2026-03-02 20:50 UTC+8（100% Python 完成 — heartbeat Python 化 + memory-keeper pipeline step）

## System History
- 2026-03-03 02:57: 信號: BTCUSDT LONG (test_injection) entry=$68920.00 [DRY_RUN]
