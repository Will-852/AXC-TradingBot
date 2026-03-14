# OpenClaw — 系統狀態快照
> 自動更新：backup_agent.sh 每次觸發
> 最後更新：2026-03-15 03:00

## 當前運行服務
- scanner (PID active) — async_scanner.py v5，根源修復版
- telegram (PID active) — tg_bot.py polling bot
- gateway (PID active) — openclaw binary

## 近期重要決定
- 2026-03-06: 文件結構重設計（ai/ + docs/guides/ + TAXONOMY.md）
- 2026-03-06: Root cause fixes R1-R5（commit cc62f8d）
  - R1: Bounded ThreadPoolExecutor 防 thread leak
  - R2+R3: load_env.sh 確保 LaunchAgent 載入 .env
  - R4: 磁碟空間 + thread 數量監控
  - R5: integration_test.sh 5/5 pass
- 2026-03-05: Architecture decisions（PERMANENT）
  - Claude API only, NO local LLM
  - voyage-3, NO sentence-transformers
  - numpy cosine, NO Faiss
- 2026-03-05: async_scanner v4→v5 並行掃描引擎
- 2026-03-05: 完整 docs/ 結構建立（12 files）

## 已知待處理問題
- memory/ RAG 系統待完善
- VOYAGE_API_KEY rotate

## 2026-03-06 完成項目
- weekly_strategy_review.py 已實現（每週一 10:00 HKT via LaunchAgent）
- Binance scanner + trader 已整合（async_scanner.py + binance_client.py）
- news_agent 已實現（RSS scraper + Haiku sentiment，每 15 分鐘）
- 交易記錄生命週期修復（position_sync.py + tg_bot.py → trades.jsonl 完整 entry+exit）
- Dashboard get_trade_history() 改讀 trades.jsonl
- 全局 ~/.claude/CLAUDE.md 建立（70行，V3 方案）
- health_check.sh 建立（39 pass / 1 warn / 0 fail）
- 5 LaunchAgent plist python3.11 → python3 統一
- TAXONOMY.md 加 binary 文件夾說明（7 folders）
- params.py 設計原則注釋（方案3：清晰分工）
- OPS.md 加參數修改指引

## 已知 Bugs
- tp_atr_mult 覆蓋 MIN_RR（settings.py line 146-147）— 概念混用，BALANCED 可能自拒

## Maintenance
- 每月1號：`bash scripts/integration_test.sh`
- Thread 告警 → 重啟 scanner
- 每日 03:00 自動 backup（crontab）
