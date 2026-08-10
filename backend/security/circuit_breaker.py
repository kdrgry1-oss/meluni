"""
Lightweight in-process circuit breaker for outbound integrations
(Trendyol/DHL/Iyzico/Doğan etc.). Prevents thundering herd toward a
broken upstream and surfaces "tripped" state to the dashboard.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CircuitOpen(Exception):
    pass


class CircuitBreaker:
    def __init__(self, name: str, fail_threshold: int = 5,
                 reset_after_sec: int = 60, half_open_max_probes: int = 1):
        self.name = name
        self.fail_threshold = fail_threshold
        self.reset_after_sec = reset_after_sec
        self.half_open_max_probes = half_open_max_probes

        self._fails = 0
        self._opened_at: float | None = None
        self._half_open_inflight = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if (time.time() - self._opened_at) >= self.reset_after_sec:
            return "half-open"
        return "open"

    async def call(self, fn: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        st = self.state
        if st == "open":
            raise CircuitOpen(f"Circuit '{self.name}' is OPEN — fail-fast")
        if st == "half-open":
            async with self._lock:
                if self._half_open_inflight >= self.half_open_max_probes:
                    raise CircuitOpen(f"Circuit '{self.name}' half-open probe in flight")
                self._half_open_inflight += 1
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            await self._on_failure()
            raise
        else:
            await self._on_success()
            return result
        finally:
            if st == "half-open":
                async with self._lock:
                    self._half_open_inflight = max(0, self._half_open_inflight - 1)

    async def _on_success(self):
        async with self._lock:
            self._fails = 0
            self._opened_at = None

    async def _on_failure(self):
        async with self._lock:
            self._fails += 1
            if self._fails >= self.fail_threshold and self._opened_at is None:
                self._opened_at = time.time()
                logger.warning(f"CircuitBreaker[{self.name}] OPEN after {self._fails} failures")


# Module-level registry — get_breaker() is the public accessor
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(name: str) -> CircuitBreaker:
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(name)
    return _breakers[name]


def all_states() -> dict:
    return {n: {"state": b.state, "fails": b._fails} for n, b in _breakers.items()}
