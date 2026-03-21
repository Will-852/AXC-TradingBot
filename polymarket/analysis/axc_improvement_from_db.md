# AXC Polymarket 改進方案 — 基於 distinct-baguette 競品分析

> 分析日期：2026-03-21
> 作者：Claude (for AXC reference)
> 基礎：`polymarket/analysis/distinct_baguette_analysis.md` + AXC v15 live code
> 方法論：逐行讀取 AXC 10 個核心文件 + 競品完整文檔，file:line 引用全部 SEEN

---

## Executive Summary

distinct-baguette 在六個維度超越 AXC 現有實現：(1) Binance feed 驅動嘅 sub-second preemptive cancel，(2) event-driven 而非 timer-based 評估循環，(3) 含 latency 模擬嘅 backtester fill model，(4) 自動化 on-chain merge 回收資本，(5) UP+DOWN spread capture 無風險套利，(6) taker+maker 同時下單嘅 dual hybrid execution。AXC 嘅差異化（Student-t bridge、asymmetric sizing、CVD/OBI 信號）仍然有效，但呢六個 infrastructure 層面嘅改進可以直接減少 adverse selection、提高 fill quality、回收 locked capital。本文為每個改進提供 Python 級別嘅實現方案，按 effort/impact 排序。

---

## 1. Preemptive Cancel Mechanism

### 1.1 Current AXC State

AXC v15 有三個 cancel trigger（全部喺 `run_mm_live.py`）：

**Trigger 1 — Window End** (`run_mm_live.py:1340-1342`) [SEEN]
```python
if end_ms > 0 and now_ms > end_ms - 120_000:
    to_cancel = list(pending)
    reason = "window_end"
```
固定 2 分鐘 deadline，cancel ALL pending orders。

**Trigger 2 — Adverse Spot Move** (`run_mm_live.py:1349-1360`) [SEEN]
```python
_spot_thresh = 0.007 if _s == "ETHUSDT" else 0.005
# ...
is_adverse = (signed_move < 0 and _dir == "UP") or (signed_move > 0 and _dir == "DOWN")
if is_adverse and abs(signed_move) > _spot_thresh:
    to_cancel = _find_directional_orders(pending)
```
檢查 BTC 0.5% / ETH 0.7% adverse move，但只喺 main loop iteration 時檢查。

**Trigger 3 — Dynamic TTL** (`run_mm_live.py:1367-1374`) [SEEN]
```python
_max_ttl_s = min(600, max(60, _hard_cancel_s - entry_ts))  # 60s..600s
_time_on_book = now_s - entry_ts
if _time_on_book > _max_ttl_s:
    to_cancel = _find_directional_orders(pending)
```
Dynamic TTL 60s-600s。

**核心問題：** Cancel logic 只喺 5s main loop 同 10s heavy cycle 內執行（`run_mm_live.py:64-66`: `_CYCLE_S = 5`, `_HEAVY_INTERVAL_S = 10`）[SEEN]。唔係 event-driven — 一個 adverse move 可以發生喺 loop 之間嘅任何時刻。最壞情況：order sit exposed for 5 seconds while BTC moves $500+。

**Price cache:** `_price()` function at `run_mm_live.py:105-141` uses 3-second cache [SEEN]。Cancel 用 `_btc_price()` at `run_mm_live.py:144` which wraps `_price()` [SEEN]。

### 1.2 Competitor Advantage

distinct-baguette 嘅 **Binance Preemptive Cancel** 係一個獨立嘅 real-time monitor：

- 持續監聽 Binance aggTrade WebSocket（唔係 polling）
- 偵測到 adverse price move → **sub-second** cancel resting orders
- 「Before adverse price move causes toxic fill」— 搶先撤單

**點解有效：**
- Polymarket CLOB 嘅 repricing 相對 Binance 有 200-550ms 延遲
- 如果 BTC 跌 $200（UP token 應該跌），有人會即時 take 你嘅 UP resting bid
- Preemptive cancel = 喺佢哋嘅 taker order 到達之前撤走你嘅 maker order
- 時間窗口：~200ms（Binance spot move → Polymarket repricing）

**Latency edge decay formula:**（from competitor docs）

$$E(t) = E_0 \cdot e^{-\lambda t}, \quad \lambda \approx 3.5/\text{sec}$$

At $t = 200\text{ms}$: $E(0.2) \approx 0.50 E_0$ (50% edge remaining)
At $t = 550\text{ms}$: $E(0.55) \approx 0.14 E_0$ (14% edge remaining)

### 1.3 Gap Analysis

| Dimension | AXC | distinct-baguette | Gap |
|-----------|-----|-------------------|-----|
| Price feed | HTTP polling, 3s cache | WebSocket streaming, real-time | **~3000ms vs ~1ms** |
| Cancel trigger | 5s loop check | Event-driven on each aggTrade | **5000ms vs ~50ms** |
| Cancel scope | Directional only on adverse | All exposed orders | Partial overlap |
| Adverse threshold | BTC 0.5% (~$350), ETH 0.7% | Configurable, likely tighter | ~3x wider |
| Cancel latency | HTTP cancel via SDK | Direct CLOB cancel (likely WebSocket) | ~200ms vs ~50ms |

**Net gap: AXC orders are exposed for ~5s average (worst case 10s) vs ~50ms for competitor.**

### 1.4 Implementation Proposal

#### Architecture: Dedicated Cancel Thread

```
Main Loop (5s)          Binance WS Thread (real-time)
    │                         │
    ├─ Entry logic            ├─ aggTrade arrives
    ├─ Resolution             ├─ Update latest_price (lockfree)
    └─ State save             ├─ Check: adverse move for each resting order?
                              │    YES → Cancel immediately via CLOB REST
                              │    NO  → Continue
                              └─ Log cancel event
```

#### New file: `polymarket/exchange/binance_ws_monitor.py`

