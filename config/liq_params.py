"""
liq_params.py — Liquidation monitor thresholds.

Separate from settings.py because these are observation-period tunable:
adjust during Week 1-2 data collection without touching core settings.
"""

# ─── On-Liqs Detection (OI delta proxy) ───
ON_LIQS_OI_DROP_PCT = 1.5           # OI drop > 1.5% in window = significant
ON_LIQS_THRESHOLD_USD = 1_000_000   # $1M+ estimated liq volume → trigger
ON_LIQS_WINDOW_MIN = 10             # 10-minute rolling window for OI delta
ON_LIQS_SIGNAL_BOOST = 1.0          # +1.0 to signal score when liq detected

# ─── Monitor Settings ───
LIQ_POLL_INTERVAL_SEC = 60          # poll every 60s
LIQ_STATE_MAX_AGE_SEC = 180         # ignore state older than 3 min
LIQ_COINS = ["BTC", "ETH", "SOL"]  # coins to monitor
LIQ_HISTORY_MAXLEN = 20             # rolling window entries (20 × 60s = 20 min)
