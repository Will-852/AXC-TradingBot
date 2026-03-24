# Task: 15M Bot — Survive the Arb, Kill the Lean
> Created: 2026-03-24 24:30 HKT (v4, definitive session synthesis)
> Bot: RUNNING (PID 90322, nohup)
> Bankroll: $232.31 (test allocation, kill switch -$50)
> Code: Dynamic ratio LIVE, Door B ACTIVE, :45 skip ACTIVE

---

## Executive Summary: What We Actually Know

```
SESSION VERDICT: The bot has a THIN arb edge and a LOSING directional bet.

Old bot (R=1.5): 14% fill rate, WR 0% on both-fills, PnL -$12.57/trade
New bot (R=1.2): 90.6% fill rate, WR 65.4%, PnL -$0.357/trade
Simulated R=1.0: PnL +$5.27 total = +$0.20/trade (POSITIVE)

The lean contribution across 26 trades = -$14.55 (THE drag).
Break-even WR for lean at current ratio = 75.9%.
Actual WR = 65.4%. Gap = 10.5 percentage points.

One trade at effective R=2.7 lost $10.44 = wiped entire session profit.
T1+T2 creates effective R up to 4.2x despite config at 1.2x.

$0.20/trade arb margin is razor thin but REAL.
```

---

## The Three Hard Truths

### Truth 1: Lean is currently negative EV
- 26 lean trades contributed -$14.55
- Break-even WR for lean = 75.9%, actual = 65.4%
- The "momentum paradox": lean bid becomes stale as market moves your way, hedge fills at worse price
- Lean looks like alpha (WR 78.8% on direction) but the PROFIT WR is only 65.4%
- Confidence: HIGH (26 trades, consistent pattern)

### Truth 2: Pure arb works but barely
- R=1.0 simulated: +$0.20/trade, +$5.27 over 26 trades
- Combined mid $0.909 = 9.1% structural discount, 92% maker = $0 fees
- But: $0.20/trade at $5.50/trade size = 3.6% margin per round trip
- Fill rate 90.6% CI = [75.8%, 96.8%] — could be as low as 76% out-of-sample
- All data from ONE evening (US hours). Overnight/Asian hours = unknown.
- Confidence: MEDIUM (small sample, one session, wide CI)

### Truth 3: We don't have enough data to make permanent decisions
- 26 both-fill trades (new bot) is NOT statistically significant
- Fill rate CI is 21pp wide
- No overnight data, no weekend data
- 5M bot just started (zero resolved trades)
- Any "optimization" on 26 trades is curve-fitting. Period.

---

## Immediate Crisis: Effective R Flying to 4.2x

### Problem
Config says R=1.2. But T1 (60%) + T2 (40%) staged entry means:
- T1 alone: effective R = lean_T1 / hedge = ~0.72x (safe)
- T1+T2 both fill: effective R = (lean_T1 + lean_T2) / hedge = up to 4.2x
- T2 is supposed to CONFIRM, but it also AMPLIFIES the bet
- One wrong T2 at high R wiped the session (the $10.44 trade at R=2.7)

### Fix (Phase 0, immediate)
T2 lean sizing must be CAPPED so total effective R never exceeds config R.
```
If T1 lean = 60% × R × hedge_shares:
  T2 lean_max = R × hedge_shares - T1_lean_filled
  If T2_lean_max ≤ 0: skip T2 lean entirely (hedge already placed)
```
This is a CODE BUG, not a parameter choice. Fix before collecting more data.

---

## Execution Plan

### Phase 0: Emergency Fixes (before next trade)
> Effort: 1-2 hours | Risk: LOW (all are tightening, not loosening)

