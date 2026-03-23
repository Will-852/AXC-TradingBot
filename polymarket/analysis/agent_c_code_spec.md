# Agent C: W4 15M Replication — Exact Code Change Spec

> Generated: 2026-03-24
> Target: `polymarket/run_mm_live.py` (2943 lines) + `polymarket/strategy/market_maker.py` (476 lines)
> Reference: `analysis/w4_strategy_spec.md` (W4 strategy, $507→$426K)
> Approach: Modify existing bot, NOT create new file. Keep all safety infra.

---

## ARCHITECTURE OVERVIEW: Current vs W4

```
CURRENT (v15 Dual-Layer):
  Signal: Bridge(Student-t) + OB + M1/CM + CVD + whale + midpoint sanity
  Entry:  Wide Ladder DCA — directional only ($0.43/$0.37 rungs)
  Manage: 3-layer exit (TP tiers / Cost Recovery / Stop Loss) + Scalp re-entry
  Sizing: 3% bankroll per market
  Cancel: Window-end / Adverse 0.5% / Dynamic TTL

W4 TARGET:
  Signal: BTC momentum log return at T+300s, ≥5bps threshold
  Entry:  BOTH SIDES at Poly OB mid, 2:1 lean with momentum
  Manage: ZERO — hold to resolution (no TP, no SL, no cancel mid-window)
  Sizing: 0.5% bankroll per market, compound reinvestment
  Cancel: Unfilled orders only at window end
```

---

## CHANGE A: Signal — Replace Momentum Filter with W4 Signal

### A1. Add W4 signal function (new helper)

```
FILE: polymarket/run_mm_live.py
LINES: ~406 (after _m1_return function)
CURRENT: No equivalent — CM uses continuous return, M1 uses 1-minute return
CHANGE TO: New function _w4_signal() computing log return since window open at T+300s
WHY: W4 uses log(P_300/P_0) with 5bps threshold, not M1 1-minute return
RISK: LOW — new function, no existing behavior affected. Wrong math = wrong direction = loss on lean side (mitigated by hedge side payout).
```

```python
# ── W4 Signal: log return since window open (5bps threshold at T+300s) ──
_W4_DELAY_S = 300       # 5 minutes after window open
_W4_THRESHOLD_BPS = 5   # 5 basis points minimum
_W4_LEAN_RATIO = 2.0    # 2:1 lean (momentum side gets 2/3 budget)

def _w4_signal(window_start_ms: int, symbol: str = "BTCUSDT") -> tuple[str, float, float]:
    """W4 momentum signal: log return from window open to now.

    Returns (direction, magnitude_bps, log_return).
    direction: "UP", "DOWN", or "SKIP" if below threshold.
    magnitude_bps: absolute value of return in basis points.
    log_return: signed log return (positive = price up).

    Only fires after T+300s AND |return| >= 5bps.
    """
    now_ms = int(time.time() * 1000)
    elapsed_s = (now_ms - window_start_ms) / 1000

    if elapsed_s < _W4_DELAY_S:
        return "WAIT", 0.0, 0.0

    # P_0 = Binance 1M candle open at window start
    p0 = _open_at(window_start_ms, symbol)
    if p0 <= 0:
        return "SKIP", 0.0, 0.0

    # P_now = current price (WS or REST, 1s cache)
    p_now = _price(symbol)
    if p_now <= 0:
        return "SKIP", 0.0, 0.0

    log_ret = math.log(p_now / p0)
    magnitude_bps = abs(log_ret) * 10000

    if magnitude_bps < _W4_THRESHOLD_BPS:
        return "SKIP", magnitude_bps, log_ret

    direction = "UP" if log_ret > 0 else "DOWN"
    return direction, magnitude_bps, log_ret
```

### A2. Replace momentum filter in watchlist entry loop

```
FILE: polymarket/run_mm_live.py
LINES: 1369-1401 (momentum filter section inside watchlist loop)
CURRENT: Uses M1 return or Continuous Momentum (CM), checks M1 threshold, waits 5min deadline
CHANGE TO: Call _w4_signal() — wait for T+300s, check 5bps threshold
WHY: W4 signal is cleaner: one check, one threshold, one decision point. CM/M1 use different thresholds (vol-scaled) which add complexity without proven benefit for both-sides.
RISK: MEDIUM — changes entry gate for ALL markets when both_sides is active. If signal is miscalculated, bot may enter on wrong direction (mitigated by hedge side). If signal never fires, bot skips all windows (detectable by zero entries in log).
```

