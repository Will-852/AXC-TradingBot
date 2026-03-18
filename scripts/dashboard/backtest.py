"""backtest.py — BT pool + worker + handlers + aggTrades."""

import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta

from scripts.dashboard.constants import HOME, SCRIPTS_DIR, HKT, BT_DATA_DIR

# ── BT Constants ─────────────────────────────────────────────────────
_BT_ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BNBUSDT", "POLUSDT", "XAGUSDT", "XAUUSDT"}
_BT_ASTER_SYMBOLS = {"XAGUSDT", "XAUUSDT"}  # use Aster DEX for klines
_BT_MAX_DAYS = 365
_BT_JOB_TIMEOUT = 600  # 10 minutes
_BT_INTERVAL_MAX_DAYS = {
    "1m": 7, "5m": 30, "15m": 60,
    "1h": 365, "4h": 365, "1d": 365,
}
_BT_VALID_INTERVALS = set(_BT_INTERVAL_MAX_DAYS.keys())

_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

# ── BT State ─────────────────────────────────────────────────────────
_bt_pool: ProcessPoolExecutor | None = None  # lazy init to avoid child re-spawn
_bt_lock = threading.Lock()
_bt_jobs: dict = {}   # job_id → {"status", "result", "error", "symbol", "days"}
_BT_MAX_JOBS = 10     # evict oldest completed jobs beyond this

# ── AggTrades State ──────────────────────────────────────────────────
_aggtrades_jobs = {}  # job_id → {"status": "running"|"done"|"error", "result": ..., "started": float}
_aggtrades_lock = threading.Lock()  # protects _aggtrades_jobs + ensures single concurrent fetch
_AGGTRADES_JOB_TTL = 600  # seconds — evict completed jobs after this
_AGGTRADES_UNSUPPORTED = {"XAGUSDT", "XAUUSDT"}  # Aster DEX — no Binance aggTrades
# BTC ~4min/day, SOL ~4min/day, ETH ~2min/day via Binance aggTrades API.
_HIGH_VOL_MAX_DAYS = {"BTCUSDT": 1, "ETHUSDT": 2, "SOLUSDT": 1}

_REPORT_FORMAT_VERSION = "1.0"


def _get_bt_pool() -> ProcessPoolExecutor:
    """Lazy-init ProcessPool to prevent child process re-creating it on import."""
    global _bt_pool
    if _bt_pool is None:
        _bt_pool = ProcessPoolExecutor(max_workers=2)
    return _bt_pool


def _evict_old_jobs():
    """Remove oldest completed/error jobs when over _BT_MAX_JOBS. Must hold _bt_lock."""
    finished = [(k, v) for k, v in _bt_jobs.items()
                if v["status"] in ("done", "error")]
    if len(finished) <= _BT_MAX_JOBS:
        return
    # job_id contains timestamp — sort by key (oldest first)
    finished.sort(key=lambda x: x[0])
    for k, _ in finished[:len(finished) - _BT_MAX_JOBS]:
        del _bt_jobs[k]


