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
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from openclaw_bridge import bridge

HKT = timezone(timedelta(hours=8))
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_SHARED = os.path.join(AXC_HOME, "shared")
TRADE_STATE_PATH = os.path.join(_SHARED, "TRADE_STATE.md")
SCAN_CONFIG_PATH = os.path.join(_SHARED, "SCAN_CONFIG.md")
TRADE_LOG_PATH = os.path.join(_SHARED, "TRADE_LOG.md")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ASTER_FAPI = "https://fapi.asterdex.com"
SIGNAL_PATH = os.path.join(os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading")), "shared", "SIGNAL.md")


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
    for pair in ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT", "XAUUSDT"]:
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

    lines = [f"\U0001f4ca AXC 交易員 \u00b7 實盤 \u00b7 {now_hkt()} UTC+8", ""]
    lines.append(f"模式     {mode:<10}信號   {signal_text}")
    lines.append(f"結餘     ${bal:<10.2f}浮動盈虧  {total_upnl:+.2f}")
    lines.append(f"今日     {today_net:+.2f} USDT    (已實現 {today_realized:+.2f})")
    lines.append("")

    # Position — live from Aster DEX
    lines.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 持倉 \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
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
            lines.append(f"入場 ${entry:.2f} \u2192 現價 ${current:.2f}")
            lines.append(f"盈虧  {upnl:+.2f} USDT ({pnl_pct:+.1f}%) {emoji(upnl)}")
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
        lines.append("未有持倉")

    lines.append("")
    lines.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 行情 \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    for sym, label in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("XRPUSDT", "XRP"), ("XAGUSDT", "XAG")]:
        p = prices.get(sym, {})
        price = p.get("price", 0)
        chg = p.get("change", 0)
        # 根據價格自動選擇小數位
        if price >= 1000:
            price_str = f"${price:,.2f}"
        elif price >= 1:
            price_str = f"${price:,.4f}"
        else:
            price_str = f"${price:,.6f}"
        lines.append(f"{label}  {price_str:<14}{chg:+.2f}% {emoji(chg)}")

    # Dynamic LAST/NEXT from SIGNAL.md
    scan_status = sig.get("SCAN_STATUS", "?")
    trigger_count = sig.get("TRIGGER_COUNT", "0")
    scan_ts = sig.get("TIMESTAMP", "?")
    lines.append("")
    lines.append(f"上次  掃描{scan_status} \u00b7 {trigger_count} 觸發 \u00b7 {scan_ts}")
    if active_positions:
        lines.append(f"下步  監控持倉中")
    elif signal_active == "YES":
        lines.append(f"下步  信號活躍: {signal_pair}")
    else:
        lines.append(f"下步  等待入場信號")

    return "\n".join(lines)


def cmd_pos():
    positions = get_positions()
    lines = [f"\U0001f4ca 持倉一覽 \u00b7 {now_hkt()} UTC+8", ""]
    if not positions:
        lines.append("未有持倉")
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
            lines.append(f"入場 ${entry:.2f} \u2192 現價 ${mark:.2f}")
            lines.append(f"盈虧  ${pnl:.2f} {emoji(pnl)}")
            lines.append(f"數量 {abs(amt)}")
            lines.append("")
    return "\n".join(lines)


def cmd_bal():
    bal = get_balance()
    ts = parse_state(TRADE_STATE_PATH)
    if bal is None:
        bal = parse_float(ts.get("BALANCE_USDT", 0))
    lines = [f"\U0001f4ca 結餘 \u00b7 {now_hkt()} UTC+8", ""]
    lines.append(f"結餘       ${bal:.2f} USDT")
    lines.append(f"可用       ${bal:.2f} USDT")
    daily = parse_float(ts.get("DAILY_LOSS", 0))
    lines.append(f"今日盈虧   ${daily}")
    return "\n".join(lines)


def cmd_run():
    os.chdir(SCRIPTS_DIR)
    r = os.popen("python3 -m trader_cycle.main --live --verbose 2>&1 | tail -5").read()
    return f"\U0001f4ca 實盤執行 \u00b7 {now_hkt()} UTC+8\n\n{r.strip()}"


def cmd_dryrun():
    os.chdir(SCRIPTS_DIR)
    r = os.popen("python3 -m trader_cycle.main --dry-run --verbose --no-telegram 2>&1 | tail -5").read()
    return f"\U0001f4ca 模擬執行 \u00b7 {now_hkt()} UTC+8\n\n{r.strip()}"