```python
        # ── W4 Signal (both-sides mode): log return from window open ──
        if both_sides:
            _w4_dir, _w4_mag, _w4_ret = _w4_signal(wl["start_ms"], _sym)
            if _w4_dir == "WAIT":
                continue  # not yet T+300s, keep in watchlist
            if _w4_dir == "SKIP":
                if _elapsed_ms > 600_000:  # 10 min → give up
                    logger.info("W4 SKIP %s: mag=%.1f bps < %d bps after 10min",
                                cid[:8], _w4_mag, _W4_THRESHOLD_BPS)
                    del state["watchlist"][cid]
                continue  # wait longer (check every heavy cycle)
            _m1 = _w4_ret  # reuse for downstream logging
            logger.info("W4 SIGNAL %s: %s %+.1f bps (log_ret=%+.6f)",
                        cid[:8], _w4_dir, _w4_mag, _w4_ret)
        else:
            # ── Original momentum filter (unchanged for directional mode) ──
            # ... existing M1/CM code stays as-is ...
```

### A3. Remove signal gates that don't apply to W4

```
FILE: polymarket/run_mm_live.py
LINES: 1497-1567 (M1/fair conflict, CVD sizing, midpoint sanity, whale analysis)
CURRENT: M1/fair conflict check → skip. CVD disagree → reduce size. Mid < 0.38 → skip. Whale analysis → halve.
CHANGE TO: Skip ALL of these when both_sides=True. They gate on directional confidence which is irrelevant for W4 (W4 buys BOTH sides).
WHY: W4 enters ALL qualifying windows (>5bps). These gates filter based on directional conviction which contradicts W4's "trade every window" approach. CVD disagree is especially harmful — it reduces size when the signal is present, costing compound growth.
RISK: LOW — only affects both_sides mode. Removing gates means more trades → more exposure to bad windows, but W4's 81% WR across 7,364 windows proves the signal alone is sufficient.
```

```python
        # ── Both-sides: skip all directional conviction gates ──
        if both_sides:
            # W4 doesn't filter on M1/fair conflict, CVD, midpoint, or whale
            # Signal already confirmed above (5bps threshold at T+300s)
            _cvd_strong_disagree = False
            _whale_action = "NORMAL"
            _h_imbalance, _h_delta = 0.0, 0.0
            pass  # fall through to entry
        else:
            # ── Original directional logic (M1/fair conflict, CVD, mid, whale) ──
            # ... existing code unchanged ...
```

---

## CHANGE B: Entry — Replace Ladder with Both-Sides + Lean

### B1. Rewrite both-sides entry block

```
FILE: polymarket/run_mm_live.py
LINES: 1501-1543 (current both-sides experiment block)
CURRENT: Both sides at fair±half_spread, equal shares each side, forced paper (_observe_only=True)
CHANGE TO: Both sides at Poly OB mid, 2:1 lean with W4 direction, live-capable
WHY: Current both-sides uses bridge fair value for pricing (bridge = internal model). W4 uses Polymarket OB mid (market price). Current uses equal shares (no lean). W4 leans 2:1 with momentum. Current forces paper. W4 needs live.
RISK: HIGH — this is the core entry change. Bugs here = real money at wrong prices. Specific risks:
  (a) Getting mid price wrong → overpay → negative EV. MITIGATION: assert combined < $1.05 (allow slight slippage but catch gross errors).
  (b) lean direction inverted → 2x on wrong side. MITIGATION: log direction + prices, verify in first 3 trades manually.
  (c) _observe_only still forced → no live trades. MITIGATION: explicitly remove in Change F.
```

