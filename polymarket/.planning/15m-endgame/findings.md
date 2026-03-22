# Findings: 15M Last-Minute Strategy
> Updated: 2026-03-22 | 3x opus agents completed

## Agent 1: Last-Minute Market Microstructure

**Data**: 8,041 BTC ticks, 209 markets, 193 with confirmed outcomes (99 UP, 94 DOWN)

### Mid Polarization (SEEN)
| Checkpoint | Extreme (>0.95 or <0.05) | Undecided (0.10-0.90) |
|---|---|---|
| T-120s | 49.2% | 43.7% |
| T-60s | 74.2% | 12.9% |
| T-30s | 81.3% | 11.4% |
| T-0s | 97.0% | 0.0% |

### Direction Flip Rate (SEEN)
| At | Flips | Rate |
|---|---|---|
| T-120s | 8/176 | 4.5% |
| T-60s | 9/177 | 5.1% |
| T-30s | 1/161 | 0.6% |
| T-10s | 0/145 | 0.0% |

Flip by mid confidence at T-120s:
- Weak (0.55-0.65): 4/11 = **36.4%** ← only live zone
- Moderate (0.65-0.80): 2/21 = 9.5%
- Strong (0.80-0.95): 2/39 = 5.1%
- Extreme (>0.95): 0/103 = **0.0%**

### 💀 Cheap Token = Death Ticket (SEEN)
**Buying cheapest side (<= $0.10) in last 60s: 0 wins / 189 markets = 0% WR**
Even at T-120s with ≤$0.10: 1 win / 119 = 0.8% WR

Underdog buying EV:
| At | WR | Avg price | EV/trade |
|---|---|---|---|
| T-120s | 4.5% | $0.096 | -$0.050 |
| T-60s | 5.1% | $0.059 | -$0.008 |
| T-30s | 0.6% | $0.029 | -$0.022 |

### Volatility by Time Bucket (INFERRED)
| Bucket | σ_poly | vs baseline | P99 |Δmid| |
|---|---|---|---|
| [0-5min] | 0.0651 | 1.00x | 0.200 |
| [10-13min] | 0.0831 | 1.28x | 0.336 |
| [14-15min] | 0.0935 | 1.44x | **0.517** |

### OB Depth (SEEN)
Depth drops ~70% from opening ($106K) to T-2min ($30K). But thin because already decided, not because of opportunity.

---

## Agent 2: Current Code — End-of-Window Paths

### Timeline (ALL SEEN)
| TTE | Event | Lines | Effect |
|---|---|---|---|
| T-300s | FORCED HOLD | 1837 | **ALL exits disabled** — no profit lock, no SL, no cost recovery. ZERO exceptions. |
| T-120s | Cancel ALL | 1729 | All pending orders cancelled |
| T-120s | Phased rungs blocked | 1628 | No new conditional orders |
| T-120s | Holder burst 5s | 1331 | Only in entry path (narrow window) |
| T-90s | Late entry gate | 1157 | No new entries, market removed from watchlist |
| T-90s | Re-entry gate | 2029 | No new scalp rounds |
| T+0s | Window ends | 594 | Pending orders expired |
| T+120s | Resolution | 878 | Binance kline check, PnL calc |

### Key: Black Swan CANNOT fire in last 5 min
Forced hold `continue` at line 1838 fires BEFORE profit lock check. Even at mid=0.99, bot cannot sell. By design (market rejects orders ~4 min before end).

### Dead Zone: T-300s to T-0s
Bot is fully passive. Cannot exit. Cannot enter (after T-90s). Just holds and waits for resolution.

### Stale comment at line 2029
Says "Enough time left in window (>4 min)" but threshold is actually 90s (1.5 min).

---

## Agent 3: Signal Quality + Whale Accuracy

**Data**: 136 signal rows, 383 order events, 145 resolved markets

### 💀 Whale Signal is DEAD (SEEN)
- whale_action = "NORMAL" in 46/46 events (100%)
- Zero instances of AGREE, FOLLOW_LOG, or EXIT
- **The whale classifier has NEVER triggered in production**

### 💀 h_imbalance is Tiny (SEEN, n=46)
| Stat | Value |
|---|---|
| Mean | 0.009 |
| Max | 0.133 |
| Min | -0.094 |
| |h_imb| > 0.30 | **0 / 46 = 0%** |
| |h_imb| > 0.10 | ~10% |

**Threshold 0.30 is 2.3x higher than the maximum ever observed (0.133)**. The whale gate can NEVER fire with current data.

### 💀 h_delta is DEAD (SEEN)
Every value is exactly 0.0. Zero variance. Data source broken or too slow to capture changes.

### Cross-Exchange Signals NOT LOGGED (SEEN)
funding_agg, funding_premium, oi_total, oi_delta_5m, ls_ratio, dvol — none exist in log files. Cannot evaluate.

### PnL Deterioration (SEEN)
| Date | PnL | WR | n |
|---|---|---|---|
| Mar 20 | +$167.51 | 43% | 77 |
| Mar 21 | +$24.43 | 17% | 41 |
| Mar 22 | -$8.57 | 6% | 16 |

### Time-of-Day (SEEN, n per hour = 4-12)
**Good**: 01-02 HKT (80-83% WR), 09-10 HKT (67-78% WR)
**Bad**: 00, 04-06, 11, 23 HKT (0% WR)

