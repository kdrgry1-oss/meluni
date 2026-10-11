"""
Abandoned Cart (Terkedilmiş Sepet) + Product Reviews + SEO Redirects.

Three lightweight modules packed together to save routes:

1. ABANDONED CART
   - Public: POST /api/cart/track     – storefront saves current cart state per session
   - Admin : GET  /api/admin/abandoned-carts  – list pending (>1h old, no order)

2. PRODUCT REVIEWS
   - Public: POST /api/reviews                      – submit (authenticated user)
   - Public: GET  /api/reviews/product/{pid}        – approved reviews for a product
   - Admin : GET  /api/admin/reviews?status=pending|approved|rejected
   - Admin : PUT  /api/admin/reviews/{rid}          – set status

3. SEO REDIRECTS
   - Admin : CRUD /api/admin/redirects (from_path, to_path, status_code 301/302)
   - Public: GET /api/seo/resolve-redirect?path=... – storefront calls on 404
   - Admin : GET /api/admin/seo/meta-overrides / PUT (product/category meta overrides)
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid
import math
import os
import re as _re

from fastapi import Request
from .deps import db, require_admin, require_auth, logger, safe_str, get_current_user, limiter, require_permission
from tenant_config import TenantConfig, get_tenant_config, invalidate as invalidate_tenant_config
from seo_runtime import resolve_product, resolve_category


# --------------- ABANDONED CART ---------------

cart_router = APIRouter(prefix="/cart", tags=["cart-tracking"])
admin_cart_router = APIRouter(prefix="/admin/abandoned-carts", tags=["admin-abandoned-carts"])


@cart_router.post("/track")
@(limiter.limit("60/minute") if limiter else (lambda f: f))
async def track_cart(payload: dict, request: Request,
                     current_user: dict = Depends(get_current_user)):
    """Storefront saves live cart. Called on add/remove/update. Anonymous OK.
    Payload: { session_id, items:[{product_id,name,qty,price,image}], total, email?, phone? }
    GÜVENLİK (DENETİM SEC-4 F1): session_id/email/phone artık safe_str ile string'e zorlanır
    ({"$ne":null} gibi NoSQL operatör enjeksiyonu ile başka müşterinin sepetini ezme engellendi);
    user_id İSTEMCİDEN DEĞİL yalnız doğrulanmış JWT'den alınır (rastgele hesaba sepet bağlama engeli)."""
    sid = safe_str(payload.get("session_id") or "", 80) or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    _raw_items = payload.get("items")
    if not isinstance(_raw_items, list):
        _raw_items = []
    # Kalemler şekle zorlanır (terkedilmiş sepet e-postası yalnız product_id/variant_id/adet'i
    # kullanır; ad/fiyat/görsel e-postada CANLI üründen okunur — istemci değerine güvenilmez).
    _items = []
    for _it in _raw_items[:100]:
        if not isinstance(_it, dict):
            continue
        try:
            _q = max(1, min(int(_it.get("qty") or 1), 99))
        except Exception:
            _q = 1
        try:
            _pr = float(_it.get("price") or 0)
            if not math.isfinite(_pr):
                _pr = 0.0
        except Exception:
            _pr = 0.0
        _items.append({
            "product_id": safe_str(_it.get("product_id") or "", 64),
            "variant_id": safe_str(_it.get("variant_id") or "", 64) or None,
            "name": safe_str(_it.get("name") or "", 200),
            "size": safe_str(_it.get("size") or "", 40),
            "qty": _q, "price": _pr,
            "image": safe_str(_it.get("image") or "", 500),
        })
    doc = {
        "session_id": sid,
        # user_id yalnız token'dan (istemci iddiasına GÜVENİLMEZ)
        "user_id": (current_user or {}).get("id"),
        "email": safe_str(payload.get("email") or "", 200),
        "phone": safe_str(payload.get("phone") or "", 32),
        "items": _items[:100],
        # Toplam SUNUCUDA kalemlerden (istemcinin "Infinity"/NaN değeri admin listesini 500'e düşürüyordu)
        "total": round(sum(max(0.0, min(_i["price"], 1e7)) * _i["qty"] for _i in _items
                           if _i["price"] == _i["price"]), 2) if _items else 0.0,
        "updated_at": now,
    }
    await db.cart_sessions.update_one(
        {"session_id": sid},
        # Yeni sepet hareketi = yeni terk döngüsü: önceki e-posta/atlanma işaretleri sıfırlanır
        # (aynı üyeye tekrar gönderim yine de İşletme Kuralları'ndaki bekleme süresiyle sınırlı).
        {"$set": doc, "$setOnInsert": {"created_at": now},
         "$unset": {"abandoned_email_sent": "", "abandoned_email_skip": "", "abandoned_email_attempts": ""}},
        upsert=True,
    )
    return {"session_id": sid}


