"""
=============================================================================
ticimax_stock_sync.py — Ticimax Web Servis Stok Senkronizasyonu
=============================================================================
Amaç: Ticimax SelectUrun + SelectVaryasyon ile canlı stok değerlerini çekip
yerel `products` koleksiyonunda hem ürün top-level `stock` alanını hem de
`variants[].stock` alanlarını günceller.

Eşleme stratejisi (öncelik sırası):
  1. csv_card_id == Ticimax UrunKartiID (ID alanı)
  2. variants[*].id  == Varyasyon.ID (Ticimax)
  3. variants[*].stock_code == Varyasyon.StokKodu
  4. variants[*].barcode == Varyasyon.Barkod

ENDPOINT:
  POST /api/admin/ticimax/sync-stock?max_products=500&aktif=1
=============================================================================
"""
from fastapi import APIRouter, Depends, Query, BackgroundTasks
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import sys, os

# routes -> backend path
_BACKEND_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_PATH not in sys.path:
    sys.path.insert(0, _BACKEND_PATH)

from .deps import db, logger, require_admin
from .marketplace_hub import log_integration_event

router = APIRouter(prefix="/admin/ticimax", tags=["admin-ticimax-stock"])


def _to_dict(zo) -> Dict:
    if zo is None: return {}
    if hasattr(zo, "__values__"):
        try: return dict(zo.__values__)
        except Exception: return {}
    return zo if isinstance(zo, dict) else {}


def _unwrap_variants(raw) -> List[Dict]:
    """`Varyasyonlar` Zeep ArrayOfVaryasyon → list[dict]"""
    if not raw:
        return []
    if hasattr(raw, "__values__"):
        try:
            vals = list(raw.__values__.values())
            raw = vals[0] if vals else []
        except Exception:
            raw = []
    if isinstance(raw, dict):
        # Bazen { "Varyasyon": [...] } formatı
        if "Varyasyon" in raw:
            raw = raw["Varyasyon"]
    if not isinstance(raw, list):
        raw = [raw] if raw else []
    return [_to_dict(v) for v in raw if v]


def _deep_serialize(o, depth=0):
    """Zeep nesnesini JSON'a çevrilebilir yapıya derinlemesine aç (probe/backfill için)."""
    if depth > 8:
        return str(o)
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    if isinstance(o, (list, tuple)):
        return [_deep_serialize(x, depth + 1) for x in o]
    if hasattr(o, "__values__"):
        try:
            return {k: _deep_serialize(v, depth + 1) for k, v in dict(o.__values__).items()}
        except Exception:
            return str(o)
    if isinstance(o, dict):
        return {k: _deep_serialize(v, depth + 1) for k, v in o.items()}
    try:
        from datetime import datetime as _dt, date as _d
        if isinstance(o, (_dt, _d)):
            return o.isoformat()
    except Exception:
        pass
    try:
        from decimal import Decimal as _Dec
        if isinstance(o, _Dec):
            return float(o)
    except Exception:
        pass
    return str(o)


@router.get("/orders/probe")
async def ticimax_orders_probe(
    start_date: str, end_date: str, page: int = 1,
    include_marketplace: bool = True,
    current_user: dict = Depends(require_admin),
):
    """Ticimax WS'ten örnek sipariş yapısını döndürür — geçmiş sipariş backfill'inin
    alan eşlemesini canlı şemayla doğrulamak için (yalnız admin)."""
    import asyncio
    from ticimax_client import get_orders as tc_get_orders
    s = await db.settings.find_one({"id": "ticimax"}) or {}
    api_key = s.get("api_key") or "AKG0M8DTRSEBAIA898JA6HW22EDIU3"
    orders = await asyncio.to_thread(
        tc_get_orders, page, 5, api_key, start_date, end_date,
        not include_marketplace, False)
    return {"count": len(orders), "samples": [_deep_serialize(o) for o in orders[:2]]}


