<p align="center">
  <h1 align="center">🦞 OpenClaw</h1>
  <p align="center">
    本地 AI 加密貨幣交易系統 — Telegram Bot + 自動交易 + 智能分析
    <br />
    你嘅 key 永遠唔會離開你部機。
    <br /><br />
    <a href="#-快速開始"><strong>快速開始 »</strong></a>
    &nbsp;&nbsp;·&nbsp;&nbsp;
    <a href="#-功能一覽"><strong>功能 »</strong></a>
    &nbsp;&nbsp;·&nbsp;&nbsp;
    <a href="#-常見問題"><strong>FAQ »</strong></a>
  </p>
</p>

---

## 目錄

- [簡介](#-簡介)
- [功能一覽](#-功能一覽)
- [快速開始](#-快速開始)
- [設定 API Keys](#-設定-api-keys)
- [啟動](#-啟動)
- [使用指南](#-使用指南)
- [環境變數](#-環境變數)
- [架構](#-架構)
- [常見問題](#-常見問題)
- [成本](#-成本)
- [License](#license)

---

## 📖 簡介

OpenClaw 係一個**完全本地運行**嘅 AI 加密貨幣交易系統，連接 [Aster DEX](https://asterdex.com) 合約交易所。

**兩種使用方式：**

| 模式 | 適合 | 包含 |
|------|------|------|
| **🤖 AXC Standalone** | 想用 Telegram Bot 查倉落單 | Telegram Bot + AI 分析 + 落單 |
| **🦞 Full OpenClaw** | 想要自動交易 + Dashboard | 以上全部 + 9 AI Agents + Dashboard + 自動掃描 |

> 大部分朋友用 **AXC Standalone** 就夠。以下指南以 AXC 為主。

---

## ✨ 功能一覽

### 查詢（零 AI 成本，直接讀交易所）

| Command | 功能 |
|---------|------|
| `/pos` | 查持倉（入場價、標記價、未實現盈虧） |
| `/bal` | USDT 餘額 + 今日盈虧 |
| `/pnl` | 已實現盈虧、資金費、手續費 |
| `/report` | 完整交易報告（一次睇晒） |
| `/sl` | 查止損單 |
| `/sl breakeven` | 移動止損到入場價（保本） |
| `/health` | 系統狀態檢查 |

### AI 分析（需要 `PROXY_API_KEY`）

| Command | 功能 |
|---------|------|
| `/ask <問題>` | AI 分析市場（結合你嘅持倉 + RAG 記憶） |
| 自然語言 | 「做多 ETH $50」→ 確認 → 自動落單 |
| 自動推送 | 倉位平倉時自動生成 AI 報告 |

### 自然語言落單示例

```
做多 ETH $50          →  ETHUSDT LONG $50
做空 BTC $100 SL 2%   →  BTCUSDT SHORT $100, 止損 2%
平倉 ETH              →  關閉 ETHUSDT 持倉
all in ETH             →  全倉做多 ETHUSDT
```

> 所有落單都有二次確認，唔會直接執行。高風險訂單（≥80% 餘額）有額外警告。

---

## 🚀 快速開始

### 1. 下載

```bash
# 方法 A：Clone（推薦，方便更新）
git clone https://github.com/Will-852/AXC-TradingBot.git
cd AXC-TradingBot

# 方法 B：Download ZIP
# 右上角綠色 Code 按鈕 → Download ZIP → 解壓
```

### 2. 安裝 Python

| 平台 | 安裝方法 |
|------|----------|
| macOS | `brew install python3` |
| Windows | [python.org/downloads](https://python.org/downloads/) → **勾選 "Add to PATH"** |
| Linux | `sudo apt install python3 python3-pip` |

確認版本（需要 **3.9+**）：
```bash
python3 --version
```

### 3. 安裝依賴

```bash
pip install -r axc_requirements.txt
```

> Windows 用 `pip` 而唔係 `pip3`。如果 `pip` 指令搵唔到，試 `python -m pip install -r axc_requirements.txt`

### 4. 設定 API Keys

```bash
cp secrets/.env.example secrets/.env
```

用任何文字編輯器打開 `secrets/.env`，填入你嘅 keys：

```env
TELEGRAM_BOT_TOKEN=你嘅token
TELEGRAM_CHAT_ID=你嘅chatid
ASTER_API_KEY=你嘅key
ASTER_API_SECRET=你嘅secret
```

> 唔知點攞呢啲 key？ → [詳細教學](#-設定-api-keys)

### 5. 啟動

```bash
# macOS / Linux
AXC_HOME=$(pwd) python3 scripts/tg_bot.py
```

```cmd
:: Windows CMD
set AXC_HOME=%cd%
python scripts\tg_bot.py
```

啟動成功你會見到：
```
🦞 OpenClaw Telegram v2.0 啟動
  Chat ID: 你嘅chat_id
```

去 Telegram 同你嘅 Bot 講 **`/start`** 🎉

---

## 🔑 設定 API Keys

### Telegram Bot Token

> ⚠️ 每位用家必須建立自己嘅 Bot，唔可以共用 Token。兩人用同一個 Token 會出 409 Conflict 錯誤。

1. 打開 Telegram，搵 **[@BotFather](https://t.me/BotFather)**
2. Send `/newbot`
3. 改個名（例如 `My Trading Bot`）
4. 改個 username（例如 `my_trading_123_bot`，必須以 `bot` 結尾）
5. BotFather 會回覆一個 **token**（格式：`123456789:ABC-DEFghijklmnop...`）
6. 複製貼到 `.env` 嘅 `TELEGRAM_BOT_TOKEN=`

### Telegram Chat ID

1. 打開 Telegram，搵 **[@userinfobot](https://t.me/userinfobot)**
2. Send `/start`
3. 佢會回覆你嘅 **ID**（一串數字，例如 `123456789`）
4. 複製貼到 `.env` 嘅 `TELEGRAM_CHAT_ID=`

> 🔒 **安全**：Bot 只會回應呢個 Chat ID，其他人嘅訊息會被靜默忽略。

### Aster DEX API Keys

1. 去 **[asterdex.com](https://asterdex.com)** → 登入（或註冊）
2. **Settings** → **API Management** → **Create API Key**
3. 權限設定：**開啟 Futures Trading**
4. 複製 **API Key** → 貼到 `.env` 嘅 `ASTER_API_KEY=`
5. 複製 **Secret Key** → 貼到 `.env` 嘅 `ASTER_API_SECRET=`

> ⚠️ **安全提示**：唔好開「提幣」權限。API key 淨係需要 Futures Trading 就夠。

### Claude API Key（選填，AI 功能用）

冇呢個 key，`/ask` 同自然語言落單唔會用到，但所有查詢指令（`/pos` `/bal` `/pnl`）照常運作。

填入 `.env`：
```env
PROXY_API_KEY=你嘅key
PROXY_BASE_URL=https://tao.plus7.plus/v1
```

---

## 🖥 啟動

### macOS / Linux

```bash
AXC_HOME=$(pwd) python3 scripts/tg_bot.py
```

### Windows — CMD

```cmd
set AXC_HOME=%cd%
python scripts\tg_bot.py
```

### Windows — PowerShell

```powershell
$env:AXC_HOME = (Get-Location).Path
python scripts\tg_bot.py
```

### 後台運行（macOS / Linux）

```bash
AXC_HOME=$(pwd) nohup python3 scripts/tg_bot.py > logs/tg_bot.log 2>&1 &
```

查看 log：
```bash
tail -f logs/tg_bot.log
```

停止：
```bash
pkill -f tg_bot.py
```

---

## 📘 使用指南

### 查持倉

Send `/pos` 到你嘅 Bot：
```
📊 POSITIONS · 2026-03-08 15:30 UTC+8

ETHUSDT LONG  50.0 USDT
  Entry  $2,150.00  Mark $2,180.00
  PnL    +$12.50 (+1.4%)
  SL     $2,100.00
```

### 自然語言落單

直接打字（唔使 `/` 開頭）：
```
做多 ETH $50
```

Bot 會顯示確認：
```
📋 確認落單：
  方向：LONG
  幣對：ETHUSDT
  金額：$50 USDT
  槓桿：10x
  止損：-2.5%
  止盈：+4.0%

  [✅ 確認]  [❌ 取消]
```

撳 ✅ 先會真正落單。60 秒後自動取消。

**支援嘅講法：**
- `做多 ETH $50` / `long ETH 50u`
- `做空 BTC $100` / `short BTC 100u`
- `平倉 ETH` / `close ETH`
- `做多 ETH $50 SL 2%` — 自訂止損
- `all in ETH` — 全倉做多

### AI 分析

```
/ask BTC 短期走勢如何？
```

Bot 會結合你嘅持倉、歷史交易記錄（RAG 記憶）同實時價格生成分析。

### 自動平倉報告

當倉位被平倉（止損/止盈觸發），Bot 會自動推送報告：
```
📊 平倉報告 · ETHUSDT

方向：LONG → 已平
入場：$2,150.00
平倉：$2,100.00
盈虧：-$25.00 (-2.3%)
持倉時間：4h 32m

💡 分析：價格跌穿支撐位觸及止損...
```

---

## 📋 環境變數

| 變數 | 必填 | 用途 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram Bot Token（[@BotFather](https://t.me/BotFather)） |
| `TELEGRAM_CHAT_ID` | ✅ | 你嘅 Chat ID（白名單，其他人用唔到） |
| `ASTER_API_KEY` | ✅ | Aster DEX API Key |
| `ASTER_API_SECRET` | ✅ | Aster DEX API Secret |
| `PROXY_API_KEY` | 選填 | Claude API Key（`/ask` + 自然語言落單） |
| `PROXY_BASE_URL` | 選填 | API endpoint（預設 `https://tao.plus7.plus/v1`） |
| `VOYAGE_API_KEY` | 選填 | Voyage AI embedding（RAG 記憶增強，冇就用 hash fallback） |

---

## 🏗 架構

```
你 (Telegram)
  │
  ▼
tg_bot.py ─────── Telegram Bot 主控
  ├── slash_cmd.py ──── 查詢指令（/pos /bal /pnl — 零 AI）
  ├── aster_client.py ── Aster DEX API（落單、查倉、止損）
  ├── Claude API ─────── AI 分析 + 自然語言理解
  └── memory/ ──────── RAG 記憶系統（歷史對話 + 交易記錄）
```

### 檔案結構

```
openclaw/
├── scripts/
│   ├── tg_bot.py                # Telegram Bot 主程式
│   ├── slash_cmd.py             # 查詢指令處理
│   ├── axc_client.py            # OpenClaw API client
│   └── trader_cycle/exchange/
│       ├── aster_client.py      # Aster DEX 交易 client
│       └── exceptions.py        # 交易異常定義
├── memory/
│   ├── writer.py                # 寫入記憶
│   ├── retriever.py             # RAG 搜索
│   └── embedder.py              # 向量嵌入
├── secrets/
│   ├── .env.example             # 環境變數模板
│   └── .env                     # 你嘅 API keys（唔會上傳 git）
├── shared/                      # 運行時數據
├── logs/                        # 日誌檔案
├── requirements.txt             # Python 依賴
├── QUICKSTART.md                # 極簡上手指南
└── README.md
```

### 安全

- 所有運算喺你本地執行
- API keys 只存喺 `secrets/.env`（已 gitignore）
- 交易需要你主動設定 exchange API key
- Bot 只回應你嘅 Chat ID，其他人靜默忽略
- 所有落單需要二次確認

---

## ❓ 常見問題

<details>
<summary><b>Bot 冇反應 / Telegram 報 409 Conflict</b></summary>

**原因**：有多個 Bot instance 同時運行，爭住讀 Telegram 更新。

**解決**：
```bash
# 殺掉所有 tg_bot 進程
pkill -f tg_bot.py

# 等幾秒再重新啟動
AXC_HOME=$(pwd) python3 scripts/tg_bot.py
```

Windows：
```cmd
taskkill /F /IM python.exe
```
</details>

<details>
<summary><b>pip install 失敗 / ImportError: No module named 'telegram'</b></summary>

**解決**：
```bash
# 確認用正確嘅 pip
python3 -m pip install -r axc_requirements.txt

# 如果權限問題
python3 -m pip install --user -r requirements.txt
```

Windows 用 `python` 唔係 `python3`：
```cmd
python -m pip install -r axc_requirements.txt
```
</details>

<details>
<summary><b>ASTER_API_KEY/ASTER_API_SECRET missing</b></summary>

**原因**：`.env` 未建立或 key 未填。

**解決**：
```bash
# 確認 .env 存在
ls secrets/.env

# 如果唔存在，從 example 複製
cp secrets/.env.example secrets/.env

# 打開編輯
nano secrets/.env
```
</details>

<details>
<summary><b>/ask 回覆好慢（>10 秒）</b></summary>

- 問短啲嘅問題
- 用 `/forget` 清除對話記憶
- 確認 `PROXY_BASE_URL` 正確
- 檢查網絡連接
</details>

<details>
<summary><b>/mode 或 /pause 顯示「需要 OpenClaw 環境」</b></summary>

呢啲指令需要完整 OpenClaw 系統（有 `config/params.py`）。

Standalone 模式唔支援，但**唔影響**查詢同落單功能。
</details>

<details>
<summary><b>Windows 啟動失敗</b></summary>

逐步檢查：
1. `python --version` → 需要 3.9+
2. 安裝時有冇勾選 **Add to PATH**？
3. 用 `python` 唔係 `python3`
4. PowerShell 執行 policy：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
5. 路徑用 `\` 唔係 `/`
</details>

<details>
<summary><b>點樣更新？</b></summary>

```bash
# Git clone 方式
git pull

# ZIP 方式
# 重新下載 → 解壓覆蓋 → 保留你嘅 secrets/.env
```
</details>

<details>
<summary><b>安全嗎？會唔會洩露我嘅 API key？</b></summary>

- API keys 只存喺你本地嘅 `secrets/.env`
- `.env` 已加入 `.gitignore`，唔會被 git 上傳
- Bot 唔會將 key 發送去任何第三方
- 交易所 API key 建議只開 **Futures Trading** 權限，唔好開提幣
</details>

---

## 💰 成本

| 操作 | 成本 |
|------|------|
| `/pos` `/bal` `/pnl` `/report` `/sl` `/health` | 免費 |
| `/ask`（短問題） | ~$0.001 |
| 自然語言落單 | ~$0.001 |
| 自動平倉報告 | ~$0.002 |
| **每日活躍使用估算** | **~$0.02/日** |

> 冇設定 `PROXY_API_KEY` 嘅話，所有 AI 功能唔會產生費用，查詢功能照常免費使用。

---

## 🤝 共同開發指南

> 如果你用 LLM（ChatGPT / Claude / Cursor）輔助開發，將呢個 section 貼畀佢就夠。

### 環境設定（一次性）

```bash
# 1. Clone
git clone https://github.com/Will-852/AXC-TradingBot.git ~/.openclaw
cd ~/.openclaw

# 2. 安裝依賴
pip3 install -r requirements.txt

# 3. 建立 secrets（唔會被 git 追蹤）
mkdir -p secrets
cp docs/friends/.env.example secrets/.env
# 用編輯器填入你嘅 API keys

# 4. 自訂交易參數（選填，唔會被 git 追蹤）
cp config/user_params.py.example config/user_params.py
# 改你想 override 嘅值，其餘用預設
```

### 檔案修改規則

```
✅ 可以改（你嘅本地檔案，gitignored）
  secrets/.env              ← 你嘅 API keys
  config/user_params.py     ← 你嘅交易參數 override

⚠️ 唔好直接改（會被 git pull 覆蓋）
  config/params.py          ← 共用預設值，改 user_params.py 代替
  scripts/*.py              ← 共用邏輯，有需要開 issue 討論

📖 唯讀參考
  docs/                     ← 文檔
  ai/                       ← AI agents 上下文
  agents/*/SOUL.md          ← Agent 人格定義
```

### 更新流程

```bash
cd ~/.openclaw
git pull                    # 攞最新 code
# secrets/.env 同 config/user_params.py 唔受影響
```

如果 `git pull` 有衝突（你改咗唔應該改嘅檔案）：
```bash
git stash                   # 暫存你嘅改動
git pull                    # 更新
git stash pop               # 還原改動，手動解決衝突
```

### 架構速查（畀 LLM 讀）

```
~/.openclaw/
├── scripts/           # 所有可執行程式
│   ├── tg_bot.py      #   Telegram Bot 入口
│   ├── dashboard.py   #   Web Dashboard（port 5555）
│   ├── async_scanner.py #  市場掃描器
│   └── trader_cycle/  #   自動交易引擎
├── config/
│   ├── params.py      #   共用參數（唔好直接改）
│   ├── user_params.py #   你嘅 override（gitignored）
│   └── modes/         #   交易模式定義
├── agents/            # 9 個 AI agents，各自有 SOUL.md
├── canvas/            # Dashboard 前端 HTML
├── memory/            # RAG 記憶系統（jsonl + npy）
├── secrets/.env       # API keys（gitignored）
├── shared/            # 運行時狀態檔案
└── logs/              # 日誌
```

### 常用指令

```bash
# Dashboard（瀏覽器打開 http://127.0.0.1:5555）
python3 scripts/dashboard.py

# Telegram Bot
AXC_HOME=~/.openclaw python3 scripts/tg_bot.py

# 掃描器
python3 scripts/async_scanner.py

# 系統健康檢查
bash scripts/health_check.sh
```

### 技術棧

| 層面 | 技術 |
|------|------|
| 語言 | Python 3.9+ |
| AI 推理 | Claude API（經 proxy） |
| 向量嵌入 | Voyage AI（voyage-3） |
| 記憶儲存 | jsonl + numpy（唔用資料庫） |
| 交易所 | Aster DEX / Binance |
| 介面 | Telegram Bot + 本地 Web Dashboard |

---

## License

MIT

---

<p align="center">
  Made with 🦞 by <a href="https://github.com/Will-852">@Will-852</a>
  <br />
  <sub>Architecture, code & docs co-developed with <a href="https://claude.ai">Claude</a> (Anthropic)</sub>
</p>
