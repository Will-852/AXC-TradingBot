# AXC v2 Development Tasks
> Source: REVIEW.md (四大支柱審查 + 架構設計)
> Created: 2026-03-13
> Last updated: 2026-03-13

---

## Sprint 0: Safety (3h) ✅

- [x] 0.1 Fix FileLock os.unlink bug in `state/file_lock.py`
- [x] 0.2 Add pipeline mutex in `main.py` using FileLock
- [x] 0.3 Add data freshness validation in `exchange/market_data.py`

## Sprint 1A: Exchange Refactor (5h) — Atomic unit ✅

- [x] 1.1 Create `exchange/retry.py` — extract shared retry decorator
- [x] 1.2 Create `exchange/base_client.py` — BaseExchangeClient ABC (~13 methods)
- [x] 1.3 Add HmacExchangeClient to `base_client.py` — shared HTTP + HMAC
- [x] 1.4 Refactor AsterClient → inherit HmacExchangeClient (430→~25 lines)
- [x] 1.5 Refactor BinanceClient → inherit HmacExchangeClient (402→~25 lines)
- [x] 1.6 Adapt HLClient → implement BaseExchangeClient interface

## Sprint 1B: Observability (5h) ✅

- [x] 1.7 Fee extraction in `execute_trade.py` + `context.py`
- [x] 1.8 Slippage calc (direction-aware) in `execute_trade.py`
- [x] 1.9 Journal update — commission + net_pnl + slippage in `trade_journal.py`
- [x] 1.10 Diagnostics module `core/diagnostics.py` (6 functions)
- [x] 1.11 Telegram /status in `scripts/tg_bot.py`

## Sprint 2A: JSON State Migration (10h) — Highest Risk ✅

- [x] 2.1 Define JSON schema — structured with positions[], system{}, risk{}, account{}, reentry{}, meta{}
- [x] 2.2 Dual-read layer — JSON first, MD fallback, backup fallback, defaults
- [x] 2.3 MD→JSON one-time migration converter (idempotent)
- [x] 2.4 Backward-compatible flat dict interface — zero consumer changes needed
- [x] 2.5 Version migration runner (`state/migrations.py`) — v0→v1
- [x] 2.6 State backup — pre-write backup + prune (48 recent + 7 daily) + rollback via STATE_FORMAT=md

## Sprint 2B: Margin (3h) ✅

- [x] 2.7 Margin fields in Position dataclass — liquidation_price, maint_margin, margin_ratio, isolated_wallet
- [x] 2.8 Read margin data from exchange APIs (`position_sync.py`)
- [x] 2.9 Margin alert in ManagePositionsStep — Phase A alert-only (ratio < 1.5 warn, liq < 2% critical)

## Sprint 3: Validation (3h) ✅

- [x] 3.1 Validator ABC + ValidationResult (`risk/validators.py`)
- [x] 3.2 Three validators: DataFreshness (>2% divergence), Balance, Duplicate
- [x] 3.3 Pipeline integration — ValidateOrderStep (step 11.5) + USE_VALIDATION_PIPELINE feature flag

---

## Test Results

| Sprint | Tests | Status |
|--------|-------|--------|
| Sprint 0 | 36/36 | 🟢 |
| Sprint 1A | 36/36 | 🟢 |
| Sprint 1B | 36/36 | 🟢 |
| Sprint 2A | 36/36 | 🟢 |
| Sprint 2B+3 | 36/36 | 🟢 |

## Git Log

```
03eb3c9 Sprint 2B+3: Margin health alerts + pre-trade validation pipeline
9795aa2 Sprint 2A: JSON state migration — dual-read, backup, rollback
2bdf9d0 Sprint 1B: Observability — fee tracking, slippage calc, diagnostics, /status
7573143 Sprint 1A: Exchange layer refactor — ABC + shared HMAC + adapter pattern
c038782 Sprint 0: Safety foundation — FileLock fix, pipeline mutex, data freshness
```

---

## Deferred

- 09 ConnectionManager — wait for /status real needs
- 10 Execution Record — wait for analysis needs
- 12 Order State Machine — wait for SL_PENDING actual occurrence
- 13 PriceDeviation + MinSize validators — wait for lite version stability
- 14 WAL — wait for higher frequency / larger capital
