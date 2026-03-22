"""Bot scheduler — auto start/stop based on time schedules.

Runs as a server-level background task via app.on_startup().
Checks every 30s if current HKT time matches any schedule.
Independent of page visits (unlike ui.timer).
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger('axc.scheduler')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
SCHEDULES_PATH = os.path.join(AXC_HOME, 'polymarket', 'config', 'schedules.json')
HKT = ZoneInfo('Asia/Hong_Kong')
CHECK_INTERVAL = 30  # seconds

# Track last action to prevent repeat triggers within the same minute
_last_action: dict[str, str] = {}


def _within_minute(now: datetime, target_hhmm: str) -> bool:
    """Check if now is within 60s of target HH:MM (same day)."""
    try:
        h, m = int(target_hhmm[:2]), int(target_hhmm[3:5])
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = abs((now - target).total_seconds())
        return diff < 60
    except (ValueError, IndexError):
        return False


def read_schedules() -> dict:
    """Read schedules from JSON file."""
    if not os.path.exists(SCHEDULES_PATH):
        return {}
    try:
        with open(SCHEDULES_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error('Failed to read schedules: %s', e)
        return {}


def write_schedules(schedules: dict):
    """Atomic write schedules to JSON file."""
    os.makedirs(os.path.dirname(SCHEDULES_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(SCHEDULES_PATH), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(schedules, f, indent=2)
        os.replace(tmp, SCHEDULES_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


async def bot_scheduler():
    """Background scheduler loop. Register via app.on_startup()."""
    from scripts.dashboard_ng.utils.poly_bot_control import (
        BOT_DEFS, start_bot, stop_bot, is_bot_running,
    )

    # Build lookup: key → (name, script, args)
    bot_lookup = {key: (name, script, args) for name, script, args, key in BOT_DEFS}

    log.info('Bot scheduler started (check every %ds, timezone %s)', CHECK_INTERVAL, HKT)

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            schedules = read_schedules()
            now = datetime.now(HKT)
            now_hhmm = now.strftime('%H:%M')

            for key, sched in schedules.items():
                if not sched.get('enabled', False):
                    continue
                if key not in bot_lookup:
                    continue

                name, script, args = bot_lookup[key]
                start_time = sched.get('start', '')
                stop_time = sched.get('stop', '')

                # Auto-start (within 60s window to avoid missed minutes)
                if start_time and _within_minute(now, start_time):
                    action_key = f'{key}_start_{start_time}'
                    if action_key not in _last_action:
                        running = is_bot_running(key)
                        if not running:
                            ok = start_bot(script, args, key)
                            if ok:
                                log.info('Scheduler: auto-started %s at %s', name, now_hhmm)
                            _last_action[action_key] = now.isoformat()

                # Auto-stop (within 60s window)
                if stop_time and _within_minute(now, stop_time):
                    action_key = f'{key}_stop_{stop_time}'
                    if action_key not in _last_action:
                        killed = stop_bot(script, key)
                        if killed:
                            log.info('Scheduler: auto-stopped %s at %s (%d killed)',
                                     name, now_hhmm, killed)
                        _last_action[action_key] = now.isoformat()

            # Clean old action keys (keep last 10 minutes only)
            cutoff = now.timestamp() - 600
            for k in list(_last_action.keys()):
                try:
                    ts = datetime.fromisoformat(_last_action[k]).timestamp()
                    if ts < cutoff:
                        del _last_action[k]
                except (ValueError, TypeError):
                    del _last_action[k]

        except Exception as e:
            log.error('Scheduler error: %s', e)