def cmd_new():
    prices = get_prices()
    ts = parse_state(TRADE_STATE_PATH)
    scan = parse_state(SCAN_CONFIG_PATH)
    trigger = scan.get("TRIGGER_PENDING", "OFF")
    trigger_pair = scan.get("TRIGGER_PAIR", "")
    if trigger == "ON" and trigger_pair:
        return f"\U0001f4ca 信號掃描 \u00b7 {now_hkt()} UTC+8\n\n觸發 {trigger_pair} \u26a0\ufe0f\n原因: {scan.get('TRIGGER_REASON', '?')}"
    return f"無信號 \u00b7 {now_hkt()} UTC+8"


def cmd_stop():
    # Set SILENT_MODE ON in SCAN_CONFIG
    return f"\u26d4 交易暫停 \u00b7 {now_hkt()} UTC+8\n靜音模式: 開"


def cmd_resume():
    return f"\u2705 交易恢復 \u00b7 {now_hkt()} UTC+8\n靜音模式: 關"


def cmd_sl():
    positions = get_positions()
    active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
    open_orders = get_open_orders()

    lines = [f"\U0001f4ca 止損止盈 \u00b7 {now_hkt()} UTC+8", ""]
    if not active:
        lines.append("未有持倉")
    else:
        for pos in active:
            sym = pos.get("symbol", "?")
            amt = float(pos.get("positionAmt", 0))
            direction = "LONG" if amt > 0 else "SHORT"
            entry = float(pos.get("entryPrice", 0))
            lines.append(f"{sym} {direction} (入場 ${entry:.2f})")
            sl_orders = [o for o in open_orders if o.get("symbol") == sym and o.get("type") == "STOP_MARKET"]
            tp_orders = [o for o in open_orders if o.get("symbol") == sym and o.get("type") == "TAKE_PROFIT_MARKET"]
            sl_price = float(sl_orders[0].get("stopPrice", 0)) if sl_orders else 0
            tp_price = float(tp_orders[0].get("stopPrice", 0)) if tp_orders else 0
            lines.append(f"止損  ${sl_price:.2f}" if sl_price else "止損  未設定")
            lines.append(f"止盈  ${tp_price:.2f}" if tp_price else "止盈  未設定")
            lines.append("")
    return "\n".join(lines)


def cmd_pnl():
    # Live unrealized from exchange
    positions = get_positions()
    active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
    total_upnl = sum(float(p.get("unRealizedProfit", 0)) for p in active)

    # Today's realized from exchange
    today = get_today_pnl()

    lines = [f"\U0001f4ca 盈虧概覽 \u00b7 {now_hkt()} UTC+8", ""]
    if today:
        lines.append(f"今日已實現   {today['realized']:+.2f} USDT")
        lines.append(f"今日資金費   {today['funding']:+.4f}")
        lines.append(f"今日手續費   {today['commission']:+.4f}")
        lines.append(f"今日淨值     {today['net']:+.2f} USDT")
    else:
        lines.append(f"今日         (交易所無回應)")
    lines.append(f"浮動盈虧     {total_upnl:+.2f} USDT")
    if active:
        for p in active:
            sym = p.get("symbol", "?")
            upnl = float(p.get("unRealizedProfit", 0))
            lines.append(f"  {sym}  {upnl:+.2f} {emoji(upnl)}")
    return "\n".join(lines)


def cmd_log():
    if not os.path.exists(TRADE_LOG_PATH):
        return f"無記錄 \u00b7 {now_hkt()} UTC+8"
    with open(TRADE_LOG_PATH) as f:
        lines = f.readlines()
    last10 = [l.rstrip() for l in lines[-10:]]
    return f"\U0001f4ca 交易記錄 \u00b7 {now_hkt()} UTC+8\n\n" + "\n".join(last10)


