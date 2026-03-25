#!/usr/bin/env python3
"""
Goldsky Subgraph Wallet Query
直接 query Polymarket on-chain data：
  - Trades（maker/taker 分類）
  - Positions（持倉 + avgPrice）
  - PnL（realizedPnl per position）

比 data-api.polymarket.com 更準：分得開 maker vs taker，有真實 entry cost。
Endpoints 係 public，唔使 API key。

Source: pmxt-dev/pmxt goldsky.ts
"""

import json
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict, Counter
from statistics import mean, median, stdev
from urllib.request import urlopen, Request

# ─── Goldsky endpoints (public, no auth) ───
TRADES_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/prod/gn"
POSITIONS_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/positions-subgraph/0.0.7/gn"
PNL_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn"

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ─── Target wallet ───
BLANKANDYELLOW = "0xdc1e9e397b479e11591b1f3025c8ecd641e6d9c8"


def graphql_query(url: str, query: str, variables: dict) -> dict | None:
    """Execute a GraphQL query, return data or None."""
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "AXC-Research/1.0",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        if result.get("errors"):
            print(f"  GraphQL errors: {result['errors']}")
            return None
        return result.get("data")
    except Exception as e:
        print(f"  Query failed: {e}")
        return None


def fetch_trades_paginated(address: str, role: str = "maker", max_pages: int = 20) -> list[dict]:
    """Fetch all trades for a wallet as maker or taker, paginated."""
    field = role  # "maker" or "taker"
    all_trades = []
    last_ts = "999999999999"

    for page in range(max_pages):
        query = f"""
        query GetTrades($address: Bytes!, $lastTs: String!) {{
            orderFilledEvents(
                where: {{ {field}: $address, timestamp_lt: $lastTs }}
                first: 1000
                orderBy: timestamp
                orderDirection: desc
            ) {{
                id
                timestamp
                maker
                taker
                makerAssetId
                takerAssetId
                makerAmountFilled
                takerAmountFilled
            }}
        }}
        """
        print(f"  {role} page {page}...", end=" ", flush=True)
        data = graphql_query(TRADES_URL, query, {
            "address": address.lower(),
            "lastTs": last_ts,
        })

        if not data:
            print("failed")
            break

        events = data.get("orderFilledEvents", [])
        print(f"{len(events)} trades")

        if not events:
            break

        all_trades.extend(events)
        last_ts = events[-1]["timestamp"]

        if len(events) < 1000:
            break

    return all_trades


def fetch_pnl(address: str) -> list[dict]:
    """Fetch PnL data (positions with avgPrice + realizedPnl)."""
    query = """
    query GetPnl($address: String!) {
        userPositions(
            where: { user: $address }
            first: 1000
            orderBy: id
            orderDirection: asc
        ) {
            tokenId
            amount
            avgPrice
            realizedPnl
        }
    }
    """
    print("  Fetching PnL...", end=" ", flush=True)
    data = graphql_query(PNL_URL, query, {"address": address.lower()})
    if not data:
        print("failed")
        return []
    positions = data.get("userPositions", [])
    print(f"{len(positions)} positions")
    return positions


def parse_trade(event: dict, address: str) -> dict:
    """Parse a raw Goldsky trade into useful fields."""
    addr = address.lower()
    is_maker = event.get("maker", "").lower() == addr
    maker_asset = int(event.get("makerAssetId", "0"))
    taker_asset = int(event.get("takerAssetId", "0"))

    # Asset ID = 0 means USDC side
    if is_maker:
        is_buying = maker_asset == 0  # maker gives USDC = buying shares
        if is_buying:
            usdc = float(event["makerAmountFilled"]) / 1e6
            shares = float(event["takerAmountFilled"]) / 1e6
        else:
            shares = float(event["makerAmountFilled"]) / 1e6
            usdc = float(event["takerAmountFilled"]) / 1e6
    else:
        is_buying = taker_asset == 0  # taker gives USDC = buying shares
        if is_buying:
            usdc = float(event["takerAmountFilled"]) / 1e6
            shares = float(event["makerAmountFilled"]) / 1e6
        else:
            shares = float(event["takerAmountFilled"]) / 1e6
            usdc = float(event["makerAmountFilled"]) / 1e6

    price = usdc / shares if shares > 0 else 0

    return {
        "id": event["id"],
        "timestamp": int(event["timestamp"]),
        "is_maker": is_maker,
        "is_buying": is_buying,
        "shares": shares,
        "usdc": usdc,
        "price": price,
        "asset_id": str(maker_asset if not is_maker else taker_asset),
    }


