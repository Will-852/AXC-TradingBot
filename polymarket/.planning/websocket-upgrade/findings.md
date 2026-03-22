# WebSocket Upgrade — Findings
> Updated: 2026-03-23

## Phase 5 Design Analysis

### What Phase 6 (5M arb) actually needs
The arb signal is SIMPLE — no bridge computation needed:
```
combined = UP_mid + DOWN_mid
if combined < threshold (e.g., $0.96):
    BUY BOTH → guaranteed $0.04+ profit at resolution
```
Reaction time goal: <200ms from arb window opening to order submission.

### Architecture options for Phase 5

**Option A: Full event-driven engine (original plan)**
- Callbacks registered on WS feeds → signal_engine → order queue
- Pro: True event-driven, lowest latency
- Con: Requires modifying ws_binance.py + ws_polymarket.py to add callback support
- Complexity: HIGH

**Option B: Fast-poll engine (100ms loop)**
- Separate thread polls WS caches every 100ms
- Computes signals from cached values
- Fires callbacks when conditions met
- Pro: No changes to existing WS feeds. Simple. <200ms latency.
- Con: Not truly event-driven (100ms poll interval)
- Complexity: LOW

**Option C: Hybrid — WS feeds push to queue, engine consumes**
- Add a thread-safe queue to each WS feed
- Signal engine reads from queue (blocking, instant wakeup)
- Pro: True event-driven without modifying WS feeds' internal logic
- Con: Queue management, ordering
- Complexity: MEDIUM

### Decision: Option B (fast-poll)
- 100ms poll of WS caches is sufficient for 200ms reaction target
- No changes to tested Phase 1-4 code
- The WS caches are already thread-safe (locked reads)
- Bridge computation: <1ms (just CDF calc). 100ms poll + 1ms calc = 101ms latency.
- Arb check: <0.1ms (two float additions). 100ms poll + 0.1ms = 100.1ms latency.

### Signal types to support
1. **ARB_COMBINED**: UP_mid + DOWN_mid < threshold → BUY BOTH (for 5M arb)
2. **BRIDGE_FAIR**: bridge p_up crosses conviction threshold → ENTER (for directional)
3. **EXIT_SIGNAL**: mid >= TP level OR mid <= SL level → SELL (for position management)

### Integration with existing bots
- 15M bot: optional — can register markets for faster exit detection
- 1H bot: optional — same
- 5M arb bot (Phase 6): REQUIRED — this is the main consumer
