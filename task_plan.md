# Task Plan: AXC Event-Driven 升級 v3 (FINAL)

## Goal
保留 20s×9 exchange REST poll（廣度），加 WebSocket 實時 BTC stream（深度）→ Redis Streams → Indicator Engine（多 timeframe 即時計算）→ trader_cycle 從 30min 加速到 15min（讀 cache 唔再自己 fetch）。

## Current Phase
Phase 1（Round 1 — 架構確認，等用戶 go）

---

## Architecture（FINAL — 已修正 BMD 全部 10 項）

```
LAYER 0 — DATA COLLECTION（獨立、並行）
┌──────────────────────────────┐  ┌──────────────────────────────┐
│ ws_manager.py [NEW,KeepAlive]│  │ async_scanner.py [改良]       │
│                              │  │                              │
│ Binance BTC WS:              │  │ 9 exchange REST, 20s/round   │
│   kline_3m ──┐               │  │ 180s = 完整 cycle            │
│   kline_15m ─┤               │  │                              │
│   kline_1h ──┼→ XADD         │  │ XADD market:poll ────────┐   │
│   kline_4h ──┤  market:klines│  │ + shared/ file bus (保留) │   │
│   miniTicker ┘  market:ticker│  └──────────────────────────┘   │
│              (dashboard only)│               │                 │
└──────────────┬───────────────┘               │                 │
               │          Redis Streams        │                 │
               └──────────────┬────────────────┘                 │
                              │                                  │
LAYER 1 — DATA PROCESSING     │                                  │
┌─────────────────────────────┴──────────────────────────────┐   │
│ indicator_engine.py [NEW, KeepAlive]                       │   │
│                                                            │   │
│ STARTUP: REST backfill 200 klines × 4 timeframes           │   │
│ → build in-memory rolling DataFrame per TF                 │   │
│ → calc initial indicators → write cache                    │   │
│                                                            │   │
│ TRIGGERS (event-driven, WS kline close x=true):           │   │
│   3m close  → LIGHT: RSI/BB/volume/z-score on 3m          │   │
│   15m close → FULL:  all 34 fields × {3m,15m,1h,4h}       │   │
│   1h close  → FULL + S/R proximity re-check               │   │
│   4h close  → MACRO: Fib recalc + MACD divergence + MA    │   │
│                                                            │   │
│ OUTPUT: shared/indicator_cache.json                        │   │
│ (EXACT schema: {symbol: {timeframe: {34 fields}}})        │   │
│                                                            │   │
│ FALLBACK: Redis/WS down → REST fetch klines every 3min    │   │
└────────────────────────────┬───────────────────────────────┘   │
                             │                                   │
LAYER 2 — DECISION + EXECUTION                                   │
┌────────────────────────────┴───────────────────────────────┐   │
│ trader_cycle [改良: 30min → 15min]                          │   │
│                                                            │   │
│ CalcIndicatorsStep: read cache (fast) OR REST (fallback)   │   │
│ 其餘 21 steps 不變                                          │   │
└────────────────────────────────────────────────────────────┘   │
```

### Data Authority（BMD #4 修正）
| Data | Source of Truth | Consumer |
|------|----------------|----------|
| BTC OHLCV + indicators | WS → indicator_engine | trader_cycle（via cache） |
| Cross-exchange spread/vol/funding | REST poll（9 exchanges） | scanner → shared/ → trader_cycle |
| Positions / balance | Exchange API | trader_cycle Step 7 |
| Live price (display) | WS miniTicker | Dashboard only |

**規則**：indicator_engine 用 WS data 算 indicator。REST poll data 係 supplementary context，唔參與 indicator 計算。

### Timing Model（BMD #1 修正 — WS trigger, 唔係 poll trigger）

```
EVENT-DRIVEN TRIGGERS (WS kline close):
  ┌─ 3m close ──→ indicator_engine LIGHT    ─┐
  ├─ 15m close ─→ indicator_engine FULL     ─┤→ write indicator_cache.json
  ├─ 1h close ──→ indicator_engine FULL+SR  ─┤
  └─ 4h close ──→ indicator_engine MACRO    ─┘

CLOCK-DRIVEN (independent):
  每 20s:  scanner polls 1 exchange
  每 180s: scanner 完成 9-exchange cycle → XADD market:poll
  每 15min: trader_cycle (launchd) → read cache → 22-step pipeline

WITHIN 15-MIN DECISION WINDOW:
  t+0m   3m close → LIGHT #1
  t+3m   3m close → LIGHT #2
  t+6m   3m close → LIGHT #3
  t+9m   3m close → LIGHT #4
  t+12m  3m close → LIGHT #5
  t+15m  15m close → FULL ← trader_cycle reads HERE
```

**Light analysis trigger = WS 3m kline close event（唔係 poll cycle）。**
Poll cycle 獨立運行，保持 prices_cache 新鮮。兩者唔需要對齊。

### Cold Start Protocol（BMD #2 修正）
```
indicator_engine 啟動：
1. Connect Redis（fail → 直接入 REST-only mode）
2. REST backfill: fetch_klines(BTCUSDT, tf, 200) for tf in [3m, 15m, 1h, 4h]
   → 4 次 REST call ≈ 2-3 秒
3. calc_indicators(df, params) for each timeframe → build initial state
4. Write indicator_cache.json（startup snapshot）
5. Subscribe Redis consumer group market:klines
6. 切換 incremental mode: each kline close → append to rolling DataFrame → recalc
   → 唔再 re-fetch 200 klines
```

### Degradation Matrix（BMD #3 修正）
| 故障 | 影響 | 自動恢復 | 最差結果 |
|------|------|---------|---------|
| Redis lag >5s | Consumer 延遲 | 警告 log | 遲幾秒，唔影響 |
| Redis down >30s | Streaming 停 | indicator_engine → REST fallback 每 3min | 從實時退化到 3min 粒度 |
| WS 斷線 | 冇 kline events | ws_manager auto-reconnect (2^n backoff, max 60s) | 短暫 gap，reconnect 後追回 |
| Redis + WS 都掛 | 全部 streaming 停 | indicator_engine REST 每 3min | 同現有 light_scan 頻率一樣 |
| indicator_engine crash | Cache 變舊 | LaunchAgent 重啟 + cold start backfill | trader_cycle fallback 到自己 CalcIndicatorsStep |
| 全部新嘢掛 | | | **= 現有系統。零退化。** |

### Role Clarity（BMD #7 修正）
| Component | 角色 | 有冇 execution 權？ |
|-----------|------|-------------------|
| ws_manager | Data collector | ❌ 只推 Redis |
| indicator_engine | Data processor | ❌ 只寫 cache |
| async_scanner | Data collector | ❌ 只寫 shared/ + Redis |
| trader_cycle | **Decision maker + Executor** | ✅ 唯一有權落單 |

### indicator_cache.json Schema（BMD #6 修正）
```json
{
  "BTCUSDT": {
    "4h": {
      "price": 87234.5, "high": 87500.0, "low": 86800.0, "volume": 12345.6,
      "bb_upper": 88000.0, "bb_basis": 87000.0, "bb_lower": 86000.0, "bb_width": 0.023,
      "rsi": 55.2, "rsi_prev": 53.1,
      "adx": 22.5, "di_plus": 18.3, "di_minus": 15.7,
      "ema_fast": 87100.0, "ema_slow": 86500.0,
      "atr": 350.0,
      "stoch_k": 62.3, "stoch_d": 58.1, "stoch_k_prev": 55.0, "stoch_d_prev": 52.0,
      "ma50": 86000.0, "ma200": 82000.0,
      "macd_line": 150.0, "macd_signal": 120.0, "macd_hist": 30.0, "macd_hist_prev": 25.0,
      "obv": 500000.0, "obv_ema": 480000.0,
      "rolling_low": 85000.0, "rolling_high": 89000.0,
      "vwap": 87100.0, "vwap_upper": 87800.0, "vwap_lower": 86400.0,
      "vol_spike": false,
      "z_robust": 0.8, "bb_width_pctl": 45.0,
      "volume_ratio": 1.2
    },
    "1h": { "...same 34 fields..." },
    "15m": { "...same 34 fields..." },
    "3m": { "...same 34 fields..." }
  },
  "_meta": {
    "last_update": "2026-03-19T12:34:56+08:00",
    "source": "ws",
    "ws_connected": true,
    "engine_uptime_s": 3600
  },
  "_macro": {
    "fib_levels": [85200, 86100, 86700, 87300, 87900],
    "fib_swing_high": 89000.0,
    "fib_swing_low": 83000.0,
    "macd_divergence": "none",
    "ma_trend": "bullish",
    "prev_day_high": 88500.0,
    "prev_day_low": 85500.0,
    "prev_day_close": 87000.0
  }
}
```

**34 fields per timeframe = EXACT match `calc_indicators()` output + `volume_ratio`。**
`_macro` = 新增，trader_cycle 可讀可唔讀（backward compatible）。

### S/R 設計（BMD #8, #9 修正）

**Fibonacci（保留，用 rolling extrema）：**
- 數據源：4H `rolling_high` / `rolling_low`（lookback=30 = ~5 日）
- Levels：`low + (high - low) × {0.236, 0.382, 0.5, 0.618, 0.786}`
- Recalc trigger：4H close 時 rolling_high 或 rolling_low 改變
- 唔用 zigzag — rolling extrema 簡單、可測、同現有 code 一致

**MACD Divergence（新增）：**
- Compare last 4 bars: `price` trend vs `macd_hist` trend
- Price higher highs + MACD lower highs → bearish divergence
- Price lower lows + MACD higher lows → bullish divergence
- Output: `"bearish" | "bullish" | "none"`

**Volume Profile：DROP。** 需要 tick-level 數據（aggTrade）。用現有 VWAP + vwap_upper/lower 做 proxy。真 VP 留到加 aggTrade 時先做。

**S/R proximity check：** 每 3m close 時，check price vs all S/R levels。用現有 `SR_PROXIMITY_TOL=0.005` (0.5%)。

---

## Phases

### Phase 1: 偵察 + 設計
- [x] 偵察完整架構（scanner, trader_cycle, indicator_calc, mode_detector）
- [x] 確認 Redis 8.6.1 已裝 + 運行
- [x] 確認 indicator schema: 34 fields × {4h, 1h}，新加 {15m, 3m}
- [x] 確認 mode_detector 輸入：rsi, macd_hist/prev, volume_ratio, price, ma50, ma200, funding
- [x] 確認 strategy 輸入：Range 讀 1h + 4h，Trend 讀 4h + 1h
- [x] BMD 完成：10 項，全部已有修正方案
- [ ] 用戶 confirm → Phase 2
- **Status:** in_progress

### Phase 2: Redis 基礎設施
- [ ] 2a: `pip install redis` + 加入 requirements.txt
- [ ] 2b: `scripts/shared_infra/redis_bus.py`
  - `RedisPool`: connection pool (lazy init, singleton)
  - `xadd(stream, data, maxlen)`: 寫入 stream
  - `xread_latest(stream)`: 讀最新 entry
  - `ensure_group(stream, group)`: create consumer group (MKSTREAM)
  - `xreadgroup(group, consumer, stream, count, block_ms)`: blocking read
  - `xack(stream, group, msg_id)`: acknowledge
  - `health_check()`: PING + stream info
  - `is_available()`: try-except connection check（caller 用呢個決定 fallback）
- [ ] 2c: 驗證：write → read → consumer group → ACK cycle
- **Status:** pending
- **改動：** 1 新文件 + requirements.txt
- **停止點：** 驗證通過後報告，確認再繼續

