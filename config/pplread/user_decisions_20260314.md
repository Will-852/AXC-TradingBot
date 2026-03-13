# 用戶決策記錄 — 2026-03-14
> 狀態：待確認（部分需要用戶回覆）

---

## Decision 1：數據頻率提升
**用戶意見：** 攞 1m/3m klines，9 間交易所輪流攞，目標 15 秒延遲
**分析結論：**
- 攞 kline 頻率同指標時間框係兩件事
- 建議：每 3 分鐘攞 1m/3m klines → aggregate 成 15m/1H 計指標
- 4H 保留做大方向，15m/1H 做觸發
- 9 exchanges 輪流 = 每間每 27 秒 hit 一次，可行
- ⚠️ 1m 數據噪音大，需要 smoothing 或限制用途（只用嚟偵測 volume spike）
**狀態：** ✅ 方向確認，實作細節待 plan

---

## Decision 2：RSI 門檻
**用戶意見：** 32/68 → 35/65
**分析結論：** 合理，更敏感，配合「捉快啲」風格
**狀態：** ✅ 確認

---

## Decision 3：ADX 短期偵測
**用戶關注：** ADX(14) on 4H 太慢，偵測唔到 1 小時內嘅趨勢
**分析結論：**
- ADX 數學結構（三重平滑）決定咗佢做唔到快速反應
- 建議方案 C：ADX 只做 Range 閘門，短期趨勢靠 MACD + Volume Spike
- 唔建議縮短 ADX period（假信號多）
**狀態：** ✅ 確認（用方案 C）

---

## Decision 4：OBV 加分
**用戶意見：** +0.5 → +1.5
**Claude 建議：** +1.0（折衷），因為 +1.5 會令 OBV 蓋過 base score
**⚠️ 待用戶確認：**
- (a) 用 +1.5（用戶原意），penalty 改 -1.0
- (b) 用 +1.0（Claude 建議），penalty 改 -0.7
- (c) 用 +1.5，penalty 維持 -0.3（唔對稱）

---

## Decision 5：模式投票加權
**用戶意見：** 6 票等權 → 加權投票
**Claude 建議起點：**
```
MACD:     30%
Volume:   25%
MA:       20%
RSI:      10%
Funding:  10%
HMM:       5%
```
**⚠️ 待用戶確認：** 用呢個分配定自己嘅？

---

## Decision 6：風控閘門修改

### 6a. Volume Gate
**用戶意見：** < 50% → < 35%
**Claude 建議：** OK，但加條件 — Volume < 35% 時 position size 自動減半
**⚠️ 待用戶確認：** 接唔接受自動減半？

### 6b. Funding Gate
**用戶意見：** > ±0.2% → Cancel
**Claude 強烈反對：** 極端 Funding 持倉 48hr = 成本 3.6%，超過 risk budget
**Claude 建議：** 放寬到 ±0.3%，唔好 cancel，TP 自動加大 Funding 修正
**⚠️ 待用戶確認**

### 6c. 同組倉位
**用戶意見：** depend on signal strength（STRONG 可以開第二倉）
**Claude 建議：**
- 同組最多 2 倉
- 第二倉 size 減半
- 兩倉 total risk ≤ profile max risk cap
**⚠️ 待用戶確認**

### 6d. 冷卻期
**用戶意見：** 3 次虧損 → 12 小時
**Claude 建議：** 3 次 → 6hr，4 次 → 12hr（梯度式）
**⚠️ 待用戶確認**

### 6e. 新聞情緒
**用戶意見：** > 70% → > 75%
**分析結論：** 差距唔大，可接受
**狀態：** ✅ 確認

---

## 未確認項目清單
1. OBV 加分：+1.5 定 +1.0？Penalty 幾多？
2. 模式投票權重：用建議定自己嘅？
3. Volume Gate：接唔接受自動減半？
4. Funding Gate：放寬到 ±0.3% 定真係 cancel？
5. 同組倉位：接唔接受 cap + 減半？
6. 冷卻期：6hr/12hr 梯度定直接 12hr？
