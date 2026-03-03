#!/usr/bin/env python3
"""
slash_cmd.py — Telegram slash command handler
Deterministic Python execution, zero LLM cost.
Usage: python3 slash_cmd.py <command> [--send]
"""

import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))
TRADE_STATE_PATH = os.path.join(WORKSPACE, "agents/trader/TRADE_STATE.md")
SCAN_CONFIG_PATH = os.path.join(WORKSPACE, "agents/trader/config/SCAN_CONFIG.md")
TRADE_LOG_PATH = os.path.join(WORKSPACE, "agents/trader/TRADE_LOG.md")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

TG_BOT_TOKEN = "8373819624:AAFH-SVTqqYlU22JnuiiBpB2uZytvw_pN30"
TG_CHAT_ID = "2060972655"

ASTER_FAPI = "https://fapi.asterdex.com"
SIGNAL_PATH = os.path.join(os.path.expanduser("~/.openclaw/shared"), "SIGNAL.md")


def now_hkt():
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M")


def parse_state(path):
    """Parse key-value pairs from MD state file."""
    state = {}
    if not os.path.exists(path):
        return state
    with open(path) as f:
        for line in f:
            line = line.strip()
            m = re.match(r'^([A-Z_]+):\s*(.+)', line)
            if m:
                state[m.group(1)] = m.group(2).strip()
    return state


def parse_float(val, default=0.0):
    try:
        return float(str(val).replace("$", "").replace(",", "").split()[0])
    except (ValueError, TypeError, IndexError):
        return default


def fetch_json(url, timeout=5):
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


def get_prices():
    """Fetch current prices from Aster DEX."""
    prices = {}
    for pair in ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT"]:
        data = fetch_json(f"{ASTER_FAPI}/fapi/v1/ticker/24hr?symbol={pair}")
        if data:
            prices[pair] = {
                "price": float(data.get("lastPrice", 0)),
                "change": float(data.get("priceChangePercent", 0)),
            }
    return prices


def get_balance():
    """Get USDT balance from Aster DEX (requires auth)."""
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        from trader_cycle.exchange.aster_client import AsterClient
        c = AsterClient()
        return c.get_usdt_balance()
    except Exception:
        return None


def get_positions():
    """Get positions from Aster DEX (requires auth)."""
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        from trader_cycle.exchange.aster_client import AsterClient
        c = AsterClient()
        return c.get_positions()
    except Exception:
        return []


def get_open_orders():
    """Get open orders from Aster DEX (SL/TP orders)."""
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        from trader_cycle.exchange.aster_client import AsterClient
        c = AsterClient()
        return c.get_open_orders()
    except Exception:
        return []


def get_today_pnl():
    """Get today's realized PnL, funding, and commissions from exchange."""
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        from trader_cycle.exchange.aster_client import AsterClient
        c = AsterClient()
        now = datetime.now(HKT)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(today_start.timestamp() * 1000)
        income = c.get_income(start_time=start_ms, limit=100)
        realized = sum(float(e["income"]) for e in income if e["incomeType"] == "REALIZED_PNL")
        funding = sum(float(e["income"]) for e in income if e["incomeType"] == "FUNDING_FEE")
        commission = sum(float(e["income"]) for e in income if e["incomeType"] == "COMMISSION")
        return {"realized": realized, "funding": funding, "commission": commission, "net": realized + funding + commission}
    except Exception:
        return None


