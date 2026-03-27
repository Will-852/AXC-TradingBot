# Progress — Infrastructure Upgrades
> Started: 2026-03-28

## Session Log

### 2026-03-28
- [x] Recon: backup system, logging, CI/CD, test setup, shared/ structure
- [x] Created task_plan.md with 3 phases
- [x] Phase 1: Off-site Backup ✅
  - Added rsync to iCloud Drive in backup_agent.sh (shared/, config/, ai/, secrets/.env, latest zip)
  - Graceful fallback if iCloud unavailable (skip sync, exit 0)
  - Updated BACKUP.md diagram + table
  - 2check: PASS (syntax OK, quoting OK, paths correct)
- [x] Phase 2: Structured Metrics Log ✅
  - Created scripts/query_metrics.py — CLI queries signal_journal.jsonl
  - Filters: symbol, strategy, direction, session_tag, date, live/dry_run, selected/executed
  - Breakdown mode: group by any field
  - PnL join with trades.jsonl via --with-pnl
  - 2check: found side/direction mismatch bug → fixed (BUY→LONG, SELL→SHORT normalize)
- [x] Phase 3: Pre-deploy Test Gate ✅
  - Created .github/workflows/test.yml (push to main + PRs → pytest)
  - Added test job as prerequisite to release.yml (needs: test)
  - Created scripts/install_hooks.sh (pre-push hook → pytest)
  - 2check: found missing pandas in CI install → fixed
  - Note: run `bash scripts/install_hooks.sh` to activate local hook
