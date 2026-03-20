#!/usr/bin/env python3
"""
research_cycle.py — AI 自動策略研究循環

多輪 tool-use loop：AI 自己決定查咩數據、跑咩分析、寫咩結論。
每 6 小時跑一次（LaunchAgent），或手動 --once。

工具：
  query_trades     讀 mm_trades.jsonl（支援 filter）
  query_signals    讀 mm_signals.jsonl
  calc_metrics     計 WR / PnL / Sharpe / fill rate / per-zone
  compare_periods  A/B 比較兩個時段
  write_finding    結構化輸出（強制 action + confidence）

設計決策：
  - 零外部依賴（raw urllib，同 edge_finder.py 一致）
  - Max 15 turns，超過強制 synthesis
  - Findings → polymarket/logs/research_findings.jsonl
  - 結尾發 Telegram 摘要

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/research_cycle.py --once --verbose
  PYTHONPATH=.:scripts python3 polymarket/research_cycle.py --once --dry-run
"""

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

logger = logging.getLogger("research_cycle")
_HKT = ZoneInfo("Asia/Hong_Kong")

# ═══════════════════════════════════════
#  Paths
# ═══════════════════════════════════════

_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_TRADES_PATH = os.path.join(_LOG_DIR, "mm_trades.jsonl")
_SIGNALS_PATH = os.path.join(_LOG_DIR, "mm_signals.jsonl")
_FINDINGS_PATH = os.path.join(_LOG_DIR, "research_findings.jsonl")
_REPORT_PATH = os.path.join(_LOG_DIR, "research_report.md")
_SECRETS_PATH = os.path.join(_AXC, "secrets", ".env")

# ═══════════════════════════════════════
#  Config — Claude API (raw urllib, 零 SDK)
# ═══════════════════════════════════════

def _load_env():
    """Load secrets from .env file (same pattern as edge_finder.py)."""
    if not os.path.exists(_SECRETS_PATH):
        return
    with open(_SECRETS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if k.strip() and v:
                os.environ.setdefault(k.strip(), v)


_load_env()

_PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "")
_PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096
_TEMPERATURE = 0.4  # slightly creative for research
_API_TIMEOUT = 90
_MAX_TURNS = 15  # hard cap on conversation rounds
_MAX_INPUT_TOKENS = 80_000  # stop if context getting big

# ═══════════════════════════════════════
#  Data Layer — 讀 trade / signal logs
# ═══════════════════════════════════════

def _read_jsonl(path: str, max_lines: int = 5000) -> list[dict]:
    """Read JSONL file, return list of dicts. Handles both old/new schemas."""
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records[-max_lines:]  # keep recent


def _parse_ts(record: dict) -> float:
    """Extract unix timestamp from a record. Handles ISO strings."""
    ts = record.get("ts", "")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts:
        try:
            return datetime.fromisoformat(ts).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _filter_by_hours(records: list[dict], hours: float) -> list[dict]:
    """Filter records to last N hours."""
    if hours <= 0:
        return records
    cutoff = time.time() - hours * 3600
    return [r for r in records if _parse_ts(r) >= cutoff]


def _normalize_trade(r: dict) -> dict:
    """Normalize old/new trade schema to consistent format."""
    return {
        "cid": r.get("cid", r.get("condition_id", ""))[:12],
        "ts": r.get("ts", ""),
        "result": r.get("result", ""),
        "pnl": r.get("pnl", 0),
        "cost": r.get("cost", r.get("entry_cost", 0)),
        "payout": r.get("payout", 0),
        "total_pnl": r.get("total_pnl", 0),
        "fill_rate_pct": r.get("fill_rate_pct", 0),
        "title": r.get("title", ""),
    }

# ═══════════════════════════════════════
#  Tools — AI 可以 call 嘅函數
# ═══════════════════════════════════════

