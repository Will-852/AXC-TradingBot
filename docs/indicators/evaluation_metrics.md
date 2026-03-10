# 評估指標 + 風險管理深度研究
> 更新：2026-03-10
> 涵蓋：Sharpe、Sortino、Calmar、Drawdown、Kelly、Risk of Ruin、Position Sizing

---

## ⚠️ Crypto 特殊注意
- **年化用 N=365**，唔係股票嘅 252 — 用 252 會低估波動率 ~17%
- **無風險利率**：crypto 冇真正嘅 risk-free rate，常用 0% 或 USDT lending rate (~3-5%)
- **Backtest PF > 4.0 係紅旗** — 幾乎肯定過度擬合。預期 live 會退化 30-50%

---

## 第一層：每筆交易值唔值得做

### 1. Expectancy（期望值）
```
E = (Win% × AvgWin) - (Loss% × AvgLoss)
```
- E > 0 = 長期賺錢
- E < 0 = 長期蝕錢
- **最重要嘅單一數字** — 正 EV 係一切嘅前提

| Expectancy | 評級 |
|---|---|
| < 0 | 蝕錢系統 ❌ |
| 0-0.1R | 微利（交易費可能食晒）|
| 0.1-0.3R | 可行 |
| 0.3-0.5R | 良好 |
| > 0.5R | 優秀（檢查是否過擬合）|

### Per-trade vs Per-dollar
- Per-trade E：每筆交易平均賺幾多 R
- Per-dollar E = Expectancy × Trade Frequency
- 頻率高 + 低 E 可以 > 頻率低 + 高 E

### 2. Profit Factor（利潤因子）
```
PF = ΣGross Wins / Σ|Gross Losses|
```

| PF | 評級 |
|---|---|
| < 1.0 | 蝕錢 ❌ |
| 1.0-1.5 | 微利（容易被滑點/手續費侵蝕）|
| 1.5-2.0 | 良好 |
| 2.0-3.0 | 優秀 |
| 3.0-4.0 | 非常好（double check 唔係過擬合）|
| > 4.0 | ⚠️ 幾乎肯定過擬合 |

### 3. R-Multiple（風險倍數）
```
R = (Exit Price - Entry Price) / (Entry Price - Stop Loss)
```
- 1R = 賺返止損嘅距離
- 2R = 賺兩倍止損距離
- -1R = 蝕晒止損距離

**Van Tharp 分佈分析**：
- 收集所有交易嘅 R-Multiple
- 畫直方圖 → 正偏 = 好系統
- 平均 R > 0 = 正 EV

---

## 第二層：策略掂唔掂

### 4. Sharpe Ratio
```
Sharpe = (Rp - Rf) / σp

Rp = 平均回報（年化）
Rf = 無風險利率（crypto 用 0% 或 3%）
σp = 回報標準差（年化）
年化：σ_annual = σ_daily × √365
```

| Sharpe | 評級 |
|---|---|
| < 0 | 蝕錢 |
| 0-0.5 | 差 |
| 0.5-1.0 | 一般 |
| 1.0-2.0 | 好 |
| 2.0-3.0 | 非常好 |
| > 3.0 | 優秀（或者過擬合）|

**BTC 參考**：2025 年 BTC 12個月 Sharpe = 2.42（超越大部分傳統資產）

**⚠️ 缺陷**：Sharpe 懲罰所有波動 — 包括向上嘅波動！crypto 大升對 Sharpe 係壞事。所以 **Sortino 更適合 crypto**。

### 5. Sortino Ratio
```
Sortino = (Rp - Rf) / σd

σd = 下行標準差（只計負回報）
```

**計算下行標準差嘅關鍵**：
- 正回報 → 計為 0（唔係排除！）
- 負回報 → 用實際值
- 然後計標準差
- ⚠️ **常見 bug**：排除正回報（而唔係設為 0）會令 Sortino 虛高

| Sortino | 評級 |
|---|---|
| < 1.0 | 一般 |
| 1.0-2.0 | 好 |
| 2.0-3.0 | 非常好 |
| > 3.0 | 優秀 |

**Crypto 參考**：被動 BTC 持有 Sortino ≈ 1.93；主動管理策略可達 3.83

### 6. Max Drawdown
```
MDD = (Peak - Trough) / Peak × 100%
```

**Drawdown 恢復表（非對稱性）**：
| 蝕 | 需要升返 | 備註 |
|---|---|---|
| 10% | 11.1% | 可控 |
| 20% | 25.0% | 開始痛 |
| 30% | 42.9% | 嚴重 |
| 40% | 66.7% | 非常嚴重 |
| 50% | 100.0% | 翻倍先回本 |
| 75% | 300.0% | 幾乎不可能恢復 |

**公式**：`Recovery % = 1/(1 - DD%) - 1`

**目標**：保持 Max DD < 20%。超過 30% 就係系統性問題。

### 7. Calmar Ratio
```
Calmar = Annualized Return / |Max Drawdown|
```
- 計算期通常 36 個月
- 衡量：賺嘅錢值唔值得捱嗰次最大回撤

| Calmar | 評級 |
|---|---|
| < 0.5 | 差 |
| 0.5-1.0 | 一般 |
| 1.0-3.0 | 好 |
| > 3.0 | 優秀 |

---

## 第三層：落幾多注

