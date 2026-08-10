"""
integrations_trendyol.py — Trendyol pazaryeri entegrasyonu (ürün/stok/sipariş/iade/soru-cevap).
2026-07-01 refactor: integrations.py'den ayrıştırıldı (bkz. integrations_common.py başlığı).
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

from .deps import db, logger, get_current_user, require_admin, generate_id, generate_short_id, get_effective_permissions
from facette_defaults import facette_fixed_value_for  # tüm-pazaryeri sabit varsayılan (gap-fill)

router = APIRouter(tags=["Integrations-Trendyol"])

from .integrations_common import (
    _BAD_COMPOSITION_VALUES,
    _ORDER_STATUS_TR,
    _RETURN_STATUS_KEYS,
    _build_product_query_from_payload,
    _claim_bucket,
    _claim_is_site_order,
    _ORDER_EXCLUDED_FOR_RETURN,
    apply_accepted_claims_to_orders,
    _closest_trendyol_value,
    _decrement_stock_for_imported_order,
    _dedupe_products_by_stock_code,
    _derive_claim_status,
    _first_seen_stamps,
    _mp_base_price,
    _ms_to_iso,
    _norm_val,
    _normalize_attr_key,
    _order_payment_type,
    _resolve_stock_code,
    _resolve_value_id,
    _search_tr_regex,
    log_integration_event,
    restock_claim_once,
)

# ---- Request/response modelleri ----
class TrendyolOrderPreviewReq(BaseModel):
    order_number: Optional[str] = None
    start_date_ms: Optional[int] = None
    end_date_ms: Optional[int] = None

class TrendyolOrderImportReq(BaseModel):
    orders: List[dict]

class CategoryMappingReq(BaseModel):
    local_category_id: str
    local_name: str
    trendyol_category_id: int
    trendyol_category_name: str

class AttributeMapping(BaseModel):
    local_attr: str
    trendyol_attr_id: int

class AttributeMappingReq(BaseModel):
    attribute_mappings: List[AttributeMapping]
    default_mappings: Optional[dict] = {}


async def get_trendyol_config():
    """Get Trendyol configuration from DB or env.
    NOTE: default_markup için Ana Ayarlar > trendyol_markup ÖNCELİKLİ — kullanıcı UI'da
    en son nereye girerse oradan okunur."""
    settings = await db.settings.find_one({"id": "trendyol"})
    # Main settings'ten markup override
    main_settings = await db.settings.find_one({"id": "main"}) or {}
    main_markup = main_settings.get("trendyol_markup")
    try:
        main_markup_f = float(main_markup) if (main_markup is not None and main_markup != "") else None
    except Exception:
        main_markup_f = None

    if settings:
        # DENETİM FIX: panel 'Canlı' için mode='prod' gönderiyor; eski kod yalnız =='live'
        # kontrol ettiğinden CANLI istekler SESSİZCE STAGING'e gidiyordu. Normalize et.
        mode = settings.get("mode", "sandbox")
        if str(mode).lower() in ("prod", "production", "canli", "canlı", "live"):
            mode = "live"
        local_markup = settings.get("default_markup", 0) or 0
        effective_markup = main_markup_f if main_markup_f is not None else local_markup
        # GÜVENLİK: api_secret at-rest şifreli (v1:...) olabilir → çöz. decrypt() düz-metni
        # geçirir; tüm Trendyol tüketicileri bu getter'dan okuduğu için tek nokta yeterli.
        try:
            from security.crypto import decrypt as _dec_secret
            _tr_secret = _dec_secret(settings.get("api_secret", "")) or ""
        except Exception:
            _tr_secret = settings.get("api_secret", "")
        return {
            "api_key": settings.get("api_key", ""),
            "api_secret": _tr_secret,
            "supplier_id": settings.get("supplier_id", ""),
            "is_active": settings.get("is_active", False),
            "mode": mode,
            "default_markup": effective_markup,
            "default_brand_id": settings.get("default_brand_id"),
            "default_cargo_company_id": settings.get("default_cargo_company_id"),
            "default_vat_rate": settings.get("default_vat_rate"),
            "base_url": 'https://api.trendyol.com' if mode == 'live' else 'https://stageapigw.trendyol.com'
        }
    
    # Fallback to env
    mode = os.environ.get('TRENDYOL_MODE', 'sandbox')
    if str(mode).lower() in ("prod", "production", "canli", "canlı", "live"):
        mode = "live"
    return {
        "api_key": os.environ.get('TRENDYOL_API_KEY', ''),
        "api_secret": os.environ.get('TRENDYOL_API_SECRET', ''),
        "supplier_id": os.environ.get('TRENDYOL_SUPPLIER_ID', ''),
        "is_active": bool(os.environ.get('TRENDYOL_API_KEY')),
        "mode": mode,
        "base_url": 'https://api.trendyol.com' if mode == 'live' else 'https://stageapigw.trendyol.com'
    }
async def get_trendyol_headers():
    config = await get_trendyol_config()
    if not config["api_key"] or not config["api_secret"]:
        return None
    credentials = f'{config["api_key"]}:{config["api_secret"]}'
    encoded = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "User-Agent": f'{config["supplier_id"]} - FacetteIntegration',
        "Content-Type": "application/json"
    }
def calculate_trendyol_price(base_price: float, product_data: dict, trendyol_config: dict) -> float:
    """Calculate price with markup logic"""
    # SSOT: her zaman global default_markup uygulanir; urun bazli override yok sayilir
    # (kullanici talebi: belirlenen oran disinda fiyat guncellenmez)
    markup = float(trendyol_config.get("default_markup", 0) or 0)

    final_price = base_price * (1 + markup / 100)
    return round(final_price, 2)


@router.get("/trendyol/settings")
async def get_trendyol_settings(current_user: dict = Depends(require_admin)):
    """Get Trendyol settings"""
    config = await get_trendyol_config()

    # Single source of truth: Ana Ayarlar sayfasındaki `trendyol_markup`
    # main settings'ten yazıldıysa ÖNCELİKLİ olarak onu kullan; aksi halde
    # Trendyol Integration kartındaki default_markup'a düş.
    main_settings = await db.settings.find_one({"id": "main"})
    main_markup = (main_settings or {}).get("trendyol_markup")
    if main_markup is not None and main_markup != "":
        try:
            default_markup = float(main_markup)
        except Exception:
            default_markup = config.get("default_markup", 0) or 0
    else:
        default_markup = config.get("default_markup", 0) or 0

    # Mask secrets
    return {
        "supplier_id": config.get("supplier_id", ""),
        "api_key": config.get("api_key", ""),
        "api_secret": "********" if config.get("api_secret") else "",
        "mode": config.get("mode", "sandbox"),
        "is_active": config.get("is_active", False),
        "default_markup": default_markup,
        "default_brand_id": config.get("default_brand_id"),
        "default_cargo_company_id": config.get("default_cargo_company_id"),
        "default_vat_rate": config.get("default_vat_rate")
    }
@router.post("/trendyol/settings")
async def save_trendyol_settings(
    settings: dict,
    current_user: dict = Depends(require_admin)
):
    """Save Trendyol settings"""
    from datetime import datetime, timezone

    # Required alan validasyonu — is_active=True ise supplier_id/api_key/api_secret zorunlu
    if settings.get("is_active"):
        existing = await db.settings.find_one({"id": "trendyol"}, {"_id": 0}) or {}
        supplier_id = settings.get("supplier_id") or existing.get("supplier_id")
        api_key = settings.get("api_key") or existing.get("api_key")
        api_secret = settings.get("api_secret")
        if api_secret in (None, "", "********"):
            api_secret = existing.get("api_secret")
        missing = [k for k, v in {"supplier_id": supplier_id, "api_key": api_key, "api_secret": api_secret}.items() if not v]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Trendyol aktifleştirmek için zorunlu alanlar eksik: {', '.join(missing)}"
            )

    update_data = {
        "supplier_id": settings.get("supplier_id", ""),
        "api_key": settings.get("api_key", ""),
        "mode": settings.get("mode", "sandbox"),
        "is_active": settings.get("is_active", False),
        "default_markup": settings.get("default_markup", 0),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    if settings.get("api_secret") and settings.get("api_secret") != "********":
        # GÜVENLİK: api_secret'ı at-rest ŞİFRELE (get_trendyol_config okurken çözer).
        try:
            from security.crypto import encrypt as _enc_secret
            update_data["api_secret"] = _enc_secret(settings.get("api_secret"))
        except Exception:
            update_data["api_secret"] = settings.get("api_secret")

    # Faz T3 (white-label): kanal varsayilanlari — yalniz gonderildiyse yaz (yoksa mevcut korunur).
    for _k in ("default_brand_id", "default_cargo_company_id", "default_vat_rate"):
        if _k in settings:
            _v = settings.get(_k)
            if _v in (None, ""):
                update_data[_k] = None
            else:
                try:
                    update_data[_k] = int(_v)
                except (TypeError, ValueError):
                    update_data[_k] = None

    await db.settings.update_one(
        {"id": "trendyol"},
        {"$set": update_data},
        upsert=True
    )

    # SSOT: trendyol_markup'i main settings'e de yansıt (UI'da ana ayarlar sayfası bu key'i okur)
    try:
        await db.settings.update_one(
            {"id": "main"},
            {"$set": {"trendyol_markup": update_data["default_markup"]}},
            upsert=True,
        )
    except Exception:
        pass

    return {"success": True, "message": "Trendyol ayarları kaydedildi"}
@router.post("/trendyol/test-connection")
async def test_trendyol_connection(current_user: dict = Depends(require_admin)):
    """Trendyol gerçek bağlantı testi — brands endpoint'i üzerinden."""
    cfg = await get_trendyol_config()
    if not cfg.get("api_key") or not cfg.get("api_secret") or not cfg.get("supplier_id"):
        return {"success": False, "message": "Trendyol API bilgileri eksik (api_key / api_secret / supplier_id zorunlu)"}
    try:
        from trendyol_client import TrendyolClient
        client = TrendyolClient(
            supplier_id=cfg["supplier_id"],
            api_key=cfg["api_key"],
            api_secret=cfg["api_secret"],
            mode=cfg["mode"],
        )
        # Hafif bir probe — ilk 1 marka yeter
        data = await client.get_brands(size=1, page=0)
        if isinstance(data, dict) and ("brands" in data or "content" in data or "totalElements" in data):
            return {"success": True, "message": "Trendyol bağlantısı başarılı", "mode": cfg["mode"]}
        return {"success": False, "message": f"Beklenmeyen yanıt: {str(data)[:200]}"}
    except Exception as e:
        msg = str(e)[:300]
        status_hint = "401/403 kimlik hatası" if ("401" in msg or "403" in msg or "Unauthorized" in msg) else "HTTP hatası"
        return {"success": False, "message": f"{status_hint}: {msg}"}
@router.get("/trendyol/status")
async def get_trendyol_status():
    """Get Trendyol integration status"""
    config = await get_trendyol_config()
    return {
        "configured": config["is_active"],
        "mode": config["mode"],
        "supplier_id": config["supplier_id"] if config["is_active"] else None
    }
@router.get("/trendyol/debug")
async def debug_trendyol_orders(current_user: dict = Depends(require_admin)):
    # DENETİM FIX: auth eklendi — bu uç kimlik doğrulamasız gerçek sipariş/müşteri PII sızdırıyordu.
    config = await get_trendyol_config()
    import sys
    import os
    import time
    from datetime import datetime, timezone
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )
    
    now = datetime.now()
    start = now - timedelta(days=14)
    end_date_ms = int(now.timestamp() * 1000)
    start_date_ms = int(start.timestamp() * 1000)
    
    try:
        resp = await client.get_orders(start_date_ms=start_date_ms, end_date_ms=end_date_ms, size=50)
        return resp
    except Exception as e:
        return {"error": str(e)}
@router.post("/trendyol/categories/sync")
async def sync_trendyol_categories(current_user: dict = Depends(require_admin)):
    """Sync and save category tree from Trendyol API to local DB"""
    config = await get_trendyol_config()
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    
    try:
        client = TrendyolClient(
            supplier_id=config["supplier_id"],
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            mode=config["mode"]
        )
        categories = await client.get_categories()
        
        # Save to DB (Drop and re-insert for clean sync)
        if categories:
            await db.trendyol_categories.delete_many({})
            await db.trendyol_categories.insert_many(categories)
            
        return {"success": True, "message": f"{len(categories)} kategori senkronize edildi."}
    except Exception as e:
        logger.error(f"Error fetching trendyol categories: {str(e)}")
        raise HTTPException(status_code=500, detail="Trendyol kategorileri alınamadı")
@router.get("/trendyol/categories/{category_id}/attributes")
async def get_trendyol_category_attributes(category_id: int, refresh: bool = Query(False, description="True ise cache atlanır, Trendyol'dan taze ve eksiksiz çekilir"), current_user: dict = Depends(require_admin)):
    """Get attributes for a specific category (From DB or Trendyol API directly)"""
    config = await get_trendyol_config()
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    
    # Check if already in local DB (refresh=true ise cache atla — eksik/eski cache'i yenile)
    if not refresh:
        existing = await db.trendyol_attributes.find_one({"category_id": category_id})
        if existing and existing.get("attributes"):
            return {"success": True, "attributes": existing.get("attributes", []), "cached": True}
        
    try:
        client = TrendyolClient(
            supplier_id=config["supplier_id"],
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            mode=config["mode"]
        )
        attributes = await client.get_category_attributes(category_id)
        
        # Save to local DB for future use.
        # T1 (TEK KAYNAK): editör "özellikleri yenile" dediğinde, GÖNDERİMİN (push)
        # okuduğu kanonik cache'i (`trendyol_category_attributes`) de aynı veriyle tazele
        # ki iki cache ayrışmasın ve push eksik şemayla göndermesin. Eklemeli + geri alınabilir.
        if attributes:
            from datetime import datetime, timezone
            _now = datetime.now(timezone.utc).isoformat()
            await db.trendyol_attributes.update_one(
                {"category_id": category_id},
                {"$set": {"category_id": category_id, "attributes": attributes, "updated_at": _now}},
                upsert=True
            )
            await db.trendyol_category_attributes.update_one(
                {"category_id": category_id},
                {"$set": {"category_id": category_id, "attributes": attributes, "updated_at": _now}},
                upsert=True
            )

        _val_count = sum(len(a.get("attributeValues") or []) for a in (attributes or []))
        return {"success": True, "attributes": attributes,
                "count": len(attributes or []), "value_count": _val_count}
    except Exception as e:
        logger.error(f"Error fetching trendyol attributes for category {category_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Özellikler (attributes) alınamadı")
@router.post("/trendyol/brands/sync")
async def sync_trendyol_brands(current_user: dict = Depends(require_admin)):
    """Sync brands from Trendyol API to local DB"""
    config = await get_trendyol_config()
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    
    try:
        client = TrendyolClient(
            supplier_id=config["supplier_id"],
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            mode=config["mode"]
        )
        # Assuming maximum 500 per page, just fetching first page for demonstration. In prod, paginate.
        brands_data = await client.get_brands(size=5000)
        brands = brands_data.get("brands", [])
        
        if brands:
            await db.trendyol_brands.delete_many({})
            await db.trendyol_brands.insert_many(brands)
            
        return {"success": True, "message": f"{len(brands)} marka senkronize edildi."}
    except Exception as e:
        logger.error(f"Error fetching trendyol brands: {str(e)}")
        raise HTTPException(status_code=500, detail="Trendyol markaları alınamadı")
_TRENDYOL_ATTR_SYNONYMS = {
    "materyal bileşeni": ["urun icerik bilgisi", "kumas bilgisi", "kumas icerigi", "urun icerigi", "icerik bilgisi"],
}
def _bridge_trendyol_attr_synonyms(local_vals: dict) -> dict:
    """Trendyol özellik adlarıyla (ör. 'Materyal Bileşeni') lokal özellik adları
    (ör. 'Ürün İçerik Bilgisi') farklı olabilir. Eksik Trendyol anahtarını uygun
    lokal kaynaktan köprüler. local_vals: {lower(label): value}."""
    if not local_vals:
        return local_vals
    norm_index: dict = {}
    for k, v in local_vals.items():
        norm_index.setdefault(_normalize_attr_key(k), v)
    for target, sources in _TRENDYOL_ATTR_SYNONYMS.items():
        if target in local_vals and local_vals.get(target):
            continue
        for src in sources:
            val = norm_index.get(src)
            if val and str(val).strip().casefold() not in _BAD_COMPOSITION_VALUES:
                local_vals[target] = val
                break
    return local_vals
@router.post("/trendyol/products/validate")
async def validate_products_for_trendyol(
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """
    Aktarım öncesi DOĞRULAMA paneli — ürün(ler)i Trendyol'a göndermeden,
    eksik zorunlu alanları (kategori mapping, barkod, görsel, zorunlu attribute)
    listeleyip raporlar. Body sync ile aynı.
    """
    payload = await request.json()
    query = await _build_product_query_from_payload(payload)
    products = await db.products.find(query, {"_id": 0}).to_list(length=None)
    # 🛡️ Aynı stock_code ile dublike doküman varsa, görseli/source'u en iyi olanı seç
    products = _dedupe_products_by_stock_code(products)

    # Category mappings (category_id -> mapping doc) — Trendyol için
    cm_list = await db.category_mappings.find(
        {"marketplace": "trendyol"}, {"_id": 0}
    ).to_list(length=3000)
    cm_by_local = {str(c.get("category_id")): c for c in cm_list}
    # Fallback: kategori adına göre
    all_cats = await db.categories.find({}, {"_id": 0}).to_list(length=5000)
    cat_by_id = {str(c.get("id")): c for c in all_cats}
    cat_by_name = {(c.get("name") or "").strip(): c for c in all_cats}
    cm_by_name = {}
    for c in all_cats:
        nm = (c.get("name") or "").strip()
        if nm and str(c.get("id")) in cm_by_local:
            cm_by_name[nm] = cm_by_local[str(c.get("id"))]

    # Trendyol mp_cat -> required attribute listesini cache'le
    attr_cache: dict = {}

    async def get_required_attrs(mp_cat_id):
        if not mp_cat_id:
            return []
        key = str(mp_cat_id)
        if key in attr_cache:
            return attr_cache[key]
        try:
            cached = await db.trendyol_category_attributes.find_one(
                {"category_id": int(mp_cat_id) if str(mp_cat_id).isdigit() else str(mp_cat_id)},
                {"_id": 0},
            )
            attrs = (cached or {}).get("attributes", []) or []
        except Exception:
            attrs = []
        required = [a for a in attrs if a.get("required")]
        attr_cache[key] = required
        return required

    results = []
    valid_count = 0
    invalid_count = 0

    for p in products:
        errors: list[str] = []
        warnings: list[str] = []
        missing_required_attrs: list[dict] = []
        unmatched_values: list[dict] = []

        cat_id = p.get("category_id")
        cat_name = p.get("category_name") or ""
        # Sync ile aynı sıra: category_id → category_name → categories.trendyol_category_id
        cm = cm_by_local.get(str(cat_id)) if cat_id else None
        if not cm and cat_name:
            cm = cm_by_name.get(cat_name.strip())
        cat_doc = cat_by_id.get(str(cat_id)) or cat_by_name.get(cat_name.strip())

        mp_cat_id = None
        if cm and cm.get("marketplace_category_id"):
            mp_cat_id = cm.get("marketplace_category_id")
        elif cat_doc and cat_doc.get("trendyol_category_id"):
            mp_cat_id = cat_doc.get("trendyol_category_id")

        if not mp_cat_id:
            errors.append("Trendyol kategori eşleştirmesi yok")

        # Görsel kontrolü
        if not (p.get("images") or []):
            errors.append("En az 1 ürün görseli yok")

        # Barkod kontrolü (varyantlı ürünlerde tüm varyantlar)
        # ⚠️ barcode_uncertain=True ise barkod yok kabul edilir (stock_code'tan kopyalanmış olabilir)
        variants = p.get("variants") or []
        if not variants:
            if not p.get("barcode") or p.get("barcode_uncertain"):
                errors.append("Barkod yok / belirsiz (Ticimax'tan doğrulayın)")
            if not p.get("stock_code"):
                warnings.append("Stok kodu yok")
        else:
            missing_v_barcode = sum(
                1 for v in variants if not v.get("barcode") or v.get("barcode_uncertain")
            )
            if missing_v_barcode:
                errors.append(f"{missing_v_barcode} varyantın barkodu eksik/belirsiz")

        # Fiyat
        try:
            if float(p.get("price", 0) or 0) <= 0:
                errors.append("Fiyat 0 veya boş")
        except Exception:
            errors.append("Fiyat geçersiz")

        # Stok
        total_stock = int(p.get("stock", 0) or 0) + sum(int(v.get("stock", 0) or 0) for v in variants)
        if total_stock <= 0:
            warnings.append("Toplam stok 0")

        # Açıklama
        if not (p.get("description") or p.get("short_description")):
            warnings.append("Açıklama boş")

        # Zorunlu attribute kontrolü (category_mappings → attribute_mappings + default_mappings)
        if mp_cat_id:
            req_attrs = await get_required_attrs(mp_cat_id)
            attr_mappings = (cm or {}).get("attribute_mappings", []) or []
            default_mappings = (cm or {}).get("default_mappings", {}) or {}
            # local_attr → mp_attr_id map'ını da kur
            mp_id_to_local = {}
            for am in attr_mappings:
                mid = str(am.get("mp_attr_id") or am.get("trendyol_attr_id") or "")
                if mid:
                    mp_id_to_local[mid] = am.get("local_attr")

            # Ürünün attribute'larından lokal isim → değer (DICT veya LIST formatını destekler)
            local_vals: dict = {}

            def _add_lv(nm, vv):
                if not nm or vv in (None, ""):
                    return
                local_vals.setdefault(str(nm).lower().strip(), str(vv))

            def _walk(attrs):
                if isinstance(attrs, dict):
                    items = sorted(
                        attrs.items(),
                        key=lambda kv: 1 if str(kv[0]).lower().startswith("ticimax_") else 0,
                    )
                    for k, v in items:
                        if isinstance(v, dict):
                            nm = v.get("label") or v.get("name") or k
                            vv = v.get("value") or v.get("attribute_value")
                            _add_lv(nm, vv)
                        elif v is not None:
                            _add_lv(k, v)
                elif isinstance(attrs, list):
                    for a in attrs:
                        if isinstance(a, dict):
                            nm = a.get("label") or a.get("name") or a.get("type") or a.get("attribute_name")
                            vv = a.get("value") or a.get("attribute_value")
                            _add_lv(nm, vv)

            _walk(p.get("attributes"))
            for v in variants:
                _walk(v.get("attributes"))
                if v.get("color"):
                    _add_lv("Renk", v["color"])
                    _add_lv("Web Color", v["color"])
                if v.get("size"):
                    _add_lv("Beden", v["size"])
            _bridge_trendyol_attr_synonyms(local_vals)

            for ra in req_attrs:
                ra_id = str(ra.get("id") or ra.get("attribute", {}).get("id") or "")
                ra_name = ra.get("name") or ra.get("attribute", {}).get("name") or "(?)"
                if not ra_id:
                    continue
                # default mapping var mı?
                default_val = default_mappings.get(ra_id) or default_mappings.get(str(ra_id))
                if default_val:
                    continue
                # local attribute mapping var mı + üründe değer var mı?
                local_attr = mp_id_to_local.get(ra_id)
                has_val = False
                if local_attr:
                    has_val = bool(local_vals.get(local_attr.lower()))
                if not has_val:
                    missing_required_attrs.append({
                        "id": ra_id,
                        "name": ra_name,
                        "mapped_local": local_attr,
                    })
            if missing_required_attrs:
                errors.append(f"{len(missing_required_attrs)} zorunlu özellik eksik")

            # 🔎 Listeli (enum) değerlerin Trendyol karşılığı var mı? Yoksa aktarım engellenir
            # (allowCustom alanlar serbest metindir, her zaman gönderilir → kontrol edilmez).
            val_mappings_v = (cm or {}).get("value_mappings", {}) or {}
            try:
                _cache_attrs = await db.trendyol_category_attributes.find_one(
                    {"category_id": int(mp_cat_id) if str(mp_cat_id).isdigit() else str(mp_cat_id)},
                    {"_id": 0},
                )
            except Exception:
                _cache_attrs = None
            local_norm_index_v = {}
            for lk, lv in local_vals.items():
                local_norm_index_v.setdefault(_normalize_attr_key(lk), lv)
            for a in (_cache_attrs or {}).get("attributes", []) or []:
                aid = a.get("id") or a.get("attribute", {}).get("id")
                if aid is None:
                    continue
                if bool(a.get("allowCustom") or a.get("attribute", {}).get("allowCustom")):
                    continue
                aname = a.get("name") or a.get("attribute", {}).get("name") or ""
                lval = local_norm_index_v.get(_normalize_attr_key(aname))
                if not lval:
                    la = mp_id_to_local.get(str(aid))
                    if la:
                        lval = local_vals.get(la.lower())
                if not lval:
                    continue
                if val_mappings_v.get(f"{aid}|{lval}"):
                    continue
                vname_map = {
                    _norm_val(v.get("name")): str(v.get("id"))
                    for v in (a.get("attributeValues") or [])
                    if v.get("id") is not None and v.get("name")
                }
                if _resolve_value_id(vname_map, lval):
                    continue
                # Trendyol'un bu özellik için KABUL ETTİĞİ değerler — kullanıcı buradan
                # doğru karşılığı seçsin (yoksa hiç yoksa özelliği silsin). allowCustom değilse
                # bu liste zorunlu; boşsa Trendyol serbest metne izin veriyordur.
                _ty_vals = [
                    {"id": str(v.get("id")), "name": str(v.get("name"))}
                    for v in (a.get("attributeValues") or [])
                    if v.get("id") is not None and v.get("name")
                ]
                # Kullanıcıya kolaylık: yazım/normalizasyon olarak en yakın Trendyol değeri öner
                _suggest = _closest_trendyol_value(lval, _ty_vals)
                unmatched_values.append({
                    "mp_attr_id": int(aid),
                    "attr_name": aname,
                    "local_value": lval,
                    "required": bool(a.get("required")),
                    "allow_custom": bool(a.get("allowCustom") or a.get("attribute", {}).get("allowCustom")),
                    "trendyol_values": _ty_vals,       # Trendyol'un kabul ettiği tüm değerler
                    "suggested_value": _suggest,        # en olası eşleşme (yoksa None)
                })
            if unmatched_values:
                errors.append(f"{len(unmatched_values)} değerin Trendyol karşılığı yok (eşleştirme gerekli)")

        is_valid = len(errors) == 0
        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1

        results.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "stock_code": _resolve_stock_code(p) or p.get("barcode") or "",
            "barcode": p.get("barcode"),
            "category_id": cat_id,          # yerel kategori — value-mapping kaydı bu id'ye yazılır
            "category_name": cat_name,
            "marketplace_category_id": mp_cat_id,
            "is_valid": is_valid,
            "errors": errors,
            "warnings": warnings,
            "missing_required_attrs": missing_required_attrs,
            "unmatched_values": unmatched_values,
        })

    # Eksik attribute istatistikleri (en sık eksik olanları üstte göster)
    attr_freq: dict = {}
    for r in results:
        for m in r["missing_required_attrs"]:
            k = m["name"]
            attr_freq[k] = attr_freq.get(k, 0) + 1
    top_missing = sorted(attr_freq.items(), key=lambda x: -x[1])[:10]

    return {
        "success": True,
        "total": len(products),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "results": results,
        "top_missing_attrs": [{"name": k, "count": v} for k, v in top_missing],
    }
@router.get("/trendyol/batch/{batch_id}")
async def get_trendyol_batch_status(
    batch_id: str,
    current_user: dict = Depends(require_admin),
):
    """Trendyol batch işleminin (ürün oluşturma vb.) gerçek durumunu döndürür.
    UI'da kullanıcı 'Detayları Gör' butonuna basınca her item'ın SUCCESS/FAILED
    durumu ve failureReasons listesini görür."""
    config = await get_trendyol_config()
    if not config or not config.get("is_active"):
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu aktif değil")
    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config.get("mode", "live"),
    )
    try:
        data = await client.get_batch_request_result(batch_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Trendyol batch detayı alınamadı: {e}")

    items = data.get("items") or []
    success_count = sum(1 for it in items if str(it.get("status")).upper() == "SUCCESS")
    failed_count = sum(1 for it in items if str(it.get("status")).upper() == "FAILED")
    # Hata özetlerini topla
    fail_freq: dict = {}
    for it in items:
        for fr in (it.get("failureReasons") or []):
            key = str(fr).split(".")[0][:120]
            fail_freq[key] = fail_freq.get(key, 0) + 1
    return {
        "batch_id": batch_id,
        "status": data.get("status"),
        "source_type": data.get("sourceType"),
        "item_count": data.get("itemCount") or len(items),
        "success_count": success_count,
        "failed_count": failed_count,
        "top_failures": [{"reason": k, "count": v} for k, v in
                         sorted(fail_freq.items(), key=lambda x: -x[1])[:10]],
        "items": items,
        "raw": data,
    }
@router.get("/trendyol/barcode-duplicates")
async def trendyol_barcode_duplicates(current_user: dict = Depends(require_admin)):
    """DB içinde aynı barkoda atanmış birden fazla varyantı tespit eder.
    Bu Trendyol'a aktarımı bloklayan ana sebep oluyor. Manuel düzeltme için liste döner.
    """
    pipeline = [
        {"$match": {"variants.barcode": {"$nin": [None, ""]}}},
        {"$unwind": "$variants"},
        {"$match": {"variants.barcode": {"$nin": [None, ""]}}},
        {"$group": {
            "_id": "$variants.barcode",
            "count": {"$sum": 1},
            "products": {"$push": {
                "product_id": "$id",
                "stock_code": "$stock_code",
                "name": "$name",
                "is_active": "$is_active",
                "variant_size": "$variants.size",
                "variant_color": "$variants.color",
                "variant_stock": "$variants.stock",
            }}
        }},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 500},
    ]
    rows = await db.products.aggregate(pipeline).to_list(length=500)
    out = []
    for r in rows:
        out.append({
            "barcode": r["_id"],
            "count": r["count"],
            "assignments": r["products"],
        })
    return {"total": len(out), "duplicates": out}
@router.post("/trendyol/ghost-scanner")
async def trendyol_ghost_scanner(
    payload: dict = Body(default={}),
    current_user: dict = Depends(require_admin),
):
    """Trendyol panelindeki tüm ürünleri (max 5000) sayfalı çekip,
    DB'de KARŞILIK BULAMAYAN ya da DB'de archive edilmiş olanları "hayalet" olarak listeler.
    Bu hayaletler genelde eski yanlış barkod kayıtlarıdır ve duplicate çakışmalara sebep olur.
    """
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient

    cli = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    only_unmatched = bool(payload.get("only_unmatched", True))
    include_archived = bool(payload.get("include_archived", False))
    page_limit = int(payload.get("page_limit", 50))  # 50 sayfa x 200 = 10K ürün max

    # DB barkodlarını VE stock_code'larını topla
    db_barcodes = set()
    db_stock_codes = set()
    async for p in db.products.find({}, {"_id": 0, "barcode": 1, "stock_code": 1, "variants.barcode": 1, "variants.stock_code": 1}):
        if p.get("barcode"):
            db_barcodes.add(str(p["barcode"]))
        if p.get("stock_code"):
            db_stock_codes.add(str(p["stock_code"]))
        for v in (p.get("variants") or []):
            if v.get("barcode"):
                db_barcodes.add(str(v["barcode"]))
            if v.get("stock_code"):
                db_stock_codes.add(str(v["stock_code"]))

    ghosts = []
    matched = 0
    total_scanned = 0
    page = 0
    while page < page_limit:
        try:
            res = await cli.get_filtered_products(
                page=page, size=200,
                archived=None if include_archived else False,
            )
        except Exception as e:
            return {"error": str(e), "scanned": total_scanned, "ghosts": ghosts}
        content = res.get("content") or []
        total_pages = res.get("totalPages") or 0
        if not content:
            break
        for row in content:
            total_scanned += 1
            bc = str(row.get("barcode") or "")
            sc = str(row.get("stockCode") or "")
            pmi = str(row.get("productMainId") or "")
            if not bc:
                continue
            # Match: barkod DB'de varsa VEYA stockCode/productMainId DB'de varsa "matched"
            if bc in db_barcodes or sc in db_barcodes or sc in db_stock_codes or pmi in db_stock_codes:
                matched += 1
                if only_unmatched:
                    continue
            else:
                ghosts.append({
                    "barcode": bc,
                    "stockCode": row.get("stockCode"),
                    "productMainId": row.get("productMainId"),
                    "title": row.get("title"),
                    "brand": row.get("brand"),
                    "approved": row.get("approved"),
                    "archived": row.get("archived"),
                    "onSale": row.get("onSale"),
                    "salePrice": row.get("salePrice"),
                    "quantity": row.get("quantity"),
                    "rejectReasonDetails": row.get("rejectReasonDetails"),
                })
        page += 1
        if page >= total_pages:
            break

    return {
        "scanned": total_scanned,
        "matched_in_db": matched,
        "ghosts_count": len(ghosts),
        "ghosts": ghosts,
    }


