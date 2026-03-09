# 加幣種操作指南
> 最後更新：2026-03-05

## 重要：修改後必須重啟

params.py 係啟動時載入一次。唔重啟 = 新設定完全唔生效。

```bash
# 標準重啟指令
pkill -f async_scanner 2>/dev/null; sleep 2
python3 ~/projects/axc-trading/scripts/async_scanner.py &
echo "掃描器已重啟"

# 確認生效
sleep 5 && cat ~/projects/axc-trading/logs/scanner_heartbeat.txt
```

---

## Aster DEX 加幣種

```python
# ~/projects/axc-trading/config/params.py

ASTER_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "XAGUSDT",
    "SOLUSDT",   # <- 加呢行
]
```

儲存 → 重啟掃描器 → 確認：

```bash
sleep 10 && grep "SOLUSDT" ~/projects/axc-trading/shared/SCAN_LOG.md
```

---

## 幣種代碼格式

| 加咩 | 代碼 |
|------|------|
| 比特幣 | `BTCUSDT` |
| 以太幣 | `ETHUSDT` |
| 白銀 | `XAGUSDT` |
| Solana | `SOLUSDT` |
| BNB | `BNBUSDT` |
| XRP | `XRPUSDT` |

格式：幣種縮寫 + USDT，全大楷。

---

## 移除幣種

```python
ASTER_SYMBOLS = [
    "BTCUSDT",
    # "XRPUSDT",   <- 注釋 = 暫停，方便日後恢復
    "XAGUSDT",
]
```

---

## Binance 幣種（整合後）

```python
BINANCE_SYMBOLS = [
    "SOLUSDT",
]
```

同一幣種可以同時喺兩個平台掃。結果自動標示 `@aster` / `@binance`。

---

## 幣種數量建議

| 數量 | 狀態 | 行動 |
|------|------|------|
| 1-10 | 正常 | 無需調整 |
| 11-20 | 正常 | 無需調整 |
| 20+ | 注意 | 先測試，如有限速：`SCAN_MAX_WORKERS = 4` |

---

## 排查

```bash
# 心跳
cat ~/projects/axc-trading/logs/scanner_heartbeat.txt

# 實時 log
tail -f ~/projects/axc-trading/logs/scanner.log

# 最新掃描
tail -20 ~/projects/axc-trading/shared/SCAN_LOG.md

# prices_cache stale check
python3 -c "
import json
from pathlib import Path
d = json.loads((Path.home()/'projects/axc-trading/shared/prices_cache.json').read_text())
for sym, v in d.items():
    stale = 'STALE' if v.get('stale') else 'OK'
    print(f'{stale} {sym}: \${v.get(\"price\", \"?\")}')
"
```
