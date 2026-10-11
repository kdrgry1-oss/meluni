"""İYS'de (SMS RET / iys.org.tr) reddeden alıcı sitede de RET olur; kampanya kitlesinden düşer."""
import json

import httpx
import respx

from conftest import kv_run
from kv_harness import kv_find, kv_findall, kv_insert

SEARCH = "https://api.netgsm.com.tr/iys/search"


def _cfg(monkeypatch):
    import routes.iys as iys

    async def _c():
        return {"username": "u", "password": "p", "brand_code": "123"}
    monkeypatch.setattr(iys, "_iys_config", _c)


def _iys_answer(statuses):
    def _h(request):
        d = json.loads(request.content)["body"]["data"][0]
        st = statuses.get(d["recipient"])
        if st is None:
            return httpx.Response(200, json={"code": "50", "error": "Kayıt Bulunamadı"})
        return httpx.Response(200, json={"code": "0", "error": False, "query": {
            "status": st[0], "consentDate": st[1], "recipient": d["recipient"], "type": d["type"], "source": "HS_MESAJ"}})
    return _h


def test_iys_ret_is_pulled_into_site_and_audience(monkeypatch):
    import routes.iys as iys
    from routes.email_marketing import _campaign_audience
    _cfg(monkeypatch)
    kv_insert("users", {"id": "u-ret", "email": "ret@example.test", "accepts_marketing": True,
                        "marketing_consent_at": "2026-09-01T10:00:00+00:00", "marketing_prefs": {"email": True, "sms": True}})
    kv_insert("newsletter_subscribers", {"email": "ret@example.test", "consent": True, "active": True,
                                         "created_at": "2026-09-01T10:00:00+00:00"})
    kv_insert("newsletter_subscribers", {"email": "keep@example.test", "consent": True, "active": True,
                                         "created_at": "2026-09-01T10:00:00+00:00"})
    # Yeni ONAY'ı henüz İYS'ye bildirilmemiş müşteri: İYS'deki ESKİ RET uygulanmaz.
    kv_insert("iys_consents", {"id": "c-new", "email": "again@example.test", "phone": "", "channels": ["EPOSTA"],
                               "status": "ONAY", "source": "HS_WEB", "consent_date": "2026-10-06T09:00:00+00:00",
                               "reported": False, "created_at": "2026-10-06T09:00:00+00:00"})
    answers = {"ret@example.test": ("RET", "2026-10-05 12:00:00"), "keep@example.test": ("ONAY", "2026-09-01 13:00:00"),
               "again@example.test": ("RET", "2026-09-20 12:00:00")}
    with respx.mock(assert_all_called=False) as m:
        route = m.post(SEARCH).mock(side_effect=_iys_answer(answers))
        res = kv_run(iys.sync_iys_rets(pause_s=0))
        assert res["ret"] == 1 and res["skipped_newer_local"] == 1, res
        n_calls = route.call_count
        # aynı gün ikinci tur tekrar sorgulamaz
        res2 = kv_run(iys.sync_iys_rets(pause_s=0))
        assert route.call_count == n_calls and res2.get("checked", 0) == 0
    aud = {r["email"] for r in kv_run(_campaign_audience())["rows"]}
    assert "ret@example.test" not in aud and "keep@example.test" in aud and "again@example.test" in aud
    u = kv_find("users", {"id": "u-ret"})
    assert u["accepts_marketing"] is False and u["marketing_prefs"] == {"email": False, "sms": True}
    assert kv_find("newsletter_subscribers", {"email": "ret@example.test"})["active"] is False
    ret = [c for c in kv_findall("iys_consents", {"email": "ret@example.test"}) if c["status"] == "RET"]
    assert len(ret) == 1 and ret[0]["source"] == "IYS" and ret[0]["reported"] is True   # İYS'ye geri gönderilmez


def test_iys_limit_error_stops_run(monkeypatch):
    import routes.iys as iys
    _cfg(monkeypatch)
    for i in range(5):
        kv_insert("newsletter_subscribers", {"email": f"n{i}@example.test", "consent": True, "active": True})
    with respx.mock(assert_all_called=False) as m:
        route = m.post(SEARCH).mock(return_value=httpx.Response(200, json={"code": "103", "error": "Sinir asimi"}))
        res = kv_run(iys.sync_iys_rets(pause_s=0))
    assert res["stopped"] == "sınır aşımı" and route.call_count == 1 and res["ret"] == 0


def test_sms_only_preference_does_not_reenable_email(kv_db):
    from routes.iys import consent_update
    from routes.consents import _collect
    kv_insert("users", {"id": "u-mix", "email": "mix@example.test", "phone": "", "accepts_marketing": True})
    kv_run(consent_update({"email": False, "sms": True}, current_user={"id": "u-mix", "email": "mix@example.test"}))
    assert kv_run(_collect())[("email", "mix@example.test")]["status"] == "ret"
    assert kv_find("users", {"id": "u-mix"})["accepts_marketing"] is False
