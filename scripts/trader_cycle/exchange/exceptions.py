"""
exceptions.py — Re-export from shared_infra.exchange.exceptions

Canonical implementation lives in shared_infra.exchange.exceptions.
"""

from shared_infra.exchange.exceptions import (  # noqa: F401
    ExchangeError,
    TemporaryError,
    DDosProtection,
    OrderError,
    InsufficientFundsError,
    InvalidOrderError,
    AuthenticationError,
    CriticalError,
)

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
