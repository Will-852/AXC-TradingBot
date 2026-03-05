# config/params.py
# ═══════════════════════════════════════════════════════
# 設計原則（方案3：清晰分工）
# ═══════════════════════════════════════════════════════
#
# 呢個文件 = 用戶可見參數層
# 讀取者：
#   dashboard.py    → 顯示 UI 設定（get_params() 動態讀全部）
#   settings.py     → profile override（只讀 TRADING_PROFILES 4 個 key）
#   indicator_calc  → BB 參數
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
#   tp_atr_mult         → RANGE_MIN_RR + TREND_MIN_RR  ⚠️ BUG: 概念混用，待修
#   max_open_positions  → MAX_CRYPTO_POSITIONS
#
# ⚠️ 加新 profile key 前：先 grep settings.py 確認有消費者
# ═══════════════════════════════════════════════════════

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
        "trigger_pct":          0.03,    # 3%（較嚴格，減少噪音）
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
        "trigger_pct":          0.025,   # 2.5%（市場 -4% 可觸發）
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
        "trigger_pct":          0.02,    # 2%（最敏感，追趨勢用）
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
ACTIVE_PROFILE = "AGGRESSIVE"

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
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    # 加幣種：加一行 "幣種USDT", 然後重啟掃描器
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
# Section 6: 新聞/情緒設定（news agent 讀）
# ═══════════════════════════════════════
NEWS_ARCHIVE_WINDOW_HOURS = 6     # RSS 文章保留時間
NEWS_ANALYSIS_WINDOW_HOURS = 1    # Sentiment 分析只看最近 N 小時
NEWS_STALE_MINUTES = 30           # Sentiment 數據過期閾值
NEWS_SCRAPE_INTERVAL_MIN = 15     # LaunchAgent 排程間隔
