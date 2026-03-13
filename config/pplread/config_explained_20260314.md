# AXC 系統解讀
> 寫畀人睇嘅版本。唔需要識 code 都睇得明。
> 最後更新：2026-03-14

---

# Part 1: Dashboard 服務狀態

## 服務架構

AXC 有 8 個核心服務，全部用 macOS LaunchAgent 管理（`launchctl`）。

```
┌─────────────────────────────────────────────────┐
│  Dashboard (port 5566)                          │
│  ├── Scanner      — 掃描 9 間交易所（常駐）       │
│  ├── Trader       — 交易引擎（每 30 分鐘跑一次）   │
│  ├── Telegram     — TG Bot（常駐）               │
│  ├── Heartbeat    — 系統心跳（每 25 分鐘跑一次）   │
│  ├── LightScan    — 輕量掃描（每 3 分鐘跑一次）   │
│  ├── NewsBot      — 新聞抓取（常駐）              │
│  └── Report       — 報告生成（每 30 分鐘跑一次）   │
└─────────────────────────────────────────────────┘
```

## 兩種服務模式

| 模式 | 行為 | 例子 |
|------|------|------|
| **常駐** | 啟動後一直跑，有 PID | Scanner, Telegram, Dashboard, NewsBot |
| **定時跑** | 每隔 N 秒跑一次，跑完就退出 | Trader, Heartbeat, LightScan, Report |

## 狀態判斷邏輯

Dashboard 顯示綠燈/紅燈嘅邏輯：

```
launchctl list | grep openclaw
→ 返回每個服務嘅 PID + 上次 exit code

判斷規則：
  有 PID（正在跑）          → 🟢 綠燈
  冇 PID + exit code = 0   → 🟢 綠燈（跑完正常退出，等下次觸發）
  冇 PID + exit code ≠ 0   → 🔴 紅燈（上次跑失敗）
  plist 唔存在              → ⚫ 灰燈（服務未安裝）
```

### 2026-03-14 修復紀錄

**問題：**舊邏輯只睇有冇 PID → 定時服務永遠顯示紅燈（因為跑完就退出）

**修復：**
- `dashboard.py` — 加 `healthy` 欄位：`pid存在 OR exit==0`
- `canvas/index.html` — 用 `healthy` 決定燈色（唔再用 `running`）
- 改動位置：`handle_services()` + `renderServices()`

## 服務環境配置

每個 LaunchAgent 需要讀 `secrets/.env` 入面嘅 API keys（交易所、Telegram token 等）。
正確做法係用 `load_env.sh` wrapper：

```
正確 ✅（tradercycle 一直都係咁）：
  /bin/bash load_env.sh python3 main.py
  → load_env.sh source secrets/.env → exec python3

錯誤 ❌（heartbeat/lightscan/report 之前）：
  /opt/homebrew/bin/python3 script.py
  → 冇 secrets → Telegram 404、API key 空白
```

### 2026-03-14 修復紀錄

**問題：** heartbeat、lightscan、report 三個 plist 直接 call python3，冇經 `load_env.sh`
- Report 每次跑都 `Telegram send failed: HTTP Error 404`（token 空字串）
- Heartbeat/LightScan 有 secrets 需求嘅功能靜默失敗

**修復：** 三個 plist 全部改為 `/bin/bash load_env.sh python3 script.py` 模式

---

# Part 2: Config 系統

## Config 係咩？

想像你打機，有一個「設定」界面：難度、角色屬性、地圖選擇。
`config/` 就係成個交易系統嘅「設定界面」。所有行為都由呢度控制。

---

## 文件結構

```
config/
├── params.py                 ← 主設定面板（14 段，控制成個系統）
├── user_params.py.example    ← 你嘅私人 override 範本（唔上 git）
└── profiles/                 ← 三套「難度」
    ├── _base.py              ← 預設屬性（所有 profile 嘅起點）
    ├── conservative.py       ← 保守：低風險、穩打穩紮
    ├── balanced.py           ← 平衡：中間值（幾乎 = base）
    ├── aggressive.py         ← 進取：高風險、追趨勢
    └── loader.py             ← 讀取邏輯（決定邊個 profile 生效）
```

---

## params.py — 主腦（14 段）

