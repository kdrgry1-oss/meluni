"""KVKK (sahip talebi) — telefon / adres / IBAN / TC at-rest ŞİFRELEME (security/contact_pii.py).

* Üye profil telefonu: kayıt/profil/sipariş akışında şifreli yazılır; mükerrer numara kontrolü,
  SMS ile şifre sıfırlama ve İYS doğrulaması HASH eşitliğiyle çalışır (regex/parça tarama yok).
* Adres defteri: telefon + açık adres şifreli, sahibine açık; tekilleştirme address_hash ile.
* Influencer + PR kaydı: telefon, adres, IBAN, TC şifreli; liste/ayrıntı maskeli, "Göster" yalnız
  campaigns.influencers (sayaç + denetim); maskeli form kaydı gerçek değeri EZMEZ; Excel'de tam değer
  yalnız campaigns.influencers_export ile, formül enjeksiyonu güvenli.
* Tek seferlik backfill (doğrula-sonra-sil, kaldığı yerden sürer, idempotent) + gece güvenlik ağı.
Tüm veriler sahtedir.
"""
from __future__ import annotations

import io
import json
import logging

import pytest

from pay_harness import (SHIP, pay_card_order, pay_customer, pay_drain, pay_find, pay_findall, pay_staff,
                         now_iso)
from routes import deps

pytestmark = pytest.mark.asyncio

PHONE = "05550000001"
PHONE10 = "5550000001"
ADDR = "Deneme Mah. Ornek Sok. No:5 D:2"
IBAN = "TR330006100519786457841326"
TCKN = "12345678950"