### Phase 3: ws_manager（BTC WebSocket → Redis）
- [ ] 3a: `scripts/ws_manager.py` — asyncio 永續進程
  - Combined stream: `wss://fstream.binance.com/stream?streams=btcusdt@kline_3m/btcusdt@kline_15m/btcusdt@kline_1h/btcusdt@kline_4h/btcusdt@miniTicker`
  - **4 kline streams + 1 miniTicker = 5 subscriptions（冇 aggTrade — BMD #5）**
  - On kline message → normalize to `{symbol, interval, o, h, l, c, v, is_closed, ts}` → XADD market:klines (maxlen=10000)
  - On miniTicker → `{symbol, price, ts}` → XADD market:ticker (maxlen=1000)
  - Auto-reconnect: exponential backoff (2^n, max 60s, jitter)
  - Binance 10min pong + 24h reconnect
  - Heartbeat: 每 30s 寫 `logs/ws_heartbeat.txt`
  - Telegram alert: 連續 3 次 reconnect 失敗
- [ ] 3b: LaunchAgent `ai.openclaw.wsmanager`（KeepAlive=true, ThrottleInterval=30）
- [ ] 3c: 驗證：連線穩定 10 分鐘 + Redis stream 有持續寫入 + 3m kline close event 到達
- **Status:** pending
- **改動：** 1 新文件 + 1 plist
- **風險：** Binance 3m kline WS 支援（高確定 — 官方 supported interval）
- **停止點：** WS 穩定 + Redis 有數據 → 報告

### Phase 4: Indicator Engine（核心 — 3 sub-phases）

#### Phase 4a: Core Engine + Backfill
- [ ] `scripts/indicator_engine.py` — asyncio 永續進程
- [ ] 啟動 cold start: REST backfill 200 klines × 4 TF → calc_indicators() → write initial cache
- [ ] In-memory rolling DataFrame per TF（append-only, trim to 300 rows）
- [ ] Consumer: xreadgroup market:klines → on is_closed=true → append to DataFrame → recalc → write cache
- [ ] Timeframe routing:
  - 3m close → calc_indicators(df_3m, params_3m) → update cache["BTCUSDT"]["3m"]
  - 15m close → calc all 4 TF（因為 15m close 同時意味上一個 3m 也 close）
  - 1h close → calc all 4 TF + S/R proximity check
  - 4h close → calc all 4 TF + MACRO（下一步 4b）
- [ ] volume_ratio: 計算方式同 market_data.py:163（current_vol / rolling_30_avg）
- [ ] Fallback: Redis/WS 斷 → 每 180s REST fetch klines + calc（= 現有 light_scan 頻率）
- [ ] LaunchAgent `ai.openclaw.indicatorengine`（KeepAlive=true）
- [ ] 加 3m params 入 config/params.py TIMEFRAME_PARAMS（15m 已有）
- [ ] 驗證：restart indicator_engine → cold start 完成 → 3m/15m/1h/4h cache 全部有值
- **Status:** pending
- **改動：** 1 新文件 + 1 plist + 1 修改（params.py）
- **停止點：** cache 正確產出所有 34 fields × 4 TF

#### Phase 4b: Macro S/R Module
- [ ] `scripts/indicator_engine_macro.py`（或 indicator_engine.py 內 class）
  - Fibonacci: 從 4H rolling_high/rolling_low 算 5 個 level
  - MACD divergence: 4-bar price vs macd_hist trend comparison
  - MA trend: price vs ma50 vs ma200 → "bullish" / "bearish" / "neutral"
  - Prev day H/L/C: 從 4H klines aggregate（或 REST fetch 1D kline）
- [ ] 結果寫入 cache["_macro"]
- [ ] 觸發：4H kline close + 1D kline close（daily H/L/C）
- [ ] 驗證：手動 check Fib levels 對唔對（同 TradingView 比較）
- **Status:** pending
- **改動：** 1 新文件或擴展 indicator_engine
- **停止點：** Fib/MACD divergence output 經人工驗證

#### Phase 4c: trader_cycle Integration
- [ ] 改 CalcIndicatorsStep:
  ```python
  cache = read_indicator_cache()
  if cache and not is_stale(cache, max_age=600):  # 10min
      ctx.indicators = cache  # fast path — 跳過 REST fetch
      return
  # slow path: 現有 REST fetch + calc（完全不變）
  ```
