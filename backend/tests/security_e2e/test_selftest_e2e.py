"""Canlı güvenlik öz-testi (security/selftest.py): kontroller gerçekten çalışıyor, rapor kişisel veri
içermiyor, e-posta alıcılara gidiyor, yalnız süper admin tetikleyebiliyor."""
import json
from types import SimpleNamespace

import httpx
import pytest

from id_harness import (id_auth, id_find, id_findall, id_run, id_seed_role, id_seed_user, id_set_rule, id_token)
from security import alerts
from security import selftest
from security.crypto import encrypt

pytestmark = pytest.mark.usefixtures("id_env")

RESEND = "https://api.resend.com/emails"


@pytest.fixture
def st_env(id_archive, id_respx, monkeypatch):
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "archive_health", lambda: {"ok": True})
    monkeypatch.setattr(r2_storage, "archive_bucket", lambda: "test-arsiv")
    monkeypatch.setattr(r2_storage, "_get_client",
                        lambda: SimpleNamespace(delete_object=lambda **k: id_archive.pop(k["Key"], None)))
    monkeypatch.setattr(alerts, "RESEND_API_KEY", "re_test_dummy_key")
    id_set_rule("security.alert_emails", "a@example.test, b@example.test")
    id_set_rule("security.alert_from", "info@shop.test")
    route = id_respx.post(RESEND).mock(return_value=httpx.Response(200, json={"id": "m"}))
    for k in selftest._ENV_KEYS:
        monkeypatch.setenv(k, "x" * 40)
    return SimpleNamespace(archive=id_archive, resend=route)


def test_selftest_all_ok_and_email_to_both_recipients(id_client, st_env):
    id_seed_user(kind="super")
    rep = id_run(selftest.run_selftest(send_email=True))
    by = {c["name"]: c for c in rep["checks"]}
    assert rep["fail"] == 0, rep["checks"]
    assert by["Gizli R2 arşivi"]["status"] == "ok"
    assert by["Şifreli sırlar çözülebiliyor"]["status"] == "ok"
    assert by["Güvenlik ortam değişkenleri"]["status"] == "ok"
    assert by["Uyarı e-postası teslimi"]["status"] == "ok"
    # iki alıcıya da gitti, gönderen kuraldaki adres
    sent = [json.loads(c.request.content) for c in st_env.resend.calls]
    to = sorted(t for p in sent for t in p["to"])
    assert to == ["a@example.test", "b@example.test"]
    assert all("info@shop.test" in p["from"] for p in sent)
    # test dosyası arşivden silindi (yalnız silme-kaydı test nesnesi kalabilir)
    assert not [k for k in st_env.archive if k.startswith("_selftest/")]
    # rapor kaydedildi ve kişisel veri (tam e-posta) içermiyor
    doc = id_find("settings", {"id": "security_selftest"})
    assert doc and doc["fail"] == 0
    assert "a@example.test" not in json.dumps(doc, ensure_ascii=False)


def test_selftest_detects_undecryptable_secret(id_client, st_env):
    id_seed_user(kind="super")
    good = encrypt("iyzico-secret")
    # başka anahtarla şifrelenmiş (çözülemeyen) değer
    bad = "v1:" + "gAAAAA" + "A" * 80
    id_run(__import__("routes.deps", fromlist=["db"]).db.settings.insert_one(
        {"id": "iyzico", "api_key": good, "api_secret": bad}))
    rep = id_run(selftest.run_selftest(send_email=False))
    chk = {c["name"]: c for c in rep["checks"]}["Şifreli sırlar çözülebiliyor"]
    assert chk["status"] == "fail" and "1/2" in chk["detail"]


def test_selftest_flags_plaintext_and_missing_env(id_client, st_env, monkeypatch):
    id_seed_user(kind="super")
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    from routes import deps
    id_run(deps.db.orders.insert_one({"id": "o1", "refund_bank_info": {"iban": "TR000000000000000000000000"},
                                      "billing_info": {"tax_number": "12345678901"}}))
    rep = id_run(selftest.run_selftest(send_email=False))
    by = {c["name"]: c for c in rep["checks"]}
    assert by["Güvenlik ortam değişkenleri"]["status"] == "warn"
    assert "SECRETS_MASTER_KEY" in by["Güvenlik ortam değişkenleri"]["detail"]
    data = by["Veri koruma durumu"]
    assert data["status"] == "warn"
    assert "düz metin iade IBAN: 1" in data["detail"] and "düz metin vergi/TC no alanı: 1" in data["detail"]
    # değerlerin kendisi raporda YOK
    assert "TR000000" not in json.dumps(rep) and "12345678901" not in json.dumps(rep)


def test_selftest_archive_failure_is_fail(id_client, st_env, monkeypatch):
    id_seed_user(kind="super")
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "archive_health", lambda: {"ok": False, "error": "AccessDenied"})
    rep = id_run(selftest.run_selftest(send_email=False))
    assert {c["name"]: c for c in rep["checks"]}["Gizli R2 arşivi"]["status"] == "fail"
    assert rep["fail"] >= 1


def test_selftest_endpoints_super_admin_only(id_client, st_env):
    sup = id_seed_user(kind="super")
    id_seed_role("ops", ["orders.view", "orders.update_status"])
    staff = id_seed_user(kind="staff", role_id="ops")
    r = id_client.post("/api/admin/security/selftest/run", headers=id_auth(id_token(staff)))
    assert r.status_code == 403
    r = id_client.post("/api/admin/security/selftest/run", headers=id_auth(id_token(sup)))
    assert r.status_code == 200, r.text
    assert r.json()["checks"]
    r = id_client.get("/api/admin/security/selftest", headers=id_auth(id_token(sup)))
    assert r.status_code == 200 and r.json().get("checks")


def test_failing_check_named_in_alert_and_not_repeated_same_day(id_client, st_env, monkeypatch):
    """SMS'e dönüşen kritik öz-test uyarısı hangi kontrolün bozuk olduğunu SÖYLER; otomatik çalışmalarda
    (her yayın sonrası) aynı hata günde bir kez bildirilir; elle çalıştırma her zaman gönderir."""
    id_seed_user(kind="super")
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "archive_health", lambda: {"ok": False, "error": "AccessDenied"})
    id_run(selftest.run_selftest())
    crit = [a for a in id_findall("alerts", {"kind": "security_selftest"}) if a["level"] == "critical"]
    assert len(crit) == 1 and "Gizli R2 arşivi" in crit[0]["title"]
    id_run(selftest.run_selftest())                       # ikinci otomatik çalışma (ör. yeni yayın)
    assert len([a for a in id_findall("alerts", {"kind": "security_selftest"}) if a["level"] == "critical"]) == 1
    id_run(selftest.run_selftest(send_email=True))        # elle "Şimdi Çalıştır"
    assert len([a for a in id_findall("alerts", {"kind": "security_selftest"}) if a["level"] == "critical"]) == 2
