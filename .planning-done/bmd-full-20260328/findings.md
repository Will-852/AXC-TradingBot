# Findings — AXC BMD 修正
> Security boundary: 外部內容只寫呢度，唔寫 task_plan.md。

## Scout 結果（2026-03-28）

### constants.py 結構
- `_LIVE_TRADE_COINS = {"btc", "sol"}` (line 61) — lowercase set
- `_BET_PCT_BY_COIN = {"btc": 0.03, "sol": 0.01}` (line 62) — lowercase keys
- Path functions (lines 25-34): `signal_path`, `order_path`, `trade_path` — 全部 `coin.upper()`
- `coin_from_title()` (line 37-42): returns UPPERCASE
- `_STATE_PATH` (line 16): `polymarket/logs/mm_state.json`
- 6 files reference `_LIVE_TRADE_COINS`
- polymarket/CLAUDE.md:26 過時（寫 `{"btc"}`，實際 `{"btc", "sol"}`）

### TIMEFRAME_PARAMS 結構
- 4 keys: `"3m"`, `"15m"`, `"1h"`, `"4h"`
- 13 sub-keys: bb_length, bb_mult, rsi_period, adx_period, ema_fast, ema_slow, atr_period, rsi_long, rsi_short, adx_range_max, bb_touch_tol, bb_width_squeeze, lookback_support
- 20+ files reference, 大部分直接 `[]` access

### 已發現 BUG
1. **grid_search.py:212** — 直接 mutate `TIMEFRAME_PARAMS["1h"]` 冇 restore
2. **engine.py:296/321/323** — mutate with try/finally 但 concurrent race condition
3. **4h_indicator_backtest.py:133** — `params = TIMEFRAME_PARAMS["4h"]` 無 `.copy()`

### state_io.py
- 冇 schema version
- Load 失敗 → 裸 `except Exception: return _default_state()`（bankroll reset 100.0）
- `_default_state()` (line 32-37) 冇 version key

### 大小寫不一致地圖
- UPPERCASE: path functions, coin_from_title(), execute_fn calls
- LOWERCASE: _LIVE_TRADE_COINS, _BET_PCT_BY_COIN, _coin_slug assignments
- 目前安全（hardcoded lowercase），但冇 runtime guard

### run_mm_live.py startup 順序
- Line 497-498: logging.basicConfig()
- Line 500: args.status check
- 冇任何 validation

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| validate.py 獨立文件 | 唔污染 import chain，可獨立 test |
| function-level import | 避免 sys.path 未 setup 問題 |
| P0 只 wire MM runner | 最高風險先，成功再擴展 |

## WS 架構 Scout（Phase 2 偵察）

### WebSocket Classes（3 個獨立 daemon thread）
| Class | Thread | State | Reconnect | Used by |
|-------|--------|-------|-----------|---------|
| BinancePriceFeed | `ws-binance` + own event loop | `_data: dict[sym→{mid,bid,ask,ts}]` lock-protected | exp backoff + 23h preemptive | MM, 1H, 5M |
| PolymarketBookFeed | `ws-polymarket` + own event loop | `_data: dict[token→state]` + `_books: dict[token→orderbook]` | exp backoff + `_reconnect_requested` Event | MM, 1H, 5M |
| PolymarketUserFeed | `ws-user` + own event loop | `_orders: dict` + `_recent_fills: deque(100)` + `_connected: bool` | exp backoff | MM only |

### 注入 Pattern
```
run_*.py → instantiate WS → set_ws_feeds(ws_binance=..., ws_poly=...) → module-level ref
         → pass ws_* as kwargs to logic functions
```

### 💀 Critical: ws_user.connected 係 cancel safety gate
`order_lifecycle.py` 每次 cancel 都 check `ws_user.connected`。WS down → cancel 跳過。
呢個係 $9.38 phantom fill race condition 嘅 fix。SharedWSManager 唔可以碰 ws_user。

### PolyBookFeed subscribe race
`subscribe()` triggers reconnect (for initial_dump) → clears `_data/_books` → 其他 runner 短暫冇 data。
REST fallback 存在，唔係 trading risk 但要 note。

### scripts/ws_manager.py 完全唔同
Binance **Futures** (fstream) → **Redis Streams** → AXC scanner。唔可以 reuse。

## Runner 差異表（BaseRunner DESCOPED 原因）

| | MM | 1H | 4H | 5M | Daily |
|---|---|---|---|---|---|
| WS | 3 feeds | 2 | 0 | 2 | 0 |
| State | module `state_io` | local `_load/_save` | local | local | local |
| Fuse | `risk_guards` | inline 22% | inline 22% | inline 20% | 無 |
| Signal | KeyboardInterrupt | `_shutdown` module | `_shutdown` main | `_shutdown` module | `_shutdown` main |
| TG | local define | import | local define | import | local define |
| Loop | `while True` | `while _running` | `while _running` | `while _running` | `while _running` |
| Unique | ws_user, protection, 3 extra flags | — | — | --coins, --w4-live | — |
| LaunchAgent | ai.openclaw.mm15m (dry-run) | ai.openclaw.conv1h (live) | ai.openclaw.conv4h (live) | 無 plist | ai.openclaw.convdaily (dry-run) |

## async HTTP Deferred — 用戶決定 + 替代方案（2026-03-28）

**跳過理由：**
- WS 已覆蓋 real-time data，async HTTP 收益只在 REST/snapshot
- aiohttp 非輕量依賴，要維護 session lifecycle + error handling
- urllib sync call 喺 startup fetch config/history 場景完全可接受

**唯一值得考慮嘅場景：** data_feeds.py 並發打多個 REST endpoint（如同時 fetch 10 幣歷史）

**輕量替代（比 aiohttp 好）：**
```python
import asyncio
async def fetch_async(url):
    return await asyncio.to_thread(urllib.request.urlopen, url)
```
wrap 現有 urllib，唔使重寫整個 file。

## Resources
- `memory/project/axc_bmd_20260328.md` — BMD 完整結果
- `memory/project/axc_coupling_map.md` — 耦合地圖
- `memory/project/axc_structure_20260328.md` — 結構快照
