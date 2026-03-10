# AXC ↔ OpenClaw 邊界定義
> 最後更新：2026-03-10

## 原則
AXC 係獨立項目，git clone 後自帶所有運行所需檔案。
OpenClaw 係公共基礎設施（Gateway），任何項目都可以接入。

---

## 擁有權

### AXC 擁有（~/projects/axc-trading/）
| 目錄 | 內容 |
|---|---|
| scripts/ | 所有 Python + Bash |
| config/ | params.py, modes/ |
| canvas/ | Dashboard HTML + SVG |
| agents/ | SOUL.md + workspace |
| shared/ | 狀態檔（TRADE_STATE, prices_cache） |
| secrets/ | .env（API keys） |
| logs/ | 所有 log 輸出 |
| memory/ | RAG（jsonl + npy） |
| docs/ | 所有文檔 |

### OpenClaw 擁有（~/.openclaw/）
| 目錄 | 內容 |
|---|---|
| openclaw.json | Gateway 設定 |
| workspace/ | Agent 狀態（SCAN_CONFIG, TRADE_LOG） |
| credentials/ | Gateway auth |
| delivery-queue/ | 訊息隊列 |
| identity/ | Gateway identity |
| devices/ | Device registry |
| canvas/ | **已棄用** — 正本在 AXC |

---

## 接口（AXC → OpenClaw）

### 1. 環境變量
```
OPENCLAW_WORKSPACE=~/.openclaw/workspace
AXC_HOME=~/projects/axc-trading
```
所有跨邊界引用必須透過 env var，唔硬編碼路徑。

### 2. openclaw_bridge.py
位置：`scripts/openclaw_bridge.py`
讀取 `~/.openclaw/openclaw.json` 提供 gateway 設定。
唯一允許直接讀 openclaw 目錄嘅 AXC 文件。

### 3. WORKSPACE 狀態檔
AXC 透過 `OPENCLAW_WORKSPACE` 讀寫：
- `agents/aster_trader/config/SCAN_CONFIG.md`
- `agents/aster_trader/TRADE_LOG.md`
- `routing/COST_TRACKER.md`

### 4. LaunchAgent
所有 plist 住 `~/Library/LaunchAgents/ai.openclaw.*.plist`。
Script 路徑指向 AXC，OPENCLAW_WORKSPACE env var 指向 Gateway。

---

## 新項目接入 OpenClaw

1. 設定 `OPENCLAW_WORKSPACE` env var
2. 複製 `openclaw_bridge.py` 模式讀 gateway config
3. 在 workspace/ 建自己嘅 agent 目錄
4. 建 LaunchAgent plist 指向自己嘅 scripts

---

## 禁止
- AXC scripts 唔可以硬編碼 `~/.openclaw/` 路徑（用 env var）
- OpenClaw 唔存放任何 AXC-specific 檔案
- Canvas 只維護 AXC 版本，openclaw/canvas 為歷史遺留
