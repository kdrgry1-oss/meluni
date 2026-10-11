"""KVKK (sahip talebi) — telefon / adres:
* saklama süresi (privacy.order_pii_retention_days, vars. 730, taban 365) dolan KAPANMIŞ siparişlerde
  minimizasyon: ilk çalışma KURU + süper admin raporu, sonra etkin; açık iade/itiraz, KVKK koparılmış
  kayıt ve yeni sipariş ATLANIR; idempotent; kural yalnız süper admin;
* sızıntı yolları: uygulama logu (gerçek handler'lar), push, güvenlik uyarı e-postası, entegrasyon logu,
  LLM (chatbot/WhatsApp + rapor asistanı), reklam platformları (açık adres / posta kodu gitmez);
* üye listesi / arama / dışa aktarım, İYS izin listesi.
Tüm veriler sahtedir.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from pay_harness import pay_drain, pay_find, pay_findall, pay_staff, now_iso
from routes import deps

pytestmark = pytest.mark.asyncio

PHONE = "05550000001"
PHONE10 = "5550000001"
ADDR = "Deneme Mah. Ornek Sok. No:5 D:2"
BACKEND = Path(__file__).resolve().parents[2]


def _blob(o):
    return json.dumps(o, default=str, ensure_ascii=False)


def _order(oid, *, days_ago=800, status="delivered", platform="meluni", **extra):
    ship = {"first_name": "Ayse", "last_name": "Yilmaz", "address": ADDR, "city": "Istanbul",
            "district": "Kadikoy", "postal_code": "34000", "phone": PHONE, "email": "a@example.test"}
    o = {"id": oid, "order_number": "W" + oid, "status": status, "payment_status": "paid", "platform": platform,
         "total": 100.0, "created_at": now_iso(days=-days_ago - 5), "delivered_at": now_iso(days=-days_ago),
         "phone": PHONE, "customer_phone": "+90" + PHONE10, "shipping_address": ship,
         "billing_address": dict(ship, tax_number_enc="v1:x", tax_number_last2="50"),
         "invoice_number": "INV1", "items": [{"name": "Elbise", "quantity": 1, "price": 100.0}]}
    o.update(extra)
    return o


async def _seed_orders():
    await deps.db.orders.insert_many([
        _order("old1"),
        _order("oldty", platform="trendyol", shipmentAddress={"address1": ADDR, "phone": PHONE, "city": "Istanbul"}),
        _order("oldcan", status="cancelled", delivered_at=None, cancelled_at=now_iso(days=-900)),
        _order("recent", days_ago=10),
        _order("openret", return_request={"status": "requested"}),
        _order("kvkk", kvkk_detached=True),
        _order("cb"),
    ])
    await deps.db.orders_deleted.insert_one(_order("del1"))
    await deps.db.chargebacks.insert_one({"id": "c1", "order_id": "cb", "status": "open"})
    await deps.db.customer_returns.insert_one({"id": "r1", "order_id": "old1", "customer_phone": PHONE,
                                               "shipping_address": {"address": ADDR, "city": "Istanbul"}})


# ═════════════════════════════════ saklama süresi işi ═════════════════════════════════
async def test_retention_first_run_is_dry_then_minimizes_closed_old_orders(pay_env, monkeypatch):
    import scheduler
    import security.events as ev
    sent = []

    async def _notify(kind, title, body="", **k):
        sent.append({"kind": kind, "title": title, "body": body})
        return 1
    monkeypatch.setattr(ev, "notify_super_admins", _notify)
    await _seed_orders()
    before = await deps.db.orders.find({}, {"_id": 0}).sort("id", 1).to_list(20)

    rep = await scheduler.minimize_old_order_contact_pii()
    assert rep["dry_run"] is True and rep["days"] == 730
    assert rep["candidates"] == {"orders": 3, "orders_deleted": 1} and rep["skipped_open"] == 1
    assert await deps.db.orders.find({}, {"_id": 0}).sort("id", 1).to_list(20) == before   # KURU: değişiklik yok
    assert sent and sent[0]["kind"] == "pii_retention_dry_run" and "KURU" in sent[0]["body"]
    assert PHONE10 not in _blob(sent) and ADDR not in _blob(sent)

    rep = await scheduler.minimize_old_order_contact_pii()
    assert rep["dry_run"] is False and rep["minimized"] == {"orders": 3, "orders_deleted": 1}
    o = await pay_find("orders", {"id": "old1"})
    sa = o["shipping_address"]
    assert sa["address"] == "" and sa["postal_code"] == "" and sa["phone"] == "+90 *** *** ** 01"
    assert sa["city"] == "Istanbul" and sa["district"] == "Kadikoy" and sa["first_name"] == "Ayse"
    assert o["phone"] == "+90 *** *** ** 01" and o["customer_phone"] == "+90 *** *** ** 01"
    assert o["billing_address"]["tax_number_enc"] == "v1:x" and o["invoice_number"] == "INV1"
    assert o["contact_minimized"] is True and o["total"] == 100.0 and o["items"][0]["name"] == "Elbise"
    ty = await pay_find("orders", {"id": "oldty"})
    assert ty["shipmentAddress"]["address1"] == "" and ty["shipmentAddress"]["city"] == "Istanbul"
    for oid in ("old1", "oldty", "oldcan"):
        b = _blob(await pay_find("orders", {"id": oid}))
        assert PHONE10 not in b and ADDR not in b, oid
    for oid in ("recent", "openret", "kvkk", "cb"):               # dokunulmayanlar
        assert (await pay_find("orders", {"id": oid}))["shipping_address"]["address"] == ADDR, oid
    assert ADDR not in _blob(await pay_find("orders_deleted", {"id": "del1"}))
    r = await pay_find("customer_returns", {"id": "r1"})
    assert PHONE10 not in _blob(r) and ADDR not in _blob(r)
    again = await scheduler.minimize_old_order_contact_pii()      # idempotent
    assert again["minimized"] == {"orders": 0, "orders_deleted": 0}
    audit = await pay_findall("audit_logs", {"action": {"$in": ["order_pii_minimize", "order_pii_minimize_dry_run"]}})
    assert len(audit) == 3 and PHONE10 not in _blob(audit)


async def test_retention_floor_and_disable_rule(pay_env, monkeypatch):
    import scheduler
    import security.events as ev

    async def _n(*a, **k):
        return 1
    monkeypatch.setattr(ev, "notify_super_admins", _n)
    import business_rules
    await deps.db.orders.insert_many([_order("d400", days_ago=400), _order("d200", days_ago=200)])
    await business_rules.save_rules(deps.db, {"privacy.order_pii_retention_days": 30})   # taban 365 uygulanır
    await deps.db.settings.insert_one({"id": scheduler.ORDER_PII_RETENTION_FLAG, "dry_run_done": True})
    rep = await scheduler.minimize_old_order_contact_pii()
    assert rep["days"] == 365 and rep["minimized"]["orders"] == 1
    assert (await pay_find("orders", {"id": "d200"}))["shipping_address"]["address"] == ADDR
    await business_rules.save_rules(deps.db, {"privacy.order_pii_minimize_enabled": False})
    assert (await scheduler.minimize_old_order_contact_pii()).get("skipped") is True


async def test_privacy_rules_only_super_admin(pay_http, pay_env):
    _, hdr = await pay_staff(["settings.edit", "settings.view"])
    r = await pay_http.put("/api/admin/business-rules", json={"privacy.order_pii_retention_days": 3650}, headers=hdr)
    assert r.status_code == 403
    _, shdr = await pay_staff(super_admin=True)
    r = await pay_http.put("/api/admin/business-rules", json={"privacy.order_pii_retention_days": 1095}, headers=shdr)
    assert r.status_code == 200, r.text
    import business_rules
    assert await business_rules.get_rule(deps.db, "privacy.order_pii_retention_days") == 1095


# ═════════════════════════════════ sızıntı yolları ═════════════════════════════════
LOG_SCRIPT = textwrap.dedent(f"""
    import os, sys, logging, logging.config
    os.environ.update(MONGO_URL="mongodb://kv-log.invalid:1", DB_NAME="kvlog2", JWT_SECRET="k" * 64)
    import motor.motor_asyncio, mongomock_motor
    motor.motor_asyncio.AsyncIOMotorClient = mongomock_motor.AsyncMongoMockClient
    sys.path.insert(0, {str(BACKEND)!r})
    import uvicorn.config
    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
    import server  # noqa: F401
    print("---BEGIN---", file=sys.stderr, flush=True)
    log = logging.getLogger("routes.kvtest")
    log.warning("order payload %s", {{"phone": "{PHONE}", "address": "{ADDR}", "city": "Istanbul"}})
    log.warning("sms gonderildi tel=+90 555 000 00 01 ok")
    log.warning("musteri notu: {ADDR} kapida birak")
    log.warning("siparis W11805 barkod 8681234567890 tutar 1.250,00 ip 203.0.113.5")
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET",
        "/api/x?phone=905550000001&page=2", "1.1", 200)
    print("---END---", file=sys.stderr, flush=True)