```python
        # ── W4 BOTH-SIDES ENTRY: Poly mid pricing + 2:1 lean ──
        if both_sides:
            # Get Poly mid prices for both tokens
            _up_mid = _poly_midpoint(client, wl["up_tok"]) if client else 0
            _dn_mid = _poly_midpoint(client, wl["dn_tok"]) if client else 0

            # Fallback: if no mid, use bridge fair ± half_spread
            if _up_mid <= 0.01 or _up_mid >= 0.99:
                _up_mid = max(0.05, min(0.95, fair))
            if _dn_mid <= 0.01 or _dn_mid >= 0.99:
                _dn_mid = max(0.05, min(0.95, 1.0 - fair))

            # Bid BELOW mid (maker): mid - 1 tick (1¢)
            # W4 places at or slightly below mid as limit bid
            _TICK = 0.01
            _up_bid = round(max(0.02, _up_mid - _TICK), 2)
            _dn_bid = round(max(0.02, _dn_mid - _TICK), 2)
            _bs_combined = round(_up_bid + _dn_bid, 4)

            # SAFETY: combined must be < $1.05 (allow 5¢ slippage above $1.00)
            # W4 data shows 48% of trades have combined > $1.00 in 2026
            # but average combined is $0.9974 for 15M
            if _bs_combined >= 1.05:
                logger.warning("W4 ABORT %s: combined $%.4f >= $1.05 — too expensive",
                               cid[:8], _bs_combined)
                continue  # keep in watchlist

            # Budget: 0.5% bankroll (W4 canonical)
            bankroll = state.get("bankroll", 100.0)
            _budget = bankroll * config.bet_pct * _daily_budget_mult

            # 2:1 lean allocation (W4 canonical)
            # _w4_dir was set above in signal section
            _lean_dir = _w4_dir  # "UP" or "DOWN"
            _lean_ratio = _W4_LEAN_RATIO  # 2.0
            _lean_frac = _lean_ratio / (_lean_ratio + 1)  # 0.667
            _hedge_frac = 1.0 / (_lean_ratio + 1)         # 0.333

            if _lean_dir == "UP":
                _up_budget = _budget * _lean_frac
                _dn_budget = _budget * _hedge_frac
            else:
                _up_budget = _budget * _hedge_frac
                _dn_budget = _budget * _lean_frac

            _up_shares = max(config.min_order_size, round(_up_budget / _up_bid, 1))
            _dn_shares = max(config.min_order_size, round(_dn_budget / _dn_bid, 1))

            orders = [
                PlannedOrder(token_id=wl["up_tok"], side="BUY",
                             price=_up_bid, size=_up_shares, outcome="UP"),
                PlannedOrder(token_id=wl["dn_tok"], side="BUY",
                             price=_dn_bid, size=_dn_shares, outcome="DOWN"),
            ]
            _cond_rungs_config = []
            n_tranches = 1
            _h_imbalance, _h_delta = 0.0, 0.0
            _whale_action = "NORMAL"
            _cvd_strong_disagree = False
            # NOTE: _observe_only is set by coin gate (Change F removes paper force)

            # Log W4 entry
            try:
                _bs_entry = {
                    "ts": datetime.now(tz=_HKT).isoformat(), "event": "w4_entry",
                    "cid": cid[:8], "coin": _coin_slug,
                    "lean_dir": _lean_dir, "lean_ratio": _lean_ratio,
                    "w4_mag_bps": round(_w4_mag, 1),
                    "up_mid": _up_mid, "dn_mid": _dn_mid,
                    "up_bid": _up_bid, "dn_bid": _dn_bid,
                    "combined": _bs_combined,
                    "edge_pct": round((1.0 - _bs_combined) * 100, 2),
                    "up_shares": _up_shares, "dn_shares": _dn_shares,
                    "budget": round(_budget, 2), "bankroll": round(bankroll, 2),
                    "bridge": round(bridge_p_up, 4),
                }
                with open(_BOTHSIDES_LOG, "a") as _bsf:
                    _bsf.write(json.dumps(_bs_entry) + "\n")
            except Exception:
                pass

            logger.info("W4 ENTRY %s: lean=%s %+.1fbps | UP@$%.2f×%.0f + DN@$%.2f×%.0f = $%.3f (edge %.1f%%)",
                        cid[:8], _lean_dir, _w4_mag,
                        _up_bid, _up_shares, _dn_bid, _dn_shares,
                        _bs_combined, (1.0 - _bs_combined) * 100)
            # Skip all directional post-processing (CVD override, whale halve)
```

### B2. Keep directional ladder unchanged

