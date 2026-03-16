"""chat.py — AI chat + Sonnet cap."""

import fcntl
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from scripts.dashboard.constants import (
    HOME, HKT,
    PROXY_BASE_URL, PROXY_API_KEY,
    PROXY2_BASE_URL, PROXY2_API_KEY,
)

# ── Chat Constants ───────────────────────────────────────────────────
_CHAT_MODEL_CHAIN_FAST = ["claude-haiku-4-5-20251001", "gpt-5.4"]
_CHAT_MODEL_CHAIN_DEEP = ["claude-sonnet-4-6", "gpt-5.4"]
_CHAT_ANALYSIS_KW = {"分析", "點解", "策略", "比較", "評估", "建議"}
_CHAT_SONNET_DAILY_CAP = 15
_SONNET_USAGE_PATH = os.path.join(HOME, "shared", "sonnet_usage.json")
_chat_history = []  # list of {role, content, ts}
_chat_lock = threading.Lock()
_CHAT_MAX_PAIRS = 5
_CHAT_EXPIRY_SEC = 600  # 10 min

_CHAT_SYSTEM_PROMPT = """你係 AXC Dashboard 嘅 AI 交易搭檔。
格式：Markdown OK（dashboard 支援 bold、list、code）。回覆上限 15 行。
語氣：香港交易員廣東話口語，直接有態度。
收到數據問數據答，有觀點要講。唔好客套。
成交量解讀：volume_ratio >1.5 = 成交活躍，breakout 可信度高；<0.5 = 成交低迷，小心假突破。"""


def _sonnet_usage_ok() -> bool:
    """Check + increment shared Sonnet daily cap. Thread-safe via file lock."""
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    try:
        os.makedirs(os.path.dirname(_SONNET_USAGE_PATH), exist_ok=True)
        with open(_SONNET_USAGE_PATH, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            raw = f.read().strip()
            data = json.loads(raw) if raw else {"date": today, "count": 0}
            if data.get("date") != today:
                data = {"date": today, "count": 0}
            if data["count"] >= _CHAT_SONNET_DAILY_CAP:
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
            data["count"] += 1
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data))
            fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except Exception:
        return False


def _build_chat_context() -> str:
    """Build compact context from collect_data() for AI chat (~1500 chars).
    設計決定：lazy import collectors 避免 circular dependency。"""
    try:
        from scripts.dashboard.collectors import collect_data
        d = collect_data()
    except Exception as e:
        return f"(context error: {e})"
    parts = []

    # Mode + risk
    parts.append(f"模式: {d.get('mode', '?')} | 連虧: {d.get('consecutive_losses', 0)}")
    risk = d.get("risk_status", {})
    if risk:
        parts.append(f"風險: DD {risk.get('current_dd_pct', 0)}% | 日限 {risk.get('daily_limit_pct', 0)}% used")

    # Balance + PnL
    parts.append(f"餘額: ${d.get('balance', 0):.2f} | 今日: {d.get('today_pnl', 0):+.2f} | 總計: {d.get('total_pnl', 0):+.2f}")

    # Positions
    positions = d.get("live_positions", [])
    if positions:
        pos_lines = []
        for p in positions[:5]:
            hs = p.get("hold_score", {})
            score_str = f" 評分={hs.get('score')}" if hs.get("score") is not None else ""
            pos_lines.append(
                f"  {p.get('pair','?')} {p.get('direction','?')} "
                f"entry={p.get('entry_price', 0)} "
                f"SL={p['sl_price'] if p.get('sl_price') else '-'} "
                f"TP={p['tp_price'] if p.get('tp_price') else '-'} "
                f"uPnL={p.get('unrealized_pnl', 0):+.2f}{score_str}"
            )
        parts.append("持倉:\n" + "\n".join(pos_lines))
    else:
        parts.append("持倉: 無")

    # Prices + changes
    ap = d.get("action_plan", [])
    if ap:
        price_parts = []
        for a in ap:
            chg = a.get("change_24h", "")
            price_parts.append(f"{a.get('symbol','?')} {a.get('price', '?')} ({chg})")
        parts.append("價格: " + " | ".join(price_parts))

    # Volume ratios (current vs 30-candle avg)
    if ap:
        vr_parts = [
            f"{a.get('symbol','?').replace('USDT','')} {a.get('volume_ratio', 0):.1f}x"
            for a in ap if a.get("volume_ratio", 0) > 0
        ]
        if vr_parts:
            parts.append("成交量: " + " | ".join(vr_parts))

    # Funding rates
    fr = d.get("funding_rates", {})
    if fr:
        fr_parts = []
        for sym, data in fr.items():
            if isinstance(data, dict):
                rate = data.get("rate", "?")
                fr_parts.append(f"{sym}={rate}")
        if fr_parts:
            parts.append("資金費率: " + " | ".join(fr_parts))

    # Latest signal
    sig = d.get("signal_active", "NO")
    if sig == "YES":
        parts.append(f"信號: {d.get('signal_pair', '?')} ACTIVE")

    return "\n".join(parts)


