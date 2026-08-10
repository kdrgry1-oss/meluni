"""
warehouse.py — Depo & Raf Yönetimi (WMS raf/bin adresleme + raf stoğu).

AMAÇ:
  Depo içi RAF ADRESLEME ve raf bazlı stok takibi: hangi ürün/varyant depoda
  hangi rafta (koridor-raf-kat-göz), kaç adet duruyor. Yerleştirme (put-away),
  raflar arası taşıma, sayım (cycle count) ve raporlama.

TASARIM (WMS araştırmasına dayalı):
  - Koleksiyonlar:
      warehouses    { id, name, code, note, is_active, created_at }
      warehouse_bins{ id, warehouse_id, code, aisle, bay, level, position,
                      is_active, note, created_at }
      bin_stock     { id, warehouse_id, bin_id, product_id, variant_key,
                      size, color, barcode, name, quantity, updated_at }
  - Raf adresi: aisle/bay/level/position → türev "code" (ör. A-01-03-B).
      Koridorlar çift sayı ile kodlanabilir (araya yeni koridor eklenebilir).
  - ATOMİK stok: raf miktarı değişimleri koşullu $inc ile — negatif stok/oversell
      YOK. Çıkarmada filtre "quantity >= qty"; taşıma iki atomik adım (kaynak
      düş, hedef ekle) + hata halinde geri alma.
  - SİPARİŞ STOK DÜŞÜMÜNE DOKUNMAZ. Bu modül depo içi fiziksel yerleşimi izler;
      ürün/varyant satış stoğu mevcut akışta kalır. (Faz 2'de mutabakat opsiyonel.)

  NOT: available/satış stoğu bu modülün sorumluluğu değildir; burada "quantity"
  rafın fiziksel içeriğidir.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timezone
from typing import Optional

from .deps import db, require_admin, generate_id, logger

router = APIRouter(prefix="/admin/warehouse", tags=["admin-warehouse"])


def _now():
    return datetime.now(timezone.utc).isoformat()


def _bin_code(aisle: str, bay: str, level: str, position: str) -> str:
    """Raf bileşenlerinden okunur adres kodu üretir (boş bileşenler atlanır)."""
    parts = [str(x).strip() for x in (aisle, bay, level, position) if str(x or "").strip()]
    return "-".join(parts)


def _s(v, limit=120):
    return (str(v) if v is not None else "").strip()[:limit]


# ============================================================================
# DEPOLAR
# ============================================================================
@router.get("/warehouses")
async def list_warehouses(current_user: dict = Depends(require_admin)):
    """Tüm depolar + her depodaki raf sayısı ve toplam raf-stok adedi."""
    out = []
    async for w in db.warehouses.find({}, {"_id": 0}).sort("created_at", 1):
        wid = w.get("id")
        bin_count = await db.warehouse_bins.count_documents({"warehouse_id": wid})
        agg = await db.bin_stock.aggregate([
            {"$match": {"warehouse_id": wid}},
            {"$group": {"_id": None, "units": {"$sum": "$quantity"},
                        "skus": {"$addToSet": "$product_id"}}},
        ]).to_list(1)
        units = agg[0]["units"] if agg else 0
        skus = len(agg[0]["skus"]) if agg else 0
        out.append({**w, "bin_count": bin_count, "total_units": units, "sku_count": skus})
    return {"items": out, "total": len(out)}


@router.post("/warehouses")
async def create_warehouse(payload: dict, current_user: dict = Depends(require_admin)):
    name = _s(payload.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="Depo adı gerekli")
    code = _s(payload.get("code"), 40) or name.upper().replace(" ", "-")[:40]
    if await db.warehouses.find_one({"code": code}):
        raise HTTPException(status_code=409, detail="Bu depo kodu zaten var")
    doc = {"id": generate_id(), "name": name, "code": code,
           "note": _s(payload.get("note"), 500), "is_active": bool(payload.get("is_active", True)),
           "created_at": _now()}
    await db.warehouses.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "warehouse": doc}


@router.put("/warehouses/{wid}")
async def update_warehouse(wid: str, payload: dict, current_user: dict = Depends(require_admin)):
    upd = {}
    for k in ("name", "note"):
        if k in payload:
            upd[k] = _s(payload.get(k), 500)
    if "is_active" in payload:
        upd["is_active"] = bool(payload.get("is_active"))
    if not upd:
        raise HTTPException(status_code=400, detail="Güncellenecek alan yok")
    r = await db.warehouses.update_one({"id": wid}, {"$set": upd})
    if not r.matched_count:
        raise HTTPException(status_code=404, detail="Depo bulunamadı")
    return {"success": True}


@router.delete("/warehouses/{wid}")
async def delete_warehouse(wid: str, current_user: dict = Depends(require_admin)):
    """Depoyu siler — YALNIZCA rafları ve raf stoğu boşsa (kaza koruması)."""
    if await db.bin_stock.count_documents({"warehouse_id": wid, "quantity": {"$gt": 0}}):
        raise HTTPException(status_code=409, detail="Depoda stoklu raf var; önce boşaltın/taşıyın.")
    await db.warehouse_bins.delete_many({"warehouse_id": wid})
    r = await db.warehouses.delete_one({"id": wid})
    if not r.deleted_count:
        raise HTTPException(status_code=404, detail="Depo bulunamadı")
    return {"success": True}


# ============================================================================
# RAFLAR (BIN)
# ============================================================================
@router.get("/warehouses/{wid}/bins")
async def list_bins(wid: str, q: Optional[str] = None, current_user: dict = Depends(require_admin)):
    """Depodaki raflar + her rafın içindeki adet/çeşit. q ile raf koduna göre süz."""
    query = {"warehouse_id": wid}
    if q:
        query["code"] = {"$regex": _s(q, 60), "$options": "i"}
    out = []
    async for b in db.warehouse_bins.find(query, {"_id": 0}).sort("code", 1).limit(2000):
        bid = b.get("id")
        agg = await db.bin_stock.aggregate([
            {"$match": {"bin_id": bid}},
            {"$group": {"_id": None, "units": {"$sum": "$quantity"}, "lines": {"$sum": 1}}},
        ]).to_list(1)
        out.append({**b, "units": (agg[0]["units"] if agg else 0), "lines": (agg[0]["lines"] if agg else 0)})
    return {"items": out, "total": len(out)}


@router.post("/warehouses/{wid}/bins")
async def create_bin(wid: str, payload: dict, current_user: dict = Depends(require_admin)):
    w = await db.warehouses.find_one({"id": wid})
    if not w:
        raise HTTPException(status_code=404, detail="Depo bulunamadı")
    aisle = _s(payload.get("aisle"), 20); bay = _s(payload.get("bay"), 20)
    level = _s(payload.get("level"), 20); position = _s(payload.get("position"), 20)
    code = _s(payload.get("code"), 60) or _bin_code(aisle, bay, level, position)
    if not code:
        raise HTTPException(status_code=400, detail="Raf kodu veya koridor/raf/kat/göz gerekli")
    if await db.warehouse_bins.find_one({"warehouse_id": wid, "code": code}):
        raise HTTPException(status_code=409, detail="Bu raf kodu bu depoda zaten var")
    doc = {"id": generate_id(), "warehouse_id": wid, "code": code,
           "aisle": aisle, "bay": bay, "level": level, "position": position,
           "note": _s(payload.get("note"), 300), "is_active": bool(payload.get("is_active", True)),
           "created_at": _now()}
    await db.warehouse_bins.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "bin": doc}


@router.post("/warehouses/{wid}/bins/bulk")
async def create_bins_bulk(wid: str, payload: dict, current_user: dict = Depends(require_admin)):
    """Toplu raf üretimi: koridor listesi × raf aralığı × kat aralığı → çok sayıda raf.
    payload: { aisles:["A","B"], bays:[1,10], levels:[1,4], positions:["A","B"] } gibi.
    bays/levels [başlangıç, bitiş] aralığı; positions/aisles liste."""
    w = await db.warehouses.find_one({"id": wid})
    if not w:
        raise HTTPException(status_code=404, detail="Depo bulunamadı")
    aisles = [(_s(a, 20)) for a in (payload.get("aisles") or []) if str(a or "").strip()]
    positions = [(_s(p, 20)) for p in (payload.get("positions") or [""])] or [""]
    def _rng(v):
        if isinstance(v, list) and len(v) == 2:
            try:
                a, b = int(v[0]), int(v[1])
                return [str(i) for i in range(min(a, b), max(a, b) + 1)]
            except (TypeError, ValueError):
                return [str(x) for x in v]
        return [str(x) for x in (v or [""])]
    bays = _rng(payload.get("bays")); levels = _rng(payload.get("levels"))
    combos = []
    for a in (aisles or [""]):
        for bay in bays:
            for lv in levels:
                for pos in positions:
                    combos.append((a, bay, lv, pos))
    if not combos or len(combos) > 5000:
        raise HTTPException(status_code=400, detail="Geçersiz aralık (1-5000 raf üretilebilir).")
    existing = set()
    async for b in db.warehouse_bins.find({"warehouse_id": wid}, {"_id": 0, "code": 1}):
        existing.add(b["code"])
    docs, created = [], 0
    for (a, bay, lv, pos) in combos:
        code = _bin_code(a, bay, lv, pos)
        if not code or code in existing:
            continue
        existing.add(code)
        docs.append({"id": generate_id(), "warehouse_id": wid, "code": code,
                     "aisle": a, "bay": bay, "level": lv, "position": pos,
                     "note": "", "is_active": True, "created_at": _now()})
        created += 1
    if docs:
        await db.warehouse_bins.insert_many(docs)
    return {"success": True, "created": created, "skipped_existing": len(combos) - created}


@router.delete("/warehouses/{wid}/bins/{bid}")
async def delete_bin(wid: str, bid: str, current_user: dict = Depends(require_admin)):
    if await db.bin_stock.count_documents({"bin_id": bid, "quantity": {"$gt": 0}}):
        raise HTTPException(status_code=409, detail="Rafta ürün var; önce boşaltın/taşıyın.")
    await db.bin_stock.delete_many({"bin_id": bid})
    r = await db.warehouse_bins.delete_one({"id": bid, "warehouse_id": wid})
    if not r.deleted_count:
        raise HTTPException(status_code=404, detail="Raf bulunamadı")
    return {"success": True}


# ============================================================================
# RAF STOĞU — yerleştir / taşı / çıkar (ATOMİK)
# ============================================================================
async def _resolve_product(product_id: str = "", barcode: str = ""):
    """Ürün/varyant çözümle (ad + barkod + beden). Barkod varyant barkodu olabilir."""
    p = None
    if product_id:
        p = await db.products.find_one({"id": product_id}, {"_id": 0, "id": 1, "name": 1, "variants": 1, "barcode": 1})
    if not p and barcode:
        p = await db.products.find_one(
            {"$or": [{"barcode": barcode}, {"variants.barcode": barcode}]},
            {"_id": 0, "id": 1, "name": 1, "variants": 1, "barcode": 1})
    return p


@router.post("/bins/{bid}/place")
async def place_stock(bid: str, payload: dict, current_user: dict = Depends(require_admin)):
    """Rafa ürün YERLEŞTİR (put-away). Aynı raf+ürün+varyant satırına adet ekler (atomik $inc).
    payload: { product_id?, barcode?, size?, quantity }"""
    b = await db.warehouse_bins.find_one({"id": bid}, {"_id": 0})
    if not b:
        raise HTTPException(status_code=404, detail="Raf bulunamadı")
    qty = int(payload.get("quantity") or 0)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Adet 1 veya daha fazla olmalı")
    product_id = _s(payload.get("product_id"), 80)
    barcode = _s(payload.get("barcode"), 60)
    p = await _resolve_product(product_id, barcode)
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı (product_id veya barkod)")
    size = _s(payload.get("size"), 30)
    variant_key = f"{p['id']}|{size}|{barcode}"
    r = await db.bin_stock.find_one_and_update(
        {"bin_id": bid, "variant_key": variant_key},
        {"$inc": {"quantity": qty},
         "$set": {"updated_at": _now()},
         "$setOnInsert": {"id": generate_id(), "warehouse_id": b["warehouse_id"], "bin_id": bid,
                          "product_id": p["id"], "name": p.get("name") or "", "size": size,
                          "barcode": barcode, "variant_key": variant_key}},
        upsert=True, return_document=True,
    )
    return {"success": True, "quantity": (r or {}).get("quantity", qty), "bin_code": b["code"]}


@router.post("/bins/{bid}/remove")
async def remove_stock(bid: str, payload: dict, current_user: dict = Depends(require_admin)):
    """Raftan ürün ÇIKAR (pick). Atomik: yalnızca yeterli adet varsa düşer (oversell yok).
    payload: { variant_key veya (product_id + size + barcode), quantity }"""
    qty = int(payload.get("quantity") or 0)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Adet 1 veya daha fazla olmalı")
    variant_key = _s(payload.get("variant_key"), 200)
    if not variant_key:
        pid = _s(payload.get("product_id"), 80); size = _s(payload.get("size"), 30); bc = _s(payload.get("barcode"), 60)
        variant_key = f"{pid}|{size}|{bc}"
    # ATOMİK: quantity >= qty iken düş; değilse null döner (yetersiz stok).
    r = await db.bin_stock.find_one_and_update(
        {"bin_id": bid, "variant_key": variant_key, "quantity": {"$gte": qty}},
        {"$inc": {"quantity": -qty}, "$set": {"updated_at": _now()}},
        return_document=True,
    )
    if r is None:
        raise HTTPException(status_code=409, detail="Rafta yeterli adet yok")
    # Satır sıfırlandıysa temizle
    if r.get("quantity", 0) <= 0:
        await db.bin_stock.delete_one({"id": r["id"]})
    return {"success": True, "remaining": max(0, r.get("quantity", 0))}


@router.post("/bins/move")
async def move_stock(payload: dict, current_user: dict = Depends(require_admin)):
    """Raflar arası TAŞI. Önce kaynaktan atomik düş (yeterli değilse hata),
    sonra hedefe ekle; hedef ekleme hata verirse kaynağa geri koy (telafi).
    payload: { from_bin_id, to_bin_id, variant_key veya (product_id+size+barcode), quantity }"""
    qty = int(payload.get("quantity") or 0)
    from_bin = _s(payload.get("from_bin_id"), 80); to_bin = _s(payload.get("to_bin_id"), 80)
    if qty <= 0 or not from_bin or not to_bin or from_bin == to_bin:
        raise HTTPException(status_code=400, detail="Geçerli kaynak/hedef raf ve adet gerekli")
    tb = await db.warehouse_bins.find_one({"id": to_bin}, {"_id": 0})
    if not tb:
        raise HTTPException(status_code=404, detail="Hedef raf bulunamadı")
    variant_key = _s(payload.get("variant_key"), 200)
    if not variant_key:
        pid = _s(payload.get("product_id"), 80); size = _s(payload.get("size"), 30); bc = _s(payload.get("barcode"), 60)
        variant_key = f"{pid}|{size}|{bc}"
    src = await db.bin_stock.find_one_and_update(
        {"bin_id": from_bin, "variant_key": variant_key, "quantity": {"$gte": qty}},
        {"$inc": {"quantity": -qty}, "$set": {"updated_at": _now()}},
        return_document=True,
    )
    if src is None:
        raise HTTPException(status_code=409, detail="Kaynak rafta yeterli adet yok")
    try:
        await db.bin_stock.find_one_and_update(
            {"bin_id": to_bin, "variant_key": variant_key},
            {"$inc": {"quantity": qty}, "$set": {"updated_at": _now()},
             "$setOnInsert": {"id": generate_id(), "warehouse_id": tb["warehouse_id"], "bin_id": to_bin,
                              "product_id": src.get("product_id"), "name": src.get("name"),
                              "size": src.get("size"), "barcode": src.get("barcode"), "variant_key": variant_key}},
            upsert=True, return_document=True,
        )
    except Exception as e:
        # TELAFİ: hedefe eklenemezse kaynağa geri koy
        await db.bin_stock.update_one({"bin_id": from_bin, "variant_key": variant_key}, {"$inc": {"quantity": qty}})
        logger.error(f"[warehouse move] hedefe eklenemedi, geri alındı: {e}")
        raise HTTPException(status_code=500, detail="Taşıma başarısız, geri alındı")
    if src.get("quantity", 0) <= 0:
        await db.bin_stock.delete_one({"id": src["id"]})
    return {"success": True}


@router.post("/bins/{bid}/count")
async def cycle_count(bid: str, payload: dict, current_user: dict = Depends(require_admin)):
    """SAYIM: raftaki bir satırın adedini sayılan gerçek değere set eder (düzeltme).
    payload: { variant_key, counted } — fark denetim için loglanır."""
    variant_key = _s(payload.get("variant_key"), 200)
    counted = int(payload.get("counted") or 0)
    if counted < 0 or not variant_key:
        raise HTTPException(status_code=400, detail="Geçerli variant_key ve sayım (>=0) gerekli")
    row = await db.bin_stock.find_one({"bin_id": bid, "variant_key": variant_key}, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail="Raf satırı bulunamadı")
    diff = counted - int(row.get("quantity") or 0)
    await db.bin_stock.update_one({"id": row["id"]}, {"$set": {"quantity": counted, "updated_at": _now()}})
    if counted <= 0:
        await db.bin_stock.delete_one({"id": row["id"]})
    logger.info(f"[warehouse count] bin={bid} key={variant_key} diff={diff} by={current_user.get('email')}")
    return {"success": True, "counted": counted, "diff": diff}


# ============================================================================
# RAPORLAR — "bu rafta ne var", "bu ürün hangi raflarda"
# ============================================================================
@router.get("/bins/{bid}/contents")
async def bin_contents(bid: str, current_user: dict = Depends(require_admin)):
    """Bu rafta hangi ürün/varyanttan kaç adet var."""
    items = await db.bin_stock.find({"bin_id": bid, "quantity": {"$gt": 0}}, {"_id": 0}).to_list(1000)
    return {"items": items, "total_units": sum(int(i.get("quantity") or 0) for i in items)}


@router.get("/product-locations")
async def product_locations(product_id: str = Query(""), barcode: str = Query(""),
                            current_user: dict = Depends(require_admin)):
    """Bir ürün/varyant depoda hangi raflarda, kaç adet? (raf kodu + depo ile)."""
    p = await _resolve_product(_s(product_id, 80), _s(barcode, 60))
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    rows = await db.bin_stock.find({"product_id": p["id"], "quantity": {"$gt": 0}}, {"_id": 0}).to_list(2000)
    # raf kodu + depo adını ekle
    bin_ids = list({r.get("bin_id") for r in rows})
    wh_ids = list({r.get("warehouse_id") for r in rows})
    bins = {b["id"]: b async for b in db.warehouse_bins.find({"id": {"$in": bin_ids}}, {"_id": 0})}
    whs = {w["id"]: w async for w in db.warehouses.find({"id": {"$in": wh_ids}}, {"_id": 0})}
    out = []
    for r in rows:
        b = bins.get(r.get("bin_id")) or {}
        w = whs.get(r.get("warehouse_id")) or {}
        out.append({
            "warehouse": w.get("name") or "—", "bin_code": b.get("code") or "—",
            "size": r.get("size") or "", "barcode": r.get("barcode") or "",
            "quantity": r.get("quantity", 0),
        })
    out.sort(key=lambda x: (x["warehouse"], x["bin_code"]))
    return {"product": {"id": p["id"], "name": p.get("name")},
            "locations": out, "total_units": sum(int(x["quantity"]) for x in out)}


@router.get("/search")
async def warehouse_search(q: str = Query(..., min_length=1), current_user: dict = Depends(require_admin)):
    """Genel arama: ürün adı/barkodu VEYA raf kodu ile raf-stok satırları."""
    ql = _s(q, 80)
    rows = await db.bin_stock.find(
        {"quantity": {"$gt": 0},
         "$or": [{"name": {"$regex": ql, "$options": "i"}},
                 {"barcode": {"$regex": ql, "$options": "i"}}]},
        {"_id": 0}).limit(500).to_list(500)
    # raf koduyla da eşleşenler
    bin_hits = await db.warehouse_bins.find({"code": {"$regex": ql, "$options": "i"}}, {"_id": 0, "id": 1}).to_list(200)
    if bin_hits:
        more = await db.bin_stock.find(
            {"quantity": {"$gt": 0}, "bin_id": {"$in": [b["id"] for b in bin_hits]}}, {"_id": 0}
        ).limit(500).to_list(500)
        seen = {r["id"] for r in rows}
        rows += [m for m in more if m["id"] not in seen]
    bin_ids = list({r.get("bin_id") for r in rows})
    bins = {b["id"]: b async for b in db.warehouse_bins.find({"id": {"$in": bin_ids}}, {"_id": 0})}
    for r in rows:
        r["bin_code"] = (bins.get(r.get("bin_id")) or {}).get("code") or "—"
    return {"items": rows[:500], "total": len(rows)}