def _blob(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


async def _raw(coll, q):
    return await deps.db[coll].find_one(q)


def _no_plain(doc, *vals):
    b = _blob(doc)
    for v in vals:
        assert v not in b, f"düz değer saklanmış: {v[:4]}…"


# ═════════════════════════════════ üye profil telefonu ═════════════════════════════════
async def test_register_seals_phone_and_duplicate_check_uses_hash(pay_http, pay_env):
    from security import contact_pii as cp
    body = {"email": "enc-reg@example.test", "password": "Passw0rd!x", "first_name": "Ece", "last_name": "Kaya",
            "phone": "0555 000 00 01"}
    r = await pay_http.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    u = await _raw("users", {"email": "enc-reg@example.test"})
    assert "phone" not in u and u["phone_last2"] == "01" and u["phone_hash"] and u["phone_enc"].startswith("v1:")
    assert cp.open_field(u, "phone") == "905550000001"
    _no_plain(u, PHONE10)
    # oturum kullanıcısı (deps) telefonu açık görür → /auth/me sahibine gerçek değer
    me = await pay_http.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.json()["phone"] == "905550000001" and "phone_enc" not in me.json()
    # aynı numara başka biçimde → mükerrer (hash eşitliği)
    r2 = await pay_http.post("/api/auth/register", json=dict(body, email="enc-reg2@example.test",
                                                             phone="+90 (555) 000-00-01"))
    assert r2.status_code == 400 and "telefon" in r2.json()["detail"].lower()


async def test_legacy_plaintext_phone_still_found_before_backfill(pay_http, pay_env):
    await deps.db.users.insert_one({"id": "legacy-1", "email": "legacy@example.test", "phone": "05550000002",
                                    "is_admin": False, "is_active": True})
    r = await pay_http.post("/api/auth/register", json={
        "email": "new@example.test", "password": "Passw0rd!x", "first_name": "A", "last_name": "B",
        "phone": "+905550000002"})
    assert r.status_code == 400


async def test_sms_password_reset_finds_sealed_phone(pay_http, pay_env):
    r = await pay_http.post("/api/auth/register", json={
        "email": "otp-enc@example.test", "password": "Passw0rd!x", "first_name": "O", "last_name": "T",
        "phone": PHONE})
    uid = (await _raw("users", {"email": "otp-enc@example.test"}))["id"]
    # Denetim #2: kayıtta OTP'siz alınan numara doğrulanana kadar SMS sıfırlama hedefi değildir.
    r = await pay_http.post("/api/auth/forgot-password/request-otp", json={"phone": "0555 000 00 01"})
    assert r.status_code == 200, r.text
    await pay_drain()
    assert not [x for x in await pay_findall("password_reset_otps") if x.get("user_id") == uid]
    # Numara OTP ile doğrulandıktan sonra (iys/otp/verify → phone_verified=True) şifreli numara bulunur.
    from routes.deps import db as _db
    await _db.users.update_one({"id": uid}, {"$set": {"phone_verified": True}})
    await _db.password_reset_otps.delete_many({})
    r = await pay_http.post("/api/auth/forgot-password/request-otp", json={"phone": "0555 000 00 01"})
    assert r.status_code == 200, r.text
    await pay_drain()
    recs = await pay_findall("password_reset_otps")
    assert recs and recs[-1]["user_id"] == uid


async def test_profile_phone_update_is_sealed(pay_http, pay_env):
    from security import contact_pii as cp
    cust, hdr = await pay_customer("prof-enc@example.test")
    r = await pay_http.put("/api/users/me", json={"phone": "0555 000 00 03"}, headers=hdr)
    assert r.status_code == 200, r.text
    u = await _raw("users", {"id": cust["id"]})
    assert "phone" not in u and cp.open_field(u, "phone") == "0555 000 00 03" and u["phone_verified"] is False
    _no_plain(u, "000 00 03")


# ═════════════════════════════════ adres defteri ═════════════════════════════════
async def test_address_book_is_sealed_owner_sees_plaintext(pay_http, pay_env):
    from security import contact_pii as cp
    cust, hdr = await pay_customer("book@example.test")
    r = await pay_http.post("/api/addresses", headers=hdr, json={
        "title": "Ev", "first_name": "A", "last_name": "B", "phone": PHONE, "address": ADDR,
        "city": "Istanbul", "district": "Kadikoy"})
    assert r.status_code == 200, r.text
    aid = r.json()["address_id"]
    a = await _raw("addresses", {"id": aid})
    _no_plain(a, PHONE10, ADDR)
    assert a["city"] == "Istanbul" and a["address_hash"] == cp.address_hash(ADDR, "Istanbul", "Kadikoy")
    mine = (await pay_http.get("/api/my-addresses", headers=hdr)).json()["addresses"]
    assert mine[0]["phone"] == PHONE and mine[0]["address"] == ADDR
    assert not any(k.endswith(("_enc", "_hash")) for k in mine[0])
    # sahibinin düzenlemesi yeniden mühürlenir; hash güncellenir
    r = await pay_http.put(f"/api/addresses/{aid}", headers=hdr, json={"address": "Yeni Sok. 9", "city": "Izmir"})
    assert r.status_code == 200
    a = await _raw("addresses", {"id": aid})
    assert cp.open_field(a, "address") == "Yeni Sok. 9" and cp.open_field(a, "phone") == PHONE
    assert a["address_hash"] == cp.address_hash("Yeni Sok. 9", "Izmir", "Kadikoy")
    _no_plain(a, "Yeni Sok. 9")


async def test_member_order_saves_sealed_address_once(pay_http, pay_env):
    cust, hdr = await pay_customer("order-book@example.test")
    await pay_card_order(pay_http, headers=hdr)
    await pay_card_order(pay_http, pid="p2", headers=hdr)          # aynı adres → tekrar yazılmaz
    rows = await deps.db.addresses.find({"user_id": cust["id"]}).to_list(10)
    assert len(rows) == 1
    _no_plain(rows, SHIP["address"], "5551234567")
    u = await _raw("users", {"id": cust["id"]})                    # profil telefonu siparişten (şifreli)
    assert "phone" not in u and u.get("phone_last2") == "67"


# ═════════════════════════════════ influencer ═════════════════════════════════
INF_BODY = {"name": "Deneme Influencer", "platform": "instagram", "instagram": "@deneme",
            "phone": PHONE, "adres": ADDR, "iban": IBAN, "tc": TCKN,
            "shipping_address": {"full_name": "Deneme Influencer", "phone": PHONE, "il": "Istanbul",
                                 "ilce": "Kadikoy", "adres": ADDR}}


async def _create_inf(pay_http, hdr, **extra):
    r = await pay_http.post("/api/influencers", json=dict(INF_BODY, **extra), headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["influencer"]


async def test_influencer_contact_iban_tc_encrypted_list_masked(pay_http, pay_env):
    from security import contact_pii as cp
    _, hdr = await pay_staff(["campaigns.influencers", "campaigns.view"])
    inf = await _create_inf(pay_http, hdr)
    _no_plain(inf, PHONE10, ADDR, IBAN, TCKN)
    raw = await _raw("influencers", {"id": inf["id"]})
    _no_plain(raw, PHONE10, ADDR, IBAN, TCKN)
    assert cp.open_field(raw, "phone") == PHONE and cp.open_field(raw, "iban") == IBAN
    assert cp.open_field(raw, "tc") == TCKN and cp.open_field(raw["shipping_address"], "adres") == ADDR
    assert raw["shipping_address"]["il"] == "Istanbul" and raw["iban_last4"] == IBAN[-4:]
    lst = (await pay_http.get("/api/influencers", headers=hdr)).json()["influencers"]
    row = lst[0]
    assert row["phone"] == "+90 *** *** ** 01" and row["adres"] == "••••••"
    assert row["shipping_address"]["adres"] == "••••••" and row["shipping_address"]["ilce"] == "Kadikoy"
    assert row["iban"].endswith(IBAN[-4:-2] + " " + IBAN[-2:]) and row["tc"] == "*********50"
    _no_plain(lst, PHONE10, ADDR, IBAN, TCKN)
    det = (await pay_http.get(f"/api/influencers/{inf['id']}", headers=hdr)).json()
    assert det["contact_masked"] is True
    _no_plain(det, PHONE10, ADDR, IBAN, TCKN)
    # telefonla arama: TAM numara hash ile bulunur; parça bulunmaz
    assert len((await pay_http.get("/api/influencers?q=0555 000 00 01", headers=hdr)).json()["influencers"]) == 1
    assert len((await pay_http.get("/api/influencers?q=00000", headers=hdr)).json()["influencers"]) == 0


async def test_influencer_reveal_permission_counter_and_audit(pay_http, pay_env):
    _, ihdr = await pay_staff(["campaigns.influencers"])
    inf = await _create_inf(pay_http, ihdr)
    viewer, vhdr = await pay_staff(["campaigns.view"])
    assert (await pay_http.get(f"/api/influencers/{inf['id']}/contact", headers=vhdr)).status_code == 403
    actor, ahdr = await pay_staff(["campaigns.influencers"])
    r = await pay_http.get(f"/api/influencers/{inf['id']}/contact", headers=ahdr)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["phone"] == PHONE and c["iban"] == IBAN and c["tc"] == TCKN and c["shipping_address"]["adres"] == ADDR
    cnt = await deps.db.pii_reveal_counters.find_one({"_id": actor["id"]})
    assert cnt["kinds"]["contact_influencer"] >= 1
    logs = await pay_findall("auth_audit_logs", {"event": "pii_contact_view", "user_id": actor["id"]})
    assert logs and logs[0]["meta"]["entity_type"] == "influencer"
    _no_plain(logs, PHONE10, ADDR, IBAN, TCKN)


async def test_influencer_masked_form_save_keeps_values_new_values_reseal(pay_http, pay_env):
    from security import contact_pii as cp
    _, hdr = await pay_staff(["campaigns.influencers", "campaigns.view"])
    inf = await _create_inf(pay_http, hdr)
    row = (await pay_http.get("/api/influencers", headers=hdr)).json()["influencers"][0]
    # panel formu maskeli değerlerle (değiştirilmeden) kaydeder — Influencers.jsx gövdesinin aynısı
    body = {"name": row["name"], "phone": row["phone"], "adres": row["adres"], "notes": "yeni not",
            "shipping_address": {"full_name": row["name"], "phone": row["phone"], "il": "Istanbul",
                                 "ilce": "Kadikoy", "adres": row["adres"]}}
    r = await pay_http.put(f"/api/influencers/{inf['id']}", json=body, headers=hdr)
    assert r.status_code == 200, r.text
    raw = await _raw("influencers", {"id": inf["id"]})
    assert cp.open_field(raw, "phone") == PHONE and cp.open_field(raw, "adres") == ADDR
    assert cp.open_field(raw["shipping_address"], "phone") == PHONE
    assert cp.open_field(raw["shipping_address"], "adres") == ADDR and raw["notes"] == "yeni not"
    assert cp.open_field(raw, "iban") == IBAN                       # gönderilmeyen alan korunur
    r = await pay_http.put(f"/api/influencers/{inf['id']}", headers=hdr, json={
        "phone": "05550000009", "shipping_address": dict(body["shipping_address"], phone="05550000009",
                                                         adres="Yeni Mah. 1")})
    raw = await _raw("influencers", {"id": inf["id"]})
    assert cp.open_field(raw, "phone") == "05550000009"
    assert cp.open_field(raw["shipping_address"], "adres") == "Yeni Mah. 1"
    _no_plain(raw, "5550000009", "Yeni Mah. 1")


async def test_pr_entry_fills_real_values_from_master_and_list_is_masked(pay_http, pay_env):
    from security import contact_pii as cp
    _, hdr = await pay_staff(["campaigns.influencers", "campaigns.view"])
    inf = await _create_inf(pay_http, hdr)
    # PR formu influencer'ı maskeli listeden seçer → telefon/adres alanları maskeli gelir
    r = await pay_http.post("/api/influencer-pr", headers=hdr, json={
        "influencer_id": inf["id"], "phone": "+90 *** *** ** 01", "adres": "••••••, Kadikoy, Istanbul"})
    assert r.status_code == 200, r.text
    _no_plain(r.json(), PHONE10, ADDR)
    pr = await _raw("influencer_pr", {"id": r.json()["entry"]["id"]})
    _no_plain(pr, PHONE10, ADDR)
    assert cp.open_field(pr, "phone") == PHONE and ADDR in cp.open_field(pr, "adres")
    lst = (await pay_http.get("/api/influencer-pr", headers=hdr)).json()["entries"]
    assert lst[0]["phone"] == "+90 *** *** ** 01" and lst[0]["adres"] == "••••••"
    _no_plain(lst, PHONE10, ADDR)


async def test_cargo_documents_use_real_address_and_need_influencer_permission(pay_http, pay_env):
    _, hdr = await pay_staff(["campaigns.influencers", "campaigns.view"])
    inf = await _create_inf(pay_http, hdr)
    await deps.db.influencer_pr.insert_one({"id": "pr-doc", "influencer_id": inf["id"], "influencer_name": "D",
                                            "cargo_barcode": "INF123", "cargo_tracking_no": "INF123",
                                            "date": now_iso(), "products": []})
    _, vhdr = await pay_staff(["campaigns.view"])
    for path in ("cargo-label", "irsaliye"):
        assert (await pay_http.get(f"/api/influencer-pr/pr-doc/{path}", headers=vhdr)).status_code == 403
        r = await pay_http.get(f"/api/influencer-pr/pr-doc/{path}", headers=hdr)
        assert r.status_code == 200, (path, r.text[:300])
        # alıcı bilgisi belgede TAM (etiket telefonu "555 000 00 01" biçiminde basar)
        assert ADDR in r.text and ("555 000 00 01" in r.text or PHONE in r.text), path


def _xlsx_cells(content: bytes) -> list:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content))
    out = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            out.extend("" if v is None else str(v) for v in row)
    return out


