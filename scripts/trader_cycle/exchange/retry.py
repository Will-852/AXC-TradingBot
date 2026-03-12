"""
retry.py — Shared quadratic backoff retry decorator for exchange operations.

Extracted from aster_client/binance_client/hyperliquid_client to eliminate
508 lines of duplicated retry logic. All three clients now import from here.

Retry policy:
  - TemporaryError, DDosProtection → retry with n² backoff
  - OrderError, InsufficientFundsError, InvalidOrderError → raise immediately
  - AuthenticationError → wrap as CriticalError and raise
"""

import time
import logging
from functools import wraps

from .exceptions import (
    TemporaryError, DDosProtection,
    OrderError, InsufficientFundsError, InvalidOrderError,
    AuthenticationError, CriticalError,
)

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = [1, 4, 9, 16, 25]  # n² sequence
_MAX_RETRIES = 5


def retry_quadratic(max_retries: int = _MAX_RETRIES):
    """Quadratic backoff retry decorator for exchange operations."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (TemporaryError, DDosProtection) as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} after {delay}s: {e}"
                        )
                        time.sleep(delay)
                except (OrderError, InsufficientFundsError, InvalidOrderError):
                    raise  # non-retryable
                except AuthenticationError as e:
                    raise CriticalError(f"Auth fatal: {e}")
            raise last_exc
        return wrapper
    return decorator