呢個文件係中央控制台。每一段控制系統嘅一個部份。

### Section 1: 掃描設定
**比喻：雷達掃描**

系統掃描 9 間交易所（Aster、Binance、Hyperliquid、Bybit、OKX、KuCoin、Gate、MEXC、Bitget），
但唔係同時 hit 全部 — 輪住嚟，每 20 秒掃一間。

- `SCAN_INTERVAL_SEC = 20` — 每 20 秒掃一間
- `EXCHANGE_ROTATION = [9 間]` — 輪轉順序
- 效果：每間交易所每 180 秒（3 分鐘）先被 hit 一次，唔會觸發 rate limit

### Section 2: BB 指標（Bollinger Bands）
**比喻：價格碰到「牆」嘅容忍度**

Bollinger Bands 係兩條線夾住價格。當價格碰到上線或下線，可能係反轉信號。
但「碰到」唔係真係要完全觸碰 — 有個容忍度：

- `BB_TOUCH_TOL_DEFAULT = 0.005` — BTC/ETH：0.5% 以內算碰到
- `BB_TOUCH_TOL_XRP = 0.008` — XRP 波動大啲，容忍度放寬到 0.8%
- `BB_WIDTH_MIN = 0.05` — BB 太窄（<5%）代表市場冇乜波動，跳過

### Section 3: 時間框參數
**比喻：望遠鏡 — 近、中、遠**

同一件事用唔同時間尺度去睇，結論可以唔同。系統用 3 個時間框：

| 時間框 | 用途 | 特點 |
|--------|------|------|
| 15m | 短線入場時機 | 快速反應、多噪音 |
| 1h | 主要判斷 | 平衡速度同穩定 |
| 4h | 大方向確認 | 慢但可靠 |

每個時間框都有自己嘅一套指標參數（BB 長度、RSI 週期、EMA 快慢線等等）。
例如 1h 嘅 RSI long 門檻係 40（較寬鬆），15m 係 30（較嚴格）。

### Section 4: Trend 策略
**比喻：判斷「風向」然後順風跑**

Trend 策略 = 發現市場有方向，跟住去。

- `TREND_RSI_LONG_LOW/HIGH = 40/55` — RSI 喺 40-55 之間先考慮做 LONG
- `PULLBACK_TOLERANCE = 0.025` — 價格回調 2.5% 先算 pullback
- `TREND_MIN_KEYS = 3` — 4 個確認條件（MA_aligned、MACD、RSI_zone、Price_pullback）至少要 pass 3 個先入場

### Section 5: 模式偵測
**比喻：分辨「平靜」同「暴風」**

市場有兩種狀態：
- **RANGE**（橫行）— 價格喺一個範圍入面彈嚟彈去
- **TREND**（趨勢）— 價格一直向一個方向走

系統用 RSI、成交量、funding rate 嚟判斷而家係邊種：
- RSI < 32 或 > 68 → 偏向 TREND
- 成交量 < 50% 或 > 150% 平均 → 偏向 TREND
- 要連續 2 次同一判斷先切換（防止誤判）

### Section 6: Dashboard + 倉位
**比喻：控制面板嘅基本設定**

- `DASHBOARD_PORT = 5566` — 唯一定義點，改 port 只改呢度
- `MAX_POSITION_SIZE_USDT = 50` — 單筆最大 $50 USDT

### Section 7: Profile 設定
**比喻：遊戲難度選擇**

- `ACTIVE_PROFILE = "AGGRESSIVE"` — 而家用緊「進取」模式
- `AUTO_PROFILE_SWITCH = False` — 唔會自動切換（要手動或 dashboard 切）

### Section 8: 幣種 + 掃描引擎
**比喻：選戰場 + 戰場規則**

定義掃描邊啲幣：
- Aster: BTC, ETH, XRP, XAG（白銀）, XAU（黃金）
- Binance: BTC, ETH, SOL, POL
- HyperLiquid: BTC, ETH, SOL

掃描引擎參數：
- 單幣超時 30 秒
- 最多 8 個同時掃描
- Log 保留 500 行 / 10MB / 5 個備份

### Section 9: 新聞/情緒
**比喻：情報部**

