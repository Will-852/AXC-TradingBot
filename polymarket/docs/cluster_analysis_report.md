# BMD 四大 Cluster 調優方案 — 批判分析報告
> Date: 2026-03-21
> Context: v15 已完成（commit 7d7991c）。此報告判定 remaining proposals 嘅 DO/SKIP/DEFER。
> 依據：91 筆 live trades + 6 個 verified wallet + $49 disaster post-mortem + 知識庫

---

## 現狀快照（v15 已做）

| 改動 | 狀態 | 效果 |
|------|------|------|
| Cancel defense TTL 5min→dynamic 10min | ✅ Done | 解決 0% fill rate 根因 |
| Adverse threshold BTC 0.3%→0.5% | ✅ Done | 減少 false cancel |
| Per-order logging (mm_order_log.jsonl) | ✅ Done | AS 分析基礎 |
| Round-dependent pricing R2×0.90, R3×0.80 | ✅ Done | 減少 re-entry loss |
| _re_mkt NameError fix | ✅ Done | Re-entry 本來完全壞嘅 |
| Student-t(ν=5) 取代 Normal+10% HC | ✅ Done | 回收 3-5pp over-correction |
| Vol candles 60→120 | ✅ Done | SE 9.2%→6.5% |

**Live data**: 94 markets, 52 filled, WR 83%, PnL +$173, bankroll $133/$140

---

## Cluster 1: Adverse Selection + Fill Model

### 建議 vs 現實

| Sub-agent 建議 | 判定 | 原因 |
|----------------|------|------|
| AS 快速診斷 (time_to_fill vs WR) | ⏳ **DEFER** — 需要數據 | v15 剛加 per-order log，目前 0 筆 fill data。最少跑 48h 先有足夠 fill 做分析 |
| Logistic fill model | ❌ **SKIP** | n=8 live fills 做唔到 logistic regression（最少需要 100+）。6 個月後再考慮 |
| Heckman two-stage WR correction | ❌ **SKIP** | 同上，更 data-hungry |
| EW toxicity score | ❌ **SKIP** | CVD buy ratio 已經 serve 同一目的。加 EW = +complexity for marginal gain |
| Round 2/3 price+size adjustment | ✅ **DONE** (v15) | R2×0.90, R3×0.80 + regime change check |

### 批判

**AS 可能冇想像中嚴重。** 證據：

1. **Live data**: 8 筆 low-fill-rate trades 中 5 win / 3 loss = 62% WR。If AS was severe, we'd expect <50% WR on filled trades.
2. **Polymarket 15M taker profile**: 大部分 taker 係散戶 noise trader（唔係 HFT informed trader）。15M binary market 唔同 equity CLOB — 冇 institutional flow。
3. **真正嘅問題係 fill rate 本身，唔係 fill quality**: v14 嘅 0% fill rate 係 cancel defense bug，唔係 AS。修好之後先知真實 AS 幾嚴重。

**最可能錯嘅位（確定程度：中）**: 如果 Polymarket 有 sophisticated bot 專門 snipe stale maker orders（類似 swisstony 嘅 $5.42M operation），AS 可以比我估計嘅嚴重。但 swisstony 自己都係 maker，唔係 taker。

### Action: 等 48h v15 live data → 跑 fast-fill vs slow-fill WR diagnostic

---

## Cluster 2: Bridge Model 改進

### 建議 vs 現實

| Sub-agent 建議 | 判定 | 原因 |
|----------------|------|------|
| B. Student-t Bridge | ✅ **DONE** (v15) | T5(ν=5) 已實裝，回收 3-5pp over-correction |
| A. TSRV + Multi-Exchange Vol | ⚠️ **QUESTIONABLE** | 見下方 |
| C. Log-odds signal weighting | ⚠️ **QUESTIONABLE** | 見下方 |
| D. Noise monitoring | ❌ **SKIP** | Over-engineering。Current vol is good enough for $133 bankroll |

### 批判

#### TSRV (Two-Scale Realized Volatility)

**數學上正確但實務上冇意義。**

- TSRV 解決嘅問題係 **microstructure noise**（bid-ask bounce），但我哋用 **close prices** from 1-min candles，唔係 tick data。Close-to-close 已經自動 average out microstructure noise。
- 120 candles close-to-close vol 嘅 SE = 6.5%。TSRV 最多再減 1-2pp，但增加顯著複雜度。
- **Multi-exchange vol average**: 我哋已經用 Binance futures klines。加 OKX + Bybit 嘅 vol = 3 個 slightly different vol estimates 嘅 average。理論上減 noise，但 3 個 exchange 嘅 BTC 1-min close 相關性 > 0.99 → effective noise reduction < 10%。唔值得 3x API calls。

**結論**: Vol estimation 唔係 bottleneck。Entry price capped at $0.40 → vol 嘅 ±6.5% error 只影響 fair ±1.5pp → 唔影響 entry decision。

#### Log-odds signal weighting

**理論上 elegant 但 calibration 冇 data。**

