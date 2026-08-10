"""
İmalat Takip (Manufacturing) module.

Tracks manufacturing orders end-to-end:
- anlaşma → numune → kumaş → kesim → dikim → kalite → teslim → fatura
- Suppliers, cost lines (F8), purchase orders (F11), fire/waste (F10)
- Stage history per record with user + timestamp
- On "teslim alındı" (delivered/stocked) the product stock is incremented
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid

from .deps import db, require_admin, logger

router = APIRouter(prefix="/manufacturing", tags=["manufacturing"])


# Canonical stage order — 6 sadeleştirilmiş aşama (kullanıcı isteği; timeline görünümü)
STAGES = [
    "siparis_dosyasi",   # Sipariş Dosyası (eski: Anlaşma İmzalandı)
    "kumas_okeyi",       # Kumaş Okeyi
    "kesim",             # Kesim Başlangıcı
    "dikim",             # Dikiş Başlangıcı
    "kalite_kontrol",    # Kalite Kontrol
    "teslim_alindi",     # Depo Teslimat (stok artışı bu aşamada tetiklenir)
]

STAGE_LABELS = {
    "siparis_dosyasi": "Sipariş Dosyası",
    "kumas_okeyi": "Kumaş Okeyi",
    "kesim": "Kesim Başlangıcı",
    "dikim": "Dikiş Başlangıcı",
    "kalite_kontrol": "Kalite Kontrol",
    "teslim_alindi": "Depo Teslimat",
}

# Eski 12'li aşama setinden kalan kayıtlar en yakın yeni aşamaya taşınır (tembel migrasyon)
_LEGACY_STAGE_MAP = {
    "anlasma": "siparis_dosyasi",
    "numune_hazirlaniyor": "siparis_dosyasi",
    "numune_onaylandi": "siparis_dosyasi",
    "kumas_siparisi": "kumas_okeyi",
    "kumas_teslim": "kumas_okeyi",
    "aksesuar": "kumas_okeyi",
    "utu_paketleme": "kalite_kontrol",
    "fatura_kesildi": "teslim_alindi",
}


async def _migrate_legacy_stages():
    """Eski aşama anahtarlı kayıtları yeni sete taşır — idempotent, listede tembel çağrılır."""
    for old, new in _LEGACY_STAGE_MAP.items():
        await db.manufacturing.update_many(
            {"current_stage": old}, {"$set": {"current_stage": new}})


@router.get("/stages")
async def get_stages(current_user: dict = Depends(require_admin)):
    return {"stages": [{"key": k, "label": STAGE_LABELS[k]} for k in STAGES]}


@router.get("")
async def list_manufacturing(
    stage: Optional[str] = None,
    search: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    await _migrate_legacy_stages()  # idempotent — eski aşama anahtarlarını yeni sete taşır
    query = {}
    if stage:
        query["current_stage"] = stage
    if search:
        import re as _re
        _s = _re.escape(str(search))  # ReDoS koruması
        query["$or"] = [
            {"product_name": {"$regex": _s, "$options": "i"}},
            {"partner_name": {"$regex": _s, "$options": "i"}},
            {"code": {"$regex": _s, "$options": "i"}},
        ]
    items = await db.manufacturing.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    # Summary counts per stage
    pipeline = [{"$group": {"_id": "$current_stage", "count": {"$sum": 1}}}]
    counts = {}
    async for row in db.manufacturing.aggregate(pipeline):
        counts[row["_id"] or ""] = row["count"]
    return {"items": items, "counts_by_stage": counts, "total": len(items)}


@router.get("/{record_id}")
async def get_manufacturing(record_id: str, current_user: dict = Depends(require_admin)):
    rec = await db.manufacturing.find_one({"id": record_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    return rec


@router.post("")
async def create_manufacturing(payload: dict, current_user: dict = Depends(require_admin)):
    now_iso = datetime.now(timezone.utc).isoformat()
    # Code: IMLT-yyyy-### incremental
    year = datetime.now(timezone.utc).year
    cnt = await db.manufacturing.count_documents({"code": {"$regex": f"^IMLT-{year}-"}})
    code = f"IMLT-{year}-{cnt + 1:04d}"

    # İmalatçı ZORUNLU ve kayıtlı listeden gelir (kullanıcı isteği).
    supplier_id = (payload.get("supplier_id") or "").strip()
    if not supplier_id:
        raise HTTPException(status_code=400, detail="İmalatçı seçimi zorunlu — listeden seçin veya yeni imalatçı ekleyin")
    _sup = await db.manufacturing_suppliers.find_one({"id": supplier_id}, {"_id": 0, "name": 1})
    if not _sup:
        raise HTTPException(status_code=400, detail="Seçilen imalatçı bulunamadı")

    _order_date = payload.get("agreement_date", now_iso)
    # Tahmini teslim: verilmemişse sipariş tarihi + 21 gün OTOMATİK.
    _exp = payload.get("expected_delivery_date")
    if not _exp:
        try:
            _d = datetime.fromisoformat(str(_order_date)[:10])
            _exp = (_d + timedelta(days=21)).strftime("%Y-%m-%d")
        except Exception:
            _exp = None

    doc = {
        "id": str(uuid.uuid4()),
        "code": code,
        "order_no": (payload.get("order_no") or code).strip(),  # İmalat Sipariş No
        "order_flags": {  # Yeni Sipariş / RPT işaret kutuları
            "new": bool((payload.get("order_flags") or {}).get("new")),
            "rpt": bool((payload.get("order_flags") or {}).get("rpt")),
        },
        "stock_code": (payload.get("stock_code") or "").strip(),  # ürün ilk burada doğar
        "colors": payload.get("colors") or [],  # sipariş edilen renkler
        "product_id": payload.get("product_id", ""),
        "product_name": payload.get("product_name", ""),
        "partner_name": _sup["name"],
        "partner_contact": payload.get("partner_contact", ""),
        "responsible_user": payload.get("responsible_user", current_user.get("email", "")),
        "agreement_date": _order_date,  # Sipariş Tarihi
        "expected_delivery_date": _exp,
        "size_distribution": payload.get("size_distribution", {}),  # e.g. {"S":10,"M":20}
        "total_units": sum((payload.get("size_distribution") or {}).values()) if payload.get("size_distribution") else payload.get("total_units", 0),
        "unit_price": float(payload.get("unit_price", 0) or 0),
        "agreed_total": float(payload.get("agreed_total", 0) or 0),
        "payment_done": bool(payload.get("payment_done")),  # tek tik: ödeme yapıldı mı
        "payment_done_at": now_iso if payload.get("payment_done") else None,
        # KURAL: 21 günlük üretim saati ÖDEME ile başlar — ödenmiş açılan kayıtta teslim = bugün+21
        "has_lining": bool(payload.get("has_lining")),      # astarlı ürün → renk bazlı astar okeyi
        "color_approvals": payload.get("color_approvals") or {},  # {"Renk": {fabric, lining}}
        "cutting_start_date": payload.get("cutting_start_date", ""),  # kesim başlangıç tarihi
        "actual_distribution": payload.get("actual_distribution") or {},  # gerçekleşen kesim adedi {"Renk|Beden": n}
        "sewing_workshop": (payload.get("sewing_workshop") or "").strip(),  # dikim atölyesi adı
        "sewing_report_images": payload.get("sewing_report_images") or [],  # imalat (görsel) raporu URL listesi
        "qc_result": payload.get("qc_result") or "",       # kalite kontrol: "" | "gecti" | "kaldi" (kaldi → Re-FRI)
        "qc_date": payload.get("qc_date") or "",           # kalite kontrol tarihi
        "qc_images": payload.get("qc_images") or [],       # kalite kontrol görselleri URL listesi
        "qc2_result": payload.get("qc2_result") or "",     # 2. kalite kontrol (Re-FRI sonrası tekrar)
        "qc2_date": payload.get("qc2_date") or "",
        "qc2_images": payload.get("qc2_images") or [],
        # Depo sevkiyatları: [{date, items:{"Renk|Beden": n}, note_images:[url]}] — kısmi teslimat takibi
        "deliveries": payload.get("deliveries") or [],
        "payments": payload.get("payments", []),
        "cost_lines": payload.get("cost_lines", []),  # F8 – maliyet kalemleri
        "purchase_orders": payload.get("purchase_orders", []),  # F11
        "waste_meters": float(payload.get("waste_meters", 0) or 0),  # F10 – fire
        "supplier_id": supplier_id,  # F7 — zorunlu, kayıtlı imalatçı
        "current_stage": payload.get("current_stage", STAGES[0]),
        "stage_history": [{
            "stage": payload.get("current_stage", STAGES[0]),
            "label": STAGE_LABELS.get(payload.get("current_stage", STAGES[0]), ""),
            "by": current_user.get("email", ""),
            "at": now_iso,
            "note": "Kayıt oluşturuldu",
        }],
        "files": [],  # F5 – ek dosyalar (base64/url list)
        "notes": payload.get("notes", ""),
        "created_at": now_iso,
        "created_by": current_user.get("email", ""),
        "updated_at": now_iso,
    }

    if doc["payment_done"]:
        doc["expected_delivery_date"] = (datetime.now(timezone.utc) + timedelta(days=21)).strftime("%Y-%m-%d")

    # Paid/remaining helpers
    doc["paid_total"] = float(sum(p.get("amount", 0) for p in doc["payments"]))
    doc["remaining"] = doc["agreed_total"] - doc["paid_total"]

    await db.manufacturing.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "record": doc}


@router.put("/{record_id}")
async def update_manufacturing(record_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    existing = await db.manufacturing.find_one({"id": record_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    update = {"updated_at": datetime.now(timezone.utc).isoformat()}
    for f in (
        "product_id", "product_name", "partner_name", "partner_contact",
        "responsible_user", "agreement_date", "expected_delivery_date", "size_distribution",
        "unit_price", "agreed_total", "payments", "cost_lines",
        "purchase_orders", "waste_meters", "supplier_id", "notes",
        "order_no", "order_flags", "stock_code", "colors",
        "has_lining", "color_approvals", "cutting_start_date", "actual_distribution", "product_created",
        "sewing_workshop", "sewing_report_images",
        "qc_result", "qc_date", "qc_images",
        "qc2_result", "qc2_date", "qc2_images", "deliveries",
    ):
        if f in payload:
            update[f] = payload[f]
    if "payment_done" in payload:
        _pd = bool(payload.get("payment_done"))
        update["payment_done"] = _pd
        if _pd and not existing.get("payment_done"):
            _now_dt = datetime.now(timezone.utc)
            update["payment_done_at"] = _now_dt.isoformat()
            # KURAL: 21 günlük üretim saati ÖDEMEYLE başlar → tahmini teslim = ödeme günü + 21
            update["expected_delivery_date"] = (_now_dt + timedelta(days=21)).strftime("%Y-%m-%d")
        elif not _pd:
            update["payment_done_at"] = None
    if "size_distribution" in payload:
        update["total_units"] = sum((payload.get("size_distribution") or {}).values())
    if "payments" in payload:
        pt = float(sum(p.get("amount", 0) for p in (payload.get("payments") or [])))
        update["paid_total"] = pt
        update["remaining"] = float(payload.get("agreed_total", existing.get("agreed_total", 0)) or existing.get("agreed_total", 0)) - pt

    # TARİH İKİLEMESİ FIX: qc_date / cutting_start_date UI'da stage_dates'e de düşüyor
    # (liste ekranı `qc_date || stage_dates.kalite_kontrol` okur). Form bu alanı temizleyince
    # stage_dates karşılığı da temizlenmeli; yoksa ekran eski tarihe geri düşüp "silinmedi"
    # görünür. Dolu → $set, boş → $unset ile stage_dates senkron tutulur.
    unset = {}
    if "qc_date" in payload:
        _v = str(payload.get("qc_date") or "").strip()
        if _v:
            update["stage_dates.kalite_kontrol"] = _v
        else:
            unset["stage_dates.kalite_kontrol"] = ""
    if "cutting_start_date" in payload:
        _v = str(payload.get("cutting_start_date") or "").strip()
        if _v:
            update["stage_dates.kesim"] = _v
        else:
            unset["stage_dates.kesim"] = ""

    ops = {"$set": update}
    if unset:
        ops["$unset"] = unset
    await db.manufacturing.update_one({"id": record_id}, ops)
    return {"success": True}


@router.post("/{record_id}/advance")
async def advance_stage(record_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    rec = await db.manufacturing.find_one({"id": record_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    new_stage = payload.get("stage")
    if new_stage not in STAGES:
        raise HTTPException(status_code=400, detail="Geçersiz aşama")

    history = rec.get("stage_history") or []
    history.append({
        "stage": new_stage,
        "label": STAGE_LABELS[new_stage],
        "by": current_user.get("email", ""),
        "at": datetime.now(timezone.utc).isoformat(),
        "note": payload.get("note", ""),
    })
    update = {
        "current_stage": new_stage,
        "stage_history": history,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Her aşama geçişinde kullanıcı o aşamanın tarihini girer → stage_dates.{aşama}
    _sd = str(payload.get("stage_date") or payload.get("cutting_start_date") or "").strip()[:10]
    if _sd:
        update[f"stage_dates.{new_stage}"] = _sd
        if new_stage == "kesim":
            update["cutting_start_date"] = _sd  # geriye uyumluluk
    # Dikime geçerken atölye adı girilebilir (kullanıcı isteği)
    _ws = str(payload.get("sewing_workshop") or "").strip()
    if _ws:
        update["sewing_workshop"] = _ws

    # F11: On "teslim_alindi" increment stock per size distribution.
    # DENETİM FIX (idempotent): stok artışı SADECE BİR KEZ yapılmalı. Eskiden 'teslim_alindi'ye
    # tekrar geçince (veya eşzamanlı çift tıkta) stok mükerrer artıyordu. Atomik koşullu bayrakla
    # (stock_incremented) yalnız KAZANAN istek artışı yapar.
    _do_increment = False
    if new_stage == "teslim_alindi" and rec.get("product_id") and not rec.get("stock_incremented"):
        _claim = await db.manufacturing.update_one(
            {"id": record_id, "stock_incremented": {"$ne": True}},
            {"$set": {"stock_incremented": True,
                      "delivered_at": datetime.now(timezone.utc).isoformat()}},
        )
        _do_increment = (_claim.modified_count == 1)
    if _do_increment:
        try:
            product = await db.products.find_one({"id": rec["product_id"]}, {"_id": 0, "id": 1, "variants": 1, "stock": 1})
            if product:
                variants = product.get("variants") or []
                size_dist = rec.get("size_distribution") or {}
                total_increment = 0
                # DENETİM FIX (#32): dağıtım yalnız BEDEN ile anahtarlıysa (ör. {"S":10}) ve ürün
                # birden çok RENGE sahipse, eski kod aynı bedendeki TÜM varyantları artırıp stoğu
                # mükerrer şişiriyordu. Artık:
                #  - "Renk|Beden" (ör. "Kırmızı|S") biçimli anahtarlar RENK+BEDEN ile birebir eşleşir;
                #  - çıplak beden anahtarları o beden için YALNIZCA BİR varyanta uygulanır (anahtar
                #    tüketilir) → mükerrer artış olmaz.
                _consumed = set()

                def _vcolor(vd):
                    c = vd.get("color") or vd.get("renk") or ""
                    if not c and isinstance(vd.get("attributes"), dict):
                        c = vd["attributes"].get("color") or vd["attributes"].get("renk") or ""
                    return str(c).strip()

                for v in variants:
                    size_name = (v.get("size") or v.get("name") or "").strip()
                    color_name = _vcolor(v)
                    composite = f"{color_name}|{size_name}" if color_name else None
                    key = None
                    if composite and composite in size_dist and composite not in _consumed:
                        key = composite
                    elif size_name in size_dist and size_name not in _consumed:
                        key = size_name
                    qty = int(size_dist.get(key, 0) or 0) if key else 0
                    if qty > 0:
                        v["stock"] = int(v.get("stock", 0) or 0) + qty
                        total_increment += qty
                        _consumed.add(key)
                if not variants and size_dist:
                    # Product-level stock fallback
                    total_increment = sum(int(v or 0) for v in size_dist.values())
                    await db.products.update_one(
                        {"id": product["id"]},
                        {"$inc": {"stock": total_increment}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}}
                    )
                else:
                    new_total = sum(int(v.get("stock", 0) or 0) for v in variants)
                    await db.products.update_one(
                        {"id": product["id"]},
                        {"$set": {"variants": variants, "stock": new_total, "updated_at": datetime.now(timezone.utc).isoformat()}}
                    )
                await db.stock_movements.insert_one({
                    "id": str(uuid.uuid4()),
                    "type": "manufacturing_delivered",
                    "record_id": record_id,
                    "code": rec.get("code"),
                    "product_id": product["id"],
                    "total_increment": total_increment,
                    "size_distribution": size_dist,
                    "created_by": current_user.get("email", ""),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as e:
            logger.error(f"Stock increment on manufacturing delivery failed: {e}")

    await db.manufacturing.update_one({"id": record_id}, {"$set": update})
    return {"success": True, "new_stage": new_stage}


@router.post("/{record_id}/create-product")
async def create_product_from_manufacturing(record_id: str, current_user: dict = Depends(require_admin)):
    """"Ürünler Kartına Aktar" — imalat kaydından ürün oluşturur (kullanıcı isteği:
    ürün İLK imalatta doğar, son aşamada onaylanınca Ürünler sayfasına aktarılır).

    - Yalnız teslim_alindi / fatura_kesildi aşamasında çalışır.
    - size_distribution ("Renk|Beden" → adet) → variants[{color,size,stock}] map'lenir;
      barkod/urun_id üretimi ve çok renkte renk-başına ürün ayrıştırma products.create_product'ta.
    - Ürün TASLAK (is_active=False) açılır — fiyat/görsel Ürünler sayfasında tamamlanır.
    - Atomik bayrak (product_created) mükerrer aktarımı engeller; stok bu aktarımla
      yazıldığından kayıt stock_incremented=True işaretlenir (çift stok artışı olmaz).
    """
    rec = await db.manufacturing.find_one({"id": record_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    if rec.get("current_stage") not in ("teslim_alindi", "fatura_kesildi"):
        raise HTTPException(status_code=400, detail="Aktarım yalnız 'Teslim Alındı' sonrasında yapılabilir")
    if rec.get("product_created"):
        raise HTTPException(status_code=409, detail="Bu kayıttan ürün zaten oluşturulmuş")
    dist = rec.get("size_distribution") or {}
    if not dist:
        raise HTTPException(status_code=400, detail="Renk/beden dağılımı boş — önce kombinasyon tablosunu doldurun")

    lock = await db.manufacturing.update_one(
        {"id": record_id, "product_created": {"$ne": True}},
        {"$set": {"product_created": True, "updated_at": datetime.now(timezone.utc).isoformat()}})
    if not lock.modified_count:
        raise HTTPException(status_code=409, detail="Aktarım zaten yapılmış")

    variants = []
    for key, qty in dist.items():
        try:
            q = int(qty or 0)
        except Exception:
            q = 0
        if q <= 0:
            continue
        if "|" in str(key):
            color, size = str(key).split("|", 1)
        else:
            color, size = "", str(key)
        variants.append({"size": size.strip(), "color": color.strip(), "stock": q})

    from .products import create_product as _create_product
    payload = {
        "name": rec.get("product_name") or "",
        "stock_code": rec.get("stock_code") or "",
        "variants": variants,
        "manufacturer": rec.get("partner_name") or "FACETTE",
        "purchase_price": float(rec.get("unit_price") or 0),
        "is_active": False,  # taslak — fiyat/görsel tamamlanınca yayına alınır
        "notes": f"İmalat kaydından aktarıldı: {rec.get('code')}",
    }
    try:
        res = await _create_product(payload, current_user)
    except Exception as e:
        # Aktarım başarısızsa bayrağı geri aç (tekrar denenebilsin)
        await db.manufacturing.update_one({"id": record_id}, {"$set": {"product_created": False}})
        raise HTTPException(status_code=500, detail=f"Ürün oluşturulamadı: {e}")

    pids = res.get("product_ids") or ([res.get("id")] if res.get("id") else [])
    await db.manufacturing.update_one(
        {"id": record_id},
        {"$set": {"product_id": (pids[0] if pids else ""), "created_product_ids": pids,
                  "stock_incremented": True,
                  "updated_at": datetime.now(timezone.utc).isoformat()}})
    await db.stock_movements.insert_one({
        "id": str(uuid.uuid4()), "type": "manufacturing_delivered",
        "record_id": record_id, "record_code": rec.get("code"),
        "items": [{"color_size": k, "qty": v} for k, v in dist.items()],
        "created_by": current_user.get("email", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "product_ids": pids,
            "message": f"{len(pids) or 1} ürün kartı oluşturuldu (taslak) — Ürünler sayfasından fiyat/görsel ekleyip yayına alın"}


@router.post("/{record_id}/files")
async def add_file(record_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    rec = await db.manufacturing.find_one({"id": record_id}, {"_id": 0, "files": 1})
    if not rec:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    files = rec.get("files") or []
    files.append({
        "id": str(uuid.uuid4()),
        "name": payload.get("name", "dosya"),
        "url": payload.get("url", ""),
        "stage": payload.get("stage", ""),
        "note": payload.get("note", ""),
        "uploaded_by": current_user.get("email", ""),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.manufacturing.update_one({"id": record_id}, {"$set": {"files": files}})
    return {"success": True, "files": files}


@router.delete("/{record_id}")
async def delete_manufacturing(record_id: str, current_user: dict = Depends(require_admin)):
    res = await db.manufacturing.delete_one({"id": record_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    return {"success": True}


# ----- Suppliers (F7) -----
suppliers_router = APIRouter(prefix="/manufacturing-suppliers", tags=["manufacturing-suppliers"])


@suppliers_router.get("")
async def list_suppliers(current_user: dict = Depends(require_admin)):
    items = await db.manufacturing_suppliers.find({}, {"_id": 0}).sort("name", 1).to_list(500)
    return {"items": items}


@suppliers_router.post("")
async def create_supplier(payload: dict, current_user: dict = Depends(require_admin)):
    name = (payload or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tedarikçi adı gerekli")
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "type": payload.get("type", "atolye"),  # atolye/kumasci/aksesuarci
        "contact": payload.get("contact", ""),
        "phone": payload.get("phone", ""),
        "address": payload.get("address", ""),
        "quality_rating": int(payload.get("quality_rating", 5) or 5),
        "notes": payload.get("notes", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user.get("email", ""),
    }
    await db.manufacturing_suppliers.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "supplier": doc}


@suppliers_router.put("/{sid}")
async def update_supplier(sid: str, payload: dict, current_user: dict = Depends(require_admin)):
    update = {"updated_at": datetime.now(timezone.utc).isoformat()}
    for f in ("name", "type", "contact", "phone", "address", "quality_rating", "notes"):
        if f in payload:
            update[f] = payload[f]
    res = await db.manufacturing_suppliers.update_one({"id": sid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Tedarikçi bulunamadı")
    return {"success": True}


@suppliers_router.delete("/{sid}")
async def delete_supplier(sid: str, current_user: dict = Depends(require_admin)):
    res = await db.manufacturing_suppliers.delete_one({"id": sid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Tedarikçi bulunamadı")
    return {"success": True}
