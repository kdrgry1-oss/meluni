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

from .deps import db, require_admin, require_auth, logger


# --------------- ABANDONED CART ---------------

cart_router = APIRouter(prefix="/cart", tags=["cart-tracking"])
admin_cart_router = APIRouter(prefix="/admin/abandoned-carts", tags=["admin-abandoned-carts"])


@cart_router.post("/track")
async def track_cart(payload: dict):
    """Storefront saves live cart. Called on add/remove/update. Anonymous OK.
    Payload: { session_id, user_id?, items:[{product_id,name,qty,price,image}], total, email?, phone? }
    """
    sid = payload.get("session_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "session_id": sid,
        "user_id": payload.get("user_id"),
        "email": payload.get("email", ""),
        "phone": payload.get("phone", ""),
        "items": payload.get("items", []),
        "total": float(payload.get("total", 0) or 0),
        "updated_at": now,
    }
    await db.cart_sessions.update_one(
        {"session_id": sid},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"session_id": sid}


@cart_router.post("/mark-ordered")
async def mark_cart_ordered(payload: dict):
    """Remove session from abandoned pool after successful order."""
    sid = payload.get("session_id")
    if sid:
        await db.cart_sessions.delete_one({"session_id": sid})
    return {"success": True}


@admin_cart_router.get("")
async def list_abandoned(
    hours: int = Query(1, ge=1, le=720),
    current_user: dict = Depends(require_admin),
):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    q = {"updated_at": {"$lte": cutoff}, "total": {"$gt": 0}, "$expr": {"$gt": [{"$size": {"$ifNull": ["$items", []]}}, 0]}}
    rows = await db.cart_sessions.find(q, {"_id": 0}).sort("updated_at", -1).to_list(300)
    # Enrich with user info where possible
    for r in rows:
        if r.get("user_id"):
            u = await db.users.find_one({"id": r["user_id"]}, {"_id": 0, "email": 1, "first_name": 1, "last_name": 1, "phone": 1})
            if u:
                r["user"] = u
    total_value = round(sum(r.get("total", 0) for r in rows), 2)
    return {"items": rows, "total": len(rows), "total_value": total_value}


@admin_cart_router.delete("/{sid}")
async def delete_abandoned(sid: str, current_user: dict = Depends(require_admin)):
    await db.cart_sessions.delete_one({"session_id": sid})
    return {"success": True}


# --------------- PRODUCT REVIEWS ---------------

reviews_public_router = APIRouter(prefix="/reviews", tags=["reviews"])
reviews_admin_router = APIRouter(prefix="/admin/reviews", tags=["admin-reviews"])


@reviews_public_router.post("")
async def submit_review(payload: dict, current_user: dict = Depends(require_auth)):
    pid = payload.get("product_id")
    rating = int(payload.get("rating", 0) or 0)
    if not pid or rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="Ürün ID ve 1-5 arası puan gerekli")
    doc = {
        "id": str(uuid.uuid4()),
        "product_id": pid,
        "user_id": current_user.get("id"),
        "user_name": f"{current_user.get('first_name','')} {current_user.get('last_name','')}".strip() or current_user.get("email", ""),
        "rating": rating,
        "title": payload.get("title", "")[:120],
        "comment": payload.get("comment", "")[:2000],
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
    current_user: dict = Depends(require_admin),
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
async def update_review(rid: str, payload: dict, current_user: dict = Depends(require_admin)):
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
        update["admin_reply"] = payload["admin_reply"][:2000]
    res = await db.reviews.update_one({"id": rid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Yorum bulunamadı")
    return {"success": True}


@reviews_admin_router.delete("/{rid}")
async def delete_review(rid: str, current_user: dict = Depends(require_admin)):
    res = await db.reviews.delete_one({"id": rid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Yorum bulunamadı")
    return {"success": True}


# --------------- SEO: 301 REDIRECTS + META OVERRIDES ---------------

seo_public_router = APIRouter(prefix="/seo", tags=["seo"])
seo_admin_router = APIRouter(prefix="/admin/seo", tags=["admin-seo"])


@seo_admin_router.get("/redirects")
async def list_redirects(current_user: dict = Depends(require_admin)):
    items = await db.seo_redirects.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"items": items}


@seo_admin_router.post("/redirects")
async def create_redirect(payload: dict, current_user: dict = Depends(require_admin)):
    from_path = (payload.get("from_path") or "").strip()
    to_path = (payload.get("to_path") or "").strip()
    if not from_path or not to_path:
        raise HTTPException(status_code=400, detail="from_path ve to_path gerekli")
    if not from_path.startswith("/"):
        from_path = "/" + from_path
    if not to_path.startswith("/") and not to_path.startswith("http"):
        to_path = "/" + to_path
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
async def delete_redirect(rid: str, current_user: dict = Depends(require_admin)):
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
    # Increment hit counter async
    try:
        await db.seo_redirects.update_one({"id": r["id"]}, {"$inc": {"hits": 1}})
    except Exception:
        pass
    return {"found": True, "to": r["to_path"], "status_code": r["status_code"]}


@seo_admin_router.get("/meta")
async def get_meta_list(current_user: dict = Depends(require_admin)):
    items = await db.seo_meta.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"items": items}


@seo_admin_router.post("/meta")
async def upsert_meta(payload: dict, current_user: dict = Depends(require_admin)):
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
async def delete_meta(mid: str, current_user: dict = Depends(require_admin)):
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
