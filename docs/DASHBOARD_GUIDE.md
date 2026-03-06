# OpenClaw Dashboard — Self-Service Guide
> 對象：想自己改 dashboard 嘅你
> 最後更新：2026-03-07

---

## 目錄
1. [render 函數結構](#1-render-函數結構)
2. [AdminLTE CSS 速查](#2-adminlte-css-速查)
3. [dashboard.py Route 模板](#3-dashboardpy-route-模板)
4. [速查表：想改什麼去哪裡](#4-速查表)
5. [自己改 vs 問 Claude](#5-自己改-vs-問-claude)

---

## 1. render 函數結構

### 數據流

```
每 5 秒
  fetchData()
    → fetch('/api/data')
    → 收到 JSON 物件 D
    → render(D)
        ├─ renderNavbar(D)           // profile badge + 通知 bell
        ├─ renderStatsCards(D, hits) // 4 個 stat cards
        ├─ renderRiskBoxes(D)        // 風控 info boxes
        ├─ renderActionPlan(D)       // 行動部署表格
        ├─ renderPnlChart(D)         // 盈虧曲線 (Chart.js)
        ├─ renderTriggerSummary(D)   // 觸發摘要
        ├─ renderPositionDetail(D)   // 持倉明細
        ├─ renderTrades(D)           // 交易記錄
        ├─ renderActivityTimeline(D) // 系統活動
        ├─ renderScanLog(D, scans)   // 掃描日誌
        └─ renderFooter(D)           // footer 更新時間
```

### 加新數據欄位（4 步）

假設要顯示 `D.win_rate`：

```
步驟 1：dashboard.py — collect_data() 加 "win_rate": calculate_win_rate()
步驟 2：index.html HTML — 加一個顯示元素 <span id="stat-winrate">–</span>
步驟 3：index.html JS — 喺合適嘅 render 函數加 setText('stat-winrate', D.win_rate + '%')
步驟 4：curl http://localhost:5555/api/data | python3 -c "import json,sys; print(json.load(sys.stdin)['win_rate'])"
```

### 工具函數速查

| 函數 | 用途 | 例子 |
|------|------|------|
| `byId(id)` | getElementById 包裝（避免 jQuery $ 衝突） | `byId('stat-positions')` |
| `setText(id, val)` | 安全設定 textContent | `setText('risk-consec', '1 / 3')` |
| `fmtPnl(n)` | 盈虧格式：+/-符號 + 2位小數 | `fmtPnl(-6.38)` → `"-6.38"` |
| `fmtPrice(n)` | 價格格式：>1000 用1位，否則4位 | `fmtPrice(68726)` → `"68726.0"` |
| `pnlColor(n)` | 盈虧顏色：≥0 teal，<0 rose | `pnlColor(-1)` → `"#e11d48"` |
| `parseScanLine(str)` | 解析 scan_log string → object | 返回 `{time, type, detail, isTriggered, pair}` |

### 鍵盤快捷鍵

| 鍵 | 動作 |
|----|------|
| `R` | 立即刷新數據 |
| `P` | 暫停 / 恢復自動刷新 |

---

## 2. AdminLTE CSS 速查

### Small Box（統計卡片）

```html
<div class="col-lg-3 col-6">
  <div class="small-box">
    <div class="inner">
      <h3 id="your-number">0</h3>
      <p>標籤文字</p>
    </div>
    <div class="icon"><i class="fas fa-chart-line"></i></div>
    <a href="#target" class="small-box-footer">
      連結文字 <i class="fas fa-arrow-circle-right"></i>
    </a>
  </div>
</div>
```

### Info Box（狀態指標）

```html
<div class="col-md-3 col-6">
  <div class="info-box">
    <span class="info-box-icon" style="background:#ede9fe;color:#635bff">
      <i class="fas fa-shield-alt"></i>
    </span>
    <div class="info-box-content">
      <span class="info-box-text">標籤</span>
      <span class="info-box-number" id="your-value">數值</span>
      <!-- 可選：progress bar -->
      <div class="progress mt-1">
        <div class="progress-bar" id="your-bar" style="width:0%;background:#635bff"></div>
      </div>
    </div>
  </div>
</div>
```

### Card（內容卡片）

```html
<div class="card">
  <div class="card-header bg-openclaw">
    <h3 class="card-title">
      <i class="fas fa-icon mr-2"></i>卡片標題
    </h3>
    <div class="card-tools">
      <button type="button" class="btn btn-tool" data-card-widget="collapse">
        <i class="fas fa-minus"></i>
      </button>
    </div>
  </div>
  <div class="card-body p-0">
    <!-- table 或其他內容 -->
  </div>
</div>
```

預設收起：加 `collapsed-card` class 到 `<div class="card">`，按鈕 icon 改 `fa-plus`。

### Badge

```html
<span class="badge badge-success">LONG</span>   <!-- 綠 -->
<span class="badge badge-danger">SHORT</span>    <!-- 紅 -->
<span class="badge badge-secondary">未觸</span>   <!-- 灰 -->
```

### 顏色 Class 規律

| Class 後綴 | Bootstrap 色 | OpenClaw 覆蓋色 |
|-----------|-------------|----------------|
| `-success` | 綠 | teal `#0d9488` |
| `-danger` | 紅 | rose `#e11d48` |
| `-warning` | 黃 | amber `#d97706` |
| `-info` | 藍 | — |
| `-primary` | 深藍 | accent `#635bff` |
| `-secondary` | 灰 | slate `#64748b` |

### CSS 變數（Stripe 色盤）

```css
--bg:      #f6f8fa   /* 頁面背景 */
--card:    #ffffff   /* 卡片背景 */
--text-1:  #1e293b   /* 標題 */
--text-2:  #475569   /* 正文 */
--text-3:  #94a3b8   /* 淡色 */
--accent:  #635bff   /* 重點紫 */
--pos:     #0d9488   /* 盈利 teal */
--neg:     #e11d48   /* 虧損 rose */
--warn:    #d97706   /* 警告 amber */
--border:  #e2e8f0   /* 邊框 */
```

### Grid 佈局

```
col-lg-8 + col-lg-4  = 8:4 左右分欄（桌面）
col-lg-3 × 4         = 4 等分（stat cards）
col-md-3 × 4         = 4 等分（info boxes）
col-12                = 全寬（掃描日誌）
col-6                 = 手機 fallback 兩欄
```

### Table

```html
<table class="table table-sm table-hover mb-0">
  <thead>
    <tr>
      <th>欄位</th>
    </tr>
  </thead>
  <tbody id="your-body">
    <tr><td>數據</td></tr>
  </tbody>
</table>
```

- `table-sm`：緊湊行距
- `table-hover`：hover 高亮
- `mb-0`：去底部 margin（放喺 card-body p-0 內）

### Timeline

```html
<div class="timeline timeline-inverse" id="your-timeline">
  <div class="time-label">
    <span class="bg-secondary">03-06 19:23</span>
  </div>
  <div>
    <i class="fas fa-heartbeat bg-secondary"></i>
    <div class="timeline-item">
      <div class="timeline-body">內容文字</div>
    </div>
  </div>
</div>
```

---

## 3. dashboard.py Route 模板

### GET JSON Route

```python
# 喺 do_GET 的 elif 鏈加：
elif path == "/api/your_endpoint":
    data = your_function()
    self._json_response(200, data)
```

```python
def your_function():
    """GET /api/your_endpoint"""
    return {
        "key1": "value1",
        "key2": 42,
    }
```

測試：
```bash
curl -s http://localhost:5555/api/your_endpoint | python3 -m json.tool
```

### GET HTML Page Route

```python
# 喺 do_GET 的 elif 鏈加：
elif path == "/your_page":
    fpath = os.path.join(CANVAS, "your_page.html")
    if os.path.exists(fpath):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        with open(fpath, "rb") as f:
            self.wfile.write(f.read())
    else:
        self._json_response(404, {"error": "Not found"})
```

測試：
```bash
curl -s http://localhost:5555/your_page | head -5
```

### POST Route

```python
# 喺 do_POST 的 elif 鏈加：
elif self.path == "/api/your_action":
    code, data = handle_your_action(body)
    self._json_response(code, data)
```

```python
def handle_your_action(body):
    """POST /api/your_action"""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}

    value = data.get("key", "").strip()
    if not value:
        return 400, {"error": "key 不能為空"}

    # 做你要做嘅事...
    return 200, {"ok": True, "message": "成功"}
```

測試：
```bash
curl -s -X POST http://localhost:5555/api/your_action \
  -H "Content-Type: application/json" \
  -d '{"key": "value"}'
```

### 回傳值規範

```python
# 成功
return 200, {"ok": True, ...}

# 客戶端錯誤
return 400, {"error": "描述"}

# 認證失敗
return 401, {"ok": False, "error": "驗證失敗"}

# 找不到
return 404, {"error": "Not found"}
```

---

## 4. 速查表

| 想改什麼 | 去哪裡 | 改什麼 |
|---------|-------|-------|
| 數字顏色 | `index.html` `<style>` | CSS 變數 `--pos` `--neg` 等 |
| 字體大小 | `index.html` `<style>` | 對應 class 的 `font-size` |
| 卡片順序 | `index.html` HTML | 移動 `<div class="row">` 區塊 |
| 顯示新欄位 | `index.html` HTML + JS | 加 `<span id>` + `setText()` |
| 隱藏某個 card | `index.html` HTML | 加 `style="display:none"` 或刪除 |
| 刷新間隔 | `index.html` JS | 改 `REFRESH_MS = 5000` |
| API 加新數據 | `dashboard.py` | `collect_data()` 加 key |
| 加新頁面 | `dashboard.py` + `canvas/` | 加 route + HTML 文件 |
| stat card 數字格式 | `index.html` JS | 對應 `render` 函數內 |
| progress bar 顏色 | `index.html` HTML | `style="background:#色碼"` |
| 表格欄位 | `index.html` HTML `<thead>` + JS | 改 header + render 函數 |

---

## 5. 自己改 vs 問 Claude

### 自己改（安全）

- CSS 顏色 / 字體 / 間距
- render 函數內 setText / innerHTML 修改
- 隱藏 / 顯示某個 card（HTML display）
- 改 `REFRESH_MS` 刷新頻率
- 加簡單 stat（API 已有欄位 → 加 HTML + setText）

### 問 Claude

- 新功能需要同時改 `dashboard.py` + `index.html`
- 加新 API route
- 改 Chart.js 圖表邏輯
- 涉及 `scripts/` 或 `agents/` 的改動
- 涉及 `config/params.py` 的參數調整

### 絕對唔好自己改

- `scripts/*.py` — scanner / trader / bot 核心邏輯
- `agents/*/SOUL.md` — 48 個引用，改一個要改全部
- `config/params.py` — 有 override 邏輯，改錯會影響交易
- `secrets/.env` — API keys，改錯會斷服務
