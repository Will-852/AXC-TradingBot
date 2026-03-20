# Findings — AXC Dashboard NiceGUI Migration

> Security boundary: 外部內容（web/API/search）只寫呢度，唔寫 task_plan.md。

## NiceGUI 核心架構（v3.5.0, Jan 2026）

### Tech Stack
- **FastAPI backend + Vue 3 + Quasar frontend + socket.io**
- v3.0 (Oct 2025) 起用 Tailwind 4 取代舊 Tailwind/Vuetify
- Server-push model: persistent WebSocket (socket.io)，UI 事件 browser→Python，Python 改動 batch-push 到 browser
- **單進程限制**：唔支援多 Uvicorn workers。HA 要兩個獨立 process behind nginx

### Multi-Page
- `@ui.page('/route')` decorator，每個 route 一個 function
- 每個 browser tab 獨立 context
- `ui.navigate.to(url)` 做 navigation
- `ui.sub_pages()` SPA 風格 sub-routing（唔 full page reload）
- ⚠️ 持久 sidebar + per-route content 唔係 native 支援 → 用 layout frame function workaround

### Storage Scopes
- `app.storage.user` — server-side per-user，重啟後存活，跨 tab 共享
- `app.storage.browser` — browser cookie
- `app.storage.client` — volatile per-connection
- `app.storage.general` — server-side 全 user 共享（適合 price feeds）

---

## Charting

### Built-in
| Library | Element | 備註 |
|---|---|---|
| Apache ECharts | `ui.echart` | Built-in，最適合 real-time |
| Plotly | `ui.plotly` | Built-in |
| Highcharts | `ui.highchart` | 需 `nicegui-highcharts` package，商業 license |

### Candlestick Charts
- **ECharts**: `type: 'candlestick'` natively supported
- Real-time update: `chart.options["series"][0]["data"].append(new_point); chart.update()`
- 或 `chart.run_chart_method('setOption', {...})`

### TradingView Lightweight Charts
- **冇官方 integration**，Discussion #813 requested 但冇 merge
- `lightweight-charts-python` (louisnw01) 支援 Jupyter/PyQt/Streamlit，**唔支援 NiceGUI**
- **實際路徑**：custom Vue component via `register_component()`

```python
# Integration pattern:
ui.add_head_html('<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>')
# Create div + init via ui.run_javascript() 或 custom component
```

### ApexCharts
- **冇官方 integration**
- 建議用 `ui.echart` 取代（功能等價，built-in）

---

## Tables

### ui.table (Quasar QTable)
- Sortable, filterable, paginated
- Server-side pagination supported
- `table.rows = new_data; table.update()`
- ⚠️ v3.0 breaking change: 必須 update `table.rows`，唔好 mutate 原 list

### ui.aggrid (AG Grid Community)
- Full AG Grid config dict in Python
- Column-level sort/filter
- Virtual scrolling for large datasets
- `grid.run_grid_method()` call any AG Grid JS API
- ⚠️ Enterprise features (server-side row model) 要 `nicegui-aggrid-enterprise` package

### ⚠️ Performance Issues
- `table.update_from_pandas()` on large DataFrames **blocks main thread 3+ seconds**
- Workaround: send only delta rows, server-side pagination
- >2000 actively updating labels will choke connection

---

## Custom JavaScript

```python
# Execute JS, get return value
result = await ui.run_javascript('return document.title')

# Inject <head> (for libraries)
ui.add_head_html('<script src="..."></script>')

# Embed raw HTML
ui.html('<canvas id="mycanvas"></canvas>')

# Serve local static files
app.add_static_files('/static', 'path/to/folder')
```

### Web Workers
- **冇 NiceGUI abstraction**
- 可以喺 custom Vue component JS 入面 `new Worker(...)`
- NiceGUI 唔管理，但 browser context 正常運行

