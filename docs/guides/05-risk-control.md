<!--
title: 風控機制
section: 操作指南
order: 5
audience: human,claude,github
-->

# 風控機制

系統有多重自動保護，唔需要手動操作。

## 9 大風控機制

| 機制 | 觸發條件 | 行為 |
|------|----------|------|
| 單筆熔斷 | 單倉虧損 ≥ 25% | 即時強制平倉 |
| 日度熔斷 | 當日虧損 ≥ 20% | 停止當日所有交易 |
| 連虧冷卻 | 連輸 2 次 | 暫停 30 分鐘 |
| 重度冷卻 | 連輸 3 次 | 暫停 2 小時 |
| 持倉上限 | 3 天未平倉 | 自動平倉（MAX_HOLD_HOURS=72） |
| 資金費率 | 資金費 > 50% 未實現盈虧 | 強制平倉 |
| 低流動性 | 成交量 < 50% 均值 | 唔入場 |
| 極端費率 | 資金費率 ≥ 0.2% | 唔入場 |
| 再入場縮減 | 虧損後再入場 | 倉位自動縮減 30% |

## 引擎內部參數

呢啲參數喺 `scripts/trader_cycle/config/settings.py`：

| 參數 | 值 | 說明 |
|------|-----|------|
| CIRCUIT_BREAKER_SINGLE | 25% | 單倉虧損上限 |
| CIRCUIT_BREAKER_DAILY | 20% | 日度虧損上限 |
| COOLDOWN_2_LOSSES_MIN | 30 min | 連輸 2 次冷卻 |
| COOLDOWN_3_LOSSES_MIN | 120 min | 連輸 3 次冷卻 |
| MAX_HOLD_HOURS | 72 | 最長持倉時間 |
| RANGE_LEVERAGE / TREND | 8x / 7x | 槓桿 |
| RANGE_SL_ATR / TREND | 1.2x / 1.5x | 止蝕倍數 |
| RANGE_MIN_RR / TREND | 2.3 / 3.0 | 最低風險回報比 |
| REENTRY_SIZE_REDUCTION | 30% | 虧損後再入場縮減 |
| ORDER_TIMEOUT_SEC | 300 | 未成交取消（5min） |
| POSITION_GROUPS | 3 組 | 每組最多 1 倉（見下） |

## 持倉分組（POSITION_GROUPS）

取代舊嘅 MAX_CRYPTO_POSITIONS / MAX_XAG_POSITIONS（已成 dead code）。

| 組別 | 幣種 | 最多倉位 |
|------|------|----------|
| crypto_correlated | BTC, ETH, SOL | 1 |
| crypto_independent | XRP, POL | 1 |
| commodity | XAG, XAU | 1 |

同組幣種互斥：BTC 有倉就唔會開 ETH 或 SOL。最多同時 3 倉（每組各 1）。

## 常見問題

如果你見到系統冇交易，最常見原因：
- 連虧冷卻中 → Telegram `/health` 查看
- 市場波動唔夠大 → 正常，繼續等
- 日度熔斷 → 第二日自動恢復
