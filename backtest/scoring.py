"""
scoring.py — WeightedScorer: 可配置評分公式

設計決定：
  - 將 production 策略嘅硬編碼加分邏輯抽出為可調權重
  - 乘法 volume multiplier（BMD 建議）取代加法 bonus
  - 保持 OBV 加減分（有方向性，唔適合用乘法）
  - clamp 防止極端放大

公式：
  score = conviction × vol_multiplier + obv_adj + reentry_boost
  vol_multiplier = clamp(1.0 + w_vol × (volume_ratio - 1.0), 0.7, 1.5)
  obv_adj = w_obv × obv_signal   # obv_signal ∈ {-1, 0, +1}
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass
class ScoringWeights:
    """Tunable scoring parameters."""
    w_vol: float = 0.3                  # volume multiplier slope
    w_obv: float = 0.5                  # OBV confirmation strength
    w_stoch: float = 1.0                # stochastic bonus (STRONG vs WEAK)
    base_score_strong: float = 4.0      # range STRONG base
    base_score_weak: float = 3.0        # range WEAK base
    base_score_trend_full: float = 5.0  # trend 4/4 base
    base_score_trend_bias: float = 3.5  # trend 3/4 bias base
    confidence_threshold_low: float = 3.0    # score <= this → 1.0x risk (ramp starts here)
    confidence_threshold_high: float = 4.5   # score >= this → max risk multiplier (ramp ends here)
    confidence_risk_high_mult: float = 1.25  # max risk multiplier at ramp top
    reentry_boost: float = 0.0          # fixed boost for re-entry signals

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> ScoringWeights:
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})


class WeightedScorer:
    """
    Pluggable scorer that replaces hard-coded bonus logic in strategies.

    Usage:
        scorer = WeightedScorer(ScoringWeights(w_vol=0.4, w_obv=0.8))
        score = scorer.score_range(strength="STRONG", volume_ratio=1.5,
                                   obv_signal=1, has_reentry=False)
    """

    def __init__(self, weights: ScoringWeights | None = None):
        self.w = weights or ScoringWeights()

    def _vol_multiplier(self, volume_ratio: float) -> float:
        """Multiplicative volume adjustment (clamped)."""
        raw = 1.0 + self.w.w_vol * (volume_ratio - 1.0)
        return max(0.7, min(1.5, raw))

    def _obv_adj(self, obv_signal: int) -> float:
        """OBV directional adjustment. obv_signal: +1 confirm, -1 against, 0 neutral."""
        return self.w.w_obv * obv_signal

    def score_range(
        self,
        strength: str,
        volume_ratio: float = 1.0,
        obv_signal: int = 0,
        has_reentry: bool = False,
    ) -> float:
        """Score a range signal with configurable weights."""
        if strength == "STRONG":
            base = self.w.base_score_strong + self.w.w_stoch
        else:
            base = self.w.base_score_weak

        conviction = base
        vol_mult = self._vol_multiplier(volume_ratio)
        obv = self._obv_adj(obv_signal)
        reentry = self.w.reentry_boost if has_reentry else 0.0

        return conviction * vol_mult + obv + reentry

    def score_trend(
        self,
        key_count: int,
        volume_ratio: float = 1.0,
        obv_signal: int = 0,
        has_reentry: bool = False,
    ) -> float:
        """Score a trend signal with configurable weights."""
        if key_count >= 4:
            base = self.w.base_score_trend_full
        else:
            base = self.w.base_score_trend_bias

        conviction = base
        vol_mult = self._vol_multiplier(volume_ratio)
        obv = self._obv_adj(obv_signal)
        reentry = self.w.reentry_boost if has_reentry else 0.0

        return conviction * vol_mult + obv + reentry

    def is_high_confidence(self, score: float) -> bool:
        """Check if score qualifies for larger position."""
        return score >= self.w.confidence_threshold_high

    def risk_multiplier(self, score: float) -> float:
        """Linear ramp risk multiplier based on score confidence.

        Below confidence_threshold_low → 1.0x (no boost).
        Above confidence_threshold_high → confidence_risk_high_mult (max boost).
        Between → linear interpolation (no cliff edge).
        """
        low = self.w.confidence_threshold_low
        high = self.w.confidence_threshold_high
        max_mult = self.w.confidence_risk_high_mult

        if high <= low:
            # Degenerate: collapse to step at low
            return max_mult if score >= low else 1.0
        if score <= low:
            return 1.0
        if score >= high:
            return max_mult
        # Linear ramp
        t = (score - low) / (high - low)
        return 1.0 + t * (max_mult - 1.0)
