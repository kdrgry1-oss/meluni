"""SOC-01 — kritik güvenlik uyarısının ≤ 15 dk İNSAN TEYİDİ ölçülebilir ve tekrarlanabilir:

  * her uyarı/kritik uyarı e-postasında alıcıya özel, imzalı, süreli, TEK KULLANIMLIK "Gördüm" bağlantısı
    (GET yalnız onay sayfası — tarayıcı ön-izlemesi onay üretmez; POST kaydeder), panelde "Onayla"
    (yalnız süper admin, denetimli);
  * onaylanmayan kritik uyarı İşletme Kuralı süresi (15 dk) sonunda TAM BİR KEZ "[YANIT YOK]" ile yeniden
    gönderilir + küresel SMS tavanına (ve yönetici MFA yedek payına) saygılı kısa SMS;
  * tatbikat: gerçek hattan [TATBİKAT] uyarısı → onay ≤ 15 dk = geçti; onay yok = kaldı + yükseltme;
    aylık otomatik tatbikat işi (ilk Pazartesi 10:07 İstanbul, kuralla kapatılabilir); öz-test kontrolü.

Gerçek uyarı hattı (security.alerts._deliver) çalışır; Resend respx ile taklit edilir.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from freezegun import freeze_time

import notification_service as ns
from id_harness import (id_auth, id_find, id_findall, id_run, id_seed_role, id_seed_user, id_set_rule,
                        id_token, id_audit_actions)
from routes import deps
from security import alert_ack, alerts

pytestmark = pytest.mark.usefixtures("id_env")

RESEND = "https://api.resend.com/emails"
API = "https://api.shop.test"
LINK_RE = re.compile(re.escape(API) + r"/api/security/alert-ack\?t=(\S+)")
T0 = "2026-10-05 07:00:00"  # UTC (10:00 İstanbul, Pazartesi)


@pytest.fixture
def soc(id_respx, monkeypatch):
    import tenant_config
    monkeypatch.setattr(alerts, "RESEND_API_KEY", "re_test_dummy_key")
    monkeypatch.setenv("PUBLIC_API_URL", API)
    tenant_config.invalidate()
    id_run(deps.db.settings.insert_one({"id": "email_smtp", "username": "noreply@shop.test"}))
    id_set_rule("security.alert_emails", "a@example.test, b@example.test")
    route = id_respx.post(RESEND).mock(return_value=httpx.Response(200, json={"id": "m"}))
    yield route
    tenant_config.invalidate()


def _payloads(route):
    return [json.loads(c.request.content) for c in route.calls]


def _tokens(route, start: int = 0) -> dict:
    """{alıcı: token} — verilen çağrı indeksinden itibaren."""
    out = {}
    for p in _payloads(route)[start:]:
        m = LINK_RE.search(p["text"])
        if m:
            out[p["to"][0]] = m.group(1)
    return out


def _critical(title="Kritik test olayı", level="critical", fp=None):
    return id_run(alerts.send_alert("unit_critical", title, "gövde", level=level,
                                    fingerprint=fp or f"fp-{title}-{level}"))


def _alert(aid):
    return id_find("alerts", {"id": aid})


def _ack(client, tok, method="post"):
    return getattr(client, method)(f"/api/security/alert-ack?t={tok}")


# ───────────────────────────── onay bağlantısı ─────────────────────────────

def test_critical_email_carries_per_recipient_signed_link_and_get_does_not_ack(id_client, soc):
    with freeze_time(T0):
        res = _critical()
    aid = res["alert_id"]
    doc = _alert(aid)
    assert doc["ack_required"] is True and doc["recipients"] == ["a@example.test", "b@example.test"]
    assert doc["subject"].endswith("Kritik test olayı") and "acked_at" not in doc
    toks = _tokens(soc)
    assert set(toks) == {"a@example.test", "b@example.test"} and len(set(toks.values())) == 2
    assert all(t.startswith(aid + ".") for t in toks.values())
    texts = [p["text"] for p in _payloads(soc)]
    assert all("Güvenlik Paneli → Onay bekleyen uyarılar → Onayla" in t for t in texts)
    assert all("15 dakika içinde onaylanmazsa" in t for t in texts)
    # nonce bağlantıda YOK (yalnız sunucuda)
    assert all(doc["ack_nonce"] not in t for t in texts)
    with freeze_time(T0) as fr:
        fr.tick(timedelta(minutes=1))
        r = _ack(id_client, toks["a@example.test"], "get")
    assert r.status_code == 200 and "Gördüm / İnceliyorum" in r.text and 'method="post"' in r.text
    assert "Kritik test olayı" not in r.text and "gövde" not in r.text  # içerik açığa çıkmaz
    assert r.headers["cache-control"] == "no-store"
    assert "acked_at" not in _alert(aid)  # GET hiçbir şey kaydetmez


def test_valid_link_records_ack_once_then_reuse_is_rejected(id_client, soc):
    with freeze_time(T0) as fr:
        aid = _critical()["alert_id"]
        toks = _tokens(soc)
        fr.tick(timedelta(minutes=3))
        r = _ack(id_client, toks["b@example.test"])
        assert r.status_code == 200 and "onayınız kaydedildi" in r.text
        doc = _alert(aid)
        assert doc["acked_by"] == "e-posta bağlantısı" and doc["ack_via"] == "email_link"
        assert doc["ack_seconds"] == 180 and doc["ack_recipient"] == "b***@example.test"
        assert doc["acked_at"].startswith("2026-10-05T07:03:00")
        # tek kullanımlık: aynı bağlantı + diğer alıcının bağlantısı + GET → 409, kayıt değişmez
        fr.tick(timedelta(minutes=1))
        assert _ack(id_client, toks["b@example.test"]).status_code == 409
        assert _ack(id_client, toks["a@example.test"]).status_code == 409
        assert _ack(id_client, toks["a@example.test"], "get").status_code == 409
    assert _alert(aid)["ack_seconds"] == 180
    assert id_audit_actions("security.alert_ack") == ["security.alert_ack"]


def test_form_post_also_records_ack(id_client, soc):
    with freeze_time(T0):
        aid = _critical()["alert_id"]
        tok = _tokens(soc)["a@example.test"]
        r = id_client.post("/api/security/alert-ack", data={"t": tok})
    assert r.status_code == 200 and _alert(aid)["acked_by"] == "e-posta bağlantısı"


def test_expired_link_is_rejected(id_client, soc):
    with freeze_time(T0) as fr:
        aid = _critical()["alert_id"]
        tok = _tokens(soc)["a@example.test"]
        fr.tick(timedelta(hours=alert_ack.ACK_LINK_TTL_HOURS, seconds=1))
        r = _ack(id_client, tok)
        assert r.status_code == 410 and "süresi dolmuş" in r.text
        assert _ack(id_client, tok, "get").status_code == 410
    assert "acked_at" not in _alert(aid)


def test_tampered_links_are_rejected(id_client, soc):
    with freeze_time(T0):
        aid = _critical()["alert_id"]
        tok = _tokens(soc)["a@example.test"]
        other = _critical("Başka olay")["alert_id"]
        a, r, exp, sig = tok.split(".")
        flipped = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        bad = {
            "imza": f"{a}.{r}.{exp}.{flipped}",
            "süre_uzatma": f"{a}.{r}.{int(exp) + 86400}.{sig}",
            "alıcı_no": f"{a}.1.{exp}.{sig}" if r == "0" else f"{a}.0.{exp}.{sig}",
            "başka_uyarı": f"{other}.{r}.{exp}.{sig}",
            "olmayan_uyarı": f"00000000-0000-0000-0000-000000000000.{r}.{exp}.{sig}",
        }
        for name, t in bad.items():
            res = _ack(id_client, t)
            assert res.status_code == 403, (name, res.status_code)
            assert "Kritik test olayı" not in res.text
        for t in ("", "x", f"{a}.{r}.{exp}", f"{a}.{r}.{exp}.short"):
            assert _ack(id_client, t).status_code == 400
        # imzası doğru ama sunucudaki nonce değişti (ör. geri yükleme) → geçersiz
        deps_db = deps.db
        id_run(deps_db.alerts.update_one({"id": aid}, {"$set": {"ack_nonce": "f" * 32}}))
        assert _ack(id_client, tok).status_code == 403
    assert "acked_at" not in _alert(aid) and "acked_at" not in _alert(other)


def test_info_and_throttled_alerts_do_not_require_ack(id_client, soc):
    with freeze_time(T0):
        info = _critical("Bilgi", level="info")["alert_id"]
        first = _critical("Tekrar", fp="same-fp")
        dup = _critical("Tekrar", fp="same-fp")
    assert dup["throttled"] is True
    assert not _alert(info).get("ack_required") and not _alert(dup["alert_id"]).get("ack_required")
    assert _alert(first["alert_id"])["ack_required"] is True
    info_payload = [p for p in _payloads(soc) if "Bilgi" in p["subject"]]
    assert info_payload and not LINK_RE.search(info_payload[0]["text"])


def test_warning_gets_link_but_no_escalation_notice(id_client, soc):
    with freeze_time(T0):
        aid = _critical("Uyarı düzeyi", level="warning")["alert_id"]
    assert _alert(aid)["ack_required"] is True
    texts = [p["text"] for p in _payloads(soc)]
    assert all(LINK_RE.search(t) for t in texts) and not any("YANIT YOK" in t for t in texts)


# ───────────────────────────── panel onayı ─────────────────────────────

def test_panel_lists_and_acks_super_admin_only_audited(id_client, soc):
    sa = id_seed_user(kind="super", email="owner@example.test")
    id_seed_role("r_sec", ["settings.view", "dashboard.view"])
    staff = id_seed_user(kind="staff", role_id="r_sec")
    cust = id_seed_user(kind="customer")
    with freeze_time(T0) as fr:
        aid = _critical()["alert_id"]
        fr.tick(timedelta(minutes=7, seconds=30))
        for who, codes in ((staff, (403,)), (cust, (401, 403))):
            h = id_auth(id_token(who))
            assert id_client.get("/api/admin/security/alert-acks", headers=h).status_code in codes
            assert id_client.post(f"/api/admin/security/alert-acks/{aid}/ack", headers=h).status_code in codes
        assert id_client.post(f"/api/admin/security/alert-acks/{aid}/ack").status_code == 401
        assert "acked_at" not in _alert(aid)
        h = id_auth(id_token(sa))
        lst = id_client.get("/api/admin/security/alert-acks", headers=h).json()
        assert [x["id"] for x in lst["pending"]] == [aid] and lst["pending_critical"] == 1
        assert lst["escalation_minutes"] == 15
        assert "ack_nonce" not in json.dumps(lst) and "a@example.test" not in json.dumps(lst)
        r = id_client.post(f"/api/admin/security/alert-acks/{aid}/ack", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["ack_seconds"] == 450 and r.json()["acked_by"] == "owner@example.test"
        assert id_client.post(f"/api/admin/security/alert-acks/{aid}/ack", headers=h).status_code == 409
        assert id_client.post("/api/admin/security/alert-acks/yok/ack", headers=h).status_code == 404
        lst = id_client.get("/api/admin/security/alert-acks", headers=h).json()
        assert lst["pending"] == [] and lst["recent_acked"][0]["acked_by"] == "owner@example.test"
    doc = _alert(aid)
    assert doc["ack_via"] == "panel" and doc["ack_seconds"] == 450
    phases = [r.get("phase") for r in id_findall("audit_logs") if r.get("action") == "security.alert_ack"]
    assert phases[:2] == ["intent", "done"]
    # bağlantı artık kullanılamaz
    with freeze_time(T0):
        assert _ack(id_client, _tokens(soc)["a@example.test"]).status_code == 409


# ───────────────────────────── yükseltme ─────────────────────────────

def _escalate():
    return id_run(alert_ack.escalate_due_alerts())


def test_escalation_exactly_once_after_rule_minutes(id_client, soc):
    with freeze_time(T0) as fr:
        crit = _critical("Kritik A")["alert_id"]
        warn = _critical("Uyarı B", level="warning")["alert_id"]
        acked = _critical("Kritik C")["alert_id"]
        id_run(alert_ack.record_ack(_alert(acked), by="x", via="panel"))
        n0 = len(soc.calls)
        fr.tick(timedelta(minutes=14, seconds=59))
        assert _escalate()["escalated"] == [] and len(soc.calls) == n0
        fr.tick(timedelta(seconds=1))  # tam 15. dakika
        res = _escalate()
        assert res["escalated"] == [crit] and res["minutes"] == 15
        new = _payloads(soc)[n0:]
        assert sorted(p["to"][0] for p in new) == ["a@example.test", "b@example.test"]
        assert all(p["subject"].startswith("[YANIT YOK] ") and "Kritik A" in p["subject"] for p in new)
        assert all("15 dakikadır onaylanmadı" in p["text"] and LINK_RE.search(p["text"]) for p in new)
        n1 = len(soc.calls)
        for _ in range(4):
            fr.tick(timedelta(minutes=5))
            assert _escalate()["escalated"] == []
        assert len(soc.calls) == n1  # TAM BİR KEZ
        # yükseltme e-postasındaki bağlantı onaylar
        tok = _tokens(soc, n0)["a@example.test"]
        assert _ack(id_client, tok).status_code == 200
    doc = _alert(crit)
    assert doc["escalated_at"].startswith("2026-10-05T07:15:00")
    assert doc["escalation"]["email_ok"] == 2 and doc["escalation"]["email_total"] == 2
    assert doc["ack_seconds"] == 35 * 60
    assert "escalated_at" not in _alert(warn) and "escalated_at" not in _alert(acked)
    assert "security.alert_escalated" in id_audit_actions("security.alert_escalated")


def test_escalation_minutes_come_from_business_rule_and_old_alerts_are_ignored(id_client, soc):
    id_set_rule("security.ack_escalation_minutes", 30)
    with freeze_time("2026-10-03 07:00:00") as fr:
        old = _critical("Eski")["alert_id"]
        fr.move_to(T0)
        new = _critical("Yeni")["alert_id"]
        fr.tick(timedelta(minutes=29))
        assert _escalate()["escalated"] == []
        fr.tick(timedelta(minutes=1))
        assert _escalate()["escalated"] == [new]
    assert "escalated_at" not in _alert(old)  # 24 saatten eski (ör. ilk deploy öncesi) → yükseltilmez
    id_set_rule("security.ack_escalation_minutes", 1)  # alt sınır 5 dk
    assert id_run(alert_ack.escalation_minutes()) == 5


def test_escalation_job_entry_never_raises(monkeypatch):
    async def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(alert_ack, "escalate_due_alerts", _boom)
    assert id_run(alert_ack.run_alert_escalation_check()) == {"ok": False, "error": "RuntimeError"}


@pytest.fixture
def sms_sink(monkeypatch):
    sent = []

    async def _fake_provider(cfg, to, message, *a, **k):
        sent.append({"to": to, "message": message})
        return {"success": True, "response": "00 test"}
    monkeypatch.setitem(ns.SMS_IMPL, "kvfake", ns._sms_guard(_fake_provider))
    id_run(deps.db.settings.insert_one({"id": "notification_providers", "sms_active": "kvfake",
                                        "whatsapp_active": False, "email_active": False, "providers": {}}))
    ns._SMS_BLOCK.update({"set": set(), "at": 0.0})
    # Bu testler 15 dk YANIT YOK yükseltme SMS'ini ölçer; anlık kritik SMS + dakikalık çağrı
    # test_alert_instant_sms.py'de ayrıca test edilir.
    id_set_rule("security.alert_sms_instant", False)
    return sent


def test_escalation_sms_sent_once_ascii_to_rule_phone(id_client, soc, sms_sink):
    id_set_rule("security.alert_sms_phones", "0555 111 22 33; 05551112233")
    with freeze_time(T0) as fr:
        aid = _critical("Kritik SMS olayı")["alert_id"]
        fr.tick(timedelta(minutes=15))
        _escalate()
        fr.tick(timedelta(minutes=10))
        _escalate()
    assert len(sms_sink) == 1  # tekilleştirilmiş numara, tek yükseltme
    msg = sms_sink[0]["message"]
    assert "YANIT YOK" in msg and "15 dk" in msg and "Kritik SMS olay" in msg
    assert msg.isascii() and len(msg) <= 300
    esc = _alert(aid)["escalation"]
    assert esc["sms_sent"] == 1 and esc["sms"]["attempted"] == 1
    logs = id_findall("notification_logs", {"event": "security_alert_escalation"})
    assert logs and logs[0]["to"] == "***2233"  # maskeli


def test_escalation_sms_falls_back_to_super_admin_phone(id_client, soc, sms_sink):
    id_seed_user(kind="super", email="sa@example.test", phone="05329998877")
    id_seed_user(kind="super", email="off@example.test", phone="05320000000", is_active=False)
    with freeze_time(T0) as fr:
        _critical()
        fr.tick(timedelta(minutes=15))
        _escalate()
    assert [s["to"][-10:] for s in sms_sink] == ["5329998877"]


def test_escalation_sms_respects_global_cap_and_never_uses_mfa_reserve(id_client, soc, sms_sink):
    id_set_rule("security.alert_sms_phones", "05551112233")
    id_set_rule("sms.global_hourly_cap", 2)
    id_set_rule("sms.global_daily_cap", 100)
    with freeze_time(T0) as fr:
        for _ in range(2):  # saatlik tavanı rutin SMS'lerle doldur
            assert id_run(ns.sms_budget_reserve())["ok"] is True
        aid = _critical()["alert_id"]
        fr.tick(timedelta(minutes=15))
        _escalate()
        esc = _alert(aid)["escalation"]
        assert sms_sink == [] and esc["sms_sent"] == 0
        assert esc["sms"]["errors"] == ["sms_budget_exceeded"]
        assert esc["email_ok"] == 2  # e-posta yükseltmesi yine gider
        # yönetici MFA yedek payı (10) dokunulmadan duruyor
        async def _mfa():
            with ns.sms_priority("admin_mfa"):
                return await ns.sms_budget_reserve()
        assert all(id_run(_mfa())["ok"] for _ in range(10))
        assert id_run(_mfa())["ok"] is False


def test_escalation_sms_daily_limit_and_no_provider(id_client, soc, sms_sink):
    id_set_rule("security.alert_sms_phones", "05551112233")
    with freeze_time(T0) as fr:
        day = datetime.now(timezone.utc).replace(hour=1).isoformat()
        id_run(deps.db.alerts.insert_many([{"id": f"old-{i}", "escalated_at": day, "escalation": {"sms_sent": 1}}
                                           for i in range(alert_ack.ESCALATION_SMS_DAILY_MAX)]))
        a = _critical("Tavan")["alert_id"]
        fr.tick(timedelta(minutes=15))
        _escalate()
        assert _alert(a)["escalation"]["sms"]["reason"] == "escalation_sms_daily_cap" and sms_sink == []
        id_run(deps.db.settings.delete_many({"id": "notification_providers"}))
        b = _critical("Sağlayıcısız")["alert_id"]
        fr.tick(timedelta(minutes=15))
        _escalate()
    assert _alert(b)["escalation"]["sms"]["reason"] == "sms_not_configured"
    assert _alert(b)["escalation"]["email_ok"] == 2


# ───────────────────────────── tatbikat ─────────────────────────────

def _start(client, user):
    return client.post("/api/admin/security/drills/start", headers=id_auth(id_token(user)))


def test_drill_end_to_end_ack_within_15_minutes_passes(id_client, soc):
    sa = id_seed_user(kind="super", email="owner@example.test")
    with freeze_time(T0) as fr:
        r = _start(id_client, sa)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "bekliyor" and d["trigger"] == "elle" and d["started_by"] == "owner@example.test"
        assert d["delivered_ok"] == 2 and d["delivered_total"] == 2
        sent = _payloads(soc)
        assert all("[TATBİKAT]" in p["subject"] and "CRITICAL" in p["subject"] for p in sent)
        assert all("BU BİR TATBİKATTIR" in p["text"] for p in sent)
        alert = _alert(d["alert_id"])
        assert alert["kind"] == "security_drill" and alert["level"] == "critical"
        # süren tatbikat varken ikincisi başlatılamaz
        assert _start(id_client, sa).status_code == 409
        fr.tick(timedelta(minutes=4, seconds=12))
        assert _ack(id_client, _tokens(soc)["b@example.test"]).status_code == 200
        lst = id_client.get("/api/admin/security/drills", headers=id_auth(id_token(sa))).json()
    drill = id_find("security_drills", {"id": d["id"]})
    assert drill["status"] == "geçti" and drill["ack_seconds"] == 252 and drill["acked_by"] == "e-posta bağlantısı"
    assert lst["last"]["status"] == "geçti" and lst["recent_success"] is True and lst["pass_minutes"] == 15
    assert lst["monthly_enabled"] is True and lst["next_monthly_at"].startswith("2026-10-05T07:07")  # 10:07 İstanbul, aynı gün
    assert {"security.drill_start", "security.drill_started", "security.alert_ack"} <= set(id_audit_actions("security."))
    # yükseltme işi onaylanmış tatbikatı yükseltmez
    with freeze_time(T0) as fr:
        fr.tick(timedelta(minutes=30))
        assert _escalate()["escalated"] == []
    from security.selftest import _c_alert_drill
    with freeze_time(T0):
        st, detail = id_run(_c_alert_drill(deps.db))
    assert st == "ok" and "4 dk 12 sn" in detail


def test_drill_without_ack_fails_and_escalates_late_ack_is_measured(id_client, soc):
    sa = id_seed_user(kind="super", email="owner@example.test")
    with freeze_time(T0) as fr:
        d = _start(id_client, sa).json()
        n0 = len(soc.calls)
        fr.tick(timedelta(minutes=14))
        _escalate()
        assert id_find("security_drills", {"id": d["id"]})["status"] == "bekliyor"
        fr.tick(timedelta(minutes=1, seconds=30))
        res = _escalate()
        assert res["escalated"] == [d["alert_id"]] and res["drills_finalized"] == 1
        drill = id_find("security_drills", {"id": d["id"]})
        assert drill["status"] == "kaldı" and "15 dakika içinde onaylanmadı" in drill["result_reason"]
        esc = _payloads(soc)[n0:]
        assert len(esc) == 2 and all(p["subject"].startswith("[YANIT YOK] ") and "[TATBİKAT]" in p["subject"]
                                     for p in esc)
        fr.tick(timedelta(minutes=4, seconds=30))  # 20. dakikada geç onay
        assert _ack(id_client, _tokens(soc, n0)["a@example.test"]).status_code == 200
        # yeni tatbikat artık başlatılabilir
        assert _start(id_client, sa).status_code == 200
    drill = id_find("security_drills", {"id": d["id"]})
    assert drill["status"] == "kaldı" and drill["late_ack"] is True and drill["ack_seconds"] == 1200
    assert "security.drill_failed" in id_audit_actions("security.drill_failed")
    from security.selftest import _c_alert_drill
    with freeze_time(T0):
        st, detail = id_run(_c_alert_drill(deps.db))
    assert st == "warn" and "başarılı alarm tatbikatı yok" in detail


def test_drill_undeliverable_is_failed_immediately(id_client, soc, monkeypatch):
    import email_smtp
    soc.mock(return_value=httpx.Response(403, json={"message": "domain not verified"}))

    async def _fail(*a, **k):  # yedek kanal (mağaza maili) de başarısız
        return {"success": False, "response": "down"}
    monkeypatch.setattr(email_smtp, "send_smtp_email", _fail)
    with freeze_time(T0):
        d = id_run(alert_ack.start_drill(trigger="elle"))
    assert d["status"] == "kaldı" and d["delivered_ok"] == 0 and "teslim edilemedi" in d["result_reason"]


def test_drill_endpoints_super_admin_only(id_client, soc):
    id_seed_role("r_all", ["settings.view", "settings.edit", "dashboard.view"])
    staff = id_seed_user(kind="staff", role_id="r_all")
    cust = id_seed_user(kind="customer")
    for who, codes in ((staff, (403,)), (cust, (401, 403))):
        h = id_auth(id_token(who))
        assert id_client.post("/api/admin/security/drills/start", headers=h).status_code in codes
        assert id_client.get("/api/admin/security/drills", headers=h).status_code in codes
    assert id_client.post("/api/admin/security/drills/start").status_code == 401
    assert id_findall("security_drills") == [] and soc.calls.call_count == 0


# ───────────────────────────── aylık tatbikat işi ─────────────────────────────

class _FakeScheduler:
    def __init__(self, *a, **k):
        self.jobs = {}

    def add_job(self, func, *a, **k):
        self.jobs[k.get("id") or getattr(func, "__name__", "?")] = (func, a, k)

    def start(self):
        pass

    def get_jobs(self):
        return list(self.jobs.values())


def test_monthly_drill_and_escalation_jobs_registered(monkeypatch):
    import scheduler
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", _FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_lead", lambda fn: fn)
    sch = scheduler.start_scheduler()
    fn, a, k = sch.jobs["security_alert_escalation"]
    assert a[0] == "interval" and k["minutes"] == 5 and k["max_instances"] == 1
    assert fn.__name__ == "run_alert_escalation_check"
    fn, a, k = sch.jobs["security_monthly_drill"]
    assert a[0] == "cron" and fn.__name__ == "run_monthly_security_drill"
    assert (k["day"], k["hour"], k["minute"], k["timezone"]) == ("1st mon", 10, 7, "Europe/Istanbul")
    # mevcut işlerin sıklığı değişmedi
    assert sch.jobs["security_pipeline_check"][2]["minutes"] == 5
    from apscheduler.triggers.cron import CronTrigger
    trig = CronTrigger(day=k["day"], hour=k["hour"], minute=k["minute"], timezone=k["timezone"])
    start = datetime(2026, 10, 6, tzinfo=timezone.utc)
    fires = []
    for _ in range(3):
        start = trig.get_next_fire_time(None, start)
        fires.append(start.isoformat())
        start = start + timedelta(minutes=1)
    assert fires == ["2026-11-02T10:07:00+03:00", "2026-12-07T10:07:00+03:00", "2027-01-04T10:07:00+03:00"]


def test_monthly_drill_once_per_month_and_disable_rule(id_client, soc):
    id_set_rule("security.monthly_drill_enabled", False)
    with freeze_time(T0):
        assert id_run(alert_ack.run_monthly_security_drill()) == {"ok": True, "skipped": "disabled"}
    assert id_findall("security_drills") == [] and soc.calls.call_count == 0
    id_set_rule("security.monthly_drill_enabled", True)
    with freeze_time(T0) as fr:
        r = id_run(alert_ack.run_monthly_security_drill())
        assert r["ok"] and r["status"] == "bekliyor"
        fr.tick(timedelta(hours=1))  # aynı ay (çok worker / yeniden çalıştırma) → tekrar yok
        assert id_run(alert_ack.run_monthly_security_drill())["skipped"] == "already_this_month"
    drills = id_findall("security_drills")
    assert len(drills) == 1 and drills[0]["trigger"] == "aylık" and drills[0]["started_by"] == "sistem"
    with freeze_time("2026-11-02 07:07:00"):
        assert id_run(alert_ack.run_monthly_security_drill())["ok"] is True
    assert len(id_findall("security_drills")) == 2


def test_monthly_drill_entry_never_raises(monkeypatch):
    async def _boom(**k):
        raise RuntimeError("x")
    monkeypatch.setattr(alert_ack, "start_drill", _boom)
    with freeze_time(T0):
        assert id_run(alert_ack.run_monthly_security_drill()) == {"ok": False, "error": "RuntimeError"}


# ───────────────────────────── öz-test + kurallar ─────────────────────────────

def test_selftest_drill_check_35_day_window(id_client):
    from security.selftest import _c_alert_drill

    def _put(days_ago, status):
        at = (datetime(2026, 10, 5, 7, tzinfo=timezone.utc) - timedelta(days=days_ago)).isoformat()
        id_run(deps.db.security_drills.insert_one({"id": f"d{days_ago}{status}", "status": status,
                                                   "started_at": at, "ack_seconds": 125}))
    with freeze_time(T0):
        st, detail = id_run(_c_alert_drill(deps.db))
        assert st == "warn" and "hiç tatbikat yapılmadı" in detail
        _put(36, "geçti")
        _put(2, "kaldı")
        st, detail = id_run(_c_alert_drill(deps.db))
        assert st == "warn" and "kaldı" in detail
        _put(34, "geçti")
        st, detail = id_run(_c_alert_drill(deps.db))
        assert st == "ok" and "2 dk 5 sn" in detail


def test_selftest_report_contains_drill_check(id_client, soc, monkeypatch):
    from security import selftest
    id_seed_user(kind="super")
    with freeze_time(T0):
        rep = id_run(selftest.run_selftest(send_email=False))
    chk = {c["name"]: c for c in rep["checks"]}["Alarm tatbikatı"]
    assert chk["status"] == "warn"


def test_new_rules_in_catalogue_and_only_super_admin_may_change(id_client):
    import business_rules as BR
    cat = {r["key"]: r for r in BR.RULE_CATALOG}
    assert cat["security.ack_escalation_minutes"]["default"] == 15
    assert cat["security.monthly_drill_enabled"]["default"] is True
    assert cat["security.alert_sms_phones"]["default"] == ""
    id_seed_role("r_rules", ["settings.view", "settings.edit"])
    staff = id_seed_user(kind="staff", role_id="r_rules")
    sa = id_seed_user(kind="super")
    for key, val in (("security.ack_escalation_minutes", 60), ("security.monthly_drill_enabled", False),
                     ("security.alert_sms_phones", "05550000000")):
        r = id_client.put("/api/admin/business-rules", json={key: val}, headers=id_auth(id_token(staff)))
        assert r.status_code == 403, (key, r.text)
    r = id_client.put("/api/admin/business-rules", json={"security.ack_escalation_minutes": 30},
                      headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    BR.invalidate()
    assert id_run(alert_ack.escalation_minutes()) == 30
