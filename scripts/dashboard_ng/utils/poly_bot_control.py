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
        result = subprocess.run(
            ['ps', '-eo', 'pid,lstart,etime,command'],
            capture_output=True, text=True, timeout=5
        )
        procs = []
        for line in result.stdout.strip().split('\n')[1:]:
            if 'polymarket' not in line.lower() and 'poly' not in line.lower():
                continue
            if 'grep' in line or 'ps -eo' in line:
                continue
            parts = line.strip().split()
            if len(parts) < 8:
                continue
            pid = parts[0]
            start_time = ' '.join(parts[1:6])
            elapsed = parts[6]
            cmd = ' '.join(parts[7:])
            cmd_short = cmd.replace('/opt/homebrew/bin/python3 -u ', '') \
                           .replace('/opt/homebrew/bin/python3 ', '') \
                           .replace(f'{MINIFORGE_PYTHON} -u ', '') \
                           .replace(f'{MINIFORGE_PYTHON} ', '')
            procs.append({
                'pid': pid,
                'start': start_time,
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
        log.info('Started bot: %s (%s %s)', process_key, script, args)
        return True
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
