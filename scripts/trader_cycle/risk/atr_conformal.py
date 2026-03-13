"""
atr_conformal.py — Distribution-free prediction interval for ATR with regime memory banks.

設計決定：
  - Nonconformity score = |ATR_t - TrueRange_{t+1}|
    （ATR 作為「預測」，下一根 candle 嘅 true range 作為「真實值」）
  - 3 個 regime memory banks：各自儲存該 regime 下嘅 scores
  - Cold start 解法：切換到冇歷史嘅 bank 時，用舊 bank scores × inflation_factor
  - 唔需要任何新 ML 模型，直接用現有 ATR
  - 零新依賴：只用 numpy

效果：
  - CP off → atr_for_sl = atr（同現有一樣）
  - CP on + regime 穩定 → atr_for_sl ≈ atr × 1.05~1.15（小幅保守）
  - CP on + 剛切 regime → atr_for_sl ≈ atr × 1.3~1.5（明顯保守）
  - Dollar risk 唔變（bounded by risk_amount），只係 SL 寬咗 + position 細咗
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

import numpy as np

log = logging.getLogger(__name__)


class ATRConformal:
    """Conformal prediction for ATR with regime memory banks.

    每根 candle 調用 update()，計算上一步嘅 nonconformity score，
    返回當前 q_hat（quantile of scores）。

    atr_high = atr + q_hat → 用作 SL 計算嘅「保守 ATR」。
    """

    REGIMES = ("RANGE", "TREND", "CRASH")

    def __init__(
        self,
        alpha: float = 0.10,
        min_scores: int = 20,
        max_scores: int = 200,
        inflation_factor: float = 1.5,
        fallback_mult: float = 1.5,
    ):
        self._alpha = alpha
        self._min_scores = min_scores
        self._max_scores = max_scores
        self._inflation_factor = inflation_factor
        self._fallback_mult = fallback_mult

        # Regime memory banks
        self._banks: dict[str, list[float]] = {r: [] for r in self.REGIMES}
        self._active_regime: str = "UNKNOWN"

        # Previous step's ATR (for computing score at next step)
        self._prev_atr: float | None = None

        # Current q_hat (cached after each update)
        self._q_hat: float | None = None

    def update(self, regime: str, atr: float, true_range: float) -> float | None:
        """每根 candle 調用。計算上一步嘅 score，返回當前 q_hat.

        Args:
            regime: Current regime label ("RANGE", "TREND", "CRASH")
            atr: Current candle's ATR value
            true_range: Current candle's true range (high - low)

        Returns:
            q_hat (float) or None if not enough scores in any bank.
        """
        # Step 1: Compute score from previous step's ATR vs current true range
        if self._prev_atr is not None:
            score = abs(self._prev_atr - true_range)
            target_regime = self._active_regime if self._active_regime in self.REGIMES else regime
            self._add_score(target_regime, score)

        # Step 2: Handle regime transition
        if regime in self.REGIMES:
            self._active_regime = regime

        # Step 3: Compute q_hat from active bank
        self._q_hat = self._compute_q_hat(regime)

        # Step 4: Store current ATR for next step
        self._prev_atr = atr

        return self._q_hat

    def get_atr_high(self, atr: float) -> float:
        """ATR + q_hat. If q_hat unavailable, return ATR × fallback_mult.

        呢個係 position_sizer 用嘅 API。
        """
        if self._q_hat is not None:
            return atr + self._q_hat
        return atr * self._fallback_mult

    def _add_score(self, regime: str, score: float) -> None:
        """Add score to bank, FIFO trim if over max_scores."""
        if regime not in self._banks:
            return
        bank = self._banks[regime]
        bank.append(score)
        if len(bank) > self._max_scores:
            self._banks[regime] = bank[-self._max_scores:]

    def _compute_q_hat(self, regime: str) -> float | None:
        """Compute quantile from active bank, with cold start fallback.

        Case A: target bank 有足夠歷史 → 直接用 target bank
        Case B: target bank 冇歷史 → 用最大嘅舊 bank × inflation_factor
        Case C: 所有 bank 都空 → return None（caller 用 fallback_mult）
        """
        quantile = (1 - self._alpha) * 100  # e.g., 90th percentile

        if regime in self._banks:
            bank = self._banks[regime]
            if len(bank) >= self._min_scores:
                # Case A: warm start
                return float(np.percentile(bank, quantile))

        # Case B: cold start — find largest available bank
        best_bank: list[float] | None = None
        best_size = 0
        for r in self.REGIMES:
            if len(self._banks[r]) > best_size:
                best_bank = self._banks[r]
                best_size = len(self._banks[r])

        if best_bank and best_size >= self._min_scores:
            inflated = [s * self._inflation_factor for s in best_bank]
            return float(np.percentile(inflated, quantile))

        # Case C: no data anywhere
        return None

    def save_state(self, path: str) -> None:
        """Persist all banks + active regime for warm restart."""
        try:
            data = {
                "banks": self._banks,
                "active_regime": self._active_regime,
                "prev_atr": self._prev_atr,
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
            log.warning("CP state save failed: %s", e)

    def load_state(self, path: str) -> bool:
        """Load persisted banks."""
        try:
            if not os.path.exists(path):
                return False
            with open(path, "r") as f:
                data = json.load(f)

            loaded_banks = data.get("banks", {})
            for r in self.REGIMES:
                if r in loaded_banks and isinstance(loaded_banks[r], list):
                    self._banks[r] = loaded_banks[r][-self._max_scores:]

            self._active_regime = data.get("active_regime", "UNKNOWN")
            self._prev_atr = data.get("prev_atr")

            total = sum(len(b) for b in self._banks.values())
            log.info("CP state loaded: %d total scores from %s", total, path)
            return True

        except Exception as e:
            log.warning("CP state load failed: %s", e)
            return False
