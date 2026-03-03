# SCAN_CONFIG.md
# 版本: 2026-03-02（修正版）
# Source of truth for light-scan. Updated by trader-cycle every 30min.
# Light-scan reads ONLY this file.
#
# WRITE PERMISSIONS:
# light-scan  → ONLY: TRIGGER_PENDING, TRIGGER_PAIR, TRIGGER_REASON, LIGHT_SCAN_COUNT
# trader-cycle → ALL other fields

# ─────────────────────────────────────────
# [META]
# ─────────────────────────────────────────
config_version: 1.0
update_count: 415
last_updated: 2026-03-03 22:03
created: 2026-02-28 UTC+8

# ─────────────────────────────────────────
# [PRICES] — Snapshot from last deep cycle
# ─────────────────────────────────────────
BTC_price: 67011.4
BTC_price_ts: 2026-03-03 22:03
ETH_price: 1953.7
ETH_price_ts: 2026-03-03 22:03
XRP_price: 1.3482
XRP_price_ts: 2026-03-03 22:03
XAG_price: 82.3000
XAG_price_ts: 2026-03-03 22:03

# ─────────────────────────────────────────
# [ATR] — ATR(14) on 4H
# ─────────────────────────────────────────
BTC_ATR: 1431.7
ETH_ATR: 55.6
XRP_ATR: 0.0342
XAG_ATR: 2.9898

# ─────────────────────────────────────────
# [SR_LEVELS] — Active Support/Resistance
# ─────────────────────────────────────────
BTC_support: 62995.1
BTC_resistance: 70076.6
ETH_support: 1835.0
ETH_resistance: 2088.7
XRP_support: 1.2700
XRP_resistance: 1.4324
XAG_support: 78.1
XAG_resistance: 97.6

# ─────────────────────────────────────────
# [SR_ZONES] — Pre-calculated ±0.3×ATR ranges
# Format: lower-upper
# ─────────────────────────────────────────
BTC_support_zone: 62565.60-63424.60
BTC_resistance_zone: 69647.10-70506.10
ETH_support_zone: 1818.33-1851.67
ETH_resistance_zone: 2072.03-2105.37
XRP_support_zone: 1.26-1.28
XRP_resistance_zone: 1.42-1.44
XAG_support_zone: 77.23-79.03
XAG_resistance_zone: 96.71-98.51

# ─────────────────────────────────────────
# [FUNDING] — Last recorded funding rates
# ─────────────────────────────────────────
BTC_funding_last: 0.0000075700
ETH_funding_last: -0.0000386200
XRP_funding_last: -0.0002142800
XAG_funding_last: 0.0000010700
funding_ts: 2026-03-03 22:03

# ─────────────────────────────────────────
# [STATE_FLAGS] — System state
# ─────────────────────────────────────────
CONFIG_VALID: true
SILENT_MODE: ON
SILENT_MODE_CYCLES: 381
TRIGGER_PENDING: OFF
TRIGGER_PAIR: XAGUSDT
TRIGGER_REASON: 24H_CHANGE_-11.7pct
LIGHT_SCAN_COUNT: 0

# ─────────────────────────────────────────
# PROTECTION RULES (for agents reading this file)
# ─────────────────────────────────────────
# If last_updated = INIT → CONFIG_VALID: false
# If age >60min → CONFIG_VALID: false
# If CONFIG_VALID: false → skip S/R zone check, skip funding delta check
# If ATR = 0 → skip ALL S/R calculations
# Timestamp format: YYYY-MM-DD HH:MM UTC+8

# ─────────────────────────────────────────
# ADAPTIVE SAMPLING MODES
# ─────────────────────────────────────────
# ACTIVE: default — full cycle reports
# SILENT: SILENT_MODE_CYCLES >= 2 — no routine Telegram
# FAST:   TRIGGER_PENDING=ON + age <25min — skip SOUL/STRATEGY read

# ─────────────────────────────────────────
# LIGHT-SCAN TRIGGER CONDITIONS
# ─────────────────────────────────────────
# Price >0.6% in 3min                     → TRIGGER
# Volume >175% 30d avg                    → TRIGGER
# S/R zone entry (CONFIG_VALID only)      → TRIGGER
# Funding delta >0.18% (CONFIG_VALID only) → TRIGGER

# ─────────────────────────────────────────
# SILENT MODE EXIT (any one)
# ─────────────────────────────────────────
# Volume >175% | Price >0.45% | S/R zone | Funding >0.18%
# TRIGGER_PENDING: ON | User: "exit silent mode"
# Silent report: every 20 light-scans (~60min)
