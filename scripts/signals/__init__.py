"""signals — Shared signal detection module for AXC strategies.

Squeeze 同 Burst strategy 共用嘅 signal detection logic。
各 strategy 獨立決策，呢度只提供 scoring + detection primitives。
"""

from .volume import score_volume_quiet, score_volume_spike, detect_volume_spike, project_volume
from .squeeze import detect_squeeze, score_squeeze, SqueezeState
from .obv import score_obv_confirmation, detect_obv_divergence

__all__ = [
    "score_volume_quiet",
    "score_volume_spike",
    "detect_volume_spike",
    "project_volume",
    "detect_squeeze",
    "score_squeeze",
    "SqueezeState",
    "score_obv_confirmation",
    "detect_obv_divergence",
]
