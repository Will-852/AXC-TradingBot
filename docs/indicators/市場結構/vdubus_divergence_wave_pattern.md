# Vdubus Divergence Wave Pattern Generator

> 作者: vdubus
> 連結: https://tw.tradingview.com/script/fi2LLSGz-Vdubus-Divergence-Wave-Pattern-Generator-V1/
> 類型: Pine Script 指標

---

![Preview](../market_structure/vdubus_divergence_preview.png)

---

## 功能

一個結構同動能 confluence 系統，將幾何學（價格形態）同物理學（動能）結合。呢個指標唔係淨係睇簡單背離，而係要求 3-Wave Structure 去確認市場既真正狀態，唔係淨係得 2 點背離就signal。

---

## 核心理念：「幾何 + 物理」

傳統技術分析既問題係：交易者經常將「位置」同「時機」混淆。

- **幾何學（價格形態）** — 話俾你知市場可能喺邊度反轉（例如阻力位或者諧波 D 點）
- **物理學（動能）** — 話俾你知趨勢既能量幾時先真正轉向

Vdubus Theory 既主張：永遠唔應該單靠幾何學就交易。一個有效既 signal 需要動能既特定分形衰減 — 即係價格結構同能量耗盡之間既「握手」。

---

## 3-Wave Momentum Filter（引擎）

大部分交易者淨係搵簡單背離（2 點）。但 Vdubus Theory 要求 3-Wave Structure 去確認市場既真正狀態：

### A. 標準反轉（Exhaustion）
呢個係「安全」既 entry，可以捕捉到趨勢既「慢死亡」。

- **Wave 1 → 2（警告）**：價格推高，但動能較低（標準背離）。呢個信號表示趨勢開始踩煞車。
- **Wave 2 → 3（確認）**：價格推到最後一個 extreme（經常係 stop-hunt），但動能持平或者低過 Wave 2（「冇背離」）。

邏輯：呢個確認買家已經耗盡所有剩餘能量。Engine is dead。

---

## 使用建議

適合熟悉價格形態同動能分析既交易者。呢個指標幫你確認既唔係「價格可以去邊」，而係「幾時能量先真正轉向」。需要有耐心等 3-Wave 確認，先可以減少假 signal。

---

*最後更新: 2025-03-11*
