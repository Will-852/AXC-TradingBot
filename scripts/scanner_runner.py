#!/usr/bin/env python3
"""
scanner_runner.py — Orchestrator: light_scan → trader_cycle
Replaces two separate LaunchAgents with one coordinated flow.

Flow:
1. Acquire process lock (prevent double execution)
2. Run light_scan.py → JSON result
3. If trigger found (exit 1) → run trader_cycle/main.py
4. If trader_cycle finds signal (exit 1) → send Telegram alert
5. Write result to shared/SIGNAL.md
6. Release lock

Usage:
  python3 scanner_runner.py              # dry-run (default)
  python3 scanner_runner.py --live       # live trading
  python3 scanner_runner.py --verbose    # verbose output

Exit codes: 0 = no trigger, 1 = signal found, 2 = error
"""

import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

# ─── Config ───
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/Users/wai/.openclaw/workspace")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = "/opt/homebrew/bin/python3.11"

LIGHT_SCAN = os.path.join(SCRIPTS_DIR, "light_scan.py")
TRADER_CYCLE = os.path.join(SCRIPTS_DIR, "trader_cycle", "main.py")
_AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
SIGNAL_MD = os.path.join(_AXC_HOME, "shared", "SIGNAL.md")
LOCK_FILE = os.path.join(_AXC_HOME, "shared", "scanner_runner.lock")

HKT = timezone(timedelta(hours=8))

# Telegram (reuse from light_scan)
sys.path.insert(0, SCRIPTS_DIR)
from light_scan import send_telegram


def now_str():
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M")


# ─── Process Lock ───

