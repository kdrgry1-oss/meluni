"""
Facette E-Commerce API - Main Server
Modular architecture with routes
"""
from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import logging
from pathlib import Path

# Load .env early so env-based integrations (EMERGENT_LLM_KEY etc.) are available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# httpx, istek URL'ini INFO seviyesinde komple loglar — CAPI gibi token'i query-string'de
# tasiyan cagrilarda access_token DUZ METIN olarak log'a duser (Railway log akisi vb. sizinti
# riski). Request-level INFO loglarini WARNING'e cekerek bunu engelle (hatalar yine gorunur).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Import routes
from routes import (
    auth_router,
    products_router,
    orders_router,
    categories_router,
    banners_router,
    cms_router,
    integrations_router,
    admin_router,
    customer_router,
    variants_router,
    webhooks_router,
    attributes_router,
    upload_router,
    upload_files_router,
    settings_router,
)
from routes.vendors import router as vendors_router
from routes.pages import router as pages_router
from routes.admin_rbac import router as admin_rbac_router
from routes.seo import router as seo_router
from routes.size_tables import router as size_tables_router, public_router as size_tables_public_router
from routes.manufacturing import router as manufacturing_router, suppliers_router as manufacturing_suppliers_router
from routes.decision_board import router as decision_board_router
from routes.ai_chatbot import router as ai_chatbot_router
from routes.whatsapp_webhook import router as whatsapp_webhook_router
from routes.meta_messaging_webhook import router as meta_messaging_webhook_router
from routes.ai_assistant import router as ai_assistant_router
from routes.locations import router as locations_router
from routes.attribution import router as attribution_router
from routes.members import router as members_router
from routes.coupons import admin_router as coupons_admin_router, public_router as coupons_public_router, campaigns_router as campaigns_router
from routes.reports import router as reports_router
from routes.extras import (
    cart_router,
    admin_cart_router,
    reviews_public_router,
    reviews_admin_router,
    seo_public_router,
    seo_admin_router,
)
from routes.catalog_extras import (
    brands_router, tags_router, member_groups_router, announcements_router, popups_router,
    storefront_extras_router,
    alerts_public_router, alerts_admin_router,
    havale_public_router, havale_admin_router,
    admin_orders_router,
    rules_router,
    extra_reports_router,
    tickets_public_router, tickets_admin_router,
    email_admin_router,
    currency_router,
)
from routes.admin_tasks import router as admin_tasks_router
from routes.business_rules_api import admin_router as business_rules_admin_router, public_router as business_rules_public_router
from routes.custom_theme import admin_router as custom_theme_admin_router, public_router as custom_theme_public_router
from routes.help_center import admin_router as help_center_admin_router, public_router as help_center_public_router
from routes.referrals import public_router as referrals_public_router, admin_router as referrals_admin_router
from routes.shared_carts import router as shared_carts_router
from routes.gift_cards import router as gift_cards_router, admin_router as gift_cards_admin_router
from routes.loyalty import router as loyalty_router
from routes.barcode_cards import router as barcode_cards_router
from routes.provider_settings import router as provider_settings_router
from routes.iys import router as iys_consent_router  # /iys — OTP + ticari ileti izni (iys_router ile ÇAKIŞMASIN)
from routes.marketplace_hub import router as marketplace_hub_router
from routes.brand_mapping import router as brand_mapping_router
from routes.category_mapping import router as category_mapping_router
from routes.automation_status import router as automation_status_router
from routes.footer_template import public_router as footer_public_router, admin_router as footer_admin_router
from routes.newsletter import public_router as newsletter_public_router, admin_router as newsletter_admin_router
from routes.instagram import public_router as instagram_public_router, admin_router as instagram_admin_router
# [A4b-ticimax-off] from routes.ticimax_stock_sync import router as ticimax_stock_sync_router
from routes.ticimax_history import router as ticimax_history_router  # YALNIZ geçmiş veri kurtarma (stok senkronu YOK)
# [A4-ticimax-off] from routes.ticimax_category_sync import router as ticimax_category_sync_router
# [A4-ticimax-off] from routes.ticimax_member_sync import router as ticimax_member_sync_router
# [A4-ticimax-off] from routes.ticimax_product_pull import router as ticimax_product_pull_router
from routes.rooftr_returns import router as rooftr_returns_router
from routes.bulk_ops import router as bulk_ops_router
from routes.analytics_extra import router as analytics_extra_router
from routes.notifications import router as notifications_router
from routes.integrations_temu import router as integrations_temu_router
from routes.trendyol_retry_queue import router as trendyol_retry_queue_router, background_retry_loop as trendyol_retry_bg_loop
from routes.capi import router as capi_router
from services.capi.orchestrator import background_retry_loop as capi_retry_bg_loop
from routes.customer_risk import router as customer_risk_router
from routes.production_plan import router as production_plan_router
from routes.marketing_pixels import router as marketing_pixels_router
from routes.social_auth import router as social_auth_router
from routes.security_dashboard import router as security_dashboard_router
from routes.mobile import router as mobile_router
from routes.admin_mobile import router as admin_mobile_router
from routes.secrets_vault import router as secrets_vault_router
from routes.system_health import router as system_health_router
from routes.reports_v2 import router as reports_v2_router, costs_router as product_costs_router
from routes.production_hooks import router as production_hooks_router
from routes.size_recommender import router as size_rec_router
from routes.iys_integration import router as iys_router

