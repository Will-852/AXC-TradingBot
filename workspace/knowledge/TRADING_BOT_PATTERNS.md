# Trading Bot Patterns Knowledge Base
# 來源: TradingAgents (TauricResearch) + freqtrade
# 建立: 2026-03-02
# 目的: Phase 3-4 實施參考 + 後續策略開發

---

## Quick Reference — 邊個 Pattern 用喺邊度

| Pattern | 來源 | 用途 | Phase |
|---------|------|------|-------|
| Retry + Quadratic Backoff | freqtrade | aster_client.py API 調用 | 3 |
| Capability Dictionary | freqtrade | Exchange adapter 設計 | 3 |
| Order-Driven State Rebuild | freqtrade | Position recovery | 3 |
| Safe Accessors | freqtrade | Order/Position 防 None crash | 3 |
| Unfilled Order Management | freqtrade | 掛單超時、partial fill | 3 |
| Dry-Run First-Class | freqtrade | 現有 DRY_RUN 驗證 | 2-3 |
| Protection Plugins | freqtrade | 風控 circuit breakers | 2（已用） |
| Pair Lock System | freqtrade | No-trade pair blocking | 2（已用） |
| Callback Hooks + **kwargs | freqtrade | Strategy interface 擴展 | 後續 |
| BM25 Memory + Reflection | TradingAgents | 交易後學習 | 4+ |
| Dual-Tier LLM Routing | TradingAgents | MODEL_ROUTER（已有） | — |
| Full State JSON Logging | TradingAgents | Audit trail | 3-4 |
| Throttled Loop + Candle Sync | freqtrade | LaunchAgent 調度 | 4 |
| Exception Hierarchy | freqtrade | Error 分類處理 | 3 |
| Scoped Session | freqtrade | 如加 DB persistence | 4+ |

---

## 一、Order Execution Patterns（Phase 3 核心）

### 1.1 Retry with Quadratic Backoff
```
來源: freqtrade/exchange/common.py
重要度: ★★★★★（API 調用必須）

Pattern:
  backoff = (retries)² + 1  → 等 1, 2, 5, 10, 17 秒
  默認 4 次重試（共 5 次調用）

Exception Mapping:
  Exchange DDosProtection → retry with backoff
  Exchange TemporaryError → retry with backoff
  InvalidOrderException  → don't retry, raise
  InsufficientFunds      → don't retry, raise

Implementation for OpenClaw:
  @retry(max_retries=4, backoff=quadratic)
  def place_order(symbol, side, amount, price):
      try:
          return aster_api.create_order(...)
      except RateLimitError:
          raise TemporaryError(...)  # triggers retry
      except InvalidOrder:
          raise  # no retry
```

### 1.2 Unfilled Order Management
```
來源: freqtrade/freqtradebot.py manage_open_orders()
重要度: ★★★★★（Production 最常見問題）

Flow:
  1. 每個 cycle 檢查所有 open orders
  2. 如果超時（configurable per order type）→ cancel
  3. Cancel 前檢查 partial fill:
     - 已 fill 部分 > minimum stake → 保留 trade，cancel 剩餘
     - 已 fill 部分 < minimum stake → delete entire trade
  4. Cancel 後可選擇 re-submit at new price

Edge Cases:
  - Dust prevention: cancel 後剩餘太少（<exchange minimum）→ 唔 cancel
  - Lost orders: exchange 有 order 但 DB 冇 → fetch + reconcile
  - Duplicate prevention: 比較 price/amount/side 避免重複下單

OpenClaw 實施:
  - 每個 cycle 查 Aster DEX open orders
  - 超過 5 分鐘未 fill → cancel + re-evaluate
  - SL/TP order 要分開管理（唔 cancel）
```

### 1.3 Order-Driven State Reconstruction
```
來源: freqtrade/persistence/trade_model.py recalc_trade_from_orders()
重要度: ★★★★☆

Pattern:
  Trade state 唔係 manual tracking，而係從 order list 計算出來：
  - entry_price = weighted average of all filled entry orders
  - amount = sum of filled entry amounts - sum of filled exit amounts
  - fee = sum of all order fees

  如果 DB 同 exchange 不一致 → 以 order history 為準重新計算

OpenClaw 適用:
  - TRADE_STATE.md 嘅 position data 應該可以從 Aster DEX order history 重建
  - 每次 cycle 開始時 verify position state vs exchange actual
```