@cart_router.post("/mark-ordered")
@(limiter.limit("60/minute") if limiter else (lambda f: f))
async def mark_cart_ordered(payload: dict, request: Request):
    """Remove session from abandoned pool after successful order."""
    sid = safe_str(payload.get("session_id") or "", 80)  # SEC-4 F1: operatör enjeksiyonu engeli
    if sid:
        await db.cart_sessions.delete_one({"session_id": sid})
    return {"success": True}


@admin_cart_router.get("")
async def list_abandoned(
    hours: int = Query(1, ge=1, le=720),
    current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True)),
):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    q = {"updated_at": {"$lte": cutoff}, "total": {"$gt": 0}, "$expr": {"$gt": [{"$size": {"$ifNull": ["$items", []]}}, 0]}}
    rows = await db.cart_sessions.find(q, {"_id": 0}).sort("updated_at", -1).to_list(300)
    # Enrich with user info where possible
    from security.contact_pii import mask_order_contact as _contact_mask, masked_view as _masked_view
    for r in rows:
        if r.get("user_id"):
            u = await db.users.find_one({"id": r["user_id"]}, {"_id": 0, "email": 1, "first_name": 1, "last_name": 1,
                                                               "phone": 1, "phone_last2": 1})
            if u:
                # KVKK: üye telefonu (şifreli ya da düz) listede yalnız son 2 hane.
                u["phone"] = _masked_view(u, "phone", "phone")
                u.pop("phone_last2", None)
                r["user"] = u
        _contact_mask(r)  # sepet oturumunda bırakılan telefon maskeli
    def _fin(v):
        try:
            v = float(v or 0)
            return v if math.isfinite(v) else 0.0
        except Exception:
            return 0.0
    for r in rows:
        r["total"] = _fin(r.get("total"))
    total_value = round(sum(r["total"] for r in rows), 2)
    return {"items": rows, "total": len(rows), "total_value": total_value}