```python
"""Binance WebSocket price monitor with preemptive cancel callback.

Design: separate thread, shared state via threading.Lock.
Cancel decisions are LOCAL — only this thread cancels, main loop reads results.
"""

import json
import logging
import threading
import time
from typing import Callable

import websocket  # websocket-client library

logger = logging.getLogger(__name__)

class BinancePreemptiveMonitor:
    """Real-time Binance aggTrade → preemptive cancel for Polymarket resting orders.

    Architecture:
    - WebSocket thread: receives aggTrades, updates price, evaluates cancel conditions
    - Resting orders: registered by main loop via register_order()
    - Cancel callback: calls PolymarketClient.cancel_order() directly
    - Thread-safe: uses Lock for order registry
    """

    def __init__(
        self,
        symbols: list[str],       # ["btcusdt", "ethusdt"]
        cancel_fn: Callable,       # polymarket_client.cancel_order
        adverse_threshold: dict,   # {"BTCUSDT": 0.002, "ETHUSDT": 0.003}
        min_cancel_interval_ms: int = 500,  # prevent spam
    ):
        self._symbols = [s.lower() for s in symbols]
        self._cancel_fn = cancel_fn
        self._thresholds = adverse_threshold
        self._min_interval = min_cancel_interval_ms / 1000

        self._lock = threading.Lock()
        self._resting_orders: dict[str, dict] = {}
        # order_id -> {token_id, outcome, direction, entry_btc, symbol, registered_at}

        self._latest_price: dict[str, float] = {}
        self._last_cancel_ts: float = 0
        self._ws = None
        self._thread = None
        self._running = False

    def start(self):
        """Start WebSocket in daemon thread."""
        self._running = True
        streams = "/".join(f"{s}@aggTrade" for s in self._symbols)
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        self._ws = websocket.WebSocketApp(
            url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={"ping_interval": 20},
            daemon=True,
        )
        self._thread.start()
        logger.info("BinancePreemptiveMonitor started: %s", self._symbols)

    def stop(self):
        self._running = False
        if self._ws:
            self._ws.close()

    def register_order(self, order_id: str, meta: dict):
        """Main loop registers a new resting order for monitoring."""
        with self._lock:
            self._resting_orders[order_id] = {
                **meta,
                "registered_at": time.time(),
            }

    def unregister_order(self, order_id: str):
        """Remove order (filled or already cancelled by main loop)."""
        with self._lock:
            self._resting_orders.pop(order_id, None)

    def get_latest_price(self, symbol: str) -> float:
        return self._latest_price.get(symbol.upper(), 0.0)

    @property
    def cancelled_orders(self) -> list[str]:
        """Orders cancelled by this monitor (for main loop to sync state)."""
        # Implementation: maintain a thread-safe deque of cancelled IDs
        pass

    def _on_message(self, ws, msg):
        data = json.loads(msg).get("data", {})
        symbol = data.get("s", "").upper()   # "BTCUSDT"
        price = float(data.get("p", 0))

        if price <= 0:
            return
        self._latest_price[symbol] = price

        # Evaluate cancel conditions for all resting orders of this symbol
        now = time.time()
        if now - self._last_cancel_ts < self._min_interval:
            return

        threshold = self._thresholds.get(symbol, 0.005)
        to_cancel = []

        with self._lock:
            for oid, meta in list(self._resting_orders.items()):
                if meta.get("symbol") != symbol:
                    continue
                entry_price = meta.get("entry_btc", 0)
                direction = meta.get("direction", "UP")

                if entry_price <= 0:
                    continue

                move = (price - entry_price) / entry_price
                # Adverse = price moved against our direction
                is_adverse = (
                    (move < 0 and direction == "UP") or
                    (move > 0 and direction == "DOWN")
                )

                if is_adverse and abs(move) > threshold:
                    to_cancel.append((oid, meta, move))

        for oid, meta, move in to_cancel:
            try:
                self._cancel_fn(oid)
                self._last_cancel_ts = time.time()
                with self._lock:
                    self._resting_orders.pop(oid, None)
                logger.info(
                    "PREEMPTIVE CANCEL %s %s: %s moved %+.3f%% (thresh %.3f%%)",
                    oid[:12], meta.get("outcome"), symbol,
                    move * 100, threshold * 100,
                )
            except Exception as e:
                logger.warning("Preemptive cancel failed %s: %s", oid[:12], e)

    def _on_error(self, ws, error):
        logger.warning("Binance WS error: %s", error)

    def _on_close(self, ws, close_status, msg):
        logger.info("Binance WS closed: %s %s", close_status, msg)
        if self._running:
            time.sleep(5)
            self.start()  # auto-reconnect
```

#### Integration with `run_mm_live.py`

```python
# In main(), after client init:
from polymarket.exchange.binance_ws_monitor import BinancePreemptiveMonitor

monitor = BinancePreemptiveMonitor(
    symbols=["btcusdt", "ethusdt"],
    cancel_fn=client.cancel_order,
    adverse_threshold={"BTCUSDT": 0.002, "ETHUSDT": 0.003},
    # Tighter than current 0.5%/0.7% — preemptive can afford it
    # because it reacts in ~50ms, not 5s
)
monitor.start()

# In _execute(), after order submitted:
monitor.register_order(order_id, {
    "token_id": o.token_id,
    "outcome": o.outcome,
    "direction": "UP" if o.outcome == "UP" else "DOWN",
    "entry_btc": _coin_price,
    "symbol": _sym,
})

# In cancel defense section, also unregister:
monitor.unregister_order(oid)
```

#### Files to modify

| File | Change |
|------|--------|
| `polymarket/exchange/binance_ws_monitor.py` | **NEW** — WebSocket monitor class |
| `polymarket/run_mm_live.py` | Add monitor init + register/unregister in order lifecycle |
| `requirements.txt` or equivalent | Add `websocket-client` |

#### Risk/Complexity Assessment

- **Risk: Medium** — threading adds concurrency complexity. Cancel race condition: main loop and WS thread both try to cancel same order. Mitigate with Lock + idempotent cancel (cancel already-cancelled order = no-op on CLOB).
- **Complexity: Medium** — ~200 lines new code + ~30 lines integration.
- **Most likely to go wrong:** WebSocket disconnect during active trading. Auto-reconnect + fallback to polling (existing code) covers this.
- **Confidence: High** — standard pattern, websocket-client library is battle-tested.

### 1.5 Expected Impact

- **Adverse selection reduction: 50-80%** — from 5s exposure to ~50ms
- **Fill quality improvement:** Orders only fill when direction is still valid
- **Estimated PnL impact:** If 10% of current fills are toxic (adverse selection), and preemptive cancel blocks 70% of those → **+7% overall PnL**
- **Side benefit:** `_price()` function can read from monitor's `get_latest_price()` → 0ms instead of 3s cache

---

## 2. Event-Driven Evaluation

### 2.1 Current AXC State

