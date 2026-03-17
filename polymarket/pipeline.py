#!/usr/bin/env python3
"""
main.py — Polymarket Prediction Market Pipeline 入口

12-step pipeline for binary prediction market trading.
Independent from trader_cycle — shared infra lives in shared_infra/:
- Pipeline + Step + CriticalError/RecoverableError
- WriteAheadLog + FileLock
- send_telegram() + format_urgent_alert()
- Exchange exceptions + retry_quadratic

Usage:
  PYTHONPATH=.:scripts python3 polymarket/pipeline.py --dry-run --verbose
  PYTHONPATH=.:scripts python3 polymarket/pipeline.py --live --verbose

Exit codes: 0 = ok, 1 = signal found, 2 = error
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime

# ─── Setup import paths ───
_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if _AXC not in sys.path:
    sys.path.insert(0, _AXC)                          # for polymarket.*
_scripts_dir = os.path.join(_AXC, "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)                   # for shared_infra.*

from polymarket.config.settings import (
    HKT, POLY_STATE_PATH, POLY_WAL_PATH, POLY_PIPELINE_LOCK_PATH,
    LOG_DIR, POLY_PAPER_GATE_HOURS, POLY_PAPER_GATE_FILE,
)
from polymarket.core.context import PolyContext
from shared_infra.pipeline import Pipeline, CriticalError, RecoverableError
from shared_infra.file_lock import FileLock
from shared_infra.wal import WriteAheadLog

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# Pipeline Steps (12 steps)
# ════════════════════════════════════════════════════════════════

class ReadStateStep:
    """Step 1: Read POLYMARKET_STATE.json."""
    name = "read_state"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.state.poly_state import read_state
        ctx.state = read_state()
        if ctx.verbose:
            print(f"    State loaded: {len(ctx.state)} keys")
        return ctx


class ReplayWALStep:
    """Step 2: Replay pending WAL intents from previous crash (live only)."""
    name = "replay_wal"

    def run(self, ctx: PolyContext) -> PolyContext:
        if not ctx.wal or ctx.dry_run:
            return ctx

        pending = ctx.wal.get_pending()
        if not pending:
            return ctx

        if ctx.verbose:
            print(f"    WAL: {len(pending)} pending intent(s)")

        for intent in pending:
            intent_id = intent.get("id", "")
            op = intent.get("op", "")
            # For now, mark stale intents as failed — proper recovery in Phase 5
            ctx.wal.log_failed(intent_id, "stale after restart")
            ctx.warnings.append(f"WAL: stale intent {intent_id} ({op})")

        ctx.wal.prune(keep_days=7)
        return ctx


class SafetyCheckStep:
    """Step 3: Circuit breaker, daily loss limit, cooldown check."""
    name = "safety_check"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.risk.risk_manager import check_safety
        ctx = check_safety(ctx)
        if ctx.risk_blocked and ctx.verbose:
            print(f"    BLOCKED: {'; '.join(ctx.risk_reasons[:3])}")
        return ctx


class ScanMarketsStep:
    """Step 4: Scan Gamma API for crypto/weather markets."""
    name = "scan_markets"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.exchange.gamma_client import GammaClient
        from polymarket.strategy.market_scanner import scan_markets

        gamma = ctx.gamma_client or GammaClient()
        ctx.gamma_client = gamma

        try:
            ctx.scanned_markets, ctx.filtered_markets = scan_markets(
                gamma, verbose=ctx.verbose,
            )
        except Exception as e:
            raise RecoverableError(f"Gamma API scan failed: {e}")

        if ctx.verbose:
            print(f"    Category matched: {len(ctx.scanned_markets)}")
            print(f"    After quality filter: {len(ctx.filtered_markets)}")
            for m in ctx.filtered_markets[:5]:
                print(f"      [{m.category}] {m.title[:50]} Yes:{m.yes_price:.3f} Liq:${m.liquidity:,.0f}")

        return ctx


class CheckPositionsStep:
    """Step 5: Sync positions + USDC balance from exchange."""
    name = "check_positions"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.core.context import PolyPosition

        # Load positions from state (dry-run or state-based tracking)
        stored_positions = ctx.state.get("positions", [])
        for pos_data in stored_positions:
            pos = PolyPosition(
                condition_id=pos_data.get("condition_id", ""),
                title=pos_data.get("title", ""),
                category=pos_data.get("category", ""),
                side=pos_data.get("side", ""),
                token_id=pos_data.get("token_id", ""),
                shares=float(pos_data.get("shares", 0)),
                avg_price=float(pos_data.get("avg_price", 0)),
                current_price=float(pos_data.get("current_price", 0)),
                cost_basis=float(pos_data.get("cost_basis", 0)),
                entry_time=pos_data.get("entry_time", ""),
                end_date=pos_data.get("end_date", ""),
            )
            # Update current price from scanned markets
            for m in ctx.scanned_markets:
                if m.condition_id == pos.condition_id:
                    if pos.side == "YES":
                        pos.current_price = m.yes_price
                    else:
                        pos.current_price = m.no_price
                    break

            # Calculate PnL
            pos.market_value = pos.shares * pos.current_price
            pos.unrealized_pnl = pos.market_value - pos.cost_basis
            if pos.cost_basis > 0:
                pos.unrealized_pnl_pct = pos.unrealized_pnl / pos.cost_basis
            ctx.open_positions.append(pos)

        # Get balance (live mode)
        if ctx.exchange_client and not ctx.dry_run:
            try:
                ctx.usdc_balance = ctx.exchange_client.get_usdc_balance()
            except Exception as e:
                ctx.warnings.append(f"Balance fetch error: {e}")
                ctx.usdc_balance = ctx.state.get("usdc_balance", 0.0)
        else:
            ctx.usdc_balance = ctx.state.get("usdc_balance", 1000.0)  # default $1000 for dry-run

        # Calculate exposure
        ctx.total_exposure = sum(p.cost_basis for p in ctx.open_positions)
        if ctx.usdc_balance > 0:
            ctx.exposure_pct = ctx.total_exposure / ctx.usdc_balance

        if ctx.verbose:
            print(f"    Balance: ${ctx.usdc_balance:,.2f}")
            print(f"    Positions: {len(ctx.open_positions)} (exposure: {ctx.exposure_pct:.1%})")

        return ctx


class ManagePositionsStep:
    """Step 6: Monitor positions for exit triggers (expiry, drift, PnL)."""
    name = "manage_positions"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.risk.position_manager import evaluate_positions

        if not ctx.open_positions:
            return ctx

        signals = evaluate_positions(
            ctx.open_positions, now=ctx.timestamp, verbose=ctx.verbose,
        )
        ctx.exit_signals = signals

        for sig in signals:
            if ctx.verbose:
                print(
                    f"    {sig.action.upper()} {sig.position.title[:40]} "
                    f"({sig.urgency}): {'; '.join(sig.reasons)}"
                )

        return ctx


class FindEdgeStep:
    """Step 7: AI probability assessment via Claude API.

    Core innovation — uses Claude to estimate real probability,
    compare with market price to find mispricing.
    """
    name = "find_edge"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.config.settings import MAX_MARKETS_FOR_AI
        from polymarket.strategy.edge_finder import assess_markets

        if ctx.risk_blocked:
            if ctx.verbose:
                print("    Skipped: risk blocked")
            return ctx

        if not ctx.filtered_markets:
            if ctx.verbose:
                print("    No markets to assess")
            return ctx

        if ctx.verbose:
            print(f"    Assessing up to {MAX_MARKETS_FOR_AI} markets via Claude...")

        ctx.edge_assessments = assess_markets(
            ctx.filtered_markets,
            max_assessments=MAX_MARKETS_FOR_AI,
            verbose=ctx.verbose,
        )

        if ctx.verbose:
            print(f"    Edge assessments: {len(ctx.edge_assessments)}")
            for ea in ctx.edge_assessments:
                if ea.edge_pct > 0:
                    print(
                        f"      {ea.side} {ea.title[:40]} "
                        f"edge:{ea.edge:+.1%} conf:{ea.confidence:.2f}"
                    )

        return ctx


class GenerateSignalsStep:
    """Step 8: Convert edge assessments above threshold to trading signals."""
    name = "generate_signals"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.config.settings import (
            MIN_EDGE_PCT, EDGE_CONFIDENCE_THRESHOLD, MAX_SIGNALS_PER_CYCLE,
            MAX_SPREAD_PCT, MIN_BOOK_DEPTH_USDC,
            CRYPTO_15M_MIN_EDGE_PCT, CRYPTO_15M_CONFIDENCE_THRESHOLD,
        )
        from polymarket.core.context import PolySignal
        from polymarket.strategy.spread_analyzer import analyze_spread

        if ctx.risk_blocked:
            return ctx

        for edge in ctx.edge_assessments:
            # Category-aware thresholds: 15M markets have lower bars
            if edge.category == "crypto_15m":
                min_edge = CRYPTO_15M_MIN_EDGE_PCT
                min_conf = CRYPTO_15M_CONFIDENCE_THRESHOLD
            else:
                min_edge = MIN_EDGE_PCT
                min_conf = EDGE_CONFIDENCE_THRESHOLD

            if edge.edge_pct < min_edge:
                continue
            if edge.confidence < min_conf:
                continue

            # Find the market
            market = None
            for m in ctx.filtered_markets:
                if m.condition_id == edge.condition_id:
                    market = m
                    break
            if not market:
                continue

            # 15M markets use taker orders — skip spread/depth check
            if edge.category != "crypto_15m":
                spread_info = analyze_spread(
                    market, ctx.exchange_client, MAX_SPREAD_PCT, MIN_BOOK_DEPTH_USDC
                )
                if not spread_info["tradeable"]:
                    if ctx.verbose:
                        print(f"      Skipped (spread): {edge.title[:40]} — {spread_info['reason']}")
                    continue

            token_id = market.yes_token_id if edge.side == "YES" else market.no_token_id
            price = market.yes_price if edge.side == "YES" else market.no_price

            signal = PolySignal(
                condition_id=edge.condition_id,
                title=edge.title,
                category=edge.category,
                side=edge.side,
                token_id=token_id,
                price=price,
                edge=edge.edge,
                confidence=edge.confidence,
                reasoning=edge.reasoning,
            )
            ctx.signals.append(signal)

        # Limit signals per cycle, sorted by edge magnitude
        ctx.signals = sorted(
            ctx.signals, key=lambda s: abs(s.edge), reverse=True
        )[:MAX_SIGNALS_PER_CYCLE]

        # Risk filter: remove duplicates + category limits
        # Note: bet_size not checked here (signals not sized yet — SizePositionsStep is next)
        from polymarket.risk.risk_manager import filter_signals
        ctx.signals = filter_signals(ctx)

        if ctx.verbose:
            print(f"    Signals: {len(ctx.signals)}")
            for s in ctx.signals:
                print(f"      {s.side} {s.title[:40]} edge:{s.edge:+.1%} conf:{s.confidence:.2f}")

        return ctx


class SizePositionsStep:
    """Step 9: Kelly criterion position sizing."""
    name = "size_positions"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.risk.binary_kelly import size_signals

        if not ctx.signals:
            return ctx

        ctx.signals = size_signals(
            signals=ctx.signals,
            bankroll=ctx.usdc_balance,
            positions=ctx.open_positions,
        )

        if ctx.verbose:
            for s in ctx.signals:
                if s.bet_size_usdc > 0:
                    print(f"    Size: {s.title[:30]} kelly={s.kelly_fraction:.3f} bet=${s.bet_size_usdc:.2f}")

        return ctx


class ExecuteTradesStep:
    """Step 10: Place orders on Polymarket."""
    name = "execute_trades"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.state.trade_log import log_trade
        from polymarket.core.context import PolyPosition

        if not ctx.signals:
            return ctx

        for signal in ctx.signals:
            if signal.bet_size_usdc <= 0:
                continue

            trade_record = {
                "condition_id": signal.condition_id,
                "title": signal.title,
                "category": signal.category,
                "side": signal.side,
                "amount": signal.bet_size_usdc,
                "price": signal.price,
                "edge": signal.edge,
                "confidence": signal.confidence,
            }

            if ctx.dry_run:
                trade_record["dry_run"] = True
                if ctx.verbose:
                    print(f"    DRY_RUN: would buy {signal.side} {signal.title[:40]} ${signal.bet_size_usdc:.2f}")

                # Log dry-run trade
                log_trade(
                    condition_id=signal.condition_id,
                    title=signal.title,
                    category=signal.category,
                    side=signal.side,
                    action="buy",
                    shares=signal.bet_size_usdc / signal.price if signal.price > 0 else 0,
                    price=signal.price,
                    amount_usdc=signal.bet_size_usdc,
                    edge=signal.edge,
                    confidence=signal.confidence,
                    kelly_fraction=signal.kelly_fraction,
                    reasoning=signal.reasoning,
                    dry_run=True,
                )

                # Add simulated position for state tracking
                shares = signal.bet_size_usdc / signal.price if signal.price > 0 else 0
                pos = PolyPosition(
                    condition_id=signal.condition_id,
                    title=signal.title,
                    category=signal.category,
                    side=signal.side,
                    token_id=signal.token_id,
                    shares=shares,
                    avg_price=signal.price,
                    current_price=signal.price,
                    cost_basis=signal.bet_size_usdc,
                    market_value=signal.bet_size_usdc,
                    entry_time=ctx.timestamp_str,
                    end_date="",
                )
                ctx.open_positions.append(pos)

            else:
                # ─── Live Execution with WAL ───
                intent_id = None
                try:
                    # WAL: log intent before executing
                    if ctx.wal:
                        intent_id = ctx.wal.log_intent(
                            op="buy",
                            pair=signal.condition_id,
                            direction=signal.side,
                            qty=signal.bet_size_usdc / signal.price if signal.price > 0 else 0,
                            price=signal.price,
                            sl_price=0,
                            platform="polymarket",
                        )

                    # Execute buy via SDK
                    result = ctx.exchange_client.buy_shares(
                        token_id=signal.token_id,
                        amount_usdc=signal.bet_size_usdc,
                        price=signal.price,
                    )

                    order_id = result.get("orderID", result.get("id", ""))
                    trade_record["order_id"] = order_id
                    trade_record["dry_run"] = False

                    # WAL: mark done
                    if ctx.wal and intent_id:
                        ctx.wal.log_done(intent_id, order_id)

                    # Log trade
                    shares = signal.bet_size_usdc / signal.price if signal.price > 0 else 0
                    log_trade(
                        condition_id=signal.condition_id,
                        title=signal.title,
                        category=signal.category,
                        side=signal.side,
                        action="buy",
                        shares=shares,
                        price=signal.price,
                        amount_usdc=signal.bet_size_usdc,
                        edge=signal.edge,
                        confidence=signal.confidence,
                        kelly_fraction=signal.kelly_fraction,
                        reasoning=signal.reasoning,
                        order_id=order_id,
                        dry_run=False,
                    )

                    if ctx.verbose:
                        print(f"    LIVE: bought {signal.side} {signal.title[:40]} ${signal.bet_size_usdc:.2f} → {order_id}")

                except Exception as e:
                    # WAL: mark failed
                    if ctx.wal and intent_id:
                        ctx.wal.log_failed(intent_id, str(e))
                    trade_record["error"] = str(e)
                    ctx.errors.append(f"Trade failed: {signal.title[:30]} — {e}")
                    logger.error("Trade execution failed: %s", e)
                    continue

            ctx.executed_trades.append(trade_record)

        return ctx


class WriteStateStep:
    """Step 11: Update POLYMARKET_STATE.json."""
    name = "write_state"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.state.poly_state import build_state_snapshot, write_state

        state = build_state_snapshot(ctx)
        ok = write_state(state)

        if not ok:
            ctx.warnings.append("State write failed")
        elif ctx.verbose:
            print(f"    State written: {POLY_STATE_PATH}")

        return ctx


class SendReportsStep:
    """Step 12: Send Telegram reports."""
    name = "send_reports"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.notify.telegram import send_poly_report, format_cycle_report

        # Print report to console in verbose mode
        if ctx.verbose:
            report = format_cycle_report(ctx)
            for line in report.replace("<b>", "").replace("</b>", "").split("\n"):
                print(f"    {line}")

        send_poly_report(ctx, no_telegram=getattr(ctx, '_no_telegram', False))
        return ctx


# ════════════════════════════════════════════════════════════════
# Pipeline Construction
# ════════════════════════════════════════════════════════════════

def build_pipeline() -> Pipeline:
    """Build the 12-step Polymarket pipeline.

    Pipeline order:
      1. read_state          — POLYMARKET_STATE.json
      2. replay_wal          — WAL crash recovery (live only)
      3. safety_check        — circuit breaker, daily loss, cooldown
      4. scan_markets        — Gamma API → filter crypto/weather
      5. check_positions     — sync positions + USDC balance
      6. manage_positions    — exit triggers (drift, PnL, expiry)
      7. find_edge           — Claude API probability assessment
      8. generate_signals    — edge > threshold → PolySignal
      9. size_positions      — half Kelly criterion
     10. execute_trades      — place orders on Polymarket
     11. write_state         — update POLYMARKET_STATE.json
     12. send_reports        — Telegram notification
    """
    pipeline = Pipeline()
    pipeline.add_step(ReadStateStep())         # 1
    pipeline.add_step(ReplayWALStep())         # 2
    pipeline.add_step(SafetyCheckStep())       # 3
    pipeline.add_step(ScanMarketsStep())       # 4
    pipeline.add_step(CheckPositionsStep())    # 5
    pipeline.add_step(ManagePositionsStep())   # 6
    pipeline.add_step(FindEdgeStep())          # 7
    pipeline.add_step(GenerateSignalsStep())   # 8
    pipeline.add_step(SizePositionsStep())     # 9
    pipeline.add_step(ExecuteTradesStep())     # 10
    pipeline.add_step(WriteStateStep())        # 11
    pipeline.add_step(SendReportsStep())       # 12
    return pipeline


# ════════════════════════════════════════════════════════════════
# Paper Gate
# ════════════════════════════════════════════════════════════════

def check_paper_gate() -> tuple[bool, str]:
    """Check if paper trading gate is satisfied (48h dry-run minimum)."""
    if not os.path.exists(POLY_PAPER_GATE_FILE):
        return False, (
            f"Paper gate file not found: {POLY_PAPER_GATE_FILE}\n"
            f"Create: echo $(date +%s) > {POLY_PAPER_GATE_FILE}"
        )
    try:
        with open(POLY_PAPER_GATE_FILE, "r") as f:
            start_ts = int(f.read().strip())
        elapsed_hours = (datetime.now().timestamp() - start_ts) / 3600
        if elapsed_hours < POLY_PAPER_GATE_HOURS:
            remaining = POLY_PAPER_GATE_HOURS - elapsed_hours
            return False, f"Paper gate: {elapsed_hours:.1f}h / {POLY_PAPER_GATE_HOURS}h ({remaining:.1f}h remaining)"
        return True, f"Paper gate passed: {elapsed_hours:.1f}h >= {POLY_PAPER_GATE_HOURS}h"
    except (ValueError, IOError) as e:
        return False, f"Paper gate error: {e}"


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Polymarket Prediction Market Pipeline")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="DRY_RUN mode (no trading, default)")
    parser.add_argument("--live", action="store_true",
                        help="Live trading (connects to Polymarket)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram sending")
    args = parser.parse_args()

    # ─── Resolve effective mode: CLI --live overrides state file ───
    if args.live:
        effective_dry_run = False
    else:
        from polymarket.state.poly_state import read_state as _read_mode_state
        _mode_state = _read_mode_state()
        effective_dry_run = _mode_state.get("dry_run", True)

    now = datetime.now(HKT)
    ts_str = now.strftime("%Y-%m-%d %H:%M")

    # ─── Pipeline Mutex ───
    try:
        _pipeline_lock = FileLock(POLY_PIPELINE_LOCK_PATH, timeout=0.1)
        _pipeline_lock.__enter__()
    except TimeoutError:
        logger.info("Poly pipeline mutex held — exiting cleanly")
        sys.exit(0)

    if args.verbose:
        mode_str = "DRY_RUN" if effective_dry_run else "LIVE"
        source = "CLI" if args.live else "state"
        print(f"[{ts_str} UTC+8] Polymarket Cycle starting ({mode_str} via {source})...")

    # ─── Startup validation ───
    os.makedirs(LOG_DIR, exist_ok=True)

    if not effective_dry_run:
        passed, msg = check_paper_gate()
        if not passed:
            print(f"  PAPER GATE: {msg}")
            sys.exit(2)

    # ─── Build context ───
    ctx = PolyContext(
        timestamp=now,
        timestamp_str=ts_str,
        dry_run=effective_dry_run,
        verbose=args.verbose,
    )

    # ─── WAL (live only) ───
    if not effective_dry_run:
        ctx.wal = WriteAheadLog(POLY_WAL_PATH)
        if args.verbose:
            print(f"  WAL initialized: {POLY_WAL_PATH}")

    # ─── Exchange client (live only) ───
    if not effective_dry_run:
        try:
            from polymarket.exchange.polymarket_client import PolymarketClient
            ctx.exchange_client = PolymarketClient(dry_run=False)
            if args.verbose:
                bal = ctx.exchange_client.get_usdc_balance()
                print(f"  PolymarketClient initialized (USDC: ${bal:.2f})")
        except Exception as e:
            print(f"  FATAL: PolymarketClient init failed: {e}")
            sys.exit(2)

    # ─── Build and run pipeline ───
    pipeline = build_pipeline()

    if args.no_telegram:
        ctx._no_telegram = True

    if args.verbose:
        print(f"  Pipeline steps: {pipeline.get_step_names()}")

    ctx = pipeline.run(ctx)

    # ─── Output summary ───
    summary = {
        "timestamp": ts_str,
        "balance": ctx.usdc_balance,
        "exposure_pct": round(ctx.exposure_pct, 4),
        "positions": len(ctx.open_positions),
        "scanned": len(ctx.scanned_markets),
        "filtered": len(ctx.filtered_markets),
        "assessments": len(ctx.edge_assessments),
        "signals": len(ctx.signals),
        "executed": len(ctx.executed_trades),
        "risk_blocked": ctx.risk_blocked,
        "errors": len(ctx.errors),
        "warnings": len(ctx.warnings),
        "dry_run": ctx.dry_run,
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
        print(f"POLY CYCLE CRASHED: {e}\n{traceback.format_exc()}", file=sys.stderr)
        try:
            from shared_infra.telegram import send_telegram, format_urgent_alert
            send_telegram(format_urgent_alert("Poly Crash", str(e)[:500]))
        except Exception:
            pass
        sys.exit(2)
