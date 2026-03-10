# config/params.py
# ═══════════════════════════════════════════════════════
# 設計原則（方案3：清晰分工）
# ═══════════════════════════════════════════════════════
#
# 呢個文件 = 用戶可見參數層
# 讀取者：
#   dashboard.py    → 顯示 UI 設定（get_params() 動態讀全部）
#   settings.py     → profile override（只讀 TRADING_PROFILES 4 個 key）
#   indicator_calc  → BB + TIMEFRAME + MACD + STOCH + SR 參數
#   async_scanner   → 幣種 + 掃描設定
#   weekly_review   → trigger_pct
#
# 唔放喺呢度：
#   交易引擎內部邏輯參數 → scripts/trader_cycle/config/settings.py
#   敏感 API keys        → secrets/.env
#
# TRADING_PROFILES override 映射（settings.py line 141-148）：
#   risk_per_trade_pct  → RANGE_RISK_PCT + TREND_RISK_PCT
#   sl_atr_mult         → RANGE_SL_ATR_MULT + TREND_SL_ATR_MULT
#   range_min_rr        → RANGE_MIN_RR
#   trend_min_rr        → TREND_MIN_RR
#   tp_atr_mult         → (reserved for future TP calc, not consumed by settings.py)
#   max_open_positions  → MAX_CRYPTO_POSITIONS
#
# ⚠️ 加新 profile key 前：先 grep settings.py 確認有消費者
# ═══════════════════════════════════════════════════════

# ═══════════════════════════════════════
# Section 1: 掃描設定（async_scanner 讀）
# ═══════════════════════════════════════
SCAN_INTERVAL_SEC = 20   # v7: 9路輪轉，每20秒掃一個exchange
SCHEDULED_CYCLE_HOURS = [0, 3, 6, 9, 12, 15, 18, 21]

# 9 路輪轉順序（每 SCAN_INTERVAL_SEC 秒輪一個）
# 效果：每個 exchange 每 180 秒才被 hit 一次
EXCHANGE_ROTATION = [
    "aster",        # BTC, ETH, XRP, XAG, XAU
    "binance",      # BTC, ETH, SOL + 數百對
    "hyperliquid",  # BTC, ETH, SOL
    "bybit",        # BTC, ETH, SOL, XRP
    "okx",          # BTC, ETH, SOL, XRP
    "kucoin",       # BTC, ETH, SOL, XRP (spot)
    "gate",         # BTC, ETH, SOL, XRP (futures)
    "mexc",         # BTC, ETH, SOL, XRP (spot)
    "bitget",       # BTC, ETH, SOL, XRP (futures)
]

# ═══════════════════════════════════════
# Section 2: BB 指標參數（indicator_calc 讀）
# ═══════════════════════════════════════
BB_TOUCH_TOL_DEFAULT = 0.005   # BTC, ETH, XAG
BB_TOUCH_TOL_XRP = 0.008       # XRP 較大容忍度
BB_WIDTH_MIN = 0.05            # 最小BB寬度過濾

# ═══════════════════════════════════════
# Section 3: 指標時間框參數（indicator_calc 讀）
# ═══════════════════════════════════════
# 每個 timeframe 嘅 BB/RSI/ADX/EMA/ATR 參數
# 改呢度 = 改 indicator_calc 嘅計算行為
TIMEFRAME_PARAMS = {
    "15m": {
        "bb_length": 20, "bb_mult": 2,
        "rsi_period": 14, "adx_period": 14,
        "ema_fast": 8, "ema_slow": 20, "atr_period": 14,
        "rsi_long": 30, "rsi_short": 70,
        "adx_range_max": 20,
        "bb_touch_tol": BB_TOUCH_TOL_DEFAULT,
        "lookback_support": 50,
    },
    "1h": {
        "bb_length": 20, "bb_mult": 2,
        "rsi_period": 14, "adx_period": 14,
        "ema_fast": 10, "ema_slow": 30, "atr_period": 14,
        "rsi_long": 35, "rsi_short": 65,
        "adx_range_max": 20,
        "bb_touch_tol": BB_TOUCH_TOL_DEFAULT,
        "lookback_support": 30,
    },
    "4h": {
        "bb_length": 20, "bb_mult": 2,
        "rsi_period": 14, "adx_period": 14,
        "ema_fast": 10, "ema_slow": 50, "atr_period": 14,
        "rsi_long": 35, "rsi_short": 65,
        "adx_range_max": 18,
        "bb_touch_tol": BB_TOUCH_TOL_DEFAULT,
        "lookback_support": 30,
    },
}