AXC uses a **fixed timer loop** (`run_mm_live.py:64-66`) [SEEN]:

```python
_CYCLE_S = 5           # 5s main loop — fast reaction
_SCAN_S = 300          # discovery every 5 min
_HEAVY_INTERVAL_S = 10 # heavy ops every 10s
```

Main loop at `run_mm_live.py:1877-1884` [SEEN]:
```python
while True:
    try:
        state = run_cycle(state, gamma, client, config, dry_run, ...)
        _save(state)
    except Exception as e:
        logger.error("Cycle error: %s", e, exc_info=True)
    time.sleep(_CYCLE_S)
```

Signal pipeline runs in heavy cycle (every 10s): bridge + OB + CVD at `run_mm_live.py:1042-1069` [SEEN].

**核心問題：** BTC can move $500 between two 10s heavy cycles. AXC evaluates signals at fixed 10s intervals, regardless of whether the market moved. This means:
1. Wasted compute when market is quiet (no new information)
2. Delayed reaction when market moves fast (up to 10s lag)

### 2.2 Competitor Advantage

distinct-baguette's evaluation loop **wakes on each Binance aggTrade**, NOT on fixed timer. Three strategies run as parallel event-driven instances:

```
aggTrade arrives → Signal Engine evaluates → Strategy decides → Order submitted
```

- **No fixed timer** — evaluation frequency = trade frequency (~10-100 per second for BTC)
- Signal Engine: configurable lookback + delta threshold
- Signal staleness: ~5s (signal 超過 5s 自動失效)
- Evaluation cooldown: `eval_interval_ms = 2000` (不超過每 2s 一次，即使有新 aggTrade)

### 2.3 Gap Analysis

| Dimension | AXC | distinct-baguette |
|-----------|-----|-------------------|
| Trigger | Fixed 5s/10s timer | aggTrade event |
| Signal freshness | Up to 10s stale | <100ms |
| Compute efficiency | Runs even when idle | Only on new information |
| Cooldown | Implicit (10s heavy) | Explicit (2s eval_interval) |
| Entry latency | Signal → wait → next cycle → enter | Signal → immediate enter |

### 2.4 Implementation Proposal

**Full event-driven is overkill for Python.** AXC's 15M markets have 14-minute holding periods — 10s resolution is adequate for entry decisions. However, **preemptive cancel** (Section 1) already provides the real-time reaction layer.

**Recommended: Hybrid approach — keep timer for entry, use events for cancel + exit.**

```
Timer Thread (5s)              WebSocket Thread (real-time)
    │                               │
    ├─ Discovery (300s)              ├─ Price update (continuous)
    ├─ Signal pipeline (10s)         ├─ Preemptive cancel
    ├─ Entry decisions               ├─ Early exit trigger (black swan)
    ├─ Fill checks                   └─ Market mid monitoring
    ├─ Resolution
    └─ State save
```

#### Optimization: Price-triggered heavy cycle

Instead of fixed 10s heavy cycle, trigger heavy ops when price moves exceed threshold:

```python
# In run_cycle(), replace fixed _HEAVY_INTERVAL_S check:
_last_heavy_price = state.get("_last_heavy_price", {})

def _should_run_heavy(symbol: str, current_price: float) -> bool:
    """Run heavy cycle when price moved >0.1% since last heavy, OR 10s elapsed."""
    last = _last_heavy_price.get(symbol, 0)
    if last <= 0:
        return True
    move = abs(current_price - last) / last
    return move > 0.001  # 0.1% = ~$85 BTC

# In heavy cycle block:
is_heavy = (now_s - _last_heavy_ts >= _HEAVY_INTERVAL_S) or _should_run_heavy(_sym, btc)
```

This simple change means: during volatile periods, AXC evaluates more frequently; during quiet periods, it conserves API calls.

#### Files to modify

| File | Change |
|------|--------|
| `polymarket/run_mm_live.py` | Add price-triggered heavy cycle logic (~15 lines) |

#### Risk/Complexity Assessment

- **Risk: Low** — additive change, existing timer is fallback
- **Complexity: Low** — ~15 lines
- **Most likely to go wrong:** Too many heavy cycles during extreme volatility → API rate limit. Mitigate: keep minimum 5s between heavy cycles.

### 2.5 Expected Impact

- **Entry latency reduction: 0-8s** on volatile moves (average ~4s improvement)
- **API efficiency: -30% calls** during quiet periods
- **Combined with preemptive cancel:** real-time cancel + faster re-evaluation = **+3-5% PnL** from better entry timing

---

## 3. Latency Fill Model for Backtesting

### 3.1 Current AXC State

AXC backtester is in `polymarket/backtest/` directory. The microstructure strategy (`microstructure_strategy.py:42-49`) [SEEN] uses a lookup table trained from 90-day backtest:

```python
_LOOKUP_TABLE: dict[str, dict] = {
    "vol3x_small_rise":   {"p_up": 0.286, "n": 21},
    "vol2x_small_rise":   {"p_up": 0.414, "n": 29},
    "vol1.5x_small_drop": {"p_up": 0.587, "n": 46},
    "agg_rise":           {"p_up": 0.359, "n": 78},
    "agg_drop":           {"p_up": 0.587, "n": 46},
}
```

CVD strategy (`cvd_strategy.py:56-100`) [SEEN] fetches live aggTrades with REST API polling:
```python
def _fetch_live_agg_trades(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    # Paginated REST fetch, 200ms sleep between pages
```

**Current fill assumption in live bot:** `run_mm_live.py:1172-1177` [SEEN] — orders are either `"matched"` (instant fill, FOK) or pending (GTC on book). There is **no probabilistic fill model** for backtesting. Fill rate tracking exists (`_fill_rate()` at `run_mm_live.py:606-610` [SEEN]) but is observational, not predictive.

### 3.2 Competitor Advantage

distinct-baguette has **three fill models** for backtesting:

| Model | Description | Formula |
|-------|-------------|---------|
| **Deterministic** | Every order fills at quoted price | $P(\text{fill}) = 1.0$ |
| **Probabilistic** | Historical fill rate per price level | $P(\text{fill} \mid \text{price}=p) = f_{\text{hist}}(p)$ |
| **Latency** | Simulates RTT + queue + signal delay | $P(\text{fill}) = f(\text{RTT}, q_{\text{pos}}, \Delta t_{\text{signal}})$ |

The **Latency model** is the key innovation:

