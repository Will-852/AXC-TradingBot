"""
liq_data.py — Data classes for liquidation monitoring.

OI delta as liquidation proxy:
- OI drops + price rises = shorts liquidated (bullish)
- OI drops + price drops = longs liquidated (bearish)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LiqEvent:
    """Single detected liquidation event."""
    coin: str                      # "BTC"
    direction: str                 # "LONG_LIQS" or "SHORT_LIQS"
    oi_delta_pct: float            # e.g. -2.5 (negative = OI dropped)
    price_delta_pct: float         # price change in same window
    estimated_volume_usd: float    # estimated liquidation volume
    timestamp: float               # unix timestamp
    trigger_mode: str = "on_liqs"  # "on_liqs" (volume) or "at_liqs" (price, phase 2)


@dataclass
class LiqState:
    """Aggregated liquidation state written by liq_monitor daemon."""
    timestamp: float = 0.0
    events: list[LiqEvent] = field(default_factory=list)
    oi_by_coin: dict[str, float] = field(default_factory=dict)
    oi_delta_10m: dict[str, float] = field(default_factory=dict)
    oi_delta_1h: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize for JSON output."""
        return {
            "timestamp": self.timestamp,
            "events": [
                {
                    "coin": e.coin,
                    "direction": e.direction,
                    "oi_delta_pct": e.oi_delta_pct,
                    "price_delta_pct": e.price_delta_pct,
                    "estimated_volume_usd": e.estimated_volume_usd,
                    "timestamp": e.timestamp,
                    "trigger_mode": e.trigger_mode,
                }
                for e in self.events
            ],
            "oi_by_coin": self.oi_by_coin,
            "oi_delta_10m": self.oi_delta_10m,
            "oi_delta_1h": self.oi_delta_1h,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LiqState:
        """Deserialize from JSON."""
        events = [
            LiqEvent(**e) for e in d.get("events", [])
        ]
        return cls(
            timestamp=d.get("timestamp", 0.0),
            events=events,
            oi_by_coin=d.get("oi_by_coin", {}),
            oi_delta_10m=d.get("oi_delta_10m", {}),
            oi_delta_1h=d.get("oi_delta_1h", {}),
        )