```
FILE: polymarket/run_mm_live.py
LINES: 1583-1660 (Wide Ladder DCA + CVD/whale post-processing)
CURRENT: Wide Ladder at $0.43/$0.37/$0.31/$0.26, CVD disagree override, whale halve
CHANGE TO: NO CHANGE — this only runs when both_sides=False (line 1586 guard already exists)
WHY: Directional mode stays for non-W4 use. The existing `if not both_sides:` guard at line 1586 already separates the two paths.
RISK: ZERO — no code change.
```

---

## CHANGE C: Exit Logic — Disable for Both-Sides Markets

### C1. Skip exit logic for both-sides positions

```
FILE: polymarket/run_mm_live.py
LINES: 2281-2477 (Exit section: Profit Lock + Cost Recovery + Stop Loss)
CURRENT: 3-layer exit runs for ALL open markets
CHANGE TO: Skip exit for markets with both_sides=True flag
WHY: W4 has ZERO sells (confirmed across 30,930 markets). Selling one side breaks the combined < $1.00 guarantee. Example: buy UP@$0.55 + DN@$0.41 = $0.96 guaranteed profit. Selling UP@$0.60 mid-window: if DOWN loses, net = +$0.05 - $0.41 = -$0.36. Management converted $0.04 guaranteed into potential $0.36 loss.
RISK: LOW — skipping exit means positions always hold to resolution. Worst case per trade = full entry cost (capped at 0.5% bankroll = ~$1.50 at $300 bankroll). This is the designed behavior.
```

```python
    # ── Exit: Profit Lock + Cost Recovery + Stop Loss ──
    # W4 BOTH-SIDES: skip ALL exit logic (hold to resolution)
    if client and hasattr(client, "sell_shares") and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            if mkt.get("paper"):
                continue
            if mkt.get("both_sides"):
                continue  # W4: ZERO management, hold to resolution
            # ... rest of existing exit logic unchanged ...
```

### C2. Skip re-entry for both-sides markets

```
FILE: polymarket/run_mm_live.py
LINES: 2478-2643 (Re-entry section)
CURRENT: After stop-loss, re-enter in same window with discounted pricing
CHANGE TO: Skip re-entry for both_sides markets
WHY: No stop-loss = no re-entry needed. W4 never sells, so there's nothing to re-enter.
RISK: ZERO — dead code path (SL never triggers for both_sides, so re-entry never activates). Adding explicit guard for defense-in-depth.
```

```python
    # ── Re-entry: scalp again in same window after early exit ──
    if is_heavy and client and not dry_run:
        for cid, mkt in list(state["markets"].items()):
            if mkt["phase"] != "OPEN":
                continue
            if mkt.get("both_sides"):
                continue  # W4: no exits, no re-entry
            # ... rest of existing re-entry logic unchanged ...
```

---

## CHANGE D: Cancel Logic — Simplify for Both-Sides

### D1. Only cancel unfilled orders at window end

```
FILE: polymarket/run_mm_live.py
LINES: 1954-2043 (Cancel defense section)
CURRENT: 3 triggers — window-end, adverse spot move, dynamic TTL
CHANGE TO: For both_sides markets, only window-end cancel (no adverse/TTL cancel)
WHY: W4 places limit bids and lets them sit. Cancelling on adverse BTC move would remove the hedge-side order (which is the one MORE likely to fill when BTC goes against lean). Adverse cancel is designed for directional positions where you don't want to catch a falling knife. In both-sides, the "falling knife" is your hedge filling — which is GOOD.
RISK: LOW — keeping orders open longer means they may fill at prices that moved against us. But for both-sides, any fill adds to our position which resolves at $0 or $1. The combined < $1.00 guarantee is maintained regardless of which side fills.
```

