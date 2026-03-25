# Task: 5M Momentum Bot — Safety Fix（Cancel + Order Tracking）
> Created: 2026-03-25
> Priority: 💀 FATAL — bot 而家唔可以跑 live，orphan orders = uncontrolled risk

## Goal
修復 4 個 safety issue（2 FATAL + 2 SEVERE），令 bot 嘅 risk management 真正 work。

## 背景
- 新 bot: `polymarket/strategies/five_m_momentum/run.py`（700 行）
- 舊 bot: `polymarket/run_5m_live.py` 有 cancel logic（reference）
- Client: `polymarket/exchange/polymarket_client.py` 已有 `cancel_order()` + `cancel_all()`
- **4 個 `buy_shares()` call site 全部冇 capture return value → order_id 遺失 → 無法 cancel**

## Risk Register
| # | Risk | 嚴重度 | Root Cause |
|---|------|--------|------------|
| 💀1 | GTC orders 冇 cancel — window 結束後 orders 仲掛住 | FATAL | run.py 冇 cancel logic |
| 💀2 | Shutdown 冇 cancel_all — bot 停咗但 orders 仲喺 CLOB | FATAL | shutdown handler 只 set flag |
| 🔴3 | PnL 係假嘅 — 用 BTC price 推斷，唔係 actual fill | SEVERE | _check_resolution 冇 query fill status |
| 🔴4 | Session cap 建基於假 PnL — 真實損失可以超過 $60 | SEVERE | 因 #3 |

## Phases

### Phase 1: Order ID Tracking `status: pending`
> 目標：所有 buy_shares() call 必須 capture order_id，存入 pending_orders

**改動 1.1 — pending_orders 數據結構升級**
- File: `run.py` line 205
- 舊：`self.pending_orders: dict = {}  # cid -> {mode, coin, direction, end_ms, ...}`
- 新：每個 entry 加 `"orders": [{"order_id": str, "side": "UP"/"DOWN", "price": float, "shares": float, "role": "lean"/"hedge"}]`
- 向後兼容：`load()` 時如果舊 format（冇 "orders" key）→ 加空 list

**改動 1.2 — buy_shares() return value capture**

4 個 call site 全部要改：

| Call Site | File:Line | Mode | Side | 改法 |
|-----------|-----------|------|------|------|
| MODE_A lean | run.py:574-576 | A | lean | `resp = client.buy_shares(...)` → extract `resp.get("orderID") or resp.get("order_id", "")` |
| MODE_A hedge | run.py:585-587 | A | hedge | 同上 |
| MODE_B lean | run.py:659-661 | B | lean | 同上 |
| MODE_B hedge | run.py:677 | B | hedge | 同上 |

**改動 1.3 — 存入 pending_orders**

每個 buy_shares 成功後，append 到 `session.pending_orders[cid]["orders"]`：
```python
session.pending_orders[cid]["orders"].append({
    "order_id": order_id,
    "side": "UP" or "DOWN",
    "price": plan.lean_price,
    "shares": plan.lean_shares,
    "role": "lean" or "hedge",
})
```

**注意**：
- `buy_shares` 可能 throw exception → order_id 只有成功時先存
- Dry-run mode 返 `{"dry_run": True}` → order_id = "" → 冇問題，cancel 會 skip 空 id
- **CLOB SDK 返嘅 key 可能係 `orderID`（camelCase）唔係 `order_id`** → 要用 `resp.get("orderID") or resp.get("order_id", "")`

**改動 1.4 — save/load 兼容**
- `save()` line 232-250：已經 serialize `pending_orders` dict → 自動包含新 fields ✅
- `load()` line 252-269：加 migration — 如果 entry 冇 `"orders"` key → 加 `[]`

**驗證**：
- Dry-run mode 跑一個 cycle → check state JSON 有 orders array
- order_id 唔係空字串（live mode）

---

### Phase 2: Pre-End Cancel `status: pending`
> 目標：window 結束前 30s cancel 所有 unfilled orders

**改動 2.1 — 新 function `_cancel_before_end()`**

位置：run.py，放喺 `_check_resolution()` 之前（~line 272）