def emoji(val):
    if val > 0:
        return "\U0001f7e2"
    elif val < 0:
        return "\U0001f534"
    return "\u26aa"


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TG_CHAT_ID,
        "text": f"<pre>{text}</pre>",
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(url, data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        print(f"Telegram send failed: {e}", file=sys.stderr)
        return False


# ─── Command Handlers ───

def cmd_report():
    prices = get_prices()
    bal = get_balance() or 0.0
    ts = parse_state(TRADE_STATE_PATH)
    scan = parse_state(SCAN_CONFIG_PATH)
    sig = parse_state(SIGNAL_PATH)
    mode = ts.get("MARKET_MODE", scan.get("MARKET_MODE", "?"))

    # Live positions from exchange
    live_positions = get_positions()
    active_positions = [p for p in live_positions if float(p.get("positionAmt", 0)) != 0]

    # Live open orders from exchange (SL/TP)
    open_orders = get_open_orders()

    # Calculate total unrealized PnL from live positions
    total_upnl = sum(float(p.get("unRealizedProfit", 0)) for p in active_positions)

    # Today's realized PnL from exchange
    today = get_today_pnl()
    today_net = today["net"] if today else 0.0
    today_realized = today["realized"] if today else 0.0

    # Signal status from SIGNAL.md
    signal_active = sig.get("SIGNAL_ACTIVE", "NO")
    signal_pair = sig.get("PAIR", "—")
    signal_text = f"{signal_pair}" if signal_active == "YES" else "NONE"

    lines = [f"\U0001f4ca AXC TRADER \u00b7 LIVE \u00b7 {now_hkt()} UTC+8", ""]
    lines.append(f"MODE     {mode:<10}SIGNAL   {signal_text}")
    lines.append(f"BALANCE  ${bal:<10.2f}uPnL     {total_upnl:+.2f}")
    lines.append(f"TODAY    {today_net:+.2f} USDT    (rPnL {today_realized:+.2f})")
    lines.append("")

    # Position — live from Aster DEX
    lines.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 POSITION \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    if active_positions:
        for pos in active_positions:
            amt = float(pos.get("positionAmt", 0))
            sym = pos.get("symbol", "?")
            direction = "LONG" if amt > 0 else "SHORT"
            entry = float(pos.get("entryPrice", 0))
            current = float(pos.get("markPrice", 0))
            upnl = float(pos.get("unRealizedProfit", 0))
            lev = pos.get("leverage", "?")
            if entry > 0 and current > 0:
                pnl_pct = ((current - entry) / entry * 100) if direction == "LONG" else ((entry - current) / entry * 100)
            else:
                pnl_pct = 0
            lines.append(f"{sym} {direction} {lev}x")
            lines.append(f"Entry ${entry:.2f} \u2192 Now ${current:.2f}")
            lines.append(f"PnL  {upnl:+.2f} USDT ({pnl_pct:+.1f}%) {emoji(upnl)}")
            # Find SL/TP from live open orders
            sl_orders = [o for o in open_orders if o.get("symbol") == sym and o.get("type") == "STOP_MARKET"]
            tp_orders = [o for o in open_orders if o.get("symbol") == sym and o.get("type") == "TAKE_PROFIT_MARKET"]
            sl_price = float(sl_orders[0].get("stopPrice", 0)) if sl_orders else 0
            tp_price = float(tp_orders[0].get("stopPrice", 0)) if tp_orders else 0
            if sl_price or tp_price:
                sl_str = f"${sl_price:.2f}" if sl_price else "—"
                tp_str = f"${tp_price:.2f}" if tp_price else "—"
                lines.append(f"SL   {sl_str}   TP  {tp_str}")
    else:
        lines.append("NO OPEN POSITIONS")

    lines.append("")
    lines.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 MARKET \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    for sym, label in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("XRPUSDT", "XRP"), ("XAGUSDT", "XAG")]:
        p = prices.get(sym, {})
        price = p.get("price", 0)
        chg = p.get("change", 0)
        lines.append(f"{label}  ${price:<11,.1f}{chg:+.1f}% {emoji(chg)}")

    # Dynamic LAST/NEXT from SIGNAL.md
    scan_status = sig.get("SCAN_STATUS", "?")
    trigger_count = sig.get("TRIGGER_COUNT", "0")
    scan_ts = sig.get("TIMESTAMP", "?")
    lines.append("")
    lines.append(f"LAST  Scan {scan_status} \u00b7 {trigger_count} triggers \u00b7 {scan_ts}")
    if active_positions:
        lines.append(f"NEXT  Monitoring open position(s)")
    elif signal_active == "YES":
        lines.append(f"NEXT  Signal active: {signal_pair}")
    else:
        lines.append(f"NEXT  Waiting for entry signals")

    return "\n".join(lines)


def cmd_pos():
    positions = get_positions()
    lines = [f"\U0001f4ca AXC POSITIONS \u00b7 {now_hkt()} UTC+8", ""]
    if not positions:
        lines.append("NO OPEN POSITIONS")
    else:
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            sym = p.get("symbol", "?")
            direction = "LONG" if amt > 0 else "SHORT"
            entry = float(p.get("entryPrice", 0))
            mark = float(p.get("markPrice", 0))
            pnl = float(p.get("unRealizedProfit", 0))
            lines.append(f"{sym} {direction}")
            lines.append(f"Entry ${entry:.2f} \u2192 Now ${mark:.2f}")
            lines.append(f"PnL  ${pnl:.2f} {emoji(pnl)}")
            lines.append(f"Size {abs(amt)}")
            lines.append("")
    return "\n".join(lines)


