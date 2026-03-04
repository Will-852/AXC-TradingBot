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