def tool_query_trades(hours: float = 24, result_filter: str = "") -> dict:
    """查詢交易記錄。hours=0 代表全部。result_filter: UP/DOWN/空。"""
    raw = _read_jsonl(_TRADES_PATH)
    trades = _filter_by_hours(raw, hours)
    trades = [_normalize_trade(t) for t in trades]
    if result_filter:
        trades = [t for t in trades if t["result"] == result_filter.upper()]
    # Cap output size — keep context lean
    if len(trades) > 30:
        trades = trades[-30:]
    return {
        "status": "ok",
        "total_in_file": len(raw),
        "filtered": len(trades),
        "hours": hours,
        "trades": trades,
    }


def tool_query_signals(hours: float = 24) -> dict:
    """查詢訊號記錄。"""
    raw = _read_jsonl(_SIGNALS_PATH)
    signals = _filter_by_hours(raw, hours)
    if len(signals) > 30:
        signals = signals[-30:]
    return {
        "status": "ok",
        "total_in_file": len(raw),
        "filtered": len(signals),
        "hours": hours,
        "signals": signals,
    }


def tool_calc_metrics(hours: float = 0) -> dict:
    """計算核心指標。hours=0 代表全部。"""
    raw = _read_jsonl(_TRADES_PATH)
    trades = _filter_by_hours(raw, hours)
    trades = [_normalize_trade(t) for t in trades]

    if not trades:
        return {"status": "ok", "n": 0, "message": "no trades"}

    n = len(trades)
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    total_pnl = sum(pnls)
    avg_pnl = total_pnl / n if n > 0 else 0
    std_pnl = math.sqrt(sum((p - avg_pnl) ** 2 for p in pnls) / n) if n > 1 else 0
    sharpe = (avg_pnl / std_pnl * math.sqrt(n)) if std_pnl > 0 else 0

    # Max drawdown
    cumulative = []
    running = 0
    for p in pnls:
        running += p
        cumulative.append(running)
    peak = 0
    max_dd = 0
    for c in cumulative:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd

    # Win/loss streaks
    current_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    for p in pnls:
        if p > 0:
            current_streak = max(1, current_streak + 1) if current_streak >= 0 else 1
            max_win_streak = max(max_win_streak, current_streak)
        elif p < 0:
            current_streak = min(-1, current_streak - 1) if current_streak <= 0 else -1
            max_loss_streak = max(max_loss_streak, abs(current_streak))

    # Per-result breakdown
    up_trades = [t for t in trades if t["result"] == "UP"]
    dn_trades = [t for t in trades if t["result"] == "DOWN"]

    # Fill rate
    fill_rates = [t["fill_rate_pct"] for t in trades if t.get("fill_rate_pct", 0) > 0]
    avg_fill = sum(fill_rates) / len(fill_rates) if fill_rates else 0

    # Avg cost
    costs = [t["cost"] for t in trades if t.get("cost", 0) > 0]
    avg_cost = sum(costs) / len(costs) if costs else 0

    # Hourly breakdown (which hours perform best)
    hourly = {}
    for t in trades:
        ts = t.get("ts", "")
        if isinstance(ts, str) and len(ts) > 13:
            try:
                h = datetime.fromisoformat(ts).hour
                hourly.setdefault(h, []).append(t["pnl"])
            except ValueError:
                pass
    hourly_stats = {}
    for h, ps in sorted(hourly.items()):
        hourly_stats[f"{h:02d}"] = {
            "n": len(ps),
            "wr": round(sum(1 for p in ps if p > 0) / len(ps) * 100, 1),
            "pnl": round(sum(ps), 4),
        }

    return {
        "status": "ok",
        "n": n,
        "wins": wins,
        "losses": losses,
        "wr_pct": round(wins / n * 100, 1) if n > 0 else 0,
        "total_pnl": round(total_pnl, 4),
        "avg_pnl": round(avg_pnl, 4),
        "std_pnl": round(std_pnl, 4),
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(max_dd, 4),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "avg_fill_rate_pct": round(avg_fill, 1),
        "avg_cost": round(avg_cost, 4),
        "up_n": len(up_trades),
        "up_wr": round(sum(1 for t in up_trades if t["pnl"] > 0) / len(up_trades) * 100, 1) if up_trades else 0,
        "dn_n": len(dn_trades),
        "dn_wr": round(sum(1 for t in dn_trades if t["pnl"] > 0) / len(dn_trades) * 100, 1) if dn_trades else 0,
        "hourly": hourly_stats,
        "last_10_pnl": [round(p, 4) for p in pnls[-10:]],
    }


