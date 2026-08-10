"""
Influencer CRM & Seeding & ROI (Modül 3 + 4).

- influencers (master) + influencer_campaigns (detail) MongoDB koleksiyonları.
- Kampanya seeding: MNG Kargo barkod üretimi + Netgsm SMS bildirimleri.
- Paylaşım doğrulaması (manuel "Paylaşıldı Onayı" + Meta webhook stub).
- ROI motoru: GET /api/influencers/{id}/roi — Aggregation ile maliyet vs ciro.

Sipariş eşleştirme: orders.create_order, resolve_influencer_for_order() çağırır:
  1) attribution.aff_id (30 günlük çerez) → influencer
  2) Fallback: order.coupon_code, influencer'ın kuponuyla eşleşirse override.
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from datetime import datetime, timezone
from typing import Optional
import uuid as _uuid

from .deps import db, logger, require_admin, generate_id
from models import Influencer, InfluencerCampaign, DEFAULT_CAMPAIGN_DIRECTIVES

router = APIRouter(tags=["Influencer CRM"])

# Başarılı sayılan sipariş statüleri (ROI ciro hesabı)
SUCCESS_STATUSES = ["confirmed", "processing", "shipped", "delivered"]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _to_int_loose(v, default: int = 0) -> int:
    """Takipçi sayısı gibi alanları TOLERANSLI biçimde int'e çevirir.
    Türkçe binlik ayracı (nokta/boşluk) ve K/B(bin)/M(milyon) ekleri desteklenir.
    Sebep: panel `type=number` inputu Türk kullanıcının yazdığı "125.500" değerini
    JS `Number()` ile 125.5 (kesirli float) yapıyordu; Pydantic `int` bunu reddedip
    kaydı 500 ile düşürüyordu ("takipçi girince kaydolmuyor" bug'ı). Artık asla patlamaz."""
    if v is None:
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        try:
            return int(round(v))
        except Exception:
            return default
    import re as _re
    s = str(v).strip().lower().replace(" ", "")
    if not s:
        return default
    m = _re.match(r"^([\d.,]+)(k|b|bin|m|mn|milyon)?$", s)
    if m:
        num, suf = m.group(1), m.group(2)
        # Türkçe: virgül = ondalık, nokta = binlik ayraç
        num = num.replace(".", "").replace(",", ".")
        try:
            val = float(num)
        except Exception:
            val = None
        if val is not None:
            if suf in ("k", "b", "bin"):
                val *= 1000
            elif suf in ("m", "mn", "milyon"):
                val *= 1_000_000
            return int(round(val))
    digits = _re.sub(r"\D", "", s)
    return int(digits) if digits else default


# =============================================================================
# Sipariş eşleştirme yardımcı fonksiyonu (orders.py'den çağrılır)
# =============================================================================

async def resolve_influencer_for_order(aff_id: Optional[str], coupon_code: Optional[str]) -> Optional[dict]:
    """Bir siparişi influencer'a bağlar.
    Öncelik: aff_id (çerez) → kupon (fallback override).
    Dönüş: {influencer_id, name, via} veya None."""
    inf = None
    via = None

    aff_id = (aff_id or "").strip()
    if aff_id:
        inf = await db.influencers.find_one({"aff_id": aff_id, "is_active": True}, {"_id": 0})
        if inf:
            via = "aff_id"

    if not inf:
        code = (coupon_code or "").strip().upper()
        if code:
            import re as _re_i
            inf = await db.influencers.find_one(
                {"coupon_code": {"$regex": f"^{_re_i.escape(code)}$", "$options": "i"}, "is_active": True},
                {"_id": 0},
            )
            if inf:
                via = "coupon"

    if not inf:
        return None
    return {"influencer_id": inf["id"], "name": inf.get("name", ""), "via": via}


# =============================================================================
# Direkt SMS (Netgsm) — template gerektirmeden
# =============================================================================

async def _send_direct_sms(phone: str, message: str) -> dict:
    try:
        from notification_service import _get_providers_config, SMS_IMPL, _sms_generic, normalize_phone_tr
        to = normalize_phone_tr(phone)
        if not to:
            return {"success": False, "response": "phone empty"}
        cfg = await _get_providers_config(db)
        providers = cfg.get("providers", {})
        sms_active = cfg.get("sms_active")
        if not sms_active:
            return {"success": False, "response": "no active sms provider"}
        impl = SMS_IMPL.get(sms_active, _sms_generic)
        prov_cfg = providers.get(sms_active, {})
        return await impl(prov_cfg, to, message)
    except Exception as e:
        logger.warning(f"Influencer SMS failed: {e}")
        return {"success": False, "response": str(e)}


# =============================================================================
# INFLUENCERS CRUD
# =============================================================================

@router.post("/influencers")
async def create_influencer(payload: dict, current_user: dict = Depends(require_admin)):
    if payload is not None and "follower_count" in payload:
        payload["follower_count"] = _to_int_loose(payload.get("follower_count"))
    model = Influencer(**payload)
    doc = model.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    doc["updated_at"] = doc["updated_at"].isoformat()
    if doc.get("coupon_code"):
        doc["coupon_code"] = doc["coupon_code"].upper()
    await db.influencers.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "influencer": doc}


