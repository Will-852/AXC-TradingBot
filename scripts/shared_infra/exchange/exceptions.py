"""
exceptions.py — Exchange-specific exception hierarchy
Based on freqtrade pattern (TRADING_BOT_PATTERNS.md Section 4.1)

Hierarchy:
  ExchangeError
  ├── TemporaryError (retry: 429, 5xx, timeout)
  │   └── DDosProtection (429)
  ├── OrderError (唔 retry: 拒絕嘅 order)
  │   ├── InsufficientFundsError (-2010)
  │   └── InvalidOrderError (-1013/-1111/-1116)
  ├── AuthenticationError (401/403)
  └── CriticalError (致命，停 pipeline)
"""

__all__ = [
    "ExchangeError",
    "TemporaryError",
    "DDosProtection",
    "OrderError",
    "InsufficientFundsError",
    "InvalidOrderError",
    "AuthenticationError",
    "CriticalError",
]


class ExchangeError(Exception):
    """Base for all exchange-related errors."""
    pass


class TemporaryError(ExchangeError):
    """Retryable: rate limit, timeout, 5xx."""
    pass


class DDosProtection(TemporaryError):
    """429 Too Many Requests."""
    pass


class OrderError(ExchangeError):
    """Order was rejected by exchange (non-retryable)."""
    pass


class InsufficientFundsError(OrderError):
    """Balance insufficient for the order."""
    pass


class InvalidOrderError(OrderError):
    """Invalid order parameters (qty precision, min notional, etc.)."""
    pass


class AuthenticationError(ExchangeError):
    """HMAC signature or API key invalid."""
    pass


class CriticalError(ExchangeError):
    """Fatal error that should halt the entire pipeline."""
    pass