### 1.4 Safe Accessors（防 None Crash）
```
來源: freqtrade/persistence/trade_model.py Order class
重要度: ★★★★☆

Pattern:
  @property
  def safe_amount(self) -> float:
      return self.amount or self.ft_amount or 0.0

  @property
  def safe_price(self) -> float:
      return self.average or self.price or self.stop_price or 0.0

  @property
  def safe_filled(self) -> float:
      return self.filled or 0.0

原因: Exchange API 回傳嘅 field 唔一定有值
     - 有時 amount 係 None（order 剛建立）
     - 有時 average 冇（limit order 未 fill）
     - 有時 filled 係 None 而唔係 0

OpenClaw 實施:
  - OrderResult dataclass 加 safe_* properties
  - 任何用到 order 數據嘅計算都用 safe accessor
```

---

## 二、Exchange Abstraction（Phase 3）

### 2.1 Capability Dictionary
```
來源: freqtrade/exchange/exchange.py _ft_has
重要度: ★★★★☆

Pattern:
  唔用 isinstance() 判斷 exchange 特性
  用 dictionary 聲明 capabilities：

  _capabilities = {
      "stoploss_on_exchange": True,
      "stop_price_param": "stopPrice",
      "ohlcv_candle_limit": 500,
      "order_time_in_force": ["GTC"],
      "funding_fee_times": [0, 8, 16],  # UTC hours
  }

  # 代碼中用:
  if self._capabilities["stoploss_on_exchange"]:
      self.create_stoploss_order(...)

OpenClaw 適用:
  - 如果後續加 Hyperliquid，用 capability dict 區分
  - Aster DEX capabilities:
    stoploss_on_exchange: True
    funding_interval_hours: 8
    max_leverage: 20
    min_order_size: {...}
```

### 2.2 Dual API Pattern（Sync + Async）
```
來源: freqtrade/exchange/exchange.py
重要度: ★★★☆☆

Pattern:
  - Market data fetch → async（batch 多個 pair 同時）
  - Order operations → sync（需要即時錯誤處理）
  - 兩個獨立 client instance，shared config

OpenClaw 目前:
  - 全部 sync（urllib），Phase 3 可能需要 async for speed
  - 暫時 sync 夠用，4 pairs 唔多
```

---

## 三、Risk Management Patterns（Phase 2 已部分實施）

### 3.1 Protection Plugins（Circuit Breakers）
```
來源: freqtrade/plugins/protections/
重要度: ★★★★★（OpenClaw 已實施 SafetyCheckStep）

freqtrade 有 4 種 protection:

  1. StoplossGuard
     N 次 consecutive SL exit within lookback → lock all pairs
     OpenClaw: ✅ 已有 COOLDOWN_2_LOSSES / COOLDOWN_3_LOSSES

  2. MaxDrawdown
     Portfolio drawdown > threshold → halt ALL trading
     Mode: equity（累計虧損 vs 起始餘額）
     OpenClaw: ✅ 已有 CIRCUIT_BREAKER_DAILY = 15%

  3. LowProfitPairs
     某 pair profit 低於閾值 → lock 該 pair
     OpenClaw: ⬜ 未實施，Phase 4 可加

  4. CooldownPeriod
     任何 trade 平倉後強制等待
     OpenClaw: ✅ 已有 REENTRY_MIN_WAIT_MIN = 10

  Pair Lock 機制:
  freqtrade 用 PairLock table:
    pair, until, reason, side, active

  支持:
    - lock_pair("BTCUSDT", until=datetime, reason="3_losses")
    - unlock_reason("3_losses")  ← batch unlock
    - per-side lock（只 lock LONG，SHORT 可繼續）

  OpenClaw 改進建議:
    - 加 per-pair lock 到 CycleContext（而唔只係 no_trade_reasons list）
    - 持久化到 TRADE_STATE.md
    - 加 per-side lock support
```

