"""paper_trading.py — Dry-run control."""

import logging
import re
import subprocess
import sys
import threading

from scripts.dashboard.constants import (
    HOME, SCRIPTS_DIR,
    TRADE_LOG_PATH, DRYRUN_LOG_PATH, LOAD_ENV_SH, MAIN_PY,
    HAS_PSUTIL,
)

# Conditional import — psutil is optional
if HAS_PSUTIL:
    import psutil

_dryrun_proc = None  # subprocess.Popen handle
_dryrun_lock = threading.Lock()


def _detect_tradercycle_mode():
    """Detect running tradercycle mode via psutil process scan.
    Returns (mode, pid): mode = 'live' | 'dry_run' | 'stopped'
    """
    if not HAS_PSUTIL:
        return "stopped", None
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            joined = " ".join(cmdline)
            if "trader_cycle/main.py" not in joined and "trader_cycle\\main.py" not in joined:
                continue
            if "--live" in cmdline:
                return "live", proc.info["pid"]
            return "dry_run", proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return "stopped", None


def _parse_dryrun_entries(limit=20):
    """Parse [DRY_RUN] entries from TRADE_LOG.md, return most recent `limit`."""
    entries = []
    try:
        with open(TRADE_LOG_PATH, "r") as f:
            for line in f:
                if "[DRY_RUN]" not in line:
                    continue
                # format: [2026-03-11 22:20] [DRY_RUN] ENTRY LONG SOLUSDT qty=1.0 @ 86.43 SL=84.42 TP=92.45 leverage=8x margin=$13.02
                m = re.match(
                    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] \[DRY_RUN\] "
                    r"(ENTRY|EXIT)\s+(LONG|SHORT)\s+(\S+)\s+"
                    r"(?:qty=(\S+)\s+@\s+(\S+))?"
                    r"(?:\s+SL=(\S+))?"
                    r"(?:\s+TP\d?=(\S+))?"
                    r"(?:\s+leverage=(\S+))?"
                    r"(?:\s+margin=\$?(\S+))?",
                    line.strip()
                )
                if m:
                    entries.append({
                        "time": m.group(1),
                        "action": m.group(2),
                        "direction": m.group(3),
                        "pair": m.group(4),
                        "qty": m.group(5) or "",
                        "price": m.group(6) or "",
                        "sl": m.group(7) or "",
                        "tp": m.group(8) or "",
                        "leverage": m.group(9) or "",
                        "margin": m.group(10) or "",
                    })
    except FileNotFoundError:
        pass
    return entries[-limit:]


def handle_paper_trading_status():
    """GET /api/paper-trading — status + recent dry-run entries."""
    mode, pid = _detect_tradercycle_mode()
    entries = _parse_dryrun_entries(20)
    return 200, {
        "mode": mode,
        "pid": pid,
        "entries": entries,
    }


def handle_paper_trading_start():
    """POST /api/paper-trading/start — launch one dry-run cycle."""
    global _dryrun_proc
    with _dryrun_lock:
        # Check if already running
        if _dryrun_proc and _dryrun_proc.poll() is None:
            return 409, {"error": "Dry-run 已在執行中", "pid": _dryrun_proc.pid}

        # Check live cycle
        mode, pid = _detect_tradercycle_mode()
        if mode == "live":
            return 409, {"error": "Live tradercycle 正在運行，無法同時執行 dry-run", "pid": pid}
        if mode == "dry_run":
            return 409, {"error": "Dry-run 已在執行中", "pid": pid}

        # Launch: bash load_env.sh python3 main.py --dry-run --verbose
        try:
            log_f = open(DRYRUN_LOG_PATH, "a")
            _dryrun_proc = subprocess.Popen(
                ["/bin/bash", LOAD_ENV_SH, sys.executable, MAIN_PY, "--dry-run", "--verbose"],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=HOME,
            )
            logging.info("Paper trading started: PID %d", _dryrun_proc.pid)
            return 200, {"ok": True, "pid": _dryrun_proc.pid}
        except Exception as e:
            logging.exception("Failed to start paper trading")
            return 500, {"error": str(e)}


def handle_paper_trading_stop():
    """POST /api/paper-trading/stop — terminate running dry-run."""
    global _dryrun_proc
    with _dryrun_lock:
        # First try our tracked subprocess
        if _dryrun_proc and _dryrun_proc.poll() is None:
            _dryrun_proc.terminate()
            try:
                _dryrun_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _dryrun_proc.kill()
            pid = _dryrun_proc.pid
            _dryrun_proc = None
            logging.info("Paper trading stopped: PID %d", pid)
            return 200, {"ok": True, "stopped_pid": pid}

        # Fallback: find via psutil
        mode, pid = _detect_tradercycle_mode()
        if mode == "dry_run" and pid and HAS_PSUTIL:
            try:
                p = psutil.Process(pid)
                p.terminate()
                p.wait(timeout=5)
                logging.info("Paper trading stopped (psutil): PID %d", pid)
                return 200, {"ok": True, "stopped_pid": pid}
            except Exception as e:
                return 500, {"error": f"無法停止 PID {pid}: {e}"}

        _dryrun_proc = None
        return 404, {"error": "沒有運行中嘅 dry-run process"}
