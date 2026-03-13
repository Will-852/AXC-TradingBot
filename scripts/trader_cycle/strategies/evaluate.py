"""
evaluate.py — Strategy evaluation + signal selection pipeline steps

EvaluateSignalsStep: runs active strategy on all pairs
SelectSignalStep: picks the strongest signal from candidates
"""

from __future__ import annotations

from ..config.settings import PRIMARY_TIMEFRAME, ALLOW_TREND, ALLOW_RANGE

from ..core.context import CycleContext, Signal
from ..core.registry import StrategyRegistry

# Sentiment risk filter threshold (read from params.py)
try:
    from config.params import BEARISH_BLOCK_LONG_CONF
except ImportError:
    BEARISH_BLOCK_LONG_CONF = 0.70


class EvaluateSignalsStep:
    """
    Step 9: Evaluate entry signals for all pairs.
    Uses the strategy registered for the current market mode.
    Skips pairs that are in no_trade_reasons.
    """
    name = "evaluate_signals"

    def run(self, ctx: CycleContext) -> CycleContext:
        # Don't evaluate if risk-blocked
        if ctx.risk_blocked:
            if ctx.verbose:
                print("    Signals: SKIPPED (risk blocked)")
            return ctx

        strategy = StrategyRegistry.get(ctx.market_mode)

        # ── Profile strategy gate ──
        if ctx.market_mode == "TREND" and not ALLOW_TREND:
            if ctx.verbose:
                print("    Signals: TREND disabled by profile (allow_trend=False)")
            return ctx
        if ctx.market_mode == "RANGE" and not ALLOW_RANGE:
            if ctx.verbose:
                print("    Signals: RANGE disabled by profile (allow_range=False)")
            return ctx

        if not strategy:
            if ctx.verbose:
                print(f"    Signals: no strategy for mode '{ctx.market_mode}'")
            return ctx

        if not ctx.mode_confirmed:
            if ctx.verbose:
                print(f"    Signals: mode '{ctx.market_mode}' not yet confirmed, skipping evaluation")
            return ctx

        # Build set of no-trade pairs
        no_trade_pairs = self._get_no_trade_pairs(ctx)

        # Evaluate each pair
        for symbol in ctx.indicators:
            if symbol in no_trade_pairs:
                if ctx.verbose:
                    print(f"    {symbol}: skipped (no-trade)")
                continue

            pair_indicators = ctx.indicators[symbol]

            # Check required timeframes
            missing_tf = [
                tf for tf in strategy.required_timeframes
                if tf not in pair_indicators
            ]
            if missing_tf:
                if ctx.verbose:
                    print(f"    {symbol}: skipped (missing {missing_tf})")
                continue

            # Run strategy
            signal = strategy.evaluate(symbol, pair_indicators, ctx)

            if signal:
                # Preserve original score for position sizing (before any boosts)
                signal.original_score = signal.score

                # Re-entry signal boost: +0.5 score if pair/direction matches
                if (ctx.reentry_eligible
                        and signal.pair == ctx.reentry_pair
                        and signal.direction == ctx.reentry_direction):
                    signal.score += 0.5
                    signal.reasons.append("REENTRY_BOOST +0.5")
                    if ctx.verbose:
                        print(f"    {symbol}: re-entry boost applied (score +0.5)")

                ctx.signals.append(signal)
                if ctx.verbose:
                    print(f"    {symbol}: {signal.direction} {signal.strength} "
                          f"(score={signal.score}) via {signal.strategy}")
            elif ctx.verbose:
                print(f"    {symbol}: no signal")

        # ─── Sentiment risk filter: block LONGs when strongly bearish ───
        self._filter_bearish_longs(ctx)

        if ctx.verbose:
            print(f"    Total signals: {len(ctx.signals)}")

        return ctx

    def _filter_bearish_longs(self, ctx: CycleContext) -> None:
        """Remove LONG signals when news sentiment is strongly bearish."""
        sentiment = ctx.news_sentiment
        if not sentiment:
            return

        overall = sentiment.get("overall_sentiment", "")
        confidence = sentiment.get("confidence", 0)

        if overall != "bearish" or confidence < BEARISH_BLOCK_LONG_CONF:
            return

        before = len(ctx.signals)
        blocked = [s for s in ctx.signals if s.direction == "LONG"]
        ctx.signals = [s for s in ctx.signals if s.direction != "LONG"]

        for s in blocked:
            ctx.no_trade_reasons.append(
                f"SENTIMENT_RISK: {s.pair} LONG blocked "
                f"(bearish {confidence:.0%})"
            )

        if ctx.verbose and blocked:
            print(f"    [SENTIMENT] Blocked {len(blocked)} LONG signal(s) "
                  f"(bearish {confidence:.0%} > {BEARISH_BLOCK_LONG_CONF:.0%})")

    def _get_no_trade_pairs(self, ctx: CycleContext) -> set[str]:
        """Extract pair symbols from no_trade_reasons."""
        no_trade = set()
        for reason in ctx.no_trade_reasons:
            # Reasons contain the symbol name (e.g., "LOW_VOLUME: BTCUSDT ...")
            for symbol in ctx.indicators:
                if symbol in reason:
                    no_trade.add(symbol)
        return no_trade


class SelectSignalStep:
    """
    Step 10: Select the best signal from all candidates.
    Ranking: highest score wins. Ties broken by BTC > ETH > XRP > XAG.
    """
    name = "select_signal"

    # Priority order for tie-breaking
    PAIR_PRIORITY = {
        "BTCUSDT": 4, "ETHUSDT": 3, "SOLUSDT": 3,
        "XRPUSDT": 2, "POLUSDT": 2,
        "XAGUSDT": 1, "XAUUSDT": 1,
    }

    def run(self, ctx: CycleContext) -> CycleContext:
        if not ctx.signals:
            if ctx.verbose:
                print("    Selection: no signals to select from")
            return ctx

        # Sort by (score DESC, pair_priority DESC)
        sorted_signals = sorted(
            ctx.signals,
            key=lambda s: (s.score, self.PAIR_PRIORITY.get(s.pair, 0)),
            reverse=True,
        )

        ctx.selected_signal = sorted_signals[0]

        if ctx.verbose:
            s = ctx.selected_signal
            print(f"    Selected: {s.pair} {s.direction} {s.strategy} "
                  f"(score={s.score}, strength={s.strength})")
            if len(sorted_signals) > 1:
                print(f"    Alternatives: {', '.join(f'{x.pair} {x.direction}' for x in sorted_signals[1:])}")

        return ctx
