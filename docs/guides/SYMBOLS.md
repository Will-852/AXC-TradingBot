# 加幣種操作指南
> 最後更新：2026-03-10

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

## 完整加幣種 Checklist（7 步）

加一個新幣種需要改以下 7 個位置。漏任何一個 = 靜默缺失。

### Step 1: `config/params.py` — 掃描入口
根據交易所加到對應 list：
```python
ASTER_SYMBOLS = ["BTCUSDT", ..., "新幣USDT"]     # Aster DEX
BINANCE_SYMBOLS = ["BTCUSDT", ..., "新幣USDT"]   # Binance Futures
HL_SYMBOLS = ["BTCUSDT", ..., "新幣USDT"]        # HyperLiquid
```

### Step 2: `scripts/trader_cycle/config/pairs.py` — 交易對定義
```python
"新幣USDT": PairConfig(
    symbol="新幣USDT", prefix="新幣",
    group="crypto_independent",       # 揀組：crypto_correlated / crypto_independent / commodity
    price_precision=4, qty_precision=0,  # 用 Binance exchangeInfo 確認
    notes="描述",
),
```

### Step 3: `scripts/trader_cycle/config/settings.py` — 三個位
```python
# 3a: PAIRS + PAIR_PREFIX
PAIRS = [..., "新幣USDT"]
PAIR_PREFIX = {..., "新幣USDT": "新幣"}

# 3b: POSITION_GROUPS（加到對應嘅組）
POSITION_GROUPS = {
    "crypto_correlated": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "crypto_independent": ["XRPUSDT", "POLUSDT", "新幣USDT"],  # <- 加呢度
    "commodity": ["XAGUSDT", "XAUUSDT"],
}

# 3c: TRADER_OWNED_FIELDS（加 ATR + S/R + zone）
"新幣_ATR",
"新幣_support", "新幣_resistance",
"新幣_support_zone", "新幣_resistance_zone",
```

### Step 4: `scripts/trader_cycle/strategies/evaluate.py` — 信號優先級
```python
PAIR_PRIORITY = {
    ...,
    "新幣USDT": 2,  # 優先級：BTC=4, ETH/SOL=3, XRP/POL=2, XAG/XAU=1
}
```

### Step 5: `scripts/light_scan.py` — 輕量掃描（只限 Aster 幣種）
```python
PAIRS = [..., "新幣USDT"]
PAIR_PREFIX = {..., "新幣USDT": "新幣"}
```
⚠️ 只加 Aster 幣種。Binance 幣種由 async_scanner 負責。

### Step 6: `scripts/slash_cmd.py` — `/price` 指令（只限 Aster 幣種）
```python
for pair in ["BTCUSDT", ..., "新幣USDT"]:
```
⚠️ 同 Step 5，只加 Aster 幣種。

### Step 7: `agents/aster_scanner/workspace/SOUL.md` — Agent 文檔（只限 Aster 幣種）
加一行到 pair 表格 + 更新 `skills/scan-rules/SKILL.md`。

---

## 當前幣種一覽

| 幣種 | Aster | Binance | HL | 組 |
|------|-------|---------|----|----|
| BTCUSDT | ✅ | ✅ | ✅ | crypto_correlated |
| ETHUSDT | ✅ | ✅ | ✅ | crypto_correlated |
| SOLUSDT | - | ✅ | ✅ | crypto_correlated |
| XRPUSDT | ✅ | - | - | crypto_independent |
| POLUSDT | - | ✅ | - | crypto_independent |
| XAGUSDT | ✅ | - | - | commodity |
| XAUUSDT | ✅ | - | - | commodity |

---

## 幣種代碼格式

格式：幣種縮寫 + USDT，全大楷。例：`BTCUSDT`, `POLUSDT`

---

## 移除幣種

```python
ASTER_SYMBOLS = [
    "BTCUSDT",
    # "XRPUSDT",   <- 注釋 = 暫停，方便日後恢復
    "XAGUSDT",
]
```

⚠️ 移除都要改同樣嘅 7 個位。

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
