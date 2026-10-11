"""AUTH14 — kayıt ucunda hesap-varlığı taraması: kayıt doğrulamasız kalır (kullanıcı kararı), toplu tarama
ağ başına tavanla engellenir, kademeli yavaşlatılır ve uyarı üretir. Gerçek müşterinin ilk kaydı etkilenmez."""
import pytest

from id_harness import id_findall, id_ip, id_seed_user, id_set_rule

pytestmark = pytest.mark.usefixtures("id_env")

REG = "/api/auth/register"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    import security.register_guard as rg
    slept = []

    async def _sleep(s):
        slept.append(s)
    monkeypatch.setattr(rg.asyncio, "sleep", _sleep)
    # slowapi 5/dk sınırı bu testlerde ağ tavanını gölgelemesin
    from routes.deps import limiter
    if limiter is not None:
        monkeypatch.setattr(limiter, "enabled", False, raising=False)
    return slept


def _reg(client, email, ip, phone=""):
    body = {"email": email, "password": "Guclu-Sifre-123", "first_name": "Ad", "last_name": "Soyad"}
    if phone:
        body["phone"] = phone
    return client.post(REG, json=body, headers=id_ip(ip))


def test_new_customer_registers_normally_and_is_logged_in(id_client, _fast):
    r = _reg(id_client, "yeni@example.test", "198.51.100.7")
    assert r.status_code == 200, r.text
    assert r.json().get("token")
    assert _fast == []                                          # hiç bekleme yok


def test_existing_email_still_answers_but_scan_from_one_network_is_blocked(id_client, _fast):
    id_set_rule("security.register_exists_daily_cap_per_net", 3)
    for i in range(3):
        id_seed_user(kind="customer", email=f"var{i}@example.test")
    codes = [_reg(id_client, f"var{i}@example.test", f"203.0.113.{10 + i}").status_code for i in range(3)]
    assert codes == [400, 400, 400]                              # tek tek sorguda mesaj aynı kalır
    assert _fast and _fast[0] == 1                              # 2. "zaten kayıtlı"dan sonra yavaşlatma
    # aynı /24 ağdan devam: kayıtlı olsun olmasın aynı 429 (tarayıcıya artık cevap sızmaz)
    r1 = _reg(id_client, "var0@example.test", "203.0.113.99")
    r2 = _reg(id_client, "hic-yok@example.test", "203.0.113.98")
    assert r1.status_code == r2.status_code == 429
    assert r1.json()["detail"] == r2.json()["detail"]
    assert not id_findall("users", {"email": "hic-yok@example.test"})
    alerts = [a for a in id_findall("alerts") if a.get("kind") == "register_enumeration_blocked"]
    assert len(alerts) == 1                                     # ağ/gün başına tek uyarı
    assert "example.test" not in str(alerts[0])                 # e-posta uyarıya/sayaca yazılmaz
    # başka ağdan gerçek müşteri etkilenmez
    assert _reg(id_client, "baska@example.test", "192.0.2.5").status_code == 200


def test_phone_probe_counts_too(id_client, _fast):
    id_set_rule("security.register_exists_daily_cap_per_net", 2)
    id_seed_user(kind="customer", email="tel@example.test")
    from id_harness import id_update
    id_update("users", {"email": "tel@example.test"}, {"$set": {"phone": "905551112233"}})
    assert _reg(id_client, "t1@example.test", "203.0.113.1", phone="05551112233").status_code == 400
    assert _reg(id_client, "t2@example.test", "203.0.113.2", phone="05551112233").status_code == 400
    assert _reg(id_client, "t3@example.test", "203.0.113.3").status_code == 429


def test_attempt_cap_per_network(id_client, _fast):
    id_set_rule("security.register_attempt_daily_cap_per_net", 3)
    oks = [_reg(id_client, f"n{i}@example.test", "198.18.0.1").status_code for i in range(4)]
    assert oks[:3] == [200, 200, 200] and oks[3] == 429


def test_distributed_wave_alerts_once(id_client, _fast):
    id_set_rule("security.register_exists_hourly_alert", 3)
    id_seed_user(kind="customer", email="dalga@example.test")
    for i in range(4):
        assert _reg(id_client, "dalga@example.test", f"10.{i}.0.1").status_code == 400
    waves = [a for a in id_findall("alerts") if a.get("kind") == "register_enumeration_wave"]
    assert len(waves) == 1 and waves[0]["level"] == "critical"


def test_counters_store_no_personal_data_and_expire(id_client, _fast):
    id_seed_user(kind="customer", email="gizli@example.test")
    _reg(id_client, "gizli@example.test", "203.0.113.50")
    docs = id_findall("register_probe_counters")
    assert docs and "gizli" not in str(docs) and "203.0.113.50" not in str(docs)
    assert all(d.get("expire_at") for d in docs)
    assert {d["net"] for d in docs} >= {"203.0.113.0/24"}


def test_rules_are_super_admin_only(id_client):
    from id_harness import id_auth, id_find, id_seed_role, id_token
    from permissions import ALL_PERMISSION_KEYS
    id_seed_role("all-but-super", sorted(ALL_PERMISSION_KEYS))
    staff = id_seed_user(kind="staff", role_id="all-but-super")
    r = id_client.put("/api/admin/business-rules",
                      json={"values": {"security.register_exists_daily_cap_per_net": 999}},
                      headers=id_auth(id_token(staff)))
    assert r.status_code == 403, r.text
    doc = id_find("settings", {"id": "business_rules"}) or {}
    assert "999" not in str(doc)
