# Task Plan — 3 Infrastructure Upgrades
> Created: 2026-03-28
> Status: COMPLETE

## Goal
Implement 3 long-term infrastructure upgrades for AXC, in priority order.

## Phases

### Phase 1: Off-site Backup ✅
**What**: Add rsync to iCloud Drive in existing `backup_agent.sh`
**Why**: Single point of failure — local disk dies = everything gone. Git push only covers code, not shared/ state or secrets.
**Files to change**:
- `scripts/backup_agent.sh` — add rsync step after existing zip
- `docs/guides/BACKUP.md` — update docs
**Decisions**:
- Target: `~/Library/Mobile Documents/com~apple~CloudDrive/AXC-Backup/` (iCloud Drive, zero setup)
- Sync: shared/, config/, secrets/.env, ai/, latest backup zip
- NOT syncing: logs/ (too large, rebuildable), .git/ (already on GitHub)
- Use rsync --delete to mirror (not accumulate)

### Phase 2: Structured Metrics Log ✅
**What**: Enrich trade records with strategy context from `signal_journal.jsonl`. Add query script.
**Why**: Can't answer "WR on DOWN signals?" or "which session is most profitable?" — data exists in signal_journal but not carried to trade records.
**Files to change**:
- New: `scripts/query_metrics.py` — CLI to query/filter/aggregate from signal_journal.jsonl directly
- `scripts/trader_cycle/analysis/metrics.py` — enhance to join trades.jsonl + signal_journal.jsonl
**Decisions**:
- Don't create new JSONL — signal_journal already has everything, just need query tooling
- Don't modify existing trades.jsonl schema (other systems depend on it)
- Query by: symbol, strategy, direction, session_tag, date range, dry_run flag

### Phase 3: Pre-deploy Test Gate ✅
**What**: GitHub Actions test job + local pre-push hook
**Why**: 2 of 3 historical $ losses were "changed A, broke B". 197 tests exist but never run automatically.
**Files to change**:
- `.github/workflows/test.yml` — new workflow: on push/PR → pytest
- `.github/workflows/release.yml` — add test job as prerequisite
- `scripts/install_hooks.sh` — installs git pre-push hook

## Errors
| # | Phase | What happened | Resolution |
|---|-------|--------------|------------|
| (none yet) | | | |
