<!--
title: 交易模式
section: 操作指南
order: 4
audience: human,claude,github
-->

# 交易模式

系統有三個交易模式，控制風險大小同觸發敏感度。

## 三個打法 Profile

config/params.py 嘅 `ACTIVE_PROFILE` 控制交易風格。Profile 覆蓋 settings.py 嘅策略常數。

| 打法 | Risk/Trade | SL (ATR) | Range R:R | Trend R:R | 適合 |
|------|-----------|----------|-----------|-----------|------|
| 穩 (CONSERVATIVE) | 1.5% | 1.5x | 2.5 | 3.5 | 市場平靜 |
| 平 (BALANCED) | 2.0% | 1.2x | 2.3 | 3.0 | 一般市況 |
| 攻 (AGGRESSIVE) | 2.5% | 1.0x | 2.0 | 2.5 | 市場活躍 |

## 點樣切換？

Telegram 指令：
```
/mode              查看當前模式（彈出按鈕選擇）
/mode BALANCED     直接切換到平衡模式
/mode AGGRESSIVE   直接切換到進取模式
```

儀表板：右上角模式顯示區域可以點擊切換。

## 注意事項

揀定一個模式後，最少跑 30 次交易先換。頻繁切換令數據冇參考價值。

## 持倉上限

3 個組別，每組最多 1 倉：
- **crypto_correlated**: BTC, ETH, SOL（互斥）
- **crypto_independent**: XRP, POL（互斥）
- **commodity**: XAG, XAU（互斥）

## 交易策略

| 策略 | 觸發條件 | SL | TP (Min R:R) | 槓桿 |
|------|----------|-----|-------------|------|
| RANGE（橫行） | BB 觸碰 + RSI 反轉 + S/R + Stoch | 1.2x ATR | 2.3:1 | 8x |
| TREND（趨勢） | 4 指標對齊 + 回調買入 + 4H+1H 確認 | 1.5x ATR | 3.0:1 | 7x |

## 模式偵測（橫行 vs 趨勢）

系統用 4H K 線上嘅 5 個指標自動判斷：

- RSI 位置（超買/超賣/中性）
- MACD 方向（上升/下降/中性）
- 成交量趨勢（放量/縮量）
- 資金費率（正/負/極端）
- BB 寬度（收窄 = RANGE，擴張 = TREND）

五票制：多數決定用 RANGE 定 TREND 策略。
