<!--
title: 儀表板 + 縮寫對照
section: 快速入門
order: 3
audience: human,claude,github
-->

# 儀表板 + 縮寫對照

打開 `http://localhost:5555` 可以見到儀表板。

## 頁面一覽

| 路徑 | 頁面 | 功能 |
|------|------|------|
| `/` | 主控台 | 持倉、盈虧、行動部署、系統活動 |
| `/backtest` | 回測 | K 線圖 + 回測模擬 + Order Flow + Live WS |
| `/details` | 系統說明 | 你而家睇緊嘅文件（全部指南） |
| `/share` | 分享 | macOS / Windows 安裝同步指南 |

## 主控台區域

| 區域 | 顯示咩 |
|------|--------|
| 行動部署 | 每隻幣嘅觸發狀態 + SL/TP 預覽 |
| 累積盈虧 | 今日 + 總計盈虧曲線 |
| 持倉明細 | 當前持倉 11 欄完整信息（入場價、SL、TP、PnL 等） |
| 交易記錄 | 每筆交易入場價、出場價、盈虧（來自交易所真實數據） |
| 系統活動 | 心跳、模式切換、入場出場事件 |
| 掃描記錄 | 掃描器最近信號同結果 |

## 行動部署狀態燈

| 狀態 | 意思 |
|------|------|
| 🟢 Ready | 波動已超過觸發門檻，可入場 |
| 🟡 Near | 接近觸發門檻（70% 以上） |
| ⚫ Far | 波動唔夠，等候中 |

## 縮寫對照（完整版）

### 價格 + 交易

| 縮寫 | 全名 | 意思 | 例子 |
|------|------|------|------|
| SL | Stop Loss | 止蝕位 — 輸到呢個價自動出場 | BTC SL $94,000 = 跌到 94K 就走 |
| TP | Take Profit | 止賺位 — 賺到呢個價自動出場 | BTC TP $100,000 = 升到 100K 就走 |
| S | Support | 支撐位 — 價格容易反彈嘅底部 | |
| R | Resistance | 阻力位 — 價格容易回落嘅頂部 | |
| PnL | Profit and Loss | 盈虧 | +$50 = 賺 50 |
| R:R | Risk-to-Reward | 風險回報比 | 2.3:1 = 潛在回報 2.3 倍於風險 |
| RR | 同 R:R | | |
| LONG | 做多 / 看升 | 低買高賣 | |
| SHORT | 做空 / 看跌 | 高賣低買 | |

### 技術指標

| 縮寫 | 全名 | 意思 | 用喺邊 |
|------|------|------|--------|
| ATR | Average True Range | 平均波幅 — 用嚟計 SL/TP 距離 | 倉位大小 |
| BB | Bollinger Bands | 布林帶 — 價格通道 | Range 策略入場 |
| RSI | Relative Strength Index | 相對強弱 — 超買/超賣 | 模式偵測 + 出場 |
| MACD | Moving Average Convergence Divergence | 移動平均收斂發散 — 動能方向 | 模式偵測 + Trend 入場 |
| EMA | Exponential Moving Average | 指數移動平均 — 趨勢方向 | Trend 策略 |
| ADX | Average Directional Index | 方向指數 — 趨勢強度 | Range 入場門檻 |
| Stoch | Stochastic Oscillator | 隨機指標 — 超買/超賣 | Range 加分 |
| OBV | On-Balance Volume | 平衡成交量 — 資金流向 | Yunis 加分/扣分 |
| MA | Moving Average | 移動平均線 | |

### 市場數據

| 縮寫 | 全名 | 意思 |
|------|------|------|
| CHG | 24h Change % | 過去 24 小時價格變化 |
| VOL | Volume | 成交量 |
| FR | Funding Rate | 資金費率 — 多空持倉成本 |
| OI | Open Interest | 未平倉合約總量 |
| USDT | Tether | 美元穩定幣（所有幣種嘅報價貨幣） |

### 系統狀態

| 縮寫 | 全名 | 意思 |
|------|------|------|
| CB | Circuit Breaker | 熔斷器 — 虧損超標自動停機 |
| CD | Cooldown | 冷卻期 — 連虧後暫停 |
| DD | Drawdown | 回撤 — 從最高點到現價嘅跌幅 |
| WR | Win Rate | 勝率 |
| PF | Profit Factor | 盈虧比 — 總盈利 ÷ 總虧損 |

### 策略模式

| 縮寫 | 意思 |
|------|------|
| RANGE | 橫行模式 — BB 觸碰 + RSI 反轉入場 |
| TREND | 趨勢模式 — EMA 排列 + 回調入場 |
| UNKNOWN | 未確定 — 保持上一個模式 |

### 交易所

| 縮寫 | 全名 |
|------|------|
| Aster | Aster DEX（去中心化交易所）— XAG/XAU |
| HL | HyperLiquid |
| Binance | Binance Futures — BTC/ETH/SOL/BNB/XRP/POL |

## 回測頁面速查

回測頁面（`/backtest`）功能摘要：

| 功能 | 說明 |
|------|------|
| 執行回測 | 用歷史數據模擬 AXC 策略，30-60 秒出結果 |
| K 線圖 | 蠟燭圖 + 入場/出場 markers + 連接線 |
| 指標 Overlay | BB、EMA、MA、RSI、MACD、Stoch（只喺 1H 顯示） |
| Order Flow | Whale 大額成交、Delta Volume、VP 成交分佈（藍=買/黃=賣）、FP 熱力圖（所有 interval 可用，Aster 幣種除外） |
| Live | 即時 K 線（Binance WebSocket，<1s 延遲） |
| Live Pos | 即時持倉線（入場/SL/TP，需要 API key） |
| A/B 對比 | 兩組參數結果並排比較 |
| 匯入/匯出 | JSON 格式報告，支持外部策略結果 |
| 畫圖工具 | 水平線、趨勢線、矩形（zone）、箭頭、Fibonacci |

詳見 → **回測頁面完整指南**（sidebar 搵「回測」）

## 鍵盤快捷鍵

| 按鍵 | 功能 | 頁面 |
|------|------|------|
| `Cmd+Enter` | 執行回測 | 回測 |
| `[` / `]` | 上/下一筆交易 | 回測 |
| `F` | 展開/縮小圖表 | 回測 |
| `P` | 開/關參數面板 | 回測 |
| `I` | 開/關指標列 | 回測 |
| `/` | 搜尋指南 | 系統說明 |
