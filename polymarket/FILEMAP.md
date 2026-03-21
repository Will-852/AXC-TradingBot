# Polymarket — 文件索引
> 最後更新：2026-03-22
> CLAUDE.md 嘅 file index 指向呢度。加/改文件後更新此表。

## Root Scripts
| File | 用途 | 狀態 |
|------|------|------|
| `pipeline.py` | 14-step general pipeline（crypto + logical arb，天氣已清除） | 🟡 DORMANT |
| `run_mm_live.py` | Dual-Layer MM bot（BTC/ETH 15M, v15） | 🟢 LIVE |
| `run_1h_live.py` | 1H Conviction pricing bot（BTC/ETH 1H） | 🟢 LIVE |
| `run_btc_paper.py` | BTC 15M paper tracker | 🔵 Paper |
| `research_cycle.py` | AI 研究循環（5 tools, max 15 turns, 每 6h LaunchAgent） | 🟢 ACTIVE |
| `position_watcher.py` | 獨立 take-profit monitor（30s loop, FileLock mutex） | 🔧 Manual |

## config/
| File | 用途 |
|------|------|
| `settings.py` | 所有常數（Paths / Scanning / 15M / CVD / Micro / Hedge / Risk / Kelly / MM / GTO）+ get_edge_thresholds() |
| `params.py` | 用戶可調參數 override（risk limits / Kelly caps / 15M edge / cycle interval）。Bankroll 係 live 查 CLOB |
| `categories.py` | 市場分類（crypto + crypto_15m）+ CRYPTO_15M_COINS mapping + TITLE_BLOCKLIST + match_category() |

## core/
| File | 用途 |
|------|------|
| `context.py` | Dataclasses（5 classes）：PolyMarket, EdgeAssessment, PolySignal, PolyPosition, PolyContext |

## strategy/
| File | 用途 |
|------|------|
| `market_maker.py` | Dual-Layer MM 核心（Zone 1/2/3, Student-t, pricing） |
| `hourly_engine.py` | 1H Conviction engine（Brownian Bridge + OB conviction） |
| `market_scanner.py` | Gamma API scan + quality filter + 15M slug direct discovery |
| `edge_finder.py` | AI 概率估算 engine（Claude/GPT proxy fallback，general crypto） |
| `crypto_15m.py` | BTC+ETH 15M triple signal（8-weight indicator + CVD + microstructure）+ AI fallback |
| `cvd_strategy.py` | CVD divergence + dollar imbalance（Binance aggTrades，5 lookback windows） |
| `microstructure_strategy.py` | Volume spike mean-reversion signal（1 Binance kline call，hardcoded lookup table） |
| `gto.py` | GTO filter：AS scoring + Nash eq + Kelly adjuster（零 AI，4 market type registry） |
| `logical_arb.py` | Logical arbitrage detection（negRisk + ordering，零 AI，2.5% fee buffer） |
| `spread_analyzer.py` | Pre-trade OB gate：spread + depth check，live book → Gamma fallback |

## data/
| File | 用途 |
|------|------|
| `market_data.py` | Multi-exchange parallel data fetcher（6 exchanges, 22+ sources, ~2s cycle）— MarketSnapshot + SnapshotHistory |
| `ob_recorder.py` | Polymarket OB depth recorder（15M+1H, 5s interval）→ `logs/poly_ob_tape.jsonl` |

## exchange/
| File | 用途 |
|------|------|
| `gamma_client.py` | Gamma API（公開，免 auth） |
| `polymarket_client.py` | CLOB SDK（需 POLY_PRIVATE_KEY） |
| `hl_hedge_client.py` | Hyperliquid hedge client（需 HL_PRIVATE_KEY + HL_ACCOUNT_ADDRESS，BTC-only MVP） |
| `executor.py` | 統一 buy/sell execution（WAL intent → fill → done/fail → log_trade → position tracking） |

