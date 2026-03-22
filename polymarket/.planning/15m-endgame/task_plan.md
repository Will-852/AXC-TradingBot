# Task: 15M BTC MM — Structural Fix + Endgame
> Created: 2026-03-22 | FINAL revision: 2026-03-23 (28-day data)

## Goal
Fix the structural defect: **bot 入場後冇任何 exit strategy = 祈禱唔係 MM**。

28-day data (2,688 windows) 證明：
- Bridge T+2min WR = **66% everywhere**（唔分時段）
- T+5min = 72%, T+7min = 77%, T+10min = 86%（universal, 所有時段一致）
- 3-day "session matters" = **statistical noise**（39.8% / 59.0% = 假）

## The Real Problem

```
Bot 嘅 flow:
  入場 → 30 shares at $0.33 → forced hold last 5min → 0 exit options → 等死

信號冇問題（66-86% WR depending on read time）。
問題係：信號錯嗰 14-34% 嘅時候，bot 做乜？
答案：乜都冇做。

一個冇止蝕嘅系統，3% per-bet cap 係假嘅。
3 bet × -100% = -10% bankroll。冇 daily cap 攔。
```

## Phases (FINAL — Priority by Impact)

### Phase 1: Daily Loss Cap — `pending`
**0 行 trading logic 改動。純 risk management。**
- If daily realized loss > $15 (5% of $300) → stop trading until next day
- Check at start of each heavy cycle: sum today's resolved PnL
- Use existing circuit breaker framework (already has 15% daily loss trigger)
- **Fix needed**: current circuit breaker counts ALL markets (incl zero-fill). Fix to count only filled trades.
- ~5 lines

### Phase 2: Delay Bridge Read (T+2→T+5min) — `pending`
**Biggest single WR improvement. +6pp. All hours. 2,688 windows prove it.**
- Current: M1 momentum filter has 180s (3min) deadline. Bot enters at ~T+2min.
- Fix: extend M1 deadline from 180s to 300s (5min). Bot enters at ~T+5min.
- WR: 66% → 72% (universal improvement, no session dependency)
- Trade-off: later entry → token price may have moved → potentially worse fill price
  - But: 66% × bad_fills < 72% × slightly_worse_fills (net positive)
- At T+7min: WR = 77%. Consider making deadline configurable (300s default, test 420s)
- ~3 lines (change `_MOMENTUM_DEADLINE_MS` from 180000 to 300000)

### Phase 3: Last-Minute Hedge — `pending`
**Max loss per trade cap. The structural fix.**
- At T-120s, for each OPEN position with filled shares:
  - Check BTC 30s momentum vs our direction
  - If BTC momentum AGAINST our position AND |move| > $30:
    - Buy 30-50% of position in OPPOSITE token
    - `mkt["hedge_placed"] = True` guard (one per market)
- Effect:
  ```
  Without hedge: wrong direction = -100% of cost
  With hedge:    wrong direction = -50% of cost (hedge pays partial)
                 right direction = -15% of profit (hedge cost)
  ```
- This is a BUY path (not sell) → no forced-hold conflict
- ~30 lines
- **Order path audit MANDATORY** (new buy path = $106 bug territory)

### Phase 4: Endgame Data Collection — `complete` ✅
Already coded + 2checked. 1 share underdog, daily cap 10, hold to resolution.

### Phase 5: Whale Threshold + Logging — `pending`
- Whale: 0.30 → 0.08 (match data max 0.133)
- Drop h_delta (always 0 in 15M)
- Add h_imb to signal_log
- ~20 lines

### ~~Phase B: Time-of-Day Gate~~ → **DROPPED**
28-day data: all sessions have identical T+2min WR (65-67%). Session-based gate is noise-fitting.
Keep as logging/monitoring. Do NOT use for trading decisions until 3+ months of data.

### Phase 6: 2check + Order Path Audit — `pending`
- Opus cross-check ALL changes
- Order path audit (MANDATORY — endgame + hedge = 2 new buy paths)
- Worst trade trace per phase

## Worst Trade Analysis (FINAL)

### Current (no fixes)
```
Any hour (equally likely). Bridge fires at T+2min. WR = 66%.
Bot buys 30 DOWN at $0.33 (cost $9.97). Direction wrong (34% chance).
T-300s: Forced hold. Cannot exit.
T-0s: UP wins. Loss = -$9.97 (100%).

3 trades × -100% = -$29.91 = -10% bankroll. No daily cap stops it.
P(3 consecutive losses) = 0.34³ = 3.9% — happens ~once per 26 trades (~7 hours).
```

### After Phase 1 (daily loss cap)
```
Same as above, but after 2nd loss ($20 cumulative):
Daily cap triggers at $15 → bot stops.
3rd trade never happens. Max daily loss = ~$20 (not $30).
```

### After Phase 1+2 (delay read)
```
Bridge waits T+5min. WR = 72%.
Wrong direction = 28% (not 34%).
P(3 consecutive) = 0.28³ = 2.2% — once per 46 trades (~12 hours).
Each trade still -$9.97 if wrong.
```

### After Phase 1+2+3 (delay + hedge)
```
Bridge waits T+5min. WR = 72%. Wrong = 28%.
At T-120s, BTC moves against → hedge placed (5 shares opposite at ~$2.50).
If wrong: -$9.97 + $2.50 hedge payout ≈ -$7.50 (not -$9.97)
If right: +$20.13 - $2.50 hedge cost = +$17.63 (not +$20.13)

Worst single trade: -$7.50 (not -$9.97)
Worst day (capped): -$15 (daily cap)
```

### Summary
| State | Worst trade | Worst day | P(3 consec loss) |
|---|---|---|---|
| Current | -$9.97 | **-$30** | 3.9% |
| +Phase 1 | -$9.97 | -$20 (capped) | 3.9% |
| +Phase 2 | -$9.97 | -$20 (capped) | **2.2%** |
| +Phase 3 | **-$7.50** | **-$15 (capped)** | 2.2% |

## Errors Log
| # | Phase | Error | Resolution |
|---|-------|-------|------------|
| 1 | — | Bot not stopped from prev session | Killed. Saved to gotchas. |
| 2 | — | 3-day WR data = noise (39.8%/59.0%) | 28-day data corrected (65-67% everywhere) |
| 3 | — | "Flip signal" idea based on noise | DROPPED |
| 4 | — | "Time-of-day gate" based on noise | DOWNGRADED to monitoring |
| 5 | 4 | Missing entry_cost on endgame fill | Fixed |
| 6 | 4 | Crash → double submit | Fixed (guard before execute) |

## Key Files
- `polymarket/run_mm_live.py` — all changes
- `polymarket/logs/mm_trades.jsonl` — 145 resolved markets
- `polymarket/logs/mm_order_log.jsonl` — 383 order events
- Binance 1-min klines — 40,320 candles (28 days)