系統每 5 分鐘抓 RSS 新聞，每 15 分鐘做一次情緒分析。

- 如果 bearish confidence > 70% → 自動攔截 LONG 信號
- 新聞保留 6 小時，分析只睇最近 1 小時
- 30 分鐘冇更新 → 標記為過期

### Section 10: HMM（隱馬爾可夫模型）
**比喻：AI 氣象站**

HMM 用歷史數據去偵測市場「而家係咩狀態」（升市 / 跌市 / 震盪）。

- 用最近 500 根 4H candle（約 83 日）訓練
- 每 24 根 candle（約 4 日）重新訓練
- 信心 < 60% → 判斷為 UNKNOWN（唔做決定）
- 至少 100 個 sample 先開始用（cold start 保護）

### Section 11: CRASH 策略
**比喻：戴安全帽**

當 HMM 偵測到「崩盤」狀態，自動切換到保守模式：

- 風險降到 1%（平時 2-3%）
- 槓桿降到 5x（平時 8-10x）
- SL 放寬到 2×ATR（畀多空間）
- RSI > 60 先考慮入場（抓反彈）

### Section 12: Regime Engine
**比喻：換腦**

4 種判斷市場狀態嘅「引擎」組合：

| Preset | 引擎 | 變點偵測 | 特點 |
|--------|------|----------|------|
| classic | 投票+HMM | ❌ | 最簡單 |
| classic_cp | 投票+HMM | ✅ | 加信心區間 |
| bocpd | BOCPD | ❌ | 更先進 |
| full | BOCPD | ✅ | 最完整 |

而家用緊 `classic`（最簡單嗰個）。

### Section 13: BOCPD（變點偵測）
**比喻：偵測「風變咗」**

BOCPD = Bayesian Online Changepoint Detection。
用數學方法偵測市場狀態有冇突然轉變。

- 預期每 ~50 根 4H candle（約 8 日）出現一次變點
- 信心 < 30% → 認為冇變
- 30 個 sample 就可以開始用（比 HMM 快）

### Section 14: Conformal Prediction
**比喻：量度「幾有信心」**

用統計方法計算 ATR 嘅信心區間。

- 90% coverage（10 次有 9 次準）
- 至少 20 個分數先開始用
- Cold start 時膨脹 1.5 倍（保守啲）

---

## Profile 系統 — 繼承 + 覆蓋

**一句話：所有 profile 從 _base.py 開始，只改唔同嘅值。**

```
_base.py（預設 = balanced 嘅值）
    ↓ 全部繼承
conservative.py  → 覆蓋 7 個值
balanced.py      → 覆蓋 0 個值（base 就係佢）
aggressive.py    → 覆蓋 12 個值
```

### 三個 Profile 對比

| 參數 | 保守 | 平衡 | 進取 |
|------|------|------|------|
| 每筆風險 | 1% | 2% | 3% |
| 信號門檻 | 3%（嚴格） | 2.5% | 2%（敏感） |
| SL 寬度 | 1.5×ATR | 1.2×ATR | 1.0×ATR（緊） |
| TP 目標 | 2.0×ATR | 2.0×ATR | 3.0×ATR（大） |
| Range RR | 2.3 | 2.3 | 2.0（放鬆） |
| Trend RR | N/A | 3.0 | 2.5 |
| 槓桿（Range） | 5x | 8x | 10x |
| 槓桿（Trend） | 3x | 7x | 8x |
| 最多倉位 | 1 | 2 | 3 |
| 做 Trend？ | ❌ | ✅ | ✅ |
| 高信心加碼 | 1.0x（唔加） | 1.25x | 1.5x |
| 風險上限 | 1.5% | 3% | 4% |
| Volume 門檻 | 0.8 | 0.8 | 0.6（更鬆） |
| Breakeven ATR | 0.8（更早） | 1.0 | 1.0 |
| 再入場冷卻 | 5 cycles | 3 cycles | 2 cycles |

### 性格對比

- **保守**：「唔做 trend，單倉，慢慢嚟。錯過機會好過輸錢。」
- **平衡**：「Range 為主，但 trend 明顯就跟。中間路線。」
- **進取**：「信號敏感、高槓桿、多倉。追趨勢追到盡。」

---

## user_params.py — 私人 Override

