# OpenClaw — 文件分類公理
> 位置：docs/architecture/TAXONOMY.md
> 更新觸發：加新頂層文件夾類型時
> 日常加文件唔需要更新呢份文件

## 新增任何文件時，用呢個判斷樹

```
這個文件係...
│
├── Claude Code / AI Agent 讀嘅上下文？
│   └── → ai/
│       ├── 系統概覽入口     → ai/CONTEXT.md
│       ├── 近期狀態快照     → ai/MEMORY.md（backup自動更新）
│       ├── 行為規則         → ai/RULES.md
│       └── 交易策略規則     → ai/STRATEGY.md（weekly自動更新）
│           ⚠️ ai/ 所有文件只引用 docs/，唔複製內容
│
├── 給人類讀嘅文檔？
│   └── → docs/
│       ├── 安裝/環境/恢復？  → docs/setup/
│       ├── 日常操作指南？    → docs/guides/
│       ├── 系統設計/決策？   → docs/architecture/
│       └── 外部評測用？      → docs/friends/
│
├── AI Agent 嘅邏輯/人格？
│   └── → agents/[名稱]/workspace/SOUL.md
│       每個 agent = 獨立插件，新增唔影響其他
│
├── 執行腳本（Python/Bash）？
│   └── → scripts/
│       判斷標準：需要 LLM 思考 = agent；唔需要 = script
│
├── 參數/設定？
│   ├── 敏感（API key / token / secret）？ → secrets/.env
│   └── 非敏感（trading params / 閾值）？  → config/params.py
│
├── 運行時產生嘅數據？
│   ├── Agent 間通信/信號？ → shared/
│   ├── 對話/交易記憶/向量？→ memory/
│   ├── 日誌？              → logs/
│   └── 備份？              → backups/
│
└── 開發過程記錄？
    └── → DEV_LOG.md（根目錄唯一其他 .md）
```

## 插件化範例

### 加新 Agent（例：news_agent）
```
mkdir -p agents/news_agent/workspace
建立 agents/news_agent/workspace/SOUL.md
→ 完成，唔改任何現有文件
```

### 加新交易模型參數
```
config/params.py 加一行：BINANCE_LEVERAGE = 3
→ 完成，唔新建文件
```

### 加新操作指南
```
docs/guides/BINANCE_SETUP.md
docs/README.md 加一行索引
→ 完成
```

### 加新平台整合（例：Binance）
```
agents/binance_trader/workspace/SOUL.md  ← agent邏輯
scripts/binance_executor.py              ← 執行腳本
config/params.py                         ← 加 BINANCE_* 參數
secrets/.env                             ← 加 BINANCE_API_KEY
docs/guides/BINANCE.md                   ← 操作指南
docs/architecture/AGENTS.md              ← 更新職責說明
→ 每層各加一個，唔影響其他任何嘢
```

## ⚠️ 系統管理文件夾（Binary 自動管理，唔動）

以下文件夾由 OpenClaw binary 自動建立和管理。
唔係用戶代碼，唔可以移動、改名、刪除或加入自己嘅文件。

| 文件夾 | 用途 | 管理者 |
|--------|------|--------|
| `completions/` | LLM response cache | Binary 自動清理 |
| `cron/` | 定時任務配置 | Binary 內部 |
| `delivery-queue/` | 訊息投遞隊列 | Binary 消費 |
| `devices/` | 設備認證記錄 | Binary 寫入 |
| `identity/` | 身份認證信息 | Binary 管理 |
| `workspace/` | 臨時工作空間 | Binary 清理 |
| `credentials/` | 交易所證書緩存 | Binary 刷新 |

**判斷規則：**
如果唔確定一個文件夾係咪 binary 管理，執行：
```
grep -r "[folder_name]" ~/projects/axc-trading/scripts/ --include="*.py" -l
```
如果 zero results = binary 管理，唔動。
如果有 results = 用戶代碼，按判斷樹處理。

**新增文件夾時：**
先執行上述 grep，確認來源。
Binary 管理嘅文件夾加入呢個表格。
用戶代碼嘅文件夾按判斷樹分類。

## 長期維護規則

1. 新增文件前先查此判斷樹
2. ai/ 文件只寫「見 docs/xxx」唔複製內容
3. 改名前先 grep，確認零引用才刪舊文件
4. CLAUDE.md 每次修改後 wc -l，超200行立即精簡
5. 新 agent = 新文件夾，唔改任何現有文件
6. 新參數 = 加入 config/params.py，唔新建文件
7. 新敏感設定 = 加入 secrets/.env，唔新建文件
