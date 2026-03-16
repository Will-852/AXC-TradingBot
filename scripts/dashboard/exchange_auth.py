"""exchange_auth.py — Exchange credential CRUD + demo mode detection."""

import json
import logging
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta

from scripts.dashboard.constants import (
    HOME, HKT, SCRIPTS_DIR, SECRETS_ENV_PATH, DEMO_DATA,
)
from scripts.dashboard.exchange_clients import (
    _run_with_timeout, _get_aster_client, _get_hl_client,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _strip_env_value(raw: str) -> str:
    """Strip surrounding quotes and whitespace from .env value."""
    val = raw.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        val = val[1:-1]
    return val


# ── Aster ───────────────────────────────────────────────────────────

def _get_aster_credentials():
    """Read Aster keys from secrets/.env"""
    api_key = api_secret = ""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("ASTER_API_KEY="):
                    api_key = _strip_env_value(line.split("=", 1)[1])
                elif line.startswith("ASTER_API_SECRET="):
                    api_secret = _strip_env_value(line.split("=", 1)[1])
    return api_key, api_secret


def _save_aster_credentials(api_key, api_secret):
    """Write or update Aster keys in secrets/.env + os.environ.

    設計決定：用 tempfile + os.replace() 原子寫入，避免寫入中途 crash 導致文件損壞。
    """
    os.environ["ASTER_API_KEY"] = api_key
    os.environ["ASTER_API_SECRET"] = api_secret
    env_dir = os.path.dirname(SECRETS_ENV_PATH)
    os.makedirs(env_dir, exist_ok=True)
    lines = []
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                if not line.strip().startswith(("ASTER_API_KEY=", "ASTER_API_SECRET=")):
                    lines.append(line.rstrip("\n"))
    lines.append(f"ASTER_API_KEY={api_key}")
    lines.append(f"ASTER_API_SECRET={api_secret}")
    fd, tmp_path = tempfile.mkstemp(dir=env_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp_path, SECRETS_ENV_PATH)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def handle_aster_status():
    """GET /api/aster/status"""
    api_key, api_secret = _get_aster_credentials()
    if not api_key or not api_secret:
        return 200, {"status": "disconnected", "label": "未連接", "balance": None}
    try:
        client = _get_aster_client()
        bal = client.get_usdt_balance()
        return 200, {
            "status": "connected", "label": "已連接",
            "balance": round(bal, 2),
            "key_preview": f"{api_key[:4]}...{api_key[-4:]}",
        }
    except Exception as e:
        return 200, {"status": "error", "label": "驗證失敗", "balance": None, "error": str(e)[:80]}


def handle_aster_connect(body):
    """POST /api/aster/connect"""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"ok": False, "error": "Invalid JSON"}
    api_key = (data.get("api_key") or "").strip()
    api_secret = (data.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        return 400, {"ok": False, "error": "API Key 和 Secret 不能為空"}
    _save_aster_credentials(api_key, api_secret)
    try:
        def verify():
            if SCRIPTS_DIR not in sys.path:
                sys.path.insert(0, SCRIPTS_DIR)
            from trader_cycle.exchange.aster_client import AsterClient
            client = AsterClient()
            return client.get_usdt_balance()
        bal = _run_with_timeout(verify)
        return 200, {"ok": True, "status": "connected", "key_preview": f"{api_key[:4]}...{api_key[-4:]}", "balance": round(bal, 2)}
    except TimeoutError as e:
        return 504, {"ok": False, "error": str(e)}
    except Exception as e:
        return 401, {"ok": False, "error": f"驗證失敗：{str(e)[:120]}"}


def handle_aster_disconnect():
    """POST /api/aster/disconnect"""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            lines = [l for l in f.read().splitlines()
                     if not l.strip().startswith(("ASTER_API_KEY=", "ASTER_API_SECRET="))]
        with open(SECRETS_ENV_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
    return 200, {"ok": True, "status": "disconnected"}


# ── Binance ─────────────────────────────────────────────────────────

def _get_binance_credentials():
    """Read Binance keys from secrets/.env"""
    api_key = api_secret = ""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                elif line.startswith("BINANCE_API_SECRET="):
                    api_secret = line.split("=", 1)[1].strip()
    return api_key, api_secret


def _save_binance_credentials(api_key, api_secret):
    """Write or update Binance keys in secrets/.env + os.environ."""
    os.environ["BINANCE_API_KEY"] = api_key
    os.environ["BINANCE_API_SECRET"] = api_secret
    os.makedirs(os.path.dirname(SECRETS_ENV_PATH), exist_ok=True)
    lines = []
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                if not line.strip().startswith(("BINANCE_API_KEY=", "BINANCE_API_SECRET=")):
                    lines.append(line.rstrip("\n"))
    lines.append(f"BINANCE_API_KEY={api_key}")
    lines.append(f"BINANCE_API_SECRET={api_secret}")
    with open(SECRETS_ENV_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def handle_binance_status():
    """GET /api/binance/status"""
    api_key, api_secret = _get_binance_credentials()
    if not api_key or not api_secret:
        return 200, {"status": "disconnected", "label": "未連接", "balance": None}
    try:
        from binance.spot import Spot
        client = Spot(api_key=api_key, api_secret=api_secret)
        account = client.account()
        balances = {
            b["asset"]: float(b["free"]) + float(b["locked"])
            for b in account["balances"]
            if float(b["free"]) + float(b["locked"]) > 0
        }
        usdt_bal = balances.get("USDT", 0)
        return 200, {
            "status": "connected", "label": "已連接",
            "balance": round(usdt_bal, 2),
            "account_type": account.get("accountType", "SPOT"),
            "key_preview": f"{api_key[:4]}...{api_key[-4:]}",
        }
    except Exception as e:
        return 200, {"status": "error", "label": "驗證失敗", "balance": None, "error": str(e)[:80]}


def handle_binance_connect(body):
    """POST /api/binance/connect"""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"ok": False, "error": "Invalid JSON"}
    api_key = (data.get("api_key") or "").strip()
    api_secret = (data.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        return 400, {"ok": False, "error": "API Key 和 Secret 不能為空"}
    try:
        def verify():
            from binance.spot import Spot
            client = Spot(api_key=api_key, api_secret=api_secret)
            return client.account()
        account = _run_with_timeout(verify)
    except TimeoutError as e:
        return 504, {"ok": False, "error": str(e)}
    except Exception as e:
        return 401, {"ok": False, "error": f"驗證失敗：{str(e)[:120]}"}
    _save_binance_credentials(api_key, api_secret)
    usdt = next((float(b["free"]) for b in account["balances"] if b["asset"] == "USDT"), 0)
    return 200, {
        "ok": True, "status": "connected",
        "key_preview": f"{api_key[:4]}...{api_key[-4:]}",
        "balance": round(usdt, 2),
    }


def handle_binance_disconnect():
    """POST /api/binance/disconnect"""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            lines = [l for l in f.read().splitlines()
                     if not l.startswith(("BINANCE_API_KEY=", "BINANCE_API_SECRET="))]
        with open(SECRETS_ENV_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
    return 200, {"ok": True, "status": "disconnected"}


# ── HyperLiquid ─────────────────────────────────────────────────────

def _get_hl_credentials():
    """Read HL keys from secrets/.env"""
    pk = addr = ""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("HL_PRIVATE_KEY="):
                    pk = line.split("=", 1)[1].strip()
                elif line.startswith("HL_ACCOUNT_ADDRESS="):
                    addr = line.split("=", 1)[1].strip()
    return pk, addr


def _save_hl_credentials(private_key, account_address):
    """Write or update HL keys in secrets/.env + os.environ."""
    os.environ["HL_PRIVATE_KEY"] = private_key
    os.environ["HL_ACCOUNT_ADDRESS"] = account_address
    lines = []
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f.read().splitlines():
                if not line.strip().startswith(("HL_PRIVATE_KEY=", "HL_ACCOUNT_ADDRESS=")):
                    lines.append(line)
    lines.append(f"HL_PRIVATE_KEY={private_key}")
    lines.append(f"HL_ACCOUNT_ADDRESS={account_address}")
    with open(SECRETS_ENV_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def handle_hl_status():
    """GET /api/hl/status"""
    pk, addr = _get_hl_credentials()
    if not pk or not addr:
        return 200, {"status": "disconnected", "label": "未連接", "balance": None}
    try:
        client = _get_hl_client()
        bal = client.get_usdt_balance()
        return 200, {
            "status": "connected", "label": "已連接",
            "balance": round(bal, 2),
        }
    except Exception as e:
        return 200, {"status": "error", "label": "驗證失敗", "balance": None, "error": str(e)[:80]}


def handle_hl_connect(body):
    """POST /api/hl/connect"""
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except Exception:
        return 400, {"ok": False, "error": "Invalid JSON"}
    private_key = (data.get("private_key") or "").strip()
    account_address = (data.get("account_address") or "").strip()
    if not private_key or not account_address:
        return 400, {"ok": False, "error": "Missing private_key or account_address"}

    _save_hl_credentials(private_key, account_address)

    try:
        def verify():
            client = _get_hl_client()
            return client.get_usdt_balance()
        bal = _run_with_timeout(verify)
        addr_preview = f"{account_address[:6]}...{account_address[-4:]}"
        return 200, {"ok": True, "status": "connected", "addr_preview": addr_preview, "balance": round(bal, 2)}
    except TimeoutError as e:
        return 504, {"ok": False, "error": str(e)}
    except Exception as e:
        return 401, {"ok": False, "status": "error", "error": str(e)[:120]}


def handle_hl_disconnect():
    """POST /api/hl/disconnect"""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            lines = [l for l in f.read().splitlines()
                     if not l.strip().startswith(("HL_PRIVATE_KEY=", "HL_ACCOUNT_ADDRESS="))]
        with open(SECRETS_ENV_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
    return 200, {"ok": True, "status": "disconnected"}


# ── Demo mode ───────────────────────────────────────────────────────

def _is_demo_mode() -> bool:
    """True when no exchange credentials configured — triggers demo data."""
    ak, asec = _get_aster_credentials()
    if ak and asec:
        return False
    bk, bsec = _get_binance_credentials()
    if bk and bsec:
        return False
    hpk, haddr = _get_hl_credentials()
    if hpk and haddr:
        return False
    return True


def _get_demo_data() -> dict:
    """Return demo data with dynamic timestamps and sine-wave PnL history."""
    now = datetime.now(HKT)
    ts = now.strftime("%Y-%m-%d %H:%M:%S UTC+8")
    today = now.strftime("%Y-%m-%d")
    data = {k: v for k, v in DEMO_DATA.items()}  # shallow copy
    data["timestamp"] = ts
    data["last_scan"] = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    data["demo_mode"] = True

    # Scan log with today's date so frontend filter picks them up
    data["scan_log"] = [
        f"[{today} 10:30:00] LIGHT scan #42 — 6 pairs, 0 triggers",
        f"[{today} 10:25:00] LIGHT scan #41 — 6 pairs, 0 triggers",
        f"[{today} 10:15:00] DEEP scan #8 — TRIGGER:BTCUSDT score=78",
        f"[{today} 10:00:00] LIGHT scan #40 — 6 pairs, 1 triggers",
        f"[{today} 09:45:00] LIGHT scan #39 — 6 pairs, 0 triggers",
    ]

    # PnL history: {t: unix_seconds, v: pnl_value}
    pnl_history = []
    base_pnl = 0.0
    for i in range(24):
        t = now - timedelta(hours=(24 - i) * 7)
        val = base_pnl + 40 * math.sin(i * 0.5) + i * 3.5
        pnl_history.append({
            "t": int(t.timestamp()),
            "v": round(val, 2),
        })
    data["pnl_history"] = pnl_history

    # Activity log with dynamic timestamps, field name = "time"
    activity = []
    for j, entry in enumerate(DEMO_DATA["activity_log"]):
        e = dict(entry)
        e["time"] = (now - timedelta(minutes=30 * (j + 1))).strftime("%Y-%m-%d %H:%M")
        activity.append(e)
    data["activity_log"] = activity

    # Exchange trades with dynamic timestamps
    trades = []
    for k, tr in enumerate(DEMO_DATA["exchange_trades"]):
        t2 = dict(tr)
        t2["time"] = (now - timedelta(hours=k * 6 + 1)).strftime("%Y-%m-%d %H:%M:%S")
        trades.append(t2)
    data["exchange_trades"] = trades

    # Agent last_seen
    agents = []
    for a in DEMO_DATA["agents"]:
        a2 = dict(a)
        a2["last_seen"] = (now - timedelta(minutes=2)).strftime("%H:%M:%S")
        agents.append(a2)
    data["agents"] = agents

    return data