async def test_influencer_exports_mask_without_export_permission_and_are_formula_safe(pay_http, pay_env):
    _, hdr = await pay_staff(["campaigns.influencers", "campaigns.view"])
    inf = await _create_inf(pay_http, hdr, name="=HYPERLINK(\"http://x.invalid\",\"tikla\")")
    await deps.db.influencer_pr.insert_one({"id": "pr-x", "influencer_id": inf["id"],
                                            "influencer_name": "=cmd|'/c calc'!A0", "cargo_barcode": "INF9",
                                            "date": now_iso(), "status": "gonderildi", "products": []})
    for url in ("/api/influencer-registry/export", "/api/influencer-pr/export?with_images=false",
                "/api/influencer-pr/calendar-export"):
        r = await pay_http.get(url, headers=hdr)
        assert r.status_code == 200, (url, r.text[:300])
        cells = _xlsx_cells(r.content)
        blob = " ".join(cells)
        assert PHONE10 not in blob and ADDR not in blob, url
        assert not any(c.startswith(("=", "+", "-", "@")) for c in cells if c), url
    _, fhdr = await pay_staff(["campaigns.view", "campaigns.influencers_export"])
    r = await pay_http.get("/api/influencer-registry/export", headers=fhdr)
    blob = " ".join(_xlsx_cells(r.content))
    assert PHONE in blob and ADDR in blob
    ev = await pay_findall("audit_logs", {"action": "security.influencer_registry_export"})
    assert ev and ev[-1]["metadata"]["contact"] == "full" and ev[-1]["metadata"]["rows"] == 1