Sub-agent 建議用 logit space 做 signal combination：
```
logit_combined = logit_bridge + β_cvd × CVD_norm + β_ob × OB
```

問題：
1. **β_cvd 同 β_ob 邊度嚟？** 需要 calibration data（hundreds of labeled trades）。我哋得 8 筆 live fills with CVD data。
2. **CVD 嘅 conditional mutual information 未知。** Sub-agent 自己都講「先跑 conditional MI test 先知」。冇 data 就冇 test。
3. **v15 已經有 working heuristic**: `fair = bridge + OB × 0.05`。唔 elegant 但 works。替換需要證明新方法 better，目前冇證據。
4. **Probability space addition 唔係「違反公理」** — 只係近似。`P + 0.05 × OBI` 在 P ∈ [0.3, 0.7] 範圍內同 logit addition 差異 < 1pp。我哋嘅 P 集中在呢個範圍（median |d| = 0.595 → P ≈ 0.72）。

**結論**: v15 嘅 heuristic 夠用。Log-odds upgrade 等有 500+ labeled trades 再考慮。

---

## Cluster 3: Backtest 方法論

### 建議 vs 現實

| Sub-agent 建議 | 判定 | 原因 |
|----------------|------|------|
| Debiased WR (overfitting correction) | 🟡 **NOTED** | 有道理但 specific numbers 有問題 |
| EV reconciliation | 🟡 **NOTED** | 值得做，但唔急 |
| Walk-forward framework | ⏳ **DEFER** | 複雜度高，bankroll $133 唔 justify |
| 100% entry rate flag | ✅ **AGREED** | 1H bot backtest 確實有呢個問題 |

### 批判

#### Debiased WR

Sub-agent 用 `inflation = SE(WR) × √(2 × ln(N_eff))` 計 overfitting inflation。

**問題 1**: N_eff ≈ 6 嘅估計冇 basis。我哋實際做咗 ~14 iterations（v1→v14），但每次改動嘅 scope 唔同 — 有啲係 parameter tweak，有啲係 architecture change。呢個公式假設每次 iteration 都做 full parameter search，但我哋唔係。

**問題 2**: Claimed debiased WR 64% (60-68%) vs live 83%。差距 19pp 太大 → 要嘛 backtest 嚴重 overfit，要嘛 live 嘅 83% 有其他原因（e.g., recent trending market bias）。

**我嘅判斷（確定程度：中）**: Live 83% WR 受惠於近期 BTC 單邊趨勢（3月 BTC 跌 → DOWN signals 連續 correct）。Debiased WR 可能在 64-72% range。但呢個唔影響 v15 嘅決策 — 我哋嘅 break-even at $0.40 entry 係 40% WR，有足夠 margin。

#### Walk-forward

**正確方法，但 overkill for $133 bankroll。**

Walk-forward 需要：
- 6 folds × 90d train + 15d test = 6+ 月 data
- 每個 fold 要 re-fit parameters → 我哋嘅 parameters 大部分係 hardcoded constants，唔係 fitted
- 4h purge gap → 15M data 嘅 4h purge = 16 windows = 合理

**根本問題**: 我哋唔係做 parameter optimization — bridge formula 冇 free parameter（ν=5 for Student-t 係 fixed, 唔係 fitted）。Walk-forward 對 parameter-free model 嘅價值有限。

**結論**: 如果 bankroll 到 $1000+，做一次 walk-forward 確認 robustness。$133 唔值得花 4-5 天。

---

## Cluster 4: Portfolio Risk + Regime

### 建議 vs 現實

| Sub-agent 建議 | 判定 | 原因 |
|----------------|------|------|
| A. Correlation sizing | ⚠️ **PARTIALLY AGREE** | 概念啱但 formula 需要調整 |
| C. CUSUM circuit breaker | ❌ **SKIP** | 現有 consecutive loss + daily limit 已足夠 |
| D. Time-of-day factor | 🟡 **MONITOR FIRST** | 需要 time-of-day data 先知有冇 pattern |
| E. Regime detection (VR+ER) | ⏳ **DEFER** | 要 paper 驗證 thresholds |

### 批判

#### Correlation Sizing

Sub-agent 建議：
```python
size_adjusted = base_size / sqrt(1 + (n_concurrent - 1) * rho)
# rho=0.85, n=2: 3% → 2.2% per bet
```

**概念正確** — BTC 同 ETH 15M correlation 確實 > 0.80。同時 trade 兩個 = 接近 double exposure。

**但 formula 太 aggressive：**
1. ρ=0.85 係 BTC-ETH price correlation，唔係 15M binary outcome correlation。Binary outcome (UP/DOWN) correlation 會比 price correlation 低，因為 noise 在 15M 放大。實際 binary ρ 可能 0.50-0.70。
2. 我哋 bankroll 只有 $133，bet_pct=3%=$4。Correlation adjust to 2.2% = $2.93。扣除 CLOB minimum 5 shares × $0.40 = $2.00 → 只剩 $0.93 headroom。接近 minimum order size。
3. **更簡單嘅做法**：max_concurrent_markets = 2（已經係）+ 唔同時 enter BTC+ETH 同一 window（加 30s stagger）。

