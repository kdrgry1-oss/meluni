"""
=============================================================================
marketplace_hub.py — Çok Pazaryerli Yönetim Altyapısı
=============================================================================

AMAÇ:
  Ticimax Marketplace v2 paneline benzer şekilde her e-ticaret pazaryeri
  için tek bir merkezi yönetim katmanı. Trendyol, Hepsiburada, Temu, N11,
  Amazon TR/DE, AliExpress, Etsy, Hepsi Global, Fruugo, eMAG, Çiçek Sepeti,
  Trendyol İhracat (ve gelecekteki tüm platformlar) için:
    1) API credentials yönetimi
    2) Sistem <-> pazaryeri aktarım kuralları (fiyat türü, komisyon,
       sipariş durum haritalaması, kargo süresi, vb.)
    3) Otomatik güncelleme (auto-sync) ayarları: periyot, on/off,
       hangi yön (ürün/sipariş/stok)
    4) Entegrasyon logları (her API çağrısı, her aktarım kayıt altında)

NASIL ÇALIŞIR?
  Her pazaryeri için tek bir döküman `marketplace_accounts` koleksiyonunda:
    {
      "key": "trendyol" | "hepsiburada" | "temu" | ...
      "enabled": true,
      "credentials": {<provider-specific>},    # API key/secret/supplier id ...
      "transfer_rules": {<provider-specific>}, # komisyon, fiyat türü, barkod ...
      "auto_sync": {
        "products_enabled": true,
        "products_interval_min": 3,
        "orders_enabled": true,
        "orders_interval_min": 5,
        "orders_lookback_hours": 100
      },
      "updated_at": "...",
      "updated_by": "..."
    }

  Ayrıca `integration_logs` koleksiyonu her API çağrısını kaydeder:
    {
      "marketplace": "trendyol",
      "action": "product_push" | "order_pull" | "stock_update" | "price_update" | ...
      "direction": "outbound" | "inbound",
      "status": "success" | "failed" | "partial",
      "ref_id": <product_id | order_id>,
      "message": "...",
      "payload_in": {...},
      "payload_out": {...},
      "duration_ms": 123,
      "created_at": "..."
    }

KULLANIM:
  Frontend:
    - /admin/pazaryerleri          → MarketplaceHub.jsx (hepsi tek sayfada)
    - /admin/entegrasyon-loglari   → IntegrationLogs.jsx
    - /admin/otomatik-guncelleme   → AutoSyncSettings.jsx

  Backend service layer'ı (integrations.py) her HTTP çağrısı öncesi ve
  sonrası `log_integration_event()` çağırarak kayıt düşer (sonraki adım).
=============================================================================
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional, List
from datetime import datetime, timezone, timedelta

from .deps import db, require_admin

router = APIRouter(prefix="/marketplace-hub", tags=["Marketplace Hub"])


# ---------------------------------------------------------------------------
# PAZARYERİ ŞEMALARI — her pazaryeri için hangi alanların toplanacağı
# (API credentials + transfer_rules).
# Frontend bu şemayı alıp her pazaryerine özel form render eder.
# Yeni bir pazaryeri eklemek için bu dict'e kayıt eklemek yeterlidir.
# ---------------------------------------------------------------------------

def _f(key, label, type="text", required=False, placeholder="", help=None, options=None, default=None):
    d = {"key": key, "label": label, "type": type, "required": required, "placeholder": placeholder}
    if help: d["help"] = help
    if options: d["options"] = options
    if default is not None: d["default"] = default
    return d


# Tüm pazaryerlerinde ortak olan transfer kuralları (sipariş durum haritalaması,
# fiyat türü, komisyon vb.) — ayar formunda ikinci grup olarak gösterilecek.
COMMON_TRANSFER_RULES = [
    _f("license_code", "Lisans Kodu", placeholder="xxxx-xxxx-xxxx-xxxx"),
    _f("minus_stock", "Eksi Stok Adedi", type="number", default=0,
       help="Bu değerin altındaki stokları 0 olarak gönder."),
    _f("price_field", "Satış Fiyatı Alanı", type="select",
       options=[{"value": "price", "label": "Standart Fiyat"},
                {"value": "member_price_1", "label": "Üye Tipi Fiyatı 1"},
                {"value": "member_price_2", "label": "Üye Tipi Fiyatı 2"},
                {"value": "wholesale", "label": "Toptan Fiyat"}],
       default="price"),
    _f("sale_price_field", "İndirimli Fiyat Alanı", type="select",
       options=[{"value": "sale_price", "label": "İndirimli Fiyat"},
                {"value": "member_price_1", "label": "Üye Tipi Fiyatı 1"},
                {"value": "price", "label": "Standart Fiyat"}],
       default="sale_price"),
    _f("commission_type", "Komisyon Tipi", type="select",
       options=[{"value": "percent", "label": "Yüzde %"},
                {"value": "amount", "label": "Sabit Tutar"},
                {"value": "none", "label": "Yok"}],
       default="percent"),
    _f("commission_value", "Komisyon Değeri", type="number", default=20,
       help="Yüzde seçildiyse oran, Tutar seçildiyse TL."),
    _f("update_prices_on_transfer", "Aktarımda Fiyatlar Güncellensin", type="switch", default=True),
    _f("transfer_by_barcode", "Barkod Koduna Göre Aktar", type="switch", default=True),
    _f("transfer_by_stock_code", "Stok Koduna Göre Aktar", type="switch", default=False),
    _f("transfer_new_products", "Yeni Ürünler Otomatik Aktarılsın", type="switch", default=True),
    _f("transfer_packages", "Oluşan Paketleri Pazaryerine Aktar", type="switch", default=False),
    _f("update_order_statuses", "Sipariş Durumları Güncellensin", type="switch", default=True),
    _f("transfer_return_status", "İade Talep ve Durum Aktarımı", type="switch", default=False),
    _f("transfer_due_date", "Sipariş Ödeme Vade Tarihi Aktar", type="switch", default=False),
    _f("transfer_delivery_date", "Sipariş Teslim Tarihi Aktar", type="switch", default=False),
    _f("brand_name", "Varsayılan Marka Adı", placeholder="FACETTE"),
    _f("marketplace_group", "MarketPlace Grup", default="Varsayılan"),
    _f("cargo_duration_days", "Kargo Süresi (Gün)", type="number", default=2),
    _f("warranty", "Garanti Süresi", default="Varsayılan"),
]


MARKETPLACES = {
    "trendyol": {
        "name": "Trendyol",
        "logo": "https://cdn.dsmcdn.com/mnresize/104/104/ty148/content/trendyol/static/js/c5/2d/c5...",
        "website": "https://partner.trendyol.com",
        "description": "Türkiye'nin en büyük pazaryeri.",
        "color": "#F27A1A",
        "credential_fields": [
            _f("supplier_id", "TY Supplier ID (Tedarikçi No)", required=True, placeholder="157840"),
            _f("api_key", "TY API Key", required=True),
            _f("api_secret", "TY API Secret", type="password", required=True),
            _f("sender_address_id", "Gönderi Adresi ID", placeholder="256867"),
            _f("return_address_id", "İade Adresi ID", placeholder="256868"),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull", "stock_update", "price_update", "return_pull", "brand_mapping", "category_mapping"],
    },
    "hepsiburada": {
        "name": "Hepsiburada",
        "website": "https://merchantapi.hepsiburada.com",
        "description": "Hepsiburada satıcı paneli entegrasyonu.",
        "color": "#FF6000",
        "credential_fields": [
            _f("merchant_id", "Merchant ID", required=True, placeholder="e88383f6-478d-4b36-9d44-...",
               help="Hepsiburada satıcı ID'niz. Basic Auth kullanıcı adı olarak da bu değer kullanılır."),
            _f("secret_key", "Secret Key", type="password", required=True,
               help="Hepsiburada API parolası (Basic Auth şifresi)."),
            _f("dev_username", "Developer Username", required=True, placeholder="ceyjewelry_dev",
               help="API isteklerinde User-Agent başlığı olarak gönderilir."),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull", "stock_update", "price_update", "return_pull", "category_mapping", "campaigns", "notifications"],
    },
    "temu": {
        "name": "Temu",
        "website": "https://seller.temu.com",
        "description": "Temu Global Seller Center entegrasyonu.",
        "color": "#FB7701",
        "credential_fields": [
            # Temu Seller paneli "Uygulama" ekranındaki alanlarla birebir:
            _f("app_key", "App Key (Uygulama Anahtarı)", required=True, placeholder="75dc702..."),
            _f("app_secret", "App Secret (Uygulama Sırrı)", type="password", required=True),
            _f("access_token", "Access Token (Erişim Belirteci)", type="password",
               help="Temu panelinde yetkilendirme sonrası oluşur. Panelde '-' görünüyorsa boş bırakın."),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test (Sandbox)"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull", "stock_update"],
    },
    "n11": {
        "name": "N11",
        "website": "https://api.n11.com",
        "description": "N11 pazaryeri entegrasyonu.",
        "color": "#FF5E00",
        "credential_fields": [
            _f("api_key", "API Key", required=True),
            _f("api_secret", "API Secret", type="password", required=True),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull", "stock_update", "price_update"],
    },
    "amazon-tr": {
        "name": "Amazon TR",
        "website": "https://sellercentral.amazon.com.tr",
        "description": "Amazon Türkiye Seller Central entegrasyonu.",
        "color": "#FF9900",
        "credential_fields": [
            _f("seller_id", "Seller ID", required=True),
            _f("marketplace_id", "Marketplace ID", default="A33AVAJ2PDY3EV", required=True),
            _f("access_key", "Access Key", type="password", required=True),
            _f("secret_key", "Secret Key", type="password", required=True),
            _f("refresh_token", "LWA Refresh Token", type="password"),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull", "stock_update", "price_update"],
    },
    "amazon-de": {
        "name": "Amazon DE",
        "website": "https://sellercentral-europe.amazon.com",
        "description": "Amazon Almanya Seller Central.",
        "color": "#FF9900",
        "credential_fields": [
            _f("seller_id", "Seller ID", required=True),
            _f("marketplace_id", "Marketplace ID", default="A1PA6795UKMFR9", required=True),
            _f("access_key", "Access Key", type="password", required=True),
            _f("secret_key", "Secret Key", type="password", required=True),
            _f("refresh_token", "LWA Refresh Token", type="password"),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull", "stock_update"],
    },
    "aliexpress": {
        "name": "AliExpress",
        "website": "https://openservice.aliexpress.com",
        "description": "AliExpress Open Platform entegrasyonu.",
        "color": "#E62E04",
        "credential_fields": [
            _f("app_key", "App Key", required=True),
            _f("app_secret", "App Secret", type="password", required=True),
            _f("access_token", "Access Token", type="password"),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull", "stock_update"],
    },
    "etsy": {
        "name": "Etsy",
        "website": "https://www.etsy.com/developers",
        "description": "Etsy marketplace.",
        "color": "#F1641E",
        "credential_fields": [
            _f("shop_id", "Shop ID", required=True),
            _f("api_key", "API Key", required=True),
            _f("oauth_token", "OAuth Token", type="password", required=True),
            _f("oauth_token_secret", "OAuth Token Secret", type="password", required=True),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull"],
    },
    "hepsi-global": {
        "name": "Hepsi Global",
        "website": "https://hepsiglobal.com",
        "description": "Hepsi Global (Hepsiburada yurtdışı).",
        "color": "#FF6000",
        "credential_fields": [
            _f("username", "Username (API)", required=True),
            _f("password", "Password", type="password", required=True),
            _f("merchant_id", "Merchant ID"),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull"],
    },
    "fruugo": {
        "name": "Fruugo",
        "website": "https://www.fruugo.com",
        "description": "Fruugo çok ülkeli pazaryeri.",
        "color": "#5B2D90",
        "credential_fields": [
            _f("username", "Username", required=True),
            _f("password", "Password", type="password", required=True),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull"],
    },
    "emag": {
        "name": "eMAG",
        "website": "https://marketplace.emag.ro",
        "description": "eMAG Romanya/Bulgaristan/Macaristan pazaryeri.",
        "color": "#EE3124",
        "credential_fields": [
            _f("username", "Username", required=True),
            _f("password", "Password", type="password", required=True),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull"],
    },
    "trendyol-ihracat": {
        "name": "Trendyol İhracat Merkezi",
        "website": "https://partner.trendyol.com",
        "description": "Trendyol mikro ihracat (uluslararası siparişler).",
        "color": "#F27A1A",
        "credential_fields": [
            _f("supplier_id", "TY Supplier ID", required=True),
            _f("api_key", "TY API Key", required=True),
            _f("api_secret", "TY API Secret", type="password", required=True),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull"],
    },
    "ciceksepeti": {
        "name": "Çiçek Sepeti",
        "website": "https://ciceksepeti.com",
        "description": "Çiçek Sepeti pazaryeri.",
        "color": "#E91E63",
        "credential_fields": [
            _f("api_key", "API Key", required=True),
            _f("seller_id", "Seller ID"),
            _f("env", "Ortam", type="select", required=True, default="prod",
               options=[{"value": "test", "label": "Test"}, {"value": "prod", "label": "Canlı"}]),
        ],
        "features": ["product_push", "order_pull"],
    },
}


# ---------------------------------------------------------------------------
# SCHEMAS
# ---------------------------------------------------------------------------
@router.get("/marketplaces")
async def list_marketplaces(current_user: dict = Depends(require_admin)):
    """Sistemde tanımlı tüm pazaryerlerinin meta bilgisi."""
    return {
        "marketplaces": [
            {"key": k, "name": v["name"], "description": v.get("description"),
             "color": v.get("color"), "website": v.get("website"),
             "features": v.get("features", [])}
            for k, v in MARKETPLACES.items()
        ]
    }


@router.get("/marketplaces/{key}/schema")
async def get_marketplace_schema(key: str, current_user: dict = Depends(require_admin)):
    """Bir pazaryerinin credential + transfer_rules alan şeması."""
    mp = MARKETPLACES.get(key)
    if not mp:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")
    return {
        "key": key,
        "name": mp["name"],
        "website": mp.get("website"),
        "description": mp.get("description"),
        "color": mp.get("color"),
        "features": mp.get("features", []),
        "credential_fields": mp["credential_fields"],
        "transfer_rule_fields": COMMON_TRANSFER_RULES,
    }


# ---------------------------------------------------------------------------
# ACCOUNT (credentials + transfer_rules + auto_sync)
# ---------------------------------------------------------------------------
@router.get("/accounts/{key}")
async def get_account(key: str, current_user: dict = Depends(require_admin)):
    """Pazaryerine kayıtlı hesabı getirir. Yoksa boş iskelet döner."""
    if key not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")
    doc = await db.marketplace_accounts.find_one({"key": key}, {"_id": 0})
    if not doc:
        return {
            "key": key,
            "enabled": False,
            "credentials": {},
            "transfer_rules": {},
            "auto_sync": {
                "products_enabled": False,
                "products_interval_min": 3,
                "orders_enabled": False,
                "orders_interval_min": 5,
                "orders_lookback_hours": 100,
            },
        }
    # GÜVENLİK: kimlik bilgilerini MASKELE — sır alanları (şifreli veya düz) asla
    # frontend'e sızmaz; UI maskeli değeri geri gönderirse save_account mevcut değeri korur.
    _cr = dict(doc.get("credentials") or {})
    for _sf in ("app_secret", "api_secret", "access_token", "secret_key", "password",
                "refresh_token", "oauth_token", "oauth_token_secret", "token", "client_secret"):
        if _cr.get(_sf):
            _cr[_sf] = "********"
    doc["credentials"] = _cr
    return doc


@router.post("/accounts/{key}")
async def save_account(key: str, payload: dict, current_user: dict = Depends(require_admin)):
    """Hesap bilgileri + transfer rules + auto-sync'i kaydeder."""
    if key not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")
    # GÜVENLİK: credentials içindeki sır alanlarını at-rest ŞİFRELE. Maskeli ("****")
    # gelirse dokunma (mevcut korunur). Getter'lar (_get_temu_config / _hb_unmask) çözer.
    _creds = dict(payload.get("credentials") or {})
    try:
        from security.crypto import encrypt as _enc_secret
        _existing = await db.marketplace_accounts.find_one({"key": key}, {"_id": 0, "credentials": 1}) or {}
        _ex_creds = _existing.get("credentials") or {}
        for _sf in ("app_secret", "api_secret", "access_token", "secret_key", "password",
                    "refresh_token", "oauth_token", "oauth_token_secret", "token", "client_secret"):
            _v = _creds.get(_sf)
            if _v is None:
                continue
            _vs = str(_v).strip()
            if not _vs or set(_vs) <= {"*", "•", "·"}:
                # maskeli/boş → mevcut şifreli değeri koru
                if _ex_creds.get(_sf) is not None:
                    _creds[_sf] = _ex_creds.get(_sf)
                continue
            if not _vs.startswith("v1:"):  # zaten şifreli değilse şifrele
                _creds[_sf] = _enc_secret(_vs)
    except Exception:
        pass
    update = {
        "key": key,
        "enabled": bool(payload.get("enabled", False)),
        "credentials": _creds,
        "transfer_rules": payload.get("transfer_rules") or {},
        "auto_sync": payload.get("auto_sync") or {
            "products_enabled": False,
            "products_interval_min": 3,
            "orders_enabled": False,
            "orders_interval_min": 5,
            "orders_lookback_hours": 100,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": current_user.get("email") or current_user.get("id"),
    }
    await db.marketplace_accounts.update_one({"key": key}, {"$set": update}, upsert=True)
    return {"success": True, "message": f"{MARKETPLACES[key]['name']} ayarları kaydedildi",
            "key": key, "enabled": update["enabled"]}


@router.get("/accounts")
async def list_accounts(current_user: dict = Depends(require_admin)):
    """Tüm kayıtlı hesapları liste olarak döner. Dashboard özeti için."""
    cursor = db.marketplace_accounts.find({}, {"_id": 0, "credentials.password": 0,
                                                "credentials.api_secret": 0,
                                                "credentials.secret_key": 0,
                                                "credentials.app_secret": 0,
                                                "credentials.access_token": 0,
                                                "credentials.refresh_token": 0,
                                                "credentials.oauth_token": 0,
                                                "credentials.oauth_token_secret": 0,
                                                "credentials.token": 0,
                                                "credentials.client_secret": 0})
    items = await cursor.to_list(length=100)
    return {"accounts": items, "total": len(items)}


# ---------------------------------------------------------------------------
# INTEGRATION LOGS (kayıt + okuma)
# ---------------------------------------------------------------------------
async def log_integration_event(
    marketplace: str,
    action: str,
    status: str,
    direction: str = "outbound",
    ref_id: Optional[str] = None,
    message: str = "",
    payload_in: Optional[dict] = None,
    payload_out: Optional[dict] = None,
    duration_ms: Optional[int] = None,
):
    """
    Servis katmanından çağrılacak yardımcı. Bir entegrasyon olayını kayda geçer.
    Bu fonksiyon integrations.py içindeki Trendyol/HB/Temu çağrı sarmalayıcısı
    tarafından API call öncesi/sonrası çağrılmalı.

    action: "product_push" | "product_update" | "order_pull" | "stock_update"
            | "price_update" | "return_pull" | "webhook_receive" | ...
    direction: "outbound" (biz onlara) | "inbound" (onlar bize)
    status: "success" | "failed" | "partial" | "queued"
    """
    doc = {
        "marketplace": marketplace,
        "action": action,
        "status": status,
        "direction": direction,
        "ref_id": ref_id,
        "message": message or "",
        "payload_in": payload_in,
        "payload_out": payload_out,
        "duration_ms": duration_ms,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db.integration_logs.insert_one(doc)
    except Exception:
        # Log yazmak ana akışı bozmamalı
        pass


@router.get("/logs")
async def list_logs(
    marketplace: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    ref_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(require_admin),
):
    """Entegrasyon logları listeleme (filtrelerle)."""
    q = {}
    if marketplace: q["marketplace"] = marketplace
    if action: q["action"] = action
    if status: q["status"] = status
    if ref_id: q["ref_id"] = ref_id
    if date_from or date_to:
        q["created_at"] = {}
        if date_from: q["created_at"]["$gte"] = date_from
        # DENETİM FIX: bitiş günü tamamen dışlanıyordu (string karşılaştırma) → gün sonu ekle.
        if date_to:
            q["created_at"]["$lte"] = date_to if "T" in str(date_to) else f"{date_to}T23:59:59.999999"

    total = await db.integration_logs.count_documents(q)
    cursor = db.integration_logs.find(q, {"_id": 0}).sort("created_at", -1) \
        .skip((page - 1) * limit).limit(limit)
    items = await cursor.to_list(length=limit)
    return {"logs": items, "total": total, "page": page, "limit": limit}


@router.get("/logs/summary")
async def logs_summary(current_user: dict = Depends(require_admin)):
    """
    Son N işlem özeti (Ticimax'taki "Son 5 İşleminiz" widget'ı gibi).
    Pazaryeri bazlı son aktarım + toplam/başarılı/hatalı sayıları.
    """
    pipeline = [
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": {"marketplace": "$marketplace", "action": "$action"},
            "last_at": {"$first": "$created_at"},
            "total": {"$sum": 1},
            "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
            "failed": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
        }},
        {"$sort": {"last_at": -1}},
        {"$limit": 20},
    ]
    items = await db.integration_logs.aggregate(pipeline).to_list(length=20)
    # BSON ObjectId yok ama _id composite; serialize et
    out = []
    for it in items:
        grp = it.get("_id") or {}
        out.append({
            "marketplace": grp.get("marketplace") if isinstance(grp, dict) else None,
            "action": grp.get("action") if isinstance(grp, dict) else None,
            "last_at": it.get("last_at"),
            "total": it.get("total", 0),
            "success": it.get("success", 0),
            "failed": it.get("failed", 0),
        })
    return {"items": out}


