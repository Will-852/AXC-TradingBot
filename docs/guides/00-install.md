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
- 一個**交易所帳號**（Aster / Binance / HyperLiquid，選一個）

> AI API Key 係選填。核心嘅自動交易完全唔用 AI，零成本。詳見「AXC / OpenClaw / Telegram 點樣連動？」指南（17）。

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

## 步驟 2：解壓文件

打開 Terminal，**逐行複製貼上**，每行按一次 Enter：

```bash
# 建立資料夾
mkdir -p ~/projects/axc-trading

# 解壓（如果下載到 Downloads 資料夾）
unzip ~/Downloads/axc-setup-*.zip -d ~/projects/axc-trading/
```

---

## 步驟 3：安裝依賴

```bash
pip3 install -r ~/projects/axc-trading/requirements.txt
```

等待安裝完成（大約 1-2 分鐘），看到 `Successfully installed` 就係完成。

---

## 步驟 4：啟動！

```bash
cd ~/projects/axc-trading && python3 scripts/dashboard.py &
```

然後打開瀏覽器，去：
👉 **http://localhost:5555**

你應該看到 AXC 儀表板。到呢步為止，你已經可以用 Demo 模式睇 Dashboard 點運作。

---

## 步驟 5：連接交易所

打開儀表板 http://localhost:5555 → 右上角「連接交易所」

| 交易所 | 需要 | 用途 |
|--------|------|------|
| Aster DEX | API Key + Secret | BTC/ETH/XRP/XAG/XAU 交易 |
| Binance Futures | API Key + Secret | BTC/ETH/SOL/POL 交易 |
| HyperLiquid | Private Key + Address | BTC/ETH/SOL 交易（選填） |

唔連接交易所 = Demo 模式（儀表板有假數據睇，唔會落真單）。
連接咗就可以自動交易，**到呢步已經係完整系統**。

---

## 步驟 6：設定 AI API Key（可以之後再加）

> 核心自動交易完全唔需要 AI API Key。呢步只影響三個附加功能：
> Telegram AI 對話、新聞情緒分析、每週策略回顧。
> 如果你而家唔想搞，跳過就得，之後隨時番嚟加。

```bash
# 複製範例設定文件
cp ~/projects/axc-trading/secrets/.env.example ~/projects/axc-trading/secrets/.env

# 用文字編輯器打開
nano ~/projects/axc-trading/secrets/.env
```

填入你的 Key：

```
PROXY_API_KEY=你的key貼在這裡
PROXY_BASE_URL=https://你的proxy地址/v1
```

**Key 點嚟？**
- 用 Proxy（便宜，推薦新手）：問介紹你嘅朋友攞
- 用官方 Claude API：去 https://console.anthropic.com 注冊

填完後：按 **Ctrl+X** → **Y** → **Enter** 儲存退出。

---

## 步驟 7：測試系統

```bash
bash ~/projects/axc-trading/scripts/health_check.sh
```

看到全部 pass、0 fail 就代表一切正常！

---

## 步驟 8：個人化設定（選填）

如果你想改交易參數但唔影響 git pull 更新：

```bash
# 建立你自己嘅設定文件（唔會被 git 覆蓋）
cp ~/projects/axc-trading/config/params.py ~/projects/axc-trading/config/user_params.py
```

然後只改 `user_params.py` 入面你想改嘅變數。詳見「想改咩？改邊度？」指南（16）。

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
見「AXC / OpenClaw / Telegram 點樣連動？」指南（17）+ Telegram 指令指南（06）

**Q：想自動開機啟動？**
見 LaunchAgents 指南（13）

---

## 下一步

- 查看儀表板：http://localhost:5555
- 閱讀完整說明：http://localhost:5555/details
- 了解系統連動：見指南（17）
- 調整交易設定：見「想改咩？改邊度？」指南（16）
