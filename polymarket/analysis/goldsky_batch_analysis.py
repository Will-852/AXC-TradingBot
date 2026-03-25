#!/usr/bin/env python3
"""
Goldsky Batch Wallet Analysis
用 on-chain data 分析所有已知錢包嘅真實 maker/taker + PnL。

Strategy:
  - PnL query: 1 request per wallet (fast)
  - Trades: 3 pages maker + 3 pages taker = 6 requests (enough for ratio)
  - Output: comparison table + per-wallet JSON
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from collections import Counter
from statistics import mean, median
from urllib.request import urlopen, Request

# ─── All known wallets ───
WALLETS = {
    # 14 wallets from classification
    "blankandyellow": "0xdc1e9e397b479e11591b1f3025c8ecd641e6d9c8",
    "kafwhsd": "0xfdb826a0fb4a90b4cc9049e408ea3ef1b73ae4c9",
    "likebot": "0x03c3b0236c5a01051381482e77f2210349073a1d",
    "stargate5": "0xb4d2499b6cabd0bb93672bb17c5ae47101759ee1",
    "xr9-PLM42": "0xa1303d0d0403549c93c9cdc2866def3b916ded0d",
    "mapleghost": "0x39636344f4906695016c13a78a8cd4ea705b2b0b",
    "VOID-PEPPER": "0xa84edaf1a562eabb463dc6cf4c3e9c407a5edbeb",
    "purple-lamp-tree": "0x6fdc687773d4ba8753ea406f4eb2a403051a953f",
    "MangoTrolley7": "0xed86741e5223f1b72945f6c4002168b2588e78c7",
    "Brundle": "0x76bc5994bf0a12d08a791b897d1fe1affea7205b",
    "blue-walnut": "0x4b188496d1b3da1716165380999afb9b314c725f",
    # Extra wallets with full addresses
    "BoneReader": "0xd84c2b6d65dc596f49c7b6aadd6d74ca91e407b9",
    "Anon": "0xe38b7a6553cbcac3bf6d9e22c83cdce092951fdc",
    "LampStore": "0x56bad0e7a00913c6e35c00dce3ec7f7cd6a311d7",
    "swisstony": "0x204f72f35326db932158cba6adff0b9a1da95e14",
    "Uncommon-Oat": "0xd0d6053c3c37e727402d84c14069780d360993aa",
    "Unlawful-Shear": "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82",
    "Awful-Alfalfa": "0x1f0ebc543b2d411f66947041625c0aa1ce61cf86",
    "Decent-Dune": "0x818f214c7f3e479cce1d964d53fe3db7297558cb",
    "Moral-Roof": "0x576b0696fd5a9225d66fd9500fd98f5be10b0cab",
    "Female-Billing": "0xd1ebe815f921b3ebbd8d9e0a4192c6ab18360f5c",
}

TRADES_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/prod/gn"
PNL_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def gql(url: str, query: str, variables: dict) -> dict | None:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "AXC-Research/1.0",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        if result.get("errors"):
            return None
        return result.get("data")
    except Exception:
        return None


def fetch_trades_sample(address: str, role: str, pages: int = 3) -> list[dict]:
    """Fetch limited trades for ratio estimation."""
    all_trades = []
    last_ts = "999999999999"
    for _ in range(pages):
        query = f"""
        query T($a: Bytes!, $ts: String!) {{
            orderFilledEvents(
                where: {{ {role}: $a, timestamp_lt: $ts }}
                first: 1000
                orderBy: timestamp
                orderDirection: desc
            ) {{
                id timestamp maker taker
                makerAssetId takerAssetId
                makerAmountFilled takerAmountFilled
            }}
        }}
        """
        data = gql(TRADES_URL, query, {"a": address.lower(), "ts": last_ts})
        if not data:
            break
        events = data.get("orderFilledEvents", [])
        if not events:
            break
        all_trades.extend(events)
        last_ts = events[-1]["timestamp"]
        if len(events) < 1000:
            break
        time.sleep(0.2)
    return all_trades


def fetch_pnl_all(address: str) -> list[dict]:
    """Fetch all PnL positions (paginated by ID)."""
    all_positions = []
    last_id = ""
    for _ in range(10):  # max 10K positions
        query = """
        query P($a: String!, $lid: String!) {
            userPositions(
                where: { user: $a, id_gt: $lid }
                first: 1000
                orderBy: id
                orderDirection: asc
            ) {
                id tokenId amount avgPrice realizedPnl
            }
        }
        """
        data = gql(PNL_URL, query, {"a": address.lower(), "lid": last_id})
        if not data:
            break
        positions = data.get("userPositions", [])
        if not positions:
            break
        all_positions.extend(positions)
        last_id = positions[-1]["id"]
        if len(positions) < 1000:
            break
        time.sleep(0.2)
    return all_positions


def parse_trade(event: dict, address: str) -> dict:
    addr = address.lower()
    is_maker = event.get("maker", "").lower() == addr
    maker_asset = int(event.get("makerAssetId", "0"))

    if is_maker:
        is_buying = maker_asset == 0
        if is_buying:
            usdc = float(event["makerAmountFilled"]) / 1e6
            shares = float(event["takerAmountFilled"]) / 1e6
        else:
            shares = float(event["makerAmountFilled"]) / 1e6
            usdc = float(event["takerAmountFilled"]) / 1e6
    else:
        taker_asset = int(event.get("takerAssetId", "0"))
        is_buying = taker_asset == 0
        if is_buying:
            usdc = float(event["takerAmountFilled"]) / 1e6
            shares = float(event["makerAmountFilled"]) / 1e6
        else:
            shares = float(event["takerAmountFilled"]) / 1e6
            usdc = float(event["makerAmountFilled"]) / 1e6

    price = usdc / shares if shares > 0 else 0
    return {
        "is_maker": is_maker,
        "is_buying": is_buying,
        "shares": shares,
        "usdc": usdc,
        "price": price,
        "timestamp": int(event["timestamp"]),
    }


def analyze_wallet(name: str, address: str) -> dict:
    print(f"  {name:<20}", end="", flush=True)

    # ── Trades sample ──
    maker_raw = fetch_trades_sample(address, "maker", pages=3)
    taker_raw = fetch_trades_sample(address, "taker", pages=3)

    seen = set()
    all_raw = []
    for t in maker_raw + taker_raw:
        if t["id"] not in seen:
            seen.add(t["id"])
            all_raw.append(t)

    trades = [parse_trade(t, address) for t in all_raw]

    # ── PnL ──
    pnl_data = fetch_pnl_all(address)

    # ── Compute stats ──
    total = len(trades)
    if total == 0:
        print(f" → no trades")
        return {"name": name, "address": address, "error": "no trades"}

    maker_n = sum(1 for t in trades if t["is_maker"])
    taker_n = total - maker_n
    buy_n = sum(1 for t in trades if t["is_buying"])
    sell_n = total - buy_n

    combos = Counter()
    for t in trades:
        role = "M" if t["is_maker"] else "T"
        action = "B" if t["is_buying"] else "S"
        combos[f"{role}{action}"] += 1

    maker_prices = [t["price"] for t in trades if t["is_maker"] and t["price"] > 0]
    taker_prices = [t["price"] for t in trades if not t["is_maker"] and t["price"] > 0]
    maker_vol = sum(t["usdc"] for t in trades if t["is_maker"])
    taker_vol = sum(t["usdc"] for t in trades if not t["is_maker"])

    # Time span
    timestamps = [t["timestamp"] for t in trades]
    span_h = (max(timestamps) - min(timestamps)) / 3600 if timestamps else 0

    # PnL
    total_realized = 0
    win_pos = 0
    lose_pos = 0
    total_profit = 0
    total_loss = 0
    for p in pnl_data:
        realized = float(p.get("realizedPnl", "0")) / 1e6
        total_realized += realized
        if realized > 0:
            win_pos += 1
            total_profit += realized
        elif realized < 0:
            lose_pos += 1
            total_loss += realized

    wr = win_pos / (win_pos + lose_pos) * 100 if (win_pos + lose_pos) > 0 else 0

    result = {
        "name": name,
        "address": address,
        "trades_sampled": total,
        "maker_raw": len(maker_raw),
        "taker_raw": len(taker_raw),
        "maker_pct": round(maker_n / total * 100, 1),
        "taker_pct": round(taker_n / total * 100, 1),
        "buy_pct": round(buy_n / total * 100, 1),
        "sell_pct": round(sell_n / total * 100, 1),
        "combos": {k: v for k, v in combos.most_common()},
        "maker_avg_price": round(mean(maker_prices), 4) if maker_prices else 0,
        "taker_avg_price": round(mean(taker_prices), 4) if taker_prices else 0,
        "maker_vol": round(maker_vol, 2),
        "taker_vol": round(taker_vol, 2),
        "span_hours": round(span_h, 1),
        "pnl_positions": len(pnl_data),
        "realized_pnl": round(total_realized, 2),
        "win_positions": win_pos,
        "lose_positions": lose_pos,
        "position_wr": round(wr, 1),
        "total_profit": round(total_profit, 2),
        "total_loss": round(total_loss, 2),
    }

    # Dominant combo
    top_combo = combos.most_common(1)[0] if combos else ("?", 0)
    top_pct = top_combo[1] / total * 100

    print(f" → {total:>5} trades | Maker {result['maker_pct']:>5.1f}% | Buy {result['buy_pct']:>5.1f}% | Top: {top_combo[0]} {top_pct:.0f}% | PnL ${total_realized:>+10,.2f} | WR {wr:.0f}%")
    return result


def main():
    print(f"{'═'*90}")
    print(f"  Goldsky Batch Analysis — {len(WALLETS)} wallets")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'═'*90}\n")

    results = {}
    for name, address in WALLETS.items():
        results[name] = analyze_wallet(name, address)
        time.sleep(0.3)

    # ═══ Comparison Table ═══
    valid = {k: v for k, v in results.items() if "error" not in v}

    print(f"\n{'═'*130}")
    print(f"  COMPARISON TABLE — sorted by Realized PnL")
    print(f"{'═'*130}")
    print(f"  {'Wallet':<20} {'Trades':>7} {'Maker%':>7} {'Taker%':>7} {'Buy%':>6} {'Sell%':>6} {'TopCombo':>9} {'MkrAvgP':>8} {'TkrAvgP':>8} {'Positions':>10} {'PnL':>12} {'WR':>5} {'Profit':>10} {'Loss':>10}")
    print(f"  {'-'*20} {'-'*7} {'-'*7} {'-'*7} {'-'*6} {'-'*6} {'-'*9} {'-'*8} {'-'*8} {'-'*10} {'-'*12} {'-'*5} {'-'*10} {'-'*10}")

    for name, r in sorted(valid.items(), key=lambda x: x[1]["realized_pnl"], reverse=True):
        top = max(r["combos"].items(), key=lambda x: x[1]) if r["combos"] else ("?", 0)
        top_pct = top[1] / r["trades_sampled"] * 100
        top_str = f"{top[0]}{top_pct:.0f}%"
        print(f"  {name:<20} {r['trades_sampled']:>7} {r['maker_pct']:>6.1f}% {r['taker_pct']:>6.1f}% {r['buy_pct']:>5.1f}% {r['sell_pct']:>5.1f}% {top_str:>9} {r['maker_avg_price']:>8.4f} {r['taker_avg_price']:>8.4f} {r['pnl_positions']:>10} ${r['realized_pnl']:>+10,.2f} {r['position_wr']:>4.0f}% ${r['total_profit']:>8,.0f} ${r['total_loss']:>8,.0f}")

    # ═══ Strategy Classification ═══
    print(f"\n{'═'*90}")
    print(f"  STRATEGY CLASSIFICATION (based on on-chain data)")
    print(f"{'═'*90}")

    for name, r in sorted(valid.items(), key=lambda x: x[1]["realized_pnl"], reverse=True):
        maker_pct = r["maker_pct"]
        buy_pct = r["buy_pct"]
        top_combo = max(r["combos"].items(), key=lambda x: x[1])[0] if r["combos"] else "?"

        # Classify
        if maker_pct > 70:
            if buy_pct > 80:
                strategy = "MAKER BUY (arb/accumulate)"
            elif buy_pct < 20:
                strategy = "MAKER SELL (liquidity provider)"
            else:
                strategy = "MAKER BOTH-SIDES (MM)"
        elif maker_pct < 30:
            if buy_pct > 70:
                strategy = "TAKER BUY (aggressive directional)"
            elif buy_pct < 30:
                strategy = "TAKER SELL (liquidating/exit)"
            else:
                strategy = "TAKER MIXED (spread capture)"
        else:
            if "MB" in r["combos"] and "TS" in r["combos"]:
                mb = r["combos"].get("MB", 0)
                ts = r["combos"].get("TS", 0)
                if mb > 0 and ts > 0:
                    strategy = "MAKER-BUY → TAKER-SELL (spread capture MM)"
                else:
                    strategy = "HYBRID"
            else:
                strategy = "HYBRID"

        print(f"  {name:<20} → {strategy}")
        print(f"    {' '.join(f'{k}={v}' for k,v in r['combos'].items())}")

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "goldsky_batch_results.json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved to: {path}")


if __name__ == "__main__":
    main()