@admin_cart_router.get("/emails")
async def list_abandoned_emails(
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True)),
):
    """Gönderilen terkedilmiş sepet e-postaları + dönüşüm (panel "Gönderilen E-postalar").
    Tıklama: maildeki "Sepetime dön" linki açıldı mı. Sipariş: gönderimden sonraki 7 gün içinde
    aynı üyenin verdiği ilk sipariş (iptal edilenler ciroya sayılmaz)."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    rows = await db.abandoned_cart_emails.find({"created_at": {"$gte": since}}, {"_id": 0}) \
        .sort("created_at", -1).to_list(500)
    uids = list({r.get("user_id") for r in rows if r.get("user_id")})
    users = {}
    if uids:
        async for u in db.users.find({"id": {"$in": uids}}, {"_id": 0, "id": 1, "email": 1,
                                                            "first_name": 1, "last_name": 1}):
            users[u["id"]] = u
    orders_by_user: dict = {}
    if uids:
        first_dt = datetime.fromisoformat(min(r["created_at"] for r in rows))
        async for o in db.orders.find(
                {"user_id": {"$in": uids},
                 "$or": [{"created_at": {"$gte": first_dt.isoformat()}}, {"created_at": {"$gte": first_dt}}]},
                {"_id": 0, "user_id": 1, "order_number": 1, "total": 1, "status": 1, "created_at": 1}):
            ca = o.get("created_at")
            o["created_at"] = ca.replace(tzinfo=ca.tzinfo or timezone.utc).isoformat() if isinstance(ca, datetime) else str(ca or "")
            orders_by_user.setdefault(o["user_id"], []).append(o)
    summary = {"sent": 0, "failed": 0, "clicked": 0, "ordered": 0, "revenue": 0.0}
    for r in rows:
        u = users.get(r.get("user_id")) or {}
        r["name"] = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
        r["email"] = r.get("email") or u.get("email") or ""
        if r.get("status") == "sent":
            summary["sent"] += 1
        else:
            summary["failed"] += 1
        if r.get("clicked_at"):
            summary["clicked"] += 1
        end = (datetime.fromisoformat(r["created_at"]) + timedelta(days=7)).isoformat()
        cand = sorted((o for o in orders_by_user.get(r.get("user_id"), [])
                       if r["created_at"] <= o["created_at"] <= end), key=lambda o: o["created_at"])
        r["order"] = cand[0] if cand else None
        if r["order"] and r.get("status") == "sent":
            summary["ordered"] += 1
            if str(r["order"].get("status") or "") not in ("cancelled", "canceled", "iptal", "failed"):
                summary["revenue"] += float(r["order"].get("total") or 0)
    summary["revenue"] = round(summary["revenue"], 2)
    last = await db.settings.find_one({"id": "abandoned_cart_email_last_run"}, {"_id": 0}) or {}
    try:
        from business_rules import get_rule
        rules = {"enabled": bool(await get_rule(db, "marketing.abandoned_cart_email_enabled", True)),
                 "delay_hours": await get_rule(db, "marketing.abandoned_cart_delay_hours", 12),
                 "cooldown_days": await get_rule(db, "marketing.abandoned_cart_cooldown_days", 3)}
    except Exception:
        rules = {}
    return {"items": rows, "summary": summary, "last_run": last, "rules": rules}


@admin_cart_router.get("/emails/{email_id}/preview")
async def preview_abandoned_email(email_id: str, current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True))):
    """Gönderilen maili, gönderildiği andaki ürün/fiyatlarla yeniden çizer (önizleme)."""
    rec = await db.abandoned_cart_emails.find_one({"id": safe_str(email_id, 40)}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    import abandoned_cart_mail as _acm
    brand = await _acm._brand(db)
    items = rec.get("products") or []
    if not items:
        raise HTTPException(status_code=404, detail="Bu kayıtta ürün görüntüsü yok")
    cta = f"{brand['site_url']}/sepet?paylasim={rec.get('share_id') or ''}&hatirlatma=1"
    html = _acm.build_html(brand=brand, items=items, cta_url=cta, unsub_url="#",
                           texts={"title": rec.get("title") or ""})
    return {"subject": rec.get("subject") or "", "html": html}


@admin_cart_router.delete("/{sid}")
async def delete_abandoned(sid: str, current_user: dict = Depends(require_permission("campaigns.manage"))):
    await db.cart_sessions.delete_one({"session_id": sid})
    return {"success": True}


# --------------- PRODUCT REVIEWS ---------------

reviews_public_router = APIRouter(prefix="/reviews", tags=["reviews"])
reviews_admin_router = APIRouter(prefix="/admin/reviews", tags=["admin-reviews"])


def _review_display_name(first: str, last: str) -> str:
    """Herkese açık yorum adı: "Ad S." — e-posta ASLA görünen ad olarak kullanılmaz (denetim 2026-10)."""
    f = str(first or "").strip()
    l_ = str(last or "").strip()
    if not f:
        return "Müşteri"
    return f"{f} {l_[:1].upper()}." if l_ else f


def _public_review_name(name) -> str:
    """Eski kayıtlarda user_name e-posta olabilir → herkese açık listede maskelenir."""
    n = str(name or "").strip()
    if not n or "@" in n:
        return "Müşteri"
    return n


@reviews_public_router.post("")
@(limiter.limit("5/hour") if limiter else (lambda f: f))
async def submit_review(payload: dict, request: Request, current_user: dict = Depends(require_auth)):
    """Yorum gönder. GÜVENLİK (denetim 2026-10): IP başına saatte 5 (spam/sel koruması)."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Geçersiz istek")
    pid = safe_str((payload or {}).get("product_id"), 64).strip()
    # SHOP05: puan YALNIZ 1-5 arası TAM sayı (3.7 → 3 sessiz kırpma, True → 1 tip karışıklığı yok).
    _raw_rating = (payload or {}).get("rating", 0)
    rating = 0
    if isinstance(_raw_rating, int) and not isinstance(_raw_rating, bool):
        rating = _raw_rating
    elif isinstance(_raw_rating, float) and _raw_rating.is_integer():
        rating = int(_raw_rating)
    elif isinstance(_raw_rating, str) and _raw_rating.strip().isdigit():
        rating = int(_raw_rating.strip())
    if not pid or rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="Ürün ID ve 1-5 arası puan gerekli")
    # SHOP05 (mass-assignment): durum/onay, doğrulanmış alışveriş, yazar adı/kimliği, kaynak, tarih ve
    # admin yanıtı İSTEMCİDEN ALINMAZ — belge yalnız aşağıdaki sunucu alanlarıyla kurulur.
    doc = {
        "id": str(uuid.uuid4()),
        "product_id": pid,
        "user_id": current_user.get("id"),
        "user_name": _review_display_name(current_user.get("first_name"), current_user.get("last_name")),
        "rating": rating,
        "title": safe_str((payload or {}).get("title"), 120),
        "comment": safe_str((payload or {}).get("comment"), 2000),
        "status": "pending",  # pending | approved | rejected
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.reviews.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "review": doc, "message": "Yorumunuz moderasyon sonrası yayınlanacaktır"}


