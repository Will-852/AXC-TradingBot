"""
settings.py — Polymarket pipeline 常數、路徑、閾值
同 trader_cycle/config/settings.py 同樣 pattern，但預測市場專用。
"""

import os
from datetime import timezone, timedelta

# ─── Paths ───
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
SECRETS_PATH = os.path.join(AXC_HOME, "secrets", ".env")
_SHARED = os.path.join(AXC_HOME, "shared")
LOG_DIR = os.path.join(AXC_HOME, "polymarket", "logs")

# Polymarket-specific state files
POLY_STATE_PATH = os.path.join(_SHARED, "POLYMARKET_STATE.json")
POLY_MARKETS_CACHE_PATH = os.path.join(_SHARED, "polymarket_markets.json")
POLY_TRADE_LOG_PATH = os.path.join(LOG_DIR, "poly_trades.jsonl")
POLY_WAL_PATH = os.path.join(_SHARED, ".poly_wal.jsonl")
POLY_PIPELINE_LOCK_PATH = os.path.join(_SHARED, ".poly_pipeline")
POLY_CREDS_CACHE_PATH = os.path.join(AXC_HOME, "secrets", ".poly_api_creds.json")

# ─── Timezone ───
HKT = timezone(timedelta(hours=8))

# ─── Polymarket API ───
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

# ─── Cycle Timing ───
CYCLE_INTERVAL_MIN = 60  # 60 分鐘 cycle（預測市場變動慢）

# ─── Market Scanning ───
MAX_MARKETS_TO_SCAN = 50       # Gamma API 拉幾多個市場
MAX_MARKETS_FOR_AI = 5         # 每 cycle 最多做 AI 評估嘅市場數
MIN_LIQUIDITY_USDC = 1000      # 最低流動性 $1000
MIN_VOLUME_24H = 500           # 最低 24h 成交量
MIN_DAYS_TO_RESOLUTION = 2     # <2 日到期唔買
MAX_DAYS_TO_RESOLUTION = 180   # >180 日太遠唔買
PRICE_FLOOR = 0.05             # 價格低於 5% 唔買（太低 = 幾乎冇可能）
PRICE_CEILING = 0.95           # 價格高於 95% 唔買（太高 = 冇 edge）

# ─── Edge Finding (AI) ───
MIN_EDGE_PCT = 0.10            # 最低 edge 10% 先考慮
EDGE_CONFIDENCE_THRESHOLD = 0.6  # AI confidence 最低 60%
AI_MODEL = "claude-sonnet-4-6"   # Claude sonnet for probability estimation
AI_MAX_TOKENS = 1024
AI_TEMPERATURE = 0.3           # 低 temp = 更穩定概率估計

# ─── Crypto 15M Binary Markets ───
CRYPTO_15M_ENABLED_COINS = ["bitcoin"]           # MVP: BTC only
CRYPTO_15M_MIN_LEAD_MIN = 15                     # 市場開始前至少 15 min
CRYPTO_15M_MAX_LEAD_MIN = 50                     # 唔超過 50 min（within one cycle）
CRYPTO_15M_MIN_EDGE_PCT = 0.065                   # net >5% after ~1.5% taker fee
CRYPTO_15M_CONFIDENCE_THRESHOLD = 0.55
CRYPTO_15M_INDICATOR_THRESHOLD = 0.55            # P(Up) 需要偏離 0.5 至少 5%
CRYPTO_15M_MAX_ASSESSMENTS = 3                   # 每 cycle 最多 3 個 AI 評估
CRYPTO_15M_MIN_LIQUIDITY = 200                   # 15M 市場流動性門檻較低
CRYPTO_15M_MAX_BET_USDC = 50.0                   # 快市場 → 細注

# ─── CVD Strategy (Cumulative Volume Delta) ───
CVD_ENABLED = True                       # Enable CVD signal source for crypto_15m
CVD_MIN_EDGE_PCT = 0.065                 # Same threshold as indicator path
CVD_LOOKBACK_MINUTES = 20                # aggTrades lookback (covers 15m windows + buffer)
CVD_STRENGTH_SCALE = 2.0                 # tanh scaling factor
CVD_MIN_PRICE_CHANGE_USD = 5.0           # BTC noise filter for divergence detection

# ─── Hyperliquid Hedge ───
HEDGE_ENABLED = False                    # Phase 3 完成驗證後先開
HEDGE_USD = 100.0                        # HL notional size per hedge ($100 at leverage)
HEDGE_LEVERAGE = 20                      # Conservative (Moon Dev 用 40x)
HEDGE_SYMBOL = "BTC"                     # Only BTC for now
HEDGE_AUTO_CLOSE_ON_RESOLVE = True       # Auto-close HL hedge when Poly market resolves
HEDGE_CATEGORIES = ["crypto_15m"]        # Only hedge crypto_15m trades

