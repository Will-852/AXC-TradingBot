# Findings — Per-Coin Architecture + WS + Mode Impact

## F1: Per-Coin Config 散落 12+ 位置
| 位置 | 存咩 | Action |
|------|------|--------|
| pairs.py PAIR_CONFIGS | vol_mult, max_lev, RSI/BB overrides, precision | → coin/{symbol}/config.py |
| indicator_calc.py PRODUCT_OVERRIDES | ETH RSI(32/68), XRP BB(0.008) | → coin/{symbol}/strategies.py → 刪 PRODUCT_OVERRIDES |
| settings.py PAIRS | ["BTCUSDT",...] | → auto-derive from loader |
| settings.py PAIR_PREFIX | {"BTCUSDT":"BTC",...} | → coin config.py |
| settings.py POSITION_GROUPS | crypto_correlated etc | → coin config.py group field |
| settings.py TRADER_OWNED_FIELDS | BTC_ATR, ETH_ATR etc | → auto-derive from active coins |
| params.py ASTER/BINANCE/HL_SYMBOLS | Exchange-specific lists | → auto-derive from coin exchange field |
| params.py SIGNAL_CONF_GATE_PER_SYMBOL | ETH gates | → coin/eth/strategies.py |
| params.py REGIME_SIGNAL_RULES | 13 cells, ALL DISABLED | → DELETE (dead code) |
| evaluate.py _CRYPTO_PAIRS | {BTC,ETH,SOL} | → auto-derive from group="crypto_*" |
| evaluate.py PAIR_PRIORITY | BTC=4, ETH=3, etc | → coin config.py priority field |
| mode_detector.py "BTCUSDT" | Hardcoded primary | → config 指定 primary |
| liq_params.py LIQ_COINS | [BTC,ETH,SOL] | → auto-derive from coin config |

## F2: WS Multi-Coin Gap
| Component | Multi-coin ready? | Effort |
|-----------|-------------------|--------|
| ws_manager.py | ❌ BTC hardcoded | Trivial (5 lines) |
| indicator_engine.py | ❌ Single-symbol state | Moderate (80-120 lines) |
| indicator_calc.py | ✅ Symbol-agnostic | Zero |
| indicator_cache.json | ✅ Already namespaced | Zero |
| trader_cycle pipeline | ✅ Already loops all pairs | Zero |
| Redis bus | ✅ Streams are tagged | Zero |

**Binance WS**: 20 streams (4 coins × 5) out of 1024 limit = 2% usage. No rate limit concern.

## F3: Mode Detection — Real Impact Map
Only **2 mechanisms** actually affect trading:

### Mechanism 1: Affinity Penalty (HIGH)
```
                    Mode detected
                 TREND    RANGE    CRASH
Strategy:
  range         -0.23    +0.00    -0.30
  trend         +0.00    -0.42    -0.08
  crash         +0.00    +0.00    +0.00
```
Effective minimum confidence to pass gate:
```
                 TREND    RANGE    CRASH
  range(0.40)    0.63     0.40     0.70
  trend(0.48)    0.48     0.90     0.56
  crash(0.33)    0.33     0.33     0.33
```
→ RANGE mode 幾乎完全 block trend signals
→ Crash strategy 永遠唔受影響

### Mechanism 2: Kelly Veto (HIGH, but requires data)
- `compute_kelly_base_risk("TREND")` → reads trades.jsonl filtered by strategy
- f* ≤ 0 → trade CANCELLED entirely
- Needs 30+ trades per strategy to activate (most coins 冇夠)

### Dead mechanisms:
- REGIME_SIGNAL_RULES: 全部 return None（disabled 2026-03-19）
- Strategy evaluate(): 完全 mode-blind（唔讀 ctx.market_mode）
- CP-adjusted ATR: CP_ENABLED = False

## F4: 8 Unused Indicators
| Indicator | Computed in | Why unused | Keep? |
|-----------|------------|------------|-------|
| DI+ / DI- | indicator_calc L145-147 | ADX 用咗但 DI+/- 冇 strategy 讀 | enabled:False |
| EMA fast/slow | indicator_calc L130-132 | 舊 range filter 殘留 | enabled:False |
| Stoch K/D | indicator_calc L140-143 | 只有舊 evaluate_range_signal() 用 | enabled:False |
| MACD line/signal | indicator_calc L135-137 | 只用 hist，line/signal 冇人讀 | enabled:False |
| VWAP + bands | indicator_calc L150-155 | 計算 3 值，零引用 | enabled:False |
| vol_spike | indicator_calc L160 | liq_monitor 自己算 | enabled:False |
| z_robust | indicator_calc L165 | 零引用 | enabled:False |
| bb_width_pctl | indicator_calc L168 | 零引用 | enabled:False |

## F5: Current Per-Coin Indicator Usage (Active Only)
| Indicator | BTC | ETH | XRP | SOL | Used by |
|-----------|-----|-----|-----|-----|---------|
| BB (upper/basis/lower/width) | ✅ | ✅ | ✅(tol=0.008) | ✅ | Range: touch + width penalty |
| RSI | ✅ | ✅(32/68) | ✅ | ✅ | All 3 strategies |
| ADX | ✅ | ✅ | ✅ | ✅ | Range: soft penalty, TP extend |
| ATR | ✅ | ✅ | ✅ | ✅ | SL fallback, trailing |
| MACD hist | ✅ | ✅ | ✅ | ✅ | Trend + Crash + mode detect |
| OBV + EMA | ✅ | ✅ | ✅ | ✅ | Range + Trend scoring |
| MA50/200 | ✅ | ✅ | ✅ | ✅ | Trend: MA alignment |
| Rolling H/L | ✅ | ✅ | ✅ | ✅ | Range: S/R proximity |
| Volume ratio | ✅ | ✅ | ✅ | ✅ | All strategies: vol gate |

## F6: Estimated Total Params After Restructure
| Category | Per-coin params | × 4 focus coins | × 3 keep coins |
|----------|----------------|-----------------|----------------|
| Identity + sizing | 8 | 32 | 24 |
| Indicator toggles | 9 on/off + ~15 tunable | 96 | (inherit defaults) |
| Strategy weights × 3 | ~20 per coin | 80 | (inherit defaults) |
| Zone A + Zone B params | ~10 per coin | 40 | (inherit defaults) |
| Signal filter per coin | ~8 | 32 | (inherit defaults) |
| **Subtotal** | ~60 per coin | 280 | 24 |
| **Total** | | **~304** | |

BUT: 大部分 inherit defaults。真正需要 tune 嘅 = 每個 focus coin ~15 個 key decisions。
4 coins × 15 = **~60 真正 tunable params**（同而家差唔多，但組織清晰好多）。
