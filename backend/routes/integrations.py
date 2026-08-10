"""
Integration routes - ana toplayıcı modül.
2026-07-01: Trendyol ve Hepsiburada blokları ayrı modüllere taşındı
(integrations_trendyol.py, integrations_hepsiburada.py) — AST analiziyle
sıfır çapraz bağımlılık doğrulandıktan sonra. Bu dosyada sadece Ticimax'a
özgü (veri taşıma bitene kadar aktif kalacak) route'lar + üç alt router'ın
birleştirilmesi kalıyor. server.py hiç değişmedi.
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

from .deps import db, logger, get_current_user, require_admin, generate_id, generate_short_id
from facette_defaults import facette_fixed_value_for  # tüm-pazaryeri sabit varsayılan (gap-fill)

router = APIRouter(tags=["Integrations"])

from .integrations_common import router as _common_router
from .integrations_trendyol import router as _trendyol_router
from .integrations_hepsiburada import router as _hb_router
router.include_router(_trendyol_router)
router.include_router(_hb_router)
router.include_router(_common_router)


# ---- Geriye dönük uyumluluk: scheduler.py, orders.py, category_mapping.py vb.
# dosyalar 'from routes.integrations import X' ile fonksiyon/sabit import ediyordu.
# Bölünme sonrası kırılmasınlar diye TÜM alt-modül isimleri burada re-export edilir.
from .integrations_common import (
    _mp_base_price,
    _dedupe_products_by_stock_code,
    _resolve_stock_code,
    _normalize_attr_key,
    _norm_val,
    _VALUE_SYNONYMS,
    _resolve_value_id,
    _BAD_COMPOSITION_VALUES,
    _build_product_query_from_payload,
    list_products_with_barcode_issues,
    fix_product_barcode,
    log_integration_event,
    _ms_to_iso,
    _decrement_stock_for_imported_order,
    _facette_product_image,
    _facette_match_for_codes,
    _to_float_tr,
    _hb_norm,
    get_integration_logs,
    GIB_MODE,
    GIB_USERNAME,
    GIB_PASSWORD,
    GIB_VKN,
    GIB_COMPANY_NAME,
    is_gib_configured,
    get_gib_status,
    _generate_slug,
    sync_missing_categories_from_products,
    _kurtarma_match,
    recover_teknik_detay_from_snapshot,
    recover_aciklama_from_snapshot,
    _rk_lower,
    _RENK_CANON,
    _renk_from_name,
    _renk_canon,
    autofill_renk_webcolor,
    _desc_is_blank,
    _attr_flat,
    _attr_get,
    generate_aciklama_ai,
    upload_rooftr_products_excel,
    XML_FEED_URL,
    _NS,
    _xml_text,
    _xml_all,
    import_xml_products,
    get_xml_feed_status,
    _CLAIM_STATUS_PRIORITY,
    _derive_claim_status,
    _RETURN_STATUS_KEYS,
    _ORDER_STATUS_BUCKET,
    _ORDER_STATUS_TR,
    _order_payment_type,
    _claim_bucket,
    _first_seen_stamps,
    _claim_is_site_order,
    _search_tr_regex,
    restock_claim_once,
    ALLOWED_MARKETPLACES,
    get_marketplace_settings,
    save_marketplace_settings,
    get_marketplace_status,
    test_marketplace_connection,
    QUESTIONS_COLLECTIONS,
    delete_marketplace_question,
    get_marketplace_questions,
    sync_marketplace_questions_stub,
    answer_marketplace_question_stub,
)
from .integrations_trendyol import (
    TrendyolOrderPreviewReq,
    TrendyolOrderImportReq,
    CategoryMappingReq,
    AttributeMapping,
    AttributeMappingReq,
    get_trendyol_config,
    get_trendyol_headers,
    calculate_trendyol_price,
    get_trendyol_settings,
    save_trendyol_settings,
    test_trendyol_connection,
    get_trendyol_status,
    debug_trendyol_orders,
    sync_trendyol_categories,
    get_trendyol_category_attributes,
    sync_trendyol_brands,
    _TRENDYOL_ATTR_SYNONYMS,
    _bridge_trendyol_attr_synonyms,
    validate_products_for_trendyol,
    get_trendyol_batch_status,
    trendyol_barcode_duplicates,
    trendyol_ghost_scanner,
    trendyol_archive_barcodes,
    sync_products_to_trendyol,
    get_trendyol_sync_logs,
    sync_trendyol_inventory,
    sync_single_product_inventory,
    _sync_inventory_to_trendyol,
    get_trendyol_batch_status_v2,
    _sync_trendyol_status_passes,
    map_trendyol_order,
    preview_trendyol_orders,
    import_selected_trendyol_orders,
    import_trendyol_orders,
    get_trendyol_category_mappings,
    save_trendyol_category_mapping,
    save_trendyol_category_value_mappings,
    get_local_category_values,
    delete_trendyol_category_mapping,
    bulk_delete_trendyol_category_mappings,
    save_trendyol_attribute_mapping,
    get_local_trendyol_categories,
    get_trendyol_cargo_label,
    _order_derived_trendyol_returns,
    _sync_trendyol_claims_core,
    sync_trendyol_claims,
    fix_claim_discounts,
    get_trendyol_claims,
    export_trendyol_claims,
    trendyol_claims_diagnostics,
    repair_trendyol_claim_status,
    dedupe_trendyol_claims,
    set_trendyol_claim_status,
    unlock_trendyol_claim,
    trendyol_shipment_probe,
    get_trendyol_issue_reasons,
    get_trendyol_claim_detail,
    generate_gider_pusulasi,
    bulk_generate_gider_pusulasi,
    update_trendyol_stock_price,
    update_trendyol_category_stock_price,
    get_trendyol_cargo_label_pkg,
    approve_trendyol_claim,
    issue_trendyol_claim,
    upload_invoice_to_trendyol,
    sync_product_to_trendyol,
)
from .integrations_hepsiburada import (
    HbOrderPreviewReq,
    HbOrderImportReq,
    HbBulkListingReq,
    HbPackageReq,
    HbInvoiceReq,
    HbCargoReq,
    HbCancelReq,
    HbClaimRejectReq,
    HbProductSyncReq,
    get_hepsiburada_category_attributes,
    HB_COMMON_ATTRS,
    _hb_common_attr_values,
    hb_get_base_field_mappings,
    hb_save_base_field_mappings,
    _hb_g,
    _hb_normalize_lines,
    _hb_group_orders,
    _hb_is_full_order,
    _hb_order_group_from_full,
    _hb_orders_from_response,
    _hb_money,
    map_hepsiburada_order,
    _hb_created_at,
    _hb_enrich_items,
    preview_hepsiburada_orders,
    hepsiburada_oms_diag,
    hepsiburada_import_by_number,
    create_hepsiburada_test_order,
    import_selected_hepsiburada_orders,
    _hb_markup,
    HB_PRICE_SOURCES,
    _HB_PRICE_SOURCE_KEYS,
    _hb_price_source,
    _hb_pick_base_price,
    _hb_merchant_sku,
    _hb_card_id,
    _hb_sku_base_from_source,
    _hb_variant_sku,
    _hb_sku_source,
    _hb_listing_items_from_product,
    _hb_push_stock_price,
    hb_update_product_stock_price,
    hb_update_category_stock_price,
    hb_update_listings_bulk,
    hb_listing_upload_status,
    hb_get_listings,
    hb_activate_listing,
    hb_deactivate_listing,
    _HB_CLOTHING_SYNONYMS,
    _hb_collect_local,
    _hb_local_for_attr,
    _hb_value_from_name,
    _HB_DIRTY_POOL_MIN,
    _hb_is_junk_value,
    _HB_ID_SHAPE_RE,
    _hb_looks_like_leaked_id,
    _hb_resolve_value,
    _hb_resolve_with_fallback,
    _hb_fabric_tokens,
    _hb_match_fabric_from_desc,
    _hb_category_attributes_for,
    _hb_base_attributes_for,
    _build_hb_product_item,
    _hb_summarize_import_status,
    _hb_poll_import,
    _hb_build_sku_to_hbsku_map,
    _hb_split_ticket_item,
    _hb_poll_ticket,
    hb_sync_products,
    hb_validate_products,
    hb_set_category_default,
    hb_set_product_attribute,
    hb_product_category_attributes,
    hb_category_attributes_by_local,
    _hb_resolve_local_to_hb,
    _hb_numeric_id_findings,
    hb_numeric_id_scan,
    hb_numeric_id_fix,
    hb_category_mapping_audit,
    hb_debug_payload,
    hb_inventory_sync,
    hb_autofill_attributes,
    hb_product_tracking,
    hb_products_by_status,
    hb_env_status,
    hb_reconcile_preview,
    hb_order_detail,
    hb_packages,
    hb_create_package,
    hb_send_invoice,
    hb_cargo_label,
    hb_change_cargo,
    hb_cancel_line,
    hb_mark_delivered,
    hb_claims,
    hb_accept_claim,
    hb_reject_claim,
)

# ==================== TICIMAX (veri taşıma bitene kadar aktif) ====================
@router.post("/site/teknik-detay/sync")
async def sync_ticimax_teknik_detay(
    use_cache: bool = Query(True, description="Cache (DB) varsa kullan, yoksa SOAP'tan çek"),
    current_user: dict = Depends(require_admin),
):
    """
    Ticimax 'Teknik Detay Özellik + Değer' master listesini çekip her ürünün
    name+description text'inde değerleri arayarak attributes alanına otomatik
    eşler. Trendyol/HB/Temu özellik formlarındaki Boy, Cep, Astar Durumu, Bel,
    Web Color, Materyal, Kalıp vs. alanları DOLDURULUR.

    use_cache=True: DB'deki master cache'i kullan (~3 sn, anında).
    use_cache=False: Ticimax SOAP'a sorgu at, master'ı yenile (~30 sn, rate limit).
    """
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from scripts.enrich_attrs_from_ticimax_master import (
        fetch_master, enrich_products, _build_value_pattern,
    )

    if use_cache:
        cached = await db.ticimax_attribute_master.find({}, {"_id": 0}).to_list(None)
        if cached:
            ozellik_map = {c["ozellik_id"]: c["ozellik_tanim"] for c in cached}
            deger_by_ozellik: dict = {}
            for c in cached:
                ozid = c["ozellik_id"]
                deger_by_ozellik[ozid] = []
                for d in c.get("degerler", []):
                    pat = _build_value_pattern(d["tanim"])
                    if pat:
                        deger_by_ozellik[ozid].append({
                            "id": d["id"], "tanim": d["tanim"], "pattern": pat,
                        })
        else:
            ozellik_map, deger_by_ozellik = fetch_master()
    else:
        ozellik_map, deger_by_ozellik = fetch_master()

    # Run enrichment (görüntülemek için stdout yakalamadan; loglar zaten)
    # `enrich_products` is async-safe; just await it directly via patched runner:
    total = 0
    enriched = 0
    added_keys: dict = {}

    prods = await db.products.find(
        {"source": {"$in": ["xml_feed", "ticimax", "csv_xml_merge"]}},
        {"_id": 0, "id": 1, "name": 1, "description": 1, "attributes": 1,
         "hepsiburada_attributes": 1, "temu_attributes": 1},
    ).to_list(None)

    import re as _re
    import unicodedata as _u

    def _norm(s: str) -> str:
        s = _u.normalize("NFKD", s or "")
        s = "".join(c for c in s if not _u.combining(c))
        s = (s.lower()
               .replace("ı", "i").replace("ş", "s").replace("ç", "c")
               .replace("ğ", "g").replace("ü", "u").replace("ö", "o"))
        return _re.sub(r"\s+", " ", s).strip()

    for p in prods:
        total += 1
        name = p.get("name") or ""
        desc = _re.sub(r"<[^>]+>", " ", p.get("description") or "")
        text = _norm(name + " " + desc)

        existing = p.get("attributes") or {}
        if isinstance(existing, list):
            new_attrs = {}
            for item in existing:
                if isinstance(item, dict) and item.get("name"):
                    new_attrs[item["name"]] = str(item.get("value", ""))
            existing = new_attrs

        hb = p.get("hepsiburada_attributes") or {}
        temu = p.get("temu_attributes") or {}

        added = False
        for ozid, tanim in ozellik_map.items():
            matched = None
            for d in deger_by_ozellik.get(ozid, []):
                if d["pattern"].search(text):
                    matched = d
                    break
            if not matched:
                continue
            if existing.get(tanim):
                continue
            value = matched["tanim"]
            existing[tanim] = value
            if not hb.get(tanim):
                hb[tanim] = value
            if not temu.get(tanim):
                temu[tanim] = value
            added_keys[tanim] = added_keys.get(tanim, 0) + 1
            added = True

        await db.products.update_one(
            {"id": p["id"]},
            {"$set": {
                "attributes": existing,
                "hepsiburada_attributes": hb,
                "temu_attributes": temu,
            }},
        )
        if added:
            enriched += 1

    return {
        "success": True,
        "total_products": total,
        "enriched_products": enriched,
        "added_by_attribute": added_keys,
        "ozellik_count": len(ozellik_map),
        "message": f"{enriched}/{total} ürüne otomatik teknik detay eşlendi.",
    }
@router.post("/rooftr/orders/import")
async def import_ticimax_orders(
    limit: int = Query(200, ge=1, le=2000),
    days: int = Query(365, ge=1, le=3650, description="Son kaç günün siparişleri çekilsin"),
    exclude_marketplace: bool = Query(False, description="True ise Trendyol/HB/N11 vb. pazaryeri siparişleri hariç tutulur"),
    only_with_phone: bool = Query(False, description="True ise telefon numarası olmayan siparişler atlanır"),
    pages: int = Query(20, ge=1, le=100, description="Kaç sayfa çekilecek"),
    current_user: dict = Depends(require_admin)
):
    # [ticimax-off 2026-06-22] Ticimax SOAP entegrasyonu kapatildi; bu uc devre disi.
    return {"success": False, "message": "Ticimax siparis cekme kapatildi. Site siparisleri React/iyzico checkout'tan gelir."}
@router.post("/ticimax/orders/backfill")
async def backfill_broken_ticimax_orders(
    limit: int = Query(1000, ge=1, le=5000, description="En fazla kaç bozuk sipariş düzeltilsin"),
    days: int = Query(365, ge=1, le=3650, description="Son kaç günü tara"),
    pages: int = Query(20, ge=1, le=100, description="Kaç sayfa Ticimax'tan çekilsin"),
    page_size: int = Query(100, ge=50, le=200),
    items_chunk: int = Query(40, ge=0, le=300, description="Her çağrıda kaç eski sipariş için ürün listesi çekilsin (0=atla)"),
    current_user: dict = Depends(require_admin)
):
    # [ticimax-off 2026-06-22] Ticimax SOAP entegrasyonu kapatildi; bu uc devre disi.
    return {"success": False, "message": "Ticimax backfill kapatildi (entegrasyon sonlandirildi)."}
