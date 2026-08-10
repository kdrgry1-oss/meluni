"""
Unified cache layer.

Priority order:
  1. Redis (if REDIS_URL is set and reachable)
  2. In-process LRU dict (per-pod fallback — survives only one worker)

Usage:
    from cache import cache_get, cache_set, cache_invalidate

    @cached("products:list", ttl=60)
    async def list_products(...): ...

The Redis client is created lazily at first use and gracefully degrades
to the in-memory fallback if Redis is unreachable. This means the app
keeps running across server migrations — see /app/MIGRATION_GUIDE.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL")
DEFAULT_TTL = int(os.environ.get("CACHE_DEFAULT_TTL") or "60")
_INMEM_MAX = int(os.environ.get("CACHE_INMEM_MAX") or "1000")

T = TypeVar("T")


# ---------------------------------------------------------------------------
# In-memory fallback (LRU + TTL)
# ---------------------------------------------------------------------------
class _InMemoryStore:
    def __init__(self, max_items: int):
        self._d: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._max = max_items
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            row = self._d.get(key)
            if not row:
                self.misses += 1
                return None
            exp, val = row
            if exp and exp < time.time():
                self._d.pop(key, None)
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return val

    async def set(self, key: str, value: Any, ttl: int):
        async with self._lock:
            self._d[key] = (time.time() + ttl if ttl > 0 else 0, value)
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    async def delete(self, *keys: str):
        async with self._lock:
            for k in keys:
                self._d.pop(k, None)

    async def delete_prefix(self, prefix: str):
        async with self._lock:
            for k in [k for k in self._d if k.startswith(prefix)]:
                self._d.pop(k, None)

    async def stats(self):
        async with self._lock:
            return {"backend": "in-memory", "size": len(self._d),
                    "hits": self.hits, "misses": self.misses, "max": self._max}


_inmem = _InMemoryStore(_INMEM_MAX)


# ---------------------------------------------------------------------------
# Redis backend (lazy init)
# ---------------------------------------------------------------------------
_redis = None
_redis_state = "uninitialized"  # uninitialized | ok | failed | disabled


async def _get_redis():
    global _redis, _redis_state
    if _redis_state == "disabled" or not REDIS_URL:
        _redis_state = "disabled"
        return None
    if _redis is not None:
        return _redis
    try:
        import redis.asyncio as redis_async  # type: ignore
        _redis = redis_async.from_url(REDIS_URL, encoding="utf-8", decode_responses=True,
                                      socket_connect_timeout=2, socket_timeout=2)
        await _redis.ping()
        _redis_state = "ok"
        logger.info("Redis cache connected")
        return _redis
    except Exception as e:
        logger.warning(f"Redis unavailable, falling back to in-memory: {e}")
        _redis_state = "failed"
        _redis = None
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def cache_get(key: str) -> Optional[Any]:
    r = await _get_redis()
    if r is not None:
        try:
            raw = await r.get(key)
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning(f"redis get failed: {e}")
    return await _inmem.get(key)


async def cache_set(key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            await r.set(key, json.dumps(value, default=str), ex=ttl)
            return
        except Exception as e:
            logger.warning(f"redis set failed: {e}")
    await _inmem.set(key, value, ttl)


async def cache_invalidate(*keys: str) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            await r.delete(*keys)
        except Exception:
            pass
    await _inmem.delete(*keys)


async def cache_invalidate_prefix(prefix: str) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            cursor = 0
            while True:
                cursor, batch = await r.scan(cursor=cursor, match=f"{prefix}*", count=100)
                if batch:
                    await r.delete(*batch)
                if cursor == 0:
                    break
        except Exception:
            pass
    await _inmem.delete_prefix(prefix)


async def cache_stats() -> dict:
    r = await _get_redis()
    if r is not None:
        try:
            info = await r.info("stats")
            return {
                "backend": "redis",
                "state": _redis_state,
                "hits": int(info.get("keyspace_hits", 0)),
                "misses": int(info.get("keyspace_misses", 0)),
                "url_present": bool(REDIS_URL),
            }
        except Exception as e:
            logger.warning(f"redis stats failed: {e}")
    s = await _inmem.stats()
    s["state"] = _redis_state
    s["url_present"] = bool(REDIS_URL)
    return s


def cached(key_prefix: str, ttl: int = DEFAULT_TTL):
    """Decorator for async functions returning JSON-serialisable data."""
    def deco(fn: Callable[..., Awaitable[T]]):
        async def wrapper(*args, **kwargs):
            kparts = [str(a) for a in args] + [f"{k}={v}" for k, v in sorted(kwargs.items())]
            cache_key = f"{key_prefix}:" + "|".join(kparts) if kparts else key_prefix
            hit = await cache_get(cache_key)
            if hit is not None:
                return hit
            value = await fn(*args, **kwargs)
            try:
                await cache_set(cache_key, value, ttl)
            except Exception:
                pass
            return value
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco
