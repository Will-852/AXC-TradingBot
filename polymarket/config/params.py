"""
polymarket_params.py — 用戶可調 Polymarket 參數
改呢個文件覆蓋 polymarket/config/settings.py 嘅默認值。
同 config/params.py 同樣 pattern。

所有大寫常數會自動 override settings.py 嘅同名常數。
Bankroll: $100 USDC（2026-03-17 初始設定）
"""

# ─── Risk Limits（$100 bankroll 保守設定） ───
MAX_TOTAL_EXPOSURE = 0.30      # 30% = $30 最大同時持倉
MAX_PER_MARKET = 0.10          # 10% = $10 per market
KELLY_MAX_BET_USDC = 10.0      # 每注最高 $10（$100 bankroll）
KELLY_MIN_BET_USDC = 2.0       # 每注最低 $2

# ─── Crypto 15M ───
CRYPTO_15M_MAX_BET_USDC = 10.0 # 15M 快市場：$10 cap（同 KELLY_MAX 一致）
CRYPTO_15M_MIN_EDGE_PCT = 0.065  # 6.5% edge after fees

# ─── Market Scanning ───
MIN_LIQUIDITY_USDC = 1000      # 最低 $1000 流動性
MIN_EDGE_PCT = 0.10            # 最低 10% edge（非 15M）

# ─── Cycle ───
CYCLE_INTERVAL_MIN = 15        # 15 min cycle（BTC 15M 需要更快）