# ─── Spread / Liquidity ───
MAX_SPREAD_PCT = 0.08          # 最大可接受 bid-ask spread 8%
MIN_BOOK_DEPTH_USDC = 500      # 最低 order book 深度

# ─── Risk — Hard Limits (非 negotiable) ───
MAX_TOTAL_EXPOSURE = 0.30      # 最多 30% bankroll 放預測市場
MAX_PER_MARKET = 0.10          # 每個市場最多 10%
MAX_PER_CATEGORY = 0.20        # 每類最多 20%
MAX_DAILY_LOSS_PCT = 0.15      # 日損 15% 熔斷
MAX_OPEN_POSITIONS = 5         # 最多 5 個同時持倉
MAX_SIGNALS_PER_CYCLE = 3      # 每 cycle 最多 3 個信號

# ─── Kelly Criterion ───
KELLY_FRACTION = 0.5           # 半 Kelly（保守）
KELLY_MIN_BET_USDC = 5.0       # 最低下注 $5
KELLY_MAX_BET_USDC = 100.0     # 最高下注 $100（初期）

# ─── Position Management ───
EXIT_PROBABILITY_DRIFT = 0.15  # 概率漂移 >15% 觸發 exit review
PROFIT_TAKE_PCT = 0.50         # 持倉升 50% → 考慮止盈
LOSS_CUT_PCT = 0.30            # 持倉跌 30% → 考慮止損

# ─── Cooldown ───
COOLDOWN_AFTER_LOSS_MIN = 60   # 虧損後冷卻 60 分鐘
COOLDOWN_AFTER_CIRCUIT_MIN = 360  # 熔斷後冷卻 6 小時

# ─── Weather Data ───
OPEN_METEO_BASE = "https://api.open-meteo.com/v1"
OWM_BASE = "https://api.openweathermap.org/data/2.5"
OWM_API_KEY = os.environ.get("OWM_API_KEY", "")

# Forecast uncertainty σ (°C) by lead days — conservative estimates
# Higher σ → flatter distribution → more conservative probability
WEATHER_SIGMA_BY_LEAD = {1: 1.2, 2: 1.8, 3: 2.3, 4: 2.8, 5: 2.8, 6: 3.5, 7: 3.5}

# Confidence decay by lead days — shorter lead = more confident
WEATHER_CONFIDENCE_BY_LEAD = {
    1: 0.90, 2: 0.80, 3: 0.70, 4: 0.60, 5: 0.55, 6: 0.50, 7: 0.45,
}

# Weather edge threshold — lower than crypto because forecast-based (more deterministic)
WEATHER_MIN_EDGE_PCT = 0.08
WEATHER_MAX_LEAD_DAYS = 3         # Lead >3d σ too large → unreliable edge, hurts Sharpe
WEATHER_ENTRY_PRICE_CAP = 0.70    # Entry >$0.70 = <1.43x odds, one miss wipes gains
WEATHER_MAX_ASSESSMENTS = 15      # Weather = zero AI cost, scan more for edge

# ─── GTO (Game Theory Optimal) ───
GTO_ADVERSE_BLOCK_THRESHOLD = 0.80    # adverse selection > 80% → block
GTO_NASH_SKIP_THRESHOLD = 0.90        # market at equilibrium + small edge → skip
GTO_UNEXPLOITABILITY_MIN = 0.30       # below 30% → too exploitable
GTO_KELLY_SCALE_ENABLED = True        # scale Kelly by unexploitability
GTO_LIVE_EVENT_BLOCK = True           # always block live events
GTO_NEWS_DRIVEN_MAX_OFFSET = 0.03     # max 3% offset for news-driven limits

# ─── Telegram ───
# 共用 trader_cycle 嘅 Telegram 設定
TG_BOT_TOKEN = ""  # loaded from settings at runtime
TG_CHAT_ID = ""    # loaded from settings at runtime

# ─── Paper Trading Gate ───
POLY_PAPER_GATE_HOURS = 48
POLY_PAPER_GATE_FILE = os.path.join(LOG_DIR, "poly_paper_gate_start.txt")

# ─── Load user params override ───
import logging as _logging
_log = _logging.getLogger(__name__)

try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_poly_params", os.path.join(AXC_HOME, "polymarket", "config", "params.py")
    )
    if _spec and _spec.loader:
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        # Override any matching constants
        for _name in dir(_mod):
            if _name.isupper() and not _name.startswith("_"):
                globals()[_name] = getattr(_mod, _name)
        del _mod
    del _ilu, _spec
except FileNotFoundError:
    pass  # polymarket_params.py optional
except Exception as _e:
    _log.warning("polymarket_params.py load error: %s", _e)

# ─── Load Telegram config from .env ───
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(SECRETS_PATH)
except ImportError:
    pass  # dotenv optional; env vars may already be set
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