```python
# Config constant
_CANCEL_BEFORE_END_S = 30  # cancel 30s before window end

def _cancel_before_end(session: SessionState, client, dry_run: bool):
    """Cancel all pending orders 30s before window end.

    Reference: run_5m_live.py:796-828
    """
    if dry_run or not client:
        return

    now_ms = int(time.time() * 1000)

    for cid, pinfo in list(session.pending_orders.items()):
        end_ms = pinfo.get("end_ms", 0)
        if end_ms <= 0:
            continue

        tte_s = (end_ms - now_ms) / 1000

        # Only cancel in the 30s window before end
        # (skip if already past end — resolution handles those)
        if not (0 < tte_s <= _CANCEL_BEFORE_END_S):
            continue

        orders = pinfo.get("orders", [])
        cancelled = 0
        for order_rec in orders:
            oid = order_rec.get("order_id", "")
            if not oid:
                continue
            try:
                client.cancel_order(oid)
                log.info("CANCEL T-%.0fs %s %s %s",
                         tte_s, cid[:8], order_rec.get("role", "?"),
                         order_rec.get("side", "?"))
                cancelled += 1
            except Exception as e:
                # Order may already be filled/cancelled — OK
                log.debug("Cancel failed %s: %s (may be filled)", oid[:8], e)

        if cancelled:
            _log_jsonl(_TRADE_LOG, {
                "ts": _now_hkt(), "event": "cancel_before_end",
                "cid": cid[:8], "cancelled": cancelled,
                "tte_s": round(tte_s, 1),
            })
```

**改動 2.2 — 插入 main loop**

File: run.py `_run_cycle()` function（line 414-451）

在 `_check_resolution()` 調用之前加：
```python
# ── Cancel orders approaching window end ──
_cancel_before_end(session, client, dry_run)

# ── Resolution ──
_check_resolution(session, now_ms)
```

位置：在 discovery 之後、watchlist processing 之前（~line 428 附近）。

**注意**：
- `_cancel_before_end` 要喺 `_check_resolution` 之前跑，因為 resolution 會 pop pending_orders
- Cancel 失敗唔 fatal — order 可能已經 fill（正常）
- Cancel 成功 = order 唔會再 fill = 安全

**驗證**：
- Dry-run：log 顯示 "CANCEL T-29s ..." messages
- 確認 cancel 唔會 crash 如果 order 已 fill

---

### Phase 3: Shutdown Cancel `status: pending`
> 目標：bot 停止時 cancel 所有 open orders

**改動 3.1 — 修改 shutdown path**

File: run.py main() function（line 405-411）

舊：
```python
# ── Shutdown ──
ws_binance.stop()
log.info("Session complete: ...")
```

新：
```python
# ── Shutdown: cancel all open orders FIRST ──
log.info("Shutting down — cancelling all open orders...")
if not dry_run:
    try:
        result = client.cancel_all()
        log.info("cancel_all result: %s", result)
    except Exception as e:
        log.error("cancel_all FAILED: %s — check CLOB manually!", e)

    # Also cancel individually as backup
    for cid, pinfo in session.pending_orders.items():
        for order_rec in pinfo.get("orders", []):
            oid = order_rec.get("order_id", "")
            if oid:
                try:
                    client.cancel_order(oid)
                    log.info("Shutdown cancel: %s %s", cid[:8], oid[:8])
                except Exception:
                    pass

ws_binance.stop()
session.save()
log.info("=" * 60)
log.info("  Session complete: A=%d B=%d PnL=$%.2f",
         session.mode_a_count, session.mode_b_count, session.pnl)
log.info("  ⚠️  PnL is ESTIMATED (BTC-based, not actual fills)")
log.info("  Logs: %s", _TRADE_LOG)
log.info("=" * 60)
```

**改動 3.2 — Signal handler 改善（optional but recommended）**

舊 signal handler（line 79-82）只 set flag。如果 main loop 正在 `time.sleep()`，可能要等 2s 先 exit。改用 atexit 作為 backup：

```python
import atexit

_client_ref = None  # module-level ref for atexit

def _atexit_cancel():
    """Last resort: cancel all orders on ANY exit path."""
    if _client_ref and not getattr(_client_ref, 'dry_run', True):
        try:
            _client_ref.cancel_all()
        except Exception:
            pass

atexit.register(_atexit_cancel)
```

