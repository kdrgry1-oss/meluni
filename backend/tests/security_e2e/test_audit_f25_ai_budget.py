"""Denetim #25: personel yapay zekâ uçlarında kişi başı saatlik sınır, günlük bütçe, eşzamanlılık
tavanı ve kullanım kaydı vardır."""
import asyncio

import pytest
from fastapi import HTTPException

import routes.ai_assistant as aa
from conftest import kv_run
from security import ai_budget


def _patch(monkeypatch):
    async def _llm(**_kw):
        return "Tamam.\n---META---\nINTENT: ASK\nKB_QUESTION: \nKB_ANSWER: \nINSTRUCTION: \n"

    async def _settings():
        return {"provider": "anthropic"}
    monkeypatch.setattr(aa, "llm_chat", _llm)
    monkeypatch.setattr(aa, "get_ai_settings", _settings)
    monkeypatch.setattr(aa, "_api_key_for", lambda s: "k")


def test_hourly_limit_and_usage_log(kv_client, kv_users, kv_db, monkeypatch):
    _patch(monkeypatch)
    kv_run(kv_db.settings.insert_one({"id": "business_rules", "values": {"ai.user_hourly_limit": 3}}))
    st = kv_users.staff(perms=["questions.view"])
    h = kv_users.hdr(st)
    for _ in range(3):
        assert kv_client.post("/api/ai-assistant/chat", json={"text": "x"}, headers=h).status_code == 200
    r = kv_client.post("/api/ai-assistant/chat", json={"text": "x"}, headers=h)
    assert r.status_code == 429
    logs = kv_run(kv_db.ai_usage.find({"user_id": st["id"]}).to_list(10))
    assert len(logs) == 3 and all(l["op"] == "assistant_chat" for l in logs)


def test_daily_budget_counts_batch_units(kv_db):
    kv_run(kv_db.settings.insert_one({"id": "business_rules", "values": {"ai.daily_limit": 10,
                                                                         "ai.user_hourly_limit": 0}}))
    kv_run(ai_budget.charge({"id": "a"}, "bulk_description", 8))
    with pytest.raises(HTTPException) as e:
        kv_run(ai_budget.charge({"id": "b"}, "bulk_description", 5))
    assert e.value.status_code == 429


def test_concurrency_cap(monkeypatch):
    dep = ai_budget.ai_slot("x")

    class _Req:
        headers = {}

    async def _run():
        g1 = dep(_Req()); await g1.__anext__()
        g2 = dep(_Req()); await g2.__anext__()
        g3 = dep(_Req())
        with pytest.raises(HTTPException) as e:
            await g3.__anext__()
        assert e.value.status_code == 429
        for g in (g1, g2):
            with pytest.raises(StopAsyncIteration):
                await g.__anext__()
        assert ai_budget._ACTIVE.get("anon", 0) == 0
    asyncio.run(_run())
