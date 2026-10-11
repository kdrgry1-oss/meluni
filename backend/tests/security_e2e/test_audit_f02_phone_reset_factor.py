"""Denetim #2: kayıtta OTP'siz alınan telefon ve sosyal bağlamayla sahibi kesinleşen hesaptaki
(şifreli saklanan) telefon SMS şifre sıfırlama hedefi olamaz."""
import routes.auth as auth_mod
from routes.deps import secure_social_link
from security import contact_pii as _contact
from conftest import kv_run, kv_ip

PHONE = "905321112233"


def _reset_target(db):
    return kv_run(db.users.find_one({**_contact.phone_lookup("phone", PHONE),
                                     **auth_mod._phone_reset_trusted_filter()}, {"_id": 0, "id": 1}))


def test_register_phone_is_unverified_and_not_reset_target(kv_client, kv_db):
    r = kv_client.post("/api/auth/register", json={
        "email": "victim@example.com", "password": "Passw0rd!x", "first_name": "A", "last_name": "B",
        "phone": "0532 111 22 33"}, headers=kv_ip())
    assert r.status_code == 200, r.text
    u = kv_run(kv_db.users.find_one({"email": "victim@example.com"}))
    assert u["phone_verified"] is False
    assert "phone" not in u or not u.get("phone")          # şifreli saklanır
    assert _reset_target(kv_db) is None


def test_social_link_marks_sealed_phone_unverified(kv_db):
    doc = _contact.seal_doc({"id": "u1", "email": "v@example.com", "phone": PHONE, "is_admin": False,
                             "email_verified": False, "password": "x", "token_version": 0}, "user")
    assert not doc.get("phone") and doc.get("phone_enc")
    kv_run(kv_db.users.insert_one(dict(doc)))
    assert _reset_target(kv_db) is not None                 # eski (alansız) hesap: geriye uyumlu
    u = kv_run(kv_db.users.find_one({"id": "u1"}, {"_id": 0}))
    kv_run(secure_social_link(u, "google"))
    u2 = kv_run(kv_db.users.find_one({"id": "u1"}))
    assert u2["phone_verified"] is False and u2["email_verified"] is True
    assert _reset_target(kv_db) is None


def test_legacy_social_secured_account_needs_explicit_verification(kv_db):
    doc = _contact.seal_doc({"id": "u2", "email": "w@example.com", "phone": PHONE,
                             "social_link_secured_at": "2026-01-01T00:00:00+00:00"}, "user")
    kv_run(kv_db.users.insert_one(dict(doc)))
    assert _reset_target(kv_db) is None                     # bağlama öncesi kalan numara güvenilmez
    kv_run(kv_db.users.update_one({"id": "u2"}, {"$set": {"phone_verified": True}}))
    assert _reset_target(kv_db) is not None                 # sonradan OTP ile doğrulandıysa güvenilir