@router.post("/trendyol/coverage-gaps")
async def trendyol_coverage_gaps(
    payload: dict = Body(default={}),
    current_user: dict = Depends(require_admin),
):
    """Sistemdeki AKTİF ürün varyantlarından (BEDEN) Trendyol'da CANLI olmayanları
    (hiç yüklenmemiş / arşivli / onaysız / barkodsuz) listeler — "aktarılmamış bedenler".

    ghost-scanner'ın TERSİ yönü: orada Trendyol'da olup DB'de olmayanlar; burada DB'de
    (aktif) olup Trendyol'da (canlı) olmayanlar.

    payload:
      - only_in_stock (bool, default True): sadece stoğu > 0 olan eksik bedenleri getir.
      - page_limit (int, default 60): Trendyol ürün sayfası limiti (60x200=12K ürün).
    Salt-okunur; Trendyol'a hiçbir yazma yapmaz.
    """
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient

    cli = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"],
    )

    only_in_stock = bool(payload.get("only_in_stock", True))
    page_limit = int(payload.get("page_limit", 60))

    # 1) Trendyol'daki TÜM ürünleri (arşivli dahil) barkod bazında topla + durumları
    ty_by_bc: dict = {}
    ty_stockcodes: set = set()
    page = 0
    total_ty = 0
    while page < page_limit:
        try:
            res = await cli.get_filtered_products(page=page, size=200, archived=None)
        except Exception as e:
            return {"error": f"Trendyol ürünleri çekilemedi: {e}", "scanned_trendyol": total_ty}
        content = res.get("content") or []
        total_pages = res.get("totalPages") or 0
        if not content:
            break
        for row in content:
            bc = str(row.get("barcode") or "").strip()
            sc = str(row.get("stockCode") or "").strip()
            if bc:
                ty_by_bc[bc] = {
                    "approved": bool(row.get("approved")),
                    "archived": bool(row.get("archived")),
                    "onSale": bool(row.get("onSale")),
                    "quantity": row.get("quantity"),
                }
                total_ty += 1
            if sc:
                ty_stockcodes.add(sc)
        page += 1
        if page >= total_pages:
            break

    def _live(bc: str, sc: str):
        """Trendyol'da CANLI mı? (onaylı + arşivsiz). Yoksa neden döner."""
        st = ty_by_bc.get(bc)
        if not st and sc and sc in ty_by_bc:
            st = ty_by_bc.get(sc)
        if not st:
            # stockCode listesinde geçiyorsa 'var ama barkod farklı' olabilir
            if (bc and bc in ty_stockcodes) or (sc and sc in ty_stockcodes):
                return (False, "barkod_uyusmuyor")
            return (False, "yok")
        if st.get("archived"):
            return (False, "arsivli")
        if not st.get("approved"):
            return (False, "onaysiz")
        return (True, "canli")

    # 2) Aktif ürünleri gez, varyant (beden) bazında eksikleri çıkar
    gaps = []
    scanned_products = 0
    async for p in db.products.find(
        {"is_active": True, "is_deleted": {"$ne": True}, "manual_deactivated": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "variants": 1},
    ):
        scanned_products += 1
        present = 0
        missing = []
        for v in (p.get("variants") or []):
            bc = str(v.get("barcode") or "").strip()
            sc = str(v.get("stock_code") or "").strip()
            stock = 0
            try:
                stock = int(v.get("stock") or 0)
            except Exception:
                stock = 0
            if not bc and not sc:
                reason = "barkodsuz"
                ok = False
            else:
                ok, reason = _live(bc, sc)
            if ok:
                present += 1
                continue
            if only_in_stock and stock <= 0:
                continue
            missing.append({
                "size": v.get("size"),
                "color": v.get("color"),
                "barcode": bc or None,
                "stock_code": sc or None,
                "stock": stock,
                "reason": reason,  # yok / arsivli / onaysiz / barkodsuz / barkod_uyusmuyor
            })
        if missing:
            gaps.append({
                "product_id": p.get("id"),
                "name": p.get("name"),
                "stock_code": p.get("stock_code"),
                "variants_live_on_trendyol": present,
                "missing_count": len(missing),
                "missing": missing,
            })

    # partial = TY'de bazı bedenleri CANLI ama bazıları eksik (net "aktarılmamış beden")
    partial = [g for g in gaps if g["variants_live_on_trendyol"] > 0]
    fully = [g for g in gaps if g["variants_live_on_trendyol"] == 0]
    total_missing = sum(g["missing_count"] for g in gaps)
    partial.sort(key=lambda g: g["missing_count"], reverse=True)
    fully.sort(key=lambda g: g["missing_count"], reverse=True)

    return {
        "only_in_stock": only_in_stock,
        "trendyol_products_scanned": total_ty,
        "active_products_scanned": scanned_products,
        "total_missing_variants": total_missing,
        "partial_products_count": len(partial),   # asıl hedef: kısmen yüklü ürünler
        "fully_missing_products_count": len(fully),
        "partial": partial[:300],
        "fully_missing": fully[:300],
    }


