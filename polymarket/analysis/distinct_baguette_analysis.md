# distinct-baguette Polymarket Trading Bot — 完整分析報告

> 分析日期：2026-03-21
> 來源：https://distinct-baguette.com/how-it-works + /doc
> 分析者：Claude (for AXC reference)

---

## 1. 產品概覽

| 項目 | 詳情 |
|------|------|
| 名稱 | distinct-baguette |
| 類型 | Polymarket crypto UP/DOWN bucket 自動交易 bot |
| 語言 | Rust（~44K 行） |
| 資產 | BTC, ETH, SOL, XRP |
| 時間窗 | 5min, 15min, 1hr |
| 定價 | $199（原價 $249），一次性買斷，crypto 支付 |
| 交付 | 6.8 GB package（source + data + tools） |
| 部署 | 單一 binary + systemd，Linux VPS |
| 宣稱表現 | "$500K+ in verified profit" |
| 聯繫 | distinct-baguette@protonmail.com / @db_polybot |

---

## 1.1 重要背景

Doc page 提到 bot 係 **"modeled on distinct-baguette and gabagool22 trading patterns"** — 即係開發者逆向工程咗已知贏錢錢包嘅行為模式（包括 distinct-baguette 本身），然後 codify 成自動化策略。

> **交叉引用：** 我哋已有類似研究 → `trading/polymarket_wallet_reverse_engineering.md`（6 個真錢包逆向工程）。佢哋嘅方法論同我哋一致，但佢哋進一步將觀察自動化成 Rust bot。

---

## 2. 三大策略詳析

### 2.1 Momentum Trading（動量）

**核心邏輯：** 利用 Binance spot price 同 Polymarket contract repricing 之間嘅延遲。監聽 Binance aggTrade WebSocket，喺市場調整前入場。

**Edge 窗口：** ~200ms 有意義，~550ms 已 marginal。

**Signal 機制：**
- 可配置 lookback window（default 10s）
- 最低 delta threshold（entry_min_delta）
- Signal 超過 ~5s 就 stale

**四種執行模式：**

| Mode | 機制 | 特點 |
|------|------|------|
| `single_taker` | FOK at ask | 最快成交，付 taker fee |
| `gtc_at_ask` | GTC limit at ask | maker rebate 回收 |
| `single_maker` | GTC at bid | 最低 fee，成交唔確定 |
| `dual_hybrid` | FOK taker + GTC maker 同時雙邊 | 對沖式 |

**關鍵參數：**
```
lookback_secs: 10      # BTC 價格回望窗口
entry_min_delta: 0.0%  # 最低觸發 delta
eval_interval_ms: 2000 # 評估冷卻
entry_delay_secs: 13   # 窗口開始後延遲
burst_count: 1         # 每次評估下單數
burst_step: 0.01       # 連續下單價格遞增
momentum_size: 10      # 每筆方向性 shares
```

**Preset 對比：**

| 參數 | Conservative | Aggressive |
|------|-------------|------------|
| lookback_secs | 2 | 1 |
| entry_min_delta | 0.02 | 0.01 |
| entry_delay_secs | 8 | 5 |
| burst_count | 1 | 3 |

### 2.2 Market Making（做市）

**核心邏輯：** UP 同 DOWN tokens 雙邊報價，賺 spread。

**關鍵創新 — Binance Preemptive Cancel：**
- 監聽 Binance 實時價格
- 偵測到 adverse move → sub-second 取消暴露嘅 resting orders
- 喺 toxic fill 發生前搶先撤單

**Position 管理：**
- 自動 position rebalancing
- On-chain merging（ProxyWallet Factory）：matched UP/DOWN pairs → 回收 capital
- 需要 POL 做 gas（~$0.01-0.05 per merge）

**關鍵參數：**
```
mm_levels: 1           # 每邊報價層數
mm_size: 10            # 每層 shares
mm_requote_ms: 500     # 重新報價間隔
mm_min_margin: 0.005   # 最低 margin (USD)
mm_level_step: 0.01    # 層間距
mm_max_imbalance: 200  # 最大 position skew
merge_interval_secs: 240
merge_fraction: 0.5
merge_min_pairs: 10
```