# MACD 參數（indicator_calc 讀）
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Stochastic 參數（indicator_calc 讀）
STOCH_K_PERIOD = 14
STOCH_K_SMOOTH = 1
STOCH_D_SMOOTH = 3
STOCH_OVERSOLD = 20
STOCH_OVERBOUGHT = 80

# 支撐/阻力接近容忍度（indicator_calc 讀）
SR_PROXIMITY_TOL = 0.005

# ═══════════════════════════════════════
# Section 4: Trend 策略參數（trend_strategy 讀）
# ═══════════════════════════════════════
# RSI 範圍：確認趨勢方向（1H timeframe）
TREND_RSI_LONG_LOW = 40       # LONG: RSI 下限
TREND_RSI_LONG_HIGH = 55      # LONG: RSI 上限
TREND_RSI_SHORT_LOW = 45      # SHORT: RSI 下限
TREND_RSI_SHORT_HIGH = 60     # SHORT: RSI 上限

# 回調容忍度：價格距 1H MA50 幾 % 先算 "pullback"
PULLBACK_TOLERANCE = 0.015    # 1.5%

# ═══════════════════════════════════════
# Section 5: 模式偵測閾值（settings.py 讀）
# ═══════════════════════════════════════
# mode_detector 用嚟判斷 RANGE vs TREND
MODE_RSI_TREND_LOW = 32       # RSI < 32 = trend signal
MODE_RSI_TREND_HIGH = 68      # RSI > 68 = trend signal
MODE_VOLUME_LOW = 0.50        # <50% avg = trend signal
MODE_VOLUME_HIGH = 1.50       # >150% avg = trend signal
MODE_FUNDING_THRESHOLD = 0.0007  # ±0.07%
MODE_CONFIRMATION_REQUIRED = 2   # 連續同 mode 先切換

# ═══════════════════════════════════════
# Section 6: 倉位管理（dashboard 讀）
# ═══════════════════════════════════════
MAX_POSITION_SIZE_USDT = 50
MAX_OPEN_POSITIONS = 3
RISK_PER_TRADE_PCT = 0.02

# ═══════════════════════════════════════
# Section 7: 打法 Profiles（dashboard + settings 讀）
# ═══════════════════════════════════════
# 三個打法對應不同風險偏好：
#   CONSERVATIVE → 低波動市場，RANGE only
#   BALANCED     → 中等波動，RANGE + 部分 TREND
#   AGGRESSIVE   → 高波動市場，追趨勢

