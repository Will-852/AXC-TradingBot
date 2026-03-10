#!/usr/bin/env python3
"""
light_scan.py — Python-based Light Scan (No LLM)
每 3 分鐘執行一次，純數學對比，唔使 LLM

功能：
1. Fetch Aster DEX API → 4 pairs 即時數據
2. 同 SCAN_CONFIG.md 上次數據對比
3. Trigger Detection（價格/成交量/S-R/Funding）
4. 更新 SCAN_CONFIG.md + SCAN_LOG.md
5. 有需要時 Send Telegram

Exit codes:
0 = NO_TRIGGER
1 = TRIGGER detected
2 = ERROR

Output: JSON summary for cron consumption
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_SHARED = os.path.join(AXC_HOME, "shared")
SCAN_CONFIG_PATH = os.path.join(_SHARED, "SCAN_CONFIG.md")
SCAN_LOG_PATH = os.path.join(_SHARED, "SCAN_LOG.md")

ASTER_BASE = "https://fapi.asterdex.com/fapi/v1"
PAIRS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT"]
PAIR_PREFIX = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "XRPUSDT": "XRP", "XAGUSDT": "XAG"}

# Trigger thresholds
PRICE_TRIGGER_PCT = 0.38      # >0.38% price change
VOLUME_TRIGGER_MULT = 1.75    # >175% of baseline
FUNDING_TRIGGER_PCT = 0.18    # >0.18% funding delta
SR_ZONE_CHECK = True          # Check S/R zones

# Telegram
TG_BOT_TOKEN = "8373819624:AAFH-SVTqqYlU22JnuiiBpB2uZytvw_pN30"
TG_CHAT_ID = "2060972655"
SILENT_REPORT_INTERVAL = 20   # Every 20 scans in silent mode

# Timezone: UTC+8
HKT = timezone(timedelta(hours=8))


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def now_hkt():
    return datetime.now(HKT)


def now_str():
    return now_hkt().strftime("%Y-%m-%d %H:%M")


def fetch_json(url, timeout=10):
    """Fetch JSON from URL with timeout."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenClaw-LightScan/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def parse_scan_config(path):
    """Parse SCAN_CONFIG.md key-value pairs."""
    config = {}
    if not os.path.exists(path):
        return config
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r'^(\w+):\s*(.+)$', line)
            if match:
                key, val = match.group(1), match.group(2).strip()
                # Try to parse as number
                try:
                    if '.' in val:
                        config[key] = float(val)
                    else:
                        config[key] = int(val)
                except ValueError:
                    config[key] = val
    return config


def update_scan_config(path, updates):
    """Update specific fields in SCAN_CONFIG.md (light-scan only writes allowed fields)."""
    if not os.path.exists(path):
        return False

    with open(path, "r") as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        updated = False
        for key, val in updates.items():
            pattern = f'^{key}:'
            if re.match(pattern, line.strip()):
                new_lines.append(f"{key}: {val}\n")
                updated = True
                break
        if not updated:
            new_lines.append(line)

    with open(path, "w") as f:
        f.writelines(new_lines)
    return True


def append_scan_log(path, entry):
    """Append a line to SCAN_LOG.md, keep max 200 lines."""
    lines = []
    if os.path.exists(path):
        with open(path, "r") as f:
            lines = f.readlines()

    lines.append(entry + "\n")

    # Keep only last 200 data lines (skip comment lines)
    data_lines = [l for l in lines if not l.strip().startswith("#") and l.strip()]
    comment_lines = [l for l in lines if l.strip().startswith("#")]

    if len(data_lines) > 200:
        data_lines = data_lines[-200:]

    with open(path, "w") as f:
        f.writelines(comment_lines)
        f.writelines(data_lines)