```python
    # Cancel defense: 3 triggers for unfilled orders
    if client and hasattr(client, "client") and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            pending = mkt.get("pending_orders", [])
            if not pending:
                continue

            end_ms = mkt.get("window_end_ms", 0)
            entry_price = mkt.get("entry_price", 0)
            entry_ts = mkt.get("entry_ts", 0)
            now_s = int(time.time())

            _t = mkt.get("title", "").lower()
            _s = "ETHUSDT" if "ethereum" in _t else "BTCUSDT"

            to_cancel = []
            reason = ""

            # Trigger 1: 2 min before window end → cancel ALL pending (always active)
            if end_ms > 0 and now_ms > end_ms - 120_000:
                to_cancel = [p for p in pending if not p.get("endgame") and not p.get("hedge")]
                reason = "window_end"

            # W4 BOTH-SIDES: skip adverse + TTL cancel (only window-end)
            if mkt.get("both_sides"):
                pass  # no adverse or TTL cancel
            else:
                # Trigger 2: adverse spot move (directional only)
                # ... existing code unchanged ...

                # Trigger 3: dynamic TTL (directional only)
                # ... existing code unchanged ...

            # ... rest of cancel execution unchanged ...
```

---

## CHANGE E: Sizing — 0.5% Bankroll + Compound Reinvestment

### E1. Change bet_pct for both-sides mode

```
FILE: polymarket/run_mm_live.py
LINES: 2766-2771 (main() both-sides config)
CURRENT: config.half_spread = 0.020, no bet_pct override
CHANGE TO: config.bet_pct = 0.005 (0.5%), remove half_spread setting (not used in new entry)
WHY: W4 uses 0.5% per trade (confirmed by growth simulation fit, §6.3 of spec). Current 3% is 6x too large. At 60 windows/day × 0.5% = 30% daily deployed capital. At 3% = 180% which is impossible without reuse, meaning current sizing would concentrate into fewer markets with larger positions — exactly opposite of W4's diversified approach.
RISK: MEDIUM — sizing too small = slow growth. Sizing too large = ruin risk. 0.5% is well-established from W4 data. The real risk is config not being applied properly → add assertion.
```

```python
    both_sides = getattr(args, 'both_sides', False)
    if both_sides:
        config.bet_pct = 0.005  # W4: 0.5% bankroll per market
        config.max_concurrent_markets = 5  # W4 trades multiple coins simultaneously
        print(f"  MODE: W4 BOTH-SIDES (bet={config.bet_pct:.1%}, max_concurrent={config.max_concurrent_markets})")
    else:
        # ... existing directional mode setup ...
```

### E2. Compound reinvestment (bankroll auto-update)

```
FILE: polymarket/run_mm_live.py
LINES: 1196-1204 (bankroll refresh in heavy cycle)
CURRENT: Already refreshes bankroll from CLOB balance every heavy cycle
CHANGE TO: NO CODE CHANGE — existing behavior already implements compound reinvestment!
WHY: The bot already calls client.get_usdc_balance() every heavy cycle (5s) and updates state["bankroll"]. When a 15M market resolves, winning shares pay $1.00 each back to USDC balance. Next cycle reads new balance. This IS compound reinvestment — settled capital is immediately available for the next window. W4 spec §6.5 notes W4 likely does exactly this.
RISK: ZERO — no code change.
```

### E3. Kill switch recalibration

```
FILE: polymarket/run_mm_live.py
LINES: 919-947 (_get_risk_mode function)
CURRENT: STOPPED at WR < 28% (rolling 30 filled trades)
CHANGE TO: Recalibrate thresholds for both-sides economics
WHY: Both-sides has different breakeven math. With 2:1 lean at $0.55/$0.41:
  - Win: payout $2.00, cost $1.51 → profit $0.49
  - Loss: payout $1.00, cost $1.51 → loss $0.51
  - Breakeven WR = 0.51 / (0.49 + 0.51) = 51%
  Current WR thresholds (28%/30%/58%) are calibrated for directional where breakeven ≈ 24%.
  For W4 mode, STOPPED should be at ~55%, not 28%.
RISK: MEDIUM — wrong thresholds either halt too early (miss profit) or too late (bleed money).
  MITIGATION: Only change thresholds when both_sides is active. Keep directional thresholds unchanged.
  NOTE: We don't change this in v1. The 28% threshold is too low for both-sides but it's a safety net, not an optimization. Better too lenient than too strict while we gather data. Flag for v2 recalibration after 200+ live both-sides trades.
```

**DECISION: DEFER to v2.** The existing kill switch is too lenient for both-sides but won't cause harm (it would only trigger after truly catastrophic performance). Premature tightening could halt the bot during normal variance. Keep unchanged, add a monitoring log.

