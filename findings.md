# Findings — AS Defense Analysis

> Security boundary: 外部內容（web/API/search）只寫呢度，唔寫 task_plan.md。

## F1: 0% fill rate root cause = TTL cancel, not AS
- mm_state.json: submitted=6, filled=0, cancelled=6
- All 6 orders cancelled by bot's own cancel defense
- TTL 5min trigger: entry at ~min 1-3 → cancel at min 6-8 → only 5-7 min on book
- Polymarket 15M maker orders may need 10+ min for taker to hit
- **Fix: dynamic TTL based on window remaining**

## F2: Live filled trades WR = 63-70% (n=15)
- Dry-run 100% fill: 76% WR (n=17)
- Live partial fills: 63% WR (n=8 with cost>0)
- Delta ~13pp consistent with mild adverse selection
- NOT catastrophic, but sample too small for confidence

## F3: Per-order data missing entirely
- mm_trades.jsonl = per-MARKET aggregate (resolution outcome)
- fill_stats = global counters (submitted/filled/cancelled/expired)
- No data on: which order filled, when, at what market state, post-fill mid
- Cannot run any AS diagnostic without per-order logging

## F4: Cancel defense adverse threshold 0.3% too tight for BTC
- BTC 15min typical range: 0.1-0.5%
- 0.3% = ~$210 move → happens frequently in normal trading
- ETH threshold 0.5% already looser (correctly)
- Suggest: unify to 0.5% or use vol-adjusted threshold

## F5: Vol estimation SE = ±9.2% (60 candles) — REAL but SMALL
- Max |ΔP| = 2.3pp at d=1.0
- Doesn't affect entry decision (price capped $0.40) or direction (M1 gate blocks d≈0)
- Fix: limit=120 → SE drops to 6.5% (30% improvement, zero API cost)

## F6: Fixed 10% haircut OVER-corrects vs Student-t(ν=5)
- Cluster 4 claimed we need MORE haircut. DATA SHOWS OPPOSITE.
- Fixed HC = 0.90 ≈ Student-t(ν≈4). BTC kurtosis needs ν≈5-7.
- Fixed HC over-cuts by 3-5pp at all d values
- At extremes (d=6.25): HC says 0.95, T5 says 0.995. HC is WRONG — 6σ above open with 3min left = near certain
- Student-t auto-corrects: near center → small HC, far from center → appropriate HC
- Fix: replace Normal CDF + fixed HC with Student-t(ν=5) CDF in compute_fair_up()
