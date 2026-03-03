# MODEL_ROUTER.md — 唯一模型控制
# 版本: 2026-03-02（tier 重構版）
# 規則: 換模型只改呢一個檔案 + openclaw.json provider，agent 唔綁死模型
# 重要: claude-3-haiku-20240307 已確認 404，禁止使用

---

## 架構：Provider = Tier

```
openclaw.json providers:
  tier1/ → 決策級（Sonnet）
  tier2/ → 掃描級（Haiku 4.5）
  tier3/ → 預留（暫時未建）

Cron jobs 用 --model "tier1/model-id" 或 "tier2/model-id"
換模型 = 改 provider 入面嘅 model → cron jobs 唔使改
```

---

## Tier 定義

| Tier | Provider | Model ID | 角色 |
|------|----------|----------|------|
| TIER_1 | `tier1` | `claude-sonnet-4-6` | 最終決策、落盤執行、複雜推理 |
| TIER_2 | `tier2` | `claude-haiku-4-5-20251001` | 掃描、NEWS 分析、監控 |
| TIER_3 | `tier3` | `gpt-5-mini` | **Default** — 簡單讀取、對話輸出、日常任務 |

## ❌ 禁用模型

- claude-3-haiku-20240307 → 404 ERROR，API key 不支援
- google/gemini-2.0-flash → Rate limit 問題，暫時唔用
- Opus → 完全移除，成本太高

---

## Sub-task → Tier 對照

| Sub-task             | Tier   | Cron Model Override                   | 理由                        |
|----------------------|--------|---------------------------------------|-----------------------------|
| trader-cycle         | —      | **Python script（唔用 LLM）**         | 深度分析 + 策略評估         |
| trade-execution      | —      | (trader-cycle 內執行, Phase 3)        | 下單 + SL/TP 設定           |
| light-scan           | —      | **Python script（唔用 LLM）**         | 高頻信號偵測，純數學對比    |
| news-analysis        | TIER_2 | (按需觸發)                            | NEWS: 評分 + 技術分析       |
| heartbeat            | —      | **Python script（唔用 LLM）**         | 例行監控，純 file I/O       |
| memory-keeper        | —      | **trader-cycle pipeline step**        | 自動記錄重要事件到 MEMORY.md |
| telegram-report      | TIER_3 | (embedded in light-scan/trader-cycle) | 簡單文字輸出                |
| sl-tp-confirmation   | TIER_2 | (embedded in trader-cycle)            | 落盤後確認（需要準確性）    |
| position-monitor     | TIER_3 | (按需觸發)                            | 讀取倉位狀態                |

---

## 換模型 SOP

### 例：TIER_2 由 Haiku 換做 GPT-5-mini

```
1. 改 openclaw.json → providers.tier2.models[0].id = "gpt-5-mini"
2. 改 openclaw.json → providers.tier2.baseUrl（如果唔同 proxy）
3. 改 openclaw.json → providers.tier2.api（如果唔同 API format）
4. 改 agents.defaults.models → "tier2/gpt-5-mini": { "alias": "tier2" }
5. 更新所有 cron jobs: openclaw cron edit <id> --model "tier2/gpt-5-mini"
6. 重啟 gateway: openclaw gateway restart
7. 更新本檔 MODEL_ROUTER.md 記錄
8. 通知用戶

⚠️ 步驟 5 係必要嘅（因為 cron payload.model 寫死咗 model ID）
   日後如果 OpenClaw 支援 alias override，可以省略呢步
```

---

## Cron Jobs / Services（最新）

| 名稱 | 頻率 | 方式 | Model/Script | Session | Timeout |
|------|------|------|-------------|---------|---------|
| **heartbeat** | **每15分鐘** | **macOS launchd** | **Python 3.11 `tools/heartbeat.py`** | **—** | **~0.5s** |
| **light-scan** | **每3分鐘** | **macOS launchd** | **Python 3.11 `tools/light_scan.py`** | **—** | **~2s** |
| **trader-cycle** | **每30分鐘** | **macOS launchd** | **Python 3.11 `tools/trader_cycle/main.py`** | **—** | **~5s** |

### Python Services（macOS launchd）
| Service | LaunchAgent | Script | Python | Log |
|---------|-------------|--------|--------|-----|
| heartbeat | `ai.openclaw.heartbeat` | `tools/heartbeat.py` | 3.11 | `logs/heartbeat.log` |
| light-scan | `ai.openclaw.lightscan` | `tools/light_scan.py` | 3.11 | `logs/lightscan.log` |
| trader-cycle | `ai.openclaw.tradercycle` | `tools/trader_cycle/main.py` | 3.11 | `logs/tradercycle.log` |

