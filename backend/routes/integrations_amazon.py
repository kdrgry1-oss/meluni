"""
Amazon entegrasyon route'ları — `/api/integrations/amazon-tr/...` altında (Trendyol/HB ile
AYNI frontend kontratı: kategori sayfasındaki Stok/Fiyat paneli ve Ürün Aktarım paneli bu
yolları çağırır). Asıl SP-API işleri routes/amazon_spapi.py'de; buradaki uçlar onları sarar.

NOT: Bu router server.py'de integrations_router'ın generic /{marketplace} catch-all'ından ÖNCE
include edilmelidir (iyzico/dogan gibi).
"""
from fastapi import APIRouter, Depends, Body

from .deps import require_admin, logger, require_permission

router = APIRouter(tags=["Amazon Integration"])


@router.post("/amazon-tr/products/inventory-sync")
async def amazon_tr_inventory_sync(payload: dict = Body(default={}),
                                   current_user: dict = Depends(require_permission("integrations.amazon"))):
    """Stok/fiyat panelinden (StockPriceUpdatePanel) manuel tetik. Boş payload → tüm aktif
    ürünler; `barcodes`/`stock_codes` → yalnız o varyantlar. force=True (manuel = değişiklik
    tespitini atla). AMAZON_ALLOW_WRITE=0 iken dry-run özeti döner."""
    from scheduler import _run_amazon_auto_stock_sync  # yerel import — döngü önleme
    payload = payload or {}
    barcodes = payload.get("barcodes") or []
    stock_codes = payload.get("stock_codes") or []
    # manual=True → tavan yok. force yalnız filtreli durumda (filtresiz 'tümü' değişiklik-tespitiyle
    # ilerler, tekrar çalıştırınca kaldığı yerden devam eder — çift push yok).
    res = await _run_amazon_auto_stock_sync(barcodes=barcodes, stock_codes=stock_codes,
                                            force=bool(barcodes or stock_codes), manual=True)
    prefix = "DRY-RUN — " if res.get("dry_run") else ""
    return {"success": not res.get("error"), "message": f"Amazon: {prefix}{res.get('message', '')}", **res}


@router.post("/amazon-tr/products/sync")
async def amazon_tr_products_sync(payload: dict = Body(default={}),
                                  current_user: dict = Depends(require_permission("integrations.amazon"))):
    """Ürün Aktarım panelinden (CategoryMapping 'Filtreli Aktarım') manuel tetik — Meluni
    ürünlerini Amazon'a LİSTELER (Listings Items PUT). Asıl mantık amazon_spapi.sync_products."""
    from .amazon_spapi import sync_products_to_amazon  # yerel import
    return await sync_products_to_amazon(payload or {}, current_user)


@router.post("/amazon-tr/products/validate")
async def amazon_tr_products_validate(payload: dict = Body(default={}),
                                      current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Aktarım ÖNCESİ doğrulama (CategoryMapping 'Doğrula') — eksik zorunlu alanları raporlar,
    canlıya yazmaz."""
    from .amazon_spapi import validate_products_for_amazon  # yerel import
    return await validate_products_for_amazon(payload or {}, current_user)