### 3.2 Stoploss 四層架構
```
來源: freqtrade/strategy/interface.py + freqtradebot.py
重要度: ★★★★☆

Layer 1: Static stoploss（class attribute）
  stoploss = -0.10

Layer 2: Custom dynamic stoploss（callback）
  def custom_stoploss(pair, trade, current_profit):
      if current_profit > 0.05:
          return -0.02  # tighten
      return -0.10

Layer 3: Trailing stoploss（config-based）
  trailing_stop = True
  trailing_stop_positive = 0.02
  trailing_stop_positive_offset = 0.05

Layer 4: On-exchange stoploss（exchange-managed, survives bot downtime）

關鍵規則: "Stop losses only walk up, never down"

OpenClaw 目前:
  - Layer 1: ✅ SL_ATR_MULT 設定
  - Layer 2: ⬜ 可加 dynamic SL
  - Layer 3: ✅ RANGE_TRAILING_TRIGGER = 1.0R → breakeven
  - Layer 4: Phase 3 用 Aster DEX SL order
```

### 3.3 ROI Table（Time-Decaying Take Profit）
```
來源: freqtrade/strategy/interface.py minimal_roi
重要度: ★★★☆☆

Pattern:
  minimal_roi = {
      "0": 0.04,    # 任何時間: 4% profit → close
      "60": 0.02,   # 60 分鐘後: 2% → close
      "120": 0.01,  # 120 分鐘後: 1% → close
      "240": 0.0    # 240 分鐘後: breakeven → close
  }

核心概念: 越耐利潤越少，應該越早 close
         避免「等緊 TP 但永遠到唔到」問題

OpenClaw 可以考慮:
  - Range trade 如果 2 小時後仍未到 TP → 降低 TP target
  - 或者用 trailing 取代
```

---

## 四、State & Persistence Patterns（Phase 3-4）

### 4.1 Exception Hierarchy
```
來源: freqtrade/exceptions.py
重要度: ★★★★☆

FreqtradeException（base）
  ├─ OperationalException → 停 bot，需要人手
  │    └─ ConfigurationError
  ├─ DependencyException → 外部依賴問題
  │    ├─ PricingError
  │    └─ ExchangeError
  │         ├─ InvalidOrderException
  │         │    ├─ RetryableOrderError → retry
  │         │    └─ InsufficientFundsError → no retry
  │         └─ TemporaryError → retry with backoff
  │              └─ DDosProtection
  └─ StrategyError → strategy code bug

OpenClaw 已有:
  - CriticalError → Telegram URGENT + abort
  - RecoverableError → skip pair + continue

建議加:
  - ExchangeError（API call failed）
  - OrderError（order rejected）
  - InsufficientFundsError（餘額不足）
  - RetryableError（可重試）
```

### 4.2 Full State JSON Logging
```
來源: TradingAgents eval_results/
重要度: ★★★☆☆

Pattern:
  每次 cycle 完成後，dump 完整 state 到 JSON:
  {
    "timestamp": "...",
    "market_data": {...},
    "indicators": {...},
    "mode_votes": {...},
    "signals": [...],
    "selected_signal": {...},
    "risk_checks": {...},
    "warnings": [...],
    "scan_config_updates": {...}
  }

用途:
  - Debug（event 過後可以重建決策過程）
  - Backtest validation（對比 live vs backtest）
  - Pattern analysis（搵邊啲 signal 最 profitable）

OpenClaw 實施:
  - 每次 cycle 寫 JSON 到 ~/.openclaw/logs/cycles/
  - 檔名: cycle_{timestamp}.json
  - 保留 30 天，之後 rotate
```

### 4.3 Trade Persistence Model
```
來源: freqtrade/persistence/trade_model.py
重要度: ★★★★☆（Phase 3 需要）

freqtrade 用 SQLAlchemy（SQLite/PostgreSQL）

Trade 核心 fields:
  - id, pair, stake_amount, amount
  - open_rate, close_rate, fee_open, fee_close
  - open_date, close_date
  - stop_loss, initial_stop_loss, stop_loss_pct
  - max_rate, min_rate（tracking highest/lowest）
  - is_open, exit_reason
  - leverage, interest_rate
  - funding_fees, trading_mode
  - orders[] → 一對多關係

OpenClaw 目前:
  - TRADE_STATE.md（文字格式，manual parse）
  - TRADE_LOG.md（append-only log）

  Phase 3 選擇:
  A. 繼續用 MD files（簡單，但 query 難）
  B. 加 SQLite（結構化，query 方便，freqtrade 證明可行）
  C. 用 JSON files per trade（折中）

  建議: Phase 3 先用 MD（同 light_scan 一致）
        Phase 4 如果需要 analytics → 加 SQLite
```

---

