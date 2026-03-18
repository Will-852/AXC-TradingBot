"""
polymarket_params.py — 用戶可調 Polymarket 參數
改呢個文件覆蓋 polymarket/config/settings.py 嘅默認值。
同 config/params.py 同樣 pattern。

所有大寫常數會自動 override settings.py 嘅同名常數。
Bankroll: live balance（pipeline 每次 cycle 查 CLOB API）

落注規則（2026-03-19）：
- 每次落注 = 實際資金 1%（Kelly 會進一步調細）
- 每個市場（完整賭局）最多 5% 實際資金
- 即係要連輸 20 局先清零
"""

# ─── Risk Limits（動態 bankroll，按比例） ───
MAX_TOTAL_EXPOSURE = 0.30      # 30% 最大同時持倉
MAX_PER_MARKET = 0.05          # 5% per market（一個完整賭局）
KELLY_MAX_BET_USDC = 7.50      # 每注上限（~5% of $150）
KELLY_MIN_BET_USDC = 1.0       # 每注最低 $1（1% of ~$100-150）

# ─── Crypto 15M ───
CRYPTO_15M_MAX_BET_USDC = 7.50 # 15M cap = 同 KELLY_MAX 一致
CRYPTO_15M_MIN_EDGE_PCT = 0.065  # 6.5% edge after fees

# ─── Market Scanning ───
MIN_LIQUIDITY_USDC = 1000      # 最低 $1000 流動性
MIN_EDGE_PCT = 0.10            # 最低 10% edge（非 15M）

# ─── Cycle ───
CYCLE_INTERVAL_MIN = 1          # 1 min cycle → positions checked every min, heavy steps time-gated

# ─── Take Profit（token price 級別） ───
TAKE_PROFIT_TOKEN_PRICE = 0.93  # token price ≥ 93% → 鎖定利潤走人（避免黑天鵝）