# ═════════════════════════════════ backfill + gece ağı ═════════════════════════════════
async def _seed_plain():
    await deps.db.influencers.insert_one({"id": "inf-l", "name": "L", "phone": PHONE, "adres": ADDR, "iban": IBAN,
                                          "tc_no": TCKN, "shipping_address": {"phone": PHONE, "adres": ADDR,
                                                                              "il": "Istanbul", "ilce": "Kadikoy"}})
    await deps.db.influencer_pr.insert_one({"id": "pr-l", "influencer_id": "inf-l", "phone": PHONE, "adres": ADDR})
    await deps.db.addresses.insert_one({"id": "ad-l", "user_id": "u-l", "phone": PHONE, "address": ADDR,
                                        "city": "Istanbul", "district": "Kadikoy"})
    await deps.db.users.insert_one({"id": "u-l", "email": "l@example.test", "phone": PHONE, "is_admin": False})
    await deps.db.users.insert_one({"id": "u-2", "email": "l2@example.test", "phone": "05550000002"})
    await deps.db.users.insert_one({"id": "staff-l", "email": "s@example.test", "phone": "05550000004",
                                    "is_admin": True, "role_id": "r"})
    await deps.db.users.insert_one({"id": "u-mask", "email": "m@example.test", "phone": "+90 *** *** ** 12"})


