## 2026-03-07 | Dashboard 大改 + Scanner 優化 + TG Bot 記憶

### Dashboard 真實數據接入
- `get_live_trade_history()` — Aster API `/fapi/v1/userTrades?limit=30`，60s cache
- `exchange_trades` + `fee_breakdown` 加入 `collect_data()` response
- 持倉明細卡 `renderPositionDetail()` — 11 欄完整持倉信息
- 交易記錄改用 exchange 真實數據，最新排前

### Dashboard UI 重設計
- OpenClaw → AXC 品牌重命名（8 處）
- Emoji → FontAwesome + SVG icons（BTC/ETH/XRP/XAG/XAU/ADA）
- SVG 靜態文件 serve route `/svg/*.svg`
- 交易模式切換 dropdown（CONSERVATIVE/BALANCED/AGGRESSIVE）
- Aster DEX + Binance CEX 專業登入 modal（branded colors）
- Sidebar layers：路徑可複製 + 簡短描述
- 多輪 screenshot feedback 修正（icon 大小、間距、header 移除）

### Scanner v6 — 梅花間竹 + 熱載入
- Aster/Binance 單雙數交替掃描，各減半 request rate
- `reload_params()` 每 10 round 熱載入 config/params.py（免重啟）
- 共用幣種（BTC/ETH）交替、獨佔幣種固定平台

### TG Bot 短期對話記憶
- `_chat_history` deque(maxlen=10) — 最近 5 組對話
- 10 分鐘無活動自動過期
- `call_claude()` 支援 multi-turn messages array
- `/forget` 手動清除短期記憶
- System prompt 更新：指示 Claude 接住上文

### 健康檢查 03:09
- 38 pass / 2 warn / 0 fail
- ⚠️ STRATEGY.md 9 行（weekly_review 未跑）
- ⚠️ 5 個 .bak 已移至 backups/
- Dashboard: 1 持倉, 23 交易, fee_breakdown ✅
- TG Bot: running (PID 45240)
- Scanner: running (PID 39591)

---

## 2026-03-05 | OpenClaw 續集開發日誌

### AI Stack 架構決策（最重要）
- 確認並記錄長期技術選型：
  - 推理層：Claude API（拒絕本地LLM）
  - 向量層：voyage-3（語義理解）
  - 搜尋層：numpy cosine similarity
  - 記憶層：jsonl + npy
- 拒絕 Faiss（過度設計）
- 拒絕 Llama/Mistral（質量差於Claude Haiku）
- 存檔：~/projects/axc-trading/ARCHITECTURE_DECISIONS.md

### 本地 LLM 實驗 + 清除
- 測試 Llama2 via Ollama vs GPT-5 Mini proxy
- 發現：測試腳本用咗錯誤 env var（ANTHROPIC_API_KEY vs PROXY_API_KEY）
- 結論：proxy 正常，系統正常，本地LLM唔值得
- 清除：Llama2（3.8GB）+ Ollama（165MB）完全刪除
- 回收約 4GB 磁碟空間

### voyage-3 Embedding 升級完成
- hash向量 → voyage-3語義向量
- 本地快取避免重複API調用
- 索引重建，22條記憶重新向量化
- VOYAGE_API_KEY 已設定（注意：需要 rotate）

### Telegram Bot 路徑修復
- /health 路徑修正：
  - 主腦：agents/main/sessions/sessions.json
  - 掃描器：workspace/.../SCAN_LOG.md
  - 心跳：logs/heartbeat.log
  - 信號：shared/SIGNAL.md

### Telegram Bot 全面修復（7項）
- 廣東話強制執行
- 假平倉報告修復（None sentinel）
- 重複告警防止（stall_warned flag）
- pending_orders 持久化
- /cancel 指令
- voyage-3 非阻塞執行
- /start 更新

### TRADE_STATE.md 自動同步
- tg_bot.py 新增 _sync_trade_state() 函數
- 觸發點：成功下單、平倉、背景監控偵測到平倉
- 同步兩個路徑：shared/ + agents/aster_trader/

### 架構規劃（待實現）
- 新Agent：news_agent / recorder_agent
- Twitter爬取：Python Script（唔係Agent）
- 信號判斷：Agent
- 策略規則歸納：weekly_strategy_review.py（方案C）

### Dashboard 用戶指南
- 頂部可折疊指南面板
- /docs/friends 路由
- docs/friends/INSTALL.md 重寫（標明獨立接口）

### GitHub 初始化
- Repo建立：https://github.com/Will-852/openclaw（private）
- 26個文件，1089行改動全部上傳
- Remote tracking 設定完成

### 環境變數澄清
- .env 用 PROXY_API_KEY（正確）
- 唔係 ANTHROPIC_API_KEY
- tg_bot.py 正確，系統正常
