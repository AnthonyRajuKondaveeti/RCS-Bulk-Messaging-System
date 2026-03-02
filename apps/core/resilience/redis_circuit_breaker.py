"""
Redis-Backed Circuit Breaker

Phase 6: Horizontal Scaling replacement for the in-process CircuitBreaker.

Why Redis?
----------
The Phase 4 in-process CircuitBreaker stores state in Python instance variables.
When you scale to N worker containers (or N Gunicorn workers within one container),
each process has its OWN copy of the state — so one container can observe 100
failures while the remaining N-1 containers keep hammering the downstream API.

This implementation stores the OPEN state in Redis with a TTL:

  Key:  "circuit_breaker:{name}:state"     → "open" | "half_open"
        Absent key                          → CLOSED (normal operation)

  Key:  "circuit_breaker:{name}:failures"  → integer counter (INCR + TTL)
        TTL = failure_window_seconds
        When >= failure_threshold → circuit trips open

  Key:  "circuit_breaker:{name}:open_ts"  → unix timestamp (float as string)

All N worker processes / containers share a single Redis view of the circuit,
so a burst of failures in one worker trips the breaker for ALL workers.

Prometheus metrics are also updated so Grafana alerts still fire correctly
(see Phase 4 circuit_breaker.py for metric definitions).

Usage (drop-in replacement):
    from apps.core.resilience.redis_circuit_breaker import RedisCircuitBreaker

    _breaker = RedisCircuitBreaker(
        name="rcssms",
        redis_url=settings.redis.url,
        failure_threshold=5,
        recovery_timeout=60,
    )

    async def send_rcs_message(self, request):
        async with _breaker():
            return await self._do_send(request)
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Optional

import redis.asyncio as aioredis

from prometheus_client import Gauge, Counter


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Prometheus metrics — same names as Phase 4 so existing dashboards work
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

_STATE_NUM = {"closed": 0, "half_open": 1, "open": 2}


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when a request is blocked because the circuit is OPEN."""


