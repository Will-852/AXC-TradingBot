# Step 4: `scripts/trader_cycle/risk/` — 風控（保安部）
> talk12 風格分析 | 2026-03-10

## 點樣搵到
```
axc-trading → scripts → trader_cycle → risk/
├── risk_manager.py       ← 安全檢查 + 持倉管理（3 個崗位）
├── position_sizer.py     ← 倉位計算 + SL/TP
└── adjust_positions.py   ← 移動止損 + TP 延伸 + 提前退出
```

---

## 1. `risk_manager.py` — 保安隊長（3 個崗位）

**比喻：** 賭場入口嘅三道安檢門。冇得傾，冇得通融。

### 崗位 A：SafetyCheckStep（Step 2）— 開門前先檢查

每個 cycle **最先跑**。任何一個觸發 → 成個 cycle 唔做嘢（`ctx.risk_blocked = True`）。

| 規則 | 閾值 | 效果 |
|------|------|------|
| 單日虧損熔斷 | > 15% 帳戶 | 停止所有交易 |
| 連輸 2 次冷卻 | 2 consecutive losses | 暫停 30 分鐘 |
| 連輸 3 次冷卻 | 3 consecutive losses | 暫停 2 小時 |

冷卻期會寫入 `TRADE_STATE.md` 嘅 `COOLDOWN_UNTIL`，跨 cycle 生效。

### 崗位 B：NoTradeCheckStep（Step 3b）— 個別 pair 禁入

唔 block 成個 cycle，只 block 個別 pair：

| 條件 | 閾值 | 效果 |
|------|------|------|
| 低成交量 | volume < 50% 平均 | 呢個 pair 唔交易 |
| 極端資金費率 | funding > ±0.2% | 呢個 pair 唔交易 |
| 同組已有倉 | group 內已有 position | 禁止開新倉 |

**Position Groups（同組限制）：**
```
crypto_correlated: [BTC, ETH]     ← 最多 1 倉（因為佢哋走勢相似）
crypto_independent: [XRP]          ← 最多 1 倉
commodity: [XAG]                   ← 最多 1 倉
```
⚠️ SOL 同 XAU 已加入 PAIRS 但未加入 POSITION_GROUPS（可能需要更新）

### 崗位 C：ManagePositionsStep（Step 8）— 巡邏已開嘅倉

| 檢查 | 閾值 | 效果 |
|------|------|------|
| 單倉虧損 | > 25% | 即刻市價平倉 |
| 持倉時間 | > 72 小時 | 強制平倉 |
| 資金費用吃利潤 | funding > 未實現 PnL 嘅 50% | 強制平倉 |
| TP 漏單 | 價格已過 TP 但倉仲開 | 強制平倉（修補機制） |

Live 模式：真正平倉 + 取消 SL/TP orders + 寫入 trades.jsonl
DRY_RUN 模式：只報告唔執行

---

## 2. `position_sizer.py` — 會計（Step 11）

**比喻：** 計數機 — 入場前計好買幾多、止損放邊、目標擺邊。

### 倉位大小

```
Step 1: 基礎風險
  risk_amount = 帳戶餘額 × risk_pct（2%）
  例：$80 × 2% = $1.60

Step 2: 信號信心調整（Yunis Collection）
  score ≥ 4.5  → risk × 1.25（高信心）
  score 3.0-4.4 → risk × 1.0（正常）
  score < 3.0  → risk × 0.6（低信心）
  上限：永遠唔超過 3%（CONFIDENCE_RISK_CAP）

Step 3: 連虧縮倉
  如果 CONSECUTIVE_LOSSES > 0 → risk × 0.7（縮 30%）

Step 4: 計算倉位
  sl_pct = SL距離 ÷ 入場價
  position_notional = risk_amount ÷ sl_pct
  position_size = notional ÷ 入場價
  margin = notional ÷ leverage
```

### SL 計算

| 策略 | SL 距離 | 例（BTC ATR=$1,131） |
|------|---------|---------------------|
| Range | 1.2 × ATR | $1,357 |
| Trend | 1.5 × ATR | $1,697 |

Pair 級別可以 override（例如 XRP 用 1.0×）

### TP 計算（策略唔同）

| 策略 | TP1 | TP2 | Fallback |
|------|-----|-----|----------|
| Range | BB 中線（平 50%） | 對面 BB band（平剩餘） | 2.3 × SL距離 |
| Trend | 下一個 S/R level（from SCAN_CONFIG） | 冇 | 3.0 × SL距離 |
| Scalp | tp_atr_mult × ATR（2.5×） | 冇 | — |

### R:R 驗證
```
reward = |TP - 入場價|
risk = SL 距離
R:R = reward ÷ risk

如果 R:R < min_rr（Range 2.3 / Trend 3.0）→ 拒絕信號
```

