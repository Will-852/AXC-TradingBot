# Polymarket — 文件索引
> 最後更新：2026-03-21
> CLAUDE.md 嘅 file index 指向呢度。加/改文件後更新此表。

## Root Scripts
| File | 用途 | 狀態 |
|------|------|------|
| `pipeline.py` | 14-step general pipeline（所有市場類型） | 🟡 DORMANT |
| `run_mm_live.py` | Dual-Layer MM bot（BTC/ETH 15M, code v15） | 🟢 LIVE |
| `run_1h_live.py` | 1H Conviction pricing bot（BTC/ETH 1H） | 🟢 LIVE |
| `run_btc_paper.py` | BTC 15M paper tracker | 🔵 Paper |
| `run_weather_paper.py` | Weather paper tracker | ❌ 廢棄 |
| `research_cycle.py` | AI 研究循環（每 6h LaunchAgent） | 🟢 ACTIVE |
| `position_watcher.py` | 獨立 take-profit monitor（30s loop） | 🔧 Manual |

## config/
| File | 用途 |
|------|------|
| `settings.py` | 所有常數、路徑、閾值 |
| `params.py` | Bankroll override（獨立於 AXC params.py） |
| `categories.py` | 市場分類 + weather cities + blocklist |

## core/
| File | 用途 |
|------|------|
| `context.py` | Dataclasses: PolyMarket, EdgeAssessment, PolySignal... |

## strategy/
| File | 用途 |
|------|------|
| `market_maker.py` | Dual-Layer MM 核心（Zone 1/2/3, Student-t, pricing） |
| `hourly_engine.py` | 1H Conviction engine（Brownian Bridge + OB conviction） |
| `market_scanner.py` | Scan + filter markets |
| `edge_finder.py` | 核心 edge 偵測（triple signal + AI fallback） |
| `crypto_15m.py` | BTC 15M 指標 pipeline |
| `cvd_strategy.py` | CVD divergence signal source |
| `microstructure_strategy.py` | Volume spike mean reversion（零 AI，1 API call） |
| `weather_tracker.py` | Multi-model ensemble（❌ 廢棄，但 edge_finder 仍 import） |
| `gto.py` | GTO filter（7 rules，純數學） |
| `logical_arb.py` | Logical arbitrage detection（negRisk + ordering） |
| `spread_analyzer.py` | Order book 分析 |

## exchange/
| File | 用途 |
|------|------|
| `gamma_client.py` | Gamma API（公開，免 auth） |
| `polymarket_client.py` | CLOB SDK（需 POLY_PRIVATE_KEY） |
| `hl_hedge_client.py` | Hyperliquid hedge（需 HL_PRIVATE_KEY，目前空） |

## risk/
| File | 用途 |
|------|------|
| `risk_manager.py` | Risk rules + protected_call() wrapper |
| `circuit_breaker.py` | 3-state CB（CLOSED/OPEN/HALF_OPEN）per service |
| `position_manager.py` | Exit triggers |
| `position_merger.py` | Mergeable position detection（Phase 1: detect only） |
| `binary_kelly.py` | Kelly sizing（half Kelly × confidence × GTO） |

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
| `shadow_observer.py` | Zero-risk shadow observer（4 coins × 2 timeframes） |
| `signal_recorder.py` | Exchange prices + Poly midpoints + OB depth recorder |
| `v15_48h_report.py` | v15 48h auto-report（AS diagnostic, fill rate, ToD WR） |

## analysis/
| File | 用途 |
|------|------|
| `whale_1h_timing.py` | Blue-walnut whale 1H entry timing analysis |

## backtest/
| File | 用途 |
|------|------|
| `mm_backtest.py` | MM backtest（original） |
| `mm_backtest_v3.py` | MM backtest v3 |
| `mm_v4_sim.py` | MM v4 simulation |
| `mm_v9_compare.py` | MM v9 comparison |
| `hybrid_backtest.py` | Hybrid strategy backtest |
| `swing_backtest.py` | Swing strategy backtest |
| `microstructure_backtest.py` | Microstructure backtest |
| `cvd_backtest.py` | CVD backtest |
| `hourly_conviction_bt.py` | 1H conviction backtest |
| `bridge_weight_bt.py` | Bridge weight backtest |

## docs/
| File | 用途 |
|------|------|
| `mm_v15_pipeline.md` | MM v15 signal pipeline（完整版，取代 v9） |
| `mm_v9_pipeline_correct.md` | ~~v9 pipeline~~（DEPRECATED，保留做歷史參考） |
| `cluster_analysis_report.md` | BMD 四大 Cluster 調優分析（2026-03-21） |
| `mm_v9_signal_pipeline.svg` | Signal pipeline diagram |
| `mm_v9_signal_pipeline_v2.svg` | Signal pipeline diagram v2 |

## tests/
| File | 用途 |
|------|------|
| `test_phase3_hedge.py` | Phase 3 hedge tests |