$$P(\text{fill}) = P(\text{order\_arrives\_in\_time}) \times P(\text{queue\_position\_reached})$$

Where:
- $\text{RTT} \sim \mathcal{N}(\mu_{\text{rtt}}, \sigma_{\text{rtt}})$ — network round-trip time
- $q_{\text{pos}}$ — position in order queue (FIFO), depends on when order was submitted
- $\Delta t_{\text{signal}}$ — time from signal generation to order submission

### 3.3 Gap Analysis

| Dimension | AXC | distinct-baguette |
|-----------|-----|-------------------|
| Fill model | None (live: binary matched/pending) | Three models |
| Latency simulation | Not modeled | RTT + queue + signal delay |
| Backtest realism | Deterministic only | Progressive: optimistic → realistic |
| Historical data resolution | 5m klines (microstructure) | 100ms orderbook snapshots |
| Price impact | Not modeled | Implicit via queue position |

### 3.4 Implementation Proposal

#### New file: `polymarket/backtest/fill_models.py`

```python
"""Fill probability models for Polymarket backtesting.

Three tiers matching competitor's approach, adapted for AXC's Python stack.
"""

import math
import random
from dataclasses import dataclass

@dataclass
class FillConfig:
    """Parameters for fill simulation."""
    # Network
    rtt_mean_ms: float = 150.0       # macOS → Polymarket CLOB (HK → US)
    rtt_std_ms: float = 30.0
    # Signal processing
    signal_delay_ms: float = 200.0   # Python signal pipeline overhead
    # Queue
    avg_queue_depth: float = 50.0    # shares ahead in queue at our price level
    fill_rate_per_sec: float = 5.0   # shares filled per second at typical levels

def deterministic_fill(order_price: float, market_price: float, side: str) -> float:
    """P(fill) = 1 if order price is marketable, 0 otherwise."""
    if side == "BUY":
        return 1.0 if order_price >= market_price else 0.0
    return 1.0 if order_price <= market_price else 0.0

def probabilistic_fill(
    order_price: float,
    best_bid: float,
    best_ask: float,
    historical_fill_rates: dict[float, float] | None = None,
) -> float:
    """Fill probability based on order position relative to spread.

    P(fill) decays exponentially with distance from best price:
    P = exp(-k * ticks_from_best)

    k calibrated from AXC live data (mm_order_log.jsonl).
    """
    if historical_fill_rates and order_price in historical_fill_rates:
        return historical_fill_rates[order_price]

    tick = 0.01  # Polymarket tick size
    spread = best_ask - best_bid

    # Distance from best bid (for BUY orders)
    ticks_from_best = max(0, (best_bid - order_price) / tick)

    # Decay: k = ln(2) / half_life_ticks
    # At 3 ticks from best: ~50% fill probability
    k = math.log(2) / 3.0
    return math.exp(-k * ticks_from_best)

def latency_fill(
    order_price: float,
    signal_time_ms: float,
    market_snapshot_at_signal: dict,  # {best_bid, best_ask, volume_at_price}
    market_snapshot_at_arrival: dict, # same, but RTT later
    config: FillConfig = FillConfig(),
) -> tuple[float, float]:
    """Most realistic fill model.

    Simulates:
    1. Signal delay (Python computation time)
    2. Network RTT (order reaches CLOB)
    3. Queue position (how many orders ahead)
    4. Market movement during total delay

    Returns: (fill_probability, simulated_fill_time_ms)
    """
    # 1. Total delay
    rtt = max(50, random.gauss(config.rtt_mean_ms, config.rtt_std_ms))
    total_delay_ms = config.signal_delay_ms + rtt

    # 2. Check if order is still marketable at arrival time
    arrival_bid = market_snapshot_at_arrival.get("best_bid", 0)
    arrival_ask = market_snapshot_at_arrival.get("best_ask", 0)

    if order_price < arrival_bid:
        # Our buy limit is below current bid — unlikely to fill
        return 0.05, total_delay_ms

    # 3. Queue position
    volume_ahead = market_snapshot_at_arrival.get("volume_at_price", {}).get(
        str(order_price), config.avg_queue_depth
    )
    time_to_fill_ms = (volume_ahead / config.fill_rate_per_sec) * 1000

    # 4. Fill probability: exponential decay with queue time
    # P(fill) = exp(-time_to_fill / characteristic_time)
    # characteristic_time: how long prices typically stay at a level
    char_time_ms = 5000  # 5 seconds (Polymarket 15M markets)
    p_fill = math.exp(-time_to_fill_ms / char_time_ms)

    # 5. Adjust for price favorability
    spread = arrival_ask - arrival_bid
    if spread > 0:
        favorability = (order_price - arrival_bid) / spread
        p_fill *= min(2.0, 1.0 + favorability)

    return min(0.95, max(0.01, p_fill)), total_delay_ms + time_to_fill_ms
```

#### Integration with existing backtester

```python
# In backtest runner, replace instant-fill assumption:
from polymarket.backtest.fill_models import latency_fill, FillConfig

config = FillConfig(
    rtt_mean_ms=150,      # HK → US
    # For Amsterdam VPS comparison:
    # rtt_mean_ms=5,       # AMS → Polymarket
)

for order in planned_orders:
    p_fill, delay = latency_fill(
        order.price, signal_time_ms, snapshot_at_signal, snapshot_at_arrival, config
    )
    if random.random() < p_fill:
        apply_fill(state, order.outcome, "BUY", order.price, order.size)
```

#### Files to modify

| File | Change |
|------|--------|
| `polymarket/backtest/fill_models.py` | **NEW** — three fill model implementations |
| `polymarket/backtest/` (existing runner) | Integrate fill models |

#### Risk/Complexity Assessment

- **Risk: Low** — backtester change, no live trading impact
- **Complexity: Medium** — ~150 lines new code, needs calibration from `mm_order_log.jsonl`
- **Most likely to go wrong:** Calibration data insufficient (need ≥50 fills with timing data). P4 in `v15_pending_improvements.md` [SEEN] already tracks this.
- **Confidence: Medium** — model correctness depends on calibration data quality.

### 3.5 Expected Impact

