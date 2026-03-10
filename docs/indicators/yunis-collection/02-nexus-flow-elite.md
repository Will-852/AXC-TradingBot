# 02 — Nexus Flow Elite System (NFES)
> 智能趨勢系統 | Trader_Yunis
> TradingView: 公開（closed-source）

---

## Talk12 — 用最簡單嘅方式講

想像你喺操場度睇一場足球賽。

**SuperTrend** 就好似一條「得分線」— 只要球（價格）喺線上面，藍隊（多頭）贏緊；跌到線下面，紅隊（空頭）贏緊。但呢條線唔係死嘅，佢會根據比賽嘅「激烈程度」（ADX）自動調整。比賽好激烈嘅時候，線會放寬少少，唔會因為一個小動作就判藍隊輸。

**機構資金流**就好似你睇到場邊有幾個大人（大戶/機構）偷偷走入嚟買嘢。如果大人不斷靜靜咁入場（accumulation），代表佢哋知道啲嘢你唔知 — 跟住佢哋通常冇錯。

**六種模式**就好似六種唔同嘅比賽戰術：
1. **趨勢模式** = 順風波，只做贏緊嗰邊
2. **逆勢模式** = 對手太攰嘅時候反攻
3. **成交量過濾** = 只有觀眾夠多（有量）先算數
4. **強度過濾** = 只有比賽夠激烈（ADX > 25）先入場
5. **雲過濾** = 用 EMA 雲確認方向
6. **雙向模式** = 順勢 + 逆勢結合

**風險管理**就好似你出場前設定好「幾時走人」：
- 贏到第一個目標就將止損搬到入場價（breakeven）
- 隨住贏越多，止損跟住升（trailing stop）
- 唔使你自己決定幾時走

---

## 技術細節

### 核心引擎
- **Adaptive SuperTrend**：SuperTrend 嘅 ATR multiplier 由 ADX 動態調整
  - ADX 高 → multiplier 大 → 更跟趨勢，唔易被震走
  - ADX 低 → multiplier 小 → 更敏感，快啲反應

### 六種訊號模式
| Mode | 條件 | 最佳市況 |
|---|---|---|
| Trend | 只做 long MA 上方嘅 long | 強趨勢 |
| Counter-Trend | RSI 極端反轉 | Range 市 |
| Volume Filter | 需要成交量 > 平均 | 過濾假突破 |
| Strength Filter | ADX > 25 | 只做有趨勢嘅 |
| Dynamic Cloud | EMA cloud 方向過濾 | 類似 Ichimoku |
| Bidirectional | 趨勢 + 逆勢結合 | 所有市況 |

### 機構資金流偵測
- 監測連續大單出現
- [B]↑（cyan）= accumulation（吸貨）
- [S]↓（gray）= distribution（出貨）
- 需要 trade-level data

### 訊號視覺
- 實心三角 = 強訊號（多條件 align）
- 透明三角 = 普通訊號
- R / R+ diamond = 反轉訊號

### 風險管理自動化
```
Entry → SL = ATR × mult 或 固定 %
價格到 TP1 → SL 搬到 Entry（breakeven）
價格繼續走 → Trailing SL 跟住
TP1 / TP2 / TP3 = 分批止盈
```

### Multi-TF Confirmation
- 同時睇 M5, M15, 1H, 4H
- 多個 TF 方向一致先出訊號
- 過濾 60-80% 假訊號

---

## AXC 可借鑒

| 概念 | 現狀 | 行動 |
|---|---|---|
| ADX-adaptive mode switching | 有 mode_detector 但唔 adaptive | ⭐ 升級 mode_detector |
| Volume filter | 冇 volume 確認 | ⭐ 最大盲點 |
| 六種模式 vs 兩種 | AXC 有 RANGE + TREND | 可加 Volume Filter + Strength Filter |
| Trailing SL | 冇 | 中期加入 |
| Breakeven after TP1 | 冇 | 中期加入 |
| MTF confirmation | 冇（單 TF） | 長期目標 |
| 機構資金流 | 冇 | 需要 trade data，長期 |