### 2.3 Spread Capture / Arbitrage（套利）

**核心邏輯：** UP + DOWN 結算必定 = $1.00。當兩邊 bid 價合計 < $1.00（例如 $0.48 + $0.49 = $0.97），同時買兩邊，鎖定 $0.03 無風險利潤。

**執行：** GTC limits at bid，batch buy 同時下單。

**關鍵參數：**
```
max_buy_order_size: 5   # shares
spread_threshold: 0.02  # 最低套利空間 (USD)
trade_cooldown: 5000ms
balance_factor: 0-1     # rebalancing 力度
price_bias: per share   # bid adjustment
```

---

## 3. 技術架構分析

### 3.1 Event-Driven 設計
- 評估循環由 Binance aggTrade 驅動，**唔係固定 timer**
- 三個策略實例並行運行
- Signal Engine（lookback + delta threshold）獨立模組

### 3.2 網絡依賴

| 服務 | 端點 | 用途 |
|------|------|------|
| CLOB Orders | clob.polymarket.com (HTTPS) | 下單/撤單 |
| Market WS | ws-subscriptions-clob.polymarket.com (WSS) | 實時 orderbook |
| User Events | ws-subscriptions-clob.polymarket.com (WSS) | Fill 通知 |
| Auth | clob.polymarket.com/auth/ (HTTPS) | EIP-712 簽名 |
| Metadata | gamma-api.polymarket.com (HTTPS) | Token info |
| Price Feed | stream.binance.com:9443 (WSS) | aggTrade |
| Blockchain | polygon-rpc.com (HTTPS) | Merge/Redeem |

### 3.3 部署要求
- **VPS 位置：Amsterdam**（DigitalOcean AMS3 / Vultr Amsterdam）
- **延遲目標：~1ms to Polymarket CLOB**
- **最低配置：** 1 vCPU, 1GB RAM, $6-12/月
- **Binary size：** ~11MB
- **Rust 1.88.0+**
- **地理限制：** Binance WS 美國 blocked，Polymarket 美國 IP 可能被 geoblock

### 3.4 認證
- EIP-712 signature 自動從 private key 推導 CLOB API credentials
- **唔需要獨立 API key**

---

## 4. Backtester 分析

### 4.1 歷史數據
- **總量：** 6.8 GB，11,201 market files
- **覆蓋：** 2025年12月 – 2026年2月
- **分佈：** BTC 3,078 (2.4GB) / ETH 1,957 (1.3GB) / SOL 1,964 (1.0GB) / XRP 1,967 (929MB)
- **解析度：** 100ms downsampling，保留所有價格變動
- **包含：** 完整 orderbook tick history + embedded Binance 1s klines

### 4.2 三種 Fill Model

| Model | 描述 | 用途 |
|-------|------|------|
| Deterministic | 假設全部即時成交 | 樂觀上限，快速篩選 |
| Probabilistic | 基於歷史 fill rate 分配概率 | 中等現實 |
| Latency | 模擬網絡 RTT + 隊列位置 + signal-to-execution delay | 最現實 |

### 4.3 Output Metrics
- Worst-case cumulative P&L
- ROI（worst-case P&L / deployed capital）
- Median per-market P&L
- Both-sides profitability %
- End-of-window position imbalance

---

## 5. 風控機制

| 機制 | 詳情 |
|------|------|
| Stop-Loss | 可配置 loss threshold + cooldown + partial exit |
| Dry-Run | `dry_run: true` 模擬全部交易 |
| Graceful Restart | SIGTERM → 撤單 + 平倉 + 保存狀態 |
| Position Merge | On-chain 回收 matched pairs |
| 最小下單 | 5 shares 硬底線 |
| Imbalance Cap | mm_max_imbalance 限制最大 skew |
| Preemptive Cancel | adverse move 時 sub-second 撤單 |

---

