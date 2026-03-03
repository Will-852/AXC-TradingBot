# SKILL: WORKSPACE_OPS — 工作區安全修改協議
# 版本: 2026-03-02
# 用途: 任何 agent 修改 workspace 結構前必讀
# 原則: 插入唔破壞，擴展唔重構

---

## 觸發條件

當你需要做以下任何操作時，執行此 Skill：
- 新增 MD 檔案
- 新增 Agent
- 新增 Cron Job
- 新增交易對
- 新增 Python 工具
- 修改檔案結構（搬、改名、刪）

---

## RULE 0 — 讀先寫後（Read Before Write）

**任何修改前，先讀以下三個檔案：**

```
1. config/PATH_CONFIG.md     → 了解現有路徑
2. routing/MODEL_ROUTER.md   → 了解模型分配
3. agents/AGENTS.md          → 了解寫入權限
```

唔讀就寫 = 盲改 = 容易撞車。

---

## OP-1: 新增 MD 檔案

### Checklist

```
□ Step 1  決定所屬目錄
          ├── config/      → 系統設定、身份
          ├── routing/     → 模型、成本
          ├── protocols/   → 運行協議
          ├── core/        → 策略、靈魂、風控
          ├── agents/X/    → Agent 專屬
          ├── memory/      → 記憶、情緒
          ├── keys/        → API 密鑰（⚠️ 敏感）
          ├── tools/       → Python 腳本
          └── skills/      → 技能協議

□ Step 2  命名規則
          - 全大寫 + 底線: TRADE_LOG.md, SCAN_CONFIG.md
          - 用途清晰: 睇名就知做乜
          - 唔用空格、唔用中文檔名

□ Step 3  檔案頭部（必須）
          # FILENAME.md — 一行描述
          # 版本: YYYY-MM-DD
          # 寫入: 邊個 agent 有權寫（或 "所有 agent"）

□ Step 4  註冊到 PATH_CONFIG.md
          - 加入對應 section
          - 格式: PATH_XXX = {ROOT}/directory/FILENAME.md
          - ⚠️ 唔註冊 = 唔存在（其他 agent 搵唔到）

□ Step 5  如果需要根目錄副本
          - 只有 OpenClaw 平台主讀取嘅檔案需要根目錄版本
          - 一般新檔案唔需要，放子目錄就夠
          - 如需根目錄版本 → 兩份內容必須同步

□ Step 6  更新 MEMORY.md
          - memory/MEMORY.md 加一行記錄新增咗乜
```

### 範例

```
新增 memory/WEEKLY_REVIEW.md:

1. 建立檔案，寫頭部
2. PATH_CONFIG 加:
   PATH_WEEKLY_REVIEW = {ROOT}/memory/WEEKLY_REVIEW.md
3. MEMORY.md 加:
   - 2026-03-02: 新增 WEEKLY_REVIEW.md（每週策略回顧）
```

---

## OP-2: 新增 Agent

### Checklist

```
□ Step 1  建立 Agent 目錄
          agents/{agent_name}/
          ├── SOUL.md          → Agent 核心靈魂（必須）
          ├── config/          → Agent 設定（如需要）
          └── logs/            → Agent 日誌（如需要）

□ Step 2  更新 agents/AGENTS.md
          - 加入 Agent section（名稱、角色、模型、職責）
          - 定義寫入權限（邊啲檔案可以寫）
          - 定義讀取範圍

□ Step 3  更新 routing/MODEL_ROUTER.md
          - Sub-task 分配表加入新 agent
          - 決定用邊個 Tier
          - 更新成本估算

□ Step 4  更新 PATH_CONFIG.md
          - 加入新 agent 嘅所有路徑

□ Step 5  如果有 Cron Job → 執行 OP-3

□ Step 6  測試
          - 確認 agent 能讀到所需檔案
          - 確認唔會同現有 agent 寫入衝突
```

### 寫入衝突防護

```
黃金規則: 一個檔案最多兩個 writer

現有 writer mapping:
  SCAN_CONFIG.md    → light-scan（部分）+ trader-cycle（全部）
  TRADE_STATE.md    → trader-cycle 專用
  TRADE_LOG.md      → trader-cycle 專用
  SCAN_LOG.md       → light-scan + trader-cycle
  MEMORY.md         → memory-keeper 專用

新 agent 唔可以寫入已有 writer 嘅檔案，除非：
  a) 替代原有 writer 嘅職責
  b) 寫入唔同嘅 section/欄位（明確標示）
```

---

## OP-3: 新增 Cron Job

### Checklist

```
□ Step 1  定義 Cron 規格
          - Schedule: every Xmin / Xh
          - Model: 查 MODEL_ROUTER.md 選 Tier
          - Session: main 或 isolated
          - Timeout: 秒數（isolated 必填）

□ Step 2  寫 Payload
          - 明確指定讀取範圍（"Read ONLY X.md"）
          - 明確指定寫入範圍
          - 唔可以寫入超出 AGENTS.md 定義嘅檔案
          - 用相對路徑（唔寫死 /Users/...）

□ Step 3  更新 CRON_PAYLOADS.md
          - 加入完整 payload section
          - 底部重建指令 section 加入新 cron

□ Step 4  更新 MODEL_ROUTER.md
          - Sub-task 分配表加入
          - 重新計算成本估算
          - 檢查熔斷規則是否需要調整

□ Step 5  更新 MEMORY.md
          - Active Cron Jobs 表格加入

□ Step 6  實際建立 Cron
          openclaw cron add --name "NAME" \
            --schedule "every Xmin" \
            --model "MODEL" \
            --session isolated \
            --timeout Xs \
            --payload "PAYLOAD"

□ Step 7  驗證
          openclaw cron list → 確認出現
          等一個 cycle → 確認有輸出
```

