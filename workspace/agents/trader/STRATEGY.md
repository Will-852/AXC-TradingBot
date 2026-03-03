# agents/trader/STRATEGY.md — Trader 策略參考
# 版本: 2026-03-02
# 完整策略: {ROOT}/core/STRATEGY.md
# 完整風控: {ROOT}/core/RISK_PROTOCOL.md

## 每個 Cycle 執行步驟（16-Step Pipeline）

```
 1. read_state        — 讀 TRADE_STATE + SCAN_CONFIG
 2. safety_check      — 熔斷？冷卻？日虧上限？
 3. fetch_market      — OHLCV + orderbook + funding
 4. calc_indicators   — RSI/MACD/BB/ATR/MA/Volume
 5. detect_mode       — 5 指標投票 RANGE/TREND
 6. no_trade_check    — volume/funding/position 封鎖
 7. check_positions   — 倉位同步 + orphan detection
 8. manage_positions  — 止盈/止損/trailing 管理
 9. evaluate_signals  — 策略評估（Range/Trend）
10. select_signal     — 揀最強信號
11. size_position     — SL/TP/size 計算
12. execute_trade     — 落盤（LIVE: Aster DEX）
13. write_state       — 更新 TRADE_STATE + SCAN_CONFIG
14. write_trade_log   — 記錄交易到 TRADE_LOG.md
15. write_memory      — 記錄重要事件
16. send_reports      — Telegram 匯報（繁體中文）
```

## 快速倉位參考

| 模式 | Capital | SL | TP | 槓桿 |
|------|---------|----|----|------|
| Range | 2% | 1.5×ATR | Next S/R | 8x |
| Trend | 2% | 1.5×ATR | Next S/R | 7x |
| Scalp | 1% | 1×ATR | 2.5×ATR | 5x |

## 入場確認數量

- Range：3/3 KEY 指標
- Trend：4/4 KEY 指標
- Re-entry：5/5 指標（更嚴格）
- 週四夜 SHORT / 週五夜 LONG：3.5/5 指標

## Adaptive Sampling 狀態

讀 SCAN_CONFIG.md 確認：
- SILENT_MODE: OFF → 正常 Cycle
- SILENT_MODE: ON → 跳過例行 Telegram 匯報
- TRIGGER_PENDING: ON + age <25min → FAST Mode（跳過讀 SOUL/STRATEGY）
