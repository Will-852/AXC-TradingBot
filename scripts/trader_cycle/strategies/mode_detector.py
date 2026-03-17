"""
mode_detector.py — HMM-dominant volatility regime detection + old voter brake check

Phase 1 重構：
  - HMM/BOCPD 做主導 → 輸出 volatility_regime (LOW/NORMAL/HIGH)
  - 舊 5 voter (RSI, MACD, Volume, MA, Funding) 做 brake check
  - ≥3 voter 投 TREND（反對 HMM 判定 vol 低）→ 降一級（更保守）
  - 舊 voter 只有煞車權，冇油門權（唔會降低 vol regime）
  - CRASH override 保留（HMM crash_confirmed + confidence ≥ threshold）
  - mode_confirmed 永遠 True（除 cold start），解除策略閘門
"""

from __future__ import annotations

import logging

from ..config.settings import (
    MODE_RSI_TREND_LOW, MODE_RSI_TREND_HIGH,
    MODE_VOLUME_LOW, MODE_VOLUME_HIGH,
    MODE_FUNDING_THRESHOLD, MODE_CONFIRMATION_REQUIRED,
    PRIMARY_TIMEFRAME,
    HMM_ENABLED, HMM_N_STATES, HMM_WINDOW,
    HMM_REFIT_INTERVAL, HMM_MIN_CONFIDENCE,
    HMM_MIN_SAMPLES, HMM_CRASH_THRESHOLD,
    HMM_STATE_PATH,
    REGIME_ENGINE,
    BOCPD_HAZARD_RATE, BOCPD_MAX_RUN_LENGTH,
    BOCPD_MIN_SAMPLES, BOCPD_CHANGEPOINT_THRESHOLD,
    BOCPD_STATE_PATH,
    CP_ENABLED, CP_ALPHA, CP_MIN_SCORES, CP_MAX_SCORES,
    CP_INFLATION_FACTOR, CP_FALLBACK_MULT, CP_STATE_PATH,
)
from ..core.context import CycleContext

log = logging.getLogger(__name__)

# ─── Singleton HMM instance (lazy init) ───
_hmm_instance = None


def _get_hmm():
    """Lazy singleton for RegimeHMM — only created when HMM_ENABLED.

    Loads persisted feature history from disk so HMM stays warm
    across short-lived process invocations (launchd every 30min).
    """
    global _hmm_instance
    if _hmm_instance is None:
        from .regime_hmm import RegimeHMM
        _hmm_instance = RegimeHMM(
            n_states=HMM_N_STATES,
            window=HMM_WINDOW,
            refit_interval=HMM_REFIT_INTERVAL,
            min_samples=HMM_MIN_SAMPLES,
        )
        _hmm_instance.load_state(HMM_STATE_PATH)
    return _hmm_instance


def reset_hmm():
    """Reset HMM singleton (for testing)."""
    global _hmm_instance
    _hmm_instance = None


# ─── Singleton BOCPD instance (lazy init) ───
_bocpd_instance = None


def _get_bocpd():
    """Lazy singleton for RegimeBOCPD — only created when REGIME_ENGINE == 'bocpd_cp'."""
    global _bocpd_instance
    if _bocpd_instance is None:
        from .regime_bocpd import RegimeBOCPD
        _bocpd_instance = RegimeBOCPD(
            hazard_rate=BOCPD_HAZARD_RATE,
            max_run_length=BOCPD_MAX_RUN_LENGTH,
            min_samples=BOCPD_MIN_SAMPLES,
            changepoint_threshold=BOCPD_CHANGEPOINT_THRESHOLD,
        )
        _bocpd_instance.load_state(BOCPD_STATE_PATH)
    return _bocpd_instance


def reset_bocpd():
    """Reset BOCPD singleton (for testing)."""
    global _bocpd_instance
    _bocpd_instance = None


# ─── Singleton CP instance (lazy init) ───
_cp_instance = None