## 6. 同 AXC 對比分析

### 6.1 策略重疊

| 維度 | distinct-baguette | AXC (我哋) |
|------|-------------------|------------|
| 核心市場 | BTC/ETH/SOL/XRP UP/DOWN | BTC+ETH 15M + 1H |
| Momentum | Binance latency arb（200-550ms） | 信號驅動方向性 bet |
| MM | 雙邊 + preemptive cancel | v15 Dual-Layer（Zone 1/2/3 hedge + directional） |
| Arb | UP+DOWN < $1 套利 | 未實現 |
| Merge | ProxyWallet Factory 自動化 | Phase 2 未做（Known Issue #4） |
| 語言 | Rust（性能優先） | Python（靈活優先） |

### 6.2 佢哋做得好嘅地方

1. **Latency edge 清晰量化**：200ms meaningful，550ms marginal — 有明確嘅 edge decay curve
2. **Preemptive cancel**：用 Binance feed 搶先撤單，解決 MM 最大痛點（adverse selection）
3. **三種 fill model backtester**：latency model 最接近真實
4. **On-chain merging 自動化**：ProxyWallet Factory 回收 matched positions
5. **Event-driven（唔係 timer-based）**：aggTrade 觸發評估，反應更快
6. **固定 share sizing**：簡單直接，避免 dollar allocation 嘅 rounding 問題
7. **Amsterdam VPS** 建議：~1ms to CLOB，latency 優勢最大化

### 6.3 佢哋嘅弱點 / 值得質疑嘅地方

1. **Edge 可持續性存疑：**
   - 200-550ms latency edge 會隨更多 bot 競爭而壓縮
   - "Signal goes stale after ~5s" — 窗口極窄
   - 自己都承認 "regime changes" 需要 re-tune

2. **$500K profit 宣稱缺乏第三方驗證：**
   - "verified" 但冇講點 verify
   - 可能包含 backtesting 結果，唔一定全部 live

3. **固定 share sizing 嘅局限：**
   - 5 shares minimum + 固定 size → 冇根據信心調整
   - 同 AXC v4 嘅 asymmetric sizing 理念矛盾

4. **多資產分散注意力：**
   - BTC/ETH/SOL/XRP × 3 timeframes = 12 combinations
   - 每個組合嘅 liquidity 同 edge profile 差異大
   - AXC 專注 BTC 15M 可能更有效率

5. **Arb strategy 天花板低：**
   - UP+DOWN < $1 嘅機會喺高效市場越來越少
   - Threshold $0.02 已經好窄

6. **賣 bot 本身就係 signal：**
   - 如果真係印鈔機，點解要 $199 賣？
   - 可能 edge 已衰減，賣 bot 係新 revenue stream
   - "12 left" 製造緊迫感 — 典型銷售手法

### 6.4 可以學嘅嘢（Actionable for AXC）

| 編號 | 學習點 | AXC 應用 | 優先級 |
|------|--------|---------|--------|
| 1 | **Preemptive cancel 機制** | v4 MM 加入 Binance feed 驅動嘅搶先撤單 | 🔴 高 |
| 2 | **Event-driven evaluation** | 用 aggTrade 觸發而唔係固定 interval | 🔴 高 |
| 3 | **Latency fill model** | Backtester 加入 RTT + queue position 模擬 | 🟡 中 |
| 4 | **On-chain merge 自動化** | 已列為 Known Issue #4（Phase 2 未做），佢哋嘅 ProxyWallet Factory 係參考實現 | 🟡 中 |
| 5 | **Amsterdam VPS** | 測試歐洲節點 latency | 🟡 中 |
| 6 | **UP+DOWN arb monitor** | 即使唔交易，監察 spread 做 market efficiency indicator | 🟢 低 |
| 7 | **Dual hybrid execution** | Taker + maker 同時落場，balanced exposure | 🟢 低 |

---

## 7. 風險評估