@router.post("/sync-stock")
async def sync_ticimax_stock(
    max_products: int = Query(2000, ge=10, le=20000),
    aktif: Optional[int] = Query(None, description="1=aktif, 0=pasif, None=hepsi"),
    page_size: int = Query(50, ge=10, le=100),
    current_user: dict = Depends(require_admin),
):
    """Ticimax SelectUrun ile canlı stok değerlerini çek + DB'de güncelle.

    Returns:
      {
        success, ticimax_total, fetched, matched_products, updated_variants,
        not_found, errors, duration_sec
      }
    """
    # Facette stok master — Ticimax stok senkronu varsayılan KAPALI; stoğu EZMESİN.
    # Yalnızca settings.ticimax.stock_sync_enabled=True ise çalışır.
    _cfg = await db.settings.find_one({"id": "ticimax"}) or {}
    if not _cfg.get("stock_sync_enabled"):
        return {
            "success": False,
            "disabled": True,
            "updated_variants": 0,
            "matched_products": 0,
            "message": "Ticimax stok senkronu kapalı (Facette stok master). Stok yalnızca sipariş/iptal/iade ile yönetilir.",
        }
    started = datetime.now(timezone.utc)
    from ticimax_client import get_products, get_product_count  # type: ignore

    settings = await db.settings.find_one({"id": "ticimax"}) or {}
    api_key = settings.get("api_key") or "AKG0M8DTRSEBAIA898JA6HW22EDIU3"
    _dom = settings.get("domain")
    if _dom:
        try:
            from ticimax_client import set_domain as _set_dom  # type: ignore
            _set_dom(_dom)
        except Exception as _e:
            logger.warning(f"[ticimax] set_domain failed: {_e}")

    # 1) Toplam sayı (UI için bilgi)
    try:
        ticimax_total = get_product_count(aktif=aktif, wscode=api_key)
    except Exception as e:
        logger.warning(f"[stock-sync] product count error: {e}")
        ticimax_total = 0

    # 2) Sayfa sayfa SelectUrun
    fetched: List[Dict] = []
    pages_to_fetch = (min(max_products, ticimax_total or max_products) // page_size) + 1
    for page in range(1, pages_to_fetch + 2):
        try:
            chunk = get_products(page=page, page_size=page_size, aktif=aktif, wscode=api_key)
        except Exception as e:
            logger.warning(f"[stock-sync] page {page} err: {e}")
            break
        if not chunk:
            break
        fetched.extend(chunk)
        if len(fetched) >= max_products:
            break
        # Ticimax SelectUrun KayitSayisinaGoreGetir=True olduğu için
        # offset bazlı pagination doğru çalışıyor.
    if len(fetched) > max_products:
        fetched = fetched[:max_products]

    # 3) Her Ticimax ürünü için DB'deki eşleşeni bul ve stok güncelle
    matched_products = 0
    updated_variants_total = 0
    errors: List[str] = []
    not_found_ids: List[int] = []

    for raw in fetched:
        d = _to_dict(raw)
        if not d:
            continue
        tc_card_id = d.get("ID") or d.get("UrunKartiID")
        try:
            tc_card_id = int(tc_card_id) if tc_card_id else None
        except Exception:
            tc_card_id = None

        ticimax_total_stock = float(d.get("ToplamStokAdedi") or 0)
        variants_raw = _unwrap_variants(d.get("Varyasyonlar"))

        # ÖNCELİK 1: csv_card_id eşleşmesi
        # O6: csv_card_id ürünlerde STRING olarak saklanıyor (bkz. dedupe_card_ids / products.py
        # str-cast). int ile sorgulanınca Mongo hiç eşleşmiyordu → kart-ID eşleşmesi hep düşer,
        # ürünler not_found'a düşer ama senkron "başarılı" raporlardı. Her iki tipi de sorgula.
        product_doc = None
        if tc_card_id:
            product_doc = await db.products.find_one(
                {"csv_card_id": {"$in": [tc_card_id, str(tc_card_id)]}},
                {"_id": 0, "id": 1, "variants": 1}
            )

        # ÖNCELİK 2: ticimax variant barkod/stock_code'larından bizdeki ürünü bul
        if not product_doc and variants_raw:
            tv_codes = [str(v.get("StokKodu") or "").strip() for v in variants_raw if v.get("StokKodu")]
            tv_bars = [str(v.get("Barkod") or "").strip() for v in variants_raw if v.get("Barkod")]
            or_clauses = []
            if tv_codes:
                or_clauses.append({"variants.stock_code": {"$in": tv_codes}})
                or_clauses.append({"stock_code": {"$in": tv_codes}})
            if tv_bars:
                or_clauses.append({"variants.barcode": {"$in": tv_bars}})
                or_clauses.append({"barcode": {"$in": tv_bars}})
            if or_clauses:
                product_doc = await db.products.find_one(
                    {"$or": or_clauses}, {"_id": 0, "id": 1, "variants": 1}
                )

        # ÖNCELİK 3: Top-level StokKodu (Varyasyon yoksa) - tek varyantlı ürünler
        if not product_doc:
            top_code = str(d.get("StokKodu") or "").strip()
            top_bar  = str(d.get("Barkod") or d.get("BarkodNo") or "").strip()
            tcl = []
            if top_code:
                tcl.append({"variants.stock_code": top_code})
                tcl.append({"stock_code": top_code})
            if top_bar:
                tcl.append({"variants.barcode": top_bar})
                tcl.append({"barcode": top_bar})
            if tcl:
                product_doc = await db.products.find_one(
                    {"$or": tcl}, {"_id": 0, "id": 1, "variants": 1}
                )

        if not product_doc:
            if tc_card_id:
                not_found_ids.append(tc_card_id)
            continue
        matched_products += 1

        # Variant bazlı eşleme map: by ID, stock_code, barcode (case-insensitive)
        local_variants = product_doc.get("variants") or []
        var_map: Dict[Any, Dict] = {}
        for lv in local_variants:
            for k in ("id", "stock_code", "barcode"):
                v = lv.get(k)
                if v:
                    var_map[(k, str(v).strip())] = lv

        new_variants: List[Dict] = list(local_variants)
        new_variants_lookup = {lv.get("id"): idx for idx, lv in enumerate(new_variants) if lv.get("id")}
        local_total_stock = 0
        for tv in variants_raw:
            tv_id   = tv.get("ID") or tv.get("VaryasyonID")
            tv_code = str(tv.get("StokKodu") or "").strip()
            tv_bar  = str(tv.get("Barkod") or "").strip()
            tv_stk  = float(tv.get("StokAdedi") or 0)

            local_v = None
            if tv_id and ("id", str(tv_id)) in var_map:
                local_v = var_map[("id", str(tv_id))]
            elif tv_code and ("stock_code", tv_code) in var_map:
                local_v = var_map[("stock_code", tv_code)]
            elif tv_bar and ("barcode", tv_bar) in var_map:
                local_v = var_map[("barcode", tv_bar)]

            if local_v is not None:
                lv_id = local_v.get("id")
                if lv_id in new_variants_lookup:
                    idx = new_variants_lookup[lv_id]
                    if new_variants[idx].get("stock") != int(tv_stk):
                        new_variants[idx] = {**new_variants[idx], "stock": int(tv_stk)}
                        updated_variants_total += 1
                    local_total_stock += int(tv_stk)
            else:
                local_total_stock += int(tv_stk)

        # Variant yoksa top-level stok kullan
        if not variants_raw and ticimax_total_stock > 0:
            local_total_stock = int(ticimax_total_stock)

        new_stock = int(ticimax_total_stock if ticimax_total_stock > 0 else local_total_stock)
        try:
            await db.products.update_one(
                {"id": product_doc["id"]},
                {"$set": {
                    "stock": new_stock,
                    "variants": new_variants,
                    "stock_synced_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }}
            )
        except Exception as e:
            errors.append(f"product {product_doc['id']}: {e}")

    duration = (datetime.now(timezone.utc) - started).total_seconds()

    # Log'a yaz
    await log_integration_event(
        marketplace="ticimax", action="stock_sync", status="success",
        direction="inbound",
        message=(f"Stok senkronu: {matched_products}/{len(fetched)} ürün eşleşti, "
                 f"{updated_variants_total} varyasyon güncellendi"),
    )

    return {
        "success": True,
        "ticimax_total": ticimax_total,
        "fetched": len(fetched),
        "matched_products": matched_products,
        "updated_variants": updated_variants_total,
        "not_found_in_db": len(not_found_ids),
        "not_found_sample": not_found_ids[:10],
        "errors": errors[:5],
        "duration_sec": round(duration, 2),
        "message": (f"{matched_products} ürün stoğu güncellendi "
                    f"({updated_variants_total} varyasyon) — {round(duration,1)}s")
    }


@router.post("/sync-stock-async")
async def sync_ticimax_stock_async(
    background_tasks: BackgroundTasks,
    max_products: int = Query(5000, ge=10, le=20000),
    aktif: Optional[int] = Query(None),
    page_size: int = Query(50, ge=10, le=100),
    current_user: dict = Depends(require_admin),
):
    """Aynı işlem ama background — admin UI'ya hemen 'started' döner,
    işlem sonu integration_logs'a yazılır."""
    async def _runner():
        try:
            from ticimax_client import get_products  # noqa
            await sync_ticimax_stock(max_products=max_products, aktif=aktif,
                                     page_size=page_size, current_user=current_user)
        except Exception as e:
            await log_integration_event(
                marketplace="ticimax", action="stock_sync", status="error",
                direction="inbound", message=f"Background stok senkron hatası: {e}"
            )
    background_tasks.add_task(_runner)
    return {"success": True, "message": "Stok senkronu arka planda başlatıldı. Otomasyon Durumu > Loglar'dan takip edebilirsiniz."}


# ═══════════════════════════════════════════════════════════════════
# KATEGORİ SENKRONİZASYONU — Ticimax → Facette
# "En Yeniler" dahil tüm kategori üyeliklerini + is_new bayrağını eşitler.
# ═══════════════════════════════════════════════════════════════════
import re as _re_cat
import unicodedata as _ud_cat

# Ticimax SelectUrun ürün nesnesinde kategori üyeliğinin hangi alanda
# geldiği siteye göre değişebildiği için OLASI tüm alan adlarını deniyoruz.
_TC_PRODUCT_CAT_FIELDS = [
    "Kategoriler", "KategoriListe", "KategoriListesi", "Kategori",
    "KategoriIDleri", "KategoriIdleri", "KategoriIDs", "Kategoriids",
    "KategoriID", "KategoriId", "Categories", "CategoryList",
]
# Tek bir kategori objesi içindeki ID / ad alanları
_TC_CAT_ID_KEYS   = ["ID", "Id", "KategoriID", "KategoriId", "CategoryId", "value"]
_TC_CAT_NAME_KEYS = ["Tanim", "Ad", "Adi", "Isim", "Name", "Text", "label"]
# "Yeni ürün" bayrağı için olası alanlar (SelectUrun ürün nesnesi)
_TC_NEW_FLAG_FIELDS = ["YeniUrun", "Yeniurun", "YENIURUN", "YeniUrunMu", "IsNew", "Vitrin"]


def _norm_cat_name(s: str) -> str:
    """Kategori adı eşleştirme için normalize: küçük harf, Türkçe karakter sadeleştir, boşluk/sembol temizle."""
    if not s:
        return ""
    s = str(s).strip()
    # Türkçe özel harf eşlemesi (İ/ı/Ş/ğ vb.)
    repl = {"İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
            "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c"}
    s = "".join(repl.get(ch, ch) for ch in s)
    s = _ud_cat.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = _re_cat.sub(r"[^a-z0-9]+", "", s)
    return s


def _extract_tc_cat_entries(prod: Dict) -> List[Dict]:
    """Bir Ticimax ürününden kategori üyeliklerini [{id, name}] olarak çıkarır.
    Alan adları siteye göre değişebildiği için defansif çalışır."""
    out: List[Dict] = []
    for fld in _TC_PRODUCT_CAT_FIELDS:
        if fld not in prod:
            continue
        val = prod.get(fld)
        if val is None:
            continue
        # int / "12,15,18" gibi düz değerler
        if isinstance(val, (int,)):
            out.append({"id": int(val), "name": ""}); continue
        if isinstance(val, str):
            for piece in _re_cat.split(r"[,;|]", val):
                piece = piece.strip()
                if piece.isdigit():
                    out.append({"id": int(piece), "name": ""})
                elif piece:
                    out.append({"id": None, "name": piece})
            continue
        # liste
        if isinstance(val, list):
            for item in val:
                if isinstance(item, (int,)):
                    out.append({"id": int(item), "name": ""})
                elif isinstance(item, str):
                    s = item.strip()
                    if s.isdigit():
                        out.append({"id": int(s), "name": ""})
                    elif s:
                        out.append({"id": None, "name": s})
                elif isinstance(item, dict):
                    cid = None
                    for k in _TC_CAT_ID_KEYS:
                        if item.get(k) not in (None, ""):
                            try: cid = int(item.get(k))
                            except Exception: cid = None
                            break
                    cname = ""
                    for k in _TC_CAT_NAME_KEYS:
                        if item.get(k):
                            cname = str(item.get(k)); break
                    if cid is not None or cname:
                        out.append({"id": cid, "name": cname})
            continue
        # tek dict
        if isinstance(val, dict):
            cid = None
            for k in _TC_CAT_ID_KEYS:
                if val.get(k) not in (None, ""):
                    try: cid = int(val.get(k))
                    except Exception: cid = None
                    break
            cname = ""
            for k in _TC_CAT_NAME_KEYS:
                if val.get(k):
                    cname = str(val.get(k)); break
            if cid is not None or cname:
                out.append({"id": cid, "name": cname})
    # tekilleştir
    seen = set(); uniq = []
    for e in out:
        key = (e.get("id"), _norm_cat_name(e.get("name") or ""))
        if key in seen:
            continue
        seen.add(key); uniq.append(e)
    return uniq


def _extract_tc_new_flag(prod: Dict) -> Optional[bool]:
    """Ticimax ürününden 'yeni ürün' bayrağını okur (bulamazsa None)."""
    for fld in _TC_NEW_FLAG_FIELDS:
        if fld in prod and prod.get(fld) is not None:
            v = prod.get(fld)
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "evet", "yes", "var")
    return None


@router.post("/category-probe")
async def ticimax_category_probe(
    sample: int = Query(3, ge=1, le=10),
    aktif: Optional[int] = Query(1),
    current_user: dict = Depends(require_admin),
):
    """KEŞİF: Veriyi DEĞİŞTİRMEZ. Ticimax'tan birkaç ürün + tüm kategorileri çekip
    ham alan adlarını döndürür. Kategori senkronundan ÖNCE çalıştırıp gerçek WS
    yapısını (kategori alanının adı, yeni-ürün bayrağı) görmek içindir."""
    from ticimax_client import get_products, get_all_categories  # type: ignore
    settings = await db.settings.find_one({"id": "ticimax"}) or {}
    api_key = settings.get("api_key") or "AKG0M8DTRSEBAIA898JA6HW22EDIU3"
    _dom = settings.get("domain")
    if _dom:
        try:
            from ticimax_client import set_domain as _set_dom  # type: ignore
            _set_dom(_dom)
        except Exception as _e:
            logger.warning(f"[ticimax] set_domain failed: {_e}")

    sample_products = []
    try:
        prods = get_products(page=1, page_size=max(sample, 5), aktif=aktif, wscode=api_key)
        for raw in prods[:sample]:
            d = _to_dict(raw) or {}
            sample_products.append({
                "all_keys": sorted(list(d.keys())),
                "detected_category_field": next((f for f in _TC_PRODUCT_CAT_FIELDS if f in d), None),
                "detected_new_flag_field": next((f for f in _TC_NEW_FLAG_FIELDS if f in d and d.get(f) is not None), None),
                "extracted_categories": _extract_tc_cat_entries(d),
                "extracted_new_flag": _extract_tc_new_flag(d),
                "ID": d.get("ID") or d.get("UrunKartiID"),
                "name_sample": d.get("UrunAdi") or d.get("Tanim") or d.get("Adi"),
            })
    except Exception as e:
        return {"success": False, "stage": "products", "error": str(e)}

    tc_cats = []
    try:
        cats = get_all_categories(wscode=api_key, sleep_between=1.0)
        tc_cats = [{"ID": c.get("ID"), "PID": c.get("PID"),
                    "Tanim": c.get("Tanim"), "Aktif": c.get("Aktif")} for c in cats]
    except Exception as e:
        return {"success": True, "warning": f"kategori çekilemedi: {e}",
                "sample_products": sample_products}

    # Bizdeki kategorilerle isim eşleşmesi önizlemesi
    local_cats = await db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "parent_id": 1}).to_list(5000)
    local_by_norm = {_norm_cat_name(c.get("name")): c for c in local_cats}
    en_yeniler = next((c for c in local_cats
                       if _norm_cat_name(c.get("name")) in ("enyeniler", "yeniurunler", "yeniurun", "yeniler")), None)
    name_match_preview = []
    for tc in tc_cats[:60]:
        nm = _norm_cat_name(tc.get("Tanim"))
        name_match_preview.append({
            "ticimax": tc.get("Tanim"), "ticimax_id": tc.get("ID"),
            "matched_local_id": (local_by_norm.get(nm) or {}).get("id"),
            "matched": nm in local_by_norm,
        })

    return {
        "success": True,
        "sample_products": sample_products,
        "ticimax_category_count": len(tc_cats),
        "ticimax_categories": tc_cats[:80],
        "local_category_count": len(local_cats),
        "local_en_yeniler_category": en_yeniler,
        "name_match_preview": name_match_preview,
        "note": ("Ürünün kategori alanını ve yeni-ürün bayrağını yukarıda görün. "
                 "detected_category_field null ise ürün nesnesi kategori üyeliği taşımıyor demektir; "
                 "bu durumda kategori başına SelectUrun(kategori_id) yöntemi kullanılır (sync-categories otomatik dener)."),
    }