async def test_backfill_encrypts_verifies_skips_staff_and_is_idempotent(pay_env):
    import scheduler
    from security import contact_pii as cp
    await _seed_plain()
    rep = await scheduler.encrypt_existing_contact_pii(batch_size=2)
    assert rep["encrypted"] == {"influencers": 1, "influencer_pr": 1, "addresses": 1, "users": 3}
    assert rep["masked_values_dropped"] == {"users": 1} and not rep["verify_failed"]
    inf = await _raw("influencers", {"id": "inf-l"})
    _no_plain(inf, PHONE10, ADDR, IBAN, TCKN)
    assert cp.open_field(inf, "phone") == PHONE and cp.open_field(inf, "iban") == IBAN
    assert cp.open_field(inf, "tc_no") == TCKN and cp.open_field(inf["shipping_address"], "adres") == ADDR
    a = await _raw("addresses", {"id": "ad-l"})
    assert cp.open_field(a, "address") == ADDR and a["address_hash"] == cp.address_hash(ADDR, "Istanbul", "Kadikoy")
    u = await _raw("users", {"id": "u-l"})
    assert "phone" not in u and cp.open_field(u, "phone") == PHONE
    assert (await _raw("users", {"id": "staff-l"}))["phone"] == "05550000004"      # personel: dokunulmaz
    assert "phone" not in await _raw("users", {"id": "u-mask"})                    # maske artığı düşer
    # hash araması backfill sonrası çalışır
    assert await deps.db.users.find_one(cp.phone_lookup("phone", "+90 555 000 00 01"))
    flag = await pay_find("settings", {"id": scheduler.CONTACT_BACKFILL_FLAG})
    assert flag["done"] is True
    logs = await pay_findall("integration_logs", {"platform": "pii_backfill", "action": "encrypt_contact_pii"})
    assert logs and logs[-1]["status"] == "success"
    _no_plain(logs, PHONE10, ADDR, IBAN, TCKN)
    before = await deps.db.influencers.find({}, {"_id": 0}).to_list(10)
    assert (await scheduler.encrypt_existing_contact_pii()).get("skipped") is True
    assert await deps.db.influencers.find({}, {"_id": 0}).to_list(10) == before