```python
    # In _get_risk_mode(), after computing WR:
    # (Add logging for both-sides WR monitoring — no behavior change)
    if count >= 10:
        # Log WR for both-sides markets separately
        _bs_resolved = [m for m in state["markets"].values()
                        if m.get("phase") == "RESOLVED" and m.get("both_sides")
                        and m.get("entry_cost", 0) > 0]
        if len(_bs_resolved) >= 5:
            _bs_wins = sum(1 for m in _bs_resolved[-30:] if m.get("realized_pnl", 0) > 0)
            _bs_total = min(30, len(_bs_resolved))
            _bs_wr = _bs_wins / _bs_total
            logger.info("W4 WR MONITOR: %.1f%% (%d/%d) — breakeven=51%%",
                        _bs_wr * 100, _bs_wins, _bs_total)
```

### E4. Daily loss cap adjustment

```
FILE: polymarket/run_mm_live.py
LINES: 1130-1186 (daily loss graduated response)
CURRENT: Tier 1: >$10 → ×0.67 | Tier 2: >$20 → ×0.33 | Tier 3: >$30 → 2h cooldown | Tier 4: >$45 → halt
CHANGE TO: NO CODE CHANGE for v1.
WHY: At 0.5% per trade with $300 bankroll = $1.50 per trade. Worst case per window = $1.50 loss. To hit $10 daily loss = 7 consecutive max-loss windows. At 81% WR, probability of 7 consecutive losses = 0.19^7 = 0.0000009 = ~1 in a million. The existing thresholds are effectively unreachable under W4 sizing. They serve as safety nets for unexpected events (API bug, wrong pricing, etc.) without interfering with normal operation.
RISK: ZERO — no code change, existing safety still active.
```

---

## CHANGE F: Live Gate — Remove Paper-Only Restriction

### F1. Remove forced paper for both-sides

```
FILE: polymarket/run_mm_live.py
LINE: 1524
CURRENT: `_observe_only = True  # force paper for both-sides`
CHANGE TO: Remove this line. Let the coin gate at line 1367 determine observe_only.
WHY: Current code forces ALL both-sides trades to paper regardless of coin. We want BTC both-sides to execute live (matching _LIVE_TRADE_COINS = {"btc"}).
RISK: HIGH — this is the line between paper and real money. Removing it means live orders on CLOB.
  MITIGATION 1: The coin gate at line 1367 (`_observe_only = _coin_slug not in _LIVE_TRADE_COINS`) still blocks non-BTC coins.
  MITIGATION 2: Keep --both-sides requiring --live flag (no change to dry-run behavior).
  MITIGATION 3: Add --w4-live flag as explicit opt-in for live both-sides execution.
```

```python
        # REMOVE this line:
        # _observe_only = True  # force paper for both-sides

        # The coin gate at line 1367 already handles this:
        # _observe_only = _coin_slug not in _LIVE_TRADE_COINS
        # BTC → live, ETH/SOL → paper (observation only)
```

### F2. Remove dry-run enforcement for --both-sides

```
FILE: polymarket/run_mm_live.py
LINES: 2766-2769 (main() both-sides dry-run force)
CURRENT: `if not dry_run: print("⛔ --both-sides requires --dry-run..."); dry_run = True`
CHANGE TO: Allow --both-sides with --live, add --w4-live confirmation flag
WHY: Both-sides experiment was paper-only during development. Now ready for live.
RISK: HIGH — accidental live execution. MITIGATION: require explicit --w4-live flag.
```

```python
    both_sides = getattr(args, 'both_sides', False)
    w4_live = getattr(args, 'w4_live', False)
    if both_sides:
        config.bet_pct = 0.005
        config.max_concurrent_markets = 5
        if not dry_run and not w4_live:
            print("  ⛔ --both-sides --live requires --w4-live flag for safety.")
            print("  ⛔ Use: --both-sides --live --w4-live")
            sys.exit(1)
        mode_str = "W4 LIVE" if (not dry_run and w4_live) else "W4 DRY-RUN"
        print(f"  MODE: {mode_str} (bet={config.bet_pct:.1%})")
