"""
test_grounding.py — grounding 模組單元測試
"""

import sys
import os

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from grounding import from_context, format_grounding_prompt, citation_instruction

# Import CycleContext + MarketSnapshot for building test data
from trader_cycle.core.context import CycleContext, MarketSnapshot


def _make_ctx(**overrides) -> CycleContext:
    """Build a CycleContext with sensible defaults for testing."""
    defaults = dict(
        market_data={
            "BTCUSDT": MarketSnapshot(symbol="BTCUSDT", price=71715.5,
                                      price_change_24h_pct=1.5),
            "ETHUSDT": MarketSnapshot(symbol="ETHUSDT", price=2118.65,
                                      price_change_24h_pct=2.1),
        },
        indicators={
            "BTCUSDT": {
                "4h": {"rsi": 45.2, "adx": 22.1, "macd_hist": -50.3,
                       "atr": 877.2, "bb_width": 0.029},
                "1h": {"rsi": 51.0},
            },
        },
        market_mode="RANGE",
        news_sentiment={"overall_sentiment": "neutral"},
    )
    defaults.update(overrides)
    return CycleContext(**defaults)


class TestFromContext:
    def test_extracts_prices(self):
        ctx = _make_ctx()
        snap = from_context(ctx)
        assert snap["BTC_PRICE"] == 71715.5
        assert snap["ETH_PRICE"] == 2118.65

    def test_extracts_indicators(self):
        ctx = _make_ctx()
        snap = from_context(ctx)
        assert snap["BTC_RSI_4H"] == 45.2
        assert snap["BTC_ADX_4H"] == 22.1
        assert snap["BTC_ATR_4H"] == 877.2
        assert snap["BTC_BB_WIDTH_4H"] == 0.029

    def test_extracts_1h_indicators(self):
        ctx = _make_ctx()
        snap = from_context(ctx)
        assert snap["BTC_RSI_1H"] == 51.0

    def test_extracts_regime(self):
        ctx = _make_ctx()
        snap = from_context(ctx)
        assert snap["REGIME"] == "RANGE"

    def test_empty_context(self):
        """空 CycleContext 唔 crash，REGIME=UNKNOWN。"""
        ctx = CycleContext()
        snap = from_context(ctx)
        assert snap["REGIME"] == "UNKNOWN"
        assert snap["RISK_OK"] is True
        assert snap["NEWS_SENTIMENT"] == "unknown"

    def test_risk_blocked(self):
        ctx = _make_ctx(risk_blocked=True, cooldown_active=True)
        snap = from_context(ctx)
        assert snap["RISK_OK"] is False
        assert "COOLDOWN" in snap

    def test_change_24h(self):
        ctx = _make_ctx()
        snap = from_context(ctx)
        assert snap["BTC_CHG24H"] == 1.5
        assert snap["ETH_CHG24H"] == 2.1


class TestFormatGroundingPrompt:
    def test_contains_header(self):
        snap = {"BTC_PRICE": 71715.5, "REGIME": "RANGE"}
        out = format_grounding_prompt(snap)
        assert "實時市場數據" in out
        assert "[KEY=VALUE]" in out

    def test_respects_max_chars(self):
        snap = {f"X{i}_PRICE": float(i) for i in range(50)}
        out = format_grounding_prompt(snap, max_chars=200)
        assert len(out) <= 200

    def test_categories_ordered(self):
        snap = {
            "BTC_PRICE": 71715.5,
            "BTC_RSI_4H": 45.2,
            "REGIME": "RANGE",
        }
        out = format_grounding_prompt(snap)
        price_pos = out.find("價格")
        regime_pos = out.find("制度")
        # 價格 should come before 制度
        assert price_pos < regime_pos


class TestCitationInstruction:
    def test_short_enough(self):
        inst = citation_instruction()
        assert len(inst) < 300

    def test_contains_format(self):
        inst = citation_instruction()
        assert "[KEY=VALUE]" in inst
        assert "虛構" in inst
