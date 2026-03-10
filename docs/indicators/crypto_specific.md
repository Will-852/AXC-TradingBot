# Crypto 專用指標深度研究
> 更新：2026-03-10
> 涵蓋：Funding Rate、OI、Liquidation、CVD、On-chain、Fear & Greed

### talk16 — 呢個文件係咩
普通指標（RSI、MACD）咩市場都用得。但 crypto 有啲獨有嘅數據係股票冇嘅：永續合約嘅 funding rate（多頭定空頭擠迫）、未平倉合約量 OI（幾多人仲揸住注）、清算瀑布風險、鏈上數據（大戶錢包搬緊錢）、同市場恐懼指數。呢啲係 crypto 獨有嘅「內部情報」— 唔使猜，直接睇到市場參與者嘅倉位同行為。呢個文件逐個講點睇、咩水平算危險、同 AXC 而家有冇用到。

---

## 1. Funding Rate（永續合約資金費率）

### 核心概念
- 永續合約冇到期日，靠 Funding Rate 錨定現貨價格
- 每 8 小時結算一次（00:00, 08:00, 16:00 UTC）
- 正 = 多頭付費俾空頭（多頭過度擁擠）
- 負 = 空頭付費俾多頭（空頭過度擁擠）

### 閾值參考
| Funding Rate | 解讀 | 年化成本 |
|---|---|---|
| 0.005%-0.03% | 正常偏多 | ~10-33% |
| > 0.05% | 極度多頭擁擠 | ~54.75% |
| > 0.1% | 過熱（隨時爆倉） | ~109.5% |
| -0.005% to 0% | 正常偏空 | - |
| < -0.01% | 空頭擁擠 | - |
| < -0.03% | 極度空頭擁擠（squeeze 風險） | - |

### 逆向交易訊號
- **極正 FR (>0.05%)** → 準備做空 / 減多倉 — 多頭過度槓桿
- **極負 FR (<-0.03%)** → 準備做多 — short squeeze 可能
- 歷史數據：極度恐懼時買入 BTC，30日後正回報概率約 80%

### AXC 現有值
- `MODE_FUNDING_THRESHOLD = 0.0007`（±0.07%）— 用嚟偵測 mode
- **建議**：加入 Funding Rate 作為環境指標，唔係入場指標

### 數據來源
- Binance API: `GET /fapi/v1/fundingRate`
- Coinglass API
- 可直接讀取，唔使計算

### 年化計算
```
Annual Rate = Funding Rate × 3 × 365
例：0.01% × 3 × 365 = 10.95%
```

---

## 2. Open Interest (OI) — 未平倉合約

### 核心概念
- OI = 所有未平倉嘅合約總數
- OI 升 = 新錢入場（開新倉）
- OI 跌 = 舊錢離場（平倉）

### 四種組合訊號
| 價格 | OI | 解讀 |
|---|---|---|
| 升 | 升 | 強勢上升（新多頭入場）✅ |
| 升 | 跌 | 空頭平倉推升（短期，唔持久）⚠️ |
| 跌 | 升 | 強勢下跌（新空頭入場）❌ |
| 跌 | 跌 | 多頭平倉（投降式下跌，可能見底）🔍 |

### OI Divergence
- 價格新高但 OI 冇新高 = 動力不足，可能反轉
- 價格新低但 OI 冇新低 = 賣壓耗盡，可能見底

### Liquidation Cascade Risk 估算
```
Risk Score = (OI × Avg Leverage) / Market Cap
```
- OI 越高 + 槓桿越高 + 市值越低 = 越危險
- 一個方向嘅大型爆倉會觸發連鎖反應

### 數據來源
- Binance: `GET /fapi/v1/openInterest`
- Coinglass: Liquidation Heatmap（估算爆倉價位）

---

## 3. CVD (Cumulative Volume Delta) — 累積成交量差

### 核心概念
- 每根 K 線嘅買入量 - 賣出量，累積計算
- 衡量：真正嘅買賣壓力（唔係淨係睇價格）

### 計算
```
Volume Delta = Buy Volume - Sell Volume（每根 K 線）
CVD = Cumulative Sum(Volume Delta)
```
- 交易所通常用 taker buy/sell 嚟分

### 訊號
| CVD | 價格 | 解讀 |
|---|---|---|
| 升 | 升 | 健康上升（買壓推動）✅ |
| 跌 | 升 | 隱性賣壓（price pump 唔真實）⚠️ |
| 升 | 跌 | 隱性買壓（smart money 吸貨）🔍 |
| 跌 | 跌 | 確認下跌（賣壓推動）❌ |

### CVD Divergence
- **最實用嘅短線 systematic signal 之一**
- 價格新高 + CVD 冇新高 = bearish divergence
- 價格新低 + CVD 冇新低 = bullish divergence

### 數據來源
- 需要 tick-level 或 trade-level 數據
- Binance WebSocket: aggTrades stream
- 計算量較大，建議 pre-compute per candle

---

## 4. Long/Short Ratio — 多空比

### 核心概念
- 全市場多頭持倉 / 空頭持倉嘅比例
- 反映散戶情緒（大戶通常另一邊）

