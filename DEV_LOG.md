## 2026-03-05 | OpenClaw 續集開發日誌

### AI Stack 架構決策（最重要）
- 確認並記錄長期技術選型：
  - 推理層：Claude API（拒絕本地LLM）
  - 向量層：voyage-3（語義理解）
  - 搜尋層：numpy cosine similarity
  - 記憶層：jsonl + npy
- 拒絕 Faiss（過度設計）
- 拒絕 Llama/Mistral（質量差於Claude Haiku）
- 存檔：~/.openclaw/ARCHITECTURE_DECISIONS.md

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
