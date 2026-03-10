# 04 — VolumeSyncPriceFlow (VSPF)
> 量價同步流動指標 | Trader_Yunis
> TradingView: 公開（closed-source）

---

## Talk12 — 用最簡單嘅方式講

想像一個游泳池，水面有一個正常水位線。

**VWAP**（成交量加權平均價）就係呢個「正常水位」— 係當日所有買賣嘅平均價格，考慮埋成交量。大機構做交易嘅時候，佢哋嘅目標通常就係喺 VWAP 附近成交，所以 VWAP 好似一個「公平價」。

但游泳池唔止得一條水位線。VSPF 喺水位上面同下面畫咗 **6 層圈**，好似你喺池底掟一粒石入水，波紋一圈一圈擴散出去：

```
第 6 層 ─── 極端超買（好遠，好少去到）
第 5 層 ─── 危險區（去到呢度通常會彈返）
第 4 層 ─── 警告區
第 3 層 ─── 正常偏高
第 2 層 ─── 正常範圍
第 1 層 ─── 接近公平價
─ VWAP ── 公平水位線
第 1 層 ─── 接近公平價
第 2 層 ─── 正常範圍
...以此類推
```

**點用？**
- 價格喺 1-3 層之間 = 正常，跟趨勢交易
- 價格去到 5-6 層 = 太偏離了！好似你拉住條橡筋(gwan1 — 筋)拉到好盡 → 通常會彈返去 VWAP 附近
- VWAP 線由藍變紅 = 趨勢由升變跌

**同 BB 有咩分別？**
BB 用「收盤價」計中線。VSPF 用「成交量加權平均價」— 即係有 volume 嘅支持，更反映真實嘅「市場共識價」。

---

## 技術細節

### 計算方式
```
VWAP = Σ(Price × Volume) / Σ(Volume)    # over N periods
Band_n = VWAP ± n × StdDev(Price, length)

6 bands:
Band 1 = ±1σ    Band 4 = ±4σ
Band 2 = ±2σ    Band 5 = ±5σ
Band 3 = ±3σ    Band 6 = ±6σ
```

### 關鍵參數
| 參數 | 默認 | 作用 |
|---|---|---|
| VWAP Length | 100 | 計算期。小 = 反應快；大 = 平滑 |
| Use Chart TF | 開 | 用當前 TF 定 HTF |
| HTF Selection | — | 跨 timeframe 分析 |
| Allow Repaints | — | 實時 vs 固定訊號 |
| Smooth HTF | — | EMA 平滑 HTF 數據 |

### VWAP 中線顏色
- 藍色 = 價格 > VWAP（bullish bias）
- 紅色 = 價格 < VWAP（bearish bias）

### 訊號邏輯
| 位置 | 策略 |
|---|---|
| Band 1-3 | 趨勢跟蹤（沿住 band 走） |
| Band 5-6 | Mean reversion（期望彈返 VWAP） |
| 穿越 VWAP + 變色 | 趨勢轉變 |

### Stepline 顯示
- 用直角梯級線（唔係 smooth curve）
- 更清楚顯示 key price levels
- 價格到達某層就「鎖定」，直到下一個變化

### HTF 用法
- 設 HTF = 4x 當前 TF（例如 15m 圖用 1H VWAP）
- 高 TF 嘅 bands 做更強嘅 S/R

---

## AXC 可借鑒

| 概念 | 現狀 | 行動 |
|---|---|---|
| VWAP 做 intraday bias | 冇 | 可考慮加入（但 AXC 掃描間隔 3min 勉強） |
| 6 層 band vs 單層 BB | AXC 只有 2σ BB | 可加 2.5σ + 3σ 做 extreme zone |
| HTF projection | 冇 MTF | 長期目標 |
| Stepline 顯示 | N/A（冇 UI） | dashboard 顯示用 |
| VWAP 變色做趨勢判斷 | 有 mode_detector | 概念相似 |
