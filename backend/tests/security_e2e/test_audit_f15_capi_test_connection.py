"""Denetim #15: CAPI bağlantı testi görüntüleme yetkisiyle yapılamaz; kayıtlı anahtar kullanılınca
sağlayıcı/piksel kayıttan gelir, test kodu zorunlu, saatlik sınır ve denetim kaydı vardır."""
import services.capi.meta as meta_mod
from conftest import kv_run


def _seed(db):
    kv_run(db.marketing_pixels.insert_one({"id": "px1", "provider": "meta", "tag_id": "REAL-PIXEL",
                                           "access_token": "stored-token-xyz"}))


def test_view_only_forbidden(kv_client, kv_users, kv_db):
    _seed(kv_db)
    v = kv_users.staff(perms=["settings.view"])
    r = kv_client.post("/api/marketing-pixels/test-connection", headers=kv_users.hdr(v),
                       json={"id": "px1", "provider": "tiktok", "tag_id": "ATTACKER", "test_event_code": "T1"})
    assert r.status_code == 403


def test_stored_token_bound_to_record_and_limited(kv_client, kv_users, kv_db, monkeypatch):
    _seed(kv_db)
    calls = []

    async def _tc(pixel_id, access_token, test_event_code=None):
        calls.append((pixel_id, test_event_code))
        return {"ok": True, "status": 200}
    monkeypatch.setattr(meta_mod, "test_connection", _tc)
    ed = kv_users.staff(perms=["settings.view", "settings.edit"])
    h = kv_users.hdr(ed)
    url = "/api/marketing-pixels/test-connection"
    r = kv_client.post(url, headers=h, json={"id": "px1", "provider": "meta", "tag_id": "ATTACKER"})
    assert r.status_code == 400 and "Test Event Code" in r.text
    r = kv_client.post(url, headers=h, json={"id": "px1", "provider": "tiktok", "tag_id": "ATTACKER",
                                              "test_event_code": "TEST1"})
    assert r.status_code == 200, r.text
    assert calls == [("REAL-PIXEL", "TEST1")]          # sağlayıcı + piksel KAYITTAN
    log = kv_run(kv_db.capi_event_logs.find_one({"event_name": "test_connection"}))
    assert log["actor_id"] == ed["id"] and log["used_stored_token"] is True
    for _ in range(9):
        kv_client.post(url, headers=h, json={"id": "px1", "provider": "meta", "test_event_code": "T"})
    r = kv_client.post(url, headers=h, json={"id": "px1", "provider": "meta", "test_event_code": "T"})
    assert r.status_code == 429
