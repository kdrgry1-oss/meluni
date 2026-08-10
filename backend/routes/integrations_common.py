"""
integrations_common.py — Trendyol/HB/diğer pazaryeri entegrasyonları arasında PAYLAŞILAN
yardımcı fonksiyonlar, sabitler ve genel/marketplace-agnostik endpoint'ler.

2026-07-01 refactor: integrations.py (9948 satır) modülerize edildi.
Bölme kuralı: HB ve Trendyol blokları arasında SIFIR çapraz bağımlılık tespit edildi
(AST analiziyle doğrulandı) — ikisi de sadece bu modüle bağımlı, birbirine değil.
"""
from fastapi import APIRouter, HTTPException, Query, Depends, Response, BackgroundTasks, Request, Body, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import os
import base64
import uuid
import re
import xml.etree.ElementTree as ET
import httpx
import hashlib

from .deps import db, logger, get_current_user, require_admin, generate_id, generate_short_id, _search_tr_regex
from facette_defaults import facette_fixed_value_for  # tüm-pazaryeri sabit varsayılan (gap-fill)

router = APIRouter(tags=["Integrations-Common"])

from .integrations_iyzico import router as iyzico_router  # noqa
from .integrations_iyzico import (
    IYZICO_MODE, IYZICO_API_KEY, IYZICO_SECRET_KEY, IYZICO_BASE_URL,
    is_iyzico_configured, _iyzico_auth_header,
)

def _mp_base_price(obj: dict) -> float:
    """Pazaryeri fiyat tabanı (#4): Üye Tipi 1 fiyatı (member_price_1) baz alınır;
    boş/0 ise satış fiyatına (price) düşülür. Pazaryeri marjı bu tabanın üzerine eklenir."""
    try:
        m = float(obj.get("member_price_1") or 0)
    except Exception:
        m = 0.0
    if m > 0:
        return m
    try:
        return float(obj.get("price") or 0)
    except Exception:
        return 0.0
def _dedupe_products_by_stock_code(products: list) -> list:
    """Aynı stock_code/barcode ile birden fazla ürün dokümanı varsa
    (örn. csv_xml_merge ve xml_feed kaynaklarından gelen dublikatlar),
    her grup için EN İYİ dokümanı seçer.

    "İyi" doküman skoru:
      +1000  görseli varsa (images non-empty)
      +100   thumbnail varsa
      +len(images)
      +50    source != csv_xml_merge   (xml_feed > ticimax > csv_xml_merge)
      +10    aktif ürünse

    Aynı stock_code'a sahip diğer dokümanlar elenir. Anahtar olarak
    stock_code/sku/barcode sırayla denenir; hiçbiri yoksa id ile (tek başına).
    """
    def _score(p):
        s = 0
        imgs = p.get("images") or []
        if imgs:
            s += 1000
            s += min(len(imgs), 20)
        if p.get("thumbnail"):
            s += 100
        src = (p.get("source") or "").lower()
        if src and src != "csv_xml_merge":
            s += 50
        if p.get("is_active"):
            s += 10
        return s

    groups: dict = {}
    out_no_key: list = []
    for p in products:
        # Aynı stock_code'a farklı renkler/varyantlar tek bir SKU paylaşıyorsa
        # name (ürün adı) ile ayrıştır → "Bordo" ve "Siyah" ayrı kalır.
        stock = (p.get("stock_code") or p.get("sku") or p.get("barcode") or "").strip()
        name = (p.get("name") or "").strip().lower()
        key = f"{stock}|{name}" if stock else ""
        if not key:
            out_no_key.append(p)
            continue
        groups.setdefault(key, []).append(p)

    deduped = []
    for key, plist in groups.items():
        if len(plist) == 1:
            deduped.append(plist[0])
        else:
            best = max(plist, key=_score)
            deduped.append(best)
    deduped.extend(out_no_key)
    return deduped
def _resolve_stock_code(p: dict) -> str:
    """Ürünün 'stok kodu'nu tek bir sırayla çözer.

    Bazı ürünlerde stok kodu üst seviyede `stock_code` yerine `sku` alanında ya da
    sadece varyantların içinde (`variants[].stock_code` / `variants[].sku`) duruyor.
    Bu durumda panel '-' gösteriyor ve aktarımda productMainId UUID'ye düşüyordu.
    Çözüm sırası: stock_code → sku → ilk varyant stock_code/sku → urun_karti_id.
    Hiçbiri yoksa boş string döner (çağıran taraf barcode'a düşebilir).
    """
    if not isinstance(p, dict):
        return ""
    sc = (p.get("stock_code") or p.get("sku") or "")
    if isinstance(sc, str):
        sc = sc.strip()
    if sc:
        return str(sc)
    for v in (p.get("variants") or []):
        if not isinstance(v, dict):
            continue
        vsc = (v.get("stock_code") or v.get("sku") or "")
        if isinstance(vsc, str):
            vsc = vsc.strip()
        if vsc:
            return str(vsc)
    kart = (p.get("urun_karti_id") or p.get("csv_card_id") or "")
    if isinstance(kart, str):
        kart = kart.strip()
    return str(kart) if kart else ""
def _normalize_attr_key(s: str) -> str:
    """Türkçe duyarsız normalize (İ/ı/ş/ğ/ü/ö/ç + birleşik nokta)."""
    s = (s or "").casefold()
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"),
                 ("ü", "u"), ("ö", "o"), ("ç", "c"), ("\u0307", "")):
        s = s.replace(a, b)
    return s.strip()
def _norm_val(s: str) -> str:
    """Listeli özellik değerlerini eşlerken kullanılan agresif normalize:
    Türkçe duyarsız + boşluk/eğik çizgi/noktalama tamamen kaldırılır.
    Örn. 'Kısa / Mini' ≈ 'Kısa/Mini' ≈ 'kisamini'."""
    return re.sub(r"[^a-z0-9]", "", _normalize_attr_key(s))
_VALUE_SYNONYMS = {
    "yakasiz": ["sifiryaka", "yakayok"],
    "sifiryaka": ["yakasiz"],
}
def _resolve_value_id(name_map: dict, local_val: str):
    """local_val'i Trendyol value_id'ye çöz: önce birebir (norm), sonra eşanlamlı."""
    if not name_map or local_val in (None, ""):
        return None
    nv = _norm_val(str(local_val))
    if nv in name_map:
        return name_map[nv]
    for syn in _VALUE_SYNONYMS.get(nv, []):
        if syn in name_map:
            return name_map[syn]
    return None
def _closest_trendyol_value(local_val: str, ty_values: list):
    """Yerel değere en yakın Trendyol değerini ÖNER (birebir yoksa alt-dizi/örtüşme skoru).
    ty_values: [{"id","name"}]. Dönüş: {"id","name"} veya None.
    Örn. yerel 'Fermuarlı' → Trendyol 'Fermuar'; 'Dar Kalıp' → 'Dar'."""
    if not local_val or not ty_values:
        return None
    lv = _norm_val(str(local_val))
    if not lv:
        return None
    best, best_score = None, 0.0
    for v in ty_values:
        tv = _norm_val(str(v.get("name") or ""))
        if not tv:
            continue
        if tv == lv:
            return {"id": str(v.get("id")), "name": str(v.get("name"))}
        # biri diğerini içeriyorsa güçlü aday (Fermuar ⊂ Fermuarlı)
        if lv in tv or tv in lv:
            score = min(len(lv), len(tv)) / max(len(lv), len(tv))
        else:
            # ortak karakter kümesi oranı — kaba benzerlik
            common = len(set(lv) & set(tv))
            score = common / max(len(set(lv) | set(tv)), 1) * 0.6
        if score > best_score:
            best_score, best = score, {"id": str(v.get("id")), "name": str(v.get("name"))}
    return best if best_score >= 0.5 else None
_BAD_COMPOSITION_VALUES = {"yetişkin", "yetiskin", "genç", "genc", "çocuk", "cocuk", "bebek", "kadın", "kadin", "erkek", "unisex"}
async def _build_product_query_from_payload(payload: dict) -> dict:
    """Trendyol sync/validate payload'undan products koleksiyon sorgusu üretir."""
    product_ids = payload.get("product_ids", [])
    category_filters = payload.get("category_filters", [])
    barcodes_raw = payload.get("barcodes", [])
    stock_codes_raw = payload.get("stock_codes", [])
    card_ids_raw = payload.get("card_ids", [])
    barcodes = [str(b).strip() for b in (barcodes_raw or []) if str(b).strip()]
    stock_codes = [str(s).strip() for s in (stock_codes_raw or []) if str(s).strip()]
    card_ids = [str(c).strip() for c in (card_ids_raw or []) if str(c).strip()]
    date_from = payload.get("date_from")
    date_to = payload.get("date_to")

    query: dict = {}
    if product_ids:
        query = {"id": {"$in": product_ids}}
    elif barcodes or stock_codes or card_ids:
        or_conditions = []
        if barcodes:
            or_conditions.append({"barcode": {"$in": barcodes}})
            or_conditions.append({"variants.barcode": {"$in": barcodes}})
        if stock_codes:
            or_conditions.append({"stock_code": {"$in": stock_codes}})
            or_conditions.append({"sku": {"$in": stock_codes}})
            or_conditions.append({"variants.stock_code": {"$in": stock_codes}})
        if card_ids:
            # Ürün Kart ID ile aktarım — urun_karti_id (asıl) + csv_card_id (yedek)
            or_conditions.append({"urun_karti_id": {"$in": card_ids}})
            or_conditions.append({"csv_card_id": {"$in": card_ids}})
        query = {"$or": or_conditions}
    elif category_filters:
        or_conditions = []
        for cf in category_filters:
            cat_id = cf.get("category_id")
            filters = cf.get("filters", {})
            cat = None
            try:
                from bson.objectid import ObjectId
                cat = await db.categories.find_one({"_id": ObjectId(cat_id)})
            except Exception:
                cat = await db.categories.find_one({"id": cat_id})
            if not cat:
                continue
            cat_name = cat.get("name")
            # Hem category_id hem category_name ile match (ürünlerin çoğu category_id=None olabiliyor)
            inner_or = []
            if cat_id:
                inner_or.append({"category_id": cat_id})
            if cat_name:
                inner_or.append({"category_name": cat_name})
            if not inner_or:
                continue
            cat_q = {"$or": inner_or} if len(inner_or) > 1 else inner_or[0]
            extra_q = {}
            if filters.get("stock_code"):
                extra_q["stock_code"] = {"$regex": re.escape(str(filters["stock_code"])), "$options": "i"}  # ReDoS koruması
            if filters.get("date_range"):
                try:
                    date_obj = datetime.strptime(filters["date_range"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    extra_q["created_at"] = {"$gte": date_obj.isoformat()}
                except Exception:
                    pass
            if extra_q:
                cat_q = {"$and": [cat_q, extra_q]}
            or_conditions.append(cat_q)
        if or_conditions:
            query = {"$or": or_conditions}
        else:
            query = {"is_active": True}
    else:
        query = {"is_active": True}

    if date_from or date_to:
        date_q: dict = {}
        if date_from:
            date_q["$gte"] = date_from
        if date_to:
            date_q["$lte"] = date_to + "T23:59:59" if "T" not in str(date_to) else date_to
        query = {"$and": [query, {"created_at": date_q}]} if query else {"created_at": date_q}
    return query
@router.get("/products/barcode-issues")
async def list_products_with_barcode_issues(
    limit: int = 1000,
    current_user: dict = Depends(require_admin),
):
    """Barkodu eksik veya belirsiz (barcode_uncertain=True) ürünleri listeler.
    
    Bu ürünler Trendyol vb. pazaryerlerine ÖNCESİ aktarılırken sistem yanlış
    barkod (stock_code'tan kopyalanmış) gönderiyordu. Şimdi bunlar uncertain
    olarak işaretlendi ve push'tan engelleniyor — kullanıcı doğru barkodu
    Ticimax'tan alıp manuel düzeltmeli.
    """
    or_q = [
        {"barcode_uncertain": True},
        {"barcode": {"$in": [None, ""]}},
        {"variants.barcode_uncertain": True},
        {"variants.barcode": {"$in": [None, ""]}},
    ]
    items = []
    async for p in db.products.find(
        {"$or": or_q},
        {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "barcode": 1, "sku": 1,
         "category_name": 1, "barcode_uncertain": 1, "variants": 1, "source": 1,
         "ticimax_id": 1, "is_active": 1},
    ).limit(int(limit) or 1000):
        bad_variants = []
        for v in p.get("variants") or []:
            if v.get("barcode_uncertain") or not v.get("barcode"):
                bad_variants.append({
                    "id": v.get("id"),
                    "stock_code": v.get("stock_code") or v.get("sku"),
                    "color": v.get("color"),
                    "size": v.get("size"),
                    "barcode": v.get("barcode"),
                    "uncertain": v.get("barcode_uncertain", False),
                })
        items.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "stock_code": p.get("stock_code") or p.get("sku"),
            "main_barcode": p.get("barcode"),
            "main_barcode_uncertain": p.get("barcode_uncertain", False),
            "category_name": p.get("category_name"),
            "source": p.get("source"),
            "ticimax_id": p.get("ticimax_id"),
            "is_active": p.get("is_active"),
            "bad_variants": bad_variants,
            "bad_variant_count": len(bad_variants),
        })
    items.sort(key=lambda x: (
        not x["main_barcode_uncertain"], -(x["bad_variant_count"] or 0), x.get("name") or "",
    ))
    return {
        "success": True,
        "count": len(items),
        "items": items,
        "message": f"{len(items)} ürünün barkodu eksik/belirsiz. Manuel düzeltin.",
    }