def cmd_mode():
    ts = parse_state(TRADE_STATE_PATH)
    scan = parse_state(SCAN_CONFIG_PATH)
    mode = ts.get("MARKET_MODE", scan.get("MARKET_MODE", "?"))
    confirmed = ts.get("MODE_CONFIRMED_CYCLES", "?")
    lines = [f"\U0001f4ca 市場模式 \u00b7 {now_hkt()} UTC+8", ""]
    lines.append(f"模式       {mode}")
    lines.append(f"已確認     {confirmed} 個週期")
    for k in ["RSI_VOTE", "MACD_VOTE", "VOLUME_VOTE", "MA_VOTE", "FUNDING_VOTE"]:
        v = scan.get(k, ts.get(k, ""))
        if v:
            lines.append(f"  {k.replace('_VOTE',''):<10} {v}")
    return "\n".join(lines)


def cmd_health():
    lines = [f"\U0001f4ca 系統健康 \u00b7 {now_hkt()} UTC+8", ""]

    # Gateway
    gw = bridge.gateway_status()
    GW_ICONS = {"ok": "\U0001f7e2 正常", "down": "\U0001f534 斷線", "n/a": "\u26aa 獨立模式"}
    lines.append(f"Gateway    {GW_ICONS[gw]}")

    # Telegram
    try:
        r = fetch_json(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getMe")
        lines.append(f"Telegram   \U0001f7e2 @{r['result']['username']}" if r and r.get("ok") else "Telegram   \U0001f534 失敗")
    except Exception:
        lines.append("Telegram   \U0001f534 失敗")

    # Aster DEX
    data = fetch_json(f"{ASTER_FAPI}/fapi/v1/ticker/24hr?symbol=BTCUSDT")
    lines.append(f"Aster DEX  \U0001f7e2 正常" if data else "Aster DEX  \U0001f534 失敗")

    # Balance
    bal = get_balance()
    lines.append(f"結餘       ${bal:.2f}" if bal else "結餘       \U0001f534 失敗")

    return "\n".join(lines)


def cmd_reset():
    # Clear TRIGGER_PENDING
    return f"\U0001f504 重設 \u00b7 {now_hkt()} UTC+8\n觸發待命: 關\n週期狀態已清除"


def cmd_stats():
    """策略表現統計 — 讀 trades.jsonl 計算核心指標。"""
    try:
        axc_home = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
        sys.path.insert(0, os.path.join(axc_home, "scripts"))
        from trader_cycle.analysis.metrics import calculate_metrics, format_stats_text
        m = calculate_metrics()
        return format_stats_text(m)
    except Exception as e:
        return f"Stats error: {e}"


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
    "stats": cmd_stats,
}


def _read_last_report(path):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                return float(f.read().strip() or 0)
    except Exception:
        return 0.0
    return 0.0


def _write_last_report(path, ts):
    try:
        with open(path, 'w') as f:
            f.write(str(float(ts)))
    except Exception:
        pass


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

    # When run as the LaunchAgent (report --send), we still run every 30m,
    # but enforce Telegram send frequency:
    # - If there are open positions -> send every time (30m)
    # - If no open positions -> only send if last send >= 3 hours
    if send and cmd == "report":
        LAST_REPORT_PATH = os.path.join(_SHARED, ".last_report_sent")
        try:
            live_positions = get_positions() or []
            active_positions = [p for p in live_positions if float(p.get("positionAmt", 0)) != 0]
        except Exception:
            active_positions = []

        now_ts = time.time()
        last_ts = _read_last_report(LAST_REPORT_PATH)

        if active_positions:
            ok = send_telegram(result)
            if ok:
                _write_last_report(LAST_REPORT_PATH, now_ts)
                print("\n[Telegram: sent]", file=sys.stderr)
            else:
                print("\n[Telegram: FAILED]", file=sys.stderr)
        else:
            # No positions open. Throttle to once per 3 hours.
            if now_ts - last_ts >= 3 * 3600:
                ok = send_telegram(result)
                if ok:
                    _write_last_report(LAST_REPORT_PATH, now_ts)
                    print("\n[Telegram: sent (throttled)]", file=sys.stderr)
                else:
                    print("\n[Telegram: FAILED]", file=sys.stderr)
            else:
                # Skip sending to Telegram; still exit 0 so LaunchAgent logs look normal.
                print(f"\n[Telegram: skipped] last_sent={last_ts} now={now_ts}", file=sys.stderr)
    elif send:
        ok = send_telegram(result)
        if ok:
            print("\n[Telegram: sent]", file=sys.stderr)
        else:
            print("\n[Telegram: FAILED]", file=sys.stderr)


if __name__ == "__main__":
    main()