def tool_compare_periods(period_a_hours: float, period_b_hours: float) -> dict:
    """比較兩個時段。A = 較舊，B = 較新。
    例：period_a=168 (7天前開始), period_b=48 (最近48小時)。
    """
    raw = _read_jsonl(_TRADES_PATH)
    now = time.time()

    a_start = now - period_a_hours * 3600
    b_start = now - period_b_hours * 3600

    a_trades = [_normalize_trade(t) for t in raw if a_start <= _parse_ts(t) < b_start]
    b_trades = [_normalize_trade(t) for t in raw if _parse_ts(t) >= b_start]

    def _stats(trades):
        if not trades:
            return {"n": 0}
        pnls = [t["pnl"] for t in trades]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        total = sum(pnls)
        avg = total / n
        std = math.sqrt(sum((p - avg) ** 2 for p in pnls) / n) if n > 1 else 0
        return {
            "n": n, "wr_pct": round(wins / n * 100, 1),
            "total_pnl": round(total, 4), "avg_pnl": round(avg, 4),
            "sharpe": round(avg / std * math.sqrt(n), 2) if std > 0 else 0,
        }

    return {
        "status": "ok",
        "period_a": {"hours": period_a_hours, "label": f"{period_a_hours}h ago to {period_b_hours}h ago", **_stats(a_trades)},
        "period_b": {"hours": period_b_hours, "label": f"last {period_b_hours}h", **_stats(b_trades)},
    }


def tool_query_signal_accuracy(hours: float = 0) -> dict:
    """對比 signal predictions vs actual outcomes。
    Join signals + trades by cid prefix，計每個 signal source 嘅準確度。
    """
    raw_signals = _read_jsonl(_SIGNALS_PATH)
    raw_trades = _read_jsonl(_TRADES_PATH)
    signals = _filter_by_hours(raw_signals, hours)

    # Build trade lookup by cid prefix
    trade_by_cid = {}
    for t in raw_trades:
        cid = t.get("cid", t.get("condition_id", ""))[:8]
        if cid:
            trade_by_cid[cid] = _normalize_trade(t)

    # Match signals to outcomes
    matched = []
    for s in signals:
        cid = s.get("cid", "")[:8]
        trade = trade_by_cid.get(cid)
        if trade and trade.get("result"):
            fair = s.get("fair", 0.5)
            predicted_up = fair > 0.5
            actual_up = trade["result"] == "UP"
            correct = predicted_up == actual_up
            matched.append({
                "cid": cid,
                "fair": round(fair, 4),
                "bridge": round(s.get("bridge", 0), 4),
                "signal": round(s.get("signal", 0), 4),
                "ob_adj": round(s.get("ob_adj", 0), 4),
                "m1_sigma": round(s.get("m1_sigma", 0), 2),
                "predicted": "UP" if predicted_up else "DOWN",
                "actual": trade["result"],
                "correct": correct,
                "pnl": trade["pnl"],
            })

    if not matched:
        return {"status": "ok", "n": 0, "message": "no matched signal-trade pairs"}

    n = len(matched)
    correct = sum(1 for m in matched if m["correct"])

    # Accuracy by confidence bucket
    high_conf = [m for m in matched if abs(m["fair"] - 0.5) > 0.15]
    med_conf = [m for m in matched if 0.05 < abs(m["fair"] - 0.5) <= 0.15]
    low_conf = [m for m in matched if abs(m["fair"] - 0.5) <= 0.05]

    def _acc(items):
        if not items:
            return {"n": 0}
        c = sum(1 for i in items if i["correct"])
        return {"n": len(items), "accuracy_pct": round(c / len(items) * 100, 1)}

    # Bridge vs signal accuracy
    bridge_correct = sum(1 for m in matched if (m["bridge"] > 0.5) == (m["actual"] == "UP"))
    signal_items = [m for m in matched if m["signal"] > 0]
    signal_correct = sum(1 for m in signal_items if (m["signal"] > 0.5) == (m["actual"] == "UP"))

    return {
        "status": "ok",
        "n": n,
        "overall_accuracy_pct": round(correct / n * 100, 1),
        "high_confidence": _acc(high_conf),
        "medium_confidence": _acc(med_conf),
        "low_confidence": _acc(low_conf),
        "bridge_accuracy_pct": round(bridge_correct / n * 100, 1) if n > 0 else 0,
        "signal_accuracy_pct": round(signal_correct / len(signal_items) * 100, 1) if signal_items else 0,
        "signal_n": len(signal_items),
        "sample": matched[-10:],
    }


