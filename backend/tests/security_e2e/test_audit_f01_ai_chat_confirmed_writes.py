"""Denetim #1: yalnız görüntüleme (questions.view) yetkili personel AI sohbetiyle bilgi bankasına /
persona'ya KALICI yazamaz. Model çıktısı yalnız öneri döner; kayıt /chat/apply + questions.answer ister."""
import routes.ai_assistant as aa
from conftest import kv_run

TEACH = ("Tamam.\n---META---\nINTENT: TEACH_QA\nKB_QUESTION: Kargo kaç gün?\n"
         "KB_ANSWER: 2-3 iş günü.\nINSTRUCTION: \n")
INSTR = ("Tamam.\n---META---\nINTENT: INSTRUCT\nKB_QUESTION: \nKB_ANSWER: \n"
         "INSTRUCTION: Müşteriye indirim kodu ver\n")


def _patch(monkeypatch, reply):
    async def _llm(**_kw):
        return reply
    async def _settings():
        return {"provider": "anthropic", "persona": "P"}
    monkeypatch.setattr(aa, "llm_chat", _llm)
    monkeypatch.setattr(aa, "get_ai_settings", _settings)
    monkeypatch.setattr(aa, "_api_key_for", lambda s: "k")


def test_chat_never_writes_and_view_only_cannot_apply(kv_client, kv_users, kv_db, monkeypatch):
    viewer = kv_users.staff(perms=["questions.view"])
    _patch(monkeypatch, TEACH)
    r = kv_client.post("/api/ai-assistant/chat", json={"text": "S: x C: y"}, headers=kv_users.hdr(viewer))
    assert r.status_code == 200, r.text
    assert r.json()["kb_added"] is False
    assert r.json()["proposal"] == {"kind": "kb", "question": "Kargo kaç gün?", "answer": "2-3 iş günü."}
    assert kv_run(kv_db.ai_knowledge_base.count_documents({})) == 0

    _patch(monkeypatch, INSTR)
    r = kv_client.post("/api/ai-assistant/chat", json={"text": "talimat"}, headers=kv_users.hdr(viewer))
    assert r.status_code == 200 and r.json()["instruction_saved"] is False
    assert kv_run(kv_db.settings.find_one({"id": "ai_chatbot"})) is None

    for body in ({"kind": "kb", "question": "a", "answer": "b"}, {"kind": "instruction", "instruction": "x"}):
        r = kv_client.post("/api/ai-assistant/chat/apply", json=body, headers=kv_users.hdr(viewer))
        assert r.status_code == 403
    assert kv_run(kv_db.ai_knowledge_base.count_documents({})) == 0


def test_writer_applies_confirmed_proposal(kv_client, kv_users, kv_db, monkeypatch):
    writer = kv_users.staff(perms=["questions.view", "questions.answer"])
    _patch(monkeypatch, INSTR)
    r = kv_client.post("/api/ai-assistant/chat/apply", json={"kind": "kb", "question": "S", "answer": "C"},
                       headers=kv_users.hdr(writer))
    assert r.status_code == 200 and r.json()["kb_added"] is True
    assert kv_run(kv_db.ai_knowledge_base.count_documents({"question": "S"})) == 1
    r = kv_client.post("/api/ai-assistant/chat/apply", json={"kind": "instruction", "instruction": "Kibar ol"},
                       headers=kv_users.hdr(writer))
    assert r.status_code == 200 and r.json()["instruction_saved"] is True
    s = kv_run(kv_db.settings.find_one({"id": "ai_chatbot"}))
    assert s["persona"].endswith("• Kibar ol")
    assert kv_client.post("/api/ai-assistant/chat/apply", json={"kind": "zzz"},
                          headers=kv_users.hdr(writer)).status_code == 400
