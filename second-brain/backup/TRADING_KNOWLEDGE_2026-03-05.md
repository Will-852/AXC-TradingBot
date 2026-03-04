# TRADING_KNOWLEDGE.md

記錄所有交易相關知識、策略、市場觀察、OpenClaw 改進、錯誤分析。
格式：連續追加，每筆註明日期。

## OpenClaw 系統
- 架構：main/trader/scanner/heartbeat 4-agent
- 位置：~/.openclaw/
- 配置：config/params.py
- 通信：shared/ 文件夾

## 交易策略
（待記錄）

## 市場觀察
（待記錄）

## 系統改進

### 2025-03-05: backup_monitor.sh 容量判斷精度修正
- **問題**：`du -sh`（人類可讀格式）搭配 `sed` 剝離導致單位錯誤
  - 500MB（0.5G）被當成 0MB
  - 1200MB（1.2G）被當成 1MB
- **修正**：改用 `du -sm` 直接輸出 MB 整數，避免單位轉換陷阱
- **結果**：備份目錄容量判斷現在準確可用

更新記錄：2025-03-05 初建