class RedisCircuitBreaker:
    """
    Distributed async circuit breaker backed by Redis.

    Shared state means all worker processes / containers see the same circuit
    state — one cluster of failures trips the breaker for everyone.

    Redis keys (all prefixed with `circuit_breaker:{name}:`):
        state        — "open" or "half_open"; absent = CLOSED
                       TTL = recovery_timeout so the circuit auto-heals even if
                       the recovery probe never fires.
        failures     — INCR counter with failure_window_seconds TTL
                       trips the circuit when it reaches failure_threshold
        open_ts      — Unix timestamp of last OPEN transition

    Args:
        name:                    Identifier (used in Redis keys + Prometheus labels)
        redis_url:               Redis connection URL (e.g. "redis://:pass@host:6379/0")
        failure_threshold:       Failures in `failure_window_seconds` before opening
        failure_window_seconds:  Rolling window for the failure counter
        recovery_timeout:        Seconds the circuit stays OPEN before probing
        success_threshold:       Consecutive successes in HALF_OPEN to close
    """

    _PREFIX = "circuit_breaker"

    def __init__(
        self,
        name: str = "rcssms",
        redis_url: str = "redis://localhost:6379/0",
        failure_threshold: int = 5,
        failure_window_seconds: int = 60,
        recovery_timeout: int = 60,
        success_threshold: int = 2,
    ):
        self.name = name
        self._redis_url = redis_url
        self.failure_threshold = failure_threshold
        self.failure_window_seconds = failure_window_seconds
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._redis: Optional[aioredis.Redis] = None
        self._lock = asyncio.Lock()

        # Prom init
        _CB_STATE.labels(name=self.name).set(_STATE_NUM["closed"])
        _CB_OPEN_TS.labels(name=self.name).set(0)

    # ------------------------------------------------------------------
    # Redis key helpers
    # ------------------------------------------------------------------

    def _key(self, suffix: str) -> str:
        return f"{self._PREFIX}:{self.name}:{suffix}"

    async def _redis_conn(self) -> aioredis.Redis:
        """Return (or lazily create) the Redis connection."""
        if self._redis is None:
            async with self._lock:
                if self._redis is None:  # double-checked
                    self._redis = await aioredis.from_url(
                        self._redis_url,
                        decode_responses=True,
                        socket_connect_timeout=2,
                        socket_timeout=2,
                    )
        return self._redis

    async def close(self) -> None:
        """Close the Redis connection (call at worker shutdown)."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    async def get_state(self) -> CircuitState:
        """Read current circuit state from Redis."""
        try:
            r = await self._redis_conn()
            raw = await r.get(self._key("state"))
            if raw == "open":
                return CircuitState.OPEN
            if raw == "half_open":
                return CircuitState.HALF_OPEN
            return CircuitState.CLOSED
        except Exception:
            # Redis unreachable → fail-open (treat as CLOSED) so a Redis
            # outage doesn't also kill the primary API path.
            logger.warning("[%s] Redis unreachable — treating circuit as CLOSED", self.name)
            return CircuitState.CLOSED

    async def _set_state(self, state: CircuitState, ttl: Optional[int] = None) -> None:
        """Write state to Redis with optional TTL."""
        try:
            r = await self._redis_conn()
            if state == CircuitState.CLOSED:
                await r.delete(self._key("state"))
            else:
                if ttl:
                    await r.setex(self._key("state"), ttl, state.value)
                else:
                    await r.set(self._key("state"), state.value)
            _CB_STATE.labels(name=self.name).set(_STATE_NUM[state.value])
        except Exception:
            logger.warning("[%s] Could not persist state to Redis", self.name)

    async def _increment_failures(self) -> int:
        """Increment failure counter; returns new count."""
        try:
            r = await self._redis_conn()
            pipe = r.pipeline()
            pipe.incr(self._key("failures"))
            pipe.expire(self._key("failures"), self.failure_window_seconds)
            results = await pipe.execute()
            return int(results[0])
        except Exception:
            logger.warning("[%s] Could not increment failure counter in Redis", self.name)
            return 0

    async def _get_failures(self) -> int:
        try:
            r = await self._redis_conn()
            raw = await r.get(self._key("failures"))
            return int(raw) if raw else 0
        except Exception:
            return 0

    async def _reset_failures(self) -> None:
        try:
            r = await self._redis_conn()
            await r.delete(self._key("failures"))
        except Exception:
            pass

    async def _increment_successes(self) -> int:
        """Track consecutive successes in HALF_OPEN (resets on state change)."""
        try:
            r = await self._redis_conn()
            # Successes key has recovery_timeout TTL — auto-clears if we go back to OPEN
            pipe = r.pipeline()
            pipe.incr(self._key("successes"))
            pipe.expire(self._key("successes"), self.recovery_timeout)
            results = await pipe.execute()
            return int(results[0])
        except Exception:
            return 0

    async def _reset_successes(self) -> None:
        try:
            r = await self._redis_conn()
            await r.delete(self._key("successes"))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Circuit breaker interface
    # ------------------------------------------------------------------

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

    async def _before_request(self) -> None:
        state = await self.get_state()
        if state == CircuitState.OPEN:
            # Check if recovery_timeout has elapsed (the Redis key has its own TTL,
            # but we may want to probe before the key expires).
            try:
                r = await self._redis_conn()
                open_ts_raw = await r.get(self._key("open_ts"))
                open_ts = float(open_ts_raw) if open_ts_raw else 0.0
            except Exception:
                open_ts = 0.0

            elapsed = time.time() - open_ts
            if elapsed >= self.recovery_timeout:
                logger.info("[%s] Circuit HALF_OPEN — probing", self.name)
                await self._set_state(CircuitState.HALF_OPEN)
                await self._reset_successes()
            else:
                wait = round(self.recovery_timeout - elapsed, 1)
                raise CircuitBreakerOpenError(
                    f"[{self.name}] Circuit is OPEN. Retry in {wait}s."
                )
        elif state == CircuitState.HALF_OPEN:
            # Allow the probe through (do nothing — just let execution continue)
            pass

    async def _on_success(self) -> None:
        _CB_SUCCESSES.labels(name=self.name).inc()
        state = await self.get_state()

        if state == CircuitState.HALF_OPEN:
            count = await self._increment_successes()
            if count >= self.success_threshold:
                logger.info("[%s] Circuit CLOSED — service recovered", self.name)
                await self._set_state(CircuitState.CLOSED)
                await self._reset_failures()
                await self._reset_successes()
        elif state == CircuitState.CLOSED:
            # Reset rolling failure counter on successful calls
            # (optional: only reset if > 0 to avoid unnecessary Redis writes)
            pass

    async def _on_failure(self, exc: Exception) -> None:
        _CB_FAILURES.labels(name=self.name).inc()
        state = await self.get_state()

        if state == CircuitState.HALF_OPEN:
            logger.warning("[%s] Probe failed — Circuit back to OPEN: %s", self.name, exc)
            await self._trip_open()

        elif state == CircuitState.CLOSED:
            failure_count = await self._increment_failures()
            if failure_count >= self.failure_threshold:
                logger.error(
                    "[%s] %d failures in %ds — Circuit OPEN for %ds",
                    self.name, failure_count, self.failure_window_seconds, self.recovery_timeout,
                )
                await self._trip_open()

    async def _trip_open(self) -> None:
        """Transition to OPEN: write state + timestamp to Redis."""
        now = time.time()
        try:
            r = await self._redis_conn()
            pipe = r.pipeline()
            # State key with TTL so circuit auto-heals if recovery probe never fires
            pipe.setex(self._key("state"), self.recovery_timeout * 2, CircuitState.OPEN.value)
            pipe.set(self._key("open_ts"), str(now))
            await pipe.execute()
        except Exception:
            logger.warning("[%s] Could not write OPEN state to Redis", self.name)

        _CB_STATE.labels(name=self.name).set(_STATE_NUM["open"])
        _CB_OPEN_TS.labels(name=self.name).set(now)
        await self._reset_successes()

    async def get_stats(self) -> dict:
        """Return current stats (for health checks / debug endpoints)."""
        state = await self.get_state()
        failures = await self._get_failures()
        try:
            r = await self._redis_conn()
            open_ts_raw = await r.get(self._key("open_ts"))
            last_open_ago = (
                round(time.time() - float(open_ts_raw), 1) if open_ts_raw else None
            )
        except Exception:
            last_open_ago = None

        return {
            "name": self.name,
            "state": state.value,
            "backend": "redis",
            "failure_count": failures,
            "failure_threshold": self.failure_threshold,
            "failure_window_seconds": self.failure_window_seconds,
            "recovery_timeout": self.recovery_timeout,
            "last_open_ago_seconds": last_open_ago,
        }