### 解讀
| Ratio | 含義 |
|---|---|
| > 2.0 | 散戶極度看多 → 逆向做空信號 |
| 1.0-2.0 | 正常偏多 |
| 0.5-1.0 | 正常偏空 |
| < 0.5 | 散戶極度看空 → 逆向做多信號 |

### 數據來源
- Binance: `GET /futures/data/globalLongShortAccountRatio`
- 分為 Top Trader 同 All Trader 兩種

---

## 5. Exchange Inflow/Outflow — 交易所資金流

### 核心概念
- Inflow = 幣轉入交易所（準備賣）
- Outflow = 幣轉出交易所（準備長期持有）
- Netflow = Inflow - Outflow

### 訊號
| 指標 | 解讀 |
|---|---|
| 大量 Inflow | 準備賣出，bearish |
| 大量 Outflow | 提幣長持，bullish |
| Whale Inflow（單筆 > 100 BTC） | 大戶可能要賣 |

### Exchange Whale Ratio
- = Top 10 Inflow / Total Inflow
- 高值 = 大戶主導資金流動

### 數據來源
- CryptoQuant API（付費）
- Glassnode API（付費）
- Whale Alert（免費通知）
- **限制**：免費數據延遲較大，唔適合短線

---

## 6. MVRV (Market Value to Realized Value)

### 核心概念
- Market Value = 現價 × 流通量（market cap）
- Realized Value = 每個幣最後一次移動時嘅價格總和
- MVRV = Market Value / Realized Value

### 解讀
| MVRV | 含義 |
|---|---|
| > 3.5 | 市場過熱，大部分持幣者盈利，賣壓大 |
| 2.0-3.5 | 偏貴 |
| 1.0-2.0 | 合理 |
| < 1.0 | 市場低估（持幣者平均蝕緊），歷史性買入機會 |

### 用途
- **Cycle-level 指標**，唔係短線用
- 用嚟判斷大週期位置（我哋喺 bull 定 bear？）
- MVRV < 1 歷史上每次都係好嘅長線買入點

### 數據來源
- Glassnode、CryptoQuant（BTC/ETH 為主）
- 只有主流幣有可靠數據

---

## 7. NVT Ratio (Network Value to Transactions)

### 核心概念
- NVT = Market Cap / Daily Transaction Volume（on-chain）
- 類似股票嘅 P/E ratio

### 解讀
| NVT | 含義 |
|---|---|
| > 95 | 高估（價格跑太前）|
| 35-95 | 正常範圍 |
| < 35 | 低估（使用量支撐價格）|

### 限制
- 受 exchange internal transfers 影響
- 唔適合所有幣種（需要 on-chain 數據）
- Cycle 指標，唔係短線

---

## 8. Fear & Greed Index

### 核心概念
- 0-100 嘅綜合情緒指數
- 組成：波動率(25%)、市場動量/成交量(25%)、社交媒體(15%)、調查(15%)、BTC 主導率(10%)、Google Trends(10%)

### 逆向訊號
| 值 | 區間 | 逆向操作 |
|---|---|---|
| 0-25 | Extreme Fear | 買入（歷史 80% 時間 30 日後正回報）|
| 25-45 | Fear | 偏多 |
| 45-55 | Neutral | 觀望 |
| 55-75 | Greed | 偏空 |
| 75-100 | Extreme Greed | 減倉 / 做空 |

### 可靠度
- 作為逆向指標用 = 中等可靠
- < 15 嘅極端值歷史上非常有效
- **唔好作為唯一訊號** — 配合其他指標用

### 數據來源
- alternative.me/crypto/fear-and-greed-index/（免費 API）

---

## 9. Order Book Imbalance (OBI)

### 核心概念
- Bid Volume vs Ask Volume 嘅比例
- 衡量即時嘅買賣壓力

### 計算
```
OBI = (Bid Volume - Ask Volume) / (Bid Volume + Ask Volume)
```
- OBI > 0 = 買壓較大
- OBI < 0 = 賣壓較大

### 限制
- ⚠️ **只適合 HFT（高頻交易）** — 毫秒級變化
- Spoofing（假單）會嚴重干擾
- AXC 掃描頻率（3分鐘）太慢，唔適合用 OBI

---

## 10. 綜合訊號框架（建議）

### 短線可程式化（適合 AXC）
| 優先級 | 指標 | 用途 | 數據取得難度 |
|---|---|---|---|
| 1 | Funding Rate | 逆向情緒 + mode detection | 低（API 直讀）|
| 2 | CVD Divergence | 買賣壓力確認 | 中（需 trade data）|
| 3 | OI Divergence | 槓桿脆弱度 | 低（API 直讀）|
| 4 | Long/Short Ratio | 散戶情緒 | 低（API 直讀）|

### 中長線參考
| 指標 | 用途 | 更新頻率 |
|---|---|---|
| MVRV | 大週期定位 | 日線 |
| Fear & Greed | 極端情緒逆向 | 日線 |
| Exchange Flow | 大戶行為 | 日線 |
| NVT | 估值參考 | 日線 |
