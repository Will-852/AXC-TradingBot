# OpenClaw Operations Patterns — AI Agent 穩定運作指南
# 來源: 社群實戰經驗分享
# 建立: 2026-03-02
# 目的: Agent 運維最佳實踐 + 知識管理架構

---

## Quick Reference

| 原則 | 核心概念 | OpenClaw 對應 |
|------|---------|---------------|
| Skill > 即興 | 程式化 > 隨機生成 | ✅ 已實踐（Python scripts 取代 LLM） |
| 持久記憶 | Agent 需要長期記憶系統 | ✅ 部分實踐（MEMORY.md + SCAN_CONFIG） |
| 工具生態 | 免費工具組合 = 完整運作環境 | ⬜ 可擴展（Tunnel、Discord、MongoDB） |
| 原子筆記 | 知識拆散再連結 > 資料夾堆積 | ⬜ 可採用（改善 knowledge/ 結構） |

---

## 第一原則：Skill（程式化）> 即興發揮

### 核心邏輯
> 可靠 > 聰明。能確定嘅部分盡可能確定，降低不確定性。
> 用一個可靠嘅員工，比用一個不可靠嘅天才更重要。

### 實踐方法
- 任務流程寫成 **明確步驟**（唔係「幫我分析」而係「跑呢 11 步 pipeline」）
- 每次結果要 **可預測**（同樣 input → 同樣 output）
- LLM 只用於真正需要理解力嘅任務

### OpenClaw 已實踐
- light-scan: Python script，每次 2 秒，結果一致
- trader-cycle: 11 步 pipeline，純 if-else，零隨機性
- 策略評估: 數學公式，唔係 LLM 判斷

### 進一步改善
- heartbeat 可以考慮 Python 化（目前仲用 LLM systemEvent）
- NEWS 分析如果啟用，應該有 structured output schema（唔係 freeform）
- Telegram 匯報用 template（目前 Python 已做到）

---

## 第二原則：持久記憶系統

### 核心邏輯
> Agent 本身冇長期記憶。你今天做咗乜，明天就忘。
> 掌控感，是你能持續信任佢嘅關鍵。

### Obsidian 方案特色
- **本地檔案**（唔係雲端鎖死）
- **雙向連結**（筆記互相 reference）
- **Mermaid 圖表**（可視化架構 + 流程）
- **跨裝置同步**（手機都睇到）

### OpenClaw 目前嘅記憶系統
| 功能 | 檔案 | 作用 |
|------|------|------|
| 系統記憶 | `memory/MEMORY.md` | 全局狀態 + 歷史 |
| 交易狀態 | `TRADE_STATE.md` | 倉位 + PnL + 冷卻 |
| 掃描設定 | `SCAN_CONFIG.md` | 市場數據 + 觸發閾值 |
| 交易日誌 | `TRADE_LOG.md` | 每筆交易記錄 |
| 掃描日誌 | `SCAN_LOG.md` | 每次掃描記錄 |
| 知識庫 | `knowledge/*.md` | 學習到嘅 patterns |
| Session 日誌 | `SESSION_LOG_*.md` | 每日工作總結 |

### 可改善方向
- **Mermaid 架構圖**: 可以加入 MEMORY.md（pipeline flow、strategy decision tree）
- **自動日誌**: trader-cycle 每次跑完自動 append structured log
- **知識連結**: knowledge/ 入面嘅 patterns 可以 cross-reference（Obsidian 風格 [[link]]）
- **Obsidian Vault**: 可以將 workspace/ 直接作為 Obsidian vault 開啟

---

## 第三原則：免費工具生態

### 推薦工具 + OpenClaw 適用度

| 工具 | 用途 | 適用度 | 備註 |
|------|------|--------|------|
| **Cloudflare Tunnel** | 外部存取本地服務 | ⭐⭐⭐ | 遠端查看 OpenClaw dashboard |
| **Discord** | 任務分類通知 | ⭐⭐ | 目前用 Telegram，Discord 可做多 channel |
| **MongoDB** | 任務狀態持久化 | ⭐ | 目前用 MD 檔案，夠用 |
| **TeamViewer** | 遠端桌面 | ⭐⭐ | 緊急排障用 |
| **Whisper (local)** | 語音轉文字 | ⭐ | 如果加入語音指令 |

### 優先實施
1. **Cloudflare Tunnel** — 最有價值，可以喺手機睇到 OpenClaw dashboard
2. **Obsidian Vault** — 將 workspace/ 作為 vault，免費獲得知識圖譜
3. 其他按需加入

---

## 知識管理架構：PARA + Zettelkasten

### 架構設計
```
00_Inbox/       → 快速收集（未處理嘅想法、資料）
10_Projects/    → 進行中嘅專案（Phase 3、新策略開發）
20_Areas/       → 持續關注嘅領域（風控、市場分析、成本優化）
30_Notes/       → 原子永久筆記（自己嘅思考 + 決策理由）
40_Sources/     → 文獻筆記（freqtrade、TradingAgents、社群分享）
50_Archives/    → 歸檔（已完成嘅 Phase、已停用嘅設定）
```

### 原子筆記 vs 傳統筆記

| 傳統 | 原子筆記 |
|------|---------|
| 按主題分類放資料夾 | 每個想法獨立一篇 |
| 整齊但獨立，難連結 | 自由連結，跨主題碰撞 |
| 收藏別人嘅話 | 用自己嘅話寫「你理解到乜」 |
| 時間久唔會回去睇 | 不斷 reference + 重組 |

### OpenClaw 可以點用
- `knowledge/` 已有嘅 MD 就係 40_Sources（外部學習）
- `memory/MEMORY.md` 就係 20_Areas（持續追蹤）
- 每次做重大決策，寫一篇 30_Notes 記錄「點解咁決定」
- 例子: 「點解用 Python 取代 LLM」→ 呢個就係一篇原子筆記

### 三層標籤系統（建議）
```
Level 1 — 分類: #trading #infra #risk #strategy
Level 2 — 狀態: #active #archived #experimental
Level 3 — 來源: #freqtrade #experience #community
```

---

## 關鍵 Takeaway

> **AI Agent 嘅價值唔在於佢有幾聰明，而在於你能唔能建立一個讓佢穩定運作嘅系統。**

三支柱：
1. **Skill 讓佢可靠** → Python scripts, structured pipeline
2. **記憶讓佢有腦** → MEMORY.md, knowledge/, session logs
3. **工具讓佢有環境** → launchd, Telegram, Aster API

OpenClaw 已經做到前兩個，第三個工具生態可以持續擴展。
