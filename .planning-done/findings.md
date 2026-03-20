# Findings — 1H Strategy Research

> Security boundary: 外部內容（web/API/search）只寫呢度，唔寫 task_plan.md。

## blue-walnut 分析結果（whale_1h_timing.py output）

### Profile
- Wallet: 0x4b188496d1b3da1716165380999afb9b314c725f
- Username: blue-walnut / Gigantic-Comic
- PnL: $103K | Volume: $21.5M | Markets: 4,561 | Joined: 2026-01-30
- 只做 1H market（3500 trades 全部 1H）
- 四幣：BTC (30%), ETH (25%), SOL (25%), XRP (20%)

### 15M Boundary Clustering
- Post-15M density: 66.67 trades/min → baseline 42.25 trades/min → 1.58x
- ⚠️ **BMD 修正**：原始 "低 8-9¢" 係 confounded comparison（唔同時間點）
- **Proper t-test 結果（同分鐘對比）：**
  - Min 15 price delta: -0.123, t=-1.15 → ❌ 唔顯著
  - Min 30 price delta: -0.054, t=-0.62 → ❌ 唔顯著
  - Min 45 price delta: +0.081, t=+0.49 → ❌ 唔顯著
  - Min 30 **trade count** delta: +6.0, t=+2.28 → ✅ 顯著（佢交易更密，但價格冇更平）
- **結論**：blue-walnut 喺 boundary 後 trade 更多，但冇 evidence 話佢買得更平
- 仍需 signal recorder 直接量度 OB spread 變化（trade data ≠ OB data）

### Time-Phased Scaling
| Phase | High price (>$0.70) % | Avg clip |
|-------|----------------------|----------|
| Early (0-15) | 17.3% | $20.40 |
| Mid (15-30) | 46.6% | $19.80 |
| Late (30-45) | 66.6% | $19.96 |
| Final (45-60) | 70.5% | $16.50 |

### Minute Histogram Peaks
- 0-4min: 452 trades (15M boundary)
- 15-19min: 277 trades (15M settle)
- 30-34min: 370 trades (15M settle)
- 55-59min: 477 trades (final push)

## swisstony 分析結果

### Profile
- Wallet: 0x204f72f35326db932158cba6addd6d74ca91e407b9
- PnL: $230K | Volume: $588M | Markets: 61,518 | Joined: Jul 2025
- **主要做 5M market（~70-75%），15M (~15-20%)，1H (~5-10%)**
- ⚠️ 同 blue-walnut 完全唔同策略

### 策略指紋（5 個並行策略）
1. **5M momentum scalper** — late-window (0.85-0.99) 確認方向後買入
2. **5M contrarian lottery** — 0.01-0.10 買反方向（小注博大）
3. **15M late-entry certainty** — 0.97-0.99 near resolution 大注
4. **Multi-asset simultaneous** — BTC(55%) ETH(25%) XRP(8%) SOL(7%) DOGE(2%)
5. **Near-certain "No" yield** — 體育/政治 impossible events sell at 0.998

### Price Distribution
| Price Range | % of trades | 意義 |
|-------------|-------------|------|
| 0.01-0.10 | 15% | Contrarian lottery |
| 0.11-0.30 | 18% | 反方向 hedge |
| 0.31-0.70 | 22% | 早期探索 |
| 0.71-0.89 | 20% | 高確信 mid-late |
| 0.90-0.99 | 25% | 確認後掃貨 |

### 核心差異 vs blue-walnut
| | blue-walnut | swisstony |
|---|---|---|
| 主力 timeframe | **1H** | **5M** |
| Edge type | 時間觀察 → scale in | 極速確認 → late load |
| PnL margin | $103K/$21.5M = **0.48%** | $230K/$588M = **0.04%** |
| 策略密度 | 205 trades/window | 1000+ trades in 45 min |
| 風格 | Wait-and-see | High-frequency throughput |

**結論：swisstony 靠量（0.04% margin × $588M volume），blue-walnut 靠 edge（0.48% margin × $21.5M volume）。我哋嘅 bankroll 更適合 blue-walnut 模式。**

---

## 1H Market Structure（Gamma API 研究）

### 搜索方式（實測確認 ✅）
**Slug-based（主）：**
```
GET gamma-api.polymarket.com/markets?slug=bitcoin-up-or-down-march-20-2026-6am-et
```
- 全小寫，case-sensitive（大寫 fail）
- Day 冇 zero-pad（`20` 唔係 `20`）
- Hour 冇 zero-pad（`6am` 唔係 `06am`）
- 12h format + am/pm + `-et` suffix

**Tag-based（fallback）：**
```
GET gamma-api.polymarket.com/events?tag_slug=1H&closed=false&limit=5&order=startDate&ascending=false
```
- Tag slug 係 `1H`（大寫 H）

