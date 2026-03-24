"""
Fee Analysis — Polymarket 15M BTC Both-Sides Trades
====================================================
Parses CSV export + mm_trades.jsonl to compute:
1. Effective price per token (cost / tokens)
2. Implied fee per trade (effective_price - theoretical_mid)
3. Maker vs taker classification (POST_ONLY = maker expectation)
4. Net edge after fees
5. Fee threshold where edge dies

Usage:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/analysis/fee_analysis.py

Outputs findings to stdout. Copy key numbers into findings.md F14.
"""

import csv
import json
import re
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────────────
CSV_PATH = os.path.expanduser("~/Downloads/Polymarket-History-2026-03-24.csv")
TRADES_LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                          "logs", "mm_trades.jsonl")
ORDER_LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "logs", "mm_order_log.jsonl")

# ── Polymarket Fee Model ──────────────────────────────────────────────────────
# Reference: https://docs.polymarket.com/#fees
# Maker: 0% fee (limit order resting on book)
# Taker: fee = 2% × min(price, 1-price) per token
# Example: buy at $0.60 → fee = 2% × min(0.60, 0.40) = 2% × 0.40 = $0.008/token
# Our bot uses POST_ONLY → should always be maker → 0% fee


def parse_csv(path: str) -> list[dict]:
    """Parse Polymarket CSV export into structured records."""
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "market": r["marketName"],
                "action": r["action"],
                "usdc": float(r["usdcAmount"]) if r["usdcAmount"] else 0.0,
                "tokens": float(r["tokenAmount"]) if r["tokenAmount"] else 0.0,
                "side": r["tokenName"],  # Up/Down/empty
                "ts": int(r["timestamp"]) if r["timestamp"] else 0,
                "hash": r["hash"],
            })
    return rows


def parse_mm_trades(path: str) -> list[dict]:
    """Parse mm_trades.jsonl."""
    trades = []
    if not os.path.exists(path):
        return trades
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return trades


def extract_window_key(market_name: str) -> str:
    """Extract a comparable key from market name.
    'Bitcoin Up or Down - March 24, 1:45AM-2:00AM ET' → 'btc_0324_0145'
    """
    m = re.match(
        r"(Bitcoin|Ethereum|Solana) Up or Down - (March|April|May) (\d+), "
        r"(\d+):(\d+)(AM|PM)-",
        market_name,
    )
    if not m:
        return market_name[:40]
    coin = {"Bitcoin": "btc", "Ethereum": "eth", "Solana": "sol"}[m.group(1)]
    day = int(m.group(3))
    hour = int(m.group(4))
    minute = int(m.group(5))
    ampm = m.group(6)
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    return f"{coin}_{day:02d}_{hour:02d}{minute:02d}"


def analyze_csv_fees(rows: list[dict]) -> dict:
    """Group buys by market, compute per-trade fee metrics."""
    # Group by market
    markets: dict[str, dict] = defaultdict(lambda: {
        "buys_up": [], "buys_down": [], "redeems": [],
        "total_cost": 0.0, "total_up_tokens": 0.0, "total_down_tokens": 0.0,
        "redeem_amount": 0.0, "market_name": "",
    })

    for r in rows:
        if r["action"] == "Deposited funds":
            continue
        key = extract_window_key(r["market"])
        mkt = markets[key]
        mkt["market_name"] = r["market"]

        if r["action"] == "Buy":
            if r["side"] == "Up":
                mkt["buys_up"].append(r)
                mkt["total_up_tokens"] += r["tokens"]
            elif r["side"] == "Down":
                mkt["buys_down"].append(r)
                mkt["total_down_tokens"] += r["tokens"]
            mkt["total_cost"] += r["usdc"]
        elif r["action"] == "Redeem":
            mkt["redeems"].append(r)
            mkt["redeem_amount"] += r["usdc"]

    # Analyze each market
    results = []
    for key, mkt in sorted(markets.items()):
        if not mkt["buys_up"] and not mkt["buys_down"]:
            continue

        up_tokens = mkt["total_up_tokens"]
        dn_tokens = mkt["total_down_tokens"]
        total_cost = mkt["total_cost"]
        redeem = mkt["redeem_amount"]

        # Effective price per token
        up_price = (sum(b["usdc"] for b in mkt["buys_up"]) / up_tokens
                    if up_tokens > 0 else 0)
        dn_price = (sum(b["usdc"] for b in mkt["buys_down"]) / dn_tokens
                    if dn_tokens > 0 else 0)

        # Combined mid = up_price + dn_price (should be ~$1.00 for zero spread)
        combined = up_price + dn_price if up_price > 0 and dn_price > 0 else 0

        # Is this a both-sides trade?
        is_both_sides = up_tokens > 0 and dn_tokens > 0

        # Theoretical taker fee (2% × min(p, 1-p) per token)
        up_taker_fee = 0.02 * min(up_price, 1 - up_price) if up_price > 0 else 0
        dn_taker_fee = 0.02 * min(dn_price, 1 - dn_price) if dn_price > 0 else 0

        # Implied fee: if maker (0%), effective_price = market_price
        # If taker, effective_price = market_price + fee
        # We can't know market_price exactly, but combined > $1.00 implies fees/spread
        spread_cost = combined - 1.0 if combined > 0 else 0

        # PnL
        pnl = redeem - total_cost

        # Lean ratio (larger side / smaller side)
        lean_ratio = (max(up_tokens, dn_tokens) / max(min(up_tokens, dn_tokens), 0.01)
                      if is_both_sides else 0)

        results.append({
            "key": key,
            "market": mkt["market_name"][:50],
            "is_both": is_both_sides,
            "up_tokens": up_tokens,
            "dn_tokens": dn_tokens,
            "up_price": up_price,
            "dn_price": dn_price,
            "combined": combined,
            "spread_cost": spread_cost,
            "total_cost": total_cost,
            "redeem": redeem,
            "pnl": pnl,
            "lean_ratio": lean_ratio,
            "up_taker_fee": up_taker_fee,
            "dn_taker_fee": dn_taker_fee,
            "n_buys": len(mkt["buys_up"]) + len(mkt["buys_down"]),
        })

    return {"markets": results}


