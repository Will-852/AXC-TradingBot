"""
axc_client.py — AXC → OpenClaw 連接層
取代 tg_bot.py 直接 file read/write，透過 dashboard.py HTTP API 操作。

Usage:
    from axc_client import OpenClawClient
    client = OpenClawClient()
    state = client.get_state()
"""

import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:5555"
REQUEST_TIMEOUT = 10  # seconds


class OpenClawClient:
    """AXC → OpenClaw API client. Wraps dashboard.py endpoints."""

    def __init__(self, base_url=DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")

    def _get(self, path: str) -> dict:
        """GET request, returns parsed JSON or raises."""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            logger.error("OpenClaw API unreachable: %s %s", url, e)
            raise
        except json.JSONDecodeError as e:
            logger.error("OpenClaw API invalid JSON: %s %s", url, e)
            raise

    def _post(self, path: str, data: dict) -> dict:
        """POST request with JSON body, returns parsed JSON or raises."""
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # Read error body for details
            err_body = e.read().decode() if e.fp else str(e)
            logger.error("OpenClaw API error %s: %s %s", e.code, url, err_body)
            raise
        except urllib.error.URLError as e:
            logger.error("OpenClaw API unreachable: %s %s", url, e)
            raise

    def get_state(self) -> dict:
        """GET /api/state — trade state + signal + active profile."""
        return self._get("/api/state")

    def get_config(self) -> dict:
        """GET /api/config — all trading params (profile-aware)."""
        return self._get("/api/config")

    def set_mode(self, mode: str) -> dict:
        """POST /api/config/mode — switch ACTIVE_PROFILE."""
        return self._post("/api/config/mode", {"mode": mode})

    def set_trading(self, enabled: bool) -> dict:
        """POST /api/config/trading — toggle TRADING_ENABLED."""
        return self._post("/api/config/trading", {"enabled": enabled})

    def get_scan_log(self) -> list:
        """GET /api/scan-log — recent scan log lines."""
        data = self._get("/api/scan-log")
        return data.get("lines", [])

    def get_health(self) -> dict:
        """GET /api/health — agent status + timestamps + scanner heartbeat."""
        return self._get("/api/health")

    def is_available(self) -> bool:
        """Check if OpenClaw dashboard is reachable."""
        try:
            self._get("/api/health")
            return True
        except Exception:
            return False