在 `main()` init client 之後 set `_client_ref = client`。

**注意**：
- `cancel_all()` 係 CLOB API call，cancel 整個 account 嘅所有 open orders
- 如果同時跑 15M bot + 5M bot → `cancel_all()` 會 cancel 15M bot 嘅 orders！
- **解法**：如果有可能同時跑兩個 bot → 用 individual `cancel_order()` instead of `cancel_all()`
- 目前只有 5M bot running → `cancel_all()` safe

**驗證**：
- Ctrl+C 後 log 顯示 "cancel_all result: ..."
- kill -TERM 同理
- atexit 作為 last resort（process crash 時可能唔 fire，但 normal exit 一定 fire）

---

### Phase 4: PnL 警告（快速 fix，唔改 resolution logic）`status: pending`
> 目標：清楚標示 PnL 係 estimated，唔係 actual

呢個 phase 唔改 `_check_resolution()` 嘅邏輯（改需要 WS user feed，工作量大）。只做 disclosure。

**改動 4.1 — Resolution log 加 warning**

File: run.py `_check_resolution()` line 332-338

加一行：
```python
log.info("RESOLVED %s (⚠️ ESTIMATED): PnL=$%.2f | ...", ...)
```

**改動 4.2 — Session state 加 estimated flag**

```python
session.pending_orders[cid]["pnl_source"] = "estimated_btc"
```

**改動 4.3 — Startup log 加 warning**

Line 362-370 已有 startup banner。加：
```python
log.warning("  ⚠️  PnL tracking is ESTIMATED (BTC-based, not actual fills)")
log.warning("  ⚠️  Session cap may not reflect true losses")
```

---

## Execution Order
```
Phase 1 (Order ID) → Phase 2 (Pre-End Cancel) → Phase 3 (Shutdown Cancel) → Phase 4 (PnL Warning)
                                                                              ↓
                                                                         每個 Phase 做完：
                                                                         1. dry-run 跑 1 cycle 驗證
                                                                         2. grep "cancel\|order_id\|orders" 確認
                                                                         3. 確認冇 break 現有功能
```

## File Change Summary
| File | Changes | Lines affected |
|------|---------|---------------|
| `strategies/five_m_momentum/run.py` | Order tracking + cancel logic + shutdown | ~80 行新 code，~20 行改動 |
| 其他 files | 唔改 | — |

## Gotchas / 已知坑
1. **CLOB SDK order_id key 可能係 camelCase `orderID`** → 用 `resp.get("orderID") or resp.get("order_id", "")`
2. **cancel 已 fill 嘅 order 唔會 error** — CLOB 返 success 或 "order not found"，唔影響
3. **cancel_all() 會 cancel 所有 bot 嘅 orders** — 如果同時跑 15M bot 要用 individual cancel
4. **Dry-run mode cancel_order() 只 log** — 唔會真的 call CLOB
5. **atexit handler 喺 SIGKILL 唔 fire** — 只有 `kill -9` 會 bypass，正常 Ctrl+C / SIGTERM 冇問題

## 依賴
- 唔需要新 dependency
- 唔改 polymarket_client.py（cancel methods 已有）
- 唔改 config.py（cancel timing 用 local constant）
- 唔改 planning modules（maker_arb.py / single_side.py）

## Safety Check（改完後必做）
```bash
# 1. Order path audit
grep -n "buy_shares\|cancel_order\|cancel_all\|order_id\|orderID" polymarket/strategies/five_m_momentum/run.py

# 2. 確認每個 buy_shares 都 capture return
grep -B2 "buy_shares" polymarket/strategies/five_m_momentum/run.py | grep -c "resp\|result\|ret"
# Expected: 4 (same as number of buy_shares calls)

# 3. Dry-run 跑 1 cycle
PYTHONPATH=.:scripts python3 polymarket/strategies/five_m_momentum/run.py --dry-run

# 4. 檢查 state file
cat polymarket/strategies/five_m_momentum/data/session_state.json | python3 -m json.tool | grep -A5 "orders"
```
