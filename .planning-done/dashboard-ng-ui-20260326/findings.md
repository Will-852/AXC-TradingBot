# Findings: Dashboard NG UI Enhancement

## Web Research: Crypto Order Form UX (Binance/Bybit/OKX)

### Standard Input Flow
- **Binance Futures**: Margin mode toggle (Cross/Isolated) at top → Leverage slider → Order type → Price → Size (USDT or Qty toggle) → SL/TP → Confirm
- **Bybit**: Similar but adds "Order Value" real-time display + margin required
- **OKX**: Most polished — shows "Cost", "Max", margin %, all real-time

### Key UX Patterns
1. **Margin mode visible** — always shown as badge/toggle, never hidden
2. **Dual input mode** — toggle between "by USDT" and "by Qty"
3. **Real-time calculations** — notional, margin required, margin % update on every keystroke
4. **Max button** — "use all available margin" shortcut
5. **Order confirmation** — summary popup before execution (Bybit default on)
6. **Validation** — red border + error text for insufficient balance / below min qty

### What we should adopt
- Margin mode badge (read-only since we hardcode CROSSED)
- Notional value display
- Margin % of balance
- Real-time min qty validation
- Reverse calc (qty → USDT)

## Indicator Inventory (from scout)

### Currently in chart (13)
BB, EMA, MA, RSI, MACD, STOCH, VWAP, VOL, WHALE, DELTA, VP, HEATMAP, CVD

### In indicator_cache but NOT in chart (6)
- ADX/DI+/DI- (adx, di_plus, di_minus) — trend strength
- OBV + EMA (obv, obv_ema) — volume momentum
- S/R Lines (rolling_low, rolling_high) — range strategy core
- BB Width Pctl (bb_width_pctl) — squeeze detection (0-100)
- Z-Robust (z_robust) — MAD-based z-score
- Volume Ratio (volume_ratio) — volume vs 30-SMA

### Data format in indicator_cache
Each symbol → each TF (3m/15m/1h/4h) → 38 fields

## Notification Issue
- `ui.notify` = bottom (not the problem)
- `ui.dialog` in notifications.py = full-screen modal overlay (the problem)
- Fix: `ui.menu` dropdown anchored to bell button
