"""
SALE MENÜSÜ — vitrindeki SALE sekmesinin açılır listesi (masaüstünde üzerine gelince,
mobilde alt alta). Hangi kalemlerin görüneceği Admin › Kampanyalar › "SALE Menüsü"nden
yönetilir (db.settings id=sale_menu).

Kalem türleri:
  • campaign → /kampanya/{kampanya id}: kampanyanın kapsamındaki ürünler (ürün listesi
    /api/products?campaign=… ile). Kampanya pasif/süresi dolmuşsa menüde GÖRÜNMEZ.
  • link     → site içi adres (ör. /sale — İNDİRİM kategorisindeki ürünler).

GET  /api/sale-menu                  — herkese açık menü [{label, url}]
GET  /api/sale-menu/campaign/{id}    — kampanya sayfası başlığı (yalnız yayındaki kampanya)
GET  /api/admin/sale-menu            — menü + seçilebilir kampanyalar
PUT  /api/admin/sale-menu            — menüyü kaydet
"""
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from .deps import db, require_permission, safe_str

public_router = APIRouter(prefix="/sale-menu", tags=["sale-menu"])
admin_router = APIRouter(prefix="/admin/sale-menu", tags=["sale-menu-admin"])

_DOC_ID = "sale_menu"
_MAX_ITEMS = 12
_CACHE = {"t": 0.0, "items": None}
_TTL = 60


def _as_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def campaign_live(c: dict, now_iso: str | None = None) -> bool:
    """Kampanya şu an yayında mı? (aktif + tarih penceresi — kampanya motoruyla aynı kural;
    yalnız tarih verilmiş bitiş günü SONUNA kadar geçerli)."""
    if not c or not c.get("is_active"):
        return False
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    start = _as_str(c.get("start_at"))
    if start and start > now_iso:
        return False
    end = _as_str(c.get("end_at"))
    if end:
        if len(end) == 10 and "T" not in end:
            end = f"{end}T23:59:59+00:00"
        if end < now_iso:
            return False
    return True


def campaign_scope_query(c: dict) -> dict:
    """Kampanyanın ürün kapsamı → Mongo koşulu (rozet/motor kapsamıyla aynı: kategori
    [atalar category_ids'te], ürün listesi, hariç ürünler, indirimli fiyatlıyı atla)."""
    cats = [str(x) for x in (c.get("categories") or []) if x]
    prods = [str(x) for x in (c.get("products") or []) if x]
    excl = [str(x) for x in (c.get("excluded_products") or []) if x]
    ands = []
    ors = []
    if cats:
        ors += [{"category_ids": {"$in": cats}}, {"category_id": {"$in": cats}}]
    if prods:
        ors.append({"id": {"$in": prods}})
    if ors:
        ands.append({"$or": ors})
    if excl:
        ands.append({"id": {"$nin": excl}})
    if c.get("skip_discounted", True):
        # Ürün kartında indirimli fiyat girili ürün bu kampanyaya girmez (motor kuralı).
        ands.append({"$nor": [{"sale_price": {"$gt": 0}}]})
    return {"$and": ands} if ands else {}


async def _load_items() -> list:
    doc = await db.settings.find_one({"id": _DOC_ID}, {"_id": 0}) or {}
    return list(doc.get("items") or [])


def invalidate_cache():
    _CACHE["t"] = 0.0
    _CACHE["items"] = None


@public_router.get("")
async def public_sale_menu():
    if _CACHE["items"] is not None and time.time() - _CACHE["t"] < _TTL:
        return {"items": _CACHE["items"]}
    items = sorted(await _load_items(), key=lambda x: int(x.get("order") or 0))
    ids = [i.get("campaign_id") for i in items if i.get("type") == "campaign" and i.get("campaign_id")]
    camps = {}
    if ids:
        async for c in db.coupons.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "is_active": 1,
                                                             "start_at": 1, "end_at": 1}):
            camps[c["id"]] = c
    now_iso = datetime.now(timezone.utc).isoformat()
    out = []
    for i in items:
        if i.get("active") is False or not (i.get("label") or "").strip():
            continue
        if i.get("type") == "campaign":
            if not campaign_live(camps.get(i.get("campaign_id")), now_iso):
                continue
            url = f"/kampanya/{i['campaign_id']}"
        else:
            url = (i.get("link") or "").strip()
            if not url.startswith("/"):
                continue
        out.append({"label": i["label"].strip(), "url": url})
    _CACHE.update(t=time.time(), items=out)
    return {"items": out}


@public_router.get("/campaign/{campaign_id}")
async def public_campaign_info(campaign_id: str):
    cid = safe_str(campaign_id, 64)
    c = await db.coupons.find_one({"id": cid, "auto_apply": True},
                                  {"_id": 0, "id": 1, "title": 1, "badge_text": 1, "is_active": 1,
                                   "start_at": 1, "end_at": 1})
    if not c or not campaign_live(c):
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı veya sona erdi")
    label = ""
    for i in await _load_items():
        if i.get("type") == "campaign" and i.get("campaign_id") == cid:
            label = (i.get("label") or "").strip()
            break
    return {"id": c["id"], "title": label or (c.get("title") or "Kampanya"), "badge": c.get("badge_text") or ""}


@admin_router.get("")
async def admin_get(current_user: dict = Depends(require_permission("campaigns.view"))):
    items = sorted(await _load_items(), key=lambda x: int(x.get("order") or 0))
    now_iso = datetime.now(timezone.utc).isoformat()
    camps = []
    async for c in db.coupons.find({"auto_apply": True},
                                   {"_id": 0, "id": 1, "title": 1, "code": 1, "type": 1, "is_active": 1,
                                    "start_at": 1, "end_at": 1}).sort("created_at", -1).limit(200):
        camps.append({"id": c["id"], "title": c.get("title") or c.get("code") or "", "code": c.get("code"),
                      "type": c.get("type"), "live": campaign_live(c, now_iso)})
    return {"items": items, "campaigns": camps}


@admin_router.put("")
async def admin_put(payload: dict, current_user: dict = Depends(require_permission("campaigns.edit"))):
    raw = (payload or {}).get("items")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="items listesi gerekli")
    if len(raw) > _MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"En fazla {_MAX_ITEMS} kalem eklenebilir")
    clean = []
    for n, it in enumerate(raw):
        if not isinstance(it, dict):
            continue
        label = safe_str(it.get("label") or "", 40).strip()
        typ = "campaign" if it.get("type") == "campaign" else "link"
        if not label:
            raise HTTPException(status_code=400, detail=f"{n + 1}. kalemin adı boş")
        row = {"id": safe_str(it.get("id") or "", 32) or secrets.token_hex(6), "label": label, "type": typ,
               "active": it.get("active") is not False, "order": n}
        if typ == "campaign":
            cid = safe_str(it.get("campaign_id") or "", 64)
            if not cid or not await db.coupons.find_one({"id": cid}, {"_id": 1}):
                raise HTTPException(status_code=400, detail=f"'{label}': kampanya seçilmedi")
            row["campaign_id"] = cid
        else:
            link = safe_str(it.get("link") or "", 200).strip()
            if not link.startswith("/") or link.startswith("//"):
                raise HTTPException(status_code=400, detail=f"'{label}': link '/' ile başlamalı (ör. /sale)")
            row["link"] = link
        clean.append(row)
    await db.settings.update_one({"id": _DOC_ID}, {"$set": {
        "id": _DOC_ID, "items": clean, "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": (current_user or {}).get("email")}}, upsert=True)
    invalidate_cache()
    return {"success": True, "items": clean}