TRADING_PROFILES = {
    "CONSERVATIVE": {
        "description": "保守：等待 RANGE 機會，最低風險",
        "trigger_pct":          0.03,    # 3%（較嚴格，減少噪音）
        "risk_per_trade_pct":   0.01,
        "sl_atr_mult":          1.5,
        "tp_atr_mult":          2.0,    # reserved: TP = N × ATR（未接入）
        "range_min_rr":         2.3,    # Range 最低 reward:risk
        "trend_min_rr":         3.0,    # Trend 最低 reward:risk（更嚴）
        "max_open_positions":   1,
        "allow_trend":          False,
        "allow_range":          True,
        "trend_min_change_pct": None,
    },
    "BALANCED": {
        "description": "平衡：RANGE 為主，容許部分 TREND",
        "trigger_pct":          0.025,   # 2.5%（市場 -4% 可觸發）
        "risk_per_trade_pct":   0.02,
        "sl_atr_mult":          1.2,
        "tp_atr_mult":          2.0,    # reserved: TP = N × ATR（未接入）
        "range_min_rr":         2.3,    # Range 最低 reward:risk
        "trend_min_rr":         3.0,    # Trend 最低 reward:risk（更嚴）
        "max_open_positions":   2,
        "allow_trend":          True,
        "allow_range":          True,
        "trend_min_change_pct": 5.0,
    },
    "AGGRESSIVE": {
        "description": "進取：追趨勢，最高風險最高回報",
        "trigger_pct":          0.02,    # 2%（最敏感，追趨勢用）
        "risk_per_trade_pct":   0.03,
        "sl_atr_mult":          1.0,
        "tp_atr_mult":          3.0,    # reserved: TP = N × ATR（未接入）
        "range_min_rr":         2.0,    # Range 最低 reward:risk（較鬆）
        "trend_min_rr":         2.5,    # Trend 最低 reward:risk（仍高於 Range）
        "max_open_positions":   3,
        "allow_trend":          True,
        "allow_range":          True,
        "trend_min_change_pct": 2.0,
    },
}

# 當前啟用模式（可手動改 或 由 /api/set_mode 自動寫入）
ACTIVE_PROFILE = "AGGRESSIVE"

# 是否啟用自動切換
AUTO_PROFILE_SWITCH = False

# ═══════════════════════════════════════
# Section 8: 幣種 + 引擎設定（scanner 讀）
# ═══════════════════════════════════════
# ⚠️ 修改後必須重啟 async_scanner.py 先生效
# 重啟指令：pkill -f async_scanner && sleep 2 &&
#            python3 ~/projects/axc-trading/scripts/async_scanner.py &

ASTER_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "XAGUSDT",
    "XAUUSDT",
    # 加幣種：加一行 "幣種USDT", 然後重啟掃描器
]

BINANCE_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    # 加幣種：加一行 "幣種USDT", 然後重啟掃描器
]

HL_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    # HyperLiquid 幣種（自動轉換 "BTCUSDT" → "BTC"）
]

# 掃描引擎設定
SCAN_TIMEOUT_SEC    = 30          # 單幣種超時（秒）
SCAN_MAX_WORKERS    = 8           # 並發上限
SCAN_LOG_MAX_LINES  = 500         # SCAN_LOG 保留行數
SCAN_LOG_MAX_BYTES  = 10_485_760  # scanner.log 單文件上限（10MB）
SCAN_LOG_BACKUPS    = 5           # scanner.log 保留備份數
# ⚠️  CRITICAL: 呢個係 fallback 值。Scanner 優先讀 ACTIVE_PROFILE 的 trigger_pct。
# 只有 params.py import 失敗時先用呢個值。正常情況改 TRADING_PROFILES 就得。
TRIGGER_PCT         = 0.05        # fallback 信號觸發閾值（5%）

# ═══════════════════════════════════════
# Section 9: 新聞/情緒設定（news agent 讀）
# ═══════════════════════════════════════
NEWS_ARCHIVE_WINDOW_HOURS = 6     # RSS 文章保留時間
NEWS_ANALYSIS_WINDOW_HOURS = 1    # Sentiment 分析只看最近 N 小時
NEWS_STALE_MINUTES = 30           # Sentiment 數據過期閾值
BEARISH_BLOCK_LONG_CONF = 0.70    # bearish confidence > 70% → block LONG signals
NEWS_SCRAPE_INTERVAL_MIN = 15     # LaunchAgent 排程間隔

# ═══════════════════════════════════════
# User Override: config/user_params.py（gitignored）
# 用家自訂參數放呢度，git pull 永遠唔衝突
# ═══════════════════════════════════════
import importlib.util as _ilu
import os as _os

_user_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "user_params.py")
if _os.path.exists(_user_path):
    _spec = _ilu.spec_from_file_location("_user_params", _user_path)
    _umod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_umod)
    for _name in dir(_umod):
        if not _name.startswith("_"):
            globals()[_name] = getattr(_umod, _name)
