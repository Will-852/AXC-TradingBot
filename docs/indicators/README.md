# TradingView 編輯精選指標大全

> 資料來源：TradingView Editors' Picks + 熱門指標
> 更新日期：2025-03-11
> 語言：廣東話/中文

---

## 📁 目錄

1. [市場結構 (Market Structure)](#1-市場結構-market-structure)
2. [成交量分析 (Volume Analysis)](#2-成交量分析-volume-analysis)
3. [支撐阻力 (Support & Resistance)](#3-支撐阻力-support--resistance)
4. [趨勢指標 (Trend Indicators)](#4-趨勢指標-trend-indicators)
5. [週期/天文 (Cycles/Astro)](#5-週期天文-cyclesastro)
6. [機器學習 (Machine Learning)](#6-機器學習-machine-learning)
7. [ICT 概念 (ICT Concepts)](#7-ict-概念-ict-concepts)
8. [傳統指標 (Classic Indicators)](#8-傳統指標-classic-indicators)

---

## 📊 指標狀態一覽

| # | 指標名稱 | 作者 | 分類 | 狀態 |
|---|----------|------|------|------|
| 1 | Stop Loss Cascades (Breakouts) | Kioseff Trading | Market Structure | ✅ |
| 2 | Swing Profile | BigBeluga | Volume Analysis | ✅ |
| 3 | Gann o Maticus | djmad | Cycles/Astro | ✅ |
| 4 | Market Break Analytics | ChartPrime | Market Structure | ✅ |
| 5 | ICT Concepts | UAlgo | ICT Concepts | ✅ |
| 6 | Support & Resistance Pro | LuxAlgo | Support & Resistance | ✅ |
| 7 | Liquidity Thermal Map | BigBeluga | Volume Analysis | ✅ |
| 8 | HTF Divergence + LTF ChoCh | gabegab1 | Trend Indicators | ✅ |
| 9 | Vdubus Divergence Wave Pattern | vdubus | Market Structure | ✅ |
| 10 | Per Bak Self-Organized Criticality | HenriqueCentieiro | Market Structure | ✅ |
| 11 | Volatility Risk Premium | EdgeTools | Market Structure | ✅ |
| 12 | Volume Gaps & Imbalances | Zeiierman | Volume Analysis | ✅ |
| 13 | Match Finder | theUltimator5 | Market Structure | ✅ |
| 14 | Tick CVD | Kioseff Trading | Volume Analysis | ✅ |
| 15 | Delta Ladder | Kioseff Trading | Volume Analysis | ✅ |
| 16 | 10x Bull Vs. Bear VP Intraday Sessions | Kioseff Trading | Volume Analysis | ✅ |
| 17 | Volume Profile Pivot Anchored | dgtrd | Volume Analysis | ✅ |
| 18 | Adaptive Trend Classification | InvestorUnknown | Trend Indicators | ✅ |
| 19 | T-Virus Sentiment | hapharmonic | Trend Indicators | ✅ |

---

## 1. 市場結構 (Market Structure)

### Stop Loss Cascades (Breakouts) [Kioseff Trading]
- **連結**: https://tw.tradingview.com/script/CJX3k6l2-Stop-Loss-Cascades-Breakouts-Kioseff-Trading/
- **類型**: 指標 (Indicator)
- **語言**: 英文
- **功能**:
  - 模擬交易者止損單配置邏輯
  - 識別止損單可能集中的價格區域
  - 當大量止損單集中在某區域，價格突破可能產生高速動能
- **原理**:
  - 分析限價訂單簿 (Limit Order Book)
  - 識別流動性集中的價格區域 (Liquidity Shelves)
  - 追蹤潛在止損觸發點
- **模型**:
  - Absorption Extremes Model
  - Volatility-At-Entry Model
- **點評**: 基於學術研究，適合理解為何某啲突破快過其他

---

### Market Break Analytics [ChartPrime]
- **連結**: https://tw.tradingview.com/script/0vET13Ra-Market-Break-Analytics-ChartPrime/
- **類型**: 指標 (Indicator)
- **功能**:
  - 結構動量和成交量分佈工具
  - 分析確認的樞軸突破
  - 測量每次結構移動的參與度
  - 顯示買/賣參與度百分比
- **特點**:
  - 追蹤確認的 Pivot High/Low
  - 識別真正既擺動起點
  - 成交量參與度分析
  - 歷史控制 (可顯示/隱藏過去結構)

---

### ICT Concepts [UAlgo]
- **連結**: https://tw.tradingview.com/script/AeUpvjaN-ICT-Concepts-UAlgo/
- **類型**: 指標 (Indicator)
- **功能** (多合一 ICT 工具):
  - Order Blocks (訂單塊)
  - Market Structure Shifts (市場結構轉變)
  - SMT Divergence (市場間差異)
  - Fair Value Gaps (公平價值缺口)
  - Balanced Price Ranges (平衡價格區間)
  - Liquidity Sweeps (流動性掃描)
  - Fibonacci Levels (斐波那契水平)
  - Killzones (交易時段)
- **特點**:
  - 每個模組都係 State-aware
  - Order Blocks 延伸到消除為止
  - FVG 延伸到價格穿過為止
  - 可跨時間框架比較

---

### Vdubus Divergence Wave Pattern [vdubus]
- **連結**: https://tw.tradingview.com/script/fi2LLSGz-Vdubus-Divergence-Wave-Pattern-Generator-V1/
- **類型**: 指標 (Indicator)
- **功能**:
  - 結構同動能 confluence 系統
  - 結合幾何學（價格形態）同物理學（動能）
  - 3-Wave Momentum Filter 要求 3 點背離確認，而非簡單 2 點
- **核心理念**: 「幾何 + 物理」— 永遠唔應該單靠幾何學就交易

---

### Per Bak Self-Organized Criticality [HenriqueCentieiro]
- **連結**: https://tw.tradingview.com/script/bZo4yadb-Per-Bak-Self-Organized-Criticality/
- **類型**: 指標 (Indicator)
- **功能**:
  - 量度市場脆弱性 (fragility)
  - 評估系統對級聯故障同相變既易損程度
  - 識別市場「準備好山泥傾瀉」既狀態
- **四個 Stress Vectors**:
  - Tail Risk（尾部風險）
  - Volatility Regime（波動率體制）
  - Credit Stress（信貸壓力）
  - Positioning Extremes（倉位極端）

---

### Volatility Risk Premium [EdgeTools]
- **連結**: https://tw.tradingview.com/script/UJgVgUvT-Volatility-Risk-Premium/
- **類型**: 指標 (Indicator)
- **功能**:
  - 計算市場期望波動率 (Implied Volatility) 同實際波動率 (Realized Volatility) 既差距
  - 相當於股票市場既「保險費」
- **解讀**:
  - VRP 高於正常範圍：投資者願意付多啲買期權，市場不確定性增加
  - VRP 低於正常範圍：期權相對便宜，可能係低風險環境

---

### Match Finder [theUltimator5]
- **連結**: https://tw.tradingview.com/script/ddvP5qAZ-Match-Finder-theUltimator5/
- **類型**: 指標 (Indicator)
- **功能**:
  - 幫你既 current ticker 搵最近期最 compatible 既 match
  - 使用 Pearson correlation 計算最近似既價格形態
  - 將 matched segment overlay 等你可以視覺化比較
- **用途**:
  - Sector Analysis — 發現某隻股票跟邊個 sector 最相關
  - Leading Indicator — 搵 leading indicators
  - Correlation Trading — 做對沖或者 pairs trade

---

## 2. 成交量分析 (Volume Analysis)

### Swing Profile [BigBeluga]
- **連結**: https://tw.tradingview.com/script/gFlv7t7R-Swing-Profile-BigBeluga/
- **類型**: 指標 (Indicator)
- **功能**:
  - 動態基於擺動既成交量 Profile 工具
  - 為每個已完成既市場擺動建立完整成交量 Profile
  - 取代固定時間段，使用確認既 Swing High/Low 作為 Profile 邊界
- **概念**:
  - Swing-Anchored Profiling — 成交量只係確認既擺動高/低之間計算
  - Directional Legs — 每個上升/下降擺動腿都有獨立成交量 Profile
  - ATR-Adaptive Bins — Profile 大小自動使用 ATR 調整
  - Real-Time Rebuild — 擺動形成時即時重新計算

---

### Liquidity Thermal Map [BigBeluga]
- **連結**: https://tw.tradingview.com/script/G30eUYdH-Liquidity-Thermal-Map-BigBeluga/
- **類型**: 指標 (Indicator)
- **功能**:
  - 視覺化指定回溯期內既最高成交量累積既價格水平
  - 使用平滑顏色梯度顯示強/弱流動性
  - 識別高興趣價格區域
- **概念**:
  - Price-Level Volume Aggregation
  - Volume Binning
  - Thermal Gradient Mapping
  - Point of Control (PoC)

---

### 10x Bull Vs. Bear VP Intraday Sessions [Kioseff Trading]
- **連結**: https://tw.tradingview.com/script/3mKewfnN-10x-Bull-Vs-Bear-VP-Intraday-Sessions-Kioseff-Trading/
- **類型**: 指標 (Indicator)
- **功能**:
  - 配置多達 10 個 session ranges 去做 Bull Vs. Bear volume profiles
  - Volume Profile 錨定到固定範圍
  - Delta Ladder 錨定到範圍
  - 分開顯示 Bull vs Bear Profiles
  - 多達 2000 Profile Rows 每個 visible range
- **用途**: 分析特定交易時段既 Bull vs Bear 成交量分佈

---

### Tick CVD [Kioseff Trading]
- **連結**: https://tw.tradingview.com/script/moMbNm8e-Tick-CVD-Kioseff-Trading/
- **類型**: 指標 (Indicator)
- **功能**:
  - 使用 live tick data 計算 CVD 同 volume delta
  - 唔需要 tick chart
  - Tick-based Moving Averages (HMA, WMA, EMA, SMA)
  - Key Tick Levels 記錄同顯示
  - Efficiency Mode 等快速市場運行更快
- **優點**: 比傳統基於 close/open 既 volume 計算更精確

---

### Delta Ladder [Kioseff Trading]
- **連結**: https://tw.tradingview.com/script/EkBUz93v-Delta-Ladder-Kioseff-Trading/
- **類型**: 指標 (Indicator)
- **功能**:
  - 以多種形式呈現 Volume Delta 數據
  - Classic Mode / On Bar Mode / Pure Ladder Mode
  - PoC Highlighting
  - 價格 bars 可以分割多達 497 次
  - Total Volume Delta 顯示

---

### Volume Profile, Pivot Anchored [dgtrd]
- **連結**: https://tw.tradingview.com/script/utCRHZeP-Volume-Profile-Pivot-Anchored-by-DGT/
- **類型**: 指標 (Indicator)
- **功能**:
  - 根據 Pivot Levels 確定既時間段繪製 Volume Profile
  - 可以錨定到 Session, Week, Month
  - 可自定義範圍，interactive 調整
  - 結合 Support and Resistance、Supply and Demand Zones

---

### Volume Gaps & Imbalances [Zeiierman]
- **連結**: https://tw.tradingview.com/script/Q7YQQq7g-Volume-Gaps-Imbalances-Zeiierman/
- **類型**: 指標 (Indicator)
- **功能**:
  - 先進既市場結構同 Order Flow 視覺化工具
  - 識別真正既零成交量空隙（比標準 FVG 更精確）
  - 繪製每個價格水平既 Bull vs Bear 成交量
  - Sector-based Delta Grid 顯示聚合既買-賣壓力
- **ICT 概念**: 基於 ICT 既 Volume Gaps 同 Imbalances，呢啲區域經常係價格既「磁石」

---

## 3. 支撐阻力 (Support & Resistance)

### Support & Resistance Pro Toolkit [LuxAlgo]
- **連結**: https://tw.tradingview.com/script/n2ODj57p-Support-Resistance-Pro-Toolkit-LuxAlgo/
- **類型**: 指標 (Indicator)
- **功能**:
  - 專業結構分析引擎
  - 整合四種檢測算法：
    - Pivots
    - Donchian Alternating
    - CSID
    - ZigZag
  - 精確水平繪製或動態 ATR 區域
  - 25 棒未來投射
- **進階過濾**:
  - 成交量要求
  - Liquidity Sweeps
  - 重測次數
  - 存活時間

---

## 4. 趨勢指標 (Trend Indicators)

### HTF Divergence + LTF ChoCh Signal
- **連結**: https://tw.tradingview.com/script/DxenecWS-HTF-Divergence-LTF-ChoCh-Signal/
- **類型**: 指標 (Indicator)
- **功能**:
  - 高時間框架背離 + 低時間框架結構突破信號
  - 設置較高時間框架 (如 15m 圖表用 4h)
  - 等待 Dashboard 顯示 "Active" HTF 狀態
  - 當 LTF 結構在 HTF 偏置後突破，出現信號三角形

---

### Adaptive Trend Classification [InvestorUnknown]
- **連結**: https://tw.tradingview.com/script/L6NreqzB-Adaptive-Trend-Classification-Moving-Averages-InvestorUnknown/
- **類型**: 指標 (Indicator)
- **功能**:
  - 自適應趨勢分類指標
  - 使用多種類型 MA (EMA, HMA, WMA, DEMA, LSMA, KAMA)
  - Dynamic Weighting Based on Performance — 根據表現分配權重
  - Exponential Growth Adjustment — 增强最近數據既影響力
  - Calibration Mode — 微調設置優化 backtest
- **優點**: 根據市場條件自動調整，唔需要手動切换策略

---

### T-Virus Sentiment [hapharmonic]
- **連結**: https://tw.tradingview.com/script/jpbq3J0S-T-Virus-Sentiment-hapharmonic/
- **類型**: 指標 (Indicator)
- **功能**:
  - 可視化市場既 DNA（情緒）
  - 結合 7 個技術分析工具既情緒分數
  - DNA helix 視覺化
  - T-Virus mascot 顯示市場健康狀態
- **7 個技術指標**: RSI, EMA, MACD, ADX, Ichimoku Cloud, Bollinger Bands, OBV
- **分數範圍**:
  - < 25%：強烈 bearish
  - 50%：中性/側向
  - > 75%：強烈 bullish

---

## 5. 週期/天文 (Cycles/Astro)

### Gann o Maticus [djmad]
- **連結**: https://tw.tradingview.com/script/UVBy6VlF-MAD-Gann-o-Maticus/
- **類型**: 指標 (Indicator)
- **功能**:
  - 全自動 Gann 網格
  - 天文週期
  - 自動 Gann quadrant boxes
  - 幾何弧線投影
- **週期選項**:
  - Standard Timeframes: 15m, 1H, 4H, 6H, 8H, 12H, 1D, 1W, 2W, 3W, 4W, 1M, 3M, 6M
  - Astrocycles: Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto
- **天文事件**:
  - High Latitude
  - Low Latitude
  - High Longitude (Retrograde Station)
  - Low Longitude (Direct Station)
  - Heliocentric Conjunction
  - Heliocentric Opposition

---

## 6. 機器學習 (Machine Learning)

### Machine Learning: Lorentzian Classification [jdehorty]
- **功能**: 機器學習分類指標
- **獲獎**: 2023 年編輯精選獎

---

### Intrabar Analyzer [Kioseff Trading]
- **功能**: 樞軸分析，訂單簿內部分析

---

## 7. ICT 概念 (ICT Concepts)

### ICT Concepts [UAlgo]
- (見上面第 1 節)

---

## 8. 傳統指標 (Classic Indicators)

### CM Ultimate RSI Multi Time Frame [ChrisMoody]
- **功能**: RSI 多時間框架版本

### Death Cross - 200 MA / 50 Cross Checker [MexPayne]
- **功能**: 死亡交叉 / 黃金交叉檢查器

### WaveTrend Oscillator (WT) [LazyBear]
- **功能**: 波形趨勢指標

### Pi Cycle Bottom Indicator [Doncic]
- **功能**: Pi 週期底部指標

### RCI3lines [gero]
- **功能**: 等級相關指數 3 線

### Stochastic RSI
- **功能**: 隨機 RSI

### TDI - Traders Dynamic Index
- **功能**: 交易者動態指數

---

## 📊 指標評分排名 (2023-2024)

### 2023 編輯精選獲獎者
1. **Intrabar Analyzer** - Kioseff Trading
2. **Machine Learning: Lorentzian Classification** - jdehorty
3. **Harmonic Patterns Based Trend Follower** - Trendosco

### 2024 TOP 10 熱門指標
1. CM Ultimate RSI Multi Time Frame
2. Death Cross - 200 MA / 50 Cross Checker
3. Gaps
4. WaveTrend Oscillator (WT)
5. MACD
6. Pi Cycle Bottom Indicator
7. RCI3lines
8. Stochastic RSI
9. TDI - Traders Dynamic Index

---

## 🔗 實用連結

- [TradingView 編輯精選首頁](https://tw.tradingview.com/scripts/editors-picks/)
- [熱門指標](https://tw.tradingview.com/scripts/)
- [Pine Script 官方文檔](https://www.tradingview.com/pine-script-docs/vn/)

---

## 📝 Notes

- 大部分指標需要 TradingView Pro+ 訂閱先可以使用全部功能
- 某些指標需要訂閱作者既付費版本先可以獲得完整功能
- 建議係免費版本測試後先決定係咪需要升級
- Backtest 結果並不代表未來表現

---

*Generated by Claude Code - 2025-03-11*
