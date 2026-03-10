#!/usr/bin/env python3.11
"""
main.py — Trader Cycle 入口
macOS launchd 每 30 分鐘調用一次

Phase 3: Live Trading on Aster DEX
- 讀取狀態 → 風控檢查 → fetch 市場數據 → 計算指標
- 偵測 mode → no-trade check → 同步倉位 → 管理倉位
- 評估策略 → 揀信號 → 計算倉位 → 落盤
- 更新 state → 交易記錄 → 記憶 → Telegram 報告

--dry-run (default): 分析 only，唔落盤
--live: 連接 Aster DEX，自動落盤（需要通過 48h paper gate）

Exit codes: 0 = ok, 1 = signal found, 2 = error
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import traceback
from datetime import datetime

# ─── Setup import paths ───
_scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Now we can import our package
from trader_cycle.config.settings import (
    HKT, SCAN_CONFIG_PATH, TRADE_STATE_PATH, SCAN_LOG_PATH,
    LOG_DIR, PAIRS, PAIR_PREFIX,
    PRIMARY_TIMEFRAME, SILENT_MODE_THRESHOLD_CYCLES,
    PAPER_GATE_HOURS, PAPER_GATE_FILE, CYCLE_LOG_DIR,
)
from trader_cycle.core.context import CycleContext
from trader_cycle.core.pipeline import Pipeline, CriticalError
from trader_cycle.core.registry import StrategyRegistry
from trader_cycle.state.scan_config import read_scan_config, write_scan_config
from trader_cycle.state.trade_state import read_trade_state
from trader_cycle.exchange.market_data import FetchMarketDataStep, CalcIndicatorsStep
from trader_cycle.exchange.position_sync import CheckPositionsStep
from trader_cycle.exchange.execute_trade import ExecuteTradeStep
from trader_cycle.strategies.mode_detector import DetectModeStep
from trader_cycle.strategies.range_strategy import RangeStrategy
from trader_cycle.strategies.trend_strategy import TrendStrategy
from trader_cycle.strategies.evaluate import EvaluateSignalsStep, SelectSignalStep
from trader_cycle.risk.risk_manager import SafetyCheckStep, NoTradeCheckStep, ManagePositionsStep
from trader_cycle.risk.adjust_positions import AdjustPositionsStep
from trader_cycle.risk.position_sizer import SizePositionStep
from trader_cycle.state.trade_log import WriteTradeLogStep
from trader_cycle.state.trade_journal import WriteTradeJournalStep
from trader_cycle.notify.telegram import SendReportsStep, send_telegram, format_urgent_alert
from trader_cycle.state.memory_keeper import WriteMemoryStep
from trader_cycle.state.read_sentiment import ReadSentimentStep


# ─── Pipeline Steps ───

class ReadStateStep:
    """Step 1: Read SCAN_CONFIG.md + TRADE_STATE.md."""
    name = "read_state"

    def run(self, ctx: CycleContext) -> CycleContext:
        # Read scan config
        ctx.scan_config = read_scan_config()
        if not ctx.scan_config:
            raise CriticalError("Cannot read SCAN_CONFIG.md")

        # Determine FAST vs FULL mode
        trigger_pending = ctx.scan_config.get("TRIGGER_PENDING", "OFF")
        last_updated = ctx.scan_config.get("last_updated", "INIT")

        if trigger_pending == "ON" and last_updated != "INIT":
            try:
                lu = datetime.strptime(str(last_updated), "%Y-%m-%d %H:%M")
                lu = lu.replace(tzinfo=HKT)
                age_min = (ctx.timestamp - lu).total_seconds() / 60
                if age_min < 25:
                    ctx.mode = "FAST"
            except (ValueError, TypeError):
                pass

        # Read trade state
        ctx.trade_state = read_trade_state()

        # Carry forward mode detection state
        ctx.prev_mode = ctx.trade_state.get("MARKET_MODE", "UNKNOWN")
        ctx.prev_mode_cycles = ctx.trade_state.get("MODE_CONFIRMED_CYCLES", 0)
        if not isinstance(ctx.prev_mode_cycles, int):
            ctx.prev_mode_cycles = 0

        if ctx.verbose:
            print(f"    Mode: {ctx.mode} | Trigger: {trigger_pending} | Prev market mode: {ctx.prev_mode}")

        return ctx


class WriteStateStep:
    """Step 13: Write back state to SCAN_CONFIG.md, TRADE_STATE.md, SCAN_LOG.md."""
    name = "write_state"

    def run(self, ctx: CycleContext) -> CycleContext:
        ts = ctx.timestamp_str

        # ─── Update SCAN_CONFIG.md ───
        updates = dict(ctx.scan_config_updates)

        # Always update prices from current market data
        for sym, snap in ctx.market_data.items():
            prefix = PAIR_PREFIX.get(sym, sym.replace("USDT", ""))
            if snap.price > 0:
                price_fmt = f"{snap.price:.1f}" if snap.price > 100 else f"{snap.price:.4f}"
                updates[f"{prefix}_price"] = price_fmt
                updates[f"{prefix}_price_ts"] = ts

            if snap.funding_rate != 0:
                updates[f"{prefix}_funding_last"] = f"{snap.funding_rate:.10f}"

        # Update ATR from indicators (4H)
        for sym in ctx.indicators:
            if PRIMARY_TIMEFRAME in ctx.indicators[sym]:
                ind = ctx.indicators[sym][PRIMARY_TIMEFRAME]
                prefix = PAIR_PREFIX.get(sym, sym.replace("USDT", ""))
                atr = ind.get("atr")
                if atr is not None:
                    updates[f"{prefix}_ATR"] = f"{atr:.4f}" if atr < 10 else f"{atr:.1f}"

                # Update S/R from rolling
                rolling_low = ind.get("rolling_low")
                rolling_high = ind.get("rolling_high")
                if rolling_low is not None:
                    updates[f"{prefix}_support"] = f"{rolling_low:.4f}" if rolling_low < 10 else f"{rolling_low:.1f}"
                if rolling_high is not None:
                    updates[f"{prefix}_resistance"] = f"{rolling_high:.4f}" if rolling_high < 10 else f"{rolling_high:.1f}"

                # Pre-calculate S/R zones (±0.3×ATR)
                if atr is not None and atr > 0:
                    zone_width = 0.3 * atr
                    if rolling_low is not None:
                        updates[f"{prefix}_support_zone"] = (
                            f"{rolling_low - zone_width:.2f}-{rolling_low + zone_width:.2f}"
                        )
                    if rolling_high is not None:
                        updates[f"{prefix}_resistance_zone"] = (
                            f"{rolling_high - zone_width:.2f}-{rolling_high + zone_width:.2f}"
                        )

        # Funding timestamp
        updates["funding_ts"] = ts

        # Trader-cycle meta updates
        updates["last_updated"] = ts
        old_count = ctx.scan_config.get("update_count", 0)
        if not isinstance(old_count, int):
            old_count = 0
        updates["update_count"] = old_count + 1
        updates["CONFIG_VALID"] = "true"

        # Clear trigger
        updates["TRIGGER_PENDING"] = "OFF"

        # Silent mode logic
        if not ctx.signals:
            silent_cycles = ctx.scan_config.get("SILENT_MODE_CYCLES", 0)
            if not isinstance(silent_cycles, int):
                silent_cycles = 0
            silent_cycles += 1
            updates["SILENT_MODE_CYCLES"] = silent_cycles
            if silent_cycles >= SILENT_MODE_THRESHOLD_CYCLES:
                updates["SILENT_MODE"] = "ON"
        else:
            updates["SILENT_MODE"] = "OFF"
            updates["SILENT_MODE_CYCLES"] = 0

        write_scan_config(updates)

        # ─── Update TRADE_STATE.md ───
        ts_updates = {
            "LAST_UPDATED": ts,
            "MARKET_MODE": ctx.market_mode,
            "MODE_CONFIRMED_CYCLES": ctx.scan_config_updates.get(
                "MODE_CONFIRMED_CYCLES", ctx.prev_mode_cycles
            ),
        }
        # Merge Phase 3 trade state updates (position info, order IDs)
        ts_updates.update(ctx.trade_state_updates)

        from trader_cycle.state.trade_state import write_trade_state
        write_trade_state(ts_updates)

        # ─── Append SCAN_LOG.md ───
        signal_status = "SIGNAL" if ctx.signals else "NO_SIGNAL"
        silent_status = updates.get("SILENT_MODE", "OFF")
        prices_str = " ".join(
            f"{sym.replace('USDT', '')}:{snap.price:.1f}" if snap.price > 10
            else f"{sym.replace('USDT', '')}:{snap.price:.4f}"
            for sym, snap in sorted(ctx.market_data.items())
        )
        live_tag = " LIVE" if not ctx.dry_run else ""
        ctx.scan_log_entry = (
            f"[{ts} UTC+8] DEEP {signal_status}{live_tag} "
            f"MODE:{ctx.mode} MARKET:{ctx.market_mode} "
            f"SILENT:{silent_status} {prices_str}"
        )

        from light_scan import append_scan_log
        append_scan_log(SCAN_LOG_PATH, ctx.scan_log_entry)

        if ctx.verbose:
            print(f"    SCAN_CONFIG: {len(updates)} fields updated")
            print(f"    SCAN_LOG: {ctx.scan_log_entry}")

        return ctx


# ─── Paper Gate ───

def check_paper_gate() -> tuple[bool, str]:
    """
    Check if 48h paper trading gate is satisfied.
    Returns (passed, message).
    """
    if not os.path.exists(PAPER_GATE_FILE):
        return False, (
            f"Paper gate file not found: {PAPER_GATE_FILE}\n"
            f"Create it with: echo $(date +%s) > {PAPER_GATE_FILE}"
        )

    try:
        with open(PAPER_GATE_FILE, "r") as f:
            start_ts = int(f.read().strip())
        elapsed_hours = (datetime.now().timestamp() - start_ts) / 3600
        if elapsed_hours < PAPER_GATE_HOURS:
            remaining = PAPER_GATE_HOURS - elapsed_hours
            return False, (
                f"Paper gate: {elapsed_hours:.1f}h / {PAPER_GATE_HOURS}h "
                f"({remaining:.1f}h remaining)"
            )
        return True, f"Paper gate passed: {elapsed_hours:.1f}h >= {PAPER_GATE_HOURS}h"
    except (ValueError, IOError) as e:
        return False, f"Paper gate file error: {e}"


# ─── Startup Validation ───

def validate_startup(live: bool = False) -> list[str]:
    """
    Validate all required configs exist and are sane.
    Returns list of errors (empty = all good).
    """
    errors = []

    # Check required files
    required_files = [
        (SCAN_CONFIG_PATH, "SCAN_CONFIG.md"),
        (TRADE_STATE_PATH, "TRADE_STATE.md"),
    ]
    for path, name in required_files:
        if not os.path.exists(path):
            errors.append(f"Missing: {name} at {path}")

    # Check log directory
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CYCLE_LOG_DIR, exist_ok=True)

    # Check SCAN_LOG directory
    scan_log_dir = os.path.dirname(SCAN_LOG_PATH)
    if not os.path.exists(scan_log_dir):
        os.makedirs(scan_log_dir, exist_ok=True)

    # Live-specific checks
    if live:
        passed, msg = check_paper_gate()
        if not passed:
            errors.append(f"PAPER_GATE: {msg}")

    return errors


# ─── Main ───

def register_strategies() -> None:
    """Register all available strategies. Add new strategies here."""
    StrategyRegistry.clear()
    StrategyRegistry.register(RangeStrategy())
    StrategyRegistry.register(TrendStrategy())
    # Future: StrategyRegistry.register(ScalpStrategy())


def build_pipeline() -> Pipeline:
    """
    Build the Phase 3 pipeline (18 steps).
    Same pipeline for DRY_RUN and LIVE — each step checks ctx.dry_run internally.

    Pipeline order:
      1. read_state          — SCAN_CONFIG + TRADE_STATE
      2. safety_check        — circuit breakers, cooldowns
      3. fetch_market        — 4 pairs live data
      4. calc_indicators     — 4H + 1H technical indicators
      4.5 read_sentiment     — news sentiment overlay
      5. detect_mode         — 5-indicator voting (RANGE/TREND)
      6. no_trade_check      — volume, funding, position limits
      7. check_positions     — sync positions + balance from exchange
      8. manage_positions    — exit rules (circuit breaker, max hold, funding)
      8.5 adjust_positions   — trailing SL, TP extension, early exit, re-entry
      9. evaluate_signals    — run active strategy on all pairs
     10. select_signal       — pick strongest signal
     11. size_position       — SL/TP/size calculation
     12. execute_trade       — place orders on Aster DEX
     13. write_state         — update SCAN_CONFIG + TRADE_STATE
     14. write_trade_log     — append to TRADE_LOG.md
     14.5 write_trade_journal — closed positions → data_analysis
     15. write_memory        — noteworthy events → MEMORY.md
     16. send_reports        — Telegram + close notifications
    """
    pipeline = Pipeline()
    pipeline.add_step(ReadStateStep())          # 1
    pipeline.add_step(SafetyCheckStep())        # 2
    pipeline.add_step(FetchMarketDataStep())    # 3
    pipeline.add_step(CalcIndicatorsStep())     # 4
    pipeline.add_step(ReadSentimentStep())      # 4.5 — news sentiment overlay
    pipeline.add_step(DetectModeStep())         # 5
    pipeline.add_step(NoTradeCheckStep())       # 6
    pipeline.add_step(CheckPositionsStep())     # 7
    pipeline.add_step(ManagePositionsStep())    # 8
    pipeline.add_step(AdjustPositionsStep())    # 8.5 — trailing SL/TP/early exit
    pipeline.add_step(EvaluateSignalsStep())    # 9
    pipeline.add_step(SelectSignalStep())       # 10
    pipeline.add_step(SizePositionStep())       # 11
    pipeline.add_step(ExecuteTradeStep())       # 12
    pipeline.add_step(WriteStateStep())         # 13
    pipeline.add_step(WriteTradeLogStep())      # 14
    pipeline.add_step(WriteTradeJournalStep())  # 14.5
    pipeline.add_step(WriteMemoryStep())        # 15
    pipeline.add_step(SendReportsStep())        # 16
    return pipeline


def init_exchange_client(verbose: bool = False):
    """Initialize AsterClient for live trading."""
    from trader_cycle.exchange.aster_client import AsterClient
    if verbose:
        import logging
        logging.basicConfig(level=logging.INFO)
    client = AsterClient()
    return client


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Trader Cycle")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="DRY_RUN mode (no trading, default)")
    parser.add_argument("--live", action="store_true",
                        help="Live trading mode (connects to Aster DEX)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram sending")
    args = parser.parse_args()

    now = datetime.now(HKT)
    ts_str = now.strftime("%Y-%m-%d %H:%M")

    if args.verbose:
        mode_str = "LIVE" if args.live else "DRY_RUN"
        print(f"[{ts_str} UTC+8] Trader Cycle starting ({mode_str})...")

    # ─── Startup validation ───
    errors = validate_startup(live=args.live)
    if errors:
        for e in errors:
            print(f"  STARTUP ERROR: {e}")
        try:
            send_telegram(format_urgent_alert("Startup Failed", "\n".join(errors)))
        except Exception:
            pass
        sys.exit(2)

    # ─── Build context ───
    ctx = CycleContext(
        timestamp=now,
        timestamp_str=ts_str,
        dry_run=not args.live,
        verbose=args.verbose,
    )

    # ─── Live mode: inject exchange clients ───
    if args.live:
        try:
            ctx.exchange_client = init_exchange_client(args.verbose)
            ctx.exchange_clients["aster"] = ctx.exchange_client
            if args.verbose:
                print(f"  AsterClient initialized (balance: ${ctx.exchange_client.get_usdt_balance():.2f})")
        except Exception as e:
            error_msg = f"AsterClient init failed: {e}"
            print(f"  FATAL: {error_msg}")
            try:
                send_telegram(format_urgent_alert("Exchange Init Failed", str(e)[:500]))
            except Exception:
                pass
            sys.exit(2)

        # Binance: optional — missing keys = skip
        try:
            from trader_cycle.exchange.binance_client import BinanceClient
            ctx.exchange_clients["binance"] = BinanceClient()
            if args.verbose:
                print(f"  BinanceClient initialized (balance: ${ctx.exchange_clients['binance'].get_usdt_balance():.2f})")
        except CriticalError:
            if args.verbose:
                print("  BinanceClient skipped (no API keys)")
        except Exception as e:
            if args.verbose:
                print(f"  BinanceClient skipped: {e}")

        # HyperLiquid: optional — missing keys = skip
        try:
            from trader_cycle.exchange.hyperliquid_client import HyperLiquidClient
            ctx.exchange_clients["hyperliquid"] = HyperLiquidClient()
            if args.verbose:
                print(f"  HyperLiquidClient initialized (balance: ${ctx.exchange_clients['hyperliquid'].get_usdt_balance():.2f})")
        except CriticalError:
            if args.verbose:
                print("  HyperLiquidClient skipped (no API keys)")
        except Exception as e:
            if args.verbose:
                print(f"  HyperLiquidClient skipped: {e}")

    # ─── Register strategies ───
    register_strategies()

    # ─── Build and run pipeline ───
    pipeline = build_pipeline()

    if args.no_telegram:
        pipeline.remove_step("send_reports")

    if args.verbose:
        print(f"  Pipeline steps: {pipeline.get_step_names()}")

    ctx = pipeline.run(ctx)

    # ─── Output summary ───
    summary = {
        "timestamp": ts_str,
        "mode": ctx.mode,
        "market_mode": ctx.market_mode,
        "mode_confirmed": ctx.mode_confirmed,
        "mode_votes": ctx.mode_votes,
        "prices": {
            sym.replace("USDT", ""): snap.price
            for sym, snap in ctx.market_data.items()
        },
        "signals_count": len(ctx.signals),
        "selected_signal": (
            {
                "pair": ctx.selected_signal.pair,
                "direction": ctx.selected_signal.direction,
                "strategy": ctx.selected_signal.strategy,
                "strength": ctx.selected_signal.strength,
                "entry": ctx.selected_signal.entry_price,
                "sl": ctx.selected_signal.sl_price,
                "tp1": ctx.selected_signal.tp1_price,
            }
            if ctx.selected_signal else None
        ),
        "risk_blocked": ctx.risk_blocked,
        "no_trade_reasons": ctx.no_trade_reasons[:5] if ctx.no_trade_reasons else [],
        "errors": len(ctx.errors),
        "warnings": len(ctx.warnings),
        "dry_run": ctx.dry_run,
        "live": args.live,
        "balance": ctx.account_balance,
        "positions": len(ctx.open_positions),
        "order_result": (
            {
                "success": ctx.order_result.success,
                "order_id": ctx.order_result.order_id,
                "price": ctx.order_result.price,
            }
            if ctx.order_result else None
        ),
        "status": "error" if ctx.errors else "ok",
    }

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    # Exit code
    if ctx.errors:
        sys.exit(2)
    elif ctx.signals:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error_msg = f"TRADER CYCLE CRASHED: {e}\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)
        try:
            send_telegram(format_urgent_alert("Crash", str(e)[:500]))
        except Exception:
            pass
        sys.exit(2)