def _call_dashboard_llm(user_msg: str, context: str,
                        history: list[dict] | None = None,
                        model_chain: list[str] | None = None) -> str:
    """Call LLM via proxy with model fallback chain."""
    chain = model_chain or _CHAT_MODEL_CHAIN_FAST

    msgs = [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}]
    if history:
        for m in history:
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user",
                 "content": f"{context}\n\n---\n\n用戶：{user_msg}"})

    for model in chain:
        is_anthropic = model.startswith("claude-")
        endpoint = "messages" if is_anthropic else "chat/completions"
        proxies = [(PROXY_BASE_URL, PROXY_API_KEY)]
        if not is_anthropic and PROXY2_BASE_URL and PROXY2_API_KEY:
            proxies.append((PROXY2_BASE_URL, PROXY2_API_KEY))

        for proxy_url, proxy_key in proxies:
            url = f"{proxy_url}/{endpoint}"
            if is_anthropic:
                body_dict = {"model": model, "max_tokens": 1200,
                             "system": _CHAT_SYSTEM_PROMPT,
                             "messages": msgs[1:]}
                headers = {"Content-Type": "application/json",
                           "Authorization": f"Bearer {proxy_key}",
                           "anthropic-version": "2023-06-01"}
            else:
                body_dict = {"model": model, "max_tokens": 1200,
                             "messages": msgs}
                headers = {"Content-Type": "application/json",
                           "Authorization": f"Bearer {proxy_key}"}

            req = urllib.request.Request(url, json.dumps(body_dict).encode(),
                                         method="POST", headers=headers)
            try:
                resp = urllib.request.urlopen(req, timeout=30)
                data = json.loads(resp.read())
                if is_anthropic:
                    return data["content"][0]["text"]
                else:
                    return data["choices"][0]["message"]["content"]
            except Exception:
                continue

    raise RuntimeError("All models in chain failed")


def handle_chat(body: str):
    """POST /api/chat — AI chat from dashboard."""
    if not PROXY_API_KEY:
        return 500, {"error": "API key not configured"}

    try:
        payload = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}

    msg = (payload.get("message") or "").strip()
    if not msg:
        return 400, {"error": "Empty message"}

    # Model routing: analysis keywords → Sonnet
    use_sonnet = any(kw in msg for kw in _CHAT_ANALYSIS_KW)
    if use_sonnet and not _sonnet_usage_ok():
        use_sonnet = False
    chain = _CHAT_MODEL_CHAIN_DEEP if use_sonnet else _CHAT_MODEL_CHAIN_FAST

    # Build context
    context = _build_chat_context()

    # Manage history (thread-safe)
    now = time.time()
    with _chat_lock:
        # Expire old entries
        _chat_history[:] = [m for m in _chat_history if now - m.get("ts", 0) < _CHAT_EXPIRY_SEC]
        # Trim to max pairs
        while len(_chat_history) > _CHAT_MAX_PAIRS * 2:
            _chat_history.pop(0)
        history_for_api = [{"role": m["role"], "content": m["content"]} for m in _chat_history]

    try:
        reply = _call_dashboard_llm(msg, context, history_for_api, chain)
    except urllib.error.URLError as e:
        logging.error("Chat API error: %s", e)
        return 502, {"error": "AI 暫時冇回應，稍後再試"}
    except Exception as e:
        logging.error("Chat error: %s", e)
        return 500, {"error": str(e)}

    # Update history
    with _chat_lock:
        _chat_history.append({"role": "user", "content": msg, "ts": now})
        _chat_history.append({"role": "assistant", "content": reply, "ts": now})

    model_label = "sonnet" if use_sonnet else "haiku"
    return 200, {"reply": reply, "model": model_label}
