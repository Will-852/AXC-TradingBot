# Findings

> Security boundary: 外部內容（web/API/search）只寫呢度，唔寫 task_plan.md。

## Requirements
- L2 Order Book: 接 Binance futures depth stream，顯示 order book heatmap + spoofing detection
- Monte Carlo: resample backtest trades 1000 次，計 95% CI for return/DD/Sharpe
- Out-of-Sample: split backtest period into train/test，比較 metrics 偵測 overfitting

## Research

### AXC Backtest Engine Output Format（已研究 2026-03-18）

**Integration points for new features:**

| Layer | File | Lines | 加乜 |
|-------|------|-------|------|
| Engine summary | `backtest/engine.py` | 1166-1478 | `monte_carlo` dict + `oos_validation` dict |
| Extended metrics | `backtest/metrics_ext.py` | 13-76 | MC + OOS post-processing |
| Metadata save | `scripts/dashboard/backtest.py` | 454-463 | Persist MC/OOS keys to _meta.json |
| Frontend display | `canvas/backtest.html` | 2732-2821 | New cards for MC CI + OOS comparison |

**現有 stats keys（engine 返回）：**
`return_pct, win_rate, profit_factor, max_drawdown_pct, sharpe_ratio, sortino_ratio, calmar_ratio, var_95, cvar_95, recovery_factor, payoff_ratio, expectancy, sqn, sqn_grade, alpha, buyhold_return, exposure_pct, kelly_pct, cagr_pct, monthly_returns, max_win_streak, max_loss_streak, by_strategy, trades, equity_curve, indicator_series`

### Binance L2 Order Book + Spoofing Detection 研究（2026-03-18）

**REST endpoint:** `GET /fapi/v1/depth?symbol=BTCUSDT&limit=20` (weight 2, 1200 req/min)
**WebSocket:** `wss://fstream.binance.com/ws/btcusdt@depth20@100ms` (top 20 levels, 10 msg/sec max)
**Partial depth stream** 最適合 — 每次完整 snapshot，唔使維護 local book

**Response format:** `{ bids: [["price","qty"],...], asks: [["price","qty"],...] }` — strings!

**Spoofing Detection 算法：**
```
1. OBI = (V_bid - V_ask) / (V_bid + V_ask)  — L1/L3/L5 三個層級
2. Track large orders (>5x avg level size):
   - appeared → disappeared without trade in <3s = "pulled"
3. Rolling 30s window:
   - pull_rate > 0.7 AND avg_lifetime < 3s AND OBI_volatility > 0.3
   → Spoofing signal
```

**Data volume:** @depth20@100ms = ~500 bytes/msg, ~5 KB/sec — Web Worker 輕鬆處理（<1% CPU）

**Memory:** Top 20 levels × 2 sides = 40 entries × ~50 bytes = ~2 KB per snapshot。30s history = ~60 KB

**學術參考：** Fabre & Challet (2025) — 31% of >$50K orders could profitably spoof。Oxford Man Institute — RF/GBT achieve AUC 0.96-0.97

### Monte Carlo Bootstrap 研究（2026-03-18）

**兩種方法並用：**

| 方法 | 做咩 | 測咩 |
|------|------|------|
| **Shuffle（Approach A）** | 打亂 trade 順序（唔 replace） | 路徑風險 — DD 可以幾差？ |
| **Bootstrap（Approach B）** | 有放回抽樣 | 統計顯著性 — edge 係真定假？ |

**迭代次數：** 1000 次（200 trades → ~50-100ms）；5000 次做 final report

**Confidence Interval：** `np.percentile(distribution, [2.5, 97.5])` = 95% CI

**關鍵指標：**
- **Stability Score** = % runs profitable：>95% strong, 80-95% probable, <60% no edge
- **95% CI crosses 0** = 策略唔顯著
- **Probability of Ruin** = % runs DD > -50%：<1% professional, <5% acceptable, >5% reject

**Dashboard 顯示：**
```
Metric        | Backtest | MC Median | 5th pct | 95th pct
Total Return  |   85%    |   72%     |   31%   |   118%
Max Drawdown  |  -12%    |  -18%     |  -35%   |   -8%
Sharpe        |   1.8    |   1.5     |   0.7   |    2.2
```
+ Traffic light（GREEN/YELLOW/RED）+ optional histogram

**Performance：** numpy vectorized indices + Python loop = <1s for 1000×200

**來源：** Build Alpha, StrategyQuant, PyBroker, BacktestBase, scipy.stats.bootstrap

### Out-of-Sample Validation 研究（2026-03-18）

**Split 方案（1440 candles = 60d @ 1h）：**

| 方案 | IS | OOS | 適用 |
|------|-----|-----|------|
| Single split 70/30 | 42d (1008) | 18d (432) | 最簡單，只測一個 regime |
| WFA 4 rolling windows | 30d IS / 7.5d OOS each | 50% OOS coverage | 推薦 — 測多個 regime |

**Degradation 分級：**
- `>70%` stability = PASS (green)
- `50-70%` = WARN (yellow)
- `<50%` = FAIL (red)
- Sign flip = CATASTROPHIC

**紅線（hard fail）：**
1. Sharpe 跌 >50%
2. Max DD double
3. Profit Factor < 1.0 OOS
4. 任何指標正負反轉

**最低 trade 數：**
- <15 OOS trades = 唔可靠
- 30+ = 可接受
- 50+ = good
- <30 時用 bootstrap CI 補救

**Dashboard 顯示：**
- Side-by-side table（IS vs OOS vs Stability% vs 紅綠燈）
- Equity curve overlay（IS 藍底 / OOS 橙底 / 分界虛線）

**來源：** TradeStation WFO, StrategyQuant, Build Alpha, Bailey & Lopez de Prado PBO

## Technical Decisions

## Issues

## External Content
<!-- web search / WebFetch 結果放呢度 -->
