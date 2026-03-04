# COMMUNICATION_LOG_2026-03-05.md

記錄當日 Claude 對話內容。

---

## 對話記錄

（自動追加）


---

### 📍 2026-03-05 02:10:26 - GitHub Backup Trigger

**內容待輸入**


---

### 📍 2026-03-05 02:10 - Telegram Bot 全面修復 + 交易安全加固

**主題**：Telegram 格式修復、下單安全網、SL/TP 修正

**核心內容**：

#### 1. Telegram 格式統一 (HTML)
- SYSTEM_PROMPT 重寫：禁 Markdown，用 `<b>` HTML
- 全部 13 個硬編碼訊息：`parse_mode="Markdown"` → `"HTML"`
- 新增 `_clean_for_telegram()` safety net：自動轉換殘留 Markdown
- 新增 `_send_html()` 統一發送邏輯
- SOUL.md × 2 加咗 Telegram 格式規則

#### 2. 下單安全網 (3 層防護)
- Sanity clamp：SL 硬限 1%-5%，TP 硬限 1%-10%
- Auto-fix：model 返回 >1 嘅值自動除 100
- 清算保護：SL 唔可以低過清算價，自動調高
- `round()` float→int bug 修復

#### 3. SL/TP 即時修正
- XRP LONG 持倉：SL $1.00 → $1.4087 (-2.5%)，TP $2.00 → $1.5026 (+4%)
- 原因：Haiku 解析出離譜嘅 sl_pct/tp_pct 值

#### 4. 下單確認 + 成功訊息加強
- 確認介面：顯示槓桿、逐倉、名義值、現價、SL/TP 實際價格
- 成功訊息：完整顯示入場/數量/名義/保證金/槓桿/SL/TP

#### 5. LaunchAgent 管理
- 發現 tg_bot.py 由 `ai.openclaw.telegram` LaunchAgent 管理 (KeepAlive: true)
- 正確重啟方式：`launchctl stop/start`，唔好直接 kill

**改動檔案**：
- `scripts/tg_bot.py` — 主要改動
- `agents/main/workspace/SOUL.md` — 加 Telegram 格式規則
- `workspace/core/SOUL.md` — 加 Telegram 格式規則

**狀態**：✅ 已部署，tg_bot 已重啟


---

### 📍 2026-03-05 02:11:43 - GitHub Backup Trigger

**內容待輸入**


---

## 2026-03-05 | OpenClaw 開發日誌

### Dashboard UI 全面修復
- Header Account Bar 強化（38px，Aster綠/$，Binance黃/箭頭）
- Sidebar 平台連接完全刪除
- 市場走勢 OKX 風格緊湊 Ticker（圖標/H&L/Sparkline）
- 觸發摘要提升至左下欄頂部，累積盈虧縮至130px
- Sidebar 隱藏滾動條
- XAG截斷修復、掃描記錄填滿高度、交易記錄empty state
- Sparkline 漸變填充+末端圓點，PnL顏色區分

### PnL + Model 修復
- baseline邏輯重寫，today/total獨立計算，唔再出現數學矛盾
- 主腦+心跳 gpt-5-mini → claude-haiku-4-5

### Telegram Bot v2.0
- 自然語言下單解析（Claude intent detection）
- Inline按鈕二次確認，高風險90秒冷靜期
- /sl breakeven 止損移至開倉價
- 平倉後自動生成交易報告
- Agent停止活動告警
- pending_orders持久化到JSON
- None sentinel防止重啟後假平倉報告
- stall_warned flag防重複告警
- /cancel指令
- voyage-3 semaphore限流保護

### Telegram 格式修復 + 交易安全加固
- 全部訊息 Markdown → HTML parse_mode
- SYSTEM_PROMPT 重寫：禁 Markdown，用 <b> HTML
- _clean_for_telegram() + _send_html() safety net
- SL/TP sanity clamp (1-5% SL, 1-10% TP) + 清算保護
- round() float precision bug fix
- 下單確認+成功訊息顯示完整 SL/TP/槓桿/名義值

### RAG 記憶系統升級
- hash向量 → voyage-3語義向量
- 本地快取，免重複API調用
- 索引重建，所有記憶重新向量化
- 自動fallback機制
- VOYAGE_API_KEY 已設定

### /health 路徑修復
- 主腦：agents/main/sessions/sessions.json
- 掃描器：workspace/.../SCAN_LOG.md
- 心跳：logs/heartbeat.log
- 信號：shared/SIGNAL.md

### 架構設計決策
- Agent vs Script 分工原則確立
- 新增計劃：news_agent / recorder_agent / twitter_scraper
- Twitter爬取=Script，信號判斷=Agent
- 兩個交易平台共用trader_agent腦袋，executor分開

### Dashboard 用戶指南
- 頂部可折疊指南面板（4格grid）
- 接口狀態點（Aster/Binance/Telegram獨立）
- 永久隱藏選項
- /docs/friends 路由渲染INSTALL.md
- docs/friends/INSTALL.md 完整重寫（標明獨立接口）

### GitHub Repo 建立
- Will-852/openclaw (private)
- 全部代碼 push 到 main branch