`params.py` 最尾段有段 magic code：如果 `user_params.py` 存在，讀入去覆蓋同名變數。

用法：
```bash
cp config/user_params.py.example config/user_params.py
# 然後改你要 override 嘅值
```

特點：
- **唔上 git** — 永遠唔會同人撞
- **只寫你想改嘅** — 唔使複製成個 params.py
- **覆蓋一切** — 包括 params.py 裏面嘅任何變數

注意：`ACTIVE_PROFILE` 唔好放 user_params！
因為 dashboard/tg_bot 會寫入 params.py 切 profile，如果 user_params 覆蓋返，UI 切換就永遠無效。

---

## loader.py — 安全網

Profile 載入器有四個聰明設計：

1. **永唔 crash** — load 失敗就用 DEFAULT（balanced），系統繼續運作
2. **過濾 unknown key** — 打錯字唔會靜靜雞出事，會 log warning
3. **Type check** — base 係 float 你寫咗 string，會警告你
4. **每次重讀文件** — 唔受 Python import cache 影響，dashboard 可以熱改

---

## 數據流（誰讀咩）

```
params.py
  ├── dashboard.py     → 顯示 UI（get_params() 動態讀全部）
  ├── settings.py      → ACTIVE_PROFILE + symbols + mode detection
  ├── indicator_calc   → BB / RSI / MACD / STOCH / SR 參數
  ├── async_scanner    → 幣種 + 掃描設定
  └── weekly_review    → trigger_pct

profiles/
  └── loader.py        → 被 settings.py 調用，返回 merged profile dict
                          → 被 dashboard.py 調用（get_all_profiles()）

user_params.py（如果存在）
  └── 喺 params.py import 時自動覆蓋 → 對所有讀者透明
```

---

## 關鍵設計決定

| 決定 | 點解 |
|------|------|
| Python 文件而唔係 JSON/YAML | 可以寫 comment、用表達式（例如引用其他變數） |
| _base.py 做預設 | 新 profile 唔使重寫 30 個值，只改差異 |
| user_params 分離 | git pull 永遠唔衝突 |
| loader 每次重讀 | dashboard 改完即生效，唔使重啟 |
| validate 唔 crash | 錯咗就用 default，寧可跑舊值都唔好死機 |
| ACTIVE_PROFILE 喺 params.py | dashboard API 可以直接 regex 寫入切換 |

---

# Part 3: params.py 深入解讀

## 點解有 14 段？

每一段對應一個「讀者」— 即係邊段 code 會 import 呢段參數。
改錯段，影響嘅可能唔係你以為嗰個部份。

```
你改 Section 3 嘅 RSI 參數
    ↓
唔係 Scanner 變，係 indicator_calc 變
    ↓
即係所有用 indicator_calc 嘅嘢都受影響
    ↓
包括 Scanner + Trader + Dashboard 圖表
```

所以要知道「我改呢個，最終影響邊度」。

---

## Section 1：掃描 — 點解輪轉？

```
EXCHANGE_ROTATION = ["aster", "binance", "hyperliquid", ...]  # 9 間
SCAN_INTERVAL_SEC = 20
```

**唔輪轉會點？**
9 間交易所 × 每間 5 隻幣 = 45 個 API call。如果同時 hit：
- 觸發 rate limit → 被 ban
- 網絡擠塞 → timeout → 錯過信號

**輪轉嘅數學：**
9 間 × 20 秒 = 每間交易所每 **180 秒**先被 hit 一次。
呢個 180 秒就係 LightScan plist 嘅 `StartInterval`。唔係巧合 — 係設計出嚟嘅。

**Scheduled Cycle：**
```
SCHEDULED_CYCLE_HOURS = [0, 3, 6, 9, 12, 15, 18, 21]
```
每 3 小時做一次完整掃描 cycle。呢個同 Trader 嘅 30 分鐘節奏唔同 —
Scanner 係「輕掃」，Cycle 係「全面體檢」。

---

## Section 2+3：指標 — 數字背後嘅思考

### BB Touch Tolerance — 點解 BTC 同 XRP 唔同？

```
BB_TOUCH_TOL_DEFAULT = 0.005   # BTC, ETH
BB_TOUCH_TOL_XRP = 0.008       # XRP
```