def _run_bt_worker(symbol: str, days: int, balance: float,
                   strategy_params: dict | None = None,
                   param_overrides: dict | None = None,
                   allowed_modes: list | None = None,
                   mode_confirmation: int | None = None,
                   platform: str = "binance") -> dict:
    """Module-level worker for ProcessPoolExecutor (must be picklable).
    設計決定：worker 內 verify sys.path 因為 child process 可能冇 parent 嘅 path。"""
    import sys as _sys
    _home = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
    if _home not in _sys.path:
        _sys.path.insert(0, _home)
    _scripts = os.path.join(_home, "scripts")
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)

    from backtest.fetch_historical import fetch_klines_range
    from backtest.engine import BacktestEngine, WARMUP_CANDLES
    from backtest.metrics_ext import extend_summary
    from datetime import datetime, timezone, timedelta

    def _calc_range(d, interval):
        now = datetime.now(timezone.utc)
        end_ms = int(now.timestamp() * 1000)
        wh = WARMUP_CANDLES * (4 if interval == "4h" else 1)
        start_ms = int((now - timedelta(hours=d * 24 + wh)).timestamp() * 1000)
        return start_ms, end_ms

    s1, e1 = _calc_range(days, "1h")
    s4, e4 = _calc_range(days, "4h")
    df_1h = fetch_klines_range(symbol, "1h", s1, e1, platform)
    df_4h = fetch_klines_range(symbol, "4h", s4, e4, platform)

    # Fetch alt data (funding rate + OI + on-chain) — best effort, non-blocking
    alt_data = {}
    try:
        from backtest.fetch_funding_oi import fetch_funding_rate_history, fetch_oi_history, fetch_longshort_ratio
        alt_data["funding"] = fetch_funding_rate_history(symbol, s1, e1)
        alt_data["oi"] = fetch_oi_history(symbol, s1, e1, period="1h")
        alt_data["ls_ratio"] = fetch_longshort_ratio(symbol, s1, e1, period="1h")
    except Exception as e:
        logging.warning("Alt data (funding/OI) fetch failed: %s", e)
    try:
        from backtest.fetch_onchain import fetch_onchain_metrics, compute_onchain_signals
        from datetime import datetime, timezone
        _start_dt = datetime.fromtimestamp(s1 / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        _end_dt = datetime.fromtimestamp(e1 / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        _oc = fetch_onchain_metrics(symbol, _start_dt, _end_dt)
        if not _oc.empty:
            alt_data["onchain"] = compute_onchain_signals(_oc)
    except Exception as e:
        logging.warning("Alt data (on-chain) fetch failed: %s", e)

    # Monkey-patch strategy constants if overrides provided
    sp = strategy_params or {}
    _originals = {}
    _patched_modules = []
    if sp:
        import trader_cycle.strategies.range_strategy as _rs
        import trader_cycle.strategies.trend_strategy as _ts
        _STRATEGY_MAP = {
            "range_sl":       [(_rs, "RANGE_SL_ATR_MULT"), (_ts, None)],
            "range_rr":       [(_rs, "RANGE_MIN_RR"), (_ts, None)],
            "trend_sl":       [(_rs, None), (_ts, "TREND_SL_ATR_MULT")],
            "trend_rr":       [(_rs, None), (_ts, "TREND_MIN_RR")],
            "risk_pct":       [(_rs, "RANGE_RISK_PCT"), (_ts, "TREND_RISK_PCT")],
            "range_leverage": [(_rs, "RANGE_LEVERAGE"), (_ts, None)],
            "trend_leverage": [(_rs, None), (_ts, "TREND_LEVERAGE")],
        }
        for key, val in sp.items():
            targets = _STRATEGY_MAP.get(key, [])
            for mod, attr in targets:
                if attr and hasattr(mod, attr):
                    _originals[(mod, attr)] = getattr(mod, attr)
                    setattr(mod, attr, val)
                    _patched_modules.append((mod, attr))

    try:
        # Extract commission/slippage overrides (if user set them in dashboard)
        _po = param_overrides or {}
        _commission = _po.pop("commission_rate", None)
        _slippage = _po.pop("sl_slippage_pct", None)
        _extra = {}
        if _commission is not None:
            _extra["commission_rate"] = float(_commission)
        if _slippage is not None:
            _extra["sl_slippage_pct"] = float(_slippage)
        engine = BacktestEngine(
            symbol=symbol, df_1h=df_1h, df_4h=df_4h,
            initial_balance=balance, quiet=True,
            param_overrides=_po,
            allowed_modes=allowed_modes,
            mode_confirmation=mode_confirmation,
            **_extra,
        )
        result = engine.run()
        result = extend_summary(result)

        # Noise injection MC (opt-in: only if param_overrides has noise_mc=true)
        _run_noise = (_po or {}).pop("noise_mc", False)
        if _run_noise:
            try:
                from backtest.monte_carlo import run_noise_mc
                _eng_kwargs = {
                    "symbol": symbol, "initial_balance": balance,
                    "param_overrides": _po, "allowed_modes": allowed_modes,
                    "mode_confirmation": mode_confirmation, "quiet": True,
                }
                if _commission is not None:
                    _eng_kwargs["commission_rate"] = float(_commission)
                if _slippage is not None:
                    _eng_kwargs["sl_slippage_pct"] = float(_slippage)
                noise_result = run_noise_mc(df_1h, df_4h, _eng_kwargs, result)
                result["noise_mc"] = noise_result
            except Exception as e:
                logging.warning("Noise MC failed: %s", e)
                result["noise_mc"] = None

        # Attach alt data summary to result (for dashboard display)
        if alt_data:
            _alt_summary = {}
            if "funding" in alt_data and not alt_data["funding"].empty:
                _fr = alt_data["funding"]
                _alt_summary["funding_rate"] = {
                    "count": len(_fr),
                    "mean": round(float(_fr["funding_rate"].mean()), 6),
                    "max": round(float(_fr["funding_rate"].max()), 6),
                    "min": round(float(_fr["funding_rate"].min()), 6),
                    "last": round(float(_fr["funding_rate"].iloc[-1]), 6),
                    "extreme_count": int((_fr["funding_rate"].abs() > 0.001).sum()),
                }
            if "oi" in alt_data and not alt_data["oi"].empty:
                _oi = alt_data["oi"]
                _alt_summary["open_interest"] = {
                    "count": len(_oi),
                    "last_oi": round(float(_oi["oi"].iloc[-1]), 2),
                    "last_oi_usd": round(float(_oi["oi_value"].iloc[-1]), 0),
                    "change_pct": round(float((_oi["oi"].iloc[-1] / _oi["oi"].iloc[0] - 1) * 100), 2) if len(_oi) > 1 else 0,
                }
            if "onchain" in alt_data and not alt_data["onchain"].empty:
                _oc = alt_data["onchain"]
                _last = _oc.iloc[-1]
                _alt_summary["onchain"] = {
                    "netflow_zscore": round(float(_last.get("netflow_zscore", 0)), 2),
                    "supply_ex_change": round(float(_last.get("supply_ex_change", 0)), 2),
                    "addr_momentum": round(float(_last.get("addr_momentum", 1)), 2),
                }
            if _alt_summary:
                result["alt_data"] = _alt_summary
    finally:
        # Restore monkey-patched constants
        for (mod, attr), orig_val in _originals.items():
            setattr(mod, attr, orig_val)

    # Serialize trades
    result["trades"] = [t.to_dict() for t in result["trades"]]
    return result


def _compute_stats_from_trades(trades: list, balance: float = 10000) -> dict:
    """Compute basic stats from trade dicts (for legacy JSONL without meta)."""
    if not trades:
        return {}
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    total_pnl = sum(t.get("pnl") or 0 for t in trades)
    n = len(trades)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t.get("pnl") or 0
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return {
        "return_pct": round(total_pnl / balance * 100, 2) if balance else 0,
        "win_rate": round(wins / n * 100, 1) if n else 0,
        "total_trades": n,
        "max_drawdown_pct": round(max_dd / balance * 100, 2) if balance else 0,
        "expectancy": round(total_pnl / n, 2) if n else 0,
        "estimated": True,  # balance was assumed, not from original run
    }


def handle_bt_list():
    """Return metadata of existing backtest JSONL files.
    Fast path: if meta sidecar exists with stats, only count JSONL lines (no parse).
    Slow path (one-time): parse trades, compute stats, persist meta for next call."""
    results = []
    if not os.path.isdir(BT_DATA_DIR):
        return results
    for fname in sorted(os.listdir(BT_DATA_DIR)):
        if not fname.endswith("_trades.jsonl"):
            continue
        # Formats: bt_BTCUSDT_60d_trades.jsonl or bt_BTCUSDT_60d_v2_trades.jsonl
        stem = fname.replace("bt_", "", 1).replace("_trades.jsonl", "")
        stem_clean = re.sub(r'_v\d+$', '', stem)
        parts = stem_clean.rsplit("_", 1)
        if len(parts) != 2:
            continue
        symbol, days_str = parts
        try:
            days = int(days_str.replace("d", ""))
        except ValueError:
            continue
        fpath = os.path.join(BT_DATA_DIR, fname)
        is_imported = bool(re.search(r'_v\d+_trades\.jsonl$', fname))

        # Check meta sidecar first
        meta_fname = fname.replace("_trades.jsonl", "_meta.json")
        meta_path = os.path.join(BT_DATA_DIR, meta_fname)
        has_meta = False
        entry = {
            "symbol": symbol, "days": days,
            "file": fname, "is_imported": is_imported,
        }

        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as mf:
                    meta = json.load(mf)
                entry["balance"] = meta.get("balance")
                entry["strategy_params"] = meta.get("strategy_params", {})
                entry["param_overrides"] = meta.get("param_overrides", {})
                entry["allowed_modes"] = meta.get("allowed_modes")
                entry["mode_confirmation"] = meta.get("mode_confirmation")
                entry["stats"] = meta.get("stats", {})
                entry["created_at"] = meta.get("created_at", "")
                has_meta = bool(entry["stats"])
            except (json.JSONDecodeError, OSError):
                pass

        if has_meta:
            # Fast path: only count lines, skip JSON parsing
            try:
                with open(fpath, encoding="utf-8") as f:
                    entry["trade_count"] = sum(1 for line in f if line.strip())
            except OSError:
                entry["trade_count"] = 0
        else:
            # Slow path (one-time): parse trades → compute stats → persist meta
            trades = []
            try:
                with open(fpath, encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            trades.append(json.loads(line))
            except (OSError, json.JSONDecodeError):
                pass
            entry["trade_count"] = len(trades)

            if trades:
                stats = _compute_stats_from_trades(trades)
                entry["stats"] = stats
                entry["balance"] = 10000
                try:
                    backfill_meta = {
                        "symbol": symbol, "days": days, "balance": 10000,
                        "strategy_params": {}, "param_overrides": {},
                        "stats": stats,
                        "created_at": datetime.now(HKT).isoformat(),
                        "backfilled": True,
                    }
                    tmp_m = tempfile.NamedTemporaryFile(
                        mode='w', dir=BT_DATA_DIR, delete=False, suffix='.tmp')
                    json.dump(backfill_meta, tmp_m, ensure_ascii=False)
                    tmp_m.close()
                    os.replace(tmp_m.name, meta_path)
                    logging.info("Backfilled meta for %s", fname)
                except OSError:
                    pass

        results.append(entry)
    return results


def handle_bt_klines(qs: dict):
    """Return klines for chart display. Supports multiple intervals."""
    symbol = qs.get("symbol", [""])[0].upper()
    days_str = qs.get("days", ["60"])[0]
    interval = qs.get("interval", ["1h"])[0].lower()
    if not symbol:
        return 400, {"error": "symbol required"}
    try:
        days = int(days_str)
    except ValueError:
        return 400, {"error": "invalid days"}
    if interval not in _BT_VALID_INTERVALS:
        return 400, {"error": f"invalid interval: {interval}. Valid: {sorted(_BT_VALID_INTERVALS)}"}

    # Enforce max days per interval
    max_days = _BT_INTERVAL_MAX_DAYS[interval]
    if days > max_days:
        days = max_days

    if HOME not in sys.path:
        sys.path.insert(0, HOME)
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from backtest.fetch_historical import fetch_klines_range
    from backtest.engine import WARMUP_CANDLES

    try:
        now = datetime.now(timezone.utc)
        end_ms = int(now.timestamp() * 1000)
        # Warmup buffer only meaningful for 1h (backtest engine timeframe)
        wh = WARMUP_CANDLES if interval == "1h" else 0
        start_ms = int((now - timedelta(hours=days * 24 + wh)).timestamp() * 1000)

        plat = "aster" if symbol in _BT_ASTER_SYMBOLS else "binance"
        df = fetch_klines_range(symbol, interval, start_ms, end_ms, plat)
    except Exception as e:
        return 500, {"error": f"Failed to fetch klines: {e}"}
    # KLineChart format
    candles = []
    for _, row in df.iterrows():
        candles.append({
            "timestamp": int(row["open_time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        })
    return 200, {"candles": candles, "interval": interval, "days": days}


def handle_bt_results(qs: dict):
    """Return trades for a specific backtest result file.
    Accepts either ?file=bt_BTCUSDT_30d_v2_trades.jsonl (exact)
    or ?symbol=BTCUSDT&days=30 (legacy, finds first match)."""
    # Prefer exact file parameter (used by loadExisting for _v{N} files)
    file_param = qs.get("file", [""])[0]
    if file_param:
        # Sanitize: only allow expected filename patterns
        if not file_param.endswith("_trades.jsonl") or "/" in file_param:
            return 400, {"error": "invalid file parameter"}
        fpath = os.path.join(BT_DATA_DIR, file_param)
        if not os.path.isfile(fpath):
            return 404, {"error": "file not found"}
        trades = []
        with open(fpath, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    trades.append(json.loads(line))
        return 200, {"file": file_param, "trades": trades}

    # Legacy: symbol + days lookup
    symbol = qs.get("symbol", [""])[0].upper()
    days_str = qs.get("days", [""])[0]
    if not symbol or not days_str:
        return 400, {"error": "symbol and days (or file) required"}
    try:
        days = int(days_str)
    except ValueError:
        return 400, {"error": "invalid days"}
    fname = f"bt_{symbol}_{days}d_trades.jsonl"
    fpath = os.path.join(BT_DATA_DIR, fname)
    if not os.path.isfile(fpath):
        return 404, {"error": "file not found"}
    trades = []
    with open(fpath, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))
    return 200, {"symbol": symbol, "days": days, "trades": trades}


def _save_bt_result(symbol: str, days: int, trades: list):
    """Auto-save backtest trades to JSONL so 'Load old results' can find them."""
    try:
        os.makedirs(BT_DATA_DIR, exist_ok=True)
        fname = f"bt_{symbol}_{days}d_trades.jsonl"
        fpath = os.path.join(BT_DATA_DIR, fname)
        tmp = fpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        os.replace(tmp, fpath)
    except OSError as e:
        logging.warning("Failed to save backtest result: %s", e)


def _save_bt_metadata(symbol: str, days: int, balance: float,
                      strategy_params: dict | None = None,
                      param_overrides: dict | None = None,
                      allowed_modes: list | None = None,
                      mode_confirmation: int | None = None,
                      stats: dict | None = None):
    """Save backtest run metadata as JSON sidecar for later reference."""
    try:
        os.makedirs(BT_DATA_DIR, exist_ok=True)
        fname = f"bt_{symbol}_{days}d_meta.json"
        fpath = os.path.join(BT_DATA_DIR, fname)
        meta = {
            "symbol": symbol, "days": days, "balance": balance,
            "strategy_params": strategy_params or {},
            "param_overrides": param_overrides or {},
            "allowed_modes": allowed_modes,
            "mode_confirmation": mode_confirmation,
            "stats": stats or {},
            "created_at": datetime.now(HKT).isoformat(),
        }
        tmp = tempfile.NamedTemporaryFile(
            mode='w', dir=os.path.dirname(fpath),
            delete=False, suffix='.tmp')
        json.dump(meta, tmp, ensure_ascii=False)
        tmp.close()
        os.replace(tmp.name, fpath)
    except OSError as e:
        logging.warning("Failed to save backtest metadata: %s", e)


def handle_bt_run(body: str):
    """Start a backtest run in ProcessPool."""
    try:
        req = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON"}
    symbol = req.get("symbol", "BTCUSDT").upper()
    try:
        days = int(req.get("days", 60))
        balance = float(req.get("balance", 10000))
    except (ValueError, TypeError):
        return 400, {"error": "invalid days or balance"}

    if symbol not in _BT_ALLOWED_SYMBOLS:
        return 400, {"error": f"symbol not allowed: {symbol}. Valid: {sorted(_BT_ALLOWED_SYMBOLS)}"}
    if days < 1 or days > _BT_MAX_DAYS:
        return 400, {"error": f"days must be 1-{_BT_MAX_DAYS}"}
    if balance < 100 or balance > 10_000_000:
        return 400, {"error": "balance must be 100-10000000"}

    # Validate param_overrides numeric ranges
    param_overrides_raw = req.get("param_overrides") or {}
    if isinstance(param_overrides_raw, dict):
        for k, v in param_overrides_raw.items():
            if not isinstance(v, (int, float)):
                return 400, {"error": f"param_overrides.{k} must be numeric"}

    # Optional overrides from param panel
    strategy_params = req.get("strategy_params") or None
    param_overrides = req.get("param_overrides") or None
    allowed_modes = req.get("allowed_modes") or None
    mode_confirmation = req.get("mode_confirmation") or None
    if mode_confirmation is not None:
        mode_confirmation = int(mode_confirmation)

    job_id = f"{symbol}_{days}d_{int(time.time())}"
    with _bt_lock:
        _evict_old_jobs()
        _bt_jobs[job_id] = {"status": "running", "symbol": symbol, "days": days,
                            "result": None, "error": None}

    def _on_done(fut):
        with _bt_lock:
            try:
                result = fut.result()
                _bt_jobs[job_id]["result"] = result
                _bt_jobs[job_id]["status"] = "done"
                # Auto-save trades to JSONL for later loading
                _save_bt_result(symbol, days, result.get("trades", []))
            except Exception as e:
                _bt_jobs[job_id]["error"] = str(e)
                _bt_jobs[job_id]["status"] = "error"
                return
        # Save metadata sidecar outside lock (I/O bound)
        stats = {k: result.get(k) for k in (
            "return_pct", "win_rate", "profit_factor",
            "max_drawdown_pct", "total_trades", "sharpe_ratio",
            "sortino_ratio", "calmar_ratio", "var_95", "cvar_95",
            "recovery_factor", "payoff_ratio",
            "expectancy", "sqn", "sqn_grade", "alpha",
            "buyhold_return", "exposure_pct",
            "kelly_pct", "cagr_pct",
            "monthly_returns",
            "monte_carlo", "oos_validation", "noise_mc",
        ) if result.get(k) is not None}
        _save_bt_metadata(symbol, days, balance,
                          strategy_params=strategy_params,
                          param_overrides=param_overrides,
                          allowed_modes=allowed_modes,
                          mode_confirmation=mode_confirmation,
                          stats=stats)

    plat = "aster" if symbol in _BT_ASTER_SYMBOLS else "binance"
    fut = _get_bt_pool().submit(
        _run_bt_worker, symbol, days, balance,
        strategy_params=strategy_params,
        param_overrides=param_overrides,
        allowed_modes=allowed_modes,
        mode_confirmation=mode_confirmation,
        platform=plat,
    )
    fut.add_done_callback(_on_done)
    return 200, {"job_id": job_id, "status": "running"}


def handle_bt_status(qs: dict):
    """Check backtest job status."""
    job_id = qs.get("job_id", [""])[0]
    if not job_id:
        with _bt_lock:
            return 200, {k: {"status": v["status"], "symbol": v["symbol"],
                              "days": v["days"]}
                         for k, v in _bt_jobs.items()}
    with _bt_lock:
        job = _bt_jobs.get(job_id)
    if not job:
        return 404, {"error": "job not found"}
    resp = {"job_id": job_id, "status": job["status"],
            "symbol": job["symbol"], "days": job["days"]}
    if job["status"] == "done":
        resp["result"] = job["result"]
    elif job["status"] == "error":
        resp["error"] = job["error"]
    return 200, resp


def handle_bt_export(qs: dict):
    """Export a complete backtest report as a single JSON file.
    Accepts ?file=exact_filename or ?symbol=X&days=N (legacy)."""
    file_param = qs.get("file", [""])[0]
    if file_param:
        if not file_param.endswith("_trades.jsonl") or "/" in file_param:
            return 400, {"error": "invalid file parameter"}
        fname = file_param
        # Extract symbol/days from filename for report metadata
        m = re.match(r'bt_([A-Z]+)_(\d+)d(?:_v\d+)?_trades\.jsonl', fname)
        if not m:
            return 400, {"error": "cannot parse filename"}
        symbol, days = m.group(1), int(m.group(2))
    else:
        symbol = qs.get("symbol", [""])[0].upper()
        days_str = qs.get("days", [""])[0]
        if not symbol or not days_str:
            return 400, {"error": "symbol and days (or file) required"}
        try:
            days = int(days_str)
        except ValueError:
            return 400, {"error": "invalid days"}
        fname = f"bt_{symbol}_{days}d_trades.jsonl"

    # Read trades from JSONL
    fpath = os.path.join(BT_DATA_DIR, fname)
    if not os.path.isfile(fpath):
        return 404, {"error": f"No saved result for {fname}"}
    trades = []
    with open(fpath, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))

    # Read meta sidecar if available (matches trades filename base)
    meta_path = os.path.join(BT_DATA_DIR, fname.replace("_trades.jsonl", "_meta.json"))
    meta = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    report = {
        "format_version": _REPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": "AXC BacktestEngine",
        "config": {
            "symbol": symbol,
            "days": days,
            "balance": meta.get("balance", 10000),
            "interval": "1h",
            "strategy_params": meta.get("strategy_params", {}),
            "param_overrides": meta.get("param_overrides", {}),
        },
        "stats": meta.get("stats", {}),
        "trades": trades,
    }

    # Also save a copy to exports folder for local reference
    export_dir = os.path.join(BT_DATA_DIR, "exports")
    os.makedirs(export_dir, exist_ok=True)
    ts = datetime.now(HKT).strftime("%Y%m%d_%H%M%S")
    export_path = os.path.join(export_dir, f"{symbol}_{days}d_{ts}.json")
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode='w', dir=export_dir, delete=False, suffix='.tmp')
        json.dump(report, tmp, ensure_ascii=False)
        tmp.close()
        os.replace(tmp.name, export_path)
        logging.info("Exported report → %s", export_path)
    except OSError as e:
        logging.warning("Failed to save export copy: %s", e)

    return 200, report


def handle_bt_import(body: str):
    """Import a backtest report JSON and save as JSONL + meta for dashboard viewing."""
    try:
        report = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON"}

    # Validate minimum required fields
    trades = report.get("trades")
    if not trades or not isinstance(trades, list):
        return 400, {"error": "trades array is required and must not be empty"}

    config = report.get("config", {})
    symbol = config.get("symbol", "").upper()
    if not symbol:
        return 400, {"error": "config.symbol is required"}

    # Validate each trade has minimum fields
    required_trade_fields = {"side", "entry", "exit"}
    for i, t in enumerate(trades):
        missing = required_trade_fields - set(t.keys())
        if missing:
            return 400, {"error": f"trade[{i}] missing fields: {missing}"}

    # Normalize trades: ensure pnl exists.
    # NOTE: fallback PnL = price diff only (ignores qty/position size).
    # External engines should include their own pnl field for accuracy.
    for t in trades:
        if "pnl" not in t:
            entry = float(t["entry"])
            exit_p = float(t["exit"])
            mult = -1 if t["side"].upper() == "SHORT" else 1
            t["pnl"] = round((exit_p - entry) * mult, 2)
        # Normalize time field
        if "entry_time" not in t and "ts" in t:
            t["entry_time"] = t["ts"]

    # Determine days from config or trade time range
    days = config.get("days", 0)
    if not days and len(trades) >= 2:
        first_ts = trades[0].get("entry_time") or trades[0].get("ts", "")
        last_ts = trades[-1].get("entry_time") or trades[-1].get("ts", "")
        if first_ts and last_ts:
            try:
                # stdlib fromisoformat handles "2026-03-01T08:00:00" and "2026-03-01 08:00:00"
                d0 = datetime.fromisoformat(first_ts)
                d1 = datetime.fromisoformat(last_ts)
                days = max(1, (d1 - d0).days)
            except (ValueError, TypeError) as e:
                logging.warning("Could not parse trade timestamps for days estimate: %s", e)
                days = len(trades)
    if not days:
        days = len(trades)

    # Use standard {days}d naming so loadExisting + export can find it.
    # To avoid overwriting native results, check if file exists and append
    # a numeric suffix: bt_BTCUSDT_30d_trades.jsonl → bt_BTCUSDT_30d_v2_trades.jsonl
    base = f"bt_{symbol}_{days}d"
    fname = f"{base}_trades.jsonl"
    fpath = os.path.join(BT_DATA_DIR, fname)
    suffix = 1
    while os.path.isfile(fpath) and suffix < 100:
        suffix += 1
        fname = f"{base}_v{suffix}_trades.jsonl"
        fpath = os.path.join(BT_DATA_DIR, fname)
    if suffix >= 100:
        return 400, {"error": f"Too many imports for {symbol} {days}d (max 100)"}

    os.makedirs(BT_DATA_DIR, exist_ok=True)
    tmp = fpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for t in trades:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    os.replace(tmp, fpath)

    # Save meta sidecar (same base name)
    stats = report.get("stats", {})
    meta = {
        "symbol": symbol,
        "days": days,
        "balance": config.get("balance", 10000),
        "strategy_params": config.get("strategy_params", {}),
        "param_overrides": config.get("param_overrides", {}),
        "stats": stats,
        "source": report.get("source", "external"),
        "created_at": datetime.now(HKT).isoformat(),
    }
    meta_fname = fname.replace("_trades.jsonl", "_meta.json")
    meta_path = os.path.join(BT_DATA_DIR, meta_fname)
    tmp_m = tempfile.NamedTemporaryFile(
        mode='w', dir=BT_DATA_DIR, delete=False, suffix='.tmp')
    json.dump(meta, tmp_m, ensure_ascii=False)
    tmp_m.close()
    os.replace(tmp_m.name, meta_path)

    # Save original imported JSON to exports folder
    export_dir = os.path.join(BT_DATA_DIR, "exports")
    os.makedirs(export_dir, exist_ok=True)
    ts_tag = datetime.now(HKT).strftime("%Y%m%d_%H%M%S")
    import_copy = os.path.join(export_dir, f"imported_{symbol}_{days}d_{ts_tag}.json")
    try:
        with open(import_copy, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
        logging.info("Imported report saved → %s", import_copy)
    except OSError as e:
        logging.warning("Failed to save import copy: %s", e)

    return 200, {
        "status": "imported",
        "file": fname,
        "days": days,
        "trades": len(trades),
        "symbol": symbol,
    }


# ── NFS+FVZ Research Backtest ─────────────────────────────────────────

_NFS_FVZ_DEFAULTS = {
    "swing_lookback": 1, "nfs_max_gap": 20, "fvz_entry": "mid",
    "stop_mode": "swing", "min_rr": 4.0, "zone_expiry": 20,
    "regime_filter": "adx>25", "min_zone_width_pct": 0.001,
}
_NFS_FVZ_NUMERIC = {"swing_lookback", "nfs_max_gap", "min_rr", "zone_expiry", "min_zone_width_pct"}
_NFS_FVZ_STRING = {"fvz_entry": {"upper", "mid", "lower"},
                   "stop_mode": {"swing", "atr", "hybrid"},
                   "regime_filter": {"none", "adx>20", "adx>25"}}


def handle_bt_nfs_fvz(body: str):
    """Run NFS+FVZ research backtest via dashboard."""
    try:
        req = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON"}

    symbol = req.get("symbol", "BTCUSDT").upper()
    if symbol not in _BT_ALLOWED_SYMBOLS:
        return 400, {"error": f"symbol not allowed: {symbol}"}

    try:
        balance = float(req.get("balance", 10000))
    except (ValueError, TypeError):
        return 400, {"error": "invalid balance"}

    # Merge params with defaults
    params = dict(_NFS_FVZ_DEFAULTS)
    user_params = req.get("nfs_fvz_params") or {}
    for k, v in user_params.items():
        if k in _NFS_FVZ_NUMERIC:
            try:
                params[k] = float(v)
                if k in ("swing_lookback", "nfs_max_gap", "zone_expiry"):
                    params[k] = int(v)
            except (ValueError, TypeError):
                return 400, {"error": f"invalid numeric param: {k}"}
        elif k in _NFS_FVZ_STRING:
            if v not in _NFS_FVZ_STRING[k]:
                return 400, {"error": f"invalid {k}: {v}. Valid: {_NFS_FVZ_STRING[k]}"}
            params[k] = v

    # Find CSV
    from backtest.research_nfs_fvz import find_longest_csv, run_single
    csv_path = find_longest_csv(symbol, "4h")
    if not csv_path:
        return 400, {"error": f"No 4H CSV data for {symbol}"}

    # Run synchronously (fast enough for single pair, <2s)
    job_id = f"nfs_{symbol}_{int(time.time())}"
    with _bt_lock:
        _evict_old_jobs()
        _bt_jobs[job_id] = {"status": "running", "symbol": symbol, "days": 0,
                            "result": None, "error": None}

    def _on_done(fut):
        with _bt_lock:
            try:
                result = fut.result()
                _bt_jobs[job_id]["result"] = result
                _bt_jobs[job_id]["status"] = "done"
            except Exception as e:
                _bt_jobs[job_id]["error"] = str(e)
                _bt_jobs[job_id]["status"] = "error"

    fut = _get_bt_pool().submit(run_single, symbol, csv_path, params, balance)
    fut.add_done_callback(_on_done)
    return 200, {"job_id": job_id, "status": "running"}


# ── AggTrades ────────────────────────────────────────────────────────

def _cleanup_old_jobs():
    """Remove completed/errored jobs older than TTL."""
    now = time.monotonic()
    expired = [jid for jid, j in _aggtrades_jobs.items()
               if j["status"] != "running" and (now - j["started"]) > _AGGTRADES_JOB_TTL]
    for jid in expired:
        del _aggtrades_jobs[jid]


def _validate_aggtrades_params(qs: dict):
    """Parse and validate aggTrades query params. Returns dict or (status, error) tuple."""
    symbol = qs.get("symbol", [""])[0].upper()
    if not symbol:
        return 400, {"error": "symbol required"}
    if symbol in _AGGTRADES_UNSUPPORTED:
        return 400, {"error": f"{symbol} 係 Aster DEX 幣種，冇公開 aggTrades API。Order Flow 只適用於 Binance 幣種。"}
    if symbol not in _BT_ALLOWED_SYMBOLS:
        return 400, {"error": f"symbol not allowed: {symbol}. Valid: {sorted(_BT_ALLOWED_SYMBOLS)}"}

    try:
        days = min(int(qs.get("days", ["7"])[0]), 14)
    except ValueError:
        return 400, {"error": "invalid days"}
    if days < 1:
        return 400, {"error": "days must be >= 1"}
    if symbol in _HIGH_VOL_MAX_DAYS:
        days = min(days, _HIGH_VOL_MAX_DAYS[symbol])

    interval = qs.get("interval", ["1h"])[0].lower()
    if interval not in _INTERVAL_MS:
        return 400, {"error": f"invalid interval: {interval}"}

    features_str = qs.get("features", ["delta,large,profile,heatmap"])[0]
    features = set(f.strip() for f in features_str.split(","))

    _MIN_BUCKET_INTERVAL = 900_000
    if _INTERVAL_MS[interval] < _MIN_BUCKET_INTERVAL:
        if "delta" in features or "heatmap" in features or "cvd" in features:
            interval = "15m"

    try:
        threshold = float(qs.get("threshold", ["100000"])[0])
    except ValueError:
        threshold = 100_000

    from backtest.fetch_agg_trades import AGG_BUCKET_DEFAULTS
    try:
        bucket_str = qs.get("bucket_size", [""])[0]
        bucket_size = float(bucket_str) if bucket_str else AGG_BUCKET_DEFAULTS.get(symbol, 50)
    except ValueError:
        bucket_size = AGG_BUCKET_DEFAULTS.get(symbol, 50)

    return {
        "symbol": symbol, "days": days, "interval": interval,
        "features": features, "threshold": threshold, "bucket_size": bucket_size,
    }


def _handle_bt_aggtrades_inner(params: dict):
    """Execute aggTrades fetch + aggregation. Runs in background thread."""
    symbol = params["symbol"]
    days = params["days"]
    interval = params["interval"]
    features = params["features"]
    threshold = params["threshold"]
    bucket_size = params["bucket_size"]

    from backtest.fetch_agg_trades import (
        fetch_agg_trades_range,
        aggregate_delta_volume,
        aggregate_large_trades,
        aggregate_volume_profile,
        aggregate_footprint_heatmap,
        aggregate_cvd,
    )

    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    interval_ms = _INTERVAL_MS[interval]
    start_ms = (start_ms // interval_ms) * interval_ms

    trades_df = fetch_agg_trades_range(symbol, start_ms, end_ms)
    candle_ts = list(range(start_ms, end_ms, interval_ms))

    result = {"symbol": symbol, "days": days, "interval": interval}
    errors = []

    if "delta" in features:
        try:
            result["delta_volume"] = aggregate_delta_volume(trades_df, candle_ts, interval_ms)
        except Exception as e:
            logging.warning("aggregate_delta_volume failed: %s", e)
            errors.append(f"delta: {e}")
            result["delta_volume"] = {}
    if "large" in features:
        try:
            result["large_trades"] = aggregate_large_trades(trades_df, threshold)
        except Exception as e:
            logging.warning("aggregate_large_trades failed: %s", e)
            errors.append(f"large: {e}")
            result["large_trades"] = []
    if "profile" in features:
        try:
            result["volume_profile"] = aggregate_volume_profile(trades_df, bucket_size)
        except Exception as e:
            logging.warning("aggregate_volume_profile failed: %s", e)
            errors.append(f"profile: {e}")
            result["volume_profile"] = []
    if "heatmap" in features:
        try:
            result["heatmap"] = aggregate_footprint_heatmap(
                trades_df, candle_ts, interval_ms, bucket_size
            )
        except Exception as e:
            logging.warning("aggregate_footprint_heatmap failed: %s", e)
            errors.append(f"heatmap: {e}")
            result["heatmap"] = {}
    if "cvd" in features:
        try:
            result["cvd"] = aggregate_cvd(trades_df, candle_ts, interval_ms)
        except Exception as e:
            logging.warning("aggregate_cvd failed: %s", e)
            errors.append(f"cvd: {e}")
            result["cvd"] = {}

    if errors:
        result["warnings"] = errors

    return 200, result


def handle_bt_aggtrades(qs: dict):
    """Start aggTrades fetch as background job, return job_id immediately (202).

    設計決定：aggTrades fetch 要 2-4 分鐘（SOL 410K trades/day），
    同步 HTTP 會 timeout。改用 background job + polling 模式。
    """
    _cleanup_old_jobs()

    # Check for running job — only 1 concurrent fetch allowed (Binance rate limit)
    for jid, job in _aggtrades_jobs.items():
        if job["status"] == "running":
            elapsed = int(time.monotonic() - job["started"])
            # Auto-expire stuck jobs (>5 min)
            if elapsed > 300:
                logging.warning("aggTrades job %s stuck (>300s), marking error", jid)
                job["status"] = "error"
                job["result"] = {"error": "fetch timed out after 300s"}
                continue
            return 429, {"error": f"aggTrades fetch already in progress ({elapsed}s elapsed)", "job_id": jid}

    # Validate params before spawning thread
    validated = _validate_aggtrades_params(qs)
    if isinstance(validated, tuple):
        return validated  # (status_code, error_dict)

    job_id = f"agg_{int(time.time())}_{id(qs) % 10000}"
    _aggtrades_jobs[job_id] = {"status": "running", "result": None, "started": time.monotonic()}

    def _run():
        try:
            _, result = _handle_bt_aggtrades_inner(validated)
            _aggtrades_jobs[job_id]["status"] = "done"
            _aggtrades_jobs[job_id]["result"] = result
        except Exception as e:
            logging.exception("aggTrades job %s failed", job_id)
            _aggtrades_jobs[job_id]["status"] = "error"
            _aggtrades_jobs[job_id]["result"] = {"error": str(e)}

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return 202, {"job_id": job_id, "status": "running", "message": "aggTrades fetch started"}


def handle_bt_aggtrades_status(qs: dict):
    """Poll for aggTrades job completion."""
    job_id = qs.get("job_id", [""])[0]
    if not job_id or job_id not in _aggtrades_jobs:
        return 404, {"error": "job not found"}
    job = _aggtrades_jobs[job_id]
    elapsed = int(time.monotonic() - job["started"])
    if job["status"] == "running":
        return 200, {"job_id": job_id, "status": "running", "elapsed": elapsed}
    elif job["status"] == "done":
        result = job["result"].copy()
        result["job_id"] = job_id
        result["elapsed"] = elapsed
        result["status"] = "done"
        return 200, result
    else:
        return 500, {"job_id": job_id, "status": "error", "error": job["result"].get("error", "unknown"), "elapsed": elapsed}


# ── Shootout (Parameter Comparison) ──────────────────────────────

def handle_bt_shootout_list():
    """List all saved shootout JSON files with summary stats."""
    results = []
    if not os.path.isdir(BT_DATA_DIR):
        return results
    for fname in sorted(os.listdir(BT_DATA_DIR), reverse=True):
        if not fname.startswith("shootout_") or not fname.endswith(".json"):
            continue
        fpath = os.path.join(BT_DATA_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            configs = list(data.keys())
            pairs = [k for k in data[configs[0]].keys() if k != "_meta"] if configs else []
            # Compute total PnL per config (skip _meta key)
            ranking = []
            for cfg_name, cfg_data in data.items():
                total = sum(v.get("pnl", 0) for k, v in cfg_data.items() if k != "_meta")
                ranking.append({"config": cfg_name, "pnl": round(total, 2)})
            ranking.sort(key=lambda x: x["pnl"], reverse=True)
            # Parse timestamp from filename: shootout_YYYYMMDD_HHMM.json
            ts_str = fname.replace("shootout_", "").replace(".json", "")
            try:
                ts = datetime.strptime(ts_str, "%Y%m%d_%H%M")
                created = ts.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                created = ts_str
            results.append({
                "file": fname,
                "created": created,
                "configs": configs,
                "pairs": pairs,
                "ranking": ranking,
            })
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return results


def handle_bt_shootout_detail(qs: dict):
    """Load a specific shootout file with full detail."""
    fname = qs.get("file", [""])[0]
    if not fname or not fname.startswith("shootout_") or not fname.endswith(".json"):
        return 400, {"error": "invalid file parameter"}
    fpath = os.path.join(BT_DATA_DIR, fname)
    if not os.path.isfile(fpath):
        return 404, {"error": "file not found"}
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        # Extract _meta per config and compute totals (skip _meta key in pair iteration)
        summary = {}
        params_dict = {}
        for cfg_name, cfg_data in data.items():
            if "_meta" in cfg_data:
                params_dict[cfg_name] = cfg_data["_meta"]
            total_pnl = 0
            total_trades = 0
            total_wins = 0
            total_losses = 0
            for pair_key, pair_data in cfg_data.items():
                if pair_key == "_meta":
                    continue
                total_pnl += pair_data.get("pnl", 0)
                total_trades += pair_data.get("trades", 0)
                total_wins += pair_data.get("wins", 0)
                total_losses += pair_data.get("losses", 0)
            wr = round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0
            summary[cfg_name] = {
                "total_pnl": round(total_pnl, 2),
                "total_trades": total_trades,
                "total_wins": total_wins,
                "total_losses": total_losses,
                "win_rate": wr,
            }
        return 200, {"file": fname, "data": data, "summary": summary, "params": params_dict}
    except (OSError, json.JSONDecodeError) as e:
        return 500, {"error": str(e)}
