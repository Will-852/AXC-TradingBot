"""
evaluate.py — Strategy evaluation + signal selection pipeline steps

Phase 1 重構：
  - 去 mode gate：全策略同時跑
  - Normalized percentile rank：跨策略公平比較
  - Cross-pair correlation boost：落後品種 +0.15
  - 排序：normalized_rank → raw confidence → PAIR_PRIORITY
"""

from __future__ import annotations

import logging

from ..config.settings import PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME
from ..core.context import CycleContext, Signal
from ..core.registry import StrategyRegistry
from .liq_signal import apply_liq_boost

log = logging.getLogger(__name__)

# Sentiment risk filter threshold
try:
    from config.params import BEARISH_BLOCK_LONG_CONF
except ImportError:
    BEARISH_BLOCK_LONG_CONF = 0.70

# Cross-pair correlation boost
_CRYPTO_PAIRS = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
_CORRELATION_BOOST = 0.15
_CORRELATION_STD_THRESHOLD = 2.0  # 偏差 > 2σ


class EvaluateSignalsStep:
    """
    Step 9: Run ALL strategies on all pairs (no mode gate).

    Phase 1 改動：
      - 唔再只跑 current mode 嘅策略，全部跑
      - 每個信號帶 confidence (0-1)
      - 後續由 SelectSignalStep 做 normalized rank 排序
    """
    name = "evaluate_signals"

    def run(self, ctx: CycleContext) -> CycleContext:
        if ctx.risk_blocked:
            if ctx.verbose:
                print("    Signals: SKIPPED (risk blocked)")
            return ctx

        no_trade_pairs = self._get_no_trade_pairs(ctx)
        all_strategies = StrategyRegistry.all_strategies()

        if not all_strategies:
            if ctx.verbose:
                print("    Signals: no strategies registered")
            return ctx

        # ─── Run ALL strategies on all pairs ───
        for mode_name, strategy in all_strategies.items():
            for symbol in ctx.indicators:
                if symbol in no_trade_pairs:
                    continue

                pair_indicators = ctx.indicators[symbol]

                # Check required timeframes
                missing_tf = [
                    tf for tf in strategy.required_timeframes
                    if tf not in pair_indicators
                ]
                if missing_tf:
                    continue

                signal = strategy.evaluate(symbol, pair_indicators, ctx)

                if signal:
                    signal.original_score = signal.score

                    # Re-entry boost
                    if (ctx.reentry_eligible
                            and signal.pair == ctx.reentry_pair
                            and signal.direction == ctx.reentry_direction):
                        signal.score += 0.5
                        signal.reasons.append("REENTRY_BOOST +0.5")

                    # Liquidation boost
                    liq_boost = apply_liq_boost(signal, ctx.liq_events)
                    if liq_boost > 0:
                        signal.score += liq_boost
                        signal.reasons.append(f"LIQ_BOOST +{liq_boost}")

                    ctx.signals.append(signal)
                    if ctx.verbose:
                        print(
                            f"    {symbol}: {signal.direction} {signal.strength} "
                            f"conf={signal.confidence:.2f} via {signal.strategy}"
                        )

        # ─── Cross-pair correlation boost ───
        self._apply_correlation_boost(ctx)

        # ─── Sentiment filter ───
        self._filter_bearish_longs(ctx)

        if ctx.verbose:
            print(f"    Total signals: {len(ctx.signals)}")

        return ctx

    def _apply_correlation_boost(self, ctx: CycleContext) -> None:
        """Boost lagging crypto pair's signal when peers have moved.

        用 4H % change（唔係 24H，減少滯後）。
        如果一隻明顯落後（偏差 > 2σ）→ 該品種同方向信號 confidence +0.15。
        """
        # Collect 4H price changes for crypto pairs
        changes: dict[str, float] = {}
        for sym in _CRYPTO_PAIRS:
            ind_4h = ctx.indicators.get(sym, {}).get(PRIMARY_TIMEFRAME, {})
            # Use close/open change as 4H proxy
            close = ind_4h.get("price")
            open_price = ind_4h.get("open")
            if close and open_price and open_price > 0:
                changes[sym] = (close - open_price) / open_price
            else:
                # Fallback to market data 24h (less ideal but available)
                md = ctx.market_data.get(sym)
                if md and md.price_change_24h_pct:
                    changes[sym] = md.price_change_24h_pct / 100.0

        if len(changes) < 2:
            return

        # Find outlier (lagging pair)
        values = list(changes.values())
        mean = sum(values) / len(values)
        if len(values) > 1:
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std = variance ** 0.5
        else:
            return

        if std < 1e-8:
            return  # all moved similarly

        for sym, change in changes.items():
            z = (change - mean) / std
            # Lagging = significantly different from mean
            if abs(z) < _CORRELATION_STD_THRESHOLD:
                continue

            # Determine expected direction: if peers dropped, lagging should drop too
            if mean < 0 and z > _CORRELATION_STD_THRESHOLD:
                # Peers dropped but this one didn't → expect SHORT
                boost_direction = "SHORT"
            elif mean > 0 and z < -_CORRELATION_STD_THRESHOLD:
                # Peers rose but this one didn't → expect LONG
                boost_direction = "LONG"
            else:
                continue

            # Apply boost to matching signals
            for signal in ctx.signals:
                if signal.pair == sym and signal.direction == boost_direction:
                    signal.confidence = min(signal.confidence + _CORRELATION_BOOST, 1.0)
                    signal.reasons.append(
                        f"CORR_BOOST: +{_CORRELATION_BOOST} "
                        f"(peers mean={mean:+.2%}, z={z:+.1f})"
                    )
                    if ctx.verbose:
                        print(f"    {sym}: correlation boost +{_CORRELATION_BOOST} ({boost_direction})")

    def _filter_bearish_longs(self, ctx: CycleContext) -> None:
        """Remove LONG signals when news sentiment is strongly bearish."""
        sentiment = ctx.news_sentiment
        if not sentiment:
            return
        overall = sentiment.get("overall_sentiment", "")
        confidence = sentiment.get("confidence", 0)
        if overall != "bearish" or confidence < BEARISH_BLOCK_LONG_CONF:
            return
        blocked = [s for s in ctx.signals if s.direction == "LONG"]
        ctx.signals = [s for s in ctx.signals if s.direction != "LONG"]
        for s in blocked:
            ctx.no_trade_reasons.append(
                f"SENTIMENT_RISK: {s.pair} LONG blocked (bearish {confidence:.0%})"
            )
        if ctx.verbose and blocked:
            print(f"    [SENTIMENT] Blocked {len(blocked)} LONG signal(s)")

    def _get_no_trade_pairs(self, ctx: CycleContext) -> set[str]:
        no_trade = set()
        for reason in ctx.no_trade_reasons:
            for symbol in ctx.indicators:
                if symbol in reason:
                    no_trade.add(symbol)
        return no_trade