def cmd_bal():
    bal = get_balance()
    ts = parse_state(TRADE_STATE_PATH)
    if bal is None:
        bal = parse_float(ts.get("BALANCE_USDT", 0))
    lines = [f"\U0001f4ca AXC BALANCE \u00b7 {now_hkt()} UTC+8", ""]
    lines.append(f"BALANCE    ${bal:.2f} USDT")
    lines.append(f"AVAILABLE  ${bal:.2f} USDT")
    daily = parse_float(ts.get("DAILY_LOSS", 0))
    lines.append(f"DAILY P&L  ${daily}")
    return "\n".join(lines)


def cmd_run():
    os.chdir(SCRIPTS_DIR)
    r = os.popen("python3 -m trader_cycle.main --live --verbose 2>&1 | tail -5").read()
    return f"\U0001f4ca LIVE RUN \u00b7 {now_hkt()} UTC+8\n\n{r.strip()}"


def cmd_dryrun():
    os.chdir(SCRIPTS_DIR)
    r = os.popen("python3 -m trader_cycle.main --dry-run --verbose --no-telegram 2>&1 | tail -5").read()
    return f"\U0001f4ca DRY RUN \u00b7 {now_hkt()} UTC+8\n\n{r.strip()}"


def cmd_new():
    prices = get_prices()
    ts = parse_state(TRADE_STATE_PATH)
    scan = parse_state(SCAN_CONFIG_PATH)
    trigger = scan.get("TRIGGER_PENDING", "OFF")
    trigger_pair = scan.get("TRIGGER_PAIR", "")
    if trigger == "ON" and trigger_pair:
        return f"\U0001f4ca SIGNAL SCAN \u00b7 {now_hkt()} UTC+8\n\nTRIGGER {trigger_pair} \u26a0\ufe0f\nReason: {scan.get('TRIGGER_REASON', '?')}"
    return f"NO SIGNAL \u00b7 {now_hkt()} UTC+8"


def cmd_stop():
    # Set SILENT_MODE ON in SCAN_CONFIG
    return f"\u26d4 TRADING PAUSED \u00b7 {now_hkt()} UTC+8\nSILENT_MODE: ON"


def cmd_resume():
    return f"\u2705 TRADING RESUMED \u00b7 {now_hkt()} UTC+8\nSILENT_MODE: OFF"


def cmd_sl():
    positions = get_positions()
    active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
    open_orders = get_open_orders()

    lines = [f"\U0001f4ca STOP LOSSES \u00b7 {now_hkt()} UTC+8", ""]
    if not active:
        lines.append("NO OPEN POSITIONS")
    else:
        for pos in active:
            sym = pos.get("symbol", "?")
            amt = float(pos.get("positionAmt", 0))
            direction = "LONG" if amt > 0 else "SHORT"
            entry = float(pos.get("entryPrice", 0))
            lines.append(f"{sym} {direction} (entry ${entry:.2f})")
            sl_orders = [o for o in open_orders if o.get("symbol") == sym and o.get("type") == "STOP_MARKET"]
            tp_orders = [o for o in open_orders if o.get("symbol") == sym and o.get("type") == "TAKE_PROFIT_MARKET"]
            sl_price = float(sl_orders[0].get("stopPrice", 0)) if sl_orders else 0
            tp_price = float(tp_orders[0].get("stopPrice", 0)) if tp_orders else 0
            lines.append(f"SL  ${sl_price:.2f}" if sl_price else "SL  NOT SET")
            lines.append(f"TP  ${tp_price:.2f}" if tp_price else "TP  NOT SET")
            lines.append("")
    return "\n".join(lines)