@reviews_public_router.get("/product/{pid}")
async def list_approved_reviews(pid: str, limit: int = Query(50, ge=1, le=200)):
    # 1) Müşteri yorumları (db.reviews, onaylı)
    rows = await db.reviews.find(
        {"product_id": pid, "status": "approved"}, {"_id": 0, "user_id": 0}
    ).sort("created_at", -1).to_list(limit)
    for r in rows:
        r["user_name"] = _public_review_name(r.get("user_name"))

    # 2) Trendyol'dan çekilen yorumlar (db.product_reviews, approved:True) — aynı listede göster.
    #    Şema normalize edilir: comment/rating/user_name/created_at + source=trendyol rozeti.
    ty = await db.product_reviews.find(
        {"product_id": pid, "approved": True},
        {"_id": 0, "id": 1, "rating": 1, "title": 1, "comment": 1, "user_name": 1,
         "is_verified": 1, "comment_date": 1, "created_at": 1, "source": 1},
    ).sort("comment_date", -1).to_list(limit)
    for r in ty:
        r.setdefault("user_name", "Trendyol Müşterisi")
        r["source"] = "trendyol"
        r["verified"] = bool(r.get("is_verified"))
        # created_at yoksa comment_date'i kullan (sıralama için)
        if not r.get("created_at"):
            r["created_at"] = r.get("comment_date") or ""

    merged = rows + ty
    # En yeni önce; tarih string ISO olduğundan lexicographic sıralama doğru çalışır
    merged.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    merged = merged[:limit]

    total_all = len(rows) + len(ty)
    all_ratings = [r["rating"] for r in rows] + [r.get("rating", 0) for r in ty]
    avg = round(sum(all_ratings) / len(all_ratings), 2) if all_ratings else 0
    return {"items": merged, "total": total_all, "average_rating": avg}


