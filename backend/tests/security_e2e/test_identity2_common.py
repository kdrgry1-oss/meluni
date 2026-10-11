"""Shared helpers for the ``test_identity2_*`` security tests (no tests in this module).

* ``id2_async`` runs a scenario against the real app through ``httpx.AsyncClient`` + ``ASGITransport``
  (same approach as pay_harness) so background tasks the app spawns (reset SMS / e-mail sends) can be
  drained deterministically and ``asyncio.gather`` really runs requests concurrently.
* ``id2_yield_shim`` makes every mongomock collection coroutine yield once before executing, so
  concurrent requests interleave at DB-operation granularity (each single op stays atomic, exactly
  like a single-document MongoDB write).
"""
from __future__ import annotations

import asyncio
import hashlib

import httpx

import server
from routes import deps

_YIELD_OPS = ("count_documents", "delete_many", "delete_one", "find_one", "find_one_and_delete",
              "find_one_and_replace", "find_one_and_update", "insert_many", "insert_one", "replace_one",
              "update_many", "update_one")


def id2_yield_shim(monkeypatch) -> None:
    coll_cls = type(deps.db.users)

    def _mk(orig):
        async def _yielding(self, *a, **k):
            await asyncio.sleep(0)
            return await orig(self, *a, **k)
        return _yielding
    for name in _YIELD_OPS:
        monkeypatch.setattr(coll_cls, name, _mk(getattr(coll_cls, name)))


async def id2_drain(rounds: int = 20) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=5)


def id2_async(scenario):
    """Run ``await scenario(client)`` on a fresh loop; background tasks are drained before exit."""
    async def _main():
        transport = httpx.ASGITransport(app=server.app, client=("203.0.113.10", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="https://www.meluni.com.tr") as c:
            try:
                return await scenario(c)
            finally:
                await id2_drain()
    return asyncio.run(_main())


def id2_hdr(token: str = "", ip: str = "") -> dict:
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if ip:
        h["cf-connecting-ip"] = ip
    return h


def id2_reset_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def id2_no_ip_limit(monkeypatch) -> None:
    """The per-IP slowapi limits are not what these tests measure (account/number budgets are)."""
    monkeypatch.setattr(deps.limiter, "enabled", False, raising=False)
    try:
        monkeypatch.setattr(server.app.state.limiter, "enabled", False, raising=False)
    except Exception:
        pass
