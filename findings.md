# Findings — Infrastructure Upgrades
> Created: 2026-03-28

## Recon Results

### Backup System
- `scripts/backup_agent.sh`: crontab 03:00 daily, git push + local zip (last 10)
- Zips: config/ agents/ shared/ scripts/ ai/ docs/ CLAUDE.md
- NOT backed up off-site: shared/ state, secrets/.env (manual iCloud), vector index
- No LaunchAgent for backup — crontab only
- Docs: `docs/guides/BACKUP.md`

### Trade Logging
- `memory/store/trades.jsonl`: sparse — id, type, content, ts, symbol, side, entry, exit, pnl
- `shared/signal_journal.jsonl`: rich — ~40 fields per cycle (strategy, regime, confidence, indicators)
- `shared/activity_log.jsonl`: lightweight system events only
- Existing metrics: `scripts/trader_cycle/analysis/metrics.py` reads trades.jsonl
- Gap: no way to query signal_journal by outcome (WR, PnL by filter)

### CI/CD
- `.github/workflows/release.yml`: tag push → zip → release. NO test step.
- No git hooks installed (only .sample files)
- 197 tests, 16 files, pytest.ini: testpaths=tests, pythonpath=. scripts
- Dependencies for tests: numpy (conftest fixtures)