class SelectSignalStep:
    """
    Step 10: Normalized rank selection.

    1. Within-strategy percentile rank (0-1)
    2. Sort by normalized_rank → raw confidence → PAIR_PRIORITY
    3. 單個信號 rank = 1.0（該策略最好嘅）
    """
    name = "select_signal"

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

        # ─── Within-strategy percentile rank ───
        strategies_seen: dict[str, list[Signal]] = {}
        for s in ctx.signals:
            strategies_seen.setdefault(s.strategy, []).append(s)

        for strat_name, group in strategies_seen.items():
            if len(group) == 1:
                group[0].normalized_rank = 1.0
                continue
            # Sort by confidence ascending for percentile calc
            sorted_group = sorted(group, key=lambda s: s.confidence)
            for i, sig in enumerate(sorted_group):
                sig.normalized_rank = i / max(len(sorted_group) - 1, 1)

        # ─── Sort: normalized_rank DESC → confidence DESC → pair priority DESC ───
        sorted_signals = sorted(
            ctx.signals,
            key=lambda s: (
                s.normalized_rank,
                s.confidence,
                self.PAIR_PRIORITY.get(s.pair, 0),
            ),
            reverse=True,
        )

        ctx.selected_signal = sorted_signals[0]

        if ctx.verbose:
            s = ctx.selected_signal
            print(
                f"    Selected: {s.pair} {s.direction} {s.strategy} "
                f"(rank={s.normalized_rank:.2f}, conf={s.confidence:.2f}, "
                f"score={s.score:.1f})"
            )
            if len(sorted_signals) > 1:
                alts = ", ".join(
                    f"{x.pair} {x.direction}/{x.strategy} "
                    f"(rank={x.normalized_rank:.2f})"
                    for x in sorted_signals[1:3]
                )
                print(f"    Alternatives: {alts}")

        return ctx