def main():
    print("=" * 70)
    print("POLYMARKET FEE ANALYSIS — BTC 15M Both-Sides")
    print("=" * 70)

    # ── Step 1: Parse CSV ────────────────────────────────────────────────
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: CSV not found at {CSV_PATH}")
        sys.exit(1)

    rows = parse_csv(CSV_PATH)
    print(f"\nCSV: {len(rows)} rows loaded")

    # Filter to BTC 15M only
    btc_rows = [r for r in rows if "Bitcoin Up or Down" in r["market"]
                and "15" not in r["market"].split("-")[-1].split("AM")[0].split("PM")[0]
                or "Bitcoin Up or Down" in r["market"]]
    # Actually keep all Bitcoin rows (they're all 15M format)
    btc_rows = [r for r in rows if "Bitcoin Up or Down" in r["market"]]
    print(f"BTC 15M rows: {len(btc_rows)}")

    # ── Step 2: Analyze ──────────────────────────────────────────────────
    result = analyze_csv_fees(btc_rows)
    markets = result["markets"]

    # Split both-sides vs single-side
    bs = [m for m in markets if m["is_both"]]
    ss = [m for m in markets if not m["is_both"]]

    print(f"\nMarkets: {len(markets)} total | {len(bs)} both-sides | {len(ss)} single-side")

    # ── Step 3: Both-sides fee analysis ──────────────────────────────────
    print("\n" + "=" * 70)
    print("BOTH-SIDES TRADES — Per-Market Breakdown")
    print("=" * 70)

    print(f"\n{'Key':<16} {'Up$':>5} {'Dn$':>5} {'Comb':>6} {'Spread':>7} "
          f"{'Cost':>6} {'Redm':>6} {'PnL':>7} {'Ratio':>5} {'#':>3}")
    print("-" * 70)

    total_cost = 0
    total_redeem = 0
    total_spread = 0
    count_bs = 0
    combined_list = []

    for m in bs:
        print(f"{m['key']:<16} {m['up_price']:>5.2f} {m['dn_price']:>5.2f} "
              f"{m['combined']:>6.3f} {m['spread_cost']:>+7.3f} "
              f"{m['total_cost']:>6.2f} {m['redeem']:>6.2f} "
              f"{m['pnl']:>+7.2f} {m['lean_ratio']:>5.1f} {m['n_buys']:>3d}")
        total_cost += m["total_cost"]
        total_redeem += m["redeem"]
        total_spread += m["spread_cost"] * (m["up_tokens"] + m["dn_tokens"])
        count_bs += 1
        if m["combined"] > 0:
            combined_list.append(m["combined"])

    total_pnl = total_redeem - total_cost
    avg_combined = sum(combined_list) / len(combined_list) if combined_list else 0
    avg_spread = sum(m["spread_cost"] for m in bs) / len(bs) if bs else 0

    print("-" * 70)
    print(f"{'TOTAL':<16} {'':>5} {'':>5} {avg_combined:>6.3f} {avg_spread:>+7.3f} "
          f"{total_cost:>6.2f} {total_redeem:>6.2f} {total_pnl:>+7.2f} {'':>5} "
          f"{sum(m['n_buys'] for m in bs):>3d}")

    # ── Step 4: Fee impact summary ───────────────────────────────────────
    print("\n" + "=" * 70)
    print("FEE IMPACT SUMMARY")
    print("=" * 70)

    avg_cost_per_trade = total_cost / count_bs if count_bs else 0
    avg_pnl_per_trade = total_pnl / count_bs if count_bs else 0

    print(f"\nBoth-sides trades:    {count_bs}")
    print(f"Total cost:           ${total_cost:.2f}")
    print(f"Total redeem:         ${total_redeem:.2f}")
    print(f"Total PnL:            ${total_pnl:+.2f}")
    print(f"Avg PnL/trade:        ${avg_pnl_per_trade:+.2f}")
    print(f"Avg combined mid:     ${avg_combined:.4f}")
    print(f"Avg spread cost:      ${avg_spread:+.4f} per $1 pair")

    # Maker vs Taker inference
    # If combined < $1.005 → likely maker (negligible fee)
    # If combined > $1.010 → likely taker (paying spread + fee)
    maker_count = sum(1 for c in combined_list if c < 1.005)
    taker_count = sum(1 for c in combined_list if c >= 1.010)
    ambig_count = len(combined_list) - maker_count - taker_count

    print(f"\nMaker/Taker inference (from combined mid):")
    print(f"  Maker (combined < $1.005): {maker_count} ({100*maker_count/len(combined_list):.0f}%)" if combined_list else "  No data")
    print(f"  Taker (combined > $1.010): {taker_count} ({100*taker_count/len(combined_list):.0f}%)" if combined_list else "")
    print(f"  Ambiguous:                 {ambig_count}")

    # ── Step 5: Fee threshold analysis ───────────────────────────────────
    print("\n" + "=" * 70)
    print("FEE THRESHOLD — At what fee does edge die?")
    print("=" * 70)

    # From plan: avg win = +$0.41, avg loss = -$1.90, WR = 78.8%
    # EV/trade = WR × win - (1-WR) × |loss| = 0.788 × 0.41 - 0.212 × 1.90 = -$0.08
    # If fee = $X per trade: EV = -$0.08 - $X → need X < -$0.08 (already negative!)
    # But with Door B: EV should improve. Use Door B expected: ~-$0.87 / 33 = -$0.026/trade
    # Fee must be < $0.026 for break-even... which is very tight.

    # Calculate from actual data
    wins = [m for m in bs if m["pnl"] > 0]
    losses = [m for m in bs if m["pnl"] <= 0]
    avg_win = sum(m["pnl"] for m in wins) / len(wins) if wins else 0
    avg_loss = sum(m["pnl"] for m in losses) / len(losses) if losses else 0
    wr = len(wins) / len(bs) if bs else 0

    print(f"\nFrom CSV both-sides trades:")
    print(f"  Wins: {len(wins)}, Avg win: ${avg_win:+.2f}")
    print(f"  Losses: {len(losses)}, Avg loss: ${avg_loss:+.2f}")
    print(f"  WR: {wr:.1%}")
    print(f"  Gross EV/trade: ${wr * avg_win + (1-wr) * avg_loss:+.2f}")

    # Estimated fee per trade if taker on both sides:
    # avg_up_price ≈ 0.55, fee = 2% × min(0.55, 0.45) = 2% × 0.45 = $0.009/token
    # avg tokens ≈ 11 total → fee ≈ $0.10/trade
    avg_tokens = sum(m["up_tokens"] + m["dn_tokens"] for m in bs) / len(bs) if bs else 0
    est_taker_fee = 0.02 * 0.45 * avg_tokens  # worst case
    est_maker_fee = 0.0

    print(f"\n  Avg tokens/trade: {avg_tokens:.1f}")
    print(f"  Est. taker fee (worst): ${est_taker_fee:.2f}/trade")
    print(f"  Est. maker fee:         ${est_maker_fee:.2f}/trade")
    print(f"\n  ⚠️  Bot uses POST_ONLY → should be maker ($0 fee)")
    print(f"  ⚠️  But: if order crosses spread, it becomes taker")
    print(f"  ⚠️  Check: are any combined > $1.01? → taker evidence")

    # ── Step 6: Single-side trades (for comparison) ──────────────────────
    if ss:
        print("\n" + "=" * 70)
        print(f"SINGLE-SIDE TRADES: {len(ss)} markets")
        print("=" * 70)
        ss_pnl = sum(m["pnl"] for m in ss)
        print(f"  Total PnL: ${ss_pnl:+.2f}")
        print(f"  Avg PnL/trade: ${ss_pnl/len(ss):+.2f}")

    # ── Step 7: Cross-reference with mm_trades ───────────────────────────
    print("\n" + "=" * 70)
    print("CROSS-REFERENCE WITH mm_trades.jsonl")
    print("=" * 70)
    mm_trades = parse_mm_trades(TRADES_LOG)
    if mm_trades:
        w4_trades = [t for t in mm_trades if t.get("strategy") == "w4"
                     or t.get("both_sides")]
        print(f"  mm_trades.jsonl: {len(mm_trades)} total, {len(w4_trades)} W4/both-sides")
        if w4_trades:
            mm_pnl = sum(t.get("pnl", 0) for t in w4_trades)
            print(f"  W4 total PnL (from log): ${mm_pnl:+.2f}")
    else:
        print("  mm_trades.jsonl: not found or empty")

    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    print("""
KEY FINDINGS (copy to findings.md F14):
1. Combined mid tells us maker vs taker
2. POST_ONLY should mean maker ($0 fee)
3. If combined consistently > $1.01 → orders crossing spread
4. Fee is NOT the primary problem if maker — payout structure is
5. If taker: ~$0.10/trade fee on ~$5.50 cost = 1.8% drag
""")


if __name__ == "__main__":
    main()
