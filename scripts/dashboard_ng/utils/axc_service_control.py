"""AXC service control — start/stop/status for LaunchAgent services.

Uses launchctl bootstrap/bootout (macOS LaunchAgent system).
Separate from poly_bot_control.py which uses nohup for Polymarket bots.
"""

import logging
import os
import subprocess

log = logging.getLogger('axc.service_control')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
PLIST_DIR = os.path.expanduser('~/Library/LaunchAgents')

# AXC services controllable from dashboard
SERVICE_DEFS = {
    "tradercycle": {
        "label": "ai.openclaw.tradercycle",
        "display": "Trader Cycle",
        "description": "15-min trading pipeline (--live)",
    },
    "scanner": {
        "label": "ai.openclaw.scanner",
        "display": "Scanner",
        "description": "9-exchange rotation scanner",
    },
    "indicatorengine": {
        "label": "ai.openclaw.indicatorengine",
        "display": "Indicator Engine",
        "description": "WS → Redis → indicator cache",
    },
    "wsmanager": {
        "label": "ai.openclaw.wsmanager",
        "display": "WS Manager",
        "description": "Binance Futures WebSocket",
    },
    "telegram": {
        "label": "ai.openclaw.telegram",
        "display": "Telegram Bot",
        "description": "@AXCTradingBot",
    },
}


def _get_uid() -> str:
    """Get current user ID for launchctl gui/ domain."""
    return str(os.getuid())


def get_service_status(label: str) -> dict:
    """Get service status via launchctl list.

    Returns: {"running": bool, "pid": int|None, "exit_code": int|None}
    """
    try:
        result = subprocess.run(
            ['launchctl', 'list', label],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            # Service not loaded
            return {"running": False, "pid": None, "exit_code": None, "loaded": False}

        # Parse output: "PID" = ... or "-" if not running
        lines = result.stdout.strip().split('\n')
        info = {}
        for line in lines:
            if '=' in line:
                parts = line.strip().strip(';').split('=', 1)
                if len(parts) == 2:
                    k = parts[0].strip().strip('"')
                    v = parts[1].strip().strip('"').strip(';')
                    info[k] = v

        pid_str = info.get('PID', info.get('pid'))
        pid = int(pid_str) if pid_str and pid_str != '0' else None
        exit_code = int(info.get('LastExitStatus', '0')) or None

        return {
            "running": pid is not None,
            "pid": pid,
            "exit_code": exit_code,
            "loaded": True,
        }
    except Exception as e:
        log.error("Failed to get status for %s: %s", label, e)
        return {"running": False, "pid": None, "exit_code": None, "loaded": False}


def get_all_status() -> dict[str, dict]:
    """Get status of all AXC services."""
    result = {}
    for key, svc in SERVICE_DEFS.items():
        status = get_service_status(svc["label"])
        status["display"] = svc["display"]
        status["description"] = svc["description"]
        result[key] = status
    return result


def start_service(service_key: str) -> bool:
    """Start a LaunchAgent service via launchctl bootstrap."""
    svc = SERVICE_DEFS.get(service_key)
    if not svc:
        log.error("Unknown service: %s", service_key)
        return False

    label = svc["label"]
    plist = os.path.join(PLIST_DIR, f"{label}.plist")
    if not os.path.isfile(plist):
        log.error("Plist not found: %s", plist)
        return False

    # Check if already loaded — bootout first, then wait for cleanup
    status = get_service_status(label)
    if status.get("loaded"):
        log.info("Service %s already loaded, bootout first", label)
        stop_service(service_key)
        delay = _RESTART_DELAY.get(service_key, _DEFAULT_RESTART_DELAY)
        time.sleep(delay)

    uid = _get_uid()
    try:
        result = subprocess.run(
            ['launchctl', 'bootstrap', f'gui/{uid}', plist],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            log.info("Started service: %s", label)
            return True
        else:
            log.error("Failed to start %s: %s", label, result.stderr.strip())
            return False
    except Exception as e:
        log.error("Exception starting %s: %s", label, e)
        return False


def stop_service(service_key: str) -> bool:
    """Stop a LaunchAgent service via launchctl bootout."""
    svc = SERVICE_DEFS.get(service_key)
    if not svc:
        log.error("Unknown service: %s", service_key)
        return False

    label = svc["label"]
    uid = _get_uid()
    try:
        result = subprocess.run(
            ['launchctl', 'bootout', f'gui/{uid}/{label}'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            log.info("Stopped service: %s", label)
            return True
        else:
            # May already be stopped
            log.warning("bootout %s: %s", label, result.stderr.strip())
            return True  # Not an error if already stopped
    except Exception as e:
        log.error("Exception stopping %s: %s", label, e)
        return False


import time

# Telegram needs longer cooldown to fully release long-polling connection (gotcha: 409 Conflict)
_RESTART_DELAY = {"telegram": 4}
_DEFAULT_RESTART_DELAY = 1


def restart_service(service_key: str) -> bool:
    """Stop then start a service. Telegram gets extra delay to avoid 409."""
    stop_service(service_key)
    delay = _RESTART_DELAY.get(service_key, _DEFAULT_RESTART_DELAY)
    time.sleep(delay)
    return start_service(service_key)


def run_trader_cycle_once(dry_run: bool = True) -> str:
    """Run a single trader_cycle and return output.

    dry_run=True → --dry-run (safe, no real orders)
    dry_run=False → --live (real orders, use with caution)
    """
    cmd = ['python3', os.path.join(AXC_HOME, 'scripts', 'trader_cycle', 'main.py')]
    if dry_run:
        cmd.append('--dry-run')
    else:
        cmd.append('--live')
    cmd.append('--verbose')

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            cwd=AXC_HOME,
        )
        output = result.stdout + result.stderr
        # Truncate if too long (for UI display)
        if len(output) > 5000:
            output = output[:2500] + '\n... (truncated) ...\n' + output[-2500:]
        return output
    except subprocess.TimeoutExpired:
        # subprocess.run auto-kills child on timeout (Python 3.9+)
        # fcntl.flock released when process dies — no orphan risk
        return "ERROR: Trader cycle timed out after 120 seconds (process killed)"
    except Exception as e:
        return f"ERROR: {e}"
