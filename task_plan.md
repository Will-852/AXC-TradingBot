# Task: AXC Dashboard → Pipeline Direct Control
> Created: 2026-03-23 HKT

## Goal
Dashboard (NiceGUI) 可以直接控制所有 AXC trading 功能。

## Current State

**已有 ✅:**
| Control | Location | Works? |
|---------|----------|--------|
| Zone A/B toggle | controls.py → writes params.py | ✅ |
| Regime preset toggle | controls.py → writes params.py | ✅ |
| Trading ON/OFF | controls.py → writes params.py | ✅ |
| Place order | trade_modal.py → exchange API | ✅ |
| Close position | positions.py → exchange API | ✅ |
| Modify SL/TP | positions.py → exchange API | ✅ |
| Service restart (sidebar) | layout.py → launchctl | ✅ |
| Polymarket bot start/stop | polymarket.py → poly_bot_control | ✅ |
| Polymarket schedule | polymarket.py → schedules.json | ✅ |

**缺少 ❌:**
| Control | 問題 |
|---------|------|
| trader_cycle start/stop | 冇 button，只能 launchctl 手動 |
| Run single cycle | Polymarket 有 "Run Cycle" button，AXC 冇 |
| Service health on main page | 冇顯示邊啲 service running/dead |
| trader_cycle 而家 exit code 1 | Dead，dashboard 唔知 |

## Phases

### Phase 1: trader_cycle control buttons — `complete`
- [x] 1.1 建 `utils/axc_service_control.py` — start/stop/restart/status for 5 LaunchAgent services
- [x] 1.2 加 "Dry Run" + "Live Run" buttons → single cycle execution
- [x] 1.3 加 Start/Stop/Restart buttons per service on main dashboard
- [x] 1.4 加 service status badges (PID/exit code/unloaded) auto-refresh 15s
- [x] 1.5 Service panel integrated into main page layout (side-by-side with controls)

### Phase 2: 2check — `pending`

## Decisions
| Decision | Rationale |
|----------|-----------|
| LaunchAgent control via launchctl | trader_cycle 係 LaunchAgent，唔係 nohup process |
| Separate from poly_bot_control | AXC services 用 launchctl，Poly bots 用 nohup — 唔同機制 |
| Run Once = dry-run | 安全，唔落真單 |
