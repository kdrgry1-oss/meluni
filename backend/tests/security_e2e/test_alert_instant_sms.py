"""Kritik güvenlik/hata uyarısında ANINDA SMS (sahip isteği 2026-10-05): numara İşletme Kuralı'ndan, içerikte
kişisel veri yok, kısılmış (aynı parmak izi) uyarıda tekrar yok, saatlik/günlük tavan, kural ile kapatılabilir,
bilgi/uyarı düzeyinde SMS yok; tek seferlik iş numarayı yalnız eksikse ekler."""
from __future__ import annotations

import httpx
import pytest

import notification_service as ns
from id_harness import id_find, id_findall, id_run, id_set_rule
from routes import deps
from security import alerts

pytestmark = pytest.mark.usefixtures("id_env")


@pytest.fixture
def sink(id_respx, monkeypatch):
    monkeypatch.setattr(alerts, "RESEND_API_KEY", "re_test_dummy_key")
    id_set_rule("security.alert_emails", "a@example.test")
    id_respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(200, json={"id": "m"}))
    sent = []

    async def _fake(cfg, to, message, *a, **k):
        sent.append({"to": to, "message": message})
        return {"success": True, "response": "00 test"}
    monkeypatch.setitem(ns.SMS_IMPL, "kvfake", ns._sms_guard(_fake))
    id_run(deps.db.settings.insert_one({"id": "notification_providers", "sms_active": "kvfake",
                                        "whatsapp_active": False, "email_active": False, "providers": {}}))
    ns._SMS_BLOCK.update({"set": set(), "at": 0.0})
    id_set_rule("security.alert_sms_phones", "05550000001")
    id_set_rule("security.alert_page_enabled", True)   # dakikalık tekrar testleri (varsayılan KAPALI)
    return sent


def _send(title, level="critical", fp=None, body=""):
    return id_run(alerts.send_alert("test_kind", title, body, level=level, fingerprint=fp))


def test_critical_alert_sends_instant_sms_without_personal_data(id_client, sink):
    r = _send("Toplu kişisel veri çözme: personel ali@example.test 05551234567",
              body="müşteri adresi Atatürk Cad. No:5, tel 05559876543")
    assert len(sink) == 1 and sink[0]["to"].endswith("5550000001")
    m = sink[0]["message"]
    assert "GUVENLIK KRITIK" in m and "Toplu kisisel veri" in m
    assert "example.test" not in m and "05551234567" not in m and "5551234567" not in m
    assert "Ataturk" not in m and "9876543" not in m           # gövde SMS'e girmez
    assert all(ord(c) < 128 for c in m) and len(m) <= 300
    doc = id_find("alerts", {"id": r["alert_id"]})
    assert doc["sms_instant"]["sent"] == 1


def test_no_sms_for_warning_info_or_throttled_duplicates(id_client, sink):
    _send("uyarı", level="warning")
    _send("bilgi", level="info")
    assert sink == []
    _send("Aynı kritik olay", fp="ayni")
    _send("Aynı kritik olay", fp="ayni")                      # kısılmış → e-posta da SMS de yok
    assert len(sink) == 1


def test_hourly_cap_and_disable_rule(id_client, sink):
    for i in range(8):
        _send(f"Kritik {i}", fp=f"k{i}")
    assert len(sink) == 5                                       # saatte en çok 5
    id_set_rule("security.alert_sms_instant", False)
    n = len(sink)
    from security import alert_sms
    res = id_run(alert_sms.instant_critical_sms("x", "k", "Kritik kapalı"))
    assert res["reason"] == "disabled" and len(sink) == n


def test_no_provider_no_crash(id_client, id_respx, monkeypatch):
    monkeypatch.setattr(alerts, "RESEND_API_KEY", "re_test_dummy_key")
    id_respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(200, json={"id": "m"}))
    id_set_rule("security.alert_emails", "a@example.test")
    id_set_rule("security.alert_sms_phones", "05550000001")
    r = _send("Kritik ama SMS sağlayıcı yok")
    assert r["alert_id"] and r["throttled"] is False and r["recipients"]   # e-posta teslimi denendi
    from security import alert_sms
    assert id_run(alert_sms.instant_critical_sms("x", "k", "t"))["reason"] == "sms_not_configured"
    assert not id_findall("security_pages")                            # SMS gitmediyse çağrı da başlamaz


def test_one_time_job_adds_number_and_stop_email_only_when_missing(id_client):
    import scheduler
    from notification_service import normalize_phone_tr
    id_set_rule("security.alert_sms_phones", "05550000001")
    id_set_rule("security.alert_page_stop_emails", "")
    id_run(scheduler._ensure_guvenlik_sms_numarasi())
    vals = id_find("settings", {"id": "business_rules"})["values"]
    nums = [normalize_phone_tr(x) for x in vals["security.alert_sms_phones"].split(",")]
    assert normalize_phone_tr("05550000001") in nums and len(nums) == 2   # panel değeri korunur, ekleme yapılır
    assert vals["security.alert_page_stop_emails"]                       # boş alan dolduruldu
    # ikinci çalıştırma (bayrak) hiçbir şey yapmaz; kullanıcı sonradan değiştirirse geri gelmez
    id_set_rule("security.alert_sms_phones", "05550000002")
    id_run(scheduler._ensure_guvenlik_sms_numarasi())
    assert id_find("settings", {"id": "business_rules"})["values"]["security.alert_sms_phones"] == "05550000002"