### 8. Kelly Criterion
```
完整 Kelly:
f* = (b × p - q) / b

簡化版（trading）:
K% = W - (1-W)/R

W = Win Rate
R = Avg Win / Avg Loss (Reward/Risk)
b = 賠率 = R
p = Win probability = W
q = 1 - W
```

**例子**：55% win rate, 1.5:1 R/R
```
K% = 0.55 - 0.45/1.5 = 0.55 - 0.30 = 25%
```

**實戰規則**：
| 方法 | 佔 Kelly % | Drawdown | Growth |
|---|---|---|---|
| Full Kelly | 100% | 50%+ 常見 | 理論最優 |
| Half Kelly | 50% | 約 25% | 75% of optimal growth |
| **Quarter Kelly** | **25%** | **約 12%** | **50% of optimal growth** |
| 固定 1-2% | ~10-20% | < 15% | 保守但穩定 |

**⚠️ 實戰建議**：
- 計算 Kelly → 除以 4 → Cap at 5%
- Quarter Kelly 已經足夠好：保留約 50% 理論增長，大幅降低爆倉風險
- Full Kelly 需要完美知道真實勝率 — 你永遠唔會知道

### 9. Risk of Ruin（破產風險）

#### Kaufman 公式
```
RoR = ((1 - Edge) / (1 + Edge)) ^ (Capital / Risk_per_trade)

Edge = Win% - Loss%
```

#### 關鍵洞察
- **Position size 減半，RoR 指數級下降**
- 從 2% risk 降到 1% risk → RoR 可以跌 80-90%
- 邊際安全性極高 — 每減少 1% 風險都大幅降低破產機率

| Risk per Trade | Approximate RoR (55% WR) |
|---|---|
| 5% | ~25% |
| 3% | ~5% |
| 2% | ~1% |
| 1% | ~0.01% |

**AXC 現有值**：`RISK_PER_TRADE_PCT = 0.02`（2%）— 合理

---

## 第四層：會唔會死

### Win Rate vs R:R 嘅根本關係
```
Breakeven: Win% = 1 / (1 + R:R)
```

| R:R | 需要嘅 Win Rate |
|---|---|
| 1:1 | 50% |
| 1.5:1 | 40% |
| 2:1 | 33.3% |
| 3:1 | 25% |

**含義**：R:R 越高，Win Rate 可以越低。但心理上低 Win Rate 好難捱。

### Z-Score of Trades（交易序列相關性）
```
Z = (N × (R-0.5) - P) / √(P(P-N)/(N-1))

N = 總交易數
R = 連續同方向交易嘅次數（runs）
P = 2 × W × L（W=贏嘅次數，L=輸嘅次數）
```

| Z | 含義 |
|---|---|
| > +2 | 贏/輸交替出現（streak 少過隨機）|
| -2 to +2 | 隨機 |
| < -2 | 贏/輸傾向連續（streak 多過隨機）|

**用途**：如果 Z < -2（連勝/連敗明顯），可以：
- 連贏後加碼
- 連敗後減碼
- ⚠️ 需要大樣本（200+ trades）先可靠

---

## 第五層：Position Sizing 方法

### Fixed Fractional（固定比例）
```
Position Size = (Account × Risk%) / Stop Distance
```
- 最常用、最簡單
- AXC 現有方法

### Fixed Ratio（固定比率）
```
每增加 delta $ 利潤 → 加 1 unit
```
- 適合小賬戶快速增長
- 但 drawdown recovery 更慢

### Optimal f（Ralph Vince）
- 最大化幾何增長率嘅 fraction
- = `最大虧損 / negative optimal_f`
- **⚠️ 非常激進** — drawdown 常超過 50%
- 實戰唔建議用，學術價值為主

### Anti-Martingale
- 贏→加碼，輸→減碼
- 同 Fixed Fractional 嘅自然結果一致
- 唔好同 Martingale（輸→加碼）搞混 — Martingale 必死

---

## Edge Decay（策略衰退）

### Crypto 特殊性
- Crypto 策略平均壽命：**3-18 個月**
- 比傳統市場短得多
- 原因：市場結構快速演變、新參與者、監管變化

### 偵測方法
1. 30 天滾動 Sharpe / Sortino 持續下降
2. Win Rate 明顯低過 backtest
3. Profit Factor 跌到 1.5 以下
4. 連續虧損次數超過歷史最長

### 應對
- 定期 re-optimize（每月/每季）
- Walk-Forward Analysis 取代 single backtest
- 多策略組合分散 edge decay 風險

---

## Backtest 正確方法

### 唔好做
- ❌ 單一 in-sample 測試然後直接上 live
- ❌ 不斷調參數直到 backtest 完美（overfitting）
- ❌ 用全部數據 train + test（data leakage）

### 要做
1. **Walk-Forward Analysis (WFA)**：
   - 滾動窗口：train → test → 前移 → 重複
   - 例：用 6 個月 train，1 個月 test，前移 1 個月
   - 最低標準

2. **Out-of-Sample (OOS) Testing**：
   - 保留 30% 數據完全唔掂
   - Train 完先用 OOS 驗證

3. **CPCV（Combinatorial Purged Cross-Validation）**：
   - 現時最佳實踐
   - 多次隨機分割 + purge overlap
   - 但實現複雜

### Overfitting 紅旗
- Backtest PF > 4.0
- Sharpe > 5.0
- Win Rate > 80%（除非 R:R < 0.5）
- 參數微調 ±5% 就大幅影響結果
