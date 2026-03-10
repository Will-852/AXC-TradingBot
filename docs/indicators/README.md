# 指標知識庫 — docs/indicators/
> 建立：2026-03-10
> 用途：深度指標研究，供 AXC 系統開發參考
> 簡要索引在 trading_knowledge.md，深度內容喺呢度

## 文件索引

### 基礎研究
| 文件 | 內容 |
|---|---|
| entry_indicators.md | BB、RSI、MACD、STOCH、EMA、ADX 深度 + 冗餘分析 |
| crypto_specific.md | Funding Rate、OI、CVD、MVRV、NVT、Fear & Greed |
| evaluation_metrics.md | Sharpe、Sortino、Kelly、Drawdown、Position Sizing |
| volume_and_structure.md | ATR、OBV、VWAP、Fibonacci、Ichimoku、MTF |
| params_reference.md | AXC params.py 數值 vs 業界標準對比 |

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

## 使用方式
1. 研究新指標 → 寫入對應文件（基礎）或新增 collection 頁面
2. 決定實裝 → 跟 trading_knowledge.md 嘅「系統架構」流程
3. params.py → indicator_calc → strategy