BTC 日波幅約 2-3%。0.5% 容忍度 = 波幅嘅 ~20%，合理。
XRP 日波幅可以去到 5-8%。如果用 0.5%，好多「接近但未碰到」嘅 signal 會被跳過。
0.8% = XRP 波幅嘅 ~12%，比例上其實更嚴格。

### 三個時間框 — 點解參數唔同？

```
"15m": { "rsi_long": 30, "adx_range_max": 20 }
"1h":  { "rsi_long": 40, "adx_range_max": 25 }
"4h":  { "rsi_long": 35, "adx_range_max": 25 }
```

**15m RSI 門檻 = 30（最嚴格）**
15m 多噪音。RSI 去到 30 先算超賣 — 要好極端先信佢。

**1h RSI 門檻 = 40（最寬鬆）**
1h 係主力判斷框。40 = 「稍微偏低就考慮」，因為 1h 信號已經過濾咗好多噪音。

**4h RSI 門檻 = 35（中間）**
4h 用嚟做大方向確認，唔使太嚴格，但都唔可以太寬鬆。

### ADX 嘅故事 — comment 記錄「點解改」

```
"1h": { "adx_range_max": 25 }   # 2026-03-13: was 20
```
原本係 20，但 diagnostic 發現 380 根 candle 入面有 246 根被 block（65%）。
即係 ADX > 20 嘅時間太多，大部份時間都唔俾入場 → 改到 25 放鬆啲。

**呢個就係 params.py 最有價值嘅嘢 — comment 記錄改動原因。**

---

## Section 4：Trend — 三把鎖

```
TREND_RSI_LONG_LOW = 40
TREND_RSI_LONG_HIGH = 55
PULLBACK_TOLERANCE = 0.025
TREND_MIN_KEYS = 3
```

想做 LONG，要過三關：

**第一把鎖：RSI 範圍（40-55）**
點解唔係越低越好？因為 Trend 策略係「順勢」— RSI 太低代表市場跌緊，唔啱做 LONG。
40-55 = 「剛從低位回升，但未到過熱」— 趨勢起步嘅甜蜜點。

**第二把鎖：Pullback（2.5%）**
直接追 = 可能買喺高位。等佢回調 2.5% 先入 = 較好嘅價位。
呢個值係 optimizer 跑出嚟嘅（comment 寫住 was 1.5%）。

**第三把鎖：3/4 確認（4 Key Voting）**

實際 4 個 Key（由 `trend_strategy.py` 定義）：

| Key | 時間框 | LONG 條件 | SHORT 條件 |
|-----|--------|-----------|------------|
| **MA_aligned** | 4H | price > MA50_4H AND price > MA200_4H | price < MA50_4H AND price < MA200_4H |
| **MACD_bullish** | 4H | histogram > 0 且擴大中 | histogram < 0 且擴大中 |
| **RSI_pullback** | 1H | RSI 喺 40-55（回調區） | RSI 喺 45-60（反彈區） |
| **Price_at_MA** | 1H | 價格距 MA50_1H < 1.5% | 同 LONG |

正常需要 4/4 全 pass。Day-of-week bias（Thu/Fri UTC+8）可以放寬到 3/4。

---

## Section 5：模式偵測 — 防鋸齒

```
MODE_CONFIRMATION_REQUIRED = 2
```

= 2 代表要連續兩次同一判斷先切換模式。

**點解？** 防止 whipsaw（鋸齒）。
市場瞬間波動好常見，如果一次就切，會不停 RANGE ↔ TREND 來回。
連續兩次同一判斷 = 更可信。

---

## Section 7：AUTO_PROFILE_SWITCH 點解關住？

```
ACTIVE_PROFILE = "AGGRESSIVE"
AUTO_PROFILE_SWITCH = False
```

如果開咗：HMM 偵測到 CRASH → 自動切去 CONSERVATIVE → 回復正常 → 自動切返。

聽落聰明，但三個問題：
1. HMM 判斷錯 → 自動切錯 profile → 用錯策略
2. 連續切換 → whipsaw
3. 你瞓緊覺唔知佢切咗 → 醒嚟發現倉位用錯 profile 開