def _get_cp():
    """Lazy singleton for ATRConformal — only created when CP_ENABLED."""
    global _cp_instance
    if _cp_instance is None:
        from ..risk.atr_conformal import ATRConformal
        _cp_instance = ATRConformal(
            alpha=CP_ALPHA,
            min_scores=CP_MIN_SCORES,
            max_scores=CP_MAX_SCORES,
            inflation_factor=CP_INFLATION_FACTOR,
            fallback_mult=CP_FALLBACK_MULT,
        )
        _cp_instance.load_state(CP_STATE_PATH)
    return _cp_instance


def reset_cp():
    """Reset CP singleton (for testing)."""
    global _cp_instance
    _cp_instance = None


def _vote_rsi(rsi: float | None) -> str:
    """RSI < 32 or > 68 → TREND, 32-68 → RANGE."""
    if rsi is None:
        return "NEUTRAL"
    if rsi < MODE_RSI_TREND_LOW or rsi > MODE_RSI_TREND_HIGH:
        return "TREND"
    return "RANGE"


def _vote_macd(hist: float | None, hist_prev: float | None) -> str:
    """Histogram expanding → TREND, narrowing/near-zero → RANGE."""
    if hist is None or hist_prev is None:
        return "NEUTRAL"
    # Expanding = magnitude increasing
    if abs(hist) > abs(hist_prev) and abs(hist) > 0.001:
        return "TREND"
    return "RANGE"


def _vote_volume(volume_ratio: float | None) -> str:
    """<50% or >150% of avg → TREND, 50-150% → RANGE."""
    if volume_ratio is None:
        return "NEUTRAL"
    if volume_ratio < MODE_VOLUME_LOW or volume_ratio > MODE_VOLUME_HIGH:
        return "TREND"
    return "RANGE"


def _vote_ma(price: float | None, ma50: float | None, ma200: float | None) -> str:
    """Price clearly above/below both MAs → TREND, between → RANGE."""
    if price is None or ma50 is None or ma200 is None:
        return "NEUTRAL"
    upper = max(ma50, ma200)
    lower = min(ma50, ma200)
    if price > upper or price < lower:
        return "TREND"
    return "RANGE"


def _vote_funding(funding_rate: float | None) -> str:
    """>±0.07% → TREND, -0.07%~+0.07% → RANGE."""
    if funding_rate is None:
        return "NEUTRAL"
    if abs(funding_rate) > MODE_FUNDING_THRESHOLD:
        return "TREND"
    return "RANGE"


def _vote_hmm_from_result(regime: str, confidence: float) -> str:
    """Convert HMM result to a RANGE/TREND vote string.

    CRASH maps to RANGE vote (defensive), UNKNOWN = NEUTRAL.
    """
    if confidence < HMM_MIN_CONFIDENCE:
        return "NEUTRAL"
    if regime == "CRASH":
        return "RANGE"  # crash → defensive = range-like vote
    if regime in ("RANGE", "TREND"):
        return regime
    return "NEUTRAL"


