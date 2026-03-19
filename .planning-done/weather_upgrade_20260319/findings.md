# Findings — Pipeline 2check + BMD

## BMD: 15M Trade 完整路徑攻擊（10 場景）

### 🔴 嚴重

| # | 攻擊 | 位置 | 問題 |
|---|------|------|------|
| 4 | "Up"/"Down" outcomes 順序反轉 | `gamma_client.py:282` + `edge_finder.py:797` | AI fallback 冇 outcome order check → 可能買錯方向 |
| 5 | Liquidity 跌到 $0 between scan/exec | `pipeline.py:839` | crypto_15m 跳過 spread check + 冇 pre-exec liquidity re-check |
| 10 | Binance 返回 stale data | 三個 signal source 全部冇 | 冇 timestamp freshness validation → phantom signal |

### 🟡 中等

| # | 攻擊 | 位置 | 問題 |
|---|------|------|------|
| 2 | 3 signal 互相矛盾 | `edge_finder.py:785` | max(edge_pct) wins，冇 conflict detection |
| 7 | SL 喺 resolution 後觸發 | `position_manager.py:90` | end_date 係 date-level，唔識 intra-day resolution |

### 🟢 OK

| # | 攻擊 | 結果 |
|---|------|------|
| 1 | Flat market | 三個 signal 正確返回 None |
| 3 | lead_minutes=0 | crypto_15m.py:119 hard filter |
| 6 | Kelly > bankroll | 6 層 cap 保護 |
| 8 | Min edge 6.5% or 10% | 6.5% for crypto_15m, 10% for generic |
| 9 | Duplicate trade | risk_manager.py:115 blocks by condition_id |

## 5M vs 15M Indicator Mismatch
- `crypto_15m.py:164` 硬編碼 `--interval 15m`
- 5M 市場用 15m candle indicator → timeframe mismatch
- 唔會 crash 但 signal quality 會差
