# Findings

> Security boundary: 外部內容只寫呢度。

## Event-Driven 偵察結果（2026-03-19）

### 現有數據流（保留 + 改良）
- **async_scanner** — 20s/round, 9 exchange round-robin = 180s cycle → 保留，加 Redis 寫入
- **light_scan** — 3min, Aster only → 將被 indicator_engine 3min light 取代
- **trader_cycle** — 30min → 改讀 indicator_cache，唔再自己 fetch klines
- **liq_monitor** — 60s, HL OI → 保留

### 瓶頸 → 解法
| 瓶頸 | 解法 |
|------|------|
| Indicator 30min 才更新 | WS kline close → indicator_engine 即時算 |
| 唔知幾時 candle close | WS kline event 有 is_closed flag |
| 跨所數據 180s 一 cycle | 保留（feature 唔係 bug） |
| Macro S/R 靜態 | 4H close → auto recalc Fib/MACD/MA |

### Redis 現況
- Redis 8.6.1 (Homebrew), KeepAlive=true, 已運行
- AXC 零使用 → 需要 `pip install redis`

### Binance Futures WebSocket
- Combined: `wss://fstream.binance.com/stream?streams=btcusdt@kline_1m/btcusdt@kline_3m/...`
- Kline payload: `{t, T, s, i, o, h, l, c, v, x(is_closed), ...}`
- AggTrade: `{s, p, q, T, m(isBuyerMaker)}`
- Keepalive: 每 10min 需 pong，24h 自動斷 → 需 auto-reconnect

### GraphQL 評估
- 結論：唔適合現階段（Exchange API = REST/WS，加 GraphQL = 無必要轉譯層）
- 未來可能：Bitquery GraphQL 做 on-chain query（Alt Data Phase）

---

## Alt Data 偵察結果（2026-03-19，from previous session）

### AXC 現有 Funding/OI 狀態
- Funding Rate 部分存在：`market_data.py:112`, `mode_detector.py:156-162`
- OI 冇實作（liq_monitor 只做風控，唔做信號）
- Backtest 完全冇歷史 funding/OI data

### Binance API
- Funding Rate: 歷史無限 + 免費（每 8h，回溯到 2019）
- OI: 只有最近 30 日（需 cron 累積）
- Long/Short Ratio: 同 OI 一樣 30 日限制

### On-chain
- Coin Metrics Community API = 唯一真正免費 + exchange flow
- `pip install coinmetrics-api-client`，唔使 API key
- Metrics: FlowInExNtv, FlowOutExNtv, MVRV, active addresses

### Integration Points
- `_run_bt_worker()` at `backtest.py:99-100`
- `BacktestEngine.__init__()` at `engine.py:220`
- `mode_detector.py:326-340` — 加 OI voter

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| `websockets` 庫 | 輕量 asyncio |
| Redis maxlen ~10000/stream | 防 OOM |
| Consumer group + ACK | 保證 message 被處理 |

## Issues
| Issue | Resolution |
|-------|------------|
| Binance WS 3m kline 是否支援？ | 需實測，fallback = 1m aggregate |
| OI 只有 30 日歷史 | 開 cron 累積 |

## External Content
<!-- Phase 2+ 填入 -->