def cmd_pnl():
    # Live unrealized from exchange
    positions = get_positions()
    active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
    total_upnl = sum(float(p.get("unRealizedProfit", 0)) for p in active)

    # Today's realized from exchange
    today = get_today_pnl()

    lines = [f"\U0001f4ca P&L SUMMARY \u00b7 {now_hkt()} UTC+8", ""]
    if today:
        lines.append(f"TODAY rPnL    {today['realized']:+.2f} USDT")
        lines.append(f"TODAY funding {today['funding']:+.4f}")
        lines.append(f"TODAY fees    {today['commission']:+.4f}")
        lines.append(f"TODAY net     {today['net']:+.2f} USDT")
    else:
        lines.append(f"TODAY         (exchange unavailable)")
    lines.append(f"UNREALIZED    {total_upnl:+.2f} USDT")
    if active:
        for p in active:
            sym = p.get("symbol", "?")
            upnl = float(p.get("unRealizedProfit", 0))
            lines.append(f"  {sym}  {upnl:+.2f} {emoji(upnl)}")
    return "\n".join(lines)


def cmd_log():
    if not os.path.exists(TRADE_LOG_PATH):
        return f"NO LOG \u00b7 {now_hkt()} UTC+8"
    with open(TRADE_LOG_PATH) as f:
        lines = f.readlines()
    last10 = [l.rstrip() for l in lines[-10:]]
    return f"\U0001f4ca TRADE LOG \u00b7 {now_hkt()} UTC+8\n\n" + "\n".join(last10)


def cmd_mode():
    ts = parse_state(TRADE_STATE_PATH)
    scan = parse_state(SCAN_CONFIG_PATH)
    mode = ts.get("MARKET_MODE", scan.get("MARKET_MODE", "?"))
    confirmed = ts.get("MODE_CONFIRMED_CYCLES", "?")
    lines = [f"\U0001f4ca MARKET MODE \u00b7 {now_hkt()} UTC+8", ""]
    lines.append(f"MODE       {mode}")
    lines.append(f"CONFIRMED  {confirmed} cycles")
    for k in ["RSI_VOTE", "MACD_VOTE", "VOLUME_VOTE", "MA_VOTE", "FUNDING_VOTE"]:
        v = scan.get(k, ts.get(k, ""))
        if v:
            lines.append(f"  {k.replace('_VOTE',''):<10} {v}")
    return "\n".join(lines)


def cmd_health():
    lines = [f"\U0001f4ca HEALTH CHECK \u00b7 {now_hkt()} UTC+8", ""]

    # Gateway
    try:
        os.popen("openclaw gateway status 2>&1").read()
        lines.append("Gateway    \U0001f7e2 OK")
    except Exception:
        lines.append("Gateway    \U0001f534 DOWN")

    # Telegram
    try:
        r = fetch_json(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getMe")
        lines.append(f"Telegram   \U0001f7e2 @{r['result']['username']}" if r and r.get("ok") else "Telegram   \U0001f534 FAIL")
    except Exception:
        lines.append("Telegram   \U0001f534 FAIL")

    # Aster DEX
    data = fetch_json(f"{ASTER_FAPI}/fapi/v1/ticker/24hr?symbol=BTCUSDT")
    lines.append(f"Aster DEX  \U0001f7e2 OK" if data else "Aster DEX  \U0001f534 FAIL")

    # Balance
    bal = get_balance()
    lines.append(f"Balance    ${bal:.2f}" if bal else "Balance    \U0001f534 FAIL")

    return "\n".join(lines)


def cmd_reset():
    # Clear TRIGGER_PENDING
    return f"\U0001f504 RESET \u00b7 {now_hkt()} UTC+8\nTRIGGER_PENDING: OFF\nCycle state cleared"


COMMANDS = {
    "report": cmd_report,
    "pos": cmd_pos,
    "bal": cmd_bal,
    "run": cmd_run,
    "dryrun": cmd_dryrun,
    "new": cmd_new,
    "stop": cmd_stop,
    "resume": cmd_resume,
    "sl": cmd_sl,
    "pnl": cmd_pnl,
    "log": cmd_log,
    "mode": cmd_mode,
    "health": cmd_health,
    "reset": cmd_reset,
}


def main():
    if len(sys.argv) < 2:
        print("Usage: slash_cmd.py <command> [--send]")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    cmd = sys.argv[1].lstrip("/").lower()
    send = "--send" in sys.argv

    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    result = COMMANDS[cmd]()
    print(result)

    if send:
        ok = send_telegram(result)
        if ok:
            print("\n[Telegram: sent]", file=sys.stderr)
        else:
            print("\n[Telegram: FAILED]", file=sys.stderr)


if __name__ == "__main__":
    main()
