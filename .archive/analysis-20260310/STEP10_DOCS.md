# Step 10: `docs/` — 文檔圖書館（按需查閱）
> talk12 風格分析 | 2026-03-10

## 點樣搵到
```
axc-trading → docs/
├── README.md                  ← 文件總索引 ⭐ 入口
├── GUIDE.md                   ← 總導覽
├── DASHBOARD_GUIDE.md         ← Dashboard 用法
│
├── setup/                     ← 安裝 + 恢復
│   ├── INSTALL.md             ← 完整安裝步驟
│   ├── ENV_SETUP.md           ← API keys 設定
│   └── RECOVERY.md            ← 換機/文件遺失恢復
│
├── guides/                    ← 日常操作（16 個教學 + 4 個指南）
│   ├── 00-install.md          ← 安裝
│   ├── 01-what-is-openclaw.md ← 系統介紹
│   ├── 02-how-it-works.md     ← 運作原理
│   ├── 03-dashboard-guide.md  ← Dashboard 教學
│   ├── 04-trading-modes.md    ← 交易模式
│   ├── 05-risk-control.md     ← 風控教學
│   ├── 06-telegram-commands.md← TG 指令
│   ├── 07-api-key-setup.md    ← API key 設定
│   ├── 08-terminal-commands.md← 終端指令
│   ├── 09-faq.md              ← 常見問題
│   ├── 10-layers-explained.md ← 層次解釋
│   ├── 11-agents.md           ← Agent 教學
│   ├── 12-scripts.md          ← Scripts 教學
│   ├── 13-launchagents.md     ← LaunchAgent 教學
│   ├── 14-data-flow.md        ← 數據流教學
│   ├── 15-dashboard-api.md    ← Dashboard API
│   ├── OPS.md                 ← 維運操作（Proxy/Key Rotate）
│   ├── BACKUP.md              ← 備份說明
│   ├── SYMBOLS.md             ← 加幣種操作 ⭐
│   └── TELEGRAM.md            ← Bot 完整指令
│
├── architecture/              ← 系統設計
│   ├── ARCHITECTURE.md        ← AI stack 選型
│   ├── AGENTS.md              ← Agent 職責
│   ├── AXC.md                 ← AXC 系統描述
│   ├── BOUNDARY.md            ← 系統邊界
│   ├── ROADMAP.md             ← 發展路線圖
│   └── TAXONOMY.md            ← 文件分類判斷樹 ⭐
│
├── indicators/                ← 指標研究
│   ├── README.md              ← 索引
│   ├── entry_indicators.md    ← BB/RSI/MACD/STOCH/EMA/ADX
│   ├── crypto_specific.md     ← Funding/OI/CVD/MVRV/NVT
│   ├── evaluation_metrics.md  ← Sharpe/Kelly/Drawdown
│   ├── volume_and_structure.md← ATR/OBV/VWAP/Fibonacci
│   ├── params_reference.md    ← params.py vs 業界標準
│   └── yunis-collection/      ← 8 個 TradingView 指標深度分析
│       ├── README.md
│       ├── 01-atr-keltner-channel.md
│       ├── 02-nexus-flow-elite.md
│       ├── 03-macd-pro.md
│       ├── 04-volume-sync-price-flow.md
│       ├── 05-vista-pro.md
│       ├── 06-trend-sync.md
│       ├── 07-volt-pro.md
│       └── 08-risk-management.md
│
├── friends/                   ← 外部評測用
│   ├── INSTALL.md             ← 簡化安裝指南
│   ├── .env.example           ← 環境變數範本
│   └── OPENCLAW_INTEGRATION.md← 整合說明
│
└── analysis-20260310/         ← ⭐ 你而家讀緊嘅分析（本系列）
    ├── ARCHITECTURE.md
    ├── FOLDER_GUIDE.md
    ├── STEP1_CONFIG.md → STEP10_DOCS.md
```

**共 ~50 個文件。** 分 7 個子目錄。

---

## 比喻

**圖書館：** 唔使全部睇。你有問題先去搵對應嘅書。

| 你想做咩 | 去邊 |
|----------|------|
| 裝系統 | setup/ |
| 日常操作 | guides/ |
| 理解設計 | architecture/ |
| 研究指標 | indicators/ |
| 加幣種 | guides/SYMBOLS.md |
| 改 Proxy | guides/OPS.md |
| 畀人試用 | friends/ |
| 理解全局架構 | analysis-20260310/ ← 你而家做緊嘅 |

---

## 7 個子目錄

### 1. `setup/` — 安裝恢復（3 個文件）

**幾時用：** 裝新機、換電腦、系統損壞。

| 文件 | 用途 |
|------|------|
| INSTALL.md | 從零安裝（pip、LaunchAgent、env） |
| ENV_SETUP.md | 9 個 API key 點攞、點填 |
| RECOVERY.md | 換機步驟（備份 → 恢復 → 驗證） |

### 2. `guides/` — 日常操作（20 個文件）⭐

**幾時用：** 日常操作遇到問題。

**教學系列 00-15：** 由淺入深嘅完整教學，cover 安裝到 Dashboard API。

