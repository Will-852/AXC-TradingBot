"""
signal_filter.py — Regime-conditional signal filter.

Runs between EvaluateSignalsStep (step 9) and SelectSignalStep (step 10).

Filter order:
  0. Regime rule check: BLOCK / BOOST / tighter gate per (pair, vol, mode, strategy)
  1. Cooldown: block new signals N hours after a trade closes
  2. Mode penalty: penalize off-mode strategies
  3. Confidence gate: discard below threshold (may be overridden by regime rule)
  4. Persistence: require N consecutive same-signal cycles

Data source: backtest/regime_analysis.py cross-period stability test (180d+360d).
State persists across LaunchAgent restarts via JSON file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from config.params import (
    SIGNAL_CONF_GATE,
    SIGNAL_CONF_GATE_PER_SYMBOL,
    SIGNAL_MODE_AFFINITY,
    SIGNAL_MODE_DEFAULT_PENALTY,
    SIGNAL_PERSISTENCE,
    SIGNAL_COOLDOWN_HOURS,
)
from ..core.context import CycleContext

log = logging.getLogger(__name__)

_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "shared",
)
_STATE_FILE = os.path.join(_STATE_DIR, ".signal_filter_state.json")


def _load_state() -> dict:
    try:
        with open(_STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_STATE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _get_regime_rule(pair: str, vol_regime: str, market_mode: str, strategy: str):
    """Lazy import to avoid circular imports."""
    from config.params import get_regime_rule
    return get_regime_rule(pair, vol_regime, market_mode, strategy)


class SignalFilterStep:
    """Step 9.5: Regime-conditional signal filter.

    Checks REGIME_SIGNAL_RULES first (BLOCK / conf_gate override),
    then applies mode penalty + default conf_gate + persistence.
    """
    name = "signal_filter"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not ctx.signals:
            return ctx

        state = _load_state()
        persist_state = state.get("persist", {})
        cooldown_state = state.get("cooldown", {})

        raw_count = len(ctx.signals)
        filtered = []
        blocked_count = 0

        vol_regime = ctx.volatility_regime

        for signal in ctx.signals:
            # ── 0. Regime rule check ──
            rule = _get_regime_rule(
                signal.pair, vol_regime, ctx.market_mode, signal.strategy
            )
            if rule == "BLOCK":
                if ctx.verbose:
                    log.info(
                        "REGIME_BLOCK %s %s %s [%s×%s]",
                        signal.pair, signal.direction, signal.strategy,
                        vol_regime, ctx.market_mode,
                    )
                blocked_count += 1
                continue

            # Determine conf_gate: regime rule → per-symbol → global default
            if isinstance(rule, dict) and "conf_gate" in rule:
                gate = rule["conf_gate"]
            elif signal.pair in SIGNAL_CONF_GATE_PER_SYMBOL:
                gate = SIGNAL_CONF_GATE_PER_SYMBOL[signal.pair].get(
                    signal.strategy, 0.50
                )
            else:
                gate = SIGNAL_CONF_GATE.get(signal.strategy, 0.50)

            # ── 1. Cooldown check ──
            last_close_iso = cooldown_state.get(signal.pair)
            if last_close_iso:
                try:
                    last_close = datetime.fromisoformat(last_close_iso)
                    elapsed_h = (ctx.timestamp.astimezone(timezone.utc)
                                 - last_close.astimezone(timezone.utc)).total_seconds() / 3600
                    if elapsed_h < SIGNAL_COOLDOWN_HOURS:
                        if ctx.verbose:
                            log.info(
                                "COOLDOWN %s: %.1fh / %dh",
                                signal.pair, elapsed_h, SIGNAL_COOLDOWN_HOURS,
                            )
                        continue
                except (ValueError, TypeError):
                    pass

            # ── 2. Mode penalty ──
            mode_penalties = SIGNAL_MODE_AFFINITY.get(
                ctx.market_mode, SIGNAL_MODE_DEFAULT_PENALTY
            )
            penalty = mode_penalties.get(signal.strategy, 0.0)
            adj_conf = signal.confidence + penalty

            # ── 3. Confidence gate ──
            if adj_conf < gate:
                if ctx.verbose:
                    log.info(
                        "GATED %s %s %s: conf=%.2f + pen=%.2f = %.2f < gate=%.2f [%s×%s]",
                        signal.pair, signal.direction, signal.strategy,
                        signal.confidence, penalty, adj_conf, gate,
                        vol_regime, ctx.market_mode,
                    )
                continue

            signal.confidence = max(adj_conf, 0.0)

            # ── 4. Persistence check ──
            threshold = SIGNAL_PERSISTENCE.get(signal.strategy, 0)
            if threshold > 1:
                key = signal.pair
                ps = persist_state.get(key, {})
                if (ps.get("strategy") == signal.strategy
                        and ps.get("direction") == signal.direction):
                    ps["count"] = ps.get("count", 0) + 1
                else:
                    ps = {"strategy": signal.strategy,
                          "direction": signal.direction, "count": 1}
                persist_state[key] = ps

                if ps["count"] < threshold:
                    if ctx.verbose:
                        log.info(
                            "PERSIST_WAIT %s %s %s: %d/%d",
                            signal.pair, signal.direction, signal.strategy,
                            ps["count"], threshold,
                        )
                    continue
                ps["count"] = 0

            filtered.append(signal)

        # ── Update cooldown from closed positions this cycle ──
        for cp in ctx.closed_positions:
            cooldown_state[cp.pair] = ctx.timestamp.astimezone(
                timezone.utc
            ).isoformat()

        ctx.signals = filtered

        # Save state for next cycle
        state["persist"] = persist_state
        state["cooldown"] = cooldown_state
        try:
            _save_state(state)
        except Exception as e:
            log.warning("signal_filter state save failed: %s", e)

        if ctx.verbose:
            dropped = raw_count - len(filtered)
            log.info(
                "Signal filter: %d passed, %d filtered (%d regime-blocked) [%s×%s]",
                len(filtered), dropped, blocked_count,
                vol_regime, ctx.market_mode,
            )

        return ctx