## 五、Strategy Interface Design（後續擴展）

### 5.1 Callback Hooks + **kwargs
```
來源: freqtrade/strategy/interface.py IStrategy
重要度: ★★★★☆（後續加新策略時用）

freqtrade 有 27+ callback hooks:
  - confirm_trade_entry(**kwargs) → return False to veto
  - confirm_trade_exit(**kwargs) → return False to veto
  - custom_entry_price(**kwargs) → override price
  - custom_stoploss(**kwargs) → dynamic SL
  - custom_exit(**kwargs) → custom exit logic
  - adjust_trade_position(**kwargs) → DCA add/reduce
  - order_filled(**kwargs) → post-fill callback
  - bot_loop_start(**kwargs) → every iteration

關鍵: 所有 hook 都有 **kwargs
      Framework 可以加新參數而唔 break 舊 strategy

OpenClaw 目前:
  StrategyBase ABC 有:
    evaluate() → 入場
    get_position_params() → 倉位
    evaluate_exit() → 出場

  建議後續加:
    confirm_entry(signal, ctx, **kwargs) → bool  # veto hook
    custom_sl(position, ctx, **kwargs) → float   # dynamic SL
    on_fill(order_result, ctx, **kwargs) → None   # post-fill
    on_exit(position, reason, ctx, **kwargs) → None  # post-exit
```

### 5.2 Signal as DataFrame Column（Backtesting 用）
```
來源: freqtrade/strategy/interface.py
重要度: ★★☆☆☆（如果做 backtest 才需要）

freqtrade 嘅 signal 唔係 return value，係 DataFrame column:
  dataframe.loc[condition, 'enter_long'] = 1
  dataframe.loc[condition, 'enter_tag'] = 'rsi_oversold'

好處: 可以 vectorized backtest（一次計所有 candle）
壞處: 唔直觀，strategy 要 return DataFrame

OpenClaw 目前用 Signal dataclass → 更直觀
如果需要 backtesting → 可以加 vectorized mode
```

---

## 六、Learning & Memory Patterns（Phase 4+）

### 6.1 BM25 Memory + Post-Trade Reflection
```
來源: TradingAgents/tradingagents/agents/utils/memory.py
重要度: ★★★☆☆（高級功能，Phase 4 後）

Pattern:
  1. 每次交易後，記錄：
     - 市場狀況（indicators snapshot）
     - 決策原因
     - 結果（profit/loss）

  2. 用 BM25（lexical similarity）建索引
     - 唔需要 embedding API（省錢）
     - 唔需要 vector DB
     - 純 Python，離線可用

  3. 下次遇到相似市場狀況 → 檢索過去經驗
     - "上次 BTC range + low volume + negative funding → 虧 2%，原因係 SL 太緊"

  4. Reflection prompt:
     "Given this trade outcome, what should I have done differently?"
     → 儲存 lesson learned

OpenClaw 實施路線:
  Phase 4: 建 trade_memory.json（每次 trade 記錄 context + outcome）
  Phase 5: 加 BM25 retrieval（搵相似歷史）
  Phase 6: 如果用 LLM 做分析 → 加 reflection loop
```

---

## 七、Deployment & Monitoring Patterns（Phase 4）

### 7.1 Throttled Loop with Candle Sync
```
來源: freqtrade/worker.py
重要度: ★★★★☆

Pattern:
  while True:
      start = time.monotonic()
      process()
      elapsed = time.monotonic() - start
      sleep(max(0, throttle_secs - elapsed))

  # 進階: 對齊到 candle boundary
  # 如果 4H candle close at XX:00
  # → 安排 cycle 在 XX:01 運行（等 candle 完全 close）

OpenClaw 目前:
  - launchd 每 30 分鐘（固定間隔）
  - 問題: 可能 cycle 同 candle close 唔同步

  建議:
  - launchd StartCalendarInterval: minute=1（即 XX:01）
  - 確保 4H candle（XX:00 close）已 finalized
```

### 7.2 Health Check & Monitoring
```
來源: freqtrade/rpc/rpc.py health()
重要度: ★★★★☆

freqtrade 暴露:
  - last_process_time（上次 cycle 時間）
  - last_process_count（上次處理嘅 trade 數）
  - bot_start_date
  - uptime

OpenClaw 可以加:
  - 每次 cycle 寫 ~/.openclaw/logs/heartbeat.json:
    {"last_cycle": "2026-03-02 17:35", "status": "ok", "duration_sec": 5.2}
  - Heartbeat agent 檢查 age > 35min → alert
  - 連續 3 次 error → Telegram URGENT
```

