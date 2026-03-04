# CLAUDE.md — 人類地圖
# 閱讀對象: 你（人類）

## 🔴 出事？

| 問題 | 指令 |
|---|---|
| Gateway | openclaw gateway health |
| Telegram | openclaw channels status --probe |
| 交易冇執行 | tail -20 logs/lightscan.log |
| Skill問題 | openclaw skills list |
| 改動歷史 | cat agents/main/workspace/EVOLUTION_LOG.md |

## 架構（按變化頻率）

🔴 常常改：
config/params.py          ← 所有數字參數
config/modes/             ← RANGE / TREND / VOLATILE

🟡 偶爾改：
agents/*/workspace/SOUL.md ← AI行為原則
agents/main/workspace/skills/ ← Skills

🟢 唔常改：
scripts/                  ← Python執行層
openclaw.json             ← OpenClaw設定

⚫ 即時變（唔需要改）：
shared/                   ← Agent狀態
logs/                     ← 日誌

## 九個 Agents
main           → agents/main/workspace/
aster_trader   → agents/aster_trader/workspace/
aster_scanner  → agents/aster_scanner/workspace/
heartbeat      → agents/heartbeat/workspace/
haiku_filter   → agents/haiku_filter/
analyst        → agents/analyst/
decision       → agents/decision/
binance_trader → agents/binance_trader/  (placeholder)
binance_scanner→ agents/binance_scanner/ (placeholder)

## 🫀 系統人體架構

🧠 大腦    main agent          決策、對話、路由
👁️ 眼      aster_scanner (t2)  感知市場訊號
💓 心臟    aster_trader (t1)   執行交易動作
🌡️ 神經    heartbeat (t3)      感應系統健康
🔬 過濾    haiku_filter (t2)   信號壓縮
📊 分析    analyst (t1)        模式/政體偵測
🎯 決策    decision (opus)     最終交易決策
🩸 血液    shared/           Agent間訊號傳遞
💪 肌肉    scripts/          Python執行層
🧬 DNA     config/           所有參數同模式
🦴 骨架    SOUL.md           唔變嘅原則支撐
🧠 記憶    agents/main/workspace/ 短期+長期記憶

重要程度：
🔴 主要（停咗會死）: main + aster_trader + scripts/trader_cycle
🟡 重要（停咗會病）: aster_scanner + heartbeat + shared/
🟢 支援（停咗會弱）: config/ + SOUL.md + memory/

核心運作鏈：
眼(aster_scanner)發現訊號
  → 血液(SIGNAL.md)傳遞
  → 心臟(aster_trader)執行
  → 血液(TRADE_STATE.md)記錄
  → 大腦(main)匯報
  → 聲帶(Telegram)通知你

## 切換模式
只改 config/modes/ 入面嘅active mode
其他唔需要動

## Gotchas [R]
[R] tier2 Haiku 唔夠強處理 >10K system prompt
[R] Skill description 空白 = 靜默失敗
[R] fcntl.flock 防止 scanner 同 tradercycle 同時執行
[R] 改參數只改 config/params.py，唔改scripts

## 備份指令
cd ~/.openclaw && \
git add -A && \
git commit -m "[$(date +%Y-%m-%d)] backup" && \
zip -r backups/backup-$(date +%Y-%m-%d-%H%M).zip \
  openclaw.json config/ agents/ shared/ scripts/ \
  ~/Library/LaunchAgents/ai.openclaw.*.plist
