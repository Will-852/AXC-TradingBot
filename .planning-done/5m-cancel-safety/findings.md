# Findings: 5M Momentum Bot Safety Fix

## 1. Code Exploration Results

### buy_shares() Call Sites — ALL 4 Missing Order ID
| Line | Mode | Side | Return Captured | Order ID Stored | Can Cancel |
|------|------|------|-----------------|-----------------|------------|
| 574-576 | A | Lean | ❌ | ❌ | ❌ |
| 585-587 | A | Hedge | ❌ | ❌ | ❌ |
| 659-661 | B | Lean | ❌ | ❌ | ❌ |
| 677 | B | Hedge | ❌ | ❌ | ❌ |

### pending_orders Structure — Missing Fields
- 舊 bot (run_5m_live.py): stores `order_id` per order ✅
- 新 bot (run.py): stores `mode, coin, direction, combined, shares, open_price, end_ms` — **冇 order_id** ❌
- 新 bot 每個 market 只有 1 entry，但實際可以有 2 orders（lean + hedge）

### Cancel Infrastructure
- `polymarket_client.py` 已有 `cancel_order(order_id)` + `cancel_all()` ✅
- `run_5m_live.py` 有 `_cancel_before_end()` reference implementation ✅
- `run.py` 零 cancel logic ❌

### Shutdown Path
- Signal handler (SIGINT/SIGTERM) 只 set `_running = False`
- Main loop exit: only `ws_binance.stop()` + log
- **零 order cancel** on any exit path

### CLOB SDK Return Format
- `client.post_order(signed, OrderType.GTC)` returns dict
- Key 可能係 `orderID` (camelCase) 或 `order_id` — 需要 handle 兩個
- Dry-run returns `{"dry_run": True}` — no order_id

## 2. Resolution Logic Issues
- Line 318: `⚠️ RISK: assumes both sides filled. May not be true.`（code 自己嘅 comment）
- Mode A PnL: `shares × (1.00 - combined)` — 假設 100% fill
- Mode B PnL: uses planned price, not actual fill price
- 冇 query actual fill status from CLOB

## 3. Risk Quantification
```
Orphan order exposure (no cancel):
  5 windows × 2 orders × $5 avg = $50 untracked
  + post-shutdown orphans = $25 more
  Total uncontrolled: up to $75 on $239 bankroll (31%)

Session cap effectiveness:
  Nominal: -$60 (25%)
  Real (with orphans + fake PnL): up to -$135 (56%)
```

## 4. cancel_all() Scope Warning
- `cancel_all()` cancels ALL orders across ALL markets for the API key
- If 15M bot running simultaneously → its orders get cancelled too
- Current state: only 5M bot planned → safe to use cancel_all()
- Future: switch to individual cancel_order() per tracked order_id
