# config/params.py
# ═══════════════════════════════════════════════════════
# 設計原則（方案3：清晰分工）
# ═══════════════════════════════════════════════════════
#
# 呢個文件 = 用戶可見參數層
# 讀取者：
#   dashboard.py    → 顯示 UI 設定（get_params() 動態讀全部）
#   settings.py     → 讀 ACTIVE_PROFILE + symbols + mode detection
#   indicator_calc  → BB + TIMEFRAME + MACD + STOCH + SR 參數
#   async_scanner   → 幣種 + 掃描設定
#   weekly_review   → trigger_pct
#
# 唔放喺呢度：
#   交易引擎內部邏輯參數 → scripts/trader_cycle/config/settings.py
#   Profile 參數         → config/profiles/（_base.py + per-profile override）
#   敏感 API keys        → secrets/.env
#
# ⚠️ 加新 profile key → 改 config/profiles/_base.py
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
        "bb_width_squeeze": 0.008,
        "lookback_support": 50,
    },
    "1h": {
        "bb_length": 20, "bb_mult": 2,
        "rsi_period": 14, "adx_period": 14,
        "ema_fast": 10, "ema_slow": 30, "atr_period": 14,
        "rsi_long": 40, "rsi_short": 60,
        "adx_range_max": 25,   # 2026-03-13: was 20, diagnostic showed 246/380 candles blocked
        "bb_touch_tol": BB_TOUCH_TOL_DEFAULT,
        "bb_width_squeeze": 0.012,
        "lookback_support": 30,
    },
    "4h": {
        "bb_length": 20, "bb_mult": 2,
        "rsi_period": 14, "adx_period": 14,
        "ema_fast": 10, "ema_slow": 50, "atr_period": 14,
        "rsi_long": 35, "rsi_short": 65,
        "adx_range_max": 25,   # 2026-03-11 optimizer: 6/6 viable ≥22, shrinkage→25 (was 18)
        "bb_touch_tol": BB_TOUCH_TOL_DEFAULT,
        "bb_width_squeeze": 0.020,
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

# OBV 參數（indicator_calc 讀）
OBV_EMA_PERIOD = 20

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
# 2026-03-11 optimizer Stage1: 6/6 viable configs 用 ≥0.020，收縮後 0.025
PULLBACK_TOLERANCE = 0.025    # 2.5% (was 1.5%, optimizer shrinkage)

# Trend 入場最少 KEY 數（4=全部確認，3=允許 1 個唔 pass）
# 2026-03-11 optimizer Stage1: 5/6 viable configs 用 3
TREND_MIN_KEYS = 3            # (was hardcoded 4)

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
# Section 6: Dashboard + 倉位管理
# ═══════════════════════════════════════
DASHBOARD_PORT = 5566          # 唯一定義點。改 port → 只改呢度
MAX_POSITION_SIZE_USDT = 50

# ═══════════════════════════════════════
# Section 7: Profile 設定
# ═══════════════════════════════════════
# Profile 參數已搬到 config/profiles/（conservative.py, balanced.py, aggressive.py）
# _base.py = 預設值，每個 profile 只 override 差異
# user_params.py 唔再支援 TRADING_PROFILES override

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
    "POLUSDT",
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
# 只有 params.py import 失敗時先用呢個值。正常情況改 config/profiles/ 就得。
TRIGGER_PCT         = 0.05        # fallback 信號觸發閾值（5%）

# ═══════════════════════════════════════
# Section 9: 新聞/情緒設定（news agent 讀）
# ═══════════════════════════════════════
NEWS_ARCHIVE_WINDOW_HOURS = 6     # RSS 文章保留時間
NEWS_ANALYSIS_WINDOW_HOURS = 1    # Sentiment 分析只看最近 N 小時
NEWS_STALE_MINUTES = 30           # Sentiment 數據過期閾值
BEARISH_BLOCK_LONG_CONF = 0.70    # bearish confidence > 70% → block LONG signals
NEWS_SCRAPE_INTERVAL_MIN = 5      # RSS 抓取間隔（分鐘）
NEWS_ANALYSIS_INTERVAL_MIN = 15   # Sentiment 分析間隔（分鐘）

# ═══════════════════════════════════════
# Section 10: HMM Regime Detection
# ═══════════════════════════════════════
HMM_ENABLED = True
HMM_N_STATES = 3
HMM_WINDOW = 500           # 4H candles for training (~83 days)
HMM_REFIT_INTERVAL = 24    # refit every ~4 days
HMM_MIN_CONFIDENCE = 0.6   # below this → HMM vote = UNKNOWN
HMM_MIN_SAMPLES = 100      # cold start threshold
HMM_CRASH_THRESHOLD = 0.7  # CRASH override needs higher confidence

# ═══════════════════════════════════════
# Section 11: CRASH Strategy
# ═══════════════════════════════════════
CRASH_RISK_PCT = 0.01       # 1% (vs 2% normal)
CRASH_LEVERAGE = 5          # lower leverage
CRASH_SL_ATR_MULT = 2.0     # wider SL
CRASH_MIN_RR = 1.5
CRASH_RSI_ENTRY = 60        # 2026-03-13: was 75, RSI>75 never triggers in crash (median=48.6). 60=catch relief rallies
CRASH_VOLUME_MIN = 1.5      # 2026-03-13: was 2.0, only 12.5% of crash candles had vol>2.0

# ═══════════════════════════════════════
# Section 12: Regime Engine
# ═══════════════════════════════════════
# Dashboard dropdown 切換 preset，正交於 Risk Profile（N×M 組合）
REGIME_PRESETS = {
    "classic":    {"REGIME_ENGINE": "votes_hmm", "CP_ENABLED": False},
    "classic_cp": {"REGIME_ENGINE": "votes_hmm", "CP_ENABLED": True},
    "bocpd":      {"REGIME_ENGINE": "bocpd_cp",  "CP_ENABLED": False},
    "full":       {"REGIME_ENGINE": "bocpd_cp",  "CP_ENABLED": True},
}
ACTIVE_REGIME_PRESET = "classic"
# Derived from preset（settings.py getattr 繼續正常運作）
REGIME_ENGINE = REGIME_PRESETS[ACTIVE_REGIME_PRESET]["REGIME_ENGINE"]
CP_ENABLED = REGIME_PRESETS[ACTIVE_REGIME_PRESET]["CP_ENABLED"]

# ═══════════════════════════════════════
# Section 13: BOCPD
# ═══════════════════════════════════════
BOCPD_HAZARD_RATE = 0.02           # 1/50 = 每 ~50 根 4H candle 期望 1 次變點 (~8 日)
BOCPD_MAX_RUN_LENGTH = 200         # truncation（記憶體 + 速度）
BOCPD_MIN_SAMPLES = 30             # cold start gate（比 HMM 100 少，BOCPD 更快收斂）
BOCPD_CHANGEPOINT_THRESHOLD = 0.3  # P(r=0) > 0.3 → confidence 低

# ═══════════════════════════════════════
# Section 14: Conformal Prediction (ATR)
# ═══════════════════════════════════════
CP_ALPHA = 0.10                    # 90% coverage
CP_MIN_SCORES = 20                 # bank 最少 scores 先用
CP_MAX_SCORES = 200                # 每個 bank 最多存幾多
CP_INFLATION_FACTOR = 1.5          # cold start inflation

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