### 已停用嘅 OpenClaw Cron
| ID | 名稱 | 原因 |
|----|------|------|
| `f7486cbf` | trader-cycle (Sonnet) | 改用 Python launchd，零 LLM 成本 |
| `df0828ad` | mission-control-heartbeat | 改用 Python launchd，零 LLM 成本 |

---

## 成本估算（每日）

```
light-scan:     Python script（唔用 LLM）          ≈ $0.00
trader-cycle:   Python script（唔用 LLM）          ≈ $0.00
heartbeat:      Python script（唔用 LLM）          ≈ $0.00
總計:           $0.00/日（was $1.59/日）— 100% Python 自動化
```

## 熔斷規則

- 日成本 >$0.50 → 警告，發 Telegram（代表有異常 LLM 調用）
- 日成本 >$1.00 → 暫停所有 LLM cron
- 注意: Python-first 架構下日常成本為 $0.00，任何 LLM 消耗都係異常

---

## 未來目標：自動 Tier Routing

> **任務難度 + 文本大小 + 重要程度 → 自動揀 tier**

### 現狀（2026-03-02）
- Python services 唔用 LLM → 唔需要 routing
- 只剩 heartbeat + Telegram 對話 + 未來 NEWS 分析用 LLM
- Default = Haiku（省錢），Sonnet 保留做 alias

### 日後規劃（等所有任務定型後實施）
| 維度 | TIER_2 (Haiku) | TIER_1 (Sonnet) |
|------|---------------|----------------|
| 文本大小 | 短文（<2k token） | 長文分析（>5k token） |
| 任務複雜度 | 簡單查詢、狀態匯報 | 多步推理、策略判斷 |
| 重要程度 | 例行監控、日誌 | 落盤決策、風控覆核 |
| 容錯性 | 高（錯咗可以重跑） | 低（錯咗有金錢損失） |

### 實施方式
- OpenClaw agent 層面做 routing（唔係 Python scripts）
- 可以喺 agent payload 加 `complexity` tag → 自動選 tier
- 或者喺 HEARTBEAT.md / agent prompt 定義 routing 規則
- **等所有任務內容完成後再做正式分配**

---

## 重要變更記錄

- 2026-03-02: Provider = Tier 重構（sonnet-4-6-tier2 → tier1, haiku-45-tier2 → tier2）
- 2026-03-02: trader-cycle 由 TIER_2 改為 TIER_1（Sonnet）
- 2026-03-02: light-scan timeout 由 60s 改為 120s（Haiku 需要更多時間）
- 2026-03-02: TIER_3 由 claude-3-haiku-20240307 改為預留位
- 2026-03-02: 所有 cron jobs 刪除重建（清除 error backoff）
- 2026-03-02: light-scan 由 LLM agent 改為 Python script（proxy 66s TTFT 問題）
- 2026-03-02: light-scan 改為 macOS launchd service（ai.openclaw.lightscan）
- 2026-03-02: 每日成本由 ~$1.64 降至 ~$1.44
- 2026-03-02: trader-cycle 由 LLM agent 改為 Python script
- 2026-03-02: trader-cycle OpenClaw cron (f7486cbf) 正式停用
- 2026-03-02: trader-cycle 改為 macOS launchd service（ai.openclaw.tradercycle, DRY_RUN）
- 2026-03-02: light-scan plist Python 3.9 → 3.11 修正
- 2026-03-02: 每日成本由 ~$1.44 降至 ~$0.15（只剩 heartbeat systemEvent）
- 2026-03-02: Default model 由 Sonnet → Haiku（openclaw.json + models.json 清理）
- 2026-03-02: Gateway 重啟生效
- 2026-03-02: 記錄用戶 vision — 自動 tier routing（等任務定型後實施）
- 2026-03-02: Heartbeat 由 LLM systemEvent 改為 Python script（零 LLM 成本）
- 2026-03-02: Heartbeat Python launchd 部署 (ai.openclaw.heartbeat)
- 2026-03-02: OpenClaw heartbeat cron (df0828ad) 正式停用
- 2026-03-02: Memory-keeper 加入 trader-cycle pipeline (WriteMemoryStep)
- 2026-03-02: 日成本由 ~$0.02 → $0.00（100% Python，零 LLM 自動消耗）
