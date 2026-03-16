"""scoring.py — Position hold score + macro state reader."""

import json
import logging
import os
import time

from scripts.dashboard.constants import MACRO_STATE_PATH

# ── Macro State ─────────────────────────────────────────────────────
_macro_cache = {"data": {}, "ts": 0}
_MACRO_CACHE_TTL = 120  # 2 min


def _get_macro_state():
    """Read macro_state.json with 2-min cache."""
    now = time.time()
    if now - _macro_cache["ts"] < _MACRO_CACHE_TTL and _macro_cache["data"]:
        return _macro_cache["data"]
    if not os.path.exists(MACRO_STATE_PATH):
        return _macro_cache["data"]
    try:
        with open(MACRO_STATE_PATH, encoding="utf-8") as f:
            _macro_cache["data"] = json.load(f)
        _macro_cache["ts"] = now
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("Failed to read macro_state: %s", e)
    return _macro_cache["data"]


# ── Position Hold Score ─────────────────────────────────────────────

_SCORE_W_PNL = 0.25
_SCORE_W_TECH = 0.25
_SCORE_W_RISK = 0.20
_SCORE_W_SENT = 0.15
_SCORE_W_MACRO = 0.15


def _score_position(pos, plan_entry, news, risk_status, funding_rates, macro):
    """即時評估持倉健康度（0-10）。純公式計算，零 API call。"""
    factors = []
    is_long = pos.get("direction") == "LONG"
    entry = float(pos.get("entry_price", 0))
    mark = float(pos.get("mark_price", 0))
    symbol = pos.get("pair", "")

    # ── 1. PnL 趨勢 (0-10) ──
    upnl_pct = float(pos.get("unrealized_pct", 0))
    pnl_base = max(0, min(10, upnl_pct + 5))

    momentum_bonus = 0
    changes = {}
    if plan_entry:
        changes = plan_entry.get("changes", {})
    ch_1h = changes.get("1h", 0)
    ch_4h = changes.get("4h", 0)
    if is_long:
        if ch_1h > 0:
            momentum_bonus += 0.5
        if ch_4h > 0:
            momentum_bonus += 0.5
    else:
        if ch_1h < 0:
            momentum_bonus += 0.5
        if ch_4h < 0:
            momentum_bonus += 0.5

    pnl_score = max(0, min(10, pnl_base + momentum_bonus))
    detail_pnl = f"{'+' if upnl_pct >= 0 else ''}{upnl_pct:.1f}%"
    if momentum_bonus > 0:
        detail_pnl += " 動量↑"
    factors.append({"name": "PnL 趨勢", "score": round(pnl_score, 1), "detail": detail_pnl})

    # ── 2. 技術位置 (0-10) ──
    tech_score = 5.0
    detail_tech = "無 S/R 數據"
    if plan_entry:
        support = float(plan_entry.get("support", 0))
        resistance = float(plan_entry.get("resistance", 0))
        if support > 0 and resistance > support and mark > 0:
            sr_range = resistance - support
            pos_in_range = (mark - support) / sr_range
            pos_in_range = max(0, min(1, pos_in_range))
            if is_long:
                tech_score = pos_in_range * 10
                if pos_in_range > 0.7:
                    detail_tech = "近阻力 TP"
                elif pos_in_range < 0.3:
                    detail_tech = "近支撐 SL"
                else:
                    detail_tech = f"S/R 中段 ({pos_in_range:.0%})"
            else:
                tech_score = (1 - pos_in_range) * 10
                if pos_in_range < 0.3:
                    detail_tech = "近支撐 TP"
                elif pos_in_range > 0.7:
                    detail_tech = "近阻力 SL"
                else:
                    detail_tech = f"S/R 中段 ({1 - pos_in_range:.0%})"
    factors.append({"name": "技術位置", "score": round(tech_score, 1), "detail": detail_tech})

    # ── 3. 風險保護 (0-10) ──
    risk_score = 0.0
    sl = float(pos.get("sl_price", 0))
    tp = float(pos.get("tp_price", 0))
    liq = float(pos.get("liq_price", 0))
    detail_parts = []

    if sl > 0:
        risk_score += 4.0
        detail_parts.append("SL✓")
    else:
        detail_parts.append("SL✗")
    if tp > 0:
        risk_score += 2.0
        detail_parts.append("TP✓")
    else:
        detail_parts.append("TP✗")

    if sl > 0 and tp > 0 and entry > 0:
        if is_long:
            risk_dist = entry - sl
            reward_dist = tp - entry
        else:
            risk_dist = sl - entry
            reward_dist = entry - tp
        if risk_dist > 0:
            rr = reward_dist / risk_dist
            if rr >= 2:
                risk_score += 3.0
            elif rr >= 1.5:
                risk_score += 2.0
            elif rr >= 1:
                risk_score += 1.0
            detail_parts.append(f"R:R {rr:.1f}")

    if liq > 0 and mark > 0:
        liq_dist_pct = abs(mark - liq) / mark * 100
        if liq_dist_pct > 20:
            risk_score += 1.0
        detail_parts.append(f"強平{liq_dist_pct:.0f}%")

    risk_score = min(10, risk_score)
    factors.append({"name": "風險保護", "score": round(risk_score, 1), "detail": " ".join(detail_parts)})

    # ── 4. 市場情緒 (0-10) ──
    sent_score = 5.0
    detail_sent = "無數據"
    if news and not news.get("stale", True):
        short = symbol.replace("USDT", "")
        sym_sent = (news.get("sentiment_by_symbol") or {}).get(short)
        if sym_sent and isinstance(sym_sent, dict):
            sentiment = sym_sent.get("sentiment") or news.get("overall_sentiment", "neutral")
            confidence = float(sym_sent.get("confidence") or news.get("confidence") or 0)
        else:
            sentiment = news.get("overall_sentiment", "neutral")
            confidence = float(news.get("confidence") or 0)
        confidence = max(0, min(1, confidence))

        bullish = sentiment in ("bullish", "positive")
        bearish = sentiment in ("bearish", "negative")
        aligned = (is_long and bullish) or (not is_long and bearish)
        opposed = (is_long and bearish) or (not is_long and bullish)

        if aligned:
            sent_score = 7 + confidence * 3
            detail_sent = f"情緒利好 ({confidence:.0%})"
        elif opposed:
            sent_score = max(0, 3 - confidence * 3)
            detail_sent = f"情緒逆向 ({confidence:.0%})"
        else:
            sent_score = 5.0
            detail_sent = "中性"
    elif news and news.get("stale"):
        detail_sent = "數據過時"

    factors.append({"name": "市場情緒", "score": round(sent_score, 1), "detail": detail_sent})

    # ── 5. 宏觀環境 (0-10) ──
    macro_score = 5.0
    detail_macro_parts = []

    vix = 0
    try:
        vix = float(macro.get("^VIX_price", 0))
    except (ValueError, TypeError):
        pass
    if vix > 0:
        if vix < 20:
            macro_score = 8.0
            detail_macro_parts.append(f"VIX {vix:.0f} 低")
        elif vix <= 30:
            macro_score = 5.0
            detail_macro_parts.append(f"VIX {vix:.0f}")
        else:
            macro_score = 2.0
            detail_macro_parts.append(f"VIX {vix:.0f} 高")
    else:
        detail_macro_parts.append("VIX N/A")

    fr = (funding_rates or {}).get(symbol, {})
    fr_rate = fr.get("rate", 0)
    if fr_rate != 0:
        funding_aligned = (is_long and fr_rate < 0) or (not is_long and fr_rate > 0)
        if funding_aligned:
            macro_score = min(10, macro_score + 1)
            detail_macro_parts.append("FR利好")
        elif abs(fr_rate) > 0.03:
            macro_score = max(0, macro_score - 0.5)
            detail_macro_parts.append("FR逆向")

    if risk_status:
        hmm_conf = float(risk_status.get("hmm_confidence", 0))
        if hmm_conf > 0.7:
            macro_score = min(10, macro_score + 1)

    factors.append({"name": "宏觀環境", "score": round(macro_score, 1),
                    "detail": " ".join(detail_macro_parts) if detail_macro_parts else "N/A"})

    # ── Weighted average ──
    weighted = (
        pnl_score * _SCORE_W_PNL +
        tech_score * _SCORE_W_TECH +
        risk_score * _SCORE_W_RISK +
        sent_score * _SCORE_W_SENT +
        macro_score * _SCORE_W_MACRO
    )
    weighted = max(0, min(10, weighted))

    return {
        "score": round(weighted, 1),
        "factors": factors,
    }
