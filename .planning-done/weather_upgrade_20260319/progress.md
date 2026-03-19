# Progress — Pipeline 2check

## Session: 2026-03-19

### Phase 1: 紅線 ✅ (with issues found)
- [x] AUTOMATED_CATEGORIES = {"crypto_15m", "weather"} ✅
- [x] ExecuteTradesStep BUY guard ✅
- [x] ExecuteExitStep SELL guard ✅
- [x] position_manager evaluates ALL (not ideal but execution blocked) ⚠️
- [x] 🔴 NBA trade bypassed guard — needs investigation

### Phase 2-3: Scanner + Edge ✅
- [x] get_recent_markets() works ✅
- [x] scan_markets() merge + dedup ✅
- [x] Regex matches both 5M and 15M ✅
- [x] 🟡 5M indicator uses 15m candle — timeframe mismatch
- [x] 🟡 Weather spread 200% cosmetic bug

### Phase 4-5: Kelly + SL ✅
- [x] MAX_PER_BET = 0.01 effective ✅
- [x] SL=9% only crypto_15m ✅
- [x] Weather/sports protected ✅

### Phase 6: Trade Audit ✅
- [x] 🔴 NBA sell: red line violation
- [x] 🟡 Chicago buy: scope gap (US city live traded)
- [x] Bet size $1.42 correct ✅
