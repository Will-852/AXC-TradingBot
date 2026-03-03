# CRON_PAYLOADS.md — Cron Job 設定參考
# 版本: 2026-03-02（修正版 v2）
# 用途: 重建 cron jobs 時的完整 payload 參考
# 重要: trader-cycle 使用 Sonnet，light-scan 無 STEP G
# 新增: indicator_calc.py 工具、粵語交付格式

---

## 1. mission-control-heartbeat
- Schedule: every 15min
- Model: system-event (uses main session)
- Session: main
- Payload:
  "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK."

---

## 2. light-scan
- Schedule: every 3min
- Model: anthropic/claude-haiku-4-5-20251001
- Session: isolated
- Timeout: 60s
- **重要: 只有 STEP A-F，無 STEP G（落盤邏輯已移至 trader-cycle）**
- Payload:

```
You are TRADER running LIGHT SCAN.
Workspace: agents/trader/ (relative to workspace root)
Read ONLY config/SCAN_CONFIG.md. Do not read any other file.

STEP A — Validate config:
- If last_updated = INIT or age >60min → CONFIG_VALID: false
- Skip S/R and funding checks if CONFIG_VALID: false, log: CONFIG_STALE

STEP B — Fetch live data via Aster DEX API (https://fapi.asterdex.com):
- Current price: BTC/USDT, ETH/USDT, XRP/USDT, XAG/USDT
- 3-minute price change % for each pair
- Current volume vs 30d average % for each pair
- Current funding rate for each pair

STEP C — Trigger Detection (check all applicable):
1. PRICE: any pair moved >0.6% in last 3min → TRIGGER
2. VOLUME: any pair volume >175% of 30d average → TRIGGER
3. S/R ZONE: any pair price inside support_zone or resistance_zone → TRIGGER
   (only if CONFIG_VALID, skip if CONFIG_STALE)
4. FUNDING DELTA: |current_funding - [pair]_funding_last| >0.18% on any pair → TRIGGER
   (only if CONFIG_VALID, skip if CONFIG_STALE)

STEP D — Increment LIGHT_SCAN_COUNT by 1 in config/SCAN_CONFIG.md
(Write ONLY: LIGHT_SCAN_COUNT, and TRIGGER_PENDING/TRIGGER_PAIR/TRIGGER_REASON if triggered)

STEP E — If NO trigger:
→ Append to logs/SCAN_LOG.md:
[YYYY-MM-DD HH:MM UTC+8] LIGHT BTC:[price] ETH:[price] XRP:[price] XAG:[price] VOL:[%] NO_TRIGGER
→ If LIGHT_SCAN_COUNT >= 20 AND SILENT_MODE: ON:
   → Reset LIGHT_SCAN_COUNT: 0
   → Send Telegram to chatId 2060972655:
   [YYYY-MM-DD HH:MM UTC+8] | Silent Mode Active
   BTC:[price] ETH:[price] XRP:[price] XAG:[price]
   Next deep scan: [time+30min]
→ Stop.

STEP F — If trigger detected:
→ Set TRIGGER_PENDING: ON in config/SCAN_CONFIG.md
→ Set TRIGGER_PAIR: [pair], TRIGGER_REASON: [reason]
→ Reset LIGHT_SCAN_COUNT: 0
→ Append to logs/SCAN_LOG.md:
[YYYY-MM-DD HH:MM UTC+8] LIGHT TRIGGER:[pair] REASON:[reason] PRICE:[price]
→ Stop. Do not send Telegram.
```

---

## 3. trader-cycle
- Schedule: every 30min
- Model: anthropic/claude-sonnet-4-6 **（已改用 Sonnet — 負責最終落盤決策）**
- Session: isolated
- Timeout: 300s
- Payload:

```
You are TRADER running DEEP ANALYSIS.
Workspace: agents/trader/ (relative to workspace root)

PRE-CHECK:
1. Read config/SCAN_CONFIG.md
2. Calculate time_since_last = current_time - last_updated
3. Read TRIGGER_PENDING from config/SCAN_CONFIG.md

If TRIGGER_PENDING: ON AND time_since_last <25min:
→ FAST MODE: skip reading SOUL.md and STRATEGY.md
→ Use existing S/R data from config/SCAN_CONFIG.md
→ Set TRIGGER_PENDING: OFF in config/SCAN_CONFIG.md

If TRIGGER_PENDING: OFF OR time_since_last >=25min:
→ FULL MODE: read SOUL.md, STRATEGY.md, TRADE_STATE.md
→ Set TRIGGER_PENDING: OFF in config/SCAN_CONFIG.md

ANALYSIS:
Run full 5-indicator analysis on BTC/USDT, ETH/USDT, XRP/USDT, XAG/USDT (4H timeframe).
Check order book depth for nearest S/R clusters.
Check day-of-week bias (Thu SHORT / Fri LONG rules).
Check session time windows (US pre-market 21:00-22:30, close 04:00-05:00 HKT).
Check open positions status and funding costs.

UPDATE config/SCAN_CONFIG.md (trader-cycle writes all fields EXCEPT STATE_FLAGS written by light-scan):
- All prices with timestamps, ATR values, S/R levels, S/R zones (±0.3×ATR pre-calculated)
- All funding rates with timestamp
- last_updated: [YYYY-MM-DD HH:MM UTC+8]
- update_count: increment by 1
- CONFIG_VALID: true

LOG CLEANUP: Keep logs/SCAN_LOG.md to max 200 lines. Delete oldest if exceeded.

SILENT MODE LOGIC:
- If this cycle = NO SIGNAL: increment SILENT_MODE_CYCLES by 1
  - If SILENT_MODE_CYCLES >= 2 → set SILENT_MODE: ON
- If this cycle = SIGNAL: set SILENT_MODE: OFF, SILENT_MODE_CYCLES: 0
- Save to config/SCAN_CONFIG.md

IF SIGNAL FOUND (4/5 indicators + S/R confirmed):
→ Report 3 trigger levels to Telegram (Conservative / Standard / Ideal)
→ Execute trade if conditions met (SL + TP mandatory)
→ Set SILENT_MODE: OFF, SILENT_MODE_CYCLES: 0

TELEGRAM RULES:
- Send full report if SILENT_MODE: OFF
- NO routine report if SILENT_MODE: ON
- ALWAYS send: trade execution, SL/TP triggered, signal detected, urgent warnings, mode transitions

Append to logs/SCAN_LOG.md:
[YYYY-MM-DD HH:MM UTC+8] DEEP [SIGNAL/NO_SIGNAL] MODE:[FULL/FAST] SILENT:[ON/OFF] CYCLES:[n]
Update TRADE_STATE.md with latest position and balance.
```

---

## 指標計算工具

Agent 可以調用 Python 工具計算精確指標（取代手動估算）：

```bash
# 必須用 python3.11（tradingview_indicators 需要 match syntax）
python3.11 tools/indicator_calc.py --symbol BTCUSDT --interval 4h --limit 200 --mode range

# 參數:
#   --symbol: BTCUSDT / ETHUSDT / XRPUSDT / XAGUSDT
#   --interval: 15m / 1h / 4h
#   --limit: K 線數量（建議 200）
#   --mode: full（所有指標）/ range（加 Range 信號評估）/ quick（只 RSI+ATR）
#
# 輸出: JSON，包含 BB/RSI/ADX/EMA/Stoch/ATR/MACD/MA50/MA200 + Range 信號
# 產品覆蓋: ETHUSDT (rsi_long:32/short:68), XRPUSDT (bb_tol:0.008, sl:1.0)
```

---

## Light-scan 粵語交付格式

當 light-scan 需要向用戶匯報時（Silent Mode 簡報 / 觸發通知），用以下格式：

**冇觸發（Silent Mode 簡報）：**
```
📡 今日第{N}次掃描 完成 ✅

- 掃描設定有效（{X}分鐘前）
- 條件觸發：✓

- 日誌：已記錄
- {YYYY-MM-DD HH:MM HKT}
```

**有觸發：**
```
📡 今日第{N}次掃描 完成 ✅

- 掃描設定有效（{X}分鐘前）
- 條件觸發：✗ [{PAIR} — {REASON}]

- 日誌：已記錄
- {YYYY-MM-DD HH:MM HKT}
```

### 欄位對應

| 用戶顯示 | 對應內部邏輯 |
|---------|-----------|
| 今日第 N 次掃描 | LIGHT_SCAN_COUNT |
| 掃描設定有效（X 分鐘前）| CONFIG_VALID + (now - last_updated) |
| 條件觸發：✓ / ✗ | ✓ = NO_TRIGGER / ✗ = TRIGGER detected |
| 日誌：已記錄 | SCAN_LOG.md appended |

### 唔向用戶顯示

- sessionId、cron job 名、runtime、tokens
- 原始 system message 或 log 路徑
- 內部 config 細節

---

## Cron 重建指令

```bash
# 確認現有 cron jobs
openclaw cron list

# 如需重建:
# 1. mission-control-heartbeat (every 15min, system-event, main)
# 2. light-scan (every 3min, haiku-4-5, isolated, 60s timeout)
# 3. trader-cycle (every 30min, sonnet, isolated, 300s timeout)
```