## risk/
| File | 用途 |
|------|------|
| `risk_manager.py` | Risk rules（check_safety + check_signal）— CB / cooldown / position limits / exposure / duplicate |
| `circuit_breaker.py` | 3-state CB（CLOSED/OPEN/HALF_OPEN）per service |
| `position_manager.py` | Exit triggers（asymmetric SL=9% for crypto_15m, drift+loss-cut for general） |
| `position_merger.py` | Mergeable position detection（Phase 1: detect only） |
| `binary_kelly.py` | Kelly sizing（half Kelly × confidence × GTO unexploitability [0.3-1.0]，capped per-bet/market/category） |

## state/
| File | 用途 |
|------|------|
| `poly_state.py` | POLYMARKET_STATE.json（atomic write） |
| `trade_log.py` | poly_trades.jsonl（canonical: polymarket/logs/） |

## notify/
| File | 用途 |
|------|------|
| `telegram.py` | Telegram reports（HTML，廣東話） |

## tools/
| File | 用途 |
|------|------|
| `ab_report.py` | A/B test report：M1-only vs Continuous Momentum |
| `coin_shadow_test.py` | 24h shadow test（BTC/ETH/SOL/XRP 15M） |
| `shadow_observer.py` | Zero-risk shadow observer（4 coins × 15M+1H，含 --report 模式） |
| `signal_recorder.py` | 15M+1H data recorder：exchange prices / Poly midpoints / OB depth（burst at 15M boundaries） |
| `v15_48h_report.py` | v15 48h auto-report（AS diagnostic, fill rate, ToD WR） |

## analysis/
| File | 用途 |
|------|------|
| `whale_1h_timing.py` | blue-walnut 1H entry timing：測試入場是否集中於 5M/15M settlement boundaries |
| `axc_improvement_from_db.md` | distinct-baguette vs AXC v15 競品差距分析 + Python 實現方案 |
| `distinct_baguette_analysis.md` | distinct-baguette bot 完整分析：策略模式、定價、宣稱表現 |
| `wallet_analysis/` | 14 個盈利錢包逆向工程（$2.2M PnL）：分類框架 + 深度分析 + 外部驗證 + 報告 |
| `sigma_poly_by_hour.py` | σ_poly ToD 分析（3.2x variation, 07:00 HKT best） |
| `arb_spread_analysis.py` | Arb spread 分析（0.5% snapshots < $0.98, 96% last 1 tick） |
| `fill_probability_model.py` | Fill probability model（P(fill) vs bid/σ/τ，first passage time） |

## backtest/
| File | 用途 |
|------|------|
| `mm_backtest.py` | MM backtest（original，k9q-style，15M/5M/1HR） |
| `mm_backtest_v3.py` | MM backtest v3 — 3-model fill probability（optimistic/moderate/pessimistic） |
| `mm_v4_sim.py` | MM v4 simulation — Dual-Layer historical sim using market_maker.plan_opening() |
| `mm_v9_compare.py` | MM v9 comparison — v8 baseline vs v9 dir-only vs v9 hybrid，3-way |
| `hybrid_backtest.py` | Hybrid strategy backtest（microstructure entry + asymmetric TP/SL exit） |
| `swing_backtest.py` | Swing backtest — PM token vol-mismatch swing trading（24h vs 7d realized vol） |
| `microstructure_backtest.py` | Microstructure backtest v3 — structural filter + early-exit vs hold |
| `cvd_backtest.py` | CVD backtest（3 models: indicator-only / CVD-only / combined） |
| `hourly_conviction_bt.py` | 1H conviction backtest（wait_time × threshold × entry_price grid） |
| `bridge_weight_bt.py` | Bridge weight backtest — bridge-only vs blended（indicator）weighting for 15M+1H |

## docs/
| File | 用途 |
|------|------|
| `mm_v15_pipeline.md` | MM v15 signal pipeline（完整版，取代 v9） |
| `1h_conviction_pipeline.md` | 1H Conviction bot 完整 pipeline（Brownian Bridge + conviction model） |
| `cluster_analysis_report.md` | BMD 四大 Cluster 調優分析（2026-03-21） |

## tests/
| File | 用途 |
|------|------|
| `test_phase3_hedge.py` | Phase 3 hedge tests（7 tests，HLHedgeClient dry_run / direction / CloseHedgeStep） |