# ─────────────── dakikalık çağrı: yetkili panele girene kadar tekrar SMS ───────────────

def _tick(at):
    from freezegun import freeze_time
    from security import alert_paging
    with freeze_time(at):
        return id_run(alert_paging.paging_tick())


def test_paging_repeats_every_minute_until_authorized_admin_enters_panel(id_client, sink):
    from datetime import datetime, timedelta, timezone
    from freezegun import freeze_time
    from id_harness import id_auth, id_seed_user, id_token
    id_set_rule("security.alert_page_stop_emails", "patron@example.test")
    t0 = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)
    with freeze_time(t0):
        _send("Sızma şüphesi", fp="p1")
    assert len(sink) == 1
    assert _tick(t0 + timedelta(seconds=30))["state"] == "waiting"      # 1 dk dolmadı
    r = _tick(t0 + timedelta(seconds=61))
    assert r["state"] == "sent" and len(sink) == 2 and "TEKRAR 1" in sink[-1]["message"]
    assert _tick(t0 + timedelta(seconds=90))["state"] == "waiting"
    assert _tick(t0 + timedelta(seconds=122))["state"] == "sent" and "TEKRAR 2" in sink[-1]["message"]
    # yeni kritik uyarı aynı çağrıya eklenir (ayrı tekrar zinciri başlamaz)
    with freeze_time(t0 + timedelta(seconds=130)):
        _send("İkinci kritik", fp="p2")
    assert _tick(t0 + timedelta(seconds=183))["state"] == "sent" and "(2 adet)" in sink[-1]["message"]
    # yetkisiz personel (başka e-posta) panele girer → durmaz
    other = id_seed_user(kind="super", email="baska@example.test")
    id_client.get("/api/admin/me/permissions", headers=id_auth(id_token(other)))
    assert _tick(t0 + timedelta(seconds=245))["state"] == "sent"
    # yetkili yönetici panele girer → çağrı kapanır, SMS durur
    boss = id_seed_user(kind="super", email="patron@example.test")
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(id_token(boss))).status_code == 200
    n = len(sink)
    assert _tick(t0 + timedelta(seconds=400))["state"] == "idle" and len(sink) == n
    closed = [d for d in id_findall("security_pages") if d.get("status") == "closed"]
    assert closed and closed[0]["closed_reason"] == "panel_girisi"


def test_customer_account_with_same_email_does_not_stop_paging(id_client, sink):
    from datetime import datetime, timedelta, timezone
    from freezegun import freeze_time
    from security import alert_paging
    id_set_rule("security.alert_page_stop_emails", "patron@example.test")
    t0 = datetime(2026, 10, 5, 2, 0, tzinfo=timezone.utc)
    with freeze_time(t0):
        _send("Kritik", fp="c1")
    id_run(alert_paging.note_panel_presence({"email": "patron@example.test", "is_admin": False}))
    assert _tick(t0 + timedelta(seconds=65))["state"] == "sent"


def test_paging_stops_when_alerts_acknowledged(id_client, sink):
    from datetime import datetime, timedelta, timezone
    from freezegun import freeze_time
    t0 = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)
    with freeze_time(t0):
        r = _send("Kritik onaylanacak", fp="a1")
    id_run(deps.db.alerts.update_one({"id": r["alert_id"]}, {"$set": {"acked_at": t0.isoformat()}}))
    assert _tick(t0 + timedelta(seconds=70))["state"] == "closed_acked"
    assert _tick(t0 + timedelta(seconds=140))["state"] == "idle"


def test_paging_slows_after_three_hours_and_daily_cap(id_client, sink, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from freezegun import freeze_time
    from security import alert_paging
    t0 = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
    with freeze_time(t0):
        _send("Uzun süren kritik", fp="l1")
    id_run(deps.db.security_pages.update_one({"status": "open"}, {"$set": {"repeats": alert_paging.ESCALATE_AFTER}}))
    assert _tick(t0 + timedelta(seconds=120))["state"] == "waiting"       # artık 10 dk aralık
    assert _tick(t0 + timedelta(seconds=605))["state"] == "sent"
    monkeypatch.setattr(alert_paging, "DAILY_PAGE_SMS_MAX", 1)
    assert _tick(t0 + timedelta(seconds=1300))["state"] == "daily_cap"


def test_jobs_registered():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler)
    assert 'id="ensure_guvenlik_sms_numarasi_once"' in src
    assert 'paging_tick, "interval", minutes=1, id="security_alert_paging"' in src


def test_paging_disabled_by_default_closes_open_page_without_sms(id_client, sink):
    from datetime import datetime, timedelta, timezone
    from freezegun import freeze_time
    id_set_rule("security.alert_page_enabled", False)
    t0 = datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc)
    with freeze_time(t0):
        _send("Kritik tekil", fp="d1")
    assert len(sink) == 1                                   # yalnız ilk anlık SMS
    assert _tick(t0 + timedelta(seconds=70))["state"] == "disabled"
    assert _tick(t0 + timedelta(seconds=140))["state"] == "idle" and len(sink) == 1
