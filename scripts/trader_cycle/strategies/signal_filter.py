"""
signal_filter.py — Per-asset confidence gate + mode penalty + persistence filter.

Bridges backtest optimizer tuning params into the live pipeline.
Runs between EvaluateSignalsStep (step 9) and SelectSignalStep (step 10).

Logic mirrors backtest/engine.py lines 691-736 exactly:
  1. Mode penalty: penalize off-mode strategies
  2. Confidence gate: discard below threshold
  3. Persistence: require N consecutive same-signal cycles
  4. Cooldown: block new signals for N hours after a trade closes

State persists across LaunchAgent restarts via JSON file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from ..core.context import CycleContext

log = logging.getLogger(__name__)

# State file for cross-cycle persistence tracking
_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "shared",
)
_STATE_FILE = os.path.join(_STATE_DIR, ".signal_filter_state.json")


def _load_state() -> dict:
    """Load persistence state from disk (atomic read)."""
    try:
        with open(_STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    """Save persistence state to disk (atomic write)."""
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


def _get_params(symbol: str) -> dict:
    """Import lazily to avoid circular imports at module level."""
    from config.params import get_signal_filter_params
    return get_signal_filter_params(symbol)


class SignalFilterStep:
    """Step 9.5: Per-asset conf_gate + mode penalty + persistence + cooldown.

    Exactly mirrors backtest/engine.py signal filter logic so that
    optimizer-tuned params produce the same filtering in live.
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

        for signal in ctx.signals:
            params = _get_params(signal.pair)

            # ── 1. Cooldown check ──
            last_close_iso = cooldown_state.get(signal.pair)
            if last_close_iso:
                try:
                    last_close = datetime.fromisoformat(last_close_iso)
                    elapsed_h = (ctx.timestamp.astimezone(timezone.utc)
                                 - last_close.astimezone(timezone.utc)).total_seconds() / 3600
                    if elapsed_h < params["cooldown_hours"]:
                        if ctx.verbose:
                            log.info(
                                "COOLDOWN %s: %.1fh / %dh",
                                signal.pair, elapsed_h, params["cooldown_hours"],
                            )
                        continue
                except (ValueError, TypeError):
                    pass

            # ── 2. Mode penalty ──
            mode_penalties = params["mode_affinity"].get(
                ctx.market_mode, params["mode_default_penalty"]
            )
            penalty = mode_penalties.get(signal.strategy, 0.0)
            adj_conf = signal.confidence + penalty

            # ── 3. Confidence gate ──
            gate = params["conf_gate"].get(signal.strategy, 0.50)
            if adj_conf < gate:
                if ctx.verbose:
                    log.info(
                        "GATED %s %s %s: conf=%.2f + pen=%.2f = %.2f < gate=%.2f",
                        signal.pair, signal.direction, signal.strategy,
                        signal.confidence, penalty, adj_conf, gate,
                    )
                continue

            # Apply adjusted confidence
            signal.confidence = max(adj_conf, 0.0)

            # ── 4. Persistence check ──
            threshold = params["persistence"].get(signal.strategy, 0)
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
                # Reset on pass
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
            print(
                f"    Signal filter: {len(filtered)} passed, "
                f"{dropped} filtered (from {raw_count} raw)"
            )

        return ctx
