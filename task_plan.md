# Task Plan: Dashboard NG UI Enhancement
> Created: 2026-03-26
> Goal: 4 個 UI 改進 — trade modal / hidden indicators / notification overlay / button colors

## Risk Legend
- 🔴 **2CHECK** — 涉及交易邏輯 / 落單 / 資金流
- 🟡 **VERIFY** — UI 行為改變，需要視覺確認
- 🟢 **SAFE** — 純 CSS / 文字 / 顏色

---

## Phase 1: Trade Modal Overhaul — `pending` 🔴

**File:** `scripts/dashboard_ng/components/trade_modal.py` (235 lines)

### 現狀問題
- USDT Amount → Qty 計算公式 `(USDT × leverage) / price` 但冇顯示 notional
- 冇顯示 margin mode（CROSSED）
- 冇顯示 margin % of balance
- 冇 real-time validation（qty < min_qty 時冇警告）
- Button 顏色同其他 component 一樣難分辨

### 改動清單
1. **加 margin mode 顯示**（line 70 附近）— 顯示 "Margin: CROSSED" badge
2. **加 notional value 顯示** — calc_qty() 時同時更新 "Notional: $XXX"
3. **加 margin % 顯示** — "(XX% of balance)"
4. **加 real-time validation** — qty < min_qty 或 notional < min_notional 時紅字提示
5. **改善 input flow** — USDT Amount 同 Quantity 雙向互算（改 qty 反算 USDT）
6. **Order confirmation summary** — submit 前顯示完整 order summary

### 參考（Binance/Bybit UX pattern）
- Binance: USDT margin input → auto-calc qty，顯示 "Cost: X USDT"
- Bybit: slider + input 雙模式，顯示 "Order Value: $XXX"
- 共通：margin mode badge 喺 header，notional 實時更新

---

## Phase 2: Expose Hidden Indicators — `pending` 🟡

**File:** `canvas/backtest.html`

### Pattern（從現有 code 提取）

每個 indicator 需要 3 個改動：

**A. registerIndicator（~line 2410+ block）**
```javascript
klinecharts.registerIndicator({
  name: 'AXC_XXX',
  figures: [{ key: 'xxx', title: 'XXX', type: 'line' }],
  calc: function(dataList) {
    return dataList.map(function(d) {
      var ind = indicatorData[d.timestamp];
      if (ind && ind.xxx != null) return { xxx: ind.xxx };
      return {};  // 冇 server data 就唔畫（唔自己算）
    });
  },
  styles: { lines: [{ color: '#XXXXXX', size: 1.5 }] }
});
```

**B. Toggle checkbox（~line 1155-1289 block）**
```html
<label data-tip="tooltip"><input type="checkbox" onchange="toggleIndicator('AXC_XXX')"> Label</label>
```

**C. applyIndicatorData + addIndicatorToChart**
- `applyIndicatorData()` line 3264: 加入 names array
- `addIndicatorToChart()` line 3284: sub-pane 名加入 isSubPane 判斷
- `activeIndicators` default 字典加 key（line 1751）

### 6 個 indicators 詳細 spec

| # | Name | Figures | Data keys | Type | Color | 人體比喻 |
|---|------|---------|-----------|------|-------|---------|
| 1 | AXC_SR | upper(S), lower(R) | `rolling_high`, `rolling_low` | overlay | red/green dashed | 天花板+地板 |
| 2 | AXC_ADX | adx, di+, di- | `adx`, `di_plus`, `di_minus` | sub-pane | orange/green/red | 肌肉力量測量儀 |
| 3 | AXC_OBV | obv, obv_ema | `obv`, `obv_ema` | sub-pane | cyan/yellow | 血液流量 |
| 4 | AXC_BBWP | bbwp | `bb_width_pctl` | sub-pane | purple | 呼吸頻率百分位 |
| 5 | AXC_VOLR | volr | `volume_ratio` | sub-pane | amber | 心跳同平時比 |
| 6 | AXC_ZROB | z | `z_robust` | sub-pane | pink | 體溫偏離程度 |

### 執行順序（2 批）
**Batch 1（最有用 — 3 個）：** AXC_SR + AXC_ADX + AXC_OBV
**Batch 2（oscillators — 3 個）：** AXC_BBWP + AXC_VOLR + AXC_ZROB

---

## Phase 3: Fix Notification Bell Overlay — `pending` 🟢

**File:** `scripts/dashboard_ng/components/notifications.py`

### 現狀
- `ui.dialog()` + `dlg.move()` = 全屏 modal overlay
- 蓋住所有 tabs + content

### Fix
- 改用 `ui.menu().props('anchor="bottom right" self="top right"')`
- Dropdown 固定喺 bell button 下方，唔係 modal
- 只改 `notifications.py` 一個文件

---

## Phase 4: Button Color Consistency — `pending` 🟢

**Files:** 8+ component files

### 現狀
- `color=indigo` 用於 primary + secondary actions（冇分別）
- `color=blue` / `color=blue-4` / `color=blue-grey-5` 混用
- 深色背景上淺藍同深藍難分辨

### 新 Color System
| Role | Color | Usage |
|------|-------|-------|
| **Primary** (submit, confirm) | `color=teal` | Place Order, Save, Connect |
| **Secondary** (modify, toggle) | `color=grey-7` | SL/TP, Edit, Toggle |
| **Danger** (close, delete) | `color=red` | Close Position, Cancel Order |
| **Warning** (live, confirm live) | `color=deep-orange` | Go Live, Confirm |
| **Cancel** (dismiss) | `flat color=grey` | Cancel, Close Dialog |

### Files to update
- `trade_modal.py` — Place Order button
- `positions.py` — Close, SL/TP, Cancel buttons
- `controls.py` — Profile, Regime, Start/Stop buttons
- `exchange_connect.py` — Connect buttons
- `layout.py` — Header Connect button
- `notifications.py` — Clear All button
- `poly_config.py` — Save button
- `pages/polymarket.py` — Various action buttons

---

## Execution Order

| Phase | Priority | Effort | Risk |
|-------|----------|--------|------|
| 3. Notification overlay | 🟢 Quick win | 15 min | Low |
| 4. Button colors | 🟢 Quick win | 20 min | Low |
| 1. Trade modal | 🔴 Core | 45 min | Medium |
| 2. Hidden indicators | 🟡 Feature | 60 min | Low |

---

## Errors
| # | Phase | Error | Resolution |
|---|-------|-------|------------|

## Decisions
| # | Decision | Reason |
|---|----------|--------|
| 1 | Teal for primary buttons | 高對比度喺深色背景，同 blue/indigo 明顯唔同 |
| 2 | `ui.menu` 取代 notification `ui.dialog` | Dropdown 唔 block interaction，UX 更好 |
| 3 | Phase 3+4 先做（quick win）| 改完即見效果，trade modal 需要更多設計 |
