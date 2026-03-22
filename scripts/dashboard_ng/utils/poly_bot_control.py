"""Bot control — start/stop Polymarket bots.

Standalone module used by both dashboard UI buttons AND background scheduler.
Bots run detached (nohup) so they survive dashboard restarts.
"""

import logging
import os
import signal
import subprocess

log = logging.getLogger('axc.bot_control')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
MINIFORGE_PYTHON = '/opt/homebrew/Caskroom/miniforge/base/bin/python3'
LOG_DIR = os.path.join(AXC_HOME, 'polymarket', 'logs')

# Bot definitions: (display_name, script_path, cli_args, process_key)
BOT_DEFS = [
    ('MM 15M', 'polymarket/run_mm_live.py', '--live', 'run_mm_live'),
    ('1H Conv', 'polymarket/run_1h_live.py', '--live', 'run_1h_live'),
]


def get_running_processes() -> list[dict]:
    """Find all running polymarket-related processes with PID, uptime, command."""
    try:
        # Use pid,etime,command only — lstart field count varies by locale
        result = subprocess.run(
            ['ps', '-eo', 'pid,etime,command'],
            capture_output=True, text=True, timeout=5
        )
        procs = []
        for line in result.stdout.strip().split('\n')[1:]:
            if 'polymarket' not in line.lower() and 'poly' not in line.lower():
                continue
            if 'grep' in line or 'ps -eo' in line:
                continue
            # Skip transient subprocesses (python3 -c "..." from poly_live/poly_market_data)
            if 'python3 -c' in line or 'python -c' in line:
                continue
            parts = line.strip().split(None, 2)  # split into 3: pid, etime, command
            if len(parts) < 3:
                continue
            pid = parts[0]
            elapsed = parts[1]
            cmd = parts[2]
            cmd_short = cmd.replace('/opt/homebrew/bin/python3 -u ', '') \
                           .replace('/opt/homebrew/bin/python3 ', '') \
                           .replace(f'{MINIFORGE_PYTHON} -u ', '') \
                           .replace(f'{MINIFORGE_PYTHON} ', '')
            procs.append({
                'pid': pid,
                'uptime': elapsed,
                'cmd': cmd_short,
                'cmd_full': cmd,
            })
        return procs
    except Exception:
        return []


def is_bot_running(process_key: str) -> dict | None:
    """Check if a specific bot is running. Returns process dict or None."""
    procs = get_running_processes()
    for p in procs:
        if process_key in p.get('cmd', '') or process_key in p.get('cmd_full', ''):
            return p
    return None


def start_bot(script: str, args: str, process_key: str) -> bool:
    """Start a bot as a detached process. Returns True if launched."""
    # Check if already running
    if is_bot_running(process_key):
        log.warning('Bot %s already running, skipping start', process_key)
        return False

    os.makedirs(LOG_DIR, exist_ok=True)
    cmd = (f'cd {AXC_HOME} && nohup {MINIFORGE_PYTHON} '
           f'-u {script} {args} > {LOG_DIR}/{process_key}_stdout.log 2>&1 &')
    try:
        subprocess.run(['bash', '-c', cmd], capture_output=True, text=True, timeout=5)
        # Verify process actually started (brief delay + check)
        import time
        time.sleep(1.5)
        if is_bot_running(process_key):
            log.info('Started bot: %s (%s %s)', process_key, script, args)
            return True
        else:
            log.error('Bot %s launched but not found in ps — likely crashed on init', process_key)
            return False
    except Exception as e:
        log.error('Failed to start %s: %s', process_key, e)
        return False


def stop_bot(script: str, process_key: str) -> int:
    """Stop a bot by killing matching processes. Returns number killed."""
    procs = get_running_processes()
    killed = 0
    for p in procs:
        if script in p.get('cmd_full', '') or script in p.get('cmd', '') or \
           process_key in p.get('cmd_full', '') or process_key in p.get('cmd', ''):
            try:
                os.kill(int(p['pid']), signal.SIGTERM)
                killed += 1
                log.info('Stopped bot PID %s (%s)', p['pid'], process_key)
            except (ProcessLookupError, PermissionError) as e:
                log.warning('Failed to kill PID %s: %s', p['pid'], e)
    return killed
