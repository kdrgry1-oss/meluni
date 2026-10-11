"""Denetim #24: /api/health?diag=1 kimliksiz çağrıda ağır teşhis modunu AÇMAZ; sabit ve ucuz canlılık
yanıtı döner. Destek anahtarı ya da personel oturumu ile açılır."""


def test_anonymous_diag_is_light(kv_client):
    r = kv_client.get("/api/health?diag=1")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "version", "db"}


def test_staff_can_open_diag(kv_client, kv_users):
    st = kv_users.staff(perms=["settings.view"])
    r = kv_client.get("/api/health?diag=1", headers=kv_users.hdr(st))
    assert r.status_code == 200
    assert set(r.json()) != {"status", "version", "db"}


def test_support_key_opens_diag(kv_client, monkeypatch):
    monkeypatch.setenv("SUPPORT_AUDIT_KEY", "k" * 40)
    r = kv_client.get("/api/health?diag=1", headers={"X-Audit-Key": "k" * 40})
    assert r.status_code == 200 and set(r.json()) != {"status", "version", "db"}
    r = kv_client.get("/api/health?diag=1", headers={"X-Audit-Key": "wrong"})
    assert set(r.json()) == {"status", "version", "db"}
