# Step 3: `scripts/trader_cycle/strategies/` — 策略邏輯（大腦）
> talk12 風格分析 | 2026-03-10

## 點樣搵到呢個文件夾

```
axc-trading/                     ← 你見到嘅最頂層
└── scripts/                     ← 撳入 scripts
    ├── indicator_calc.py        ← （呢啲係獨立工具，唔使理住）
    ├── async_scanner.py
    ├── tg_bot.py
    ├── ...
    └── trader_cycle/            ← 再撳入 trader_cycle（核心交易引擎）
        ├── main.py              ← 引擎入口
        ├── strategies/          ← ⭐ 就係呢度！Step 3 講嘅 5 個文件
        │   ├── mode_detector.py
        │   ├── range_strategy.py
        │   ├── trend_strategy.py
        │   ├── evaluate.py
        │   └── base.py
        ├── risk/                ← Step 4
        ├── exchange/            ← Step 5
        ├── state/               ← 狀態管理
        ├── config/              ← 交易設定（唔同頂層 config/）
        ├── notify/              ← TG 推送
        ├── analysis/            ← 交易統計
        └── core/                ← 核心框架
```

導航路線：`axc-trading → scripts → trader_cycle → strategies`

---

5 個文件，按執行順序講解。

---

## 1. `mode_detector.py` — 裁判：而家係橫行定趨勢？

**比喻：** 5 個評判舉牌投票，少數服從多數。

### 5 個評判（全部用 4H 數據）

| 評判 | 投「趨勢」嘅條件 | 投「橫行」嘅條件 | 唔夠數據 |
|------|----------------|----------------|---------|
| RSI | < 32 或 > 68 | 32-68 之間 | NEUTRAL |
| MACD | 柱狀圖越嚟越大（magnitude↑） | 縮小或近零 | NEUTRAL |
| Volume | < 50% 或 > 150% 平均 | 50%-150% | NEUTRAL |
| MA | 價格喺 MA50+MA200 上面/下面 | 夾住中間 | NEUTRAL |
| Funding | > ±0.07% | -0.07% ~ +0.07% | NEUTRAL |

### 投票結果
- 3+ 票同邊 → 判定（RANGE 或 TREND）
- 2:2:1 或以下 → UNKNOWN → 維持舊 mode

### 防手震：確認機制
- 連續 2 次投票結果一樣先切換 mode（`MODE_CONFIRMATION_REQUIRED = 2`）
- 4H cycle × 2 次 = 最少 8 小時先真正換 mode
- 從 UNKNOWN 狀態 → 第一次就接受

### DetectModeStep 邏輯
- 用 BTCUSDT 做主要判斷（最可靠）
- BTC 冇數據 → fallback 用第一個有 4H 數據嘅 pair
- 全部冇 4H → 維持舊 mode + 加 warning

### 當前狀態
- `MARKET_MODE: TREND`，已確認 9 個 cycle

---

## 2. `range_strategy.py` — 橫行策略（BB 反轉）

**比喻：** 打乒乓球 — 波彈到左邊牆就打返去右邊。

### 前提條件（由 mode_detector 保證）
- Market mode = RANGE

### 入場邏輯（1H timeframe）

**Pre-check（必須通過）：**
| 代號 | 條件 | 設定值 |
|------|------|--------|
| R0 | BB 寬度 > BB_WIDTH_MIN | > 5%（唔係 squeeze） |
| R1 | ADX < adx_range_max | < 20（冇方向） |

**入場觸發（C1 + C2 + C3 必須全過）：**
| 代號 | 條件 | 意思 |
|------|------|------|
| C1 | BB band touch | 價格掂到上/下 Bollinger Band |
| C2 | RSI reversal | RSI 超賣/超買後開始掉頭 |
| C3 | S/R proximity | 價格喺支撐位或阻力位附近 |
| C4（可選） | Stochastic crossover | 額外確認 → STRONG 信號 |

### 分數計算
```
base_score = 4.0 (STRONG) 或 3.0 (WEAK)

加分：
+ Volume bonus: ratio >= 2.0 → +1.0 | ratio >= 1.5 → +0.5
+ OBV confirm:  OBV 方向同信號一致 → +0.5
- OBV against:  OBV 方向相反 → -0.5（× min(volume_ratio, 1.0)）
```

### Volume Gate（Yunis Collection）
- 4H volume_ratio < 0.8 → 直接跳過，唔評估（低成交 = 低信心）

### 倉位參數
| 參數 | 值 | 意思 |
|------|-----|------|
| risk_pct | 2% | 每次冒 2% 本金風險 |
| leverage | 8x | 槓桿倍數 |
| sl_atr_mult | 1.2 | SL = 1.2 × ATR |
| min_rr | 2.3 | 最少贏 2.3 倍先入場 |

### 退出條件
- TP1: 價格到 BB 中線 → 平 50%
- TP2: 價格到對面 BB → 平剩餘
- SL: 交易所 order 處理
- ⚠️ `evaluate_exit()` 而家 return None — Phase 3 未實現

---

## 3. `trend_strategy.py` — 趨勢策略（追回調）

**比喻：** 火車向上行。唔係追火車，而係等佢停站（回調）先上車。

### 前提條件
- Market mode = TREND（已確認）

### LONG 入場：4 個 KEY 條件

