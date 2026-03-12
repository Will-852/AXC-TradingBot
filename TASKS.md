# AXC v2 Development Tasks
> Source: REVIEW.md (四大支柱審查 + 架構設計)
> Created: 2026-03-13

---

## Sprint 0: Safety (3h)

- [ ] 0.1 Fix FileLock os.unlink bug in `state/file_lock.py`
- [ ] 0.2 Add pipeline mutex in `main.py` using FileLock
- [ ] 0.3 Add data freshness validation in `exchange/market_data.py`

## Sprint 1A: Exchange Refactor (5h) — Atomic unit

- [ ] 1.1 Create `exchange/retry.py` — extract shared retry decorator
- [ ] 1.2 Create `exchange/base_client.py` — BaseExchangeClient ABC (~13 methods)
- [ ] 1.3 Add HmacExchangeClient to `base_client.py` — shared HTTP + HMAC
- [ ] 1.4 Refactor AsterClient → inherit HmacExchangeClient (430→~25 lines)
- [ ] 1.5 Refactor BinanceClient → inherit HmacExchangeClient (402→~25 lines)
- [ ] 1.6 Adapt HLClient → implement BaseExchangeClient interface

## Sprint 1B: Observability (5h)

- [ ] 1.7 Fee extraction in `execute_trade.py` + `context.py`
- [ ] 1.8 Slippage calc (direction-aware) in `execute_trade.py`
- [ ] 1.9 Journal update — commission + net_pnl + slippage in `trade_journal.py`
- [ ] 1.10 Diagnostics module `core/diagnostics.py` (6 functions)
- [ ] 1.11 Telegram /status in `scripts/tg_bot.py`

## Sprint 2A: JSON State Migration (10h) — Highest Risk

- [ ] 2.1 Define JSON schema — TradeState with positions[], risk{}, meta{}
- [ ] 2.2 Dual-read layer — JSON first, fallback MD
- [ ] 2.3 MD→JSON one-time migration converter
- [ ] 2.4 Update 8 consumer files to use new interface
- [ ] 2.5 Version migration runner (`state/migrations.py`)
- [ ] 2.6 State backup — pre-write backup + prune (48 recent + 7 daily)

## Sprint 2B: Margin (3h)

- [ ] 2.7 Margin fields in Position dataclass (`context.py`)
- [ ] 2.8 Read margin data from exchange APIs (`position_sync.py`)
- [ ] 2.9 Margin alert in ManagePositionsStep — Phase A alert-only

## Sprint 3: Validation (3h)

- [ ] 3.1 Validator ABC (`risk/validators.py`)
- [ ] 3.2 Three validators: DataFreshness, Balance, Duplicate
- [ ] 3.3 Pipeline integration — ValidateOrderStep (step 11.5)

---

## Deferred

- 09 ConnectionManager — wait for /status real needs
- 10 Execution Record — wait for analysis needs
- 12 Order State Machine — wait for SL_PENDING actual occurrence
- 13 PriceDeviation + MinSize validators — wait for lite version stability
- 14 WAL — wait for higher frequency / larger capital