### Custom Vue Component Pattern
```python
# my_chart.js — Vue 3 component
register_component('tv-chart', __file__, 'my_chart.js')

class TVChart(Element):
    def __init__(self, options: dict):
        super().__init__('tv-chart')
        self._props['options'] = options

    def set_data(self, bars: list):
        self.run_method('setData', bars)  # calls JS method

    def update(self, bar: dict):
        self.run_method('update', bar)
```
- `register_component()` 必須 module import time 調用
- 支援 `.vue` SFC 文件
- 雙向通信：`$emit('event', payload)` → `.on('event', handler)`

---

## External WebSocket (Binance Streams)

```python
import asyncio, websockets
from nicegui import app

async def binance_feed():
    async with websockets.connect('wss://stream.binance.com:9443/ws/btcusdt@kline_1m') as ws:
        async for msg in ws:
            app.storage.general['price'] = json.loads(msg)['k']['c']

app.on_startup(binance_feed)
# UI reads via ui.timer()
```
- NiceGUI internal socket.io 同 external WS client 完全獨立
- Binance WS: 24h max, ping/pong 20s, max 1024 streams/connection

---

## Layout System
- `ui.header()` — sticky top navbar
- `ui.left_drawer()` / `ui.right_drawer()` — collapsible sidebars
- `ui.footer()` — bottom bar
- `ui.tabs()` + `ui.tab_panels()` — tabbed navigation
- `ui.card()`, `ui.row()`, `ui.column()` — flex containers
- `ui.splitter()` — resizable split panes

### Dark Mode
```python
dark = ui.dark_mode()
dark.enable()
ui.dark_mode().bind_value(app.storage.user, 'dark_mode')
```
- ⚠️ 初始白閃（user storage 要等 client connect 才有）

---

## Real-Time Updates
- `ui.timer(interval, callback)` — per-client timer，主要 real-time 機制
- Background tasks: `asyncio.create_task()` 或 `app.on_startup()`
- CPU-bound: `await run.cpu_bound(fn, *args)`
- IO-bound: `await run.io_bound(fn, *args)`
- ⚠️ Background → UI 更新要小心 client context
  - 最安全：background 更新 shared state → `ui.timer()` 讀

---

## Modals & Notifications

```python
# Awaitable dialog
with ui.dialog() as dialog, ui.card():
    ui.label('Are you sure?')
    ui.button('Yes', on_click=lambda: dialog.submit('yes'))
result = await dialog

# Notification
ui.notify('Order submitted', type='positive')  # positive/negative/warning/info
```

---

## Framework 對比結論

| Criterion | NiceGUI | Dash | Reflex | Streamlit |
|---|---|---|---|---|
| Real-time (WS-native) | ✅ | Partial | ✅ | ❌ |
| Embed custom JS | ✅ | Hard (React) | ✅ | Hard |
| Control panel UX | Excellent | Okay | Good | Poor |
| Deploy simplicity | Simple | Medium | Complex | Simple |
| Dark mode | Built-in | Manual | Built-in | Manual |

**NiceGUI 唯一弱點：TradingView charts + Web Workers 要 custom component。但呢個係所有框架嘅問題。**

---

## 現有 Dashboard 規模

| Component | Count | Notes |
|---|---|---|
| HTML pages | 7 | index(4288), backtest(5220), polymarket(836), paper(369), details(1053), share×2 |
| JS files | 5 | chat.js, trade-modal.js, backtest-compare.js, depth-worker.js, live-orderflow-worker.js |
| CSS files | 2 | chat.css, trade-modal.css |
| Backend Python | 18 | server.py, handlers.py, collectors.py, etc. |
| API endpoints | ~50 | GET + POST |
| Static assets | 15+ | SVGs for coins/exchanges |

## Resources
- NiceGUI docs: nicegui.io/documentation
- NiceGUI GitHub: github.com/zauberzeug/nicegui
- Dashboard template: github.com/s71m/nicegui_dashboard
- AG Grid Enterprise: github.com/xaptronic/nicegui-aggrid-enterprise
- TradingView custom element tutorial: tradingview.github.io/lightweight-charts/tutorials/webcomponents/custom-element
