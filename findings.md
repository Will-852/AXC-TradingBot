# Findings — 1H Strategy Upgrade

## Codebase Scout Results

### 1H Entry Flow (run_1h_live.py)
- Line 285-295: conviction_signal() called
- Line 338: entry decision (if sig.action == "ENTER" or "ADD")
- Line 342-346: vol_imbalance filter (confirm direction)
- Line 370-406: holder imbalance adjustment (±size, flip direction)
- Line 416-427: size calculation + budget enforcement
- Line 429-434: daily entry cap (max 2/day)

### Conviction Formula (hourly_engine.py)
- Line 180-195: Brownian Bridge fair_up (Student-t ν=5)
- Line 197-200: confidence = |fair_up - 0.50| × 2
- Line 227: time_trust = min(t/40, 1.0)
- Line 229-239: ob_factor = sqrt(spread × depth), penalize if <0.30
- Line 242: conviction = confidence × time_trust × ob_factor
- Line 244-248: threshold = max(0.12, 0.33 - t×0.005)
- Line 285-298: size_fraction = 0.05 × conviction² × ob_quality

### Taker Flow Infrastructure (already exists!)
- ws_aggtrade_recorder.py: recording BTC/ETH/SOL to CSV (30-day retention)
- Path: backtest/data/aggtrades/{SYMBOL}_{YYYYMMDD}_agg.live.csv
- Columns: agg_id, price, qty, timestamp, is_buyer_maker
- fetch_agg_trades.py: multi-source fetcher with aggregate_delta_volume()
- cvd_strategy.py: has divergence detection but NOT wired to 1H

### SharedWSManager
- Only used by: run_mm_live.py, run_1h_live.py, run_5m_live.py
- 4H and Daily don't use WS
- If MM+5M stopped, only 1H consumer → refcount always 1