### 7.1 如果作為購買對象
- **唔建議購買**：$199 買嘅係 Rust source code，AXC 係 Python stack，整合成本高
- 歷史數據（6.8GB）有分析價值，但格式同 AXC 唔 compatible
- 策略概念可以自己實現，唔需要買佢嘅 code

### 7.2 如果作為競爭對手
- 佢哋嘅 latency edge 同 AXC 嘅信號 edge 唔同維度
- Preemptive cancel 係直接威脅：如果佢哋撤單比我哋 fill 快，我哋嘅 adverse selection 會惡化
- 多個類似 bot 運行 → 整體市場 efficiency 上升 → 所有 bot 嘅 edge 都會壓縮

### 7.3 市場結構影響
- 更多人用類似 bot → spread 壓縮 → arb 機會減少
- Momentum latency edge 係 zero-sum：最快嘅 bot 食晒
- MM 嘅 preemptive cancel 成為標配 → 冇做嘅 bot 食更多 toxic flow

---

## 8. 關鍵結論

1. **distinct-baguette 係一個成熟嘅 latency-focused trading system**，核心 edge 來自 Binance-to-Polymarket 嘅定價延遲
2. **對 AXC 最有價值嘅係兩個概念**：preemptive cancel + event-driven evaluation
3. **唔好買佢嘅 bot** — 學概念就夠，實現用我哋自己嘅 Python stack
4. **佢嘅存在（同類似 bot）長期會壓縮所有人嘅 edge** — 要做好 edge decay 嘅準備
5. **AXC 嘅差異化方向應該係信號質量 + asymmetric sizing**，唔係同佢哋鬥 latency
6. **佢哋逆向工程錢包行為 → codify 成 bot** 嘅方法論同我哋 `polymarket_wallet_reverse_engineering.md` 一脈相承，驗證咗我哋嘅研究方向正確

---

## Appendix: 完整參數速查表

<details>
<summary>General</summary>

| Parameter | Default | Description |
|-----------|---------|-------------|
| symbol | btc | Asset |
| strategy | arb | Strategy type |
| dry_run | false | Simulation mode |
| window | 15m | Market window |
| web_port | 0 | Dashboard port |
| min_price | 0.0 | Price floor |
| max_price | 1.0 | Price ceiling |

</details>

<details>
<summary>Momentum</summary>

| Parameter | Default | Description |
|-----------|---------|-------------|
| momentum_size | 10 | Shares per trade |
| momentum_mode | single_taker | Execution mode |
| eval_interval_ms | 2000 | Cooldown |
| lookback_secs | 10 | BTC lookback |
| entry_min_delta | 0.0 | Min delta % |
| entry_delay_secs | 13 | Window open buffer |
| burst_count | 1 | Orders per eval |
| burst_step | 0.01 | Price increment |
| stop_loss_pct | 0.0 | Stop loss (disabled) |
| stop_loss_keep_shares | - | Retention after SL |
| stop_loss_cooldown_ms | - | Re-entry block |

</details>

<details>
<summary>Market Making</summary>

| Parameter | Default | Description |
|-----------|---------|-------------|
| mm_levels | 1 | Quote levels per side |
| mm_size | 10 | Shares per level |
| mm_requote_ms | 500 | Requote interval |
| mm_min_margin | 0.005 | Min margin USD |
| mm_level_step | 0.01 | Level spacing |
| mm_max_imbalance | 200 | Max position skew |

</details>

<details>
<summary>Arbitrage</summary>

| Parameter | Default | Description |
|-----------|---------|-------------|
| max_buy_order_size | 5 | Shares |
| spread_threshold | 0.02 | Min arb spread |
| trade_cooldown | 5000 | Cooldown ms |
| balance_factor | 0-1 | Rebalance aggression |
| price_bias | - | Bid adjustment |

</details>

<details>
<summary>Merge</summary>

| Parameter | Default | Description |
|-----------|---------|-------------|
| merge_interval_secs | 240 | Merge cycle |
| merge_fraction | 0.5 | Pairs per cycle |
| merge_min_pairs | 10 | Min threshold |
| polygon_rpc_url | - | RPC endpoint |

</details>