所以暫時 `False` = 安全啲。

---

## Section 10-14：四層偵測

```
第 1 層：投票機制（RSI + Volume + Funding）→ RANGE/TREND
第 2 層：HMM（隱馬爾可夫）→ 升/跌/震盪
第 3 層：BOCPD（變點偵測）→ 幾時「風變咗」
第 4 層：Conformal Prediction → 「幾有信心」
```

而家 `ACTIVE_REGIME_PRESET = "classic"` = **只用第 1+2 層**。

```
REGIME_PRESETS = {
    "classic":    votes_hmm + CP off      ← 而家用緊
    "classic_cp": votes_hmm + CP on
    "bocpd":      bocpd_cp  + CP off
    "full":       bocpd_cp  + CP on       ← 最完整
}
```

**點解唔用 full？**
BOCPD + CP 加上去，系統複雜度跳一級。出事唔知係邊層問題。
先用 classic 跑穩定，之後一層一層加上去驗證。

---

## 最後嗰段 Magic Code — user_params 覆蓋機制

```
params.py 尾段：
    if user_params.py 存在:
        import 佢
        遍歷所有唔係底線開頭嘅變數
        逐個覆蓋入 globals()
```

效果：其他 script `from config.params import SCAN_INTERVAL_SEC` 嘅時候，
如果 user_params 改咗呢個值，讀到嘅已經係覆蓋後嘅版本。完全透明。

### ACTIVE_PROFILE 覆蓋陷阱

```
Dashboard UI 按「切去 CONSERVATIVE」
    ↓
API 用 regex 改 params.py → ACTIVE_PROFILE = "CONSERVATIVE" ✅
    ↓
但 params.py 尾段 magic code 執行：
    user_params.py 裏面寫住 ACTIVE_PROFILE = "AGGRESSIVE"
    ↓
覆蓋返 → 最終 ACTIVE_PROFILE = "AGGRESSIVE" ❌
    ↓
你點撳都切唔到 💀
```

所以 ACTIVE_PROFILE 永遠唔好放 user_params。
params.py 係「可被系統寫入」嘅；user_params 係「只有你手動改」嘅。

---

# Part 4：Trend 4-Key 嚴格度分析 + 加權評分方案

> 背景：而家 Trend 入場用 binary voting（每個 key = pass/fail），
> 需要 3/4 或 4/4。問題：太嚴格？適唔適合「4H 內捉大戶出手」嘅打法？

---

## 而家嘅問題

### Binary Voting 嘅缺陷

| 情景 | Binary 結果 | 實際情況 |
|------|-------------|----------|
| MACD histogram +0.001（勉強正） | ✅ pass | 信號極弱，唔應該同 +5.0 一樣重量 |
| RSI = 55.1（超出 40-55 範圍 0.1） | ❌ fail | 差 0.1 就唔入？太 rigid |
| Price 距 MA50 = 1.6%（超 1.5% 門檻） | ❌ fail | 差 0.1% 就放棄？ |
| MA50 > MA200 但 price 喺中間 | ✅ pass | 方向啱但位置唔理想 |

**結論：Binary 對邊界情況太殘忍，對弱信號太寬容。**

### 4H 窗口嘅時間壓力

你嘅打法：捉大戶喺 4H 內嘅動作。
- 4H = 240 分鐘
- 而家 cycle 每 30 分鐘跑一次 = 最多 8 次機會
- Binary 4/4 全 pass 嘅概率極低（除非市場極度配合）
- 結果：好多「八成啱」嘅機會被跳過

---

## 加權評分方案

### 你揀嘅權重

```
MACD           45%    ← 動量為王：大戶出手一定有 momentum 變化
Price_pullback 35%    ← 入場質素：回調到 MA 附近 = 好價位 + 風險可控
MA_aligned     15%    ← 方向確認：有用但太慢，4H MA 滯後
RSI_zone        5%    ← 輔助：太窄嘅窗口，參考就好
```

### 點解呢個分配合理？

**MACD 45%** — 最高權重
- MACD histogram 係即時反映買賣力量嘅指標
- 大戶入場 → volume spike → MACD histogram 擴大
- 4H timeframe 嘅 MACD 已經過濾咗噪音
- 配合你「捉大戶出手」嘅核心理念