async def _build_category_maps(api_key: str):
    """Ticimax kategorilerini çekip bizdeki kategorilerle eşler.
    Döner: (tc_id_to_local_id, tc_id_to_name, en_yeniler_local_id, tc_cats, created_count)
    İsim eşleşeni bulunan bizdeki kategoriye ticimax_category_id de yazar (gelecek senkronlar ID-bazlı olsun diye)."""
    from ticimax_client import get_all_categories  # type: ignore
    tc_cats = get_all_categories(wscode=api_key, sleep_between=1.0)

    local_cats = await db.categories.find(
        {}, {"_id": 0, "id": 1, "name": 1, "parent_id": 1, "ticimax_category_id": 1}).to_list(5000)
    local_by_norm = {_norm_cat_name(c.get("name")): c for c in local_cats}
    local_by_tcid = {c.get("ticimax_category_id"): c for c in local_cats if c.get("ticimax_category_id")}

    tc_id_to_local_id: Dict[int, str] = {}
    tc_id_to_name: Dict[int, str] = {}
    for tc in tc_cats:
        try:
            tcid = int(tc.get("ID"))
        except Exception:
            continue
        nm = tc.get("Tanim") or ""
        tc_id_to_name[tcid] = nm
        # 1) Daha önce eşlenmiş ticimax_category_id
        if tcid in local_by_tcid:
            tc_id_to_local_id[tcid] = local_by_tcid[tcid]["id"]; continue
        # 2) İsim eşleşmesi
        lc = local_by_norm.get(_norm_cat_name(nm))
        if lc:
            tc_id_to_local_id[tcid] = lc["id"]
            # ileriye dönük ID eşlemesini kaydet
            if lc.get("ticimax_category_id") != tcid:
                await db.categories.update_one({"id": lc["id"]}, {"$set": {"ticimax_category_id": tcid}})

    # "En Yeniler" kategorisini bul
    en_local_id = None
    for c in local_cats:
        if _norm_cat_name(c.get("name")) in ("enyeniler", "yeniurunler", "yeniurun", "yeniler"):
            en_local_id = c["id"]; break

    return tc_id_to_local_id, tc_id_to_name, en_local_id, tc_cats


