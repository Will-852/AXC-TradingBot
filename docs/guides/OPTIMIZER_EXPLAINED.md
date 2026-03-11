# Backtest Optimizer 完整解說（talk12 版）
> 寫於 2026-03-11 | 對應代碼：`backtest/` 目錄

---

## 大畫面：我哋做咗咩

想像你開咗間餐廳（trading bot），有兩個廚師：

- **Range 廚師**：專煮「市場行來行去」嘅菜（橫行市）
- **Trend 廚師**：專煮「市場一路升/跌」嘅菜（趨勢市）

---

## 問題 1：廚師太揀擇，幾乎唔出菜

之前嘅設定好似同廚師講：「食材要100分先准煮」。結果 180 日只出咗 7 碟菜（7 筆交易）。咁少冇辦法知道廚師煮嘢究竟好唔好食。

**我哋做咗咩**：放寬標準，等廚師肯出多啲菜。

具體改咗 3 個最重要嘅門檻（`config/params.py`）：

| 設定 | 之前 | 之後 | 白話 |
|------|------|------|------|
| `PULLBACK_TOLERANCE` | 1.5% | 2.5% | Trend 廚師之前要求價格同 MA50 差距 <1.5% 先肯煮，而家放寬到 2.5% |
| `TREND_MIN_KEYS` | 4/4 全過 | 3/4 就得 | 之前要 4 個指標全部亮綠燈，而家 3 個就夠 |
| `adx_range_max` | 18 | 25 | Range 廚師之前要求市場「非常靜」先肯煮，而家「普通靜」都得 |

**結果**：7 筆 → 214 筆交易，虧 $223 → 賺 $7,173。

---

## 問題 2：所有菜一視同仁

放寬之後出多咗菜，但有啲菜好食（強信號），有啲普普通通（弱信號）。問題係餐廳對每碟菜都用一樣份量嘅食材（一樣大嘅注碼）。

好似你考試考 95 分同考 60 分，老師都畀你一樣嘅獎勵。唔合理。

**我哋做咗咩**：加咗「評分系統」，每個信號都有分數。

- **min_score 過濾**：分數太低嘅信號直接唔做（好似話「低過 60 分嘅菜唔好出」）
- **按分數調注碼**：高分信號下注大啲，低分信號下注細啲

分數點計？每個信號由幾個因素加起嚟：
- 基本分（指標有幾強）
- 成交量加成（多人買賣 = 信號更可靠）
- 資金流方向（OBV：大戶係咪同方向）

---

## 問題 3：調注碼有陷阱

加咗評分之後，我哋用電腦自動搵「最佳設定」。但發現 3 個陷阱：

### 陷阱 A：懸崖效應（Cliff Edge）

之前嘅設計係：分數 ≥ 4.5 → 注碼 ×1.25，分數 < 4.5 → 注碼 ×1.0。

問題：4.49 分同 4.51 分只差 0.02，但注碼差 25%。好似考試 59 分肥佬、60 分合格，差一分天同地。

**修正**：改成斜坡。分數 3.0→4.5 之間，注碼由 1.0x 慢慢升到 1.25x。冇突然跳躍。

```
注碼倍數
1.25 ─────────────────────╱━━━━━━━  ← 4.5 以上 = 最大
                        ╱
1.0  ━━━━━━━━━━━━━━━━╱              ← 3.0 以下 = 正常
     ──────────────────────────────
          3.0        4.5     分數
```

### 陷阱 B：電腦走捷徑

自動搜索可能搵到嘅「最佳設定」係：「所有信號都下最大注碼」。喺升市，大注碼梗係賺更多。但呢個唔係搵到更好嘅信號，只係加大賭注。一跌就爆。

好似話：「考試最佳策略係每題都寫最長答案」——喺某啲考試啱，但唔係真正嘅實力。

**修正**：加咗硬上限 `MAX_RISK_PCT = 5%`。無論評分幾高，每筆交易最多只冒 5% 風險。

### 陷阱 C：收縮拉回零

我哋有個防過擬合機制叫「收縮」：最終設定 = 70% 電腦搵到嘅值 + 30% 原本嘅值。

但 `min_score` 嘅原本值係 0（即唔過濾）。如果電腦搵到最佳 min_score = 2.5，收縮後變成 0.7×2.5 + 0.3×0 = 1.75。收縮永遠將佢拉向「唔過濾」，浪費咗優化結果。

**修正**：min_score 唔做收縮，直接用電腦搵到嘅值。

---

## 成個流程圖

```
Stage 1：放寬入場條件
  100 個隨機組合 × 3 個幣 → 搵出 6 個「可行配置」
  （要求：每個幣 ≥15 筆交易 + 總 PnL 正數）
          ↓
  套用最佳入場參數去 production
          ↓
Stage 2：優化評分權重  ← 之前壞咗（分數被丟棄），而家修好
  每個可行配置 × 80 次嘗試 × 8 個幣
  搵最佳權重（幾重視成交量？幾重視 OBV？過濾門檻幾高？）
          ↓
  Walk-Forward 驗證（用舊數據優化，新數據測試，防過擬合）
          ↓
  最終推薦設定
```

---

## 而家嘅狀態（2026-03-11）

- Stage 1 ✅ 完成，結果已套用去 production（`config/params.py`）
- Stage 2 之前跑過但冇用（因為 engine 忽略分數）
- Engine 已修好（分數過濾 + 分數調注碼 + 安全護欄）
- **下一步**：重跑 Stage 2，呢次權重優化會真正起作用

---

## 技術對照表

| 白話 | 代碼位置 | 變數/函數 |
|------|---------|----------|
| 廚師太揀擇 | `config/params.py` | `PULLBACK_TOLERANCE`, `TREND_MIN_KEYS`, `adx_range_max` |
| 評分系統 | `backtest/scoring.py` | `WeightedScorer.score_range()`, `score_trend()` |
| 過濾低分 | `backtest/engine.py:437-438` | `signal.score < self.min_score` |
| 按分調注碼 | `backtest/engine.py:483-486` | `risk_pct *= scorer.risk_multiplier(score)` |
| 斜坡（唔係懸崖） | `backtest/scoring.py:111-131` | `risk_multiplier()` linear ramp |
| 注碼硬上限 | `backtest/engine.py:49,486` | `MAX_RISK_PCT = 0.05` |
| 收縮跳過 min_score | `backtest/optimizer.py:722-724` | `_SHRINKAGE_SKIP = {"min_score"}` |
| 自動搜索 | `backtest/optimizer.py` | `run_stage1()`, `run_stage2()` |
| 防過擬合 | `backtest/optimizer.py` | `run_walk_forward()`, `apply_shrinkage()` |

---

## 改動文件清單

### 新建（backtest scope，唔影響 production）
- `backtest/scoring.py` — 評分公式
- `backtest/strategies/bt_range_strategy.py` — 可配置 range 策略
- `backtest/strategies/bt_trend_strategy.py` — 可配置 trend 策略
- `backtest/weight_config.py` — 搜索空間定義
- `backtest/optimizer.py` — 優化引擎
- `backtest/run_optimizer.py` — CLI 入口

### 修改
- `backtest/engine.py` — 加 score filtering + sizing + hard cap
- `config/params.py` — Stage 1 結果套用（3 個參數）
- `scripts/trader_cycle/strategies/trend_strategy.py` — 讀 TREND_MIN_KEYS
