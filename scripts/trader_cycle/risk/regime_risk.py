"""
regime_risk.py — 波幅 → 風險 Profile 映射

設計決定：
  - 波幅 regime 由 HMM/BOCPD 決定（mode_detector 寫入 ctx.volatility_regime）
  - 映射簡單直覺：低波幅 → 激進（窄SL高槓桿）；高波幅 → 穩健（闊SL低槓桿）
  - Profile 載入用現有 config/profiles/ 系統，唔重複實作
  - Cold start（NORMAL, confidence=0）→ balanced，最安全嘅中間值
"""

from __future__ import annotations

import logging

from ..core.context import CycleContext

log = logging.getLogger(__name__)

# ─── Volatility → Risk Profile 映射 ───
VOL_PROFILE_MAP: dict[str, str] = {
    "LOW":    "balanced",       # 降級：低波幅窄SL→巨額notional→頻繁爆SL，2%比3%安全
    "NORMAL": "balanced",       # 正常 → 平衡
    "HIGH":   "conservative",   # 高波幅 → 穩健（闊SL、低槓桿）
}


class SelectRiskProfileStep:
    """Pipeline step: 根據 volatility regime 選擇 risk profile。

    放喺 DetectModeStep 之後、NoTradeCheckStep 之前。
    讀 ctx.volatility_regime → 設 ctx.active_risk_profile。
    """

    name = "select_risk_profile"

    def run(self, ctx: CycleContext) -> CycleContext:
        vol_regime = ctx.volatility_regime  # "LOW" / "NORMAL" / "HIGH"
        profile_name = VOL_PROFILE_MAP.get(vol_regime, "balanced")

        ctx.active_risk_profile = profile_name

        if ctx.verbose:
            log.info(
                "Risk profile: %s → %s (regime_confidence=%.2f)",
                vol_regime, profile_name, ctx.regime_confidence,
            )

        return ctx
