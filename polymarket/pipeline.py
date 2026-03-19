#!/usr/bin/env python3
"""
main.py — Polymarket Prediction Market Pipeline 入口

14-step pipeline for binary prediction market trading.
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
import time
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
    """Step 4: Scan Gamma API for crypto/weather markets.

    Time-gated: skip if last scan < SCAN_INTERVAL_SEC ago.
    Reuses cached scanned_markets from state on skip.
    """
    name = "scan_markets"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.config.settings import SCAN_INTERVAL_SEC
        from polymarket.exchange.gamma_client import GammaClient
        from polymarket.strategy.market_scanner import scan_markets
        from polymarket.core.context import PolyMarket

        # Always init GammaClient (zero API calls on init) so downstream
        # steps (LogicalArbStep) can fetch sibling markets even on cached cycles
        gamma = ctx.gamma_client or GammaClient()
        ctx.gamma_client = gamma

        # ─── Time gate: reuse cached scan if recent enough ───
        last_scan_ts = ctx.state.get("last_scan_ts", 0)
        elapsed = time.time() - last_scan_ts
        if elapsed < SCAN_INTERVAL_SEC:
            # Rebuild scanned_markets from state cache
            cached = ctx.state.get("cached_scanned_markets", [])
            for m_data in cached:
                ctx.scanned_markets.append(PolyMarket(
                    condition_id=m_data.get("condition_id", ""),
                    title=m_data.get("title", ""),
                    category=m_data.get("category", ""),
                    yes_price=float(m_data.get("yes_price", 0)),
                    no_price=float(m_data.get("no_price", 0)),
                    liquidity=float(m_data.get("liquidity", 0)),
                    volume_24h=float(m_data.get("volume_24h", 0)),
                    end_date=m_data.get("end_date", ""),
                    event_id=m_data.get("event_id", ""),
                    neg_risk=m_data.get("neg_risk", False),
                    yes_token_id=m_data.get("yes_token_id", ""),
                    no_token_id=m_data.get("no_token_id", ""),
                ))
            ctx.filtered_markets = list(ctx.scanned_markets)
            if ctx.verbose:
                skip_sec = int(SCAN_INTERVAL_SEC - elapsed)
                print(f"    Scan skipped (gate: {skip_sec}s left). Cached: {len(ctx.scanned_markets)}")
            return ctx

        try:
            ctx.scanned_markets, ctx.filtered_markets = scan_markets(
                gamma, verbose=ctx.verbose,
            )
        except Exception as e:
            raise RecoverableError(f"Gamma API scan failed: {e}")

        # Cache scanned markets in state for time-gated reuse
        ctx.state_updates["last_scan_ts"] = time.time()
        ctx.state_updates["cached_scanned_markets"] = [
            {
                "condition_id": m.condition_id, "title": m.title,
                "category": m.category, "yes_price": m.yes_price,
                "no_price": m.no_price, "liquidity": m.liquidity,
                "volume_24h": m.volume_24h, "end_date": m.end_date,
                "event_id": m.event_id, "neg_risk": m.neg_risk,
                "yes_token_id": m.yes_token_id, "no_token_id": m.no_token_id,
            }
            for m in ctx.scanned_markets
        ]

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
                hedge_side=pos_data.get("hedge_side", ""),
                hedge_size=float(pos_data.get("hedge_size", 0)),
                hedge_entry_px=float(pos_data.get("hedge_entry_px", 0)),
            )
            # Update current price — prefer CLOB midpoint (real-time)
            # over scanned_markets (may be stale from time-gating)
            got_live_price = False
            if ctx.exchange_client and not ctx.dry_run and pos.token_id:
                try:
                    mid = ctx.exchange_client.get_midpoint(pos.token_id)
                    if mid and mid > 0:
                        pos.current_price = mid
                        got_live_price = True
                except Exception as e:
                    logger.debug("Midpoint fetch failed for %s: %s", pos.token_id[:10], e)

            if not got_live_price:
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


class MergeCheckStep:
    """Step 5.5: Detect mergeable positions (YES+NO pairs).

    Detection only — reports via ctx for Telegram notification.
    On-chain merge execution is Phase 2 TODO.
    """
    name = "merge_check"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.risk.position_merger import detect_mergeable, format_merge_report

        # Need a wallet address to check positions on Data API
        # In dry-run, skip (no real positions to merge)
        if ctx.dry_run:
            if ctx.verbose:
                print("    Merge check: skipped (dry-run)")
            return ctx

        # Get proxy wallet address from exchange client
        address = ""
        if ctx.exchange_client:
            address = getattr(ctx.exchange_client, "proxy_address", "")
            if not address:
                address = getattr(ctx.exchange_client, "address", "")

        if not address:
            if ctx.verbose:
                print("    Merge check: no wallet address")
            return ctx

        mergeables = detect_mergeable(address, verbose=ctx.verbose)

        if mergeables:
            report = format_merge_report(mergeables)
            ctx.telegram_messages.append(report)
            total = sum(m.reclaimable_usdc for m in mergeables)
            if ctx.verbose:
                print(
                    f"    Merge check: {len(mergeables)} mergeable "
                    f"→ ${total:.2f} reclaimable"
                )
        elif ctx.verbose:
            print("    Merge check: no mergeable positions")

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


class CloseHedgeStep:
    """Step 6.5: Close HL hedges for resolved/exited crypto_15m positions."""
    name = "close_hedge"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.config.settings import (
            HEDGE_ENABLED, HEDGE_AUTO_CLOSE_ON_RESOLVE, HEDGE_SYMBOL,
        )

        if not HEDGE_ENABLED or not HEDGE_AUTO_CLOSE_ON_RESOLVE:
            return ctx

        # Check if any exit signals target hedged positions
        hedged_exits = []
        for sig in ctx.exit_signals:
            pos = sig.position if hasattr(sig, "position") else None
            if pos and pos.hedge_side:
                hedged_exits.append(pos)

        if not hedged_exits:
            return ctx

        try:
            if ctx.hl_hedge_client is None:
                from polymarket.exchange.hl_hedge_client import HLHedgeClient
                ctx.hl_hedge_client = HLHedgeClient(dry_run=ctx.dry_run)

            for pos in hedged_exits:
                result = ctx.hl_hedge_client.close_hedge(HEDGE_SYMBOL)
                status = result.get("status", "?")

                if ctx.verbose:
                    print(
                        f"    HEDGE CLOSE ({status}): {pos.hedge_side} "
                        f"{pos.title[:40]}"
                    )

                # Clear hedge fields
                pos.hedge_side = ""
                pos.hedge_size = 0.0
                pos.hedge_entry_px = 0.0

        except Exception as e:
            logger.warning("HL hedge close failed: %s", e)
            ctx.warnings.append(f"HL hedge close failed: {e}")

        return ctx


class ExecuteExitStep:
    """Step 6.7: Execute sell orders for positions flagged to exit.

    Iterates ctx.exit_signals where should_exit=True, sells via CLOB SDK,
    removes exited positions from ctx.open_positions, and recalculates exposure.
    Uses WAL for crash safety (same pattern as ExecuteTradesStep).
    """
    name = "execute_exits"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.state.trade_log import log_trade

        from polymarket.config.settings import AUTOMATED_CATEGORIES

        exits = [sig for sig in ctx.exit_signals if sig.should_exit]
        if not exits:
            return ctx

        exited_ids: set[str] = set()

        for sig in exits:
            pos = sig.position

            # ─── Scope guard: 只操作 automated categories ───
            if pos.category not in AUTOMATED_CATEGORIES:
                logger.warning(
                    "SKIP EXIT: %s category=%s 唔喺自動化範圍 (%s)",
                    pos.title[:30], pos.category, AUTOMATED_CATEGORIES,
                )
                continue
            trade_record = {
                "condition_id": pos.condition_id,
                "title": pos.title,
                "category": pos.category,
                "side": pos.side,
                "action": "sell",
                "shares": pos.shares,
                "price": pos.current_price,
                "pnl": pos.unrealized_pnl,
                "reasons": sig.reasons,
                "urgency": sig.urgency,
            }

            if ctx.dry_run:
                trade_record["dry_run"] = True
                if ctx.verbose:
                    print(
                        f"    DRY_RUN EXIT: would sell {pos.side} "
                        f"{pos.title[:40]} ({sig.urgency}) "
                        f"PnL=${pos.unrealized_pnl:+.2f}"
                    )

                log_trade(
                    condition_id=pos.condition_id,
                    title=pos.title,
                    category=pos.category,
                    side=pos.side,
                    action="sell",
                    shares=pos.shares,
                    price=pos.current_price,
                    amount_usdc=pos.market_value,
                    reasoning="; ".join(sig.reasons),
                    pnl=pos.unrealized_pnl,
                    dry_run=True,
                )
                exited_ids.add(pos.condition_id)

            else:
                # ─── Live Exit with WAL ───
                intent_id = None
                try:
                    if ctx.wal:
                        intent_id = ctx.wal.log_intent(
                            op="sell",
                            pair=pos.condition_id,
                            direction=pos.side,
                            qty=pos.shares,
                            price=pos.current_price,
                            sl_price=0,
                            platform="polymarket",
                        )

                    # FOK market sell (price=0) for immediate exit
                    result = ctx.exchange_client.sell_shares(
                        token_id=pos.token_id,
                        shares=pos.shares,
                        price=0,
                    )

                    order_id = result.get("orderID", result.get("id", ""))
                    trade_record["order_id"] = order_id
                    trade_record["dry_run"] = False

                    if ctx.wal and intent_id:
                        ctx.wal.log_done(intent_id, order_id)

                    log_trade(
                        condition_id=pos.condition_id,
                        title=pos.title,
                        category=pos.category,
                        side=pos.side,
                        action="sell",
                        shares=pos.shares,
                        price=pos.current_price,
                        amount_usdc=pos.market_value,
                        reasoning="; ".join(sig.reasons),
                        order_id=order_id,
                        pnl=pos.unrealized_pnl,
                        dry_run=False,
                    )

                    if ctx.verbose:
                        print(
                            f"    LIVE EXIT: sold {pos.side} "
                            f"{pos.title[:40]} → {order_id}"
                        )

                    exited_ids.add(pos.condition_id)

                except Exception as e:
                    if ctx.wal and intent_id:
                        ctx.wal.log_failed(intent_id, str(e))
                    trade_record["error"] = str(e)
                    ctx.errors.append(
                        f"Exit failed: {pos.title[:30]} — {e}"
                    )
                    logger.error("Exit execution failed: %s", e)
                    continue

            ctx.executed_trades.append(trade_record)

        # Remove exited positions + recalculate exposure
        if exited_ids:
            ctx.open_positions = [
                p for p in ctx.open_positions
                if p.condition_id not in exited_ids
            ]
            ctx.total_exposure = sum(
                p.cost_basis for p in ctx.open_positions
            )
            bankroll = ctx.usdc_balance + ctx.total_exposure
            ctx.exposure_pct = (
                ctx.total_exposure / bankroll if bankroll > 0 else 0.0
            )

            if ctx.verbose:
                print(
                    f"    Exited {len(exited_ids)} position(s). "
                    f"Exposure: ${ctx.total_exposure:.2f} "
                    f"({ctx.exposure_pct:.0%})"
                )

        return ctx


class FindEdgeStep:
    """Step 7: AI probability assessment via Claude API.

    Time-gated: skip if last edge-finding < SCAN_INTERVAL_SEC ago.
    Core innovation — uses Claude to estimate real probability,
    compare with market price to find mispricing.
    """
    name = "find_edge"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.config.settings import (
            MAX_MARKETS_FOR_AI, SCAN_INTERVAL_SEC, WEATHER_MAX_ASSESSMENTS,
        )
        from polymarket.strategy.edge_finder import assess_markets

        if ctx.risk_blocked:
            if ctx.verbose:
                print("    Skipped: risk blocked")
            return ctx

        if not ctx.filtered_markets:
            if ctx.verbose:
                print("    No markets to assess")
            return ctx

        # ─── Weather: always run (zero AI cost, deterministic forecast) ───
        weather_markets = [m for m in ctx.filtered_markets if m.category == "weather"]
        non_weather = [m for m in ctx.filtered_markets if m.category != "weather"]

        weather_edges = []
        if weather_markets:
            weather_edges = assess_markets(
                weather_markets,
                max_assessments=WEATHER_MAX_ASSESSMENTS,
                verbose=ctx.verbose,
            )
            if ctx.verbose:
                print(f"    Weather: {len(weather_edges)} edges (no gate, zero AI cost)")

        # ─── Non-weather: time-gated (Claude API cost) ───
        non_weather_edges = []
        last_edge_ts = ctx.state.get("last_edge_ts", 0)
        elapsed = time.time() - last_edge_ts
        if elapsed < SCAN_INTERVAL_SEC:
            if ctx.verbose and non_weather:
                skip_sec = int(SCAN_INTERVAL_SEC - elapsed)
                print(f"    Non-weather edge skipped (gate: {skip_sec}s left)")
        elif non_weather:
            if ctx.verbose:
                print(f"    Assessing up to {MAX_MARKETS_FOR_AI} non-weather markets via Claude...")
            non_weather_edges = assess_markets(
                non_weather,
                max_assessments=MAX_MARKETS_FOR_AI,
                verbose=ctx.verbose,
            )
            ctx.state_updates["last_edge_ts"] = time.time()

        ctx.edge_assessments = weather_edges + non_weather_edges

        if ctx.verbose:
            print(f"    Edge assessments: {len(ctx.edge_assessments)}")
            for ea in ctx.edge_assessments:
                if ea.edge_pct > 0:
                    print(
                        f"      {ea.side} {ea.title[:40]} "
                        f"edge:{ea.edge:+.1%} conf:{ea.confidence:.2f}"
                    )

        return ctx


class LogicalArbStep:
    """Step 7.3: Detect logical arbitrage across related markets.

    Zero AI cost. Groups markets by event_id, checks:
    - NegRisk: sum(YES prices) should be ~1.0
    - Ordering: higher threshold must have lower probability

    Arb signals bypass GTO (mathematically guaranteed edge).
    """
    name = "logical_arb"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.strategy.logical_arb import detect_arb
        from polymarket.core.context import EdgeAssessment

        # Scan ALL markets (not just filtered) to see full event groups
        # Pass gamma_client so negRisk events can fetch ALL sibling markets
        all_markets = list(ctx.scanned_markets)
        opps = detect_arb(
            all_markets,
            gamma_client=ctx.gamma_client,
            verbose=ctx.verbose,
        )
        ctx.arb_opportunities = opps

        if not opps:
            if ctx.verbose:
                print("    No logical arb opportunities")
            return ctx

        # Convert arb opportunities to EdgeAssessments
        for opp in opps:
            if opp.arb_type == "neg_risk_overpriced":
                # Find the most overpriced outcome to sell NO on
                most_overpriced = max(opp.markets, key=lambda m: m.yes_price)
                ctx.edge_assessments.append(EdgeAssessment(
                    condition_id=most_overpriced.condition_id,
                    title=most_overpriced.title,
                    category=most_overpriced.category or "arb",
                    market_price=most_overpriced.yes_price,
                    ai_probability=most_overpriced.yes_price - opp.edge_pct,
                    edge=-opp.edge_pct,
                    edge_pct=opp.edge_pct,
                    confidence=0.95,  # math-based, high confidence
                    side="NO",
                    reasoning=f"Logical arb: {opp.detail}",
                    signal_source="logical_arb",
                    gto_approved=True,
                    gto_reasoning="logical arb — GTO bypass",
                    gto_order_type="LIMIT",
                    is_dominant_strategy=True,
                ))

            elif opp.arb_type == "neg_risk_underpriced":
                # Find the most underpriced outcome to buy YES on
                most_underpriced = min(opp.markets, key=lambda m: m.yes_price)
                ctx.edge_assessments.append(EdgeAssessment(
                    condition_id=most_underpriced.condition_id,
                    title=most_underpriced.title,
                    category=most_underpriced.category or "arb",
                    market_price=most_underpriced.yes_price,
                    ai_probability=most_underpriced.yes_price + opp.edge_pct,
                    edge=opp.edge_pct,
                    edge_pct=opp.edge_pct,
                    confidence=0.95,
                    side="YES",
                    reasoning=f"Logical arb: {opp.detail}",
                    signal_source="logical_arb",
                    gto_approved=True,
                    gto_reasoning="logical arb — GTO bypass",
                    gto_order_type="LIMIT",
                    is_dominant_strategy=True,
                ))

            elif opp.arb_type == "ordering_violation":
                # Two markets: sell the overpriced one (NO), buy the underpriced one (YES)
                if len(opp.markets) >= 2:
                    m_low, m_high = opp.markets[0], opp.markets[1]
                    ctx.edge_assessments.append(EdgeAssessment(
                        condition_id=m_high.condition_id,
                        title=m_high.title,
                        category=m_high.category or "arb",
                        market_price=m_high.yes_price,
                        ai_probability=m_low.yes_price,
                        edge=-(m_high.yes_price - m_low.yes_price),
                        edge_pct=opp.edge_pct,
                        confidence=0.90,
                        side="NO",
                        reasoning=f"Ordering violation: {opp.detail}",
                        signal_source="logical_arb",
                        gto_approved=True,
                        gto_reasoning="logical arb — GTO bypass",
                        gto_order_type="LIMIT",
                        is_dominant_strategy=True,
                    ))

        # Ensure arb markets are in filtered_markets so GenerateSignalsStep can find them
        existing_cids = {m.condition_id for m in ctx.filtered_markets}
        for opp in opps:
            for m in opp.markets:
                if m.condition_id and m.condition_id not in existing_cids:
                    ctx.filtered_markets.append(m)
                    existing_cids.add(m.condition_id)

        if ctx.verbose:
            print(f"    Logical arb: {len(opps)} opportunities → {len(opps)} edge assessments added")

        return ctx


class GTOFilterStep:
    """Step 7.5: GTO filter — adverse selection, Nash equilibrium, fill quality.

    Zero AI cost. Classifies market type, scores adverse selection risk,
    checks Nash equilibrium, and either blocks or adjusts Kelly sizing.
    """
    name = "gto_filter"

    def run(self, ctx: PolyContext) -> PolyContext:
        from polymarket.strategy.gto import assess_gto_batch

        if not ctx.edge_assessments:
            return ctx

        # Build market list from filtered + scanned (edge may reference either)
        all_markets = list(ctx.filtered_markets)
        seen_ids = {m.condition_id for m in all_markets}
        for m in ctx.scanned_markets:
            if m.condition_id not in seen_ids:
                all_markets.append(m)
                seen_ids.add(m.condition_id)

        gto_results = assess_gto_batch(all_markets, ctx.edge_assessments)
        ctx.gto_assessments = gto_results

        # Attach GTO fields to each EdgeAssessment
        blocked = 0
        for ea in ctx.edge_assessments:
            # Logical arb signals are mathematically guaranteed — skip GTO
            if ea.signal_source == "logical_arb":
                continue

            gto = gto_results.get(ea.condition_id)
            if not gto:
                # Fail-open: no market match → pass through with visibility
                ea.gto_approved = True
                ea.gto_reasoning = "no GTO market match — pass-through"
                logger.warning("GTO: %s — no market match, pass-through", ea.title[:50])
                if ctx.verbose:
                    print(f"      [no-match] {ea.title[:40]} — GTO pass-through")
                continue

            ea.gto_type = gto.gto_type
            ea.adverse_selection_score = gto.adverse_selection_score
            ea.nash_equilibrium_score = gto.nash_equilibrium_score
            ea.unexploitability_score = gto.unexploitability_score
            ea.fill_quality = gto.fill_quality
            ea.gto_approved = gto.approved
            ea.gto_order_type = gto.order_type
            ea.gto_limit_offset = gto.limit_offset
            ea.gto_reasoning = gto.reasoning
            ea.is_dominant_strategy = gto.is_dominant_strategy

            if not gto.approved:
                blocked += 1

        ctx.gto_blocked_count = blocked

        if ctx.verbose:
            total = len(gto_results)
            print(f"    GTO: {total} assessed, {blocked} blocked")
            for ea in ctx.edge_assessments:
                gto = gto_results.get(ea.condition_id)
                if gto:
                    status = "APPROVED" if gto.approved else "BLOCKED"
                    print(
                        f"      [{gto.gto_type}] {ea.title[:40]} "
                        f"adv:{gto.adverse_selection_score:.2f} "
                        f"nash:{gto.nash_equilibrium_score:.2f} "
                        f"fill:{gto.fill_quality} {status}"
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
            weather_min_edge,
        )
        from polymarket.core.context import PolySignal
        from polymarket.strategy.spread_analyzer import analyze_spread

        if ctx.risk_blocked:
            return ctx

        for edge in ctx.edge_assessments:
            # GTO filter: skip blocked markets
            if not edge.gto_approved:
                if ctx.verbose:
                    print(f"      Skipped (GTO): {edge.title[:40]} — {edge.gto_reasoning}")
                continue

            # Category-aware thresholds
            if edge.category == "crypto_15m":
                min_edge = CRYPTO_15M_MIN_EDGE_PCT
                min_conf = CRYPTO_15M_CONFIDENCE_THRESHOLD
            elif edge.category == "weather":
                # Dynamic threshold: lower bar for cheap tail buckets (high payout)
                entry = edge.market_price if edge.side == "YES" else (1 - edge.market_price)
                min_edge = weather_min_edge(entry)
                min_conf = EDGE_CONFIDENCE_THRESHOLD
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

            # 15M + weather use taker/limit orders on thin books — skip spread/depth check
            if edge.category not in ("crypto_15m", "weather"):
                spread_info = analyze_spread(
                    market, ctx.exchange_client, MAX_SPREAD_PCT, MIN_BOOK_DEPTH_USDC,
                    side=edge.side,
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
                signal_source=edge.signal_source,
                # GTO fields
                gto_type=edge.gto_type,
                adverse_selection_score=edge.adverse_selection_score,
                unexploitability_score=edge.unexploitability_score,
                gto_order_type=edge.gto_order_type,
                gto_limit_offset=edge.gto_limit_offset,
                is_dominant_strategy=edge.is_dominant_strategy,
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

        from polymarket.config.settings import AUTOMATED_CATEGORIES

        if not ctx.signals:
            return ctx

        for signal in ctx.signals:
            if signal.bet_size_usdc <= 0:
                continue

            # ─── Scope guard: 只落注 automated categories ───
            if signal.category not in AUTOMATED_CATEGORIES:
                logger.warning(
                    "SKIP TRADE: %s category=%s 唔喺自動化範圍 (%s)",
                    signal.title[:30], signal.category, AUTOMATED_CATEGORIES,
                )
                continue

            # ─── Pre-exec liquidity re-check (crypto_15m only) ───
            # Scan-time liquidity can be up to 180s stale; re-check before real money
            if signal.category == "crypto_15m" and not ctx.dry_run:
                try:
                    from polymarket.exchange.gamma_client import GammaClient
                    _g = ctx.gamma_client or GammaClient()
                    fresh_market = _g.get_market(signal.condition_id)
                    fresh_liq = float(fresh_market.get("liquidityNum", 0) or 0)
                    if fresh_liq < 100:
                        logger.warning(
                            "SKIP TRADE: %s liquidity dried up ($%.0f < $100)",
                            signal.title[:30], fresh_liq,
                        )
                        continue
                except Exception as e:
                    logger.warning("Pre-exec liquidity check failed: %s (proceeding)", e)

            trade_record = {
                "condition_id": signal.condition_id,
                "title": signal.title,
                "category": signal.category,
                "side": signal.side,
                "amount": signal.bet_size_usdc,
                "price": signal.price,
                "edge": signal.edge,
                "confidence": signal.confidence,
                "gto_type": signal.gto_type,
                "gto_order_type": signal.gto_order_type,
                "gto_limit_offset": signal.gto_limit_offset,
                "adverse_selection": signal.adverse_selection_score,
                "unexploitability": signal.unexploitability_score,
                "is_dominant_strategy": signal.is_dominant_strategy,
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

                    # Execute buy via SDK — respect GTO order type
                    # MARKET → price=0 (FOK taker); LIMIT → price=signal.price (GTC)
                    exec_price = 0 if signal.gto_order_type == "MARKET" else signal.price
                    if signal.gto_limit_offset and signal.gto_order_type == "LIMIT":
                        logger.info(
                            "GTO limit_offset=%.4f logged (orderbook mid TBD Phase 2)",
                            signal.gto_limit_offset,
                        )
                    result = ctx.exchange_client.buy_shares(
                        token_id=signal.token_id,
                        amount_usdc=signal.bet_size_usdc,
                        price=exec_price,
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

            # ─── Hyperliquid Hedge (crypto_15m only) ───
            self._try_open_hedge(ctx, signal, trade_record)

        return ctx

    @staticmethod
    def _try_open_hedge(ctx: PolyContext, signal, trade_record: dict):
        """Open HL hedge after successful Poly trade (crypto_15m only)."""
        from polymarket.config.settings import (
            HEDGE_ENABLED, HEDGE_USD, HEDGE_LEVERAGE, HEDGE_SYMBOL, HEDGE_CATEGORIES,
        )

        if not HEDGE_ENABLED:
            return
        if signal.category not in HEDGE_CATEGORIES:
            return
        if trade_record.get("error"):
            return

        # Inverse direction: Poly YES (predict UP) → HL SHORT (hedge against UP being wrong)
        hedge_direction = "SHORT" if signal.side == "YES" else "LONG"

        try:
            if ctx.hl_hedge_client is None:
                from polymarket.exchange.hl_hedge_client import HLHedgeClient
                ctx.hl_hedge_client = HLHedgeClient(dry_run=ctx.dry_run)

            result = ctx.hl_hedge_client.open_hedge(
                direction=hedge_direction,
                usdc_size=HEDGE_USD,
                leverage=HEDGE_LEVERAGE,
                symbol=HEDGE_SYMBOL,
            )

            trade_record["hedge"] = {
                "direction": hedge_direction,
                "usdc_size": HEDGE_USD,
                "leverage": HEDGE_LEVERAGE,
                "status": result.get("status", "unknown"),
            }

            # Record hedge info on the position (last appended)
            for pos in reversed(ctx.open_positions):
                if pos.condition_id == signal.condition_id:
                    pos.hedge_side = hedge_direction
                    pos.hedge_size = result.get("qty", 0.0)
                    pos.hedge_entry_px = result.get("mid_px", 0.0)
                    break

            if ctx.verbose:
                status = result.get("status", "?")
                print(
                    f"    HEDGE ({status}): {hedge_direction} {HEDGE_SYMBOL} "
                    f"${HEDGE_USD:.0f} at {HEDGE_LEVERAGE}x"
                )

        except Exception as e:
            logger.warning("HL hedge open failed: %s", e)
            trade_record["hedge_error"] = str(e)
            ctx.warnings.append(f"HL hedge failed: {e}")


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
    """Build the 14-step Polymarket pipeline.

    Pipeline order:
      1.   read_state          — POLYMARKET_STATE.json
      2.   replay_wal          — WAL crash recovery (live only)
      3.   safety_check        — circuit breaker, daily loss, cooldown
      4.   scan_markets        — Gamma API → filter crypto/weather
      5.   check_positions     — sync positions + USDC balance
      5.5  merge_check         — detect mergeable YES+NO pairs (report only)
      6.   manage_positions    — exit triggers (drift, PnL, expiry)
      6.5  close_hedge         — close HL hedge for resolved/exited positions
      6.7  execute_exits       — sell positions flagged by exit triggers
      7.   find_edge           — Claude API probability assessment
      7.3  logical_arb         — detect pricing contradictions across related markets
      7.5  gto_filter          — GTO: adverse selection, Nash eq, fill quality
      8.   generate_signals    — edge > threshold → PolySignal (skips GTO-blocked)
      9.   size_positions      — half Kelly (scaled by unexploitability)
     10.   execute_trades      — place orders on Polymarket + open HL hedge
     11.   write_state         — update POLYMARKET_STATE.json
     12.   send_reports        — Telegram notification
    """
    pipeline = Pipeline()
    pipeline.add_step(ReadStateStep())         # 1
    pipeline.add_step(ReplayWALStep())         # 2
    pipeline.add_step(SafetyCheckStep())       # 3
    pipeline.add_step(ScanMarketsStep())       # 4
    pipeline.add_step(CheckPositionsStep())    # 5
    pipeline.add_step(MergeCheckStep())        # 5.5
    pipeline.add_step(ManagePositionsStep())   # 6
    pipeline.add_step(CloseHedgeStep())        # 6.5
    pipeline.add_step(ExecuteExitStep())       # 6.7
    pipeline.add_step(FindEdgeStep())          # 7
    pipeline.add_step(LogicalArbStep())        # 7.3
    pipeline.add_step(GTOFilterStep())         # 7.5
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
