# 5M Both-Sides Bot — Architecture Spec
> Created: 2026-03-24
> Status: DRAFT — awaiting 2check before implementation
> Author: Claude Opus 4.6

---

## 1. Design Goal

Standalone `run_5m_live.py` that:
- Pure W4 both-sides strategy (no hedge layer, no endgame, no TP/SL exit)
- Each coin/timeframe = independent file for easy on/off
- Shares WS feeds + shared modules with 15M/1H bots
- Independent state file, trade log, PID — can run simultaneously with 15M + 1H
- Per-coin parameters without affecting other bots

---

## 2. File: `polymarket/run_5m_live.py`

### Section-by-Section Blueprint

---

### §A — Header + Imports

**IMPORT from shared modules:**
```python
from polymarket.strategy.market_maker import (
    MMMarketState, PlannedOrder, resolve_market,  # state + resolution
    apply_fill,                                    # fill processing
)
from polymarket.exchange.gamma_client import GammaClient
from polymarket.data.ws_binance import BinancePriceFeed
from polymarket.data.ws_polymarket import PolymarketBookFeed
from polymarket.data.ws_user import PolymarketUserFeed  # optional: instant fill detection
```

**NOT imported (5M doesn't need):**
- `MMConfig` — 5M uses its own config dataclass (different half_spread, no zones)
- `compute_fair_up` — 5M doesn't use Brownian Bridge for entry decisions
- `plan_opening` — 5M uses W4 both-sides ordering, not dual-layer
- `should_enter_market` — 5M has its own slug-based discovery
- `calc_tranches` — 5M enters once per window, no tranching
- `HourlyConfig` / `conviction_signal` — 1H-specific

**Pattern reference:** Follow `run_1h_live.py` import structure (lines 39–46). It imports only what it needs from `market_maker.py`.

---

### §B — 5M-Specific Constants + Per-Coin Config

**WRITE NEW:**
```python
# ── Paths (independent from 15M/1H) ──
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state_5m.json")
_TRADE_LOG  = os.path.join(_LOG_DIR, "mm_trades_5m.jsonl")
_SIGNAL_LOG = os.path.join(_LOG_DIR, "mm_signals_5m.jsonl")
_ORDER_LOG  = os.path.join(_LOG_DIR, "mm_order_log_5m.jsonl")
_W4_LOG     = os.path.join(_LOG_DIR, "mm_w4_5m.jsonl")

# ── 5M Strategy Parameters ──
_WINDOW_S = 300           # 5M = 300 seconds
_W4_DELAY_S = 120         # 2 min wait (not 5 min like 15M)
_W4_THRESHOLD_BPS = 5     # 5 basis points minimum move
_W4_LEAN_RATIO = 1.5      # 1.5:1 lean toward momentum direction

_CYCLE_S = 5              # 5s main loop (same as 15M)
_HEAVY_INTERVAL_S = 10    # heavy ops every 10s (5M windows are short)
_SCAN_S = 60              # discover every 60s (5M windows every 5 min = 12x/hr)
_CANCEL_BEFORE_END_S = 30 # cancel 30s before window end (not 120s like 15M)

# ── Per-Coin Config ──
@dataclass
class CoinConfig:
    live: bool = False
    delay_s: int = 120
    threshold_bps: int = 5
    lean_ratio: float = 1.5
    contrarian: bool = False  # SOL: buy against momentum
    symbol: str = "BTCUSDT"
    slug_prefix: str = "btc"

COIN_CONFIG = {
    "btc": CoinConfig(live=True,  symbol="BTCUSDT", slug_prefix="btc"),
    "eth": CoinConfig(live=False, symbol="ETHUSDT", slug_prefix="eth"),
    "sol": CoinConfig(live=False, symbol="SOLUSDT", slug_prefix="sol",
                      contrarian=True, delay_s=60, threshold_bps=10),
    "xrp": CoinConfig(live=False, symbol="XRPUSDT", slug_prefix="xrp"),
}
```

**Why per-coin dataclass:** The 15M bot uses a flat `_LIVE_TRADE_COINS = {"btc"}` set + global constants. The 1H bot uses `_LIVE_COINS = {"BTC", "ETH", "SOL"}`. For 5M, we need per-coin tuning (SOL contrarian, different delay/threshold), so a config dataclass is cleaner. Easy to modify one coin without touching others.

**Slug format:** Confirmed from `w4_signal_detection.py:56` and `market_scanner.py:117`:
```
btc-updown-5m-{unix_timestamp}
```
Where timestamp = window start (floor of current time to 300s boundary).

---

### §C — Data Layer (Price, Vol, OB)

**COPY from `run_1h_live.py`:**
- `_get_json()` (lines 109–116) — identical HTTP helper
- `_btc_price()` (lines 290–308) — per-coin price with WS → REST fallback
- `_vol_1m()` (lines 547–565) — per-minute vol from Binance 1m klines
- `_poly_midpoint()` (lines 568–581) — token midpoint with WS → REST fallback
- `_binance_open()` (lines 311–317) — open price from Binance kline

**COPY from `run_mm_live.py`:**
- `_w4_signal()` (lines 413–433) — core W4 momentum signal
  - **MODIFY:** Replace `_W4_DELAY_S` reference with per-coin `config.delay_s`
  - **MODIFY:** Replace `_W4_THRESHOLD_BPS` with per-coin `config.threshold_bps`

**NOT copied (5M doesn't need):**
- `_cross_exchange_price()` — overkill for 5M (15M uses it for flash crash detection)
- `_cvd_buy_ratio()` — directional signal, 5M uses pure W4
- `_m1_return()` — M1 momentum, replaced by W4
- `_holder_imbalance()` — whale tracking, 5M windows too short (5 min)
- `_vol_imbalance()` — volume imbalance filter, 5M is pure momentum

**Design note:** The 1H bot has a cleaner data layer pattern (per-coin symbols in `_COIN_SYMBOLS` dict) vs the 15M bot's more complex caching. Follow the 1H pattern.

---

### §D — Discovery

**WRITE NEW (based on 15M pattern from `run_mm_live.py` lines 520–570):**

```
def _discover_5m(gamma: GammaClient) -> list[dict]:
    """Find 5M markets for current + next 5 windows via slug.

    Slug format: {coin}-updown-5m-{unix_timestamp}
    Windows: every 300s (5 min), 24/7 continuous.
    """
```

Key differences from 15M `_discover()`:
| Aspect | 15M | 5M |
|--------|-----|-----|
| Slug format | `{coin}-updown-15m-{ts}` | `{coin}-updown-5m-{ts}` |
| Window alignment | `(now_et.minute // 15) * 15` | `floor(now / 300) * 300` |
| Lookahead windows | 5 | 5 |
| Coins | `[btc, eth, sol]` hardcoded | From `COIN_CONFIG` dict |
| Return type | `list[tuple[PolyMarket, dict]]` | `list[dict]` (follow 1H pattern) |

**Follow 1H pattern** (`run_1h_live.py` lines 739–770): Returns flat `list[dict]` with `{cid, coin, slug, up_tok, dn_tok, start_ms, end_ms}`. This is simpler than 15M's `list[tuple[PolyMarket, dict]]` which requires `PolyMarket` construction + `should_enter_market()` check.

**5M slug timestamp calculation:**
```python
now_s = int(time.time())
window_start = (now_s // 300) * 300  # floor to 5-min boundary
```

---

### §E — W4 Both-Sides Entry

**COPY from `run_mm_live.py` lines 1530–1642** (the `if both_sides:` block).

**Key modifications:**
1. Remove the `if both_sides:` gate — 5M is ALWAYS both-sides
2. Replace `_W4_DELAY_S` / `_W4_THRESHOLD_BPS` / `_W4_LEAN_RATIO` with per-coin config values
3. Add contrarian support: if `coin_config.contrarian`, FLIP lean direction
4. Remove `state["_w4_live"]` gate — 5M has its own `CoinConfig.live` flag
5. Remove `_observe_only` from `_LIVE_TRADE_COINS` — use `coin_config.live` instead

**Extract as function** (unlike 15M which inlines it in run_cycle):
```python
def _w4_entry(coin_cfg: CoinConfig, wl: dict, state: dict,
              client, dry_run: bool) -> list[PlannedOrder] | None:
    """W4 both-sides entry: momentum lean with per-coin config.
    Returns orders list, or None if no signal/too early/already entered."""
```

**Contrarian logic (SOL):**
```python
if coin_cfg.contrarian:
    lean_dir = "DOWN" if w4_dir == "UP" else "UP"
else:
    lean_dir = w4_dir
```

---

### §F — Order Execution

**COPY from `run_1h_live.py` lines 777–822** (`_execute_order()`).

The 1H pattern is cleaner: single function with explicit params. The 15M `_execute()` takes a `list[PlannedOrder]` which is also fine, but 1H's per-order approach gives better error isolation.

**Adaptation needed:**
- Add `_log_order()` for AS analysis (copy from 1H lines 1060–1074)
- W4 log goes to `_W4_LOG` (copy from 15M's `_BOTHSIDES_LOG` pattern)

---

### §G — Fill Confirmation

**COPY from `run_1h_live.py` lines 829–923** (`_check_fills()`).

This is nearly identical between 15M and 1H. The 1H version is simpler (no `_ws_user` fast path). For 5M:
- **Option A:** Simple REST-only (like 1H) — good enough for paper, simpler
- **Option B:** Add `_ws_user` support (like 15M) — needed for live to detect fills fast

**Recommendation:** Start with Option A (REST-only, like 1H). Add ws_user later when going live. The 5M window is 300s, and fills typically confirm in <30s via REST.

---

### §H — Resolution

**COPY from `run_1h_live.py` lines 930–997** (`_check_resolutions()`).

**Modifications:**
1. Change Binance kline interval from `1h` to `5m`
2. Change post-resolution delay from `+120_000ms` to `+60_000ms` (5M resolves faster)
3. Both-sides specific resolution logging (copy from 15M lines 1031–1051)

**Resolution logic (Binance 5m candle):**
```python
data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=5m&startTime={start_ms}&limit=1")
btc_o, btc_c = float(data[0][1]), float(data[0][4])
result = "UP" if btc_c >= btc_o else "DOWN"
```

---

### §I — State Management

**COPY from `run_1h_live.py` lines 999–1074:**
- `_load()` — with 5M default state structure
- `_save()` — atomic write via tempfile + os.replace
- `_to_dict()` / `_from_dict()` — MMMarketState serialization
- `_log_trade()` / `_log_order()` — trade + order logging

All paths point to 5M-specific files (`_STATE_PATH`, `_TRADE_LOG`, etc.).

**State structure (same as 1H):**
```json
{
  "markets": {},
  "watchlist": {},
  "daily_pnl": 0.0,
  "total_pnl": 0.0,
  "total_markets": 0,
  "bankroll": 100.0,
  "consecutive_losses": 0,
  "cooldown_until": "",
  "daily_pnl_date": "",
  "fill_stats": {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}
}
```

---

### §J — Cancel Defense

**WRITE NEW (simpler than 15M):**

5M cancel is much simpler than 15M:
- No dynamic TTL, no layer-specific cancel, no adverse spot move cancel
- Just: cancel all open orders at T-30s (window end minus 30s)

```python
def _cancel_before_end(state: dict, client, dry_run: bool):
    """Cancel all pending orders 30s before window end."""
    now_ms = int(time.time() * 1000)
    for cid, mkt in state["markets"].items():
        if mkt["phase"] != "OPEN":
            continue
        end_ms = mkt.get("window_end_ms", 0)
        tte_s = (end_ms - now_ms) / 1000
        if 0 < tte_s <= _CANCEL_BEFORE_END_S:
            # Cancel all pending
            for po in mkt.get("pending_orders", []):
                oid = po.get("order_id", "")
                if oid and client and hasattr(client, "client") and not dry_run:
                    try:
                        client.client.cancel(order_id=oid)
                    except Exception:
                        pass
            mkt["pending_orders"] = []
```

---

### §K — Kill Switches + Risk

**COPY from `run_1h_live.py` lines 1111–1124** (simpler than 15M's graduated system).

For 5M Phase 1, use the 1H pattern:
- Daily loss > 15% of bankroll → STOP
- Cooldown after 5 consecutive hour-losses → 4h cooldown
- Total loss > 22% of initial bankroll → permanent fuse (switch to dry-run)

**Do NOT copy** 15M's graduated daily loss tiers ($10/$20/$30/$45) — that's tuned for 15M's PnL profile. 5M will need its own calibration after paper data.

---

### §L — Main Loop (`run_cycle`)

**Structure follows `run_1h_live.py` `run_cycle()` (lines 1096–1295):**

```
run_cycle():
  1. Daily reset
  2. Kill switches
  3. Fast ops (every cycle): cancel defense, fill check, resolution
  4. Heavy ops (every 10s):
     a. Discovery (every 60s)
     b. Refresh vol + bankroll
     c. For each active market:
        - Get price + open price
        - W4 signal (per-coin delay/threshold)
        - If signal → W4 entry (with contrarian for SOL)
  5. Save state
```

**Key timing differences:**
| Parameter | 15M | 1H | 5M |
|-----------|-----|-----|-----|
| Main loop | 5s | 10s | 5s |
| Heavy cycle | 5s | 20s | 10s |
| Discovery | 300s | 300s | 60s |
| W4 delay | 300s | n/a | 120s (per-coin) |
| Cancel | T-120s | n/a | T-30s |
| Window | 900s | 3600s | 300s |

---

### §M — main() + WS Setup

**COPY from `run_1h_live.py` `main()` (lines 1529–1684).**

WS setup is identical: create own instances of BinancePriceFeed + PolymarketBookFeed.

**CLI args:**
```
--dry-run / --live / --status  (mutually exclusive)
--cycle                         (run 1 cycle, exit)
--verbose
--bankroll FLOAT
--bet-pct FLOAT
--coins btc,eth                 (override active coins)
```

---

## 3. Simultaneous Running — Isolation Analysis

### Can 5M + 15M + 1H run at the same time?

**YES.** Each bot is an independent process with:

| Resource | 15M | 1H | 5M |
|----------|-----|-----|-----|
| State file | `mm_state.json` | `mm_state_1h.json` | `mm_state_5m.json` |
| Trade log | `mm_trades.jsonl` | `mm_trades_1h.jsonl` | `mm_trades_5m.jsonl` |
| PID | separate | separate | separate |
| Bankroll | from CLOB | from CLOB | from CLOB |

### WS Connections: Separate per process

Each bot creates its own `BinancePriceFeed()` + `PolymarketBookFeed()` instances. This means:
- **3 Binance WS connections** (one per bot) — fine, Binance allows 5/IP
- **3 Polymarket WS connections** (one per bot) — fine, no documented limit
- **1-3 UserWS connections** — only 15M uses it now; 5M can add later

**Alternative (NOT recommended for Phase 1):** Shared WS daemon process via IPC. Too complex for the benefit. Each WS connection uses <1MB RAM and <1KB/s bandwidth.

### CLOB Order Conflict

**Risk:** All bots share the same POLY_PRIVATE_KEY wallet. Orders from different bots could interact.

**Mitigation (already exists in 15M):** At startup, 15M cancels only its OWN orphan orders (filtered by `_own_cids`, lines 2926–2938). Same pattern for 5M.

**Critical:** 5M markets have different condition_ids from 15M markets (different slug format: `-5m-` vs `-15m-`). So orders never overlap.

### Bankroll Sharing

**Risk:** Both bots read `get_usdc_balance()` and see the SAME balance. If both enter simultaneously, total exposure could exceed limits.

**Mitigation options:**
1. **Separate bankroll fraction** (recommended): 5M uses 30% of balance, 15M uses 60%, 1H uses 10%
2. **FileLock mutex** on bankroll read — too complex, not needed
3. **Max concurrent markets cap** — already exists per bot

**Recommendation:** Add `_BANKROLL_FRACTION = 0.30` to 5M config. Override `state["bankroll"]` as `balance * _BANKROLL_FRACTION` in run_cycle.

---

## 4. LaunchAgent

**Separate plist:** `~/Library/LaunchAgents/ai.openclaw.polymarket-5m.plist`

```xml
<!-- ai.openclaw.polymarket-5m.plist -->
<key>ProgramArguments</key>
<array>
    <string>/bin/bash</string>
    <string>/Users/wai/projects/axc-trading/polymarket/scripts/load_env_5m.sh</string>
</array>
```

Where `load_env_5m.sh` runs:
```bash
cd ~/projects/axc-trading
PYTHONPATH=.:scripts python3 polymarket/run_5m_live.py --live
```

**Or** reuse existing `load_env.sh` wrapper pattern from 15M/1H.

---

## 5. What NOT to Include (Phase 1)

| Feature | Why excluded |
|---------|-------------|
| Endgame bets | 5M windows too short (5 min). No time for last-2-min data collection. |
| Hedge (HL) | Both-sides IS the hedge. No HL futures needed. |
| TP/SL exit | W4 holds to resolution. No early exit. |
| Dynamic TTL cancel | Only T-30s cancel. 5M has no time for dynamic TTL. |
| Cross-exchange price validation | Overkill for 5M. |
| Holder imbalance / whale tracking | Window too short for holder data to be meaningful. |
| CVD / M1 / indicator pipeline | 5M uses pure W4 momentum. No indicator blending. |
| Conditional rungs / ladder DCA | Both-sides = 2 orders (UP + DOWN). No laddering. |
| Paper PnL tracker | Can add later. Start with real W4 log for analysis. |
| Newbie protection | 5M starts paper-only (all coins `live=False`). Protection is the default. |

---

## 6. Estimated File Size

| Section | Lines (est.) |
|---------|-------------|
| §A Header + Imports | 40 |
| §B Constants + CoinConfig | 60 |
| §C Data Layer | 120 |
| §D Discovery | 50 |
| §E W4 Entry | 100 |
| §F Execution | 50 |
| §G Fill Confirmation | 80 |
| §H Resolution | 60 |
| §I State Management | 80 |
| §J Cancel Defense | 30 |
| §K Kill Switches | 40 |
| §L run_cycle() | 120 |
| §M main() | 100 |
| **Total** | **~930 lines** |

Manageable as a single file (under 1000 lines). If it grows beyond 1200, extract data layer + state mgmt into shared helpers.

---

## 7. Implementation Order

1. **Phase 1:** Scaffold — constants, config, state mgmt, discovery, main loop (dry-run only)
2. **Phase 2:** W4 signal + both-sides entry (paper logging, no execution)
3. **Phase 3:** Fill confirmation + resolution (complete paper cycle)
4. **Phase 4:** Live execution for BTC only (`CoinConfig.live=True`)
5. **Phase 5:** Per-coin tuning (SOL contrarian, ETH params) after 48h paper data

---

## 8. Files to Update After Implementation

| File | Change |
|------|--------|
| `polymarket/CLAUDE.md` | Add 5M bot entry to §Current Phase + §三個交易系統 |
| `polymarket/FILEMAP.md` | Add `run_5m_live.py` entry |
| `memory/rules/polymarket_redline.md` | Update scope if 5M goes live |
| LaunchAgent plist | Create new plist for 5M |

---

## 9. Open Questions for 2check

1. **Bankroll fraction:** 30% for 5M reasonable? Or should it be lower (20%) given more windows/day?
2. **SOL contrarian:** Confirmed from wallet analysis that SOL mean-reverts in 5M. But should we start with momentum (like BTC) and add contrarian after paper data?
3. **ws_user:** Worth adding in Phase 1? Or REST-only fill check is good enough for paper?
4. **XRP/DOGE/HYPE/BNB:** External verification says 5M has 7 assets. Should we support all 7 in config? Or focus BTC+ETH+SOL first?
5. **5M resolution timing:** Binance 5m kline close at minute :05, :10, :15, etc. Polymarket resolution may lag by 50-120s (same as 15M). Need to verify with actual 5M market observation.