# Findings accumulator (written to JSONL at end)
_findings: list[dict] = []


def tool_write_finding(
    hypothesis: str,
    result: str,
    metric_value: float,
    sample_size: int,
    confidence: str,
    action: str,
    risk: str,
) -> dict:
    """寫一個結構化研究發現。
    result: confirmed / rejected / inconclusive
    confidence: high / medium / low
    action: 具體建議（改咩參數，改幾多）
    risk: 最可能錯嘅位
    """
    if result not in ("confirmed", "rejected", "inconclusive"):
        return {"status": "error", "message": "result must be confirmed/rejected/inconclusive"}
    if confidence not in ("high", "medium", "low"):
        return {"status": "error", "message": "confidence must be high/medium/low"}
    if sample_size < 5:
        return {"status": "error", "message": f"sample_size {sample_size} too small (min 5)"}

    finding = {
        "ts": datetime.now(tz=_HKT).isoformat(),
        "hypothesis": hypothesis,
        "result": result,
        "metric_value": metric_value,
        "sample_size": sample_size,
        "confidence": confidence,
        "action": action,
        "risk": risk,
    }
    _findings.append(finding)
    logger.info("FINDING [%s] %s: %s (n=%d, conf=%s)",
                result, hypothesis[:50], action[:50], sample_size, confidence)
    return {"status": "ok", "finding_id": len(_findings), "saved": True}


# ═══════════════════════════════════════
#  Tool Registry — schema for Claude API
# ═══════════════════════════════════════

_TOOL_MAP = {
    "query_trades": tool_query_trades,
    "query_signals": tool_query_signals,
    "calc_metrics": tool_calc_metrics,
    "compare_periods": tool_compare_periods,
    "query_signal_accuracy": tool_query_signal_accuracy,
    "write_finding": tool_write_finding,
}

_TOOL_SCHEMAS = [
    {
        "name": "query_trades",
        "description": "查詢 MM 交易記錄。返回 trades 列表 with cid, ts, result (UP/DOWN), pnl, cost, payout。",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "number", "description": "最近幾小時。0=全部。", "default": 24},
                "result_filter": {"type": "string", "description": "只要 UP 或 DOWN。空=全部。", "default": ""},
            },
        },
    },
    {
        "name": "query_signals",
        "description": "查詢訊號記錄。返回 signals 列表 with cid, m1, m1_sigma, bridge, signal, fair, xdiv, ob_adj。",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "number", "description": "最近幾小時。0=全部。", "default": 24},
            },
        },
    },
    {
        "name": "calc_metrics",
        "description": "計算核心指標：WR, PnL, Sharpe, max drawdown, fill rate, per-hour breakdown, streaks。",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "number", "description": "最近幾小時。0=全部。", "default": 0},
            },
        },
    },
    {
        "name": "compare_periods",
        "description": "A/B 比較兩個時段嘅表現。period_a=較舊邊界（小時），period_b=較新邊界。例：a=168, b=48 = 比較上週 vs 最近兩日。",
        "input_schema": {
            "type": "object",
            "properties": {
                "period_a_hours": {"type": "number", "description": "較舊時段起點（幾小時前）"},
                "period_b_hours": {"type": "number", "description": "較新時段起點（幾小時前）"},
            },
            "required": ["period_a_hours", "period_b_hours"],
        },
    },
    {
        "name": "query_signal_accuracy",
        "description": "對比 signal predictions vs actual outcomes。計 bridge/signal/fair 嘅預測準確度，分 confidence bucket。",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "number", "description": "最近幾小時。0=全部。", "default": 0},
            },
        },
    },
    {
        "name": "write_finding",
        "description": "寫一個結構化研究發現。每個發現必須有 hypothesis, result, action, confidence。sample_size < 5 會被拒絕。",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string", "description": "測試咗咩假設"},
                "result": {"type": "string", "enum": ["confirmed", "rejected", "inconclusive"]},
                "metric_value": {"type": "number", "description": "核心數字（WR %, PnL, Sharpe 等）"},
                "sample_size": {"type": "integer", "description": "樣本數（最少 5）"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "action": {"type": "string", "description": "具體建議：改咩參數、改幾多、點驗證"},
                "risk": {"type": "string", "description": "最可能錯嘅位"},
            },
            "required": ["hypothesis", "result", "metric_value", "sample_size", "confidence", "action", "risk"],
        },
    },
]

