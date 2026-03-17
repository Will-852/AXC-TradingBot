"""
regime_bocpd.py — Bayesian Online Changepoint Detection for volatility regime shifts.

設計決定：
  - 監測 norm_atr（ATR/close）而非 log_return，因為 regime label
    直接由 volatility 決定（同 HMM 一致）
  - Normal-Gamma 共軛先驗：closed-form 更新，唔需要 MCMC
  - Student-t 預測概率：自動處理厚尾（自由度低時更保守）
  - Truncation：run length > max_run_length 嘅機率合併到最長 run，
    防止記憶體線性增長
  - 逐 candle 更新（vs HMM 每 24 根 refit），偵測變點更快
  - 零新依賴：numpy + math.lgamma

Interface 同 RegimeHMM.update() 完全一致：
  (regime_label: str, confidence: float, crash_confirmed: bool)
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile

import numpy as np

log = logging.getLogger(__name__)

# ─── Constants ───
_LOG_2PI = math.log(2 * math.pi)


def _student_t_logpdf(x: float, df: float, mu: float, var: float) -> float:
    """Log PDF of Student-t distribution. Pure math — no scipy needed.

    設計決定：用 math.lgamma 而非 scipy.special.gammaln，
    避免 scipy 依賴。精度足夠（雙精度 float）。
    """
    if var <= 0 or df <= 0:
        return -1e300  # effectively -inf but avoids actual -inf in arithmetic
    c = (
        math.lgamma((df + 1) / 2)
        - math.lgamma(df / 2)
        - 0.5 * (math.log(df) + math.log(math.pi) + math.log(var))
    )
    return c - (df + 1) / 2 * math.log(1 + (x - mu) ** 2 / (df * var))


class RegimeBOCPD:
    """Bayesian Online Changepoint Detection for market regime shifts.

    算法：Normal-Gamma conjugate BOCPD (univariate on norm_atr).
    每步 O(max_run_length)，500 candles replay < 0.5 秒。

    Regime 分類同 HMM 一致（用 norm_atr 歷史分位數）：
      - < P33 → RANGE
      - P33–P67 → TREND
      - ≥ P67 → CRASH
    """

    LABELS = ("RANGE", "TREND", "CRASH")
    CRASH_PERCENTILE = 85  # same as HMM
    # Map regime labels → volatility regime (for risk profile selection)
    _VOL_REGIME_MAP = {"RANGE": "LOW", "TREND": "NORMAL", "CRASH": "HIGH"}

    def __init__(
        self,
        hazard_rate: float = 0.02,
        max_run_length: int = 200,
        min_samples: int = 30,
        changepoint_threshold: float = 0.3,
        # Normal-Gamma prior hyperparameters (weakly informative)
        mu_0: float = 0.0,
        kappa_0: float = 1.0,
        alpha_0: float = 1.0,
        beta_0: float = 0.01,
        # Feature history window (same as HMM)
        window: int = 500,
    ):
        self.hazard_rate = hazard_rate
        self.max_run_length = max_run_length
        self.min_samples = min_samples
        self.changepoint_threshold = changepoint_threshold
        self.window = window

        # Normal-Gamma prior
        self._mu_0 = mu_0
        self._kappa_0 = kappa_0
        self._alpha_0 = alpha_0
        self._beta_0 = beta_0

        # Run length distribution: P(r_t | x_{1:t})
        # Index i = run length i. Start with r_0 = 0 (changepoint at t=0).
        self._run_length_dist = np.array([1.0])

        # Sufficient statistics per run length
        # Arrays of size (current_max_run_length + 1,)
        self._mu = np.array([mu_0])
        self._kappa = np.array([kappa_0])
        self._alpha = np.array([alpha_0])
        self._beta = np.array([beta_0])

        # Observation history for regime classification
        self._norm_atr_history: list[float] = []
        self._prev_close: float | None = None
        self._n_updates: int = 0
        # Cache last prediction for get_volatility_regime()
        self._last_label: str = "UNKNOWN"
        self._last_confidence: float = 0.0

    def update(self, indicators_4h: dict) -> tuple[str, float, bool]:
        """Feed new 4H candle, return (regime_label, confidence, crash_confirmed).

        Same interface as RegimeHMM.update().
        Returns ("UNKNOWN", 0.0, False) if not enough data.
        """
        # Extract norm_atr
        close = indicators_4h.get("price")
        atr = indicators_4h.get("atr")

        if close is None or atr is None or close <= 0:
            return ("UNKNOWN", 0.0, False)

        norm_atr = atr / close
        self._prev_close = close

        # Store history
        self._norm_atr_history.append(norm_atr)
        if len(self._norm_atr_history) > self.window:
            self._norm_atr_history = self._norm_atr_history[-self.window:]

        self._n_updates += 1

        # Cold start
        if self._n_updates < self.min_samples:
            return ("UNKNOWN", 0.0, False)

        # Run BOCPD step
        self._bocpd_step(norm_atr)

        # Classify regime from MAP run length's mean
        regime = self._classify_regime(norm_atr)
        confidence = self._compute_confidence()

        # Cache for get_volatility_regime()
        self._last_label = regime
        self._last_confidence = confidence

        # Crash percentile gate (same as HMM)
        crash_confirmed = False
        if regime == "CRASH":
            threshold = float(np.percentile(self._norm_atr_history, self.CRASH_PERCENTILE))
            crash_confirmed = norm_atr >= threshold

        return (regime, confidence, crash_confirmed)

    def get_volatility_regime(self) -> tuple[str, float]:
        """Map regime label to volatility level for risk profile selection.

        設計決定：同 HMM 一致嘅映射：
          RANGE (< P33 norm_atr) → LOW
          TREND (P33–P67)       → NORMAL
          CRASH (≥ P67)         → HIGH
        Cold start / unknown → ("NORMAL", 0.0) — 安全 fallback。
        """
        vol_regime = self._VOL_REGIME_MAP.get(self._last_label, "NORMAL")
        confidence = self._last_confidence if self._last_label != "UNKNOWN" else 0.0
        return (vol_regime, confidence)

    def _bocpd_step(self, x: float) -> None:
        """One step of BOCPD. Updates run length distribution + sufficient stats.

        算法：
        1. 計算每個 run length 嘅 Student-t 預測概率
        2. Growth probabilities（冇變點）
        3. Changepoint probability（有變點）
        4. 更新 Normal-Gamma sufficient statistics
        5. Truncation 到 max_run_length
        """
        n = len(self._run_length_dist)
        H = self.hazard_rate

        # Step 1: Predictive probabilities for each run length
        # Student-t: df=2α, loc=μ, scale=β(κ+1)/(ακ)
        pred_probs = np.zeros(n)
        for r in range(n):
            df = 2.0 * self._alpha[r]
            mu = self._mu[r]
            var = self._beta[r] * (self._kappa[r] + 1) / (self._alpha[r] * self._kappa[r])
            pred_probs[r] = math.exp(_student_t_logpdf(x, df, mu, var))

        # Step 2: Growth probabilities (no changepoint)
        growth = pred_probs * (1 - H) * self._run_length_dist

        # Step 3: Changepoint probability (sum over all run lengths)
        changepoint = np.sum(pred_probs * H * self._run_length_dist)

        # Step 4: New run length distribution
        new_dist = np.empty(n + 1)
        new_dist[0] = changepoint
        new_dist[1:] = growth
        # Normalize
        total = new_dist.sum()
        if total > 0:
            new_dist /= total
        else:
            new_dist[0] = 1.0  # reset to changepoint

        # Step 5: Update sufficient statistics
        # New run length 0 gets prior
        new_mu = np.empty(n + 1)
        new_kappa = np.empty(n + 1)
        new_alpha = np.empty(n + 1)
        new_beta = np.empty(n + 1)

        new_mu[0] = self._mu_0
        new_kappa[0] = self._kappa_0
        new_alpha[0] = self._alpha_0
        new_beta[0] = self._beta_0

        # Existing run lengths: Bayesian update
        old_kappa = self._kappa
        old_mu = self._mu
        old_alpha = self._alpha
        old_beta = self._beta

        new_kappa[1:] = old_kappa + 1
        new_mu[1:] = (old_kappa * old_mu + x) / new_kappa[1:]
        new_alpha[1:] = old_alpha + 0.5
        new_beta[1:] = old_beta + old_kappa * (x - old_mu) ** 2 / (2 * new_kappa[1:])

        # Step 6: Truncation — merge r > max_run_length into last slot
        if len(new_dist) > self.max_run_length + 1:
            # Sum probability of truncated run lengths into last valid slot
            new_dist[self.max_run_length] += new_dist[self.max_run_length + 1:].sum()
            new_dist = new_dist[:self.max_run_length + 1]
            new_mu = new_mu[:self.max_run_length + 1]
            new_kappa = new_kappa[:self.max_run_length + 1]
            new_alpha = new_alpha[:self.max_run_length + 1]
            new_beta = new_beta[:self.max_run_length + 1]
            # Renormalize
            total = new_dist.sum()
            if total > 0:
                new_dist /= total

        self._run_length_dist = new_dist
        self._mu = new_mu
        self._kappa = new_kappa
        self._alpha = new_alpha
        self._beta = new_beta

    def _classify_regime(self, current_norm_atr: float) -> str:
        """Classify current regime using MAP run length's mean norm_atr.

        用 norm_atr 歷史分位數（同 HMM 一致）：
          - MAP run length 嘅 posterior mean → 代表當前 regime 嘅 volatility
          - < P33 → RANGE, P33–P67 → TREND, ≥ P67 → CRASH
        """
        if len(self._norm_atr_history) < self.min_samples:
            return "UNKNOWN"

        # MAP run length
        map_r = int(np.argmax(self._run_length_dist))

        # Use the posterior mean of the MAP run length as the regime indicator
        # (mu[r] is the posterior mean of norm_atr for that run)
        regime_indicator = self._mu[map_r]

        # Percentile thresholds from history
        p33, p67 = np.percentile(self._norm_atr_history, [33, 67])

        if regime_indicator < p33:
            return "RANGE"
        elif regime_indicator < p67:
            return "TREND"
        else:
            return "CRASH"

    def _compute_confidence(self) -> float:
        """Confidence = 1 - P(r_t=0). High changepoint prob → low confidence."""
        if len(self._run_length_dist) == 0:
            return 0.0
        return float(1.0 - self._run_length_dist[0])

    def save_state(self, path: str) -> None:
        """Persist norm_atr history + prev_close for warm restart.

        設計決定：只存 history（同 HMM 一樣），load 後 replay 重建
        run length distribution。簡單可靠，避免存 numpy arrays 嘅
        serialization 問題。
        """
        try:
            data = {
                "norm_atr_history": self._norm_atr_history,
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
            log.warning("BOCPD state save failed: %s", e)

    def load_state(self, path: str) -> bool:
        """Load persisted history and replay to rebuild run length distribution.

        Replay 500 candles < 0.5 秒（M3 Max）。
        """
        try:
            if not os.path.exists(path):
                return False
            with open(path, "r") as f:
                data = json.load(f)

            history = data.get("norm_atr_history", [])
            self._prev_close = data.get("prev_close")

            if not history:
                return False

            # Trim to window
            if len(history) > self.window:
                history = history[-self.window:]

            # Reset state and replay
            self._run_length_dist = np.array([1.0])
            self._mu = np.array([self._mu_0])
            self._kappa = np.array([self._kappa_0])
            self._alpha = np.array([self._alpha_0])
            self._beta = np.array([self._beta_0])
            self._norm_atr_history = []
            self._n_updates = 0

            for val in history:
                self._norm_atr_history.append(val)
                self._n_updates += 1
                if len(self._norm_atr_history) > self.window:
                    self._norm_atr_history = self._norm_atr_history[-self.window:]
                if self._n_updates >= self.min_samples:
                    self._bocpd_step(val)

            log.info(
                "BOCPD state loaded: %d candles replayed from %s",
                len(self._norm_atr_history), path,
            )
            return True

        except Exception as e:
            log.warning("BOCPD state load failed: %s", e)
            return False