- [ ] 改 LaunchAgent `ai.openclaw.tradercycle`: StartInterval 1800 → 900（15min）
- [ ] 驗證：
  - trader_cycle 讀到 cache（fast path）→ 確認 mode_detector + strategies 全部正常
  - 斷 indicator_engine → trader_cycle fallback 到 slow path → 確認唔 crash
- **Status:** pending
- **改動：** 2 修改（market_data.py + tradercycle plist）
- **停止點：** trader_cycle 15min 跑一次，cache hit + fallback 都 work

### Phase 5: Scanner Redis Integration
- [ ] 5a: async_scanner 每完成 1 exchange → XADD market:poll（保留現有 file bus）
  - Data: `{exchange, symbol, price, change_24h, volume, funding, ts}`
- [ ] 5b: indicator_engine optional: 讀 market:poll 做 cross-exchange context（唔影響 indicator 計算）
- [ ] 5c: Fallback: Redis down → scanner 照寫 file bus，indicator_engine 唔讀 poll 數據（lose cross-exchange context, 唔影響 indicator）
- **Status:** pending
- **改動：** 1 修改（async_scanner.py）

### Phase 6: 監控 + 全路徑驗證
- [ ] 6a: heartbeat.py 加 Redis + WS 狀態 check
- [ ] 6b: Telegram alerts:
  - WS 連續 3 次 reconnect 失敗
  - Redis unreachable >60s
  - indicator_cache stale >10min
- [ ] 6c: Integration test — 全路徑跑 1 小時確認：
  - 3m/15m/1h close 都有 cache update
  - trader_cycle 15min 讀到 fresh cache
  - 手動殺 Redis → confirm fallback
  - 手動殺 WS → confirm reconnect
  - 手動殺 indicator_engine → confirm trader_cycle fallback
- [ ] 6d: 2check + bmd 全部新代碼
- **Status:** pending

### Phase 7: Dashboard Live Feed（LATER — 用戶話 step by step）
- [ ] Dashboard 訂閱 market:ticker 做 live price
- [ ] Dashboard 訂閱 market:klines 做 live chart
- **Status:** deferred

### Phase 8: 交付
- [ ] 更新 shared/PROTOCOL.md（加 Redis Streams section）
- [ ] 更新 architecture docs
- [ ] handin
- **Status:** pending

---

## Decisions（FINAL）
| # | Decision | Rationale | BMD ref |
|---|----------|-----------|---------|
| 1 | 保留 20s REST poll | 9 exchange 廣度唔可能用單 WS 取代 | — |
| 2 | WS trigger indicator calc | 精確 candle close timing，唔靠 poll cycle | BMD #1 |
| 3 | Cold start = REST backfill 先 | 冇歷史 = NaN indicators，等 33 日先有 MA200 | BMD #2 |
| 4 | Redis 係 convenience 唔係 necessity | 每個 consumer 有 REST fallback，全掛 = 現有系統 | BMD #3 |
| 5 | WS data = indicator 唯一源 | 唔 mix REST poll 數據入 indicator 計算 | BMD #4 |
| 6 | 冇 aggTrade（Phase 3） | 50-200 msg/s 噪音太大，kline 已夠用 | BMD #5 |
| 7 | Cache schema = calc_indicators() 34 fields | Zero format migration，trader_cycle 無感 | BMD #6 |
| 8 | indicator_engine = data producer ONLY | 唔做 decision，唔落單，唔 send Telegram | BMD #7 |
| 9 | Fibonacci 用 rolling extrema | 同現有 rolling_high/low 一致，簡單可測 | BMD #8 |
| 10 | Drop Volume Profile | 需要 tick data，用 VWAP proxy | BMD #9 |
| 11 | trader_cycle 30min → 15min | 用戶需求，cache 令 CalcIndicatorsStep 幾乎零成本 | — |
| 12 | miniTicker 代替 aggTrade 做 live price | ~1 msg/s vs 50-200 msg/s，dashboard 夠用 | BMD #5 |

## Errors
| Error | Attempt | Resolution |
|-------|---------|------------|
