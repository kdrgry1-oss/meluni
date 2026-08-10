"""
Admin → Üyeler (Members) module.

Provides CRUD + analytics for registered customers. A "member" here is a
document in the `users` collection that is NOT an admin.

Endpoints (all admin-protected):
  GET    /api/admin/members                – list, filters, pagination
  GET    /api/admin/members/stats          – totals, segments, acquisition sources
  GET    /api/admin/members/{mid}          – detail with orders + addresses
  POST   /api/admin/members                – create manually
  PUT    /api/admin/members/{mid}          – update
  DELETE /api/admin/members/{mid}          – delete
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone, timedelta
from typing import Optional

from .deps import db, require_admin, hash_password, generate_id, logger

router = APIRouter(prefix="/admin/members", tags=["admin-members"])


def _member_order_match(user: dict) -> dict:
    """Üyenin siparişlerini yakalayan koşul: user_id VE e-posta.
    Misafirken verilen (user_id=null) siparişler yalnız user_id ile aranınca
    üye detayında hiç görünmez — e-posta eşleşmesi aynı kişinin geçmiş
    siparişlerini güvenle kapsar (customer.py'deki _owner_or_clauses ile aynı ilke)."""
    import re as _re
    uid = user.get("id")
    ors = [{"user_id": uid}]
    email = (user.get("email") or "").strip()
    if email:
        rx = {"$regex": f"^{_re.escape(email)}$", "$options": "i"}
        ors += [{"shipping_address.email": rx}, {"email": rx}, {"billing_address.email": rx}]
    return {"$or": ors}


async def _annotate(user: dict) -> dict:
    uid = user.get("id")
    # Aggregate order stats
    pipeline = [
        {"$match": {"$and": [_member_order_match(user), {"status": {"$ne": "cancelled"}}]}},
        {
            "$group": {
                "_id": None,
                "orders": {"$sum": 1},
                "total_spent": {"$sum": {"$ifNull": ["$total", 0]}},
                "last_order_at": {"$max": "$created_at"},
            }
        },
    ]
    agg = None
    async for row in db.orders.aggregate(pipeline):
        agg = row
    user["orders_count"] = int(agg["orders"]) if agg else 0
    user["total_spent"] = round(float(agg["total_spent"]), 2) if agg else 0.0
    user["last_order_at"] = agg.get("last_order_at") if agg else None

    # Segment
    spent = user["total_spent"]
    if spent >= 5000:
        user["segment"] = "vip"
    elif user["orders_count"] >= 2:
        user["segment"] = "returning"
    elif user["orders_count"] == 1:
        user["segment"] = "new"
    else:
        user["segment"] = "prospect"
    return user


@router.get("")
async def list_members(
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    search: Optional[str] = None,
    segment: Optional[str] = None,
    source: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    query: dict = {"is_admin": {"$ne": True}}
    if search:
        # DENETİM FIX: kaçışsız regex özel karakterlerde ('(' '[' vb.) 500 / ReDoS veriyordu.
        import re as _re
        _s = _re.escape(str(search).strip())
        query["$or"] = [
            {"email": {"$regex": _s, "$options": "i"}},
            {"first_name": {"$regex": _s, "$options": "i"}},
            {"last_name": {"$regex": _s, "$options": "i"}},
            {"phone": {"$regex": _s, "$options": "i"}},
        ]
    if source:
        query["acquisition_source"] = source

    skip = (page - 1) * limit

    # DENETİM FIX (#39): segment sunucuda materyalize edilir. Eskiden segment yalnız
    # o SAYFAYA gelen kayıtlar üzerinde Python'da kırpılıyordu → 'total' yanlış, sayfalar
    # eksik/boş kalıyordu. Artık orders istatistiği tek $lookup ile hesaplanır, segment
    # $addFields ile türetilir, filtre uygulandıktan SONRA total sayılır ve skip/limit
    # $facet içinde yapılır. Sonuç: doğru sayfalama + doğru toplam.
    base_pipeline: list = [
        {"$match": query},
        {"$lookup": {
            "from": "orders",
            "let": {"uid": "$id", "em": {"$toLower": {"$ifNull": ["$email", ""]}}},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$ne": ["$status", "cancelled"]},
                    {"$or": [
                        {"$eq": ["$user_id", "$$uid"]},
                        {"$and": [{"$ne": ["$$em", ""]}, {"$eq": [{"$toLower": {"$ifNull": ["$email", ""]}}, "$$em"]}]},
                        {"$and": [{"$ne": ["$$em", ""]}, {"$eq": [{"$toLower": {"$ifNull": ["$shipping_address.email", ""]}}, "$$em"]}]},
                        {"$and": [{"$ne": ["$$em", ""]}, {"$eq": [{"$toLower": {"$ifNull": ["$billing_address.email", ""]}}, "$$em"]}]},
                    ]},
                ]}}},
                {"$group": {"_id": None, "orders": {"$sum": 1},
                            "total_spent": {"$sum": {"$ifNull": ["$total", 0]}},
                            "last_order_at": {"$max": "$created_at"}}},
            ],
            "as": "_ostats",
        }},
        {"$addFields": {
            "orders_count": {"$ifNull": [{"$arrayElemAt": ["$_ostats.orders", 0]}, 0]},
            "total_spent": {"$round": [{"$ifNull": [{"$arrayElemAt": ["$_ostats.total_spent", 0]}, 0]}, 2]},
            "last_order_at": {"$arrayElemAt": ["$_ostats.last_order_at", 0]},
        }},
        {"$addFields": {
            "segment": {"$switch": {"branches": [
                {"case": {"$gte": ["$total_spent", 5000]}, "then": "vip"},
                {"case": {"$gte": ["$orders_count", 2]}, "then": "returning"},
                {"case": {"$eq": ["$orders_count", 1]}, "then": "new"},
            ], "default": "prospect"}},
        }},
    ]
    if segment:
        base_pipeline.append({"$match": {"segment": segment}})
    base_pipeline.append({"$facet": {
        "meta": [{"$count": "total"}],
        "items": [
            {"$sort": {"created_at": -1}},
            {"$skip": skip},
            {"$limit": limit},
            {"$project": {"_id": 0, "password": 0, "_ostats": 0}},
        ],
    }})

    agg_out = None
    async for row in db.users.aggregate(base_pipeline):
        agg_out = row
    items = (agg_out or {}).get("items", [])
    meta = (agg_out or {}).get("meta", [])
    total = int(meta[0]["total"]) if meta else 0

    return {"items": items, "total": total, "page": page, "pages": (total + limit - 1) // limit}


@router.get("/stats")
async def stats(current_user: dict = Depends(require_admin)):
    total = await db.users.count_documents({"is_admin": {"$ne": True}})
    since_30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new_30 = await db.users.count_documents({"is_admin": {"$ne": True}, "created_at": {"$gte": since_30}})

    # Acquisition by source (from user.acquisition_source OR first order attribution.channel)
    pipeline = [
        {"$match": {"user_id": {"$ne": None}}},
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": "$user_id", "first_channel": {"$first": "$attribution.channel"}}},
        {"$group": {"_id": "$first_channel", "members": {"$sum": 1}}},
        {"$sort": {"members": -1}},
    ]
    by_channel = []
    async for row in db.orders.aggregate(pipeline):
        by_channel.append({"channel": row["_id"] or "direct", "members": row["members"]})

    # Segments – compute lightweight
    seg = {"vip": 0, "returning": 0, "new": 0, "prospect": 0}
    async for u in db.users.find({"is_admin": {"$ne": True}}, {"_id": 0, "id": 1}):
        pipeline_user = [
            {"$match": {"user_id": u["id"], "status": {"$ne": "cancelled"}}},
            {"$group": {"_id": None, "o": {"$sum": 1}, "t": {"$sum": {"$ifNull": ["$total", 0]}}}},
        ]
        a = None
        async for row in db.orders.aggregate(pipeline_user):
            a = row
        o = int(a["o"]) if a else 0
        t = float(a["t"]) if a else 0.0
        if t >= 5000:
            seg["vip"] += 1
        elif o >= 2:
            seg["returning"] += 1
        elif o == 1:
            seg["new"] += 1
        else:
            seg["prospect"] += 1

    return {"total": total, "new_last_30_days": new_30, "segments": seg, "acquisition_by_channel": by_channel}


@router.get("/{mid}")
async def detail(mid: str, current_user: dict = Depends(require_admin)):
    u = await db.users.find_one({"id": mid, "is_admin": {"$ne": True}}, {"_id": 0, "password": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")

    u = await _annotate(u)
    orders = await db.orders.find(_member_order_match(u), {"_id": 0}).sort("created_at", -1).to_list(200)
    addresses = await db.addresses.find({"user_id": mid}, {"_id": 0}).to_list(20)
    # Adres defteri boşsa geçmiş siparişlerin teslimat adreslerinden türet (görüntüleme amaçlı)
    if not addresses and orders:
        seen = set()
        for o in orders:
            sa = o.get("shipping_address") or {}
            key = ((sa.get("address") or "").strip().lower(), (sa.get("city") or "").strip().lower())
            if not key[0] or key in seen:
                continue
            seen.add(key)
            addresses.append({
                "id": "", "user_id": mid, "title": f"Sipariş Adresi ({o.get('order_number','')})",
                "first_name": sa.get("first_name", ""), "last_name": sa.get("last_name", ""),
                "phone": sa.get("phone", ""), "address": sa.get("address", ""),
                "city": sa.get("city", ""), "district": sa.get("district", ""),
                "postal_code": sa.get("postal_code", ""), "is_default": False,
                "source": "order",
            })
            if len(addresses) >= 5:
                break

    # Attribution breakdown from orders
    ch_map: dict = {}
    for o in orders:
        ch = (o.get("attribution") or {}).get("channel") or "direct"
        ch_map[ch] = ch_map.get(ch, 0) + 1
    attribution_summary = [{"channel": k, "orders": v} for k, v in sorted(ch_map.items(), key=lambda x: -x[1])]

    return {
        "member": u,
        "orders": orders,
        "addresses": addresses,
        "attribution_summary": attribution_summary,
    }


@router.post("")
async def create_member(payload: dict, current_user: dict = Depends(require_admin)):
    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="E-posta gerekli")
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="E-posta zaten kayıtlı")
    doc = {
        "id": generate_id(),
        "email": email,
        "password": hash_password(payload.get("password", "Facette123!")),
        "first_name": payload.get("first_name", ""),
        "last_name": payload.get("last_name", ""),
        "phone": payload.get("phone", ""),
        "is_admin": False,
        "is_active": True,
        "accepts_marketing": bool(payload.get("accepts_marketing", False)),
        "acquisition_source": payload.get("acquisition_source", "admin_manual"),
        "notes": payload.get("notes", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by_admin": current_user.get("email", ""),
    }
    await db.users.insert_one(doc)
    doc.pop("_id", None)
    doc.pop("password", None)
    return {"success": True, "member": doc}


@router.put("/{mid}")
async def update_member(mid: str, payload: dict, current_user: dict = Depends(require_admin)):
    # DENETİM FIX (#41): group_id atanabilir olmalı — Üye Grupları indirimi gruba bağlı üyeye uygulanır.
    allowed = ("first_name", "last_name", "phone", "is_active", "accepts_marketing", "acquisition_source", "notes", "segment_tags", "group_id")
    update = {k: v for k, v in payload.items() if k in allowed}
    if payload.get("password"):
        update["password"] = hash_password(payload["password"])
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = await db.users.update_one({"id": mid, "is_admin": {"$ne": True}}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")
    return {"success": True}


@router.delete("/{mid}")
async def delete_member(mid: str, current_user: dict = Depends(require_admin)):
    res = await db.users.delete_one({"id": mid, "is_admin": {"$ne": True}})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")
    return {"success": True}
