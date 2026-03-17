"""
regime_hmm.py — HMM-based market regime detection

設計決定：
  - 用 hmmlearn GaussianHMM 而非自己寫，因為 Baum-Welch 收斂處理成熟
  - 3 states（RANGE / TREND / CRASH），4H 級別數據量不足分 4 種
  - 4 features 全部來自現有 indicators，唔使新增
  - State label mapping 用 mean volatility 排序，每次 refit 後重新 map
  - Percentile gate: CRASH override 只喺 current norm_atr ≥ 歷史 85th percentile
    時觸發。低於 threshold 嘅 CRASH label 繼續做 RANGE vote（防守），但唔觸發
    CRASH mode（防止正常波動誤觸發全面防守模式）
  - Cold start（<100 candles）→ 棄權，fallback 純 5-vote
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile

import numpy as np
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)

# Lazy import hmmlearn to avoid startup cost if HMM disabled
_GaussianHMM = None


def _get_hmm_class():
    global _GaussianHMM
    if _GaussianHMM is None:
        from hmmlearn.hmm import GaussianHMM
        _GaussianHMM = GaussianHMM
    return _GaussianHMM


class RegimeHMM:
    """GaussianHMM wrapper for market regime detection.

    3 hidden states mapped to RANGE / TREND / CRASH by mean feature volatility:
      - Lowest norm_atr mean → RANGE
      - Middle → TREND
      - Highest → CRASH

    4 features (all from existing 4H indicators):
      1. log_return = log(close / prev_close)
      2. norm_atr = ATR / close
      3. volume_ratio (vs 30-candle avg, already computed)
      4. adx (trend strength, already computed)
    """

    LABELS = ("RANGE", "TREND", "CRASH")
    CRASH_PERCENTILE = 85  # current norm_atr must be ≥ this percentile for CRASH override
    # Map regime labels → volatility regime (for risk profile selection)
    _VOL_REGIME_MAP = {"RANGE": "LOW", "TREND": "NORMAL", "CRASH": "HIGH"}

    def __init__(self, n_states: int, window: int, refit_interval: int,
                 min_samples: int = 100):
        self.n_states = n_states
        self.window = window
        self.refit_interval = refit_interval
        self.min_samples = min_samples

        self.model = None
        self.scaler = StandardScaler()
        self._candles_since_fit = 0
        self._feature_history: list[list[float]] = []
        self._state_map: dict[int, str] = {}
        self._prev_close: float | None = None
        self._crash_vol_threshold: float = float("inf")  # percentile gate
        # Cache last prediction for get_volatility_regime()
        self._last_label: str = "UNKNOWN"
        self._last_confidence: float = 0.0

    def save_state(self, path: str) -> None:
        """Persist feature history + prev_close for warm restart across processes.

        設計決定：只存 feature_history（JSON-safe list[list[float]]），唔存 model。
        每次 load 後 model=None → 自動觸發 _fit()。簡單可靠。
        """
        try:
            data = {
                "feature_history": self._feature_history,
                "prev_close": self._prev_close,
            }
            dir_name = os.path.dirname(path)
            os.makedirs(dir_name, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            log.warning("HMM state save failed: %s", e)

    def load_state(self, path: str) -> bool:
        """Load persisted feature history for warm restart.

        Returns True if loaded successfully. Model is NOT restored —
        first update() will trigger _fit() on the loaded history.
        """
        try:
            if not os.path.exists(path):
                return False
            with open(path, "r") as f:
                data = json.load(f)
            self._feature_history = data.get("feature_history", [])
            self._prev_close = data.get("prev_close")
            # Trim to window
            if len(self._feature_history) > self.window:
                self._feature_history = self._feature_history[-self.window:]
            log.info(
                "HMM state loaded: %d candles from %s",
                len(self._feature_history), path,
            )
            return True
        except Exception as e:
            log.warning("HMM state load failed: %s", e)
            return False

    def update(self, indicators_4h: dict) -> tuple[str, float, bool]:
        """Feed new 4H candle, return (regime_label, confidence, crash_confirmed).

        crash_confirmed: True only when label=CRASH AND current norm_atr ≥ 85th
        percentile. Callers should only trigger CRASH mode override when True.
        Returns ("UNKNOWN", 0.0, False) if not enough data or model not ready.
        """
        features = self._extract_features(indicators_4h)
        if features is None:
            return ("UNKNOWN", 0.0, False)

        self._feature_history.append(features)
        self._candles_since_fit += 1

        # Trim to window size
        if len(self._feature_history) > self.window:
            self._feature_history = self._feature_history[-self.window:]

        # Cold start: not enough data
        if len(self._feature_history) < self.min_samples:
            return ("UNKNOWN", 0.0, False)

        # Refit if needed
        if self.model is None or self._candles_since_fit >= self.refit_interval:
            success = self._fit()
            if not success:
                return ("UNKNOWN", 0.0, False)

        # Predict current state
        label, confidence = self._predict()

        # Cache for get_volatility_regime()
        self._last_label = label
        self._last_confidence = confidence

        # Percentile gate: CRASH override only when current vol is extreme
        crash_confirmed = False
        if label == "CRASH":
            current_norm_atr = features[1]  # index 1 = norm_atr
            crash_confirmed = current_norm_atr >= self._crash_vol_threshold

        return (label, confidence, crash_confirmed)

    def get_volatility_regime(self) -> tuple[str, float]:
        """Map regime label to volatility level for risk profile selection.

        設計決定：HMM 3 states 已按 norm_atr 排序，直接映射：
          RANGE (lowest vol) → LOW
          TREND (middle vol) → NORMAL
          CRASH (highest vol) → HIGH
        Cold start / unknown → ("NORMAL", 0.0) — 安全 fallback。
        """
        vol_regime = self._VOL_REGIME_MAP.get(self._last_label, "NORMAL")
        confidence = self._last_confidence if self._last_label != "UNKNOWN" else 0.0
        return (vol_regime, confidence)

    def _extract_features(self, ind: dict) -> list[float] | None:
        """Extract [log_return, norm_atr, volume_ratio, adx] from 4H indicators."""
        close = ind.get("price")
        atr = ind.get("atr")
        volume_ratio = ind.get("volume_ratio")
        adx = ind.get("adx")

        if any(v is None for v in [close, atr, adx]):
            return None

        if close is None or close <= 0:
            return None

        # log_return
        if self._prev_close is not None and self._prev_close > 0:
            log_return = math.log(close / self._prev_close)
        else:
            log_return = 0.0
        self._prev_close = close

        # norm_atr
        norm_atr = atr / close if close > 0 else 0.0

        # Default volume_ratio if missing
        if volume_ratio is None:
            volume_ratio = 1.0

        return [log_return, norm_atr, volume_ratio, adx]

    def _fit(self) -> bool:
        """Train HMM on feature history window.

        After fit: map states by sorting mean norm_atr (column 1):
          lowest → RANGE, middle → TREND, highest → CRASH
        Returns True if fit succeeded.
        """
        try:
            X = np.array(self._feature_history)
            X_scaled = self.scaler.fit_transform(X)

            GaussianHMM = _get_hmm_class()
            self.model = GaussianHMM(
                n_components=self.n_states,
                covariance_type="full",
                n_iter=100,
                random_state=42,
                verbose=False,
            )
            self.model.fit(X_scaled)
            self._candles_since_fit = 0

            # Map states by mean norm_atr (feature index 1 in original space)
            # Decode full sequence to get state assignments
            states = self.model.predict(X_scaled)

            # Compute mean norm_atr per state (use original unscaled data)
            state_vol = {}
            for s in range(self.n_states):
                mask = states == s
                if mask.any():
                    state_vol[s] = float(np.mean(X[mask, 1]))  # norm_atr
                else:
                    state_vol[s] = float("inf")  # empty state → safest rank = CRASH

            # Sort by volatility: lowest=RANGE, mid=TREND, highest=CRASH
            sorted_states = sorted(state_vol.keys(), key=lambda s: state_vol[s])
            for rank, state_id in enumerate(sorted_states):
                self._state_map[state_id] = self.LABELS[rank]

            # Percentile gate: store threshold for CRASH override decision.
            # CRASH label is always assigned (→ RANGE vote for defense), but
            # CRASH mode override only triggers when current norm_atr ≥ threshold.
            all_norm_atr = X[:, 1]
            self._crash_vol_threshold = float(
                np.percentile(all_norm_atr, self.CRASH_PERCENTILE)
            )

            log.info(
                "HMM refit: %d samples, state_vol=%s, map=%s, "
                "crash_pct_threshold=%.6f",
                len(X), {s: f"{v:.6f}" for s, v in state_vol.items()},
                self._state_map, self._crash_vol_threshold,
            )
            return True

        except Exception as e:
            log.warning("HMM fit failed: %s", e)
            self.model = None
            self._candles_since_fit = 0  # backoff: wait full refit_interval
            return False

    def _predict(self) -> tuple[str, float]:
        """Predict current regime and confidence (posterior probability)."""
        if self.model is None or not self._feature_history:
            return ("UNKNOWN", 0.0)

        try:
            X = np.array(self._feature_history)
            X_scaled = self.scaler.transform(X)

            # Get posterior probabilities for the last observation
            posteriors = self.model.predict_proba(X_scaled)
            last_posterior = posteriors[-1]

            current_state = int(np.argmax(last_posterior))
            confidence = float(last_posterior[current_state])
            label = self._state_map.get(current_state, "UNKNOWN")

            return (label, confidence)

        except Exception as e:
            log.warning("HMM predict failed: %s", e)
            return ("UNKNOWN", 0.0)