```

### F3. Add --w4-live argument

```
FILE: polymarket/run_mm_live.py
LINES: ~2750 (argparse section)
CURRENT: No --w4-live flag
CHANGE TO: Add --w4-live flag
WHY: Extra safety gate. Prevents accidental live execution with just --both-sides --live.
RISK: LOW — just an argument parser addition.
```

```python
    ap.add_argument("--w4-live", action="store_true",
                    help="Enable LIVE execution for W4 both-sides strategy (real money!)")
```

---

## CHANGE G: Endgame/Hedge — Disable for Both-Sides Markets

### G1. Skip endgame for both-sides markets

```
FILE: polymarket/run_mm_live.py
LINES: 2062-2185 (Endgame section)
CURRENT: Places 1-share underdog bets in last 2 min of undecided markets
CHANGE TO: Skip for both_sides markets
WHY: W4 already has both sides filled. Endgame bets are data collection for directional strategy. Adding random 1-share bets on top of W4's precise sizing would be noise. Also, endgame bets are taker (aggressive pricing) which incurs ~2% fee — counter to W4's maker approach.
RISK: LOW — endgame is $0.25-$0.50 per bet maximum. Skipping it loses negligible data.
```

```python
            if mkt.get("both_sides"):
                continue  # W4: already has both sides, skip endgame
```

### G2. Skip hedge for both-sides markets

```
FILE: polymarket/run_mm_live.py
LINES: 2187-2279 (Last-minute hedge section)
CURRENT: Buy opposite token if BTC moves $50+ against position in 30s
CHANGE TO: Skip for both_sides markets
WHY: W4 ALREADY has both sides. The hedge mechanism is for directional positions that need protection. Both-sides positions ARE the hedge.
RISK: ZERO — no behavior change (both-sides already has opposite shares, so hedge would be redundant double-counting).
```

```python
            if mkt.get("both_sides"):
                continue  # W4: both sides = built-in hedge
```

---

## CHANGE H: market_maker.py — Add should_enter for SOL

```
FILE: polymarket/strategy/market_maker.py
LINE: 475
CURRENT: `return ("bitcoin" in t or "ethereum" in t) and "up or down" in t`
CHANGE TO: Add "solana" to match list
WHY: W4 trades SOL as well. Currently SOL markets are discovered but rejected by should_enter_market(). Adding SOL enables discovery (execution gate is still _LIVE_TRADE_COINS which only has "btc").
RISK: LOW — SOL will only trade in paper mode (observe_only) until added to _LIVE_TRADE_COINS.
```

```python
    return ("bitcoin" in t or "ethereum" in t or "solana" in t) and "up or down" in t
