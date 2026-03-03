# config/params.py
# 所有交易參數集中地
# 後期調整：只改呢個文件

# ── 掃描設定 ──
SCAN_INTERVAL_SEC = 180
SCHEDULED_CYCLE_HOURS = [0, 3, 6, 9, 12, 15, 18, 21]
MIN_VOLUME_THRESHOLD = 50000

# ── RANGE 模式參數 ──
RANGE_SL_ATR_MULT = 1.2
RANGE_TP_ATR_MULT = 2.0
RANGE_ENTRY_CONFIRM = "bb_squeeze"

# ── TREND 模式參數 ──
TREND_SL_ATR_MULT = 1.5
TREND_TP_ATR_MULT = 3.0
TREND_ENTRY_CONFIRM = "ema_cross"

# ── Bollinger Band 參數 ──
BB_TOUCH_TOL_DEFAULT = 0.005   # BTC, ETH, XAG
BB_TOUCH_TOL_XRP = 0.008       # XRP 較大容忍度
BB_WIDTH_MIN = 0.05            # 最小BB寬度過濾

# ── 倉位管理 ──
MAX_POSITION_SIZE_USDT = 50
MAX_OPEN_POSITIONS = 3
RISK_PER_TRADE_PCT = 0.02