- [x] 0.1 **CAP effective R**: T2 lean sizing must respect config R ceiling (see above)
- [ ] 0.2 **Budget cap restart**: SOL min_order_size inflates spend 4.2x (bug #6, fix written, needs restart)
- [ ] 0.3 **Build daily dashboard** (still not done):
  ```
  Date | Trades | BothFill% | WR | PnL | CumPnL | EffR_max | Lean_contrib | Arb_contrib
  ```
  Must separate lean contribution from arb contribution per trade.
- [x] 0.4 Restart bot with 0.1 + 0.2 applied
- [ ] 0.5 Verify: first 3 trades, check effective R ≤ config R

### Phase 0.5: Danger Zone Fixes (2026-03-24 audit)
> **Danger Zone doc**: `docs/DANGER_ZONES.md` — 9 known high-risk locations
> DZ-1 (reprice race) FIXED. DZ-2~4 are CRITICAL, must fix before 50-trade checkpoint.

- [x] 0.5.1 **DZ-1: Reprice race condition** — WS pre-check + post-cancel verify (FIXED)
- [x] 0.5.2 **DZ-4: Instant fill partial-fill blindspot** — `size_matched` from `takingAmount` (FIXED, 6 sites)
- [x] 0.5.3 **DZ-3: Exit paths no fill confirmation** — check sell status before reducing shares (FIXED, 4 sites)
- [x] 0.5.4 **DZ-2: Resolve no on-chain reconciliation** — pre-resolve `get_trades` reconciliation (FIXED)
- [x] 0.5.5 **DZ-5: _execute orphan order** — VERIFIED SAFE, no fix needed (false alarm)

### Phase 1: Data Collection (50-trade checkpoint)
> Duration: 2-5 days depending on signal frequency
> Goal: Get CLEAN data with fixed effective-R to make real decisions
> NO parameter changes during this phase

**What's running (AFTER Phase 0 restart):**
- BTC 15M: LIVE, 3%, **R=1.0 pure arb** (lean killed, ratio cap 1.1)
- SOL 15M: LIVE, 1% (with budget cap fix)
- ETH 15M: DRY-RUN (paper trades, collecting data)
- XRP 15M: DRY-RUN (discovery mode)
- Combined gate: < $0.99 (reject if arb spread too thin)
- Dead hours: skip HKT 22-06 (low liquidity, unvalidated)
- 5M bot: DRY-RUN (all 4 coins, separate evaluation)
- :45 windows: SKIPPED
- Door B: ACTIVE
- Heavy cycle: 3s

**What to log per trade:**
```
trade_id | coin | signal_bps | tier | config_R | effective_R |
T1_lean | T2_lean | hedge | lean_pnl | arb_pnl | total_pnl |
fill_time_lean | fill_time_hedge | door_b_triggered | timestamp
```

**Checkpoint at 25 trades (early warning):**
- If PnL < -$15 → STOP immediately, do not wait for 50
- If effective R still exceeding config R → code bug not fixed
- If fill rate < 70% → market conditions changed, pause

**Checkpoint at 50 trades (decision gate):**

| Metric | Gate | Action if FAIL |
|--------|------|----------------|
| Overall WR | ≥ 55% | STOP. Edge may not exist. |
| Both-fill rate | ≥ 80% | Investigate pricing/timing |
| Arb-only PnL (R=1.0 trades) | > $0 | If negative, structural problem |
| Lean contribution | > -$5 total | If worse, go full R=1.0 |
| Max single loss | < $8 | If exceeded, effective R cap broken |
| Effective R max | ≤ config R | If exceeded, Phase 0 fix failed |

### Phase 2: Path Selection (after 50-trade gate)
> This is where the real decision happens.
> Three paths, ranked by user + data.

#### Path A: Sub-Range Filter (user rating: 9/10)
**When**: 50 trades show 5-7bps WR significantly > 8-10bps WR
**What**: Split 5-10bps tier into 5-7bps (keep R=1.2) and 8-10bps (drop to R=1.0)
**Why it could work**: Adverse selection is a gradient, not a cliff. 5-7bps may be the sweet spot — enough momentum to fill, not enough to be priced in.
**Risk**: Sample size per sub-range will be ~12-15 trades. Statistical power is LOW.
**Validation**: Need 30+ trades per sub-range to have any confidence.
**Confidence that this helps**: MEDIUM (theory is sound, data is thin)

#### Path B: Pure Arb R=1.0 (sacrifice direction)
**When**: 50 trades show lean contribution still negative
**What**: Set R=1.0 for ALL tiers. Bot becomes pure spread harvester.
**Why it could work**: Simulated +$0.20/trade. Zero directional risk. Consistent with whale LampStore model (maker, 86% both-fill, massive throughput).
**Risk**: $0.20/trade × 20 trades/day = $4/day. At $232 bankroll = 1.7%/day. Decent if it holds. But $0.20 is THIN — any market condition change could flip it negative.
**Validation**: 50 trades at R=1.0, PnL > $0.
**Confidence that this works**: MEDIUM-LOW (one session of data, wide CI on fill rate)

#### Path C: Speed Optimization (faster execution)
**When**: Fill rate is the binding constraint
**What**: Reduce latency (3s → 1s cycle, precompute OB, parallel order submission)
**Why it could work**: Faster fill = less adverse selection. Whale Unlawful-Shear pays spread for certainty ($1.04 combined) but gets 97% coverage.
**Risk**: Python latency floor ~200ms. Marginal gains. Won't solve the lean problem.
**Validation**: Compare fill rate before/after.
**Confidence that this helps**: LOW as standalone. Useful as complement to Path A or B.

#### Decision Matrix at 50 trades:

```
IF lean_contribution > $0 AND WR ≥ 67%:
  → Stay current. Consider scaling to 5%.

IF lean_contribution ∈ [-$5, $0] AND WR ≥ 60%:
  → Path A (sub-range filter). Collect 30 more trades per sub-range.

IF lean_contribution < -$5 OR WR < 60%:
  → Path B (R=1.0 pure arb). Run 50 more trades.

IF both-fill rate < 80%:
  → Path C first (speed), then re-evaluate.

IF WR < 55% AND arb PnL < $0:
  → STOP. Reassess entire strategy. The edge may not exist at current spreads.
```

### Phase 3: Validate Selected Path (50 more trades)
> Only enter this after Phase 2 decision is made

- [ ] 3.1 Implement selected path changes
- [ ] 3.2 2check + BMD audit (mandatory for any code change)
- [ ] 3.3 ORDER PATH AUDIT (mandatory — real money)
- [ ] 3.4 Run 50 trades with new params
- [ ] 3.5 Compare: Phase 1 baseline vs Phase 3 results
  - Statistical test: paired comparison on PnL/trade
  - If Phase 3 worse: revert and accept Phase 1 as ceiling
  - If Phase 3 better: user decides scaling

### Phase 4: 5M Market Evaluation (parallel, dry-run only)
> Separate from 15M. Different dynamics. Different edge.

**Data collection (ongoing):**
- 5M bot running dry-run on BTC, ETH, SOL, XRP
- Log: signal strength, fill simulation, resolution, spread

**Evaluation at 200 dry-run trades:**
- [ ] 4.1 WR by coin × signal tier
- [ ] 4.2 Spread analysis (is 9.1% discount still present?)
- [ ] 4.3 Fill rate simulation (5M = faster market = more adverse selection?)
- [ ] 4.4 Overlap with 15M signals (same or different alpha?)
- [ ] 4.5 Decision: promote to live or kill

**5M-specific risks:**
- Higher frequency = more fees if taker rate increases
- 5M resolution = less time for price to mean-revert = more adverse selection
- Completely unvalidated. Treat as pure experiment.

---

## Whale Benchmarks (for calibrating expectations)

| Whale | Style | Combined Cost | Fill Rate | Revenue | Lesson |
|-------|-------|--------------|-----------|---------|--------|
| Unlawful-Shear | Taker, directional | $1.04 (pays spread) | 97% | $119K/34d | Fill certainty > spread savings |
| LampStore | Maker, throughput | $0.97 (tight) | 86% both-fill | $115K/19.5K mkts | Volume × thin margin = money |
| Awful-Alfalfa | Hybrid multi-TF | Unknown | Unknown | $326K/24d | Multi-timeframe + extreme tilt |
| Decent-Dune | Both-sides arb | Unknown | Unknown | $426K/162d | $507 → $426K, 840x |

**Our position**: Maker, thin margin, low volume. Most similar to early Decent-Dune.
**Honest assessment**: We are at the bottom of the food chain. $0.20/trade edge with 20 trades/day = $4/day theoretical. Whales make $3K-$14K/day. The gap is 3 orders of magnitude.
**Path to whale**: Either (a) prove edge scales with bet size, or (b) prove edge scales with coin count + timeframe count. Both unproven.

---

## Data-Backed Decisions (updated)

| # | Decision | Evidence | Confidence |
|---|----------|----------|------------|
| 1 | Directional alpha exists | z=3.31, WR 79% vs random 50-53% | HIGH (p<0.01) |
| 2 | Payout structure is broken | win $0.41, lose $1.90, loss/win=4.6x | HIGH |
| 3 | Adverse selection confirmed | 5-10bps=76% WR, 10-20bps=52% | HIGH (3 datasets) |
| 4 | R=1.0 simulated profitable | +$0.20/trade, +$5.27/26 trades | MEDIUM (one session) |
| 5 | Lean = THE drag | -$14.55 across 26 trades | HIGH |
| 6 | Effective R explodes via T1+T2 | Up to 4.2x despite config 1.2x | HIGH (code confirmed) |
| 7 | One bad trade wipes session | $10.44 loss at R=2.7 | HIGH (observed) |
| 8 | 92% maker, $0 fees | CSV analysis, 619 rows | HIGH |
| 9 | :45 windows = 27% WR | 52 trades, anomaly | MEDIUM (small n) |
| 10 | Fill rate 90.6% (new bot) | 32 trades | MEDIUM (CI: 76-97%) |
| 11 | Fill rate 14% (old bot) | Old config | HIGH (clearly broken) |
| 12 | Door B = risk mgmt only | Save ~$1.84 expected, not $6.30 | HIGH (corrected) |
| 13 | BTC was net DOWN period | 53.5% DOWN resolution | MEDIUM |
| 14 | No market manipulation | Volatility 0.82x at boundaries | MEDIUM |

## BMD Warnings (carry forward + new)

| # | Warning | Status |
|---|---------|--------|
| 1 | ~~74% WR CI too wide (n=19)~~ | Superseded: now 65.4% WR at n=26 |
| 2 | Door B is backfitted on 33 trades | OPEN — monitor out-of-sample |
| 3 | ~~Sim must adjust cost + redeem~~ | FIXED: f10_validate.py gate |
| 4 | Cap ratio hurts if WR > 75% | OPEN — WR is 65.4%, not relevant yet |
| 5 | ~~Fee impact unknown~~ | FIXED: $0 fees |
| 6 | "Improving trajectory" is likely noise | OPEN — don't draw trends |
| 7 | Sunk cost: -$234 already gone | PERMANENT |
| **8** | **$0.20/trade is razor thin** | **NEW — any condition change flips it** |
| **9** | **Fill rate CI = [76%, 97%]** | **NEW — could be 15pp worse than point estimate** |
| **10** | **All data from one evening session** | **NEW — overnight/weekend = unknown** |
| **11** | **Effective R bug amplifies losses** | **NEW — fix in Phase 0 before more data** |
| **12** | **SOL budget cap bug = 4.2x overshoot** | **NEW — fix written, needs restart** |

## What We're NOT Doing (and why)

| Excluded | Why |
|----------|-----|
| Scaling bet size | Not until 100+ validated trades |
| Aggressive lean pricing (mid instead of mid-1) | Door B handles lean-miss. Q4 deferred. |
| Hedge-miss aggressive repricing | 12.1% rate, -$0.07/trade drag. Tolerable. Phase 3+. |
| More wallet reverse engineering | Left side answered. Right side = own data now. |
| Rust rewrite / infra optimization | Python latency not the binding constraint |
| Multi-timeframe (1H overlay) | One variable at a time. 15M first. |
| Chainlink integration | Signal quality not the problem |
| Going taker for fill certainty | Would add ~$0.10/side fee. Kills thin margin. |
| Optimizing on 26 trades | That's curve-fitting. Collect data first. |

---

## Success Criteria (before ANY scaling)

```
100 total trades (50 Phase 1 + 50 Phase 3) with:
├─ Overall PnL > $0 (any amount — we need to prove we can make money)
├─ Per-trade PnL > $0.05 (above noise floor)
├─ No single trade loss > $8 (effective R cap working)
├─ Both-fill rate > 80% (execution quality)
├─ Arb component PnL > $0 (the floor works)
├─ Lean component PnL > -$10 (directional bet is bounded)
├─ Data from ≥ 3 different sessions (not one lucky evening)
├─ Data from ≥ 2 time zones (US hours + Asian hours)
└─ Daily dashboard reviewed by user ≥ 5 times

ALL must pass. Any fail → stop, diagnose, don't scale.
```

---

## Open Questions (ordered by impact)

1. **Does the arb edge survive overnight/weekend?** — Unknown. Need data.
2. **Is 65.4% WR the true WR or was this session an outlier?** — Need 100+ trades.
3. **Does effective R cap fix the single-trade blowup problem?** — Need Phase 0 code fix + verification.
4. **Is 5-7bps genuinely better than 8-10bps, or is it noise?** — Need 30+ trades per sub-range.
5. **Can this scale with bet size?** — At $5.50/trade, we're invisible. At $50/trade, we might move the book. Unknown.
6. **5M: different alpha or same signal faster?** — Dry-run data will tell.

---

## Files Reference

| File | Purpose |
|------|---------|
| `polymarket/run_mm_live.py` | Main bot (dynamic ratio, :45 skip, Door B, per-coin sizing) |
| `polymarket/analysis/f10_validate.py` | Sim validation gate (6 checks) |
| `polymarket/analysis/fee_analysis.py` | Fee analysis script |
| `task_plan.md` | This file |
| `progress.md` | Session timeline + state |
| `findings.md` | All findings F0-F15 |
