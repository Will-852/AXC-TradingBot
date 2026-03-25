# Task: SL/TP Expected PnL + Trade Modal 3-Pick-2 Auto-Calc

## Phases

### Phase 1: SL/TP Expected PnL on Position Card `status: pending`
- File: `scripts/dashboard_ng/components/positions.py`
- Formula: SL loss = |entry - sl| × size, TP gain = |tp - entry| × size
- Show as colored $$ next to SL/TP prices

### Phase 2: Trade Modal 3-Pick-2 Auto-Calc `status: pending`
- File: `canvas/trade-modal.js`
- Three inputs: margin, qty, leverage — fill any 2, auto-calc 3rd
- Guard against infinite loop with `_calcLock` flag

### Phase 3: 2check `status: pending`