@router.get("/influencers")
async def list_influencers(
    q: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    current_user: dict = Depends(require_admin),
):
    query = {}
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"handle": {"$regex": q, "$options": "i"}},
            {"instagram": {"$regex": q, "$options": "i"}},
            {"tiktok": {"$regex": q, "$options": "i"}},
            {"phone": {"$regex": q, "$options": "i"}},
            {"coupon_code": {"$regex": q, "$options": "i"}},
        ]
    if platform:
        query["platform"] = platform
    if is_active is not None:
        query["is_active"] = is_active
    docs = await db.influencers.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"influencers": docs, "total": len(docs)}


@router.get("/influencers/{influencer_id}")
async def get_influencer(influencer_id: str, current_user: dict = Depends(require_admin)):
    doc = await db.influencers.find_one({"id": influencer_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Influencer bulunamadı")
    campaigns = await db.influencer_campaigns.find(
        {"influencer_id": influencer_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(200)
    doc["campaigns"] = campaigns
    return doc


@router.put("/influencers/{influencer_id}")
async def update_influencer(influencer_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    existing = await db.influencers.find_one({"id": influencer_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Influencer bulunamadı")
    allowed = {
        "name", "platform", "handle", "instagram", "tiktok", "birthday",
        "phone", "email", "follower_count",
        "coupon_code", "aff_id", "commission_rate", "shipping_address", "notes", "is_active",
    }
    update = {k: v for k, v in payload.items() if k in allowed}
    if "follower_count" in update:
        update["follower_count"] = _to_int_loose(update.get("follower_count"))
    if "coupon_code" in update and update["coupon_code"]:
        update["coupon_code"] = update["coupon_code"].upper()
    update["updated_at"] = _now_iso()
    await db.influencers.update_one({"id": influencer_id}, {"$set": update})
    doc = await db.influencers.find_one({"id": influencer_id}, {"_id": 0})
    return {"success": True, "influencer": doc}


@router.delete("/influencers/{influencer_id}")
async def delete_influencer(influencer_id: str, current_user: dict = Depends(require_admin)):
    res = await db.influencers.delete_one({"id": influencer_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Influencer bulunamadı")
    await db.influencer_campaigns.delete_many({"influencer_id": influencer_id})
    return {"success": True}


# =============================================================================
# CAMPAIGNS CRUD
# =============================================================================

@router.post("/influencers/{influencer_id}/campaigns")
async def create_campaign(influencer_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    inf = await db.influencers.find_one({"id": influencer_id}, {"_id": 0})
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer bulunamadı")
    payload["influencer_id"] = influencer_id
    # directives boş gönderilse bile zorunlu standartlar yazılsın
    if not (payload.get("directives") or "").strip():
        payload["directives"] = DEFAULT_CAMPAIGN_DIRECTIVES
    model = InfluencerCampaign(**payload)
    doc = model.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    doc["updated_at"] = doc["updated_at"].isoformat()
    await db.influencer_campaigns.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "campaign": doc}


@router.get("/influencers/{influencer_id}/campaigns")
async def list_campaigns(influencer_id: str, current_user: dict = Depends(require_admin)):
    docs = await db.influencer_campaigns.find(
        {"influencer_id": influencer_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(200)
    return {"campaigns": docs, "total": len(docs)}


@router.get("/influencer-campaigns")
async def list_all_campaigns(
    q: Optional[str] = Query(None),
    shared: Optional[bool] = Query(None),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin),
):
    """TÜM influencer gönderimleri (ürün yollama geçmişi) — her kayıt influencer
    adı/hesabıyla zenginleştirilir. 'Gönderim Geçmişi' sekmesini besler.
    Filtreler: q (başlık/influencer/ürün adı), shared (paylaşıldı mı), status."""
    query = {}
    if shared is not None:
        query["shared"] = shared
    if status:
        query["status"] = status
    camps = await db.influencer_campaigns.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)
    inf_ids = list({c.get("influencer_id") for c in camps if c.get("influencer_id")})
    infs = {}
    if inf_ids:
        cursor = db.influencers.find(
            {"id": {"$in": inf_ids}},
            {"_id": 0, "id": 1, "name": 1, "handle": 1, "instagram": 1, "tiktok": 1, "platform": 1},
        )
        async for d in cursor:
            infs[d["id"]] = d
    ql = (q or "").strip().lower()
    out = []
    for c in camps:
        inf = infs.get(c.get("influencer_id"), {})
        c["influencer_name"] = inf.get("name", "")
        c["influencer_handle"] = inf.get("handle") or inf.get("instagram") or inf.get("tiktok") or ""
        c["influencer_platform"] = inf.get("platform", "")
        if ql:
            hay = " ".join([
                str(c.get("title") or ""),
                str(c["influencer_name"]),
                str(c["influencer_handle"]),
                " ".join([str((p or {}).get("name") or "") for p in (c.get("sent_products") or [])]),
            ]).lower()
            if ql not in hay:
                continue
        out.append(c)
    return {"campaigns": out, "total": len(out)}


@router.put("/influencer-campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    existing = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    allowed = {
        "title", "fee_paid", "product_cost", "cargo_cost", "sent_products",
        "directives", "status", "cargo_status", "cargo_barcode", "cargo_tracking_no",
        "content_url", "notes", "sent_at", "shared", "shared_at",
    }
    update = {k: v for k, v in payload.items() if k in allowed}
    # Paylaşım tik'i (checkbox toggle): shared True↔False; shared_at otomatik damgalanır/silinir.
    if "shared" in update:
        if update["shared"]:
            update.setdefault("shared_at", _now_iso())
            if (existing.get("status") or "draft") in ("draft", "shipped"):
                update.setdefault("status", "shared")
        else:
            update["shared_at"] = None
    update["updated_at"] = _now_iso()
    await db.influencer_campaigns.update_one({"id": campaign_id}, {"$set": update})
    # Kampanya iptal edilirse düşülen seeding stoğu otomatik geri döner (idempotent).
    if update.get("status") == "cancelled" and existing.get("stock_deducted"):
        try:
            await _seeding_restock({**existing, **update, "id": campaign_id}, reason="campaign_cancelled")
        except Exception as e:
            logger.warning(f"[influencer] iptalde stok iadesi başarısız {campaign_id}: {e}")
    doc = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    return {"success": True, "campaign": doc}


@router.delete("/influencer-campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str, current_user: dict = Depends(require_admin)):
    # Stok düşülmüş kampanya silinirse ürünler stoğa geri döner (kayıp olmasın).
    camp = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if camp and camp.get("stock_deducted"):
        try:
            await _seeding_restock(camp, reason="campaign_deleted")
        except Exception as e:
            logger.warning(f"[influencer] silmede stok iadesi başarısız {campaign_id}: {e}")
    res = await db.influencer_campaigns.delete_one({"id": campaign_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    return {"success": True}


# =============================================================================
# SEEDING — Gönderilecek ürünler: seçim + STOK DÜŞÜMÜ (atomik, idempotent)
# =============================================================================

async def _resolve_seeding_items(products: list) -> list:
    """[{barcode, qty}] → doğrulanmış [{barcode, quantity, product_id, name, size, unit_cost}].
    Barkod varyantta veya üründe aranır; bulunamayan/stok yetmeyen kalem hata verir."""
    items = []
    for p in products or []:
        bc = str((p or {}).get("barcode") or "").strip()
        try:
            qty = max(1, int((p or {}).get("qty") or (p or {}).get("quantity") or 1))
        except Exception:
            qty = 1
        if not bc:
            continue
        prod = await db.products.find_one(
            {"$or": [{"variants.barcode": bc}, {"barcode": bc}]},
            {"_id": 0, "id": 1, "name": 1, "price": 1, "cost_price": 1, "variants": 1, "stock": 1})
        if not prod:
            raise HTTPException(status_code=400, detail=f"Barkod bulunamadı: {bc}")
        size = ""
        stock = prod.get("stock")
        for v in (prod.get("variants") or []):
            if str(v.get("barcode") or "").strip() == bc:
                size = v.get("size") or ""
                stock = v.get("stock")
                break
        if stock is not None and int(stock or 0) < qty:
            raise HTTPException(status_code=409,
                                detail=f"Stok yetersiz: {prod.get('name')} {size} (stok {stock}, istenen {qty})")
        # Birim maliyet: manuel product_costs > products.cost_price > fiyatın %50'si (rapor motoruyla aynı kural)
        cost_rec = await db.product_costs.find_one({"product_id": prod["id"]}, {"_id": 0, "cost_price": 1})
        unit_cost = float((cost_rec or {}).get("cost_price") or prod.get("cost_price") or 0) \
            or float(prod.get("price") or 0) * 0.5
        items.append({"barcode": bc, "quantity": qty, "product_id": prod["id"],
                      "name": prod.get("name") or "", "size": size,
                      "unit_cost": round(unit_cost, 2)})
    if not items:
        raise HTTPException(status_code=400, detail="Geçerli ürün seçilmedi")
    return items


async def _seeding_restock(camp: dict, reason: str) -> None:
    """Kampanyanın düşülen stoğunu İDEMPOTENT geri yükler (atomik bayrak kilidi)."""
    lock = await db.influencer_campaigns.update_one(
        {"id": camp["id"], "stock_deducted": True},
        {"$set": {"stock_deducted": False, "updated_at": _now_iso()}})
    if not lock.modified_count:
        return  # zaten iade edilmiş / hiç düşülmemiş
    from .orders import _stock_delta_for_order
    items = [{"barcode": p.get("barcode"), "quantity": p.get("qty") or p.get("quantity") or 1}
             for p in (camp.get("sent_products") or []) if p.get("barcode")]
    moves = await _stock_delta_for_order({"items": items}, +1)
    await db.stock_movements.insert_one({
        "id": str(_uuid.uuid4()), "type": "influencer_seeding_restock",
        "campaign_id": camp["id"], "influencer_id": camp.get("influencer_id"),
        "reason": reason, "items": moves, "created_at": _now_iso()})
    logger.info(f"[influencer] seeding stok iadesi: kampanya {camp['id']} ({reason})")


@router.post("/influencer-campaigns/{campaign_id}/commit-products")
async def commit_campaign_products(campaign_id: str, payload: dict,
                                   current_user: dict = Depends(require_admin)):
    """Gönderilecek ürünleri kampanyaya işler ve STOKTAN DÜŞER.

    Body: {"products": [{"barcode": "868...", "qty": 1}], "auto_cost": true}
    - sent_products doldurulur (ad/beden/barkod/adet), stok stock_movements'a
      'influencer_seeding' hareketiyle düşülür (çift işleme atomik bayrakla kapalı).
    - auto_cost (varsayılan açık): ürün maliyeti toplamı product_cost'a yazılır
      (rapordaki maliyet kuralıyla aynı: manuel maliyet > cost_price > fiyat*0.5) → ROI gerçekçi.
    """
    camp = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if camp.get("stock_deducted"):
        raise HTTPException(status_code=409,
                            detail="Bu kampanyanın stoğu zaten düşülmüş — önce 'Stok İadesini Geri Al' kullanın")
    items = await _resolve_seeding_items((payload or {}).get("products"))

    # Atomik kilit: aynı anda iki commit gelirse yalnız biri düşer.
    lock = await db.influencer_campaigns.update_one(
        {"id": campaign_id, "stock_deducted": {"$ne": True}},
        {"$set": {"stock_deducted": True, "updated_at": _now_iso()}})
    if not lock.modified_count:
        raise HTTPException(status_code=409, detail="Stok bu kampanya için zaten düşülmüş")

    from .orders import _stock_delta_for_order
    moves = await _stock_delta_for_order({"items": items}, -1)
    await db.stock_movements.insert_one({
        "id": str(_uuid.uuid4()), "type": "influencer_seeding",
        "campaign_id": campaign_id, "influencer_id": camp.get("influencer_id"),
        "items": moves, "created_by": current_user.get("email", ""),
        "created_at": _now_iso()})

    sent = [{"name": it["name"], "barcode": it["barcode"], "size": it["size"], "qty": it["quantity"]}
            for it in items]
    upd = {"sent_products": sent, "updated_at": _now_iso()}
    total_cost = round(sum(it["unit_cost"] * it["quantity"] for it in items), 2)
    if (payload or {}).get("auto_cost", True) and total_cost > 0:
        upd["product_cost"] = total_cost
    await db.influencer_campaigns.update_one({"id": campaign_id}, {"$set": upd})
    doc = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    return {"success": True, "campaign": doc, "stock_moves": len(moves), "product_cost": total_cost}


@router.post("/influencer-campaigns/{campaign_id}/uncommit-products")
async def uncommit_campaign_products(campaign_id: str, current_user: dict = Depends(require_admin)):
    """Düşülen seeding stoğunu geri yükler (yanlış seçim / kampanya vazgeçildi)."""
    camp = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if not camp.get("stock_deducted"):
        raise HTTPException(status_code=400, detail="Bu kampanya için düşülmüş stok yok")
    await _seeding_restock(camp, reason="manual_uncommit")
    doc = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    return {"success": True, "campaign": doc}


# =============================================================================
# SEEDING — Kargo barkod üretimi (MNG) + SMS
# =============================================================================

@router.post("/influencer-campaigns/{campaign_id}/cargo")
async def create_campaign_cargo(campaign_id: str, current_user: dict = Depends(require_admin)):
    """Influencer seeding gönderisi için MNG kargo barkodu üretir ve SMS atar."""
    camp = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    inf = await db.influencers.find_one({"id": camp["influencer_id"]}, {"_id": 0})
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer bulunamadı")

    addr = inf.get("shipping_address") or {}
    il = (addr.get("il") or addr.get("city") or "").strip()
    ilce = (addr.get("ilce") or addr.get("district") or "").strip()
    adres = (addr.get("adres") or addr.get("address") or "").strip()
    full_name = (addr.get("full_name") or inf.get("name") or "").strip()
    phone = (addr.get("phone") or inf.get("phone") or "").strip()
    if not (il and ilce and adres and phone):
        raise HTTPException(status_code=400, detail="Influencer kargo adresi eksik (il, ilçe, adres, telefon gerekli)")

    # MNG ayarları (helper orders.py içinde tanımlı)
    try:
        from .orders import _get_mng_settings
        mng = await _get_mng_settings()
    except Exception as e:
        logger.warning(f"MNG settings load failed: {e}")
        raise HTTPException(status_code=400, detail="Kargo entegrasyonu yapılandırılmamış")
    username = mng.get("username")
    password = mng.get("password")
    if not (username and password):
        raise HTTPException(status_code=400, detail="MNG Kargo ayarları yapılmamış")

    siparis_no = f"INF{campaign_id[:8].upper()}"
    icerik = ", ".join([p.get("name", "Ürün") for p in (camp.get("sent_products") or [])]) or "Numune Ürün"

    from mng_kargo_client import create_shipment
    from fastapi.concurrency import run_in_threadpool

    def _ship():
        return create_shipment(
            username=username, password=password, siparis_no=siparis_no,
            icerik=icerik, hizmet_sekli="NORMAL", teslim_sekli=1,
            al_sms=0, gn_sms=1 if phone else 0,
            parca_list="1:1:20:30:15:;",
            alici_ad=full_name, il=il, ilce=ilce, adres=adres,
            tel_cep=phone, odeme_sekli="P", platform_adi="", platform_kodu="",
        )

    try:
        res = await run_in_threadpool(_ship)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MNG kargo hatası: {e}")

    barkod = res.get("barkod") or ""
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=f"Kargo oluşturulamadı: {res.get('hata') or res}")

    await db.influencer_campaigns.update_one(
        {"id": campaign_id},
        {"$set": {
            "cargo_status": "created",
            "cargo_barcode": barkod,
            "cargo_tracking_no": siparis_no,
            "status": "shipped" if camp.get("status") == "draft" else camp.get("status"),
            "sent_at": camp.get("sent_at") or _now_iso(),   # gönderim tarihi (kargoya verildi)
            "updated_at": _now_iso(),
        }},
    )

    # SMS bildirimi (influencer'a)
    sms_res = await _send_direct_sms(
        phone,
        f"Merhaba {inf.get('name','')}, Facette numune gonderiniz hazirlandi. Takip: {siparis_no}",
    )

    return {"success": True, "cargo_barcode": barkod, "tracking_no": siparis_no, "sms": sms_res}


@router.post("/influencers/cargo-webhook")
async def influencer_cargo_webhook(payload: dict, request: Request):
    """Kargo statü değişimi webhook'u → influencer'a SMS.
    Payload: { tracking_no | siparis_no, status }  (Kargoya Verildi / Teslim Edildi).
    GÜVENLİK: Paylaşımlı secret zorunlu (fail-closed) — aksi halde herkes ücretli SMS
    tetikleyip kargo durumu forge edebiliyordu."""
    import os as _os, hmac as _hmac
    _secret = (_os.environ.get("INFLUENCER_WEBHOOK_SECRET", "") or "").strip()
    if not _secret:
        _cfg = await db.settings.find_one({"id": "influencer"}, {"_id": 0, "webhook_secret": 1}) or {}
        _secret = str(_cfg.get("webhook_secret") or "").strip()
    _provided = (request.headers.get("X-Webhook-Secret")
                 or request.query_params.get("key") or "").strip()
    if not _secret or not _provided or not _hmac.compare_digest(_secret, _provided):
        raise HTTPException(status_code=401, detail="Yetkisiz webhook")
    tracking = (payload.get("tracking_no") or payload.get("siparis_no") or "").strip()
    status = (payload.get("status") or "").strip()
    if not tracking:
        raise HTTPException(status_code=400, detail="tracking_no gerekli")

    camp = await db.influencer_campaigns.find_one({"cargo_tracking_no": tracking}, {"_id": 0})
    if not camp:
        return {"success": False, "message": "Eşleşen kampanya yok"}

    inf = await db.influencers.find_one({"id": camp["influencer_id"]}, {"_id": 0})

    status_lower = status.lower()
    new_cargo_status = camp.get("cargo_status")
    sms_text = None
    if "teslim" in status_lower or "delivered" in status_lower:
        new_cargo_status = "delivered"
        sms_text = "Facette numune gonderiniz teslim edildi. Icerik standartlari: 9:16 dikey format, @facette mention. Iyi cekimler!"
    elif "kargo" in status_lower or "shipped" in status_lower or "yola" in status_lower:
        new_cargo_status = "shipped"
        sms_text = f"Facette numune gonderiniz kargoya verildi. Takip: {tracking}"

    await db.influencer_campaigns.update_one(
        {"id": camp["id"]},
        {"$set": {"cargo_status": new_cargo_status, "updated_at": _now_iso()}},
    )

    sms_res = None
    if sms_text and inf and (inf.get("phone") or (inf.get("shipping_address") or {}).get("phone")):
        phone = inf.get("phone") or (inf.get("shipping_address") or {}).get("phone")
        sms_res = await _send_direct_sms(phone, sms_text)

    return {"success": True, "cargo_status": new_cargo_status, "sms": sms_res}


# =============================================================================
# PAYLAŞIM DOĞRULAMASI
# =============================================================================

@router.post("/influencer-campaigns/{campaign_id}/confirm-share")
async def confirm_share(campaign_id: str, payload: dict = None, current_user: dict = Depends(require_admin)):
    """Manuel 'Paylaşıldı Onayı'. payload: { content_url? }"""
    camp = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    content_url = ((payload or {}).get("content_url") or "").strip()
    await db.influencer_campaigns.update_one(
        {"id": campaign_id},
        {"$set": {
            "shared": True,
            "shared_at": _now_iso(),
            "content_url": content_url or camp.get("content_url"),
            "status": "shared",
            "updated_at": _now_iso(),
        }},
    )
    doc = await db.influencer_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    return {"success": True, "campaign": doc}


@router.post("/influencers/meta-webhook")
async def meta_share_webhook(payload: dict):
    """Meta mention webhook stub — markayı mention'layan içeriği yakalar.
    Üretimde Meta App webhook subscription'a bağlanır (verify token + signature)."""
    logger.info(f"Meta mention webhook received: {str(payload)[:300]}")
    return {"success": True, "received": True}


# =============================================================================
# ROI MOTORU (Modül 4)
# =============================================================================

@router.get("/influencers/{influencer_id}/roi")
async def get_influencer_roi(influencer_id: str, current_user: dict = Depends(require_admin)):
    """Influencer ROI: Toplam Maliyet (ücret+ürün+kargo) vs Ciro → Net Kâr & ROAS."""
    inf = await db.influencers.find_one({"id": influencer_id}, {"_id": 0})
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer bulunamadı")

    # --- Maliyet (kampanya aggregation) ---
    cost_pipeline = [
        {"$match": {"influencer_id": influencer_id}},
        {"$group": {
            "_id": None,
            "fee_paid": {"$sum": {"$ifNull": ["$fee_paid", 0]}},
            "product_cost": {"$sum": {"$ifNull": ["$product_cost", 0]}},
            "cargo_cost": {"$sum": {"$ifNull": ["$cargo_cost", 0]}},
            "campaign_count": {"$sum": 1},
            "shared_count": {"$sum": {"$cond": [{"$eq": ["$shared", True]}, 1, 0]}},
        }},
    ]
    cost_agg = await db.influencer_campaigns.aggregate(cost_pipeline).to_list(1)
    c = cost_agg[0] if cost_agg else {}
    fee_paid = float(c.get("fee_paid", 0) or 0)
    product_cost = float(c.get("product_cost", 0) or 0)
    cargo_cost = float(c.get("cargo_cost", 0) or 0)
    total_cost = fee_paid + product_cost + cargo_cost

    # --- Ciro (başarılı siparişler) ---
    rev_pipeline = [
        {"$match": {
            "influencer_id": influencer_id,
            "status": {"$in": SUCCESS_STATUSES},
        }},
        {"$group": {
            "_id": None,
            "revenue": {"$sum": {"$ifNull": ["$total", 0]}},
            "order_count": {"$sum": 1},
        }},
    ]
    rev_agg = await db.orders.aggregate(rev_pipeline).to_list(1)
    r = rev_agg[0] if rev_agg else {}
    revenue = float(r.get("revenue", 0) or 0)
    order_count = int(r.get("order_count", 0) or 0)

    # Toplam (iptal/iade dahil) sipariş sayısı — dönüşüm görünürlüğü için
    total_orders = await db.orders.count_documents({"influencer_id": influencer_id})

    net_profit = revenue - total_cost
    roas = round(revenue / total_cost, 2) if total_cost > 0 else None
    commission = round(revenue * float(inf.get("commission_rate", 0) or 0) / 100.0, 2)

    return {
        "influencer_id": influencer_id,
        "influencer_name": inf.get("name", ""),
        "cost": {
            "fee_paid": round(fee_paid, 2),
            "product_cost": round(product_cost, 2),
            "cargo_cost": round(cargo_cost, 2),
            "total_cost": round(total_cost, 2),
            "campaign_count": int(c.get("campaign_count", 0) or 0),
            "shared_count": int(c.get("shared_count", 0) or 0),
        },
        "revenue": {
            "revenue": round(revenue, 2),
            "successful_orders": order_count,
            "total_orders": total_orders,
            "commission_due": commission,
        },
        "net_profit": round(net_profit, 2),
        "roas": roas,
    }