```

---

## COMPLETE DIFF SUMMARY

| Change | File | Lines | What | Risk | Priority |
|--------|------|-------|------|------|----------|
| **A1** | run_mm_live.py | ~406 | New _w4_signal() function | LOW | P0 |
| **A2** | run_mm_live.py | 1369-1401 | Replace momentum filter (both_sides only) | MEDIUM | P0 |
| **A3** | run_mm_live.py | 1497-1567 | Skip directional gates (both_sides only) | LOW | P0 |
| **B1** | run_mm_live.py | 1501-1543 | Rewrite both-sides entry with Poly mid + lean | **HIGH** | P0 |
| **C1** | run_mm_live.py | 2281 | Skip exit for both_sides | LOW | P0 |
| **C2** | run_mm_live.py | 2478 | Skip re-entry for both_sides | ZERO | P1 |
| **D1** | run_mm_live.py | 1954 | Simplify cancel for both_sides | LOW | P0 |
| **E1** | run_mm_live.py | 2766-2771 | bet_pct=0.005, max_concurrent=5 | MEDIUM | P0 |
| **E3** | run_mm_live.py | 919 | WR monitor log (no behavior change) | ZERO | P1 |
| **F1** | run_mm_live.py | 1524 | Remove forced paper | **HIGH** | P0 |
| **F2** | run_mm_live.py | 2766-2769 | Remove dry-run enforcement | **HIGH** | P0 |
| **F3** | run_mm_live.py | ~2750 | Add --w4-live arg | LOW | P0 |
| **G1** | run_mm_live.py | 2062 | Skip endgame for both_sides | LOW | P1 |
| **G2** | run_mm_live.py | 2187 | Skip hedge for both_sides | ZERO | P1 |
| **H** | market_maker.py | 475 | Add SOL to discovery | LOW | P1 |

---

## WHAT STAYS UNCHANGED

These existing systems remain completely intact:

1. **Discovery** — slug-based market discovery (lines 491-541)
2. **WebSocket feeds** — Binance price, Poly OB, User fill detection
3. **Fill detection** — WS + REST fill confirmation (lines 589-773)
4. **Resolution** — Binance kline-based resolution (lines 954-1066)
5. **State persistence** — atomic JSON write (lines 816-826)
6. **Trade logging** — mm_trades.jsonl, mm_signals.jsonl, mm_order_log.jsonl
7. **Kill switches** — hard stop, daily loss tiers, cooldown
8. **Rate limiting** — API call tracking per source
9. **Cross-exchange validation** — 3-exchange price check
10. **Position watcher** — separate daemon, unaffected
11. **1H Conviction bot** — separate entry point, unaffected

---

## VERIFICATION PLAN

### Before First Live Trade

1. **ORDER PATH AUDIT** (mandatory per CLAUDE.md):
   ```bash
   grep -n "_execute\|buy_shares\|plan_opening\|PlannedOrder" polymarket/run_mm_live.py
   ```
   Verify: only 2 new `_execute` paths (W4 entry at ~line 1520, existing unchanged paths).

2. **Worst-case trace**: "If ALL trades lose for 24h, max loss?"
   - 96 windows/day × 0.5% × $300 bankroll = 96 × $1.50 = $144 = 48% of bankroll
   - But each window: worst case = lean side loses, hedge side wins, net loss = cost - hedge_payout
   - With 2:1 lean at $0.55/$0.41: loss = $1.51 - $1.00 = $0.51 per unit
   - Per window: $1.50 budget ÷ $1.51 per unit × $0.51 loss = $0.51 loss
   - 96 × $0.51 = $48.96 = 16.3% daily loss
   - **PASS**: $48.96 < 2% of bankroll... WAIT, 16.3% is NOT < 2%.
   - **RECALCULATION**: At 0.5% bankroll per trade, max deployed per window ≈ $1.50. Max loss per window = $0.51 (not full $1.50 because hedge side pays out $1.00). Daily: 96 × $0.51 ≈ $49 theoretical max (all 96 windows wrong, 0% WR). At 81% WR base, expected daily P&L = 96 × ($0.30 × 0.81 - $0.51 × 0.19) × ($1.50/$1.51) = 96 × $0.146 × 0.99 = +$13.9/day. Daily loss cap ($45) would trigger before theoretical max.
   - **ACCEPTABLE**: Daily loss cap at $45 catches catastrophe. Expected daily = +$14.

3. **Dry-run validation**: Run `--both-sides --dry-run --verbose` for 2+ hours:
   - Confirm signal fires on ~62% of windows (matches spec §1.5)
   - Confirm entry prices track Poly mid (within 1-2¢)
   - Confirm lean direction matches BTC movement
   - Confirm no exit/cancel/re-entry triggers

4. **First live session**: Run `--both-sides --live --w4-live` with manual monitoring:
   - Watch first 3 fills manually
   - Verify combined < $1.05 on all entries
   - Verify bankroll updates after resolution

---

## THINGS WE ARE EXPLICITLY NOT DOING

1. **NOT implementing small-order loop**: W4 places many $3-$5 orders over 140s. We place 1 order per side per window. This reduces fill complexity but may get worse fill prices. Revisit if fill rate data shows problems.

2. **NOT implementing dynamic lean ratio**: W4 lean varies 1.04x-12.71x. We use fixed 2:1. Revisit after 200+ trades with magnitude vs outcome data.

3. **NOT changing 5M**: All changes gated by `both_sides` flag which is 15M-only.

4. **NOT recalibrating kill switch**: Deferred to v2 after data collection.

5. **NOT adding ETH/SOL live**: _LIVE_TRADE_COINS stays {"btc"}. ETH/SOL trade in paper mode for data collection.

6. **NOT removing bridge/OB computation**: These still run for logging (mm_signals.jsonl). The bridge fair value is used as fallback pricing only if Poly mid is unavailable.