@router.post("/logs/test")
async def create_test_log(payload: dict, current_user: dict = Depends(require_admin)):
    """
    Geliştirme/demo için manuel log ekleme. Canlıya geçişte silinebilir.
    """
    await log_integration_event(
        marketplace=payload.get("marketplace", "trendyol"),
        action=payload.get("action", "product_push"),
        status=payload.get("status", "success"),
        direction=payload.get("direction", "outbound"),
        ref_id=payload.get("ref_id"),
        message=payload.get("message", "Test log"),
    )
    return {"success": True}


# ---------------------------------------------------------------------------
# UNIFIED AUTO-SYNC VIEW (Ticimax Otomatik Güncelleme ekranı için)
# ---------------------------------------------------------------------------
@router.get("/auto-sync")
async def get_auto_sync_settings(current_user: dict = Depends(require_admin)):
    """
    Tüm pazaryerlerinin auto-sync ayarlarını tek yerde toplu döner.
    Frontend AutoSyncSettings.jsx'i besler.
    """
    cursor = db.marketplace_accounts.find({}, {"_id": 0, "key": 1, "enabled": 1, "auto_sync": 1})
    items = await cursor.to_list(length=100)
    meta = {k: {"name": v["name"], "color": v.get("color")} for k, v in MARKETPLACES.items()}
    # Eksikleri tamamla (kayıt olmayan pazaryerleri için default)
    present = {it["key"] for it in items}
    for k, v in meta.items():
        if k not in present:
            items.append({"key": k, "enabled": False, "auto_sync": {
                "products_enabled": False, "products_interval_min": 3,
                "orders_enabled": False, "orders_interval_min": 5,
                "orders_lookback_hours": 100,
            }})
    for it in items:
        m = meta.get(it["key"], {})
        it["name"] = m.get("name", it["key"])
        it["color"] = m.get("color")
    return {"items": items}
