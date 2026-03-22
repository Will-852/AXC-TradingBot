# 1H Ladder Upgrade — Findings
> Updated: 2026-03-22

## Existing 1H Bot State
- File: `run_1h_live.py` (1318 lines)
- Status: LIVE (BTC), ETH observe-only
- Entry: single conviction-based order, one-order-per-market guard
- Exit: profit lock at 95¢ (sell 95% + hedge), conviction EXIT signal
- SL: none explicit (conviction engine EXIT only)
- Cancel defense: none (only window expiry cleanup)
- Fills: 4 resolved markets (all pnl=0, cost=0 — no actual fills yet)

## Data Available
- `signal_tape.jsonl`: 6949 entries (15M only — cannot use for 1H)
- `analysis_1h.jsonl`: 404 entries (read-only analysis, not price snapshots)
- `observe_1h.jsonl`: 362 entries (ETH observe signals)
- `mm_trades_1h.jsonl`: 4 entries (no fills)
- **Conclusion**: Must build 1H backtest from Binance klines (not from existing tape)

## Key 1H Market Characteristics (from analysis_1h.jsonl)
- price_range: 0.25 typical (wider than 15M)
- price_min: 0.445-0.485 (UP mid can go LOW)
- trades_buy_ratio: 0.74-0.80 (heavy buy bias)
- Conviction engine entry range: $0.20-$0.39

## Existing Conviction Engine (hourly_engine.py) — KEEP
- conviction = confidence × time_trust × ob_factor
- time_trust saturates at 40 min (designed for 60 min window)
- Dynamic threshold: 0.33 → 0.12 over 42 min
- Entry price: $0.25-$0.39 (hard ceiling)
- This engine decides WHEN + DIRECTION. Ladder handles HOW.
