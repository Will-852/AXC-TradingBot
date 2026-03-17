"""
polymarket_params.py — 用戶可調 Polymarket 參數
改呢個文件覆蓋 polymarket_cycle/config/settings.py 嘅默認值。
同 config/params.py 同樣 pattern。

所有大寫常數會自動 override settings.py 嘅同名常數。
"""

# ─── Risk Limits（初始保守設定，熟悉後可調高） ───
MAX_TOTAL_EXPOSURE = 0.30      # 30% bankroll cap
MAX_PER_MARKET = 0.10          # 10% per market
KELLY_MAX_BET_USDC = 100.0     # 每注最高 $100

# ─── Market Scanning ───
MIN_LIQUIDITY_USDC = 1000      # 最低 $1000 流動性
MIN_EDGE_PCT = 0.10            # 最低 10% edge

# ─── Cycle ───
CYCLE_INTERVAL_MIN = 60        # 60 min cycle
