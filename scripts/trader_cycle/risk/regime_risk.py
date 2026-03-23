"""
regime_risk.py — Signal Confidence → Zone 映射（2-Zone system）

2-Zone system (2026-03-23):
  Zone A (1-10x):  confidence < ZONE_B_THRESHOLD → 穩陣
  Zone B (11-20x): confidence >= ZONE_B_THRESHOLD → 確定性高，高槓桿

設計決定：
  - 用 signal confidence 做 zone 分界（唔係 vol regime）
  - Vol regime 仍然影響 No-Trade Gate（extreme vol → block），但唔再選 profile
  - 3% margin per trade，兩個 zone 只差喺 leverage + SL width
  - Cold start / 冇 signal → Zone A（最安全）
"""

from __future__ import annotations

import logging
import os

from ..core.context import CycleContext

log = logging.getLogger(__name__)

# ─── Zone Selection ───
ZONE_B_THRESHOLD = 0.70  # confidence >= 0.70 → Zone B (高槓桿)
ZONE_B_CONFIRM_CYCLES = 2  # 連續 N cycles 先升 Zone B（防 spike）
# 降級（Zone B → A）= 即時（安全優先）

# 跨 cycle state（module-level，persist across pipeline runs）
_zone_b_consecutive = 0


class SelectRiskProfileStep:
    """Pipeline step: 初步 zone 選擇。

    設 ctx.active_risk_profile → "zone_a" or "zone_b"。

    呢步用 regime_confidence（Step 5），唔係 signal confidence（Step 9）。
    升 Zone B 需要連續 ZONE_B_CONFIRM_CYCLES 個 cycle 通過 threshold。
    降回 Zone A 即時生效（安全優先）。
    """

    name = "select_risk_profile"

    def run(self, ctx: CycleContext) -> CycleContext:
        global _zone_b_consecutive

        confidence = ctx.regime_confidence

        if confidence >= ZONE_B_THRESHOLD:
            _zone_b_consecutive += 1
        else:
            _zone_b_consecutive = 0

        # 升 Zone B 需要連續 N cycles 確認；降級即時
        if _zone_b_consecutive >= ZONE_B_CONFIRM_CYCLES:
            auto_zone = "zone_b"
        else:
            auto_zone = "zone_a"

        # 檢查用戶手動設定（via dashboard/telegram）
        # params.py ACTIVE_PROFILE 可能被 UI 改為 "ZONE_B"
        manual_zone = _get_manual_zone()
        if manual_zone and manual_zone != auto_zone:
            if manual_zone == "zone_b" and auto_zone == "zone_a":
                # 用戶手動要求 Zone B 但 confidence 唔夠 → 尊重用戶，但 log warning
                log.warning(
                    "Manual ZONE_B override active (params.py), but regime_conf=%.2f < %.2f. "
                    "Using ZONE_B as requested — user bears risk.",
                    confidence, ZONE_B_THRESHOLD,
                )
                zone = "zone_b"
            else:
                zone = auto_zone
        else:
            zone = auto_zone

        ctx.active_risk_profile = zone

        if ctx.verbose:
            log.info(
                "Zone: %s (regime_conf=%.2f, consecutive=%d/%d, vol=%s, manual=%s)",
                zone.upper(), confidence,
                _zone_b_consecutive, ZONE_B_CONFIRM_CYCLES,
                ctx.volatility_regime, manual_zone or "none",
            )

        return ctx


def _get_manual_zone() -> str | None:
    """Read ACTIVE_PROFILE from params.py to detect manual zone override.

    Returns "zone_a" or "zone_b" if manually set, None on error.
    """
    try:
        import importlib.util
        params_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)
            ))),
            "config", "params.py",
        )
        spec = importlib.util.spec_from_file_location("_params_check", params_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        name = getattr(mod, "ACTIVE_PROFILE", "ZONE_A").lower()
        if name in ("zone_a", "zone_b"):
            return name
    except Exception:
        pass
    return None
