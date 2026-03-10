<!--
title: 常見問題
section: 操作指南
order: 9
audience: human,claude,github
-->

# 常見問題

## 系統冇交易，點解？

- 市場波動唔夠大（低於觸發門檻）→ 正常，繼續等
- 連虧冷卻中（2 次暫停 30min，3 次暫停 2hr）→ Telegram `/health` 查看
- 日度熔斷（當日虧 ≥20%）→ 第二日自動恢復
- 暫停模式 → Telegram `/resume` 恢復
- 服務停咗 → `launchctl list | grep openclaw` 確認

## Telegram 冇收到通知？

```bash
launchctl list | grep openclaw
```

確認 `ai.openclaw.telegram` 有 PID（第一欄有數字）。冇嘅話：

```bash
launchctl start ai.openclaw.telegram
```

## 儀表板開唔到？

```bash
cd ~/projects/axc-trading && python3 scripts/dashboard.py &
```

然後瀏覽器開 `http://localhost:5555`

## 掃描器停咗？

```bash
# 1. 檢查狀態
launchctl list | grep scanner

# 2. 檢查日誌
tail -20 ~/projects/axc-trading/logs/scanner.log

# 3. 如果有鎖死
rm ~/projects/axc-trading/shared/scanner_runner.lock

# 4. 重啟
launchctl stop ai.openclaw.scanner && launchctl start ai.openclaw.scanner
```

## 災難恢復（系統壞晒點算？）

完整恢復約 15 分鐘，7 個步驟：

1. Clone repo：`git clone https://github.com/Will-852/AXC-TradingBot.git ~/projects/axc-trading`
2. 還原 .env：從 iCloud 或備份 zip 複製 `secrets/.env`
3. 安裝依賴：`pip3 install numpy requests`
4. 重建 RAG：`python3 scripts/memory_init.py`
5. 驗證：`bash scripts/health_check.sh`
6. 還原 LaunchAgents：複製 plist 到 `~/Library/LaunchAgents/`
7. 還原 crontab：`crontab -e` 加回 03:00 備份

詳見 `docs/setup/RECOVERY.md`

## 改交易參數

想改交易行為（SL/TP/leverage）：

| 層級 | 文件 | 說明 |
|------|------|------|
| 用戶層（UI 可見） | `config/params.py` 的 TRADING_PROFILES | 模式、觸發門檻、風險 |
| 引擎層（內部邏輯） | `scripts/trader_cycle/config/settings.py` | 熔斷、槓桿、冷卻 |

注意：TRADING_PROFILES 只能覆蓋 settings.py 已有嘅 key。新增 key 前先確認 settings.py 有對應定義：

```bash
grep "KEY_NAME" ~/projects/axc-trading/scripts/trader_cycle/config/settings.py
```
