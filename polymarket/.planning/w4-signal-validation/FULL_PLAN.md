# FULL PLAN — Polymarket 15M + 5M Strategy
> Created: 2026-03-24 04:10 HKT | Updated: 2026-03-24 04:30 HKT
> 呢個 plan 記錄所有要做嘅嘢。唔漏。
> Q1-Q8 answered, new findings integrated.

---

## 🔴 IMMEDIATE（bugs found in session — 已 fix 或需驗證）

| # | Bug | Status | Impact |
|---|-----|--------|--------|
| 1 | **Lean budget→shares 反轉** — 方向啱蝕錢 | ✅ Fixed (share count ratio) | 💀 live 蝕錢 |
| 2 | **M1 filter 殺 W4 signal** — bot 永遠唔入場 | ✅ Fixed (bypass for both_sides) | 🔴 bot 空轉 |
| 3 | **5M dry_run 冇 enforce** — 落真錢 $1.10 | ✅ Fixed (hard block in _execute_order) | 💀 live 蝕錢 |
| 4 | **Exit logic 冇 skip both-sides** (live mode) | ✅ Fixed (BMD #5) | 🔴 TP 砍 winner |
| 5 | **Cancel triggers 2+3 殺 both-sides orders** | ✅ Fixed | 🔴 hedge 被 cancel |
| 6 | **Combined cap $1.00 太緊** → reject 50% trades | ✅ Fixed → $1.05 | 🔴 miss trades |
| 7 | **_w4_live persist in state** | ✅ Fixed (reset on startup) | 🔴 意外 live |
| 8 | **Endgame/hedge 冇 both-sides guard** | ✅ Fixed | 🟡 浪費 API |
| 9 | **5M resolution 用 title parsing 唔用 stored coin** | ✅ Fixed | 🔴 wrong symbol |
| 10 | **5M 冇 startup orphan cleanup** | ✅ Fixed | 🔴 orphan orders |
| 11 | **5M 冇 concurrent position cap** | ✅ Fixed (max 8) | 🟡 exposure |
| 12 | ⏳ **Lean fix 未驗證** — 需要見到正確嘅 share allocation | 等下個 W4 entry | 💀 |

---

## 🟡 已發現但未做（priority order）

### P1: 15M/5M Window Overlap Signal（用戶提出）
**你講嘅：** 15M 後半段，方向已經 near-locked。5M window open price 同 15M open price 唔同 → 可以利用。
- 15M open $70,886，BTC now $70,750 → DOWN 99%+ certain
- 5M open $70,744，BTC now $70,750 → fresh 50/50
- **Action**: 15M 後半（T+600s+），如果 |move| > 10bps → lean ratio 大幅提升（5:1 → 10:1）
- **Data needed**: backtest 15M WR by elapsed time (T+300 vs T+600 vs T+750)
- **Status**: 未做。需要新 backtest + code change。

### P2: Chainlink vs Binance 價差
**Memory 已有：** "Indicator 用 Binance 但 resolution 用 Chainlink — 兩者有微妙價差"
- Resolution 用 Chainlink BTC/USD，唔係 Binance
- Signal 用 Binance futures
- 如果 Chainlink 同 Binance 有 >2bps 差距 → 可能 flip resolution
- **Action**: fetch Chainlink data stream，比較同 Binance 嘅 divergence
- **Status**: 未做。

### P3: Dynamic Lean Ratio
**發現：** W4 lean 係 dynamic（1x-48x），唔係 fixed。可能 scale with：
- Signal magnitude（大 move → 更 aggressive lean）
- Time remaining（越接近 resolution → 越高 lean）
- Combined cost（< $1.00 → 更高 lean because guaranteed edge）
- **Action**: design dynamic lean formula, backtest
- **Status**: 未做。

### P4: 多個 window 同時 trade
- 15M window 同 5M window 可以同時 active
- 佢哋 correlated（同一個 BTC price）
- 同時 trade 15M + 5M = 2x exposure on same underlying
- **Action**: correlation analysis, diversification benefit vs double exposure risk
- **Status**: 未做。

### P5: Maker vs Taker pricing
**BMD 發現：** taker fee 1.5% 可以 eat 全部 edge
- 依家用 mid-1¢（maker attempt）
- W4 可能 sweep book（taker）
- **Action**: track fill rate at maker vs taker pricing, calculate net edge after fees
- **Status**: 未做。需要 live data。

### P6: ETH/SOL/XRP per-coin parameters
**發現：** 每個幣 momentum persistence 唔同
- BTC: 47.9% raw, 84%+ with filter
- SOL: 44.6% raw（weakest）, possibly contrarian
- XRP: 46.6%
- **Action**: per-coin backtest on 5M/15M data, set independent thresholds
- **Status**: 只有 BTC validated。其他幣未做。

---

## ❓ 我唔確定 / 需要問你嘅

1. **你有冇其他因素我 miss 咗？**（例如：特定時段 edge 更大？weekend vs weekday？亞洲 vs 美國 session？）

2. **你有冇 access 到 Polymarket 嘅 Discord / Telegram group？** 可能有 insider info on fee changes, latency updates。

3. **你嘅 risk tolerance 係幾多？** $253 bankroll 蝕幾多會停？（依家 kill switch 係 -20% = $50）

4. **你想幾時加 ETH？** 依家只有 BTC live。ETH paper data 有冇夠？

5. **你有冇其他 profitable wallet 想分析？** 我哋分析咗 7 個，但可能有新嘅。

6. **Rust 優化嘅 priority 幾高？** 你話做部分 Rust，想幾時開始？

7. **1H Conviction bot（run_1h_live.py）仲跑唔跑？** 同 15M both-sides 有冇衝突？

8. **你有冇注意到任何 pattern 我冇提到？** 例如：某啲時段特別容易贏/蝕、某啲 BTC price level 有 support/resistance 效應、news event 前後表現唔同？

---

## 📊 已驗證嘅事實（High Confidence）

| Fact | Source | Confidence |
|------|--------|------------|
| BTC 5M momentum persistence 84% at 120s/5bps | 13-month backtest, 103K windows | HIGH |
| BTC 15M momentum persistence 81% at 300s/5bps | 13-month backtest, 7,364 windows | HIGH |
| Monthly stability: zero degradation | 13 months | HIGH |
| Regime independent (trending/choppy/normal) | 13 months | HIGH |
| Polymarket pricing lag 10+ min | 8,023 OB snapshots | HIGH |
| W4 $507→$433K in 162 days | On-chain PnL timeseries | HIGH |
| W4 holds to resolution (0 sells / 2,516 trades) | API data | HIGH |
| TP destroys value in binary markets | Counterfactual analysis | HIGH |
| Adverse selection: 91% accurate unfilled, 46% filled | Signal analysis | HIGH |
| Both-sides eliminates adverse selection | Structural analysis | HIGH |

## ⚠️ 未驗證 / 低 Confidence

| Claim | Issue | Confidence |
|-------|-------|------------|
| W4 signal = BTC momentum >5bps | 58% match rate, p=0.14 not significant | LOW |
| W4 lean = 12.5:1 fixed | Actual range 1x-48x, CV=144% | LOW |
| SOL contrarian | 7/9 windows, p=0.09 | LOW |
| W4 is maker not taker | 22% classified maker, 69% mixed | LOW |
| 5M better than 15M for PnL | WR higher but real fill rate unknown | MEDIUM |
| $2-3/day realistic for 15M | Based on simulation, not live | MEDIUM |

---

## 📋 Execution Order

### Now（今日）
1. ⏳ 驗證 lean fix on live（等下個 W4 entry）
2. ⏳ Monitor 15M live bot

### Short-term（1-3 日）
3. Collect 15M live data: 50+ trades → real fill rate, real PnL
4. Fix 5M bot lean bug（已 done）→ restart 5M paper with real OB
5. Backtest P1: 15M WR by elapsed time（overlap signal validation）

### Medium-term（1-2 週）
6. P2: Chainlink data integration
7. P3: Dynamic lean ratio design + backtest
8. P5: Maker vs taker fill rate analysis from live data
9. P6: ETH per-coin validation → go live
10. 5M live micro-test

### Long-term
11. Rust order submission optimization
12. SOL/XRP per-coin validation
13. Multi-timeframe coordination (15M + 5M + 1H)