### 7.3 Dry-Run as First-Class Citizen
```
來源: freqtrade（全系統設計原則）
重要度: ★★★★★

freqtrade 嘅每一層都支持 dry_run:
  - Exchange: 模擬 order、fake order ID、模擬 partial fill
  - Wallet: 從虛擬起始資金計算
  - Persistence: 真實寫 DB（可以 review dry-run trades）
  - RPC: 正常發 notification

OpenClaw 已有:
  ✅ ctx.dry_run flag
  ✅ Pipeline 正常運行（只 skip execute_trade）

  Phase 3 應該:
  - dry_run 時也計算 position size + SL/TP
  - dry_run 時也寫 TRADE_LOG（標記 [DRY_RUN]）
  - 方便 48h paper trading gate
```

---

## 八、Anti-Patterns（要避免嘅）

### 8.1 TradingAgents — LLM 做風控
```
TradingAgents 嘅風控完全靠 LLM debate
冇 hard limits → 如果所有 agent 同意一個壞 trade，冇任何保護

教訓: 數學規則（circuit breaker、max loss）必須係 code
      唔可以委託俾 LLM 判斷
      OpenClaw ✅ 已正確實施
```

### 8.2 TradingAgents — 冇 Persistent State
```
TradingAgents 嘅 BM25 memory 只存在 process lifetime
重啟就冇晒

教訓: 所有重要 state 必須持久化
      OpenClaw ✅ MD files 已持久化
```

### 8.3 過度依賴 pandas iterrows
```
freqtrade backtest 特意避免 pandas iterrows（太慢）
改用 list-based iteration

教訓: 如果後續做 backtest，用 list/numpy，唔用 pandas iteration
```

---

## 九、OpenClaw Phase 3 Implementation Checklist
（基於以上 patterns 整理）

### aster_client.py 應該有:
- [ ] HMAC-SHA256 auth（現有 API_KEYS.md）
- [ ] Retry decorator with quadratic backoff（Pattern 1.1）
- [ ] Exception mapping（Pattern 4.1）
- [ ] Safe accessors on order response（Pattern 1.4）
- [ ] Capability dictionary for Aster DEX（Pattern 2.1）

### Order management 應該有:
- [ ] Place order + set SL/TP within 30 seconds
- [ ] Unfilled order timeout（5 min）+ cancel（Pattern 1.2）
- [ ] Partial fill handling（Pattern 1.2）
- [ ] Order state reconciliation（Pattern 1.3）
- [ ] Dry-run order simulation（Pattern 7.3）

### Position management 應該有:
- [ ] Verify position vs exchange每個 cycle
- [ ] Trailing stop update（SL only walks up）
- [ ] Max hold time check（72h）
- [ ] Funding cost monitoring
- [ ] Circuit breaker per-position（25% loss）

### State management 應該有:
- [ ] Full cycle state JSON dump（Pattern 4.2）
- [ ] TRADE_LOG.md with structured entries
- [ ] TRADE_STATE.md with position tracking
- [ ] Error recovery: state rebuild from exchange orders

---

## 十、附錄 — 兩個 Repo 特性對比

| 特性 | TradingAgents | freqtrade | OpenClaw |
|------|--------------|-----------|----------|
| 語言 | Python | Python | Python |
| 架構 | LLM multi-agent graph | Throttled event loop | Pipeline steps |
| 策略定義 | LLM prompts | IStrategy ABC | StrategyBase ABC |
| 風控 | LLM debate（軟） | Quantitative plugins（硬） | Quantitative code（硬） |
| 落盤 | ❌ 冇 | ✅ ccxt 抽象層 | Phase 3 |
| State | Ephemeral dict | SQLAlchemy DB | MD files |
| Exchange | ❌ | 22+ exchanges | Aster DEX |
| Backtest | ❌ | ✅ 完整 | 未來 |
| Monitoring | ❌ | Telegram+REST+WS+Discord | Telegram |
| Learning | BM25 memory | ❌ | 未來 |
| Dry-run | ❌ | ✅ First-class | ✅ |
| 成本 | $$（LLM API） | $0 | $0（Python） |