---

## OP-4: 新增交易對

### Checklist

```
□ Step 1  驗證 Aster DEX 支持
          GET /fapi/v1/exchangeInfo → 確認 symbol 存在
          記錄: 精度、最小下單量、lot size

□ Step 2  更新以下檔案（全部）：

  agents/trader/config/SCAN_CONFIG.md:
    - [PRICES] 加入 PAIR_price / PAIR_price_ts
    - [ATR] 加入 PAIR_ATR
    - [SR_LEVELS] 加入 PAIR_support / PAIR_resistance
    - [SR_ZONES] 加入 PAIR_support_zone / PAIR_resistance_zone
    - [FUNDING] 加入 PAIR_funding_last

  agents/trader/EXCHANGE_CONFIG.md:
    - 交易對設定表格加入新 pair

  core/STRATEGY.md:
    - 交易對特殊規則加入（如有）
    - Scalp 時間窗口確認（如適用）

  protocols/NEWS_SOURCES.md:
    - 信號解讀 section 確認覆蓋

  CRON_PAYLOADS.md:
    - light-scan STEP B 嘅 pair 列表加入
    - trader-cycle ANALYSIS 嘅 pair 列表加入

  memory/MEMORY.md:
    - 記錄新增交易對
```

---

## OP-5: 修改現有檔案

### 安全規則

```
RULE 1: 先讀後改
        → 讀完整檔案，理解 context，再改

RULE 2: 最小改動
        → 只改需要改嘅部分，唔重寫整個檔案
        → 用 Edit（old_string → new_string），唔用 Write 覆蓋

RULE 3: 根目錄同步
        → 如果改嘅係有根目錄副本嘅檔案（見下），兩邊都要改
        → 同步清單:
           config/IDENTITY.md ↔ IDENTITY.md
           config/USER.md     ↔ USER.md
           config/TOOLS.md    ↔ TOOLS.md
           protocols/HEARTBEAT.md ↔ HEARTBEAT.md
        → 唔需要同步（各自獨立）:
           core/SOUL.md ≠ SOUL.md（角色唔同）
           agents/AGENTS.md ≠ AGENTS.md（用途唔同）

RULE 4: 版本標記
        → 改完更新檔案頭部嘅版本日期

RULE 5: 備份同步
        → 重要修改後 rsync 到 backup:
           rsync -av {ROOT}/ ~/002.openclaw/openclaw/workspace/
```

---

## OP-6: 刪除 / 搬移檔案

### ⚠️ 高危操作

```
RULE: 唔建議刪除，建議 deprecate

Step 1  將檔案頂部加:
        # ⚠️ DEPRECATED — 此檔案已棄用
        # 替代: [新檔案路徑]
        # 棄用日期: YYYY-MM-DD

Step 2  PATH_CONFIG.md 加 comment:
        # DEPRECATED: PATH_XXX = {ROOT}/old/FILE.md → 已由 PATH_YYY 替代

Step 3  等 7 日確認無 agent 仲讀緊先刪除

搬移 = 新建 + deprecate 舊
```

---

## OP-7: 新增 Python 工具

### Checklist

```
□ Step 1  建立 tools/{tool_name}.py
          - 必須有 main() 函數
          - 必須有錯誤處理（try/except）
          - 唔寫死 API keys（從環境變數或 .env 讀）

□ Step 2  更新 PATH_CONFIG.md
          PATH_TOOL_NAME = {ROOT}/tools/{tool_name}.py

□ Step 3  更新 config/TOOLS.md（根目錄 + 子目錄）

□ Step 4  如需新 Python 依賴
          → 更新 requirements.txt
          → pip install 確認
```

---

## 快速參考卡

```
┌────────────────────────────────────────────────┐
│           WORKSPACE_OPS 快速 Checklist          │
├────────────────────────────────────────────────┤
│                                                │
│  ✋ 改之前:                                     │
│     □ 讀 PATH_CONFIG.md                        │
│     □ 讀 MODEL_ROUTER.md                       │
│     □ 讀 agents/AGENTS.md                      │
│                                                │
│  ✏️ 改嘅時候:                                   │
│     □ 唔寫死路徑（用 {ROOT}）                    │
│     □ 唔寫死模型（查 MODEL_ROUTER）              │
│     □ 唔違反寫入權限                             │
│     □ 檔案頭部加版本                             │
│                                                │
│  ✅ 改完之後:                                    │
│     □ 更新 PATH_CONFIG.md                       │
│     □ 更新 MEMORY.md                            │
│     □ 根目錄需要同步？                            │
│     □ rsync backup                              │
│                                                │
│  ❌ 絕對唔做:                                    │
│     □ 直接刪除檔案（用 deprecate）                │
│     □ 同時改 >3 個檔案嘅結構                     │
│     □ 改 PATH_CONFIG 嘅 ROOT 定義               │
│     □ 用 claude-3-haiku-20240307                │
│                                                │
└────────────────────────────────────────────────┘
```

---

## 版本歷史

| 日期 | 變更 |
|------|------|
| 2026-03-02 | 初版建立 — 覆蓋 OP-1 至 OP-7 |