async def test_backfill_resumes_after_leadership_loss(pay_env, monkeypatch):
    import scheduler
    await _seed_plain()
    calls = {"n": 0}

    async def _leader():
        calls["n"] += 1
        return calls["n"] <= 1
    monkeypatch.setattr(scheduler, "_still_leader", _leader)
    rep = await scheduler.encrypt_existing_contact_pii(batch_size=1)
    assert rep["aborted"] is True
    assert not (await pay_find("settings", {"id": scheduler.CONTACT_BACKFILL_FLAG})).get("done")

    async def _yes():
        return True
    monkeypatch.setattr(scheduler, "_still_leader", _yes)
    rep2 = await scheduler.encrypt_existing_contact_pii(batch_size=1)
    assert rep2["aborted"] is False
    for coll in ("influencers", "influencer_pr", "addresses"):
        _no_plain(await deps.db[coll].find({}).to_list(10), PHONE10, ADDR)


async def test_backfill_keeps_plaintext_when_verification_fails(pay_env, monkeypatch):
    import scheduler
    from security import contact_pii as cp
    await deps.db.influencers.insert_one({"id": "bad", "phone": PHONE})
    real_open = cp.open_field
    monkeypatch.setattr(cp, "open_field", lambda c, f: "0000" if f == "v" else real_open(c, f))
    rep = await scheduler.encrypt_existing_contact_pii()
    assert rep["verify_failed"] == {"influencers": 1} and rep["encrypted"]["influencers"] == 0
    raw = await _raw("influencers", {"id": "bad"})
    assert raw["phone"] == PHONE and "phone_enc" not in raw


async def test_nightly_net_catches_straggler_and_warns(pay_env, caplog):
    import scheduler
    from security import contact_pii as cp
    assert sum((await scheduler.encrypt_existing_contact_pii(nightly=True))["encrypted"].values()) == 0
    await deps.db.influencer_pr.insert_one({"id": "late", "phone": PHONE})          # mühürlemeyen bir yol
    with caplog.at_level(logging.WARNING, logger="scheduler"):
        rep = await scheduler.encrypt_existing_contact_pii(nightly=True)
    assert rep["encrypted"]["influencer_pr"] == 1 and rep["paths"]["influencer_pr"] == {"phone": 1}
    assert any("GECE AĞI" in m and "influencer_pr" in m for m in caplog.messages)
    assert cp.open_field(await _raw("influencer_pr", {"id": "late"}), "phone") == PHONE


async def test_scheduler_registers_new_jobs_only():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler)
    for jid in ('id="pii_contact_backfill_once"', 'id="pii_contact_nightly_net"', 'id="order_pii_retention_minimize"'):
        assert jid in src
    # mevcut TCKN işleri değişmedi
    assert 'id="pii_tax_id_nightly_net"' in src and "CronTrigger(hour=1, minute=47)" in src


async def test_phone_scrub_and_kvkk_delete_work_with_sealed_phone(pay_env):
    import server
    from security import contact_pii as cp
    from routes.kvkk import anonymize_user, find_deletion_tombstone
    await deps.db.users.insert_one(cp.seal_doc({"id": "sc-1", "email": "sc@example.test", "phone": PHONE,
                                                "is_admin": False}, "user"))
    await deps.db.influencers.insert_one(cp.seal_doc({"id": "sc-i", "phone": PHONE}, "influencer"))
    await server.scrub_phone_once(PHONE, reason="test")
    u = await _raw("users", {"id": "sc-1"})
    assert not cp.open_field(u, "phone") and "phone_hash" not in u
    assert not cp.open_field(await _raw("influencers", {"id": "sc-i"}), "phone")
    # KVKK silme: şifreli telefon mezar taşına (HMAC) işlenir → aynı numarayla geri dönüş tanınır
    await deps.db.users.insert_one(cp.seal_doc({"id": "kv-1", "email": "kv@example.test", "phone": "05550000005",
                                                "is_admin": False}, "user"))
    await anonymize_user(await _raw("users", {"id": "kv-1"}), source="test", request_id="rq-1",
                         send_iys_ret=False)
    assert await find_deletion_tombstone(None, "05550000005")