def send_telegram(text):
    """Send Telegram message."""
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────
# MAIN SCAN LOGIC
# ─────────────────────────────────────────
def run_light_scan():
    ts = now_str()
    result = {"timestamp": ts, "triggers": [], "prices": {}, "status": "ok"}

    # ─── STEP A: Read config ───
    config = parse_scan_config(SCAN_CONFIG_PATH)
    if not config:
        result["status"] = "error"
        result["error"] = "Cannot read SCAN_CONFIG.md"
        print(json.dumps(result, indent=2))
        return 2

    # Check config validity
    last_updated = config.get("last_updated", "INIT")
    config_valid = config.get("CONFIG_VALID", "false")

    if last_updated == "INIT" or config_valid == "false":
        config_valid = False
    else:
        # Check age
        try:
            lu = datetime.strptime(str(last_updated), "%Y-%m-%d %H:%M")
            lu = lu.replace(tzinfo=HKT)
            age_min = (now_hkt() - lu).total_seconds() / 60
            config_valid = age_min <= 60
        except:
            config_valid = False

    result["config_valid"] = config_valid

    # ─── STEP B: Fetch live data ───
    market_data = {}
    for pair in PAIRS:
        prefix = PAIR_PREFIX[pair]

        # Ticker data
        ticker = fetch_json(f"{ASTER_BASE}/ticker/24hr?symbol={pair}")
        if "error" in ticker:
            result["status"] = "partial"
            result.setdefault("errors", []).append(f"{pair} ticker: {ticker['error']}")
            continue

        # Funding data
        funding = fetch_json(f"{ASTER_BASE}/premiumIndex?symbol={pair}")

        price = float(ticker.get("lastPrice", 0))
        volume_24h = float(ticker.get("quoteVolume", 0))
        price_change_pct = float(ticker.get("priceChangePercent", 0))
        funding_rate = float(funding.get("lastFundingRate", 0)) if "error" not in funding else 0

        market_data[prefix] = {
            "price": price,
            "volume_24h": volume_24h,
            "price_change_24h_pct": price_change_pct,
            "funding_rate": funding_rate
        }
        result["prices"][prefix] = price

    if not market_data:
        result["status"] = "error"
        result["error"] = "All API calls failed"
        print(json.dumps(result, indent=2))
        return 2

    # ─── STEP C: Trigger Detection ───
    triggers = []

    for prefix, data in market_data.items():
        current_price = data["price"]
        last_price = config.get(f"{prefix}_price", 0)

        # 1. PRICE TRIGGER: compare with last stored price
        if last_price > 0 and current_price > 0:
            price_delta_pct = abs(current_price - last_price) / last_price * 100
            if price_delta_pct > PRICE_TRIGGER_PCT:
                direction = "+" if current_price > last_price else "-"
                triggers.append({
                    "pair": f"{prefix}USDT",
                    "type": "PRICE",
                    "reason": f"PRICE_DELTA_{direction}{price_delta_pct:.2f}pct",
                    "price": current_price
                })

        # 2. VOLUME TRIGGER: use 24h price change as volume proxy
        # Note: We don't have 30d avg easily, use 24h change magnitude as proxy
        # If 24h change > 3% = unusual volume activity
        if abs(data["price_change_24h_pct"]) > 3.0:
            triggers.append({
                "pair": f"{prefix}USDT",
                "type": "VOLUME",
                "reason": f"24H_CHANGE_{data['price_change_24h_pct']:.1f}pct",
                "price": current_price
            })

        # 3. S/R ZONE TRIGGER (only if config valid)
        if config_valid and SR_ZONE_CHECK:
            for zone_type in ["support_zone", "resistance_zone"]:
                zone_str = config.get(f"{prefix}_{zone_type}", "0-0")
                try:
                    parts = str(zone_str).split("-")
                    if len(parts) == 2:
                        zone_low = float(parts[0])
                        zone_high = float(parts[1])
                        if zone_low > 0 and zone_high > 0:
                            if zone_low <= current_price <= zone_high:
                                triggers.append({
                                    "pair": f"{prefix}USDT",
                                    "type": "SR_ZONE",
                                    "reason": f"{zone_type.upper()}_{zone_low:.2f}-{zone_high:.2f}",
                                    "price": current_price
                                })
                except:
                    pass

        # 4. FUNDING DELTA TRIGGER (only if config valid)
        if config_valid:
            last_funding = config.get(f"{prefix}_funding_last", 0)
            current_funding = data["funding_rate"]
            if isinstance(last_funding, (int, float)) and last_funding != 0:
                funding_delta = abs(current_funding - last_funding) * 100  # Convert to percentage
                if funding_delta > FUNDING_TRIGGER_PCT:
                    triggers.append({
                        "pair": f"{prefix}USDT",
                        "type": "FUNDING",
                        "reason": f"FUNDING_DELTA_{funding_delta:.3f}pct",
                        "price": current_price
                    })

    result["triggers"] = triggers
    has_trigger = len(triggers) > 0

    # ─── STEP D: Update SCAN_CONFIG.md ───
    scan_count = config.get("LIGHT_SCAN_COUNT", 0)
    if not isinstance(scan_count, int):
        scan_count = 0

    updates = {}

    if has_trigger:
        # Trigger detected
        t = triggers[0]  # Primary trigger
        updates["TRIGGER_PENDING"] = "ON"
        updates["TRIGGER_PAIR"] = t["pair"]
        updates["TRIGGER_REASON"] = t["reason"]
        updates["LIGHT_SCAN_COUNT"] = 0
    else:
        updates["LIGHT_SCAN_COUNT"] = scan_count + 1

    # Always update prices from current scan
    for prefix, data in market_data.items():
        updates[f"{prefix}_price"] = f"{data['price']:.4f}" if data['price'] < 100 else f"{data['price']:.1f}"
        updates[f"{prefix}_price_ts"] = ts

    # Update funding rates
    for prefix, data in market_data.items():
        if data["funding_rate"] != 0:
            updates[f"{prefix}_funding_last"] = f"{data['funding_rate']:.10f}"
    updates["funding_ts"] = ts

    update_scan_config(SCAN_CONFIG_PATH, updates)

    # ─── STEP E/F: Write log ───
    prices_str = " ".join([f"{p}:{market_data[p]['price']:.1f}" if market_data[p]['price'] > 10
                           else f"{p}:{market_data[p]['price']:.4f}"
                           for p in sorted(market_data.keys())])

    if has_trigger:
        t = triggers[0]
        log_entry = f"[{ts} UTC+8] LIGHT TRIGGER:{t['pair']} REASON:{t['reason']} {prices_str}"
    else:
        log_entry = f"[{ts} UTC+8] LIGHT {prices_str} NO_TRIGGER"

    append_scan_log(SCAN_LOG_PATH, log_entry)

    # ─── Telegram Logic ───
    silent_mode = config.get("SILENT_MODE", "OFF")

    if has_trigger:
        # Don't send Telegram for triggers (trader-cycle handles that)
        result["telegram"] = "skip_trigger"
    elif silent_mode == "ON" and (scan_count + 1) >= SILENT_REPORT_INTERVAL:
        # Silent mode periodic report
        msg = (f"<b>[{ts} UTC+8] | Silent Mode Active</b>\n"
               + "\n".join([f"{p}: {market_data[p]['price']}" for p in sorted(market_data.keys())])
               + f"\nNext deep scan: ~{(now_hkt() + timedelta(minutes=30)).strftime('%H:%M')}")
        tg_result = send_telegram(msg)
        result["telegram"] = "sent" if "error" not in tg_result else tg_result["error"]
    else:
        result["telegram"] = "silent"

    # ─── Output ───
    result["scan_count"] = updates.get("LIGHT_SCAN_COUNT", 0)
    result["trigger_count"] = len(triggers)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if has_trigger else 0


if __name__ == "__main__":
    try:
        exit_code = run_light_scan()
        sys.exit(exit_code)
    except Exception as e:
        error_result = {
            "timestamp": now_str(),
            "status": "error",
            "error": str(e)
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(2)
