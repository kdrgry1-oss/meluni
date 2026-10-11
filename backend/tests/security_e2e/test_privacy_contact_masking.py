"""KVKK (sahip talebi) — müşteri telefon / adres: personel LİSTELERİNDE maske, tek kayıt "Göster"
(izin + PRIV07 toplu çözme sayacı + denetim kaydı), düzenleme formunda maskenin gerçek değeri EZMEMESİ,
herkese açık sipariş-no ucu, personel e-postası ve TÜM GET uçlarının taraması.

Tüm veriler sahtedir (example.test, 0555… numaraları). Dış servis çağrısı yoktur (pay_harness).
"""
from __future__ import annotations

import json
import uuid

import pytest

from pay_harness import (SHIP, pay_card_order, pay_customer, pay_find, pay_findall, pay_seed_product,
                         pay_staff, now_iso)
from routes import deps

pytestmark = pytest.mark.asyncio

PHONE10 = "5551234567"                       # SHIP["phone"] = 05551234567
ADDR = SHIP["address"]
MASKED_PHONE = "+90 *** *** ** 67"


def _blob(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def _no_contact(obj, phone10=PHONE10, addr=ADDR):
    b = _blob(obj)
    assert phone10 not in b, "tam telefon yanıtta"
    assert addr not in b, "açık adres yanıtta"


def _shim_convert_to_date(monkeypatch):
    """mongomock `$convert` desteklemez ve tarih + sayı `$add`'ini reddeder (GET /api/orders sıralama
    alanı). Sıralama bu testlerin konusu değil → tarihe çevirme `onNull` döner (alan None, $add atlanır)."""
    import mongomock.aggregate as _agg
    orig = _agg._Parser._handle_type_convertion_operator

    def _conv(self, operator, values):
        if operator == "$convert" and isinstance(values, dict):
            if values.get("to") == "date":
                return values.get("onNull")
            try:
                return self.parse(values.get("input"))
            except KeyError:
                return values.get("onNull")
        return orig(self, operator, values)
    monkeypatch.setattr(_agg._Parser, "_handle_type_convertion_operator", _conv)


# ═════════════════════════════ birim: maske / süzgeç yardımcıları ═════════════════════════════
async def test_mask_helpers_and_text_scrub():
    from security import contact_pii as cp
    assert cp.mask_phone("05551234567") == MASKED_PHONE
    assert cp.mask_phone("+90 (555) 123 45 67") == MASKED_PHONE
    assert cp.mask_phone("") == "" and cp.mask_phone(None) == ""
    o = {"shipping_address": {"first_name": "Ayse", "phone": "05551234567", "address": ADDR,
                              "city": "Istanbul", "district": "Kadikoy", "postal_code": "34000"},
         "customer_phone": "+905551234567", "phone": "05551234567",
         "sender": {"address": "Depo Cad. 1", "phone": "02120000000"}}       # gönderici: dokunulmaz
    cp.mask_order_contact(o)
    sa = o["shipping_address"]
    assert sa["phone"] == MASKED_PHONE and sa["address"] == cp.ADDRESS_MASK and sa["postal_code"] == cp.ADDRESS_MASK
    assert sa["city"] == "Istanbul" and sa["district"] == "Kadikoy" and sa["first_name"] == "Ayse"
    assert o["customer_phone"] == MASKED_PHONE and o["phone"] == MASKED_PHONE
    assert o["sender"]["address"] == "Depo Cad. 1"
    # serbest metin: telefon + adres kalıbı; sipariş no / barkod / tutar / IP dokunulmaz
    t = cp.scrub_contact_text("Tel 0555 123 45 67; adres: Moda Cad. No:1 Kadıköy; Sipariş W11805 barkod "
                              "8681234567890 tutar 1.250,00 ip 203.0.113.5")
    assert "123 45 67" not in t and "Moda Cad" not in t and "[telefon]" in t and "[adres]" in t
    assert "W11805" in t and "8681234567890" in t and "1.250,00" in t and "203.0.113.5" in t
    # adres maskesi: yalnız '••••••' belirteci; gerçek adresteki tek '*' maske DEĞİL
    assert cp.is_masked_address("••••••") and cp.is_masked_address("••••••, Kadikoy, Istanbul")
    assert not cp.is_masked_address("Blok *A Daire 3")


async def test_masked_edit_values_never_overwrite_real_order_contact():
    from security import contact_pii as cp
    old = {"shipping_address": {"phone": "05551234567", "address": ADDR, "city": "Istanbul"},
           "phone": "05551234567"}
    new = {"shipping_address": {"phone": MASKED_PHONE, "address": cp.ADDRESS_MASK, "city": "Izmir"},
           "phone": MASKED_PHONE, "shipping_address.address": cp.ADDRESS_MASK}
    cp.carry_over_masked_order_contact(new, old)
    assert new["shipping_address"] == {"phone": "05551234567", "address": ADDR, "city": "Izmir"}
    assert new["phone"] == "05551234567" and new["shipping_address.address"] == ADDR


# ═════════════════════════════ sipariş listesi / detay / Göster ═════════════════════════════
async def test_order_list_and_detail_are_masked_reveal_is_permissioned_counted_audited(pay_http, pay_env,
                                                                                    monkeypatch):
    _shim_convert_to_date(monkeypatch)
    order = await pay_card_order(pay_http)
    staff, shdr = await pay_staff(["orders.view"])
    r = await pay_http.get("/api/orders?show_hidden=1", headers=shdr)
    assert r.status_code == 200, r.text
    row = next(o for o in r.json()["orders"] if o["id"] == order["id"])
    sa = row["shipping_address"]
    assert sa["phone"] == MASKED_PHONE and sa["address"] == "••••••" and sa["postal_code"] == "••••••"
    assert sa["city"] == "Istanbul" and sa["district"] == "Kadikoy" and sa["first_name"] == "Ayse"
    _no_contact(r.json())
    # tek kayıt detayı da maskeli (tam değer yalnız "Göster")
    d = (await pay_http.get(f"/api/orders/{order['id']}", headers=shdr)).json()
    assert d["contact_masked"] is True and d["shipping_address"]["phone"] == MASKED_PHONE
    _no_contact(d)
    assert not await pay_findall("auth_audit_logs", {"event": "pii_contact_view"})
    # Göster → tam değer + sayaç + denetim (değerin kendisi log'a yazılmaz)
    r = await pay_http.get(f"/api/orders/{order['id']}/contact", headers=shdr)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["shipping_address"]["phone"] == SHIP["phone"] and c["shipping_address"]["address"] == ADDR
    assert not any(k.endswith(("_enc", "_hash")) for k in c["shipping_address"])
    cnt = await deps.db.pii_reveal_counters.find_one({"_id": staff["id"]})
    assert cnt["count"] >= 1 and cnt["kinds"].get("contact_order", 0) >= 1
    logs = await pay_findall("auth_audit_logs", {"event": "pii_contact_view"})
    assert len(logs) == 1 and logs[0]["user_id"] == staff["id"] and logs[0]["meta"]["entity_id"] == order["id"]
    _no_contact(logs)


async def test_reveal_and_detail_require_orders_permission(pay_http, pay_env):
    order = await pay_card_order(pay_http)
    _, hdr = await pay_staff(["products.view"])
    assert (await pay_http.get(f"/api/orders/{order['id']}/contact", headers=hdr)).status_code == 403
    assert (await pay_http.get(f"/api/orders/{order['id']}", headers=hdr)).status_code == 403
    # sipariş sahibi müşteri kendi siparişinde tam adresini görür (personel maskesi ona uygulanmaz)
    cust, chdr = await pay_customer("owner-contact@example.test")
    o2 = await pay_card_order(pay_http, pid="p2", headers=chdr)
    mine = (await pay_http.get(f"/api/orders/{o2['id']}", headers=chdr)).json()
    assert mine["shipping_address"]["address"] == ADDR and "contact_masked" not in mine


async def test_bulk_contact_reveal_trips_alarm_and_blocks_all_reveals(pay_http, pay_env, monkeypatch):
    monkeypatch.setenv("PII_REVEAL_HOURLY_MAX", "2")
    await pay_seed_product("p1", stock=10)
    ids = [(await pay_card_order(pay_http))["id"] for _ in range(3)]
    staff, hdr = await pay_staff(["orders.view", "customers.view"])
    for oid in ids[:2]:
        assert (await pay_http.get(f"/api/orders/{oid}/contact", headers=hdr)).status_code == 200
    r = await pay_http.get(f"/api/orders/{ids[2]}/contact", headers=hdr)
    assert r.status_code == 429
    _no_contact(r.json())
    ev = await pay_findall("audit_logs", {"action": "security.pii_bulk_reveal"})
    assert ev and ev[-1]["metadata"]["last_kind"] == "contact_order"
    # engel 1 saat: başka türde açma da reddedilir (üye "Göster")
    cust, _ = await pay_customer("blocked-member@example.test")
    assert (await pay_http.get(f"/api/admin/members/{cust['id']}/contact", headers=hdr)).status_code == 429


async def test_edit_form_saved_with_masks_keeps_real_contact(pay_http, pay_env):
    order = await pay_card_order(pay_http)
    _, ahdr = await pay_staff(super_admin=True)
    det = (await pay_http.get(f"/api/orders/{order['id']}", headers=ahdr)).json()
    body = {"shipping_address": dict(det["shipping_address"], first_name="Ayşe")}   # maskeli form
    r = await pay_http.put(f"/api/orders/{order['id']}", json=body, headers=ahdr)
    assert r.status_code == 200, r.text
    sa = (await pay_find("orders", {"id": order["id"]}))["shipping_address"]
    assert sa["phone"] == SHIP["phone"] and sa["address"] == ADDR and sa["postal_code"] == "34000"
    assert sa["first_name"] == "Ayşe"
    # gerçek yeni değer yazılabilir
    r = await pay_http.put(f"/api/orders/{order['id']}", headers=ahdr,
                           json={"shipping_address": dict(sa, phone="05559998877", address="Yeni Sok. 3")})
    assert r.status_code == 200
    sa = (await pay_find("orders", {"id": order["id"]}))["shipping_address"]
    assert sa["phone"] == "05559998877" and sa["address"] == "Yeni Sok. 3"


async def test_returns_and_deleted_order_lists_are_masked(pay_http, pay_env):
    order = await pay_card_order(pay_http)
    await deps.db.customer_returns.insert_one({
        "id": "ret-1", "order_id": order["id"], "order_number": order["order_number"], "status": "requested",
        "items": [], "created_at": now_iso(), "refund_amount": 0})
    await deps.db.orders_deleted.insert_one(dict(order, id="del-1", deleted_at=now_iso()))
    _, hdr = await pay_staff(["orders.view", "returns.view"])
    r = await pay_http.get("/api/orders/returns/admin/list", headers=hdr)
    assert r.status_code == 200, r.text
    row = r.json()["returns"][0]
    assert row["customer_phone"] == MASKED_PHONE
    _no_contact(r.json())
    for url, key in (("/api/orders/deleted", "orders"), ("/api/admin/orders/deleted", "items")):
        r = await pay_http.get(url, headers=hdr)
        assert r.status_code == 200, (url, r.text)
        assert r.json()[key] and r.json()[key][0]["shipping_address"]["city"] == "Istanbul"
        _no_contact(r.json())


async def test_public_order_number_lookup_has_no_address_or_full_phone(pay_http, pay_env):
    order = await pay_card_order(pay_http)
    # Denetim #27: kanıtsız → yalnız durum özeti.
    r0 = await pay_http.get(f"/api/orders/by-number/{order['order_number']}")
    assert r0.status_code == 200 and "shipping_address" not in r0.json()
    _no_contact(r0.json())
    # Siparişi açan istemcinin anahtarıyla bile tam telefon/adres DÖNMEZ (maskeli).
    from pay_harness import PAY_TOKENS
    r = await pay_http.get(f"/api/orders/by-number/{order['order_number']}",
                           headers={"X-Order-Token": PAY_TOKENS[order["id"]]})
    assert r.status_code == 200
    sa = r.json()["shipping_address"]
    assert sa["address"] == "" and sa["phone"] == "+90 *** *** ** **" and sa["city"] == "Istanbul"   # #17: hane yok
    _no_contact(r.json())


async def test_admin_new_order_mail_has_no_phone_or_street_address():
    import notification_service as ns
    html = ns.build_admin_order_email_html({"order_number": "W1", "total": 10, "phone": SHIP["phone"]},
                                           dict(SHIP), "Yeni Sipariş")
    assert PHONE10 not in html and ADDR not in html and "Test Mah" not in html
    assert "Kadikoy / Istanbul" in html and "*** ** 67" in html


# ═════════════════════════════ tüm GET uçlarının taraması ═════════════════════════════
# Tam telefon/adresi BİLEREK döndüren uçlar (gerekçeli): tek kayıt "Göster" uçları (izin + sayaç +
# denetim) ve sunucu-tarafı kargo/fatura/irsaliye belgeleri (operasyon; kargo etiketi alıcı adresini
# basmak ZORUNDA). Yeni bir liste/rapor ucu tam veri döndürürse bu test KIRMIZI olur.
FULL_CONTACT_ALLOWED = {
    "/api/orders/{order_id}/contact",
    "/api/admin/members/{mid}/contact",
    "/api/influencers/{influencer_id}/contact",
    "/api/orders/{order_id}/cargo-label",
    "/api/orders/{order_id}/invoice/print",
    "/api/influencer-pr/{entry_id}/cargo-label",
    "/api/influencer-pr/{entry_id}/irsaliye",
}


async def test_no_staff_get_endpoint_leaks_full_phone_or_address(pay_http, pay_env, monkeypatch):
    _shim_convert_to_date(monkeypatch)
    from security import contact_pii as cp
    ph, ad = "5550000071", "Sentinel Sokak No 71"
    cust, _ = await pay_customer("sweep@example.test")
    await deps.db.users.update_one({"id": cust["id"]}, {"$set": {"phone": "0" + ph, "first_name": "S"}})
    oid = str(uuid.uuid4())
    ship = {"first_name": "A", "last_name": "B", "address": ad, "city": "Istanbul", "district": "Kadikoy",
            "phone": "0" + ph, "email": "sweep@example.test"}
    order = {"id": oid, "order_number": "W990071", "user_id": cust["id"], "status": "delivered",
             "payment_status": "paid", "payment_method": "credit_card", "platform": "meluni", "total": 10.0,
             "created_at": now_iso(days=-3), "delivered_at": now_iso(days=-1), "phone": "0" + ph,
             "customer_phone": "+90" + ph, "email": "sweep@example.test",
             "items": [{"product_id": "p1", "name": "Elbise", "quantity": 1, "price": 10.0}],
             "shipping_address": ship, "billing_address": dict(ship)}
    await deps.db.orders.insert_one(dict(order))
    await deps.db.orders.insert_one(dict(order, id="ty-1", order_number="TY1", platform="trendyol",
                                         status="shipped"))
    await deps.db.orders_deleted.insert_one(dict(order, id="del-9", deleted_at=now_iso()))
    await deps.db.addresses.insert_one(cp.seal_doc({"id": "a1", "user_id": cust["id"], "phone": "0" + ph,
                                                    "address": ad, "city": "Istanbul"}, "address"))
    await deps.db.influencers.insert_one({"id": "i1", "name": "Inf", "phone": "0" + ph, "adres": ad,
                                          "shipping_address": {"adres": ad, "il": "Istanbul", "ilce": "Kadikoy",
                                                               "phone": "0" + ph}, "created_at": now_iso()})
    await deps.db.influencer_pr.insert_one({"id": "p1", "influencer_id": "i1", "influencer_name": "Inf",
                                            "phone": "0" + ph, "adres": ad, "date": now_iso(),
                                            "status": "beklemede", "cargo_barcode": "INF1"})
    await deps.db.customer_returns.insert_one({"id": "r1", "order_id": oid, "order_number": "W990071",
                                               "status": "requested", "created_at": now_iso(),
                                               "customer_phone": "0" + ph, "user_id": cust["id"]})
    await deps.db.iys_consents.insert_one({"id": "c1", "phone": "+90" + ph, "channels": ["MESAJ"],
                                           "status": "ONAY", "created_at": now_iso(), "order_id": oid})
    await deps.db.cart_sessions.insert_one({"session_id": "s1", "phone": "0" + ph, "user_id": cust["id"],
                                            "updated_at": now_iso(days=-1), "items": [{"name": "x"}], "total": 10})
    await deps.db.stock_alerts.insert_one({"id": "sa1", "product_id": "p1", "phone": "0" + ph,
                                           "email": "x@example.test", "created_at": now_iso()})
    _, hdr = await pay_staff(super_admin=True)
    from fastapi.routing import APIRoute
    import server
    repl = {"{order_id}": oid, "{mid}": cust["id"], "{member_id}": cust["id"], "{user_id}": cust["id"],
            "{influencer_id}": "i1", "{entry_id}": "p1", "{return_id}": "r1", "{rid}": "r1",
            "{order_number}": "W990071", "{marketplace}": "trendyol"}
    leaks, checked = [], 0
    for r in server.app.routes:
        if not isinstance(r, APIRoute) or "GET" not in r.methods or r.path in FULL_CONTACT_ALLOWED:
            continue
        p = r.path
        for k, v in repl.items():
            p = p.replace(k, v)
        if "{" in p or "stream" in p:
            continue
        try:
            resp = await pay_http.get(p, headers=hdr)
        except Exception:
            continue
        checked += 1
        body = resp.content.decode("utf-8", "ignore")
        if ph in body or ad in body:
            leaks.append(f"{resp.status_code} {r.path}")
    assert checked > 300
    assert not leaks, leaks