**結論**: 唔用 correlation sizing formula。改為 stagger BTC/ETH entry（如果同一 window，BTC 先入 → 等 30s → ETH 再入 or skip）。

#### CUSUM Circuit Breaker

**Over-engineering。**

Current system: 5 consecutive losses → 24h cooldown + 20% daily loss limit → halt。

Sub-agent 建議 CUSUM with 15% trip + correlation-aware loss counting。

**問題**:
1. 我哋得 94 trades，最長連敗係 3（唔係 5）。Current CB 已經 never triggered。
2. CUSUM 需要 calibrate drift parameter μ → 冇足夠 data。
3. 24h cooldown 太長？可能。但 v15 有 hard stop at 20% total loss，呢個比 CUSUM 更直接。

**結論**: 現有 CB 冇問題。改嘅 ROI < 0。

#### Time-of-Day

**有道理但冇 data 支持。**

Sub-agent 建議 US open × 0.7, late US × 0.6。BTC 24/7 trade，唔同 equity 有明確 session。15M market maker 唔一定受 session 影響 — BTC 15M binary outcome 接近 coin flip，session 只影響 vol（而 vol 已經在 bridge 入面）。

**需要做嘅嘢**: 從 mm_trades.jsonl 按小時分 group → 睇 WR 有冇 significant pattern。如果冇 → skip。

#### Regime Detection

**概念啱，thresholds 未驗證。**

VR (vol ratio) + ER (efficiency ratio) 係合理嘅 regime indicators。但：
1. Thresholds (VR<0.8 = QUIET, VR≥1.3 = VOLATILE) 冇 Polymarket 15M 數據支持
2. 需要 2-4 週 paper data 驗證
3. Bridge 已經自動 adjust for vol — QUIET regime → low vol → bridge less confident → smaller directional bet。Regime detection 做嘅嘢 bridge 部分已經做咗。

**結論**: DEFER 到有 1000+ trades 嘅 data。

---

## 整合：實際行動優先順序

### 即做（Day 1-2）

| # | 行動 | 時間 | 原因 |
|---|------|------|------|
| 1 | **啟動 v15 live** | 5 min | TTL fix 係最高優先。冇 live data = 冇進展 |
| 2 | **等 48h 收 per-order data** | 0 | 被動等待。mm_order_log.jsonl 自動記錄 |

### 48h 後（Day 3）

| # | 行動 | 時間 | 原因 |
|---|------|------|------|
| 3 | **AS diagnostic**: `groupby(time_to_fill quartile).agg(wr)` | 30 min | 用 mm_order_log.jsonl 嘅 fill data |
| 4 | **Time-of-day check**: `groupby(hour).agg(wr, n)` | 30 min | 用 mm_trades.jsonl |
| 5 | **BTC-ETH entry stagger**: 如果同一 window，BTC 先 → 30s gap → ETH | 1h | 簡化版 correlation sizing |

### 1 週後（有 ~200+ trades）

| # | 行動 | 時間 | 原因 |
|---|------|------|------|
| 6 | **Debiased WR check**: compare backtest WR vs live WR by period | 2h | 驗證 overfitting claim |
| 7 | **EV reconciliation**: avg win × WR - avg loss × (1-WR) vs reported | 1h | 數學對唔對帳 |

### SKIP（唔做）

| 建議 | 原因 |
|------|------|
| Logistic fill model | n=8, need 100+ |
| Heckman correction | n=8, need 500+ |
| EW toxicity score | CVD covers this |
| TSRV vol | Close-to-close already handles noise |
| Log-odds calibration | No calibration data |
| CUSUM CB | Current CB works, never triggered |
| Walk-forward framework | Overkill for $133 bankroll |
| Regime detection | Bridge already adjusts for vol |
| Multi-exchange vol | ρ>0.99 between exchanges, minimal gain |

---

## 最重要嘅 1 句話

**v15 嘅核心改進（TTL fix + Student-t + per-order log）已經做完。剩下嘅 proposals 大部分係 premature optimization — 冇 data 支持，或者 bankroll 太細唔 justify complexity。等 48h live data 再決定下一步。**

---

## 風險聲明

| 風險 | 確定程度 | Mitigation |
|------|---------|------------|
| v15 TTL 太長 → 被 adverse select | 中 | window_end-3min 硬 cancel 兜底 |
| Student-t(ν=5) 唔 fit BTC 嘅 actual kurtosis | 中 | ν=5 比 old HC 更準（verified），即使唔完美 |
| 繼續 0% fill rate（唔係 TTL 問題） | 低 | Per-order log 會 show 真正 cancel reason |
| BTC 趨勢逆轉 → WR 大跌 | 中 | Hard stop at 20% total loss + rolling WR check |
