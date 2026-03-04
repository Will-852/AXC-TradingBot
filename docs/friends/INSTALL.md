# OpenClaw 安裝指南

**支援：** macOS | **Python：** 3.11+

## 前置要求
- [ ] Python 3.11+（`python3 --version`）
- [ ] Binance 帳號 + API Key（現貨交易權限）
- [ ] Anthropic API Key（console.anthropic.com）

## 安裝步驟

### 1. 複製系統
```bash
git clone [REPO_URL] ~/.openclaw
```

### 2. 安裝依賴
```bash
pip3 install binance-connector anthropic \
             requests psutil python-dotenv \
             --break-system-packages
```

### 3. 啟動 Dashboard
```bash
python3 ~/.openclaw/scripts/dashboard.py &
open http://127.0.0.1:5555
```

### 4. 連接 Binance
在 Dashboard sidebar 找到「平台連接」
點擊「連接 Binance」按鈕
輸入你的 API Key + Secret
點擊「驗證並連接」

### 5. Binance API Key 安全設定
✅ 開啟：讀取帳戶資訊、現貨交易
❌ 關閉：提款權限（重要！）
✅ 開啟：IP 白名單（填你的家居 IP）

## 功能狀態
| 功能 | 狀態 |
|------|------|
| Dashboard 監控 | ✅ 可用 |
| Binance 市場數據 | ✅ 可用 |
| 掃描訊號分析 | ✅ 可用 |
| Telegram 通知 | ✅ 可用 |
| Binance 自動下單 | ⏳ 開發中 |

## ⚠️ 安全提示
- API Key 只存於本機 ~/.openclaw/secrets/.env
- 絕不要把 .env 上傳至 Git 或分享給任何人
- 建議開啟 Binance IP 白名單