# ═══════════════════════════════════════
#  Claude API — Tool-Use Loop
# ═══════════════════════════════════════

_SYSTEM_PROMPT = """你係一個量化策略研究員，專門分析 Polymarket BTC/ETH 15-minute binary market maker 策略。

你嘅工作：
1. 用工具查詢真實交易 + 訊號數據
2. 發現 pattern、問題、改進機會
3. 用 write_finding 寫結構化發現（每個發現必須 actionable）

可用工具（直接用呢啲名，唔好加 prefix）：
- calc_metrics — 計 WR / PnL / Sharpe 等核心指標
- query_trades — 查交易記錄
- query_signals — 查訊號記錄
- compare_periods — A/B 比較兩個時段
- query_signal_accuracy — 訊號預測準確度
- write_finding — 寫結構化發現

研究方向（按優先順序）：
- 勝率趨勢：整體 + 分時段 + 分方向（UP vs DOWN）
- Signal 質量：bridge vs triple signal vs OB，邊個最準？
- Fill rate：掛單成交率，有冇惡化？
- 時段效應：邊個小時 / 邊個 window 表現最好/最差？
- 異常檢測：連續虧損嘅共通點、outlier trades
- 參數建議：基於數據嘅具體調整（唔好猜，要有數字支持）

規則：
- 只報告工具返回嘅數字。唔好自己計算或估計。
- sample_size < 5 嘅發現唔好寫（write_finding 會拒絕）。
- 每個 action 要具體：「改 X 參數由 Y 到 Z」，唔好寫「考慮改進」。
- 唔確定就寫 result=inconclusive，唔好扮有結論。
- 最多寫 5 個 findings。質量 > 數量。
- 完成後用一段文字總結所有發現。"""


def _call_api(messages: list[dict], system: str) -> dict:
    """Call Claude API with tool-use. Returns raw response dict."""
    if not _PROXY_BASE_URL or not _PROXY_API_KEY:
        raise RuntimeError("PROXY_BASE_URL or PROXY_API_KEY not set")

    payload = json.dumps({
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "temperature": _TEMPERATURE,
        "system": system,
        "messages": messages,
        "tools": _TOOL_SCHEMAS,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_PROXY_API_KEY}",
        "anthropic-version": "2023-06-01",
    }

    req = urllib.request.Request(
        f"{_PROXY_BASE_URL}/messages",
        data=payload, method="POST", headers=headers,
    )
    # Single retry on connection drop (NOT on tool execution — safe here)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except (ConnectionError, urllib.error.URLError) as e:
            if attempt == 0:
                logger.warning("Connection dropped, retrying in 3s: %s", e)
                time.sleep(3)
                # Rebuild request (urllib might have consumed it)
                req = urllib.request.Request(
                    f"{_PROXY_BASE_URL}/messages",
                    data=payload, method="POST", headers=headers,
                )
            else:
                raise


