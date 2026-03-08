# OpenClaw 朋友評測指南

> 感謝你幫忙測試！
> 評測只需 Dashboard，Telegram 和交易係獨立接口，唔設定都可以。

## 快速開始（5分鐘）

### 1. 安裝

```bash
git clone https://github.com/Will-852/AXC-TradingBot ~/.openclaw
pip3 install -r ~/.openclaw/requirements.txt --break-system-packages
```

### 2. API Key（只需一個）

```bash
cp ~/.openclaw/docs/friends/.env.example ~/.openclaw/secrets/.env
# 填入 PROXY_API_KEY（向我索取測試 key）
nano ~/.openclaw/secrets/.env
```

### 3. 自訂參數（選填）

```bash
cp ~/.openclaw/config/user_params.py.example ~/.openclaw/config/user_params.py
nano ~/.openclaw/config/user_params.py
```

你嘅設定放呢度，`git pull` 更新代碼永遠唔會衝突。
唔改都可以，會用預設值。

### 4. 啟動

```bash
python3 ~/.openclaw/scripts/dashboard.py
```

打開：**http://127.0.0.1:5555**

---

## 評測重點

請告訴我：

1. **Dashboard 可讀性** — 資訊清晰嗎？
2. **市場走勢** — 價格/圖表準確嗎？
3. **Agent 狀態** — 活躍度顯示正常嗎？
4. **整體體驗** — 有什麼可以改善？

---

## 獨立接口說明

```
Dashboard    本地運行，唔需要額外設定
Telegram     選填，需要自己建立 bot
Aster DEX    選填，需要交易所 API key
Binance      選填，需要交易所 API key

= 唔設定交易接口，唔會有任何真實交易
```

---

## 常見問題

**Q: 安全嗎？**
A: 完全本地運行。唔連接任何外部服務（除非你填入交易所 key）。

**Q: 需要付費嗎？**
A: Dashboard 本地免費。AI 分析功能需要 API key（向我索取測試用）。

**Q: 支持 Windows？**
A: 目前只測試 macOS。
