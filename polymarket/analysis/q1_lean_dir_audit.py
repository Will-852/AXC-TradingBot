"""
Q1 Lean Dir Audit
=================
Cross-reference mm_w4_5m.jsonl (entry decisions + resolution records)
vs mm_trades_5m.jsonl (resolution results).

KEY FINDING: mm_w4_5m.jsonl contains TWO event types:
  - event=w4_entry    → the signal state at decision time
  - event=resolution  → the outcome logged at resolution time

The 'lean_dir' in resolution records (both files) appears to be INVERTED vs the
entry-time lean_dir. This audit checks whether that inversion affects the 50.3% WR.
"""

import json
from pathlib import Path
from collections import defaultdict
import statistics
import datetime

ENTRY_LOG = Path("/Users/wai/projects/axc-trading/polymarket/logs/mm_w4_5m.jsonl")
TRADE_LOG = Path("/Users/wai/projects/axc-trading/polymarket/logs/mm_trades_5m.jsonl")


def load_jsonl(path):
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def parse_ts(ts_str):
    return datetime.datetime.fromisoformat(ts_str)


def is_win(lean_dir, result):
    """Win = the side we leaned on resolved YES (matching result)."""
    return lean_dir.upper() == result.upper()


def main():
    all_records = load_jsonl(ENTRY_LOG)
    trades_file = load_jsonl(TRADE_LOG)

    # ── Split entry log into w4_entry vs resolution records ─────────────────
    entry_records = [r for r in all_records if r.get("event") == "w4_entry"]
    resol_records = [r for r in all_records if r.get("event") == "resolution"]
    other_records = [r for r in all_records if r.get("event") not in ("w4_entry", "resolution")]

    print(f"mm_w4_5m.jsonl total lines : {len(all_records)}")
    print(f"  event=w4_entry            : {len(entry_records)}")
    print(f"  event=resolution          : {len(resol_records)}")
    print(f"  event=other               : {len(other_records)}")
    if other_records:
        event_types = set(r.get("event") for r in other_records)
        print(f"  other event types         : {event_types}")
    print(f"mm_trades_5m.jsonl records : {len(trades_file)}")
    print()

    # ── Build lookups by short cid (entry log uses short cid) ───────────────
    # Entry cid format: 0x21739f  (0x + 6 hex chars)
    # Trade cid format: 0x21739fb35bdf...  (full 32-byte hash)
    # Match: trade_cid.startswith(entry_cid)

    def normalise_short(cid):
        return cid.lower()

    entry_by_cid = {}
    for e in entry_records:
        cid = normalise_short(e["cid"])
        if cid in entry_by_cid:
            print(f"  [WARN] duplicate w4_entry cid: {cid}")
        entry_by_cid[cid] = e

    resol_by_cid = {}
    for r in resol_records:
        cid = normalise_short(r["cid"])
        resol_by_cid[cid] = r

    # Map short cid → trade record (from mm_trades_5m.jsonl)
    # Trade cids are full hashes; we match by startswith(short_cid)
    trade_by_short = {}
    for t in trades_file:
        full_cid = t["cid"].lower()
        # Find matching short cid
        for short_cid in entry_by_cid:
            if full_cid.startswith(short_cid):
                trade_by_short[short_cid] = t
                break

    print(f"Entry records matched to trade records: {len(trade_by_short)}/{len(entry_by_cid)}")
    unmatched_entries = [c for c in entry_by_cid if c not in trade_by_short]
    if unmatched_entries:
        print(f"  Unmatched entry cids: {unmatched_entries[:5]}")
    print()

    # ── Build matched triples: (entry, resolution_in_entry_log, trade_record) ─
    triples = []
    for short_cid, e in entry_by_cid.items():
        r = resol_by_cid.get(short_cid)       # resolution in mm_w4_5m.jsonl
        t = trade_by_short.get(short_cid)     # record in mm_trades_5m.jsonl
        if t is not None:
            triples.append((e, r, t))

    print(f"Complete triples (entry + trade): {len(triples)}")
    print()

    # ── 1. Lean-dir comparison: w4_entry vs resolution ──────────────────────
    print("=" * 72)
    print("  SECTION 1: lean_dir — w4_entry LOG vs resolution/trade LOG")
    print("=" * 72)

    match_count    = 0
    invert_count   = 0
    invert_details = []

    for e, r, t in triples:
        e_dir = e["lean_dir"].upper()
        t_dir = t["lean_dir"].upper()
        if e_dir == t_dir:
            match_count += 1
        else:
            invert_count += 1
            invert_details.append((e, r, t))

    n = len(triples)
    print(f"  MATCHING  lean_dir : {match_count:3d} / {n}  ({100*match_count/n:.1f}%)")
    print(f"  INVERTED  lean_dir : {invert_count:3d} / {n}  ({100*invert_count/n:.1f}%)")
    print()

    if invert_details:
        print(f"  Sample of inverted pairs (first 8):")
        print(f"  {'cid':<10} {'coin':<6} {'entry_lean':<12} {'trade_lean':<12} {'result':<8} {'pnl':>7}")
        print("  " + "-" * 62)
        for e, r, t in invert_details[:8]:
            print(f"  {e['cid']:<10} {e['coin']:<6} {e['lean_dir']:<12} {t['lean_dir']:<12} {t['result']:<8} {t['pnl']:>7.3f}")
        print()

    # ── 2. WR calculations ───────────────────────────────────────────────────
    print("=" * 72)
    print("  SECTION 2: WIN RATE ANALYSIS")
    print("=" * 72)

    # 2a. WR using TRADE LOG lean_dir (as currently reported)
    wr_trade_wins = sum(1 for e, r, t in triples if is_win(t["lean_dir"], t["result"]))
    wr_trade = wr_trade_wins / n

    # 2b. WR using ENTRY LOG lean_dir (signal at decision time — the TRUE signal)
    wr_entry_wins = sum(1 for e, r, t in triples if is_win(e["lean_dir"], t["result"]))
    wr_entry = wr_entry_wins / n

    delta_pp = (wr_entry - wr_trade) * 100

    print(f"  WR using trade-log lean_dir   (REPORTED) : {wr_trade_wins}/{n} = {100*wr_trade:.1f}%")
    print(f"  WR using entry-log lean_dir   (CORRECTED): {wr_entry_wins}/{n} = {100*wr_entry:.1f}%")
    print(f"  Delta                                     : {delta_pp:+.1f} pp")
    print()

    # 2c. For the inverted subset specifically
    if invert_details:
        inv_n = len(invert_details)
        inv_wins_wrong     = sum(1 for e, r, t in invert_details if is_win(t["lean_dir"], t["result"]))
        inv_wins_corrected = sum(1 for e, r, t in invert_details if is_win(e["lean_dir"], t["result"]))
        print(f"  Inverted subset ({inv_n} trades):")
        print(f"    WR using trade lean_dir (WRONG)  : {inv_wins_wrong}/{inv_n} = {100*inv_wins_wrong/inv_n:.1f}%")
        print(f"    WR using entry lean_dir (CORRECT): {inv_wins_corrected}/{inv_n} = {100*inv_wins_corrected/inv_n:.1f}%")
        print()

    # ── 3. WR by subset ─────────────────────────────────────────────────────
    print("=" * 72)
    print("  SECTION 3: CORRECTED WR BY SUBSET")
    print("=" * 72)

    same_set   = [(e, r, t) for e, r, t in triples if e["lean_dir"].upper() == t["lean_dir"].upper()]
    invert_set = [(e, r, t) for e, r, t in triples if e["lean_dir"].upper() != t["lean_dir"].upper()]

    if same_set:
        w = sum(1 for e, r, t in same_set if is_win(e["lean_dir"], t["result"]))
        print(f"  SAME-dir subset  ({len(same_set):3d} trades): corrected WR = {w}/{len(same_set)} = {100*w/len(same_set):.1f}%")
    if invert_set:
        w = sum(1 for e, r, t in invert_set if is_win(e["lean_dir"], t["result"]))
        print(f"  INVERT subset    ({len(invert_set):3d} trades): corrected WR = {w}/{len(invert_set)} = {100*w/len(invert_set):.1f}%")
    print()

    # ── 4. Entry timing ─────────────────────────────────────────────────────
    print("=" * 72)
    print("  SECTION 4: ENTRY → RESOLUTION TIMING (seconds)")
    print("=" * 72)

    delays = []
    for e, r, t in triples:
        try:
            et = parse_ts(e["ts"])
            tt = parse_ts(t["ts"])
            delay = (tt - et).total_seconds()
            delays.append(delay)
        except Exception:
            pass

    if delays:
        print(f"  Count  : {len(delays)}")
        print(f"  Mean   : {statistics.mean(delays):.1f}s")
        print(f"  Median : {statistics.median(delays):.1f}s")
        print(f"  Min    : {min(delays):.1f}s")
        print(f"  Max    : {max(delays):.1f}s")
        under_60  = sum(1 for d in delays if d < 60)
        s60_120   = sum(1 for d in delays if 60 <= d < 120)
        s120_300  = sum(1 for d in delays if 120 <= d < 300)
        over_300  = sum(1 for d in delays if d >= 300)
        print(f"  < 60s  : {under_60}")
        print(f"  60-120s: {s60_120}")
        print(f"  2-5min : {s120_300}")
        print(f"  >5min  : {over_300}")
    print()

    # ── 5. Signal strength: w4_mag_bps ──────────────────────────────────────
    print("=" * 72)
    print("  SECTION 5: SIGNAL STRENGTH — w4_mag_bps AT ENTRY")
    print("=" * 72)

    mags = [(e["w4_mag_bps"], e, r, t) for e, r, t in triples if "w4_mag_bps" in e]
    mag_vals = [m for m, *_ in mags]

    if mag_vals:
        print(f"  Count  : {len(mag_vals)}")
        print(f"  Mean   : {statistics.mean(mag_vals):.2f} bps")
        print(f"  Median : {statistics.median(mag_vals):.2f} bps")
        print(f"  Min    : {min(mag_vals):.2f} bps")
        print(f"  Max    : {max(mag_vals):.2f} bps")
        print(f"  Stdev  : {statistics.stdev(mag_vals):.2f} bps")
        gt5  = sum(1 for v in mag_vals if v > 5)
        gt10 = sum(1 for v in mag_vals if v > 10)
        gt20 = sum(1 for v in mag_vals if v > 20)
        print(f"  >  5bps: {gt5}  ({100*gt5/len(mag_vals):.1f}%)")
        print(f"  > 10bps: {gt10}  ({100*gt10/len(mag_vals):.1f}%)")
        print(f"  > 20bps: {gt20}  ({100*gt20/len(mag_vals):.1f}%)")
        print()

        print("  WR by w4_mag_bps bucket (corrected lean_dir):")
        print(f"  {'Bucket':<14} {'N':>5}  {'Wins':>5}  {'WR':>7}  {'Avg PnL':>9}")
        print("  " + "-" * 48)
        buckets = [
            ("0-5 bps",   lambda v: v <= 5),
            ("5-10 bps",  lambda v: 5 < v <= 10),
            ("10-20 bps", lambda v: 10 < v <= 20),
            (">20 bps",   lambda v: v > 20),
        ]
        for label, pred in buckets:
            sub = [(e, r, t) for m, e, r, t in mags if pred(m)]
            if sub:
                wins = sum(1 for e, r, t in sub if is_win(e["lean_dir"], t["result"]))
                avg_pnl = statistics.mean(t["pnl"] for _, __, t in sub)
                print(f"  {label:<14} {len(sub):>5}  {wins:>5}  {100*wins/len(sub):>6.1f}%  {avg_pnl:>9.3f}")
    print()

    # ── 6. Per-coin breakdown ────────────────────────────────────────────────
    print("=" * 72)
    print("  SECTION 6: PER-COIN WR (corrected lean_dir)")
    print("=" * 72)
    print(f"  {'Coin':<8} {'N':>5}  {'Wins':>5}  {'WR':>7}  {'Avg PnL':>9}")
    print("  " + "-" * 46)

    coin_groups = defaultdict(list)
    for e, r, t in triples:
        coin_groups[e["coin"]].append((e, r, t))

    for coin in sorted(coin_groups):
        sub = coin_groups[coin]
        wins = sum(1 for e, r, t in sub if is_win(e["lean_dir"], t["result"]))
        avg_pnl = statistics.mean(t["pnl"] for _, __, t in sub)
        print(f"  {coin:<8} {len(sub):>5}  {wins:>5}  {100*wins/len(sub):>6.1f}%  {avg_pnl:>9.3f}")
    print()

    # ── 7. Contrarian vs non-contrarian breakdown ────────────────────────────
    print("=" * 72)
    print("  SECTION 7: CONTRARIAN vs NON-CONTRARIAN (corrected lean_dir)")
    print("=" * 72)
    for label, pred in [("contrarian=True", lambda e: e.get("contrarian")),
                        ("contrarian=False", lambda e: not e.get("contrarian"))]:
        sub = [(e, r, t) for e, r, t in triples if pred(e)]
        if sub:
            wins = sum(1 for e, r, t in sub if is_win(e["lean_dir"], t["result"]))
            avg_pnl = statistics.mean(t["pnl"] for _, __, t in sub)
            print(f"  {label:<20} N={len(sub):3d}  WR={100*wins/len(sub):.1f}%  Avg PnL={avg_pnl:.3f}")
    print()

    # ── 8. VERDICT ───────────────────────────────────────────────────────────
    print("=" * 72)
    print("  VERDICT")
    print("=" * 72)
    print(f"  mm_w4_5m.jsonl contains BOTH w4_entry AND resolution events.")
    print(f"  Resolution events duplicate mm_trades_5m.jsonl.")
    print()
    print(f"  lean_dir INVERSION rate: {invert_count}/{n} = {100*invert_count/n:.1f}%")
    print(f"  (entry log lean_dir ≠ trade/resolution log lean_dir for these trades)")
    print()
    print(f"  Reported WR (trade-log lean_dir) : {100*wr_trade:.1f}%")
    print(f"  Corrected WR (entry-log lean_dir): {100*wr_entry:.1f}%")
    print(f"  Delta                            : {delta_pp:+.1f} pp")
    print()

    if invert_count == n:
        print("  *** CONFIRMED: ALL lean_dirs are inverted in resolution/trade log ***")
        print("  *** The 50.3% WR is calculated on WRONG lean_dir data            ***")
        print("  *** The true corrected WR is different — see above               ***")
    elif invert_count > n * 0.5:
        print("  *** MAJORITY inverted — WR metric is unreliable                  ***")
    elif invert_count == 0:
        print("  lean_dir is consistent between entry and trade logs — no bug here.")
    else:
        print(f"  PARTIAL inversion ({100*invert_count/n:.1f}%) — investigate root cause.")

    print()
    print("  WHAT IS lean_dir in the resolution/trade log?")
    print("  It appears to log lean_dir=UP always (or inverted from entry signal).")
    # Check if trade log always has UP
    up_count   = sum(1 for t in trades_file if t["lean_dir"].upper() == "UP")
    down_count = sum(1 for t in trades_file if t["lean_dir"].upper() == "DOWN")
    print(f"  mm_trades_5m.jsonl lean_dir=UP  : {up_count}")
    print(f"  mm_trades_5m.jsonl lean_dir=DOWN: {down_count}")
    print()


if __name__ == "__main__":
    main()