def _execute_tool(name: str, input_args: dict) -> str:
    """Execute a tool and return JSON string result."""
    # Strip common prefixes models sometimes add (mcp__, tools__, etc.)
    clean = name
    for prefix in ("mcp__", "tools__", "functions__", "tool__"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    fn = _TOOL_MAP.get(clean) or _TOOL_MAP.get(name)
    if not fn:
        return json.dumps({"status": "error", "message": f"unknown tool: {name}"})
    try:
        result = fn(**input_args)
        # Cap output size to prevent context explosion
        result_str = json.dumps(result, default=str)
        if len(result_str) > 4000:
            result_str = result_str[:3900] + '..."truncated"}'
        return result_str
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate. 1 token ≈ 4 chars for English, ~2 for CJK."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 3
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(json.dumps(block)) // 3
    return total


def run_research(dry_run: bool = False, verbose: bool = False) -> list[dict]:
    """Run multi-round research loop. Returns list of findings."""
    global _findings
    _findings = []

    total_input_tokens = 0
    total_output_tokens = 0

    def _api_turn(msgs: list[dict]) -> dict | None:
        """Single API turn with token tracking. Returns response or None."""
        nonlocal total_input_tokens, total_output_tokens
        try:
            resp = _call_api(msgs, _SYSTEM_PROMPT)
            usage = resp.get("usage", {})
            total_input_tokens += usage.get("input_tokens", 0)
            total_output_tokens += usage.get("output_tokens", 0)
            return resp
        except Exception as e:
            logger.error("API call failed: %s", e)
            return None

    def _run_tool_turn(msgs: list[dict], verbose_flag: bool) -> bool:
        """Execute one API turn with tool calls. Returns True if successful."""
        resp = _api_turn(msgs)
        if not resp:
            return False
        content = resp.get("content", [])
        msgs.append({"role": "assistant", "content": content})

        if resp.get("stop_reason") == "tool_use":
            tool_results = []
            for block in content:
                if block.get("type") == "tool_use":
                    name = block["name"]
                    inp = block.get("input", {})
                    logger.info("TOOL: %s(%s)", name,
                                json.dumps(inp, ensure_ascii=False)[:80])
                    result_str = _execute_tool(name, inp)
                    if verbose_flag:
                        logger.info("RESULT: %s", result_str[:200])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": result_str,
                    })
            msgs.append({"role": "user", "content": tool_results})
        for block in content:
            if block.get("type") == "text" and verbose_flag:
                logger.info("AI: %s", block["text"][:300])
        return True

    # ── Phase 1: Gather data (3 tool calls max) ──
    logger.info("Phase 1: Gathering data...")
    phase1_msgs = [{"role": "user", "content":
        "Phase 1: 用 3 個工具收集數據。請依次 call：\n"
        "1. calc_metrics(hours=0) — 整體指標\n"
        "2. query_signal_accuracy(hours=0) — 訊號準確度\n"
        "3. query_trades(hours=0) — 交易明細\n"
        "每次只 call 1 個工具，唔好寫分析。"}]

    if dry_run:
        logger.info("[DRY-RUN] Would run Phase 1 + 2")
        return _findings, "", 0.0

    for i in range(4):  # 3 tool calls + 1 potential text
        ok = _run_tool_turn(phase1_msgs, verbose)
        if not ok:
            break
        # Check if model stopped (end_turn)
        last = phase1_msgs[-1] if phase1_msgs else {}
        if last.get("role") == "assistant":
            content = last.get("content", [])
            if isinstance(content, list):
                has_tool = any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))
                if not has_tool:
                    break

    # ── Extract AI's own summary from phase 1 (much smaller than raw data) ──
    ai_summary = ""
    for m in reversed(phase1_msgs):
        if m.get("role") == "assistant":
            content = m.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        ai_summary = block["text"]
                        break
            if ai_summary:
                break
    # Fallback to raw tool results if no AI summary
    if not ai_summary:
        gathered_data = []
        for m in phase1_msgs:
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            gathered_data.append(block.get("content", ""))
        ai_summary = "\n---\n".join(d[:1000] for d in gathered_data[:3])
    data_summary = ai_summary[:3000]
    logger.info("Phase 1 done: summary %d chars", len(data_summary))

    # ── Phase 2: Text-only analysis (no tools — avoids proxy payload issue) ──
    logger.info("Phase 2: Analysis (text-only)...")

    phase2_system = """你係量化策略研究員。分析以下 BTC/ETH 15-min binary MM 數據。

嚴格輸出格式（唔好加其他嘢）：
```json
[
  {"hypothesis": "...", "result": "confirmed", "metric_value": 60.3, "sample_size": 63, "confidence": "high", "action": "具體改咩參數", "risk": "最可能錯嘅位"}
]
```
SUMMARY: 一段話總結

規則：
- result 只能係 confirmed / rejected / inconclusive
- confidence 只能係 high / medium / low
- sample_size 必須 >= 5
- action 要具體：「改 X 由 Y 到 Z」
- 最多 5 個 findings
- 先輸出 JSON array，再輸出 SUMMARY: 開頭嘅一段話"""

    phase2_payload = json.dumps({
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "temperature": _TEMPERATURE,
        "system": phase2_system,
        "messages": [{"role": "user", "content": data_summary}],
    }).encode("utf-8")

    phase2_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_PROXY_API_KEY}",
        "anthropic-version": "2023-06-01",
    }

    phase2_text = ""
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                f"{_PROXY_BASE_URL}/messages",
                data=phase2_payload, method="POST", headers=phase2_headers,
            )
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                p2_data = json.loads(resp.read().decode())
            usage = p2_data.get("usage", {})
            total_input_tokens += usage.get("input_tokens", 0)
            total_output_tokens += usage.get("output_tokens", 0)
            content = p2_data.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    phase2_text = block["text"]
            break
        except Exception as e:
            if attempt == 0:
                logger.warning("Phase 2 attempt 1 failed: %s, retrying in 3s", e)
                time.sleep(3)
            else:
                logger.error("Phase 2 failed: %s", e)

    if verbose and phase2_text:
        logger.info("Phase 2 output: %s", phase2_text[:500])

    # Parse findings — try JSON first, fallback to prose extraction
    summary = ""
    if phase2_text:
        # Try JSON parse (if model obeyed format)
        json_extracted = False
        parts = phase2_text.split("SUMMARY:")
        json_part = parts[0].strip()
        summary = parts[1].strip() if len(parts) > 1 else ""

        if json_part.startswith("```"):
            lines = json_part.split("\n")
            json_part = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            parsed = json.loads(json_part)
            if isinstance(parsed, list):
                for f in parsed:
                    if isinstance(f, dict) and "hypothesis" in f:
                        try:
                            tool_write_finding(
                                hypothesis=str(f.get("hypothesis", "")),
                                result=str(f.get("result", "inconclusive")),
                                metric_value=float(f.get("metric_value", 0)),
                                sample_size=int(f.get("sample_size", 0)),
                                confidence=str(f.get("confidence", "low")),
                                action=str(f.get("action", "")),
                                risk=str(f.get("risk", "")),
                            )
                        except Exception:
                            pass
                json_extracted = True
        except json.JSONDecodeError:
            pass

        if not json_extracted:
            # Prose mode: model gave markdown analysis. Save as single finding.
            logger.info("Extracting findings from prose analysis")
            # Use Phase 1 summary + Phase 2 analysis as the finding
            tool_write_finding(
                hypothesis="Automated strategy review (full analysis in report)",
                result="confirmed",
                metric_value=0,
                sample_size=max(5, len(_read_jsonl(_TRADES_PATH))),
                confidence="medium",
                action="See research_report.md for detailed analysis and recommendations",
                risk="Proxy model output in prose, not structured JSON — manual review needed",
            )
            summary = phase2_text[:3000]

    if not summary:
        summary = data_summary

    # Combine messages for cost tracking
    messages = phase1_msgs

    # Extract final text summary from last assistant message
    summary = ""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            content = m.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        summary = block["text"]
                        break
            if summary:
                break

    # Cost estimate (Sonnet pricing: $3/M input, $15/M output)
    cost_est = total_input_tokens / 1_000_000 * 3 + total_output_tokens / 1_000_000 * 15
    logger.info("API usage: %d input + %d output tokens ≈ $%.3f",
                total_input_tokens, total_output_tokens, cost_est)

    return _findings, summary, cost_est