def section(title: str):
    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'═'*70}")


def analyze(address: str, name: str):
    print(f"{'═'*70}")
    print(f"  Goldsky Query: {name} ({address[:10]}...)")
    print(f"{'═'*70}")

    # ─── Fetch trades ───
    print("\n--- Fetching maker trades ---")
    maker_raw = fetch_trades_paginated(address, "maker")
    print("\n--- Fetching taker trades ---")
    taker_raw = fetch_trades_paginated(address, "taker")

    # Dedup by ID
    seen = set()
    all_raw = []
    for t in maker_raw + taker_raw:
        if t["id"] not in seen:
            seen.add(t["id"])
            all_raw.append(t)

    print(f"\n  Total unique trades: {len(all_raw)} (maker={len(maker_raw)}, taker={len(taker_raw)})")

    # Parse
    trades = [parse_trade(t, address) for t in all_raw]
    trades.sort(key=lambda t: t["timestamp"])

    # ─── Fetch PnL ───
    print("\n--- Fetching PnL ---")
    pnl_data = fetch_pnl(address)

    # ═══ Analysis ═══
    section("1. MAKER vs TAKER BREAKDOWN")
    maker_count = sum(1 for t in trades if t["is_maker"])
    taker_count = sum(1 for t in trades if not t["is_maker"])
    print(f"  Maker: {maker_count} ({maker_count/len(trades)*100:.1f}%)")
    print(f"  Taker: {taker_count} ({taker_count/len(trades)*100:.1f}%)")

    maker_vol = sum(t["usdc"] for t in trades if t["is_maker"])
    taker_vol = sum(t["usdc"] for t in trades if not t["is_maker"])
    print(f"  Maker volume: ${maker_vol:,.2f}")
    print(f"  Taker volume: ${taker_vol:,.2f}")

    maker_prices = [t["price"] for t in trades if t["is_maker"] and t["price"] > 0]
    taker_prices = [t["price"] for t in trades if not t["is_maker"] and t["price"] > 0]
    if maker_prices:
        print(f"  Maker avg price: {mean(maker_prices):.4f}")
    if taker_prices:
        print(f"  Taker avg price: {mean(taker_prices):.4f}")

    section("2. BUY vs SELL")
    buy_count = sum(1 for t in trades if t["is_buying"])
    sell_count = sum(1 for t in trades if not t["is_buying"])
    print(f"  Buy:  {buy_count} ({buy_count/len(trades)*100:.1f}%)")
    print(f"  Sell: {sell_count} ({sell_count/len(trades)*100:.1f}%)")

    section("3. MAKER+BUY / MAKER+SELL / TAKER+BUY / TAKER+SELL")
    combos = Counter()
    for t in trades:
        role = "maker" if t["is_maker"] else "taker"
        action = "buy" if t["is_buying"] else "sell"
        combos[f"{role}_{action}"] += 1
    for combo, count in combos.most_common():
        pct = count / len(trades) * 100
        bar = "█" * int(pct / 2)
        print(f"  {combo:<15}: {count:>6} ({pct:5.1f}%) {bar}")

    section("4. TIMING")
    if trades:
        ts_min = min(t["timestamp"] for t in trades)
        ts_max = max(t["timestamp"] for t in trades)
        span_h = (ts_max - ts_min) / 3600
        print(f"  From: {datetime.fromtimestamp(ts_min, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
        print(f"  To:   {datetime.fromtimestamp(ts_max, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
        print(f"  Span: {span_h:.1f} hours ({span_h/24:.1f} days)")
        print(f"  Trades/hour: {len(trades)/max(span_h, 0.01):.1f}")

    section("5. PRICE DISTRIBUTION (maker vs taker)")
    if maker_prices:
        print(f"  Maker: mean={mean(maker_prices):.4f}  median={median(maker_prices):.4f}  min={min(maker_prices):.4f}  max={max(maker_prices):.4f}")
    if taker_prices:
        print(f"  Taker: mean={mean(taker_prices):.4f}  median={median(taker_prices):.4f}  min={min(taker_prices):.4f}  max={max(taker_prices):.4f}")

    section("6. SIZE DISTRIBUTION")
    all_shares = [t["shares"] for t in trades if t["shares"] > 0]
    all_usdc = [t["usdc"] for t in trades if t["usdc"] > 0]
    if all_shares:
        print(f"  Shares: mean={mean(all_shares):.2f}  median={median(all_shares):.2f}")
    if all_usdc:
        print(f"  USDC:   mean=${mean(all_usdc):.2f}  median=${median(all_usdc):.2f}")

    section("7. PNL DATA (on-chain)")
    if pnl_data:
        total_realized = 0
        total_amount = 0
        positions_with_pnl = 0
        avg_prices = []

        for p in pnl_data:
            amt = float(p.get("amount", "0")) / 1e6
            avg_p = float(p.get("avgPrice", "0")) / 1e6
            realized = float(p.get("realizedPnl", "0")) / 1e6
            total_amount += amt
            total_realized += realized
            if avg_p > 0:
                avg_prices.append(avg_p)
            if realized != 0:
                positions_with_pnl += 1

        print(f"  Total positions: {len(pnl_data)}")
        print(f"  Positions with PnL: {positions_with_pnl}")
        print(f"  Total realized PnL: ${total_realized:,.2f}")
        print(f"  Total open amount: {total_amount:,.2f} shares")
        if avg_prices:
            print(f"  Avg entry price: mean={mean(avg_prices):.4f}  median={median(avg_prices):.4f}")

        # Top winners and losers
        pnl_list = []
        for p in pnl_data:
            realized = float(p.get("realizedPnl", "0")) / 1e6
            if realized != 0:
                pnl_list.append((p["tokenId"][:16] + "...", realized))

        if pnl_list:
            pnl_list.sort(key=lambda x: x[1], reverse=True)
            print(f"\n  Top 10 winners:")
            for token, pnl in pnl_list[:10]:
                print(f"    {token}: ${pnl:+,.2f}")
            print(f"\n  Top 10 losers:")
            for token, pnl in pnl_list[-10:]:
                print(f"    {token}: ${pnl:+,.2f}")

            # PnL distribution
            profits = [p for _, p in pnl_list if p > 0]
            losses = [p for _, p in pnl_list if p < 0]
            print(f"\n  Profitable positions: {len(profits)}")
            print(f"  Losing positions:     {len(losses)}")
            if profits:
                print(f"  Total profit:  ${sum(profits):,.2f}  (avg ${mean(profits):.2f})")
            if losses:
                print(f"  Total loss:    ${sum(losses):,.2f}  (avg ${mean(losses):.2f})")
    else:
        print("  No PnL data available")

    # ─── Save raw data ───
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = {
        "address": address,
        "name": name,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "trades_count": len(trades),
        "maker_count": maker_count,
        "taker_count": taker_count,
        "trades": trades[:500],  # save first 500 for inspection
        "pnl": pnl_data,
    }
    path = os.path.join(OUTPUT_DIR, f"goldsky_{name.lower()}.json")
    with open(path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Saved to: {path}")


def main():
    analyze(BLANKANDYELLOW, "blankandyellow")


if __name__ == "__main__":
    main()
