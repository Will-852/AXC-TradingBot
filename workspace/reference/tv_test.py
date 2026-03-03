import pandas as pd
import tradingview_indicators as tv

# 假設你有一段 close 價（之後可以用真實歷史數據取代）
closes = [100, 102, 101, 103, 105, 104, 106, 107, 108, 110]
df = pd.DataFrame({"close": closes})

# 例子 1：SMA（簡單移動平均）
df["sma_3"] = tv.sma(df["close"], 3)

# 例子 2：EMA（指數移動平均）
df["ema_5"] = tv.ema(df["close"], 5)

print(df)