**Price_pullback 35%** — 第二高
- 直接影響入場價位 = 直接影響 R:R
- 價格喺 MA 附近 = 有支撐/阻力做 SL 錨點
- 唔係追高買入，而係等回調
- 35% 確保就算其他信號強，入場價唔好都唔會硬入

**MA_aligned 15%** — 低權重
- 4H MA50 + MA200 方向判斷有價值
- 但 MA 天生滯後 — 等到 MA 確認時，move 可能已經走咗一半
- 15% = 有方向 bonus，但唔會因為 MA 未 align 就放棄一個 MACD 爆發嘅機會

**RSI_zone 5%** — 最低
- RSI 40-55 呢個窗口太窄
- 好多時 RSI 56 同 55 嘅分別 = 零
- 5% = 基本上只係 tiebreaker，唔影響大局

### 連續評分 vs Binary

每個 Key 唔再係 0 或 1，而係 0.0 到 1.0：

```
MACD_score:
  histogram > 0 且擴大中 → 0.8-1.0（按擴大幅度）
  histogram > 0 但收縮中 → 0.4-0.6
  histogram ≈ 0            → 0.2
  histogram < 0            → 0.0

Price_pullback_score:
  距 MA50 < 0.5%  → 1.0（完美位置）
  距 MA50 < 1.0%  → 0.8
  距 MA50 < 1.5%  → 0.6
  距 MA50 < 2.5%  → 0.3
  距 MA50 > 2.5%  → 0.0

MA_aligned_score:
  price > MA50 > MA200（完美排列）→ 1.0
  price > MA50, price > MA200      → 0.7
  price > MA200 only               → 0.3
  兩條 MA 都喺上面                   → 0.0

RSI_zone_score:
  RSI 喺 45-50（sweet spot）→ 1.0
  RSI 喺 40-55（標準區）    → 0.7
  RSI 喺 35-60（放寬區）    → 0.3
  RSI 超出                   → 0.0
```

### 最終分數計算

```
total_score = (MACD_score × 0.45)
            + (Price_pullback_score × 0.35)
            + (MA_aligned_score × 0.15)
            + (RSI_zone_score × 0.05)
```

### 入場門檻建議

```
score ≥ 0.70  →  STRONG 入場（base position × 1.2）
score ≥ 0.55  →  BIAS 入場（base position × 1.0）
score ≥ 0.40  →  WEAK 入場（base position × 0.7）— 可選
score < 0.40  →  唔入場
```

### 同而家對比

| 場景 | Binary（而家） | 加權（新） |
|------|----------------|------------|
| MACD 爆發 + Price 完美 + MA 未 align + RSI 偏高 | 2/4 → ❌ 唔入 | 0.45×0.9 + 0.35×1.0 + 0.15×0.0 + 0.05×0.3 = **0.77** → ✅ STRONG |
| 四個都勉強 pass | 4/4 → ✅ 入場 | 0.45×0.4 + 0.35×0.4 + 0.15×0.4 + 0.05×0.4 = **0.40** → ⚠️ 邊界 |
| MACD 弱 + 其他全強 | 3/4 → ✅ 入場 | 0.45×0.2 + 0.35×0.9 + 0.15×0.9 + 0.05×0.9 = **0.59** → ✅ BIAS |

**加權嘅優勢：MACD 強爆發唔會被 MA 滯後拖死；四個都弱就算全 pass 都唔會硬入。**

### Position Sizing 聯動

加權分數可以直接影響倉位大小：
```
position_mult = 0.7 + (total_score - 0.40) × 1.67
# score 0.40 → mult 0.7（最細倉）
# score 0.70 → mult 1.2（正常偏大）
# score 1.00 → mult 1.7（最大倉）— 受 profile cap 限制
```

---

## 實作注意（待確認後執行）

改動範圍：
1. `trend_strategy.py` — 核心邏輯從 binary → weighted
2. `config/params.py` — 新增 `TREND_WEIGHTS` dict + 門檻參數
3. `config/profiles/_base.py` — 門檻值入 profile 系統
4. 可能影響 logging / dashboard signal display

⚠️ 涉及交易邏輯 + >3 文件 → 必須 plan + 確認先實作。