@router.post("/sync-categories")
async def sync_ticimax_categories(
    max_products: int = Query(5000, ge=10, le=20000),
    aktif: Optional[int] = Query(1),
    page_size: int = Query(50, ge=10, le=100),
    dry_run: bool = Query(True, description="True iken DB'ye yazmaz, sadece raporlar"),
    sync_all_categories: bool = Query(True, description="True: tüm kategori üyeliklerini eşitle; False: sadece is_new + En Yeniler"),
    current_user: dict = Depends(require_admin),
):
    """Ticimax ürün kategorilerini Facette'e eşitler.

    - Tüm kategori üyeliklerini Ticimax'taki haliyle eşitler (sync_all_categories=True).
    - is_new bayrağını Ticimax 'Yeni Ürün' değerine göre ayarlar.
    - "En Yeniler" kategorisini bulup yeni ürünleri ona bağlar.
    - Eşleşmeyen Ticimax ürünlerini ATLAR ve detaylı liste döndürür.
    - dry_run=True iken hiçbir şey yazılmaz (önce bununla kontrol et!).
    """
    started = datetime.now(timezone.utc)
    from ticimax_client import get_products, get_product_count  # type: ignore

    settings = await db.settings.find_one({"id": "ticimax"}) or {}
    api_key = settings.get("api_key") or "AKG0M8DTRSEBAIA898JA6HW22EDIU3"
    _dom = settings.get("domain")
    if _dom:
        try:
            from ticimax_client import set_domain as _set_dom  # type: ignore
            _set_dom(_dom)
        except Exception as _e:
            logger.warning(f"[ticimax] set_domain failed: {_e}")

    # 1) Kategori eşleme tabloları
    try:
        tc_id_to_local, tc_id_to_name, en_local_id, tc_cats = await _build_category_maps(api_key)
    except Exception as e:
        return {"success": False, "stage": "categories", "error": str(e)}

    # 2) Ürünleri çek
    try:
        ticimax_total = get_product_count(aktif=aktif, wscode=api_key)
    except Exception:
        ticimax_total = 0
    fetched: List[Dict] = []
    pages = (min(max_products, ticimax_total or max_products) // page_size) + 2
    for page in range(1, pages + 1):
        try:
            chunk = get_products(page=page, page_size=page_size, aktif=aktif, wscode=api_key)
        except Exception as e:
            logger.warning(f"[cat-sync] page {page} err: {e}"); break
        if not chunk:
            break
        fetched.extend(chunk)
        if len(fetched) >= max_products:
            break
    fetched = fetched[:max_products]

    # 3) Her ürünü eşle
    matched = 0
    updated = 0
    set_new_count = 0
    bound_en_yeniler = 0
    not_found: List[Dict] = []
    changes_preview: List[Dict] = []
    cat_field_seen = False

    # bizdeki kategori ata-zinciri (category_ids düzleştirme) için harita
    all_local_cats = await db.categories.find({}, {"_id": 0, "id": 1, "parent_id": 1}).to_list(5000)
    parent_map = {c.get("id"): c.get("parent_id") for c in all_local_cats if c.get("id")}

    def _expand_with_ancestors(ids: List[str]) -> List[str]:
        res, seen = [], set()
        for cid in ids:
            cur, g = cid, 0
            while cur and cur not in seen and g < 50:
                seen.add(cur); res.append(cur); cur = parent_map.get(cur); g += 1
        return res

    for raw in fetched:
        d = _to_dict(raw)
        if not d:
            continue
        tc_card_id = d.get("ID") or d.get("UrunKartiID")
        try:
            tc_card_id = int(tc_card_id) if tc_card_id else None
        except Exception:
            tc_card_id = None
        variants_raw = _unwrap_variants(d.get("Varyasyonlar"))

        # --- bizdeki ürünü bul (stok-sync ile aynı 3 öncelik) ---
        product_doc = None
        if tc_card_id:
            # O6: csv_card_id string saklanır — int ve string birlikte sorgula.
            product_doc = await db.products.find_one(
                {"csv_card_id": {"$in": [tc_card_id, str(tc_card_id)]}},
                {"_id": 0, "id": 1, "name": 1, "categories": 1, "is_new": 1})
        if not product_doc and variants_raw:
            tv_codes = [str(v.get("StokKodu") or "").strip() for v in variants_raw if v.get("StokKodu")]
            tv_bars = [str(v.get("Barkod") or "").strip() for v in variants_raw if v.get("Barkod")]
            oc = []
            if tv_codes:
                oc += [{"variants.stock_code": {"$in": tv_codes}}, {"stock_code": {"$in": tv_codes}}]
            if tv_bars:
                oc += [{"variants.barcode": {"$in": tv_bars}}, {"barcode": {"$in": tv_bars}}]
            if oc:
                product_doc = await db.products.find_one(
                    {"$or": oc}, {"_id": 0, "id": 1, "name": 1, "categories": 1, "is_new": 1})
        if not product_doc:
            top_code = str(d.get("StokKodu") or "").strip()
            top_bar = str(d.get("Barkod") or d.get("BarkodNo") or "").strip()
            tcl = []
            if top_code:
                tcl += [{"variants.stock_code": top_code}, {"stock_code": top_code}]
            if top_bar:
                tcl += [{"variants.barcode": top_bar}, {"barcode": top_bar}]
            if tcl:
                product_doc = await db.products.find_one(
                    {"$or": tcl}, {"_id": 0, "id": 1, "name": 1, "categories": 1, "is_new": 1})

        if not product_doc:
            not_found.append({
                "ticimax_id": tc_card_id,
                "name": d.get("UrunAdi") or d.get("Tanim") or d.get("Adi") or "",
                "stock_code": str(d.get("StokKodu") or "").strip(),
                "barcode": str(d.get("Barkod") or "").strip(),
            })
            continue
        matched += 1

        # --- Ticimax kategori üyeliklerini çıkar ---
        tc_entries = _extract_tc_cat_entries(d)
        if tc_entries:
            cat_field_seen = True
        # local kategori id'lerine çevir
        local_cat_ids: List[str] = []
        for e in tc_entries:
            lid = None
            if e.get("id") is not None and e["id"] in tc_id_to_local:
                lid = tc_id_to_local[e["id"]]
            elif e.get("name"):
                lid = next((tc_id_to_local[k] for k, nm in tc_id_to_name.items()
                            if _norm_cat_name(nm) == _norm_cat_name(e["name"]) and k in tc_id_to_local), None)
            if lid and lid not in local_cat_ids:
                local_cat_ids.append(lid)

        # --- yeni ürün bayrağı ---
        tc_new = _extract_tc_new_flag(d)

        # --- güncelleme seti ---
        set_fields: Dict[str, Any] = {}
        if tc_new is not None and bool(product_doc.get("is_new")) != tc_new:
            set_fields["is_new"] = tc_new
            if tc_new:
                set_new_count += 1

        # "En Yeniler" üyeliği: yeni ürünse kategoriye ekle, değilse çıkar
        target_cats = list(product_doc.get("categories") or [])
        if sync_all_categories and local_cat_ids:
            # Ticimax üyeliğiyle birebir eşitle (tüm kategoriler)
            target_cats = local_cat_ids[:]
        if en_local_id:
            in_en = en_local_id in target_cats
            if tc_new and not in_en:
                target_cats.append(en_local_id); bound_en_yeniler += 1
            elif (tc_new is False) and in_en:
                target_cats = [c for c in target_cats if c != en_local_id]

        if target_cats != list(product_doc.get("categories") or []):
            set_fields["categories"] = target_cats
            set_fields["category_id"] = target_cats[0] if target_cats else ""
            set_fields["category_ids"] = _expand_with_ancestors(target_cats)

        if set_fields:
            updated += 1
            if len(changes_preview) < 40:
                changes_preview.append({
                    "product_id": product_doc["id"],
                    "name": product_doc.get("name", ""),
                    "ticimax_id": tc_card_id,
                    "changes": {k: set_fields[k] for k in set_fields},
                })
            if not dry_run:
                set_fields["category_synced_at"] = datetime.now(timezone.utc).isoformat()
                await db.products.update_one({"id": product_doc["id"]}, {"$set": set_fields})

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    if not dry_run:
        await log_integration_event(
            marketplace="ticimax", action="category_sync", status="success",
            direction="inbound",
            message=(f"Kategori senkronu: {matched} eşleşti, {updated} güncellendi, "
                     f"{len(not_found)} bulunamadı ({round(duration,1)}s)"))

    return {
        "success": True,
        "dry_run": dry_run,
        "sync_all_categories": sync_all_categories,
        "ticimax_total": ticimax_total,
        "fetched": len(fetched),
        "matched_products": matched,
        "would_update" if dry_run else "updated_products": updated,
        "is_new_set": set_new_count,
        "en_yeniler_bound": bound_en_yeniler,
        "en_yeniler_local_id": en_local_id,
        "ticimax_category_count": len(tc_cats),
        "mapped_category_count": len(tc_id_to_local),
        "product_carries_category_field": cat_field_seen,
        "not_found_count": len(not_found),
        "not_found_products": not_found,          # TAM liste (atlananlar)
        "changes_preview": changes_preview,        # ilk 40 değişiklik önizlemesi
        "duration_sec": round(duration, 2),
        "message": (
            (("[KURU ÇALIŞMA] " if dry_run else "") +
             f"{matched} ürün eşleşti, {updated} ürün {'güncellenecek' if dry_run else 'güncellendi'}, "
             f"{len(not_found)} Ticimax ürünü bizde bulunamadı (atlandı).") +
            ("" if cat_field_seen else
             " ⚠ Ürün nesnesi kategori alanı taşımıyor görünüyor — /category-probe ile kontrol edin.")
        ),
    }
