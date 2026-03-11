<!--
title: 想改咩？改邊度？
section: 參數速查
order: 16
audience: human
-->

# 想改咩？改邊度？

你唔需要睇晒成個系統先可以改設定。呢頁幫你快速搵到：「我想改 X → 改邊個文件、邊個變數」。

---

## 信號到落單嘅完整邏輯

要明白改參數會影響咩，先睇信號點樣變成一張單：

```
你嘅參數                    系統邏輯                     結果
─────────                 ────────                   ────────

SCAN_INTERVAL_SEC ───────▶ 掃描器幾密檢查市場
TRIGGER_PCT_* ───────────▶ 幾大波動先觸發分析
                              │
BB_TOUCH_TOL ────────────▶ BB 觸碰判定（Range 入場）
MODE_RSI_TREND_LOW/HIGH ─▶ 5 票偵測 RANGE 定 TREND ─────▶ 揀策略
MODE_VOLUME_LOW/HIGH ────▶
MODE_FUNDING_THRESHOLD ──▶
MODE_CONFIRMATION ───────▶ 要確認幾多次先切換
                              │
                         策略產生信號（LONG/SHORT）
                              │
ENTRY_VOLUME_MIN ────────▶ 成交量夠唔夠？唔夠就跳過
OBV_CONFIRM_BONUS ───────▶ OBV 同方向加分 / 反方向扣分
                              │
PAIR_PRIORITY ───────────▶ 7 隻幣排名，揀最高分
POSITION_GROUPS ─────────▶ 同組有倉就唔開新
                              │
RANGE_RISK_PCT ──────────▶ 落幾多錢？（風險%）
RANGE_SL_ATR_MULT ───────▶ 止蝕幾遠？（ATR 倍數）
RANGE_MIN_RR ────────────▶ 回報比夠唔夠？唔夠唔開
RANGE_LEVERAGE ──────────▶ 幾倍槓桿？
CONFIDENCE_RISK_* ───────▶ 信心高加碼 / 信心低縮減
                              │
                         計算 entry / SL / TP / 倉位大小
                              │
                         落盤 → 設 SL → 設 TP
                              │
TRAILING_SL_BREAKEVEN ───▶ 賺到幾多移 SL 到保本
TRAILING_SL_LOCK_PROFIT ─▶ 賺更多鎖利
EARLY_EXIT_RSI_* ────────▶ RSI 觸發提前出場
TP_EXTEND_* ─────────────▶ 趨勢夠強延伸 TP
                              │
CIRCUIT_BREAKER_SINGLE ──▶ 單倉虧 25% → 即平
CIRCUIT_BREAKER_DAILY ───▶ 日虧 20% → 停所有
MAX_HOLD_HOURS ──────────▶ 72 小時 → 強制平
```

**改任何左邊嘅參數 → 會影響右邊嘅行為。**

---

## 點樣自訂你嘅打法？

### 方法 1：用內建 Profile（最簡單）

改 `config/params.py` 嘅 `ACTIVE_PROFILE`：

```python
# 揀其中一個，取消註釋：
ACTIVE_PROFILE = "AGGRESSIVE"   # 攻：2.5% risk, 寬 SL, R:R 較低（目前啟用）
# ACTIVE_PROFILE = "BALANCED"   # 平：2.0% risk
# ACTIVE_PROFILE = "CONSERVATIVE" # 穩：1.5% risk, 嚴 SL, R:R 較高
```

### 方法 2：用 user_params.py（推薦新用戶）

唔想改原始 `params.py`（怕 git pull 衝突）：

```bash
# 1. 從 example 複製（只包含常用設定，唔使整份 params.py）
cp ~/projects/axc-trading/config/user_params.py.example ~/projects/axc-trading/config/user_params.py

# 2. 取消你想改嘅變數嘅註釋，改值
nano ~/projects/axc-trading/config/user_params.py
```

`user_params.py` 係 gitignored 嘅 — git pull 永遠唔會覆蓋你嘅設定。只寫你想 override 嘅變數，其餘自動用 `params.py` 預設值。

### 方法 3：寫自己嘅外部 .py（進階）

如果你有獨特嘅交易邏輯（例如新嘅指標組合），可以寫一個獨立 .py 文件：

```python
# config/my_strategy.py（你自己寫嘅）
# 呢個文件嘅變數會覆蓋 params.py 嘅同名變數

ACTIVE_PROFILE = "MY_STYLE"

TRADING_PROFILES = {
    "MY_STYLE": {
        "risk_per_trade_pct": 0.015,   # 1.5%
        "sl_atr_mult": 1.8,            # 寬 SL
        "range_min_rr": 2.0,           # 較低 R:R
        "trend_min_rr": 2.5,
        "max_open_positions": 2,
    },
}

# 你嘅模式偵測偏好
MODE_RSI_TREND_LOW = 35       # 比預設 32 寬鬆
MODE_RSI_TREND_HIGH = 65      # 比預設 68 寬鬆
MODE_CONFIRMATION_REQUIRED = 1  # 唔需要確認（更快切換）
```