""")


async def test_application_logs_redact_phone_and_address():
    pytest.importorskip("uvicorn")
    p = subprocess.run([sys.executable, "-c", LOG_SCRIPT], capture_output=True, text=True, timeout=180,
                       cwd=str(BACKEND))
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-3000:]
    out = out.split("---BEGIN---", 1)[1].split("---END---", 1)[0] + p.stdout   # uvicorn.access → stdout
    assert PHONE10 not in out and "555 000 00 01" not in out and "Ornek Sok" not in out, out
    assert "[TEL]" in out and "[ADRES]" in out
    assert "W11805" in out and "8681234567890" in out and "203.0.113.5" in out and "page=2" in out


async def test_push_alert_and_integration_log_scrub_contact(pay_env, monkeypatch):
    from routes.push import sanitize_admin_push
    t, b, d = sanitize_admin_push("Yeni iade", f"Adres: {ADDR}; tel {PHONE}",
                                  {"type": "return", "adres": ADDR, "gsm": PHONE, "order_number": "W1"})
    assert PHONE10 not in _blob([t, b, d]) and "Ornek Sok" not in _blob([t, b, d]) and d["order_number"] == "W1"
    # güvenlik uyarısı: başlık/gövde/meta tek noktada süzülür (kayıt + e-posta)
    import security.alerts as al
    delivered = []

    async def _deliver(subject, text, target):
        delivered.append(subject + "\n" + text)
        return {"to": target, "smtp": True, "resend": False, "ok": True, "error": ""}
    monkeypatch.setattr(al, "_deliver", _deliver)
    await al.send_alert("test_kind", f"Uyarı {PHONE}", f"Müşteri adresi {ADDR}", level="warning",
                        fingerprint="contact-scrub-test", meta={"phone": PHONE, "note": f"tel {PHONE}"})
    stored = await deps.db.alerts.find({}, {"_id": 0}).to_list(10)
    assert delivered and stored
    assert PHONE10 not in _blob([delivered, stored]) and "Ornek Sok" not in _blob([delivered, stored])
    # entegrasyon logu: ham pazaryeri yükü (shipmentAddress) + hata metni
    from routes.integrations_common import log_integration_event
    await log_integration_event("trendyol", "import_order", "order", "1", "error", f"hata: alıcı tel {PHONE}",
                                {"raw": {"shipmentAddress": {"fullAddress": ADDR, "phone": PHONE, "city": "Ankara"},
                                         "identityNumber": "12345678950"}})
    lg = await pay_find("integration_logs", {"platform": "trendyol"})
    assert PHONE10 not in _blob(lg) and ADDR not in _blob(lg) and "12345678950" not in _blob(lg)
    assert lg["details"]["raw"]["shipmentAddress"]["city"] == "Ankara"


async def test_llm_calls_never_receive_customer_phone_email_or_address(monkeypatch):
    captured = {}

    class _Completions:
        async def create(self, **kw):
            captured.update(kw)
            msg = types.SimpleNamespace(content="tamam")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    class _Client:
        def __init__(self, api_key=None):
            self.chat = types.SimpleNamespace(completions=_Completions())
    fake = types.ModuleType("openai")
    fake.AsyncOpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", fake)
    from routes.ai_chatbot import llm_chat
    out = await llm_chat("k", "openai", "m", f"Sipariş bağlamı: tel {PHONE}",
                         f"Merhaba, adresim {ADDR}, numaram +90 555 000 00 01, e-posta a@example.test")
    assert out == "tamam"
    sent = _blob(captured["messages"])
    assert PHONE10 not in sent and "555 000 00 01" not in sent and "Ornek Sok" not in sent
    assert "a@example.test" not in sent and "[telefon]" in sent and "[adres]" in sent
    # rapor asistanı: araç sonuçları + personelin sorusu
    from routes.report_assistant import _temizle, _gecmisi_temizle
    res = _temizle({"rows": [{"musteri_phone": PHONE, "shipping_address": ADDR, "city": "Istanbul",
                              "note": f"aradı {PHONE}"}]})
    assert PHONE10 not in _blob(res) and ADDR not in _blob(res) and res["rows"][0]["city"] == "Istanbul"
    g = _gecmisi_temizle([{"role": "user", "text": f"{PHONE} numaralı müşterinin siparişleri?"}])
    assert PHONE10 not in _blob(g)


async def test_ad_platforms_get_no_street_or_zip(pay_env, monkeypatch):
    from routes import orders as orders_mod
    import services.capi.orchestrator as orch
    seen = []

    async def _dispatch(db, **kw):
        seen.append(kw)
        return {}
    monkeypatch.setattr(orch, "dispatch_event", _dispatch)
    await deps.db.orders.insert_one(_order("capi1", days_ago=0, payment_status="paid", status="confirmed"))
    assert await orders_mod.dispatch_purchase_capi("capi1", source="test")
    ud = seen[0]["user_data"]
    from services.capi.hash_utils import hash_street, hash_zip, hash_phone
    vals = _blob(ud)
    assert hash_street(ADDR) not in vals and hash_zip("34000") not in vals
    assert not ud.get("street") and not ud.get("zp")
    assert ud.get("ph") == hash_phone(PHONE)                        # telefon yalnız SHA-256


# ═════════════════════════════════ üye listesi / arama / dışa aktarım ═════════════════════════════════
async def test_members_list_search_detail_and_export(pay_http, pay_env):
    from security import contact_pii as cp
    await deps.db.users.insert_one(cp.seal_doc({"id": "m1", "email": "m1@example.test", "first_name": "Mel",
                                                "last_name": "T", "phone": PHONE, "is_admin": False,
                                                "created_at": now_iso()}, "user"))
    await deps.db.addresses.insert_one(cp.seal_doc({"id": "ma", "user_id": "m1", "phone": PHONE,
                                                    "address": ADDR, "city": "Istanbul"}, "address"))
    _, hdr = await pay_staff(["customers.view"])
    r = await pay_http.get("/api/admin/members", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["phone"] == "+90 *** *** ** 01" and PHONE10 not in r.text
    assert len((await pay_http.get("/api/admin/members?search=0555 000 00 01", headers=hdr)).json()["items"]) == 1
    assert len((await pay_http.get("/api/admin/members?search=00000", headers=hdr)).json()["items"]) == 0
    d = await pay_http.get("/api/admin/members/m1", headers=hdr)
    assert d.status_code == 200 and PHONE10 not in d.text and ADDR not in d.text
    assert d.json()["member"]["contact_masked"] is True
    c = (await pay_http.get("/api/admin/members/m1/contact", headers=hdr)).json()
    assert c["phone"] == PHONE and c["addresses"][0]["address"] == ADDR
    assert await pay_findall("auth_audit_logs", {"event": "pii_contact_view"})
    # toplu dışa aktarım: customers.export ile TAM telefon (şifreli değer çözülür) + satır sayılı denetim
    _, ehdr = await pay_staff(["customers.export"])
    r = await pay_http.get("/api/admin/members/export.xlsx", headers=ehdr)
    assert r.status_code == 200
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert any(PHONE in str(c.value) for row in ws.iter_rows() for c in row)
    logs = await pay_findall("auth_audit_logs", {"event": "members_export"})
    assert logs and logs[-1]["meta"]["rows"] == 1
    # yetkisiz personel: dışa aktarım ve "Göster" yok
    assert (await pay_http.get("/api/admin/members/export.xlsx", headers=hdr)).status_code == 403
    _, nhdr = await pay_staff(["products.view"])
    assert (await pay_http.get("/api/admin/members/m1/contact", headers=nhdr)).status_code == 403


async def test_member_phone_edit_from_masked_form_is_noop_and_new_phone_sealed(pay_http, pay_env):
    from security import contact_pii as cp
    await deps.db.users.insert_one(cp.seal_doc({"id": "m2", "email": "m2@example.test", "phone": PHONE,
                                                "is_admin": False}, "user"))
    _, hdr = await pay_staff(super_admin=True)
    r = await pay_http.put("/api/admin/members/m2", json={"phone": "+90 *** *** ** 01", "notes": "n"}, headers=hdr)
    assert r.status_code == 200, r.text
    u = await deps.db.users.find_one({"id": "m2"})
    assert cp.open_field(u, "phone") == PHONE and u["notes"] == "n"
    r = await pay_http.put("/api/admin/members/m2", json={"phone": "05550000007"}, headers=hdr)
    assert r.status_code == 200, r.text
    u = await deps.db.users.find_one({"id": "m2"})
    assert "phone" not in u and cp.open_field(u, "phone") == "05550000007" and u["phone_verified"] is True


async def test_consent_list_masks_sms_recipient(pay_http, pay_env):
    await deps.db.iys_consents.insert_one({"id": "c1", "phone": "+90" + PHONE10, "channels": ["MESAJ"],
                                           "status": "ONAY", "created_at": now_iso()})
    _, hdr = await pay_staff(["customers.view"])
    r = await pay_http.get("/api/admin/consents", headers=hdr)
    assert r.status_code == 200, r.text
    assert PHONE10 not in r.text
    assert any(i["recipient"].endswith("** 01") for i in r.json()["items"] if i["channel"] == "sms")
    await pay_drain()
