# Task: Bot Scheduler — Auto Start/Stop + Schedule UI
> Created: 2026-03-22 03:25 HKT

## Goal
Dashboard 直接控制 MM/1H bot：Start/Stop 按鈕 + 排程自動啟停。

## Phases

### Phase 1: Extract bot control → standalone module — `in_progress`
- `utils/poly_bot_control.py` (new) — start_bot(), stop_bot(), is_running()
- Refactor polymarket.py to call this module

### Phase 2: Schedule storage — `pending`
- `polymarket/config/schedules.json`
- Schema: `{ "run_mm_live": { "start": "09:30", "stop": "16:00", "enabled": true } }`

### Phase 3: Background scheduler — `pending`
- `scripts/dashboard_ng/scheduler.py` — async loop, 30s check
- Register via `app.on_startup()` in main.py

### Phase 4: Schedule UI — `pending`
- Time pickers + enable toggle next to Start/Stop buttons

## Decisions
| # | Decision | Why |
|---|----------|-----|
| 1 | app.on_startup async loop | ui.timer only fires with active page |
| 2 | schedules.json | Inspectable, atomic write |
| 3 | 30s check interval | Minute-level accuracy |
| 4 | HKT timezone | User timezone |
| 5 | miniforge python | Bots need py_clob_client |
