<!--
title: 由零開始安裝 AXC
section: 安裝指南
order: 0
audience: human,github
-->

# 由零開始安裝 AXC Trading System

> 唔需要編程經驗。跟住步驟做，大約 10 分鐘完成。

## AXC 同 OpenClaw 嘅關係

- **AXC** = 呢個交易系統本身（掃描、策略、下單、風控）
- **OpenClaw** = 底層開源平台（提供 Agent 框架、Gateway、Telegram 橋接）
- 你安裝嘅係 AXC。OpenClaw 係引擎，好似 WordPress 同 WooCommerce 嘅關係

---

## 你需要準備什麼？

- 一台 **Mac 電腦**（macOS 12 或以上）
- 能上網
- 一個 **AI API Key**（見步驟 2）— 用嚟做新聞情緒分析 + Telegram 對話

---

## 步驟 1：下載 AXC

**方法 A：GitHub 直接下載（推薦）**

1. 打開瀏覽器，去：
   👉 **https://github.com/Will-852/AXC-TradingBot/releases/latest**
2. 揀 `axc-setup-vX.X.X.zip`，點擊下載
3. 下載完成後，打開 Mac 嘅 **Terminal**
   （按 Command+空格，輸入「Terminal」，按 Enter）

**方法 B：用 Dashboard 分享頁下載**

如果朋友已經安裝咗 AXC，叫佢打開：
http://localhost:5555/share → 點「下載 Setup Package」

---

## 步驟 2：申請 API Key

AXC 嘅部分功能需要 AI（見下方「邊啲功能用 LLM」）。如果你只想用自動交易，唔用 Telegram 對話同新聞分析，可以跳過呢步。

**用 Proxy（便宜，推薦新手）：**
1. 去 proxy 供應商網站申請（問介紹你嘅朋友）
2. 拎到一個 Key，長得好似：`sk-ant-xxxxxxxx`

**用官方 Claude API：**
1. 去 https://console.anthropic.com 注冊
2. 去 API Keys → 建立新 Key

---

## 步驟 3：解壓文件

打開 Terminal，**逐行複製貼上**，每行按一次 Enter：

```bash
# 建立資料夾
mkdir -p ~/projects/axc-trading

# 解壓（如果下載到 Downloads 資料夾）
unzip ~/Downloads/axc-setup-*.zip -d ~/projects/axc-trading/
```

---

## 步驟 4：設定你的 API Key

```bash
# 複製範例設定文件
cp ~/projects/axc-trading/secrets/.env.example ~/projects/axc-trading/secrets/.env

# 用文字編輯器打開（會彈出 nano 編輯器）
nano ~/projects/axc-trading/secrets/.env
```

你會看到這樣的畫面：

```
PROXY_API_KEY=
PROXY_BASE_URL=
...
```

用方向鍵移到 `PROXY_API_KEY=` 後面，填入你的 Key：

```
PROXY_API_KEY=你的key貼在這裡
PROXY_BASE_URL=https://你的proxy地址/v1
```

填完後：
- 按 **Ctrl+X**（退出）
- 按 **Y**（儲存）
- 按 **Enter**（確認）

---

## 步驟 5：安裝依賴

```bash
pip3 install -r ~/projects/axc-trading/requirements.txt
```

等待安裝完成（大約 1-2 分鐘），看到 `Successfully installed` 就係完成。

---

## 步驟 6：啟動！

```bash
cd ~/projects/axc-trading && python3 scripts/dashboard.py &
```

然後打開瀏覽器，去：
👉 **http://localhost:5555**

你應該看到 AXC 儀表板。

---

## 步驟 7：測試系統

```bash
bash ~/projects/axc-trading/scripts/health_check.sh
```

看到全部 pass、0 fail 就代表一切正常！

---

## 步驟 8：連接交易所（選填）

打開儀表板 http://localhost:5555 → 右上角「連接交易所」

| 交易所 | 需要 | 用途 |
|--------|------|------|
| Aster DEX | API Key + Secret | BTC/ETH/XRP/XAG/XAU 交易 |
| Binance Futures | API Key + Secret | BTC/ETH/SOL/POL 交易 |
| HyperLiquid | Private Key + Address | BTC/ETH/SOL 交易（選填） |

唔連接交易所 = Demo 模式（儀表板有假數據睇，唔會落真單）。

---

## 步驟 9：個人化設定（選填）

如果你想改交易參數但唔影響 git pull 更新：

```bash
# 建立你自己嘅設定文件（唔會被 git 覆蓋）
cp ~/projects/axc-trading/config/params.py ~/projects/axc-trading/config/user_params.py
```

然後只改 `user_params.py` 入面你想改嘅變數。詳見「想改咩？改邊度？」指南。

---

## 常見問題

**Q：出現 `command not found: pip3`？**
```bash
# 先安裝 Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# 再安裝 Python
brew install python3
```

**Q：出現 `Permission denied`？**
```bash
chmod +x ~/projects/axc-trading/scripts/*.sh
```

**Q：儀表板打唔開？**
```bash
# 確認 dashboard 係咪跑緊
ps aux | grep dashboard
# 唔係就重新啟動
cd ~/projects/axc-trading && python3 scripts/dashboard.py &
```

**Q：想設定 Telegram 通知？**
見 Telegram 指令指南（06）

**Q：想自動開機啟動？**
見 LaunchAgents 指南（13）

---

## 下一步

- 查看儀表板：http://localhost:5555
- 閱讀完整說明：http://localhost:5555/details
- 用 Telegram 控制系統（選填）
- 調整交易設定：見「想改咩？改邊度？」指南（16）
