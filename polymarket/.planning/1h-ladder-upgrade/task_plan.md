# 1H Conviction Bot → Ladder DCA + Tiered TP Upgrade
> Created: 2026-03-22 | Status: PLANNING

## Goal
Port 15M MM bot improvements to 1H bot:
1. Wide Ladder DCA (multi-rung entry)
2. Checkpoint gate (conditional deep rungs)
3. Tiered partial TP (progressive profit taking)
4. Free roll hedge (opposite side on final TP)
5. Stop loss + cancel remaining rungs
6. Cancel defense (adverse move + window-end)

## Architecture Decision: ADAPT, not copy-paste

### Why 1H ≠ 15M (critical differences)

| Dimension | 15M Bot | 1H Bot | Implication |
|-----------|---------|--------|-------------|
| Window | 900s | 3600s (4x) | More time for fills + TP levels |
| Entry range | $0.25-$0.43 | $0.20-$0.39 | Rung prices must be LOWER |
| Signal | Bridge + M1 gate | Conviction engine | Keep conviction for TIMING, add ladder for EXECUTION |
| OB depth | Higher | Thinner (4% slippage) | Larger spread between rungs |
| Cancel defense | 3 triggers | None currently | Must add |
| Existing data | 6949 signal_tape entries | 404 analysis entries (no fills) | Need Binance-based backtest |
| Slug format | `btc-updown-15m-{ts}` | `bitcoin-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et` | No change needed |

### Core Design

```
CONVICTION ENGINE (timing + direction)
         ↓ action=ENTER
WIDE LADDER DCA (execution)
         ↓ fills
TIERED TP + DEFENSE (exit management)
```

Conviction engine stays — it decides WHEN to enter and WHICH direction.
Ladder replaces single-order with multi-rung entry.
Tiered TP manages exits progressively.

## Phase 0: Backtest (DATA FIRST)

### 0a. Build 1H ladder backtest
- Fetch Binance 1H klines (30 days = 720 hours)
- For each hour: fetch 1m klines within that hour
- Simulate: BTC open → compute fair_up → determine direction
- For each candidate rung level: did mid reach that price? If yes, did we win?
- Test rung combinations: [$0.38/$0.33/$0.28/$0.23], [$0.36/$0.30/$0.25/$0.20], etc.

### 0b. Backtest tiered TP for 1H
- Same approach as 15M: test x1.2/x1.3/x1.4/x1.5/x1.6/x1.8/x2.0/HOLD
- Sharpe ratio as objective
- Test single TP vs tiered (25%/35%/35%)

### 0c. Validate defense thresholds
- What BTC move threshold for adverse cancel? (15M uses 0.5%)
- 1H BTC can move more → maybe 1.0% or 1.5%?
- SL level: -25% (15M) vs -49% (current 1H) — which is better for ladder?

## Phase 1: Implement Ladder Entry

### Changes to `run_1h_live.py`:
1. When conviction_signal returns ENTER:
   - Place AUTO rungs (top 2) immediately
   - Log COND rungs for checkpoint evaluation
2. Checkpoint gate: evaluate COND rungs when mid approaches them
   - Pass: whale not against → place deep rungs
   - Fail: whale against (FLIP/EXIT) → skip deep rungs
3. Replace `_execute_order()` single call with loop over rungs
4. Remove one-order-per-market guard (ladder = multi-order by design)
   - BUT: keep per-WINDOW budget guard
5. Budget: conviction_signal.size_fraction × bankroll → split across rungs

### New constants (validated by Phase 0 backtest):
```python
_LADDER_AUTO_1H = [TBD, TBD]       # always place
_LADDER_COND_1H = [TBD, TBD]       # conditional
_LADDER_BUDGET_PCT_1H = 0.05        # 5% of bankroll per window (same as config.max_size_fraction)
```

## Phase 2: Implement Tiered TP

### Changes to `run_1h_live.py`:
1. Add _check_partial_tp() called every cycle (alongside _check_black_swan)
2. Three tiers: T1/T2/T3 with sell percentages from backtest
3. T3 triggers free roll hedge (buy opposite side, 5% of sold value)
4. Profit lock at 95¢ stays (already exists as _check_black_swan)

### New exit flow:
```
T1: mid ≥ entry × TBD → sell TBD%
T2: mid ≥ entry × TBD → sell TBD%
T3: mid ≥ entry × TBD → sell TBD% + free roll hedge
Profit Lock: mid ≥ 95¢ → sell 95% + greed hedge (existing)
Resolution: remaining → $1 or $0
```

## Phase 3: Implement Defense

### 3a. Stop loss + cancel remaining
- SL threshold: TBD from backtest (probably -25% like 15M)
- When SL fires: sell filled shares + cancel ALL pending orders
- SL fires even with pending rungs (15M fix: don't wait for fills_confirmed)

### 3b. Cancel defense (NEW for 1H)
- Adverse BTC move: cancel unfilled rungs if BTC moves against us > TBD%
- Window-end: cancel all pending at window_end - 5min (1H has more time)
- Dynamic TTL per order (optional)

### 3c. Kill switches (existing, may need tuning)
- Daily loss > 15% → stop (existing)
- Total loss > 22% → permanent fuse (existing)
- Consecutive loss cooldown: 4h (existing, from hourly resolution)

## Phase 4: Test + Deploy

1. Syntax check: `python3 -c "from polymarket.run_1h_live import run_cycle; print('OK')"`
2. Dry-run: `--dry-run` for 2 hours → verify ladder placement + TP logic
3. Review logs: check order counts, fill patterns, TP triggers
4. Live deploy: `--live --bet-pct 0.03`

## Files Modified
- `polymarket/run_1h_live.py` — main changes
- `polymarket/analysis/ladder_backtest_1h.py` — NEW: 1H-specific backtest
- `polymarket/strategy/hourly_engine.py` — NO CHANGE (keep as timing signal)

## Risk Assessment
- **Biggest risk**: 1H OB is thinner → ladder orders may not fill well at deep levels
- **Mitigation**: backtest fill rates per rung; cap deep rungs at reasonable prices
- **Second risk**: 1H window is long → more time for things to go wrong
- **Mitigation**: tighter SL + earlier cancel defense than 15M
