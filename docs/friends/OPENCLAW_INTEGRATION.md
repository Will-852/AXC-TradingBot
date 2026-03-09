# AXC ↔ OpenClaw Integration Spec

> AXC 完全獨立運行。裝咗 OpenClaw 會自動獲得額外狀態資訊。
> 本文件描述 AXC bridge 期望嘅 contract，方便 OpenClaw 側適配。

## 偵測機制

AXC 用 `scripts/openclaw_bridge.py` 做唯一接觸點。
啟動時自動偵測，**唔需要用家手動設定任何嘢**。

偵測順序：
1. `shutil.which("openclaw")` — binary 喺 PATH 入面？
2. 讀 `~/projects/axc-trading/openclaw.json` — 設定檔存在？
3. 任何一步失敗 → graceful fallback，零 error

## Contract（OpenClaw 需要提供）

### 1. Binary 喺 PATH

```bash
# AXC 會用 shutil.which() 偵測
which openclaw    # 要有輸出
```

### 2. Gateway 狀態指令

```bash
openclaw gateway status
# 成功：exit code 0 + stdout 有內容 → bridge 報 "ok"
# 失敗：exit code ≠ 0 或 stdout 空白 → bridge 報 "down"
# timeout: 5 秒
```

### 3. openclaw.json 格式

放喺 AXC 根目錄（`~/projects/axc-trading/openclaw.json`）。
Bridge 只讀以下兩個 section，其餘欄位隨意：

```jsonc
{
  "gateway": {
    "port": 18789          // int — bridge.gateway_port() 返回呢個值
  },
  "agents": {
    "list": [
      {
        "id": "main",                    // string — agent 識別碼
        "model": "tier3/gpt-5-mini"      // string — "prefix/model-name" 格式
      },
      {
        "id": "aster_trader",
        "model": "tier1/claude-sonnet-4-6"
      }
    ]
  }
}
```

**注意：**
- `model` 欄位用 `"tier/model-name"` 格式，bridge 會自動 strip prefix（`/` 前面嘅部分）
- `agents.list` 入面每個 entry 要有 `id` 同 `model`，缺任何一個會被跳過
- 冇 `gateway` 或 `agents` section → bridge 返回 `None` / `{}`，唔會 error

## Bridge API

AXC scripts 透過以下 API 讀取 OpenClaw 狀態：

```python
from openclaw_bridge import bridge

bridge.available          # bool: OpenClaw 裝咗未
bridge.gateway_status()   # "ok" / "down" / "n/a"
bridge.gateway_port()     # int 或 None
bridge.agent_models()     # {"main": "gpt-5-mini", "aster_trader": "claude-sonnet-4-6"}
```

## 冇裝 OpenClaw 嘅行為

| API | 返回值 |
|-----|--------|
| `bridge.available` | `False` |
| `bridge.gateway_status()` | `"n/a"` |
| `bridge.gateway_port()` | `None` |
| `bridge.agent_models()` | `{}` |

Dashboard 同 Telegram bot 照常運作，gateway 欄顯示 "N/A (standalone)"。

## 快速驗證

```bash
python3 -c "
from openclaw_bridge import bridge
print(f'Available: {bridge.available}')
print(f'Gateway:   {bridge.gateway_status()}')
print(f'Port:      {bridge.gateway_port()}')
print(f'Models:    {bridge.agent_models()}')
"
```

有 OpenClaw → 四項都有值。冇 → 全部 fallback，零 error。
