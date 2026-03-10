# 指標知識庫 — docs/indicators/
> 建立：2026-03-10 | 更新：2026-03-11
> 用途：深度指標研究，供 AXC 系統開發參考
> 簡要索引在 trading_knowledge.md，深度內容喺呢度

### talk16 — 成個資料夾係咩
交易要做決定，每個決定需要唔同嘅工具。呢度按決定嘅順序排列：幾時入場 → 喺咩價位入 → 策略掂唔掂 → crypto 獨有情報 → 參數有冇問題 → 做市理論。每個文件開頭都有 talk16 簡介，唔使打開就知道入面有咩。

## 文件索引

### 基礎研究
| 文件 | 一句講 | 內容 |
|---|---|---|
| entry_indicators.md | 幾時入場？ | BB、RSI、MACD、STOCH、EMA、ADX 深度 + 冗餘分析 |
| volume_and_structure.md | 喺咩價位入？止損放邊？ | ATR、OBV、VWAP、Fibonacci、Ichimoku、MTF |
| evaluation_metrics.md | 策略掂唔掂？落幾多注？ | Sharpe、Sortino、Kelly、Drawdown、Position Sizing |
| crypto_specific.md | Crypto 獨有情報 | Funding Rate、OI、CVD、MVRV、NVT、Fear & Greed |
| params_reference.md | AXC 參數有冇問題？ | params.py 數值 vs 業界標準對比 |
| glft-reservation-pricing.md | 做市理論（完整模擬） | GLFT 保留價格、VPIN 毒性、Jump-Diffusion |

### Trader Yunis Collection
> 8 個 TradingView 指標，每個獨立頁面，talk12 風格講解
> → [yunis-collection/README.md](yunis-collection/README.md)

| 文件 | 指標 | 核心概念 |
|---|---|---|
| yunis-collection/01-atr-keltner-channel.md | ATR Keltner Channel | 三層 ATR band + wick signal |
| yunis-collection/02-nexus-flow-elite.md | Nexus Flow Elite | ADX-adaptive + 機構流 + MTF |
| yunis-collection/03-macd-pro.md | MACD PRO | 4-color histogram + divergence |
| yunis-collection/04-volume-sync-price-flow.md | VolumeSyncPriceFlow | VWAP + 6 StdDev bands |
| yunis-collection/05-vista-pro.md | VISTA PRO | 漸進確認 + MR oscillator |
| yunis-collection/06-trend-sync.md | TrendSync | EMA ribbon + multi-BB + SMC |
| yunis-collection/07-volt-pro.md | VOLT PRO | CVD + Z-Score 雙共振 ⭐ |
| yunis-collection/08-risk-management.md | Risk Management | Position sizing 工具 |

## 模式速查 — 邊個模式重點睇咩

指標本身三個模式都一樣（RSI 就係 RSI），變嘅係**你要關注嘅重點同參數**。

### CONSERVATIVE（只做 RANGE，1% 風險，單倉）
| 重點 | 去邊度睇 |
|---|---|
| BB 觸帶 + RSI 超買超賣 = 你嘅核心入場 | entry_indicators.md §BB, §RSI |
| ATR × 1.5 止損（比其他模式寬） | volume_and_structure.md §ATR |
| Quarter Kelly + Risk of Ruin 要特別低 | evaluation_metrics.md §Kelly, §RoR |
| Funding Rate 異常 → 暫停（你風險預算最少） | crypto_specific.md §Funding Rate |

### BALANCED（RANGE 為主 + 部分 TREND，2% 風險）
| 重點 | 去邊度睇 |
|---|---|
| BB + RSI + EMA 方向確認 | entry_indicators.md §BB, §RSI, §EMA |
| ADX 判斷而家 range 定 trend → 決定用邊套邏輯 | entry_indicators.md §ADX |
| OBV 確認量有冇跟（假突破過濾） | volume_and_structure.md §OBV |
| ATR × 1.2 止損 + Fibonacci 支撐位做 TP 參考 | volume_and_structure.md §ATR, §Fibonacci |
| OI + CVD 判斷大戶倉位方向 | crypto_specific.md §OI, §CVD |

### AGGRESSIVE（追趨勢，3% 風險，最多 3 倉）
| 重點 | 去邊度睇 |
|---|---|
| EMA + ADX 趨勢確認係你嘅命脈（唔好逆趨勢） | entry_indicators.md §EMA, §ADX |
| ATR × 1.0 緊 SL — 要快止損快止盈 | volume_and_structure.md §ATR |
| MACD 動力確認 + RSI 75/25 畀多啲空間跑 | entry_indicators.md §MACD, §RSI |
| OI 高 + Funding 極端 → 清算瀑布風險（你揸 3 倉最傷） | crypto_specific.md §OI, §Liquidation |
| 多倉位要計總風險暴露：3 × 3% = 最多 9% 同時 at risk | evaluation_metrics.md §Position Sizing |

---

## 使用方式
1. 研究新指標 → 寫入對應文件（基礎）或新增 collection 頁面
2. 決定實裝 → 跟 trading_knowledge.md 嘅「系統架構」流程
3. params.py → indicator_calc → strategy