**操作指南：**
| 文件 | 重點內容 |
|------|---------|
| OPS.md | Proxy 切換（一鍵）、Key Rotate、TG bot 重複 Instance 處理 |
| BACKUP.md | 自動備份機制、手動恢復 |
| SYMBOLS.md | 加幣種步驟（改 params.py → 重啟 scanner） |
| TELEGRAM.md | Bot 所有指令完整列表 |

### 3. `architecture/` — 系統設計（6 個文件）

**幾時用：** 想理解「點解」而唔係「點做」。

| 文件 | 核心內容 |
|------|---------|
| ARCHITECTURE.md | AI stack 選型（點解 Claude、voyage-3、numpy） |
| AGENTS.md | 10 個 agent 嘅職責分工 |
| AXC.md | AXC 系統整體描述 |
| BOUNDARY.md | 系統邊界定義（AXC vs OpenClaw） |
| ROADMAP.md | 已完成 + 未來計劃 |
| TAXONOMY.md | 文件分類判斷樹 ⭐（加新文件前必讀） |

### 4. `indicators/` — 指標研究（14 個文件）

**幾時用：** 想深入理解某個指標、或者研究新指標。

**基礎研究（5 個）：**
| 文件 | 內容 |
|------|------|
| entry_indicators.md | BB/RSI/MACD/STOCH/EMA/ADX 深度分析 + 冗餘度 |
| crypto_specific.md | Funding Rate/OI/CVD/MVRV/NVT/Fear&Greed |
| evaluation_metrics.md | Sharpe/Sortino/Kelly/Drawdown/Position Sizing |
| volume_and_structure.md | ATR/OBV/VWAP/Fibonacci/Ichimoku/MTF |
| params_reference.md | AXC params.py 數值 vs 業界標準對比 |

**Yunis Collection（8 個）：**
你用嘅 TradingView 指標嘅深度解構（talk12 風格）。
每個指標一頁：ATR Keltner → Nexus Flow → MACD PRO → VolumeSyncPriceFlow → VISTA PRO → TrendSync → VOLT PRO → Risk Management。

### 5. `friends/` — 外部評測（3 個文件）

**幾時用：** 想畀朋友試用系統。

包含簡化安裝指南 + `.env.example`（API key 模板，值係空嘅）。

### 6. `analysis-20260310/` — 本次分析

你而家做緊嘅 10 步架構分析。完成後呢度有：
```
ARCHITECTURE.md    ← 系統整體架構
FOLDER_GUIDE.md    ← 10 步指南
STEP1_CONFIG.md    ← config/params.py
STEP2_SHARED.md    ← shared/
STEP3_STRATEGIES.md ← strategies/
STEP3_QA.md        ← 代碼版本問答
STEP4_RISK.md      ← risk/
STEP5_EXCHANGE.md  ← exchange/
STEP6_SCRIPTS_ROOT.md ← scripts/ 根目錄
STEP7_AI.md        ← ai/
STEP8_AGENTS.md    ← agents/
STEP9_BACKTEST.md  ← backtest/
STEP10_DOCS.md     ← docs/（呢個文件）
```

---

## 關鍵文件

### TAXONOMY.md — 文件分類判斷樹

**比喻：** 郵差嘅分信手冊。每封信（新文件）根據內容分去唔同郵箱。

```
新文件係...
├── AI Agent 讀嘅？      → ai/
├── 人類讀嘅文檔？        → docs/
├── Agent 性格/邏輯？     → agents/*/SOUL.md
├── 執行腳本？            → scripts/
├── 參數/設定？            → config/ 或 secrets/
├── 運行時數據？           → shared/ / memory/ / logs/
└── 開發記錄？            → DEV_LOG.md
```

### SYMBOLS.md — 加幣種操作

**呢個同你而家嘅 SOL+XAU 問題直接相關。** 文件教你：
1. 改 `config/params.py` 嘅 `ASTER_SYMBOLS`
2. 重啟 scanner
3. 驗證

但佢只講咗 scanner 層。實際上 SOL+XAU 仲需要改 4 個位：
- `light_scan.py` PAIRS
- `slash_cmd.py` get_prices()
- `evaluate.py` PAIR_PRIORITY
- `settings.py` POSITION_GROUPS

---

## ⚠️ 分析中觀察到嘅特點

### 🟢 文檔結構清晰
TAXONOMY.md 判斷樹 + README.md 索引 = 新文件唔會放錯位。

### 🟢 教學系列完整
00-15 由安裝到 Dashboard API，cover 所有層面。

### 🟢 Yunis Collection 有獨立深度研究
8 個 TradingView 指標有完整 talk12 解構。呢個對理解策略背後嘅理論好有幫助。

### 🟡 SYMBOLS.md 唔完整
只教改 `params.py` + 重啟 scanner。漏咗 light_scan / slash_cmd / evaluate / settings 嘅更新。

### 🟡 ROADMAP.md 可能過時
需要檢查「未來計劃」section 有冇已經完成但未更新嘅項目。

---

## 自檢問題

1. **我要加新文件應該放邊？** → 先讀 TAXONOMY.md 判斷樹
2. **我要裝落新電腦？** → setup/INSTALL.md + ENV_SETUP.md
3. **我要研究某個指標？** → indicators/ 對應文件
4. **我要畀朋友試用？** → friends/ 有簡化版
5. **SYMBOLS.md 要唔要更新？** → 要。加入 light_scan/slash_cmd/evaluate/settings 嘅步驟
