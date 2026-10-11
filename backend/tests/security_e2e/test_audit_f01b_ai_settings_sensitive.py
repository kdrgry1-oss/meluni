"""Denetim #1 (tamamlama): AI sağlayıcı/model/anahtar değişikliği settings.edit ister; persona gibi
soru-ekibi alanları questions.answer ile kaydedilebilir (panel tüm alanları gönderse bile)."""
from conftest import kv_run


def test_sensitive_fields_need_settings_edit(kv_client, kv_users, kv_db):
    kv_run(kv_db.settings.insert_one({"id": "ai_chatbot", "provider": "anthropic", "model": "m1", "persona": "P"}))
    qa = kv_users.staff(perms=["questions.view", "questions.answer"])
    h = kv_users.hdr(qa)
    # persona değişir, hassas alanlar aynı → izinli
    r = kv_client.post("/api/ai/settings", headers=h, json={"provider": "anthropic", "model": "m1", "persona": "P2"})
    assert r.status_code == 200, r.text
    for body in ({"provider": "openai"}, {"model": "m2"}, {"custom_api_key": "yeni-anahtar-degeri"}):
        assert kv_client.post("/api/ai/settings", headers=h, json=body).status_code == 403, body
    doc = kv_run(kv_db.settings.find_one({"id": "ai_chatbot"}))
    assert doc["provider"] == "anthropic" and doc["model"] == "m1" and doc["persona"] == "P2"
    admin = kv_users.staff(perms=["questions.answer", "settings.edit"])
    assert kv_client.post("/api/ai/settings", headers=kv_users.hdr(admin), json={"model": "m2"}).status_code == 200
