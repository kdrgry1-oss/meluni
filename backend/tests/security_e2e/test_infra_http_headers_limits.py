"""HTTP hardening: no-store on PII/identity endpoints, public tracking + guest return rate limits,
MIME header-injection safety for marketing mail."""
from __future__ import annotations

import pytest

from kv_harness import kv_order, kv_count, kv_insert, kv_iso  # noqa: F401
from conftest import KvUsers


@pytest.mark.parametrize("method,path,auth", [
    ("GET", "/api/my-addresses", True),
    ("POST", "/api/addresses", True),
    ("GET", "/api/favorites", True),
    ("GET", "/api/favorites/ids", True),
    ("GET", "/api/track/DOES-NOT-EXIST", False),
    ("GET", "/api/orders", True),
    ("GET", "/api/my-orders", True),
    ("GET", "/api/customer/my-orders", True),
    ("GET", "/api/auth/me", True),
    ("GET", "/api/orders", False),        # error responses too
    ("GET", "/api/favorites", False),
])
def test_no_store_on_sensitive_endpoints(kv_client, kv_users, method, path, auth):
    h = {"cf-connecting-ip": "198.51.100.150"}
    if auth:
        h.update(KvUsers.hdr(kv_users.customer()))
    kw = {"json": {"title": "Ev", "address": "x", "city": "Istanbul", "first_name": "A", "last_name": "B",
                   "phone": "05551112233"}} if method == "POST" else {}
    r = kv_client.request(method, path, headers=h, **kw)
    assert r.status_code < 500, r.text
    cc = r.headers.get("cache-control", "")
    assert "no-store" in cc, (path, r.status_code, cc)


def test_public_catalog_is_not_forced_no_store(kv_client):
    r = kv_client.get("/api/reviews/product/x")
    assert "no-store" not in r.headers.get("cache-control", "")


def test_track_rate_limit_20_per_minute(kv_client):
    o = kv_order(None, order_number="TRK123456", status="shipped", cargo_tracking_number="CT987",
                 email="tracked@example.com", phone="05559998877",
                 shipping_address={"first_name": "Gizli", "last_name": "Kisi", "email": "tracked@example.com",
                                   "phone": "05559998877", "address": "Gizli Sk. 5", "city": "Izmir"})
    ip = {"cf-connecting-ip": "198.51.100.160"}
    codes = [kv_client.get("/api/track/TRK123456", headers=ip).status_code for _ in range(20)]
    assert codes == [200] * 20
    r = kv_client.get("/api/track/TRK123456", headers=ip)
    assert r.status_code == 429
    # another client is unaffected
    assert kv_client.get("/api/track/TRK123456", headers={"cf-connecting-ip": "198.51.100.161"}).status_code == 200
    body = kv_client.get("/api/track/CT987", headers={"cf-connecting-ip": "198.51.100.162"}).json()
    assert "user_id" not in body
    for secret in (o["email"], "05559998877", "Gizli Sk. 5"):
        assert secret not in str(body)


_LIMITER_URL_KEY_REASON = (
    "BUG routes/deps.py: slowapi Limiter(key_func=_rate_key, default_limits=[]) uses the default "
    "key_style='url', so every distinct request PATH gets its own bucket. Endpoints whose path carries the "
    "enumerated value (/api/track/{tracking_code} 20/min, /api/orders/by-number/{order_number}/return-request "
    "5/min) are therefore limited per code, not per client: an attacker enumerating codes is never throttled. "
    "Fix: Limiter(..., key_style='endpoint') (same scope as the in-memory fallback limiter).")


def test_track_limit_also_throttles_enumeration_of_different_codes(kv_client):
    ip = {"cf-connecting-ip": "198.51.100.163"}
    codes = [kv_client.get(f"/api/track/ENUM{i:05d}", headers=ip).status_code for i in range(25)]
    assert 429 in codes, codes   # 25 different codes from ONE client within a minute


def test_guest_return_limit_also_throttles_different_order_numbers(kv_client):
    ip = {"cf-connecting-ip": "198.51.100.172"}
    codes = [kv_client.post(f"/api/orders/by-number/NOPE{i:04d}/return-request", headers=ip,
                            json={"email": "x@example.com", "reason": "x"}).status_code for i in range(8)]
    assert 429 in codes, codes


def test_spoofed_forwarding_headers_do_not_bypass_track_limit(kv_client):
    kv_order(None, order_number="TRK777", status="shipped")
    # direct-to-origin request from a public non-Cloudflare hop: XFF/cf-connecting-ip rotation must not
    # create fresh buckets (the key falls back to the last hop)
    statuses = []
    for i in range(22):
        statuses.append(kv_client.get("/api/track/TRK777", headers={
            "x-forwarded-for": f"10.0.{i}.1, 8.8.8.8", "cf-connecting-ip": f"203.0.113.{i + 1}"}).status_code)
    assert 429 in statuses


def test_guest_return_rate_limit_5_per_minute(kv_client):
    o = kv_order(None, order_number="GR100200", email="guest@example.com", status="delivered",
                 delivered_at=kv_iso(-2), user_id=None)
    ip = {"cf-connecting-ip": "198.51.100.170"}
    codes = []
    for _ in range(6):
        r = kv_client.post("/api/orders/by-number/GR100200/return-request", headers=ip,
                           json={"email": "wrong@example.com", "reason": "x"})
        codes.append(r.status_code)
    assert codes[:5] == [403] * 5, codes
    assert codes[5] == 429
    assert kv_count("customer_returns") == 0
    # per-order verification lock also kicks in (5 failures / hour) even from a fresh IP
    r = kv_client.post("/api/orders/by-number/GR100200/return-request",
                       headers={"cf-connecting-ip": "198.51.100.171"},
                       json={"email": "guest@example.com", "reason": "x"})
    assert r.status_code == 429
    assert o["order_number"] == "GR100200"


def test_marketing_mime_header_injection_neutralised():
    from email_ses import build_marketing_mime
    import email as _email
    cfg = {"from_email": "bulten@news.kv-test.invalid", "from_name": "Magaza",
           "unsubscribe_mailto": "u@news.kv-test.invalid\r\nBcc: attacker@example.com"}
    raw = build_marketing_mime(cfg, "to@example.com", "Konu", "<p>x</p>",
                               reply_to="r@example.com\r\nBcc: attacker@example.com",
                               list_unsubscribe_url="https://api.kv-test.invalid/u?e=a\r\nBcc: attacker@example.com")
    msg = _email.message_from_bytes(raw)
    assert msg.get_all("Bcc") is None
    assert "attacker@example.com" not in (msg["To"] or "")


def test_track_global_cap_stops_rotating_ip_enumeration(kv_client):
    """IP havuzu (her istekte farklı IP) kullanan numaralandırma da genel üst sınıra (120/dk) takılır."""
    codes = [kv_client.get(f"/api/track/ENUM{i:05d}",
                           headers={"cf-connecting-ip": f"198.51.{100 + i // 250}.{i % 250 + 1}"}).status_code
             for i in range(121)]
    assert 429 not in codes[:120], codes[:120]
    assert codes[120] == 429
