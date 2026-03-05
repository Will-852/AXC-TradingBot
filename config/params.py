# config/params.py
# 所有交易參數集中地
# 後期調整：只改呢個文件

# ═══════════════════════════════════════
# Section 1: 掃描設定（async_scanner 讀）
# ═══════════════════════════════════════
SCAN_INTERVAL_SEC = 180
SCHEDULED_CYCLE_HOURS = [0, 3, 6, 9, 12, 15, 18, 21]

# ═══════════════════════════════════════
# Section 2: BB 指標參數（indicator_calc 讀）
# ═══════════════════════════════════════
BB_TOUCH_TOL_DEFAULT = 0.005   # BTC, ETH, XAG
BB_TOUCH_TOL_XRP = 0.008       # XRP 較大容忍度
BB_WIDTH_MIN = 0.05            # 最小BB寬度過濾

# ═══════════════════════════════════════
# Section 3: 倉位管理（dashboard 讀）
# ═══════════════════════════════════════
MAX_POSITION_SIZE_USDT = 50
MAX_OPEN_POSITIONS = 3
RISK_PER_TRADE_PCT = 0.02

# ═══════════════════════════════════════
# Section 4: 打法 Profiles（dashboard + settings 讀）
# ═══════════════════════════════════════
# 三個打法對應不同風險偏好：
#   CONSERVATIVE → 低波動市場，RANGE only
#   BALANCED     → 中等波動，RANGE + 部分 TREND
#   AGGRESSIVE   → 高波動市場，追趨勢

TRADING_PROFILES = {
    "CONSERVATIVE": {
        "description": "保守：等待 RANGE 機會，最低風險",
        "trigger_pct":          0.50,
        "risk_per_trade_pct":   0.01,
        "sl_atr_mult":          1.5,
        "tp_atr_mult":          2.0,
        "max_open_positions":   1,
        "allow_trend":          False,
        "allow_range":          True,
        "trend_min_change_pct": None,
    },
    "BALANCED": {
        "description": "平衡：RANGE 為主，容許部分 TREND",
        "trigger_pct":          0.38,
        "risk_per_trade_pct":   0.02,
        "sl_atr_mult":          1.2,
        "tp_atr_mult":          2.0,
        "max_open_positions":   2,
        "allow_trend":          True,
        "allow_range":          True,
        "trend_min_change_pct": 5.0,
    },
    "AGGRESSIVE": {
        "description": "進取：追趨勢，最高風險最高回報",
        "trigger_pct":          0.25,
        "risk_per_trade_pct":   0.03,
        "sl_atr_mult":          1.0,
        "tp_atr_mult":          3.0,
        "max_open_positions":   3,
        "allow_trend":          True,
        "allow_range":          True,
        "trend_min_change_pct": 2.0,
    },
}

# 當前啟用模式（可手動改 或 由 /api/set_mode 自動寫入）
ACTIVE_PROFILE = "BALANCED"

# 是否啟用自動切換
AUTO_PROFILE_SWITCH = False

# ═══════════════════════════════════════
# Section 5: 幣種 + 引擎設定（scanner 讀）
# ═══════════════════════════════════════
# ⚠️ 修改後必須重啟 async_scanner.py 先生效
# 重啟指令：pkill -f async_scanner && sleep 2 &&
#            python3 ~/.openclaw/scripts/async_scanner.py &

ASTER_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "XAGUSDT",
    # 加幣種：加一行 "幣種USDT", 然後重啟掃描器
]

BINANCE_SYMBOLS = [
    # Binance 整合後填入，同樣需要重啟
    # "SOLUSDT",
]

# 掃描引擎設定
SCAN_TIMEOUT_SEC    = 30          # 單幣種超時（秒）
SCAN_MAX_WORKERS    = 8           # 並發上限
SCAN_LOG_MAX_LINES  = 500         # SCAN_LOG 保留行數
SCAN_LOG_MAX_BYTES  = 10_485_760  # scanner.log 單文件上限（10MB）
SCAN_LOG_BACKUPS    = 5           # scanner.log 保留備份數
TRIGGER_PCT         = 0.05        # 信號觸發閾值（5%）
