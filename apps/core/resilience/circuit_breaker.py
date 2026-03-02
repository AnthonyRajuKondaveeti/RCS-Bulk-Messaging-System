"""
Circuit Breaker with Prometheus Metrics

Phase 4 addition: Prometheus gauges are registered at module import time and
updated every time the circuit state changes or a call completes.

Gauges exported:
    circuit_breaker_state{name="rcssms"}
        0 = CLOSED (healthy), 1 = HALF_OPEN, 2 = OPEN (failing fast)

    circuit_breaker_failure_total{name="rcssms"}
        Monotonically increasing failure counter (resets on CLOSED).

    circuit_breaker_success_total{name="rcssms"}
        Monotonically increasing success counter.

    circuit_breaker_open_timestamp_seconds{name="rcssms"}
        Unix timestamp of the last time the circuit opened (0 if never opened).

These are scraped by Prometheus at /metrics alongside the standard process
and FastAPI metrics.

NOTE: State is still in-process only (not shared across Gunicorn workers).
      A Redis-backed circuit breaker is a Phase 5 item.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Optional

from prometheus_client import Gauge, Counter


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Prometheus metric descriptors — created once at module-level.
# prometheus_client de-duplicates by name so importing this module in
# multiple places is safe.
# --------------------------------------------------------------------------

_CB_STATE = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state: 0=CLOSED, 1=HALF_OPEN, 2=OPEN",
    labelnames=["name"],
)

_CB_FAILURES = Counter(
    "circuit_breaker_failures_total",
    "Total circuit breaker failure count",
    labelnames=["name"],
)

_CB_SUCCESSES = Counter(
    "circuit_breaker_successes_total",
    "Total circuit breaker success count",
    labelnames=["name"],
)

_CB_OPEN_TS = Gauge(
    "circuit_breaker_open_timestamp_seconds",
    "Unix timestamp when the circuit last transitioned to OPEN (0 = never)",
    labelnames=["name"],
)

_STATE_ORDINAL = {
    "closed": 0,
    "half_open": 1,
    "open": 2,
}


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when a request is blocked because the circuit is OPEN."""


class CircuitBreaker:
    """
    Async circuit breaker with Prometheus metrics.

    Args:
        failure_threshold:  Consecutive failures before opening.
        recovery_timeout:   Seconds before probing (OPEN → HALF_OPEN).
        success_threshold:  Consecutive successes in HALF_OPEN to close.
        name:               Identifier used in logs + Prometheus labels.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
        name: str = "circuit_breaker",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

        # Initialise Prometheus labels so the metrics appear immediately
        # (even before the first call) rather than only after the first event.
        _CB_STATE.labels(name=self.name).set(_STATE_ORDINAL["closed"])
        _CB_OPEN_TS.labels(name=self.name).set(0)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        return self._state

    @asynccontextmanager
    async def __call__(self):
        """Use as: `async with breaker(): ...`"""
        await self._before_request()
        try:
            yield
            await self._on_success()
        except CircuitBreakerOpenError:
            raise
        except Exception as exc:
            await self._on_failure(exc)
            raise

    def get_stats(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_ago": (
                round(time.monotonic() - self._last_failure_time, 1)
                if self._last_failure_time else None
            ),
        }

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def _before_request(self) -> None:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - (self._last_failure_time or 0)
                if elapsed >= self.recovery_timeout:
                    logger.info("[%s] Circuit HALF_OPEN — probing", self.name)
                    self._transition(CircuitState.HALF_OPEN)
                    self._success_count = 0
                else:
                    wait = round(self.recovery_timeout - elapsed, 1)
                    raise CircuitBreakerOpenError(
                        f"[{self.name}] Circuit is OPEN. Retry in {wait}s."
                    )

    async def _on_success(self) -> None:
        async with self._lock:
            _CB_SUCCESSES.labels(name=self.name).inc()
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    logger.info("[%s] Circuit CLOSED — service recovered", self.name)
                    self._transition(CircuitState.CLOSED)
                    self._failure_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            _CB_FAILURES.labels(name=self.name).inc()

            if self._state == CircuitState.HALF_OPEN:
                logger.warning(
                    "[%s] Probe failed — Circuit back to OPEN: %s", self.name, exc
                )
                self._transition(CircuitState.OPEN)
                _CB_OPEN_TS.labels(name=self.name).set(time.time())

            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    logger.error(
                        "[%s] %d consecutive failures — Circuit OPEN for %ds",
                        self.name, self._failure_count, self.recovery_timeout,
                    )
                    self._transition(CircuitState.OPEN)
                    _CB_OPEN_TS.labels(name=self.name).set(time.time())

    def _transition(self, new_state: CircuitState) -> None:
        """Apply state transition and update the Prometheus state gauge."""
        self._state = new_state
        _CB_STATE.labels(name=self.name).set(_STATE_ORDINAL[new_state.value])
        logger.debug("[%s] → %s", self.name, new_state.value)