class ProcessLock:
    """File-based lock to prevent double execution."""

    def __init__(self, path):
        self.path = path
        self.fp = None

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fp = open(self.path, "w")
        try:
            fcntl.flock(self.fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.write(f"{os.getpid()}\n{now_str()}\n")
            self.fp.flush()
            return True
        except BlockingIOError:
            self.fp.close()
            self.fp = None
            return False

    def release(self):
        if self.fp:
            try:
                fcntl.flock(self.fp, fcntl.LOCK_UN)
                self.fp.close()
            except Exception:
                pass
            self.fp = None
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass


# ─── SIGNAL.md Writer ───

def write_signal_md(data: dict):
    """Write signal state to shared/SIGNAL.md."""
    os.makedirs(os.path.dirname(SIGNAL_MD), exist_ok=True)
    lines = [
        "# SIGNAL.md — Cross-Agent Signal Communication",
        f"# Updated: {now_str()} UTC+8",
        "",
        "## 當前信號",
        "",
        f"SIGNAL_ACTIVE: {data.get('active', 'NO')}",
        f"PAIR: {data.get('pair', '—')}",
        f"DIRECTION: {data.get('direction', '—')}",
        f"STRATEGY: {data.get('strategy', '—')}",
        f"STRENGTH: {data.get('strength', '—')}",
        f"SCORE: {data.get('score', 0)}",
        f"ENTRY_PRICE: {data.get('entry', 0)}",
        f"TIMESTAMP: {data.get('timestamp', '—')}",
        f"REASONS: {data.get('reasons', '—')}",
        "",
        f"## Last Scan",
        "",
        f"TRIGGER_COUNT: {data.get('trigger_count', 0)}",
        f"SCAN_STATUS: {data.get('scan_status', '—')}",
        f"CYCLE_STATUS: {data.get('cycle_status', '—')}",
    ]
    with open(SIGNAL_MD, "w") as f:
        f.write("\n".join(lines) + "\n")


# ─── Main ───

def run(live=False, verbose=False):
    ts = now_str()

    # ── Step 1: Run light_scan ──
    if verbose:
        print(f"[{ts}] Running light_scan.py...")

    try:
        scan_result = subprocess.run(
            [PYTHON, LIGHT_SCAN],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "OPENCLAW_WORKSPACE": WORKSPACE},
        )
    except subprocess.TimeoutExpired:
        print(f"[{ts}] ERROR: light_scan.py timed out (30s)")
        write_signal_md({"scan_status": "TIMEOUT", "timestamp": ts})
        return 2

    scan_exit = scan_result.returncode
    scan_stdout = scan_result.stdout.strip()

    # Parse JSON output
    scan_data = {}
    try:
        scan_data = json.loads(scan_stdout)
    except (json.JSONDecodeError, ValueError):
        pass

    trigger_count = scan_data.get("trigger_count", 0)
    triggers = scan_data.get("triggers", [])
    prices = scan_data.get("prices", {})

    if verbose:
        print(f"  Status: {scan_data.get('status', '?')} | "
              f"Triggers: {trigger_count} | Exit: {scan_exit}")
        for t in triggers:
            print(f"    {t['pair']} {t['type']}: {t['reason']}")

    # ── No trigger → done ──
    if scan_exit != 1 or trigger_count == 0:
        if verbose:
            print(f"  No trigger. Done.")
        write_signal_md({
            "scan_status": scan_data.get("status", "ok"),
            "trigger_count": 0,
            "timestamp": ts,
        })
        return 0

    # ── Step 2: Trigger found → run trader_cycle ──
    mode_flag = "--live" if live else "--dry-run"
    cmd = [PYTHON, TRADER_CYCLE, mode_flag]
    if verbose:
        cmd.append("--verbose")

    if verbose:
        mode_str = "LIVE" if live else "DRY-RUN"
        print(f"[{ts}] Trigger detected! Running trader_cycle ({mode_str})...")

    try:
        cycle_result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "OPENCLAW_WORKSPACE": WORKSPACE},
        )
    except subprocess.TimeoutExpired:
        print(f"[{ts}] ERROR: trader_cycle timed out (120s)")
        write_signal_md({
            "scan_status": "ok",
            "cycle_status": "TIMEOUT",
            "trigger_count": trigger_count,
            "timestamp": ts,
        })
        return 2

    cycle_exit = cycle_result.returncode
    cycle_stdout = cycle_result.stdout.strip()

    if verbose:
        # Print trader_cycle verbose output line by line
        for line in cycle_stdout.split("\n"):
            if line.strip().startswith("{"):
                break  # Don't dump the full JSON
            if line.strip():
                print(f"  {line}")

    # Parse trader_cycle JSON — it outputs a JSON object as the last block.
    # The verbose output has text lines before it, so find the last { ... } block.
    cycle_data = {}
    brace_depth = 0
    json_start = -1
    for i, ch in enumerate(cycle_stdout):
        if ch == "{":
            if brace_depth == 0:
                json_start = i
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0 and json_start >= 0:
                # Keep overwriting — we want the LAST complete JSON block
                try:
                    cycle_data = json.loads(cycle_stdout[json_start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    pass
                json_start = -1

    signal_count = cycle_data.get("signals_count", 0)
    selected = cycle_data.get("selected_signal")
    cycle_status = cycle_data.get("status", "unknown")

    if verbose:
        print(f"  Cycle status: {cycle_status} | Signals: {signal_count} | Exit: {cycle_exit}")

    # ── Step 3: Signal found → alert ──
    if cycle_exit == 1 and selected:
        pair = selected.get("pair", "?")
        direction = selected.get("direction", "?")
        strategy = selected.get("strategy", "?")
        strength = selected.get("strength", "?")
        entry = selected.get("entry", 0)

        if verbose:
            print(f"  SIGNAL: {pair} {direction} via {strategy} "
                  f"(strength={strength}, entry={entry})")

        # Write SIGNAL.md
        write_signal_md({
            "active": "YES",
            "pair": pair,
            "direction": direction,
            "strategy": strategy,
            "strength": strength,
            "score": selected.get("score", 0),
            "entry": entry,
            "timestamp": ts,
            "reasons": f"{strategy} {strength}",
            "trigger_count": trigger_count,
            "scan_status": "ok",
            "cycle_status": cycle_status,
        })

        # Send Telegram alert (only in live mode)
        if live:
            mode_str = "LIVE"
        else:
            mode_str = "DRY-RUN"
        alert_msg = (
            f"<b>⚡ SIGNAL DETECTED [{mode_str}]</b>\n"
            f"<pre>"
            f"{pair} {direction} ({strategy})\n"
            f"Strength: {strength}\n"
            f"Entry: ${entry}\n"
            f"SL: ${selected.get('sl', '—')}\n"
            f"TP1: ${selected.get('tp1', '—')}"
            f"</pre>"
        )
        send_telegram(alert_msg)
        if verbose:
            print(f"  Telegram alert sent.")

        return 1

    # ── No signal from trader_cycle ──
    if verbose:
        print(f"  No trading signal. Done.")

    write_signal_md({
        "scan_status": "ok",
        "cycle_status": cycle_status,
        "trigger_count": trigger_count,
        "timestamp": ts,
    })
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scanner Runner — light_scan + trader_cycle")
    parser.add_argument("--live", action="store_true", help="Live trading mode")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry-run (default)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Acquire lock
    lock = ProcessLock(LOCK_FILE)
    if not lock.acquire():
        print(json.dumps({
            "timestamp": now_str(),
            "status": "skipped",
            "reason": "Another scanner_runner is already running",
        }))
        sys.exit(0)

    try:
        exit_code = run(live=args.live, verbose=args.verbose)
        sys.exit(exit_code)
    except Exception as e:
        print(json.dumps({
            "timestamp": now_str(),
            "status": "error",
            "error": str(e),
        }))
        try:
            send_telegram(f"🚨 <b>SCANNER RUNNER ERROR</b>\n{str(e)[:300]}")
        except Exception:
            pass
        sys.exit(2)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