@router.post("/trendyol/archive-barcodes")
async def trendyol_archive_barcodes(
    payload: dict = Body(...),
    current_user: dict = Depends(require_admin),
):
    """Verilen barkodları Trendyol'da arşivler (panelden gizler, slot iadesi sağlar).
    Hayalet ürünlerin temizliği için kullanılır.
    """
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    barcodes = [str(b).strip() for b in (payload.get("barcodes") or []) if str(b).strip()]
    if not barcodes:
        raise HTTPException(status_code=400, detail="Arşivlenecek barkod listesi boş.")
    if len(barcodes) > 1000:
        raise HTTPException(status_code=400, detail="Tek seferde max 1000 barkod arşivlenebilir.")

    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient

    cli = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    try:
        resp = await cli.archive_products(barcodes)
        batch_id = (resp or {}).get("batchRequestId")
        from datetime import datetime, timezone
        log_doc = {
            "id": generate_id(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "archive",
            "products_attempted": len(barcodes),
            "batch_request_id": batch_id,
            "archived_barcodes": barcodes,
            "trendyol_response": resp,
            "message": f"{len(barcodes)} barkod için arşivleme batch'i Trendyol'a gönderildi (batch: {batch_id}).",
        }
        await db.trendyol_sync_logs.insert_one(log_doc)
        log_doc.pop("_id", None)
        return {
            "success": bool(batch_id),
            "batchRequestId": batch_id,
            "barcodes_count": len(barcodes),
            "response": resp,
        }
    except Exception as e:
        logger.error(f"Archive barcodes error: {e}")
        raise HTTPException(status_code=500, detail=f"Trendyol arşiv hatası: {str(e)}")
@router.post("/trendyol/products/sync")
async def sync_products_to_trendyol(
    request: Request,
    current_user: dict = Depends(require_admin)
):
    """Sync products to Trendyol via Batch Request"""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
    
    # DENETİM FIX (#52): boş gövde POST'unda request.json() 500 veriyordu — dayanıklı parse.
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    product_ids = payload.get("product_ids", [])
    category_filters = payload.get("category_filters", [])
    # Yeni: Barkod/stok kodu ile filtre (kullanıcı UI'da yazıp aktarabilsin)
    barcodes_raw = payload.get("barcodes", [])
    stock_codes_raw = payload.get("stock_codes", [])
    card_ids_raw = payload.get("card_ids", [])
    barcodes = [str(b).strip() for b in (barcodes_raw or []) if str(b).strip()]
    stock_codes = [str(s).strip() for s in (stock_codes_raw or []) if str(s).strip()]
    card_ids = [str(c).strip() for c in (card_ids_raw or []) if str(c).strip()]
    # Yeni: Tarih aralığı (created_at — ürün eklenme tarihi)
    date_from = payload.get("date_from")  # ISO format "2026-01-01"
    date_to = payload.get("date_to")
    
    query = {}
    if product_ids:
        query = {"id": {"$in": product_ids}}
    elif barcodes or stock_codes or card_ids:
        # Hem ürünün kendi barcode/stock_code'una hem de variants[] içine bak
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
        # Build an $or query for each category + its filters
        or_conditions = []
        for cf in category_filters:
            cat_id = cf.get("category_id")
            filters = cf.get("filters", {})
            try:
                from bson.objectid import ObjectId
                cat = await db.categories.find_one({"_id": ObjectId(cat_id)})
            except Exception:
                cat = await db.categories.find_one({"id": cat_id})

            if not cat:
                continue
            
            cat_q = {"category_name": cat.get("name")}
            if filters.get("stock_code"):
                # A3: kullanıcı girdisini re.escape ile kaçır — ham regex/ReDoS enjeksiyonu önlenir.
                cat_q["stock_code"] = {"$regex": re.escape(str(filters["stock_code"])), "$options": "i"}
            if filters.get("date_range"):
                try:
                    from datetime import datetime, timezone
                    # Format: YYYY-MM-DD
                    date_obj = datetime.strptime(filters["date_range"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    cat_q["created_at"] = {"$gte": date_obj.isoformat()}
                except Exception:
                    pass
            or_conditions.append(cat_q)
            
        if or_conditions:
            query = {"$or": or_conditions}
        else:
            query = {"is_active": True}
    else:
        # DENETİM FIX (#52): hiçbir filtre yoksa (product_ids/barcodes/stock_codes/card_ids/
        # category_filters ve tarih aralığı da yok) SESSİZCE tüm aktif ürünleri üretime itmek
        # tehlikeli — yanlışlıkla binlerce ürün gönderilebilir. Tarih aralığı tek başına
        # yeterli bir daraltmadır; onun dışında en az bir hedef seçimi zorunlu.
        if not (date_from or date_to):
            raise HTTPException(
                status_code=400,
                detail="En az bir hedef seçin: ürün, barkod/stok kodu, kart ID, kategori "
                       "veya tarih aralığı. Filtresiz toplu aktarım güvenlik için engellendi.",
            )
        query = {"is_active": True}

    # Tarih aralığı filtresi (created_at) — diğer filtrelerle AND ile birleşir
    if date_from or date_to:
        date_q: dict = {}
        if date_from:
            date_q["$gte"] = date_from
        if date_to:
            date_q["$lte"] = date_to + "T23:59:59" if "T" not in str(date_to) else date_to
        query = {"$and": [query, {"created_at": date_q}]} if query else {"created_at": date_q}

    products = await db.products.find(query).to_list(length=None)
    # 🛡️ Aynı stock_code ile dublike doküman varsa, görseli/source'u en iyi olanı seç
    products = _dedupe_products_by_stock_code(products)

    # 🔍 Hangi stok kodları / barkodlar DB'de bulundu? Bulunmayanları topla.
    not_found_codes = []
    if barcodes or stock_codes:
        found_barcodes = set()
        found_stock_codes = set()
        for p in products:
            if p.get("barcode"):
                found_barcodes.add(str(p["barcode"]))
            if p.get("stock_code"):
                found_stock_codes.add(str(p["stock_code"]))
            if p.get("sku"):
                found_stock_codes.add(str(p["sku"]))
            for v in (p.get("variants") or []):
                if v.get("barcode"):
                    found_barcodes.add(str(v["barcode"]))
                if v.get("stock_code"):
                    found_stock_codes.add(str(v["stock_code"]))
        requested = set([str(c) for c in (barcodes or [])] + [str(c) for c in (stock_codes or [])])
        not_found_codes = sorted([c for c in requested if c not in found_barcodes and c not in found_stock_codes])

    # ❗ Hiç ürün bulunamadıysa anında net mesajla dön — kullanıcı "DB'de yok mu, validasyon mu hatalı?" diye anlamayabiliyor
    if not products:
        from datetime import datetime, timezone
        msg = (
            f"DB'de bu kod(lar)la ürün bulunamadı: {', '.join(not_found_codes[:10])}"
            + (f" (+{len(not_found_codes)-10} daha)" if len(not_found_codes) > 10 else "")
        ) if not_found_codes else "Sorgu kriterlerine uyan ürün bulunamadı."
        await db.trendyol_sync_logs.insert_one({
            "id": generate_id(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "products_attempted": 0,
            "products_sent": 0,
            "batch_request_id": None,
            "errors": [msg],
            "not_found_codes": not_found_codes,
            "message": msg,
        })
        return {
            "success": False,
            "message": msg,
            "total": 0,
            "successful": 0,
            "failed": 0,
            "not_found_codes": not_found_codes,
            "errors": [msg],
        }
    
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )
    
    items_to_send = []
    errors = []

    # Trendyol kategori özellik cache'lerini bu sync için yükle (attribute meta + geçerli value_id'ler)
    _attr_meta_cache: dict = {}

    async def _get_attr_meta(mp_cat_id):
        if mp_cat_id in _attr_meta_cache:
            return _attr_meta_cache[mp_cat_id]
        meta: dict = {}
        try:
            cache = await db.trendyol_category_attributes.find_one(
                {"category_id": int(mp_cat_id) if str(mp_cat_id).isdigit() else str(mp_cat_id)},
                {"_id": 0},
            )
        except Exception:
            cache = None
        # T1 EMNİYET: kanonik cache boşsa (ör. kategori yalnız ürün editöründen yenilenmiş
        # ya da kategori-eşleme tazelemesi hata vermişse), editörün yazdığı
        # `trendyol_attributes` koleksiyonuna düş. Böylece push hiçbir zaman boş/eksik
        # şemayla kalmaz; aynı ham `categoryAttributes` listesi okunur (şekil birebir uyumlu).
        if not (cache or {}).get("attributes"):
            try:
                _legacy = await db.trendyol_attributes.find_one(
                    {"category_id": int(mp_cat_id) if str(mp_cat_id).isdigit() else str(mp_cat_id)},
                    {"_id": 0},
                )
                if _legacy and _legacy.get("attributes"):
                    cache = _legacy
            except Exception:
                pass
        for a in (cache or {}).get("attributes", []) or []:
            aid = a.get("id") or a.get("attribute", {}).get("id")
            if aid is None:
                continue
            valid_value_ids = {str(v.get("id")) for v in (a.get("attributeValues") or []) if v.get("id") is not None}
            # Normalize edilmiş "değer adı → value_id" haritası (isimle otomatik eşleştirme için)
            value_name_to_id = {}
            for v in (a.get("attributeValues") or []):
                if v.get("id") is None or not v.get("name"):
                    continue
                value_name_to_id[_norm_val(v["name"])] = str(v["id"])
            meta[int(aid)] = {
                "allow_custom": bool(a.get("allowCustom") or a.get("attribute", {}).get("allowCustom")),
                "required": bool(a.get("required")),
                "valid_value_ids": valid_value_ids,
                "value_name_to_id": value_name_to_id,
                "name": a.get("name") or a.get("attribute", {}).get("name") or "",
            }
        _attr_meta_cache[mp_cat_id] = meta
        return meta

    def _collect_local_values(product, variant):
        """Ürünün ve varyantın attribute değerlerini lokalName(lower) → value şeklinde toplar.
        attributes hem LIST hem DICT formatını destekler."""
        out = {}
        def _put(nm, vv):
            if not nm or vv in (None, ""):
                return
            out.setdefault(str(nm).lower().strip(), str(vv))

        def _walk(attrs):
            if isinstance(attrs, dict):
                # Çakışan mükerrer özelliklerde TEMİZ (güncel, elle/teknik) anahtarlar
                # önce işlenir; `ticimax_*` (eski/ham 113-kolon) anahtarları yalnızca
                # fallback olur. _put setdefault olduğundan ilk gelen kazanır.
                items = sorted(
                    attrs.items(),
                    key=lambda kv: 1 if str(kv[0]).lower().startswith("ticimax_") else 0,
                )
                for k, v in items:
                    if isinstance(v, dict):
                        nm = v.get("label") or v.get("name") or k
                        vv = v.get("value") or v.get("attribute_value")
                        _put(nm, vv)
                    elif v is not None:
                        _put(k, v)
            elif isinstance(attrs, list):
                for a in attrs:
                    if isinstance(a, dict):
                        nm = a.get("label") or a.get("name") or a.get("type") or a.get("attribute_name")
                        vv = a.get("value") or a.get("attribute_value")
                        _put(nm, vv)
        _walk(product.get("attributes"))
        _walk((product.get("trendyol_attributes_labels") or {}))  # opsiyonel
        if variant:
            _walk(variant.get("attributes"))
            if variant.get("color"):
                _put("Renk", variant["color"])
                _put("Web Color", variant["color"])
            if variant.get("size"):
                _put("Beden", variant["size"])
        _bridge_trendyol_attr_synonyms(out)
        return out

    def resolve_attributes(base_attrs, product, variant, category, meta):
        """meta = _get_attr_meta sonucu: {attr_id: {allow_custom, required, valid_value_ids, name}}
        Geçersiz value_id ya da allow_custom=False olan custom değerleri SESSİZCE atlar."""
        item_attrs = list(base_attrs)
        processed = {int(a["attributeId"]) for a in item_attrs if "attributeId" in a}
        
        attr_mappings = category.get("attribute_mappings", []) or []
        val_mappings = category.get("value_mappings", {}) or {}
        default_mappings = category.get("default_mappings", {}) or {}

        local_vals = _collect_local_values(product, variant)

        def _push(ty_id: int, value_id=None, custom=None):
            """Cache'e karşı doğrulayarak append."""
            # Trendyol'un BU kategorisinde OLMAYAN attribute'ı GÖNDERME. Sistemde birden çok
            # pazaryeri eşlemesi olduğundan HB (Hepsiburada) attribute id'leri veya eski/yanlış
            # Trendyol id'leri sızabiliyor; bunlar Trendyol'da geçersizdir ve TÜM ürünü reddettirir.
            # meta (Trendyol'dan çekilen gerçek kategori attribute'ları) doluyken yalnız geçerli
            # id'ler geçer; meta boşsa (çekilemedi) over-filter yapma (eski davranış).
            if meta and ty_id not in meta:
                logger.info(f"[TY ATTR] kategori disi attribute atlandi id={ty_id} (Trendyol meta'da yok)")
                return False
            am = meta.get(ty_id) or {}
            # Dosya linki / sertifika gerektiren attribute'ları skip (custom text kabul etmez)
            am_name = (am.get("name") or "").lower()
            if any(p in am_name for p in ["analiz testi", "test raporu", "sertifika dosya", "dosya linki"]):
                return False
            if value_id is not None:
                vid = str(value_id)
                # Cache'de yoksa custom'a düşür
                if am.get("valid_value_ids") and vid not in am["valid_value_ids"]:
                    if am.get("allow_custom") and custom:
                        item_attrs.append({"attributeId": ty_id, "customAttributeValue": str(custom)})
                        processed.add(ty_id)
                        return True
                    return False
                item_attrs.append({"attributeId": ty_id, "attributeValueId": int(vid)})
                processed.add(ty_id)
                return True
            if custom is not None:
                if not am.get("allow_custom"):
                    return False
                item_attrs.append({"attributeId": ty_id, "customAttributeValue": str(custom)})
                processed.add(ty_id)
                return True
            return False
        
        for mapping in attr_mappings:
            # Yeni format: mp_attr_id, eski format: trendyol_attr_id
            ty_id = mapping.get("mp_attr_id") or mapping.get("trendyol_attr_id")
            if not ty_id:
                continue
            try:
                ty_id = int(ty_id)
            except (ValueError, TypeError):
                continue
            if ty_id in processed:
                continue
                
            local_attr_name = str(mapping.get("local_attr") or "").strip()
            local_key = local_attr_name.lower()
            local_val = None

            if variant:
                if local_key in ["renk", "color", "web color"]:
                    local_val = variant.get("color")
                elif local_key in ["beden", "size"]:
                    local_val = variant.get("size") or local_vals.get(local_key)
                    
            if not local_val:
                local_val = local_vals.get(local_key)
            if not local_val and local_attr_name:
                local_val = local_vals.get(local_attr_name.lower())
                        
            if local_val:
                str_ty_id = str(ty_id)
                # value_mapping format: "343|Erkek" → "value_id"
                mapped_val = val_mappings.get(f"{str_ty_id}|{local_val}")
                # eski yapı: {"343": {"Erkek": "value_id"}}
                if not mapped_val and str_ty_id in val_mappings and isinstance(val_mappings[str_ty_id], dict):
                    mapped_val = val_mappings[str_ty_id].get(str(local_val))
                if mapped_val:
                    if str(mapped_val).isdigit():
                        if _push(ty_id, value_id=mapped_val, custom=local_val):
                            continue
                    else:
                        if _push(ty_id, custom=mapped_val):
                            continue
                # Kaydedilmiş eşleştirme yok → listeli (enum) değeri ADIYLA otomatik eşle.
                # Örn. local "Dokuma"/"Örme"/"Kısa" → Trendyol value_id (Türkçe/boşluk duyarsız).
                am_meta = meta.get(ty_id) or {}
                name_map = am_meta.get("value_name_to_id") or {}
                if name_map:
                    auto_vid = _resolve_value_id(name_map, local_val)
                    if auto_vid and _push(ty_id, value_id=auto_vid, custom=local_val):
                        continue
                # Mapping yok ama allow_custom varsa local_val'i custom olarak yolla
                if _push(ty_id, custom=local_val):
                    continue
                
            # Default mapping
            str_ty_id = str(ty_id)
            if str_ty_id in default_mappings and default_mappings[str_ty_id]:
                def_val = default_mappings[str_ty_id]
                if str(def_val).isdigit():
                    _push(ty_id, value_id=def_val, custom=local_val)
                else:
                    _push(ty_id, custom=def_val)

        # Default mapping'de olup attribute_mappings'de olmayanları da ekle
        for ty_str, def_val in default_mappings.items():
            if not def_val:
                continue
            try:
                ty_id = int(ty_str)
            except (ValueError, TypeError):
                continue
            if ty_id not in processed:
                if str(def_val).isdigit():
                    _push(ty_id, value_id=def_val)
                else:
                    _push(ty_id, custom=def_val)

        # 🎯 GARANTİ: Trendyol "Materyal Bileşeni" (serbest metin / allowCustom) alanı,
        # mapping yapılmamış olsa bile HER kategoride ürünün "Ürün İçerik Bilgisi"
        # değerinden otomatik gönderilir. local_vals["materyal bileşeni"] köprü ile dolar.
        icerik_val = local_vals.get("materyal bileşeni")
        if icerik_val:
            for m_ty_id, m_meta in meta.items():
                if m_ty_id in processed:
                    continue
                mname = (m_meta.get("name") or "").lower()
                if "materyal bileşeni" in mname and m_meta.get("allow_custom"):
                    _push(m_ty_id, custom=icerik_val)

        # 🎯 GENEL GARANTİ: Trendyol özellik adı ile lokal özellik adı eşleşen ama
        # attribute_mappings'te tanımlanmamış alanları (Boy, Desen, Kumaş Tipi vb.)
        # otomatik gönder. Listeli alanlar değeri ADIYLA value_id'ye eşlenir,
        # serbest alanlar custom olarak yollanır.
        local_norm_index = {}
        for lk, lv in local_vals.items():
            local_norm_index.setdefault(_normalize_attr_key(lk), lv)
        for m_ty_id, m_meta in meta.items():
            if m_ty_id in processed:
                continue
            lval = local_norm_index.get(_normalize_attr_key(m_meta.get("name") or ""))
            if not lval:
                continue
            name_map = m_meta.get("value_name_to_id") or {}
            auto_vid = _resolve_value_id(name_map, lval)
            if auto_vid:
                _push(m_ty_id, value_id=auto_vid, custom=lval)
            else:
                _push(m_ty_id, custom=lval)

        # 🎯 FACETTE SABİT VARSAYILANLAR (gap-fill, EN SON):
        # Menşei=Türkiye · Cinsiyet=Kadın · Yaş Grubu=Yetişkin · Ortam/Koleksiyon=Casual/Günlük ·
        # Ek Özellik=Mevcut Değil · Kutu Durumu=Kutu Yok · Persona=Fashion Forward ·
        # Performans=Cool & Comfort · Üretici/İthalatçı (GPSR)=FACETTE bilgileri.
        # Yalnız ürünün DOLDURMADIĞI (processed olmayan) Trendyol özelliğine yazılır; listeli
        # alanlar ADIYLA value_id'ye çözülür (Türkiye→TR), serbest alanlar custom gider.
        for m_ty_id, m_meta in meta.items():
            if m_ty_id in processed:
                continue
            fv = facette_fixed_value_for(m_meta.get("name") or "")
            if not fv:
                continue
            name_map = m_meta.get("value_name_to_id") or {}
            auto_vid = _resolve_value_id(name_map, fv) if name_map else None
            if auto_vid:
                _push(m_ty_id, value_id=auto_vid, custom=fv)
            else:
                _push(m_ty_id, custom=fv)

        return item_attrs
    
    for product in products:
        try:
            # 1. Category Mapping check — önce category_id, sonra category_name üzerinden category_mappings
            trendyol_cat_id = None
            category = None
            cat_id = product.get("category_id")
            cat_name = (product.get("category_name") or "").strip()
            cm = None

            # 1a. Doğrudan category_id ile mapping arayalım
            if cat_id:
                cm = await db.category_mappings.find_one(
                    {"category_id": str(cat_id), "marketplace": "trendyol"}, {"_id": 0}
                )

            # 1b. Bulunamadıysa: kategori adından sistem kategorisini, oradan mapping'i bul
            if not cm and cat_name:
                sys_cat = await db.categories.find_one({"name": cat_name}, {"_id": 0, "id": 1, "trendyol_category_id": 1})
                if sys_cat and sys_cat.get("id"):
                    cm = await db.category_mappings.find_one(
                        {"category_id": str(sys_cat["id"]), "marketplace": "trendyol"}, {"_id": 0}
                    )

            if cm and cm.get("marketplace_category_id"):
                trendyol_cat_id = cm["marketplace_category_id"]
                category = cm  # category_mappings doc — attribute_mappings, default_mappings içerir

            # 1c. Eski legacy fallback: db.categories.trendyol_category_id
            if not trendyol_cat_id and cat_name:
                cat_doc = await db.categories.find_one({"name": cat_name})
                if cat_doc and cat_doc.get("trendyol_category_id"):
                    trendyol_cat_id = cat_doc["trendyol_category_id"]
                    category = cat_doc

            # 1c.5 ÜRÜNÜN KENDİ kategori listesi (İKİNCİL/ÜÇÜNCÜL) — kullanıcı isteği:
            # birincil kategori "EN YENİLER" gibi bir koleksiyon olsa da, ürünün
            # categories[]/category_ids[] içinde Trendyol'a EŞLEŞEN bir kategori varsa ONUNLA
            # gönder. Sırayla dener, ilk eşleşen mapping'i kullanır.
            if not trendyol_cat_id:
                _own = []
                for _f in ("category_ids", "categories"):
                    _v = product.get(_f)
                    if isinstance(_v, list):
                        _own += [str(x) for x in _v if x]
                _seen_own = set()
                for _cid in _own:
                    if _cid in _seen_own or str(_cid) == str(cat_id):
                        continue
                    _seen_own.add(_cid)
                    _ocm = await db.category_mappings.find_one(
                        {"category_id": str(_cid), "marketplace": "trendyol"}, {"_id": 0})
                    if _ocm and _ocm.get("marketplace_category_id"):
                        trendyol_cat_id = _ocm["marketplace_category_id"]
                        category = _ocm
                        logger.info(f"[trendyol] {product.get('name')} birincil kategoride mapping yok "
                                    f"→ ürünün ikincil kategorisi kullanıldı (cat_id={_cid})")
                        break

            # 1d. KARDEŞ-FALLBACK (denetim Finding 3): birincil kategori bir KOLEKSİYON
            # (ör. "EN YENİLER") olup mapping yoksa, AYNI ürünün renk-kardeşinin (aynı
            # urun_karti_id / stock_code) EŞLEŞTİRİLMİŞ kategorisini kullan. Böylece Siyah
            # "Pantolon"a eşliyken Ekru/Acı Kahve "EN YENİLER"e düşse bile Trendyol'a doğru
            # (Pantolon) eşleştirmeyle gider — MAĞAZA kategorisi DEĞİŞMEZ (storefront'a dokunmaz).
            if not trendyol_cat_id:
                _sc = product.get("stock_code") or product.get("sku")
                _anchor = product.get("urun_karti_id") or product.get("csv_card_id")
                # Renk varyantları AYNI stock_code'u paylaşır ama urun_karti_id RENK BAZLI
                # olabilir → her iki anahtarı da $or ile dene (stock_code en güvenilir bağ).
                _or = []
                if _anchor:
                    _or += [{"urun_karti_id": _anchor}, {"csv_card_id": _anchor}]
                if _sc:
                    _or += [{"stock_code": _sc}, {"variants.stock_code": _sc}]
                _sib_q = {"$or": _or} if _or else None
                if _sib_q:
                    async for _sib in db.products.find(
                        _sib_q, {"_id": 0, "id": 1, "category_id": 1}):
                        if str(_sib.get("id")) == str(product.get("id")):
                            continue
                        _scat = _sib.get("category_id")
                        if not _scat:
                            continue
                        _scm = await db.category_mappings.find_one(
                            {"category_id": str(_scat), "marketplace": "trendyol"}, {"_id": 0})
                        if _scm and _scm.get("marketplace_category_id"):
                            trendyol_cat_id = _scm["marketplace_category_id"]
                            category = _scm
                            logger.info(f"[trendyol] {product.get('name')} birincil kategoride mapping "
                                        f"yok → renk-kardeşinin kategorisi kullanıldı (cat_id={_scat})")
                            break

            if not trendyol_cat_id:
                errors.append(f"{product.get('name')} - Trendyol kategori eşleştirmesi (Mapping) yok.")
                continue
                
            # 2. Attributes check and formatting
            raw_attrs = product.get("trendyol_attributes", {})
            attributes = []
            for attr_id, val_id in raw_attrs.items():
                if val_id:
                    # Trendyol expects attributeId and attributeValueId, or customAttributeValue
                    if str(val_id).isdigit():
                        attributes.append({
                            "attributeId": int(attr_id),
                            "attributeValueId": int(val_id)
                        })
                    else:
                        attributes.append({
                            "attributeId": int(attr_id),
                            "customAttributeValue": str(val_id)
                        })
            
            # Description (HTML → düz metin: br/li/p satır sonuna, entity decode, min 30 karakter)
            import re as _re
            import html as _html
            raw_desc = (product.get("description") or product.get("short_description")
                        or product.get("long_description") or "").strip()
            # 1) Blok/satır kıran tagları newline'a çevir (görsel paragraf yapısı korunur).
            _desc = raw_desc
            _desc = _re.sub(r"<\s*br\s*/?\s*>", "\n", _desc, flags=_re.IGNORECASE)
            _desc = _re.sub(r"</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", _desc, flags=_re.IGNORECASE)
            _desc = _re.sub(r"<\s*li\b[^>]*>", "• ", _desc, flags=_re.IGNORECASE)
            # 2) Tüm kalan HTML tag'lerini at.
            _desc = _re.sub(r"<[^>]+>", " ", _desc)
            # 3) HTML entity'leri decode et (&nbsp;, &amp;, &uuml; vb.).
            _desc = _html.unescape(_desc)
            # 4) Yatay boşlukları sıkıştır ama satır sonlarını koru.
            _desc = _re.sub(r"[ \t]+", " ", _desc)
            _desc = _re.sub(r"\n[ \t]+", "\n", _desc)
            _desc = _re.sub(r"[ \t]+\n", "\n", _desc)
            _desc = _re.sub(r"\n{3,}", "\n\n", _desc)
            clean_desc = _desc.strip()
            if not clean_desc or len(clean_desc) < 30:
                # Description yoksa veya çok kısaysa ürün adından bir minimum açıklama üret
                fallback = (product.get("name") or "").strip()
                if fallback and len(fallback) >= 10:
                    clean_desc = f"{fallback}. Kaliteli kumaş, modern kesim, şık tasarım. Günlük ve özel kullanım için ideal."
                else:
                    errors.append(f"{product.get('name')} - Açıklama eksik (Trendyol min 30 karakter zorunlu).")
                    continue

            # Görseller: normal görseller önce, ÖLÇÜ GÖRSELİ ({is_size_table:true}) SON sırada
            # (kullanıcı kararı — eski 'pazaryerine gönderme' kuralı kaldırıldı). data: URL atlanır.
            _ty_norm, _ty_size = [], []
            for _im in (product.get("images", []) or []):
                _u = _im.get("url") if isinstance(_im, dict) else _im
                if not _u or str(_u).startswith("data:"):
                    continue
                if isinstance(_im, dict) and _im.get("is_size_table"):
                    _ty_size.append(_u)
                else:
                    _ty_norm.append(_u)
            _ty_imgs = (_ty_norm[:7] + _ty_size[:1]) if _ty_size else _ty_norm[:8]

            # 3. Base Product Details
            base_item = {
                "title": product.get("name"),
                "productMainId": _resolve_stock_code(product) or product.get("id"),
                "brandId": int(product.get("trendyol_brand_id") or config.get("default_brand_id") or 975755),
                "categoryId": int(trendyol_cat_id),
                "description": clean_desc,
                "currencyType": product.get("currency", "TRY"),
                "listPrice": calculate_trendyol_price(_mp_base_price(product), product, config),
                "salePrice": calculate_trendyol_price(_mp_base_price(product), product, config),
                "vatRate": int(product.get("vat_rate", config.get("default_vat_rate") or 20)),
                "cargoCompanyId": int(config.get("default_cargo_company_id") or 10), # Assuming 10 is MNG Kargo (Needs specific Cargo Provider ID)
                "dimensionalWeight": float(product.get("cargo_weight", 1)),
                "images": [{"url": u} for u in _ty_imgs]
            }
            
            if not base_item["images"]:
                errors.append(f"{product.get('name')} - En az 1 görsel gerekli.")
                continue

            # 3b. Trendyol attribute meta (cache) — value_id ve allowCustom validasyonu için
            meta = await _get_attr_meta(trendyol_cat_id)
            
            # 4. Handle Variants or No-Variants
            variants = product.get("variants", [])
            if not variants:
                if not product.get("barcode") or product.get("barcode_uncertain"):
                    errors.append(f"{product.get('name')} - Barkod yok ya da belirsiz (Ticimax'tan doğrulayın).")
                    continue
                item = base_item.copy()
                item["barcode"] = product.get("barcode")
                item["stockCode"] = product.get("stock_code") or product.get("sku") or product.get("barcode")
                item["quantity"] = int(product.get("stock", 0))
                item["attributes"] = resolve_attributes(attributes, product, None, category, meta)
                items_to_send.append(item)
            else:
                # 🎯 Eğer kullanıcı barcodes parametresi ile spesifik barkodlar istediyse,
                # sadece o barkodları batch'e ekle. Aksi halde parent'ın tüm varyantları
                # gönderilir → Trendyol'da kardeş varyantlar zaten varsa duplicate hatası
                # oluşur ve yeni eklenmek istenen barkod da reject olur.
                # AMA: Kullanıcı stok kodu girip parent ürünü hedeflediyse, tüm varyantlar
                # gönderilmelidir (stockCode == "FCSSxxx" hiçbir varyant barkoduyla
                # eşleşmediği için aksi halde sıfır varyant gönderilir).
                product_match_by_stock = False
                if stock_codes:
                    sc_set = set(str(s) for s in stock_codes)
                    if (
                        str(product.get("stock_code") or "") in sc_set
                        or str(product.get("sku") or "") in sc_set
                        or any(str(v.get("stock_code") or "") in sc_set for v in variants)
                    ):
                        product_match_by_stock = True
                if product_match_by_stock:
                    requested_set = None  # tüm varyantları gönder
                else:
                    requested_set = set(barcodes) if barcodes else None
                for v in variants:
                    if not v.get("barcode") or v.get("barcode_uncertain"):
                        errors.append(f"{product.get('name')} - Varyant ({v.get('size') or v.get('color') or '?'}) barkodu yok / belirsiz.")
                        continue
                    if requested_set and str(v.get("barcode")) not in requested_set:
                        continue  # kullanıcı bu barkodu istemedi, atla
                    item = base_item.copy()
                    item["barcode"] = v.get("barcode")
                    # stockCode = parent stok kodu (FCSS.../FCFW...). Trendyol unique
                    # check'i barcode üzerinden yapar, stockCode aynı olabilir.
                    parent_stock = (product.get("stock_code") or product.get("sku") or "").strip()
                    v_stock = (v.get("stock_code") or v.get("sku") or "").strip()
                    # Varyantın kendi unique stock_code'u parent'tan farklıysa onu kullan,
                    # değilse parent stock_code'u gönder.
                    if v_stock and v_stock != parent_stock:
                        item["stockCode"] = v_stock
                    elif parent_stock:
                        item["stockCode"] = parent_stock
                    else:
                        item["stockCode"] = v.get("barcode")
                    item["quantity"] = int(v.get("stock", 0))
                    item["attributes"] = resolve_attributes(attributes, product, v, category, meta)
                    items_to_send.append(item)
                    
        except Exception as e:
            errors.append(f"{product.get('name')} - Hazırlama Hatası: {str(e)}")
            
    if not items_to_send:
        # Save failure log — products bulundu ama validasyondan geçemedi
        from datetime import datetime, timezone
        first_reason = errors[0] if errors else "Bilinmeyen validasyon hatası."
        msg = (
            f"{len(products)} ürün DB'de bulundu ama Trendyol'a gönderilemedi. "
            f"İlk hata: {first_reason}"
        )
        log_doc = {
            "id": generate_id(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "products_attempted": len(products),
            "products_sent": 0,
            "batch_request_id": None,
            "errors": errors,
            "not_found_codes": not_found_codes,
            "message": msg,
        }
        await db.trendyol_sync_logs.insert_one(log_doc)
        return {
            "success": False,
            "message": msg,
            "total": len(products),
            "successful": 0,
            "failed": len(errors),
            "not_found_codes": not_found_codes,
            "errors": errors
        }
        
    try:
        from datetime import datetime, timezone
        import asyncio as _asyncio
        started_at = datetime.now(timezone.utc).isoformat()
        response = await client.create_products(items_to_send)
        batch_id = response.get("batchRequestId")
        # Trendyol returned an error response (no batchRequestId)?
        trendyol_error = None
        if not batch_id:
            # response may contain {"errors":[...]} or {"message":...}
            trendyol_error = (
                response.get("message")
                or (response.get("errors") if isinstance(response.get("errors"), list) else None)
                or str(response)[:500]
            )
            if isinstance(trendyol_error, list) and trendyol_error:
                trendyol_error = "; ".join([
                    (e.get("message") if isinstance(e, dict) else str(e)) for e in trendyol_error
                ])[:1000]

            # 🛟 FALLBACK: "Tekrarlı ürün oluşturma isteği atılamaz" (Trendyol anti-spam)
            # geldiyse, ürün zaten Trendyol'da yaşıyor demektir → create yerine
            # price-and-inventory POST (stok/fiyat) ile güncellemeyi dene. Bu endpoint
            # farklı bir kapı (inventory/...) ve recurring throttling'e takılmaz.
            # Hala başarısızsa update_products (PUT) ile son bir deneme yap.
            if trendyol_error and (
                "tekrarl" in trendyol_error.lower()
                or "duplicate request" in trendyol_error.lower()
                or "recurring" in trendyol_error.lower()
            ):
                # ⚠️ Ürün Trendyol'da zaten yaşıyor. İKİ İŞ birden yapılmalı:
                #   1) price-and-inventory  → stok/fiyat senkronu (ayrı, throttle'a takılmaz kapı)
                #   2) update_products PUT  → kategori/attribute/görsel senkronu
                # ÖNCEKİ HATA: price-and-inventory batch dönünce burada DURULUYORDU; PUT hiç
                # çalışmadığı için "tekrar aktardım ama özellikleri güncellenmedi" oluyordu.
                # Artık PUT her zaman çalışır ve raporlanan batch = PUT batch'i olur (attribute
                # sonucunu kullanıcı görsün). pi yalnızca stok/fiyat için kısa süre poll edilir.
                logger.info("Trendyol create reddetti (tekrarlı). price-and-inventory + update_products fallback.")
                try:
                    pi_items = []
                    for it in items_to_send:
                        if not it.get("barcode"):
                            continue
                        entry = {
                            "barcode": str(it.get("barcode")),
                            "quantity": int(it.get("quantity", 0)),
                        }
                        if it.get("salePrice") is not None:
                            try:
                                entry["salePrice"] = float(it["salePrice"])
                            except Exception:
                                pass
                        if it.get("listPrice") is not None:
                            try:
                                entry["listPrice"] = float(it["listPrice"])
                            except Exception:
                                pass
                        pi_items.append(entry)
                    # 1) Stok/fiyat senkronu (best-effort)
                    pi_batch = None
                    try:
                        pi_response = await client.update_price_and_inventory(pi_items) if pi_items else {}
                        pi_batch = (pi_response or {}).get("batchRequestId")
                        if pi_batch:
                            logger.info(f"Tekrarlı fallback price-and-inventory batch={pi_batch}")
                    except Exception as pi_err:
                        logger.warning(f"Tekrarlı fallback price-and-inventory exception: {pi_err}")
                    # 2) Kategori/attribute/görsel senkronu — HER ZAMAN çalışır
                    upd_response = await client.update_products(items_to_send)
                    upd_batch = (upd_response or {}).get("batchRequestId")
                    if upd_batch:
                        batch_id = upd_batch
                        response = upd_response
                        trendyol_error = None
                        logger.info(f"Tekrarlı fallback update_products (attribute) başarılı: batch={upd_batch}")
                    elif pi_batch:
                        # PUT batch dönmediyse en azından stok/fiyat batch'ini takip et
                        batch_id = pi_batch
                        response = pi_response
                        trendyol_error = None
                        logger.info(f"Tekrarlı fallback: PUT batch yok, price-and-inventory batch={pi_batch} izleniyor.")
                    else:
                        upd_err = (upd_response or {}).get("message") or str(upd_response)[:500]
                        logger.warning(f"Tekrarlı fallback update_products reddetti: {upd_err}")
                except Exception as upd_err:
                    logger.error(f"Update fallback exception: {upd_err}")

        # 🔍 Gerçek batch sonucunu sorgula: Trendyol asenkron işliyor; 5-15sn'de tamamlanır.
        # "aktarıldı diyor ama Trendyol'da yok" tuzağını önler.
        # Trendyol BUG: bazen status=COMPLETED dönüp items[]'in işlenmiş olduğunu söyler,
        # ama failedItemCount=0 olur halbuki birkaç sn sonra items[].status FAILED olur.
        # Bu yüzden polling birkaç tur daha sürdürüyoruz: items[].status sayısı itemCount'a ulaşmalı
        # ve "tüm item'lar terminal status'ta" (SUCCESS|FAILED|COMPLETED) olmalı.
        batch_failed_items = []
        batch_success_count = 0
        batch_final_status = "INPROGRESS"

        def _norm_status(s: str) -> str:
            """Trendyol bazen 'IN_PROGRESS' bazen 'INPROGRESS' döndürüyor — normalize."""
            return (s or "").upper().replace("_", "").replace(" ", "").replace("-", "")

        if batch_id:
            for attempt in range(16):  # 16 deneme x 2.5sn = max ~40sn (ingress 60sn limitine güvenli)
                await _asyncio.sleep(2.5)
                try:
                    br = await client.get_batch_request_result(batch_id)
                    batch_final_status = (br or {}).get("status") or "INPROGRESS"
                    items = (br or {}).get("items", []) or []
                    item_count = (br or {}).get("itemCount", 0) or len(items)
                    # Tüm item'ların terminal status'ta olmasını bekle
                    terminal_set = {"SUCCESS", "FAILED", "COMPLETED"}
                    statuses_present = [(it.get("status") or "").upper() for it in items]
                    all_terminal = (
                        len(items) >= item_count
                        and all(s in terminal_set for s in statuses_present)
                    )
                    if _norm_status(batch_final_status) in ("COMPLETED", "FAILED") and all_terminal:
                        batch_failed_items = []
                        for it in items:
                            if (it.get("status") or "").upper() == "FAILED":
                                req = it.get("requestItem", {}) or {}
                                prod = req.get("product", {}) if isinstance(req, dict) else {}
                                batch_failed_items.append({
                                    "stock_code": prod.get("stockCode") or req.get("barcode") or "",
                                    "barcode": prod.get("barcode") or req.get("barcode") or "",
                                    "title": prod.get("title") or "",
                                    "reasons": it.get("failureReasons") or [],
                                })
                        # ✔ Gerçek başarı = items listesinden say (failedItemCount değil!)
                        batch_success_count = sum(1 for s in statuses_present if s == "SUCCESS")
                        break
                except Exception as poll_err:
                    logger.warning(f"Batch poll {attempt+1}/12 failed: {poll_err}")

        # 🛟 Polling timeout durumunda da elde olan son durumu işle — fallback'ler
        # böylece her senaryoda tetiklenir (kritik: aksi halde duplicate'lar sessizce kalır).
        if batch_id and not batch_failed_items and batch_success_count == 0:
            try:
                br = await client.get_batch_request_result(batch_id)
                items = (br or {}).get("items", []) or []
                if items:
                    batch_final_status = (br or {}).get("status") or batch_final_status
                    for it in items:
                        if (it.get("status") or "").upper() == "FAILED":
                            req = it.get("requestItem", {}) or {}
                            prod = req.get("product", {}) if isinstance(req, dict) else {}
                            batch_failed_items.append({
                                "stock_code": prod.get("stockCode") or req.get("barcode") or "",
                                "barcode": prod.get("barcode") or req.get("barcode") or "",
                                "title": prod.get("title") or "",
                                "reasons": it.get("failureReasons") or [],
                            })
                    batch_success_count = sum(
                        1 for it in items if (it.get("status") or "").upper() == "SUCCESS"
                    )
            except Exception as final_poll_err:
                logger.warning(f"Final batch poll failed: {final_poll_err}")

        # 🔁 SMART CONFLICT RESOLUTION:
        # - "Self duplicate" (sent_bc == conflict_bc) → PUT (update_products)
        # - "Cross duplicate" (sent_bc != conflict_bc) → eski barkodu ARCHIVE + yeni barkodu CREATE
        upsert_attempted = 0
        upsert_succeeded = 0
        upsert_failed_items = []
        upsert_batch_id = None
        upsert_final_status = None
        archived_barcodes: list = []
        archive_batch_id = None
        retry_create_batch_id = None
        retry_create_succeeded = 0

        def _is_duplicate_error(reasons):
            """Trendyol'un duplicate (çakışma) hatalarını tespit eder."""
            if not reasons:
                return False
            text = " ".join([
                (r.get("message") if isinstance(r, dict) else str(r)) for r in reasons
            ]).lower()
            patterns = [
                "aynı barkodlu",
                "ayni barkodlu",
                "aynı barkod",
                "barkod zaten",
                "stockcode zaten",
                "stok kodu zaten",
                "duplicate barcode",
                "duplicate stockcode",
                "already exist",
                "zaten mevcut",
                "zaten kayıtlı",
                "zaten kayitli",
                "productmainid",
                "bulunduğundan",
                "bulundugundan",
            ]
            return any(p in text for p in patterns)

        def _parse_conflict_barcode(reasons):
            """Trendyol hata mesajından çakışan barkodu çıkar."""
            import re
            text = " ".join([
                (r.get("message") if isinstance(r, dict) else str(r)) for r in (reasons or [])
            ])
            m = re.search(r"Barkod[:\s]+(\d{6,})", text)
            return m.group(1) if m else None

        def _is_not_found_error(reasons):
            """Trendyol'un 'ürün bulunamadı' (price-and-inventory'de Trendyol'da olmayan
            barkod) hatasını tespit eder."""
            if not reasons:
                return False
            text = " ".join([
                (r.get("message") if isinstance(r, dict) else str(r)) for r in reasons
            ]).lower()
            patterns = [
                "ürün bulunamadı",
                "urun bulunamadi",
                "product not found",
                "tedarikçi id si",
                "tedarikci id si",
            ]
            return any(p in text for p in patterns)

        # Hangi item'lar duplicate yüzünden patladı?
        duplicate_failed = [f for f in batch_failed_items if _is_duplicate_error(f.get("reasons"))]
        if batch_id and duplicate_failed:
            # Cross conflict: eski barkod ARCHIVE + yeni barkod CREATE
            # Self conflict: PUT update_products
            cross_conflicts = []   # (sent_barcode, conflict_old_barcode, item_payload)
            self_conflicts = []    # item_payload

            # items_to_send → barcode lookup
            items_by_barcode = {str(it.get("barcode") or ""): it for it in items_to_send}

            for f in duplicate_failed:
                sent_bc = str(f.get("barcode") or "")
                conflict_bc = _parse_conflict_barcode(f.get("reasons")) or ""
                payload = items_by_barcode.get(sent_bc)
                if not payload:
                    continue
                if conflict_bc and conflict_bc != sent_bc:
                    cross_conflicts.append((sent_bc, conflict_bc, payload))
                else:
                    self_conflicts.append(payload)

            # 1) CROSS CONFLICTS: önce ARCHIVE + RETRY CREATE.
            # (Önceki versiyonlarda PUT update_products deneniyordu ama Trendyol
            # stockCode altında çoklu kayıt olduğunda hayalet SUCCESS dönüyor → kaldırıldı.)
            if cross_conflicts:
                put_cross_succeeded_barcodes = set()  # şimdilik boş (PUT yok)

                # Hâlâ başarısız olan cross item'lar için archive + retry create
                remaining_cross = [
                    c for c in cross_conflicts
                    if str(c[2].get("barcode") or "") not in put_cross_succeeded_barcodes
                ]
                if remaining_cross:
                    # 🧹 AGRESİF TEMİZLİK: cross-conflict varsa, conflict'in tek barkodunu
                    # arşivlemek yetmez. Trendyol cache'inde aynı stockCode altında onlarca
                    # eski varyant olabiliyor. DB'de OLMAYAN tüm Trendyol barkodlarını
                    # bul ve arşivle. Sonra retry create yapılabilir.
                    old_barcodes_to_archive: set = set()
                    db_barcodes_per_sc: dict = {}
                    # 1) cross_conflicts'tan stockCode'ları topla, her stockCode için DB barkodlarını çek
                    stock_codes_in_play: set = set()
                    for c in remaining_cross:
                        old_barcodes_to_archive.add(c[1])
                        sc = (c[2] or {}).get("productMainId") or (c[2] or {}).get("stockCode")
                        if sc:
                            stock_codes_in_play.add(str(sc))
                    for sc in stock_codes_in_play:
                        # DB'deki barkodlar (bu stockCode için)
                        if sc not in db_barcodes_per_sc:
                            # H1 FIX (VERİ KAYBI): aynı stock_code BİRDEN FAZLA ürün dokümanında
                            # paylaşılır (renk varyantları `stock|name` olarak AYRI doc). Eski
                            # find_one YALNIZCA bir rengin barkodlarını getiriyordu → diğer renklerin
                            # CANLI barkodları "DB'de yok" sanılıp ARŞİVLENİYOR, yani canlı/stokta
                            # ürünler Trendyol'dan siliniyordu ("kaybolan ürünler"). Artık TÜM
                            # eşleşen dokümanları gezip barkodları BİRLEŞTİRİYORUZ.
                            db_bcs = set()
                            async for db_prod in db.products.find(
                                {"$or": [{"stock_code": sc}, {"sku": sc}, {"variants.stock_code": sc}]},
                                {"_id": 0, "barcode": 1, "variants.barcode": 1}
                            ):
                                if db_prod.get("barcode"):
                                    db_bcs.add(str(db_prod.get("barcode")))
                                for v in (db_prod.get("variants") or []):
                                    if v.get("barcode"):
                                        db_bcs.add(str(v.get("barcode")))
                            db_barcodes_per_sc[sc] = db_bcs
                        # 2) Trendyol'da bu stockCode altındaki tüm barkodları çek
                        try:
                            tr_resp = await client.get_filtered_products(stock_code=sc, archived=False, size=100)
                            for p in (tr_resp or {}).get("content", []):
                                tr_bc = p.get("barcode")
                                if tr_bc and str(tr_bc) not in db_barcodes_per_sc[sc]:
                                    old_barcodes_to_archive.add(str(tr_bc))
                        except Exception as fe:
                            logger.warning(f"get_filtered_products(stock_code={sc}) failed: {fe}")
                    old_barcodes_to_archive = list(old_barcodes_to_archive)
                    logger.info(f"Trendyol CROSS-CONFLICT (deep): {len(old_barcodes_to_archive)} eski barkod arşivlenecek: {old_barcodes_to_archive[:10]}…")
                    try:
                        arch_resp = await client.archive_products(old_barcodes_to_archive) if old_barcodes_to_archive else {}
                        archive_batch_id = (arch_resp or {}).get("batchRequestId")
                        archived_barcodes = old_barcodes_to_archive
                        # Archive batch poll
                        if archive_batch_id:
                            for attempt in range(5):
                                await _asyncio.sleep(1.5)
                                try:
                                    abr = await client.get_batch_request_result(archive_batch_id)
                                    if _norm_status((abr or {}).get("status", "")) in ("COMPLETED", "FAILED"):
                                        break
                                except Exception:
                                    pass
                    except Exception as ae:
                        logger.error(f"Archive products error: {ae}")

                    # Retry CREATE with the new barcodes
                    retry_items = [c[2] for c in remaining_cross]
                    if retry_items:
                        try:
                            retry_resp = await client.create_products(retry_items)
                            retry_create_batch_id = (retry_resp or {}).get("batchRequestId")
                            if retry_create_batch_id:
                                for attempt in range(5):
                                    await _asyncio.sleep(2.0)
                                    try:
                                        rbr = await client.get_batch_request_result(retry_create_batch_id)
                                        rstatus = (rbr or {}).get("status") or "INPROGRESS"
                                        if _norm_status(rstatus) in ("COMPLETED", "FAILED"):
                                            for rit in (rbr or {}).get("items", []) or []:
                                                if (rit.get("status") or "").upper() == "FAILED":
                                                    req = rit.get("requestItem", {}) or {}
                                                    prod = req.get("product", {}) if isinstance(req, dict) else {}
                                                    upsert_failed_items.append({
                                                        "stock_code": prod.get("stockCode") or req.get("barcode") or "",
                                                        "barcode": prod.get("barcode") or req.get("barcode") or "",
                                                        "title": prod.get("title") or "",
                                                        "reasons": rit.get("failureReasons") or [],
                                                        "phase": "retry_create",
                                                    })
                                            retry_create_succeeded = max(
                                                0,
                                                (rbr or {}).get("itemCount", 0) - (rbr or {}).get("failedItemCount", 0),
                                            )
                                            upsert_succeeded += retry_create_succeeded
                                            break
                                    except Exception as rp:
                                        logger.warning(f"Retry-create poll {attempt+1}/5 failed: {rp}")
                        except Exception as re_err:
                            logger.error(f"Retry create after archive error: {re_err}")

            # 2) SELF CONFLICTS: PUT update_products + price-and-inventory POST
            # PUT mevcut ürünün kategori/attribute/image alanlarını günceller; ancak
            # stok/fiyat değerleri için Trendyol'un ayrı bir endpoint'i daha güvenilir
            # (`/inventory/.../price-and-inventory`). Stok güncellemesinin Trendyol
            # panelinde garantili yansıması için ikisini birden çalıştırıyoruz.
            if self_conflicts:
                # 2a) Önce price-and-inventory POST (stok/fiyat senkronu)
                try:
                    pi_items = []
                    for it in self_conflicts:
                        if not it.get("barcode"):
                            continue
                        entry = {
                            "barcode": str(it.get("barcode")),
                            "quantity": int(it.get("quantity", 0)),
                        }
                        if it.get("salePrice") is not None:
                            try:
                                entry["salePrice"] = float(it["salePrice"])
                            except Exception:
                                pass
                        if it.get("listPrice") is not None:
                            try:
                                entry["listPrice"] = float(it["listPrice"])
                            except Exception:
                                pass
                        pi_items.append(entry)
                    if pi_items:
                        pi_resp = await client.update_price_and_inventory(pi_items)
                        pi_batch = (pi_resp or {}).get("batchRequestId")
                        if pi_batch:
                            logger.info(f"Self-conflict price-and-inventory batch: {pi_batch}")
                            # Polling — kısa süre içinde tamamlanır
                            for attempt in range(5):
                                await _asyncio.sleep(1.5)
                                try:
                                    pbr = await client.get_batch_request_result(pi_batch)
                                    if _norm_status((pbr or {}).get("status", "")) in ("COMPLETED", "FAILED"):
                                        break
                                except Exception:
                                    pass
                except Exception as pi_err:
                    logger.warning(f"Self-conflict price-and-inventory exception: {pi_err}")

                # 2b) Sonra update_products PUT (kategori/attribute/image senkronu)
                upsert_attempted += len(self_conflicts)
                try:
                    upd_resp = await client.update_products(self_conflicts)
                    upsert_batch_id = (upd_resp or {}).get("batchRequestId")
                    if upsert_batch_id:
                        for attempt in range(8):
                            await _asyncio.sleep(2.5)
                            try:
                                ubr = await client.get_batch_request_result(upsert_batch_id)
                                upsert_final_status = (ubr or {}).get("status") or "INPROGRESS"
                                if _norm_status(upsert_final_status) in ("COMPLETED", "FAILED"):
                                    put_succeeded = max(
                                        0,
                                        (ubr or {}).get("itemCount", 0) - (ubr or {}).get("failedItemCount", 0),
                                    )
                                    for uit in (ubr or {}).get("items", []) or []:
                                        if (uit.get("status") or "").upper() == "FAILED":
                                            req = uit.get("requestItem", {}) or {}
                                            prod = req.get("product", {}) if isinstance(req, dict) else {}
                                            upsert_failed_items.append({
                                                "stock_code": prod.get("stockCode") or req.get("barcode") or "",
                                                "barcode": prod.get("barcode") or req.get("barcode") or "",
                                                "title": prod.get("title") or "",
                                                "reasons": uit.get("failureReasons") or [],
                                                "phase": "put_update",
                                            })
                                    upsert_succeeded += put_succeeded
                                    break
                            except Exception as up_poll_err:
                                logger.warning(f"Upsert PUT poll {attempt+1}/8 failed: {up_poll_err}")
                    else:
                        upd_err = (upd_resp or {}).get("message") or str(upd_resp)[:500]
                        logger.warning(f"Trendyol PUT update_products reddetti: {upd_err}")
                except Exception as up_err:
                    logger.error(f"Trendyol PUT update_products exception: {up_err}")

        # 🔁 3. FALLBACK: Price-and-inventory "ürün bulunamadı" hatası verirse
        # → Bu varyant Trendyol'da hiç yok. Asıl payload ile create_products'a gönder.
        # (Recurring fallback'te tüm itemlar price-and-inventory'ye yönlendirilince,
        # Trendyol'da olmayan varyantlar "ürün bulunamadı" der; onları create'e geri gönderelim.)
        not_found_failed = [f for f in batch_failed_items if _is_not_found_error(f.get("reasons"))]
        if not_found_failed:
            items_by_barcode = {str(it.get("barcode") or ""): it for it in items_to_send}
            nf_create_items = []
            for f in not_found_failed:
                bc = str(f.get("barcode") or "")
                payload = items_by_barcode.get(bc)
                if payload:
                    nf_create_items.append(payload)
            if nf_create_items:
                upsert_attempted += len(nf_create_items)
                try:
                    nf_resp = await client.create_products(nf_create_items)
                    nf_batch = (nf_resp or {}).get("batchRequestId")
                    if nf_batch:
                        logger.info(f"Not-found → create_products fallback batch: {nf_batch}")
                        if not retry_create_batch_id:
                            retry_create_batch_id = nf_batch
                        for attempt in range(5):
                            await _asyncio.sleep(2.0)
                            try:
                                nbr = await client.get_batch_request_result(nf_batch)
                                if _norm_status((nbr or {}).get("status", "")) in ("COMPLETED", "FAILED"):
                                    nf_succeeded = 0
                                    nf_failed_barcodes = set()
                                    for nit in (nbr or {}).get("items", []) or []:
                                        if (nit.get("status") or "").upper() == "SUCCESS":
                                            nf_succeeded += 1
                                        else:
                                            req = nit.get("requestItem", {}) or {}
                                            prod = req.get("product", {}) if isinstance(req, dict) else {}
                                            bc_n = prod.get("barcode") or req.get("barcode") or ""
                                            nf_failed_barcodes.add(str(bc_n))
                                            upsert_failed_items.append({
                                                "stock_code": prod.get("stockCode") or bc_n,
                                                "barcode": bc_n,
                                                "title": prod.get("title") or "",
                                                "reasons": nit.get("failureReasons") or [],
                                                "phase": "not_found_create",
                                            })
                                    upsert_succeeded += nf_succeeded
                                    # not-found-success olanları batch_failed_items'tan düş
                                    succeeded_barcodes = {
                                        str(f.get("barcode"))
                                        for f in not_found_failed
                                        if str(f.get("barcode")) not in nf_failed_barcodes
                                    }
                                    if succeeded_barcodes:
                                        batch_failed_items = [
                                            f for f in batch_failed_items
                                            if str(f.get("barcode")) not in succeeded_barcodes
                                        ]
                                        batch_success_count += nf_succeeded
                                    break
                            except Exception as nf_poll_err:
                                logger.warning(f"Not-found-create poll {attempt+1}/5 failed: {nf_poll_err}")
                except Exception as nf_err:
                    logger.error(f"Not-found → create_products exception: {nf_err}")

        # Upsert ile düzelen item'ları batch_failed_items'tan düş
        if upsert_succeeded > 0 and duplicate_failed:
            still_failed_keys = set()
            for f in upsert_failed_items:
                if f.get("barcode"):
                    still_failed_keys.add(str(f["barcode"]))

            def _still_failed(f):
                if not _is_duplicate_error(f.get("reasons")):
                    return True
                return str(f.get("barcode") or "") in still_failed_keys

            batch_failed_items = [f for f in batch_failed_items if _still_failed(f)]
            batch_success_count = batch_success_count + upsert_succeeded

        # Local "errors" + Trendyol API hataları + Batch failure'ları birleştir
        all_errors = list(errors)
        if trendyol_error:
            all_errors.append(f"Trendyol API: {trendyol_error}")
        for f in batch_failed_items:
            reason = "; ".join([
                (r.get("message") if isinstance(r, dict) else str(r)) for r in (f.get("reasons") or [])
            ])
            all_errors.append(f"{f.get('title') or f.get('stock_code')} [{f.get('stock_code')}]: {reason}")
        for f in upsert_failed_items:
            reason = "; ".join([
                (r.get("message") if isinstance(r, dict) else str(r)) for r in (f.get("reasons") or [])
            ])
            all_errors.append(f"[UPDATE] {f.get('title') or f.get('stock_code')} [{f.get('stock_code')}]: {reason}")

        # Final status hesapla
        if not batch_id:
            final_status = "failed"
        elif batch_failed_items and batch_success_count == 0:
            final_status = "failed"
        elif batch_failed_items:
            final_status = "partial"
        elif _norm_status(batch_final_status) == "INPROGRESS":
            final_status = "pending"  # 60sn'de tamamlanmadı, kullanıcıya logdan takibi öneriliyor
        else:
            final_status = "success" if not errors else "partial"

        log_doc = {
            "id": generate_id(),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": final_status,
            "products_attempted": len(products),
            "products_sent": batch_success_count if batch_id else 0,
            "products_failed": len(batch_failed_items),
            "batch_request_id": batch_id,
            "batch_final_status": batch_final_status,
            "upsert_attempted": upsert_attempted,
            "upsert_succeeded": upsert_succeeded,
            "upsert_batch_id": upsert_batch_id,
            "upsert_final_status": upsert_final_status,
            "upsert_failed_items": upsert_failed_items,
            "archived_barcodes": archived_barcodes,
            "archive_batch_id": archive_batch_id,
            "retry_create_batch_id": retry_create_batch_id,
            "retry_create_succeeded": retry_create_succeeded,
            "errors": all_errors,
            "failed_items": batch_failed_items,
            "trendyol_response": response,
            "message": (
                f"{batch_success_count} ürün başarıyla aktarıldı." + (f" ({upsert_succeeded} adet UPDATE ile)." if upsert_succeeded else "") if batch_id and _norm_status(batch_final_status) == "COMPLETED" and not batch_failed_items
                else f"{batch_success_count} başarılı, {len(batch_failed_items)} HATA — detaylar loglarda." if batch_failed_items
                else f"Batch alındı, Trendyol işliyor (durum: {batch_final_status}). Loglardan takip edin." if batch_id
                else f"Trendyol kabul etmedi: {trendyol_error}"
            ),
        }
        await db.trendyol_sync_logs.insert_one(log_doc)
        # _id ekleniyor MongoDB tarafından — response'a koyma
        log_doc.pop("_id", None)

        # 🤖 OTOMATİK RETRY KUYRUĞU: "Aynı barkod var" / "ürün bulunamadı" / "Trendyol cache"
        # tipi sıkışmalarda barkodları otomatik kuyruğa ekle (saatte bir retry edilir).
        # Header'da X-Internal-Retry varsa atla (loop önlemi).
        try:
            from fastapi import Request as _Req  # noqa
            # request header'ına direkt erişim yok; bunun yerine Request inject etmiyoruz.
            # Bunun yerine sadece "stuck" tipi hataları kuyruğa al.
            stuck_barcodes = []
            for f in batch_failed_items:
                reasons_text = " ".join([
                    (r.get("message") if isinstance(r, dict) else str(r))
                    for r in (f.get("reasons") or [])
                ]).lower()
                if any(p in reasons_text for p in ["aynı barkodlu", "ayni barkodlu", "ürün bulunamadı", "urun bulunamadi", "tedarikçi id si", "tedarikci id si"]):
                    if f.get("barcode"):
                        stuck_barcodes.append(str(f["barcode"]))
            for f in upsert_failed_items:
                reasons_text = " ".join([
                    (r.get("message") if isinstance(r, dict) else str(r))
                    for r in (f.get("reasons") or [])
                ]).lower()
                if any(p in reasons_text for p in ["aynı barkodlu", "ayni barkodlu", "ürün bulunamadı", "urun bulunamadi"]):
                    if f.get("barcode"):
                        stuck_barcodes.append(str(f["barcode"]))
            if stuck_barcodes:
                from routes.trendyol_retry_queue import add_to_queue as _add_to_queue
                added = await _add_to_queue(db, stuck_barcodes, reason="Trendyol cache conflict — auto-queued")
                logger.info(f"Trendyol auto-queue: {added} barkod retry kuyruğuna eklendi")
        except Exception as q_err:
            logger.warning(f"Auto-queue failed: {q_err}")

        return {
            "success": bool(batch_id) and not batch_failed_items and _norm_status(batch_final_status) != "FAILED",
            "message": log_doc["message"],
            "total": len(products),
            "successful": batch_success_count if batch_id else 0,
            "failed": len(batch_failed_items) + (0 if batch_id else len(items_to_send)),
            "batchRequestId": batch_id,
            "batch_final_status": batch_final_status,
            "failed_items": batch_failed_items,
            "upsert_attempted": upsert_attempted,
            "upsert_succeeded": upsert_succeeded,
            "upsert_batch_id": upsert_batch_id,
            "upsert_failed_items": upsert_failed_items,
            "archived_barcodes": archived_barcodes,
            "archive_batch_id": archive_batch_id,
            "retry_create_batch_id": retry_create_batch_id,
            "errors": all_errors,
            "trendyol_response": response,
        }
    except Exception as e:
        logger.error(f"Error syncing products to Trendyol: {str(e)}")
        from datetime import datetime, timezone
        log_doc = {
            "id": generate_id(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "error",
            "products_attempted": len(products),
            "products_sent": 0,
            "batch_request_id": None,
            "errors": errors + [f"API Hatası: {str(e)}"],
            "message": "Trendyol API hatası oluştu."
        }
        await db.trendyol_sync_logs.insert_one(log_doc)
        if hasattr(e, "response") and hasattr(e.response, "json"):
            raise HTTPException(status_code=500, detail=e.response.json())
        raise HTTPException(status_code=500, detail="Trendyol API ürün gönderimi başarısız oldu.")
@router.get("/trendyol/sync-logs")
async def get_trendyol_sync_logs(
    page: int = 1,
    limit: int = 20,
    current_user: dict = Depends(require_admin)
):
    """Get paginated Trendyol sync logs"""
    skip = (page - 1) * limit
    logs = await db.trendyol_sync_logs.find({}, {"_id": 0}).sort("started_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.trendyol_sync_logs.count_documents({})
    return {"logs": logs, "total": total, "page": page}
@router.post("/trendyol/products/inventory-sync")
async def sync_trendyol_inventory(payload: dict = Body(default={}), current_user: dict = Depends(require_admin)):
    """Bulk sync stock and prices to Trendyol.

    payload BOŞSA  -> tüm aktif ürünler (eski davranış, byte-aynı).
    payload barcodes/stock_codes/product_ids/category_filters İÇERİYORSA
                   -> yalnız hedeflenen ürünler (kod-bazlı stok/fiyat güncelleme, Faz T2).
    Aktarmadaki _build_product_query_from_payload aynen yeniden kullanılır.
    """
    has_filter = any(payload.get(k) for k in ("barcodes", "stock_codes", "product_ids", "category_filters"))
    if has_filter:
        query = await _build_product_query_from_payload(payload)
        # query boş kalırsa (örn. eşleşmeyen kategori) HER ŞEYİ değil, hiçbir şeyi güncelle -> güvenli.
        products = await db.products.find(query).to_list(length=None) if query else []
    else:
        products = await db.products.find({"is_active": True}).to_list(length=None)
    return await _sync_inventory_to_trendyol(products)
@router.post("/trendyol/products/{product_id}/sync-inventory")
async def sync_single_product_inventory(
    product_id: str,
    current_user: dict = Depends(require_admin)
):
    """Sync stock and prices for a single product to Trendyol"""
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    return await _sync_inventory_to_trendyol([product])
async def _sync_inventory_to_trendyol(products: list):
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
        
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )
    
    items_to_send = []
    # Global markup (Ana Ayarlar/Trendyol default_markup) uygula — cron ile manuel push
    # AYNI mantıkta çalışsın (aksi halde otomatik senkron zammı geri alıyordu).
    default_markup = float(config.get("default_markup", 0) or 0)

    for product in products:
        _mult = product.get("trendyol_multiplier")
        markup = float(_mult) if (_mult is not None and float(_mult) > 0) else default_markup
        factor = 1 + markup / 100.0
        base_price = _mp_base_price(product) * factor  # #4: üye fiyatı baz
        sale_price = _mp_base_price(product) * factor  # Trendyol: indirimsiz satis fiyati
        variants = product.get("variants", [])
        if not variants:
            if product.get("barcode"):
                items_to_send.append({
                    "barcode": product["barcode"],
                    "quantity": int(product.get("stock", 0)),
                    "salePrice": round(sale_price, 2),
                    "listPrice": round(base_price, 2)
                })
        else:
            for v in variants:
                if v.get("barcode"):
                    diff = float(v.get("price_diff") or 0)
                    items_to_send.append({
                        "barcode": v["barcode"],
                        "quantity": int(v.get("stock", 0)),
                        "salePrice": round(sale_price + diff, 2),
                        "listPrice": round(base_price + diff, 2)
                    })
    
    from datetime import datetime, timezone
    started_at = datetime.now(timezone.utc).isoformat()
    
    if not items_to_send:
        # Log failure to sync screen
        log_doc = {
            "id": generate_id(),
            "started_at": started_at,
            "status": "failed",
            "products_attempted": len(products),
            "products_sent": 0,
            "batch_request_id": None,
            "errors": ["Gönderilecek stok/fiyat bilgisi bulunamadı (barkodlar eksik olabilir)."],
            "message": "Envanter güncellemesi başarısız (geçerli barkod yok)."
        }
        await db.trendyol_sync_logs.insert_one(log_doc)
        return {"success": False, "message": "Gönderilecek stok/fiyat bilgisi bulunamadı (barkod eksik?)"}
        
    try:
        res = await client.update_price_and_inventory(items_to_send)
        batch_id = res.get("batchRequestId", "")

        # M1 FIX: batchRequestId YOKSA Trendyol isteği REDDETTİ (client HTTP hatasında
        # e.response.json() döndürüyor → batchRequestId olmaz). Eskiden yine "success" deniyordu
        # → tamamen reddedilen stok/fiyat pushları başarı sanılıp SESSİZ stok/fiyat sürüklenmesi
        # oluyordu. Artık batch_id yoksa HATA raporla.
        if not batch_id:
            _err = str(res.get("errors") or res.get("message") or res)[:500]
            log_doc = {
                "id": generate_id(),
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "products_attempted": len(products),
                "products_sent": len(items_to_send),
                "batch_request_id": "",
                "errors": [_err],
                "message": f"Trendyol stok/fiyat güncellemesini reddetti: {_err}",
            }
            await db.trendyol_sync_logs.insert_one(log_doc)
            return {"success": False, "message": f"Trendyol stok/fiyat reddetti: {_err}", "batch_id": ""}

        # Log to the new sync logs screen
        log_doc = {
            "id": generate_id(),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "success",
            "products_attempted": len(products),
            "products_sent": len(items_to_send),
            "batch_request_id": batch_id,
            "errors": [],
            "message": "Stok ve fiyat güncellemesi başarıyla gönderildi."
        }
        await db.trendyol_sync_logs.insert_one(log_doc)

        return {"success": True, "message": f"{len(items_to_send)} kalem ürünün stok/fiyat bilgisi Trendyol'a gönderildi.", "batch_id": batch_id}
    except Exception as e:
        logger.error(f"Trendyol inventory sync error: {str(e)}")
        log_doc = {
            "id": generate_id(),
            "started_at": started_at,
            "status": "error",
            "products_attempted": len(products),
            "products_sent": 0,
            "batch_request_id": None,
            "errors": [f"API Hatası: {str(e)}"],
            "message": "Stok/fiyat güncellemesi sırasında hata oluştu."
        }
        await db.trendyol_sync_logs.insert_one(log_doc)
        raise HTTPException(status_code=500, detail=f"Trendyol stok/fiyat güncelleme hatası: {str(e)}")
@router.get("/trendyol/products/batch-status/{batch_id}")
async def get_trendyol_batch_status_v2(batch_id: str, current_user: dict = Depends(require_admin)):
    """Check the status of a batch request"""
    config = await get_trendyol_config()
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    
    try:
        client = TrendyolClient(
            supplier_id=config["supplier_id"],
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            mode=config["mode"]
        )
        status = await client.get_batch_request_result(batch_id)
        return {"success": True, "status": status}
    except Exception as e:
        logger.error(f"Error fetching batch status: {str(e)}")
        raise HTTPException(status_code=500, detail="Batch durumu alınamadı.")
async def _sync_trendyol_status_passes(client, start_date_ms, end_date_ms, widen_cancel=True, statuses=("Cancelled", "UnSupplied", "Returned", "UnDelivered")):
    """İptal/iade/teslim edilemedi gibi durum değişikliklerini ayrı status
    sorgularıyla yakalar; yalnızca MEVCUT siparişlerin status alanını günceller
    (kaydı baştan ezmez). Trendyol orders ucu ~2 haftalık pencereyle sınırlıdır;
    daha eski iadeler İadeler (claims) modülünden takip edilir."""
    from datetime import datetime, timezone, timedelta
    updated = 0
    _logged_sample = False
    _CANCEL_PASS = ("Cancelled", "UnSupplied")
    for st in statuses:
        # İptaller daha GENİŞ pencerede taranır: Trendyol startDate/endDate sipariş
        # TARİHİNE göre filtreler → 14 günden eski bir sipariş sonradan iptal olursa dar
        # pencereye girmez ve İptaller'e hiç düşmezdi ("bazıları düşmemiş" kök nedeni).
        # Cancelled için pencereyi 45 güne kadar geriye çek.
        _start = start_date_ms
        if st in _CANCEL_PASS and widen_cancel:
            _wide = int((datetime.now(timezone.utc) - timedelta(days=45)).timestamp() * 1000)
            _start = min(start_date_ms, _wide) if start_date_ms else _wide
        try:
            # Y8: Trendyol orders ucu ~14 gunluk startDate/endDate araligina izin verir.
            # Onceden Cancelled icin 45 gunluk TEK istek atiliyordu → 400 → except yutuyor →
            # TUM iptal senkronu sessizce hicbir sey yapmiyordu. Artik aralik 14 gunluk
            # pencerelere bolunur ve her pencere ayri sayfalanir.
            _WIN = 14 * 24 * 3600 * 1000
            _win_s = _start or (end_date_ms - _WIN)
            _win_e = min(_win_s + _WIN, end_date_ms)
            page = 0
            _guard = 0
            while _guard < 400:
                _guard += 1
                resp = await client.get_orders(
                    start_date_ms=_win_s, end_date_ms=_win_e,
                    status=st, size=200, page=page,
                )
                chunk = resp.get("content", []) or []
                for t_order in chunk:
                    onum = str(t_order.get("orderNumber"))
                    mapped = map_trendyol_order(t_order)
                    _raw_status = t_order.get("status")
                    # GERÇEK SEBEP ARAYIŞI — ground truth için: iptal kaydının HAM yapısını
                    # her taramada BİR KEZ logla; Trendyol'un sebebi hangi alanda verdiğini
                    # (varsa) buradan kesin görürüz.
                    if st in _CANCEL_PASS and not _logged_sample:
                        try:
                            import json as _json
                            _lines0 = (t_order.get("lines") or [{}])[0]
                            logger.info(
                                "[trendyol cancel sample %s] top_keys=%s line_keys=%s "
                                "cancellationReason=%r cancelReason=%r packageHistories=%r "
                                "line.cancellationReason=%r line.orderLineItemStatusName=%r",
                                onum, sorted([str(k) for k in t_order.keys()]),
                                sorted([str(k) for k in _lines0.keys()]),
                                t_order.get("cancellationReason"), t_order.get("cancelReason"),
                                str(t_order.get("packageHistories"))[:300],
                                _lines0.get("cancellationReason"), _lines0.get("orderLineItemStatusName"),
                            )
                            _logged_sample = True
                        except Exception:
                            _logged_sample = True
                    # Sebebi orders ucundaki TÜM olası alanlardan dene (sipariş + line seviyesi).
                    _l0 = (t_order.get("lines") or [{}])[0] if t_order.get("lines") else {}
                    _reason = (
                        t_order.get("cancellationReason") or t_order.get("cancelReason")
                        or t_order.get("cancellationReasonText") or t_order.get("customerCancellationReason")
                        or _l0.get("cancellationReason") or _l0.get("cancelReason") or ""
                    )
                    if isinstance(_reason, dict):
                        _reason = _reason.get("name") or _reason.get("text") or _reason.get("reason") or ""
                    _reason = str(_reason or "").strip()
                    if not _reason and st in _CANCEL_PASS:
                        # Yedek: müşteri iade/iptal claim'i varsa oradaki sebep.
                        try:
                            _cl = await db.trendyol_claims.find_one(
                                {"order_number": onum, "claim_reason": {"$nin": ["", None]}},
                                {"_id": 0, "claim_reason": 1},
                                sort=[("updated_at", -1)],
                            )
                            if _cl and _cl.get("claim_reason"):
                                _reason = _cl["claim_reason"]
                        except Exception:
                            pass
                    if not _reason:
                        _reason = ("Trendyol iptali" if st in _CANCEL_PASS
                                   else ("Teslim edilemedi (Trendyol)" if st == "UnDelivered"
                                         else "Trendyol iadesi"))
                    # ── KISMİ İPTAL KORUMASI ───────────────────────────────────────────
                    # Trendyol kısmi iptalde siparişi ÇOK PAKETE böler (biri iptal, biri AKTİF).
                    # Sistem tek orderNumber kaydı tuttuğundan, iptal paketi aktif paketi EZİP
                    # siparişi komple "cancelled" gösteriyordu → kalan ürüne fatura kesilemiyordu.
                    # Bu yüzden bir siparişi Cancelled yapmadan ÖNCE orderNumber'ın TÜM paketlerini
                    # Trendyol'dan çek: AKTİF (iptal/iade/teslim-edilemedi OLMAYAN) paket varsa
                    # siparişi İPTAL ETME → aktif paketin kalem/tutar/durumuna çek (kısmi iptal).
                    # Tamamen defensive: herhangi bir hata olursa eski (tam-iptal) akışa düşer.
                    if st in _CANCEL_PASS:
                        _active_pkg = None
                        try:
                            _allp = await client.get_orders(
                                order_number=onum,
                                start_date_ms=max(0, end_date_ms - 120 * 24 * 3600 * 1000),
                                end_date_ms=end_date_ms, size=100,
                            )
                            _DEAD_ST = ("Cancelled", "Returned", "UnDelivered", "UnSupplied")
                            for _pk in (_allp.get("content") or []):
                                _pst = str(_pk.get("shipmentPackageStatus") or _pk.get("status") or "")
                                if _pst and _pst not in _DEAD_ST:
                                    _active_pkg = _pk
                                    break
                        except Exception as _pce:
                            logger.warning(f"[trendyol kismi-iptal kontrol {onum}] {_pce}")
                            _active_pkg = None
                        if _active_pkg:
                            try:
                                _amap = map_trendyol_order(_active_pkg)
                                _pset = {
                                    "status": _amap.get("status") or "confirmed",
                                    "items": _amap.get("items"),
                                    "trendyol_status_raw": (_active_pkg.get("shipmentPackageStatus")
                                                            or _active_pkg.get("status")),
                                    "trendyol_active_package_id": str(_active_pkg.get("id") or ""),
                                    "partial_cancelled": True,
                                    "updated_at": datetime.now(timezone.utc).isoformat(),
                                }
                                if _amap.get("total") is not None:
                                    _pset["total"] = _amap.get("total")
                                # İADE KORUMASI: kısmi-iptal aktif pakete çekerken, claim ile
                                # 'returned' yapılmış siparişi ezme (iptal→aktif meşru, iade→aktif değil).
                                _ppf = {"order_number": onum, "platform": "trendyol",
                                        "status": {"$nin": ["returned", "refunded", "partial_refunded"]}}
                                await db.orders.update_one(_ppf, {"$set": _pset})
                                updated += 1
                                logger.info(f"[trendyol kismi-iptal] {onum}: aktif paket "
                                            f"{_active_pkg.get('id')} yansitildi, siparis iptal EDILMEDI")
                                continue  # tam-iptal bloğunu ATLA
                            except Exception as _pae:
                                logger.error(f"[trendyol kismi-iptal uygula {onum}] {_pae}")
                                # düşerse normal (tam) iptal akışına devam — güvenli
                    _set = {
                        "status": mapped.get("status"),
                        "trendyol_status_raw": _raw_status,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    if st in _CANCEL_PASS:
                        _set["cancel_reason"] = _reason
                        _set["cancel_source"] = "trendyol"
                    # İADE KORUMASI (kök neden): bu sweep bir siparişi ASLA terminal (iade/iptal)
                    # durumundan TEKRAR aktif 'confirmed'e ÇEKMESİN. Örn. "UnDelivered" paketi
                    # 'confirmed'e map olur; claim ile 'returned' yapılmış siparişi bu ezip ciroyu
                    # geri şişiriyordu (211 iade kayboldu). Terminal→terminal (returned↔cancelled)
                    # serbest; yalnız terminal→confirmed engellenir.
                    _uf = {"order_number": onum, "platform": "trendyol"}
                    if mapped.get("status") not in ("cancelled", "returned", "refunded", "partial_refunded"):
                        _uf["status"] = {"$nin": ["returned", "refunded", "partial_refunded", "cancelled"]}
                    res = await db.orders.update_one(_uf, {"$set": _set})
                    if res.modified_count:
                        updated += 1
                    # ÖNEMLİ ("bi düşüyor bi düşmüyor" kök nedeni): sipariş bizde YOKSA
                    # (eski / hiç senkronlanmamış) eskiden update_one sessizce düşüyordu.
                    # Artık tam kaydı iptal/iade durumuyla EKLERİZ → İptaller/İadeler
                    # sayfasına garanti düşer. created_at = gerçek sipariş tarihi (orderDate)
                    # ki tarih sıralaması doğru olsun.
                    _was_new = (res.matched_count == 0)
                    if _was_new:
                        try:
                            mapped["id"] = generate_id()
                            mapped["created_at"] = _ms_to_iso(t_order.get("orderDate")) or datetime.now(timezone.utc).isoformat()
                            mapped["trendyol_status_raw"] = _raw_status
                            if st in _CANCEL_PASS:
                                mapped["cancel_reason"] = _reason
                                mapped["cancel_source"] = "trendyol"
                            await db.orders.insert_one(mapped)
                            updated += 1
                        except Exception as _ie:
                            logger.error(f"[trendyol status upsert {onum}] {_ie}")
                    # İptal senkronu → stoğu BİR KEZ geri ekle (idempotent; manuel iptalle aynı
                    # order_cancelled guard). YENİ eklenen kayıtta stok geri EKLENMEZ: bu sipariş
                    # bizde hiç olmadığı için stok daha önce DÜŞÜLMEDİ → +1 yanlış olurdu.
                    if st in _CANCEL_PASS and not _was_new:
                        try:
                            _o = await db.orders.find_one({"order_number": onum, "platform": "trendyol"}, {"_id": 0, "id": 1, "items": 1})
                            if _o:
                                from routes.orders import _stock_delta_for_order, _RESTORE_MOVE_TYPES
                                # B4: guard'ı TÜM restore hareketlerine genişlet (kısmi iade
                                # sonrası tam-iptal çift-restock'unu engelle).
                                _already = await db.stock_movements.find_one({"order_id": _o.get("id"), "type": {"$in": _RESTORE_MOVE_TYPES}}, {"_id": 1})
                                if not _already:
                                    _moves = await _stock_delta_for_order(_o, +1)
                                    await db.stock_movements.insert_one({
                                        "id": str(uuid.uuid4()), "type": "order_cancelled",
                                        "order_id": _o.get("id"), "order_number": onum,
                                        "items": _moves, "source": "trendyol_cancel_sync",
                                        "created_at": datetime.now(timezone.utc).isoformat(),
                                    })
                        except Exception as _re:
                            logger.error(f"[trendyol cancel restock {onum}] {_re}")
                total_pages = resp.get("totalPages") or 0
                page += 1
                if chunk and page < total_pages:
                    continue
                # Bu 14 gunluk pencere bitti → varsa sonraki pencereye gec (Y8).
                if _win_e >= end_date_ms:
                    break
                _win_s = _win_e
                _win_e = min(_win_s + _WIN, end_date_ms)
                page = 0
        except Exception as e:
            logger.error(f"[trendyol status pass {st}] {e}")
    return updated
def map_trendyol_order(t_order: dict) -> dict:
    from datetime import datetime, timezone
    order_number = t_order.get("orderNumber")
    
    items = []
    total_price = t_order.get("totalPrice", 0)
    gross_amount = t_order.get("grossAmount", 0)
    total_discount = t_order.get("totalDiscount", 0)
    
    for line in t_order.get("lines", []):
        qty = max(line.get("quantity", 1), 1)
        # KRİTİK: TY satır alanları (lineGrossAmount/amount/price/discount) ZATEN BİRİM'dir —
        # adede BÖLÜNMEZ. Eski /qty bölmesi qty>1 siparişte birim fiyatı YARIYA düşürüyordu
        # (11419198311 kanıtı; paket totalPrice = birim × adet ile doğrulandı).
        unit_price = line.get("lineGrossAmount", line.get("amount", 0)) or 0  # birim brüt
        # Trendyol 'price' alanı = net (indirimli) BİRİM fiyat
        net_price = line.get("price", line.get("lineUnitPrice", 0)) or 0
        discount_per_item = line.get("discount", 0) or 0  # birim indirim

        items.append({
            "product_id": line.get("productCode"),
            "product_name": line.get("productName"),
            "quantity": qty,
            "unit_price": unit_price, # İndirimsiz birim fiyat
            "discount_amount": discount_per_item, # Birim başına indirim
            "price": net_price, # Net ödenen birim fiyat (Faturalandırılan)
            "size": line.get("productSize", ""),
            "color": line.get("productColor", ""),
            "barcode": line.get("barcode", ""),
            "currency": line.get("currencyCode", "TRY")
        })

    shipment_address = t_order.get("shipmentAddress", {})
    invoice_address = t_order.get("invoiceAddress", {})

    # --- Kurumsal fatura alanları (VKN / vergi dairesi / ünvan) ---
    # Trendyol bu alanları invoiceAddress İÇİNDE de, sipariş ÜST SEVİYESİNDE de
    # (taxNumber / taxOffice) verebilir. Eskiden yalnızca invoiceAddress.* okunduğu
    # için müşteri kurumsal fatura talebinde VKN girse bile (üst seviye taxNumber)
    # billing'e düşmüyor → e-Arşiv VKN'siz/vergi-dairesiz kalıp Doğan reddediyordu.
    # Artık ikisi de okunur, dolu olan kullanılır.
    _cust_tax_number = (str(invoice_address.get("taxNumber") or "").strip()
                        or str(t_order.get("taxNumber") or "").strip())
    _cust_tax_office = (str(invoice_address.get("taxOffice") or "").strip()
                        or str(t_order.get("taxOffice") or "").strip())
    _cust_company = (str(invoice_address.get("company") or "").strip()
                     or str(invoice_address.get("companyName") or "").strip()
                     or str(invoice_address.get("companyTitle") or "").strip())
    _is_corporate = (len(_cust_tax_number) == 10) or bool(_cust_company)

    # --- Mikro İhracat Tespiti ---
    # KESİN gösterge: Trendyol paketindeki `micro` bayrağı (ve ETGB no/tarihi).
    # Adres ülkesine güvenilmez: mikro ihracatta kargo Türkiye içi aktarım
    # noktasına teslim edildiği için shipmentAddress.country = "Türkiye" görünür;
    # yabancı ülke invoiceAddress (alıcı) tarafındadır. Bu yüzden yedek ülke
    # kontrolü invoiceAddress üzerinden yapılır.
    delivery_type = (t_order.get("deliveryType") or "").lower()
    _buyer_country = (invoice_address.get("country") or "").strip().lower()
    _is_intl_buyer = bool(_buyer_country) and _buyer_country not in ("turkey", "türkiye", "tr")
    is_micro_export = bool(
        t_order.get("micro")                 # birincil/kesin bayrak
        or t_order.get("etgbNo")             # ETGB numarası => mikro ihracat
        or t_order.get("etgbDate")
        or "micro" in delivery_type
        or "international" in delivery_type
        or _is_intl_buyer                    # yedek: ALICI ülkesi TR dışı
    )
    
    # Pazaryeri siparisi panele DAIMA "confirmed" (Onaylandi) duser; Trendyol'un is
    # akisi durumu (Picking/Invoiced/Shipped/Delivered) bizim operasyonel durumumuzu
    # EZMEZ. Yalnizca iptal/iade/teslim-edilemedi terminal durumlari yansir.
    # KRİTİK: "UnDelivered" MÜŞTERİ İADESİ DEĞİL — kargo teslim edilemedi (adreste yok vb.);
    # çoğu yeniden teslim edilir. Eskiden "returned"a eşlenmesi Delivered siparişleri iade
    # gösterip ciroyu düşürüyordu (mutabakat: claim'siz 36 sahte iade). Artık iade sayılmaz →
    # varsayılan "confirmed" (yeniden teslim bekliyor). Yalnız gerçek müşteri iadesi (Trendyol
    # onaylı CLAIM) siparişi "returned" yapar (claims-sync). "UnDeliveredAndReturned" = mal
    # geri döndü, satış tamamlanmadı → ciro dışı "cancelled".
    status_map = {
        "Cancelled": "cancelled",
        "UnSupplied": "cancelled",   # Tedarik Edilemedi — Trendyol da iptal sayar
        "Returned": "returned",
        "UnDeliveredAndReturned": "cancelled",
    }
    
    order_doc = {
        "order_number": str(order_number),
        "platform": "trendyol",
        "trendyol_package_id": t_order.get("id"),
        "user_id": None,
        "items": items,
        "shipping_address": {
            "first_name": shipment_address.get("firstName", "Trendyol"),
            "last_name": shipment_address.get("lastName", "Müşterisi"),
            "phone": shipment_address.get("phone", ""),
            "email": t_order.get("customerEmail", ""),
            "address": shipment_address.get("fullAddress", ""),
            "city": shipment_address.get("city", ""),
            "district": shipment_address.get("district", ""),
            "country": shipment_address.get("country", "")
        },
        "billing_address": {
            "first_name": invoice_address.get("firstName", "Trendyol"),
            "last_name": invoice_address.get("lastName", "Müşterisi"),
            "phone": invoice_address.get("phone", ""),
            "address": invoice_address.get("fullAddress", ""),
            "city": invoice_address.get("city", ""),
            "district": invoice_address.get("district", ""),
            "country": invoice_address.get("country", ""),
            "company_name": _cust_company,
            "tax_number": _cust_tax_number,
            "tax_office": _cust_tax_office,
            "is_corporate": _is_corporate
        },
        "billing_info": {
            "is_corporate": _is_corporate,
            "company_name": _cust_company,
            "tax_number": _cust_tax_number,
            "tax_office": _cust_tax_office,
            # Müşteri self-deklarasyonu burada yok; e-Fatura mükellefiyeti kesimde
            # Doğan CheckUser ile sorgulanır → mükellefse e-Fatura, değilse e-Arşiv.
            "e_invoice_user": False,
        },
        "subtotal": gross_amount if gross_amount else total_price,
        "shipping_cost": 0,
        "discount_amount": total_discount,
        "total": total_price,
        "payment_method": "marketplace",
        "payment_status": "paid",
        "status": status_map.get(t_order.get("status"), "confirmed"),
        "cargo_tracking_number": t_order.get("cargoTrackingNumber", ""),
        "cargo_tracking_link": t_order.get("cargoTrackingLink", ""),
        "cargo_provider_name": t_order.get("cargoProviderName", ""),
        "invoice_link": t_order.get("invoiceLink", ""),
        # Mikro ihracat bilgileri — ayrı faturalama/ETGB akışı için
        "is_micro_export": is_micro_export,
        "shipment_country": shipment_address.get("country", ""),
        "delivery_type": t_order.get("deliveryType", ""),
        "trendyol_customer_id": str(t_order.get("customerId") or ""),
        "trendyol_identity_number": str(t_order.get("identityNumber") or ""),
        # --- FAZ 1b: Pazaryeri / SLA bilgileri (ham .get; alan yoksa boş) ---
        "marketplace_status": t_order.get("status", ""),
        "marketplace_agreed_delivery_date": _ms_to_iso(t_order.get("agreedDeliveryDate")),
        "marketplace_estimated_delivery_start": _ms_to_iso(t_order.get("estimatedDeliveryStartDate")),
        "marketplace_estimated_delivery_end": _ms_to_iso(t_order.get("estimatedDeliveryEndDate")),
        "marketplace_last_modified": _ms_to_iso(t_order.get("lastModifiedDate")),
        "cargo_sender_number": str(t_order.get("cargoSenderNumber") or ""),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    return order_doc
@router.post("/trendyol/orders/preview")
async def preview_trendyol_orders(req: TrendyolOrderPreviewReq, current_user: dict = Depends(require_admin)):
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"], api_key=config["api_key"],
        api_secret=config["api_secret"], mode=config["mode"]
    )
    try:
        resp = await client.get_orders(
            start_date_ms=req.start_date_ms, 
            end_date_ms=req.end_date_ms, 
            order_number=req.order_number, 
            size=100
        )
        content = resp.get("content", [])
        return {"success": True, "orders": content}
    except Exception as e:
        logger.error(f"Error previewing Trendyol orders: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
@router.post("/trendyol/orders/import-selected")
async def import_selected_trendyol_orders(req: TrendyolOrderImportReq, current_user: dict = Depends(require_admin)):
    try:
        from datetime import datetime, timezone
        from .deps import generate_id
        imported_count = 0
        updated_count = 0
        errors = []
        for t_order in req.orders:
            order_number = str(t_order.get("orderNumber"))
            try:
                order_data = map_trendyol_order(t_order)
                existing = await db.orders.find_one({"order_number": order_number, "platform": "trendyol"})
                if existing:
                    await db.orders.update_one({"_id": existing["_id"]}, {"$set": {k: v for k, v in order_data.items() if k != "status"}})
                    updated_count += 1
                else:
                    order_data["id"] = generate_id()
                    order_data["created_at"] = _ms_to_iso(t_order.get("orderDate")) or datetime.now(timezone.utc).isoformat()
                    await db.orders.insert_one(order_data)
                    imported_count += 1
                    await _decrement_stock_for_imported_order(order_data, "trendyol")
                await log_integration_event("trendyol", "import_order", "order", order_number, "success", "Sipariş başarıyla aktarıldı.")
            except Exception as e:
                err_msg = str(e)
                errors.append({"orderNumber": order_number, "error": err_msg})
                await log_integration_event("trendyol", "import_order", "order", order_number, "error", f"Aktarım hatası: {err_msg}", {"raw": t_order})
                
        return {"success": True, "imported": imported_count, "updated": updated_count, "errors": errors}
    except Exception as e:
        logger.error(f"Error importing selected orders: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
@router.post("/trendyol/orders/backfill")
async def backfill_trendyol_orders(payload: dict, current_user: dict = Depends(require_admin)):
    """GEÇMİŞ Trendyol siparişlerini tarih aralığıyla içe aktarır (tek seferlik veri kurtarma).

    KURALLAR (kullanıcı şartı — bozma):
    - STOK DÜŞÜMÜ YAPILMAZ: güncel stok zaten doğru; geçmiş siparişin düşümü stoku bozar.
    - Bildirim/mail/push tetiklenmez — yalnız sipariş kaydı yazılır (raporlar için).
    - Var olan sipariş (order_number + platform=trendyol) ATLANIR, asla güncellenmez.
    - TY orders API ~2 haftalık pencere ister → aralık 13 günlük dilimlerle taranır.
    payload: {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", "dry_run": false}
    """
    from datetime import datetime, timezone, timedelta
    from .deps import generate_id
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
    try:
        sd = datetime.strptime(str(payload.get("start_date")), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        ed = datetime.strptime(str(payload.get("end_date")), "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    except Exception:
        raise HTTPException(status_code=400, detail="start_date/end_date YYYY-MM-DD biçiminde olmalı")
    if ed <= sd:
        raise HTTPException(status_code=400, detail="end_date, start_date'ten büyük olmalı")
    dry = bool(payload.get("dry_run"))

    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"], api_key=config["api_key"],
        api_secret=config["api_secret"], mode=config["mode"]
    )

    WIN = timedelta(days=13)
    scanned = imported = skipped = 0
    errors = []
    windows = []
    cur = sd
    while cur < ed:
        wend = min(cur + WIN, ed)
        s_ms, e_ms = int(cur.timestamp() * 1000), int(wend.timestamp() * 1000)
        w_scan = w_imp = w_skip = 0
        page = 0
        while True:
            try:
                resp = await client.get_orders(start_date_ms=s_ms, end_date_ms=e_ms, size=200, page=page)
            except Exception as e:
                errors.append({"window": cur.date().isoformat(), "page": page, "error": str(e)[:300]})
                break
            content = resp.get("content") or []
            for t_order in content:
                w_scan += 1
                onum = str(t_order.get("orderNumber") or "")
                if not onum:
                    continue
                if await db.orders.find_one({"order_number": onum, "platform": "trendyol"}, {"_id": 1}):
                    w_skip += 1
                    continue
                if dry:
                    w_imp += 1
                    continue
                try:
                    od = map_trendyol_order(t_order)
                    od["id"] = generate_id()
                    od["created_at"] = _ms_to_iso(t_order.get("orderDate")) or datetime.now(timezone.utc).isoformat()
                    od["backfill"] = True  # geçmiş veri kurtarma işareti (stok/bildirim akışına girmedi)
                    await db.orders.insert_one(od)
                    w_imp += 1
                except Exception as e:
                    errors.append({"orderNumber": onum, "error": str(e)[:300]})
            total_pages = resp.get("totalPages") or 0
            page += 1
            if not content or page >= total_pages:
                break
        windows.append({"start": cur.date().isoformat(), "end": (wend - timedelta(seconds=1)).date().isoformat(),
                        "scanned": w_scan, "imported": w_imp, "skipped_existing": w_skip})
        scanned += w_scan
        imported += w_imp
        skipped += w_skip
        cur = wend
    await log_integration_event("trendyol", "backfill_orders", "orders",
                                f"{payload.get('start_date')}..{payload.get('end_date')}",
                                "success", f"tarandı={scanned} aktarıldı={imported} atlandı={skipped} dry={dry}")
    return {"dry_run": dry, "scanned": scanned, "imported": imported,
            "skipped_existing": skipped, "windows": windows, "errors": errors[:20]}
@router.post("/trendyol/orders/import")
async def import_trendyol_orders(current_user: dict = Depends(require_admin)):
    """Import orders from Trendyol (Last 15 days) auto job"""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
    
    import sys
    import os
    import time
    from datetime import datetime, timezone
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    from .deps import generate_id
    
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )
    
    # Trendyol siparis ucu en fazla ~2 hafta (14 gun) araliga izin verir.
    now = datetime.now()
    start = now - timedelta(days=14)
    end_date_ms = int(now.timestamp() * 1000)
    start_date_ms = int(start.timestamp() * 1000)
    
    imported_count = 0
    updated_count = 0
    
    try:
        # TUM sayfalari dolas - daha once tek sayfa (200) cekilip fazlasi atlaniyordu.
        content = []
        page = 0
        MAX_PAGES = 50
        while page < MAX_PAGES:
            resp = await client.get_orders(
                start_date_ms=start_date_ms, end_date_ms=end_date_ms, size=200, page=page
            )
            chunk = resp.get("content", []) or []
            content.extend(chunk)
            total_pages = resp.get("totalPages") or 0
            page += 1
            if not chunk or page >= total_pages:
                break
        
        for t_order in content:
            order_number = t_order.get("orderNumber")
            existing_order = await db.orders.find_one({"order_number": str(order_number), "platform": "trendyol"})
            try:
                order_data = map_trendyol_order(t_order)
                if existing_order:
                    await db.orders.update_one(
                        {"_id": existing_order["_id"]},
                        {"$set": {k: v for k, v in order_data.items() if k != "status"}}
                    )
                    updated_count += 1
                else:
                    order_data["id"] = generate_id()
                    order_data["created_at"] = _ms_to_iso(t_order.get("orderDate")) or datetime.now(timezone.utc).isoformat()
                    await db.orders.insert_one(order_data)
                    imported_count += 1
                    await _decrement_stock_for_imported_order(order_data, "trendyol")
            except Exception as e:
                logger.error(f"Error mapping/saving order {order_number}: {e}")
                await log_integration_event("trendyol", "auto_import", "order", str(order_number), "error", f"Otomatik aktarım hatası: {str(e)}", {"raw": t_order})
        
        try:
            await _sync_trendyol_status_passes(client, start_date_ms, end_date_ms)
        except Exception as _e:
            logger.error(f"[trendyol status passes] {_e}")

        return {
            "success": True, 
            "message": f"Trendyol'dan {imported_count} yeni sipariş aktarıldı, {updated_count} sipariş güncellendi.",
            "imported": imported_count,
            "updated": updated_count
        }
    except Exception as e:
        logger.error(f"Error importing Trendyol orders: {str(e)}")
        await log_integration_event("trendyol", "auto_import_job", "system", "-", "error", f"Toplu aktarım hatası: {str(e)}")
        raise HTTPException(status_code=500, detail="Sipariş aktarımı sırasında bir hata oluştu.")
@router.get("/trendyol/category-mappings")
async def get_trendyol_category_mappings(current_user: dict = Depends(require_admin)):
    """Get all local categories with their Trendyol mappings, excluding hidden ones"""
    try:
        categories = await db.categories.find({"trendyol_hidden": {"$ne": True}}).to_list(1000)
        mappings = []
        for c in categories:
            mappings.append({
                "id": str(c["_id"]) if "_id" in c else c.get("id"),
                "local_name": c.get("name"),
                "trendyol_category_id": c.get("trendyol_category_id"),
                "trendyol_category_name": c.get("trendyol_category_name"),
                "attribute_mappings": c.get("attribute_mappings", []),
                "value_mappings": c.get("value_mappings", {}),
                "default_mappings": c.get("default_mappings", {}),
                "has_children": c.get("has_children", c.get("children_count", 0) > 0),
                "is_matched": bool(c.get("trendyol_category_id"))
            })
        return {"success": True, "mappings": mappings}
    except Exception as e:
        logger.error(f"Error fetching category mappings: {e}")
        raise HTTPException(status_code=500, detail="Kategori eşleştirmeleri alınamadı.")
@router.post("/trendyol/category-mappings")
async def save_trendyol_category_mapping(req: CategoryMappingReq, current_user: dict = Depends(require_admin)):
    """Save a Trendyol category mapping to a local category"""
    from bson.objectid import ObjectId
    try:
        # Support both string UUID and ObjectId
        filter_q = {"id": req.local_category_id} if len(req.local_category_id) > 24 else {"$or": [{"id": req.local_category_id}, {"_id": ObjectId(req.local_category_id)}]}
        
        await db.categories.update_one(
            filter_q,
            {"$set": {
                "trendyol_category_id": req.trendyol_category_id,
                "trendyol_category_name": req.trendyol_category_name
            }}
        )
        return {"success": True, "message": "Eşleştirme kaydedildi"}
    except Exception as e:
        logger.error(f"Error saving category mapping: {e}")
        raise HTTPException(status_code=500, detail="Eşleştirme kaydedilemedi.")
@router.post("/trendyol/category-mappings/{local_category_id}/value-mappings")
async def save_trendyol_category_value_mappings(local_category_id: str, req: Request, current_user: dict = Depends(require_admin)):
    payload = await req.json()
    value_mappings = payload.get("value_mappings", {})
    from bson.objectid import ObjectId
    filter_q = {"id": local_category_id} if len(local_category_id) > 24 else {"$or": [{"id": local_category_id}, {"_id": ObjectId(local_category_id)}]}
    
    await db.categories.update_one(
        filter_q,
        {"$set": {"value_mappings": value_mappings}}
    )
    return {"success": True}


@router.post("/trendyol/value-mappings/merge")
async def merge_trendyol_value_mappings(req: Request, current_user: dict = Depends(require_admin)):
    """Aktarım-doğrulama ekranından SEÇİLEN değer eşleştirmelerini KADEMELİ (merge) kaydeder.
    Doğrulama VE sync `db.category_mappings` (marketplace=trendyol, category_id=<yerel>) dokümanının
    `value_mappings` alanını okur — eski uç yanlışlıkla `db.categories`'e yazıyordu, bu yüzden
    eşleştirmeler hiç etki etmiyordu. Bu uç DOĞRU koleksiyona yazar.
    body: {category_id, mappings:[{mp_attr_id, local_value, value_id}]}
      value_id rakamsa Trendyol value_id; değilse serbest metin (custom) olarak gider.
    Anahtar formatı sync ile aynı: "<mp_attr_id>|<local_value>"."""
    payload = await req.json()
    local_cat = str(payload.get("category_id") or "").strip()
    mappings = payload.get("mappings") or []
    if not local_cat:
        raise HTTPException(status_code=400, detail="category_id gerekli")
    if not isinstance(mappings, list) or not mappings:
        raise HTTPException(status_code=400, detail="mappings boş")

    set_ops = {}
    for m in mappings:
        aid = str(m.get("mp_attr_id") or "").strip()
        lval = str(m.get("local_value") or "").strip()
        vid = str(m.get("value_id") or "").strip()
        if not aid or not lval or not vid:
            continue
        # $set ile tek tek anahtar yaz → mevcut eşleştirmeler korunur (merge)
        set_ops[f"value_mappings.{aid}|{lval}"] = vid

    if not set_ops:
        raise HTTPException(status_code=400, detail="Geçerli eşleştirme yok")

    res = await db.category_mappings.update_one(
        {"marketplace": "trendyol", "category_id": local_cat},
        {"$set": set_ops,
         "$setOnInsert": {"marketplace": "trendyol", "category_id": local_cat}},
        upsert=True,
    )
    return {"success": True, "saved": len(set_ops),
            "matched": res.matched_count, "upserted": bool(res.upserted_id)}


@router.post("/trendyol/products/{product_id}/remove-attribute")
async def remove_product_attribute(product_id: str, req: Request, current_user: dict = Depends(require_admin)):
    """Ürün özelliğini (ör. Trendyol'da karşılığı olmayan 'Kapama Şekli: Fermuarlı') ÜRÜNDEN kaldırır.
    body: {attr_name}. products.attributes içindeki eşleşen tip(ler) silinir; varyant attributes'ta da temizlenir."""
    payload = await req.json()
    attr_name = str(payload.get("attr_name") or "").strip()
    if not attr_name:
        raise HTTPException(status_code=400, detail="attr_name gerekli")
    prod = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not prod:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    def _norm(s):
        return _normalize_attr_key(str(s or ""))
    tgt = _norm(attr_name)

    new_attrs = [a for a in (prod.get("attributes") or [])
                 if _norm(a.get("type") or a.get("name") or a.get("attribute_name")) != tgt]
    new_variants = []
    for v in (prod.get("variants") or []):
        if isinstance(v.get("attributes"), list):
            v = {**v, "attributes": [a for a in v["attributes"]
                                     if _norm(a.get("type") or a.get("name") or a.get("attribute_name")) != tgt]}
        elif isinstance(v.get("attributes"), dict):
            v = {**v, "attributes": {k: val for k, val in v["attributes"].items() if _norm(k) != tgt}}
        new_variants.append(v)

    await db.products.update_one(
        {"id": product_id},
        {"$set": {"attributes": new_attrs, "variants": new_variants}},
    )
    return {"success": True, "removed_attr": attr_name}


@router.get("/trendyol/category-values/{local_category_id}")
async def get_local_category_values(local_category_id: str, current_user: dict = Depends(require_admin)):
    from bson.objectid import ObjectId
    import re
    
    filter_q = {"id": local_category_id} if len(local_category_id) > 24 else {"$or": [{"id": local_category_id}, {"_id": ObjectId(local_category_id)}]}
    category = await db.categories.find_one(filter_q)
    
    if not category:
         raise HTTPException(status_code=404, detail="Kategori bulunamadı")
         
    # Case-insensitive category name search
    name = category.get("name")
    cat_id = category.get("id") or str(category.get("_id"))
    
    query = {
        "$or": [
            {"category_name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}},
            {"category_id": cat_id}
        ]
    }
    
    products = await db.products.find(query).to_list(None)
    
    val_map = {
        "Renk": set(),
        "Beden": set(),
        "Boy": set()
    }
    
    for p in products:
        # Pull from variants (standard for clothing)
        for v in p.get("variants", []):
            if v.get("color"):
                c = str(v["color"]).strip()
                if c and c.lower() != "none":
                    val_map["Renk"].add(c)
            if v.get("size"): 
                s = str(v["size"]).strip()
                if s and s.lower() != "none":
                    val_map["Beden"].add(s)
        
        # Pull from attributes array (from CSV imports or manual entry)
        for a in p.get("attributes", []):
            t = str(a.get("type", "")).strip()
            val = str(a.get("value", "")).strip()
            if t and val and val.lower() != "none":
                if t not in val_map:
                    val_map[t] = set()
                val_map[t].add(val)
                
    result = []
    for k, v in val_map.items():
        if v:
            # Sort values naturally if possible
            sorted_vals = sorted(list(v))
            result.append({"attribute_name": k, "values": sorted_vals})
            
    return {"success": True, "local_values": result}
@router.delete("/trendyol/category-mappings/{local_category_id}")
async def delete_trendyol_category_mapping(local_category_id: str, current_user: dict = Depends(require_admin)):
    """Hide a category from the Trendyol mappings list and clear its mapping"""
    from bson.objectid import ObjectId
    try:
        filter_q = {"id": local_category_id} if len(local_category_id) > 24 else {"$or": [{"id": local_category_id}, {"_id": ObjectId(local_category_id)}]}
        
        await db.categories.update_one(
            filter_q,
            {
                "$unset": {
                    "trendyol_category_id": "",
                    "trendyol_category_name": "",
                    "attribute_mappings": ""
                },
                "$set": {
                    "trendyol_hidden": True
                }
            }
        )
        return {"success": True, "message": "Kategori listeden kaldırıldı ve eşleştirmesi silindi"}
    except Exception as e:
        logger.error(f"Error hiding category mapping: {e}")
        raise HTTPException(status_code=500, detail="Eşleştirme silinemedi.")
@router.post("/trendyol/category-mappings/bulk-delete")
async def bulk_delete_trendyol_category_mappings(req: Request, current_user: dict = Depends(require_admin)):
    payload = await req.json()
    category_ids = payload.get("category_ids", [])
    if not category_ids:
        return {"success": True}
    from bson.objectid import ObjectId
    try:
        flat_filters = []
        for cid in category_ids:
            cid_str = str(cid)
            flat_filters.append({"id": cid_str})
            if len(cid_str) <= 24:
                try:
                    flat_filters.append({"_id": ObjectId(cid_str)})
                except Exception:
                    pass

        if not flat_filters:
            return {"success": True}

        await db.categories.update_many(
            {"$or": flat_filters},
            {
                "$unset": {
                    "trendyol_category_id": "",
                    "trendyol_category_name": "",
                    "attribute_mappings": ""
                },
                "$set": {
                    "trendyol_hidden": True
                }
            }
        )
        return {"success": True, "message": "Seçili kategoriler kaldırıldı"}
    except Exception as e:
        logger.error(f"Error bulk hiding categories: {e}")
        raise HTTPException(status_code=500, detail="Kategoriler silinemedi.")
@router.post("/trendyol/category-mappings/{local_category_id}/attributes")
async def save_trendyol_attribute_mapping(local_category_id: str, req: AttributeMappingReq, current_user: dict = Depends(require_admin)):
    from bson.objectid import ObjectId
    try:
        filter_q = {"id": local_category_id} if len(local_category_id) > 24 else {"$or": [{"id": local_category_id}, {"_id": ObjectId(local_category_id)}]}
        
        mappings = [{"local_attr": m.local_attr, "trendyol_attr_id": m.trendyol_attr_id} for m in req.attribute_mappings]
        
        await db.categories.update_one(
            filter_q,
            {"$set": {
                "attribute_mappings": mappings,
                "default_mappings": req.default_mappings
            }}
        )
        return {"success": True, "message": "Özellik eşleştirmeleri kaydedildi"}
    except Exception as e:
        logger.error(f"Error saving attribute mapping: {e}")
        raise HTTPException(status_code=500, detail="Özellik eşleştirmeleri kaydedilemedi.")
@router.get("/trendyol/categories")
async def get_local_trendyol_categories(current_user: dict = Depends(require_admin)):
    """Fetch previously downloaded Trendyol categories for UI lists"""
    try:
        categories = await db.trendyol_categories.find({}, {"_id": 0, "id": 1, "name": 1, "subCategories": 1}).to_list(1000)
        # Flatten simple list for datalist mapping
        def flatten(cats, parent_name=""):
            result = []
            for c in cats:
                full_name = f"{parent_name} > {c['name']}" if parent_name else c["name"]
                result.append({"id": c["id"], "name": full_name})
                if c.get("subCategories"):
                    result.extend(flatten(c["subCategories"], full_name))
            return result
        flat_list = flatten(categories)
        return {"success": True, "categories": flat_list}
    except Exception as e:
        logger.error(f"Error fetching trendyol categories from db: {e}")
        return {"success": False, "categories": []}
@router.get("/trendyol/orders/label/{cargo_tracking_number}")
async def get_trendyol_cargo_label(cargo_tracking_number: str, current_user: dict = Depends(require_admin)):
    """Fetch PDF Cargo label from Trendyol"""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
        
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from trendyol_client import TrendyolClient
    from fastapi.responses import Response
    
    try:
        client = TrendyolClient(
            supplier_id=config["supplier_id"],
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            mode=config["mode"]
        )
        pdf_bytes = await client.get_cargo_label(cargo_tracking_number)
        return Response(content=pdf_bytes, media_type="application/pdf")
    except Exception as e:
        logger.error(f"Error fetching Trendyol cargo label: {str(e)}")
        raise HTTPException(status_code=500, detail="Kargo etiketi alınırken hata oluştu.")


def _claim_activity_key(c: dict) -> str:
    """İade satırının EN GÜNCEL hareket tarihi (sıralama anahtarı).
    Talep tarihi + onay/ret + son değişiklik alanlarının en yenisini döndürür ki
    site/Trendyol/HB fark etmeksizin liste 'en yeni hareket en üstte' sıralanabilsin.
    ISO string karşılaştırması (hepsi ISO olduğundan max güvenli)."""
    cands = [
        c.get("created_date"), c.get("updated_at"), c.get("updated_date"),
        c.get("return_approved_at"), c.get("return_rejected_at"),
        c.get("last_modified_date"), c.get("lastModifiedDate"),
    ]
    vals = [str(x) for x in cands if x]
    return max(vals) if vals else ""


def _claim_dup_signature(c: dict):
    """AYNI iadeyi temsil eden çift kayıtları yakalamak için içerik imzası.
    Sipariş no + tip + kalem (barkod, adet) kümesi + iade tutarı aynıysa bu kayıtlar
    fiilen aynı iadedir (tekrar senkron/çift yazım). Farklı kalem/tutar → farklı iade
    (müşteri gerçekten birden çok kez iade etmiş) → ayrı kalır. order_number yoksa
    imza None (tekilleştirme uygulanmaz)."""
    onum = str(c.get("order_number") or "").strip()
    if not onum:
        return None
    plt = str(c.get("platform") or "").lower()
    ctype = str(c.get("claim_type") or "RETURN")
    items = c.get("items") or []
    sig_items = tuple(sorted(
        (str(i.get("barcode") or i.get("merchantSku") or i.get("productName") or i.get("product_name") or ""),
         int(i.get("quantity") or 1))
        for i in items
    ))
    refund = round(float(c.get("refund_amount") or 0), 2)
    return (plt, onum, ctype, sig_items, refund)


def _group_hb_claims_by_order(rows: list) -> list:
    """Hepsiburada'nın KALEM-BAZLI claim'lerini sipariş no'ya göre TEK satırda birleştirir.

    HB, 3 kalemli tek bir iadeyi 3 ayrı claim (ayrı claim_id) olarak açar → ekranda 3 satır
    görünüp 'müşteri 3 kez mi iade etti' karışıklığı olur. Bu fonksiyon aynı siparişin HB
    claim'lerini tek satırda toplar: kalemler birleşir, iade tutarı toplanır, TÜM claim_id'ler
    `merged_claim_ids`'te tutulur (gider pusulası/durum işlemleri bu satırda 3 claim'e birden
    uygulanır — bkz. generate_gider_pusulasi / set-status fan-out). Trendyol satırları, manuel
    satırlar ve tek-claim'li HB siparişleri DEĞİŞMEDEN geçer."""
    out, hb_groups = [], {}
    for c in rows:
        if (str(c.get("platform") or "").lower() == "hepsiburada"
                and c.get("order_number") and not c.get("manual")):
            hb_groups.setdefault(str(c.get("order_number")), []).append(c)
        else:
            out.append(c)
    for onum, grp in hb_groups.items():
        if len(grp) == 1:
            out.append(grp[0]); continue
        grp_sorted = sorted(grp, key=lambda c: (c.get("created_date") or ""), reverse=True)
        primary = dict(grp_sorted[0])
        items, refund, cids = [], 0.0, []
        for c in grp_sorted:
            items.extend(c.get("items") or [])
            refund += float(c.get("refund_amount") or 0)
            if c.get("claim_id"):
                cids.append(c.get("claim_id"))
        primary["items"] = items
        primary["refund_amount"] = round(refund, 2)
        primary["merged_claim_ids"] = cids
        primary["merged_count"] = len(grp)
        out.append(primary)
    return out


def _dedup_claims_by_content(claims: list) -> list:
    """claim_id tekilleştirmesinden SONRA, içerik imzası aynı olan çift kayıtları
    ayıklar. Giriş created_date'e göre yeniden→eskiye sıralı olduğundan ilk (en yeni)
    kayıt tutulur. Böylece bir sipariş no yanlışlıkla 2-3 kez görünmez; gerçekten
    farklı iadeler (farklı kalem/tutar) ayrı kalır."""
    seen_sig = set()
    seen_claim_ids = set()
    out = []
    for c in claims:
        cid = str(c.get("claim_id") or "").strip()
        if cid:
            # GERÇEK claim_id benzersizdir (Trendyol/HB her iadeye ayrı id verir). İçerik imzası
            # aynı olsa bile FARKLI claim_id'ler AYRI iadelerdir — aynı üründen 2 adet / 2 ayrı
            # iade birbirini SİLMEZ. Yalnızca AYNI claim_id tekrarı elenir. İmza da işaretlenir ki
            # claim_id'siz türetilmiş (order-derived) kopyası varsa o elensin.
            if cid in seen_claim_ids:
                continue
            seen_claim_ids.add(cid)
            sig = _claim_dup_signature(c)
            if sig is not None:
                seen_sig.add(sig)
            out.append(c)
            continue
        # claim_id yok (türetilmiş/manuel) → içerik imzasıyla tekilleştir
        sig = _claim_dup_signature(c)
        if sig is not None:
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
        out.append(c)
    return out


async def _order_derived_trendyol_returns(search: str = "", claim_type: str = "",
                                          exclude_order_numbers=None):
    """İade durumundaki Trendyol siparişlerinden — senkron claim'i OLMAYANLARI —
    iade satırına çevirir. Tamamen read-side; satırlar `manual=True` işaretlenir."""
    if claim_type == "CANCEL":
        return []  # manuel iade satırları RETURN tipidir; iptal sekmesine girmez
    exclude = exclude_order_numbers or set()
    q = {"platform": "trendyol", "status": {"$in": _RETURN_STATUS_KEYS}}
    proj = {
        "_id": 0, "id": 1, "order_number": 1, "status": 1, "items": 1,
        "shipping_address": 1, "billing_address": 1, "customer_name": 1, "full_name": 1,
        "total": 1, "subtotal": 1, "invoice_number": 1, "created_at": 1, "updated_at": 1,
        "payment_method": 1, "return_request": 1, "cargo_tracking_number": 1,
        "cargo_provider_name": 1, "manual_return": 1, "manual_return_at": 1,
    }
    _rx = re.compile(_search_tr_regex(search.strip()), re.IGNORECASE) if search else None
    out = []
    async for o in db.orders.find(q, proj).sort("updated_at", -1):
        onum = str(o.get("order_number") or "")
        # MANUEL ÖNCELİK (Kadir kuralı): admin bir siparişi elle iade durumuna çektiyse
        # (manual_return=true) o satır claim'i ne olursa olsun HER ZAMAN gösterilir ve
        # varsa gerçek claim'i EZER (aşağıda get_trendyol_claims o claim'i düşürür).
        # Yalnızca elle-çekilmemiş siparişlerde 'senkron claim'i zaten var → tekrarlama'
        # kuralı geçerli.
        if onum and onum in exclude and not o.get("manual_return"):
            continue
        addr = o.get("shipping_address") or {}
        bill = o.get("billing_address") or {}
        name = (" ".join([addr.get("first_name") or "", addr.get("last_name") or ""]).strip()
                or addr.get("full_name") or o.get("customer_name") or o.get("full_name")
                or " ".join([bill.get("first_name") or "", bill.get("last_name") or ""]).strip()
                or "—")
        st = o.get("status") or ""
        rr = o.get("return_request") or {}
        items = []
        for it in (o.get("items") or []):
            up = float(it.get("unit_price") or it.get("list_price") or it.get("price") or 0)
            pr = float(it.get("price") or up or 0)
            dc = float(it.get("discount_amount") or it.get("discount") or 0)
            _nm = it.get("name") or it.get("product_name") or "Ürün"
            items.append({
                "claim_item_id": "", "productName": _nm, "product_name": _nm,
                "barcode": it.get("barcode") or it.get("product_id") or it.get("sku") or "",
                "size": it.get("size", ""), "color": it.get("color", ""),
                "quantity": int(it.get("quantity", 1) or 1),
                "unit_price": up, "price": pr, "discount_amount": dc, "reason": "",
            })
        net = float(o.get("total") or 0) or sum(i["price"] for i in items)
        row = {
            "claim_id": "ord:" + str(o.get("id") or onum),
            "order_id": o.get("id"), "order_number": onum,
            "claim_type": "RETURN", "claim_reason": rr.get("reason") or "",
            "claim_status": st, "order_status": st,
            "manual": True, "source": "order_status",
            "customer_name": name,
            # Sıralama tarihi: elle iade işlemi tarihi (manual_return_at) öncelikli ki
            # yeni çekilen manuel iade listenin EN ÜSTÜNde çıksın (eski sipariş tarihiyle
            # sayfa sonuna düşüp "kaybolmasın").
            "created_date": o.get("manual_return_at") or o.get("updated_at") or o.get("created_at") or "",
            "items": items, "refund_amount": net,
            "invoice_number": str(o.get("invoice_number") or ""),
            "cargo_tracking_number": str(o.get("cargo_tracking_number") or ""),
            "cargo_provider_name": o.get("cargo_provider_name") or "",
            "payment_type": _order_payment_type(o.get("payment_method")),
        }
        if _rx is not None:
            hay = " ".join([onum, name, row["invoice_number"], row["cargo_tracking_number"],
                            " ".join(i["productName"] for i in items)])
            if not _rx.search(hay):
                continue
        out.append(row)
    # GP bilgisini ekle: bu siparişlere ait customer_returns köprü kaydı varsa
    # (gider pusulası kesildiyse) numarayı/işaretini satıra taşı (tek toplu sorgu).
    _oids = [r["order_id"] for r in out if r.get("order_id")]
    if _oids:
        _gp = {}
        async for cr in db.customer_returns.find(
            {"order_id": {"$in": _oids}},
            {"_id": 0, "order_id": 1, "id": 1, "has_gider_pusulasi": 1, "gider_pusulasi_no": 1}
        ).sort("created_at", -1):
            _gp.setdefault(cr.get("order_id"), cr)
        for r in out:
            _c = _gp.get(r.get("order_id"))
            if _c:
                r["return_id"] = _c.get("id")
                r["has_gider_pusulasi"] = bool(_c.get("has_gider_pusulasi"))
                r["gider_pusulasi_no"] = _c.get("gider_pusulasi_no") or ""
    return out
async def _sync_trendyol_claims_core(days_back: int = 1095):
    """Trendyol claims çekirdek senkron — endpoint + scheduler ortak kullanır.

    days_back: geçmiş tarama penceresi (ilk backfill 1095=3yıl; scheduler kısa pencere).
    """
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    from trendyol_client import TrendyolClient

    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    end_date = datetime.now(timezone.utc)
    total_synced = 0
    order_cache = {}  # order_number -> order_data cache

    # Trendyol max 15 günlük aralık destekliyor, parçalıyoruz
    chunk_days = 15
    current_end = end_date

    while True:
        current_start = current_end - timedelta(days=chunk_days)
        days_elapsed = (end_date - current_start).days

        if days_elapsed > days_back:
            current_start = end_date - timedelta(days=days_back)

        start_ts = int(current_start.timestamp() * 1000)
        end_ts = int(current_end.timestamp() * 1000)

        current_page = 0
        page_size = 200

        while True:
            try:
                url = f"{client.base_url}/order/sellers/{client.supplier_id}/claims"
                params = {
                    "page": current_page,
                    "size": page_size,
                    "startDate": start_ts,
                    "endDate": end_ts,
                }
                async with httpx.AsyncClient(timeout=30.0) as http_client:
                    headers = client._get_headers()
                    response = await http_client.get(url, headers=headers, params=params)
                    response.raise_for_status()
                    result = response.json()
            except Exception as e:
                logger.error(f"Claims sync error: {str(e)}")
                break

            data = result if isinstance(result, dict) else {}
            content = data.get("content", [])
            total_pages = data.get("totalPages", 0)

            if not content:
                break

            for claim in content:
                claim_id = str(claim.get("claimId", claim.get("id", "")))
                if not claim_id:
                    continue

                # Mevcut claim: pahalı order API'sini TEKRAR ÇEKME (iskonto zaten kayıtlı),
                # ama DURUM/kargo/tarih alanlarını canlı tazele — durum geçişleri (Created→
                # Accepted/Rejected) yansısın. Trendyol kayıtları lastModifiedDate sırasıyla gelir.
                existing_claim = await db.trendyol_claims.find_one(
                    {"claim_id": claim_id},
                    {"_id": 1, "return_approved_at": 1, "return_rejected_at": 1, "manual_locked": 1}
                )
                if existing_claim:
                    _new_status = _derive_claim_status(claim)
                    _lm = claim.get("lastModifiedDate")
                    _lm_iso = ""
                    if _lm:
                        try:
                            _lm_iso = datetime.fromtimestamp(_lm / 1000, tz=timezone.utc).isoformat()
                        except Exception:
                            _lm_iso = ""
                    _now_iso = datetime.now(timezone.utc).isoformat()
                    _set = {
                        "cargo_tracking_number": str(claim.get("cargoTrackingNumber", "")),
                        "cargo_provider_name": claim.get("cargoProviderName", ""),
                        "raw_data": claim,
                        "updated_at": _now_iso,
                    }
                    # MANUEL KİLİT: admin durumu elle ilerlettiyse (manual_locked) Trendyol senkronu
                    # claim_status'u EZMEZ — yalnız kargo/raw bilgisi tazelenir, durum manuel kalır.
                    if not existing_claim.get("manual_locked"):
                        _set["claim_status"] = _new_status
                        # Durum geçiş tarih damgaları (idempotent — yalnız ilk geçişte yazılır)
                        if _new_status == "Accepted" and not existing_claim.get("return_approved_at"):
                            _set["return_approved_at"] = _lm_iso or _now_iso
                        if _new_status in ("Rejected", "Cancelled") and not existing_claim.get("return_rejected_at"):
                            _set["return_rejected_at"] = _lm_iso or _now_iso
                    await db.trendyol_claims.update_one({"claim_id": claim_id}, {"$set": _set})
                    total_synced += 1
                    continue

                # Claim items'dan tip ve sebep çıkar
                claim_items = []
                claim_type = ""
                claim_reason = ""
                refund_amount = 0
                
                # Claims API'sinde iskonto bilgisi yok. Sipariş API'sinden çek.
                order_number = str(claim.get("orderNumber", ""))
                order_discount_map = {}  # barcode -> {discount, gross_price, net_price}
                if order_number:
                    # Cache kontrolü: aynı sipariş numarasını tekrar çekme
                    cache_key = f"order_{order_number}"
                    if cache_key not in order_cache:
                        try:
                            order_data = await client.get_orders(order_number=order_number)
                            order_cache[cache_key] = order_data
                        except Exception as e:
                            logger.warning(f"Could not fetch order {order_number} for discount: {e}")
                            order_cache[cache_key] = {}
                    
                    cached = order_cache.get(cache_key, {})
                    # BİRİM fiyat haritası (adede bölme YOK, iptal paket gerçek satırı ezmez).
                    # Eski /qty bölmesi qty>1 satırlarda tutarı yarıya düşürüyordu (11419198311).
                    order_discount_map = _build_order_unit_price_map(cached)

                for item in claim.get("items", []):
                    order_line = item.get("orderLine", {})
                    for ci in item.get("claimItems", []):
                        reason_info = ci.get("customerClaimItemReason", {})
                        if not claim_type:
                            code = reason_info.get("code", "").upper()
                            if code in ["ABANDON", "UNDELIVERED", "NOTDELIVERED"]:
                                claim_type = "CANCEL"
                            else:
                                claim_type = "RETURN"
                        if not claim_reason:
                            claim_reason = reason_info.get("name", "")

                        barcode = order_line.get("barcode", "")
                        claim_price = order_line.get("price", 0)
                        
                        # İskontoyu sipariş verisinden al
                        order_info = order_discount_map.get(barcode, {})
                        if order_info:
                            gross_price = order_info.get("gross", claim_price)
                            net_price = order_info.get("net", claim_price)
                            discount = order_info.get("discount", 0)
                        else:
                            # Fallback: Claims API verisini kullan (iskonto yok)
                            gross_price = claim_price
                            net_price = claim_price
                            discount = 0
                        
                        claim_items.append({
                            "claim_item_id": str(ci.get("id", "")),
                            "productName": order_line.get("productName", ""),
                            "barcode": barcode,
                            "unit_price": gross_price,
                            "discount_amount": discount,
                            "price": net_price,
                            "quantity": 1,
                            "reason": reason_info.get("name", "")
                        })
                        refund_amount += net_price

                # Tarih formatı
                claim_date = claim.get("claimDate")
                created_date_str = ""
                if claim_date:
                    try:
                        created_date_str = datetime.fromtimestamp(claim_date / 1000, tz=timezone.utc).isoformat()
                    except Exception:
                        created_date_str = str(claim_date)

                # Fatura numarasını çıkar: sipariş verisinden veya claim'den
                invoice_number = ""
                for item in claim.get("items", []):
                    ol = item.get("orderLine", {})
                    inv = ol.get("invoiceNumber", "") or item.get("invoiceNumber", "")
                    if inv:
                        invoice_number = str(inv)
                        break
                if not invoice_number:
                    invoice_number = str(claim.get("invoiceNumber", "") or "")
                # Sipariş verisinden fatura no çek
                if not invoice_number and order_discount_map:
                    try:
                        _order_data = await client.get_orders(order_number=order_number)
                        for pkg in _order_data.get("content", []):
                            inv_no = pkg.get("invoiceNumber", "")
                            if inv_no:
                                invoice_number = str(inv_no)
                                break
                    except Exception:
                        pass

                claim_doc = {
                    "claim_id": claim_id,
                    "order_number": order_number,
                    "claim_type": claim_type,
                    "claim_reason": claim_reason,
                    "claim_status": _derive_claim_status(claim),
                    **_first_seen_stamps(claim),
                    "customer_name": f"{claim.get('customerFirstName', '')} {claim.get('customerLastName', '')}".strip(),
                    "created_date": created_date_str,
                    "items": claim_items,
                    "refund_amount": refund_amount,
                    "invoice_number": invoice_number,
                    "invoice_link": claim.get("invoiceLink", ""), # Yeni eklendi
                    "cargo_tracking_number": str(claim.get("cargoTrackingNumber", "")),
                    "cargo_provider_name": claim.get("cargoProviderName", ""),
                    "raw_data": claim,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

                # MANUEL TUTAR KORUMASI: bir iade elle düzeltildiyse (set-amount → amount_overridden)
                # periyodik senkron tutarı/kalemleri Trendyol değeriyle EZMESİN — yoksa "dün
                # düzelttin ama yine yanlış" olur. Sadece o iki alan korunur; durum/kargo tazelenir.
                _existing = await db.trendyol_claims.find_one(
                    {"claim_id": claim_id}, {"_id": 0, "amount_overridden": 1})
                if _existing and _existing.get("amount_overridden"):
                    claim_doc.pop("refund_amount", None)
                    claim_doc.pop("items", None)

                await db.trendyol_claims.update_one(
                    {"claim_id": claim_id},
                    {"$set": claim_doc, "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat()}},
                    upsert=True
                )
                total_synced += 1

                # Claim sebebini eşleşen İPTAL siparişine bağla. claim_type sınıflandırması
                # (CANCEL/RETURN) Trendyol sebep koduna göre dar kalabildiğinden ONA GÜVENMEYİZ:
                # sipariş zaten status=cancelled ise (iade post-teslimat olduğundan iptalle
                # karışmaz) o claim'in sebebi = iptal sebebidir. Böylece "Stok tükendi",
                # "Adreste bulunmayacağım" gibi gerçek sebepler İptaller'e yazılır.
                if claim_reason and order_number:
                    try:
                        await db.orders.update_one(
                            {"order_number": str(order_number), "platform": "trendyol", "status": "cancelled"},
                            {"$set": {"cancel_reason": claim_reason, "cancel_source": "trendyol",
                                      "updated_at": datetime.now(timezone.utc).isoformat()}},
                        )
                    except Exception as _ce:
                        logger.error(f"[trendyol claim->order reason {order_number}] {_ce}")

                # DENETİM HATA-1: onaylanmış İADE (Accepted, iptal-dışı) siparişe yansıtılır —
                # aksi halde parası iade edilen sipariş raporlarda "satış" kalır (net ciro şişer).
                # Yalnız statü + iz alanları; STOK/kupon/ödeme alanlarına DOKUNULMAZ (değişmez #6).
                if order_number and claim_doc.get("claim_status") == "Accepted" and claim_type != "CANCEL":
                    try:
                        _now2 = datetime.now(timezone.utc).isoformat()
                        await db.orders.update_one(
                            {"order_number": str(order_number),
                             "$or": [{"platform": "trendyol"}, {"marketplace": "trendyol"}],
                             "status": {"$nin": _ORDER_EXCLUDED_FOR_RETURN}},
                            {"$set": {"status": "returned",
                                      "returned_at": claim_doc.get("accepted_at") or _now2,
                                      "return_source": "trendyol_claim",
                                      "return_claim_id": claim_id,
                                      "updated_at": _now2},
                             "$push": {"status_history": {"status": "returned", "at": _now2,
                                                          "by": "claims-sync",
                                                          "note": f"Trendyol iadesi onaylı (claim {claim_id})"}}})
                    except Exception as _re:
                        logger.error(f"[trendyol claim->order return {order_number}] {_re}")

            current_page += 1
            if current_page >= total_pages:
                break

        current_end = current_start
        if days_elapsed >= days_back:
            break

    return {
        "message": f"Son {days_back} gündeki toplam {total_synced} iade/iptal kaydı senkronize edildi",
        "total_synced": total_synced,
        "days_back": days_back
    }
@router.get("/trendyol/claims/apply-returns-to-orders")
async def apply_returns_to_orders_endpoint(
    dry_run: bool = True,
    platform: str = "",
    current_user: dict = Depends(require_admin),
):
    """DENETİM HATA-1 geçmiş onarımı: onaylanmış pazaryeri iadelerini siparişlere yansıtır.
    dry_run=true (varsayılan) yalnız sayar, YAZMAZ. Uygulamak için ?dry_run=false."""
    plats = [platform.strip().lower()] if platform.strip() else None
    return await apply_accepted_claims_to_orders(platforms=plats, dry_run=dry_run)


# ── KESİN DOĞRULAMA (MUTABAKAT) ───────────────────────────────────────────────
# Trendyol'un kendi sipariş listesiyle bizim panelin SİPARİŞ / ADET / TUTAR bazında
# birebir karşılaştırması. "Trendyol 1.700 diyor, bizde 1.560 görünüyor" tipi soruyu
# tahminle değil ham veriyle kapatır: 1.700 = ADET, 1.560 = SİPARİŞ.
_TY_DEAD_CANCEL = ("Cancelled", "UnSupplied")


def _ty_pkg_status(pkg: dict) -> str:
    return str(pkg.get("shipmentPackageStatus") or pkg.get("status") or "")


def _ty_pkg_totals(pkg: dict) -> tuple:
    """Bir Trendyol paketinin (adet, net tutar) toplamı. Satır alanları BİRİM'dir
    (bkz. map_trendyol_order); 'price' = indirim düşülmüş birim fiyat."""
    units = 0
    amount = 0.0
    for ln in (pkg.get("lines") or []):
        try:
            q = max(1, int(ln.get("quantity") or 1))
        except Exception:
            q = 1
        try:
            p = float(ln.get("price") if ln.get("price") is not None else ln.get("lineUnitPrice") or 0)
        except Exception:
            p = 0.0
        units += q
        amount += p * q
    return units, round(amount, 2)


async def _ty_fetch_orders_range(client, start_ms: int, end_ms: int) -> dict:
    """Aralıktaki TÜM Trendyol paketlerini sipariş numarasına göre toplar.
    Trendyol orders ucu ~14 günlük pencereye izin verdiğinden aralık bölünür."""
    _WIN = 14 * 24 * 3600 * 1000
    by_order: dict = {}
    win_s = start_ms
    while win_s < end_ms:
        win_e = min(win_s + _WIN, end_ms)
        page = 0
        guard = 0
        while guard < 400:
            guard += 1
            resp = await client.get_orders(start_date_ms=win_s, end_date_ms=win_e,
                                           size=200, page=page)
            chunk = resp.get("content", []) or []
            for pkg in chunk:
                onum = str(pkg.get("orderNumber") or "")
                if not onum:
                    continue
                pid = str(pkg.get("id") or "")
                d = by_order.setdefault(onum, {
                    "order_date": pkg.get("orderDate"), "packages": {},
                })
                # Aynı paket birden çok sayfada gelebilir → id ile tekilleştir.
                d["packages"][pid] = pkg
                if pkg.get("orderDate") and (not d.get("order_date") or pkg["orderDate"] < d["order_date"]):
                    d["order_date"] = pkg["orderDate"]
            total_pages = int(resp.get("totalPages") or 1)
            page += 1
            if page >= total_pages or not chunk:
                break
        win_s = win_e
    # Paket sözlüğünü özete indir
    out = {}
    for onum, d in by_order.items():
        u_c = a_c = 0
        u_a = 0
        a_a = 0.0
        for pkg in d["packages"].values():
            u, a = _ty_pkg_totals(pkg)
            if _ty_pkg_status(pkg) in _TY_DEAD_CANCEL:
                u_c += u
                a_c += a
            else:
                u_a += u
                a_a += a
        out[onum] = {
            "order_date": d["order_date"],
            "units_cancelled": u_c, "amount_cancelled": round(float(a_c), 2),
            "units_active": u_a, "amount_active": round(a_a, 2),
            "units": u_c + u_a, "amount": round(float(a_c) + a_a, 2),
            "fully_cancelled": (u_a == 0 and u_c > 0),
            "partially_cancelled": (u_a > 0 and u_c > 0),
        }
    return out


@router.get("/trendyol/reconcile")
async def trendyol_reconcile(
    start_date: str = Query(..., description="YYYY-MM-DD — TR yerel gün (dahil)"),
    end_date: str = Query(..., description="YYYY-MM-DD — TR yerel gün (dahil)"),
    apply: bool = Query(False, description="true → tespit edilen İPTAL farklarını DÜZELTİR"),
    list_limit: int = Query(300, ge=0, le=3000, description="Dönen sipariş no listesi üst sınırı"),
    current_user: dict = Depends(require_admin),
):
    """Trendyol ↔ panel KESİN MUTABAKAT (sipariş no bazında).

    Karşılaştırır: sipariş sayısı · ÜRÜN ADEDİ · tutar; ve farkları sipariş no
    listesiyle verir:
      missing_in_panel  — Trendyol'da var, bizde YOK
      extra_in_panel    — bizde var, Trendyol'un o aralıktaki listesinde YOK
      cancel_mismatch   — Trendyol'da TAMAMEN iptal, bizde hâlâ aktif
      partial_cancel    — Trendyol'da kalemin bir kısmı iptal, sipariş aktif

    apply=true YALNIZ statü/iz alanlarına dokunur (CLAUDE.md değişmez #6): iptali
    yansıtır ve stoğu BİR KEZ (idempotent guard) geri ekler. Ödeme/kupon/puan
    alanlarına DOKUNMAZ, sipariş oluşturma yolunu kullanmaz.
    """
    from .deps import tr_range_to_utc
    config = await get_trendyol_config()
    if not config.get("is_active"):
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
    from trendyol_client import TrendyolClient
    client = TrendyolClient(supplier_id=config["supplier_id"], api_key=config["api_key"],
                            api_secret=config["api_secret"], mode=config["mode"])

    s_iso, e_iso = tr_range_to_utc(start_date, end_date)
    start_ms = int(datetime.fromisoformat(s_iso).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(e_iso).timestamp() * 1000)
    if end_ms <= start_ms:
        raise HTTPException(status_code=400, detail="Bitiş tarihi başlangıçtan sonra olmalı")
    # Trendyol ucu 14 günlük pencerelerle sayfalanır; çok uzun aralık Cloudflare'in
    # 100 sn isteği sınırına takılıp SESSİZCE yarım sonuç döndürür. Açıkça reddet.
    _days = (end_ms - start_ms) / 86400000.0
    if _days > 62:
        raise HTTPException(status_code=400,
                            detail=f"Mutabakat aralığı en fazla 62 gün olabilir (seçilen: {int(_days)} gün). "
                                   f"Ay ay çalıştırın.")

    ty = await _ty_fetch_orders_range(client, start_ms, end_ms)

    # Bizim taraf — aynı TR yerel aralık, Trendyol kaynaklı siparişler
    ours: dict = {}
    async for o in db.orders.find(
            {"created_at": {"$gte": s_iso, "$lte": e_iso},
             "$or": [{"platform": "trendyol"}, {"marketplace": "trendyol"}]},
            {"_id": 0, "id": 1, "order_number": 1, "status": 1, "total": 1,
             "items.quantity": 1, "partial_cancel_amount": 1}):
        onum = str(o.get("order_number") or "")
        if onum:
            ours[onum] = o

    def _units(o):
        its = o.get("items") or []
        if not its:
            return 1
        n = 0
        for it in its:
            try:
                n += max(1, int(it.get("quantity") or 1))
            except Exception:
                n += 1
        return n or 1

    _CANCELLED_LOCAL = ("cancelled", "cancel_refunded")
    missing, extra, cancel_mm, partial_mm = [], [], [], []
    for onum, t in ty.items():
        o = ours.get(onum)
        if not o:
            missing.append({"order_number": onum, "units": t["units"], "amount": t["amount"],
                            "fully_cancelled": t["fully_cancelled"]})
            continue
        st = str(o.get("status") or "")
        if t["fully_cancelled"] and st not in _CANCELLED_LOCAL:
            cancel_mm.append({"order_number": onum, "id": o.get("id"), "local_status": st,
                              "units": t["units"], "amount": t["amount"]})
        elif t["partially_cancelled"]:
            try:
                already = float(o.get("partial_cancel_amount") or 0)
            except Exception:
                already = 0.0
            if abs(already - t["amount_cancelled"]) > 0.01:
                partial_mm.append({"order_number": onum, "id": o.get("id"),
                                   "cancelled_units": t["units_cancelled"],
                                   "cancelled_amount": t["amount_cancelled"],
                                   "recorded": round(already, 2)})
    for onum, o in ours.items():
        if onum not in ty:
            extra.append({"order_number": onum, "status": o.get("status"),
                          "units": _units(o), "amount": round(float(o.get("total") or 0), 2)})

    applied = {"cancelled": 0, "partial_recorded": 0, "restocked": 0}
    if apply:
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in cancel_mm:
            try:
                res = await db.orders.update_one(
                    {"id": row["id"], "status": {"$nin": list(_CANCELLED_LOCAL)}},
                    {"$set": {"status": "cancelled",
                              "cancel_reason": "Trendyol iptali (mutabakat)",
                              "cancel_source": "trendyol_reconcile",
                              "updated_at": now_iso},
                     "$push": {"status_history": {"status": "cancelled", "at": now_iso,
                                                  "by": "reconcile",
                                                  "note": "Trendyol mutabakatı: sipariş Trendyol'da iptal"}}})
                if not res.modified_count:
                    continue
                applied["cancelled"] += 1
                # Stok BİR KEZ geri eklenir — manuel iptalle aynı idempotent guard.
                _o = await db.orders.find_one({"id": row["id"]}, {"_id": 0, "id": 1, "items": 1})
                if _o:
                    from routes.orders import _stock_delta_for_order, _RESTORE_MOVE_TYPES
                    _already = await db.stock_movements.find_one(
                        {"order_id": _o["id"], "type": {"$in": _RESTORE_MOVE_TYPES}}, {"_id": 1})
                    if not _already:
                        _moves = await _stock_delta_for_order(_o, +1)
                        await db.stock_movements.insert_one({
                            "id": str(uuid.uuid4()), "type": "order_cancelled",
                            "order_id": _o["id"], "order_number": row["order_number"],
                            "items": _moves, "source": "trendyol_reconcile",
                            "created_at": now_iso})
                        applied["restocked"] += 1
            except Exception as _ce:
                logger.error(f"[reconcile cancel {row['order_number']}] {_ce}")
        for row in partial_mm:
            try:
                await db.orders.update_one(
                    {"id": row["id"]},
                    {"$set": {"partial_cancel_amount": row["cancelled_amount"],
                              "partial_cancel_units": row["cancelled_units"],
                              "partial_cancelled": True, "updated_at": now_iso}})
                applied["partial_recorded"] += 1
            except Exception as _pe:
                logger.error(f"[reconcile partial {row['order_number']}] {_pe}")

    ty_units = sum(t["units"] for t in ty.values())
    ty_amount = round(sum(t["amount"] for t in ty.values()), 2)
    our_units = sum(_units(o) for o in ours.values())
    our_amount = round(sum(float(o.get("total") or 0) for o in ours.values()), 2)
    return {
        "range": {"start": start_date, "end": end_date},
        "trendyol": {"orders": len(ty), "units": ty_units, "amount": ty_amount},
        "panel": {"orders": len(ours), "units": our_units, "amount": our_amount},
        "diff": {
            "orders": len(ours) - len(ty),
            "units": our_units - ty_units,
            "amount": round(our_amount - ty_amount, 2),
            "amount_pct": round(100 * (our_amount - ty_amount) / ty_amount, 3) if ty_amount else 0,
        },
        "missing_in_panel": {"count": len(missing), "items": missing[:list_limit]},
        "extra_in_panel": {"count": len(extra), "items": extra[:list_limit]},
        "cancel_mismatch": {"count": len(cancel_mm), "items": cancel_mm[:list_limit]},
        "partial_cancel": {"count": len(partial_mm), "items": partial_mm[:list_limit]},
        "applied": applied if apply else None,
    }


@router.get("/trendyol/claims/sync")
async def sync_trendyol_claims(
    days_back: int = 1095,
    current_user: dict = Depends(require_admin)
):
    """Trendyol'dan iade/iptal (claim) kayıtlarını çeker ve MongoDB'ye kaydeder.

    days_back varsayılan 1095 (3 yıl) — geçmiş backfill için. UI bu ucu tetikler;
    durum geçişleri her çağrıda canlı tazelenir.
    """
    return await _sync_trendyol_claims_core(days_back)
async def _refresh_open_claims_core() -> dict:
    """AÇIK iade kovalarını (talep/kargoda/aksiyon) Trendyol'un CANLI durumuyla eşitler.
    Hafif iştir (statü filtreli birkaç sayfa + artık başına tekil sorgu) — scheduler
    DAKİKADA BİR çağırır; Onaylanan/Reddedilen dahil tüm statü geçişleri anında yansır.

    1) TY'den claimItemStatus=Created/WaitingInAction/InAnalysis kayıtları TARİHSİZ çekilir
       → statü + kargo takip no güncellenir (kova ayrımı tazelenir).
    2) DB'de açık görünüp TY canlı açık setinde OLMAYAN kayıtlar orderNumber ile tekil
       sorgulanır → gerçek statüsüne (Accepted/Rejected/Cancelled...) taşınır.
    """
    config = await get_trendyol_config()
    if not config["is_active"]:
        return {"skipped": "config"}
    from trendyol_client import TrendyolClient
    import httpx as _httpx
    client = TrendyolClient(supplier_id=config["supplier_id"], api_key=config["api_key"],
                            api_secret=config["api_secret"], mode=config["mode"])
    url = f"{client.base_url}/order/sellers/{client.supplier_id}/claims"
    headers = client._get_headers()
    live = {}  # claim_id -> {status, cargo}
    async with _httpx.AsyncClient(timeout=25.0) as hc:
        for st in ("Created", "WaitingInAction", "InAnalysis"):
            page = 0
            while page < 10:
                try:
                    r = await hc.get(url, headers=headers,
                                     params={"page": page, "size": 200, "claimItemStatus": st})
                    data = r.json() if r.status_code == 200 else {}
                except Exception as e:
                    logger.warning(f"[open-claims] {st} p{page}: {e}")
                    break
                content = data.get("content") or []
                for cl in content:
                    cid = str(cl.get("id") or "")
                    if not cid:
                        continue
                    live[cid] = {
                        "status": st,
                        "cargo": str(cl.get("cargoTrackingNumber") or cl.get("claimCargoTrackingNumber") or ""),
                        "last_modified": cl.get("lastModifiedDate"),
                    }
                if page >= int(data.get("totalPages") or 1) - 1 or not content:
                    break
                page += 1

        updated = closed = claimed = 0
        # 1) Canlı açık kayıtları DB'ye işle (statü/kargo değiştiyse)
        for cid, info in live.items():
            _set = {"claim_status": info["status"], "updated_at": datetime.now(timezone.utc).isoformat()}
            if info["cargo"]:
                _set["cargo_tracking_number"] = info["cargo"]
            res = await db.trendyol_claims.update_one(
                {"claim_id": cid,
                 "$or": [{"claim_status": {"$ne": info["status"]}},
                         *([{"cargo_tracking_number": {"$ne": info["cargo"]}}] if info["cargo"] else [])]},
                {"$set": _set})
            if res.modified_count:
                updated += 1

        # 2) DB'de açık görünen ama canlı açık sette OLMAYANLAR → tekil canlı sorgu
        stale = await db.trendyol_claims.find(
            {"claim_status": {"$in": ["Created", "WaitingInAction", "InAnalysis"]},
             "platform": {"$ne": "hepsiburada"}},
            {"_id": 0, "claim_id": 1, "order_number": 1}).to_list(2000)
        for c in stale:
            cid = str(c.get("claim_id") or "")
            if not cid or cid in live:
                continue
            onum = str(c.get("order_number") or "")
            new_status = None
            if not cid.startswith("ord:") and onum:
                try:
                    r = await hc.get(url, headers=headers,
                                     params={"page": 0, "size": 20, "orderNumber": onum})
                    for cl in (r.json().get("content") or []) if r.status_code == 200 else []:
                        if str(cl.get("id")) == cid:
                            try:
                                new_status = str(((cl.get("items") or [{}])[0].get("claimItems") or [{}])[0]
                                                 .get("claimItemStatus", {}).get("name") or "")
                            except Exception:
                                new_status = ""
                            break
                except Exception as e:
                    logger.warning(f"[open-claims] tekil {onum}: {e}")
            if new_status:
                await db.trendyol_claims.update_one(
                    {"claim_id": cid},
                    {"$set": {"claim_status": new_status,
                              "updated_at": datetime.now(timezone.utc).isoformat()}})
                closed += 1
        return {"live_open": len(live), "updated": updated, "closed_or_moved": closed,
                "stale_checked": len(stale)}


@router.get("/trendyol/claims/refresh-open")
async def refresh_open_claims(current_user: dict = Depends(require_admin)):
    """Açık iade kovalarını Trendyol canlı verisiyle ANINDA eşitler (cron da dakikada bir çağırır)."""
    return await _refresh_open_claims_core()


@router.get("/trendyol/claims/sync-background")
async def sync_trendyol_claims_background(days_back: int = 1095, current_user: dict = Depends(require_admin)):
    """Uzun geçmiş (ör. 3 yıl) claim senkronunu ARKA PLANDA başlatır — Cloudflare'in
    100 sn sınırına takılmaz. Durum: GET /trendyol/claims/sync-background/status."""
    import asyncio
    await db.settings.update_one(
        {"id": "ty_claims_backfill"},
        {"$set": {"status": "running", "days_back": days_back,
                  "started_at": datetime.now(timezone.utc).isoformat()},
         "$unset": {"result": "", "error": ""}},
        upsert=True)

    async def _run():
        try:
            res = await _sync_trendyol_claims_core(days_back)
            await db.settings.update_one(
                {"id": "ty_claims_backfill"},
                {"$set": {"status": "done", "result": res,
                          "finished_at": datetime.now(timezone.utc).isoformat()}})
        except Exception as e:
            logger.error(f"[ty claims backfill] {e}")
            await db.settings.update_one(
                {"id": "ty_claims_backfill"},
                {"$set": {"status": "error", "error": str(e)[:500],
                          "finished_at": datetime.now(timezone.utc).isoformat()}})

    asyncio.create_task(_run())
    return {"started": True, "days_back": days_back}
@router.get("/trendyol/claims/sync-background/status")
async def sync_trendyol_claims_background_status(current_user: dict = Depends(require_admin)):
    doc = await db.settings.find_one({"id": "ty_claims_backfill"}, {"_id": 0})
    return doc or {"status": "none"}
def _build_order_unit_price_map(order_data: dict) -> dict:
    """Trendyol sipariş verisinden barkod → BİRİM fiyat haritası kurar.
    KRİTİK: TY satır alanları (price/amount/lineGrossAmount/discount) ZATEN BİRİM'dir —
    adede BÖLÜNMEZ (eski /qty bölmesi qty>1'de tutarı yarıya düşürüyordu; 11419198311 kanıtı).
    Aynı barkod birden çok pakette olabilir → İPTAL/teslim edilmeyen paket satırları,
    gerçek (teslim/aktif) satırı EZMESİN (yalnız hiç kayıt yoksa fallback)."""
    _CANCEL = {"Cancelled", "UnDelivered", "UnSupplied", "Returned"}
    m = {}
    for pkg in (order_data or {}).get("content", []):
        _pkg_cancel = str(pkg.get("status") or "") in _CANCEL
        for line in pkg.get("lines", []):
            bc = line.get("barcode", "")
            if not bc:
                continue
            line_cancel = _pkg_cancel or str(line.get("orderLineItemStatusName") or "") in _CANCEL
            if bc in m and line_cancel:
                continue  # gerçek satır varken iptal satırı ezmesin
            m[bc] = {
                "gross": line.get("lineGrossAmount", line.get("amount", 0)) or 0,
                "net": line.get("price", 0) or 0,
                "discount": line.get("discount", 0) or 0,
                "_cancel": line_cancel,
            }
    return m


@router.post("/trendyol/claims/resync-amounts-all")
async def resync_claim_amounts_all(payload: Optional[dict] = Body(default=None),
                                   current_user: dict = Depends(require_admin)):
    """TÜM TY iadelerini arka planda sipariş verisinden yeniden türetir (birim fiyat
    düzeltmesi). CF timeout'unu aşmamak için asyncio.create_task; durum settings
    id='ty_amount_resync'. payload: {dry_run=true, force=false}."""
    p = payload or {}
    dry = bool(p.get("dry_run", True))
    force = bool(p.get("force"))
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
    from trendyol_client import TrendyolClient

    async def _run():
        client = TrendyolClient(supplier_id=config["supplier_id"], api_key=config["api_key"],
                                api_secret=config["api_secret"], mode=config["mode"])
        st = {"id": "ty_amount_resync", "status": "running", "dry_run": dry, "force": force,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "scanned": 0, "changed": 0, "skipped_override": 0, "no_order": 0, "examples": []}
        await db.settings.update_one({"id": "ty_amount_resync"}, {"$set": st}, upsert=True)
        order_cache = {}
        try:
            claims = await db.trendyol_claims.find(
                {"platform": {"$ne": "hepsiburada"}},
                {"_id": 0, "claim_id": 1, "order_number": 1, "items": 1,
                 "refund_amount": 1, "amount_overridden": 1}).to_list(None)
            for c in claims:
                st["scanned"] += 1
                if c.get("amount_overridden") and not force:
                    st["skipped_override"] += 1
                elif not str(c.get("order_number") or ""):
                    st["no_order"] += 1
                else:
                    onum = str(c.get("order_number"))
                    if onum not in order_cache:
                        try:
                            order_cache[onum] = await client.get_orders(order_number=onum)
                        except Exception:
                            order_cache[onum] = {}
                    umap = _build_order_unit_price_map(order_cache.get(onum, {}))
                    if umap:
                        new_items, new_refund = [], 0.0
                        for it in (c.get("items") or []):
                            u = umap.get(str(it.get("barcode") or ""))
                            _it = dict(it)
                            if u:
                                _it["unit_price"] = u["gross"]; _it["discount_amount"] = u["discount"]; _it["price"] = u["net"]
                            new_refund += float(_it.get("price", 0) or 0) * max(int(_it.get("quantity", 1) or 1), 1)
                            new_items.append(_it)
                        new_refund = round(new_refund, 2)
                        old_refund = round(float(c.get("refund_amount") or 0), 2)
                        if abs(new_refund - old_refund) >= 0.01:
                            st["changed"] += 1
                            if len(st["examples"]) < 50:
                                st["examples"].append({"order_number": onum, "eski": old_refund, "yeni": new_refund})
                            if not dry:
                                await db.trendyol_claims.update_one(
                                    {"claim_id": c.get("claim_id")},
                                    {"$set": {"items": new_items, "refund_amount": new_refund,
                                              "amount_resynced_at": datetime.now(timezone.utc).isoformat()}})
                    else:
                        st["no_order"] += 1
                if st["scanned"] % 100 == 0:
                    await db.settings.update_one({"id": "ty_amount_resync"}, {"$set": st})
            st["status"] = "done"
            st["finished_at"] = datetime.now(timezone.utc).isoformat()
            await db.settings.update_one({"id": "ty_amount_resync"}, {"$set": st})
        except Exception as e:
            st["status"] = "error"; st["error"] = str(e)[:400]
            await db.settings.update_one({"id": "ty_amount_resync"}, {"$set": st})

    import asyncio
    asyncio.create_task(_run())
    return {"started": True, "dry_run": dry, "force": force}


@router.get("/trendyol/claims/resync-amounts-all/status")
async def resync_claim_amounts_all_status(current_user: dict = Depends(require_admin)):
    doc = await db.settings.find_one({"id": "ty_amount_resync"}, {"_id": 0})
    return doc or {"status": "none"}


@router.post("/trendyol/claims/resync-amounts")
async def resync_claim_amounts(payload: Optional[dict] = Body(default=None),
                               current_user: dict = Depends(require_admin)):
    """İade tutarlarını Trendyol sipariş verisinden YENİDEN türetir (BİRİM fiyat düzeltmesi).
    Eski /qty bölme hatasıyla YARIYA düşmüş tutarları onarır (11419198311 gibi).

    payload: {order_number?, claim_id?, status?, limit=50, dry_run=true, force=false}
    - dry_run: kaydetmeden eski↔yeni tutar karşılaştırması döner.
    - force=false: amount_overridden (elle düzeltilmiş) iadeleri ATLAR."""
    p = payload or {}
    order_number = str(p.get("order_number") or "").strip()
    claim_id = str(p.get("claim_id") or "").strip()
    status = str(p.get("status") or "").strip()
    limit = max(1, min(int(p.get("limit") or 50), 500))
    dry = bool(p.get("dry_run", True))
    force = bool(p.get("force"))

    q = {"platform": {"$ne": "hepsiburada"}}
    if claim_id:
        q["claim_id"] = claim_id
    if order_number:
        q["order_number"] = order_number
    if status:
        q["claim_status"] = status
    claims = await db.trendyol_claims.find(
        q, {"_id": 0, "claim_id": 1, "order_number": 1, "items": 1,
            "refund_amount": 1, "amount_overridden": 1}).sort("created_date", -1).to_list(limit)

    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
    from trendyol_client import TrendyolClient
    client = TrendyolClient(supplier_id=config["supplier_id"], api_key=config["api_key"],
                            api_secret=config["api_secret"], mode=config["mode"])

    order_cache = {}
    changed, skipped_override, no_order, unchanged = [], 0, 0, 0
    for c in claims:
        if c.get("amount_overridden") and not force:
            skipped_override += 1
            continue
        onum = str(c.get("order_number") or "")
        if not onum:
            no_order += 1
            continue
        if onum not in order_cache:
            try:
                order_cache[onum] = await client.get_orders(order_number=onum)
            except Exception as e:
                logger.warning(f"[resync-amounts] {onum}: {e}")
                order_cache[onum] = {}
        umap = _build_order_unit_price_map(order_cache.get(onum, {}))
        if not umap:
            no_order += 1
            continue
        new_items, new_refund = [], 0.0
        for it in (c.get("items") or []):
            bc = str(it.get("barcode") or "")
            u = umap.get(bc)
            _it = dict(it)
            if u:
                _it["unit_price"] = u["gross"]
                _it["discount_amount"] = u["discount"]
                _it["price"] = u["net"]
            new_refund += float(_it.get("price", 0) or 0) * max(int(_it.get("quantity", 1) or 1), 1)
            new_items.append(_it)
        new_refund = round(new_refund, 2)
        old_refund = round(float(c.get("refund_amount") or 0), 2)
        if abs(new_refund - old_refund) < 0.01:
            unchanged += 1
            continue
        rec = {"claim_id": c.get("claim_id"), "order_number": onum,
               "eski_tutar": old_refund, "yeni_tutar": new_refund}
        changed.append(rec)
        if not dry:
            await db.trendyol_claims.update_one(
                {"claim_id": c.get("claim_id")},
                {"$set": {"items": new_items, "refund_amount": new_refund,
                          "amount_resynced_at": datetime.now(timezone.utc).isoformat()}})
    return {"dry_run": dry, "taranan": len(claims), "degisen": len(changed),
            "degismeyen": unchanged, "override_atlandi": skipped_override,
            "siparissiz": no_order, "detay": changed[:100]}


@router.post("/trendyol/claims/fix-discounts")
async def fix_claim_discounts(current_user: dict = Depends(require_admin)):
    """Fix discount data for existing claims by fetching from order API"""
    settings = await db.settings.find_one({"id": "trendyol"}, {"_id": 0})
    if not settings or not settings.get("api_key"):
        raise HTTPException(status_code=400, detail="Trendyol API ayarları eksik")
    
    from trendyol_client import TrendyolClient
    client = TrendyolClient(settings["supplier_id"], settings["api_key"], settings["api_secret"])
    
    # Get claims that need discount fix (where items have 0 discount)
    claims = await db.trendyol_claims.find({}, {"_id": 0, "claim_id": 1, "order_number": 1, "items": 1, "amount_overridden": 1}).to_list(None)
    
    order_cache = {}
    fixed = 0
    
    for claim in claims:
        order_number = claim.get("order_number", "")
        if not order_number:
            continue
        
        items = claim.get("items", [])
        needs_fix = any(item.get("discount_amount", 0) == 0 and item.get("unit_price", 0) == item.get("price", 0) for item in items)
        if not needs_fix:
            continue
        
        # Get order data (cached)
        if order_number not in order_cache:
            try:
                order_cache[order_number] = await client.get_orders(order_number=order_number)
            except Exception:
                order_cache[order_number] = {}
        
        cached = order_cache.get(order_number, {})
        # BİRİM fiyat (adede bölme YOK, iptal paket ezmez) — ortak helper.
        discount_map = _build_order_unit_price_map(cached)
        invoice_number = ""
        for pkg in cached.get("content", []):
            if pkg.get("invoiceNumber"):
                invoice_number = pkg.get("invoiceNumber", "")
                break
        
        # MANUEL TUTAR KORUMASI: elle düzeltilmiş iadeyi (amount_overridden) atla.
        if claim.get("amount_overridden"):
            continue

        updated_items = []
        refund_amount = 0
        for item in items:
            bc = item.get("barcode", "")
            if bc in discount_map:
                item["unit_price"] = discount_map[bc]["gross"]
                item["discount_amount"] = discount_map[bc]["discount"]
                item["price"] = discount_map[bc]["net"]
            # ADET ile çarp: aynı üründen 2 adet iade edildiyse tutar 2× olmalı (yoksa yarısı).
            refund_amount += float(item.get("price", 0) or 0) * max(int(item.get("quantity", 1) or 1), 1)
            updated_items.append(item)

        update_set = {"items": updated_items, "refund_amount": round(refund_amount, 2)}
        if invoice_number:
            update_set["invoice_number"] = invoice_number

        await db.trendyol_claims.update_one(
            {"claim_id": claim["claim_id"]},
            {"$set": update_set}
        )
        fixed += 1
    
    return {"success": True, "fixed": fixed, "message": f"{fixed} iadenin iskonto bilgisi güncellendi"}
@router.get("/trendyol/claims")
async def get_trendyol_claims(
    page: int = 1,
    limit: int = 20,
    claim_type: str = "",
    search: str = "",
    status: str = "",
    platform: str = "trendyol",
    current_user: dict = Depends(require_admin)
):
    """Yerel veritabanındaki iade kayıtlarını listele.

    Tüm claim'ler İADE'dir (İptal ayrı menüde, sipariş durumu üzerinden).
    `status` = durum sekmesi anahtarı: all / talep_olusturulan / kargoya_verilen /
    aksiyon_bekleyen / onaylanan / reddedilen.
    `platform` = 'facette' (web/site iadeleri) veya bir pazaryeri anahtarı
    (trendyol, hepsiburada, ...). Ayrım `_claim_is_site_order` kuralıyla yapılır.
    """
    # Sekme kovası eşlemesi artık _claim_bucket(c) helper'ında (status + kargo takip durumu).
    _VALID_TABS = {"talep_olusturulan", "kargoya_verilen", "acik_iade", "aksiyon_bekleyen", "onaylanan", "reddedilen"}

    base_query = {}
    if claim_type:
        base_query["claim_type"] = claim_type
    if search:
        _s = search.strip()
        _rx = {"$regex": _search_tr_regex(_s), "$options": "i"}
        _ors = [
            {"order_number": _rx},
            {"customer_name": _rx},
            {"claim_id": _rx},
            {"invoice_number": _rx},
            {"gider_pusulasi_no": _rx},
            {"cargo_tracking_number": _rx},
            {"cargo_provider_name": _rx},
            {"claim_reason": _rx},
            {"items.productName": _rx},
            {"items.product_name": _rx},
            {"items.barcode": _rx},
            {"items.merchantSku": _rx},
        ]
        # GİDER PUSULASI NO ile arama (Kadir): display_number eşleşen pusulalardan claim_id topla.
        try:
            _gp_rx = {"$regex": re.escape(_s), "$options": "i"}
            _gp_cids = [str(_g.get("claim_id")) async for _g in db.gider_pusulasi.find(
                {"claim_id": {"$exists": True, "$ne": ""}, "display_number": _gp_rx},
                {"_id": 0, "claim_id": 1}) if _g.get("claim_id")]
            if _gp_cids:
                _ors.append({"claim_id": {"$in": _gp_cids}})
        except Exception:
            pass
        # Telefon: sadece rakam → son 10 hane (kayıtta varsa)
        _digits = re.sub(r"\D", "", _s)
        if len(_digits) >= 7:
            _ors.append({"customer_phone": {"$regex": re.escape(_digits[-10:])}})
        # Çok kelimeli ad: tüm kelimeler customer_name içinde geçsin (sıra önemsiz)
        _words = [w for w in _s.split() if len(w) >= 2]
        if len(_words) >= 2:
            _ors.append({"$and": [
                {"customer_name": {"$regex": _search_tr_regex(w), "$options": "i"}}
                for w in _words
            ]})
        base_query["$or"] = _ors

    want_tab = status if (status and status != "all" and status in _VALID_TABS) else None

    # base_query (arama/tip filtreli, AMA status filtresiz) ile TÜM kayıtları çek.
    # Status filtresi bellekte uygulanır ki tab_counts tüm kovaları doğru sayabilsin.
    raw = await db.trendyol_claims.find(
        base_query, {"_id": 0, "raw_data": 0}
    ).sort("created_date", -1).to_list(None)

    # (a) claim_id'ye göre tekilleştir — aynı claim birden fazla belge olarak yazılmışsa
    # en güncel (created_date'e göre zaten sıralı) ilk görüleni tut.
    seen = set()
    deduped = []
    for c in raw:
        cid = c.get("claim_id") or c.get("order_number")
        if cid in seen:
            continue
        seen.add(cid)
        deduped.append(c)

    # Sipariş-durumu köprüsü: Trendyol'da GÖRÜNÜR claim'i OLMAYAN ama elle iade durumuna
    # alınmış Trendyol siparişlerini de ekle (Web Sitesi/Rooftr deseninin aynası).
    # DİKKAT: bastırma listesi yalnızca İADE ekranında GÖRÜNEN (iptal-DIŞI) claim'leri sayar.
    # Aksi halde: siparişin tek claim'i İPTAL ise -> claim iptal sekmesine gider (burada gizli)
    # AMA order_number _seen_orders'a girip manuel satırı da bastırır -> sipariş HİÇBİR YERDE
    # görünmez ("kayıp"). Elle iade talebine çekilen böyle bir sipariş artık yüzeye çıkar.
    _seen_orders = {c.get("order_number") for c in deduped
                    if c.get("order_number") and _claim_bucket(c) != "iptal"}
    _manual_rows = await _order_derived_trendyol_returns(
        search=search, claim_type=claim_type, exclude_order_numbers=_seen_orders)
    # MANUEL ÖNCELİK: elle iade durumuna çekilen sipariş için üretilen manuel satır,
    # aynı sipariş no'ya ait gerçek claim'i EZER — o claim'i listeden düşür, manuel satır kalsın.
    _manual_onums = {r.get("order_number") for r in _manual_rows if r.get("order_number")}
    if _manual_onums:
        deduped = [c for c in deduped
                   if not (c.get("order_number") in _manual_onums and not c.get("manual"))]
    deduped = deduped + _manual_rows

    # (b) trendyol_claims koleksiyonu pazaryeri iade kayıtlarını tutar; HB iadeleri de
    # buraya platform="hepsiburada" ile yazılır (ortak ekran/GP akışı). Site iadeleri
    # ayrı koleksiyonda (customer_returns) ve ayrı sekmede (Web Sitesi) gösterilir.
    # `platform` paramı: "hepsiburada" -> yalnız HB kayıtları; "trendyol"/boş -> HB
    # OLMAYANLAR (eski kayıtlarda platform alanı yok -> Trendyol'da kalırlar).
    _plt = str(platform or "trendyol").strip().lower()
    if _plt == "hepsiburada":
        platform_scoped = [c for c in deduped
                           if str(c.get("platform") or "").lower() == "hepsiburada"]
    elif _plt in ("trendyol", ""):
        platform_scoped = [c for c in deduped
                           if str(c.get("platform") or "").lower() != "hepsiburada"]
    elif _plt in ("facette", "site", "web"):
        # DENETİM HATA-5: site iadeleri bu koleksiyonda DEĞİL (customer_returns'te) —
        # eskiden tüm pazaryeri kayıtları dönüyordu. Site sinyali taşıyanlar süzülür
        # (pratikte boş liste), pazaryeri kayıtları asla dönmez.
        platform_scoped = [c for c in deduped if _claim_is_site_order(c)]
    else:
        # Bilinmeyen platform değeri: her şeyi döndürme — yalnız birebir eşleşen
        platform_scoped = [c for c in deduped
                           if str(c.get("platform") or "").lower() == _plt]

    # İptal (Cancelled iade statüsü) bu iade ekranından TAMAMEN dışlanır; iptaller
    # ayrı bir alandan yönetilir. Böylece "Tüm İadeler" sekmesi ve "Toplam İade"
    # kartı aynı evreni (iptal-hariç tekil iade) sayar.
    # Aynı iadenin çift kaydını (tekrar senkron / farklı claim_id ile aynı içerik)
    # ayıkla — bir sipariş no yanlışlıkla 2-3 kez görünmesin. Farklı kalem/tutar = ayrı iade.
    platform_scoped = _dedup_claims_by_content(platform_scoped)
    # HB kalem-bazlı claim'leri sipariş no'ya göre tek satırda birleştir (4598214509 gibi).
    platform_scoped = _group_hb_claims_by_order(platform_scoped)
    iade_scoped = [c for c in platform_scoped if _claim_bucket(c) != "iptal"]
    # En yeni HAREKET en üstte (site/Trendyol/HB fark etmez) — talep/onay/ret/değişiklik en yenisi.
    iade_scoped.sort(key=_claim_activity_key, reverse=True)

    # (c) status sekmesi filtresi (bellekte) — _claim_bucket ile
    if want_tab == "acik_iade":
        filtered = [c for c in iade_scoped if _claim_bucket(c) in ("talep_olusturulan", "kargoya_verilen")]
    elif want_tab is not None:
        filtered = [c for c in iade_scoped if _claim_bucket(c) == want_tab]
    else:
        filtered = iade_scoped

    total = len(filtered)
    skip = (page - 1) * limit
    claims = filtered[skip: skip + limit]

    # Her claim'e kova + Türkçe durum etiketi ekle (frontend durum rozeti için).
    # talep/kargoda tek "Açık İade" altında birleşir; geçmiş kovalar korunur.
    _BUCKET_LABEL = {
        "talep_olusturulan": "Açık İade",
        "kargoya_verilen": "Açık İade",
        "aksiyon_bekleyen": "Aksiyon Bekleyen",
        "onaylanan": "Onaylandı",
        "reddedilen": "Reddedildi",
        "iptal": "İptal",
    }
    for c in claims:
        _b = _claim_bucket(c)
        c["bucket"] = _b
        c["bucket_label"] = _BUCKET_LABEL.get(_b, "—")
        if c.get("manual") and c.get("order_status"):
            c["bucket_label"] = _ORDER_STATUS_TR.get(c.get("order_status"), c["bucket_label"])
        # İADE SEBEBİNİ GÖSTERMEYE ZORLA: claim_reason boşsa (eski/eksik kayıtlar) raw_data'daki
        # customerClaimItemReason.name'den türet + kalem sebeplerini de doldur. Böylece Trendyol/HB
        # iadelerinde "Sebep" alanı ve kalem sebepleri "-" kalmaz.
        if not (c.get("claim_reason") or "").strip() or any(not (it.get("reason") or "").strip() for it in (c.get("items") or [])):
            _raw_items = (c.get("raw_data") or {}).get("items") or []
            # barkod/kalem-id -> sebep haritası
            _rmap, _first = {}, ""
            for _it in _raw_items:
                _ol = _it.get("orderLine") or {}
                _bc = str(_ol.get("barcode") or "")
                for _ci in (_it.get("claimItems") or []):
                    _rn = ((_ci.get("customerClaimItemReason") or {}).get("name") or "").strip()
                    if _rn:
                        _first = _first or _rn
                        if _bc:
                            _rmap[_bc] = _rn
                        _rmap[str(_ci.get("id") or "")] = _rn
            if _first and not (c.get("claim_reason") or "").strip():
                c["claim_reason"] = _first
            for it in (c.get("items") or []):
                if not (it.get("reason") or "").strip():
                    it["reason"] = _rmap.get(str(it.get("barcode") or "")) or _rmap.get(str(it.get("claim_item_id") or "")) or _first or ""

    # Personel (admin) notlari: bu sayfadaki claim'lerin siparislerinden admin_notes'u tek
    # sorguyla cek, order_number'a gore iade satirina ekle (Siparisler'de girilen personel
    # notu iade panelinde de gorunsun — ayirt edici sekilde gosterilir).
    _onums = [str(c.get("order_number")) for c in claims if c.get("order_number")]
    if _onums:
        _notes_map = {}
        async for _o in db.orders.find(
            {"order_number": {"$in": _onums}, "admin_notes": {"$exists": True, "$ne": []}},
            {"_id": 0, "order_number": 1, "admin_notes": 1},
        ):
            _sn = [{"text": (n or {}).get("text") or "", "by": (n or {}).get("by") or "", "at": (n or {}).get("at") or ""}
                   for n in (_o.get("admin_notes") or []) if (n or {}).get("text")]
            if _sn:
                _notes_map[str(_o.get("order_number"))] = _sn
        for c in claims:
            c["staff_notes"] = _notes_map.get(str(c.get("order_number")), [])

    # e-FATURA BAYRAĞI: bu sayfadaki claim'lerin siparişlerini invoice_type/billing ile
    # tek sorguda çek → e-Fatura/kurumsal siparişlere kırmızı uyarı + GP butonu kilidi.
    if _onums:
        from .orders import _order_is_efatura
        _ef_map = {}
        async for _o in db.orders.find(
            {"order_number": {"$in": _onums}},
            {"_id": 0, "order_number": 1, "invoice_type": 1, "billing_info": 1, "billing_address": 1},
        ):
            _ef_map[str(_o.get("order_number"))] = _order_is_efatura(_o)
        for c in claims:
            c["is_efatura"] = bool(_ef_map.get(str(c.get("order_number")), False))

    # KALEM BEDENİ: iade kalemlerinde beden görünsün (kullanıcı isteği). productName/barcode
    # var ama size çoğu kayıtta boş → barkodu ürün kataloğundaki varyanttan zenginleştir.
    _bcs = list({str(it.get("barcode") or "").strip()
                 for c in claims for it in (c.get("items") or [])
                 if str(it.get("barcode") or "").strip() and not str(it.get("size") or "").strip()})
    if _bcs:
        _size_map = {}
        async for _p in db.products.find(
                {"variants.barcode": {"$in": _bcs}}, {"_id": 0, "variants": 1}):
            for _v in (_p.get("variants") or []):
                _vbc = str(_v.get("barcode") or "").strip()
                if _vbc and _vbc not in _size_map:
                    _size_map[_vbc] = _v.get("size") or _v.get("beden") or ""
        for c in claims:
            for it in (c.get("items") or []):
                if not str(it.get("size") or "").strip():
                    _sz = _size_map.get(str(it.get("barcode") or "").strip())
                    if _sz:
                        it["size"] = _sz

    # Sekme adetleri (#11) — Trendyol "aksiyon bekleyen ÜRÜN sayısı" ile birebir tutması için
    # ÜRÜN (kalem) bazında sayılır. KRİTİK: bir claim KARIŞIK statülü olabilir (ör. 2 kalem;
    # 1'i WaitingInAction, 1'i Accepted). Bu yüzden claim'in tüm kalemlerini tek kovaya atmak
    # YANLIŞ (Trendyol'u aşar). Doğrusu: her claimItem'i KENDİ statüsüyle kovaya atmak.
    # Statü kaynağı: raw_data.items[].claimItems[].claimItemStatus.name (her claim'de mevcut,
    # re-sync gerekmez). Manuel/site kaydı veya raw yoksa → claim kovasında stored kalem (min 1).
    def _item_status_to_bucket(_nm: str, _has_cargo: bool):
        _nm = (_nm or "").strip()
        if _nm == "Accepted":
            return "onaylanan"
        if _nm in ("Rejected", "Unresolved"):
            return "reddedilen"
        if _nm in ("WaitingInAction", "InAnalysis"):
            return "aksiyon_bekleyen"
        if _nm == "Created":
            return "kargoya_verilen" if _has_cargo else "talep_olusturulan"
        return None  # Cancelled / bilinmeyen → sayma (iptal edilmiş kalem iadelerde görünmez)

    _bcount = {"talep_olusturulan": 0, "kargoya_verilen": 0, "aksiyon_bekleyen": 0, "onaylanan": 0, "reddedilen": 0}
    _ccount = dict(_bcount)  # claim (satır) adedi — liste satır sayısıyla tutması için
    _all_items = 0
    for c in iade_scoped:
        _cb = _claim_bucket(c)
        if _cb in _ccount:
            _ccount[_cb] += 1
        _has_cargo = bool(str(c.get("cargo_tracking_number") or "").strip())
        _raw = c.get("raw_data") or {}
        _counted = 0
        if not c.get("manual") and (_raw.get("items")):
            for _it in (_raw.get("items") or []):
                for _ci in (_it.get("claimItems") or []):
                    _nm = ((_ci.get("claimItemStatus") or {}).get("name") or "")
                    _b = _item_status_to_bucket(_nm, _has_cargo)
                    if _b in _bcount:
                        _bcount[_b] += 1
                        _all_items += 1
                        _counted += 1
        if _counted == 0 and _cb in _bcount:
            # Manuel/site kaydı veya raw_data yok → claim kovasında stored kalem adedi (en az 1)
            _n = len(c.get("items") or []) or 1
            _bcount[_cb] += _n
            _all_items += _n
    tab_counts = {"all": _all_items, **_bcount, "acik_iade": _bcount["talep_olusturulan"] + _bcount["kargoya_verilen"]}
    # Claim (satır) bazlı adetler — frontend "N talep" ipucu için ayrıca döner.
    tab_claim_counts = {"all": len(iade_scoped), **_ccount, "acik_iade": _ccount["talep_olusturulan"] + _ccount["kargoya_verilen"]}

    # İstatistikler — "Toplam İade" kartı = "Tüm İadeler" sekmesi (iptal hariç tekil iade).
    total_returns = len(iade_scoped)
    total_cancels = await db.trendyol_claims.count_documents({"claim_type": "CANCEL"})
    total_refund = sum((c.get("refund_amount") or 0) for c in iade_scoped)

    return {
        "claims": claims,
        "total": total,
        "page": page,
        "limit": limit,
        "tab_counts": tab_counts,
        "tab_claim_counts": tab_claim_counts,
        "stats": {
            "total_returns": total_returns,
            "total_cancels": total_cancels,
            "total_refund": total_refund
        }
    }
@router.get("/trendyol/claims/export")
async def export_trendyol_claims(
    status: str = "",
    search: str = "",
    platform: str = "trendyol",
    current_user: dict = Depends(require_admin),
):
    """Trendyol iadelerini bulunulan sekme (status) + arama filtresiyle Excel'e aktarır.
    Liste endpoint'iyle (get_trendyol_claims) AYNI dedup + kova mantığını kullanır;
    böylece hangi sekmedeyse o sekmenin kayıtları döner.
    """
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from fastapi.responses import StreamingResponse

    _VALID_TABS = {"talep_olusturulan", "kargoya_verilen", "acik_iade", "aksiyon_bekleyen", "onaylanan", "reddedilen"}
    base_query = {}
    if search:
        _rx = {"$regex": _search_tr_regex(search.strip()), "$options": "i"}
        base_query["$or"] = [
            {"order_number": _rx},
            {"customer_name": _rx},
            {"claim_id": _rx},
            {"invoice_number": _rx},
            {"cargo_tracking_number": _rx},
            {"cargo_provider_name": _rx},
            {"items.productName": _rx},
            {"items.product_name": _rx},
            {"items.barcode": _rx},
        ]
    want_tab = status if (status and status != "all" and status in _VALID_TABS) else None

    raw = await db.trendyol_claims.find(base_query, {"_id": 0, "raw_data": 0}).sort("created_date", -1).to_list(None)
    seen = set(); deduped = []
    for c in raw:
        cid = c.get("claim_id") or c.get("order_number")
        if cid in seen:
            continue
        seen.add(cid); deduped.append(c)
    # Bastırma listesi yalnızca iptal-DIŞI (İade ekranında görünen) claim'leri sayar (bkz. liste ucu).
    _seen_orders = {c.get("order_number") for c in deduped
                    if c.get("order_number") and _claim_bucket(c) != "iptal"}
    _manual_rows = await _order_derived_trendyol_returns(search=search, exclude_order_numbers=_seen_orders)
    _manual_onums = {r.get("order_number") for r in _manual_rows if r.get("order_number")}
    if _manual_onums:
        deduped = [c for c in deduped
                   if not (c.get("order_number") in _manual_onums and not c.get("manual"))]
    deduped = deduped + _manual_rows
    # Platform süzmesi (liste ucuyla aynı kural): hepsiburada -> yalnız HB; trendyol/boş -> HB olmayanlar.
    _plt = str(platform or "trendyol").strip().lower()
    if _plt == "hepsiburada":
        deduped = [c for c in deduped if str(c.get("platform") or "").lower() == "hepsiburada"]
    elif _plt in ("trendyol", ""):
        deduped = [c for c in deduped if str(c.get("platform") or "").lower() != "hepsiburada"]
    deduped = _dedup_claims_by_content(deduped)
    deduped = _group_hb_claims_by_order(deduped)
    iade_scoped = [c for c in deduped if _claim_bucket(c) != "iptal"]
    iade_scoped.sort(key=_claim_activity_key, reverse=True)
    if want_tab == "acik_iade":
        rows = [c for c in iade_scoped if _claim_bucket(c) in ("talep_olusturulan", "kargoya_verilen")]
    elif want_tab is not None:
        rows = [c for c in iade_scoped if _claim_bucket(c) == want_tab]
    else:
        rows = iade_scoped

    _BUCKET_LABEL = {
        "talep_olusturulan": "Açık İade", "kargoya_verilen": "Açık İade",
        "aksiyon_bekleyen": "Aksiyon Bekleyen", "onaylanan": "Onaylandı",
        "reddedilen": "Reddedildi", "iptal": "İptal",
    }

    wb = Workbook()
    ws = wb.active
    ws.title = "Trendyol İadeleri"
    headers = ["Sipariş No", "Müşteri", "Ürün", "Tutar", "Tarih", "Durum", "Gider Pusulası No"]
    ws.append(headers)
    hfill = PatternFill("solid", fgColor="FCE4B6")
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = hfill
        c.alignment = Alignment(horizontal="center", vertical="center")

    for c in rows:
        items = c.get("items") or []
        urun = ", ".join([
            (i.get("product_name") or i.get("name") or "").strip()
            for i in items if (i.get("product_name") or i.get("name"))
        ])
        b = _claim_bucket(c)
        _dlabel = _ORDER_STATUS_TR.get(c.get("order_status")) if c.get("manual") else None
        ws.append([
            c.get("order_number") or "",
            c.get("customer_name") or "",
            urun,
            float(c.get("refund_amount") or 0),
            str(c.get("created_date") or "")[:10],
            _dlabel or _BUCKET_LABEL.get(b, "—"),
            c.get("gider_pusulasi_no") or "",
        ])

    for col in ws.columns:
        ml = max((len(str(cc.value)) for cc in col if cc.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(ml + 2, 50)

    # GÜVENLİK: Excel/CSV formül injection — = + - @ ile başlayan metin hücrelerini kaçışla
    for _ws in wb.worksheets:
        for _row in _ws.iter_rows():
            for _c in _row:
                if isinstance(_c.value, str) and _c.value[:1] in ("=", "+", "-", "@", "\t", "\r"):
                    _c.value = "'" + _c.value
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"trendyol-iadeleri-{status or 'tum'}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
@router.get("/trendyol/claims/diagnostics")
async def trendyol_claims_diagnostics(current_user: dict = Depends(require_admin)):
    """Kova kalibrasyonu teşhisi: ham status dağılımı + kova eşlemesi + kargo kırılımı.

    Panel'deki Trendyol sekme sayılarını (talep/kargoda/aksiyon/onay/ret) gerçek veriyle
    karşılaştırıp _claim_bucket eşlemesini doğrulamak/ayarlamak için kullanılır.
    Backfill bitince çağır; by_bucket panel sayılarıyla tutmazsa by_status_raw'a bakıp
    _claim_bucket tek noktadan düzeltilir.
    """
    rows = await db.trendyol_claims.find(
        {}, {"_id": 0, "claim_status": 1, "cargo_tracking_number": 1, "claim_id": 1}
    ).to_list(None)
    seen = set()
    uniq = []
    for c in rows:
        cid = c.get("claim_id")
        if cid in seen:
            continue
        seen.add(cid)
        uniq.append(c)

    by_status = {}
    by_bucket = {"talep_olusturulan": 0, "kargoya_verilen": 0, "aksiyon_bekleyen": 0, "onaylanan": 0, "reddedilen": 0, "iptal": 0}
    created_with_cargo = 0
    created_without_cargo = 0
    for c in uniq:
        st = (c.get("claim_status") or "").strip() or "(boş)"
        by_status[st] = by_status.get(st, 0) + 1
        b = _claim_bucket(c)
        if b in by_bucket:
            by_bucket[b] += 1
        if (c.get("claim_status") or "").strip() == "Created":
            if str(c.get("cargo_tracking_number") or "").strip():
                created_with_cargo += 1
            else:
                created_without_cargo += 1

    return {
        "total_unique_claims": len(uniq),
        "by_status_raw": by_status,
        "by_bucket": by_bucket,
        "created_with_cargo": created_with_cargo,
        "created_without_cargo": created_without_cargo,
        "expected_from_panel": {
            "talep_olusturulan": 34, "kargoya_verilen": 54,
            "aksiyon_bekleyen": 16, "onaylanan": 3583, "reddedilen": 49,
        },
        "note": "by_bucket panel ile tutmazsa _claim_bucket eşlemesi by_status_raw'a göre ayarlanır.",
    }
@router.post("/trendyol/claims/repair-status")
async def repair_trendyol_claim_status(current_user: dict = Depends(require_admin)):
    """Mevcut TÜM claim kayıtlarının statüsünü KAYITLI raw_data'dan yeniden türetir.

    Eski sync, claim-level olmayan `status` alanını okuduğu için kayıtlar statüsüz
    yazılmıştı. Bu uç Trendyol API'sine YENİ istek atmadan raw_data'daki gerçek item
    statülerinden (claimItemStatus.name) claim_status'ü, kargo alanlarını ve onay/ret
    tarih damgalarını yeniden hesaplar. İdempotent — tekrar çağrılabilir.
    """
    cursor = db.trendyol_claims.find(
        {}, {"_id": 0, "claim_id": 1, "raw_data": 1, "return_approved_at": 1, "return_rejected_at": 1}
    )
    scanned = 0
    fixed = 0
    bucket_after = {"talep_olusturulan": 0, "kargoya_verilen": 0, "aksiyon_bekleyen": 0, "onaylanan": 0, "reddedilen": 0, "iptal": 0}
    async for c in cursor:
        scanned += 1
        raw = c.get("raw_data") or {}
        if not raw:
            continue
        new_status = _derive_claim_status(raw)
        cargo_no = str(raw.get("cargoTrackingNumber", "") or "")
        _set = {
            "claim_status": new_status,
            "cargo_tracking_number": cargo_no,
            "cargo_provider_name": raw.get("cargoProviderName", "") or "",
        }
        stamps = _first_seen_stamps(raw)
        if stamps.get("return_approved_at") and not c.get("return_approved_at"):
            _set["return_approved_at"] = stamps["return_approved_at"]
        if stamps.get("return_rejected_at") and not c.get("return_rejected_at"):
            _set["return_rejected_at"] = stamps["return_rejected_at"]
        await db.trendyol_claims.update_one({"claim_id": c["claim_id"]}, {"$set": _set})
        fixed += 1
        b = _claim_bucket({"claim_status": new_status, "cargo_tracking_number": cargo_no})
        if b in bucket_after:
            bucket_after[b] += 1
    return {"scanned": scanned, "fixed": fixed, "bucket_after": bucket_after}
@router.post("/trendyol/claims/dedupe")
async def dedupe_trendyol_claims(current_user: dict = Depends(require_admin)):
    """Aynı claim_id'ye sahip MÜKERRER belgeleri temizler (her claim_id için en güncel
    updated_at olanı tutar), sonra claim_id üzerinde unique index kurarak gelecekte
    mükerrerlenmeyi engeller. İdempotent — tekrar çağrılabilir.
    """
    pipeline = [
        {"$group": {"_id": "$claim_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
    ]
    groups = await db.trendyol_claims.aggregate(pipeline).to_list(None)
    removed = 0
    affected = 0
    for g in groups:
        cid = g["_id"]
        docs = await db.trendyol_claims.find(
            {"claim_id": cid}, {"_id": 1, "updated_at": 1, "created_date": 1, "created_at": 1}
        ).to_list(None)
        if len(docs) <= 1:
            continue
        docs.sort(key=lambda d: (str(d.get("updated_at") or ""), str(d.get("created_date") or ""), str(d.get("created_at") or "")), reverse=True)
        to_delete = [d["_id"] for d in docs[1:]]
        if to_delete:
            res = await db.trendyol_claims.delete_many({"_id": {"$in": to_delete}})
            removed += res.deleted_count
            affected += 1
    index_created = False
    index_error = ""
    try:
        await db.trendyol_claims.create_index("claim_id", unique=True, name="uniq_claim_id")
        index_created = True
    except Exception as e:
        index_error = str(e)[:200]
    remaining = await db.trendyol_claims.count_documents({})
    return {
        "duplicate_groups": len(groups),
        "claims_affected": affected,
        "removed": removed,
        "index_created": index_created,
        "index_error": index_error,
        "remaining_docs": remaining,
    }
@router.post("/trendyol/claims/{claim_id}/set-status")
async def set_trendyol_claim_status(claim_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    """Trendyol iade durumunu MANUEL ilerletir ve KİLİTLER.

    manual_locked=True olunca sonraki Trendyol senkronları bu claim'in claim_status'unu
    EZMEZ (yalnız kargo/raw bilgisi tazelenir). Kilidi kaldırmak için /unlock çağrılır.
    """
    new_status = (payload.get("status") or "").strip()
    valid = {"Created", "WaitingInAction", "InAnalysis", "Accepted", "Rejected", "Unresolved", "Cancelled"}
    if new_status not in valid:
        raise HTTPException(status_code=400, detail=f"Geçersiz durum. Geçerli: {', '.join(sorted(valid))}")
    now = datetime.now(timezone.utc).isoformat()
    _set = {"claim_status": new_status, "manual_locked": True, "manual_status_at": now, "updated_at": now}
    if new_status == "Accepted":
        _set["return_approved_at"] = now
    if new_status in ("Rejected", "Cancelled"):
        _set["return_rejected_at"] = now
    res = await db.trendyol_claims.update_one({"claim_id": claim_id}, {"$set": _set})
    if not res.matched_count:
        raise HTTPException(status_code=404, detail="İade (claim) bulunamadı")
    # HB KALEM-BAZLI BİRLEŞTİRME: tek satırda gösterilen HB claim'i için durum değişikliği,
    # aynı siparişin tüm (iptal-dışı) HB kardeş claim'lerine uygulanır (ekranda tek satır → tek işlem).
    _target = await db.trendyol_claims.find_one({"claim_id": claim_id}, {"_id": 0, "platform": 1, "order_number": 1})
    fanned = 0
    if _target and str(_target.get("platform") or "").lower() == "hepsiburada" and _target.get("order_number"):
        _r = await db.trendyol_claims.update_many(
            {"order_number": _target.get("order_number"), "platform": "hepsiburada",
             "claim_id": {"$ne": claim_id}, "claim_status": {"$ne": "Cancelled"}},
            {"$set": _set})
        fanned = _r.modified_count
    return {"success": True, "claim_id": claim_id, "status": new_status, "manual_locked": True, "fanned_siblings": fanned}
@router.post("/trendyol/claims/{claim_id}/unlock")
async def unlock_trendyol_claim(claim_id: str, current_user: dict = Depends(require_admin)):
    """Manuel kilidi kaldırır → durum tekrar Trendyol senkronundan güncellenmeye başlar."""
    res = await db.trendyol_claims.update_one(
        {"claim_id": claim_id},
        {"$set": {"manual_locked": False, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if not res.matched_count:
        raise HTTPException(status_code=404, detail="İade (claim) bulunamadı")
    return {"success": True, "claim_id": claim_id, "manual_locked": False}
@router.post("/trendyol/claims/{claim_id}/set-amount")
async def set_trendyol_claim_amount(claim_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    """İade tutarını MANUEL düzeltir (sistemdeki tutar Trendyol'un gerçek tutarıyla örtüşmüyorsa).

    refund_amount hedefe çekilir; kalem net/brüt değerleri oransal ÖLÇEKLENİR (scale_items=false
    ile kapatılabilir) ki gider pusulası kalemleri de yeni toplamla birebir örtüşsün.
    Mevcut pusula varsa 'gider pusulası oluştur'a tekrar basılınca yeni tutarla güncellenir
    (numara korunur — idempotent)."""
    try:
        amt = round(float(payload.get("refund_amount")), 2)
    except Exception:
        amt = 0.0
    if amt <= 0:
        raise HTTPException(status_code=400, detail="Geçerli bir tutar girin (refund_amount)")
    claim = await db.trendyol_claims.find_one(
        {"claim_id": claim_id}, {"_id": 0, "items": 1, "refund_amount": 1})
    if not claim:
        raise HTTPException(status_code=404, detail="İade (claim) bulunamadı")
    now = datetime.now(timezone.utc).isoformat()
    items = claim.get("items") or []
    _old = round(float(claim.get("refund_amount") or 0), 2)
    _set = {"refund_amount": amt, "amount_overridden": True,
            "amount_overridden_by": current_user.get("email", ""), "amount_overridden_at": now,
            "updated_at": now}
    _cur = round(sum(float(i.get("price") or 0) * int(i.get("quantity") or 1) for i in items), 2)
    if items and _cur > 0 and payload.get("scale_items", True):
        ratio = amt / _cur
        for it in items:
            _old_unit = float(it.get("unit_price") or it.get("price") or 0)
            it["price"] = round(float(it.get("price") or 0) * ratio, 2)
            it["unit_price"] = round(_old_unit * ratio, 2)
            it["discount_amount"] = round(max(0.0, float(it["unit_price"]) - float(it["price"])), 2)
        _set["items"] = items
    await db.trendyol_claims.update_one({"claim_id": claim_id}, {"$set": _set})
    return {"success": True, "claim_id": claim_id, "refund_amount": amt, "old_refund_amount": _old,
            "scaled_items": bool(items and _cur > 0 and payload.get("scale_items", True))}
@router.get("/trendyol/claims/shipment-probe")
async def trendyol_shipment_probe(order_number: str = "", current_user: dict = Depends(require_admin)):
    """GEÇİCİ ARAŞTIRMA: Trendyol sipariş paketi servisini (getShipmentPackages) bir iadenin
    orderNumber'ı ile sorgular ve paket durum/satır alanlarını döndürür. Amaç: talep vs
    kargoya-verilen ayrımı için kullanılabilir bir 'shipped/returned' sinyali var mı görmek.
    """
    config = await get_trendyol_config()
    if not config.get("api_key") or not config.get("supplier_id"):
        raise HTTPException(status_code=400, detail="Trendyol kimliği yapılandırılmamış")
    # O10: Order V2 geçişi — legacy `/sapigw/suppliers/{sid}/orders` (Order V1) 15 Ekim 2026'da
    # kapanıyor. Elle V1 URL kurmak yerine zaten V2 (apigw.trendyol.com/integration/order/
    # sellers/{sid}/orders) olan TrendyolClient.get_orders'ı kullan — tek kaynak, test edilmiş.
    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"], api_key=config["api_key"],
        api_secret=config["api_secret"], mode=config["mode"],
    )
    sid = config["supplier_id"]
    probed = []
    # order_number verilmezse birkaç açık (Created) claim üzerinde dene
    targets = []
    if order_number:
        targets = [order_number]
    else:
        async for c in db.trendyol_claims.find({"claim_status": "Created"}, {"_id": 0, "order_number": 1}).limit(3):
            on = c.get("order_number")
            if on:
                targets.append(on)
    for on in targets:
        try:
            data = await client.get_orders(order_number=on)
            pkgs = []
            for pkg in (data.get("content") or [])[:5]:
                pkgs.append({
                    "id": pkg.get("id"),
                    "status": pkg.get("status"),
                    "cargoTrackingNumber": pkg.get("cargoTrackingNumber"),
                    "grossAmount": pkg.get("grossAmount"),
                    "totalDiscount": pkg.get("totalDiscount"),
                    "totalPrice": pkg.get("totalPrice"),
                    "packageTotalPrice": pkg.get("packageTotalPrice"),
                    "lines_status": [l.get("orderLineItemStatusName") for l in (pkg.get("lines") or [])[:4]],
                    # KESİN TUTAR KAYNAĞI: satır bazlı fiyat alanları (birim vs satır ayrımı için)
                    "lines_fin": [{
                        "barcode": l.get("barcode"),
                        "productName": l.get("productName"),
                        "quantity": l.get("quantity"),
                        "price": l.get("price"),
                        "amount": l.get("amount"),
                        "lineGrossAmount": l.get("lineGrossAmount"),
                        "discount": l.get("discount"),
                        "status": l.get("orderLineItemStatusName"),
                    } for l in (pkg.get("lines") or [])],
                    "top_keys": list(pkg.keys()),
                })
            probed.append({"order_number": on, "http": 200, "package_count": len(data.get("content") or []), "packages": pkgs})
        except Exception as e:
            probed.append({"order_number": on, "error": str(e)[:200]})
    return {"base": client.base_url, "supplier_id": sid, "probed": probed}
@router.get("/trendyol/claims/issue-reasons")
async def get_trendyol_issue_reasons(current_user: dict = Depends(require_admin)):
    """Fetch claim issue reasons from Trendyol"""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    try:
        url = f"{client.base_url}/order/sellers/{client.supplier_id}/claim-issue-reasons"
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            headers = client._get_headers()
            response = await http_client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch issue reasons: {str(e)}")
        # Return common fallback reasons
        return [
            {"id": 1, "name": "Kullanım Hatası / Tüketici Kaynaklı Hasar"},
            {"id": 2, "name": "Ürün Orijinal Kutusunda / Ambalajında Değil"},
            {"id": 4, "name": "Eksik Aksesuar / Parça"},
            {"id": 6, "name": "İade Süresi Geçmiş"},
            {"id": 21, "name": "Ürün Kullanılmış / Etiketi Koparılmış"}
        ]
@router.get("/trendyol/claims/{claim_id}/media-probe")
async def probe_claim_media(claim_id: str, current_user: dict = Depends(require_admin)):
    """TANI ucu: müşteri iade görselleri TY API'de hangi uçtan geliyor — adayları dener.
    (Görsel özelliği yerleşince kaldırılabilir; yalnız GET, veri değiştirmez.)"""
    config = await get_trendyol_config()
    if not config.get("is_active"):
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")
    from trendyol_client import TrendyolClient
    client = TrendyolClient(config["supplier_id"], config["api_key"], config["api_secret"])
    sid = config["supplier_id"]
    import httpx as _httpx
    candidates = [
        f"/order/sellers/{sid}/claims?claimIds={claim_id}",
        f"/order/sellers/{sid}/claims/{claim_id}/claim-issue-reasons",
        f"/order/sellers/{sid}/claims/{claim_id}/files",
        f"/order/sellers/{sid}/claims/{claim_id}/audit",
        f"/order/sellers/{sid}/claims/items/{claim_id}/media",
        f"/order/sellers/{sid}/claims/{claim_id}/gallery",
    ]
    out = []
    async with _httpx.AsyncClient(timeout=20.0) as hc:
        for ep in candidates:
            try:
                r = await hc.get(f"{client.base_url}{ep}", headers=client._get_headers())
                out.append({"endpoint": ep, "status": r.status_code, "body": r.text[:800]})
            except Exception as e:
                out.append({"endpoint": ep, "status": "err", "body": str(e)[:200]})
    return {"claim_id": claim_id, "results": out}


@router.get("/trendyol/claims/{claim_id}")
async def get_trendyol_claim_detail(claim_id: str, current_user: dict = Depends(require_admin)):
    """Tek bir iade/iptal kaydının detayını getir."""
    claim = await db.trendyol_claims.find_one({"claim_id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    return claim
@router.post("/trendyol/claims/vouchers/recompute")
async def recompute_claim_vouchers(payload: Optional[dict] = Body(default=None),
                                   current_user: dict = Depends(require_admin)):
    """ESKİ kesilen Trendyol/HB gider pusulalarını GÜNCEL tutar kurallarıyla yeniden
    hesaplar (tam iadede net = Trendyol'un gerçek iade tutarı; vade/kargo mutabakatı).
    - Numara ve previous_numbers KORUNUR (idempotent yeniden kesim).
    - KISMİ pusulalar korunur: pusuladaki kalemler claim kalemlerine (barkod+adet)
      eşlenip aynı seçim item_indexes olarak geçilir; eşlenemeyen atlanır.
    - Kurumsal/e-Fatura engeline takılanlar atlanır (değiştirilmez)."""
    payload = payload or {}
    limit = int(payload.get("limit", 5000) or 5000)
    only_mismatch = payload.get("only_mismatch", True)
    recomputed = changed = skipped = failed = 0
    errors = []
    async for gp in db.gider_pusulasi.find(
            {"claim_id": {"$exists": True, "$ne": ""}},
            {"_id": 0, "claim_id": 1, "items": 1, "totals": 1}).limit(limit):
        cid = str(gp.get("claim_id") or "")
        claim = await db.trendyol_claims.find_one({"claim_id": cid}, {"_id": 0, "items": 1, "refund_amount": 1})
        if not claim:
            skipped += 1
            continue
        gp_items = gp.get("items") or []
        cl_items = claim.get("items") or []
        sel_payload = None
        if gp_items and cl_items and len(gp_items) < len(cl_items):
            # Kısmi pusula: kalemleri (barkod, adet) ile claim index'lerine eşle
            used = set()
            idxs = []
            ok = True
            for gi in gp_items:
                g_bc = str(gi.get("barcode") or "").strip()
                g_q = int(gi.get("quantity", 1) or 1)
                hit = None
                for i, ci in enumerate(cl_items):
                    if i in used:
                        continue
                    if str(ci.get("barcode") or "").strip() == g_bc and int(ci.get("quantity", 1) or 1) == g_q:
                        hit = i
                        break
                if hit is None:
                    ok = False
                    break
                used.add(hit)
                idxs.append(hit)
            if not ok:
                skipped += 1
                continue
            sel_payload = {"item_indexes": idxs}
        else:
            # TAM pusula — yalnız tutarı güncel kuralla uyuşmayanları yeniden kes
            if only_mismatch:
                _old_net = round(float((gp.get("totals") or {}).get("net") or 0), 2)
                _ref = round(float(claim.get("refund_amount") or 0), 2)
                if _ref <= 0 or abs(_old_net - _ref) < 0.01:
                    skipped += 1
                    continue
        try:
            _old_net = round(float((gp.get("totals") or {}).get("net") or 0), 2)
            res = await generate_gider_pusulasi(cid, payload=sel_payload, current_user=current_user)
            recomputed += 1
            _new_net = round(float(((res or {}).get("gider_pusulasi") or {}).get("totals", {}).get("net") or 0), 2)
            if abs(_new_net - _old_net) >= 0.01:
                changed += 1
        except HTTPException as he:
            failed += 1
            errors.append({"claim_id": cid, "detail": str(he.detail)[:120]})
        except Exception as e:
            failed += 1
            errors.append({"claim_id": cid, "detail": str(e)[:120]})
    return {"success": True, "recomputed": recomputed, "amount_changed": changed,
            "skipped": skipped, "failed": failed, "errors": errors[:30]}


@router.post("/trendyol/claims/{claim_id}/gider-pusulasi")
async def generate_gider_pusulasi(claim_id: str, payload: Optional[dict] = Body(default=None), current_user: dict = Depends(require_admin)):
    """Generate expense receipt (gider pusulası) data for a return claim"""
    claim = await db.trendyol_claims.find_one({"claim_id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="İade kaydı bulunamadı")

    # HB KALEM-BAZLI BİRLEŞTİRME: Hepsiburada bir siparişin iadesini kalem kalem ayrı claim'lere
    # böler. Ekranda tek satırda birleştiriyoruz (bkz. _group_hb_claims_by_order); gider pusulası
    # da TÜM kardeş claim'lerin kalemlerini + tutarını kapsamalı ve hepsinin stoğunu geri eklemeli.
    _gp_claim_ids = [claim_id]
    if str(claim.get("platform") or "").lower() == "hepsiburada" and claim.get("order_number"):
        _sibs = await db.trendyol_claims.find(
            {"order_number": claim.get("order_number"), "platform": "hepsiburada"}, {"_id": 0}
        ).to_list(None)
        _sibs = [s for s in _sibs if _claim_bucket(s) != "iptal"]
        if len(_sibs) > 1:
            _m_items, _m_refund, _gp_claim_ids = [], 0.0, []
            for s in _sibs:
                _m_items.extend(s.get("items") or [])
                _m_refund += float(s.get("refund_amount") or 0)
                if s.get("claim_id"):
                    _gp_claim_ids.append(s.get("claim_id"))
            claim["items"] = _m_items
            claim["refund_amount"] = round(_m_refund, 2)

    # KATI KURAL: Kurumsal/e-Fatura siparişinde gider pusulası KESİNLİKLE düzenlenemez —
    # iade faturası gerekir (kullanıcı isteği; tek doğruluk kaynağı orders._order_is_efatura).
    _onum = claim.get("order_number", "")
    if _onum:
        from .orders import _order_is_efatura
        _ord = await db.orders.find_one(
            {"order_number": _onum},
            {"_id": 0, "invoice_type": 1, "billing_info": 1, "billing_address": 1})
        if _order_is_efatura(_ord or {}):
            raise HTTPException(status_code=400, detail="Bu sipariş kurumsal/e-Fatura siparişi — gider pusulası düzenlenemez. Müşteriden iade faturası gerekir (Doğan'dan panele düşecek, onaylayınca stok +1).")

    # İade onayımız = gider pusulası oluşturmak. Stoğu BİR KEZ geri ekle (idempotent) —
    # HB birleştirmede tüm kardeş claim'lerin stoğu geri eklenir.
    for _cid in _gp_claim_ids:
        await restock_claim_once(_cid, "gider_pusulasi", current_user.get("email", ""))

    settings = await db.settings.find_one({"id": "main"}, {"_id": 0})
    company = settings.get("company_info", {}) if settings else {}
    # Alıcı adresi claim'de tutulmuyor; sipariş kaydından çek
    order = await db.orders.find_one({"order_number": claim.get("order_number", "")}, {"_id": 0}) or {}
    _ship = order.get("shipping_address", {}) or {}
    _cust_name = claim.get("customer_name", "") or (f"{_ship.get('first_name','')} {_ship.get('last_name','')}".strip())
    _cust_addr = _ship.get("address", "") or (claim.get("shipping_address", "") if isinstance(claim.get("shipping_address"), str) else "")
    _cust_district = _ship.get("district", "")
    _cust_city = _ship.get("city", "") or claim.get("shipping_city", "")
    _cust_country = _ship.get("country", "") or "Türkiye"

    _all_items = claim.get("items", [])
    items = _all_items
    # Kısmi gider pusulası: yalnızca seçili kalemler (item_indexes verilirse SADECE onlar hesaplanır)
    _sel_idx = (payload or {}).get("item_indexes")
    _is_partial = False
    if isinstance(_sel_idx, list) and _sel_idx:
        _filtered = []
        for _i in _sel_idx:
            try:
                _filtered.append(items[int(_i)])
            except Exception:
                continue
        if _filtered and len(_filtered) < len(_all_items):
            items = _filtered
            _is_partial = True
    _item_net_sum = round(sum(item.get("price", 0) * item.get("quantity", 1) for item in items), 2)
    # TUTARLILIK (kullanıcı isteği): TAM iadede net, TRENDYOL'un verdiği gerçek iade tutarını
    # (claim.refund_amount) baz alır — fatura/İYS/pusula hepsi Trendyol'un kuruşuyla ÖRTÜŞSÜN.
    # Kalem toplamı yalnızca kısmi iadede ya da Trendyol tutarı yoksa kullanılır.
    _ty_refund = round(float(claim.get("refund_amount") or 0), 2)
    total_net = (_ty_refund if (not _is_partial and _ty_refund > 0) else _item_net_sum)
    total_discount = sum(item.get("discount_amount", 0) * item.get("quantity", 1) for item in items)
    total_gross = sum(item.get("unit_price", 0) * item.get("quantity", 1) for item in items)
    vat_rate = settings.get("default_vat_rate", 10) if settings else 10
    vat_amount = round(total_net * vat_rate / (100 + vat_rate), 2)
    net_without_vat = round(total_net - vat_amount, 2)

    # İDEMPOTENT numara: bu claim için pusula zaten varsa numarayı KORU (yeniden düzenleme
    # yeni numara yakmaz — tutar düzeltmesi mevcut pusulanın numarasını değiştirmez).
    _existing_gp = await db.gider_pusulasi.find_one(
        {"claim_id": claim_id}, {"_id": 0, "number": 1, "display_number": 1})
    # #15: İlk gider pusulası kesildikten SONRA yeniden oluşturma yalnızca Finans
    # (muhasebe) yetkisiyle. İlk oluşturma her admin; 2. ve sonrası returns.expense_note ister.
    if _existing_gp:
        _perms = await get_effective_permissions(current_user)
        if "*" not in _perms and "returns.expense_note" not in _perms:
            raise HTTPException(status_code=403,
                detail="Bu iade için gider pusulası zaten oluşturulmuş; yeniden oluşturma yalnızca Finans (muhasebe) yetkisine sahip kullanıcı tarafından yapılabilir.")
    tracking_no = str((payload or {}).get("tracking_no") or "").strip()
    if _existing_gp and _existing_gp.get("number"):
        gp_number = _existing_gp["number"]
        display_number = tracking_no or _existing_gp.get("display_number") or f"GP-{gp_number:06d}"
    else:
        last_gp = await db.gider_pusulasi.find_one({}, sort=[("number", -1)])
        gp_number = (last_gp.get("number", 0) + 1) if last_gp else 1
        display_number = tracking_no if tracking_no else f"GP-{gp_number:06d}"

    # Kalemleri ürün kataloğundaki beden ile zenginleştir (barkod -> variant.size)
    gp_items = []
    for _it in items:
        _bc = str(_it.get("barcode", "") or "").strip()
        _size = ""
        if _bc:
            _pv = await db.products.find_one({"variants.barcode": _bc}, {"_id": 0, "variants": 1})
            if _pv:
                for _v in (_pv.get("variants") or []):
                    if str(_v.get("barcode")) == _bc:
                        _size = _v.get("size") or _v.get("beden") or ""
                        break
        gp_items.append({
            "name": _it.get("productName", ""),
            "barcode": _bc,
            "size": _size,
            "quantity": _it.get("quantity", 1),
            "unit_price": _it.get("unit_price", 0),
            "discount": _it.get("discount_amount", 0),
            "net_price": _it.get("price", 0),
            "reason": _it.get("reason", ""),
        })

    # Yuvarlama mutabakatı: kalem net'leri toplamı, Trendyol'un tutarına (total_net) BİREBİR
    # eşitlenir (kuruş farkı son ürün satırına yazılır) → pusula ↔ fatura ↔ Trendyol tutarı örtüşür.
    if gp_items and total_net > 0:
        _cur = round(sum(round(float(g.get("net_price", 0)), 2) * int(g.get("quantity", 1) or 1) for g in gp_items), 2)
        _diff = round(total_net - _cur, 2)
        if abs(_diff) >= 0.01:
            _last = gp_items[-1]
            _q = int(_last.get("quantity", 1) or 1)
            _last["net_price"] = round(float(_last.get("net_price", 0)) + _diff / _q, 2)
            _last["discount"] = round(max(0.0, float(_last.get("unit_price", 0)) - float(_last["net_price"])), 2)

    gider_pusulasi = {
        "number": gp_number,
        "display_number": display_number,
        "claim_id": claim_id,
        "order_number": claim.get("order_number", ""),
        "date": datetime.now(timezone.utc).isoformat(),
        "company": company,
        "customer": {
            "name": _cust_name,
            "address": _cust_addr,
            "district": _cust_district,
            "city": _cust_city,
            "country": _cust_country,
        },
        "sales_invoice_no": claim.get("invoice_number", ""),
        "cargo_company": claim.get("cargo_provider_name", ""),
        "sales_rep": "",
        "items": gp_items,
        "totals": {
            "gross": total_gross,
            "discount": total_discount,
            "net": total_net,
            "vat_rate": vat_rate,
            "vat_amount": vat_amount,
            "net_without_vat": net_without_vat,
        },
        "claim_type": claim.get("claim_type", ""),
        "claim_reason": claim.get("claim_reason", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Yeniden kesimde koçan numarası DEĞİŞİYORSA eski numarayı kaybetme (muhasebe
    # mutabakatı: fiziki eski kağıt durur — hangi numaranın iptal/yenilenmiş olduğu
    # previous_numbers'tan izlenir; 1217 vs 840 sayım farkının bir nedeni buydu).
    _gp_upd = {"$set": gider_pusulasi}
    _old_no = (_existing_gp or {}).get("display_number")
    if _old_no and _old_no != display_number:
        _gp_upd["$addToSet"] = {"previous_numbers": _old_no}
    await db.gider_pusulasi.update_one(
        {"claim_id": claim_id},
        _gp_upd,
        upsert=True
    )

    await db.trendyol_claims.update_one(
        {"claim_id": claim_id},
        {"$set": {"has_gider_pusulasi": True, "gider_pusulasi_no": gider_pusulasi["display_number"]}}
    )

    return {"success": True, "gider_pusulasi": gider_pusulasi}
@router.post("/trendyol/claims/gp-bulk-range")
async def gp_bulk_by_range(payload: dict, current_user: dict = Depends(require_admin)):
    """İADE ONAY TARİHİ aralığındaki YALNIZ ONAYLANMIŞ ve pusulası HENÜZ OLMAYAN iadeler için
    (site + Trendyol + Hepsiburada tek havuz, İADE ONAY tarihine göre sıralı)
    toplu gider pusulası keser (kullanıcı isteği).

    payload: {date_from, date_to, sources:["site","trendyol","hepsiburada"],
              start_no:"085500", limit:50, dry_run:false}
    dry_run=true → kesmeden aday listesini döndürür (önizleme/kontrollü kesim)."""
    date_from = str(payload.get("date_from") or "").strip()
    date_to = str(payload.get("date_to") or "").strip()
    sources = payload.get("sources") or ["site", "trendyol", "hepsiburada"]
    limit = max(1, min(int(payload.get("limit") or 50), 2000))
    dry = bool(payload.get("dry_run"))
    start_no = str(payload.get("start_no") or "").strip()
    try:
        base = int(start_no) if start_no else None
    except ValueError:
        base = None
    if not dry and base is None:
        raise HTTPException(status_code=400, detail="Başlangıç koçan numarası (start_no) zorunlu")

    def _in_range(v):
        s = str(v or "")[:10]
        if not s:
            return False
        if date_from and s < date_from:
            return False
        if date_to and s > date_to:
            return False
        return True

    # Pusulası zaten olanlar atlanır — ölçüt KOÇAN NUMARASI: numarası temizlenmiş
    # (clear-numbers) pusulalar havuza GERİ girer ve yeni sıralı numara alır.
    _has_no = {"$or": [{"number": {"$exists": True, "$ne": None}},
                       {"display_number": {"$exists": True, "$nin": ["", None]}}]}
    gp_claims = {str(g.get("claim_id")) for g in await db.gider_pusulasi.find(
        {"claim_id": {"$exists": True, "$ne": ""}, **_has_no},
        {"_id": 0, "claim_id": 1}).to_list(None)}
    gp_returns = {str(g.get("return_id")) for g in await db.gider_pusulasi.find(
        {"return_id": {"$exists": True, "$ne": ""}, **_has_no},
        {"_id": 0, "return_id": 1}).to_list(None)}

    # TARİH ÖLÇÜTÜ = İADE ONAY TARİHİ (Kadir isteği: 'iade onay tarihine göre filtrele+sırala').
    # TY/HB: return_approved_at (yoksa created_date); site: customer_returns.approval.at (yoksa created_at).
    cands = []
    if "trendyol" in sources or "hepsiburada" in sources:
        async for c in db.trendyol_claims.find(
                {"claim_status": "Accepted"},
                {"_id": 0, "claim_id": 1, "order_number": 1, "customer_name": 1,
                 "created_date": 1, "return_approved_at": 1, "refund_amount": 1,
                 "platform": 1, "has_gider_pusulasi": 1}):
            plat = "hepsiburada" if str(c.get("platform") or "").lower() == "hepsiburada" else "trendyol"
            if plat not in sources:
                continue
            cid = str(c.get("claim_id") or "")
            if not cid or cid.startswith("ord:"):
                continue
            if c.get("has_gider_pusulasi") or cid in gp_claims:
                continue
            _appr = str(c.get("return_approved_at") or c.get("created_date") or "")
            if not _in_range(_appr):
                continue
            cands.append({"kaynak": plat, "key": cid, "siparis": c.get("order_number") or "",
                          "musteri": c.get("customer_name") or "",
                          "tarih": _appr[:10],
                          "tutar": round(float(c.get("refund_amount") or 0), 2)})
    if "site" in sources:
        async for r in db.customer_returns.find(
                {"status": {"$in": ["approved", "refunded", "partial_refunded"]}},
                {"_id": 0, "id": 1, "order_number": 1, "created_at": 1, "approval": 1,
                 "refund_amount": 1, "refund_breakdown": 1}):
            rid = str(r.get("id") or "")
            if not rid or rid in gp_returns:
                continue
            _appr = str((r.get("approval") or {}).get("at") or r.get("created_at") or "")
            if not _in_range(_appr):
                continue
            # Onaydaki kargo kararı (deducted → kargoyu müşteriden kes) — GP hesabında baz alınır.
            _inc_cargo = bool((r.get("refund_breakdown") or {}).get("cargo", {}).get("mode") == "deducted")
            cands.append({"kaynak": "site", "key": rid, "siparis": r.get("order_number") or "",
                          "musteri": "", "tarih": _appr[:10], "_inc_cargo": _inc_cargo,
                          "tutar": round(float(r.get("refund_amount") or 0), 2)})

    # KATI KURAL: e-Fatura/kurumsal siparişler toplu kesim HAVUZUNDAN tamamen çıkarılır
    # (kullanıcı isteği) — bu siparişlere gider pusulası KESİLMEZ, iade faturası gerekir.
    from .orders import _order_is_efatura
    _onums = list({c.get("siparis") for c in cands if c.get("siparis")})
    _efatura_onums = set()
    _name_by_num = {}  # site adayının müşteri adı sipariş shipping_address'ten (Kadir: 'adı yok').
    if _onums:
        async for _o in db.orders.find(
                {"order_number": {"$in": _onums}},
                {"_id": 0, "order_number": 1, "invoice_type": 1,
                 "billing_info": 1, "billing_address": 1, "shipping_address": 1, "customer_name": 1}):
            if _order_is_efatura(_o):
                _efatura_onums.add(str(_o.get("order_number")))
            _sh = _o.get("shipping_address") or {}
            _nm = (f"{_sh.get('first_name','')} {_sh.get('last_name','')}".strip()
                   or _sh.get("full_name") or _sh.get("name") or _o.get("customer_name") or "")
            if _nm:
                _name_by_num[str(_o.get("order_number"))] = _nm
    _elenen_efatura = len([c for c in cands if str(c.get("siparis")) in _efatura_onums])
    cands = [c for c in cands if str(c.get("siparis")) not in _efatura_onums]
    # Site adaylarına müşteri adını doldur (TY/HB'de zaten var).
    for c in cands:
        if c.get("kaynak") == "site" and not c.get("musteri"):
            c["musteri"] = _name_by_num.get(str(c.get("siparis")), "")

    cands.sort(key=lambda x: x["tarih"])  # İADE ONAY tarihine göre eskiden yeniye (3 kaynak tek havuz)
    batch = cands[:limit]
    # Kaynak dağılımı (kullanıcı: 'site/HB önizlemeye gelmiyor' — havuzda var mı doğrula).
    from collections import Counter
    _dag = Counter(c["kaynak"] for c in cands)
    if dry:
        # SİTE adaylarının TUTAR'ını GERÇEK gider pusulası hesabıyla (kesimle AYNI, preview=persist YOK)
        # doldur — raw refund_amount çoğu 0/yanlış (Kadir). Kargo kararı onaydan (_inc_cargo) taşınır.
        from .orders import site_return_gider_pusulasi
        for c in batch:
            if c.get("kaynak") != "site":
                continue
            try:
                _res = await site_return_gider_pusulasi(
                    c["key"], payload={"include_cargo": bool(c.get("_inc_cargo"))},
                    preview=True, current_user=current_user)
                _net = ((_res or {}).get("gider_pusulasi") or {}).get("totals", {}).get("net")
                if _net is not None:
                    c["tutar"] = round(float(_net), 2)
            except Exception:
                pass
        return {"dry_run": True, "toplam_aday": len(cands), "bu_partide": len(batch),
                "elenen_efatura": _elenen_efatura,
                "kaynak_dagilim": {"site": _dag.get("site", 0), "trendyol": _dag.get("trendyol", 0),
                                   "hepsiburada": _dag.get("hepsiburada", 0)},
                "adaylar": batch}

    from .orders import site_return_gider_pusulasi
    kesilen, hatalar = [], []
    n = 0
    pusulalar = []  # YAZDIRMA için tam gider pusulası belgeleri (frontend 4'lü A4'e basar)
    for c in batch:
        tno = f"{base + n:06d}"
        try:
            if c["kaynak"] == "site":
                res = await site_return_gider_pusulasi(
                    c["key"], payload={"tracking_no": tno, "include_cargo": bool(c.get("_inc_cargo"))},
                    preview=False, current_user=current_user)
            else:
                res = await generate_gider_pusulasi(c["key"], {"tracking_no": tno}, current_user)
            gp = (res or {}).get("gider_pusulasi") or {}
            kesilen.append({**c, "gp_no": gp.get("display_number") or tno,
                            "net": (gp.get("totals") or {}).get("net")})
            if gp:
                pusulalar.append({**gp, "assigned_no": tno})
            n += 1
        except HTTPException as he:
            hatalar.append({**c, "hata": str(he.detail)[:140]})
        except Exception as e:
            hatalar.append({**c, "hata": str(e)[:140]})
    next_no = f"{base + n:06d}"
    return {"success": True, "kesilen": len(kesilen), "hata": len(hatalar),
            "kalan_aday": max(0, len(cands) - len(batch)), "next_no": next_no,
            "detay": kesilen, "hatalar": hatalar[:20], "pusulalar": pusulalar}


@router.post("/trendyol/claims/bulk-gider-pusulasi")
async def bulk_generate_gider_pusulasi(payload: dict, current_user: dict = Depends(require_admin)):
    """Generate expense receipts for multiple claims"""
    claim_ids = payload.get("claim_ids", [])
    if not claim_ids:
        raise HTTPException(status_code=400, detail="Claim ID listesi boş")

    start_no = str(payload.get("start_no") or "").strip()
    try:
        base = int(start_no) if start_no else None
    except ValueError:
        base = None

    results = []
    n = 0
    for cid in claim_ids:
        try:
            tno = f"{base + n:06d}" if base is not None else None
            result = await generate_gider_pusulasi(cid, {"tracking_no": tno} if tno else None, current_user)
            results.append(result.get("gider_pusulasi"))
            n += 1
        except Exception:
            pass

    next_no = f"{base + n:06d}" if base is not None else ""
    return {"success": True, "gider_pusulalari": results, "count": len(results), "next_no": next_no}
@router.post("/trendyol/products/{product_id}/update-stock-price")
async def update_trendyol_stock_price(
    product_id: str,
    current_user: dict = Depends(require_admin)
):
    """Tek bir ürünün stok ve fiyatını Trendyol'a gönderir."""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    # Varyantlı ürün mü?
    items = []
    variants = product.get("variants", [])
    # O10: Toplu senkron _mp_base_price (üye fiyatı) + ürün-özel trendyol_multiplier kullanır;
    # tekil uç ise product.price + yalnızca config markup kullanıyordu → aynı barkoda tekil vs
    # toplu farklı fiyat gidip Trendyol'da fiyat oynuyordu. Aynı taban ve çarpan mantığı uygulanır.
    _default_markup = float(config.get("default_markup", 0) or 0)
    _mult = product.get("trendyol_multiplier")
    _markup = float(_mult) if (_mult is not None and float(_mult) > 0) else _default_markup
    _factor = 1 + _markup / 100.0
    trendyol_multiplier = _markup  # geri uyumluluk (aşağıda kullanılıyorsa)
    base_price = _mp_base_price(product) * _factor
    sale_price = base_price  # Trendyol: indirimsiz satis fiyati

    if variants:
        for v in variants:
            barcode = v.get("barcode", "")
            if not barcode:
                continue
            v_price = base_price + (v.get("price_diff", 0) or 0)
            v_sale = sale_price + (v.get("price_diff", 0) or 0)
            items.append({
                "barcode": barcode,
                "quantity": v.get("stock", 0),
                "salePrice": round(v_sale, 2),
                "listPrice": round(v_price, 2)
            })
    else:
        barcode = product.get("barcode", "")
        if barcode:
            items.append({
                "barcode": barcode,
                "quantity": product.get("stock", 0),
                "salePrice": round(sale_price, 2),
                "listPrice": round(base_price, 2)
            })

    if not items:
        raise HTTPException(status_code=400, detail="Ürünün barkodu bulunamadı")

    try:
        result = await client.update_price_and_inventory(items)
    except Exception as e:
        logger.error(f"Trendyol stock/price update error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    batch_id = result.get("batchRequestId", "")
    await db.products.update_one(
        {"id": product_id},
        {"$set": {"trendyol_stock_price_batch": str(batch_id), "trendyol_stock_price_updated": datetime.now(timezone.utc).isoformat()}}
    )

    return {
        "success": True,
        "message": f"{len(items)} kalem stok/fiyat güncellendi",
        "batch_id": batch_id,
        "items_count": len(items)
    }
@router.post("/trendyol/categories/{category_id}/update-stock-price")
async def update_trendyol_category_stock_price(
    category_id: str,
    current_user: dict = Depends(require_admin)
):
    """Bir kategorideki tüm ürünlerin stok ve fiyatlarını Trendyol'a gönderir."""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    # Kategorideki tüm ürünleri bul
    category = await db.categories.find_one({"id": category_id}, {"_id": 0})
    if not category:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")

    products = await db.products.find(
        {"category_name": category.get("name"), "is_active": True},
        {"_id": 0}
    ).to_list(500)

    if not products:
        # Fallback: try category id
        products = await db.products.find(
            {"category_id": category_id, "is_active": True},
            {"_id": 0}
        ).to_list(500)

    if not products:
        raise HTTPException(status_code=404, detail="Bu kategoride ürün bulunamadı")

    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    items = []
    _default_markup = float(config.get("default_markup", 0) or 0)
    for product in products:
        # O10: toplu senkronla aynı taban (_mp_base_price) + ürün-özel çarpan.
        _mult = product.get("trendyol_multiplier")
        _markup = float(_mult) if (_mult is not None and float(_mult) > 0) else _default_markup
        _factor = 1 + _markup / 100.0
        base_price = _mp_base_price(product) * _factor
        sale_price = base_price  # Trendyol: indirimsiz satis fiyati

        variants = product.get("variants", [])
        if variants:
            for v in variants:
                barcode = v.get("barcode", "")
                if not barcode:
                    continue
                v_price = base_price + (v.get("price_diff", 0) or 0)
                v_sale = sale_price + (v.get("price_diff", 0) or 0)
                items.append({
                    "barcode": barcode,
                    "quantity": v.get("stock", 0),
                    "salePrice": round(v_sale, 2),
                    "listPrice": round(v_price, 2)
                })
        else:
            barcode = product.get("barcode", "")
            if barcode:
                items.append({
                    "barcode": barcode,
                    "quantity": product.get("stock", 0),
                    "salePrice": round(sale_price, 2),
                    "listPrice": round(base_price, 2)
                })

    if not items:
        raise HTTPException(status_code=400, detail="Bu kategorideki ürünlerin barkodu bulunamadı")

    # Trendyol max 1000 item per request (v2 client metodu — güncel endpoint)
    batch_ids = []
    for i in range(0, len(items), 1000):
        chunk = items[i:i+1000]
        try:
            result = await client.update_price_and_inventory(chunk)
            batch_ids.append(result.get("batchRequestId", ""))
        except Exception as e:
            logger.error(f"Trendyol category stock/price update error: {str(e)}")

    return {
        "success": True,
        "message": f"{category.get('name')} kategorisindeki {len(items)} kalem stok/fiyat güncellendi",
        "items_count": len(items),
        "batch_ids": batch_ids
    }
@router.get("/trendyol/cargo/label/{shipment_package_id}")
async def get_trendyol_cargo_label_pkg(
    shipment_package_id: str,
    current_user: dict = Depends(require_admin)
):
    """Trendyol kargo etiketi PDF/ZPL verisini getirir."""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    try:
        url = f"{client.base_url}/shipment/sellers/{client.supplier_id}/shipment-packages/{shipment_package_id}/shipping-label"
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            headers = client._get_headers()
            response = await http_client.get(url, headers=headers)
            response.raise_for_status()
            
            content_type = response.headers.get("content-type", "")
            
            if "application/pdf" in content_type:
                return Response(
                    content=response.content,
                    media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=label_{shipment_package_id}.pdf"}
                )
            else:
                # ZPL veya text format
                return Response(
                    content=response.content,
                    media_type=content_type or "application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename=label_{shipment_package_id}"}
                )
    except httpx.HTTPStatusError as e:
        logger.error(f"Cargo label error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=f"Etiket alınamadı: {e.response.text}")
    except Exception as e:
        logger.error(f"Cargo label error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
@router.post("/trendyol/claims/{claim_id}/approve")
async def approve_trendyol_claim(
    claim_id: str,
    payload: dict,
    current_user: dict = Depends(require_admin)
):
    """Approve a list of claim items in Trendyol"""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    claim_item_ids = payload.get("claim_item_ids", [])
    if not claim_item_ids:
        raise HTTPException(status_code=400, detail="Onaylanacak iade kalemleri belirtilmedi.")

    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    try:
        url = f"{client.base_url}/order/sellers/{client.supplier_id}/claims/{claim_id}/items/approve"
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            headers = client._get_headers()
            req_data = {
                "claimLineItemIdList": claim_item_ids
            }
            response = await http_client.put(url, headers=headers, json=req_data)
            response.raise_for_status()
            
            await log_integration_event("trendyol", "claim_approve", current_user["email"], claim_id, "success", f"{len(claim_item_ids)} kalem onaylandı", req_data)
            
            # Update claim in DB with action status
            await db.trendyol_claims.update_one(
                {"claim_id": claim_id},
                {"$set": {
                    "panel_action": "approved",
                    "panel_action_date": datetime.now(timezone.utc).isoformat(),
                    "panel_action_by": current_user["email"],
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )

            # A7: İade onaylanınca stok otomatik geri iade — ATOMİK (A1/A2 fix).
            try:
                claim_doc = await db.trendyol_claims.find_one({"claim_id": claim_id}, {"_id": 0, "items": 1, "order_number": 1, "stock_restored": 1})
                # A1: guard'ı ATOMİK al — onay + GP(restock_claim_once) çift restock yapmasın.
                _lock = await db.trendyol_claims.update_one(
                    {"claim_id": claim_id, "stock_restored": {"$ne": True}},
                    {"$set": {"stock_restored": True, "stock_restored_at": datetime.now(timezone.utc).isoformat(),
                              "stock_restored_source": "approve"}})
                _now = datetime.now(timezone.utc).isoformat()
                restocked_items = []
                _cids = {str(x) for x in claim_item_ids}
                for item in (((claim_doc or {}).get("items") or []) if _lock.modified_count else []):
                    if str(item.get("claim_item_id", "")) not in _cids:
                        continue
                    barcode = item.get("barcode", "")
                    qty = int(item.get("quantity", 1) or 1)
                    if not barcode:
                        continue
                    # A2: ATOMİK varyant $inc (tüm diziyi $set etme — oversell/sürüklenme yok)
                    prod = await db.products.find_one({"variants.barcode": barcode}, {"_id": 0, "id": 1})
                    if prod:
                        await db.products.update_one(
                            {"id": prod["id"], "variants.barcode": barcode},
                            {"$inc": {"variants.$[v].stock": qty}, "$set": {"updated_at": _now}},
                            array_filters=[{"v.barcode": barcode}])
                        await db.products.update_one(
                            {"id": prod["id"]},
                            [{"$set": {"stock": {"$sum": {"$map": {"input": {"$ifNull": ["$variants", []]},
                                "as": "vv", "in": {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}}}}}}])
                        restocked_items.append({"barcode": barcode, "qty": qty, "product_id": prod["id"]})
                    else:
                        p2 = await db.products.find_one({"barcode": barcode}, {"_id": 0, "id": 1})
                        if p2:
                            await db.products.update_one(
                                {"id": p2["id"]}, {"$inc": {"stock": qty}, "$set": {"updated_at": _now}})
                            restocked_items.append({"barcode": barcode, "qty": qty, "product_id": p2["id"]})

                # Stok hareketi loglama — A1: order_id ile de (sipariş yolu guard'ı görsün)
                if restocked_items:
                    _onum = str((claim_doc or {}).get("order_number") or "")
                    _o = await db.orders.find_one({"order_number": _onum}, {"_id": 0, "id": 1}) if _onum else None
                    await db.stock_movements.insert_one({
                        "id": str(uuid.uuid4()),
                        "type": "return_restock",
                        "claim_id": claim_id,
                        "order_id": (_o or {}).get("id"),
                        "order_number": _onum,
                        "items": restocked_items,
                        "created_by": current_user["email"],
                        "created_at": _now,
                    })
            except Exception as restock_err:
                logger.error(f"Restock after claim approve failed: {restock_err}")
                # non-fatal

            return {"success": True, "message": "İade işlemi Trendyol tarafında onaylandı."}

    except httpx.HTTPStatusError as e:
        logger.error(f"Claim approve error: {e.response.status_code} - {e.response.text}")
        await log_integration_event("trendyol", "claim_approve", current_user["email"], claim_id, "error", f"API Hatası: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=f"Onay işlemi başarısız: {e.response.text}")
    except Exception as e:
        logger.error(f"Claim approve error: {str(e)}")
        await log_integration_event("trendyol", "claim_approve", current_user["email"], claim_id, "error", str(e))
        raise HTTPException(status_code=500, detail=str(e))
@router.post("/trendyol/claims/{claim_id}/issue")
async def issue_trendyol_claim(
    claim_id: str,
    payload: dict,
    current_user: dict = Depends(require_admin)
):
    """Reject/Issue a list of claim items in Trendyol"""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    claim_item_ids = payload.get("claim_item_ids", [])
    issue_reason_id = payload.get("issue_reason_id")
    description = payload.get("description", "")

    if not claim_item_ids or not issue_reason_id:
        raise HTTPException(status_code=400, detail="İtiraz edilecek kalemler veya itiraz sebebi belirtilmedi.")

    from trendyol_client import TrendyolClient
    client = TrendyolClient(
        supplier_id=config["supplier_id"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        mode=config["mode"]
    )

    try:
        url = f"{client.base_url}/order/sellers/{client.supplier_id}/claims/{claim_id}/issue"
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            headers = client._get_headers()
            req_data = {
                "claimIssueReasonId": int(issue_reason_id),
                "claimItemIdList": claim_item_ids,
                "description": description
            }
            response = await http_client.post(url, headers=headers, json=req_data)
            response.raise_for_status()
            
            await log_integration_event("trendyol", "claim_issue", current_user["email"], claim_id, "success", f"{len(claim_item_ids)} kalem için itiraz açıldı", req_data)
            
            # Update claim in DB with action status
            await db.trendyol_claims.update_one(
                {"claim_id": claim_id},
                {"$set": {
                    "panel_action": "issued",
                    "panel_action_date": datetime.now(timezone.utc).isoformat(),
                    "panel_action_by": current_user["email"],
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            
            return {"success": True, "message": "İade işlemi için Trendyol tarafında itiraz oluşturuldu."}

    except httpx.HTTPStatusError as e:
        logger.error(f"Claim issue error: {e.response.status_code} - {e.response.text}")
        await log_integration_event("trendyol", "claim_issue", current_user["email"], claim_id, "error", f"API Hatası: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=f"İtiraz işlemi başarısız: {e.response.text}")
    except Exception as e:
        logger.error(f"Claim issue error: {str(e)}")
        await log_integration_event("trendyol", "claim_issue", current_user["email"], claim_id, "error", str(e))
        raise HTTPException(status_code=500, detail=str(e))
@router.post("/trendyol/invoices/{order_number}")
async def upload_invoice_to_trendyol(order_number: str, payload: dict, current_user: dict = Depends(require_admin)):
    """Upload invoice link to Trendyol for a given order number"""
    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapilandirilmamis")

    invoice_link = payload.get("invoice_link", "").strip()
    invoice_number = payload.get("invoice_number", "").strip()
    if not invoice_link:
        raise HTTPException(status_code=400, detail="Fatura linki bos olamaz")

    order = await db.orders.find_one({"order_number": order_number, "platform": "trendyol"})
    if not order:
        raise HTTPException(status_code=404, detail="Siparis bulunamadi")

    package_id = order.get("trendyol_package_id")
    if not package_id:
        raise HTTPException(status_code=400, detail="Trendyol paket ID bulunamadi")

    supplier_id = config["supplier_id"]
    headers = await get_trendyol_headers()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # O9: Legacy `sapigw` gateway kapatildi (client apigw.trendyol.com/integration'a
            # tasindi). Fatura linki gonderimi guncel `seller-invoice-links` ucunu kullanir.
            url = f"https://apigw.trendyol.com/integration/sellers/{supplier_id}/seller-invoice-links"
            body = {
                "invoiceLink": invoice_link,
                "shipmentPackageId": int(package_id) if str(package_id).isdigit() else package_id,
                "invoiceNumber": invoice_number or f"FAT-{order_number}",
                "invoiceDateTime": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()

        await db.orders.update_one(
            {"order_number": order_number},
            {"$set": {"invoice_link": invoice_link, "invoice_number": invoice_number, "invoice_uploaded_at": datetime.now(timezone.utc).isoformat()}}
        )

        await log_integration_event("trendyol", "upload_invoice", current_user["email"], order_number, "success", "Fatura yuklendi", body)
        return {"success": True, "message": "Fatura Trendyol'a basariyla yuklendi"}
    except httpx.HTTPStatusError as e:
        logger.error(f"Invoice upload error: {e.response.text}")
        await log_integration_event("trendyol", "upload_invoice", current_user["email"], order_number, "error", e.response.text)
        raise HTTPException(status_code=e.response.status_code, detail=f"Fatura yukleme hatasi: {e.response.text}")
    except Exception as e:
        logger.error(f"Invoice upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
@router.post("/trendyol/products/{product_id}/sync")
async def sync_product_to_trendyol(product_id: str, current_user: dict = Depends(require_admin)):
    """Tekil ürün senkronu — TOPLU (doğru/validasyonlu) yola DELEGE eder.

    Eski tekil implementasyon ayrı ve eksik bir kopyaydı: required-attribute gap-fill,
    Facette sabit varsayılanları, kategori meta/allowCustom doğrulaması ve value-id
    doğrulaması YOKTU. Bu yüzden mp_attr_id KeyError, int('XXS') çökmesi ve enum reddi
    gibi hatalar veriyor; per-ürün "Trendyol'a Gönder" ve "Başarısız Aktarımlar → tekrar
    dene" butonlarını kırıyordu. Artık toplu senkron gövdesine yönlendirir → tek ve doğru
    kod yolu (kategori eşleştirme/zorunlu özellik doğrulaması aynen uygulanır)."""
    class _ReqShim:
        async def json(self):
            return {"product_ids": [product_id]}
    res = await sync_products_to_trendyol(_ReqShim(), current_user)
    # Tekil UX: ürün tümüyle başarısızsa HTTP hata olarak yükselt (buton kırmızı görsün,
    # Trendyol'un gerçek sebebini göstersin: görsel/mapping/beden vb.).
    try:
        if isinstance(res, dict) and not res.get("successful") and (res.get("failed") or res.get("errors")):
            _errs = "; ".join(res.get("errors") or []) or res.get("message") or "Trendyol aktarımı başarısız"
            raise HTTPException(status_code=400, detail=_errs)
    except HTTPException:
        raise
    except Exception:
        pass
    return res
