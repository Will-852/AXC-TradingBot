# shared/ — Inter-Agent Communication Protocol

## 現有文件（不變，向後兼容）

| 文件                  | 寫入方           | 讀取方                    | 格式     |
|-----------------------|-----------------|--------------------------|--------|
| SIGNAL.md             | aster_scanner   | main, aster_trader       | Markdown |
| TRADE_STATE.md        | aster_trader    | main, dashboard          | Markdown |
| SCAN_LOG.md           | aster_scanner   | dashboard, main          | Markdown |

## 新增文件（新 pipeline 使用）

| 文件                        | 寫入方           | 讀取方          | 格式  | 過期時間 |
|-----------------------------|-----------------|----------------|-------|---------|
| haiku_filter_output.json    | haiku_filter    | analyst        | JSON  | 5分鐘   |
| analyst_output.json         | analyst         | decision       | JSON  | 5分鐘   |
| decision_output.json        | decision        | aster_trader   | JSON  | 60秒    |
| aster_execution_log.json    | aster_trader    | main（監控）    | JSON  | Append  |
| binance_execution_log.json  | binance_trader  | main（監控）    | JSON  | Append  |

## 規則
1. 每個 agent 只覆寫自己的 output 文件
2. execution_log 文件 append-only（不覆寫）
3. decision_output.json 超過60秒未被消費 → 視為過期，aster_trader 拒絕執行
4. Binance 文件已預留路徑，但在 binance_trader 啟用前不會生成

## Redis Streams（2026-03-19 新增）

File bus 仍然係 primary IPC。Redis Streams 係 supplementary，提供實時數據 fan-out。

### Streams
| Stream | Producer | Consumer | 內容 | maxlen |
|--------|----------|----------|------|--------|
| `market:klines` | ws_manager | indicator_engine | BTC kline events (3m/15m/1h/4h) | 10,000 |
| `market:ticker` | ws_manager | dashboard (future) | BTC miniTicker (~2s) | 1,000 |
| `market:poll` | async_scanner | indicator_engine (optional) | 9-exchange poll results | 5,000 |

### Consumer Groups
| Group | Consumer | Stream | 用途 |
|-------|----------|--------|------|
| `indicators` | `indicator-1` | `market:klines` | kline close → recalc indicators |

### 新文件
| 文件 | 寫入方 | 讀取方 | 格式 | 過期 |
|------|--------|--------|------|------|
| indicator_cache.json | indicator_engine | trader_cycle (CalcIndicatorsStep) | JSON | 10min |

### Degradation
- Redis down → indicator_engine 自動 fallback REST fetch every 3min
- indicator_cache stale → trader_cycle 自動 fallback 到原有 REST + calc
- 全部新嘢掛 = 現有系統，零退化

### 服務
| 服務 | LaunchAgent | 類型 |
|------|-------------|------|
| ws_manager | ai.openclaw.wsmanager | KeepAlive |
| indicator_engine | ai.openclaw.indicatorengine | KeepAlive |
| trader_cycle | ai.openclaw.tradercycle | StartInterval=900 (15min) |

## 文件命名慣例
[source_agent]_output.json
[platform]_execution_log.json
