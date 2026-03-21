# Progress Log: Data Diversity Layer

## Session: 2026-03-22

### Phase 0: Research ✅
- 3 × Opus subagent parallel research:
  1. Exchange API audit — 6 exchanges, 25+ unused endpoints confirmed
  2. Anti-overfitting data strategies — ranked by independence × practicality
  3. MM/1H integration point audit — signal flow mapped, file:line references
- Key insight from user: **用數量壓延遲** — parallel fetch N sources, fastest wins
- Architecture decision: `market_data.py` shared fetcher with ThreadPoolExecutor
- Plan written, ready for Phase 1

### Phase 1: In Progress
- ✅ 1A: Created `polymarket/data/market_data.py` — StaggeredFetcher + MarketSnapshot + SnapshotHistory
- ✅ 1B: Price from 5 sources (Binance spot/fut, OKX, Bybit, HL) — all working
- ✅ 1C: Funding from 5 sources (Binance, OKX, Bybit, Deribit, HL) — all working
- ✅ 1D: MarketSnapshot dataclass with all fields + metadata
- ✅ CLI test: **22/22 sources, 1.8-1.9s** for BTC + ETH
- ✅ DVOL working (BTC=52.3, ETH=76.5), OI correct per-symbol, L/S extreme detection working
- 🔲 1E: Wire into MM bot
- 🔲 1F: Wire into 1H bot
- 🔲 1G: Funding as sizing modifier
- 🔲 1H: Signal log
- 🔲 1I: Dry-run timing benchmark

### Phase 4: Fill Model Data Collection ✅
- ✅ #1: σ_poly by hour — 3.2x ToD effect (07:00 HKT best, 05:00 worst). r=0.063 vs σ_btc.
- ✅ #2: OB recorder — poly_ob_tape.jsonl, 5s interval, rate-limited to ~24 req/min
- ✅ #3: Arb spread — 0.5% of snapshots < $0.98, 96% last 1 tick. Not viable as strategy.
- ✅ Safety: OB recorder rate limit reduced (2 windows, 0.5s delay), disk-full protected
- ✅ SOL + ETH 15M discovery enabled (dry-run only)

### Key Findings (from analysis)
- σ_poly ≠ σ_btc (r=0.063) → fill model must track Poly OB, not exchange vol
- ToD effect 3.2x → free edge from entry timing
- Arb transient → use as dislocation signal, not standalone strategy
- Cancel policy kills 43pp of fill rate → TTL review is highest priority

### Next Steps
1. Start OB recorder daemon (collect 48h of depth data)
2. Wire market_data.py into MM bot (15M BTC live, ETH+SOL dry-run)
3. Cancel policy review (TTL diagnostic mode → extend TTL → validate)

## Reboot Check
| Question | Answer |
|----------|--------|
| 做緊咩？ | 增加 MM/1H bot 數據來源數量 + 真實性，用數量壓延遲 |
| 目標？ | 25 endpoints / 6 exchanges / parallel fetch <500ms |
| 到邊？ | Phase 0 research done, Phase 1 ready |
| 紅線？ | 唔改 bridge 公式，新 signal 只做 gate/modifier |
