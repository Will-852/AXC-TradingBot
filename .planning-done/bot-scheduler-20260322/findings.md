# Findings: Bot Scheduler
- NiceGUI ui.timer = per-page only, NOT for daily schedules
- app.on_startup(async_func) = server-level, existing pattern in state.py:111
- Bot python: /opt/homebrew/Caskroom/miniforge/base/bin/python3
- Bots self-handle sys.path, no load_env.sh needed
- 22 LaunchAgents exist, 2 poly-related (polypipeline, polywatcher)