# Database
from routes.deps import client, db

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown"""
    # Startup
    logger.info("Starting Facette E-Commerce API...")
    
    try:
        # Create admin user if not exists
        admin = await db.users.find_one({"email": "admin@facette.com"})
        if not admin:
            from routes.deps import hash_password, generate_id
            from datetime import datetime, timezone
            import secrets as _secrets
            # Y13: Sabit 'admin123' KULLANILMAZ ve şifre ASLA loglanmaz. Şifre ADMIN_INITIAL_PASSWORD
            # ortam değişkeninden alınır; yoksa güçlü rastgele üretilip yalnızca korumalı bir dosyaya
            # (backend/data/.admin_initial_password, chmod 600) yazılır ve log'a sadece DOSYA YOLU düşer.
            _admin_pw = (os.environ.get("ADMIN_INITIAL_PASSWORD") or "").strip()
            _generated = False
            if len(_admin_pw) < 8:
                _admin_pw = _secrets.token_urlsafe(16)
                _generated = True
            await db.users.insert_one({
                "id": generate_id(),
                "email": "admin@facette.com",
                "password": hash_password(_admin_pw),
                "first_name": "Admin",
                "last_name": "User",
                "is_admin": True,
                "is_super_admin": True,
                "is_active": True,
                "must_change_password": _generated,
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            if _generated:
                try:
                    _pdir = os.path.join(os.path.dirname(__file__), "data")
                    os.makedirs(_pdir, exist_ok=True)
                    _pf = os.path.join(_pdir, ".admin_initial_password")
                    with open(_pf, "w") as _fh:
                        _fh.write(_admin_pw)
                    try:
                        os.chmod(_pf, 0o600)
                    except Exception:
                        pass
                    logger.warning(f"Admin olusturuldu (admin@facette.com). Ilk sifre: {_pf} dosyasinda. "
                                   "Giris sonrasi degistirin; dosyayi silin.")
                except Exception:
                    logger.warning("Admin olusturuldu; rastgele sifre dosyaya yazilamadi — "
                                   "sifre-sifirlama ile giris yapin.")
            else:
                logger.info("Admin olusturuldu (admin@facette.com) — ADMIN_INITIAL_PASSWORD kullanildi.")
        elif not admin.get("is_super_admin"):
            await db.users.update_one(
                {"email": "admin@facette.com"},
                {"$set": {"is_super_admin": True}},
            )

        # GÜVENLİK: is_admin=True kalmış MÜŞTERİ hesaplarını (personel işareti taşımayan:
        # created_by/role_id/is_super_admin/varsayılan admin YOK) admin'likten düşür →
        # Üyeler listesine geçsinler, panele giremesinler. İdempotent; her başlangıçta güvenli.
        try:
            from routes.admin_rbac import demote_customer_admins
            _dc = await demote_customer_admins()
            if _dc.get("demoted"):
                logger.warning(f"[guvenlik] {_dc['demoted']} musteri hesabi admin'likten dusuruldu (Uyeler'e tasindi).")
        except Exception as _de:
            logger.error(f"[guvenlik] customer-admin temizligi hatasi: {_de}")

        # TELAFİ: bozuk/eksik XML feed yüzünden pasife alınmış ("ticimax_xml_missing") ürünleri
        # TEK SEFER geri aktif et — "bazı ürünler yok oldu" sorununu otomatik onarır (bayrakla 1 kez).
        try:
            from routes.integrations_common import restore_xml_missing_products_once
            _rx = await restore_xml_missing_products_once()
            if _rx.get("restored"):
                logger.warning(f"[urun-telafi] {_rx['restored']} pasife alinan urun geri aktiflestirildi.")
        except Exception as _re:
            logger.error(f"[urun-telafi] xml-missing restore hatasi: {_re}")

        # TELAFİ: parası HİÇ alınmadığı hâlde 'İptal Edildi' görünen (auto-cancel) kart siparişlerini
        # 'payment_failed' (Ödeme Alınamadı) yap → ekip yanlışlıkla PARA İADESİ yapmasın. İdempotent.
        try:
            from routes.orders import reclassify_auto_cancelled_unpaid_orders
            _rf = await reclassify_auto_cancelled_unpaid_orders()
            if _rf.get("reclassified"):
                logger.warning(f"[odeme-telafi] {_rf['reclassified']} odenmemis 'iptal' siparis 'payment_failed' yapildi.")
        except Exception as _rfe:
            logger.error(f"[odeme-telafi] reclassify hatasi: {_rfe}")

        # Create indexes
        await db.products.create_index("slug")
        await db.products.create_index("stock_code")
        # Performans: ürün listesi/detay sorguları için (Iter perf)
        await db.products.create_index("id")
        await db.products.create_index("category_name")
        await db.products.create_index([("is_active", 1), ("created_at", -1)])
        await db.products.create_index([("category_ids", 1), ("is_active", 1)])
        await db.orders.create_index("order_number")
        await db.orders.create_index("user_id")
        # Performans: sipariş listesi tarih/durum/platform filtreleri için
        await db.orders.create_index([("created_at", -1)])
        await db.orders.create_index([("status", 1), ("created_at", -1)])
        await db.orders.create_index([("platform", 1), ("created_at", -1)])
        await db.orders.create_index([("payment_status", 1), ("created_at", -1)])
        # İADE / GİDER PUSULASI performansı (Atlas "Scanned/Returned > 1000" uyarısı):
        # bu koleksiyonlar order_number/claim_id/return_id/status ile SÜREKLİ sorgulanıyordu
        # ama index YOKTU → her sorgu tam koleksiyon taraması. Sık filtre alanlarını indeksle.
        await db.trendyol_claims.create_index("order_number")
        await db.trendyol_claims.create_index([("platform", 1), ("claim_status", 1)])
        await db.trendyol_claims.create_index([("claim_status", 1)])
        await db.trendyol_claims.create_index([("created_date", -1)])
        await db.trendyol_claims.create_index("has_gider_pusulasi")
        await db.customer_returns.create_index("order_id")
        await db.customer_returns.create_index("order_number")
        await db.customer_returns.create_index([("status", 1), ("created_at", -1)])
        await db.customer_returns.create_index([("created_at", -1)])
        await db.gider_pusulasi.create_index("claim_id")
        await db.gider_pusulasi.create_index("return_id")
        await db.gider_pusulasi.create_index("order_number")
        await db.gider_pusulasi.create_index([("number", -1)])
        await db.order_events.create_index([("order_id", 1), ("created_at", -1)])
        await db.order_events.create_index([("created_at", -1)])
        await db.orders_deleted.create_index([("deleted_at", -1)])
        await db.users.create_index("email", unique=True)
        # Security audit indexes — fast forensic queries as the collection grows
        await db.auth_audit_logs.create_index([("created_at", -1)])
        await db.auth_audit_logs.create_index([("event", 1), ("email", 1), ("created_at", -1)])
        await db.auth_audit_logs.create_index([("ip", 1), ("created_at", -1)])
        await db.auth_audit_logs.create_index([("success", 1), ("created_at", -1)])
        # Mobile devices (push notifications)
        await db.user_devices.create_index([("user_id", 1), ("device_id", 1)], unique=True)
        await db.user_devices.create_index([("push_token", 1)])
        await db.user_devices.create_index([("is_active", 1), ("platform", 1)])
        # IP blocklist (Iter36 — brute force IP-level ban)
        await db.ip_blocklist.create_index([("ip", 1)], unique=True)
        await db.ip_blocklist.create_index([("blocked_until", 1)])
        # Secrets Vault & monitoring (Iter39)
        await db.vault_secrets.create_index("key", unique=True)
        await db.error_logs.create_index([("created_at", -1)])
        await db.error_logs.create_index([("level", 1), ("created_at", -1)])
        await db.error_logs.create_index([("kind", 1), ("created_at", -1)])
        await db.alerts.create_index([("created_at", -1)])
        await db.alerts.create_index([("read", 1), ("created_at", -1)])
        await db.alerts.create_index([("fingerprint", 1), ("created_at", -1)])
        # Product costs (manuel maliyet) — Iter 42
        await db.product_costs.create_index("product_id", unique=True)
        # Iter 43 indexes
        await db.production_plan.create_index([("product_id", 1), ("status", 1)])
        await db.production_plan.create_index([("created_at", -1)])
        await db.iys_permissions.create_index(
            [("recipient", 1), ("recipient_type", 1), ("message_type", 1)], unique=True)
        await db.iys_permissions.create_index([("expires_at", 1)])
        
        logger.info("Database indexes created")
    except Exception as e:
        logger.warning(f"Database initialization warning (server will still start): {e}")

    # Start background scheduler (auto-cancel 48h unpaid havale orders)
    try:
        from scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler start warning: {e}")

    # Start Trendyol stuck barcode retry queue (saatte bir)
    try:
        import asyncio as _asyncio
        _asyncio.create_task(trendyol_retry_bg_loop(lambda: db))
        logger.info("Trendyol retry queue background loop started (her saat)")
    except Exception as e:
        logger.warning(f"Trendyol retry loop start warning: {e}")

    # Start CAPI (Conversions API) retry queue (30 dk'da bir)
    try:
        import asyncio as _asyncio
        _asyncio.create_task(capi_retry_bg_loop(lambda: db, interval_seconds=1800))
        logger.info("CAPI retry queue background loop started (her 30 dk)")
    except Exception as e:
        logger.warning(f"CAPI retry loop start warning: {e}")

    # Tek seferlik onarım: ölçü tablosu görsellerinin kaybolan is_size_table işareti
    # (bayrakla korunur — yalnız bir kez çalışır; arka planda, startup'ı bloklamaz)
    try:
        import asyncio as _asyncio
        from routes.size_tables import repair_size_table_markers
        _asyncio.create_task(repair_size_table_markers())
    except Exception as e:
        logger.warning(f"Size-table marker repair start warning: {e}")

    # Tek seferlik onarım: eski Trendyol yorumlarının created_at'i gerçek yorum tarihine
    # çekilir (bayrak korumalı; ham comment_date kayıtlı olanlar — ağ gerekmez).
    try:
        import asyncio as _asyncio
        from routes.integrations_trendyol_qna import backfill_review_dates
        _asyncio.create_task(backfill_review_dates())
    except Exception as e:
        logger.warning(f"Review date backfill start warning: {e}")

    # Tek seferlik onarım: geçmişte iptal edilmiş siparişlerde yanan kupon hakları
    # geri açılır (hoş geldin kodu iptal sonrası tekrar kullanılamıyordu) — bayrak korumalı.
    try:
        import asyncio as _asyncio
        from routes.orders import release_cancelled_redemptions_backfill
        _asyncio.create_task(release_cancelled_redemptions_backfill())
    except Exception as e:
        logger.warning(f"Coupon cancel-release backfill start warning: {e}")

    # Tek seferlik migrasyon: kapida odemeyi varsayilan olarak KAPAT.
    # Admin panelinden (Ayarlar > Odeme Yontemleri) tekrar acilabilir; bu blok
    # _cod_default_off_v1 isaretiyle korundugu icin SADECE BIR KEZ calisir ve
    # admin sonradan tekrar acarsa bir daha kapatmaz.
    try:
        _cfg = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
        if not _cfg.get("_cod_default_off_v1"):
            _pm = dict(_cfg.get("payment_methods") or {})
            _pm.setdefault("credit_card", True)
            _pm.setdefault("bank_transfer", True)
            _pm["cash_on_delivery"] = False
            await db.settings.update_one(
                {"id": "main"},
                {"$set": {"payment_methods": _pm, "_cod_default_off_v1": True},
                 "$setOnInsert": {"id": "main"}},
                upsert=True,
            )
            logger.info("[migrate] cash_on_delivery varsayilan KAPALI uygulandi (_cod_default_off_v1)")
    except Exception as e:
        logger.warning(f"[migrate] cod_default_off atlandi: {e}")

    # Tek seferlik: "Google Merchant" XML feed kaydini olustur (XML Feed'ler sayfasinda gorunur).
    # Cikti /api/products/feed/google-merchant.xml — google-merchant-feed.xml ile birebir ayni format.
    try:
        _cfgf = await db.settings.find_one({"id": "main"}, {"_id": 0, "_google_feed_seeded_v1": 1}) or {}
        if not _cfgf.get("_google_feed_seeded_v1"):
            from routes.deps import generate_id as _gid
            from datetime import datetime as _dt, timezone as _tz
            if not await db.xml_feeds.find_one({"slug": "google-merchant"}):
                await db.xml_feeds.insert_one({
                    "id": _gid(), "name": "Google Merchant", "slug": "google-merchant",
                    "target": "google", "enabled": True, "in_stock_only": False,
                    "created_at": _dt.now(_tz.utc).isoformat(),
                })
                logger.info("[migrate] Google Merchant XML feed seed edildi (/feed/google-merchant.xml)")
            await db.settings.update_one(
                {"id": "main"},
                {"$set": {"_google_feed_seeded_v1": True}, "$setOnInsert": {"id": "main"}},
                upsert=True)
    except Exception as e:
        logger.warning(f"[migrate] google feed seed atlandi: {e}")

    # Tek seferlik: LEGACY iade siparisi temizligi.
    #   (A) Import-gunu (2026-06-11) telefonsuz MUKERRER kopyalari sil — yalnizca ayni
    #       siparis numarasindan gercek ikizi (telefonlu/gercek tarihli) olanlari.
    #   (B) id'siz kalan tum siparislere id ata — satir bazli durum degistirme
    #       (/api/orders/{id}/status id alanina gore eslesir) bu kayitlarda calissin.
    # Guard: _orders_iade_cleanup_v1 → sadece BIR KEZ calisir.
    try:
        _omf = await db.settings.find_one({"id": "main"}, {"_id": 0, "_orders_iade_cleanup_v1": 1}) or {}
        if not _omf.get("_orders_iade_cleanup_v1"):
            from routes.deps import generate_id as _gidc
            removed = 0
            async for _d in db.orders.find(
                {"created_at": {"$regex": "^2026-06-11"}},
                {"_id": 1, "order_number": 1, "phone": 1, "shipping_address": 1},
            ):
                _ph = ((_d.get("shipping_address") or {}).get("phone") or _d.get("phone") or "").strip()
                if _ph:
                    continue  # telefonu olan kopyayi ASLA silme
                _onum = _d.get("order_number")
                if not _onum:
                    continue
                _twin = await db.orders.find_one(
                    {"order_number": _onum, "_id": {"$ne": _d["_id"]}}, {"_id": 1})
                if _twin:  # gercek ikizi var → bu import-gunu kopyayi sil
                    await db.orders.delete_one({"_id": _d["_id"]})
                    removed += 1
            fixed = 0
            async for _d in db.orders.find(
                {"$or": [{"id": {"$exists": False}}, {"id": None}, {"id": ""}]}, {"_id": 1}):
                await db.orders.update_one({"_id": _d["_id"]}, {"$set": {"id": _gidc()}})
                fixed += 1
            await db.settings.update_one(
                {"id": "main"},
                {"$set": {"_orders_iade_cleanup_v1": True}, "$setOnInsert": {"id": "main"}},
                upsert=True)
            logger.info(f"[migrate] iade cleanup: {removed} mukerrer silindi, {fixed} id atandi (_orders_iade_cleanup_v1)")
    except Exception as e:
        logger.warning(f"[migrate] iade cleanup atlandi: {e}")

    # Tek seferlik: iade-akisi durumlarinda SMS+mail bildirimini AC (refund-pay dahil).
    # Canli notify config'inde 'refunded' vb. kapali oldugu icin "Iade Bedeli Odendi"de
    # bildirim gitmiyordu. Guard: _return_notify_all_v1 → bir kez acar, sonra admin degistirebilir.
    try:
        _osc = await db.settings.find_one(
            {"id": "order_status_config"}, {"_id": 0, "_return_notify_all_v1": 1}) or {}
        if not _osc.get("_return_notify_all_v1"):
            _ret_keys = ["return_requested", "return_approved", "return_rejected",
                         "return_in_transit", "returned", "partial_refunded", "refunded"]
            _setn = {f"notify.{k}": {"sms": True, "email": True} for k in _ret_keys}
            _setn["_return_notify_all_v1"] = True
            await db.settings.update_one(
                {"id": "order_status_config"},
                {"$set": _setn, "$setOnInsert": {"id": "order_status_config"}},
                upsert=True)
            logger.info("[migrate] iade durumlari SMS+mail acildi (_return_notify_all_v1)")
    except Exception as e:
        logger.warning(f"[migrate] return notify enable atlandi: {e}")

    yield
    
    # Shutdown
    logger.info("Shutting down...")
    try:
        from scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception:
        pass
    client.close()

# Create FastAPI app
# GÜVENLİK: prod'da interaktif API dokümanı (/docs, /redoc, OpenAPI şeması) kapalı —
# saldırı yüzeyini/uç envanteri ifşasını azaltır. DEV'de açmak için ENABLE_API_DOCS=1.
_docs_enabled = os.environ.get("ENABLE_API_DOCS", "").strip() == "1" or \
    os.environ.get("ENVIRONMENT", "production").strip().lower() in ("dev", "development", "local")
app = FastAPI(
    title="Facette E-Commerce API",
    version="3.0",
    description="Modular E-Commerce API with Iyzico, Trendyol, MNG Kargo, GIB integrations",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# CORS — strict whitelist (no wildcard in production). Configure via CORS_ORIGINS env.
cors_origins = os.environ.get("CORS_ORIGINS", "")
_origins_list = [o.strip() for o in cors_origins.split(",") if o.strip()] if cors_origins and cors_origins != "*" else (["*"] if cors_origins == "*" else [])
if not _origins_list:
    logger.warning("CORS_ORIGINS env is missing/empty — defaulting to localhost only. "
                   "Set explicit whitelist in /app/backend/.env for production.")
    _origins_list = ["http://localhost:3000"]
# Güvenlik: wildcard origin ("*") ile allow_credentials=True BİRLİKTE kullanılamaz — Starlette
# çağıranın Origin'ini yansıtıp kimlik-bilgili (cookie/Authorization) çapraz-origin isteğe izin
# verirdi. Wildcard varsa credentials KAPATILIR (spec gereği zaten geçersiz kombinasyon).
_allow_credentials = _origins_list != ["*"]
if not _allow_credentials:
    logger.warning("CORS_ORIGINS=* ile credentials devre dışı bırakıldı. Üretimde açık bir "
                   "origin whitelist'i tanımlayın.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins_list,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)

# ---------------------------------------------------------------------------
# RATE LIMITING (slowapi) — protects /api/auth/* against brute force
# ---------------------------------------------------------------------------
try:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from routes.deps import limiter as _limiter

    if _limiter is not None:
        app.state.limiter = _limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)
except Exception as _e:
    logger.warning(f"slowapi rate limiter not enabled: {_e}")

# ---------------------------------------------------------------------------
# SECURITY HEADERS MIDDLEWARE (CSP, HSTS, XFO, XCTO, Referrer-Policy, etc.)
# ---------------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware as _BHM


class SecurityHeadersMiddleware(_BHM):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Skip for static asset routes if any
        path = request.url.path
        # Frontend may inject 3rd-party pixels (GA4, Meta, TikTok). Allow https:
        # script sources broadly but block 'unsafe-eval'. 'unsafe-inline' is
        # tolerated for inline tag-manager bootstraps.
        csp = (
            "default-src 'self' https: data: blob:; "
            "script-src 'self' 'unsafe-inline' https: blob:; "
            "style-src 'self' 'unsafe-inline' https: data:; "
            "img-src 'self' data: blob: https:; "
            "font-src 'self' data: https:; "
            "connect-src 'self' https: wss:; "
            "frame-src 'self' https:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self' https:; "
            "frame-ancestors 'none'"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(self), usb=(), interest-cohort=()",
        )
        # HSTS — only meaningful over HTTPS but harmless on HTTP previews
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        # Disable cross-origin Spectre-class leaks for API responses
        if path.startswith("/api/"):
            response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response


app.add_middleware(SecurityHeadersMiddleware)


class BodySizeLimitMiddleware(_BHM):
    """DoS koruması: Content-Length çok büyük olan istekleri body okunmadan 413 ile
    reddeder (devasa gövde → bellek tükenmesi). Cap 120MB — video yükleme sınırının
    (100MB) üstünde olduğu için meşru hiçbir isteği engellemez."""
    MAX_BYTES = 120 * 1024 * 1024

    async def dispatch(self, request, call_next):
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > self.MAX_BYTES:
                    from starlette.responses import JSONResponse as _JR
                    return _JR(status_code=413, content={"detail": "İstek gövdesi çok büyük"})
            except (ValueError, TypeError):
                pass
        return await call_next(request)


app.add_middleware(BodySizeLimitMiddleware)

# ---------------------------------------------------------------------------
# INTEGRATION LOGGING MIDDLEWARE
# ---------------------------------------------------------------------------
# /api/integrations/{marketplace}/... altındaki tüm çağrıları otomatik olarak
# `integration_logs` koleksiyonuna kaydeder. Bu sayede main agent'ın her
# endpoint'i manuel sarmalamasına gerek kalmaz.
#
# Marketplace, URL path'inin 3. segmentinden (trendyol / hepsiburada / temu /
# iyzico vb.) alınır. iyzico/gib/cargo gibi non-marketplace olanlar atlanır.
# Action, URL path'in kalanından türetilir (products/sync → product_push vb.).
# ---------------------------------------------------------------------------
import time as _time
from starlette.middleware.base import BaseHTTPMiddleware


MARKETPLACE_PATH_KEYS = {"trendyol", "hepsiburada", "temu", "n11", "amazon-tr",
                         "amazon-de", "aliexpress", "etsy", "hepsi-global",
                         "fruugo", "emag", "trendyol-ihracat", "ciceksepeti"}


def _action_from_path(path: str) -> str:
    """
    URL'den kaba bir "action" çıkarır. Örn:
      /api/integrations/trendyol/products/sync        → product_push
      /api/integrations/trendyol/orders/import        → order_pull
      /api/integrations/trendyol/products/inventory-sync → stock_update
      /api/integrations/hepsiburada/products/push     → product_push
    """
    p = path.lower()
    if "inventory" in p or "stock" in p: return "stock_update"
    if "price" in p: return "price_update"
    if "category" in p or "categories" in p: return "category_sync"
    if "brand" in p: return "brand_sync"
    if "claim" in p or "return" in p: return "return_pull"
    if "/orders/import" in p or "/orders/pull" in p or "/orders/sync" in p or "/orders/fetch" in p: return "order_pull"
    if "/orders/" in p: return "order_update"
    if "/products/" in p: return "product_push"
    if "webhook" in p: return "webhook_receive"
    if "settings" in p or "status" in p or "debug" in p: return "config_read"
    return "api_call"


class IntegrationLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        # Sadece /api/integrations/{marketplace}/... yollarını ilgilendir
        if not path.startswith("/api/integrations/"):
            return await call_next(request)

        parts = [p for p in path.split("/") if p]
        # parts: ["api","integrations","<marketplace>","..."]
        mk = parts[2] if len(parts) > 2 else None
        # non-marketplace veya ayar/okuma ise log atla
        if mk not in MARKETPLACE_PATH_KEYS:
            return await call_next(request)
        # GET = config_read — çok gürültü yapar, atla
        if request.method.upper() == "GET":
            return await call_next(request)

        start = _time.time()
        status = "success"
        msg = ""
        response = None
        try:
            response = await call_next(request)
            if response.status_code >= 500: status = "failed"
            elif response.status_code >= 400: status = "failed"
            msg = f"{request.method} {path} → HTTP {response.status_code}"
        except Exception as e:
            status = "failed"
            msg = f"{request.method} {path} → EX {type(e).__name__}: {e}"
            raise
        finally:
            try:
                duration = int((_time.time() - start) * 1000)
                # Lazy import — circular dependency'yi önler
                from routes.marketplace_hub import log_integration_event
                await log_integration_event(
                    marketplace=mk,
                    action=_action_from_path(path),
                    status=status,
                    direction="outbound",
                    message=msg,
                    duration_ms=duration,
                )
            except Exception:
                pass
        return response


app.add_middleware(IntegrationLoggingMiddleware)

# ---------------------------------------------------------------------------
# ERROR TRACKING & ALERTING — captures 5xx, slow responses, exceptions
# ---------------------------------------------------------------------------
from security.monitoring import ErrorTrackingMiddleware
app.add_middleware(ErrorTrackingMiddleware)

# Main API Router
api_router = APIRouter(prefix="/api")

# Documentation download (public — markdown indir)
from routes.docs import router as docs_router
api_router.include_router(docs_router)

# İstemci yüklenme/hata telemetrisi (site açılmıyor teşhisi — cihaz/IP/hata)
from routes.client_log import router as client_log_router
api_router.include_router(client_log_router)

from routes.stock_notify import router as stock_notify_router
api_router.include_router(stock_notify_router)

from routes.payment import router as payment_router
api_router.include_router(payment_router)

# Include all route modules
api_router.include_router(auth_router)
api_router.include_router(products_router)
api_router.include_router(orders_router)
api_router.include_router(categories_router)
api_router.include_router(banners_router)
api_router.include_router(cms_router)
api_router.include_router(pages_router)
# Iyzico endpoint'leri — integrations_router'ın catch-all /{marketplace} rotasından ÖNCE include edilmeli
from routes.integrations_iyzico import router as iyzico_router
from routes.integrations_dogan import router as dogan_router
from routes.integrations_trendyol_qna import router as trendyol_qna_router
api_router.include_router(iyzico_router, prefix="/integrations")
# Doğan e-Dönüşüm — Iter35 refactor: ayrı modül. Catch-all /{marketplace}'den ÖNCE
api_router.include_router(dogan_router, prefix="/integrations")
# Trendyol Q&A + Reviews — Iter37 refactor: catch-all'dan ÖNCE
api_router.include_router(trendyol_qna_router, prefix="/integrations")
api_router.include_router(integrations_router, prefix="/integrations")
api_router.include_router(integrations_temu_router, prefix="/integrations")
api_router.include_router(trendyol_retry_queue_router)
api_router.include_router(capi_router)
api_router.include_router(admin_router)
api_router.include_router(customer_router)
# ALIAS — Checkout.jsx, üye adreslerini /api/customer/... yolundan çağırıyor; router
# ön eksiz mount edildiği için bu yol 404 dönüyordu (üye checkout'ta kayıtlı adreslerini
# göremiyor, modaldan adres kaydedemiyordu). Aynı router'ı /customer önekiyle de mount
# ederek her iki yolu da çalışır kılıyoruz (Account.jsx ön eksiz yolu kullanmaya devam eder).
api_router.include_router(customer_router, prefix="/customer")
api_router.include_router(variants_router)
api_router.include_router(webhooks_router)
api_router.include_router(attributes_router)
api_router.include_router(upload_router)
api_router.include_router(upload_files_router)
api_router.include_router(settings_router)
api_router.include_router(vendors_router, prefix="/vendors")
api_router.include_router(admin_rbac_router)
api_router.include_router(size_tables_router)
api_router.include_router(size_tables_public_router)
api_router.include_router(manufacturing_router)
api_router.include_router(decision_board_router)
api_router.include_router(manufacturing_suppliers_router)
api_router.include_router(ai_chatbot_router)
api_router.include_router(whatsapp_webhook_router)
api_router.include_router(meta_messaging_webhook_router)
api_router.include_router(ai_assistant_router)
api_router.include_router(locations_router)
api_router.include_router(attribution_router)
from routes.push import router as push_router  # mobil admin push bildirimleri
api_router.include_router(push_router)

from routes.consent import router as consent_router  # KVKK çerez onayı kaydı
api_router.include_router(consent_router)
api_router.include_router(members_router)
api_router.include_router(coupons_admin_router)
api_router.include_router(coupons_public_router)
api_router.include_router(campaigns_router)
api_router.include_router(reports_router)
api_router.include_router(cart_router)
api_router.include_router(admin_cart_router)
api_router.include_router(reviews_public_router)
api_router.include_router(reviews_admin_router)
api_router.include_router(seo_public_router)
api_router.include_router(seo_admin_router)
# Ticimax P1 — catalog extras, ops, reports, communications
for _r in (
    brands_router, tags_router, member_groups_router, announcements_router, popups_router,
    storefront_extras_router,
    alerts_public_router, alerts_admin_router,
    havale_public_router, havale_admin_router,
    admin_orders_router,
    rules_router,
    extra_reports_router,
    tickets_public_router, tickets_admin_router,
    email_admin_router,
    currency_router,
):
    api_router.include_router(_r)
api_router.include_router(admin_tasks_router)
api_router.include_router(business_rules_admin_router)
api_router.include_router(business_rules_public_router)
api_router.include_router(custom_theme_admin_router)
api_router.include_router(custom_theme_public_router)
api_router.include_router(help_center_admin_router)
api_router.include_router(help_center_public_router)
api_router.include_router(referrals_public_router)
api_router.include_router(referrals_admin_router)
api_router.include_router(shared_carts_router)
api_router.include_router(gift_cards_router)
api_router.include_router(gift_cards_admin_router)
api_router.include_router(loyalty_router)
# Barcode cards (products & variants) — tek tek veya toplu yazdırılabilir HTML
# kartlar. Products.jsx'deki "Barkod Yazdır" akışları buraya bağlıdır.
api_router.include_router(barcode_cards_router)
# E-Fatura ve Kargo entegratör ayarları (provider seçimi + credential formu).
# Frontend: EInvoiceSettings.jsx + CargoSettings.jsx bu endpoint'leri kullanır.
api_router.include_router(provider_settings_router)
# İYS (ticari ileti izni) + OTP doğrulama — ödeme adımında kampanya izni ve dijital İYS bildirimi.
api_router.include_router(iys_consent_router)
# Marketplace Hub: tüm e-ticaret pazaryerlerinin (Trendyol, HB, Temu, N11,
# Amazon, AliExpress, Etsy, ...) merkezi yönetimi: credentials, transfer_rules,
# auto_sync ayarları + integration_logs.
api_router.include_router(marketplace_hub_router)
# Marka Eşleştirme (multi-marketplace)
api_router.include_router(brand_mapping_router)
# Kategori Eşleştirme (multi-marketplace)
api_router.include_router(category_mapping_router)
# Otomasyon durumu — admin için cron + log özet
api_router.include_router(automation_status_router)
# Footer şablonu (public + admin)
api_router.include_router(footer_public_router)
api_router.include_router(footer_admin_router)
api_router.include_router(newsletter_public_router)
api_router.include_router(newsletter_admin_router)
from routes.email_marketing import admin_router as email_mkt_admin_router, public_router as email_mkt_public_router
api_router.include_router(email_mkt_admin_router)
api_router.include_router(email_mkt_public_router)
api_router.include_router(instagram_public_router)
api_router.include_router(instagram_admin_router)
# Ticimax canlı stok senkronu (admin)
# [A4b-ticimax-off] api_router.include_router(ticimax_stock_sync_router)
api_router.include_router(ticimax_history_router)  # geçmiş sipariş kurtarma (A4 stok senkronlarını AÇMAZ)
# Ticimax kategori senkronu — "En Yeniler" tam ayna + tüm kategoriler (admin)
# [A4-ticimax-off] api_router.include_router(ticimax_category_sync_router)
# Ticimax üye içe aktarma — e-posta eşleştirme + geçmiş sipariş bağlama (admin)
# [A4-ticimax-off] api_router.include_router(ticimax_member_sync_router)
# Ticimax belirli kart ID ürün çekme — kaynak→hedef kart ID ile yeni ürün (admin)
# [A4-ticimax-off] api_router.include_router(ticimax_product_pull_router)

api_router.include_router(rooftr_returns_router)
# Toplu fiyat/stok Excel ops + stok uyarı + yeniden sipariş önerisi
api_router.include_router(bulk_ops_router)
# RFM müşteri segmentasyonu + marketplace karlılık + Google Merchant feed
api_router.include_router(analytics_extra_router)
api_router.include_router(notifications_router)
api_router.include_router(customer_risk_router)
api_router.include_router(production_plan_router)
api_router.include_router(marketing_pixels_router)
api_router.include_router(social_auth_router)
# Security dashboard — auth_audit_logs üzerinde admin görünürlüğü
api_router.include_router(security_dashboard_router)
# Mobile app endpoints — version check, device registration, runtime config
api_router.include_router(mobile_router)
api_router.include_router(admin_mobile_router)
# Secrets Vault (encrypted credentials store) + System Health monitoring
api_router.include_router(secrets_vault_router)
api_router.include_router(system_health_router)
# Iteration 42 — Yeni rapor seti (stok değer, hızlı/yavaş satan, iade oranı, kanal kâr)
api_router.include_router(reports_v2_router)
api_router.include_router(product_costs_router)
# Iteration 43 — production hooks + size recommender + IYS
api_router.include_router(production_hooks_router)
api_router.include_router(size_rec_router)
api_router.include_router(iys_router)

# Theme management (admin) + storefront theme reader (public)
from routes.themes import admin_router as themes_admin_router, public_router as themes_public_router
api_router.include_router(themes_admin_router)
api_router.include_router(themes_public_router)

# Influencer CRM & Seeding & ROI (Modül 3 + 4)
from routes.influencers import router as influencers_router
api_router.include_router(influencers_router)

# Amazon Selling Partner API (SP-API) — LWA only, no SigV4
from routes.amazon_spapi import router as amazon_spapi_router
api_router.include_router(amazon_spapi_router)

# Amazon DPP / Compliance (PII retention + checklist)
from routes.compliance import router as compliance_router
api_router.include_router(compliance_router)

# TOTP MFA (çok faktörlü doğrulama) — Amazon DPP uyumu
from routes.mfa import router as mfa_router
api_router.include_router(mfa_router)
# Cloudflare Email Routing — gelen mail webhook'u
from routes.inbound_mail import router as inbound_mail_router
api_router.include_router(inbound_mail_router)

# Root endpoint
@api_router.get("/")
async def root():
    return {
        "message": "Facette E-Commerce API",
        "version": "3.0",
        "status": "running"
    }

# Health check
@api_router.get("/health")
async def health():
    return {"status": "healthy"}

# Include API router
app.include_router(api_router)

# SEO: /sitemap.xml ve /robots.txt — KÖK seviye (api_router'a DEĞİL, /api prefix'siz)
app.include_router(seo_router)

# Static files (if needed)
static_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