然後改 `config/params.py` 最尾加一行：

```python
# 載入你嘅自訂設定（覆蓋以上所有）
from config.my_strategy import *
```

系統會無縫接受你嘅參數 — 因為 `settings.py` 喺啟動時會讀 `params.py`，而你嘅 import 會覆蓋佢。

---

## 最常改嘅 3 件事

### 1. 改交易風格

見上面「點樣自訂你嘅打法」。

### 2. 加 / 減交易幣種

詳見 `docs/guides/SYMBOLS.md`。需要改 **7 個位**：

| # | 文件 | 改咩 |
|---|------|------|
| 1 | `config/params.py` | ASTER_SYMBOLS 或 BINANCE_SYMBOLS |
| 2 | `scripts/trader_cycle/config/pairs.py` | 加 PairConfig（精度、組別） |
| 3 | `scripts/trader_cycle/config/settings.py` | PAIRS + PAIR_PREFIX + POSITION_GROUPS |
| 4 | `scripts/trader_cycle/strategies/evaluate.py` | PAIR_PRIORITY |
| 5 | `scripts/light_scan.py` | PAIRS（如果係 Aster 幣種） |
| 6 | `scripts/slash_cmd.py` | get_prices() loop |
| 7 | `agents/aster_scanner/workspace/SOUL.md` | pair 列表 |

### 3. 改止蝕 / 止賺 / 槓桿

文件：`scripts/trader_cycle/config/settings.py`

| 想改 | 變數 | 當前值 |
|------|------|--------|
| Range 槓桿 | `RANGE_LEVERAGE` | 8x |
| Trend 槓桿 | `TREND_LEVERAGE` | 7x |
| Range 止蝕距離 | `RANGE_SL_ATR_MULT` | 1.2 × ATR |
| Trend 止蝕距離 | `TREND_SL_ATR_MULT` | 1.5 × ATR |
| Range 最低回報比 | `RANGE_MIN_RR` | 2.3:1 |
| Trend 最低回報比 | `TREND_MIN_RR` | 3.0:1 |
| 每次風險 | `RANGE_RISK_PCT` / `TREND_RISK_PCT` | 2% |

注意：如果 `ACTIVE_PROFILE` 有設定，profile 值會覆蓋以上。想直接改，確保 profile 入面冇對應嘅 key。

---

## 完整速查表

### 風控相關

| 想改 | 文件 | 變數 | 當前值 |
|------|------|------|--------|
| 單倉最大虧損 | settings.py | `CIRCUIT_BREAKER_SINGLE` | 25% |
| 日度最大虧損 | settings.py | `CIRCUIT_BREAKER_DAILY` | 20% |
| 連輸 2 次冷卻 | settings.py | `COOLDOWN_2_LOSSES_MIN` | 30 min |
| 連輸 3 次冷卻 | settings.py | `COOLDOWN_3_LOSSES_MIN` | 120 min |
| 最長持倉時間 | settings.py | `MAX_HOLD_HOURS` | 72 小時 |
| 低流動性門檻 | settings.py | `NO_TRADE_VOLUME_MIN` | 0.50 |
| 極端資金費率 | settings.py | `NO_TRADE_FUNDING_EXTREME` | ±0.2% |
| 資金費率強制平倉 | settings.py | `FUNDING_COST_FORCE_RATIO` | 50% |

### 持倉分組

文件：`scripts/trader_cycle/config/settings.py` → `POSITION_GROUPS`

| 組別 | 幣種 | 最多倉位 |
|------|------|----------|
| crypto_correlated | BTC, ETH, SOL | 1（互斥） |
| crypto_independent | XRP, POL | 1（互斥） |
| commodity | XAG, XAU | 1（互斥） |

同組嘅幣種唔可以同時開倉。例如：已經有 BTC 倉 → ETH 同 SOL 唔會入場。

### 模式偵測（RANGE vs TREND）

文件：`config/params.py`（覆蓋 settings.py 預設）

| 想改 | 變數 | 當前值 | 意思 |
|------|------|--------|------|
| RSI 趨勢判斷 | `MODE_RSI_TREND_LOW/HIGH` | 32 / 68 | RSI 超出呢個範圍 = 趨勢票 |
| 成交量判斷 | `MODE_VOLUME_LOW/HIGH` | 0.50 / 1.50 | 成交量偏離均值 = 趨勢票 |
| 資金費率判斷 | `MODE_FUNDING_THRESHOLD` | 0.07% | 費率高 = 趨勢票 |
| 確認次數 | `MODE_CONFIRMATION_REQUIRED` | 2 | 需要連續 2 次 4H 確認（= 8 小時） |

