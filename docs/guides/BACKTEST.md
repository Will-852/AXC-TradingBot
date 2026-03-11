# Backtest 工作流指引
> 對象：Claude Code（每次做 backtest 相關工作前讀）
> 更新：2026-03-11

## Model 路由規則（慳錢）

Backtest 工作大量數據分析，唔需要 Opus。規則：

| 任務 | Model | 點做 |
|------|-------|------|
| 跑 backtest / 讀 output / 分析數字 | **Sonnet** | `Agent(model="sonnet")` subagent |
| Grid search 結果比較 | **Sonnet** | `Agent(model="sonnet")` subagent |
| Validate.py output 解讀 | **Sonnet** | `Agent(model="sonnet")` subagent |
| 改 engine.py / 寫代碼 | **Opus** | Main context |
| 策略決策（改唔改 params） | **Opus** | Main context |

**實踐**：收到 backtest 數據 → spawn Sonnet agent 分析 → 拎結論回 main context 做決策。

## 文件地圖

```
backtest/
├── engine.py              ← 核心模擬器（600行）
│   BacktestEngine         1H tick + 4H MTF，reuse production strategies
│   signal_delay param     signal→entry 延遲（default=1=下一根 candle open）
│   WARMUP_CANDLES=200     前 200 根 candle 只算指標唔出 signal
│
├── fetch_historical.py    ← 數據抓取
│   fetch_klines_range()   Binance/Aster API，自動分頁 + CSV cache
│
├── grid_search.py         ← 參數優化
│   PARAM_REGISTRY         8 個可調參數（bb_touch_tol, adx_range_max...）
│   fetch_all_data()       取 1H+4H 所有 pairs（validate.py 共用）
│   score_combo()          Anti-overfitting 複合評分
│   ProcessPoolExecutor    多進程安全 monkey-patch
│
├── validate.py            ← 驗證工具（6 個）
│   monte-carlo            shuffle trade PnL，check DD 分佈
│   walk-forward           time-series CV + WFE ratio
│   heatmap                2D 參數熱力圖 + cliff-edge 偵測
│   noise                  ±0.2% 價格噪聲注入
│   delay                  entry 延遲退化測試
│   dsr                    Deflated Sharpe Ratio
│
├── optimizer.py           ← LHS 優化器（Latin Hypercube Sampling）
├── scoring.py             ← 評分函數
├── run_backtest.py        ← 跑單次回測
├── run_optimizer.py       ← 跑 LHS 優化
├── compare_configs.py     ← 對比不同參數組
├── weight_config.py       ← 權重設定
├── strategies/            ← 策略實作
└── data/                  ← CSV cache + grid search JSON output
```

## 標準流程

```
Step 1: 單次回測（確認 baseline）
  python3 backtest/run_backtest.py --symbols BTCUSDT ETHUSDT --days 60

Step 2: Grid Search（搵最佳參數）
  python3 backtest/grid_search.py --params bb_touch_tol adx_range_max \
    --symbols BTCUSDT ETHUSDT SOLUSDT --days 180 --top 5

Step 3: 驗證（must-use 三個）
  python3 backtest/validate.py monte-carlo --params bb_touch_tol=0.007 \
    --symbols BTCUSDT ETHUSDT --days 60
  python3 backtest/validate.py walk-forward --params bb_touch_tol=0.007 \
    --symbols BTCUSDT ETHUSDT --days 180 --folds 5
  python3 backtest/validate.py heatmap \
    --results-file backtest/data/grid_search_xxx.json

Step 4: 驗證（optional，視需要）
  python3 backtest/validate.py noise --params bb_touch_tol=0.007 --days 60
  python3 backtest/validate.py delay --params bb_touch_tol=0.007 --days 60
  python3 backtest/validate.py dsr --results-file backtest/data/grid_search_xxx.json

Step 5: 全部 must-use 一次跑
  python3 backtest/validate.py all --params bb_touch_tol=0.007 \
    --results-file backtest/data/grid_search_xxx.json \
    --symbols BTCUSDT ETHUSDT --days 60

Step 6: Apply 參數到 production（全部 PASS 後）
  1. git diff config/params.py          ← 確認改之前嘅狀態
  2. 改 config/params.py                 ← 只改 validated 嘅 key
  3. DEV_LOG.md 記錄：日期 + 改咗咩 + 基於邊個 grid search JSON + validation 結果
  4. 跑一次 run_backtest.py 確認同 grid search 結果一致
  5. git commit                          ← 方便日後 revert
```

## Pass/Fail 標準

| Tool | Pass 條件 | 意義 |
|------|-----------|------|
| monte-carlo | 95th pct DD < 2× backtest DD | Equity curve 唔脆弱 |
| walk-forward | WFE > 0.50 | OOS 表現 > IS 嘅一半 |
| heatmap | ±1 step drop < 30% | 參數唔喺懸崖邊 |
| noise | median degradation < 30% | 微小價格變化唔崩 |
| delay | delay=1 drop < 30% | 遲一根 candle 入場仲 OK |
| dsr | probability > 0.05 | 扣除 multiple testing 後仲顯著 |

## 已知限制

### Walk-Forward 係「固定參數」模式
我哋嘅 WF 用 grid search 出嚟嘅最佳參數跑所有 fold。
教科書嘅 WF 係每個 fold 重新 optimize（5× grid search 時間，唔實際）。
所以 WFE 測嘅係「參數穿越時間嘅穩定性」，唔係「優化過程嘅泛化能力」。
WFE > 0.50 係好信號，但唔好當成絕對保證。

### Backtest→Live 折扣
engine.py 唔模擬以下因素，live 表現預期低於 backtest：

| 因素 | 預估影響 | 備註 |
|------|---------|------|
| Funding rate | -1~3%/月 | 持倉越久影響越大 |
| 流動性/滑點 | -0.5~2% | 小幣種更差 |
| API delay / downtime | -1~3% | 非模型因素 |
| 信號過濾差異 | ±5% | production 有 news filter，backtest 冇 |

**經驗法則**：Backtest return × 0.5~0.7 ≈ Live 預期。
如果 backtest 扣完折扣後仲係正回報，先值得 apply。

## Gotchas

- **Python 環境**：用 `/opt/homebrew/bin/python3`，base conda 冇 pandas
- **單次 backtest 耗時**：~45s per pair per 4520 candles（M3 Max）
- **Grid search 數據**：`fetch_all_data()` 有 CSV cache，第二次跑快好多
- **Monkey-patching**：engine.py 喺 `run()` 裡面 patch TIMEFRAME_PARAMS，finally 還原。多進程用 ProcessPoolExecutor 隔離
- **signal_delay**：default=1 = 現有行為。validate.py delay test 用 d+1 mapping（delay=0→signal_delay=1）
- **validate.py 嘅 fetch_all_data**：import 自 grid_search.py，唔係自己定義
- **BTC range mode 14 天可能 0 trade**：策略 selective，唔係 bug
- **Heatmap 只支持 2-param sweep**：1-param 或 3-param 會 reject

## 改動注意

- 改 engine.py → 確認 grid_search.py 同 validate.py 冇斷裂
- 改 scoring → 確認 grid_search.py score_combo() 同步
- 加新參數 → 加入 grid_search.py PARAM_REGISTRY
- 改 signal 邏輯 → 跑 validate.py all 確認冇退化