@reviews_admin_router.get("")
async def admin_list_reviews(
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True)),
):
    q: dict = {}
    if status:
        q["status"] = status
    total = await db.reviews.count_documents(q)
    skip = (page - 1) * limit
    rows = await db.reviews.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    # attach product name
    for r in rows:
        p = await db.products.find_one({"id": r.get("product_id")}, {"_id": 0, "name": 1})
        r["product_name"] = p.get("name") if p else "—"
    return {"items": rows, "total": total, "page": page, "pages": (total + limit - 1) // limit}


@reviews_admin_router.put("/{rid}")
async def update_review(rid: str, payload: dict, current_user: dict = Depends(require_permission("campaigns.manage"))):
    allowed_status = {"pending", "approved", "rejected"}
    new_status = payload.get("status")
    if new_status and new_status not in allowed_status:
        raise HTTPException(status_code=400, detail="Geçersiz durum")
    update = {}
    if new_status:
        update["status"] = new_status
        update["moderated_at"] = datetime.now(timezone.utc).isoformat()
        update["moderated_by"] = current_user.get("email", "")
    if "admin_reply" in payload:
        update["admin_reply"] = safe_str(payload.get("admin_reply"), 2000)
    res = await db.reviews.update_one({"id": rid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Yorum bulunamadı")
    return {"success": True}


@reviews_admin_router.delete("/{rid}")
async def delete_review(rid: str, current_user: dict = Depends(require_permission("campaigns.manage"))):
    res = await db.reviews.delete_one({"id": rid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Yorum bulunamadı")
    return {"success": True}


# --------------- SEO: 301 REDIRECTS + META OVERRIDES ---------------

seo_public_router = APIRouter(prefix="/seo", tags=["seo"])
seo_admin_router = APIRouter(prefix="/admin/seo", tags=["admin-seo"])


# SHOP09 — AÇIK YÖNLENDİRME KORUMASI: SEO yönlendirme hedefi yalnız AYNI SİTE olabilir — göreli yol
# ("/yeni-sayfa") ya da mağazanın kendi alan adı(ları)na mutlak http(s) adres. "//evil", "/\\evil",
# "javascript:" ve yabancı alan adı reddedilir (oluştururken 400; eski kayıt çözümlemede yok sayılır).
async def _site_hosts() -> set:
    """İzinli alan adları — beyaz etiket: firma ayarı (company.site_url) + ortam değişkenleri."""
    hosts = set()
    try:
        from urllib.parse import urlparse as _up
        from company import get_site_url
        _h = (_up(await get_site_url(db) or "").hostname or "").lower()
        if _h:
            hosts.add(_h)
            hosts.add(_h[4:] if _h.startswith("www.") else "www." + _h)
    except Exception:
        pass
    for _env in ("FRONTEND_PUBLIC_URL", "SITE_URL"):
        try:
            from urllib.parse import urlparse as _up
            _h = (_up(os.environ.get(_env) or "").hostname or "").lower()
            if _h:
                hosts.add(_h)
        except Exception:
            pass
    hosts.discard("")
    return hosts


def safe_same_site_target(target: str, allowed_hosts: set) -> bool:
    """Hedef aynı siteye mi gidiyor? Tarayıcıların '\\' karakterini '/' saydığı ve kontrol
    karakterlerini yuttuğu da hesaba katılır."""
    t = str(target or "")
    if not t or len(t) > 2000:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in t) or "\\" in t:
        return False
    if t.startswith("/"):
        return not t.startswith("//")
    from urllib.parse import urlparse as _up
    try:
        p = _up(t)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or p.username is not None or p.password is not None:
        return False
    host = (p.hostname or "").lower().rstrip(".")
    return bool(host) and host in {h.lower() for h in (allowed_hosts or set())}


@seo_admin_router.get("/redirects")
async def list_redirects(current_user: dict = Depends(require_permission("tasarim.view"))):
    items = await db.seo_redirects.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"items": items}


@seo_admin_router.post("/redirects")
async def create_redirect(payload: dict, current_user: dict = Depends(require_permission("tasarim.seo"))):
    from_path = (payload.get("from_path") or "").strip()
    to_path = (payload.get("to_path") or "").strip()
    if not from_path or not to_path:
        raise HTTPException(status_code=400, detail="from_path ve to_path gerekli")
    if not from_path.startswith("/"):
        from_path = "/" + from_path
    if not to_path.startswith("/") and not to_path.startswith("http"):
        to_path = "/" + to_path
    if not safe_same_site_target(to_path, await _site_hosts()):
        raise HTTPException(status_code=400, detail=(
            "Yönlendirme hedefi yalnız sitenin kendi sayfası olabilir (ör. /yeni-sayfa ya da kendi alan adınız)."))
    code = int(payload.get("status_code", 301))
    if code not in (301, 302):
        code = 301
    doc = {
        "id": str(uuid.uuid4()),
        "from_path": from_path.lower(),
        "to_path": to_path,
        "status_code": code,
        "hits": 0,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.seo_redirects.update_one({"from_path": doc["from_path"]}, {"$set": doc}, upsert=True)
    return {"success": True, "redirect": doc}


@seo_admin_router.delete("/redirects/{rid}")
async def delete_redirect(rid: str, current_user: dict = Depends(require_permission("tasarim.seo"))):
    res = await db.seo_redirects.delete_one({"id": rid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Yönlendirme bulunamadı")
    return {"success": True}


@seo_public_router.get("/resolve-redirect")
async def resolve_redirect(path: str = Query(..., min_length=1)):
    """Storefront calls this on 404 to see if a 301/302 applies."""
    p = path.lower()
    if not p.startswith("/"):
        p = "/" + p
    r = await db.seo_redirects.find_one({"from_path": p, "is_active": True}, {"_id": 0})
    if not r:
        return {"found": False}
    if not safe_same_site_target(r.get("to_path") or "", await _site_hosts()):
        # SHOP09: kayıt silinmez/değiştirilmez (kullanıcı verisi) — yalnız güvensiz hedefe GİDİLMEZ.
        logger.warning(f"[seo-redirect] güvensiz hedef yok sayıldı from={p}")
        return {"found": False}
    # Increment hit counter async
    try:
        await db.seo_redirects.update_one({"id": r["id"]}, {"$inc": {"hits": 1}})
    except Exception:
        pass
    return {"found": True, "to": r["to_path"], "status_code": r["status_code"]}


@seo_admin_router.get("/meta")
async def get_meta_list(current_user: dict = Depends(require_permission("tasarim.view"))):
    items = await db.seo_meta.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"items": items}


@seo_admin_router.post("/meta")
async def upsert_meta(payload: dict, current_user: dict = Depends(require_permission("tasarim.seo"))):
    """Per-path meta override. { path, title, description, og_image, noindex }"""
    path = (payload.get("path") or "").strip().lower()
    if not path:
        raise HTTPException(status_code=400, detail="path gerekli")
    if not path.startswith("/"):
        path = "/" + path
    doc = {
        "id": str(uuid.uuid4()),
        "path": path,
        "title": payload.get("title", "")[:200],
        "description": payload.get("description", "")[:400],
        "og_image": payload.get("og_image", ""),
        "noindex": bool(payload.get("noindex", False)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.seo_meta.update_one({"path": path}, {"$set": doc, "$setOnInsert": {"created_at": doc["updated_at"]}}, upsert=True)
    return {"success": True}


@seo_admin_router.delete("/meta/{mid}")
async def delete_meta(mid: str, current_user: dict = Depends(require_permission("tasarim.seo"))):
    res = await db.seo_meta.delete_one({"id": mid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Meta bulunamadı")
    return {"success": True}


@seo_public_router.get("/meta")
async def get_public_meta(path: str):
    p = path.lower()
    if not p.startswith("/"):
        p = "/" + p
    doc = await db.seo_meta.find_one({"path": p}, {"_id": 0})
    return {"found": bool(doc), "meta": doc}


@seo_public_router.get("/runtime-config")
async def seo_runtime_config():
    """Public, non-secret company/SEO values used by SPA navigation."""
    cfg = await get_tenant_config(db)
    # Explicit projection: banking/tax identifiers and secret_refs must never
    # become public merely because the canonical schema later grows.
    return {
        "brand": {k: cfg["brand"].get(k) for k in ("store_name", "logo_url", "favicon_url")},
        "company": {k: cfg["company"].get(k) for k in
                    ("legal_name", "address", "city", "district", "country")},
        "contact": {k: cfg["contact"].get(k) for k in
                    ("email", "support_email", "phone", "whatsapp", "instagram", "facebook", "x", "tiktok")},
        "domains": {k: cfg["domains"].get(k) for k in ("storefront_url", "cdn_url")},
        "commerce": {k: cfg["commerce"].get(k) for k in
                     ("currency_code", "currency_symbol", "locale", "prices_include_vat")},
        "seo_geo": cfg["seo_geo"],
    }


@seo_admin_router.get("/config")
async def get_seo_config(current_user: dict = Depends(require_permission("tasarim.view"))):
    cfg = await get_tenant_config(db, use_cache=False)
    return cfg["seo_geo"]


@seo_admin_router.put("/config")
async def save_seo_config(payload: dict, current_user: dict = Depends(require_permission("tasarim.seo"))):
    cfg = await get_tenant_config(db, use_cache=False)
    merged = {**cfg.get("seo_geo", {}), **(payload or {})}
    candidate = TenantConfig.model_validate({**cfg, "seo_geo": merged}).model_dump()
    await db.settings.update_one({"id": "tenant_config"}, {"$set": candidate}, upsert=True)
    invalidate_tenant_config(db)
    return {"success": True, "seo_geo": candidate["seo_geo"]}


async def _seo_preview_rows(kind: str, limit: int = 100):
    cfg = await get_tenant_config(db)
    rows = []
    if kind in ("product", "all"):
        async for item in db.products.find(
            {"is_active": {"$ne": False}, "is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "slug": 1, "description": 1,
             "seo_description": 1, "meta_title": 1, "meta_description": 1,
             "images": 1, "image": 1, "price": 1, "sale_price": 1, "stock": 1,
             "variants": 1, "brand": 1, "barcode": 1, "stock_code": 1,
             "category_name": 1, "category_slug": 1},
        ).limit(limit):
            resolved = resolve_product(item, cfg)
            rows.append({"kind": "product", "id": item.get("id"), "name": item.get("name"),
                         "current": {"title": item.get("meta_title") or "",
                                     "description": item.get("meta_description") or ""},
                         "generated": {k: resolved.get(k) for k in ("title", "description", "canonical", "og_image", "robots", "sources")},
                         "would_change": bool((not item.get("meta_title") and resolved.get("title")) or
                                              (not item.get("meta_description") and resolved.get("description")))})
    if kind in ("category", "all"):
        async for item in db.categories.find(
            {"is_active": {"$ne": False}, "members_only": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "slug": 1, "description": 1,
             "meta_title": 1, "meta_description": 1, "image": 1},
        ).limit(limit):
            resolved = resolve_category(item, cfg)
            rows.append({"kind": "category", "id": item.get("id"), "name": item.get("name"),
                         "current": {"title": item.get("meta_title") or "",
                                     "description": item.get("meta_description") or ""},
                         "generated": {k: resolved.get(k) for k in ("title", "description", "canonical", "og_image", "robots", "sources")},
                         "would_change": bool((not item.get("meta_title") and resolved.get("title")) or
                                              (not item.get("meta_description") and resolved.get("description")))})
    return rows


@seo_admin_router.get("/bulk-preview")
async def seo_bulk_preview(kind: str = Query("all"),
                           limit: int = Query(100, ge=1, le=1000),
                           current_user: dict = Depends(require_permission("tasarim.view"))):
    if kind not in ("all", "product", "category"):
        raise HTTPException(status_code=400, detail="kind geçersiz")
    rows = await _seo_preview_rows(kind, limit)
    return {"dry_run": True, "apply_executed": False, "count": len(rows), "items": rows}


@seo_admin_router.post("/bulk-apply")
async def seo_bulk_apply(payload: dict, current_user: dict = Depends(require_permission("tasarim.seo"))):
    """Explicit snapshot apply; never overwrites manual title/description."""
    if (payload or {}).get("confirmation") != "APPLY_GENERATED_SEO":
        raise HTTPException(status_code=400, detail="confirmation=APPLY_GENERATED_SEO gerekli")
    kind = (payload or {}).get("kind", "all")
    if kind not in ("all", "product", "category"):
        raise HTTPException(status_code=400, detail="kind geçersiz")
    try:
        limit = int((payload or {}).get("limit") or 100)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="limit geçersiz")
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=400, detail="limit 1-1000 arasında olmalı")
    rows = await _seo_preview_rows(kind, limit)
    updated = 0
    for row in rows:
        if row.get("id") is None:
            continue
        generated = row["generated"]
        collection = db.products if row["kind"] == "product" else db.categories
        # Separate conditional writes prevent a concurrent manual edit in one
        # field being overwritten while the other field is still empty.
        changed = False
        if not row["current"]["title"] and generated.get("title"):
            result = await collection.update_one(
                {"id": row["id"], "meta_title": {"$in": [None, ""]}},
                {"$set": {"meta_title": generated["title"]}},
            )
            changed = changed or bool(result.modified_count)
        if not row["current"]["description"] and generated.get("description"):
            result = await collection.update_one(
                {"id": row["id"], "meta_description": {"$in": [None, ""]}},
                {"$set": {"meta_description": generated["description"]}},
            )
            changed = changed or bool(result.modified_count)
        updated += int(changed)
    return {"success": True, "updated": updated, "manual_overrides_preserved": True}


# =============================================================================
# EDGE SEO — per-sayfa meta çözümleyici (functions/_middleware.js buradan okur)
# =============================================================================
_FRONT_URL = (os.environ.get("FRONTEND_PUBLIC_URL") or "").rstrip("/")
# Ürün/kategori OLMAYAN, indekslenmeyen veya statik kök yollar → soft-404 taraması yapma.
_SEO_NONINDEX_PREFIXES = ("/sepet", "/odeme", "/checkout", "/hesabim", "/account", "/admin",
                          "/giris", "/login", "/kayit", "/register", "/order-success",
                          "/siparis", "/sifremi", "/reset", "/iade", "/return")
_SEO_KNOWN_STATIC = {"/", "/hakkimizda", "/iletisim", "/sss", "/kvkk", "/gizlilik"}


@seo_public_router.get("/page-meta")
async def seo_page_meta(path: str = Query("/", max_length=512)):
    """Bir storefront yolu için title/description/canonical/OG + (ürün) JSON-LD döndürür.
    Cloudflare Pages edge middleware (functions/_middleware.js) her HTML isteğinde çağırır
    ve dönen meta'yı ilk HTML'e enjekte eder → ürün/kategori sayfaları Google'da doğru
    başlık/açıklama/canonical ile indekslenir (eskiden HEPSİ ana sayfaya canonical'lıydı).
    Savunmacı: her hata found:false döner (middleware ilk HTML'i olduğu gibi bırakır)."""
    try:
        raw = path or "/"
        # yalnız path kısmı, query/fragment at
        raw = raw.split("?", 1)[0].split("#", 1)[0]
        if not raw.startswith("/"):
            raw = "/" + raw
        p = raw.rstrip("/") or "/"
        low = p.lower()
        cfg = await get_tenant_config(db)
        site = (cfg["domains"].get("storefront_url") or _FRONT_URL).rstrip("/")

        # Admin override varsa öncelik (mevcut seo_meta)
        override = await db.seo_meta.find_one({"path": low}, {"_id": 0})

        # İndekslenmeyen/panel yolları → noindex (soft-404 + özel alan koruması)
        if any(low == pre or low.startswith(pre + "/") or low.startswith(pre) for pre in _SEO_NONINDEX_PREFIXES):
            return {"found": True, "robots": "noindex,follow", "canonical": f"{site}{p}"}

        # Ana sayfa / bilinen statikler use canonical global settings at runtime.
        if low in _SEO_KNOWN_STATIC:
            seo = cfg["seo_geo"]
            canonical = f"{site}/" if low == "/" else f"{site}{p}"
            m = {"found": True, "robots": seo.get("default_robots") or "index,follow",
                 "canonical": canonical, "title": seo.get("default_title") or "",
                 "description": seo.get("default_description") or "",
                 "og_title": seo.get("default_title") or "",
                 "og_description": seo.get("default_description") or "",
                 "og_url": canonical, "og_image": seo.get("default_og_image_url") or "",
                 "og_type": "website", "og_site_name": cfg["brand"].get("store_name") or "",
                 "og_locale": seo.get("locale") or ""}
            if low == "/":
                address = {"@type": "PostalAddress",
                           "streetAddress": cfg["company"].get("address") or None,
                           "addressLocality": cfg["company"].get("district") or cfg["company"].get("city") or None,
                           "addressRegion": cfg["company"].get("city") or None,
                           "addressCountry": cfg["company"].get("country") or None}
                address = {k: v for k, v in address.items() if v not in (None, "")}
                same_as = [cfg["contact"].get(key) for key in
                           ("instagram", "facebook", "x", "tiktok") if cfg["contact"].get(key)]
                org = {"@context": "https://schema.org", "@type": "Organization",
                       "name": cfg["brand"].get("store_name"), "url": site,
                       "legalName": cfg["company"].get("legal_name") or None,
                       "logo": cfg["brand"].get("logo_url") or None,
                       "description": seo.get("organization_description") or None,
                       "email": cfg["contact"].get("email") or None,
                       "telephone": cfg["contact"].get("phone") or None,
                       "address": address if len(address) > 1 else None,
                       "sameAs": same_as or None}
                m["jsonld"] = [{k: v for k, v in org.items() if v not in (None, "")}]
            if override:
                if override.get("title"):
                    m["title"] = m["og_title"] = override["title"]
                if override.get("description"):
                    m["description"] = m["og_description"] = override["description"]
                if override.get("og_image"):
                    m["og_image"] = override["og_image"]
                if override.get("noindex"):
                    m["robots"] = "noindex,nofollow"
            return m

        store = cfg["brand"].get("store_name") or ""

        def _simple(title_core: str, desc: str, canonical_path: str) -> dict:
            canonical = f"{site}{canonical_path}"
            title = f"{title_core} | {store}" if store else title_core
            m = {"found": True, "type": "page", "robots": cfg["seo_geo"].get("default_robots") or "index,follow",
                 "canonical": canonical, "title": title, "description": desc, "og_title": title,
                 "og_description": desc, "og_url": canonical, "og_type": "website",
                 "og_site_name": store, "og_locale": cfg["seo_geo"].get("locale") or ""}
            if override:
                if override.get("title"):
                    m["title"] = m["og_title"] = override["title"]
                if override.get("description"):
                    m["description"] = m["og_description"] = override["description"]
                if override.get("noindex"):
                    m["robots"] = "noindex,nofollow"
            return m

        # Gerçek mağaza sayfaları — eskiden soft-404 sayılıp noindex alıyordu (Hakkımızda, İletişim,
        # SSS, İndirim). Bunlar Google'da görünmesi gereken sayfalardır.
        if low in ("/sikca-sorulan-sorular", "/sss"):
            return _simple("Sıkça Sorulan Sorular",
                           f"{store} sipariş, kargo, iade ve üyelik hakkında sıkça sorulan sorular.".strip(),
                           "/sikca-sorulan-sorular")
        if low == "/sale":
            return _simple("İndirim", f"{store} indirimli ürünler ve özel fiyatlar.".strip(), "/sale")
        mpage = _re.match(r"^/sayfa/([^/]+)$", low)
        if mpage:
            pg = await db.pages.find_one({"slug": mpage.group(1), "is_active": {"$ne": False}},
                                         {"_id": 0, "title": 1, "slug": 1, "content": 1,
                                          "meta_title": 1, "meta_description": 1})
            if pg:
                _txt = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", str(pg.get("content") or ""))).strip()
                _desc = (pg.get("meta_description") or _txt[:155]).strip()
                m = _simple(pg.get("title") or mpage.group(1), _desc, f"/sayfa/{pg.get('slug')}")
                if pg.get("meta_title"):
                    m["title"] = m["og_title"] = pg["meta_title"]
                return m

        # Slug çöz: /urun/{slug} veya /{slug}
        slug = None
        mprod = _re.match(r"^/urun/([^/]+)$", p)
        mcat = _re.match(r"^/kategori/([^/]+)$", p)
        if mprod:
            slug = mprod.group(1)
        elif mcat:
            slug = p.split("/", 2)[2]
        elif _re.match(r"^/[^/]+$", p):
            slug = p[1:]
        if not slug:
            # çok segmentli bilinmeyen yol → noindex
            return {"found": True, "robots": "noindex,follow", "canonical": f"{site}{p}"}

        slug_l = slug.lower()

        # 1) ÜRÜN dene (aktif + silinmemiş; üyeye-özel değil)
        prod = None
        if not mcat:
            cands = await db.products.find(
                {"$or": [{"slug": slug_l}, {"slug": slug}, {"slug_aliases": slug_l}, {"id": slug}]},
                {"_id": 0, "name": 1, "slug": 1, "id": 1, "description": 1, "seo_description": 1,
                 "meta_description": 1, "meta_title": 1, "images": 1, "image": 1, "price": 1,
                 "sale_price": 1, "brand": 1, "barcode": 1, "stock_code": 1, "variants": 1,
                 "category_name": 1, "category_slug": 1, "stock": 1, "is_active": 1, "is_deleted": 1,
                 "members_only": 1, "category_id": 1, "category_ids": 1, "categories": 1}).to_list(10)
            prod = (next((c for c in cands if c.get("is_active") is not False and not c.get("is_deleted")), None)
                    or None)
        from .products import _members_only_cat_ids, product_is_members_only
        _mo_meta = await _members_only_cat_ids()
        if prod and not prod.get("members_only") and not product_is_members_only(prod, _mo_meta):
            return resolve_product(prod, cfg, override=override)

        # 2) KATEGORİ dene
        cat = await db.categories.find_one(
            {"$or": [{"slug": slug_l}, {"slug": slug}, {"slug_aliases": slug_l}]},
            {"_id": 0, "name": 1, "slug": 1, "description": 1, "meta_title": 1,
             "meta_description": 1, "image": 1, "members_only": 1, "id": 1})
        if cat and not cat.get("members_only") and str(cat.get("id")) not in _mo_meta:
            return resolve_category(cat, cfg, override=override)

        # 3) Ne ürün ne kategori → soft-404: noindex (Google indeks bütçesi boşa gitmesin)
        return {"found": True, "robots": "noindex,follow", "canonical": f"{site}{p}"}
    except Exception as e:
        logger.warning(f"[seo] page-meta çözümlenemedi path={path}: {e}")
        return {"found": False}
