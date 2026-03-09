"""Optional OpenClaw bridge — detect platform, expose helpers, never fail."""

import json
import os
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)

_OC_JSON = os.path.expanduser("~/.openclaw/openclaw.json")


class _Bridge:
    """Singleton that detects OpenClaw and exposes safe helpers."""

    def __init__(self):
        self._available = shutil.which("openclaw") is not None
        self._conf = self._load_conf()

    # --- internal ---

    def _load_conf(self):
        """Read openclaw.json once; return {} on any failure."""
        try:
            with open(os.path.normpath(_OC_JSON)) as f:
                return json.load(f)
        except Exception:
            return {}

    # --- public API ---

    @property
    def available(self) -> bool:
        return self._available

    def gateway_status(self) -> str:
        """'ok' / 'down' / 'n/a' — never raises."""
        if not self._available:
            return "n/a"
        try:
            r = subprocess.run(
                ["openclaw", "gateway", "status"],
                capture_output=True, text=True, timeout=5,
            )
            return "ok" if r.returncode == 0 and r.stdout.strip() else "down"
        except Exception:
            return "down"

    def gateway_port(self):
        """Return configured gateway port or None."""
        try:
            return self._conf["gateway"]["port"]
        except (KeyError, TypeError):
            return None

    def agent_models(self) -> dict:
        """Return {agent_id: model_name} from openclaw.json, or {}."""
        try:
            agents = self._conf["agents"]["list"]
            return {
                a["id"]: a["model"].split("/", 1)[-1]
                for a in agents if "id" in a and "model" in a
            }
        except (KeyError, TypeError):
            return {}


bridge = _Bridge()
