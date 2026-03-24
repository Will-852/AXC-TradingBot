# DANGER ZONES — 高危代碼位置登記冊
> ⚠️ 涉及真金白銀嘅邏輯。改動前必讀，改動後必審。
> 最後更新：2026-03-24

## 使用方法
- 改任何列出嘅代碼 → 必須 2check + bmd
- 新 session 涉及 MM bot → 快速掃一眼呢個文件
- 發現新危險位 → 加入呢度（唔係 gotchas.md，呢度係「未爆嘅彈」）

---

## 🔴 CRITICAL — 可以直接蝕錢

### DZ-1: Reprice Cancel Race Condition ✅ FIXED 2026-03-24
**File**: `run_mm_live.py:2486-2520` (reprice) + `run_mm_live.py:2370-2397` (cancel defense)
**Bug**: cancel(already_matched_order) = no-op success → bot 唔 track fill → 再落新 order = 2x exposure
**Impact**: -$9.38 single trade, -$35 daily (8 markets affected)
**Fix**: WS pre-check + post-cancel verify. Cancel defense 同步修復。
**Residual risk**: WS 斷線時 fallback 到舊行為（accept risk, 唔 reprice）

### DZ-2: Resolve 用 Bot State 唔 Check On-Chain ✅ FIXED 2026-03-24
**File**: `run_mm_live.py:1066-1095` (pre-resolve reconciliation in `_check_resolutions`)
**Bug**: resolve 純用 bot-tracked 數字，phantom fill / partial fill 會令 bankroll drift compound。
**Fix**: resolve 前 query `get_trades(market=cid)` 對帳，差 >1 share 就 warn + 用 on-chain 數字。API 失敗時 fallback bot state。

### DZ-3: Exit Paths 唔等 Fill Confirmation ✅ FIXED 2026-03-24
**File**: `run_mm_live.py:~3002` (Partial TP), `~3051` (Profit Lock), `~3113` (Cost Recovery), `~3145` (Stop Loss)
**Bug**: `sell_shares()` 後立即 zero shares，唔等 fill confirm。
**Fix**: check `sell_shares()` 返回嘅 status。matched → 即刻更新。live → 加入 `pending_sells`，唔減 shares。
**Residual risk**: `pending_sells` 冇 check loop（暫時靠 resolve reconciliation 兜底）

### DZ-4: Instant Fill Partial Fill Blindspot ✅ FIXED 2026-03-24
**File**: `run_mm_live.py:~637` (_execute), `~1948` (T1), `~2127` (T2), `~2761` (endgame), `~2862` (hedge), `~3265` (reentry)
**Bug**: Instant fill 用 `r["size"]`（submitted size），唔 check `size_matched`。
**Fix**: `_execute` 提取 `takingAmount` → `size_matched`。所有 6 個 instant fill site 用 `r.get("size_matched", r["size"])`。

---

## 🟡 HIGH — 間接蝕錢或加大風險

### DZ-5: `_execute` Mid-Submit Exception = Orphan Order ✅ VERIFIED SAFE 2026-03-24
**File**: `run_mm_live.py:647-649` (_execute exception handler), `1877-1888` (caller loop)
**Original concern**: UP submit OK → DN throws → UP order lost from tracking
**Audit result**: FALSE ALARM — `_execute` catches per-order, all results returned, caller processes all submitted orders correctly。
**No fix needed**.

### DZ-6: Emergency Stop Cancels ALL Wallet Orders
**File**: `run_mm_live.py:1178-1188` (hard stop), `1218-1226` (daily kill)
**Bug**: `get_orders()` → cancel all = 會 cancel 5M + 1H bot 嘅 orders
**Impact**: 15M emergency 會誤殺其他 bot 嘅 open orders
**Fix**: Filter by own condition_ids before cancelling

### DZ-7: T2 Budget Cap Missing
**File**: `run_mm_live.py:2035-2050` (T2 order placement)
**Bug**: T1 有 budget cap（line 1671），T2 冇 → T2 可以落超過 budget 嘅注
**Impact**: Oversizing on T2

---

## 🟢 MEDIUM — 需要特定條件觸發

### DZ-8: 5 Latent XRP Coin-Detection Bugs
**File**: `run_mm_live.py` (cancel defense, T2, checkpoint, endgame)
**Bug**: XRP default 到錯嘅 coin slug
**Safe**: Behind observe-only gate (`_LIVE_TRADE_COINS` 唔包 xrp)

### DZ-9: Startup Orphan Cancel = Silent Fill Loss
**File**: `run_mm_live.py:3311-3329`
**Bug**: Restart 時 cancel 前 session 嘅 orders，唔 check 佢哋有冇 fill
**Impact**: 前 session 嘅 fill 靜默消失

---

## 審計歷史
| 日期 | 審計範圍 | 結果 |
|------|---------|------|
| 2026-03-24 | Full cancel/reprice paths | DZ-1 fixed, DZ-2~9 identified |

## 改動呢個文件嘅規則
- 新增 DZ 條目：必須有 file:line + bug 描述 + impact + fix 方向
- Fix 後：標 ✅ FIXED + 日期，保留條目（歷史記錄）
- 每月 1 號：review 所有 ❌ UNFIXED 條目