def detect_mode_for_pair(
    indicators_4h: dict, funding_rate: float,
    hmm_regime: str | None = None, hmm_confidence: float = 0.0,
    hmm_crash_confirmed: bool = False,
) -> tuple[str, dict[str, str]]:
    """
    Run 6-indicator voting for one pair's 4H data.

    If HMM is available (hmm_regime not None), adds HMM as 6th vote.
    CRASH override: requires crash_confirmed (percentile gate) + high confidence.
    Returns (mode, votes_dict).
    """
    # CRASH override: HMM high-confidence + percentile-confirmed crash
    if (
        (HMM_ENABLED or REGIME_ENGINE == "bocpd_cp")
        and hmm_regime == "CRASH"
        and hmm_crash_confirmed
        and hmm_confidence >= HMM_CRASH_THRESHOLD
    ):
        votes = {"HMM": f"CRASH (conf={hmm_confidence:.0%})"}
        return "CRASH", votes

    votes = {
        "RSI": _vote_rsi(indicators_4h.get("rsi")),
        "MACD": _vote_macd(
            indicators_4h.get("macd_hist"),
            indicators_4h.get("macd_hist_prev")
        ),
        "Volume": _vote_volume(indicators_4h.get("volume_ratio")),
        "MA": _vote_ma(
            indicators_4h.get("price"),
            indicators_4h.get("ma50"),
            indicators_4h.get("ma200")
        ),
        "Funding": _vote_funding(funding_rate),
    }

    # Add HMM as 6th vote if available
    if HMM_ENABLED and hmm_regime is not None:
        votes["HMM"] = _vote_hmm_from_result(hmm_regime, hmm_confidence)

    trend_count = sum(1 for v in votes.values() if v == "TREND")
    range_count = sum(1 for v in votes.values() if v == "RANGE")

    # Threshold: majority of actual votes (excluding NEUTRAL)
    # With 6 voters: 4/6 = majority. With 5 (HMM absent): 3/5 = majority.
    total_non_neutral = sum(1 for v in votes.values() if v in ("TREND", "RANGE"))
    threshold = max((total_non_neutral // 2) + 1, 3)  # at least 3

    if trend_count >= threshold:
        mode = "TREND"
    elif range_count >= threshold:
        mode = "RANGE"
    else:
        mode = "UNKNOWN"  # will maintain current mode

    return mode, votes


    # ─── Volatility regime downgrade table (brake = more conservative) ───
_VOL_DOWNGRADE = {"LOW": "NORMAL", "NORMAL": "HIGH", "HIGH": "HIGH"}
# Voter weights for weighted composite (informational logging)
_VOTER_WEIGHTS = {
    "Volume": 0.30,
    "MA": 0.25,
    "MACD": 0.20,
    "RSI": 0.15,
    "Funding": 0.10,
}


class DetectModeStep:
    """
    Step 6: HMM-dominant volatility regime detection + old voter brake check.

    1. HMM/BOCPD → volatility_regime (LOW/NORMAL/HIGH)
    2. Old 5 voters → brake check (≥3 TREND votes when HMM says low vol → downgrade)
    3. CRASH override preserved (HMM crash_confirmed + high confidence → force HIGH)
    4. mode_confirmed = always True (except cold start) — 解除策略閘門
    """
    name = "detect_mode"

    def run(self, ctx: CycleContext) -> CycleContext:
        # ─── Find primary pair's 4H indicators ───
        primary = "BTCUSDT"
        if primary not in ctx.indicators or PRIMARY_TIMEFRAME not in ctx.indicators[primary]:
            for sym in ctx.indicators:
                if PRIMARY_TIMEFRAME in ctx.indicators[sym]:
                    primary = sym
                    break
            else:
                ctx.warnings.append("No 4H indicators available for mode detection")
                ctx.market_mode = ctx.prev_mode
                ctx.volatility_regime = "NORMAL"
                ctx.regime_confidence = 0.0
                return ctx

        ind_4h = ctx.indicators[primary][PRIMARY_TIMEFRAME]
        funding = ctx.market_data.get(primary, None)
        funding_rate = funding.funding_rate if funding else 0.0

        # ─── 1. Run HMM/BOCPD → regime label + confidence ───
        hmm_regime = None
        hmm_confidence = 0.0
        hmm_crash_confirmed = False
        vol_regime = "NORMAL"  # fallback
        vol_confidence = 0.0

        if REGIME_ENGINE == "bocpd_cp":
            try:
                bocpd = _get_bocpd()
                hmm_regime, hmm_confidence, hmm_crash_confirmed = bocpd.update(ind_4h)
                vol_regime, vol_confidence = bocpd.get_volatility_regime()
                bocpd.save_state(BOCPD_STATE_PATH)
            except Exception as e:
                log.warning("BOCPD update failed: %s", e)
        elif HMM_ENABLED:
            try:
                hmm = _get_hmm()
                hmm_regime, hmm_confidence, hmm_crash_confirmed = hmm.update(ind_4h)
                vol_regime, vol_confidence = hmm.get_volatility_regime()
                hmm.save_state(HMM_STATE_PATH)
            except Exception as e:
                log.warning("HMM update failed: %s", e)

        # ─── 2. CRASH override (preserved) ───
        if (
            hmm_regime == "CRASH"
            and hmm_crash_confirmed
            and hmm_confidence >= HMM_CRASH_THRESHOLD
        ):
            vol_regime = "HIGH"
            vol_confidence = hmm_confidence
            ctx.market_mode = "CRASH"
            ctx.mode_confirmed = True
            ctx.volatility_regime = vol_regime
            ctx.regime_confidence = vol_confidence
            ctx.mode_votes = {"HMM": f"CRASH (conf={hmm_confidence:.0%})"}
            ctx.scan_config_updates["HMM_REGIME"] = hmm_regime
            ctx.scan_config_updates["HMM_CONFIDENCE"] = f"{hmm_confidence:.3f}"
            ctx.scan_config_updates["REGIME_ENGINE"] = REGIME_ENGINE
            ctx.scan_config_updates["VOL_REGIME"] = vol_regime
            self._update_cp(ind_4h, "CRASH", ctx)
            if ctx.verbose:
                print(f"    Mode: CRASH override → vol_regime=HIGH (conf={hmm_confidence:.0%})")
            return ctx

        # ─── 3. Run old 5 voters ───
        votes = {
            "RSI": _vote_rsi(ind_4h.get("rsi")),
            "MACD": _vote_macd(
                ind_4h.get("macd_hist"),
                ind_4h.get("macd_hist_prev"),
            ),
            "Volume": _vote_volume(ind_4h.get("volume_ratio")),
            "MA": _vote_ma(
                ind_4h.get("price"),
                ind_4h.get("ma50"),
                ind_4h.get("ma200"),
            ),
            "Funding": _vote_funding(funding_rate),
        }

        # ─── 4. Brake check: ≥3 TREND voters when HMM says low/normal vol → downgrade ───
        trend_voters = sum(1 for v in votes.values() if v == "TREND")
        downgraded = False
        if trend_voters >= 3 and vol_regime != "HIGH":
            old_regime = vol_regime
            vol_regime = _VOL_DOWNGRADE[vol_regime]
            downgraded = True
            log.info(
                "Voter brake: %d/5 TREND votes, vol_regime %s → %s",
                trend_voters, old_regime, vol_regime,
            )

        # ─── 5. Set context ───
        # Backward compat: set market_mode from HMM regime (informational)
        if hmm_regime in ("RANGE", "TREND", "CRASH"):
            ctx.market_mode = hmm_regime
        else:
            ctx.market_mode = ctx.prev_mode if ctx.prev_mode != "UNKNOWN" else "TREND"

        # mode_confirmed = always True except cold start (vol_confidence == 0)
        ctx.mode_confirmed = vol_confidence > 0
        ctx.volatility_regime = vol_regime
        ctx.regime_confidence = vol_confidence
        ctx.mode_votes = votes

        # Audit trail
        ctx.scan_config_updates["HMM_REGIME"] = hmm_regime or "UNKNOWN"
        ctx.scan_config_updates["HMM_CONFIDENCE"] = f"{hmm_confidence:.3f}"
        ctx.scan_config_updates["REGIME_ENGINE"] = REGIME_ENGINE
        ctx.scan_config_updates["VOL_REGIME"] = vol_regime
        ctx.scan_config_updates["VOL_DOWNGRADED"] = str(downgraded)

        # CP update
        self._update_cp(ind_4h, ctx.market_mode, ctx)

        if ctx.verbose:
            vote_str = " | ".join(f"{k}:{v}" for k, v in votes.items())
            brake_tag = " [BRAKE]" if downgraded else ""
            print(
                f"    Vol regime: {vol_regime} (conf={vol_confidence:.0%}){brake_tag} "
                f"| mode={ctx.market_mode} [{vote_str}]"
            )

        return ctx

    def _update_cp(self, ind_4h: dict, regime: str, ctx: CycleContext):
        """Update Conformal Prediction calibration every 4H candle."""
        if not CP_ENABLED:
            return
        try:
            cp = _get_cp()
            atr = ind_4h.get("atr")
            if not atr or atr <= 0:
                return
            true_range = ind_4h.get("high", 0) - ind_4h.get("low", 0)
            if true_range <= 0:
                true_range = atr  # fallback
            cp.update(regime=regime, atr=atr, true_range=true_range)
            cp.save_state(CP_STATE_PATH)
            if ctx.verbose:
                atr_high = cp.get_atr_high(atr)
                q_hat = atr_high - atr
                print(f"      CP: atr={atr:.2f} + q_hat={q_hat:.2f} = atr_high={atr_high:.2f}")
        except Exception as e:
            log.warning("CP update failed: %s", e)