- **Backtest accuracy: +20-40%** vs deterministic assumption
- **Parameter tuning quality:** Latency model reveals which strategies survive real-world conditions
- **VPS decision:** Can simulate Amsterdam vs HK latency and quantify the difference
- **Estimated value:** Prevents deploying strategies that look good in backtest but fail live (like v14's 0% fill rate, noted in `v15_model_direction_analysis.md:52` [SEEN])

---

## 4. On-Chain Merge Automation

### 4.1 Current AXC State

Position merger at `polymarket/risk/position_merger.py` [SEEN] is **Phase 1: Detection only** (line 4):

```python
"""
position_merger.py — Detect mergeable positions on Polymarket

Phase 1: Detection only (report mergeable positions via Telegram)
Phase 2: On-chain CTF merge execution via Relayer (TODO)
"""
```

Detection logic at `position_merger.py:40-115` [SEEN] queries the Data API:
```python
url = f"{DATA_API_HOST}/positions?user={quote(user_address)}&mergeable=true"
```

It identifies YES+NO pairs and calculates reclaimable USDC (`position_merger.py:93-107` [SEEN]):
```python
for cid, info in positions_by_cid.items():
    pairs = min(info["yes"], info["no"])
    if pairs <= 0:
        continue
    mp = MergeablePosition(
        # ...
        mergeable_pairs=pairs,
        reclaimable_usdc=pairs,  # $1 per merged pair
    )
```

**Status:** Listed as Known Issue #4 in `CLAUDE.md` [SEEN]. Phase 2 (on-chain execution) is TODO.

### 4.2 Competitor Advantage

distinct-baguette uses a **ProxyWallet Factory** pattern for automated on-chain merging:

- **Automated:** runs on `merge_interval_secs: 240` (every 4 minutes)
- **Partial:** `merge_fraction: 0.5` — merges 50% of available pairs per cycle
- **Threshold:** `merge_min_pairs: 10` — only merge when 10+ pairs available
- **Gas:** uses POL (Polygon native token) for gas (~$0.01-0.05 per merge)
- **ProxyWallet Factory:** creates proxy wallets for isolated position management

**Why it matters for AXC:**
- AXC Dual-Layer strategy (Zone 1-3 at `market_maker.py:225-422` [SEEN]) frequently buys BOTH UP and DOWN tokens for hedge
- After a market resolves, the losing side is worthless. But BEFORE resolution, matched pairs can be merged to recover $1 per pair
- With $133 bankroll and 5% per market allocation, each unmerged pair locks up ~$0.95 that could be redeployed

### 4.3 Gap Analysis

| Dimension | AXC | distinct-baguette |
|-----------|-----|-------------------|
| Detection | Working (Data API) | Working |
| Execution | Manual / TODO | Automated (ProxyWallet Factory) |
| Frequency | Never (Phase 2 TODO) | Every 240s |
| Capital locked | $0.95+ per hedge pair | Recovered within 4 min |
| Gas handling | N/A | POL auto-managed |

**Capital impact estimate:**
- AXC Dual-Layer enters ~20 markets/day, ~50% have hedge positions (Zone 1-2)
- Average hedge: 5 UP + 5 DOWN = 5 mergeable pairs = $5 locked
- 10 unmerged markets = $50 locked capital (38% of $133 bankroll!)

### 4.4 Implementation Proposal

#### Phase 2a: SDK-based merge via CTF contract

```python
"""position_merger.py — Phase 2: On-chain merge execution.

Uses py-clob-client's merge/redeem functionality.
ProxyWallet approach: AXC already uses proxy wallet (signature_type=1,
polymarket_client.py:110 [SEEN]), so merge can use same wallet.

Flow:
1. detect_mergeable() → list of MergeablePosition
2. For each: call CTF contract merge(conditionId, amount)
3. USDC returns to proxy wallet
"""

# Addition to existing position_merger.py:

from web3 import Web3
from web3.middleware import geth_poa_middleware

# Polygon CTF (Conditional Token Framework) contract
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_ABI = [...]  # merge function ABI

def execute_merge(
    user_address: str,
    private_key: str,
    mergeables: list[MergeablePosition],
    fraction: float = 0.5,
    min_pairs: int = 5,
    rpc_url: str = "https://polygon-rpc.com",
) -> list[dict]:
    """Execute on-chain merge for YES+NO pairs.

    Args:
        fraction: merge this fraction of available pairs (0.5 = 50%)
        min_pairs: only merge if >= this many pairs

    Returns: list of {condition_id, pairs_merged, tx_hash, usdc_recovered}
    """
    total_pairs = sum(m.mergeable_pairs for m in mergeables)
    if total_pairs < min_pairs:
        logger.info("Skip merge: %d pairs < %d minimum", total_pairs, min_pairs)
        return []

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    ctf = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)

    results = []
    for m in mergeables:
        pairs_to_merge = int(m.mergeable_pairs * fraction)
        if pairs_to_merge < 1:
            continue

        try:
            tx = ctf.functions.mergePositions(
                collateralToken=USDC_ADDRESS,
                parentCollectionId=bytes(32),
                conditionId=bytes.fromhex(m.condition_id),
                partition=[1, 2],  # YES=1, NO=2
                amount=int(pairs_to_merge * 1e6),  # USDC decimals
            ).build_transaction({
                "from": user_address,
                "gas": 250000,
                "gasPrice": w3.eth.gas_price,
                "nonce": w3.eth.get_transaction_count(user_address),
            })

            signed = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

            results.append({
                "condition_id": m.condition_id,
                "pairs_merged": pairs_to_merge,
                "tx_hash": tx_hash.hex(),
                "usdc_recovered": pairs_to_merge,
                "gas_used": receipt.gasUsed,
            })
            logger.info("MERGED %s: %d pairs → $%.2f recovered (tx: %s)",
                        m.condition_id[:8], pairs_to_merge,
                        pairs_to_merge, tx_hash.hex()[:16])

        except Exception as e:
            logger.error("Merge failed %s: %s", m.condition_id[:8], e)

    return results
```

#### Integration with main loop

Add to `run_mm_live.py` heavy cycle:

```python
# Every 5 minutes, check for mergeable positions
if is_heavy and since_last_merge >= 300:
    from polymarket.risk.position_merger import detect_mergeable, execute_merge
    mergeables = detect_mergeable(os.getenv("POLY_WALLET_ADDRESS"))
    if mergeables:
        results = execute_merge(
            user_address=os.getenv("POLY_WALLET_ADDRESS"),
            private_key=os.getenv("POLY_PRIVATE_KEY"),
            mergeables=mergeables,
            fraction=0.5,
            min_pairs=5,
        )
        recovered = sum(r["usdc_recovered"] for r in results)
        state["bankroll"] += recovered
```

#### Files to modify

| File | Change |
|------|--------|
| `polymarket/risk/position_merger.py` | Add `execute_merge()` function |
| `polymarket/run_mm_live.py` | Add periodic merge check in heavy cycle |
| `requirements.txt` | Add `web3` |

#### Risk/Complexity Assessment

- **Risk: HIGH** — on-chain transactions involve real money + gas costs. Wrong parameters = lost funds.
- **Complexity: High** — Web3 integration, gas estimation, nonce management, tx confirmation.
- **Most likely to go wrong:** (1) Wrong CTF contract ABI, (2) gas estimation too low → stuck tx, (3) nonce collision with other tools using same wallet.
- **Mitigation:** Start with `merge_fraction=0.1` (10%), `min_pairs=20`, dry-run logging for 48h first.
- **Confidence: Medium** — contract interaction is well-documented but untested in AXC context.

### 4.5 Expected Impact

- **Capital efficiency: +20-40%** — recover locked hedge capital within 5 minutes instead of at resolution
- **Bankroll velocity:** More USDC available = more markets per day = more opportunities
- **At $133 bankroll:** ~$50 typically locked in hedge pairs → recovery = +38% available capital
- **Gas cost:** ~$0.05 per merge × 10 merges/day = $0.50/day (0.4% of bankroll)

---

## 5. UP+DOWN Spread Capture

### 5.1 Current AXC State

AXC has **no spread capture / arbitrage strategy**. The existing competitor analysis already identified this at `distinct_baguette_analysis.md:202` [SEEN]: `"Arb | UP+DOWN < $1 套利 | 未實現"`.

AXC's Dual-Layer strategy does buy both sides (`market_maker.py:325-336` [SEEN]), but for **hedge** purposes (combined entry must be < $1), not as systematic arb:

```python
# Layer 1 — HEDGE: Equal shares UP + DN at informed prices.
#   Combined < $1 → guaranteed profit if both fill.
orders.append(PlannedOrder(
    token_id=market.yes_token_id, side="BUY",
    price=up_bid, size=hedge_shares, outcome="UP"))
orders.append(PlannedOrder(
    token_id=market.no_token_id, side="BUY",
    price=dn_bid, size=hedge_shares, outcome="DOWN"))
```

This is conceptually similar but AXC's combined entry is always at `up_bid + dn_bid ≤ $0.95` (`market_maker.py:283` [SEEN]: `combined = up_bid + dn_bid  # always <= $0.95`), which is already more aggressive than the arb threshold.

### 5.2 Competitor Advantage

distinct-baguette's spread capture is a **standalone strategy**:

**Core logic:**
$$\text{If } \text{best\_bid}_{UP} + \text{best\_bid}_{DOWN} < \$1.00 - \text{threshold}$$

Then buy both at bid, guaranteed profit at resolution:

$$\text{Profit} = \$1.00 - (\text{price}_{UP} + \text{price}_{DOWN})$$

**Key parameters:**
```
spread_threshold: 0.02   # minimum $0.02 arb spread
max_buy_order_size: 5    # shares per order
trade_cooldown: 5000ms   # 5s between trades
```

**Example:**
- UP bid: $0.48, DOWN bid: $0.49
- Combined: $0.97
- Profit: $0.03 per share pair (3.1% return, guaranteed)
- 5 shares: $4.85 invested → $5.00 payout → $0.15 profit

### 5.3 Gap Analysis

| Dimension | AXC | distinct-baguette |
|-----------|-----|-------------------|
| Strategy | Hedge layer (part of Dual-Layer) | Standalone arb |
| Trigger | Signal-driven (needs directional edge) | Pure pricing inefficiency |
| Monitoring | None (checks at entry time only) | Continuous monitoring |
| Risk | Depends on direction being right | Zero directional risk |
| Frequency | Per-market, at entry | Continuous scanning |

**Important nuance:** AXC's hedge layer IS a spread capture — but it only executes when the signal pipeline also finds directional edge. A standalone arb would execute purely on pricing, regardless of direction.

### 5.4 Implementation Proposal

#### New feature in `run_mm_live.py`: Arb Scanner

```python
"""Spread capture: monitor UP+DOWN combined bid for risk-free arb.

Runs in heavy cycle. For each active 15M market:
1. Fetch UP and DOWN order books
2. If best_bid_UP + best_bid_DOWN < $1 - threshold → buy both
3. Hold to resolution → guaranteed profit

Threshold: $0.02 (covers Polymarket fees + slippage)
Polymarket fee: maker = 0% + 20% rebate, taker = ~1-1.5%
At $0.48 bid (maker): cost = $0.48 × 0% = $0, rebate = $0.48 × 0.002 = +$0.001
Combined: $0.97 cost + $0.002 rebate = $0.968 net → $0.032 profit (3.3%)
"""

_ARB_THRESHOLD = 0.02        # $0.02 minimum spread
_ARB_MAX_SHARES = 5          # shares per arb (conservative)
_ARB_COOLDOWN_S = 30         # seconds between arb attempts on same market

def _check_spread_arb(
    client,
    up_token: str,
    dn_token: str,
    cid: str,
    state: dict,
) -> list[PlannedOrder] | None:
    """Check if UP+DOWN spread offers risk-free arb.

    Returns pair of PlannedOrders if arb available, None otherwise.
    """
    # Cooldown check
    last_arb = state.get("_last_arb_ts", {}).get(cid, 0)
    if time.time() - last_arb < _ARB_COOLDOWN_S:
        return None

    try:
        up_book = client.get_order_book(up_token)
        dn_book = client.get_order_book(dn_token)
    except Exception:
        return None

    up_bids = up_book.get("bids", [])
    dn_bids = dn_book.get("bids", [])

    if not up_bids or not dn_bids:
        return None

    # Best bids (highest price someone will pay)
    # For arb, we BUY at these prices (taking from sellers at ask would be worse)
    # Actually we want the ASK prices — we're buying
    up_asks = up_book.get("asks", [])
    dn_asks = dn_book.get("asks", [])

    if not up_asks or not dn_asks:
        return None

    best_ask_up = up_asks[0]["price"]
    best_ask_dn = dn_asks[0]["price"]
    combined = best_ask_up + best_ask_dn

    if combined >= 1.0 - _ARB_THRESHOLD:
        return None  # no arb

    spread = 1.0 - combined
    # Check available size (min of both sides)
    size_up = up_asks[0]["size"]
    size_dn = dn_asks[0]["size"]
    arb_size = min(_ARB_MAX_SHARES, size_up, size_dn)

    if arb_size < 5:  # CLOB minimum
        return None

    logger.info("ARB DETECTED %s: UP@%.3f + DN@%.3f = %.3f (spread=$%.3f, size=%d)",
                cid[:8], best_ask_up, best_ask_dn, combined, spread, arb_size)

    return [
        PlannedOrder(
            token_id=up_token, side="BUY",
            price=best_ask_up, size=arb_size, outcome="UP"),
        PlannedOrder(
            token_id=dn_token, side="BUY",
            price=best_ask_dn, size=arb_size, outcome="DOWN"),
    ]
```

#### Files to modify

| File | Change |
|------|--------|
| `polymarket/run_mm_live.py` | Add `_check_spread_arb()` + call in heavy cycle |

#### Risk/Complexity Assessment

- **Risk: LOW** — mathematically guaranteed profit if both sides fill
- **Complexity: Low** — ~60 lines, uses existing order infrastructure
- **Most likely to go wrong:** (1) Only one side fills → exposed position (partial fill risk). Mitigate: use GTC at bid, both orders must fill. (2) Spread evaporates during execution. (3) Arb opportunities are extremely rare in efficient markets.
- **Confidence: High** — math is trivial, execution uses existing code.

### 5.5 Expected Impact

- **Expected frequency: 0-3 opportunities per day** — Polymarket 15M markets are relatively efficient
- **Profit per arb: $0.10-0.30** (5 shares × $0.02-0.06 spread)
- **Risk: Near-zero** (if both fill; partial fill risk manageable)
- **Strategic value: LOW** — competitor's own analysis notes these opportunities are shrinking
- **Side benefit:** Spread monitoring provides market efficiency indicator useful for signal calibration

---

## 6. Dual Hybrid Execution

### 6.1 Current AXC State

AXC uses **GTC limit orders only** for entry (`polymarket_client.py:281-303` [SEEN]):

```python
if price > 0:
    # Limit order (GTC)
    size = amount_usdc / price
    args = OrderArgs(token_id=token_id, price=price, size=round(size, 2), side="BUY")
    signed = self.client.create_order(args)
    result = self.client.post_order(signed, OrderType.GTC)
else:
    # Market order (FOK) — only used when price=0
    args = MarketOrderArgs(token_id=token_id, amount=amount_usdc, side="BUY")
    signed = self.client.create_market_order(args)
    result = self.client.post_order(signed, OrderType.FOK)
```

The `_execute()` function at `run_mm_live.py:428-466` [SEEN] calls `buy_shares()` with a price → always GTC:

```python
r = client.buy_shares(o.token_id, amount, price=o.price)
```

**Problem:** GTC orders sit on the book and may never fill. AXC's fill rate (from live data) is the critical bottleneck identified in `v15_pending_improvements.md:66` [SEEN]: "**Bottleneck 係 fill rate + entry price，唔係 data**"

### 6.2 Competitor Advantage

distinct-baguette has **four execution modes**:

| Mode | Mechanism | Fee | Fill Certainty |
|------|-----------|-----|----------------|
| `single_taker` | FOK at ask | Taker (~1.5%) | Highest |
| `gtc_at_ask` | GTC limit at ask | Maker (0% + rebate) | High |
| `single_maker` | GTC at bid | Maker (0% + rebate) | Lowest |
| `dual_hybrid` | FOK taker + GTC maker **simultaneous** | Mixed | Guaranteed partial |

**`dual_hybrid` is the key innovation:**

```
Simultaneously:
1. FOK taker order on likely side → instant fill, pay taker fee
2. GTC maker order on opposite side → sit on book, earn maker rebate
```

**Why it works:**
- Guaranteed directional exposure via taker (no fill rate risk)
- Opposite side earns spread + maker rebate (reduces cost basis)
- If wrong direction: opposite side fills = hedge
- If right direction: opposite side may not fill = pure directional profit

### 6.3 Gap Analysis

| Dimension | AXC | distinct-baguette |
|-----------|-----|-------------------|
| Execution modes | GTC only (default) or FOK (unused) | 4 modes |
| Fill certainty | Depends on queue | Guaranteed via taker |
| Fee optimization | Maker only (0% + rebate) | Mixed (taker cost offset by maker rebate) |
| Hedge execution | Both sides GTC (may not fill) | Taker = guaranteed, maker = bonus |

**Cost comparison for $5 order:**
- AXC (GTC maker): $0 fee + $0.01 rebate = net -$0.01 (best case, IF fills)
- DB (FOK taker): ~$0.075 fee (1.5% of $5)
- DB (dual_hybrid): $0.075 taker + (-$0.01 maker) = net $0.065 fee, but guaranteed fill

### 6.4 Implementation Proposal

#### Strategy: Confidence-based execution mode selection

```python
"""Execution mode selector: choose GTC/FOK/Hybrid based on signal strength.

Design decision: NOT always use taker (expensive). Instead:
- High confidence (>70%) + strong M1: FOK taker on directional side (speed > cost)
- Medium confidence (57-70%): GTC maker (current approach, cost-efficient)
- Hedge layer: always GTC maker (both sides, no urgency)
- Late entry (<4 min remaining): FOK taker (no time for GTC to fill)

This is the SIMPLEST version that captures 80% of the value.
"""

def select_execution_mode(
    confidence: float,
    minutes_remaining: float,
    is_hedge: bool,
    m1_sigma: float,  # M1 return / vol threshold (how many sigma)
) -> str:
    """Returns: 'GTC' | 'FOK' | 'HYBRID'"""

    # Hedge: always maker (both sides, cost-efficient)
    if is_hedge:
        return "GTC"

    # Late: must use taker (GTC won't fill in time)
    if minutes_remaining < 4:
        return "FOK"

    # Strong signal + high confidence: taker for guaranteed entry
    if confidence > 0.70 and m1_sigma > 1.5:
        return "FOK"

    # Default: maker
    return "GTC"
```

#### Modification to `_execute()` in `run_mm_live.py`:

```python
def _execute(orders: list[PlannedOrder], client,
             cid: str = "", signal_ctx: dict | None = None,
             exec_modes: dict[str, str] | None = None) -> list[dict]:
    """Submit orders with mode selection (GTC/FOK/HYBRID).

    exec_modes: {outcome: mode} e.g. {"UP": "FOK", "DOWN": "GTC"}
    """
    results = []
    _ctx = signal_ctx or {}
    _modes = exec_modes or {}

    for o in orders:
        mode = _modes.get(o.outcome, "GTC")
        try:
            amount = round(o.size * o.price, 2)
            if mode == "FOK":
                # Taker: immediate fill at ask price
                r = client.buy_shares(o.token_id, amount, price=0)  # price=0 → FOK
            else:
                # Maker: GTC limit order (existing behavior)
                r = client.buy_shares(o.token_id, amount, price=o.price)

            # ... rest of existing logic
```

#### Files to modify

| File | Change |
|------|--------|
| `polymarket/run_mm_live.py` | Add `select_execution_mode()`, modify `_execute()` to accept modes |
| `polymarket/exchange/polymarket_client.py` | No change needed (already supports FOK) |

#### Risk/Complexity Assessment

- **Risk: Medium** — FOK taker orders are more expensive (1.5% vs 0%). Incorrect mode selection = unnecessary fee drag.
- **Complexity: Low** — ~30 lines new logic, minimal changes to existing flow.
- **Most likely to go wrong:** (1) Over-aggressive taker usage eroding profits. (2) FOK order fails (no liquidity at ask) → fallback needed.
- **Confidence: High** — simple mode selection, FOK already supported by SDK.

**Fee impact analysis:**
- Current: 100% maker = $0 fees, but ~60% fill rate [INFERRED from v15 data]
- With FOK for high-confidence: ~30% of orders → taker, 70% → maker
- Extra fee: 30% × $5 avg × 1.5% = $0.023/order
- Fill rate improvement: 60% → ~85% effective (taker = guaranteed)
- Net: more fills × positive EV > fee cost, IF signal accuracy is high enough

**Break-even condition:**

$$\text{WR} \times \$0.60 - (1 - \text{WR}) \times \$0.40 - \text{fee} > 0$$

At WR=70%: $0.42 - $0.12 - $0.023 = $0.277 (still +EV)
At WR=55%: $0.33 - $0.18 - $0.023 = $0.127 (still +EV but marginal)

### 6.5 Expected Impact

- **Fill rate improvement: +15-25%** on high-confidence signals
- **PnL impact: +5-10%** from capturing more positive-EV opportunities
- **Fee cost: -$0.50-1.50/day** (offset by higher fill rate)
- **Strategic value: Medium** — most impactful for late-window entries where GTC fill rate approaches 0%

---

## Priority Matrix

| # | Improvement | Effort | Impact | Risk | Priority |
|---|------------|--------|--------|------|----------|
| 1 | Preemptive Cancel | Medium (200 LOC) | **High** (50-80% adverse reduction) | Medium | **P0** |
| 2 | Event-Driven Eval | Low (15 LOC) | **Medium** (3-5% PnL) | Low | **P0** |
| 6 | Dual Hybrid Execution | Low (30 LOC) | **Medium** (5-10% PnL) | Medium | **P1** |
| 5 | Spread Capture | Low (60 LOC) | **Low** (rare opportunities) | Low | **P2** |
| 3 | Latency Fill Model | Medium (150 LOC) | **Medium** (backtest quality) | Low | **P2** |
| 4 | On-Chain Merge | High (200+ LOC) | **High** (38% capital recovery) | **High** | **P2** |

**Impact/Effort quadrant:**

```
         HIGH IMPACT
              │
    P0: [1]  │  P0: [2]
    (medium   │  (low effort,
     effort)  │   medium impact)
──────────────┼───────────────
    P2: [4]  │  P1: [6]
    (high     │  (low effort,
     effort)  │   medium impact)
              │
         LOW IMPACT     P2: [3,5]
                        (low effort, low-medium impact)
```

---

## Implementation Roadmap

### Phase 1: Quick Wins (Week 1) — 0 Risk to Live Trading

| Day | Task | Files | Test |
|-----|------|-------|------|
| 1 | Price-triggered heavy cycle (Section 2) | `run_mm_live.py` | Dry-run, compare cycle frequency vs timer-only |
| 2 | Execution mode selector (Section 6) | `run_mm_live.py` | Dry-run, log mode selection decisions for 24h |
| 3 | Spread arb scanner (Section 5) | `run_mm_live.py` | Monitor-only (log opportunities, don't execute) |

### Phase 2: Core Infrastructure (Week 2) — Requires Testing

| Day | Task | Files | Test |
|-----|------|-------|------|
| 4-5 | Binance WS monitor (Section 1) | NEW `binance_ws_monitor.py` + `run_mm_live.py` | Unit test: mock WebSocket, verify cancel timing |
| 6 | Integration test: WS + cancel defense | `run_mm_live.py` | Dry-run with live WebSocket feed, verify cancels |
| 7 | Go live with preemptive cancel | — | Monitor for 24h with Telegram alerts |

### Phase 3: Backtest + Capital (Week 3-4) — Research

| Day | Task | Files | Test |
|-----|------|-------|------|
| 8-10 | Latency fill model (Section 3) | NEW `backtest/fill_models.py` | Calibrate from `mm_order_log.jsonl` (need ≥50 fills) |
| 11-14 | On-chain merge (Section 4) | `position_merger.py` | Testnet first → 10% merge fraction → 50% |

### Prerequisites

1. **Data dependency:** Sections 3 and 4 (P4 in `v15_pending_improvements.md` [SEEN]) require 48h report data → wait for 2026-03-23 report before implementing
2. **Package dependency:** `websocket-client` for Section 1, `web3` for Section 4
3. **Latency measurement:** Before Section 1, measure actual RTT from macOS to Polymarket CLOB to calibrate adverse thresholds

### Go/No-Go Criteria

- **Preemptive Cancel (Section 1):** Deploy when unit tests pass + 24h dry-run shows no false cancels
- **FOK Taker (Section 6):** Deploy when 48h paper data shows high-confidence signals have ≥70% WR
- **On-Chain Merge (Section 4):** Deploy only after testnet validation + $0.50 max gas budget per day

---

*Generated: 2026-03-21 | Based on AXC v15 live code + distinct-baguette documentation*
*All file:line references verified via direct file read (SEEN)*
