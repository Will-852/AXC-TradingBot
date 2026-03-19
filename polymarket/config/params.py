"""
polymarket_params.py — 用戶可調 Polymarket 參數
改呢個文件覆蓋 polymarket/config/settings.py 嘅默認值。
同 config/params.py 同樣 pattern。

所有大寫常數會自動 override settings.py 嘅同名常數。
Bankroll: live balance（pipeline 每次 cycle 查 CLOB API）

落注規則（2026-03-19 更新）：
- 每次落注 = 實際資金 1%（Kelly cap，唔係 Kelly target）
- 每個市場（完整賭局 = 一個城市+日期 或 一個 15M window）最多 10%
- 即係要連輸 100 局先清零（1% per bet）
- BTC 15M 同天氣用同一套規則
"""

# ─── Risk Limits（動態 bankroll，按比例） ───
MAX_TOTAL_EXPOSURE = 0.30      # 30% 最大同時持倉
MAX_PER_BET = 0.01             # 1% per individual bet（Kelly cap）
MAX_PER_MARKET = 0.10          # 10% per market/event（同一城市+日期 或 同一 15M window）
KELLY_MAX_BET_USDC = 50.0      # 絕對上限（fallback，百分比 cap 先生效）
KELLY_MIN_BET_USDC = 1.0       # 每注最低 $1

# ─── Crypto 15M ───
CRYPTO_15M_MAX_BET_USDC = 50.0 # 15M 絕對上限（百分比 cap 先生效）
CRYPTO_15M_MIN_EDGE_PCT = 0.065  # 6.5% edge after fees

# ─── Market Scanning ───
MAX_MARKETS_TO_SCAN = 300      # 加大掃描範圍，天氣市場流動性低排唔到 top 50
MIN_LIQUIDITY_USDC = 1000      # 最低 $1000 流動性（天氣/15M 有各自嘅門檻）
MIN_EDGE_PCT = 0.10            # 最低 10% edge（非 15M，非 weather）

# ─── Cycle ───
CYCLE_INTERVAL_MIN = 1          # 1 min cycle → positions checked every min, heavy steps time-gated

# ─── Take Profit（token price 級別） ───
TAKE_PROFIT_TOKEN_PRICE = 0.93  # token price ≥ 93% → 鎖定利潤走人（避免黑天鵝）