5 個 voter 投票：RSI、MACD、成交量、資金費率、BB 寬度。3 票以上決定用 Range 定 Trend。

### 掃描相關

| 想改 | 文件 | 變數 |
|------|------|------|
| 掃描間隔 | params.py | `SCAN_INTERVAL_SEC`（預設 180 秒） |
| 觸發門檻 | params.py | `TRIGGER_PCT_*`（AGGRESSIVE = 2%） |
| BB 觸碰容忍度 | params.py | `BB_TOUCH_TOL_DEFAULT`（0.5%） |
| XRP 容忍度 | params.py | `BB_TOUCH_TOL_XRP`（0.8%） |
| BB 擠壓門檻 | params.py | `BB_WIDTH_MIN`（5%） |

### 進階：Yunis Collection

文件：`scripts/trader_cycle/config/settings.py`

| 功能 | 變數 | 當前值 | 意思 |
|------|------|--------|------|
| 成交量入場門檻 | `ENTRY_VOLUME_MIN` | 0.8 | 4H volume_ratio < 0.8 → 跳過 |
| MACD 減弱出場 | `MACD_HIST_DECAY_THRESHOLD` | 0.6 | histogram 縮到 60% → 弱化信號 |
| OBV 確認加分 | `OBV_CONFIRM_BONUS` | +0.5 | OBV 同方向 → 信號 +0.5 分 |
| OBV 反方向扣分 | `OBV_AGAINST_PENALTY` | -0.5 | OBV 反方向 → -0.5 分 |
| 高信心加碼 | `CONFIDENCE_RISK_HIGH` | 1.25x | score ≥ 4.5 → risk × 1.25 |
| 低信心縮減 | `CONFIDENCE_RISK_LOW` | 0.6x | score < 3.0 → risk × 0.6 |
| 風險上限 | `CONFIDENCE_RISK_CAP` | 3% | 無論幾高信心，單次唔超 3% |

### 移動止蝕 + 出場

文件：`scripts/trader_cycle/config/settings.py`

| 想改 | 變數 | 當前值 |
|------|------|--------|
| 保本觸發 | `TRAILING_SL_BREAKEVEN_ATR` | 1.0 × ATR 盈利 → SL 移到入場價 |
| 鎖利觸發 | `TRAILING_SL_LOCK_PROFIT_ATR` | 2.0 × ATR 盈利 → SL 移到入場 + 1 × ATR |
| RSI 超買出場 | `EARLY_EXIT_RSI_OVERBOUGHT` | 70（LONG 出場） |
| RSI 超賣出場 | `EARLY_EXIT_RSI_OVERSOLD` | 30（SHORT 出場） |
| TP 延伸 ADX | `TP_EXTEND_ADX_MIN` | 25（ADX 夠強先延伸 TP） |
| TP 延伸距離 | `TP_EXTEND_ATR_MULT` | 1.0 × ATR |
| 再入場冷卻 | `REENTRY_COOLDOWN_CYCLES` | 3 cycles ≈ 1.5 小時 |

---

## Config 文件嘅關係

```
config/my_strategy.py（你自己寫嘅，選填）
    │ import *
    ▼
config/params.py（主設定 — 或者用 user_params.py 覆蓋）
    │ settings.py 啟動時讀取
    ▼
scripts/trader_cycle/config/settings.py（引擎預設值）
```

- **新用戶**：改 `params.py` 嘅 `ACTIVE_PROFILE` 就夠
- **進階用戶**：複製 `config/user_params.py.example` → `config/user_params.py`，改裡面嘅值（gitignored，唔會衝突）
- **引擎層**：改 `settings.py`（例如 Yunis Collection、風控閾值）

## 改完之後點做？

1. **改 params.py / user_params.py** → 重啟 trader_cycle：`launchctl stop ai.openclaw.tradercycle && launchctl start ai.openclaw.tradercycle`
2. **改 settings.py** → 同上
3. **改 ASTER_SYMBOLS / BINANCE_SYMBOLS** → 重啟 scanner：`launchctl stop ai.openclaw.scanner && launchctl start ai.openclaw.scanner`
4. **想驗證先？** → 先跑回測：`python3 backtest/run_backtest.py --symbol BTCUSDT --days 30`

## 常見陷阱

- Profile 值會覆蓋 settings.py 嘅同名 key。如果改咗 settings.py 但 profile 有設定，你嘅改動會被蓋過
- scanner 每 10 輪會自動重新讀 params.py，但 trader_cycle 唔會 → 改完要重啟
- 加幣種要改 7 個位，唔好漏（詳見 SYMBOLS.md）
- `MAX_CRYPTO_POSITIONS` / `MAX_XAG_POSITIONS` 係 dead code，改咗冇用。實際由 POSITION_GROUPS 控制
- `MODE_CONFIRMATION_REQUIRED = 2` 用 4H candle，即係切換模式最少要 **8 小時**
