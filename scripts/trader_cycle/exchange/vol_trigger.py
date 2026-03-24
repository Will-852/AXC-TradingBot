"""
vol_trigger.py — Read volume spike triggers from indicator_engine

indicator_engine monitors open 1H klines for volume projection.
When projected volume > 5x AND squeeze_ready (elapsed > 30%) → writes to market:vol_trigger.
This step reads those triggers and injects them into CycleContext.

設計決定：
- 非阻塞 xrange（唔用 xreadgroup — triggers 係 broadcast，唔需要 consumer group）
- 讀完即 xdel — 避免重複 process
- 每次 cycle 最多讀 10 triggers
"""

from __future__ import annotations

import logging

from ..core.context import CycleContext

log = logging.getLogger(__name__)

MAX_TRIGGERS = 10


def _read_triggers() -> list[dict]:
    """Read pending volume triggers from Redis stream."""
    try:
        from shared_infra.redis_bus import get_redis, is_available, STREAM_VOL_TRIGGER
        if not is_available():
            return []
        r = get_redis()
        if r is None:
            return []

        # Read all pending entries (from beginning)
        raw = r.xrange(STREAM_VOL_TRIGGER, count=MAX_TRIGGERS)
        if not raw:
            return []

        triggers = []
        entry_ids = []
        for entry_id, fields in raw:
            # Redis returns bytes — decode
            decoded = {}
            for k, v in fields.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                decoded[key] = val
            triggers.append(decoded)
            entry_ids.append(entry_id)

        # Delete processed entries to avoid re-reading
        if entry_ids:
            r.xdel(STREAM_VOL_TRIGGER, *entry_ids)

        return triggers
    except Exception as exc:
        log.warning("Failed to read vol triggers: %s", exc)
        return []


class CheckVolTriggersStep:
    """Step 4.7: Read event-driven volume spike triggers.

    Reads market:vol_trigger stream written by indicator_engine.
    Injects into ctx.vol_triggers for EvaluateSignalsStep to consider.
    """

    name = "check_vol_triggers"

    def run(self, ctx: CycleContext) -> CycleContext:
        triggers = _read_triggers()

        if triggers:
            ctx.vol_triggers = triggers
            for t in triggers:
                log.info(
                    "Vol trigger: %s projected=%sx dir=%s",
                    t.get("symbol"), t.get("projected_ratio"), t.get("direction"),
                )
                if ctx.verbose:
                    print(
                        f"    VOL TRIGGER: {t.get('symbol')} "
                        f"projected={t.get('projected_ratio')}x "
                        f"dir={t.get('direction')}"
                    )

        return ctx
