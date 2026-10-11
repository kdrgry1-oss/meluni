"""
Product Attributes routes - CRUD and Sync
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from bson.objectid import ObjectId
import logging
import re

from .deps import db, require_admin, generate_id, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attributes", tags=["Attributes"])

class AttributeBase(BaseModel):
    name: str
    values: List[str] = []

class AttributeCreate(AttributeBase):
    pass

class AttributeUpdate(BaseModel):
    # Kısmi güncelleme: yalnız gönderilen alanlar yazılır. Eski istemciler
    # {name, values} gönderir → değişmez çalışır. Ayar kartı yalnız tek alan
    # (default_value / show_in_product_card / our_required) gönderebilir.
    name: Optional[str] = None
    values: Optional[List[str]] = None
    default_value: Optional[str] = None
    show_in_product_card: Optional[bool] = None
    our_required: Optional[bool] = None
    # Kategori-bazlı "bizim için zorunlu": bu özelliğin ZORUNLU sayılacağı YEREL kategori
    # id'lerinin listesi. our_required (tüm-sistem) yanına EK; doğrulama her ikisini de üretir.
    category_required: Optional[List[str]] = None
    # "Ürün verisinden çek ama ürün kartında gösterme": değerleri variants/attributes'tan
    # otomatik toplanır (Beden/Renk gibi), ama kartta gizlenir (show_in_product_card ile birlikte).
    collect_from_products: Optional[bool] = None


def _norm_attr(s: str) -> str:
    """Türkçe-duyarsız normalize (İ/ı/ş/ğ/ü/ö/ç + birleşik nokta). İsim eşleştirme için."""
    s = (s or "").casefold()
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"),
                 ("ü", "u"), ("ö", "o"), ("ç", "c"), ("̇", "")):
        s = s.replace(a, b)
    return s.strip()


def _with_setting_defaults(attr: dict) -> dict:
    """Ayar kartı alanları için güvenli varsayılanlar (eski dokümanlar için)."""
    attr.setdefault("default_value", "")
    attr.setdefault("show_in_product_card", True)
    attr.setdefault("our_required", False)
    attr.setdefault("category_required", [])
    attr.setdefault("collect_from_products", False)
    return attr


@router.get("")
async def get_attributes(current_user: dict = Depends(require_permission("products.view", any_in_area=True))):
    """Get all global product attributes"""
    try:
        attrs = await db.attributes.find({}, {"_id": 0}).sort("name", 1).to_list(1000)
        for a in attrs:
            _with_setting_defaults(a)
        return {"success": True, "attributes": attrs}
    except Exception as e:
        logger.error(f"Error fetching attributes: {e}")
        raise HTTPException(status_code=500, detail="Özellikler alınamadı.")

@router.post("")
async def create_attribute(req: AttributeCreate, current_user: dict = Depends(require_permission("products.attributes"))):
    """Create a new global product attribute"""
    try:
        existing = await db.attributes.find_one({"name": {"$regex": f"^{re.escape(req.name)}$", "$options": "i"}})
        if existing:
            raise HTTPException(status_code=400, detail="Bu özellik zaten mevcut.")

        attr_doc = {
            "id": generate_id(),
            "name": req.name.strip(),
            "values": list(set([str(v).strip() for v in req.values if str(v).strip()])),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        await db.attributes.insert_one(attr_doc)
        attr_doc["_id"] = str(attr_doc["_id"])
        return {"success": True, "attribute": attr_doc, "message": "Özellik oluşturuldu"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating attribute: {e}")
        raise HTTPException(status_code=500, detail="Özellik oluşturulamadı.")

@router.put("/{attr_id}")
async def update_attribute(attr_id: str, req: AttributeUpdate, current_user: dict = Depends(require_permission("products.attributes"))):
    """KISMİ güncelleme: yalnız gönderilen alanları yazar.
    Eski istemci {name, values} gönderir → aynen çalışır. Ayar kartı tek bir alanı
    (default_value / show_in_product_card / our_required) da gönderebilir; diğerlerine dokunulmaz.
    default_value BOŞ string gönderilebilir (kullanıcı varsayılanı SİLER) → DB'de "" olarak tutulur
    ve seed bunu yeniden DAYATMAZ (default_value alanı artık mevcut)."""
    try:
        existing = await db.attributes.find_one({"id": attr_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Özellik bulunamadı.")

        update: dict = {}

        if req.name is not None:
            new_name = req.name.strip()
            if new_name:
                duplicate = await db.attributes.find_one({
                    "id": {"$ne": attr_id},
                    "name": {"$regex": f"^{re.escape(new_name)}$", "$options": "i"}
                })
                if duplicate:
                    raise HTTPException(status_code=400, detail="Bu isimde başka bir özellik zaten mevcut.")
                update["name"] = new_name

        if req.values is not None:
            update["values"] = list(set([str(v).strip() for v in req.values if str(v).strip()]))

        if req.default_value is not None:
            # Boş string DE geçerlidir (kullanıcı siler) → alanı "" olarak yaz.
            update["default_value"] = str(req.default_value).strip()

        if req.show_in_product_card is not None:
            update["show_in_product_card"] = bool(req.show_in_product_card)

        if req.our_required is not None:
            update["our_required"] = bool(req.our_required)

        if req.category_required is not None:
            # Yerel kategori id'lerini temizle + tekilleştir (sıra korunur).
            _seen = set()
            _cats = []
            for _c in req.category_required:
                _cid = str(_c).strip()
                if _cid and _cid not in _seen:
                    _seen.add(_cid)
                    _cats.append(_cid)
            update["category_required"] = _cats

        if req.collect_from_products is not None:
            update["collect_from_products"] = bool(req.collect_from_products)

        if not update:
            return {"success": True, "message": "Değişiklik yok"}

        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.attributes.update_one({"id": attr_id}, {"$set": update})
        return {"success": True, "message": "Özellik güncellendi"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating attribute: {e}")
        raise HTTPException(status_code=500, detail="Özellik güncellenemedi.")


@router.get("/{attr_id}/required-in")
async def get_attribute_required_in(attr_id: str, current_user: dict = Depends(require_permission("products.view", any_in_area=True))):
    """Bu özelliğin ADIYLA eşleşerek hangi pazaryerinin hangi kategorilerinde ZORUNLU olduğunu
    listeler. Kaynak: category_mappings (matched) + pazaryeri kategori-attribute cache'leri
    (trendyol/hepsiburada/temu_category_attributes). İsim eşleştirme Türkçe-duyarsız normalize."""
    attr = await db.attributes.find_one({"id": attr_id}, {"_id": 0})
    if not attr:
        raise HTTPException(status_code=404, detail="Özellik bulunamadı.")
    target = _norm_attr(attr.get("name") or "")
    if not target:
        return {"success": True, "attribute": attr.get("name"), "grouped": {}, "required_in": []}

    marketplaces = [
        ("trendyol", "trendyol_category_attributes"),
        ("hepsiburada", "hepsiburada_category_attributes"),
        ("temu", "temu_category_attributes"),
    ]

    grouped: dict = {}
    flat: list = []

    for mp, coll in marketplaces:
        try:
            mappings = await db.category_mappings.find(
                {"marketplace": mp, "marketplace_category_id": {"$nin": [None, ""]}},
                {"_id": 0, "category_name": 1, "marketplace_category_id": 1},
            ).to_list(length=5000)
        except Exception:
            mappings = []
        if not mappings:
            continue

        # mp_cat_id -> bu kategoride hedef özellik zorunlu mu? (cache'i tekilleştir)
        req_cache: dict = {}

        async def _is_required(mp_cat_id) -> bool:
            key = str(mp_cat_id)
            if key in req_cache:
                return req_cache[key]
            required = False
            try:
                cache_key = int(mp_cat_id) if str(mp_cat_id).isdigit() else str(mp_cat_id)
                doc = await db[coll].find_one({"category_id": cache_key}, {"_id": 0, "attributes": 1})
            except Exception:
                doc = None
            for a in (doc or {}).get("attributes", []) or []:
                if not isinstance(a, dict):
                    continue
                aname = a.get("name") or (a.get("attribute", {}) or {}).get("name") or ""
                if _norm_attr(aname) != target:
                    continue
                is_req = bool(
                    a.get("required") or a.get("mandatory") or a.get("mandatoryVariant")
                    or (a.get("attribute", {}) or {}).get("required")
                )
                if is_req:
                    required = True
                    break
            req_cache[key] = required
            return required

        seen_cats = set()
        cat_names = []
        for cm in mappings:
            mp_cat_id = cm.get("marketplace_category_id")
            cname = (cm.get("category_name") or "").strip()
            dedup = (str(mp_cat_id), cname)
            if dedup in seen_cats:
                continue
            seen_cats.add(dedup)
            if await _is_required(mp_cat_id):
                if cname and cname not in cat_names:
                    cat_names.append(cname)
                flat.append({"marketplace": mp, "category_name": cname})
        if cat_names:
            grouped[mp] = sorted(cat_names)

    return {"success": True, "attribute": attr.get("name"), "grouped": grouped, "required_in": flat}

@router.delete("/{attr_id}")
async def delete_attribute(attr_id: str, current_user: dict = Depends(require_permission("products.attributes"))):
    """Delete a global product attribute"""
    try:
        result = await db.attributes.delete_one({"id": attr_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Özellik bulunamadı.")
        return {"success": True, "message": "Özellik silindi"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting attribute: {e}")
        raise HTTPException(status_code=500, detail="Özellik silinemedi.")

@router.post("/sync-from-trendyol")
async def sync_attributes_from_trendyol(payload: dict = Body(default={}), current_user: dict = Depends(require_permission("products.attributes"))):
    """Trendyol kategori cache'lerinden özellik DEĞERLERİNİ global attributes'a çeker.

    SCOPE: `attribute_name` GÖNDERİLİRSE (Ayar Kartı 'Trendyol'dan Aktar' butonu SEÇİLİ
    özelliğin adını yollar) YALNIZ o özellik doldurulur — Türkçe-duyarsız isim eşleşen
    Trendyol attribute'larının değerleri o özelliğe eklenir; BAŞKA özellik OLUŞTURULMAZ/
    KİRLETİLMEZ. `attribute_name` yoksa eski davranış (tümü) korunur (geri uyumluluk)."""
    try:
        scope_name = str((payload or {}).get("attribute_name") or "").strip()
        scope_norm = _norm_attr(scope_name) if scope_name else ""

        trendyol_caches = await db.trendyol_attributes.find({}).to_list(1000)
        if not trendyol_caches:
            return {"success": False, "message": "Trendyol'dan çekilmiş kategori özelliği bulunamadı. Önce kategorilerde 'Özellikler' butonuna tıklayarak bazı kategorileri çekin."}

        # Extract unique names and unique values per name
        attribute_map = {}
        for cache in trendyol_caches:
            attrs = cache.get("attributes", [])
            for attr in attrs:
                # attr format: {"attribute": {"name": "Renk"}, "attributeValues": [{"name": "Kırmızı"}]}
                attr_name = attr.get("name") or (attr.get("attribute", {}).get("name"))
                if not attr_name:
                    continue

                attr_name = attr_name.strip()
                # SCOPE: yalnız seçili özelliğin Trendyol karşılığındaki değerleri topla.
                if scope_norm and _norm_attr(attr_name) != scope_norm:
                    continue
                if attr_name not in attribute_map:
                    attribute_map[attr_name] = set()

                vals = attr.get("attributeValues", [])
                for val in vals:
                    val_name = val.get("name")
                    if val_name:
                        attribute_map[attr_name].add(str(val_name).strip())

        if not attribute_map:
            if scope_norm:
                return {"success": False, "message": f"'{scope_name}' için Trendyol'da eşleşen özellik/değer bulunamadı. Bu özelliği içeren bir kategoriyi 'Özellikler'den çekin."}
            return {"success": False, "message": "Kategorilerin içinde geçerli bir özellik veya değer bulunamadı."}

        # SCOPE: değerleri TEK hedef özelliğe (kullanıcının seçtiği ad) yaz — Trendyol'da
        # ad farklı yazılmış olabilir (ör. "Beden" vs "Beden Grubu"); hepsini seçili özelliğe topla.
        if scope_norm:
            merged = set()
            for _vs in attribute_map.values():
                merged |= _vs
            attribute_map = {scope_name: merged}

        # Upsert into db.attributes
        new_count = 0
        update_count = 0
        for attr_name, val_set in attribute_map.items():
            existing = await db.attributes.find_one({"name": {"$regex": f"^{re.escape(attr_name)}$", "$options": "i"}})
            if existing:
                # Merge values
                current_vals = set(existing.get("values", []))
                merged_vals = list(current_vals.union(val_set))
                if len(merged_vals) > len(current_vals):
                    await db.attributes.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {"values": merged_vals, "updated_at": datetime.now(timezone.utc).isoformat()}}
                    )
                    update_count += 1
            else:
                # Create new
                await db.attributes.insert_one({
                    "id": generate_id(),
                    "name": attr_name,
                    "values": list(val_set),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                new_count += 1
                
        if scope_norm:
            _tot = sum(len(v) for v in attribute_map.values())
            _msg = (f"'{scope_name}' özelliğine Trendyol'dan {_tot} değer eklendi/güncellendi."
                    if (new_count or update_count) else
                    f"'{scope_name}' için yeni değer yok (zaten güncel).")
            return {"success": True, "message": _msg}
        return {"success": True, "message": f"Trendyol'dan {new_count} yeni özellik eklendi, {update_count} özellik güncellendi."}
    except Exception as e:
        logger.error(f"Error syncing from trendyol: {e}")
        raise HTTPException(status_code=500, detail="Trendyol özellikleri senkronize edilemedi.")

async def urunlerden_ozellik_haritasi() -> dict:
    """Katalogdaki TÜM özellik değerlerini toplar: {"Renk": {"Siyah", …}, …}

    ÜÇ KAYNAK — eskiden yalnız birincisi vardı ve o da yanlış alandan okuyordu:
      1. Ürünün `attributes` listesi. Eski kod yalnız `type` anahtarına
         bakıyordu; bizim ürettiğimiz kayıtlar `name` taşıyor, bu yüzden
         kütüphane hep boş kalıyordu ("Sistemde tanımlı 0 özellik var").
      2. VARYANT renk/bedenleri — hiç okunmuyordu. Oysa renk ve beden yalnız
         varyantta duruyor; pazaryeri eşleştirmesi de değeri buradan bekliyor.
      3. Ürün düzeyindeki `color`.

    Renkler tek biçime getirilir (SİYAH/Haki̇ → Siyah/Haki), aksi hâlde
    kütüphanede aynı rengin üç yazımı birden birikiyor.
    """
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from product_attributes import renk_duzelt
    except Exception:
        def renk_duzelt(x):
            return str(x or "").strip()

    harita: dict = {}

    def _ekle(ad, deger):
        ad = str(ad or "").strip()
        deger = str(deger or "").strip()
        if not ad or not deger:
            return
        harita.setdefault(ad, set()).add(deger)

    async for product in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "attributes": 1, "variants": 1, "color": 1}):
        attrs = product.get("attributes")
        if isinstance(attrs, list):
            for attr in attrs:
                if isinstance(attr, dict):
                    _ekle(attr.get("name") or attr.get("type") or attr.get("label"),
                          attr.get("value"))
        elif isinstance(attrs, dict):
            for anahtar, deger in attrs.items():
                if isinstance(deger, dict):
                    _ekle(deger.get("label") or anahtar, deger.get("value"))
                else:
                    _ekle(anahtar, deger)

        for v in (product.get("variants") or []):
            if not isinstance(v, dict):
                continue
            _ekle("Renk", renk_duzelt(v.get("color")))
            _ekle("Beden", v.get("size"))
        _ekle("Renk", renk_duzelt(product.get("color")))

    return harita


@router.post("/sync-from-products")
async def sync_attributes_from_products(current_user: dict = Depends(require_permission("products.attributes"))):
    """Scan all existing products' attributes arrays and populate the global attributes list"""
    try:
        # PERF/GÜVENLİK: yalnız gerekli alanları çek (base64 görselli tam doküman çekmek
        # belleği/aktarımı aşıp 500 veriyordu). Filtre YOK: Beden/Renk varyantlarda olduğundan
        # attributes'ı olmayan ürünler de taranmalı.
        attribute_map = {}
        async for product in db.products.find(
                {}, {"_id": 0, "attributes": 1, "variants.size": 1, "variants.color": 1, "color": 1}):
            try:
                attrs = product.get("attributes")
                if isinstance(attrs, list):
                    for attr in attrs:
                        if not isinstance(attr, dict):
                            continue
                        attr_type = attr.get("type") or attr.get("name")
                        if not attr_type:
                            continue
                        attr_type = str(attr_type).strip()
                        attribute_map.setdefault(attr_type, set())
                        attr_val = attr.get("value")
                        if attr_val:
                            attribute_map[attr_type].add(str(attr_val).strip())
                # BEDEN/RENK VARYANTTA DURUR → variants[].size/color'dan da topla (kullanıcı bildirdi).
                for _v in (product.get("variants") or []):
                    if not isinstance(_v, dict):
                        continue
                    _sz = str(_v.get("size") or "").strip()
                    if _sz:
                        attribute_map.setdefault("Beden", set()).add(_sz)
                    _cl = str(_v.get("color") or _v.get("renk") or "").strip()
                    if _cl:
                        attribute_map.setdefault("Renk", set()).add(_cl)
                _pc = str(product.get("color") or "").strip()
                if _pc:
                    attribute_map.setdefault("Renk", set()).add(_pc)
            except Exception:
                continue  # bir ürün bozuksa atla, tarama devam etsin

        if not attribute_map:
            return {"success": False, "message": "Ürünlerinizin içinde herhangi bir özellik (Beden, Renk vb.) bulunamadı."}
            
        # Upsert into db.attributes
        new_count = 0
        update_count = 0
        for attr_name, val_set in attribute_map.items():
            existing = await db.attributes.find_one({"name": {"$regex": f"^{re.escape(attr_name)}$", "$options": "i"}})
            if existing:
                current_vals = set(existing.get("values", []))
                merged_vals = list(current_vals.union(val_set))
                if len(merged_vals) > len(current_vals):
                    await db.attributes.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {"values": merged_vals, "updated_at": datetime.now(timezone.utc).isoformat()}}
                    )
                    update_count += 1
            else:
                await db.attributes.insert_one({
                    "id": generate_id(),
                    "name": attr_name,
                    "values": list(val_set),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                new_count += 1
                
        return {"success": True, "message": f"Mevcut ürünlerden {new_count} yeni özellik eklendi, {update_count} özellik güncellendi."}
    except Exception as e:
        logger.error(f"Error syncing from products: {e}")
        raise HTTPException(status_code=500, detail="Ürün özellikleri taraması başarısız oldu.")


@router.post("/cleanup-non-textile")
async def cleanup_non_textile_attributes(current_user: dict = Depends(require_permission("products.attributes"))):
    """Remove non-textile attributes from the library"""
    remove_names = [
        "Kadran Renk", "Kasa Materyali", "Kasa Renk", "Kasa Çapı", "Kordon Materyali", "Kordon Renk",
        "Mekanizma", "Cam Tipi", "Cam Şekli", "Dönence", "Su Geçirmezlik", "Kutu Durumu",
        "Batarya Boyutu", "Batarya Türü", "Mp3 Çalar", "CE Uygunluk Sembolu", "Berraklık",
        "Raf Sayısı", "Sineklik", "Tekerlek", "Taşıma Kapasitesi", "Taşıma Çantası", "Klips Sayısı",
        "Altın Ayar", "Karat", "Taş Cinsi", "Ayar",
        "TEST_Attr1", "TEST_Attr2", "Test", "Test Kalıp", "Test Kumaş",
        "Birincil İthalatçı Adres Bilgisi", "Birincil İthalatçı Adı", "Birincil İthalatçı Mail Adresi",
        "İkincil İthalatçı Adres Bilgisi", "İkincil İthalatçı Adı", "İkincil İthalatçı Mail Adresi",
        "Üçüncül İthalatçı Adres Bilgisi", "Üçüncül İthalatçı Adı", "Üçüncül İthalatçı Mail Adresi",
        "Üretici Adres Bilgisi", "Üretici Adı", "Üretici Mail Adresi",
        "Paket Derinlik", "Paket Genişlik", "Paket Yükseklik", "Paket İçeriği",
        "Paket Görseli (arka)", "Paket Görseli (ön)",
        "Ağırlık", "Boyut/Ebat", "Derinlik", "Genişlik", "Yükseklik", "Ölçü",
        "Alt Açma Ünitesi", "Bant Stili", "Garanti Süresi", "Kap", "Karakter",
        "Kullanım Alanı", "Kullanım Talimatı/Uyarıları", "Model", "Parça Sayısı",
        "Teknik", "Tema / Stil", "Özellik", "Ürün Tipi", "Yaş", "Ara Kat"
    ]
    result = await db.attributes.delete_many({"name": {"$in": remove_names}})
    return {"success": True, "deleted": result.deleted_count, "message": f"{result.deleted_count} alakasız özellik silindi"}


@router.post("/bulk-set-defaults")
async def bulk_set_default_attributes(current_user: dict = Depends(require_permission("products.attributes"))):
    """Set Yaş Grubu=Yetişkin, Menşei=TR ve Sürdürülebilirlik Detayı=Hayır
    for all products that don't have them"""
    _defaults = [("Yaş Grubu", "Yetişkin"), ("Menşei", "TR"),
                 ("Sürdürülebilirlik Detayı", "Hayır")]
    products = await db.products.find({}, {"_id": 0, "id": 1, "attributes": 1}).to_list(None)
    updated = 0
    for p in products:
        attrs = p.get("attributes") or []
        # Veri hijyeni: bazı eski kayıtlarda attributes listesi string öğeler
        # içerebiliyor ('str' has no attribute 'get' → 500). Dict olmayanları atla.
        if isinstance(attrs, dict):
            changed = False
            for _nm, _val in _defaults:
                if not attrs.get(_nm):
                    attrs[_nm] = _val
                    changed = True
            if changed:
                await db.products.update_one(
                    {"id": p["id"]},
                    {"$set": {"attributes": attrs, "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
                updated += 1
            continue
        if not isinstance(attrs, list):
            continue
        have = set()
        for a in attrs:
            if isinstance(a, dict):
                nm = a.get("type") or a.get("name")
                if nm:
                    have.add(nm)
            elif isinstance(a, str) and a.strip():
                have.add(a.strip())
        changed = False

        for _nm, _val in _defaults:
            if _nm not in have:
                attrs.append({"type": _nm, "name": _nm, "value": _val})
                changed = True

        if changed:
            await db.products.update_one(
                {"id": p["id"]},
                {"$set": {"attributes": attrs, "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
            updated += 1

    # Also ensure these values exist in attribute library
    for attr_name, val in [("Yaş Grubu", "Yetişkin"), ("Menşei", "TR")]:
        existing = await db.attributes.find_one({"name": attr_name})
        if existing:
            vals = existing.get("values", [])
            if val not in vals:
                vals.append(val)
                await db.attributes.update_one({"name": attr_name}, {"$set": {"values": vals}})
        else:
            await db.attributes.insert_one({
                # generate_short_id ithal EDİLMEMİŞTİ; üstelik async ve koleksiyon adı
                # ister — bu çağrı her yeni özellik eklemede NameError veriyordu.
                # Dosyanın diğer dört insert'i zaten generate_id() kullanıyor.
                "id": generate_id(),
                "name": attr_name,
                "values": [val],
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    return {"success": True, "updated": updated, "message": f"{updated} ürüne Yaş Grubu ve Menşei eklendi"}


async def seed_attribute_defaults():
    """Koddaki MELUNI sabit varsayılanlarını (meluni_defaults.MELUNI_FIXED_ATTR_DEFAULTS —
    Cinsiyet=Kadın, Menşei=Türkiye, Yaş Grubu=Yetişkin, Ek Özellik=Yok, Ortam/Koleksiyon=
    Casual/Günlük, Kutu Durumu=Kutu Yok, Persona=Fashion Forward, Performans=Cool & Comfort,
    Sürdürülebilirlik Detayı=Hayır) attributes koleksiyonunun `default_value` alanına TOHUMLAR.

    Böylece bu kurallar Özellik Ayar Kartı'nda GÖRÜNÜR ve kullanıcı düzenleyebilir/silebilir.

    İDEMPOTENT + DB-OTORİTE:
      - Yalnız `default_value` alanı HİÇ YOKSA yazar. Kullanıcı UI'dan silince alan "" olarak
        KALIR (mevcut) → seed bir daha DAYATMAZ. Yani DB otoritedir, kod yeniden empoze etmez.
      - Eşleşen özellik yoksa OLUŞTURUR (görünür/düzenlenebilir olsun diye), values=[].
    İsim eşleştirme Türkçe-duyarsız normalize ile yapılır (mevcut kayıt varsa ona yazar).
    """
    try:
        from meluni_defaults import MELUNI_FIXED_ATTR_DEFAULTS
    except Exception as e:
        logger.error(f"seed_attribute_defaults: meluni_defaults import edilemedi: {e}")
        return {"created": 0, "seeded": 0}

    existing = await db.attributes.find({}, {"_id": 0, "id": 1, "name": 1, "default_value": 1}).to_list(5000)
    by_norm = {}
    for a in existing:
        by_norm.setdefault(_norm_attr(a.get("name") or ""), a)

    created = 0
    seeded = 0
    now = datetime.now(timezone.utc).isoformat()
    for name, value in MELUNI_FIXED_ATTR_DEFAULTS.items():
        norm = _norm_attr(name)
        doc = by_norm.get(norm)
        if doc:
            # Alan HİÇ yoksa tohumla; "" (kullanıcı silmiş) ise DOKUNMA.
            if "default_value" not in doc:
                await db.attributes.update_one(
                    {"id": doc["id"]},
                    {"$set": {"default_value": value, "updated_at": now}},
                )
                seeded += 1
        else:
            new_doc = {
                "id": generate_id(),
                "name": name,
                "values": [],
                "default_value": value,
                "show_in_product_card": True,
                "our_required": False,
                "seeded_default": True,
                "created_at": now,
                "updated_at": now,
            }
            await db.attributes.insert_one(new_doc)
            by_norm[norm] = new_doc
            created += 1

    return {"created": created, "seeded": seeded}
