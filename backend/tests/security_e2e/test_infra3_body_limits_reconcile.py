"""INFRA-03 (2026-10 denetim #2) — istek gövdesi tavanı + mutabakat penceresi açlığı.

1) Gövde tavanı (BodySizeLimitMiddleware):
   * Eski sınır yalnız Content-Length başlığına bakıyordu (120 MB, herkese). Cloudflare 100 MB'a
     kadar gövdeyi geçirir → kimliksiz biri herkese açık bir JSON ucuna (ör. /api/products/
     cart-pricing, /api/orders) ~100 MB JSON yollayıp json ayrıştırmasıyla belleği tüketebiliyordu.
   * Content-Length'siz (chunked) gövde tavanı tamamen atlıyordu.
   Yeni kural: personel (geçerli imzalı staff JWT) eski 120 MB tavanını korur; multipart (dosya
   yükleme — Starlette diske taşır) eski tavanda kalır; webhook uçları orta tavan; diğer her
   (kimliksiz/müşteri) JSON/form gövdesi küçük tavana tabidir. Chunked gövde akış sırasında
   sayılır ve tavan aşılınca 413 döner.

2) Mutabakat (reconcile_charged_but_unrecorded_orders) aday penceresi:
   * Aday sorgusu en yeni 50 siparişi alıyor, paymentId'si OLMAYANLARI döngüde atlıyordu. Ödeme
     denemesi hiç yapılmamış (paymentId'siz) yeni siparişler 50'lik pencereyi doldurunca, kartı
     GERÇEKTEN çekilmiş (needs_reconciliation + paymentId) daha eski sipariş hiç denetlenmiyordu
     (misafir checkout ile ucuzca doldurulabilir). Artık sorgu yalnız paymentId taşıyanları alır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import kv_run


# ─────────────────────────────── 1) gövde tavanı ───────────────────────────────
def _json_blob(n_bytes: int) -> bytes:
    pad = "x" * max(0, n_bytes - 20)
    return ('{"items":[],"pad":"' + pad + '"}').encode()


def _chunks(data: bytes, size: int = 64 * 1024):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def test_anonymous_large_json_rejected_413(kv_client):
    body = _json_blob(2 * 1024 * 1024)
    r = kv_client.post("/api/products/cart-pricing", content=body,
                       headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_anonymous_chunked_large_json_rejected_413(kv_client):
    body = _json_blob(2 * 1024 * 1024)
    r = kv_client.post("/api/products/cart-pricing", content=_chunks(body),
                       headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_customer_token_large_json_rejected_413(kv_client, kv_users):
    c = kv_users.customer()
    body = _json_blob(2 * 1024 * 1024)
    r = kv_client.post("/api/orders", content=body,
                       headers={"content-type": "application/json", **kv_users.hdr(c)})
    assert r.status_code == 413


def test_forged_staff_token_does_not_unlock_big_cap(kv_client):
    import jwt as _jwt
    forged = _jwt.encode({"user_id": "x", "is_admin": True, "typ": "staff",
                          "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                         "not-the-real-secret-0123456789abcdef", algorithm="HS256")
    body = _json_blob(2 * 1024 * 1024)
    r = kv_client.post("/api/products/cart-pricing", content=body,
                       headers={"content-type": "application/json", "Authorization": f"Bearer {forged}"})
    assert r.status_code == 413


def test_staff_large_json_still_allowed(kv_client, kv_users):
    s = kv_users.staff(super_admin=True)
    body = _json_blob(2 * 1024 * 1024)
    r = kv_client.post("/api/products/cart-pricing", content=body,
                       headers={"content-type": "application/json", **kv_users.hdr(s)})
    assert r.status_code != 413


def test_small_anonymous_json_unaffected(kv_client):
    r = kv_client.post("/api/products/cart-pricing", json={"items": []})
    assert r.status_code != 413


def test_anonymous_multipart_upload_keeps_upload_cap(kv_client):
    # Misafir dekont yükleme (multipart) küçük JSON tavanına TAKILMAZ (sipariş yok → 404/400/403).
    r = kv_client.post("/api/orders/by-number/NOPE-1/payment-notification",
                       files={"file": ("dekont.pdf", b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024), "application/pdf")},
                       data={"contact": "x@example.com"})
    assert r.status_code != 413


def test_webhook_path_gets_medium_cap(kv_client):
    body = _json_blob(2 * 1024 * 1024)
    r = kv_client.post("/api/inbound-mail", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 401  # tavan değil, paylaşımlı sır kontrolü yanıtlar


def test_global_cap_enforced_on_chunked_body(kv_client, kv_users, monkeypatch):
    import server
    monkeypatch.setattr(server.BodySizeLimitMiddleware, "MAX_BYTES", 1024 * 1024)
    s = kv_users.staff(super_admin=True)
    body = _json_blob(2 * 1024 * 1024)
    r = kv_client.post("/api/products/cart-pricing", content=_chunks(body),
                       headers={"content-type": "application/json", **kv_users.hdr(s)})
    assert r.status_code == 413


# ─────────────────────────── 2) mutabakat penceresi açlığı ───────────────────────────
def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


@pytest.fixture
def motor_to_list_length(monkeypatch):
    """mongomock_motor to_list(length) uzunluğu YOK SAYAR; gerçek Motor ilk `length` belgeyi döndürür.
    Pencere açlığını sınamak için gerçek davranışı taklit et."""
    import mongomock_motor as _mm

    async def _to_list(self, length=None, *a, **k):
        cur = self._AsyncCursor__cursor
        out = []
        for doc in cur:
            out.append(doc)
            if length and len(out) >= length:
                break
        return out

    monkeypatch.setattr(_mm.AsyncCursor, "to_list", _to_list)


def test_reconcile_not_starved_by_paymentless_orders(kv_db, monkeypatch, motor_to_list_length):
    import scheduler
    import routes.payment as payment

    async def _seed():
        # Kartı çekilmiş ama kaydedilmemiş ESKİ sipariş (paymentId var, mutabakat bekliyor)
        await kv_db.orders.insert_one({
            "id": "charged-1", "order_number": "C1", "payment_method": "credit_card",
            "payment_status": "expired", "status": "payment_failed", "needs_reconciliation": True,
            "reconcile_payment_id": "pid-charged", "created_at": _iso(hours=-2)})
        # Daha YENİ 60 adet ödeme denemesi olmayan (paymentId'siz) bekleyen kart siparişi
        await kv_db.orders.insert_many([{
            "id": f"junk-{i}", "order_number": f"J{i}", "payment_method": "credit_card",
            "payment_status": "pending", "status": "awaiting_payment",
            "created_at": _iso(minutes=-(i % 10))} for i in range(60)])
    kv_run(_seed())

    seen = []

    async def _fake_finalize(order_id, pid, *a, **k):
        seen.append((order_id, pid))
        return False

    monkeypatch.setattr(payment, "_finalize_by_payment_id", _fake_finalize)
    kv_run(scheduler.reconcile_charged_but_unrecorded_orders())
    assert ("charged-1", "pid-charged") in seen


def test_reconcile_still_reads_nested_payment_id(kv_db, monkeypatch, motor_to_list_length):
    import scheduler
    import routes.payment as payment

    async def _seed():
        await kv_db.orders.insert_one({
            "id": "nested-1", "order_number": "N1", "payment_method": "credit_card",
            "payment_status": "failed", "status": "awaiting_payment",
            "iyzico_retrieve_response": {"paymentId": "pid-nested"}, "created_at": _iso(hours=-1)})
    kv_run(_seed())
    seen = []

    async def _fake_finalize(order_id, pid, *a, **k):
        seen.append((order_id, pid))
        return False

    monkeypatch.setattr(payment, "_finalize_by_payment_id", _fake_finalize)
    kv_run(scheduler.reconcile_charged_but_unrecorded_orders())
    assert seen == [("nested-1", "pid-nested")]
