"""Kimlik / hesap yönetimi av turu (identity-3) — mevcut testlerin kapsamadığı açıklar için
regresyon testleri.

  ID3-01  GET /api/auth/me (ve sosyal giriş yanıtı) kimlik bilgisi materyalini döndürmemeli:
          eski şifre hash'leri (password_history), şifreli MFA sırrı/telefonu, kurtarma kodu
          hash'leri, kilit sayaçları.
  ID3-02  GET /api/orders/{id}: "is_admin" bayrağı tek başına TAM (maskesiz, vergi no açık)
          sipariş okumaya yetmemeli — orders.view yetkisi olmayan personel başkasının siparişini
          göremez (liste/olaylar uçlarıyla aynı yetki).
  ID3-03  Facebook ile YENİ açılan hesabın e-postası doğrulanmış sayılmaz → o e-postayla verilmiş
          misafir siparişleri (adres, iade/IBAN) bu hesaba bağlanmaz.
  ID3-04  Şifre değişince / sıfırlanınca bekleyen şifre-sıfırlama token'ları ölür.
  ID3-05  /auth/change-password mevcut şifre denemesi hesap kilidine tabidir (çalınmış oturumla
          şifreyi çevrimiçi kaba kuvvetle bulma engeli).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from id_harness import (ID_PASSWORD, ID_NEW_PASSWORD, id_seed_role, id_seed_user, id_auth, id_ip, id_find,
                        id_run, id_token)
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

ME = "/api/auth/me"
_SECRET_FIELDS = ("password", "password_history", "mfa_secret_enc", "mfa_pending_secret_enc", "mfa_phone_enc",
                  "mfa_pending_phone_enc", "mfa_recovery_codes", "failed_attempts", "locked_until",
                  "fail_window_start", "first_failed_at", "mfa_fail_count", "mfa_day_fail_count")


def _insert(coll, doc):
    async def _i():
        await deps.db[coll].insert_one(dict(doc))
    id_run(_i())


# ════════════════════════════════ ID3-01 ════════════════════════════════
def test_me_does_not_leak_password_history_or_mfa_material(id_client):
    old_hash = "$2b$04$" + "a" * 53
    staff = id_seed_user(kind="super", password_history=[old_hash, old_hash],
                         mfa_enabled=True, mfa_method="totp", mfa_secret_enc="gAAAA-sifreli-sir",
                         mfa_phone_enc="gAAAA-sifreli-tel", mfa_pending_secret_enc="gAAAA-bekleyen",
                         mfa_recovery_codes=[{"h": "f" * 64, "used": False}],
                         failed_attempts=3, first_failed_at="2026-01-01T00:00:00+00:00",
                         mfa_fail_count=2, mfa_day_fail_count=4)
    r = id_client.get(ME, headers=id_auth(id_token(staff)))
    assert r.status_code == 200, r.text
    body = r.json()
    leaked = [k for k in _SECRET_FIELDS if k in body]
    assert not leaked, f"/auth/me kimlik bilgisi materyali döndürdü: {leaked}"
    assert old_hash not in r.text
    # İstemcinin kullandığı alanlar korunur
    assert body["id"] == staff["id"] and body["email"] == staff["email"]
    assert body.get("is_admin") is True and body.get("mfa_enabled") is True


def test_me_after_password_change_has_no_old_hashes(id_client):
    cust = id_seed_user(kind="customer")
    r = id_client.post("/api/auth/change-password", headers=id_auth(id_token(cust)),
                       json={"current_password": ID_PASSWORD, "new_password": ID_NEW_PASSWORD})
    assert r.status_code == 200, r.text
    me = id_client.get(ME, headers=id_auth(r.json()["token"]))
    assert me.status_code == 200
    assert "password_history" not in me.json()
    assert "$2b$" not in me.text


# ════════════════════════════════ ID3-02 ════════════════════════════════
def _seed_order(owner_id: str, oid: str = "ord-id3-1") -> dict:
    o = {"id": oid, "order_number": "FC-ID3-1", "user_id": owner_id, "status": "confirmed",
         "payment_status": "paid", "payment_method": "credit_card", "total": 100,
         "shipping_address": {"first_name": "Ayşe", "last_name": "Kurban", "phone": "905551112233",
                              "email": "ayse@example.test", "address": "Gizli Sk. 1", "city": "İstanbul",
                              "district": "Kadıköy"},
         "admin_notes": [{"text": "RISK-NOTU"}], "customer_ip": "203.0.113.9",
         "items": [], "created_at": "2026-09-01T10:00:00+00:00"}
    _insert("orders", o)
    return o


def test_staff_without_orders_permission_cannot_read_full_order(id_client):
    owner = id_seed_user(kind="customer")
    o = _seed_order(owner["id"])
    id_seed_role("r_products_only", ["products.view", "products.edit"])
    other_staff = id_seed_user(kind="staff", role_id="r_products_only")
    r = id_client.get(f"/api/orders/{o['id']}", headers=id_auth(id_token(other_staff)))
    assert r.status_code in (403, 404), r.text
    assert "Gizli Sk" not in r.text and "RISK-NOTU" not in r.text and "905551112233" not in r.text
    # Aynı personel liste ucuna da giremez (tutarlılık kontrolü)
    assert id_client.get("/api/orders", headers=id_auth(id_token(other_staff))).status_code == 403


def test_staff_with_role_removed_cannot_read_full_order(id_client):
    owner = id_seed_user(kind="customer")
    o = _seed_order(owner["id"], oid="ord-id3-2")
    # is_admin bayrağı kalmış ama rolü alınmış (eski/bozuk) hesap
    ghost = id_seed_user(kind="customer", is_admin=True)
    tok = deps.create_token(ghost["id"], True, token_version=0, auth_time=deps.now_ts())
    r = id_client.get(f"/api/orders/{o['id']}", headers=id_auth(tok))
    assert r.status_code in (401, 403, 404), r.text
    assert "Gizli Sk" not in r.text


def test_order_detail_still_works_for_owner_and_authorised_staff(id_client):
    owner = id_seed_user(kind="customer")
    o = _seed_order(owner["id"], oid="ord-id3-3")
    r = id_client.get(f"/api/orders/{o['id']}", headers=id_auth(id_token(owner)))
    assert r.status_code == 200 and r.json()["id"] == o["id"]
    assert "admin_notes" not in r.json() and "customer_ip" not in r.json()
    id_seed_role("r_orders_view", ["orders.view"])
    ops = id_seed_user(kind="staff", role_id="r_orders_view")
    r = id_client.get(f"/api/orders/{o['id']}", headers=id_auth(id_token(ops)))
    assert r.status_code == 200 and r.json()["id"] == o["id"]
    assert r.json().get("admin_notes")  # yetkili personel iç alanları görür
    sup = id_seed_user(kind="super")
    assert id_client.get(f"/api/orders/{o['id']}", headers=id_auth(id_token(sup))).status_code == 200


# ════════════════════════════════ ID3-03 ════════════════════════════════
def test_facebook_new_account_email_is_not_trusted_for_guest_orders(id_client, id_env):
    _insert("settings", {"id": "social_auth", "facebook_enabled": True, "facebook_app_id": "fb-app",
                         "facebook_app_secret": "fb-secret", "facebook_redirect_uri": "https://shop.test/cb"})
    victim_email = "kurban.misafir@example.test"
    _insert("orders", {"id": "guest-ord-1", "order_number": "FC-G-1", "user_id": None, "status": "delivered",
                       "payment_status": "paid", "payment_method": "bank_transfer", "total": 250,
                       "email": victim_email,
                       "shipping_address": {"first_name": "Kurban", "last_name": "Misafir",
                                            "email": victim_email, "phone": "905559998877",
                                            "address": "Ev Adresi 42", "city": "Ankara"},
                       "items": [], "created_at": "2026-09-20T10:00:00+00:00"})
    rx = id_env["respx"]
    rx.get("https://graph.facebook.com/v23.0/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "fb-at"}))
    rx.get("https://graph.facebook.com/v23.0/me").mock(
        return_value=httpx.Response(200, json={"id": "fb-attacker-1", "name": "Saldırgan Kişi",
                                               "email": victim_email}))
    r = id_client.post("/api/auth/facebook", json={"code": "c0de"}, headers=id_ip("198.51.100.77"))
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    u = id_find("users", {"email": victim_email})
    assert u and u.get("email_verified") is not True, "Facebook e-postası doğrulanmış sayıldı"
    mine = id_client.get("/api/my-orders", headers=id_auth(tok))
    assert mine.status_code == 200
    assert mine.json()["total"] == 0, "misafir siparişi Facebook hesabına sızdı"
    assert "Ev Adresi 42" not in mine.text
    # iade/IBAN uçları da kapalı
    rr = id_client.post("/api/orders/guest-ord-1/return-request", headers=id_auth(tok),
                        json={"items": [0], "reason": "x"})
    assert rr.status_code == 403


# ════════════════════════════════ ID3-04 ════════════════════════════════
def _plant_email_reset_token(user: dict, raw: str) -> None:
    from routes.auth import _hash_reset_token
    now = datetime.now(timezone.utc)
    _insert("password_reset_otps", {"email": user["email"], "channel": "email", "user_id": user["id"],
                                    "used": True, "reset_token_hash": _hash_reset_token(raw),
                                    "reset_token_expires": now.timestamp() + 1800,
                                    "created_at": now.isoformat()})


def test_pending_reset_token_dies_after_password_change(id_client):
    cust = id_seed_user(kind="customer")
    # Posta kutusuna kısa süre erişen saldırgan sıfırlama linkini aldı; sahibi şifresini değiştirdi.
    _plant_email_reset_token(cust, "attacker-held-reset-token-0001")
    r = id_client.post("/api/auth/change-password", headers=id_auth(id_token(cust)),
                       json={"current_password": ID_PASSWORD, "new_password": ID_NEW_PASSWORD})
    assert r.status_code == 200, r.text
    r2 = id_client.post("/api/auth/forgot-password/reset",
                        json={"reset_token": "attacker-held-reset-token-0001", "new_password": "Sald1rgan!Pw#9"})
    assert r2.status_code == 400, r2.text
    assert id_client.post("/api/auth/login", json={"email": cust["email"], "password": ID_NEW_PASSWORD},
                          headers=id_ip("198.51.100.5")).status_code == 200


def test_other_reset_tokens_die_after_successful_reset(id_client):
    cust = id_seed_user(kind="customer")
    _plant_email_reset_token(cust, "legit-reset-token-aaaa-0001")
    _plant_email_reset_token(cust, "stale-reset-token-bbbb-0002")
    ok = id_client.post("/api/auth/forgot-password/reset",
                        json={"reset_token": "legit-reset-token-aaaa-0001", "new_password": ID_NEW_PASSWORD})
    assert ok.status_code == 200, ok.text
    again = id_client.post("/api/auth/forgot-password/reset",
                           json={"reset_token": "stale-reset-token-bbbb-0002", "new_password": "Sald1rgan!Pw#9"})
    assert again.status_code == 400, again.text


# ════════════════════════════════ ID3-05 ════════════════════════════════
def test_change_password_current_password_guess_is_throttled(id_client):
    cust = id_seed_user(kind="customer")
    hdr = {**id_auth(id_token(cust)), **id_ip("198.51.100.66")}
    codes = []
    for i in range(8):
        r = id_client.post("/api/auth/change-password", headers=hdr,
                           json={"current_password": f"tahmin-{i}-yanlis", "new_password": ID_NEW_PASSWORD})
        codes.append(r.status_code)
    assert 429 in codes, f"mevcut şifre tahmini sınırsız: {codes}"
    # Kilitliyken DOĞRU şifre de kabul edilmez (tahmin doğrulanamaz)
    r = id_client.post("/api/auth/change-password", headers=hdr,
                       json={"current_password": ID_PASSWORD, "new_password": ID_NEW_PASSWORD})
    assert r.status_code == 429
    assert deps.verify_password(ID_PASSWORD, id_find("users", {"id": cust["id"]}, {"_id": 0, "password": 1})["password"])


def test_mfa_reauth_password_guess_is_throttled(id_client):
    cust = id_seed_user(kind="customer")
    # 5 dk'dan eski oturum → şifreyle yeniden doğrulama yolu
    tok = id_token(cust, recent=False)
    hdr = {**id_auth(tok), **id_ip("198.51.100.67")}
    codes = [id_client.post("/api/auth/mfa/channel", headers=hdr,
                            json={"channel": "email", "current_password": f"yanlis-{i}"}).status_code
             for i in range(7)]
    assert 429 in codes, f"yeniden doğrulama şifre tahmini sınırsız: {codes}"
    r = id_client.post("/api/auth/mfa/channel", headers=hdr,
                       json={"channel": "email", "current_password": ID_PASSWORD})
    assert r.status_code == 429


# ════════════════════════════════ ID3-06 ════════════════════════════════
from test_identity_google import google, _g  # noqa: E402,F401  (fixture + yardımcı)


async def _all_otps():
    return await deps.db.password_reset_otps.find({}, {"_id": 0}).to_list(100)


def test_prehijacked_account_phone_cannot_reset_after_owner_links_google(id_client, google):
    """Saldırgan, kurbanın e-postası + KENDİ telefonuyla hesabı önceden açar. Kurban daha sonra Google
    ile girince hesap güvenceye alınır (şifre rastgele, oturumlar düşer). Saldırgan kendi numarasına
    SMS sıfırlama kodu isteyip kurbanın artık kullandığı hesaba geri girememeli."""
    attacker_phone = "905321234567"
    pre = id_seed_user(kind="customer", email="kurban.google@example.test", email_verified=False,
                       phone=attacker_phone)
    _plant_email_reset_token(pre, "prehijack-held-token-000001")
    r = _g(id_client, google("cred-ph", sub="g-sub-ph-1", email="kurban.google@example.test"))
    assert r.status_code == 200, r.text
    u = id_find("users", {"id": pre["id"]})
    assert u["email_verified"] is True
    assert u.get("phone") == attacker_phone, "telefon silinmemeli (yalnız doğrulanmamış işaretlenir)"
    assert u.get("phone_verified") is False
    # Saldırganın elindeki bekleyen sıfırlama token'ı öldü
    dead = id_client.post("/api/auth/forgot-password/reset",
                          json={"reset_token": "prehijack-held-token-000001", "new_password": "Sald1rgan!Pw#9"})
    assert dead.status_code == 400, dead.text
    # Saldırganın numarasına giden SMS sıfırlama artık bu hesaba bağlanmaz
    rq = id_client.post("/api/auth/forgot-password/request-otp", json={"phone": attacker_phone},
                        headers=id_ip("198.51.100.68"))
    assert rq.status_code == 200
    recs = [x for x in id_run(_all_otps()) if x.get("phone") == attacker_phone]
    assert recs and all(not x.get("user_id") for x in recs), recs
