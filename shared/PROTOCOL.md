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

## 文件命名慣例
[source_agent]_output.json
[platform]_execution_log.json