# ═══════════════════════════════════════
#  Persistence + Reporting
# ═══════════════════════════════════════

def _save_findings(findings: list[dict], summary: str, cost: float):
    """Save findings to JSONL (atomic write)."""
    os.makedirs(_LOG_DIR, exist_ok=True)

    record = {
        "run_ts": datetime.now(tz=_HKT).isoformat(),
        "n_findings": len(findings),
        "cost_usd": round(cost, 4),
        "findings": findings,
        "summary": summary[:2000],
    }

    # Append to findings JSONL
    with open(_FINDINGS_PATH, "a") as f:
        f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

    # Also write latest report as readable markdown
    md_lines = [
        f"# Research Report — {datetime.now(tz=_HKT).strftime('%Y-%m-%d %H:%M HKT')}",
        f"> Cost: ${cost:.3f} | Findings: {len(findings)}",
        "",
    ]
    for i, finding in enumerate(findings, 1):
        icon = {"confirmed": "V", "rejected": "X", "inconclusive": "?"}
        md_lines.append(f"## [{icon.get(finding['result'], '?')}] Finding {i}: {finding['hypothesis']}")
        md_lines.append(f"- Result: **{finding['result']}** | Metric: {finding['metric_value']} | n={finding['sample_size']} | Confidence: {finding['confidence']}")
        md_lines.append(f"- Action: {finding['action']}")
        md_lines.append(f"- Risk: {finding['risk']}")
        md_lines.append("")
    md_lines.append("## Full Analysis")
    md_lines.append(summary[:4000])

    fd, tmp = tempfile.mkstemp(dir=_LOG_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(md_lines))
        os.replace(tmp, _REPORT_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    logger.info("Saved %d findings to %s + %s", len(findings), _FINDINGS_PATH, _REPORT_PATH)


def _send_telegram(findings: list[dict], summary: str, cost: float):
    """Send Telegram summary via shared_infra."""
    try:
        from shared_infra.telegram import send_telegram
    except ImportError:
        logger.warning("shared_infra.telegram not available, skip Telegram")
        return

    if not findings:
        send_telegram("<b>Research Cycle</b>\nNo actionable findings this round.")
        return

    lines = [f"<b>Research Cycle</b> | {len(findings)} findings | ${cost:.3f}"]
    for f in findings[:5]:
        icon = {"confirmed": "V", "rejected": "X", "inconclusive": "?"}[f["result"]]
        lines.append(f"\n[{icon}] <b>{f['hypothesis'][:60]}</b>")
        lines.append(f"  {f['result']} | n={f['sample_size']} | {f['confidence']}")
        lines.append(f"  Action: {f['action'][:80]}")

    # Truncate for Telegram (4096 char limit)
    msg = "\n".join(lines)
    if len(msg) > 3900:
        msg = msg[:3900] + "\n..."

    send_telegram(msg)
    logger.info("Telegram report sent")


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AI Research Cycle for MM strategy")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't call Claude API")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--no-telegram", action="store_true", help="Skip Telegram notification")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Sanity check: any data?
    trades = _read_jsonl(_TRADES_PATH)
    signals = _read_jsonl(_SIGNALS_PATH)
    logger.info("Data: %d trades, %d signals", len(trades), len(signals))

    if len(trades) < 5:
        logger.warning("Not enough trades (%d < 5) for meaningful research. Skipping.", len(trades))
        return

    findings, summary, cost = run_research(
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    if findings:
        _save_findings(findings, summary, cost)

    if not args.no_telegram and not args.dry_run and findings:
        _send_telegram(findings, summary, cost)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Research complete: {len(findings)} findings, ${cost:.3f}")
    for f in findings:
        icon = {"confirmed": "V", "rejected": "X", "inconclusive": "?"}[f["result"]]
        print(f"  [{icon}] {f['hypothesis'][:60]}")
        print(f"      -> {f['action'][:80]}")
    print(f"{'='*60}")
    if summary:
        print(f"\nSummary:\n{summary[:500]}")


if __name__ == "__main__":
    main()
