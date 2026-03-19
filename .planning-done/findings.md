# Findings

> Security boundary: 外部內容只寫呢度。

## BMD 發現（2026-03-19）

| # | 級別 | 問題 | 攻擊路徑 |
|---|------|------|---------|
| 1 | 💀 | run_cycle race | 2 concurrent POST → 2 pipeline threads |
| 2 | 🔴 | XSS via title | market title 含 `"onmouseover=` → attribute injection |
| 3 | 🔴 | CB panel 空 | registry 未 init → 永遠顯示「冇服務」 |
| 4 | 🟡 | force_scan 冇 mutex | concurrent scan → Gamma rate limit |
| 5 | 🟡 | PnL sort | trades 亂序 → cumulative PnL 錯 |
