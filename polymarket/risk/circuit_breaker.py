"""
circuit_breaker.py — 3-state circuit breaker for external service calls

States:
  CLOSED    → Normal operation, requests flow through
  OPEN      → Service failing, all requests immediately rejected
  HALF_OPEN → Testing recovery, limited requests allowed through

Transitions:
  CLOSED → OPEN:      failure_count >= failure_threshold
  OPEN → HALF_OPEN:   recovery_timeout elapsed since last failure
  HALF_OPEN → CLOSED: success_count >= success_threshold
  HALF_OPEN → OPEN:   any failure during probe

Adapted from Polymarket/agents PR #205.
Thread-safe via threading.Lock().
"""

import logging
import time
import threading
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Per-service tuning parameters."""
    failure_threshold: int = 5     # failures before tripping OPEN
    recovery_timeout: int = 60     # seconds before OPEN → HALF_OPEN
    half_open_max_calls: int = 3   # probe calls allowed in HALF_OPEN
    success_threshold: int = 2     # successes needed to close

    @classmethod
    def for_service(cls, service_name: str) -> "CircuitBreakerConfig":
        """Pre-tuned configs for known services."""
        configs = {
            "polymarket": cls(failure_threshold=3, recovery_timeout=30,
                              half_open_max_calls=1, success_threshold=1),
            "gamma":      cls(failure_threshold=5, recovery_timeout=60,
                              half_open_max_calls=2, success_threshold=2),
            "claude":     cls(failure_threshold=5, recovery_timeout=120,
                              half_open_max_calls=2, success_threshold=2),
            "binance":    cls(failure_threshold=5, recovery_timeout=30,
                              half_open_max_calls=2, success_threshold=2),
        }
        return configs.get(service_name, cls())


class CircuitBreakerOpen(Exception):
    """Raised when circuit is OPEN and request is rejected."""

    def __init__(self, service: str, state: CircuitState, retry_after: float = 0):
        self.service = service
        self.state = state
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker OPEN for {service} "
            f"(retry after {retry_after:.0f}s)"
        )


class CircuitBreaker:
    """3-state circuit breaker for a single service."""

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        self.name = name
        self.config = config or CircuitBreakerConfig.for_service(name)
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time: float = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_recovery_timeout()
            return self._state

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED

    def _check_recovery_timeout(self):
        """OPEN → HALF_OPEN after recovery_timeout seconds."""
        if self._state == CircuitState.OPEN and self._last_failure_time:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.config.recovery_timeout:
                logger.info(
                    "CB[%s]: OPEN → HALF_OPEN (%.0fs elapsed)",
                    self.name, elapsed,
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._success_count = 0

    def call(self, func, *args, **kwargs):
        """Execute function through circuit breaker.

        Raises CircuitBreakerOpen if circuit is OPEN or HALF_OPEN probe limit reached.
        """
        with self._lock:
            self._check_recovery_timeout()

            if self._state == CircuitState.OPEN:
                retry_after = max(
                    0,
                    self.config.recovery_timeout - (time.time() - self._last_failure_time),
                )
                raise CircuitBreakerOpen(self.name, self._state, retry_after)

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpen(self.name, self._state, 5)
                self._half_open_calls += 1

        # Execute outside lock (may take time)
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    logger.info("CB[%s]: HALF_OPEN → CLOSED", self.name)
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
            else:
                # CLOSED: reset failure count on success
                self._failure_count = 0

    def _on_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                logger.warning("CB[%s]: HALF_OPEN → OPEN (probe failed)", self.name)
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.config.failure_threshold:
                logger.warning(
                    "CB[%s]: CLOSED → OPEN (%d failures)",
                    self.name, self._failure_count,
                )
                self._state = CircuitState.OPEN

    def reset(self):
        """Manual reset to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0

    def status(self) -> dict:
        """Current status for monitoring/reporting."""
        with self._lock:
            self._check_recovery_timeout()
            return {
                "service": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "last_failure": self._last_failure_time,
            }


# ─── Global Registry ───

_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Get or create a circuit breaker for a service."""
    with _registry_lock:
        if name not in _registry:
            _registry[name] = CircuitBreaker(name)
        return _registry[name]


def all_statuses() -> list[dict]:
    """Get status of all registered circuit breakers."""
    with _registry_lock:
        return [cb.status() for cb in _registry.values()]