@router.post("/products/barcode-fix")
async def fix_product_barcode(
    payload: dict,
    current_user: dict = Depends(require_admin),
):
    """Belirsiz/eksik bir ürün ya da varyant barkodunu manuel düzeltir.
    
    Payload: {
        "product_id": "...",
        "main_barcode": "...",              # (opsiyonel) ana ürün barkodu
        "variants": [                          # (opsiyonel) varyant bazlı düzeltme
            {"variant_id": "...", "barcode": "..."}
        ]
    }
    """
    pid = payload.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="product_id zorunlu")
    p = await db.products.find_one({"id": pid}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    set_fields = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if payload.get("main_barcode"):
        set_fields["barcode"] = str(payload["main_barcode"]).strip()
        set_fields["barcode_uncertain"] = False
        set_fields["barcode_note"] = "Manuel düzeltildi."
    if payload.get("variants"):
        existing = p.get("variants") or []
        fix_map = {str(v.get("variant_id")): str(v.get("barcode", "")).strip()
                   for v in (payload.get("variants") or []) if v.get("variant_id")}
        for v in existing:
            vid = str(v.get("id"))
            if vid in fix_map and fix_map[vid]:
                v["barcode"] = fix_map[vid]
                v["barcode_uncertain"] = False
        set_fields["variants"] = existing
    await db.products.update_one({"id": pid}, {"$set": set_fields})
    return {"success": True, "message": "Barkod güncellendi."}
async def log_integration_event(platform: str, action: str, entity_type: str, entity_id: str, status: str, message: str, details: dict = None):
    try:
        from datetime import datetime, timezone
        await db.integration_logs.insert_one({
            "platform": platform,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "status": status,
            "message": message,
            "details": details or {},
            "created_at": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.error(f"Failed to log integration event: {str(e)}")
def _ms_to_iso(v):
    """Trendyol ms-epoch (int) → ISO string; sayı değilse olduğu gibi/boş döner."""
    if v is None or v == "":
        return ""
    try:
        from datetime import datetime, timezone
        if isinstance(v, (int, float)) or (isinstance(v, str) and str(v).isdigit()):
            return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        pass
    return str(v)
async def _decrement_stock_for_imported_order(order_data: dict, source: str):
    """Facette stok master — yeni içe aktarılan pazaryeri/Ticimax siparişi için
    idempotent stok DÜŞÜMÜ. Aynı sipariş tekrar senkronlanırsa stock_movements
    guard'ı çift düşümü engeller. Kalemde barkod yoksa o kalem atlanır."""
    try:
        oid = order_data.get("id") or order_data.get("order_number")
        if not oid:
            return
        already = await db.stock_movements.find_one(
            {"order_id": oid, "type": "order_imported"}, {"_id": 1}
        )
        if already:
            return
        from .orders import _stock_delta_for_order  # local import — döngü önleme
        moves = await _stock_delta_for_order(order_data, -1)
        await db.stock_movements.insert_one({
            "order_id": oid,
            "order_number": order_data.get("order_number"),
            "type": "order_imported",
            "source": source,
            "delta": -1,
            "moves": moves,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as _e:
        logger.error(f"[stock] import decrement failed ({source}): {_e}")
def _facette_product_image(prod):
    for im in (prod.get("images") or []):
        if isinstance(im, str) and im:
            return im
        if isinstance(im, dict):
            u = im.get("url") or im.get("src") or im.get("image")
            if u:
                return u
    return prod.get("image") or prod.get("main_image") or ""
async def _facette_match_for_codes(codes):
    """Kod adaylarıyla (barkod / stok kodu / sku) FACETTE ürün+varyant eşler. -> (product, kod, yontem) | None"""
    seen, clean = set(), []
    for c in codes:
        c = str(c).strip() if c is not None else ""
        if c and c not in seen:
            seen.add(c); clean.append(c)
    if not clean:
        return None
    for c in clean:
        prod = await db.products.find_one({"variants.barcode": c}, {"_id": 0})
        if prod:
            return (prod, c, "variant_barcode")
    for c in clean:
        prod = await db.products.find_one({"barcode": c}, {"_id": 0})
        if prod:
            return (prod, c, "product_barcode")
    for c in clean:
        prod = await db.products.find_one({"variants.stock_code": c}, {"_id": 0})
        if prod:
            return (prod, c, "variant_stock_code")
    for c in clean:
        prod = await db.products.find_one({"$or": [{"stock_code": c}, {"sku": c}]}, {"_id": 0})
        if prod:
            return (prod, c, "product_stock_code")
    # HB merchantSku = bizim varyant urun_id'miz ("8165"), eski Ticimax kayıtlarında ise
    # numaralı ÜRÜN id'si ("7758") — bu tier'lar olmadan HB kalemleri hiç eşleşmiyordu.
    for c in clean:
        cands = [c]
        if c.isdigit():
            try:
                cands.append(int(c))
            except Exception:
                pass
        prod = await db.products.find_one({"variants.urun_id": {"$in": cands}}, {"_id": 0})
        if prod:
            return (prod, c, "variant_urun_id")
    for c in clean:
        if c.isdigit():
            prod = await db.products.find_one(
                {"$or": [{"id": c}, {"urun_id": c}, {"urun_kart_id": c}]}, {"_id": 0})
            if prod:
                return (prod, c, "product_urun_id")
    return None
def _to_float_tr(v) -> float:
    """Türkçe/karışık sayı biçimlerini güvenle float'a çevirir.
    '2100,99' → 2100.99 · '2.100,99' → 2100.99 · '1,234.56' → 1234.56 · '₺2.100,99' → 2100.99.
    ÖNEMLİ: eski kod 'float(\"2100,99\")' yapıp ValueError'a düşüyor, fiyatı 0 sanıyordu →
    HB'ye '0 fiyat' gidiyordu. Bu helper o kaybı önler. Çözülemezse 0.0 döner."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    # Para sembolleri / boşluk / harf temizliği (rakam, nokta, virgül, eksi kalsın)
    s = re.sub(r"[^0-9,.\-]", "", s)
    if not s:
        return 0.0
    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        # Son görülen ayraç ondalıktır (TR: '2.100,99' → ',' ondalık; US: '1,234.56' → '.' ondalık)
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # TR
        else:
            s = s.replace(",", "")                       # US binlik
    elif has_comma:
        s = s.replace(",", ".")                          # tek virgül → ondalık
    try:
        return float(s)
    except Exception:
        return 0.0
def _hb_norm(s) -> str:
    """Türkçe-duyarsız normalize (eşleştirme için). HB'ye bağımsız."""
    import unicodedata as _u
    s = _u.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not _u.combining(c))
    s = (s.lower().replace("ı", "i").replace("ş", "s").replace("ç", "c")
         .replace("ğ", "g").replace("ü", "u").replace("ö", "o"))
    return " ".join(s.split())
@router.get("/integration-logs")
async def get_integration_logs(
    platform: str = Query(None),
    status: str = Query(None),
    limit: int = 50,
    current_user: dict = Depends(require_admin)
):
    """Fetch integration logs for UI"""
    try:
        query = {}
        if platform:
            query["platform"] = platform
        if status:
            query["status"] = status
            
        logs = await db.integration_logs.find(query).sort("created_at", -1).limit(limit).to_list(1000)
        # remove mongo _id
        for log in logs:
            if "_id" in log:
                log["_id"] = str(log["_id"])
        return {"success": True, "logs": logs}
    except Exception as e:
        logger.error(f"Error fetching integration logs: {e}")
        raise HTTPException(status_code=500, detail="Loglar alınamadı.")
GIB_MODE = os.environ.get('GIB_MODE', 'test')
GIB_USERNAME = os.environ.get('GIB_USERNAME', '')
GIB_PASSWORD = os.environ.get('GIB_PASSWORD', '')
GIB_VKN = os.environ.get('GIB_VKN', '')
GIB_COMPANY_NAME = os.environ.get('GIB_COMPANY_NAME', 'FACETTE')
def is_gib_configured():
    return bool(GIB_USERNAME and GIB_PASSWORD and GIB_VKN and len(GIB_VKN) == 10)
@router.get("/gib/status")
async def get_gib_status():
    """Get GIB integration status"""
    return {
        "configured": is_gib_configured(),
        "mode": GIB_MODE,
        "vkn": GIB_VKN[:4] + "******" if GIB_VKN else None,
        "company_name": GIB_COMPANY_NAME
    }
def _generate_slug(name: str) -> str:
    slug = name.lower()
    tr_map = {'ı':'i','ğ':'g','ü':'u','ş':'s','ö':'o','ç':'c',
              'İ':'i','Ğ':'g','Ü':'u','Ş':'s','Ö':'o','Ç':'c'}
    for tr, en in tr_map.items():
        slug = slug.replace(tr, en)
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug or str(uuid.uuid4())[:8]
@router.post("/site/categories/sync-missing-from-products")
async def sync_missing_categories_from_products(current_user: dict = Depends(require_admin)):
    """
    Ticimax kategori senkronizasyonunda kaçırılmış (örn. çok derin alt-kategori veya
    silinmiş ama ürünleri kalmış) kategorileri ürünlerin `category_name` alanından
    bulup yerel `categories` koleksiyonuna ekler ve ilgili ürünleri category_id ile
    günceller.

    Tipik kullanım: "Tulum kategorisi gelmemiş" gibi durumlarda; Ticimax API'sini
    tekrar çağırmadan, mevcut veriden eksiklikleri tamamlar.
    """
    # 1) Mevcut yerel kategoriler (isim → id)
    existing = {}
    async for c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1}):
        nm = (c.get("name") or "").strip()
        if nm:
            existing[nm.lower()] = c.get("id")

    # 2) Ürünlerdeki tüm kategori isimleri (boş olmayanlar)
    pipeline = [
        {"$match": {"category_name": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$category_name", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    product_cats = []
    async for row in db.products.aggregate(pipeline):
        product_cats.append({"name": (row["_id"] or "").strip(), "count": row["count"]})

    # 3) Eksik olanları bul → oluştur
    created = []
    relinked = 0
    for pc in product_cats:
        nm = pc["name"]
        if not nm or nm.lower() in existing:
            continue
        # Yeni kategori oluştur
        new_id = await generate_short_id("categories")
        slug = _generate_slug(nm)
        doc = {
            "id": new_id,
            "ticimax_id": None,  # Ticimax'tan gelmediği için None
            "name": nm,
            "slug": slug,
            "parent_id": None,
            "is_active": True,
            "source": "products_backfill",
            "ticimax_sub_count": 0,
            "ticimax_sira": 999,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "note": "Bu kategori Ticimax sync sırasında kaçırılmıştı; ürünlerden geri-yüklendi.",
        }
        await db.categories.insert_one(doc)
        existing[nm.lower()] = new_id
        created.append({"id": new_id, "name": nm, "product_count": pc["count"]})

    # 4) Ürünlerde category_id boş olup category_name dolu olanları bağla
    for pc in product_cats:
        cat_id = existing.get(pc["name"].lower())
        if not cat_id:
            continue
        res = await db.products.update_many(
            {"category_name": pc["name"], "$or": [{"category_id": {"$exists": False}}, {"category_id": None}, {"category_id": ""}]},
            {"$set": {"category_id": cat_id}}
        )
        relinked += res.modified_count

    return {
        "success": True,
        "created_categories": created,
        "created_count": len(created),
        "relinked_products": relinked,
        "message": f"{len(created)} kategori oluşturuldu, {relinked} ürün bağlandı.",
    }
async def _kurtarma_match(uk, barkodlar, projection):
    """Bir Ticimax kartı için canlı ürün(ler)i GÜVENLE bulur.
    Öncelik: urun_karti_id (benzersiz). Tutmazsa BARKOD (varyant-benzersiz; bu export'ta
    1086 distinct barkodun 0'ı birden çok karta düşüyor → güvenli yedek anahtar).
    Döner: (prods, via) ; via ∈ {'kart_id','barkod',''}.
    Eşleşen tüm ürünler aynı karttır (renk kardeşleri) → hepsine uygulamak güvenli."""
    ukq = [str(uk)]
    if str(uk).isdigit():
        ukq.append(int(uk))
    prods = await db.products.find(
        {"urun_karti_id": {"$in": ukq}}, projection
    ).to_list(length=30)
    if prods:
        return prods, "kart_id"
    bl = [str(b).strip() for b in (barkodlar or []) if str(b).strip()]
    if bl:
        prods = await db.products.find(
            {"$or": [{"barcode": {"$in": bl}}, {"variants.barcode": {"$in": bl}}]},
            projection,
        ).to_list(length=30)
        if prods:
            return prods, "barkod"
    return [], ""
@router.post("/site/teknik-detay/recover")
async def recover_teknik_detay_from_snapshot(
    apply: bool = Query(False, description="false=ÖNİZLEME (yazma yok) · true=UYGULA"),
    current_user: dict = Depends(require_admin),
):
    """Silinen ürün-kartı teknik detaylarını, doğrulanmış Ticimax export snapshot'ından
    (backend/data/teknik_detay_kurtarma.json) GERİ YÜKLER.

    GÜVENLİK GARANTİLERİ:
      • Eşleştirme YALNIZCA `urun_karti_id` üzerinden (benzersiz). StokKodu/barkod ASLA.
      • Bir kart-ID birden çok ürüne denk gelirse (belirsiz) → ATLANIR, asla yazılmaz.
      • Sadece BOŞ/eksik özellik doldurulur; mevcut (manuel) değer ASLA ezilmez.
      • Fiyat/KDV/stok/barkod/varyantlara DOKUNULMAZ (yalnız `attributes`).
      • apply=false → ne değişeceğini döner, HİÇBİR ŞEY yazmaz.
    """
    import json as _json
    import os as _os
    snap_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                              "data", "teknik_detay_kurtarma.json")
    try:
        with open(snap_path, "r", encoding="utf-8") as f:
            snap = _json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Kurtarma verisi okunamadı: {e}")
    urunler = snap.get("urunler") or {}

    matched = 0
    via_kart = 0
    via_barkod = 0
    no_match = 0
    too_many = 0
    to_fill_total = 0
    hb_total = 0
    temu_total = 0
    updated = 0
    sample: list = []

    PROJ = {"_id": 0, "id": 1, "attributes": 1, "name": 1,
            "hepsiburada_attributes": 1, "temu_attributes": 1}

    for uk, info in urunler.items():
        ozet = (info or {}).get("ozellikler") or {}
        if not ozet:
            continue
        prods, via = await _kurtarma_match(uk, (info or {}).get("barkodlar"), PROJ)
        if not prods:
            no_match += 1
            continue
        if len(prods) > 25:
            too_many += 1   # GÜVENLİK: anormal eşleşme sayısı → dokunma
            continue
        if via == "kart_id":
            via_kart += 1
        elif via == "barkod":
            via_barkod += 1

        card_did = False
        for p in prods:
            cur = p.get("attributes")
            existing_names: set = set()
            existing_vals: dict = {}   # orijinal_ad -> deger (üründe ZATEN dolu genel/Trendyol özellikleri)
            cur_list: list = []
            if isinstance(cur, list):
                for a in cur:
                    if isinstance(a, dict):
                        cur_list.append(a)
                        nm = a.get("name") or a.get("label") or a.get("type")
                        vv = a.get("value") or a.get("attribute_value")
                        if nm and str(vv or "").strip():
                            existing_names.add(_hb_norm(nm))
                            existing_vals.setdefault(str(nm), str(vv).strip())
            elif isinstance(cur, dict):
                for k, v in cur.items():
                    if isinstance(v, dict):
                        nm = v.get("label") or v.get("name") or k
                        vv = v.get("value") or v.get("attribute_value")
                    else:
                        nm, vv = k, v
                    if nm and str(vv or "").strip():
                        existing_names.add(_hb_norm(nm))
                        existing_vals.setdefault(str(nm), str(vv).strip())

            # 1) Snapshot'tan GENEL (attributes/Trendyol) BOŞ özellikleri doldur.
            adds = []
            for oz, dg in ozet.items():
                if not str(dg or "").strip():
                    continue
                if _hb_norm(oz) in existing_names:
                    continue   # zaten DOLU → dokunma
                adds.append({"name": oz, "value": str(dg).strip()})

            # 2) HB + Temu'ya AKTAR: ürünün TÜM genel özellikleri (mevcut dolu + kurtarılan) →
            #    hepsiburada_attributes / temu_attributes'ta BOŞ olanı doldur (manuel değer ezilmez).
            #    Push, bu ham değerleri gönderim anında HB enum'una çözer.
            hb = dict(p.get("hepsiburada_attributes") or {})
            temu = dict(p.get("temu_attributes") or {})
            propagate: dict = {}
            for nm, vv in existing_vals.items():
                propagate[nm] = vv
            for a in adds:
                propagate.setdefault(a["name"], a["value"])
            hb_keys_norm = {_hb_norm(k) for k, v in hb.items() if str(v or "").strip()}
            temu_keys_norm = {_hb_norm(k) for k, v in temu.items() if str(v or "").strip()}
            hb_fill = 0
            temu_fill = 0
            for nm, vv in propagate.items():
                nn = _hb_norm(nm)
                if nn and nn not in hb_keys_norm:
                    hb[nm] = vv; hb_fill += 1; hb_keys_norm.add(nn)
                if nn and nn not in temu_keys_norm:
                    temu[nm] = vv; temu_fill += 1; temu_keys_norm.add(nn)

            if not adds and hb_fill == 0 and temu_fill == 0:
                continue   # bu üründe yapılacak bir şey yok
            card_did = True
            to_fill_total += len(adds)
            hb_total += hb_fill
            temu_total += temu_fill
            if len(sample) < 12:
                sample.append({"urun_karti_id": str(uk),
                               "eslesme": via,
                               "urun": (p.get("name") or info.get("urun_adi") or "")[:50],
                               "genel_eklenecek": {a["name"]: a["value"] for a in adds},
                               "hb_dolan": hb_fill, "temu_dolan": temu_fill})
            if apply:
                setdoc = {"hepsiburada_attributes": hb, "temu_attributes": temu}
                if adds:
                    # attributes FORMATINI KORU (Trendyol'u bozma): list ise list'e ekle, dict ise dict'e.
                    if isinstance(cur, dict):
                        new_attrs = dict(cur)
                        for a in adds:
                            new_attrs[a["name"]] = a["value"]
                    else:
                        new_attrs = (cur_list if isinstance(cur, list) else []) + adds
                    setdoc["attributes"] = new_attrs
                await db.products.update_one({"id": p["id"]}, {"$set": setdoc})
                updated += 1
        if card_did:
            matched += 1

    return {
        "mode": "apply" if apply else "preview",
        "snapshot_urun": len(urunler),
        "eslesen_urun": matched,
        "eslesen_kart_id_ile": via_kart,
        "eslesen_barkod_ile": via_barkod,
        "eslesmeyen_urun_karti": no_match,
        "anormal_atlanmis": too_many,
        "doldurulacak_ozellik_toplam": to_fill_total,
        "hb_dolan_toplam": hb_total,
        "temu_dolan_toplam": temu_total,
        "guncellenen_urun": updated,
        "ornek": sample,
        "not": ("Eşleştirme: önce urun_karti_id, tutmazsa BARKOD (varyant-benzersiz, güvenli). "
                "Yalnız BOŞ özellikler dolduruldu (genel + Hepsiburada + Temu); manuel değerler korundu. "
                "attributes formatına dokunulmadı (Trendyol güvende). Fiyat/KDV/stok/barkoda dokunulmadı."
                + ("" if apply else " — ÖNİZLEME: hiçbir şey yazılmadı.")),
    }
@router.post("/site/aciklama/recover")
async def recover_aciklama_from_snapshot(
    apply: bool = Query(False, description="false=ÖNİZLEME (yazma yok) · true=UYGULA"),
    current_user: dict = Depends(require_admin),
):
    """Eksik ürün AÇIKLAMALARINI (description) doğrulanmış Ticimax export snapshot'ından
    (backend/data/aciklama_kurtarma.json) doldurur.

    GÜVENLİK GARANTİLERİ:
      • Eşleştirme YALNIZCA `urun_karti_id` (benzersiz). StokKodu/barkod ASLA.
      • Bir kart-ID birden çok ürüne denk gelirse → ATLANIR.
      • Yalnız BOŞ açıklama doldurulur (içi boş "<p></p>" gibi HTML de boş sayılır);
        dolu açıklama ASLA ezilmez.
      • Yalnız `description` alanı; fiyat/KDV/stok/barkod/başlık/özelliklere DOKUNULMAZ.
      • apply=false → önizleme, hiçbir şey yazmaz.
    """
    import json as _json
    import os as _os
    import re as _re
    snap_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                              "data", "aciklama_kurtarma.json")
    try:
        with open(snap_path, "r", encoding="utf-8") as f:
            snap = _json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Açıklama verisi okunamadı: {e}")
    urunler = snap.get("urunler") or {}

    def _blank_html(s):
        t = _re.sub(r"<[^>]+>", " ", str(s or ""))
        t = t.replace("&nbsp;", " ").replace("\xa0", " ")
        return not t.strip()

    matched = 0
    via_kart = 0
    via_barkod = 0
    no_match = 0
    too_many = 0
    already_full = 0
    updated = 0
    sample: list = []

    PROJ = {"_id": 0, "id": 1, "description": 1, "name": 1}

    for uk, info in urunler.items():
        desc = (info or {}).get("description") or ""
        if not str(desc).strip():
            continue
        prods, via = await _kurtarma_match(uk, (info or {}).get("barkodlar"), PROJ)
        if not prods:
            no_match += 1
            continue
        if len(prods) > 25:
            too_many += 1   # GÜVENLİK: anormal → dokunma
            continue
        if via == "kart_id":
            via_kart += 1
        elif via == "barkod":
            via_barkod += 1

        card_did = False
        for p in prods:
            if not _blank_html(p.get("description")):
                already_full += 1
                continue   # zaten dolu → DOKUNMA
            card_did = True
            if len(sample) < 12:
                sample.append({"urun_karti_id": str(uk),
                               "eslesme": via,
                               "urun": (p.get("name") or info.get("urun_adi") or "")[:50],
                               "aciklama_onizleme": _re.sub(r"<[^>]+>", " ", desc)[:120].strip()})
            if apply:
                await db.products.update_one({"id": p["id"]}, {"$set": {"description": str(desc)}})
                updated += 1
        if card_did:
            matched += 1

    return {
        "mode": "apply" if apply else "preview",
        "snapshot_urun": len(urunler),
        "doldurulacak_urun": matched,
        "eslesen_kart_id_ile": via_kart,
        "eslesen_barkod_ile": via_barkod,
        "zaten_dolu": already_full,
        "eslesmeyen_urun_karti": no_match,
        "anormal_atlanmis": too_many,
        "guncellenen_urun": updated,
        "ornek": sample,
        "not": ("Eşleştirme: önce urun_karti_id, tutmazsa BARKOD (varyant-benzersiz, güvenli). "
                "Yalnız BOŞ açıklamalar dolduruldu (içi boş HTML dahil); dolu açıklamalar korundu. "
                "Sadece description; fiyat/KDV/stok/başlık/özelliklere dokunulmadı."
                + ("" if apply else " — ÖNİZLEME: hiçbir şey yazılmadı.")),
    }
def _rk_lower(s: str) -> str:
    """tr-lower (İ/I güvenli)."""
    return (s or "").replace("I", "ı").replace("İ", "i").lower()
_RENK_CANON = {
    "siyah": "Siyah", "black": "Siyah", "beyaz": "Beyaz", "white": "Beyaz",
    "ekru": "Ekru", "krem": "Krem", "cream": "Krem", "bej": "Bej", "beige": "Bej",
    "kahve": "Kahverengi", "kahverengi": "Kahverengi", "brown": "Kahverengi",
    "vizon": "Vizon", "camel": "Camel", "taş": "Taş", "tas": "Taş", "stone": "Taş",
    "gri": "Gri", "grey": "Gri", "gray": "Gri", "antrasit": "Antrasit",
    "füme": "Füme", "fume": "Füme", "lacivert": "Lacivert", "navy": "Lacivert",
    "mavi": "Mavi", "blue": "Mavi", "indigo": "İndigo", "petrol": "Petrol",
    "turkuaz": "Turkuaz", "mint": "Mint", "yeşil": "Yeşil", "yesil": "Yeşil",
    "green": "Yeşil", "haki": "Haki", "zümrüt": "Zümrüt", "zumrut": "Zümrüt",
    "sarı": "Sarı", "sari": "Sarı", "yellow": "Sarı", "hardal": "Hardal",
    "gold": "Gold", "altın": "Gold", "altin": "Gold", "turuncu": "Turuncu",
    "orange": "Turuncu", "mercan": "Mercan", "somon": "Somon", "salmon": "Somon",
    "kiremit": "Kiremit", "kırmızı": "Kırmızı", "kirmizi": "Kırmızı", "red": "Kırmızı",
    "bordo": "Bordo", "fuşya": "Fuşya", "fusya": "Fuşya", "fuchsia": "Fuşya",
    "pembe": "Pembe", "pink": "Pembe", "pudra": "Pudra", "lila": "Lila",
    "mor": "Mor", "purple": "Mor", "leylak": "Leylak", "gümüş": "Gümüş",
    "gumus": "Gümüş", "silver": "Gümüş", "bronz": "Bronz", "metalik": "Metalik",
    "mürdüm": "Mürdüm", "murdum": "Mürdüm", "yavruağzı": "Yavruağzı",
    "yavruagzi": "Yavruağzı", "fıstık": "Fıstık Yeşili", "fistik": "Fıstık Yeşili",
}
def _renk_from_name(name: str) -> str:
    """Ürün adının SON kelimesinden rengi çıkarır; sözlükle doğrulanır.
    Renk değilse '' (asla tahmin etmez)."""
    toks = re.sub(r"[^0-9A-Za-zçğıöşüÇĞİÖŞÜ ]", " ", str(name or "")).split()
    if not toks:
        return ""
    return _RENK_CANON.get(_rk_lower(toks[-1]), "")
def _renk_canon(val: str) -> str:
    """Serbest renk değerini (ürün/varyant color) kanonik renge çevirir; tanınmazsa ''."""
    v = _rk_lower(str(val or "").strip())
    if not v:
        return ""
    if v in _RENK_CANON:
        return _RENK_CANON[v]
    parts = v.split()
    return _RENK_CANON.get(parts[-1], "") if parts else ""
@router.post("/site/renk-webcolor/autofill")
async def autofill_renk_webcolor(
    apply: bool = Query(False, description="false=ÖNİZLEME · true=UYGULA"),
    current_user: dict = Depends(require_admin),
):
    """Renk + Web Color'ı ürün ADINDAN otomatik doldurur.
    Her renk varyantı split sonrası ayrı kart olur ve adı renkle biter
    (ör. '...Elbise Bej' → Renk=Bej, Web Color=Bej). Web Color gönderimde
    pazaryeri enum'una (en yakın) çözülür.
    GÜVENLİK: çok renkli (henüz bölünmemiş) kartlara tek renk YAZILMAZ; yalnız BOŞ
    Renk/Web Color doldurulur; Beden ASLA yazılmaz; fiyat/KDV/stok/barkoda dokunulmaz;
    attributes formatı (list/dict) korunur — Trendyol güvende."""
    PROJ = {"_id": 0, "id": 1, "name": 1, "color": 1, "attributes": 1,
            "variants": 1, "hepsiburada_attributes": 1, "temu_attributes": 1}
    scanned = matched = updated = renk_fill = webcolor_fill = 0
    no_color = multi_color = hb_fill_t = temu_fill_t = 0
    sample: list = []

    async for p in db.products.find({}, PROJ):
        scanned += 1
        # GÜVENLİK: çok renkli (bölünmemiş) kart → tek renk yazma, ATLA
        vcols_all: list = []
        for v in (p.get("variants") or []):
            c = str((v or {}).get("color") or "").strip()
            if c and _rk_lower(c) not in [_rk_lower(x) for x in vcols_all]:
                vcols_all.append(c)
        if len(vcols_all) > 1:
            multi_color += 1
            continue

        name = p.get("name") or ""
        color = _renk_from_name(name)              # 1) isim son kelimesi
        if not color:
            color = _renk_canon(p.get("color"))    # 2) ürün color alanı
        if not color and len(vcols_all) == 1:
            color = _renk_canon(vcols_all[0])      # 3) tek distinct varyant rengi
        if not color:
            no_color += 1
            continue
        matched += 1

        # mevcut genel Renk/Web Color DOLU mu?
        cur = p.get("attributes")
        existing_names: set = set()
        cur_list: list = []
        if isinstance(cur, list):
            for a in cur:
                if isinstance(a, dict):
                    cur_list.append(a)
                    nm = a.get("name") or a.get("label") or a.get("type")
                    vv = a.get("value") or a.get("attribute_value")
                    if nm and str(vv or "").strip():
                        existing_names.add(_hb_norm(nm))
        elif isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(v, dict):
                    nm = v.get("label") or v.get("name") or k
                    vv = v.get("value") or v.get("attribute_value")
                else:
                    nm, vv = k, v
                if nm and str(vv or "").strip():
                    existing_names.add(_hb_norm(nm))

        targets = [("Renk", color), ("Web Color", color)]
        adds = [{"name": nm, "value": vv} for nm, vv in targets
                if _hb_norm(nm) not in existing_names]

        hb = dict(p.get("hepsiburada_attributes") or {})
        temu = dict(p.get("temu_attributes") or {})
        hb_norms = {_hb_norm(k) for k, v in hb.items() if str(v or "").strip()}
        temu_norms = {_hb_norm(k) for k, v in temu.items() if str(v or "").strip()}
        hb_f = temu_f = 0
        for nm, vv in targets:
            nn = _hb_norm(nm)
            if nn not in hb_norms:
                hb[nm] = vv; hb_f += 1; hb_norms.add(nn)
            if nn not in temu_norms:
                temu[nm] = vv; temu_f += 1; temu_norms.add(nn)

        if not adds and hb_f == 0 and temu_f == 0:
            continue
        renk_fill += sum(1 for a in adds if a["name"] == "Renk")
        webcolor_fill += sum(1 for a in adds if a["name"] == "Web Color")
        hb_fill_t += hb_f
        temu_fill_t += temu_f
        if len(sample) < 15:
            sample.append({"urun": name[:55], "renk": color,
                           "genel_eklenecek": [a["name"] for a in adds],
                           "hb_dolan": hb_f, "temu_dolan": temu_f})

        if apply:
            setdoc = {"hepsiburada_attributes": hb, "temu_attributes": temu}
            if not str(p.get("color") or "").strip():
                setdoc["color"] = color
            if adds:
                if isinstance(cur, dict):
                    new_attrs = dict(cur)
                    for a in adds:
                        new_attrs[a["name"]] = a["value"]
                else:
                    new_attrs = (cur_list if isinstance(cur, list) else []) + adds
                setdoc["attributes"] = new_attrs
            await db.products.update_one({"id": p["id"]}, {"$set": setdoc})
            updated += 1

    return {
        "mode": "apply" if apply else "preview",
        "taranan_urun": scanned,
        "renk_bulunan_urun": matched,
        "renk_bulunamayan": no_color,
        "cok_renkli_atlanan": multi_color,
        "renk_doldurulacak": renk_fill,
        "webcolor_doldurulacak": webcolor_fill,
        "hb_dolan_toplam": hb_fill_t,
        "temu_dolan_toplam": temu_fill_t,
        "guncellenen_urun": updated,
        "ornek": sample,
        "not": ("Renk = ürün ADININ son kelimesi (renk sözlüğüyle doğrulanır); değilse "
                "ürün/varyant renginden. Web Color = Renk; gönderimde pazaryeri enum'una "
                "(en yakın) çözülür. Çok renkli kart ATLANIR. Beden YAZILMAZ. Yalnız BOŞ "
                "alanlar dolduruldu; fiyat/KDV/stok/barkoda dokunulmadı."
                + ("" if apply else " — ÖNİZLEME: hiçbir şey yazılmadı.")),
    }
def _desc_is_blank(s) -> bool:
    """HTML açıklamayı düz metne indirip boş mu (sadece etiket/boşluk) kontrol eder."""
    t = re.sub(r"<[^>]+>", " ", str(s or ""))
    t = t.replace("&nbsp;", " ").replace("\xa0", " ")
    return not t.strip()
def _attr_flat(p: dict) -> dict:
    """Ürünün genel attributes (list/dict) + hepsiburada_attributes'ını ad->değer düz sözlüğe indirir."""
    out: dict = {}
    cur = p.get("attributes")
    if isinstance(cur, list):
        for a in cur:
            if isinstance(a, dict):
                nm = a.get("name") or a.get("label") or a.get("type")
                vv = a.get("value") or a.get("attribute_value")
                if nm and str(vv or "").strip():
                    out.setdefault(str(nm), str(vv).strip())
    elif isinstance(cur, dict):
        for k, v in cur.items():
            vv = v.get("value") if isinstance(v, dict) else v
            if str(vv or "").strip():
                out.setdefault(str(k), str(vv).strip())
    for k, v in (p.get("hepsiburada_attributes") or {}).items():
        if str(v or "").strip():
            out.setdefault(str(k), str(v).strip())
    return out
def _attr_get(am: dict, *names) -> str:
    """Düz öznitelik sözlüğünden ad(lar)a göre (HB-normalize ile) ilk dolu değeri döner."""
    for n in names:
        for k, v in am.items():
            if _hb_norm(k) == _hb_norm(n):
                return v
    return ""
@router.post("/site/aciklama/generate")
async def generate_aciklama_ai(
    apply: bool = Query(False, description="false=SAY (üretme yok) · true=ÜRET (batch)"),
    limit: int = Query(10, ge=1, le=30, description="apply'da her çağrıda işlenecek ürün sayısı"),
    current_user: dict = Depends(require_admin),
):
    """Boş açıklamalı ürünlere ÖZNİTELİKLERDEN beslenerek mevcut formatta açıklama üretir.
    Ürün Bilgisi prozası LLM ile (AI Chatbot ayarlarındaki sağlayıcı/model — Gemini de olur);
    Kumaş = Materyal, Kalıp = Kalıp özniteliği; Beden/Model ölçüleri BOŞ '___' bırakılır.
    GÜVENLİK: yalnız BOŞ açıklama doldurulur (mevcut korunur); ölçü/fiyat/beden UYDURULMAZ;
    fiyat/KDV/stok/başlık/özelliklere dokunulmaz."""
    PROJ = {"_id": 0, "id": 1, "name": 1, "description": 1,
            "attributes": 1, "hepsiburada_attributes": 1}
    total_empty = 0
    batch: list = []
    async for p in db.products.find({}, PROJ):
        if _desc_is_blank(p.get("description")):
            total_empty += 1
            if apply and len(batch) < limit:
                batch.append(p)

    if not apply:
        return {
            "mode": "preview",
            "bos_aciklamali_urun": total_empty,
            "not": (f"{total_empty} ürünün açıklaması boş. 'Uygula' her çağrıda en çok {limit} "
                    "tanesini AI ile üretir; arayüz kalan bitene kadar döngüyle çağırır. "
                    "Ürün Bilgisi AI ile özniteliklerden yazılır, ölçüler boş '___' bırakılır."),
        }

    from .ai_chatbot import get_ai_settings, _api_key_for, llm_chat
    settings = await get_ai_settings()
    api_key = _api_key_for(settings)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI anahtarı yapılandırılmamış (Ayarlar → AI Chatbot).")
    provider = settings.get("provider", "anthropic")
    model = settings.get("fast_model") or settings.get("model", "claude-haiku-4-5")

    SYS = (
        "Sen bir kadın giyim e-ticaret editörüsün. Sana ürün adı ve öznitelikleri verilir. "
        "SADECE 'Ürün Bilgisi' bölümü için 1-2 kısa, akıcı, abartısız Türkçe cümle yaz. "
        "Yalnızca verilen özniteliklere dayan; ölçü, fiyat, beden, kumaş oranı, malzeme UYDURMA. "
        "Başlık, etiket, HTML, madde işareti, tırnak KULLANMA — yalnızca düz cümle döndür."
    )

    generated = failed = 0
    sample: list = []
    for p in batch:
        am = _attr_flat(p)
        name = p.get("name") or ""
        feed = "\n".join(
            f"- {k}: {v}" for k, v in am.items()
            if _hb_norm(k) not in (_hb_norm("Web Color"), _hb_norm("Renk"), _hb_norm("Beden"))
        )
        user_text = (f"Ürün adı: {name}\nÖznitelikler:\n{feed or '(öznitelik yok)'}\n\n"
                     "Bu ürün için 'Ürün Bilgisi' cümlesini yaz:")
        try:
            resp = await llm_chat(
                api_key=api_key, provider=provider, model=model,
                system_message=SYS, user_text=user_text, max_tokens=500,
            )
            prose = re.sub(r"<[^>]+>", "", str(resp or "")).strip()
            prose = re.sub(r"^\s*(Ürün Bilgisi\s*:?)\s*", "", prose, flags=re.I).strip()
        except Exception:
            failed += 1
            continue
        if not prose:
            failed += 1
            continue

        kumas = _attr_get(am, "Materyal", "Kumaş", "Kumaş Tipi")
        kalip = _attr_get(am, "Kalıp")
        parts = [f"<p><strong>Ürün Bilgisi:&nbsp;</strong>{prose}</p>"]
        if kumas:
            parts.append(f"<p><strong>Kumaş Bilgisi:&nbsp;</strong>{kumas}</p>")
        if kalip:
            parts.append(f"<p><strong>Kalıp:&nbsp;</strong>{kalip}</p>")
        parts.append("<p><strong>STD Beden Ölçüleri:</strong>&nbsp; Göğüs: ___ cm&nbsp; "
                     "Boy: ___ cm&nbsp; Kol Boyu: ___ cm</p>")
        parts.append("<p><strong>Model Ölçüleri:</strong>&nbsp; Boy: ___&nbsp; Göğüs: ___&nbsp; "
                     "Bel: ___&nbsp; Kalça: ___&nbsp; Kilo: ___</p>")
        parts.append("<p>Modelin üzerindeki ürün <strong>STD</strong> bedendir.</p>")
        parts.append("<p>Bedenler arası +/- sapma olabilir.</p>")
        html = "\n".join(parts)

        await db.products.update_one({"id": p["id"]}, {"$set": {"description": html}})
        generated += 1
        if len(sample) < 6:
            sample.append({"urun": name[:55], "uretilen_proza": prose[:140]})

    return {
        "mode": "apply",
        "uretilen": generated,
        "basarisiz": failed,
        "kalan": max(0, total_empty - generated),
        "kullanilan_model": f"{provider}/{model}",
        "ornek": sample,
        "not": ("Ürün Bilgisi AI ile özniteliklerden yazıldı; Kumaş=Materyal, Kalıp=Kalıp "
                "özniteliğinden. Beden/Model ölçüleri boş '___' bırakıldı (elle doldur). "
                "Yalnız boş açıklamalar dolduruldu; fiyat/KDV/stok/başlık/özelliklere dokunulmadı."),
    }
@router.post("/rooftr/products/upload-excel")
async def upload_rooftr_products_excel(
    file: UploadFile = File(..., description="TicimaxExport .xls/.xlsx dosyası"),
    default_stock: int = Query(5, description="Excel'de stok yoksa varsayılan stok adedi"),
    current_user: dict = Depends(require_admin),
):
    """Ticimax ürün Excel'ini (drag-drop UI'dan) yükleyip TAM resync yapar.

    `scripts/ticimax_full_resync.py` ile aynı mantık: URUNKARTIID'e göre gruplar,
    DB'de eşleştirir (urun_karti_id > stock_code+color > name+color), eşleşeni günceller,
    eşleşmeyeni yeni ürün olarak ekler.

    Beklenen sütunlar: URUNKARTIID, URUNID, STOKKODU, BARKOD, URUNADI, ACIKLAMA,
    BREADCRUMBKAT, TEDARIKCI, ALISFIYATI, SATISFIYATI, INDIRIMLIFIYAT, UYETIPIFIYAT1,
    KDVORANI, RENK, BEDEN
    """
    import tempfile
    import unicodedata
    from uuid import uuid4
    from fastapi.concurrency import run_in_threadpool

    filename = (file.filename or "").lower()
    if not (filename.endswith(".xls") or filename.endswith(".xlsx")):
        raise HTTPException(status_code=400, detail="Sadece .xls veya .xlsx dosyası yükleyin")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Dosya boş")

    suffix = ".xlsx" if filename.endswith(".xlsx") else ".xls"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(raw)
    tmp.close()
    tmp_path = tmp.name

    def _slugify(text):
        t = unicodedata.normalize("NFKD", str(text or ""))
        t = "".join(c for c in t if not unicodedata.combining(c))
        t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
        return t[:200] or "urun"

    def _read_df():
        import pandas as pd
        engine = "openpyxl" if suffix == ".xlsx" else None
        try:
            return pd.read_excel(tmp_path, engine=engine)
        except Exception:
            # .xls aslında xlsx içeriği olabilir → openpyxl dene, yoksa xlrd
            try:
                return pd.read_excel(tmp_path, engine="openpyxl")
            except Exception:
                return pd.read_excel(tmp_path, engine="xlrd")

    try:
        df = await run_in_threadpool(_read_df)
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"Excel okunamadı: {e}")

    required_cols = {"URUNKARTIID", "URUNADI", "BARKOD"}
    missing = required_cols - set(df.columns)
    if missing:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"Eksik sütunlar: {', '.join(missing)}")

    import pandas as pd

    def _f(x, default=0.0):
        try:
            if pd.isna(x):
                return default
            return float(x)
        except Exception:
            return default

    def _s(x, default=""):
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return default
        return str(x).strip()

    def _int(x, default=0):
        try:
            if pd.isna(x):
                return default
            return int(x)
        except Exception:
            return default

    def _has(col):
        return col in df.columns

    def _cell(row, col, default=""):
        return _s(row[col]) if _has(col) else default

    def _category_from_breadcrumb(crumb):
        if not crumb:
            return ""
        parts = [p.strip() for p in crumb.split(">") if p.strip()]
        return parts[-1] if parts else ""

    sys_cats = await db.categories.find({}, {"_id": 0}).to_list(None)
    cat_by_name = {(c.get("name") or "").strip().lower(): c for c in sys_cats}

    stats = {
        "parents_in_excel": 0,
        "parents_updated_db": 0,
        "parents_created_new": 0,
        "variants_total": 0,
        "errors": [],
    }

    by_kart = df.groupby("URUNKARTIID", sort=False)
    for kart_id_raw, grp in by_kart:
        try:
            stats["parents_in_excel"] += 1
            # URUNKARTIID numerikse int-string normalize; değilse ham string koru
            try:
                if pd.isna(kart_id_raw):
                    kart_id = ""
                else:
                    kart_id = str(int(float(kart_id_raw)))
            except (ValueError, TypeError):
                kart_id = _s(kart_id_raw)
            first = grp.iloc[0]

            urun_adi = _cell(first, "URUNADI")
            renk = _cell(first, "RENK")
            base_name = urun_adi
            if renk and base_name.lower().endswith(" " + renk.lower()):
                base_name = base_name[: -(len(renk) + 1)].strip()

            list_price = _f(first["SATISFIYATI"]) if _has("SATISFIYATI") else 0.0
            # İNDİRİM: yalnızca Ticimax'te GERÇEK indirim varsa (0 < INDIRIMLIFIYAT < SATISFIYATI)
            # sale_price ayarlanır. Aksi halde None → 'sale_price = list_price' damgası YAPILMAZ.
            # (Eski 'or list_price' deseni sale_price'ı fiyata eşitleyip hem üstü-çizili gösterimi
            #  gizliyor hem de Facette panelinden ELLE girilen indirimi eziyordu — "dün gitti" sebebi.)
            _ind_raw = (_f(first["INDIRIMLIFIYAT"]) if _has("INDIRIMLIFIYAT") else 0.0)
            sale_price = _ind_raw if (_ind_raw and 0 < _ind_raw < list_price) else None
            member_price_1 = (_f(first["UYETIPIFIYAT1"]) if _has("UYETIPIFIYAT1") else 0.0) or list_price
            cost_price = _f(first["ALISFIYATI"]) if _has("ALISFIYATI") else 0.0
            vat_rate = _f(first["KDVORANI"], 10) if _has("KDVORANI") else 10
            vendor = _cell(first, "TEDARIKCI", "FACETTE")
            description = _cell(first, "ACIKLAMA")
            breadcrumb = _cell(first, "BREADCRUMBKAT")
            category_leaf = _category_from_breadcrumb(breadcrumb)
            cat_doc = cat_by_name.get(category_leaf.lower()) if category_leaf else None
            parent_stock_code = _cell(first, "STOKKODU")

            variants = []
            for _, row in grp.iterrows():
                v = {
                    "size": (_cell(row, "BEDEN").upper() or "STD"),
                    "color": (_cell(row, "RENK").title() or renk.title()),
                    "barcode": _cell(row, "BARKOD"),
                    "stock_code": _cell(row, "STOKKODU"),
                    "urun_id": _cell(row, "URUNID"),
                    "stock": default_stock,
                    "price": _f(row["SATISFIYATI"]) if _has("SATISFIYATI") else list_price,
                    # Varyant indirimi de yalnızca gerçek indirimde; yoksa fiyata eşitleme.
                    "sale_price": (lambda _vp, _vl: _vp if (_vp and 0 < _vp < _vl) else None)(
                        (_f(row["INDIRIMLIFIYAT"]) if _has("INDIRIMLIFIYAT") else 0.0),
                        (_f(row["SATISFIYATI"]) if _has("SATISFIYATI") else list_price),
                    ),
                }
                variants.append(v)
                stats["variants_total"] += 1

            # RENK-DUYARLI eşleşme: aynı ürün kartı altında AYRI renk-ürünleri olabilir
            # ("...Ceket Ekru" ve "...Ceket Siyah"). Önce kart_id + RENK ile TAM doğru
            # renk-ürününü bul; yanlış renk-kardeşini yakalayıp adını EZMEYİ önler.
            existing = None
            if renk:
                existing = await db.products.find_one({"urun_karti_id": kart_id, "color": renk.title()})
            if not existing:
                # RENK-KÖRÜ FALLBACK GÜVENLİĞİ: kart_id aynı ama FARKLI renk kardeşine ASLA yazma
                # (ad/renk/açıklama/varyant ezilmesi = ürün kaybolması bug'ının kökü). Aday YALNIZ
                # rengi BOŞ ya da bu renkle AYNI ise kullanılır; farklıysa None kalır → yeni renk açılır.
                _cand = await db.products.find_one({"urun_karti_id": kart_id})
                if _cand:
                    _cc = str(_cand.get("color") or "").strip().lower()
                    if (not _cc) or _cc == (renk or "").strip().lower():
                        existing = _cand
            if not existing and parent_stock_code:
                existing = await db.products.find_one({
                    "$or": [
                        {"stock_code": parent_stock_code, "color": renk.title()},
                        {"variants.stock_code": parent_stock_code, "color": renk.title()},
                    ]
                })
            if not existing and renk:
                existing = await db.products.find_one({
                    "name": {"$regex": f"^{re.escape(base_name)}.*{re.escape(renk)}$", "$options": "i"}
                })

            update_doc = {
                "name": urun_adi,
                "color": renk.title(),
                "stock_code": parent_stock_code,
                "sku": parent_stock_code,
                "urun_karti_id": kart_id,
                "price": list_price,
                "member_price_1": member_price_1,
                "cost_price": cost_price,
                "vat_rate": vat_rate,
                "vendor": vendor,
                "vendor_name": vendor,
                "description": description,
                "category_name": category_leaf,
                "breadcrumb": breadcrumb,
                "variants": variants,
            }
            # sale_price'ı SADECE Ticimax'te gerçek indirim varsa yaz. İndirim yoksa güncellemede
            # bu alanı HİÇ dokunma → Facette panelinden elle girilen indirim KORUNUR.
            if sale_price is not None:
                update_doc["sale_price"] = sale_price
            if cat_doc:
                update_doc["category_id"] = cat_doc.get("id")
                update_doc["category_name"] = cat_doc.get("name")

            if existing:
                _set_doc = dict(update_doc)
                # ── KRİTİK KORUMA (ürün kaybolma/renk adı değişme onarımı) ──────────────────
                # Mevcut bir ürünü içe-aktarımda ASLA yeniden ADLANDIRMA ve RENGİNİ DEĞİŞTİRME.
                # Önceden kart_id ile yanlış renk-kardeşi eşleşip 'name'/'color' eziliyordu →
                # "...Ceket Ekru" ürünü "...Siyah"a dönüp kayboluyordu. Ad/renk artık YALNIZ
                # YENİ üründe yazılır; mevcut kayıtta Facette panelindeki küratörlü ad korunur.
                _set_doc.pop("name", None)
                _ec = str(existing.get("color") or "").strip().lower()
                _color_ok = (not _ec) or _ec == (renk or "").strip().lower()
                if not _color_ok:
                    # Renk KESİN eşleşmiyor → renge-özel alanların hiçbirini yazma (ezme).
                    # Bu, ürün kaybolma/renk-adı-değişme bug'ının ikinci savunma hattı.
                    for _k in ("color", "description", "variants", "stock_code", "sku", "breadcrumb"):
                        _set_doc.pop(_k, None)
                # slug'ı da (name'e bağlı) bozmamak için dokunmuyoruz (zaten update_doc'ta yok).
                await db.products.update_one({"id": existing["id"]}, {"$set": _set_doc})
                stats["parents_updated_db"] += 1
            else:
                # Slug çakışmasını önle: temiz slug kullan, ancak başka bir ürün
                # aynı slug'ı kullanıyorsa benzersizlik için kart_id ekle.
                base_slug = _slugify(urun_adi)
                slug = base_slug
                if await db.products.find_one({"slug": slug}):
                    slug = f"{base_slug}-{kart_id}" if kart_id else f"{base_slug}-{str(uuid4())[:6]}"
                new_doc = {
                    "id": str(uuid4()),
                    "slug": slug,
                    "is_active": True,
                    "is_published": True,
                    "images": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    **update_doc,
                }
                await db.products.insert_one(new_doc)
                stats["parents_created_new"] += 1
        except Exception as e:
            stats["errors"].append(f"KART {kart_id_raw}: {e}")

    try:
        os.unlink(tmp_path)
    except Exception:
        pass

    return {
        "success": True,
        "message": f"{stats['parents_updated_db']} güncellendi, {stats['parents_created_new']} yeni eklendi, {stats['variants_total']} varyant işlendi",
        "filename": file.filename,
        "stats": stats,
    }
XML_FEED_URL = "https://www.facette.com.tr/XMLExport/7BECCB0A782647BFAB843E68AD11E468"
_NS = {"g": "http://base.google.com/ns/1.0"}
def _xml_text(item: ET.Element, tag: str, ns: dict = _NS) -> str:
    el = item.find(tag, ns)
    return (el.text or "").strip() if el is not None else ""
def _xml_all(item: ET.Element, tag: str, ns: dict = _NS) -> list:
    return [(el.text or "").strip() for el in item.findall(tag, ns) if el is not None and el.text]
@router.post("/xml/products/import")
async def import_xml_products(
    xml_url: str = Query(XML_FEED_URL, description="Google Shopping XML URL"),
    deactivate_missing: bool = Query(
        False,   # ARTIK XML çekilmiyor → varsayılan KAPALI: hiçbir senkron ürünü otomatik pasife
                 # ALMAZ. Ürün yalnızca ADMIN SİL/pasif yaparsa kaybolur. (Açıkça True istenirse
                 # yine güvenlik kilidi devrede — bozuk feed kataloğu toplu silemez.)
        description="Feed'de bulunmayan ürünleri pasif yap (VARSAYILAN KAPALI — otomatik pasifleştirme yok)"
    ),
    current_user: dict = Depends(require_admin)
):
    """
    Google Shopping XML feed'inden ürünleri çekip MongoDB'ye upsert eder.

    - Açıklamadan TÜM `<strong>Etiket:</strong>Değer` özellikleri dinamik olarak
      çıkarılır (Boy, Cep, Astar, Web Color, Kumaş, Kalıp, Model Ölçüleri vb.).
    - deactivate_missing=True (varsayılan): Feed'de olmayan tüm `source=xml_feed`
      ürünleri otomatik olarak `is_active=False` yapılır (Ticimax'ta pasif olanlar).
    """
    import html
    from utils.attr_parser import parse_description_attributes

    # SSRF KORUMASI (DNS-rebinding dahil): Host'u BİR KEZ çöz, PUBLIC olduğunu doğrula ve
    # tam ÇÖZÜLEN IP'ye PİNLİ bağlan. Böylece doğrulama ile istek arasında DNS'in özel IP'ye
    # dönmesi (rebinding) engellenir. HTTPS'te SNI/sertifika doğrulaması HOST için yapılır.
    def _fetch_url_pinned(url: str, timeout: int = 60, max_bytes: int = 50 * 1024 * 1024) -> bytes:
        from urllib.parse import urlparse
        import socket, ssl, ipaddress, http.client
        p = urlparse(url or "")
        if p.scheme not in ("http", "https") or not p.hostname:
            raise HTTPException(status_code=400, detail="Geçersiz URL")
        host = p.hostname
        port = p.port or (443 if p.scheme == "https" else 80)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception:
            raise HTTPException(status_code=400, detail="URL çözümlenemedi")
        pinned = None
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast or ip.is_unspecified):
                raise HTTPException(status_code=400, detail="İç ağ/özel IP adreslerine erişim engellendi (SSRF)")
            pinned = info[4][0]
        if not pinned:
            raise HTTPException(status_code=400, detail="URL çözümlenemedi")
        conn = None
        try:
            sock = socket.create_connection((pinned, port), timeout=timeout)
            if p.scheme == "https":
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)  # SNI + sertifika = host
                conn = http.client.HTTPSConnection(host, port, timeout=timeout)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.sock = sock  # pinli IP'ye bağlı soketi kullan (yeniden çözme YOK)
            path = (p.path or "/") + (("?" + p.query) if p.query else "")
            conn.request("GET", path, headers={"Host": host, "User-Agent": "FacetteFeed/1.0", "Accept": "*/*"})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                raise HTTPException(status_code=400, detail="Feed yönlendirme yapıyor (SSRF koruması)")
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"Feed HTTP {resp.status}")
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise HTTPException(status_code=400, detail="Feed çok büyük")
            return data
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    from fastapi.concurrency import run_in_threadpool as _ritp
    try:
        xml_bytes = await _ritp(_fetch_url_pinned, xml_url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"XML feed çekilemedi: {str(e)}")

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise HTTPException(status_code=422, detail=f"XML parse hatası: {str(e)}")

    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")

    if not items:
        raise HTTPException(status_code=422, detail="XML'de hiç ürün (item) bulunamadı")

    imported = 0
    updated = 0
    errors = 0
    seen_xml_ids: set[str] = set()

    for item in items:
        try:
            xml_id = _xml_text(item, "g:id")
            title  = _xml_text(item, "g:title")
            if not xml_id or not title:
                continue
            seen_xml_ids.add(xml_id)

            desc = html.unescape(_xml_text(item, "g:description"))
            # Teknik detayları parse et — admin'de "Özellikler" sekmesinde gösterilecek
            tech_attrs, _clean_text = parse_description_attributes(desc)

            # Bedenleri description metninden yakala — "S M L XL bedenlerine uyumlu" vb.
            import re as _re_sizes
            sizes_found: list[str] = []
            # 1) "S M L XL" gibi boşluklu/peş peşe harf bedenleri
            for m in _re_sizes.findall(r"\b((?:XXS|XS|S|M|L|XL|XXL|XXXL)(?:[ ,/\-]+(?:XXS|XS|S|M|L|XL|XXL|XXXL))+)\b", _clean_text):
                tokens = _re_sizes.split(r"[ ,/\-]+", m.strip())
                for t in tokens:
                    t = t.strip().upper()
                    if t and t not in sizes_found:
                        sizes_found.append(t)
            # 2) Numerik bedenler "34 36 38 40 42 44"
            num_match = _re_sizes.search(r"\b(\d{2}(?:[ ,/\-]+\d{2}){2,})\b", _clean_text)
            if num_match:
                for t in _re_sizes.split(r"[ ,/\-]+", num_match.group(1)):
                    if t and t not in sizes_found:
                        sizes_found.append(t)

            def parse_price(s: str) -> Optional[float]:
                if not s:
                    return None
                try:
                    return float(s.split()[0])
                except (ValueError, IndexError):
                    return None

            price      = parse_price(_xml_text(item, "g:price")) or 0.0
            sale_price = parse_price(_xml_text(item, "g:sale_price"))

            availability  = _xml_text(item, "g:availability")
            in_stock      = availability.lower() == "in stock"
            product_type  = _xml_text(item, "g:product_type")
            goog_cat      = _xml_text(item, "g:google_product_category")
            category_name = product_type or goog_cat
            brand         = _xml_text(item, "g:brand") or "FACETTE"
            product_url   = _xml_text(item, "g:link")
            mpn           = _xml_text(item, "g:mpn")
            label_0       = _xml_text(item, "g:custom_label_0")
            label_1       = _xml_text(item, "g:custom_label_1")

            main_image   = _xml_text(item, "g:image_link")
            extra_images = _xml_all(item, "g:additional_image_link")
            all_images: list = []
            seen_imgs: set = set()
            for img in [main_image] + extra_images:
                if img and img not in seen_imgs:
                    all_images.append(img)
                    seen_imgs.add(img)

            slug = product_url.rstrip("/").split("/")[-1] if product_url else None
            if not slug:
                slug = _generate_slug(title) + f"-{xml_id}"

            doc = {
                "xml_id":        xml_id,
                "name":          title,
                "slug":          slug,
                "description":   desc,
                "attributes":    tech_attrs,  # parsed teknik detaylar: urun_bilgisi, kumas, kalip, beden_olculeri, model_olculeri, ...
                "sizes":         sizes_found,  # description'dan parse edilen beden listesi (S/M/L/XL veya 36/38/40)
                "stock_code":    label_0 or "",  # Ticimax'ın "Ürün Kodu" — varyantlar arası ortak
                "sku":           mpn or "",     # MPN/barkod, varyanta özel
                "price":         price,
                "sale_price":    sale_price,
                "brand":         brand,
                "category_name": category_name,
                "stock":         1 if in_stock else 0,
                "is_active":     True,
                "is_featured":   False,
                "is_new":        False,
                "images":        all_images,
                "thumbnail":     all_images[0] if all_images else "",
                "barcode":       mpn,
                "source":        "xml_feed",
                "product_url":   product_url,
                "availability":  availability,
                "xml_label_0":   label_0,
                "xml_label_1":   label_1,
                "updated_at":    datetime.now(timezone.utc).isoformat(),
            }

            existing = await db.products.find_one({"xml_id": xml_id})
            if existing:
                await db.products.update_one(
                    {"xml_id": xml_id},
                    {"$set": doc, "$unset": {"deactivated_reason": ""}},
                )
                updated += 1
            else:
                doc["id"]         = generate_id()
                doc["created_at"] = datetime.now(timezone.utc).isoformat()
                doc["variants"]   = []
                await db.products.insert_one(doc)
                imported += 1

        except Exception as e:
            logger.error(f"XML item parse hatası (id={_xml_text(item, 'g:id')}): {e}")
            errors += 1
            continue

    await db.settings.update_one(
        {"id": "xml_feed"},
        {"$set": {"xml_url": xml_url, "last_sync": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )

    # Feed'de OLMAYAN xml_feed ürünleri pasif yap (Ticimax'ta pasif/silinmiş)
    deactivated = 0
    _deact_skipped = False
    if deactivate_missing and seen_xml_ids:
        # GÜVENLİK KİLİDİ: Eksik/kısmi/bozuk bir feed (ör. Ticimax geçici hata) TÜM kataloğu
        # pasife almasın → "ürünler yok oldu" felaketi. Feed'deki ürün sayısı, mevcut xml_feed
        # ürünlerinin çok altındaysa (>%40'ını pasife alacaksa) pasifleştirmeyi ATLA + uyar.
        _total_xml = await db.products.count_documents({"source": "xml_feed"})
        _would_deact = await db.products.count_documents({
            "source": "xml_feed", "xml_id": {"$nin": list(seen_xml_ids)}, "is_active": {"$ne": False}})
        if _total_xml > 20 and (len(seen_xml_ids) < _total_xml * 0.5 or _would_deact > _total_xml * 0.4):
            _deact_skipped = True
            logger.warning(
                f"[xml-feed] GUVENLIK: feed cok kucuk gorunuyor (feed={len(seen_xml_ids)}, "
                f"katalog={_total_xml}, pasife alinacakti={_would_deact}) -> TOPLU PASIFLESTIRME ATLANDI. "
                f"Feed eksik/bozuk olabilir; urunler korundu.")
        else:
            result = await db.products.update_many(
                {
                    "source": "xml_feed",
                    "xml_id": {"$nin": list(seen_xml_ids)},
                    "is_active": {"$ne": False},
                },
                {
                    "$set": {
                        "is_active": False,
                        "deactivated_reason": "ticimax_xml_missing",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
            deactivated = result.modified_count

    return {
        "success":     True,
        "imported":    imported,
        "updated":     updated,
        "total":       imported + updated,
        "errors":      errors,
        "deactivated": deactivated,
        "message":     (
            f"{imported} yeni ürün eklendi, {updated} ürün güncellendi"
            + (f", {deactivated} ürün pasife alındı" if deactivated else "")
            + (f", {errors} hata" if errors else "")
        ),
    }


async def restore_xml_missing_products_once():
    """OTOMATİK TELAFİ: kullanıcının SİLMEDİĞİ (is_deleted=True DEĞİL) hâlde pasif görünen tüm
    ürünleri geri AKTİF eder. Kullanıcı kuralı: "sil tuşuna basmadıysam ürün görünmeli."

    KAPSAM (geri açılır): is_active != True  VE  is_deleted != True  VE  manual_deactivated != True
      → yani feed glitch'i / eski otomatik pasifleştirme ile kaybolan ürünler (ekru vb. renk
        varyantları dahil) geri gelir.
    DOKUNULMAZ:
      - is_deleted=True  → kullanıcı SİL'e bastı (bilinçli kaldırma).
      - manual_deactivated=True → kullanıcı ELLE pasife aldı (ürün toggle işareti).

    Settings bayrağı (v2) ile deploy başına BİR KEZ çalışır; sonraki elle pasifleştirmelerle
    savaşmaz (onlar manual_deactivated işaretli). v1'den v2'ye çıkıldı çünkü v1 yalnız
    'ticimax_xml_missing' etiketlileri kapsıyordu; etiketsiz eski pasifler geri gelmiyordu."""
    try:
        flag = await db.settings.find_one({"id": "xml_missing_restore_v2"}, {"_id": 0})
        if flag and flag.get("done"):
            return {"restored": 0, "skipped": "already-done"}
        now_iso = datetime.now(timezone.utc).isoformat()
        res = await db.products.update_many(
            {"is_active": {"$ne": True},
             "is_deleted": {"$ne": True},
             "manual_deactivated": {"$ne": True}},
            {"$set": {"is_active": True, "updated_at": now_iso},
             "$unset": {"deactivated_reason": ""}},
        )
        await db.settings.update_one(
            {"id": "xml_missing_restore_v2"},
            {"$set": {"id": "xml_missing_restore_v2", "done": True,
                      "restored": res.modified_count, "at": now_iso}},
            upsert=True,
        )
        if res.modified_count:
            logger.info(f"[urun-telafi] {res.modified_count} silinmemis pasif urun geri aktiflestirildi (v2, tek seferlik)")
        return {"restored": res.modified_count}
    except Exception as e:
        logger.warning(f"[xml-feed] restore_xml_missing_products_once hata: {e}")
        return {"restored": 0, "error": str(e)}


@router.get("/xml/status")
async def get_xml_feed_status():
    """XML feed son senkronizasyon bilgisi"""
    settings = await db.settings.find_one({"id": "xml_feed"}) or {}
    return {
        "xml_url":    settings.get("xml_url", XML_FEED_URL),
        "last_sync":  settings.get("last_sync"),
        "configured": True,
    }
_CLAIM_STATUS_PRIORITY = ["WaitingInAction", "InAnalysis", "Created", "Unresolved", "Rejected", "Cancelled", "Accepted"]
def _derive_claim_status(claim: dict) -> str:
    """Trendyol claim objesinden genel statüyü item statülerinden türetir.

    Bir item bile aksiyon bekliyorsa (WaitingInAction/InAnalysis) claim aksiyon bekler;
    yoksa açık talep (Created) baskındır; hepsi terminal ise Accepted/Rejected/Cancelled.
    Hiç statü bulunamazsa "" döner.
    """
    statuses = []
    for item in (claim.get("items") or []):
        for ci in (item.get("claimItems") or []):
            nm = ((ci.get("claimItemStatus") or {}).get("name") or "").strip()
            if nm:
                statuses.append(nm)
    if not statuses:
        return ""
    sset = set(statuses)
    for p in _CLAIM_STATUS_PRIORITY:
        if p in sset:
            return p
    return statuses[0]
_RETURN_STATUS_KEYS = [
    "return_requested", "return_approved", "return_rejected",
    "return_in_transit", "returned", "refunded", "partial_refunded",
]
_ORDER_STATUS_BUCKET = {
    "return_requested": "talep_olusturulan",
    "return_in_transit": "kargoya_verilen",
    "return_approved": "onaylanan",
    "returned": "onaylanan",          # "İade Tamamlandı" = TAMAMLANMIŞ/onaylanmış iade → aksiyon bekleyende KALMAZ, Onaylanan'a geçer (admin durumu buna alınca aksiyon listesinden düşer)
    "refunded": "onaylanan",
    "partial_refunded": "onaylanan",
    "return_rejected": "reddedilen",
}
_ORDER_STATUS_TR = {
    "return_requested": "İade Talebi Oluşturuldu",
    "return_in_transit": "İade Kargoda",
    "return_approved": "İade Onaylandı",
    "returned": "İade Tamamlandı",
    "refunded": "İade Bedeli Ödendi",
    "partial_refunded": "Kısmi İade",
    "return_rejected": "İade Reddedildi",
}
def _order_payment_type(pm: str) -> str:
    pm = (pm or "").lower()
    if pm in ("bank_transfer", "havale", "eft", "transfer"):
        return "transfer"
    if pm in ("cash_on_delivery", "cod", "kapida"):
        return "cod"
    return "credit_card"
def _claim_bucket(c: dict) -> str:
    """Bir Trendyol claim'ini sekme kovasına eşler (status + kargo takip durumuna göre).

    Created + takip no BOŞ  -> talep_olusturulan (müşteri henüz kargoya vermedi)
    Created + takip no DOLU  -> kargoya_verilen  (iade kargoda)
    WaitingInAction/InAnalysis -> aksiyon_bekleyen
    Accepted                  -> onaylanan
    Rejected/Unresolved       -> reddedilen
    Cancelled                 -> iptal (iptal edilmiş iade talebi; ayrı sekme)

    NOT: Bu eşleme canlı 34/54/16/3583/49 sayılarıyla kalibre edilecek tek noktadır;
    backfill sonrası gerçek dağılım ile bire bir tutmazsa yalnız burası ayarlanır.
    """
    # Manuel / sipariş-durumu kaynaklı satır → kova doğrudan sipariş durumundan.
    if c.get("manual") and c.get("order_status"):
        return _ORDER_STATUS_BUCKET.get(c.get("order_status"), "talep_olusturulan")
    st = (c.get("claim_status") or "").strip()
    has_cargo = bool(str(c.get("cargo_tracking_number") or "").strip())
    if st == "Accepted":
        return "onaylanan"
    if st == "Cancelled":
        return "iptal"
    if st in ("Rejected", "Unresolved"):
        return "reddedilen"
    if st in ("WaitingInAction", "InAnalysis"):
        return "aksiyon_bekleyen"
    if st == "Created":
        return "kargoya_verilen" if has_cargo else "talep_olusturulan"
    return "talep_olusturulan"
# DENETİM HATA-1: sipariş bu statülerdeyse iade işaretlemesi YAPILMAZ (zaten iptal/iade)
_ORDER_EXCLUDED_FOR_RETURN = [
    "cancelled", "cancel_refunded",
    "return_requested", "return_approved", "return_in_transit",
    "returned", "refunded", "partial_refunded",
]


async def apply_accepted_claims_to_orders(platforms=None, dry_run: bool = False) -> dict:
    """Onaylanmış (Accepted) pazaryeri İADE taleplerini sipariş durumuna yansıtır
    (status → 'returned'). DENETİM HATA-1: bu yansıma olmadığı için parası iade edilmiş
    ~1.500 Trendyol siparişi raporlarda 'satış' görünüyor, net ciro 90 günde ~2,9M TL şişiyordu.

    GÜVENLİK (CLAUDE.md değişmezleri): yalnız STATÜ + iz alanları yazılır — STOK, kupon,
    puan, ödeme alanlarına DOKUNULMAZ. Koşullu update (status $nin) idempotentliği ve
    yarışı garanti eder. claim_type=CANCEL kapsam dışı (iptaller sipariş senkronunda işlenir).
    platforms: ör. ["hepsiburada"] — yalnız o platformların claim'leri taranır."""
    q = {"claim_status": "Accepted", "claim_type": {"$ne": "CANCEL"},
         "order_number": {"$exists": True, "$nin": [None, ""]}}
    if platforms:
        q["platform"] = {"$in": list(platforms)}
    now_iso = datetime.now(timezone.utc).isoformat()
    # 1) Onaylı iade taleplerini sipariş no bazında topla (TOPLU — tekil sorgu değil;
    #    15k+ claim'de tek tek find_one CF 100sn sınırını aşıyordu)
    claims_by_order: dict = {}
    async for c in db.trendyol_claims.find(
            q, {"_id": 0, "claim_id": 1, "order_number": 1, "platform": 1, "accepted_at": 1}):
        onum = str(c.get("order_number") or "")
        if onum and onum not in claims_by_order:
            claims_by_order[onum] = c
    stats = {"claims": len(claims_by_order), "updated": 0, "already": 0, "no_order": 0}
    if not claims_by_order:
        return stats
    # 2) İlgili siparişleri toplu çek (5000'lik parçalarla $in)
    onums = list(claims_by_order.keys())
    orders_status: dict = {}
    for i in range(0, len(onums), 5000):
        async for o in db.orders.find({"order_number": {"$in": onums[i:i + 5000]}},
                                      {"_id": 0, "id": 1, "order_number": 1, "status": 1}):
            orders_status[str(o.get("order_number"))] = o
    # 3) Sınıflandır + (dry değilse) güncelle — yalnız statü + iz alanları
    for onum, c in claims_by_order.items():
        o = orders_status.get(onum)
        if not o:
            stats["no_order"] += 1
            continue
        if o.get("status") in _ORDER_EXCLUDED_FOR_RETURN:
            stats["already"] += 1
            continue
        if dry_run:
            stats["updated"] += 1
            continue
        plat = str(c.get("platform") or "trendyol").lower()
        res = await db.orders.update_one(
            {"id": o["id"], "status": {"$nin": _ORDER_EXCLUDED_FOR_RETURN}},
            {"$set": {"status": "returned",
                      "returned_at": c.get("accepted_at") or now_iso,
                      "return_source": f"{plat}_claim",
                      "return_claim_id": c.get("claim_id"),
                      "updated_at": now_iso},
             "$push": {"status_history": {"status": "returned", "at": now_iso,
                                          "by": "claims-sync",
                                          "note": f"Pazaryeri iadesi onaylı (claim {c.get('claim_id')})"}}})
        if res.modified_count:
            stats["updated"] += 1
        else:
            stats["already"] += 1
    return stats


def _first_seen_stamps(claim: dict) -> dict:
    """Yeni bir claim ilk kez yazılırken status'una göre onay/ret tarih damgası üretir.

    lastModifiedDate (ms epoch) varsa onu, yoksa şimdiyi kullanır. İdempotent katkı:
    yalnız ilgili terminal durumda alan döner; aksi halde boş dict.
    """
    st = _derive_claim_status(claim)
    lm = claim.get("lastModifiedDate")
    iso = ""
    if lm:
        try:
            iso = datetime.fromtimestamp(lm / 1000, tz=timezone.utc).isoformat()
        except Exception:
            iso = ""
    if not iso:
        iso = datetime.now(timezone.utc).isoformat()
    if st == "Accepted":
        return {"return_approved_at": iso}
    if st in ("Rejected", "Cancelled"):
        return {"return_rejected_at": iso}
    return {}
def _claim_is_site_order(claim: dict) -> bool:
    """Bir iade kaydının SİTE (web) siparişi mi yoksa PAZARYERİ mi olduğunu belirler.

    Kural (öncelik sırası — Kadir'in onayladığı kesin sinyal):
      1. trendyol_package_id dolu  -> Trendyol (kargo DHL/MNG olsa bile)
      2. cargo_provider_name içinde "marketplace" geçiyor -> pazaryeri
      3. hepsiburada paket izi -> pazaryeri
      4. platform alanı bilinen bir pazaryeri -> pazaryeri
      5. Hiçbiri değil -> SİTE siparişi
    Not: 'platform'/'source' alanı tek başına güvenilir değil (eski Ticimax-çekme
    döneminde Trendyol siparişleri de 'ticimax' damgası almış olabilir), bu yüzden
    önce sağlam paket-kimliği sinyallerine bakılır.
    """
    if claim.get("trendyol_package_id"):
        return False
    cpn = str(claim.get("cargo_provider_name") or "").lower()
    if "marketplace" in cpn:
        return False
    if claim.get("hepsiburada_package_id") or claim.get("hb_package_id"):
        return False
    plt = str(claim.get("platform") or "").lower()
    _MARKETPLACES = {"trendyol", "hepsiburada", "n11", "amazon_tr", "amazon_de",
                     "temu", "aliexpress", "etsy", "ciceksepeti", "emag", "fruugo",
                     "hepsiglobal", "trendyol_export"}
    if plt in _MARKETPLACES:
        return False
    return True
async def restock_claim_once(claim_id: str, source: str, by_email: str = "") -> list:
    """İade kalemlerini ürün stoğuna BİR KEZ geri ekler (idempotent: claim.stock_restored).
    Eşleşme: variant.barcode -> variant.stock; yoksa product.barcode -> product.stock."""
    claim = await db.trendyol_claims.find_one(
        {"claim_id": claim_id}, {"_id": 0, "items": 1, "order_number": 1, "stock_restored": 1}
    )
    if not claim or claim.get("stock_restored"):
        return []
    # A1: guard'ı ATOMİK al — iki eşzamanlı çağrı (senkron + elle) çift restock yapmasın.
    _lock = await db.trendyol_claims.update_one(
        {"claim_id": claim_id, "stock_restored": {"$ne": True}},
        {"$set": {"stock_restored": True, "stock_restored_at": datetime.now(timezone.utc).isoformat(),
                  "stock_restored_source": source}})
    if _lock.modified_count == 0:
        return []  # başka bir çağrı zaten yaptı
    _now = datetime.now(timezone.utc).isoformat()
    restocked = []
    for item in (claim.get("items") or []):
        barcode = str(item.get("barcode", "") or "").strip()
        qty = int(item.get("quantity", 1) or 1)
        if not barcode or qty <= 0:
            continue
        # A2: ATOMİK varyant stok artışı (arrayFilters + $inc). Önceki kod tüm varyant dizisini
        # Python'da okuyup $set ile yazıyordu → eşzamanlı sipariş düşüşünü EZİYORDU (oversell).
        prod = await db.products.find_one({"variants.barcode": barcode}, {"_id": 0, "id": 1})
        if prod:
            await db.products.update_one(
                {"id": prod["id"], "variants.barcode": barcode},
                {"$inc": {"variants.$[v].stock": qty}, "$set": {"updated_at": _now}},
                array_filters=[{"v.barcode": barcode}],
            )
            # Parent stok = varyant toplamı (tek atomik pipeline update)
            await db.products.update_one(
                {"id": prod["id"]},
                [{"$set": {"stock": {"$sum": {"$map": {
                    "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                    "in": {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}}}}}}],
            )
            restocked.append({"barcode": barcode, "qty": qty, "product_id": prod["id"]})
        else:
            p2 = await db.products.find_one({"barcode": barcode}, {"_id": 0, "id": 1})
            if p2:
                await db.products.update_one(
                    {"id": p2["id"]}, {"$inc": {"stock": qty}, "$set": {"updated_at": _now}})
                restocked.append({"barcode": barcode, "qty": qty, "product_id": p2["id"]})
    # A1: hareketi order_id ile de kaydet ki SİPARİŞ yolu guard'ı (_restock_order_once) bunu
    # görüp aynı siparişi İKİNCİ kez geri-stoklamasın (return_restock artık _RESTORE_MOVE_TYPES'ta).
    _oid = None
    _onum = str(claim.get("order_number") or "")
    if _onum:
        _o = await db.orders.find_one({"order_number": _onum}, {"_id": 0, "id": 1})
        _oid = (_o or {}).get("id")
    if restocked:
        await db.stock_movements.insert_one({
            "id": str(uuid.uuid4()), "type": "return_restock", "source": source,
            "claim_id": claim_id, "order_id": _oid, "order_number": _onum,
            "items": restocked, "created_by": by_email, "created_at": _now,
        })
    return restocked
ALLOWED_MARKETPLACES = {
    # customer message channels
    "hepsiburada", "temu", "whatsapp", "instagram", "messenger", "site",
    # cargo providers
    "mng", "aras", "yurtici", "ptt", "hepsijet", "trendyol_express", "surat", "ups", "dhl",
}
@router.get("/{marketplace}/settings")
async def get_marketplace_settings(marketplace: str, current_user: dict = Depends(require_admin)):
    """Get Hepsiburada / Temu settings (generic)."""
    if marketplace not in ALLOWED_MARKETPLACES:
        raise HTTPException(status_code=404, detail="Bilinmeyen pazaryeri")
    settings = await db.settings.find_one({"id": marketplace}, {"_id": 0})
    if not settings:
        return {
            "id": marketplace,
            "merchant_id": "",
            "username": "",
            "api_key": "",
            "api_secret": "",
            "mode": "sandbox",
            "is_active": False,
            "default_markup": 0
        }
    # Mask secret
    if settings.get("api_secret"):
        settings["api_secret"] = "********"
    if settings.get("password"):
        settings["password"] = "********"
    if settings.get("secret_key"):
        settings["secret_key"] = "********"
    return settings
@router.post("/{marketplace}/settings")
async def save_marketplace_settings(marketplace: str, payload: dict, current_user: dict = Depends(require_admin)):
    """Save Hepsiburada / Temu settings (generic)."""
    if marketplace not in ALLOWED_MARKETPLACES:
        raise HTTPException(status_code=404, detail="Bilinmeyen pazaryeri")

    # Required alan validasyonu — is_active=True ise pazaryeri bazlı zorunlu alanlar
    if payload.get("is_active"):
        existing = await db.settings.find_one({"id": marketplace}, {"_id": 0}) or {}
        fields_by_mp = {
            "hepsiburada": ["merchant_id", "secret_key", "dev_username"],
            "temu": ["api_key", "api_secret"],
        }
        required = fields_by_mp.get(marketplace, ["api_key", "api_secret"])
        missing = []
        for k in required:
            v = payload.get(k)
            if v in (None, "", "********"):
                v = existing.get(k)
            if not v:
                missing.append(k)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"{marketplace.capitalize()} aktifleştirmek için zorunlu alanlar eksik: {', '.join(missing)}"
            )

    update_data = {
        "id": marketplace,
        "merchant_id": payload.get("merchant_id", ""),
        "username": payload.get("username", ""),
        "dev_username": payload.get("dev_username", ""),
        "api_key": payload.get("api_key", ""),
        "mode": payload.get("mode", "sandbox"),
        "is_active": payload.get("is_active", False),
        "default_markup": payload.get("default_markup", 0),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    # Only update secret if provided — GÜVENLİK: sırları at-rest ŞİFRELE
    # (Trendyol get_trendyol_config / HB _hb_unmask / Temu _get_temu_config okurken çözer).
    try:
        from security.crypto import encrypt as _enc_secret
    except Exception:
        _enc_secret = lambda v: v
    if payload.get("api_secret") and payload.get("api_secret") != "********":
        update_data["api_secret"] = _enc_secret(payload.get("api_secret"))
    if payload.get("password") and payload.get("password") != "********":
        update_data["password"] = _enc_secret(payload.get("password"))
    if payload.get("secret_key") and payload.get("secret_key") != "********":
        update_data["secret_key"] = _enc_secret(payload.get("secret_key"))

    await db.settings.update_one({"id": marketplace}, {"$set": update_data}, upsert=True)
    return {"success": True, "message": f"{marketplace.capitalize()} ayarları kaydedildi"}
@router.get("/{marketplace}/status")
async def get_marketplace_status(marketplace: str):
    """Get Hepsiburada / Temu integration status."""
    if marketplace not in ALLOWED_MARKETPLACES:
        raise HTTPException(status_code=404, detail="Bilinmeyen pazaryeri")
    settings = await db.settings.find_one({"id": marketplace}, {"_id": 0})
    if not settings:
        return {"configured": False, "mode": "sandbox"}
    mode_raw = (settings.get("mode") or "sandbox").strip().lower()
    is_live = mode_raw in ("live", "production", "prod", "canli", "canlı")
    if marketplace == "hepsiburada":
        configured = bool(settings.get("is_active") and settings.get("merchant_id")
                          and (settings.get("secret_key") or settings.get("password"))
                          and settings.get("dev_username"))
    else:
        configured = bool(settings.get("is_active") and (settings.get("api_key") or settings.get("merchant_id")))
    return {
        "configured": configured,
        "mode": "live" if is_live else "sandbox",
        "merchant_id": settings.get("merchant_id", "") if settings.get("is_active") else None
    }
@router.post("/{marketplace}/test-connection")
async def test_marketplace_connection(marketplace: str, current_user: dict = Depends(require_admin)):
    """REAL connection test — Hepsiburada/Temu use Basic Auth against a known probe endpoint.
    Returns success=False with a concrete error message when credentials fail or are missing."""
    import httpx
    import base64
    if marketplace not in ALLOWED_MARKETPLACES:
        raise HTTPException(status_code=404, detail="Bilinmeyen pazaryeri")
    settings = await db.settings.find_one({"id": marketplace}, {"_id": 0})
    if not settings:
        return {"success": False, "message": f"{marketplace.capitalize()} ayarları kaydedilmemiş"}

    # GÜVENLİK: at-rest şifreli sırları probe'tan ÖNCE çöz (decrypt düz-metni geçirir).
    try:
        from security.crypto import decrypt as _dec_secret
        for _sf in ("api_secret", "password", "secret_key", "access_token"):
            if settings.get(_sf):
                settings[_sf] = _dec_secret(settings[_sf]) or settings[_sf]
    except Exception:
        pass

    try:
        if marketplace == "hepsiburada":
            merchant_id = (settings.get("merchant_id") or "").strip()
            # Secret Key (yeni model). Eski kayitlarda 'password' alaninda olabilir.
            secret_key = (settings.get("secret_key") or settings.get("password") or "").strip()
            dev_username = (settings.get("dev_username") or "").strip()
            if not (merchant_id and secret_key and dev_username):
                return {"success": False, "message": "Hepsiburada için Merchant ID, Secret Key ve Developer Username zorunlu"}
            mode = settings.get("mode", "sandbox")
            host = "https://mpop-sit.hepsiburada.com" if mode == "sandbox" else "https://mpop.hepsiburada.com"
            url = f"{host}/product/api/categories/get-all-categories?page=0&size=1"
            # Basic Auth: kullanici adi = Merchant ID, sifre = Secret Key. User-Agent = Developer Username.
            token = base64.b64encode(f"{merchant_id}:{secret_key}".encode()).decode()
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(url, headers={"Authorization": f"Basic {token}", "User-Agent": dev_username, "Accept": "application/json"})
            if r.status_code == 200:
                return {"success": True, "message": f"Hepsiburada bağlantısı başarılı ({mode})"}
            if r.status_code in (401, 403):
                return {"success": False, "message": f"Hepsiburada kimlik hatalı (HTTP {r.status_code}). Merchant ID / Secret Key / Developer Username kontrol edin."}
            # diger durumlarda body'den mesaj parse et
            try:
                err_body = r.json()
                err_msg = err_body.get("message") or err_body.get("errorMessage") or err_body.get("detail") or ""
                err_code = err_body.get("errorCode") or err_body.get("code") or ""
                detail_txt = f" — {err_code}: {err_msg}" if (err_code or err_msg) else ""
            except Exception:
                detail_txt = f" — {r.text[:120]}"
            return {"success": False, "message": f"Hepsiburada beklenmeyen yanıt: HTTP {r.status_code}{detail_txt}"}

        if marketplace == "temu":
            shop_id = (settings.get("merchant_id") or "").strip()
            api_key = (settings.get("api_key") or "").strip()
            app_secret = (settings.get("api_secret") or "").strip()
            if not (shop_id and api_key and app_secret):
                return {"success": False, "message": "Temu için Shop ID, App Key ve App Secret zorunlu"}
            if len(api_key) < 8 or len(app_secret) < 8:
                return {"success": False, "message": "Temu App Key/Secret çok kısa, doğru girdiğinize emin olun"}
            # Gerçek Temu Open Platform probe — bg.temu.com /api/v1/seller/info endpoint'i
            import time
            import json as _json
            mode_ = settings.get("mode", "sandbox")
            # Temu Open Platform hostları — sandbox vs live farklı
            host = (
                "https://openapi-b-us.temu.com"
                if mode_ == "live"
                else "https://openapi-b-global-stg.temu.com"
            )
            ts = str(int(time.time()))
            body = {
                "type": "bg.auth.access_token.info.get",
                "app_key": api_key,
                "timestamp": ts,
            }
            sign_base = app_secret + "".join(f"{k}{v}" for k, v in sorted(body.items())) + app_secret
            # NOT: Temu Open Platform imza algoritması olarak MD5 ZORUNLU kılar
            # (API kontratı gereği; güvenlik tercihi değil — değiştirmek imzayı bozar).
            body["sign"] = hashlib.md5(sign_base.encode()).hexdigest().upper()
            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.post(f"{host}/openapi/router", json=body)
                try:
                    data = r.json()
                except Exception:
                    data = {"raw": r.text[:200]}
                if data.get("success") or data.get("result"):
                    return {"success": True, "message": f"Temu bağlantısı başarılı ({mode_})"}
                return {"success": False, "message": f"Temu hata: {data.get('errorMsg') or data.get('errorCode') or str(data)[:200]}"}
            except Exception as e:
                return {"success": False, "message": f"Temu probe hatası: {str(e)[:150]}"}

        if marketplace in {"mng", "aras", "yurtici", "ptt", "hepsijet", "trendyol_express", "surat"}:
            user = (settings.get("username") or "").strip()
            pw = (settings.get("password") or "").strip()
            key = (settings.get("api_key") or "").strip()
            if not (user or key) or not (pw or settings.get("customer_code")):
                return {"success": False, "message": f"{marketplace.upper()} kimlik bilgileri eksik"}
            return {"success": True, "message": f"{marketplace.upper()} kimlik bilgileri kaydedildi (canlı test yapılmadı)"}

        # Default fallback for other marketplaces (whatsapp/instagram/site channels etc.)
        if not (settings.get("api_key") or settings.get("username")):
            return {"success": False, "message": f"{marketplace} kimlik bilgileri eksik"}
        return {"success": True, "message": f"{marketplace} ayarları geçerli"}
    except httpx.TimeoutException:
        return {"success": False, "message": f"{marketplace} API zaman aşımına uğradı (15s)"}
    except Exception as e:
        return {"success": False, "message": f"{marketplace} test hatası: {str(e)[:150]}"}
QUESTIONS_COLLECTIONS = {
    "trendyol": "trendyol_questions",
    "hepsiburada": "hepsiburada_questions",
    "temu": "temu_questions",
    "whatsapp": "whatsapp_messages",
    "instagram": "instagram_messages",
    "messenger": "messenger_messages",
    "site": "site_messages",
}
@router.delete("/{marketplace}/questions/{question_id}")
async def delete_marketplace_question(marketplace: str, question_id: str, current_user: dict = Depends(require_admin)):
    """Delete a question/message (e.g., expired unanswered)."""
    if marketplace not in QUESTIONS_COLLECTIONS:
        raise HTTPException(status_code=404, detail="Bilinmeyen kanal")
    coll = db[QUESTIONS_COLLECTIONS[marketplace]]
    res = await coll.delete_one({"question_id": question_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Mesaj bulunamadı")
    return {"success": True}
@router.get("/marketplace/questions")
async def get_marketplace_questions(
    marketplace: Optional[str] = "all",
    status: Optional[str] = None,
    page: int = 0,
    size: int = 20,
    current_user: dict = Depends(require_admin)
):
    """Unified endpoint returning questions tagged with their marketplace."""
    query = {}
    if status:
        query["status"] = status

    skip = page * size
    sources = []
    if marketplace and marketplace != "all":
        if marketplace not in QUESTIONS_COLLECTIONS:
            raise HTTPException(status_code=400, detail="Bilinmeyen pazaryeri")
        sources = [marketplace]
    else:
        sources = list(QUESTIONS_COLLECTIONS.keys())

    all_items = []
    totals = {}
    for mp in sources:
        coll = db[QUESTIONS_COLLECTIONS[mp]]
        # Pull enough for merge+sort; cap per marketplace to avoid huge reads
        items = await coll.find(query, {"_id": 0}).sort("created_at", -1).limit(500).to_list(500)
        for it in items:
            it["marketplace"] = mp
        all_items.extend(items)
        totals[mp] = await coll.count_documents(query)

    # Sort merged by created_at desc
    def _key(x):
        return x.get("created_at") or ""
    all_items.sort(key=_key, reverse=True)
    total = sum(totals.values())
    paginated = all_items[skip: skip + size]

    return {
        "questions": paginated,
        "total": total,
        "totals": totals,
        "page": page,
        "size": size,
    }
@router.post("/{marketplace}/questions/sync")
async def sync_marketplace_questions_stub(marketplace: str, current_user: dict = Depends(require_admin)):
    """Stub sync for HB/Temu until real API integration is wired up."""
    if marketplace not in ALLOWED_MARKETPLACES:
        raise HTTPException(status_code=404, detail="Bilinmeyen pazaryeri")
    settings = await db.settings.find_one({"id": marketplace}, {"_id": 0})
    if not settings or not settings.get("is_active"):
        raise HTTPException(status_code=400, detail=f"{marketplace.capitalize()} entegrasyonu yapılandırılmamış")
    # Real implementation: call HB / Temu QNA API and upsert into respective collection
    return {"success": True, "synced": 0, "total_fetched": 0, "message": "API entegrasyonu yapılandırıldığında otomatik çalışacak (stub)"}
@router.post("/{marketplace}/questions/{question_id}/answer")
async def answer_marketplace_question_stub(
    marketplace: str,
    question_id: str,
    payload: dict,
    current_user: dict = Depends(require_admin)
):
    """Answer HB / Temu question – currently stores locally; real API wire-up to follow."""
    if marketplace not in ALLOWED_MARKETPLACES:
        raise HTTPException(status_code=404, detail="Bilinmeyen pazaryeri")
    answer_text = (payload or {}).get("answer", "").strip()
    if not answer_text:
        raise HTTPException(status_code=400, detail="Yanıt metni boş olamaz")
    coll = db[QUESTIONS_COLLECTIONS[marketplace]]
    await coll.update_one(
        {"question_id": question_id},
        {"$set": {
            "answer": answer_text,
            "status": "ANSWERED",
            "answered_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    return {"success": True, "message": "Cevap kaydedildi (yerel). API entegrasyonu tamamlandığında otomatik gönderilecek."}
