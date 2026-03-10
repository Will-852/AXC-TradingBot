# Step 3 Q&A — 策略文件問答
> 2026-03-10

---

## Q(a): `__init__.py` 點解係空嘅？

**答：** Python 規則。一個文件夾要有 `__init__.py` 先算「package」，其他文件先可以用 `from strategies.xxx import yyy` 嚟 import。

空嘅就夠用 — 只係一個「門牌」，話畀 Python 知「呢度有嘢可以 import」。

如果你想 `import strategies` 時自動載入某啲嘢，可以喺入面加 code。但通常空嘅就最好，因為明確 import（`from strategies.mode_detector import detect_mode_for_pair`）比自動載入更清晰。

---

## Q(b-f): 代碼版本差異

用戶貼嘅代碼（可能來自 GitHub 或舊備份）同磁碟上嘅最新版本有以下分別：

### `range_strategy.py`（差異）

| 功能 | 用戶版本 | 磁碟最新版 |
|------|---------|-----------|
| Volume gate（4H vol < 0.8 → skip） | ❌ 冇 | ✅ 有 |
| Volume bonus（vol ≥ 1.5 → +0.5, ≥ 2.0 → +1.0） | ❌ 冇 | ✅ 有 |
| OBV confirmation（+0.5 / -0.5） | ❌ 冇 | ✅ 有 |

### `trend_strategy.py`（差異）

| 功能 | 用戶版本 | 磁碟最新版 |
|------|---------|-----------|
| Volume gate | ❌ 冇 | ✅ 有 |
| Volume bonus + OBV | ❌ 冇 | ✅ 有 |
| MACD weakening exit（decay <60% + R:R ≥ 1.0） | ❌ 冇 | ✅ 有 |
| params.py 路徑 | `~/.openclaw/config/params.py` ⚠️ 舊路徑 | `~/projects/axc-trading/config/params.py` ✅ |

### `evaluate.py`（差異）

| 功能 | 用戶版本 | 磁碟最新版 |
|------|---------|-----------|
| Sentiment filter（bearish >70% → block LONG） | ❌ 冇 | ✅ 有 |
| Re-entry boost（pair+direction match → score +0.5） | ❌ 冇 | ✅ 有 |
| Signal original_score preservation | ❌ 冇 | ✅ 有 |

### `base.py`（無差異）
✅ 兩個版本一致

### `mode_detector.py`（無差異）
✅ 兩個版本一致

---

## 呢啲缺少嘅功能係咩？（Yunis Collection）

呢啲功能統稱「Yunis Collection」，係一套增強交易決策嘅改進：

### 1. Volume Gate
- 4H volume_ratio < 0.8 → 唔評估入場
- 原因：低成交量 = 市場冇人玩 = 假信號多

### 2. Volume Bonus
- volume_ratio ≥ 1.5 → score +0.5
- volume_ratio ≥ 2.0 → score +1.0
- 原因：高成交量 = 更多人參與 = 信號更可靠

### 3. OBV Confirmation
- OBV（On-Balance Volume）方向同信號一致 → score +0.5
- OBV 方向相反 → score -0.5
- 原因：量價配合 = 更可信

### 4. MACD Weakening Exit
- MACD histogram 同方向但縮到 <60% → 趨勢失力
- 只有 R:R ≥ 1.0（已有利潤）先觸發
- 原因：趨勢衰減時提前鎖定利潤

### 5. Sentiment Filter
- 新聞整體 bearish + confidence > 70% → 禁止所有 LONG 信號
- 原因：壞消息太強烈時唔應該做多

### 6. Re-entry Boost
- 之前嘅倉位同方向再出信號 → score +0.5
- 原因：持續確認同方向 = 更有信心

---

## 行動建議

1. **確認你睇嘅係邊個版本** — 如果係 GitHub，可能需要 push 最新代碼上去
2. **磁碟上嘅版本係 production 版** — 系統實際跑緊嘅係最新版（有 Yunis Collection）
3. **唔使擔心** — 系統運行正常，只係你嘅閱讀來源可能過時