### Funding 調整
```
如果資金費率逆向你嘅方向：
  LONG + 正 funding → 你每 8 小時要畀錢
  SHORT + 負 funding → 你每 8 小時要畀錢

TP 會推遠嚟補償：
  Range: 預計持倉 24h = 3 個 funding period
  Trend: 預計持倉 48h = 6 個 funding period

  funding_impact = 入場價 × |funding_rate| × periods
  TP 推遠呢個數
```
呢個特別重要係因為 XAG funding = +0.214%/8h = 每日 0.64%

---

## 3. `adjust_positions.py` — 戰場護理員（Step 8.5）

**比喻：** 比賽進行中嘅替補教練 — 根據場上情況微調。

**3 個操作必須按順序跑：** Trailing SL → TP Extension → Early Exit
（如果 Early Exit 先跑，倉位已平，後面嘅操作會失敗）

### 操作 1：Trailing SL（移動止損）

| 條件 | 動作 | 意思 |
|------|------|------|
| 利潤 > 1×ATR | SL 移去入場價 | 保本（最差打和） |
| 利潤 > 2×ATR | SL 移去入場價 + 1×ATR | 鎖定利潤 |

**安全規則：**
- SL 只可以往有利方向移（LONG: 只升不降 / SHORT: 只降不升）
- 新 SL order 失敗 → 自動恢復舊 SL
- 恢復也失敗 → `CRITICAL` 錯誤（冇 SL 保護！）

### 操作 2：TP Extension（延伸止盈）

所有條件必須全部通過：

| 條件 | 閾值 |
|------|------|
| 價格距 TP | < 0.3% |
| ADX | > 25（趨勢夠強） |
| RSI | LONG < 75 / SHORT > 25（仲有空間） |
| Volume | > 1.0（仲有人玩） |
| 已延伸次數 | < 2（上限） |

效果：TP 推遠 1×ATR
失敗保護：新 TP 落單失敗 → 恢復舊 TP

### 操作 3：Early Exit（提前退出）

| 情況 | 觸發條件 |
|------|---------|
| 動能反轉（LONG） | RSI > 70 + MACD histogram < 0 |
| 動能反轉（SHORT） | RSI < 30 + MACD histogram > 0 |
| 成交量逆向衝擊 | volume > 2× + 價格逆向移動 > 0.2% |

退出後設定 **re-entry 資格**：
- 3 cycles（≈1.5h）內可以用 +0.5 score 加分重新入場
- 寫入 TRADE_STATE 跨 cycle 保存
- 到期自動清除

---

## 數據流概覽

```
每個 Cycle 嘅風控流程：

Step 2:  SafetyCheckStep    → 熔斷/冷卻？     → risk_blocked
Step 3b: NoTradeCheckStep   → 個別 pair 禁入？ → no_trade_reasons
Step 8:  ManagePositionsStep → 持倉要唔要平？  → 平倉
Step 8.5: AdjustPositionsStep → 移 SL？延 TP？ → 修改 orders
Step 9:  EvaluateSignalsStep → 出信號          → signals
Step 11: SizePositionStep   → 計倉位 + R:R    → 落單參數
```

---

## ⚠️ 分析中發現嘅問題

### 🟡 POSITION_GROUPS 缺 SOL + XAU
```python
POSITION_GROUPS = {
    "crypto_correlated": ["BTCUSDT", "ETHUSDT"],  # max 1
    "crypto_independent": ["XRPUSDT"],              # max 1
    "commodity": ["XAGUSDT"],                        # max 1
}
```
SOL 同 XAU 冇 group → 唔受同組限制 → 理論上可以同時開 BTC+ETH+SOL（3 個 crypto 倉）

**建議：**
- SOL 加入 `crypto_correlated`（同 BTC/ETH 走勢相關）或新建 `crypto_sol` group
- XAU 加入 `commodity`（同 XAG 一組）

### 🟢 失敗保護完善
- Trailing SL 失敗 → 恢復舊 SL ✅
- TP Extension 失敗 → 恢復舊 TP ✅
- 每個操作獨立 try/except → 一個失敗唔影響其他 ✅

---

## 自檢問題

1. **你而家 CONSECUTIVE_LOSSES: 1** → 再輸一次就觸發 30min 冷卻
2. **BTC SHORT 倉距 SL $1,769（2.5%）** → 未觸發 trailing SL（需要利潤 > 1×ATR = $1,131 先）
3. **SOL 冇 POSITION_GROUP** → 如果 BTC 有倉 + SOL 出信號，系統會照開（冇同組限制）
4. **TP Extension 最多 2 次** → 之後就等 TP 或 SL hit
5. **funding 調整只影響 TP 計算** → 唔影響 SL 位置