### Fill Rate (SEEN)
22.6% overall. Uniform across bridge conviction levels (~22-29%). Pricing (distance from mid) is the dominant fill factor, not signal strength.

---

## Z-Score Math Review (from user analysis)
- min_samples must be ≥20 (stdev ±33% error at n=5)
- Imbalance + delta need separate ring buffers
- Imbalance is fat-tailed → z=2 ≈ 8-15% event (not 4.55%)
- Phase 2: use empirical percentile instead of fixed z threshold
- Whale absorption into baseline is a feature

---

## Synthesis: What the Data Actually Says

### REJECTED hypotheses:
1. ❌ "Last 60s cheap tokens are lottery tickets" → **0% WR on DECIDED markets (0/189)**
   - ⚠️ BUT: This tested the WRONG question. User asked about UNDECIDED markets.
   - 43.7% of markets still undecided at T-120s. Weak (mid 0.55-0.65) flip 36.4% (n=11, CI very wide)
2. ⚠️ "Whale signal never triggers" → TRUE with threshold 0.30, but max observed = 0.133
   - Threshold 0.30 is 2.3x above data max → whale gate is effectively disabled
3. ⚠️ "h_delta always zero" → TRUE, but likely NOT a bug — 15M holder composition doesn't change within-market
4. ❌ "Cross-exchange signals aren't logged" → WRONG — they ARE in mm_signals.jsonl under nested `mkt` key (fund_agg, oi_total, etc.), conditionally

### CONFIRMED insights:
1. ✅ Direction locked early — 95.5% at T-120s, 100% at T-10s (for decided markets)
2. ✅ Weak markets (mid 0.55-0.65) are live — 36.4% flip (BUT n=11, CI=[11%,69%])
3. ✅ Time-of-day matters: 01-02 HKT good (80%), 04-06/11/23 HKT bad (0%)
4. ✅ PnL deterioration = MIXED: partly vol regime (Mar 21 dead), partly code/fill issue (Mar 22 vol recovered but fills didn't)

### Gate Test Results (BMD Round 2)

**Gate 1: "Market rejects orders in last 4 min"** ✅ VERIFIED FALSE
- VERDICT: **DEFINITIVELY FALSE**
- 0/106 orders ever rejected in logs
- `cancelled_external` = race condition (one was actually a fill!)
- **Live poll (2026-03-23 00:19 HKT)**: `acceptingOrders=True` from TTE=590s → TTE=-50s
  - Market stays open **50 seconds AFTER window ends**
  - `closed` stays `False` even at T+50s
- Trades Data API: all 5 recent markets had trades in last 1 min, latest at TTE=-13s
- **The 5-min forced hold, 2-min cancel, 1.5-min late gate are ALL self-imposed**
- Polymarket 15M markets accept orders from start to ~T+50s (until resolution ~T+120s)

**Fill Rate by Rung Price** (SEEN, n=108):
| Rung | Price | Submits | Fills | Fill% |
|------|-------|---------|-------|-------|
| 1 | $0.43 | 34 | 8 | 23.5% |
| 2 | $0.37 | 37 | 12 | 32.4% |
| 3 | $0.31 | 27 | 3 | 11.1% |
| 4 | $0.26 | 10 | 1 | 10.0% |

**Gate 2: BTC vol regime change**
- VERDICT: **MIXED — vol explains Mar 21, code explains Mar 22**
- Mar 21 vol = 8.9bp (vs 26bp normal) → genuine dead zone → explains low fills
- Mar 22 vol = 19.7bp (recovered 76%) → BUT fill rate CONTINUED declining (45%→27%→17.8%)
- **Corrected WR** (excl zero-fills): Mar 20 = 79%, Mar 21 = 78%, Mar 22 = 20% (n=5)
- **Smoking gun**: Bridge confidence INCREASED (0.34→0.54) while momentum DECREASED → model overconfident
- Fill rate decoupled from vol recovery = pricing/code issue, not pure regime

**Gate 3: Signal logging**
- 37 signals computed, 32 logged, 5 never logged
- Cross-exchange IS in mm_signals.jsonl (nested `mkt` dict, conditional)
- Cross-exchange NOT in mm_order_log → can't correlate with fills
- h_imb/h_delta ONLY in order_log submit (not signal_log — written before holder fetch)
- Phased rung submits have ZERO signal context
- Re-entry submits have MINIMAL context (fair, round, bridge only)

### TRUE priorities (revised after ALL gating tests — 2026-03-23):
1. ✅ ~~**P0: Verify late order placement**~~ — **CONFIRMED: market accepts orders until T+50s**
2. **P1: Design endgame strategy** — undecided markets (43.7% at T-120s), micro-bet in last 90s
3. **P2: Fix whale threshold** → 0.30 → 0.08-0.10 (h_imb max=0.133)
4. **P3: Fix signal logging** — h_imb→signal_log, cross-exchange→order_log, ctx for re-entry+phased
5. **P4: Expand dead hours** — add 00/11/23 HKT (0% WR)
6. **P5: Z-score filter (log-only)** — imbalance buffer, min_samples=20
7. **P6: Stale comments** — "market rejects" → "self-imposed guard", "4 min" → "1.5 min"
8. **P7: Monitor fill rate** — Mar 22 decline (17.8%) may be n=16 noise. Need 50+ markets.