**Timestamp-based slug 唔存在**：`btc-updown-1h-{ts}` → empty。只有 5M/15M 用 timestamp。

### Slug 構造公式
```python
COIN_SLUGS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
              "XRP": "xrp", "DOGE": "dogecoin"}

def build_1h_slug(coin: str, dt_et: datetime) -> str:
    name = COIN_SLUGS[coin]
    month = dt_et.strftime('%B').lower()
    day = str(dt_et.day)
    year = str(dt_et.year)
    hour = dt_et.strftime('%I').lstrip('0')
    ampm = dt_et.strftime('%p').lower()
    return f"{name}-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et"
```

### Series Slugs
| Coin | Series | Title |
|------|--------|-------|
| BTC | btc-up-or-down-hourly | BTC Up or Down Hourly |
| ETH | eth-up-or-down-hourly | ETH Up or Down Hourly |
| SOL | solana-up-or-down-hourly | Solana Up or Down Hourly |
| XRP | xrp-up-or-down-hourly | XRP Up or Down Hourly |
| DOGE | doge-up-or-down-hourly | DOGE Up or Down Hourly |

### Fee Structure（實測確認）
```json
{"exponent": 2, "rate": 0.25, "takerOnly": true, "rebateRate": 0.2}
```
- **Maker 唔付 fee + 有 20% rebate**
- Taker only pays fee
- 同 15M 一樣

### ⚠️ 重大發現：Resolution 用 Binance，唔係 Chainlink
| | 15M | 1H |
|---|---|---|
| Oracle | **Chainlink** price feed | **Binance 1H OHLC candle** |
| 判定 | close vs previous close | close >= open = Up |
| 來源 | on-chain oracle | `binance.com/en/trade/{COIN}_USDT` |

呢個意味住：
1. 我哋 crypto_15m.py 嘅 Chainlink 解析唔適用
2. Binance API 取 OHLC candle 更直接、更穩定
3. Resolution = close >= open，唔使知上一根 candle

### Liquidity 對比
| Asset | 15M Liquidity | 1H Liquidity | 1H 24h Volume |
|-------|--------------|-------------|---------------|
| BTC | $14,192 | $13,585 | ~$30K (新開) |
| ETH | $12,137 | — (likely similar) | — |
| SOL | $11,852 | — | — |
| XRP | $11,867 | $10,905 | $457,344 |

1H liquidity 數字同 15M 相約。但 **OB 結構完全唔同**：

### ⚠️ 1H OB = 空心結構（實測 2026-03-20 06:47 ET）
```
BTC 1H UP token (window ending soon):
  Best BID: $0.01 × 10,671 shares ← 底部 lottery
  Best ASK: $0.99 × 10,421 shares ← 頂部掃貨
  Mid-market ($0.40-0.60): ZERO orders
  Spread: $0.98 ← 實質冇 market maker
```
- Volume 集中在兩極（$0.01-0.05 同 $0.95-0.99）
- 中間價位 ($0.30-0.70) 近乎空白
- blue-walnut 唔係「market making」— 係 **directional binary betting** at conviction prices
- 呢個意味住：15M boundary dislocation 概念可能 N/A（冇 tight spread 可以 dislocate）
- **✅ CONFIRMED: hour 初段 book 結構完全唔同！**
  - 7AM BTC (minute 0): spread=$0.01, mid-market depth 26,317 shares
  - 6AM BTC (minute 47): spread=$0.98, mid-market depth 0
  - Book 隨方向確認 gradually 移向兩極 → blue-walnut 喺 liquidity 密嘅時候入場
  - 15M boundary dislocation thesis 仲 alive — 需 recorder 確認 spread 喺 boundary 有冇 widen

### Fee Structure
**同 15M 完全一樣**：maker 0.1% / taker 0.1%

### Assets 確認
- BTC ✅ | SOL ✅ | XRP ✅ | DOGE ✅
- ETH / BNB — 極大可能存在但 API 截斷

### ⚠️ Market 預早建立
1H market 喺 candle 開始前 ~2 日已建立。但 **真正活躍交易** 發生喺 candle hour 內（blue-walnut 數據證實）。

---

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 跟 blue-walnut pattern | 我哋 bankroll 小，需要高 margin (0.48%) 唔係高 volume (0.04%) |
| Resolution 用 Binance OHLC | 1H market oracle = Binance，唔係 Chainlink |
| 4 幣同做 | blue-walnut + swisstony 都 multi-asset |
| 15M boundary entry timing | ⚠️ clustering 真但 price dislocation 未證實 — 需 OB data |

## External Content
<!-- 2-Action Rule: 每 2 次 search/browse 後強制更新 -->
- whale_1h_analysis.json saved at polymarket/logs/whale_1h_analysis.json
- swisstony profile: $588M vol, $230K PnL, 5M primary
- 1H market slug: `{coin}-up-or-down-{date}-{hour}et`, resolution = Binance OHLC
