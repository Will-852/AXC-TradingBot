# Task Plan: Config Fix + Backtest Split
> Created: 2026-03-26
> Goal: (A) 修復 config 分裂 + TG token 安全問題, (B) 拆 backtest/engine.py

## Risk Legend
- 🔴 **2CHECK** — 涉及交易邏輯 / 會改變 live/backtest 行為
- 🟡 **VERIFY** — 涉及 import 路徑 / 值變化
- 🟢 **SAFE** — 純清理 / 零行為改變

---

## Phase A: Config 分裂修復 — `pending`

### Step A.1: Fix settings.py stubs — 🟡 VERIFY
**問題**：settings.py 嘅 fallback stub 值同 params.py 唔一致。如果 params.py import 失敗，production 用錯值。
**修復**：
| Parameter | params.py (correct) | settings.py stub (wrong) | Action |
|-----------|---------------------|--------------------------|--------|
| `CRASH_RSI_ENTRY` | 60 | 75 | Fix → 60 |
| `CRASH_VOLUME_MIN` | 1.5 | 2.0 | Fix → 1.5 |
| `MODE_RSI_TREND_LOW` | 34 | 32 | Fix → 34 |
| `MODE_RSI_TREND_HIGH` | 69 | 68 | Fix → 69 |
| `MODE_CONFIRMATION_REQUIRED` | 1 | 2 | Fix → 1 |
| `SCAN_LOG_MAX_LINES` | 500 | 200 | Add getattr override |

### Step A.2: Fix backtest/engine.py divergence — 🔴 2CHECK
**問題**：engine.py 有獨立 hardcoded 值，唔讀 config/params.py。Backtest 同 live 跑唔同參數。
**修復**：
| Parameter | engine.py (wrong) | params.py (correct) | Action |
|-----------|-------------------|---------------------|--------|
| `REGIME_ADJUST_ENABLED` | True | False | Import from settings |
| `_STRATEGY_CONF_GATE` | all 0.50 | range=0.40, trend=0.48, crash=0.33 | Import from params |
| `_MODE_AFFINITY` | 多處 diverge | params.py 值 | Import from params |
| `_MODE_DEFAULT_PENALTY` | trend=-0.25, range=-0.10 | trend=-0.18, range=-0.20 | Import from params |
| `PERSISTENCE_THRESHOLD` (trend) | 4 | 1 (DEPRECATED) | Import from params |

🔴 **注意**：呢個改動會改變 backtest 結果！之前所有 backtest 都跑喺「舊」參數上。

### Step A.3: TG token → .env — 🟢 SAFE
**問題**：`8373819624:AAFH-SVT...` 明文喺 2 個 source file。
**修復**：
1. 確認 `secrets/.env` 已有 `TG_BOT_TOKEN=...`
2. `scripts/trader_cycle/config/settings.py:169` → `os.environ.get("TG_BOT_TOKEN", "")`
3. `scripts/light_scan.py:49` → `os.environ.get("TG_BOT_TOKEN", "")`

### Step A.4: Python path → env variable — 🟢 SAFE
**修復**：
1. `scripts/scanner_runner.py:31` → `os.environ.get("PYTHON3", "python3")`
2. `polymarket/strategy/crypto_15m.py:159` → same
3. Shell scripts → `${PYTHON3:-python3}`

---

## Phase B: backtest/engine.py split (1,554 → facade + 2 modules) — `pending`

### Step B.1: Create `backtest/engine_types.py` — 🟢 SAFE
**搬咩**：
- All module-level constants (WARMUP_CANDLES, COMMISSION_RATE, etc.)
- `BTPosition` dataclass
- `BTTrade` dataclass
- `_PendingSignal` dataclass
- `_get_size_tier()` function
- Re-export pass-throughs (calc_indicators, TIMEFRAME_PARAMS, PRODUCT_OVERRIDES, MAX_CRYPTO_POSITIONS)

### Step B.2: Create `backtest/engine_reporting.py` — 🟢 SAFE
**搬咩**：
- `_summary()` method → standalone function taking engine instance
- `_summarize_confidences()` → standalone function
- `_detect_clusters()` → standalone function

### Step B.3: Slim `backtest/engine.py` as facade — 🟡 VERIFY
- BacktestEngine class stays (但 _summary 等 delegate to engine_reporting)
- Top of file: `from backtest.engine_types import *`
- Top of file: `from backtest.engine_reporting import ...`
- 17 import sites 零改動

---

## Errors
| # | Phase | Error | Resolution |
|---|-------|-------|------------|

## Decisions
| # | Decision | Reason |
|---|----------|--------|
| 1 | Fix stubs 而唔係刪 stubs | Fallback chain 係 safety net |
| 2 | Backtest engine import from params | 統一 live + backtest 參數 |
| 3 | TG token 用 env var | Security best practice |
| 4 | engine.py facade re-export | 17 import sites 零改動 |