| KEY | 時間框 | 條件 | 意思 |
|-----|--------|------|------|
| KEY1: MA | 4H | Price > MA50 AND Price > MA200 | 火車向上（上升結構） |
| KEY2: MACD | 4H | Histogram > 0 + 越嚟越大 | 引擎仲有力（動能） |
| KEY3: RSI | 1H | RSI 40-55 | 唔太熱，仲有位升 |
| KEY4: Price | 1H | 距 MA50 < 1.5% | 正在停站（回調入場區） |

### SHORT 入場：4 個 KEY 條件（反轉）

| KEY | 條件 |
|-----|------|
| KEY1 | Price < MA50 AND Price < MA200 |
| KEY2 | Histogram < 0 + 越嚟越大（絕對值） |
| KEY3 | RSI 45-60 |
| KEY4 | 距 MA50 < 1.5% |

### 星期偏好（Day Bias, UTC+8）
| 時段 | 偏好 | 效果 |
|------|------|------|
| 週四 21:00 - 週五 01:00 | SHORT | 3/4 KEY 就夠（唔使全過） |
| 週五 21:00 - 週六 03:00 | LONG | 3/4 KEY 就夠 |
| 其他時間 | 無偏好 | 必須 4/4 KEY 全過 |

### 分數計算
```
base_score = 5.0 (4/4 KEY = STRONG) 或 3.5 (3/4 KEY = BIAS)

加分：同 Range 策略一樣嘅 Volume + OBV bonus
```

### 退出條件（已持倉時每 cycle 檢查）
| 條件 | 觸發 | 意思 |
|------|------|------|
| MACD reversal | 4H histogram 正→負 或 負→正 | 動能反轉 |
| MACD weakening | histogram 縮到 <60% 且 R:R ≥ 1.0 | 動能衰減，有利潤先走 |
| MA cross | 價格返去 MA50 同 MA200 之間 | 趨勢結構崩壞 |

### 倉位參數
| 參數 | 值 | 同 Range 比較 |
|------|-----|-------------|
| risk_pct | 2% | 一樣 |
| leverage | 7x | 低啲（趨勢風險大） |
| sl_atr_mult | 1.5 | 大啲（畀多空間） |
| min_rr | 3.0 | 高啲（要求更高回報） |

---

## 4. `base.py` — 策略基類

```python
PositionParams:
    risk_pct      # 每次冒幾多 % 本金風險
    leverage      # 槓桿倍數
    sl_atr_mult   # 止損 = ATR × 呢個數
    min_rr        # 最少要贏幾倍先入場
    tp_atr_mult   # （未使用）TP = ATR × 呢個數

StrategyBase (ABC):
    - evaluate()          → 必須實現：評估入場
    - get_position_params() → 必須實現：倉位參數
    - evaluate_exit()     → 可選覆寫：退出條件
```

加新策略步驟：
1. 建新 `.py` 繼承 `StrategyBase`
2. 實現 `evaluate()` + `get_position_params()`
3. 在 `main.py` 用 `StrategyRegistry.register(MyStrategy())`

---

## 5. `evaluate.py` — 調度員

### EvaluateSignalsStep（Step 9）
```
流程：
1. 有 risk block？ → 跳過
2. 攞對應 mode 嘅策略（RANGE → RangeStrategy, TREND → TrendStrategy）
3. Mode 未 confirmed？ → 跳過
4. 對每個 pair 跑策略 → 收集所有信號
5. Re-entry boost：pair+direction 匹配 → score +0.5
6. 新聞過濾：bearish + confidence > 70% → block 所有 LONG
7. 輸出信號列表
```

### SelectSignalStep（Step 10）
```
排序規則：
1. Score 最高分先
2. 同分 → pair 優先級：BTC(4) > ETH(3) > XRP(2) > XAG(1)
3. 選第一名執行
```

---

## ⚠️ 分析中發現嘅問題

### 🟡 PAIR_PRIORITY 缺 SOL + XAU
`SelectSignalStep.PAIR_PRIORITY` 只有 4 個 pair：
```python
PAIR_PRIORITY = {"BTCUSDT": 4, "ETHUSDT": 3, "XRPUSDT": 2, "XAGUSDT": 1}
```
SOL + XAU 嘅 priority = 0（default），同分時永遠排最後。

**建議：** 加入 `"SOLUSDT": 3, "XAUUSDT": 1`（SOL 同 ETH 同級，XAU 同 XAG 同級）

### 🟡 Range evaluate_exit() 未實現
`range_strategy.py` line 186: `return None` — Phase 3 功能未完成。
而家靠交易所 SL/TP order 處理退出，冇主動退出邏輯。

### 🟢 Trend 退出邏輯已完整
MACD reversal + weakening + MA cross 三個條件都有實現。

---

## 自檢問題

1. **你知唔知而家係 TREND mode？** → 係，已確認 9 cycles
2. **TREND 策略要 4/4 KEY** → 好嚴格。180d backtest 顯示 Trend 策略 BTC 4W/2L、SOL 5W/2L，表現不錯
3. **Range 策略 180d backtest** → 6W/16L，有結構性問題（C3 S/R 判斷可能太寬）
4. **Day bias 合理嗎？** → 週四尾 SHORT、週五尾 LONG 係基於加密貨幣週末歷史模式
5. **SOL 同分時排最後** → 如果 SOL 同 BTC 同時出信號，BTC 永遠贏。需要修正 PAIR_PRIORITY。
