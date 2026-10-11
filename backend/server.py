"""
Meluni E-Commerce API - Main Server
Modular architecture with routes
"""
from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pymongo.errors import DuplicateKeyError
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


def _luhn_pan(d: str) -> bool:
    if len(d) not in (15, 16):
        return False
    tot = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        tot += n
    return tot % 10 == 0


class _SecretRedactFilter(logging.Filter):
    """GÜVENLİK (BL-019): log satırlarında JWT/Bearer, parola/anahtar parametreleri ve kart
    numarası benzeri dizileri maskeler. Log altyapısına sır/kart verisi düşmesin.
    KVKK: TR telefon numaraları ve adres benzeri alanlar/kalıplar da maskelenir ([TEL] / [ADRES])."""
    import re as _re
    _PATS = (
        (_re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[JWT]"),
        (_re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1[REDACTED]"),
        (_re.compile(r"(?i)((?:password|passwd|secret|api_?key|token|apisecret)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]{4,}"), r"\1[REDACTED]"),
        (_re.compile(r"(?<!\d)(?:\d[ -]?){12,15}\d(?!\d)"), lambda m: "[PAN]" if _luhn_pan("".join(c for c in m.group(0) if c.isdigit())) else m.group(0)),
        # KVKK (sahip talebi): müşteri/influencer TELEFONU ve ADRESİ log altyapısına düşmez.
        #   anahtar=değer biçimi (phone=…, "address": "…", gsm: …, ?phone=… sorgu dizesi)
        (_re.compile(r"(?i)((?:phone|telefon|gsm|mobile|tel_cep|cep_tel|msisdn)[a-z_]*[\"']?\s*[:=]\s*[\"']?)"
                     r"\+?[\d][\d\s().\-]{6,}\d"), r"\1[TEL]"),
        (_re.compile(r"(?i)((?:address|adres|street|acik_adres|full_?address|address_?line\d?)[\"']?\s*[:=]\s*[\"']?)"
                     r"(?![{\[\s])[^\"'&}\n]{3,}"), r"\1[ADRES]"),
        #   serbest metin: TR telefon biçimleri + "… Mah./Sok./Cad. …" adres kalıbı
        (__import__("security.contact_pii", fromlist=["x"]).ADDRESS_TEXT_RE, "[ADRES]"),
        (__import__("security.contact_pii", fromlist=["x"]).PHONE_TEXT_RE, "[TEL]"),
    )

    @classmethod
    def _redact(cls, s: str) -> str:
        for rx, rep in cls._PATS:
            s = rx.sub(rep, s)
        return s

    def filter(self, record):
        try:
            msg = record.getMessage()
            red = self._redact(msg)
            if red != msg:
                # Biçim yapısını (msg + args) mümkünse KORU: uvicorn AccessFormatter record.args'ı
                # (client, method, path, …) açar; args=() yapılırsa erişim satırı hiç yazılamaz.
                # Argüman-bazlı maskeleme tam-mesaj maskelemesiyle AYNI sonucu vermiyorsa düzleştir.
                args, kept = record.args, False
                if isinstance(record.msg, str) and isinstance(args, (tuple, dict)) and args:
                    try:
                        if isinstance(args, tuple):
                            new_args = tuple(self._redact(a) if isinstance(a, str) else a for a in args)
                        else:
                            new_args = {k: (self._redact(v) if isinstance(v, str) else v) for k, v in args.items()}
                        new_msg = self._redact(record.msg)
                        if (new_msg % new_args) == red:
                            record.msg, record.args, kept = new_msg, new_args, True
                    except Exception:
                        kept = False
                if not kept:
                    record.msg, record.args = red, ()
        except Exception:
            pass
        return True


def _install_secret_redaction() -> None:
    """Filtreyi kök logger'ın VE uvicorn'un kendi handler'larının (uvicorn / uvicorn.error /
    uvicorn.access — propagate=False, kök handler'a ulaşmaz) hepsine bir kez ekler. Üretimde
    `python -m uvicorn` logging'i uygulama import edilmeden ÖNCE kurar; erişim satırındaki
    sorgu dizesi token'ları (?token=…) ve hata satırlarındaki Bearer değerleri böylece maskelenir."""
    for _name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn.error", "gunicorn.access"):
        for _h in logging.getLogger(_name).handlers:
            if not any(isinstance(f, _SecretRedactFilter) for f in _h.filters):
                _h.addFilter(_SecretRedactFilter())


_install_secret_redaction()
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
from routes.supplier_xml import router as supplier_xml_router
from routes.ai_product_images import router as ai_product_images_router
from routes.catalog_readiness import router as catalog_readiness_router
from routes.trendyol_stock_guard import router as trendyol_stock_guard_router
from routes.dropship import router as dropship_router
from routes.ai_chatbot import router as ai_chatbot_router
from routes.whatsapp_webhook import router as whatsapp_webhook_router
from routes.meta_messaging_webhook import router as meta_messaging_webhook_router
from routes.ai_assistant import router as ai_assistant_router
from routes.locations import router as locations_router
from routes.attribution import router as attribution_router
from routes.members import router as members_router
from routes.consents import router as consents_router
from routes.coupons import admin_router as coupons_admin_router, public_router as coupons_public_router, campaigns_router as campaigns_router
from routes.reports import router as reports_router
from routes.report_assistant import router as report_assistant_router
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
# [ticimax-off] from routes.ticimax_history import router as ticimax_history_router  # geçmiş veri kurtarma — kullanıcı kararı: Ticimax TAMAMEN kapalı
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
    logger.info("Starting Meluni E-Commerce API...")
    
    try:
        # Create admin user if not exists.
        # A2: `--workers N` ile calisirken her worker acilista bu blogu ayri
        # ayri calistiriyor. Ikisi de "admin yok" gorup FARKLI rastgele
        # parolalarla kayit ekliyordu; parola dosyasina biri yaziliyor,
        # giriste digeri donuyor ve HICBIR parola calismiyordu (email uzerindeki
        # tekil indeks de bu yuzden E11000 ile kurulamiyordu).
        # Cozum: once tekil indeksi kur, sonra insert'i yarisa dayanikli yap.
        try:
            await db.users.create_index("email", unique=True)
        except Exception:
            pass
        _admin_email = (os.environ.get("ADMIN_EMAIL") or "admin@meluni.com.tr").strip().lower()
        admin = await db.users.find_one({"email": _admin_email})
        if not admin:
            from routes.deps import hash_password, generate_id
            from datetime import datetime, timezone
            # Bootstrap parolası diske veya loga yazılmaz. Yeni ortamda yönetici
            # oluşturmak için güçlü parola deployment secret'ı olarak verilmelidir.
            _admin_pw = (os.environ.get("ADMIN_INITIAL_PASSWORD") or "").strip()
            if len(_admin_pw) < 12:
                logger.error("Admin bootstrap atlandı: ADMIN_INITIAL_PASSWORD en az 12 karakter olmalı")
            else:
                await db.users.insert_one({
                    "id": generate_id(),
                    "email": _admin_email,
                    "password": hash_password(_admin_pw),
                    "first_name": "Admin",
                    "last_name": "User",
                    "is_admin": True,
                    "is_super_admin": True,
                    "is_active": True,
                    "must_change_password": True,
                    "created_at": datetime.now(timezone.utc).isoformat()
                })
                logger.info("Admin olusturuldu — ADMIN_INITIAL_PASSWORD kullanildi.")
        # Never elevate an existing account solely because its email matches
        # the bootstrap address. Existing owners retain their explicit flag/role.

        # KURTARMA KOLU (kilitlenme önlemi): 7e50926 ile rolsüz hesaplar ve is_super_admin
        # bayrağı olmayan sahip hesabı yönetim API'sine giremez (403) ve panelden rol/bayrak
        # atayamaz (tavuk-yumurta). DB erişimi olmadan güvenli çıkış: yalnız deployment
        # secret'ı OWNER_SUPER_ADMIN_EMAILS (virgülle ayrılmış) ile listelenen MEVCUT hesaplar
        # is_super_admin=True yapılır. Env boşsa hiçbir şey yapılmaz; e-posta ile örtük yükseltme YOK.
        try:
            _owners = [e.strip().lower() for e in (os.environ.get("OWNER_SUPER_ADMIN_EMAILS") or "").split(",") if e.strip()]
            for _oe in _owners:
                _res = await db.users.update_one(
                    {"email": _oe, "is_super_admin": {"$ne": True}},
                    {"$set": {"is_super_admin": True, "is_admin": True, "is_active": True}})
                if _res.modified_count:
                    logger.warning(f"[guvenlik] OWNER_SUPER_ADMIN_EMAILS: {_oe} is_super_admin=True yapildi")
            _n_super = await db.users.count_documents({"is_super_admin": True, "is_active": {"$ne": False}})
            if _n_super == 0:
                logger.error("[guvenlik] SİSTEMDE AKTİF SÜPER-ADMİN YOK — panel yönetimi kilitli olabilir. "
                             "Railway'de OWNER_SUPER_ADMIN_EMAILS=<sahip e-postası> tanımlayıp yeniden başlatın.")
        except Exception as _oe_err:
            logger.error(f"[guvenlik] owner bootstrap hatasi: {_oe_err}")

        # GÜVENLİK: acil kapatma (break-glass) env'leri açıksa (ADMIN_MFA_ENFORCE=off,
        # ADMIN_MFA_CHANNEL_POLICY=off, TWO_PERSON_RULE=off, ALLOW_DIRECT_STAFF_CREATE=1,
        # STAFF_IDLE_TIMEOUT=off) her açılışta BİR KEZ süper adminlere uyarı (arka planda).
        try:
            from security.events import alert_break_glass_on_boot, fire_and_forget
            fire_and_forget(alert_break_glass_on_boot())
        except Exception as _bg_err:
            logger.error(f"[guvenlik] break-glass uyarisi hatasi: {_bg_err}")

        # ADM-07: görsel dönüştürme çocuk süreçleri önceden başlatılır (ilk yüklemede spawn
        # gecikmesi olmasın). Arka planda; hata açılışı etkilemez.
        try:
            from image_worker import prewarm as _img_prewarm
            _img_prewarm()
        except Exception as _iw_err:
            logger.warning(f"[image-worker] ön ısıtma atlandı: {_iw_err}")

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

        # IAM-02 (en az yetki): önceden her personele açık uçlar artık sayfa izni istiyor. Mevcut
        # rollere, gördükleri sayfaların yeni/uyuyan yazma izinleri TEK SEFER eklenir (yalnız ekler,
        # süper admine dokunmaz, bayrakla bir kez) → kimse bugünkü erişimini kaybetmez. İstek
        # sunulmadan ÖNCE (eşzamanlı) çalışır: deploy ile geçiş arasında 403 penceresi olmaz.
        try:
            from routes.admin_rbac import grant_least_privilege_upgrade_permissions
            _lp = await grant_least_privilege_upgrade_permissions()
            if not _lp.get("skipped"):
                logger.warning(f"[guvenlik] IAM-02 rol izin gecisi: {len(_lp.get('roles') or [])} rol guncellendi")
        except Exception as _lpe:
            logger.error(f"[guvenlik] IAM-02 rol izin gecisi hatasi: {_lpe}")

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

        # ÖZELLİK VARSAYILANLARI: koddaki MELUNI sabit varsayılanlarını (Cinsiyet=Kadın,
        # Menşei=Türkiye, Yaş Grubu=Yetişkin, Ek Özellik=Yok …) attributes.default_value alanına
        # İDEMPOTENT tohumla → Özellik Ayar Kartı'nda görünür/düzenlenebilir. DB OTORİTEDİR:
        # yalnız alan HİÇ yoksa yazar, kullanıcı silince ("") yeniden dayatmaz.
        try:
            from routes.attributes import seed_attribute_defaults
            _sd = await seed_attribute_defaults()
            if _sd.get("created") or _sd.get("seeded"):
                logger.info(f"[ozellik-seed] {_sd.get('created', 0)} yeni ozellik olusturuldu, "
                            f"{_sd.get('seeded', 0)} varsayilan deger tohumlandi.")
        except Exception as _se:
            logger.error(f"[ozellik-seed] hata: {_se}")

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
        # Müşteri risk skoru (sipariş listesi) e-posta eşleşmesi — eskiden indekssiz tarama.
        for _ix in (("orders", "shipping_address.email"), ("orders", "email"),
                    ("customer_returns", "user_id"), ("customer_returns", "customer_email")):
            try:
                await db[_ix[0]].create_index(_ix[1])
            except Exception as _ixe:
                logger.warning(f"index {_ix}: {_ixe}")
        # Performans: sipariş listesi tarih/durum/platform filtreleri için
        await db.orders.create_index([("created_at", -1)])
        await db.orders.create_index([("status", 1), ("created_at", -1)])
        await db.orders.create_index([("platform", 1), ("created_at", -1)])
        await db.orders.create_index([("payment_status", 1), ("created_at", -1)])
        # RAPOR PERFORMANSI: her satış raporunun ana filtresi effective_order_date_match ->
        #   {$or: [ {marketplace_order_date: {$gte,$lte}},
        #           {marketplace_order_date: {$in:[None,""]}, created_at: {$gte,$lte}} ]}
        # marketplace_order_date HİÇ indeksli değildi → her rapor açılışı TÜM orders
        # koleksiyonunu tarıyordu (raporlar sayfası geç açılıyordu). Bu bileşik indeks
        # $or'un İKİ dalını da karşılar: 1. dal için ön-ek (aralık), 2. dal için eşitlik
        # (None/"") + created_at aralığı.
        await db.orders.create_index([("marketplace_order_date", 1), ("created_at", -1)])
        # DÖNEM MUHASEBESİ: iptaller iptal tarihine göre sayılır (cancelled_at ?? updated_at).
        await db.orders.create_index([("cancelled_at", -1)])
        # KISMİ İPTAL taraması: partial_cancel_amount indekssizdi → her rapor yüklemesinde
        # TÜM sipariş koleksiyonu taranıyordu (ay değiştirmenin yavaşlığının ana sebebi).
        # Kısmi iptal nadir olduğu için partial (sparse) indeks: koleksiyonun küçük bir
        # kısmını tutar, yazma maliyeti yok denecek kadar azdır.
        await db.orders.create_index(
            [("partial_cancel_at", -1)],
            partialFilterExpression={"partial_cancel_amount": {"$gt": 0}})
        await db.orders.create_index(
            [("updated_at", -1)],
            partialFilterExpression={"partial_cancel_amount": {"$gt": 0}},
            name="partial_cancel_updated_at")
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
        # DÖNEM MUHASEBESİ (raporlar): iadeler gider pusulasının KESİLDİĞİ tarihe göre
        # sayılıyor; bu sorgu indekssizdi → her rapor yüklemesinde tam tarama.
        await db.gider_pusulasi.create_index([("created_at", -1)])
        await db.gider_pusulasi.create_index("claim_id")
        await db.gider_pusulasi.create_index("return_id")
        await db.gider_pusulasi.create_index("order_number")
        await db.gider_pusulasi.create_index([("number", -1)])
        await db.order_events.create_index([("order_id", 1), ("created_at", -1)])
        await db.order_events.create_index([("created_at", -1)])
        await db.orders_deleted.create_index([("deleted_at", -1)])
        # DENETİM (DB-bütünlük): sıcak sorgular COLLSCAN'liyordu → indeks. unique DEĞİL (mevcut
        # veride olası duplikeler boot'u kırmasın; benzersizlik ayrı dedup migration ister).
        await db.orders.create_index("id")                       # ödeme callback {"id": order_id}
        await db.orders.create_index("iyzico_payment_id")        # cross-order paymentId replay kontrolü
        # DENETİM (race R1): idempotency_key partial-UNIQUE — eşzamanlı çift POST /orders'ı DB
        # seviyesinde reddeder (çift sipariş). Ayrı try: mevcut duplike varsa DİĞER indeksleri kırmasın.
        try:
            await db.orders.create_index(
                "idempotency_key", unique=True,
                partialFilterExpression={"idempotency_key": {"$type": "string"}})
        except Exception as _ie:
            logger.warning(f"idempotency_key unique index atlandı (mevcut duplike olabilir): {_ie}")
        await db.stock_movements.create_index([("order_id", 1), ("type", 1)])   # iptal/restock idempotency
        await db.stock_movements.create_index([("product_id", 1), ("created_at", -1)])
        # Per-SKU defter toplamı (stok izi / mutabakat raporu): deltalar items[]/moves[]
        # içinde durur; bu indeksler olmadan her ürün için koleksiyon taraması gerekir.
        await db.stock_movements.create_index([("items.product_id", 1), ("created_at", -1)])
        await db.stock_movements.create_index([("moves.product_id", 1), ("created_at", -1)])
        await db.stock_movements.create_index([("items.barcode", 1)])
        await db.stock_movements.create_index([("moves.barcode", 1)])
        await db.coupons.create_index("code")                    # checkout apply_coupon (public)
        await db.gift_cards.create_index("code")
        await db.products.create_index("barcode")                # barkod→ürün çözümü
        await db.products.create_index("variants.barcode")
        await db.cargo_logs.create_index([("created_at", -1)])
        await db.attribution_sessions.create_index("session_id")
        try:
            await db.users.create_index("id")  # her kimlikli istekte users.find_one({"id"}) — COLLSCAN'i önler
        except Exception as _uie:
            logger.warning(f"users.id index: {_uie}")
        await db.users.create_index("email", unique=True)
        # Üye listesi/segment hızı: is_admin+created_at (sıralama), cached_segment (segment filtresi),
        # cached_spent (VIP/harcama sıralaması). Sipariş istatistiği artık users.cached_* önbelleğinden.
        await db.users.create_index([("is_admin", 1), ("created_at", -1)])
        await db.users.create_index([("cached_segment", 1)])
        await db.users.create_index([("cached_spent", -1)])
        # Security audit indexes — fast forensic queries as the collection grows
        await db.auth_audit_logs.create_index([("created_at", -1)])
        await db.auth_audit_logs.create_index([("event", 1), ("email", 1), ("created_at", -1)])
        await db.auth_audit_logs.create_index([("ip", 1), ("created_at", -1)])
        await db.auth_audit_logs.create_index([("success", 1), ("created_at", -1)])
        await db.audit_logs.create_index([("created_at", -1)])
        await db.audit_logs.create_index([("actor.email", 1), ("created_at", -1)])
        await db.audit_logs.create_index([("action", 1), ("source", 1), ("created_at", -1)])
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

    try:
        from routes.coupons import ensure_coupon_redemption_unique_index
        await ensure_coupon_redemption_unique_index()
    except Exception as e:
        logger.warning("Coupon redemption index: %s", type(e).__name__)

    # Start background scheduler (auto-cancel 48h unpaid havale orders)
    try:
        from scheduler import start_scheduler
        start_scheduler()
        # Olay döngüsü kilitlenme izleyicisi (teşhis: destek_q=blokaj)
        try:
            from security.monitoring import loop_lag_monitor as _llm
            import asyncio as _aio_llm
            _aio_llm.create_task(_llm())
        except Exception as _llm_e:
            logger.warning(f"loop lag monitor başlatılamadı: {_llm_e}")
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

    # Tek seferlik onarım: eski İ-bug'ından bozuk kategori slug'ları (gi-yi-m → giyim)
    # düzeltilir; eski slug alias+301 ile korunur (URL kırılmaz, SEO düzelir).
    try:
        import asyncio as _asyncio
        from routes.categories import repair_category_slugs
        _asyncio.create_task(repair_category_slugs())
    except Exception as e:
        logger.warning(f"Category slug repair start warning: {e}")

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
    title="Meluni E-Commerce API",
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
# ADM-15: asgari native uygulama sürümü (İşletme Kuralları: app.min_version_admin/_customer).
# CORS'tan ÖNCE eklenir → CORS'un İÇİNDE çalışır; 426 yanıtı da CORS başlıklarını alır.
from security.app_version_policy import AppVersionPolicyMiddleware  # noqa: E402
app.add_middleware(AppVersionPolicyMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins_list,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # X-App-*: yalnız native uygulama gönderir (sürüm politikası, ADM-15).
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With",
                   "X-App-Version", "X-App-Platform", "X-App-Target",
                   # Denetim #4/#27: sipariş anahtarı + misafir sipariş doğrulaması (vitrin).
                   "X-Order-Token", "X-Order-Contact", "X-Pay-Token"],
    # X-Auth-Code: oturum iptal kodu (session_revoked → cihaz önbelleği silinir), ADM-15.
    expose_headers=["Content-Disposition", "X-Auth-Code", "X-App-Update-Required"],
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
        # DENETİM (auth-redteam #6): kimlik/PII yanıtlarına no-store — ara katman/tarayıcı
        # önbelleğe almasın (token/PII sızıntısı). Public katalog uçları hariç.
        _SENSITIVE = ("/api/auth", "/api/users", "/api/admin", "/api/my-orders",
                      "/api/orders", "/api/customer", "/api/members", "/api/iys",
                      "/api/settings", "/api/referrals", "/api/loyalty", "/api/gift",
                      "/api/my-addresses", "/api/addresses", "/api/favorites", "/api/track")
        if any(path.startswith(p) for p in _SENSITIVE):
            response.headers.setdefault("Cache-Control", "no-store, private")
        return response


app.add_middleware(SecurityHeadersMiddleware)


class BodySizeLimitMiddleware:
    """DoS koruması: çok büyük istek gövdelerini 413 ile reddeder (bellek tükenmesi).

    GÜVENLİK (denetim 2026-10 #2, INFRA-03): eski sürüm yalnız Content-Length'e bakıyordu ve
    herkese 120 MB tanıyordu. Cloudflare 100 MB'a kadar gövde geçirir → kimliksiz biri herkese açık
    bir JSON ucuna (/api/orders, /api/products/cart-pricing…) dev JSON yollayıp ayrıştırma ile
    belleği tüketebiliyordu; Content-Length'siz (chunked) gövde tavanı hiç görmüyordu.
    Kademeli tavan:
      * multipart/form-data (dosya/video yükleme — Starlette diske taşır) → MAX_BYTES (120 MB),
      * geçerli İMZALI personel JWT'si (typ=staff) → MAX_BYTES (toplu içe aktarma vb. korunur),
      * webhook uçları (sunucudan sunucuya; gelen e-posta ekleri) → WEBHOOK_MAX_BYTES,
      * diğer her şey (misafir / müşteri JSON-form gövdesi) → PUBLIC_MAX_BYTES.
    Gövde AKIŞ sırasında da sayılır (chunked) → tavan aşılınca okuma kesilir, 413 döner.
    Saf ASGI: yanıt akışına (StreamingResponse) dokunmaz."""
    MAX_BYTES = 120 * 1024 * 1024
    WEBHOOK_MAX_BYTES = 16 * 1024 * 1024
    PUBLIC_MAX_BYTES = 1 * 1024 * 1024

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _is_webhook_path(path: str) -> bool:
        p = (path or "").lower()
        return "webhook" in p or p.startswith("/api/inbound-mail")

    @staticmethod
    def _is_staff_bearer(auth: str) -> bool:
        if not auth or not auth[:7].lower() == "bearer ":
            return False
        try:
            from routes.deps import _decode_jwt_strict
            payload = _decode_jwt_strict(auth[7:].strip())
        except Exception:
            return False
        return payload.get("typ") == "staff" or (payload.get("typ") is None and bool(payload.get("is_admin")))

    def _limit_for(self, scope) -> int:
        cap = int(self.MAX_BYTES)
        hdrs = {}
        for k, v in scope.get("headers") or []:
            hdrs[k.decode("latin-1").lower()] = v.decode("latin-1")
        ctype = (hdrs.get("content-type") or "").lower()
        _staff = self._is_staff_bearer(hdrs.get("authorization") or "")
        if ctype.startswith("multipart/form-data"):
            # Denetim #12: kimliksiz/müşteri multipart 16 MB; personelde uç bazında tavan.
            from security.uploads import multipart_cap_for
            return min(cap, multipart_cap_for(scope.get("path") or "", _staff))
        if _staff:
            return cap
        if self._is_webhook_path(scope.get("path") or ""):
            return min(cap, int(self.WEBHOOK_MAX_BYTES))
        return min(cap, int(self.PUBLIC_MAX_BYTES))

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse as _JR
        _cl = None
        for k, v in scope.get("headers") or []:
            if k.lower() == b"content-length":
                _cl = v
                break
        # Tavan (JWT çözümü içerir) yalnız gövde varsa ve bir kez hesaplanır.
        _lim = {"v": None}

        def _limit() -> int:
            if _lim["v"] is None:
                _lim["v"] = self._limit_for(scope)
            return _lim["v"]

        if _cl is not None and _cl.strip() not in (b"", b"0"):
            try:
                if int(_cl) > _limit():
                    return await _JR(status_code=413, content={"detail": "İstek gövdesi çok büyük"})(scope, receive, send)
            except (ValueError, TypeError):
                pass
        # Tavan aşılınca okuma KESİLİR: uygulamaya "istemci koptu" (http.disconnect) verilir —
        # iç BaseHTTPMiddleware katmanları receive'den yükselen istisnayı ExceptionGroup'a sardığı
        # için istisna yerine kopma sinyali kullanılır. Uygulamanın o noktada yazdığı yanıt
        # (400/500) _send içinde TEK bir 413 yanıtıyla değiştirilir.
        state = {"received": 0, "started": False, "overflow": False, "sent413": False}
        import json as _json
        _body413 = _json.dumps({"detail": "İstek gövdesi çok büyük"}, ensure_ascii=False).encode("utf-8")

        async def _recv():
            if state["overflow"]:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg.get("type") == "http.request" and msg.get("body"):
                state["received"] += len(msg.get("body") or b"")
                if state["received"] > _limit():
                    state["overflow"] = True
                    return {"type": "http.disconnect"}
            return msg

        async def _send_413():
            if state["sent413"] or state["started"]:
                return
            state["sent413"] = True
            state["started"] = True
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(_body413)).encode())]})
            await send({"type": "http.response.body", "body": _body413, "more_body": False})

        async def _send(msg):
            if state["overflow"]:
                # Uygulamanın yanıtı yerine 413 (yalnız bir kez); geri kalan parçalar yutulur.
                if msg.get("type") == "http.response.start":
                    await _send_413()
                return
            if msg.get("type") == "http.response.start":
                state["started"] = True
            await send(msg)

        # Denetim #12: kimliksiz multipart gövdeleri için eşzamanlılık tavanı (bekletmeden 429).
        _sem = None
        _ct = b""
        _auth = b""
        for k, v in scope.get("headers") or []:
            kl = k.lower()
            if kl == b"content-type":
                _ct = v.lower()
            elif kl == b"authorization":
                _auth = v
        if _ct.startswith(b"multipart/form-data") and not self._is_staff_bearer(_auth.decode("latin-1")):
            from security.uploads import public_multipart_semaphore
            _sem = public_multipart_semaphore()
            if _sem.locked():
                return await _JR(status_code=429, content={"detail": "Şu anda çok fazla yükleme var, lütfen biraz sonra tekrar deneyin."})(scope, receive, send)
            await _sem.acquire()
        try:
            await self.app(scope, _recv, _send)
        except Exception:
            # Ara katmanlardan kaçan tavan aşımı (ör. "No response returned") → 413 yaz.
            if state["overflow"]:
                await _send_413()
                return
            raise
        finally:
            if _sem is not None:
                _sem.release()
        if state["overflow"]:
            await _send_413()


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

# ---------------------------------------------------------------------------
# AKAN DIŞA AKTARIM KORUMASI (CHAIN09) — en dışta (sunucuya en yakın) olsun ki gövde parçaları
# arasındaki yeniden denetim gerçek gönderim hızını izlesin. Yalnız freeze_exports kapısından geçmiş
# (register_export ile işaretlenmiş) yanıtlara dokunur; diğer her istek aynen geçer.
# ---------------------------------------------------------------------------
from security.export_guard import ExportGuardMiddleware
app.add_middleware(ExportGuardMiddleware)

# Denetim #11: vitrin "üye mi?" kararı için Bearer oturumunu (aktiflik + token_version + security
# epoch) doğrulayıp istek durumuna yazar (routes.deps.MemberPrincipalMiddleware).
from routes.deps import MemberPrincipalMiddleware
app.add_middleware(MemberPrincipalMiddleware)

# Main API Router
api_router = APIRouter(prefix="/api")

from routes.admin_activity import router as admin_activity_router
api_router.include_router(admin_activity_router)

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
from routes.chargebacks import router as chargebacks_router  # noqa: E402 (ödeme itirazları)
api_router.include_router(chargebacks_router)
from routes.kvkk import router as kvkk_router, admin_router as kvkk_admin_router  # noqa: E402
from security_switches import router as security_switches_router  # noqa: E402
api_router.include_router(kvkk_router)
api_router.include_router(kvkk_admin_router)
api_router.include_router(security_switches_router)
from security.selftest import router as security_selftest_router  # noqa: E402
api_router.include_router(security_selftest_router)
# COM12: iyzico komisyon/hesaba-geçiş defteri + mutabakat uyuşmazlıkları (yalnız süper admin).
from security.payment_ledger import router as payment_ledger_router  # noqa: E402
api_router.include_router(payment_ledger_router)
# T19 / ADM-08: vitrin bütünlük izleyicisi durumu + karantina (yalnız süper admin).
from security.storefront_integrity import router as storefront_integrity_router  # noqa: E402
api_router.include_router(storefront_integrity_router)
# SOC-01: güvenlik uyarısı onayı (panel: süper admin; e-posta: imzalı tek kullanımlık bağlantı) + tatbikat.
from security.alert_ack import router as alert_ack_router, public_router as alert_ack_public_router  # noqa: E402
api_router.include_router(alert_ack_router)
api_router.include_router(alert_ack_public_router)

# Include all route modules
api_router.include_router(auth_router)
api_router.include_router(products_router)
api_router.include_router(orders_router)
api_router.include_router(categories_router)
api_router.include_router(supplier_xml_router)
api_router.include_router(ai_product_images_router)
api_router.include_router(catalog_readiness_router)
api_router.include_router(trendyol_stock_guard_router)
api_router.include_router(dropship_router)
api_router.include_router(banners_router)
api_router.include_router(cms_router)
from routes.full_look import router as full_look_router
api_router.include_router(full_look_router)
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
# Amazon entegrasyon uçları (amazon-tr/products/*) — catch-all /{marketplace}'den ÖNCE
from routes.integrations_amazon import router as integrations_amazon_router
api_router.include_router(integrations_amazon_router, prefix="/integrations")
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
api_router.include_router(consents_router)
api_router.include_router(coupons_admin_router)
api_router.include_router(coupons_public_router)
api_router.include_router(campaigns_router)
from routes.sale_menu import public_router as sale_menu_public_router, admin_router as sale_menu_admin_router
api_router.include_router(sale_menu_public_router)
api_router.include_router(sale_menu_admin_router)
api_router.include_router(reports_router)
api_router.include_router(report_assistant_router)   # Raporlar "Bana Sor" asistanı
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
# [ticimax-off] api_router.include_router(ticimax_history_router)  # kullanıcı kararı: Ticimax'a ait HİÇBİR uç açık değil
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
from routes.amazon_catalog import router as amazon_catalog_router
api_router.include_router(amazon_catalog_router)
from routes.amazon_autosetup import router as amazon_autosetup_router
api_router.include_router(amazon_autosetup_router)
from routes.amazon_claims import router as amazon_claims_router
api_router.include_router(amazon_claims_router, prefix="/integrations")

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
        "message": "Meluni E-Commerce API",
        "version": "3.0",
        "status": "running"
    }

# Health check
async def zero_deleted_on_marketplaces_once() -> None:
    """Çöp kutusundaki (is_deleted) TÜM ürünler için pazaryerlerine 0 stok gönderir (tek seferlik, bayraklı).
    Hedefli stok güncellemesi çöpteki ürünleri de yakalayıp Trendyol'a stok geri göndermişti."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    import asyncio as _aio
    flag_id = "migrations.deleted_zero_push_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        from routes.integrations_common import on_product_deactivated
        n = 0
        async for p in _db.products.find({"is_deleted": True, "variants.barcode": {"$exists": True}}, {"_id": 0}).limit(400):
            try:
                await on_product_deactivated(dict(p))
                n += 1
            except Exception as _pe:
                logger.warning(f"[deleted-zero] {p.get('name')}: {_pe}")
            await _aio.sleep(0.3)
        await _db.settings.update_one({"id": flag_id}, {"$set": {"id": flag_id, "done": True, "pushed": n,
                                                                 "at": _dt.now(_tz.utc).isoformat()}}, upsert=True)
        logger.warning(f"[deleted-zero] pazaryerlerine 0 stok gönderilen çöp ürün: {n}")
    except Exception as e:
        logger.error(f"[deleted-zero] hata: {e}")


async def stock_audit_diag(name_patterns=("Limited Edition Ku", "Limited Edition Pensli")) -> None:
    """TEŞHİS: (1) birden fazla üründe (aktif/çöp) ortak barkodlar, (2) adı verilen ürünlerin
    güncel varyant stokları + son 21 günün stok hareketleri (tip/kaynak/sipariş/zaman).
    Sonuç settings.stock_audit_diag → /health.stock_audit (kişisel veri yok)."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import re as _re
    out = {"at": _dt.now(_tz.utc).isoformat()}
    try:
        # (1) mükerrer barkodlar
        pipeline = [
            {"$match": {"variants.barcode": {"$exists": True}}},
            {"$project": {"id": 1, "name": 1, "is_deleted": 1, "is_active": 1, "variants.barcode": 1, "variants.stock": 1, "variants.size": 1}},
            {"$unwind": "$variants"},
            {"$match": {"variants.barcode": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$variants.barcode", "n": {"$sum": 1},
                        "prods": {"$push": {"id": "$id", "name": "$name", "deleted": "$is_deleted", "active": "$is_active",
                                            "stock": "$variants.stock", "size": "$variants.size"}}}},
            {"$match": {"n": {"$gt": 1}}},
            {"$limit": 200},
        ]
        dups = await _db.products.aggregate(pipeline).to_list(200)
        out["duplicate_barcodes_total"] = len(dups)
        out["duplicate_with_deleted"] = sum(1 for d in dups if any(p.get("deleted") for p in d["prods"]) and any(not p.get("deleted") for p in d["prods"]))
        out["duplicate_samples"] = [{"barcode": d["_id"], "products": [{"name": (p.get("name") or "")[:50], "deleted": bool(p.get("deleted")),
                                                                        "active": bool(p.get("active")), "stock": p.get("stock"), "size": p.get("size")}
                                                                       for p in d["prods"][:4]]} for d in dups[:25]]
        # (2) adı geçen ürünler
        rx = "|".join(_re.escape(x) for x in name_patterns)
        prods = await _db.products.find({"name": {"$regex": rx, "$options": "i"}},
                                        {"_id": 0, "id": 1, "name": 1, "is_deleted": 1, "is_active": 1, "updated_at": 1, "variants.barcode": 1, "variants.size": 1, "variants.stock": 1}).to_list(20)
        since = (_dt.now(_tz.utc) - _td(days=21)).isoformat()
        report = []
        for p in prods:
            bcs = [str(v.get("barcode") or "") for v in (p.get("variants") or []) if v.get("barcode")]
            moves = []
            if bcs:
                q = {"created_at": {"$gte": since}, "$or": [{"items.barcode": {"$in": bcs}}, {"moves.barcode": {"$in": bcs}}, {"product_id": p["id"]}]}
                async for m in _db.stock_movements.find(q, {"_id": 0, "type": 1, "source": 1, "order_number": 1, "created_at": 1, "created_by": 1,
                                                            "items": 1, "moves": 1, "reason": 1, "note": 1}).sort("created_at", -1).limit(40):
                    rows = [x for x in ((m.get("items") or []) + (m.get("moves") or [])) if isinstance(x, dict) and str(x.get("barcode") or "") in bcs]
                    moves.append({"at": str(m.get("created_at") or "")[:19], "type": m.get("type"), "source": m.get("source"),
                                  "order": m.get("order_number"), "by": (m.get("created_by") or "")[:3] + "…" if m.get("created_by") else "",
                                  "rows": [{"b": str(x.get("barcode"))[-4:], "d": x.get("delta", x.get("qty", x.get("applied")))} for x in rows][:8]})
            report.append({"name": p.get("name"), "deleted": bool(p.get("is_deleted")), "active": bool(p.get("is_active")),
                           "updated_at": str(p.get("updated_at") or "")[:19],
                           "variants": [{"size": v.get("size"), "stock": v.get("stock"), "b": str(v.get("barcode") or "")[-4:]} for v in (p.get("variants") or [])],
                           "movements": moves})
        out["products"] = report
        # (2b) ÇÖP KUTUSUNDAKİ ürünlere gelen pazaryeri siparişleri (son 14 gün) + çöpte stoğu >0 kalan ürünler
        try:
            _since14 = (_dt.now(_tz.utc) - _td(days=14)).isoformat()
            _del = await _db.products.find({"is_deleted": True, "variants.barcode": {"$exists": True}},
                                           {"_id": 0, "id": 1, "name": 1, "deleted_at": 1, "variants.barcode": 1, "variants.stock": 1}).to_list(3000)
            _bc2p = {}
            _del_with_stock = []
            for p in _del:
                st_sum = 0
                for v in (p.get("variants") or []):
                    if v.get("barcode"):
                        _bc2p[str(v["barcode"])] = p
                    st_sum += int(v.get("stock") or 0)
                if st_sum > 0:
                    _del_with_stock.append({"name": (p.get("name") or "")[:50], "stock": st_sum, "deleted_at": str(p.get("deleted_at") or "")[:10]})
            out["deleted_with_local_stock"] = len(_del_with_stock)
            out["deleted_with_local_stock_samples"] = _del_with_stock[:15]
            _hits = []
            if _bc2p:
                async for o in _db.orders.find({"platform": {"$in": ["trendyol", "hepsiburada", "amazon"]}, "created_at": {"$gte": _since14},
                                                "$or": [{"items.barcode": {"$in": list(_bc2p.keys())}}, {"items.sku": {"$in": list(_bc2p.keys())}}]},
                                               {"_id": 0, "order_number": 1, "platform": 1, "created_at": 1, "status": 1, "items.barcode": 1, "items.sku": 1, "items.quantity": 1}).limit(40):
                    for it in (o.get("items") or []):
                        b = str(it.get("barcode") or it.get("sku") or "")
                        if b in _bc2p:
                            _hits.append({"order": o.get("order_number"), "platform": o.get("platform"), "at": str(o.get("created_at") or "")[:16],
                                          "status": o.get("status"), "product": (_bc2p[b].get("name") or "")[:45], "b": b[-4:], "qty": it.get("quantity"),
                                          "deleted_at": str(_bc2p[b].get("deleted_at") or "")[:10]})
            out["orders_for_deleted_products_14d"] = _hits[:40]
        except Exception as _oe:
            out["orders_for_deleted_error"] = str(_oe)[:120]
        # (3) stok yazan admin audit kayıtları (ürün güncelleme) — son 21 gün, bu ürünler
        try:
            ids = [p["id"] for p in prods]
            aud = await _db.audit_logs.find({"entity_id": {"$in": ids}, "created_at": {"$gte": since}},
                                             {"_id": 0, "action": 1, "created_at": 1, "source": 1, "entity_id": 1}).sort("created_at", -1).limit(30).to_list(30)
            out["admin_audit"] = [{"at": str(a.get("created_at") or "")[:19], "action": a.get("action"), "source": a.get("source")} for a in aud]
        except Exception as _ae:
            out["admin_audit_error"] = str(_ae)[:100]
    except Exception as e:
        out["error"] = str(e)[:300]
    try:
        await _db.settings.update_one({"id": "stock_audit_diag"}, {"$set": {"id": "stock_audit_diag", **out}}, upsert=True)
    except Exception:
        pass


async def fix_size_table_order_once() -> None:
    """Ürün galerisinde beden tablosu (is_size_table) nesnesi normal görsellerin ÖNÜNE geçmişse sona alır.
    Kartlar images[0]'ı görsel sandığı için ürün resimsiz görünüyordu (ör. Mold Balon Pantolon)."""
    from routes.deps import db as _db
    from routes.products import _size_tables_last
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.size_table_order_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        fixed = 0
        async for p in _db.products.find({"images.0.is_size_table": True}, {"_id": 0, "id": 1, "images": 1}):
            new = _size_tables_last(p.get("images") or [])
            if new != (p.get("images") or []):
                await _db.products.update_one({"id": p["id"]}, {"$set": {"images": new}})
                fixed += 1
        await _db.settings.update_one({"id": flag_id}, {"$set": {"id": flag_id, "done": True, "fixed": fixed,
                                                                 "at": _dt.now(_tz.utc).isoformat()}}, upsert=True)
        logger.warning(f"[size-table-order] düzeltilen ürün: {fixed}")
    except Exception as e:
        logger.error(f"[size-table-order] hata: {e}")


async def refit_installment_returns_once() -> None:
    """TEK SEFERLİK: taksitli siparişlerin ONAYLI ama HENÜZ ÖDENMEMİŞ kısmi iadelerini yeni vade farkı
    dağıtımına (kargo dahil orantılı, iyzico ile birebir) göre yeniden hesaplar. Elle düzeltilmiş
    tutarlara (refund_amount ≠ otomatik hesap) DOKUNMAZ. Ödenmiş/reddedilmiş iadeler kapsam dışı.
    Sonuç settings.migrations.vade_refit_v1 → /health.vade_refit."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.vade_refit_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        from routes.orders import _compute_refund_breakdown, _order_vade_farki, _round2, site_return_gider_pusulasi
        changed, skipped_manual, gp_regen, examined = [], 0, 0, 0
        cur = _db.customer_returns.find({"status": "approved", "refund_breakdown.is_partial": True,
                                         "refund_breakdown.vade_farki": {"$gt": 0.005}}, {"_id": 0})
        async for rec in cur:
            examined += 1
            order = await _db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}
            vf, _c, inst = _order_vade_farki(order)
            if vf <= 0.005 or inst <= 1:
                continue
            bd_old = rec.get("refund_breakdown") or {}
            old_amt = _round2(rec.get("refund_amount") or 0)
            if abs(old_amt - _round2(bd_old.get("auto_refund") or 0)) > 0.011:
                skipped_manual += 1      # operatör elle tutar girmiş → dokunma
                continue
            fault = rec.get("fault") or bd_old.get("fault") or "store"
            ret_net = bd_old.get("returned_products_net")
            if ret_net in (None, ""):
                continue
            cargo_ovr = bd_old.get("return_cargo_fee") if fault == "customer" else None
            bd_new = await _compute_refund_breakdown(rec, order, fault, return_cargo_fee_override=cargo_ovr,
                                                     returned_net_override=ret_net)
            if bd_old.get("cargo"):
                bd_new["cargo"] = bd_old.get("cargo")
            new_amt = _round2(bd_new.get("auto_refund") or 0)
            if abs(new_amt - old_amt) < 0.005:
                continue
            now = _dt.now(_tz.utc).isoformat()
            await _db.customer_returns.update_one({"id": rec["id"]}, {"$set": {
                "refund_breakdown": bd_new, "refund_amount": new_amt, "updated_at": now,
                "vade_refit": {"at": now, "old_amount": old_amt, "new_amount": new_amt,
                               "reason": "vade farkı kargo dahil orantılı (iyzico ile birebir)"}}})
            changed.append({"order": rec.get("order_number"), "old": old_amt, "new": new_amt})
            if await _db.gider_pusulasi.find_one({"return_id": rec["id"]}, {"_id": 0, "display_number": 1}):
                try:
                    _gp = await _db.gider_pusulasi.find_one({"return_id": rec["id"]}, {"_id": 0, "display_number": 1})
                    await site_return_gider_pusulasi(rec["id"], payload={
                        "include_cargo": fault == "customer", "tracking_no": _gp.get("display_number") or "",
                        "refund_amount": new_amt}, preview=False, current_user={"email": "sistem@vade-refit", "id": "system"})
                    gp_regen += 1
                except Exception as _ge:
                    logger.warning(f"[vade-refit] GP yenileme hatası {rec.get('order_number')}: {_ge}")
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(), "examined": examined,
            "changed": changed[:100], "changed_count": len(changed), "skipped_manual": skipped_manual,
            "gp_regenerated": gp_regen}}, upsert=True)
        logger.warning(f"[vade-refit] incelenen={examined} değişen={len(changed)} elle={skipped_manual} gp={gp_regen}")
    except Exception as e:
        logger.error(f"[vade-refit] hata: {e}")


async def _ty_mapping_probe(local_cat_id: str) -> dict:
    """TEŞHİS (hafif, 3 find_one): yerel kategorinin Trendyol eşlemesi + Menşei attribute meta'sı.
    'Menşei alanı geçersiz' hatasının kökü: varsayılan değer id'si bu kategorinin geçerli değerleri
    arasında yoksa resolve_attributes onu SESSİZCE düşürür → Trendyol zorunlu alanı boş görür."""
    out = {"local_cat": local_cat_id}
    try:
        cm = await db.category_mappings.find_one({"category_id": str(local_cat_id), "marketplace": "trendyol"}, {"_id": 0})
        if not cm:
            out["mapping"] = None
            return out
        mp_cat = cm.get("marketplace_category_id")
        out["mp_cat"] = mp_cat
        dm = cm.get("default_mappings") or {}
        out["default_mappings"] = dm
        out["attr_mappings"] = [(m.get("local_attr"), m.get("mp_attr_id") or m.get("trendyol_attr_id")) for m in (cm.get("attribute_mappings") or [])][:40]
        cache = await db.trendyol_category_attributes.find_one({"category_id": int(mp_cat) if str(mp_cat).isdigit() else str(mp_cat)}, {"_id": 0})
        if not (cache or {}).get("attributes"):
            cache = await db.trendyol_attributes.find_one({"category_id": int(mp_cat) if str(mp_cat).isdigit() else str(mp_cat)}, {"_id": 0})
        attrs = (cache or {}).get("attributes") or []
        out["meta_attr_count"] = len(attrs)
        out["meta_source"] = "trendyol_category_attributes" if cache and cache.get("attributes") else None
        req = []
        for a in attrs:
            aid = a.get("id") or (a.get("attribute") or {}).get("id")
            nm = a.get("name") or (a.get("attribute") or {}).get("name") or ""
            if a.get("required"):
                req.append({"id": aid, "name": nm, "default": dm.get(str(aid))})
            if "menşe" in nm.lower() or "mense" in nm.lower():
                vals = [(v.get("id"), v.get("name")) for v in (a.get("attributeValues") or [])]
                out["mensei"] = {"id": aid, "name": nm, "required": a.get("required"), "allowCustom": a.get("allowCustom"),
                                 "default_in_mapping": dm.get(str(aid)), "value_count": len(vals),
                                 "default_valid": str(dm.get(str(aid))) in {str(v[0]) for v in vals},
                                 "values_sample": vals[:12], "tr_values": [v for v in vals if str(v[1]).strip().lower() in ("tr", "türkiye", "turkiye")]}
        out["required_attrs"] = req
    except Exception as e:
        out["error"] = str(e)[:160]
    return out


async def sale_min_discount_once(min_pct: float = 30.0) -> None:
    """TEK SEFERLİK (kullanıcı talebi): SALE vitrinindeki (İNDİRİM kategorisi üyesi VEYA indirimli fiyatlı)
    ürünlerden etkin indirimi %30'un ALTINDA olanları %30'a getirir (sale_price = price × 0,70).
    Etkin indirim = ürün kartındaki indirimli fiyat oranı; yoksa aktif sepet kampanyası yüzdesi.
    %30 ve üzeri olanlara DOKUNULMAZ. Eski değerler db.price_change_log'a yazılır (geri alınabilir).
    Pazaryeri fiyatları etkilenmez (onlar liste/üye fiyatından türetilir). Sonuç: settings.migrations.sale_min30_v1."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.sale_min30_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        from routes.products import _auto_campaigns_for_badges, _campaign_pct_for_product
        _slugs = {"indirim", "sale", "firsat", "fırsat", "indirimli"}
        cat_ids = []
        async for c in _db.categories.find({}, {"_id": 0, "id": 1, "slug": 1, "name": 1, "slug_aliases": 1}):
            sl = (c.get("slug") or "").strip().lower()
            als = {str(a).strip().lower() for a in (c.get("slug_aliases") or [])}
            nm = (c.get("name") or "").strip().lower()
            if sl in _slugs or (als & _slugs) or nm in {"indirim", "i̇ndi̇ri̇m", "sale", "fırsat"}:
                cat_ids.append(str(c.get("id")))
        q = {"is_deleted": {"$ne": True}, "$or": [
            {"sale_price": {"$gt": 0}}, {"discount_price": {"$gt": 0}}, {"is_on_sale": True}, {"sale_active": True},
            {"category_ids": {"$in": cat_ids}}, {"category_id": {"$in": cat_ids}}]}
        camps = await _auto_campaigns_for_badges()
        factor = round(1 - min_pct / 100.0, 4)
        changed, untouched, skipped = [], 0, 0
        now = _dt.now(_tz.utc).isoformat()
        async for p in _db.products.find(q, {"_id": 0, "id": 1, "name": 1, "price": 1, "sale_price": 1,
                                             "category_id": 1, "category_ids": 1, "variants": 1, "is_active": 1}):
            price = float(p.get("price") or 0)
            if price <= 0:
                skipped += 1
                continue
            sp = float(p.get("sale_price") or 0)
            rate = (1 - sp / price) * 100.0 if (0 < sp < price) else 0.0
            src = "indirimli_fiyat" if rate > 0 else "kampanya"
            if rate <= 0:
                try:
                    rate = float((_campaign_pct_for_product(p, camps) or (0, ""))[0] or 0)
                except Exception:
                    rate = 0.0
            if rate >= min_pct - 0.05:
                untouched += 1
                continue
            new_sp = round(price * factor, 2)
            upd = {"sale_price": new_sp, "updated_at": now}
            vs = p.get("variants") or []
            v_changed = False
            for v in vs:
                if not isinstance(v, dict):
                    continue
                vp = float(v.get("price") or 0)
                if vp <= 0:
                    continue
                vsp = float(v.get("sale_price") or 0)
                vrate = (1 - vsp / vp) * 100.0 if (0 < vsp < vp) else 0.0
                if vrate < min_pct - 0.05:
                    v["sale_price"] = round(vp * factor, 2)
                    v_changed = True
            if v_changed:
                upd["variants"] = vs
            await _db.products.update_one({"id": p["id"]}, {"$set": upd})
            rec = {"product_id": p["id"], "name": p.get("name"), "price": price, "old_sale_price": sp or None,
                   "new_sale_price": new_sp, "old_rate": round(rate, 2), "old_source": src, "new_rate": min_pct,
                   "variants_touched": v_changed, "at": now, "reason": "SALE min %30 (kullanıcı talebi)",
                   "batch": flag_id}
            await _db.price_change_log.insert_one(dict(rec))
            changed.append({k: rec[k] for k in ("product_id", "name", "price", "old_sale_price", "new_sale_price", "old_rate", "old_source")})
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": now, "sale_category_ids": cat_ids, "changed_count": len(changed),
            "untouched_30_plus": untouched, "skipped_no_price": skipped, "changed": changed[:200]}}, upsert=True)
        logger.warning(f"[sale-min30] değişen={len(changed)} dokunulmayan={untouched} fiyatsız={skipped}")
    except Exception as e:
        logger.error(f"[sale-min30] hata: {e}")


async def fix_otp_email_template_once() -> None:
    """TEK SEFERLİK: 'Şifre Sıfırlama Kodu' E-POSTA şablonu jenerik üretilmişti ve içinde
    {otp_code} YOKTU → mail gidiyor ama KOD görünmüyordu (17.09). Markalı, kodu büyük gösteren
    şablonla değiştirilir. Elle düzenlenmiş şablona DOKUNULMAZ."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.otp_email_tpl_v2"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        from routes.notifications import _EMAIL_HTML_TEMPLATES, _DEFAULT_TEMPLATES
        now = _dt.now(_tz.utc).isoformat()
        yazilan = []
        # Hem şifre sıfırlama hem GİRİŞ doğrulama şablonları (e-posta + SMS) kurulur.
        for ev in ("password_reset_otp", "login_verification_code"):
            rich = _EMAIL_HTML_TEMPLATES.get(ev)
            if rich:
                cur = await _db.notification_templates.find_one(
                    {"event": ev, "channel": "email"}, {"_id": 0}) or {}
                if not (cur.get("manually_edited") and "{otp_code}" in str(cur.get("body") or "")):
                    await _db.notification_templates.update_one(
                        {"event": ev, "channel": "email"},
                        {"$set": {"event": ev, "channel": "email", "subject": rich["subject"],
                                  "body": rich["body"], "enabled": True, "updated_at": now}}, upsert=True)
                    yazilan.append(f"{ev}/email")
            _sms = _DEFAULT_TEMPLATES.get((ev, "sms"))
            if _sms:
                cur2 = await _db.notification_templates.find_one(
                    {"event": ev, "channel": "sms"}, {"_id": 0}) or {}
                if not cur2.get("body"):
                    await _db.notification_templates.update_one(
                        {"event": ev, "channel": "sms"},
                        {"$set": {"event": ev, "channel": "sms", "body": _sms,
                                  "enabled": True, "updated_at": now}}, upsert=True)
                    yazilan.append(f"{ev}/sms")
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": now, "yazilan": yazilan}}, upsert=True)
        logger.warning(f"[otp-mail] doğrulama şablonları kuruldu: {yazilan}")
    except Exception as e:
        logger.error(f"[otp-mail] hata: {e}")


async def audit_unpaid_confirmed_card_orders_once() -> None:
    """TEK SEFERLİK denetim: ödemesi ALINMAMIŞ olduğu hâlde 'onaylı/ilerlemiş' duruma çekilmiş
    KART siparişleri (W11874 ile aynı kusur). Yalnız RAPORLAR — hiçbir siparişi değiştirmez.
    Sonuç settings.migrations.audit_unpaid_card_v1 → /health.unpaid_card_audit."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.audit_unpaid_card_v2"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        _card = ["credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"]
        _settled = ["paid", "completed", "success", "succeeded", "captured"]
        _live = ["confirmed", "processing", "preparing", "shipped", "delivered", "undelivered"]
        rows, imported = [], 0
        async for o in _db.orders.find(
                {"status": {"$in": _live}, "payment_method": {"$in": _card},
                 "payment_status": {"$nin": _settled},
                 "paid_with_gift_card_only": {"$ne": True}},
                {"_id": 0, "order_number": 1, "status": 1, "payment_status": 1, "total": 1,
                 "created_at": 1, "iyzico_payment_id": 1, "needs_reconciliation": 1,
                 "imported_from": 1, "platform": 1}).limit(2000):
            # AKTARILMIŞ GEÇMİŞ (Ticimax/pazaryeri) siparişleri kusur DEĞİL: bu sistemde hiç
            # ödeme akışından geçmediler, payment_status aktarımda hiç 'paid' yazılmadı.
            if o.get("imported_from") or (o.get("platform") and o.get("platform") != "site"):
                imported += 1
                continue
            rows.append({"no": o.get("order_number"), "durum": o.get("status"),
                         "odeme": o.get("payment_status"), "tutar": o.get("total"),
                         "tarih": str(o.get("created_at") or "")[:16],
                         "iyzico_var": bool(o.get("iyzico_payment_id")),
                         "mutabakat_bekliyor": bool(o.get("needs_reconciliation"))})
        rows.sort(key=lambda r: r.get("tarih") or "", reverse=True)
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "count": len(rows), "imported_skipped": imported, "rows": rows[:60],
            "note": "Sadece rapor — hiçbir sipariş değiştirilmedi. Aktarılmış geçmiş siparişler hariç."}},
            upsert=True)
        logger.warning(f"[denetim] ödemesi alınmamış ama onaylı KENDİ kart siparişimiz: {len(rows)} "
                       f"(aktarılmış/pazaryeri hariç: {imported})")
    except Exception as e:
        logger.error(f"[denetim] unpaid-card hata: {e}")


# Ödeme denetimi çıktısı KİŞİSEL/TİCARİ veri taşıdığından /health üzerinde yalnız bu
# anahtarla açılır (sağlık ucu herkese açıktır; sayılar herkese, detay anahtarla).
# GÜVENLİK (2026-09-29): teşhis anahtarı Railway env SUPPORT_AUDIT_KEY'den okunur.
# GÜVENLİK (denetim 2026-10, #4): repoda açık duran sabit YEDEK anahtar KALDIRILDI. Env
# tanımlı değilse (boşsa) anahtarlı teşhis TAMAMEN KAPALIDIR. Anahtar
# tercihen `X-Audit-Key` başlığıyla gönderilir (query param geriye uyum için kabul edilir).
# Karşılaştırma sabit zamanlıdır (hmac.compare_digest).
def _audit_ok(k) -> bool:
    import hmac as _hmac
    k = str(k or "").strip()
    expected = (os.environ.get("SUPPORT_AUDIT_KEY") or "").strip()
    if not k or not expected:
        return False
    return _hmac.compare_digest(k.encode(), expected.encode())


async def audit_card_paid_without_charge_once() -> None:
    """TEK SEFERLİK DENETİM (kullanıcı bildirimi, W11805): iyzico'da ödeme TAMAMLANMAMIŞ
    (3DS üye işyerine dönüldü) olmasına rağmen panelde 'ödendi' görünen KART siparişleri.

    Yalnız OKUR — hiçbir siparişi değiştirmez:
      1) W11805'in kaydı + iyzico'ya CANLI sorgu (payment/detail) ile gerçek durumu,
      2) Son 180 günün 'ödendi' kart siparişlerinden, ödeme görüntüsünde GERÇEK çekim
         kanıtı (authCode/hostReference) veya paymentStatus=SUCCESS bulunmayanlar,
      3) Bu şüphelilerin ilk 25'i için iyzico'dan canlı doğrulama.
    Sonuç: settings.migrations.audit_card_paid_v1 → /health?diag=1&audit_key=..."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz, timedelta as _tdl
    import asyncio as _aio
    flag_id = "migrations.audit_card_paid_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return

        async def _iyzico_durum(oid, pid):
            """iyzico payment/detail — SALT OKUNUR (siparişe dokunmaz)."""
            if not pid:
                return {"hata": "paymentId yok"}
            try:
                import json as _json, httpx as _httpx
                from routes.payment import (_get_iyzico_settings as _gs, _v2_headers as _vh,
                                            PAYMENT_DETAIL_PATH as _pd)
                st = await _gs()
                body = {"locale": "tr", "conversationId": str(oid), "paymentId": str(pid)}
                bs = _json.dumps(body, separators=(",", ":"), ensure_ascii=False)
                hd = _vh(st["api_key"], st["api_secret"], _pd, bs)
                async with _httpx.AsyncClient(timeout=25) as c:
                    r = await c.post(f"{st['base_url']}{_pd}", content=bs.encode("utf-8"), headers=hd)
                if not r.headers.get("content-type", "").startswith("application/json"):
                    return {"hata": f"json degil ({r.status_code})"}
                d = r.json()
                return {"status": d.get("status"), "paymentStatus": d.get("paymentStatus"),
                        "paidPrice": d.get("paidPrice"), "authCode": d.get("authCode"),
                        "hostReference": d.get("hostReference"), "fraudStatus": d.get("fraudStatus"),
                        "errorCode": d.get("errorCode"), "errorMessage": str(d.get("errorMessage") or "")[:120]}
            except Exception as e:
                return {"hata": f"{type(e).__name__}: {str(e)[:120]}"}

        _PROJ = {"_id": 0, "id": 1, "order_number": 1, "status": 1, "payment_status": 1,
                 "payment_method": 1, "total": 1, "created_at": 1, "paid_at": 1,
                 "iyzico_payment_id": 1, "payment_id": 1, "reconcile_payment_id": 1,
                 "needs_reconciliation": 1, "iyzico_retrieve_response": 1,
                 "imported_from": 1, "platform": 1, "paid_with_gift_card_only": 1}

        # 1) W11805
        o = await _db.orders.find_one({"order_number": "W11805"}, _PROJ) or {}
        snap = o.get("iyzico_retrieve_response") or {}
        pid = str(o.get("iyzico_payment_id") or o.get("payment_id")
                  or snap.get("paymentId") or o.get("reconcile_payment_id") or "")
        w = {
            "bulundu": bool(o), "durum": o.get("status"), "odeme_durumu": o.get("payment_status"),
            "yontem": o.get("payment_method"), "tutar": o.get("total"),
            "olusturma": str(o.get("created_at") or "")[:19], "odendi_isareti": str(o.get("paid_at") or "")[:19],
            "paymentId": pid,
            "kayitli_yanit": {k: snap.get(k) for k in
                              ("status", "paymentStatus", "authCode", "hostReference",
                               "paidPrice", "errorMessage", "installment")},
            "iyzico_canli": await _iyzico_durum(o.get("id"), pid) if o else None,
        }

        # 2) Şüpheli tarama: 'ödendi' kart siparişleri, gerçek çekim kanıtı olmayanlar
        _card = ["credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"]
        cutoff = (_dt.now(_tz.utc) - _tdl(days=180)).isoformat()
        supheli, toplam_paid = [], 0
        async for od in _db.orders.find(
                {"payment_status": "paid", "payment_method": {"$in": _card},
                 "created_at": {"$gt": cutoff},
                 "paid_with_gift_card_only": {"$ne": True},
                 "imported_from": {"$exists": False}}, _PROJ):
            toplam_paid += 1
            sn = od.get("iyzico_retrieve_response") or {}
            ps = str(sn.get("paymentStatus") or "")
            kanit = bool(sn.get("authCode")) or bool(sn.get("hostReference"))
            if ps.upper() == "SUCCESS" and kanit:
                continue
            if not sn or (not kanit and ps.upper() != "SUCCESS"):
                supheli.append({
                    "siparis": od.get("order_number"), "id": od.get("id"),
                    "tarih": str(od.get("created_at") or "")[:19], "tutar": od.get("total"),
                    "durum": od.get("status"), "paymentStatus": ps or None,
                    "authCode": sn.get("authCode"), "hostReference": sn.get("hostReference"),
                    "paymentId": str(od.get("iyzico_payment_id") or od.get("payment_id")
                                     or sn.get("paymentId") or ""),
                })
        supheli.sort(key=lambda r: r.get("tarih") or "", reverse=True)

        # 3) Şüphelilerin ilk 25'i için iyzico'ya canlı sor (ne çekilmiş, ne çekilmemiş)
        for r in supheli[:25]:
            r["iyzico_canli"] = await _iyzico_durum(r.get("id"), r.get("paymentId"))
            await _aio.sleep(0.4)

        cekilmemis = [r for r in supheli[:25]
                      if isinstance(r.get("iyzico_canli"), dict)
                      and str((r["iyzico_canli"] or {}).get("paymentStatus") or "").upper() != "SUCCESS"]
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "w11805": w, "paid_kart_siparis": toplam_paid,
            "supheli_adet": len(supheli), "canli_dogrulanan": min(25, len(supheli)),
            "cekim_bulunamayan": len(cekilmemis),
            "supheli": [{k: v for k, v in r.items() if k != "id"} for r in supheli[:40]],
            "note": "Sadece rapor — hiçbir sipariş değiştirilmedi."}}, upsert=True)
        logger.warning(f"[denetim] kart 'odendi' ama cekim kaniti yok: supheli={len(supheli)} "
                       f"/ paid={toplam_paid} | canli dogrulamada cekim yok={len(cekilmemis)}")
    except Exception as e:
        logger.error(f"[denetim] kart-odendi denetimi hata: {e}")


async def audit_card_paid_without_charge_v2_once() -> None:
    """DENETİM v2 (W11805 devamı): v1'de 'şüpheli' çıkanların çoğu eski platformdan aktarılan
    (W ile başlamayan) siparişlerdi. v2 YALNIZ kendi siparişlerimizi (W…) tarar, paymentId'si
    olanları iyzico'ya CANLI sorar ve gerçekten çekim OLMAYANLARI ayırır.
    Ayrıca W11805'in iade durumu: iyzico kaydının tamamı (PII'siz) + siparişteki refunds[].
    SALT OKUNUR. Sonuç settings.migrations.audit_card_paid_v2 → /health (audit_key ile)."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz, timedelta as _tdl
    import asyncio as _aio
    flag_id = "migrations.audit_card_paid_v2"
    _PII = {"buyer", "shippingAddress", "billingAddress", "cardUserKey", "cardToken",
            "binNumber", "lastFourDigits", "cardHolderName"}
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return

        async def _detail(oid, pid, tam=False):
            if not pid:
                return {"hata": "paymentId yok"}
            try:
                import json as _json, httpx as _httpx
                from routes.payment import (_get_iyzico_settings as _gs, _v2_headers as _vh,
                                            PAYMENT_DETAIL_PATH as _pd)
                st = await _gs()
                body = {"locale": "tr", "conversationId": str(oid), "paymentId": str(pid)}
                bs = _json.dumps(body, separators=(",", ":"), ensure_ascii=False)
                hd = _vh(st["api_key"], st["api_secret"], _pd, bs)
                async with _httpx.AsyncClient(timeout=25) as c:
                    r = await c.post(f"{st['base_url']}{_pd}", content=bs.encode("utf-8"), headers=hd)
                if not r.headers.get("content-type", "").startswith("application/json"):
                    return {"hata": f"json degil ({r.status_code})"}
                d = r.json()
                if tam:   # tüm alanlar (iade bilgisi dahil), PII temizlenmiş
                    def _clean(x):
                        if isinstance(x, dict):
                            return {k: _clean(v) for k, v in x.items() if k not in _PII}
                        if isinstance(x, list):
                            return [_clean(i) for i in x]
                        return x
                    return _clean(d)
                return {"paymentStatus": d.get("paymentStatus"), "status": d.get("status"),
                        "paidPrice": d.get("paidPrice"), "authCode": d.get("authCode"),
                        "errorMessage": str(d.get("errorMessage") or "")[:100]}
            except Exception as e:
                return {"hata": f"{type(e).__name__}: {str(e)[:100]}"}

        # ── W11805: iade gerçekten yapılmış mı? ───────────────────────────────
        o = await _db.orders.find_one({"order_number": "W11805"}, {"_id": 0}) or {}
        w = {
            "durum": o.get("status"), "odeme_durumu": o.get("payment_status"),
            "tutar": o.get("total"), "refunds": o.get("refunds") or [],
            "iade_talebi": bool(o.get("return_request")),
            "iyzico_kaydi": await _detail(o.get("id"),
                                          o.get("iyzico_payment_id") or o.get("payment_id"), tam=True),
        }
        try:
            _ret = await _db.returns.find_one({"order_number": "W11805"}, {"_id": 0})
            w["iade_kaydi"] = {k: _ret.get(k) for k in
                               ("status", "refund_amount", "refund_method", "created_at",
                                "approved_at", "refunded_at", "return_code")} if _ret else None
        except Exception:
            w["iade_kaydi"] = None

        # ── Kendi siparişlerimiz (W…): 'ödendi' ama gerçekten çekilmiş mi? ─────
        _card = ["credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"]
        cutoff = (_dt.now(_tz.utc) - _tdl(days=180)).isoformat()
        rows = await _db.orders.find(
            {"payment_status": "paid", "payment_method": {"$in": _card},
             "created_at": {"$gt": cutoff}, "order_number": {"$regex": "^W"},
             "paid_with_gift_card_only": {"$ne": True}},
            {"_id": 0, "id": 1, "order_number": 1, "total": 1, "status": 1, "created_at": 1,
             "iyzico_payment_id": 1, "payment_id": 1, "iyzico_retrieve_response": 1}
        ).sort("created_at", -1).to_list(600)
        pidsiz, kontrol = [], []
        for r in rows:
            pid = str(r.get("iyzico_payment_id") or r.get("payment_id")
                      or (r.get("iyzico_retrieve_response") or {}).get("paymentId") or "").strip()
            (kontrol if pid else pidsiz).append((r, pid))
        cekilmis, cekilmemis, hatali = 0, [], 0
        for r, pid in kontrol[:300]:
            d = await _detail(r.get("id"), pid)
            ps = str(d.get("paymentStatus") or "").upper()
            if ps == "SUCCESS":
                cekilmis += 1
            elif d.get("hata"):
                hatali += 1
            else:
                cekilmemis.append({"siparis": r.get("order_number"), "tarih": str(r.get("created_at"))[:19],
                                   "tutar": r.get("total"), "durum": r.get("status"),
                                   "paymentId": pid, "iyzico": d})
            await _aio.sleep(0.25)
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "w11805": w,
            "kendi_paid_kart": len(rows), "paymentId_olan": len(kontrol),
            "paymentId_olmayan": len(pidsiz),
            "paymentId_olmayan_ornek": [{"siparis": x[0].get("order_number"),
                                         "tarih": str(x[0].get("created_at"))[:19],
                                         "tutar": x[0].get("total")} for x in pidsiz[:15]],
            "canli_kontrol": len(kontrol[:300]), "iyzico_SUCCESS": cekilmis,
            "iyzico_cekim_yok": len(cekilmemis), "sorgu_hatasi": hatali,
            "cekim_yok_liste": cekilmemis[:40],
            "note": "Sadece rapor — hiçbir sipariş değiştirilmedi."}}, upsert=True)
        logger.warning(f"[denetim v2] kendi paid kart={len(rows)} pid'li={len(kontrol)} "
                       f"SUCCESS={cekilmis} CEKIM-YOK={len(cekilmemis)} hata={hatali}")
    except Exception as e:
        logger.error(f"[denetim v2] hata: {e}")


async def audit_w11805_refund_state_once() -> None:
    """DENETİM v3: W11805'te para ÇEKİLMİŞ (iyzico SUCCESS 1899 TL) ve sipariş panelde
    'iade edildi' görünüyor; ancak sipariş üzerinde iyzico iade kaydı YOK. Müşteriye para
    geri ödendi mi, ödenmediyse nerede duruyor — tüm iade/iptal kayıtları ve sipariş
    geçmişi taranır. Ayrıca kalan kart siparişlerinin TAMAMI iyzico'ya sorulur.
    SALT OKUNUR."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz, timedelta as _tdl
    import asyncio as _aio
    import json
    flag_id = "migrations.audit_w11805_refund_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        o = await _db.orders.find_one({"order_number": "W11805"}, {"_id": 0}) or {}
        oid = o.get("id")
        _ilgi = [k for k in o.keys() if any(x in k.lower() for x in
                 ("refund", "iade", "cancel", "iptal", "return", "status", "paid", "gider"))]
        siparis = {k: (str(o.get(k))[:200] if not isinstance(o.get(k), (list, dict))
                       else json.dumps(o.get(k), ensure_ascii=False, default=str)[:400])
                   for k in _ilgi}
        # Tüm koleksiyonlarda bu siparişe ait iade/iptal kaydı var mı?
        kayitlar = {}
        for coll in ("returns", "rooftr_returns", "return_requests", "cancellations",
                     "refunds", "order_events", "activity_log", "integration_logs"):
            try:
                cur = _db[coll].find({"$or": [{"order_number": "W11805"}, {"order_id": oid},
                                              {"entity_id": oid}, {"ref_id": oid}]}, {"_id": 0})
                docs = await cur.to_list(10)
                if docs:
                    kayitlar[coll] = [json.dumps({k: v for k, v in d.items()
                                                  if k not in ("items", "products", "address",
                                                               "shipping_address", "buyer")},
                                                 ensure_ascii=False, default=str)[:400] for d in docs]
            except Exception as _ce:
                kayitlar[coll] = [f"okunamadi: {str(_ce)[:60]}"]
        # Kalan kart siparişlerinin TAMAMI iyzico'da doğrulansın (v2'de ilk 300 bakılmıştı)
        _card = ["credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"]
        cutoff = (_dt.now(_tz.utc) - _tdl(days=180)).isoformat()
        rows = await _db.orders.find(
            {"payment_status": "paid", "payment_method": {"$in": _card},
             "created_at": {"$gt": cutoff}, "order_number": {"$regex": "^W"},
             "paid_with_gift_card_only": {"$ne": True}},
            {"_id": 0, "id": 1, "order_number": 1, "total": 1, "status": 1, "created_at": 1,
             "iyzico_payment_id": 1, "payment_id": 1}
        ).sort("created_at", 1).to_list(1200)
        try:
            from routes.payment import (_get_iyzico_settings as _gs, _v2_headers as _vh,
                                        PAYMENT_DETAIL_PATH as _pd)
            import httpx as _httpx
            st = await _gs()
            ok_n, kotu, hata_n = 0, [], 0
            async with _httpx.AsyncClient(timeout=25) as c:
                for r in rows:
                    pid = str(r.get("iyzico_payment_id") or r.get("payment_id") or "").strip()
                    if not pid:
                        kotu.append({"siparis": r.get("order_number"), "sebep": "paymentId yok",
                                     "tutar": r.get("total"), "tarih": str(r.get("created_at"))[:19]})
                        continue
                    try:
                        body = {"locale": "tr", "conversationId": str(r.get("id")), "paymentId": pid}
                        bs = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
                        resp = await c.post(f"{st['base_url']}{_pd}", content=bs.encode("utf-8"),
                                            headers=_vh(st["api_key"], st["api_secret"], _pd, bs))
                        d = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                        if str(d.get("paymentStatus") or "").upper() == "SUCCESS":
                            ok_n += 1
                        else:
                            kotu.append({"siparis": r.get("order_number"), "tutar": r.get("total"),
                                         "tarih": str(r.get("created_at"))[:19],
                                         "paymentId": pid, "durum": r.get("status"),
                                         "iyzico": str(d.get("paymentStatus")),
                                         "hata": str(d.get("errorMessage") or "")[:80]})
                    except Exception as _pe:
                        hata_n += 1
                    await _aio.sleep(0.2)
        except Exception as _ie:
            ok_n, kotu, hata_n = 0, [{"sebep": f"tarama hatasi: {str(_ie)[:100]}"}], 0
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "w11805_siparis": siparis, "w11805_kayitlar": kayitlar,
            "tarama_toplam": len(rows), "tarama_SUCCESS": ok_n,
            "tarama_sorunlu": len(kotu), "sorunlu_liste": kotu[:40], "tarama_hata": hata_n,
            "note": "Sadece rapor."}}, upsert=True)
        logger.warning(f"[denetim v3] W11805 iade kayitlari={list(kayitlar)} | "
                       f"tam tarama: {ok_n}/{len(rows)} SUCCESS, sorunlu={len(kotu)}")
    except Exception as e:
        logger.error(f"[denetim v3] hata: {e}")


async def audit_cancel_requested_visibility_once() -> None:
    """TEK SEFERLİK TEŞHİS: 'İptal Talebi Alındı' (cancel_requested) siparişler ana listede
    MOR satır olarak görünmeli. Kullanıcı: isimle arayınca görünüyor, platform=Web Sitesi
    süzgeciyle görünmüyor. Hangi alanın/süzgecin gizlediğini veriyle saptar. SALT OKUNUR."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz, timedelta as _tdl
    from collections import Counter
    flag_id = "migrations.audit_cancelreq_vis_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        cutoff = (_dt.now(_tz.utc) - _tdl(days=365)).isoformat()
        rows = await _db.orders.find(
            {"status": "cancel_requested", "created_at": {"$gt": cutoff}},
            {"_id": 0, "order_number": 1, "platform": 1, "marketplace": 1, "payment_status": 1,
             "payment_method": 1, "created_at": 1, "refund_pending": 1, "total": 1}
        ).sort("created_at", -1).to_list(300)
        _settled = {"paid", "completed", "success", "succeeded", "captured"}
        _offline = {"bank_transfer", "havale", "eft", "havale_eft", "banka_havale", "transfer",
                    "cash_on_delivery", "kapida", "kapida_odeme", "cod"}
        _fulfilled = {"confirmed", "processing", "preparing", "shipped", "delivered",
                      "completed", "undelivered", "cancelled"}
        det, plat_c, gizli = [], Counter(), 0
        for r in rows:
            pf = r.get("platform")
            plat_c[str(pf)] += 1
            web = pf in ("meluni", None, "")
            junk = (web and str(r.get("payment_status") or "").lower() not in _settled
                    and str(r.get("payment_method") or "").lower() not in _offline
                    and "cancel_requested" not in _fulfilled)
            if junk:
                gizli += 1
            det.append({"siparis": r.get("order_number"), "tarih": str(r.get("created_at"))[:19],
                        "platform": pf, "odeme": r.get("payment_status"),
                        "yontem": r.get("payment_method"), "tutar": r.get("total"),
                        "meluni_suzgecinde_gorunur": pf == "meluni",
                        "junk_suzgeci_gizler": junk})
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "adet": len(rows), "platform_dagilimi": dict(plat_c),
            "junk_suzgeci_gizleyen": gizli,
            "meluni_suzgecinde_gorunmeyen": sum(1 for d in det if not d["meluni_suzgecinde_gorunur"]),
            "ornekler": det[:40], "note": "Sadece rapor."}}, upsert=True)
        logger.warning(f"[teshis] cancel_requested={len(rows)} platform={dict(plat_c)} "
                       f"junk_gizler={gizli}")
    except Exception as e:
        logger.error(f"[teshis] cancel_requested gorunurluk hatasi: {e}")


async def audit_3ds_dropoff_once() -> None:
    """TEK SEFERLİK ÖLÇÜM (müşteri şikâyeti): banka 3DS onayından sonra tarayıcı dönüşü
    kırılan siparişler ("Safari: adres geçersiz"). Son 21 günde kaç kart siparişi 3DS
    başlattı, kaçı tamamlandı, tamamlanmayanların parası GERÇEKTEN çekilmiş mi?
    SALT OKUNUR. Sonuç /health (audit_key ile) → threeds_audit."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz, timedelta as _tdl
    import asyncio as _aio
    flag_id = "migrations.audit_3ds_dropoff_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        cutoff = (_dt.now(_tz.utc) - _tdl(days=21)).isoformat()
        _card = ["credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"]
        _proj = {"_id": 0, "id": 1, "order_number": 1, "created_at": 1, "total": 1,
                 "status": 1, "payment_status": 1, "payment_flow": 1,
                 "iyzico_conversation_id": 1, "iyzico_payment_id": 1, "payment_id": 1,
                 "reconcile_payment_id": 1}
        toplam = basladi = odendi = 0
        yarim = []
        async for o in _db.orders.find(
                {"created_at": {"$gt": cutoff}, "payment_method": {"$in": _card},
                 "order_number": {"$regex": "^W"}}, _proj):
            toplam += 1
            _3ds = bool(o.get("payment_flow") == "3ds" or o.get("iyzico_conversation_id"))
            if not _3ds:
                continue
            basladi += 1
            if str(o.get("payment_status") or "").lower() == "paid":
                odendi += 1
            else:
                yarim.append(o)
        yarim.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)

        # Tamamlanmayanların parası çekilmiş mi? (iyzico'ya canlı sor — en fazla 40 tanesi)
        cekilmis, sorgulanan, ornekler = [], 0, []
        try:
            import json as _json, httpx as _httpx
            from routes.payment import (_get_iyzico_settings as _gs, _v2_headers as _vh,
                                        PAYMENT_DETAIL_PATH as _pd)
            st = await _gs()
            async with _httpx.AsyncClient(timeout=25) as c:
                for o in yarim[:40]:
                    pid = str(o.get("iyzico_payment_id") or o.get("payment_id")
                              or o.get("reconcile_payment_id") or "").strip()
                    kayit = {"siparis": o.get("order_number"),
                             "tarih": str(o.get("created_at") or "")[:19],
                             "tutar": o.get("total"), "durum": o.get("status"),
                             "odeme": o.get("payment_status"),
                             "paymentId": pid or None, "iyzico": None}
                    if pid:
                        sorgulanan += 1
                        try:
                            body = {"locale": "tr", "conversationId": str(o.get("id")), "paymentId": pid}
                            bs = _json.dumps(body, separators=(",", ":"), ensure_ascii=False)
                            r = await c.post(f"{st['base_url']}{_pd}", content=bs.encode("utf-8"),
                                             headers=_vh(st["api_key"], st["api_secret"], _pd, bs))
                            d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                            kayit["iyzico"] = str(d.get("paymentStatus") or d.get("status") or "?")
                            if str(d.get("paymentStatus") or "").upper() == "SUCCESS":
                                cekilmis.append(kayit)
                        except Exception as _pe:
                            kayit["iyzico"] = f"hata: {str(_pe)[:60]}"
                        await _aio.sleep(0.25)
                    ornekler.append(kayit)
        except Exception as _ie:
            ornekler.append({"hata": str(_ie)[:120]})

        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "gun": 21, "kart_siparisi": toplam, "3ds_baslatan": basladi,
            "3ds_tamamlanan": odendi, "yarim_kalan": len(yarim),
            "tamamlanma_orani": (round(odendi / basladi * 100, 1) if basladi else None),
            "paymentId_ile_sorgulanan": sorgulanan,
            "PARASI_CEKILMIS_AMA_KAYITSIZ": len(cekilmis),
            "cekilmis_liste": cekilmis[:10], "son_yarim_kalanlar": ornekler[:15],
            "note": "Salt okunur. 'Parası çekilmiş ama kayıtsız' > 0 ise elle finalize gerekir."}},
            upsert=True)
        logger.warning(f"[denetim] 3DS: baslatan={basladi} tamamlanan={odendi} "
                       f"yarim={len(yarim)} cekilmis-kayitsiz={len(cekilmis)}")
    except Exception as e:
        logger.error(f"[denetim] 3ds dropoff hata: {e}")


async def audit_member_group_discount_once() -> None:
    """TEK SEFERLİK denetim: üye-grubu indirimi (users.group_id → member_groups.discount_percent)
    kasada GÖSTERİLİYOR ama sipariş kaydında HESAPLANMIYORDU. Onay-tutarı denetimi devrede
    olduğu için indirimli gruptaki üye sipariş veremiyor, 409 "Sepet tutarı güncellendi"
    hatası alıyordu. İndirim artık kampanya motorunun içinde (önizleme = sipariş).

    Bu görev yalnız RAPORLAR + düzeltmeyi doğrular: kaç grubun indirimi var, kaç üye etkileniyor
    ve örnek bir üye için motor gerçekten indirimi üretiyor mu. Hiçbir kayıt değiştirilmez.
    Sonuç settings.migrations.audit_member_discount_v1 → /health.member_discount_audit."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.audit_member_discount_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        groups = await _db.member_groups.find(
            {}, {"_id": 0, "id": 1, "name": 1, "discount_percent": 1}).to_list(200)
        rows, sample_uid, etkilenen = [], None, 0
        for g in groups:
            try:
                pct = float(g.get("discount_percent") or 0)
            except Exception:
                pct = 0.0
            if pct <= 0:
                continue
            n = await _db.users.count_documents({"group_id": g.get("id")})
            etkilenen += n
            rows.append({"grup": g.get("name") or "", "yuzde": pct, "uye": n})
            if n and not sample_uid:
                u = await _db.users.find_one({"group_id": g.get("id")}, {"_id": 0, "id": 1}) or {}
                sample_uid = u.get("id")
        # Doğrulama: örnek üye için motor 1000 TL'lik sepette indirimi üretiyor mu?
        dogrulama = None
        if sample_uid:
            try:
                from routes.coupons import evaluate_cart_promotions as _ev_fn
                _ev = await _ev_fn(cart_total=1000.0, items=[], user_id=sample_uid,
                                   email="", entered_code="", payment_method="")
                dogrulama = {"taban": 1000.0,
                             "uye_indirimi": _ev.get("member_discount"),
                             "yuzde": _ev.get("member_discount_pct"),
                             "toplam_indirim": _ev.get("total_discount")}
            except Exception as _ve:
                dogrulama = {"hata": str(_ve)[:200]}
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "indirimli_grup": len(rows), "etkilenen_uye": etkilenen,
            "gruplar": rows, "dogrulama": dogrulama,
            "note": "Sadece rapor. Üye indirimi artık kampanya motorunda hesaplanıyor "
                    "(kasa önizlemesi = sipariş kaydı)."}},
            upsert=True)
        logger.warning(f"[denetim] uye-grubu indirimi: indirimli grup={len(rows)} "
                       f"etkilenen uye={etkilenen} dogrulama={dogrulama}")
    except Exception as e:
        logger.error(f"[denetim] uye-grubu indirimi denetimi hata: {e}")


async def audit_subset_return_overrefund_once() -> None:
    """DENETİM (SALT OKUNUR): geçmişte "alt küme iade talebi → TÜM SİPARİŞ iadesi" hatasından
    kaç siparişte FAZLA para iade edilmiş, toplamı ne kadar?

    Hata: müşteri çok kalemli siparişin BİR kalemi için iade talebi açtığında
    customer_returns.items siparişin alt kümesi oluyordu; onay akışı kısmiliği yalnız
    "seçim < KAYITTAKİ kalem" ile ölçtüğünden kısmi saymıyor, iade hesabı tam iade dalına
    düşüp order.total'ı iade ediyordu. Kod düzeltildi (_return_covers_whole_order);
    bu görev YALNIZ geçmiş hasarı raporlar, hiçbir kaydı değiştirmez.

    Sonuç settings.migrations.audit_subset_refund_v1 → /health?diag=1&audit_key=..."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.audit_subset_refund_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return

        def _r2(v):
            try:
                return round(float(v or 0), 2)
            except Exception:
                return 0.0

        def _q(it):
            try:
                return max(1, int((it or {}).get("quantity", 1) or 1))
            except Exception:
                return 1

        supheli, toplam_fazla, taranan = [], 0.0, 0
        cur = _db.customer_returns.find(
            {"status": {"$in": ["approved", "refunded", "returned", "partial_refunded"]}},
            {"_id": 0, "id": 1, "order_id": 1, "order_number": 1, "items": 1,
             "refund_amount": 1, "status": 1, "created_at": 1, "approval": 1})
        async for rec in cur:
            taranan += 1
            _ri = rec.get("items") or []
            if not _ri:
                continue
            o = await _db.orders.find_one(
                {"id": rec.get("order_id")},
                {"_id": 0, "order_number": 1, "items": 1, "total": 1, "subtotal": 1}) or {}
            _oi = o.get("items") or []
            if not _oi:
                continue
            # Alt küme mi? (kalem sayısı VEYA toplam adet eksik)
            if len(_ri) >= len(_oi) and sum(_q(i) for i in _ri) >= sum(_q(i) for i in _oi):
                continue
            _ref = _r2(rec.get("refund_amount"))
            _tot = _r2(o.get("total"))
            if _ref <= 0.01 or _tot <= 0.01:
                continue
            # İade edilen kalemlerin brüt karşılığı (üst sınır) — bunun ÜSTÜ fazla ödemedir.
            _sel_gross = _r2(sum(_r2((i or {}).get("price")) * _q(i) for i in _ri))
            if _ref <= _sel_gross + 0.02:
                continue          # seçili kalemler kadar ya da altında → sorun yok
            _fazla = _r2(_ref - _sel_gross)
            toplam_fazla = _r2(toplam_fazla + _fazla)
            supheli.append({
                "iade_id": rec.get("id"), "siparis": o.get("order_number") or rec.get("order_number"),
                "durum": rec.get("status"), "tarih": str(rec.get("created_at") or "")[:19],
                "iade_edilen": _ref, "siparis_toplami": _tot,
                "iade_kalem": len(_ri), "siparis_kalem": len(_oi),
                "secili_brut": _sel_gross, "fazla": _fazla,
                "elle_mi": bool((rec.get("approval") or {}).get("manual_override")),
            })
        supheli.sort(key=lambda x: -x["fazla"])
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "taranan_iade": taranan, "supheli_adet": len(supheli),
            "toplam_fazla_tl": toplam_fazla, "ornekler": supheli[:40],
            "note": "Salt okunur. 'fazla' = iade edilen tutar − iade talebindeki kalemlerin "
                    "brüt toplamı. Kök sebep kodda kapatıldı (_return_covers_whole_order); "
                    "elle_mi=True olanlarda tutarı personel bilerek girmiş olabilir."}},
            upsert=True)
        logger.warning(f"[denetim] alt-kume iade fazla odeme: supheli={len(supheli)} "
                       f"toplam_fazla={toplam_fazla} taranan={taranan}")
    except Exception as e:
        logger.error(f"[denetim] alt-kume iade denetimi hata: {e}")


class _NullCtx:
    """httpx yoksa denetimin çökmemesi için boş bağlam (iyzico sorgusu atlanır)."""
    async def __aenter__(self):
        return None
    async def __aexit__(self, *a):
        return False


async def audit_subset_return_money_v2_once() -> None:
    """DENETİM v2 (SALT OKUNUR): v1'in işaretlediği "fazla iade" vakalarında para GERÇEKTEN
    çıkmış mı? Sipariş üzerindeki iade kayıtları + iyzico ödeme detayı (itemTransactions
    içindeki iade satırları) okunur; kuruş hareket ettirilmez.

    Sonuç settings.migrations.audit_subset_refund_v2 → /health?diag=1&audit_key=..."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    import asyncio as _aio
    import json
    flag_id = "migrations.audit_subset_refund_v2"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        # v1 aynı açılışta paralel koşuyor olabilir — bitmesini bekle (bitmezse bu açılışta
        # HİÇ done yazma ki sonraki açılışta tekrar denensin).
        v1 = {}
        for _w in range(40):
            v1 = await _db.settings.find_one({"id": "migrations.audit_subset_refund_v1"}, {"_id": 0}) or {}
            if v1.get("done"):
                break
            await _aio.sleep(3)
        if not v1.get("done"):
            return
        vakalar = v1.get("ornekler") or []
        if not vakalar:
            await _db.settings.update_one({"id": flag_id}, {"$set": {
                "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
                "sonuc": "v1 şüpheli vaka bulmadı"}}, upsert=True)
            return
        try:
            from routes.payment import (_get_iyzico_settings as _gs, _v2_headers as _vh,
                                        PAYMENT_DETAIL_PATH as _pd)
            import httpx as _httpx
            st = await _gs()
        except Exception:
            st, _httpx = None, None
        rapor = []
        _cm = _httpx.AsyncClient(timeout=25) if _httpx else _NullCtx()
        async with _cm as c:
            for v in vakalar[:20]:
                o = await _db.orders.find_one({"order_number": v.get("siparis")}, {"_id": 0}) or {}
                pid = str(o.get("iyzico_payment_id") or o.get("payment_id") or "").strip()
                _iade_alanlari = {k: o.get(k) for k in o.keys()
                                  if any(x in k.lower() for x in ("refund", "iade")) }
                iyz = {"paymentId": pid or None}
                if st and pid:
                    try:
                        body = {"locale": "tr", "conversationId": str(o.get("id")), "paymentId": pid}
                        bs = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
                        resp = await c.post(f"{st['base_url']}{_pd}", content=bs.encode("utf-8"),
                                            headers=_vh(st["api_key"], st["api_secret"], _pd, bs))
                        d = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                        _its = d.get("itemTransactions") or []
                        _iade_toplam = 0.0
                        for _t in _its:
                            try:
                                _iade_toplam += float(_t.get("paidPrice") or 0) - float(
                                    _t.get("convertedPayout", {}).get("paidPrice") or _t.get("paidPrice") or 0)
                            except Exception:
                                pass
                        iyz.update({
                            "durum": str(d.get("paymentStatus") or ""),
                            "cekilen": d.get("paidPrice"),
                            "kalem_sayisi": len(_its),
                            "kalem_durumlari": [str(_t.get("transactionStatus")) for _t in _its][:10],
                            "iade_satirlari": [{"tutar": _t.get("paidPrice"),
                                                "durum": str(_t.get("transactionStatus"))}
                                               for _t in _its if str(_t.get("transactionStatus")) in ("3", "-1")][:10],
                        })
                    except Exception as _pe:
                        iyz["hata"] = str(_pe)[:120]
                    await _aio.sleep(0.2)
                rapor.append({
                    "siparis": v.get("siparis"), "iade_id": v.get("iade_id"),
                    "onaylanan_iade": v.get("iade_edilen"), "olmasi_gereken": v.get("secili_brut"),
                    "fazla": v.get("fazla"), "iade_durumu": v.get("durum"),
                    "siparis_durumu": o.get("status"), "odeme_durumu": o.get("payment_status"),
                    "siparis_iade_alanlari": {k: str(x)[:120] for k, x in _iade_alanlari.items()},
                    "iyzico": iyz,
                })
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "at": _dt.now(_tz.utc).isoformat(),
            "vaka": len(rapor), "rapor": rapor,
            "note": "Salt okunur. 'onaylanan_iade' panelde onaylanan tutar; paranın fiilen "
                    "çıkıp çıkmadığı iyzico kalem durumlarından ve siparişin iade "
                    "alanlarından okunur."}}, upsert=True)
        logger.warning(f"[denetim] alt-kume iade v2: vaka={len(rapor)}")
    except Exception as e:
        logger.error(f"[denetim] alt-kume iade v2 hata: {e}")


async def merge_duplicate_body_category_once() -> None:
    """TEK SEFERLİK (kullanıcı talebi): panelde AYNI ada/slug'a sahip İKİ "Body" kategorisi
    vardı (GİYİM > Üst Giyim altında). Ürünler tek kategoride toplanır, boş olan silinir.

    Güvenli yol: routes/categories.merge_categories — alt kategoriler, ürün
    category_id/category_ids/slug/name, kampanya kapsamı, manuel ürün sırası, feed etiket
    kuralları ve pazaryeri eşlemeleri hedefe taşınır; kaynakta ürün KALIRSA silme YAPILMAZ.

    KORUNAN kategori = ÜRÜNÜ ÇOK olan (eşitse önce oluşturulan). Beklenen durum dışında
    (2'den farklı sayıda Body, farklı üst kategori) HİÇBİR ŞEY yapılmaz, yalnız rapor yazılır.
    Sonuç settings.migrations.merge_dup_body_v1 → /health?diag=1 → body_merge"""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.merge_dup_body_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return

        def _vars(v):
            out = [v, str(v)]
            try:
                out.append(int(str(v)))
            except (TypeError, ValueError):
                pass
            seen, uniq = set(), []
            for x in out:
                k = (type(x).__name__, str(x))
                if k not in seen:
                    seen.add(k)
                    uniq.append(x)
            return uniq

        cats = await _db.categories.find(
            {"$or": [{"slug": "body"}, {"name": {"$regex": "^body$", "$options": "i"}}]},
            {"_id": 0}).to_list(20)

        async def _cnt(cid):
            return await _db.products.count_documents(
                {"$or": [{"category_id": {"$in": _vars(cid)}}, {"category_ids": {"$in": _vars(cid)}}]})

        durum = [{"id": c.get("id"), "slug": c.get("slug"), "parent_id": c.get("parent_id"),
                  "created_at": str(c.get("created_at") or ""), "source": c.get("source") or "",
                  "urun": await _cnt(c.get("id"))} for c in cats]

        async def _kayit(**extra):
            await _db.settings.update_one({"id": flag_id}, {"$set": {
                "id": flag_id, "at": _dt.now(_tz.utc).isoformat(), "bulunan": durum, **extra}},
                upsert=True)

        if len(cats) != 2:
            await _kayit(done=True, sonuc=f"beklenen 2 Body yerine {len(cats)} bulundu — DOKUNULMADI")
            logger.warning(f"[body-merge] {len(cats)} kategori bulundu, islem yapilmadi")
            return
        a, b = cats[0], cats[1]
        if str(a.get("parent_id")) != str(b.get("parent_id")):
            await _kayit(done=True, sonuc="üst kategorileri farklı — DOKUNULMADI (mükerrer olmayabilir)")
            return

        na, nb = await _cnt(a.get("id")), await _cnt(b.get("id"))
        if na != nb:
            keeper, loser = (a, b) if na > nb else (b, a)
        else:
            _ca, _cb = str(a.get("created_at") or "9"), str(b.get("created_at") or "9")
            keeper, loser = (a, b) if _ca <= _cb else (b, a)

        from routes.categories import merge_categories as _merge
        rapor = await _merge({"source_id": loser.get("id"), "target_id": keeper.get("id")},
                             current_user={"email": "system-migration"})
        await _kayit(done=True, sonuc="birleştirildi",
                     korunan={"id": keeper.get("id"), "urun": max(na, nb)},
                     silinen={"id": loser.get("id"), "urun": min(na, nb)},
                     rapor=(rapor or {}).get("report"))
        logger.warning(f"[body-merge] {loser.get('id')} → {keeper.get('id')} tamam: {rapor}")
    except Exception as e:
        logger.error(f"[body-merge] hata: {e}")


async def fix_w11874_payment_method_once() -> None:
    """TEK SEFERLİK veri düzeltmesi (kullanıcı talebi): W11874 siparişi panelde 'Kredi Kartı'
    görünüyordu ama müşteri parayı HAVALE/EFT ile gönderdi.

    Ne olmuştu: müşteri kartla sipariş açtı, kart ödemesi BAŞARISIZ oldu (iyzico kaydı yok,
    payment_status=failed), 11:10'da iptal talebi oluşturdu, 11:31'de personel listeden durumu
    'Onaylandı' yaptı. Durum kapısı o an kart siparişini engellemediği için sipariş ödenmiş gibi
    göründü, ödeme yöntemi de kart olarak kaldı. Kök sebep ayrıca koda kapatıldı
    (orders.update_order_status: ödemesi alınmamış KART siparişi 'Onaylandı' yapılamaz).

    Burada YALNIZ bu siparişin ödeme yöntemi Havale/EFT'ye çekilir ve tahsilat personel onayıyla
    (11:31) hizalanır. Tutar/kalem/stok DEĞİŞMEZ. Olay günlüğüne düşülür."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.fix_w11874_pm_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        o = await _db.orders.find_one({"order_number": "W11874"},
                                      {"_id": 0, "id": 1, "payment_method": 1, "payment_status": 1,
                                       "status": 1, "total": 1})
        if not o:
            return
        # Emniyet: yalnız BEKLENEN bozuk durumdaysa düzelt (başkası düzelttiyse dokunma).
        if (o.get("payment_method") or "").lower() not in ("credit_card", "card", "kredi_karti",
                                                            "kart", "iyzico", "creditcard"):
            await _db.settings.update_one({"id": flag_id}, {"$set": {
                "id": flag_id, "done": True, "skipped": "ödeme yöntemi zaten kart değil",
                "current": {k: o.get(k) for k in ("payment_method", "payment_status", "status")},
                "at": _dt.now(_tz.utc).isoformat()}}, upsert=True)
            return
        now = _dt.now(_tz.utc).isoformat()
        before = {k: o.get(k) for k in ("payment_method", "payment_status", "status")}
        await _db.orders.update_one({"id": o["id"]}, {"$set": {
            "payment_method": "bank_transfer",
            "payment_method_raw": "Havale/EFT (yanlış yöntem düzeltildi)",
            "payment_status": "paid",
            "paid_at": now,
            "paid_by": "ilayda.aman@meluni.co",
            "payment_approved_via": "manual_correction_w11874",
            "updated_at": now}})
        try:
            from routes.orders import _log_order_event
            await _log_order_event(o["id"], "payment",
                                   "Ödeme yöntemi düzeltildi: Kredi Kartı → Havale/EFT "
                                   "(kart ödemesi başarısızdı; tahsilat havale ile yapıldı)",
                                   {"email": "sistem@duzeltme"}, {"before": before},
                                   order_number="W11874")
        except Exception as _le:
            logger.warning(f"[w11874] olay günlüğü yazılamadı: {_le}")
        await _db.settings.update_one({"id": flag_id}, {"$set": {
            "id": flag_id, "done": True, "order_id": o["id"], "before": before,
            "after": {"payment_method": "bank_transfer", "payment_status": "paid", "status": o.get("status")},
            "at": now}}, upsert=True)
        logger.warning(f"[w11874] ödeme yöntemi düzeltildi: {before} → havale/paid")
    except Exception as e:
        logger.error(f"[w11874] hata: {e}")


async def sale_min_discount_revert_outside_once() -> None:
    """TEK SEFERLİK: %30 kuralı, SALE vitrininde 'indirimli fiyat' yüzünden görünen ama İNDİRİM
    kategorisine ÜYE OLMAYAN ürünlere de uygulanmıştı (Mold Balon Pantolon, Yüksek Bel Kapri…).
    Kullanıcı: SALE = kategori üyeliği. Bu ürünlerin indirimli fiyatı price_change_log'daki eski
    değerine geri alınır; kategori üyeleri olduğu gibi kalır."""
    from routes.deps import db as _db
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.sale_min30_revert_outside_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        src = await _db.settings.find_one({"id": "migrations.sale_min30_v1"}, {"_id": 0, "sale_category_ids": 1, "done": 1})
        if not (src or {}).get("done"):
            return
        cat_ids = [str(x) for x in (src.get("sale_category_ids") or [])]
        reverted, kept = [], 0
        now = _dt.now(_tz.utc).isoformat()
        async for rec in _db.price_change_log.find({"batch": "migrations.sale_min30_v1", "reverted": {"$ne": True}}, {"_id": 0}):
            p = await _db.products.find_one({"id": rec.get("product_id")}, {"_id": 0, "id": 1, "name": 1, "sale_price": 1, "category_id": 1, "category_ids": 1})
            if not p:
                continue
            cats = {str(x) for x in (p.get("category_ids") or [])} | ({str(p.get("category_id"))} if p.get("category_id") else set())
            if cats & set(cat_ids):
                kept += 1
                continue
            # Kategori üyesi DEĞİL → eski indirimli fiyata dön (biz yazdığımız değer hâlâ duruyorsa)
            cur = float(p.get("sale_price") or 0)
            if abs(cur - float(rec.get("new_sale_price") or 0)) > 0.011:
                continue   # sonradan elle değiştirilmiş → dokunma
            old_sp = rec.get("old_sale_price")
            await _db.products.update_one({"id": p["id"]}, {"$set": {"sale_price": old_sp, "updated_at": now}})
            await _db.price_change_log.update_one({"product_id": rec["product_id"], "batch": "migrations.sale_min30_v1"},
                                                  {"$set": {"reverted": True, "reverted_at": now, "revert_reason": "İNDİRİM kategorisi üyesi değil"}})
            reverted.append({"product_id": p["id"], "name": p.get("name"), "from": rec.get("new_sale_price"), "to": old_sp})
        await _db.settings.update_one({"id": flag_id}, {"$set": {"id": flag_id, "done": True, "at": now,
                                                                 "reverted_count": len(reverted), "kept_members": kept, "reverted": reverted}}, upsert=True)
        logger.warning(f"[sale-min30-revert] geri alınan={len(reverted)} kategori üyesi kalan={kept}")
    except Exception as e:
        logger.error(f"[sale-min30-revert] hata: {e}")


async def trendyol_orphan_preview() -> None:
    """Açılışta KURU tarama: Trendyol'da olup sistemde olmayan ürünler (sıfırlama YAPMAZ).
    Sonuç settings.trendyol_orphan_zero_preview → /health.trendyol_orphan_preview."""
    from routes.deps import db as _db
    import asyncio as _aio
    await _aio.sleep(20)
    try:
        from routes.integrations_trendyol import _trendyol_orphan_zero_core
        res = await _trendyol_orphan_zero_core(dry_run=True)
        await _db.settings.update_one({"id": "trendyol_orphan_zero_preview"},
                                      {"$set": {"id": "trendyol_orphan_zero_preview", **res}}, upsert=True)
        logger.warning(f"[orphan-preview] tarandı={res.get('scanned')} aday={res.get('candidates_first_check')} onaylı={res.get('confirmed')}")
    except Exception as e:
        logger.error(f"[orphan-preview] hata: {e}")


async def seed_feed_custom_label_once() -> None:
    """Tek seferlik: 'google-merchant' feed'ine EN YENİLER kategorisi → custom_label_0=AW26 kuralı ekler
    (Google Ads'te reklam verilen sezon ürünlerini ayırt etmek için). Kural zaten varsa dokunmaz."""
    from routes.deps import db as _db
    from routes.products import generate_slug as _gs
    from datetime import datetime as _dt, timezone as _tz
    flag_id = "migrations.feed_custom_label_aw26_v1"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        feed = await _db.xml_feeds.find_one({"slug": "google-merchant"}, {"_id": 0})
        if not feed:
            logger.warning("[feed-label] google-merchant feed yok, kural eklenmedi")
            return
        en_id = None
        async for c in _db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1}):
            if (c.get("slug") or "").strip().lower() == "en-yeniler" or _gs(c.get("name") or "") == "en-yeniler":
                en_id = str(c.get("id"))
                break
        if not en_id:
            logger.warning("[feed-label] EN YENİLER kategorisi bulunamadı")
            return
        rules = [r for r in (feed.get("custom_labels") or []) if isinstance(r, dict)]
        if not any(int(r.get("label", -1) or 0) == 0 for r in rules):
            rules.append({"label": 0, "value": "AW26", "all": False, "category_ids": [en_id]})
            await _db.xml_feeds.update_one({"id": feed["id"]}, {"$set": {"custom_labels": rules}})
        await _db.settings.update_one({"id": flag_id}, {"$set": {"id": flag_id, "done": True, "category_id": en_id,
                                                                 "at": _dt.now(_tz.utc).isoformat()}}, upsert=True)
        logger.warning(f"[feed-label] google-merchant: custom_label_0=AW26 (kategori {en_id})")
    except Exception as e:
        logger.error(f"[feed-label] hata: {e}")


async def scrub_phone_once(raw_phone: str, reason: str = "") -> None:
    """Bir telefon numarasını TÜM kayıtlardan siler (üye, adres, sipariş, İYS izni, OTP, influencer)
    ve SMS kara listesine alır. Tek seferlik (bayraklı); sonuç sayaçları settings.phone_scrub_<tail>."""
    import re as _re
    from datetime import datetime as _dt, timezone as _tz
    from notification_service import normalize_phone_tr as _norm
    from routes.deps import db as _db
    norm = _norm(raw_phone)
    tail = norm[-10:] if len(norm) >= 10 else norm
    if not tail:
        return
    flag_id = f"phone_scrub_{tail}"
    try:
        if await _db.settings.find_one({"id": flag_id, "done": True}):
            return
        rx = {"$regex": _re.escape(tail) + r"\s*$"}
        now = _dt.now(_tz.utc).isoformat()
        counts = {}
        r = await _db.users.update_many({"phone": rx}, {"$set": {"phone": "", "phone_verified": False, "phone_scrubbed_at": now}})
        counts["users"] = r.modified_count
        r = await _db.addresses.update_many({"phone": rx}, {"$set": {"phone": "", "phone_scrubbed_at": now}})
        counts["addresses"] = r.modified_count
        for fld in ("shipping_address.phone", "billing_address.phone", "phone", "customer_phone"):
            r = await _db.orders.update_many({fld: rx}, {"$set": {fld: "", "phone_scrubbed_at": now}})
            counts[f"orders.{fld}"] = r.modified_count
        r = await _db.iys_consents.update_many({"phone": rx}, {"$set": {"phone": "", "phone_scrubbed_at": now}, "$pull": {"channels": "MESAJ"}})
        counts["iys_consents"] = r.modified_count
        r = await _db.otp_verifications.delete_many({"phone": rx})
        counts["otp_verifications"] = r.deleted_count
        for coll, fld in (("influencers", "phone"), ("influencers", "shipping_address.phone"), ("influencer_pr", "phone")):
            try:
                r = await getattr(_db, coll).update_many({fld: rx}, {"$set": {fld: "", "phone_scrubbed_at": now}})
                counts[f"{coll}.{fld}"] = r.modified_count
            except Exception:
                pass
        # KVKK: at-rest ŞİFRELİ telefonlar (security.contact_pii) düz değerle eşleşmez → HASH ile bulunur;
        # mühür alanları (_enc/_hash/_last2) silinir.
        try:
            from security.contact_pii import phone_hashes as _ph_hashes
            _hs = _ph_hashes(norm)
            if _hs:
                _uns = lambda f: {f"{f}_enc": "", f"{f}_hash": "", f"{f}_last2": ""}
                for coll, fld in (("users", "phone"), ("addresses", "phone"), ("influencers", "phone"),
                                  ("influencers", "shipping_address.phone"), ("influencer_pr", "phone")):
                    _set = {fld: "", "phone_scrubbed_at": now}
                    if coll == "users":
                        _set["phone_verified"] = False
                    r = await getattr(_db, coll).update_many({f"{fld}_hash": {"$in": _hs}},
                                                             {"$set": _set, "$unset": _uns(fld)})
                    counts[f"{coll}.{fld}_sealed"] = r.modified_count
        except Exception as _se:
            logger.warning(f"[phone-scrub] şifreli kayıt temizliği atlandı: {type(_se).__name__}")
        await _db.sms_suppressions.update_one({"phone": norm}, {"$set": {"phone": norm, "reason": reason, "created_at": now}}, upsert=True)
        await _db.settings.update_one({"id": flag_id}, {"$set": {"id": flag_id, "done": True, "at": now, "counts": counts}}, upsert=True)
        logger.warning(f"[phone-scrub] tamamlandı: {counts}")
    except Exception as e:
        logger.error(f"[phone-scrub] hata: {e}")
        try:
            await _db.settings.update_one({"id": flag_id}, {"$set": {"id": flag_id, "done": False, "error": str(e)[:200]}}, upsert=True)
        except Exception:
            pass


async def _stock_trace_block(q: str = "") -> dict:
    """SALT OKUNUR stok izi: bir ürünün stoğu BUGÜNKÜ değerine NASIL geldi?

    `q` = ürün adı parçası / stok kodu / barkod. Eşleşen ürünler için:
      • şu anki stok (ürün + varyant kırılımı)
      • db.stock_movements'taki TÜM hareketler (en eski→en yeni), delta ve aktörüyle
      • defter toplamı ile canlı stoğun karşılaştırması → AÇIKLANAMAYAN fark
      • bu ürünün barkodlarını içeren siparişlerin durum dağılımı
    HİÇBİR YAZMA YAPMAZ. Ticari veri taşıdığı için çağıran yer audit_key ister.
    """
    try:
        import re as _re_st
        if not q or len(str(q).strip()) < 2:
            return {"hata": "stock_q gerekli (ürün adı parçası / stok kodu / barkod)"}
        qs = str(q).strip()
        rx = {"$regex": _re_st.escape(qs), "$options": "i"}
        prods = await db.products.find(
            {"$or": [{"name": rx}, {"stock_code": rx}, {"barcode": qs},
                     {"variants.barcode": qs}, {"variants.stock_code": rx}]},
            {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "barcode": 1, "stock": 1,
             "variants": 1, "is_active": 1, "created_at": 1, "updated_at": 1},
        ).limit(4).to_list(4)
        if not prods:
            return {"hata": f"'{qs}' ile ürün bulunamadı"}

        out = []
        for p in prods:
            pid = str(p.get("id") or "")
            variants = p.get("variants") or []
            bcs = [str(v.get("barcode")) for v in variants if v.get("barcode")]
            if p.get("barcode"):
                bcs.append(str(p["barcode"]))
            vnow = [{"barkod": v.get("barcode"), "beden": v.get("size"),
                     "renk": v.get("color"), "stok": v.get("stock")} for v in variants]
            canli_toplam = sum(int(v.get("stock") or 0) for v in variants) if variants \
                else int(p.get("stock") or 0)

            # ── Hareket defteri (tüm şemalar: items / moves / v2) ──────────────
            movs = await db.stock_movements.find(
                {"$or": [{"items.product_id": pid}, {"moves.product_id": pid},
                         {"product_id": pid}, {"items.barcode": {"$in": bcs[:50]}},
                         {"moves.barcode": {"$in": bcs[:50]}}]},
                {"_id": 0},
            ).sort("created_at", 1).limit(4000).to_list(4000)

            satirlar, defter_delta, tip_ozet, tip_qty = [], 0, {}, {}
            for m in movs:
                rows = (m.get("items") or []) + (m.get("moves") or [])
                d, detay = 0, []
                for it in rows:
                    if not isinstance(it, dict):
                        continue
                    _bc = str(it.get("barcode") or "")
                    if it.get("product_id") and str(it["product_id"]) != pid and _bc not in bcs:
                        continue
                    if not it.get("product_id") and _bc and _bc not in bcs:
                        continue
                    _dl = it.get("delta")
                    # BAZI YAZICILAR 'qty' KULLANIYOR: pazaryeri iade (claim) geri-stok
                    # satırları delta yerine qty yazıyor (integrations_common.py:2198,
                    # integrations_trendyol.py:7037). $inc GERÇEKTEN çalışmış ama defter
                    # satırı 'delta' okuyan her yere 0 görünüyor. Burada doğru okunur;
                    # kaynaktaki alan adı ayrıca düzeltilmelidir.
                    if _dl is None and it.get("qty") is not None:
                        _dl = it.get("qty")
                    if _dl is None and it.get("old") is not None and it.get("new") is not None:
                        _dl = int(it["new"]) - int(it["old"])
                    try:
                        _dl = int(_dl or 0)
                    except Exception:
                        _dl = 0
                    d += _dl
                    detay.append({"barkod": _bc or None, "beden": it.get("size"),
                                  "delta": _dl, "once": it.get("old"), "sonra": it.get("new")})
                if any(isinstance(x, dict) and x.get("delta") is None
                       and x.get("qty") is not None for x in rows):
                    tip_qty[str(m.get("type") or "?")] = tip_qty.get(
                        str(m.get("type") or "?"), 0) + 1
                defter_delta += d
                _t = str(m.get("type") or m.get("action") or "?")
                _o = tip_ozet.setdefault(_t, {"adet": 0, "delta": 0})
                _o["adet"] += 1
                _o["delta"] += d
                satirlar.append({
                    "tarih": m.get("created_at"), "tip": _t,
                    "kaynak": m.get("source") or (m.get("sync_result") or {}).get("platform"),
                    "siparis": m.get("order_number") or m.get("order_id"),
                    "kim": (m.get("actor") or {}).get("email") or m.get("created_by") or "",
                    "toplam_delta": d, "kalemler": detay[:12],
                })

            # ── Bu ürünün barkodlarını içeren siparişler ───────────────────────
            sip_durum, sip_adet = {}, 0
            try:
                async for r in db.orders.aggregate([
                    {"$match": {"items.barcode": {"$in": bcs[:50]}}},
                    {"$group": {"_id": "$status", "n": {"$sum": 1}}},
                ], allowDiskUse=True):
                    sip_durum[str(r.get("_id") or "?")] = int(r.get("n") or 0)
                    sip_adet += int(r.get("n") or 0)
            except Exception as _oe:
                sip_durum = {"hata": str(_oe)[:120]}

            # ── SİPARİŞ-SİPARİŞ MUTABAKAT: hangi sipariş stoğu DÜŞÜRMEDİ? ─────
            # "Stok neden düşmedi?" sorusunun tek kesin cevabı burada: bu ürünü
            # içeren HER siparişi stok defteriyle eşleştirir, düşüm hareketi
            # OLMAYANLARI ay/kanal/statü kırılımıyla ve ADETİYLE gösterir.
            DUS = ["order_created", "order_imported", "manual_decrement",
                   "backfill_decrement", "influencer_seeding"]
            # Stoğu meşru şekilde DÜŞÜRMEMESİ gereken statüler (hiç rezerve edilmedi).
            YOK = {"cancelled", "cancel_refunded", "pending", "awaiting_payment",
                   "payment_failed", "payment_notified"}
            eksik = {"adet_siparis": 0, "adet_urun": 0, "ay": {}, "kanal": {},
                     "statu": {}, "ornekler": []}
            dusen = {"siparis": 0, "urun": 0}
            try:
                _oids = []
                async for o in db.orders.find(
                        {"items.barcode": {"$in": bcs[:50]}},
                        {"_id": 0, "id": 1, "order_number": 1, "status": 1,
                         "platform": 1, "marketplace": 1, "created_at": 1,
                         "marketplace_order_date": 1, "items.barcode": 1,
                         "items.quantity": 1}):
                    _oids.append(o)
                _ids = [str(o.get("id")) for o in _oids if o.get("id")]
                _has = set()
                for i in range(0, len(_ids), 4000):
                    async for mv in db.stock_movements.find(
                            {"order_id": {"$in": _ids[i:i + 4000]},
                             "type": {"$in": DUS}}, {"_id": 0, "order_id": 1}):
                        _has.add(str(mv.get("order_id")))
                for o in _oids:
                    _q = 0
                    for it in (o.get("items") or []):
                        if str((it or {}).get("barcode") or "") in bcs:
                            try:
                                _q += max(1, int(it.get("quantity") or 1))
                            except Exception:
                                _q += 1
                    if str(o.get("id")) in _has:
                        dusen["siparis"] += 1
                        dusen["urun"] += _q
                        continue
                    _st = str(o.get("status") or "?")
                    if _st in YOK:
                        continue          # zaten düşmemesi gerekiyordu
                    _ay = str(o.get("marketplace_order_date")
                              or o.get("created_at") or "")[:7] or "?"
                    _ch = (str(o.get("platform") or "").lower()
                           or str(o.get("marketplace") or "").lower() or "site")
                    eksik["adet_siparis"] += 1
                    eksik["adet_urun"] += _q
                    eksik["ay"][_ay] = eksik["ay"].get(_ay, 0) + _q
                    eksik["kanal"][_ch] = eksik["kanal"].get(_ch, 0) + _q
                    eksik["statu"][_st] = eksik["statu"].get(_st, 0) + _q
                    if len(eksik["ornekler"]) < 15:
                        eksik["ornekler"].append(
                            {"siparis": o.get("order_number"), "statu": _st,
                             "kanal": _ch, "tarih": _ay, "adet": _q})
            except Exception as _re2:
                eksik = {"hata": str(_re2)[:200]}

            out.append({
                "urun": {"id": pid, "ad": p.get("name"), "stok_kodu": p.get("stock_code"),
                         "aktif": p.get("is_active"), "olusturma": p.get("created_at"),
                         "son_guncelleme": p.get("updated_at")},
                "canli_stok": {"urun_alani": p.get("stock"), "varyant_toplami": canli_toplam,
                               "varyantlar": vnow},
                "defter": {"hareket_adedi": len(movs), "toplam_delta": defter_delta,
                           "alan_adi_qty_olan_hareketler": tip_qty,
                           "tip_ozeti": tip_ozet,
                           "aciklanamayan_fark": canli_toplam - defter_delta,
                           "not": ("Defter toplamı ile canlı stok arasındaki fark, DEFTERE "
                                   "YAZILMADAN yapılmış stok değişikliğidir (ör. eski manuel "
                                   "düzenleme, import, doğrudan DB yazımı).")},
                "siparisler": {"toplam": sip_adet, "durum_dagilimi": sip_durum,
                               "stogu_dusen": dusen,
                               "STOGU_DUSMEYEN": eksik},
                "hareketler": satirlar,
            })
        return {"sorgu": qs, "urun_sayisi": len(out), "sonuc": out}
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _order_lookup(nos: str = "") -> dict:
    """SALT OKUNUR: verilen sipariş numaralarını bizim kayıtlarımızla eşleştir.

    "Pazaryeri listesindeki her sipariş bizde var mı?" sorusunun sipariş-sipariş
    cevabı. Her numara için: bizde var mı, statüsü ne, hangi tarihle kayıtlı.
    Saat kayması teşhisi için kayıtlı ham tarih de döner (dönüştürülmeden).
    """
    try:
        _l = [x.strip() for x in str(nos or "").replace(";", ",").split(",") if x.strip()]
        if not _l:
            return {"hata": "orders_q gerekli (virgülle sipariş numaraları)"}
        _l = _l[:600]
        bulunan = {}
        async for o in db.orders.find(
                {"order_number": {"$in": _l}},
                {"_id": 0, "order_number": 1, "status": 1, "total": 1, "platform": 1,
                 "marketplace_order_date": 1, "created_at": 1, "cancelled_at": 1,
                 "items.quantity": 1}):
            _q = 0
            for it in (o.get("items") or []):
                try:
                    _q += max(1, int((it or {}).get("quantity") or 1))
                except Exception:
                    _q += 1
            bulunan[str(o.get("order_number"))] = {
                "statu": o.get("status"), "tutar": o.get("total"), "adet": _q,
                "kayitli_siparis_tarihi": o.get("marketplace_order_date"),
                "olusturma": o.get("created_at"), "iptal_tarihi": o.get("cancelled_at"),
                "platform": o.get("platform"),
            }
        eksik = [n for n in _l if n not in bulunan]
        _st = {}
        _adet = 0
        _tut = 0.0
        for v in bulunan.values():
            _st[str(v.get("statu"))] = _st.get(str(v.get("statu")), 0) + 1
            _adet += int(v.get("adet") or 0)
            try:
                _tut += float(v.get("tutar") or 0)
            except Exception:
                pass
        return {
            "sorgulanan": len(_l), "bulunan": len(bulunan), "EKSIK": len(eksik),
            "eksik_liste": eksik[:60],
            "statu_dagilimi": _st,
            "toplam_urun_adedi": _adet,
            "toplam_tutar": round(_tut, 2),
            "ornek_kayitlar": dict(list(bulunan.items())[:8]),
        }
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _marketplace_clock_block(days: int = 60) -> dict:
    """SALT OKUNUR: her kanalın kayıtlı sipariş saatinin UTC dağılımı.

    Trendyol'da kanıtlandı: pazaryeri ms-epoch'u TÜRKİYE yerel saatini taşıyordu, biz
    UTC sanıp kaydediyorduk → raporlar +3 saat kayıyor, 21:00 sonrası siparişler ertesi
    güne/aya yazılıyordu. AYNI hatanın Hepsiburada/Amazon/Temu'da olup olmadığı ancak
    ölçülerek anlaşılır.

    Ölçüt: Türkiye'de alışveriş saat 20:00–23:00 TR arasında zirve yapar; bu, doğru
    kaydedilmiş veride UTC 17:00–20:00 demektir. Bir kanalın zirvesi UTC 20:00–23:00'te
    çıkıyorsa saat TR yerel olarak kaydediliyor (3 saat kayma) demektir.

    Ayrıca her kanalda 'marketplace_order_date' alanının doluluk oranını verir: alan boşsa
    rapor 'created_at'e düşer, o da senkron anı olabilir (gerçek sipariş saati değil).
    Hiçbir şey YAZMAZ.
    """
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        _since = (_dt.now(_tz.utc) - _td(days=max(1, int(days or 60)))).isoformat()
        _ed = {"$ifNull": ["$marketplace_order_date", "$created_at"]}
        rows = await db.orders.aggregate([
            {"$addFields": {"_ed": _ed}},
            {"$match": {"$expr": {"$gte": [{"$toString": "$_ed"}, _since]}}},
            {"$addFields": {
                "_saat": {"$toInt": {"$substr": [{"$toString": "$_ed"}, 11, 2]}},
                "_kanal": {"$ifNull": ["$platform", "site"]},
                "_mod_var": {"$cond": [
                    {"$in": [{"$ifNull": ["$marketplace_order_date", ""]}, [None, ""]]}, 0, 1]},
            }},
            {"$group": {"_id": {"k": "$_kanal", "s": "$_saat"},
                        "n": {"$sum": 1}, "mod": {"$sum": "$_mod_var"}}},
        ], allowDiskUse=True).to_list(None)
        out: dict = {}
        for r in rows:
            k = str((r.get("_id") or {}).get("k") or "site") or "site"
            s = int((r.get("_id") or {}).get("s") or 0)
            d = out.setdefault(k, {"toplam": 0, "mod_dolu": 0, "saat": {}})
            d["toplam"] += int(r.get("n") or 0)
            d["mod_dolu"] += int(r.get("mod") or 0)
            d["saat"][s] = d["saat"].get(s, 0) + int(r.get("n") or 0)
        for k, d in out.items():
            h = d["saat"]
            # En yoğun 3 saatlik pencerenin başlangıcı (UTC)
            best, best_n = 0, -1
            for st in range(24):
                tot = sum(h.get((st + i) % 24, 0) for i in range(3))
                if tot > best_n:
                    best, best_n = st, tot
            d["zirve_3saat_utc"] = f"{best:02d}:00-{(best + 3) % 24:02d}:00"
            d["zirve_3saat_tr"] = f"{(best + 3) % 24:02d}:00-{(best + 6) % 24:02d}:00"
            d["saat"] = dict(sorted(h.items()))
            d["mod_dolulugu"] = (f"{round(100 * d['mod_dolu'] / d['toplam'])}%"
                                 if d["toplam"] else "-")
            d["deger"] = ("BEKLENEN (TR akşam zirvesi doğru yerde)"
                          if 15 <= best <= 19 else
                          "ŞÜPHELİ — saat TR yerel kaydediliyor olabilir (3 saat kayma)"
                          if 18 <= (best + 3) % 24 <= 22 and best >= 19 else
                          "İNCELE")
        # ── Trendyol saat göçünün KAPSAMI (geçmiş aylar dahil mi?) ───────────
        # Göç ay süzgeci kullanmaz: marketplace_order_date'i olan TÜM Trendyol
        # siparişlerini düzeltir. Yine de "eski aylar atlandı mı" sorusu ancak
        # sayımla cevaplanır. Kalan varsa hangi aylarda olduğu da dökülür.
        kapsam: dict = {}
        try:
            _q_ty = {"platform": "trendyol"}
            kapsam["trendyol_toplam"] = await db.orders.count_documents(_q_ty)
            kapsam["duzeltildi"] = await db.orders.count_documents(
                {**_q_ty, "ty_date_utc_fixed": True})
            # Göçten SONRA gelen siparişler zaten doğru yazılıyor ve damga taşımıyor;
            # onları "kalan" saymak yanlış alarm üretirdi. Bu yüzden eşik, göçün
            # uygulandığı andır — yalnız ondan ÖNCEKİ kayıtlar denetlenir.
            _mig = await db.settings.find_one({"id": "ty_date_utc_fix_v1"},
                                              {"_id": 0, "applied_at": 1}) or {}
            _esik = str(_mig.get("applied_at") or "")
            _kq = {**_q_ty, "ty_date_utc_fixed": {"$ne": True},
                   "marketplace_order_date": {"$exists": True, "$nin": ["", None]}}
            if _esik:
                _kq["marketplace_order_date"] = {"$exists": True, "$nin": ["", None],
                                                 "$lt": _esik}
            kapsam["KALAN_tarihi_olan"] = await db.orders.count_documents(_kq)
            kapsam["kalan_esigi"] = _esik or "(göç kaydı yok)"
            kapsam["goc_sonrasi_yeni"] = await db.orders.count_documents(
                {**_q_ty, "ty_date_utc_fixed": {"$ne": True},
                 "marketplace_order_date": {"$exists": True, "$gte": _esik}}) if _esik else 0
            kapsam["siparis_tarihi_YOK"] = await db.orders.count_documents(
                {**_q_ty, "$or": [{"marketplace_order_date": {"$exists": False}},
                                  {"marketplace_order_date": {"$in": ["", None]}}]})
            _ay: dict = {}
            async for r in db.orders.aggregate([
                {"$match": {**_q_ty, "ty_date_utc_fixed": True}},
                {"$group": {"_id": {"$substr": [{"$toString": "$marketplace_order_date"}, 0, 7]},
                            "n": {"$sum": 1}}},
            ]):
                _ay[str(r.get("_id") or "?")] = int(r.get("n") or 0)
            kapsam["duzeltilen_ay_dagilimi"] = dict(sorted(_ay.items()))
            _ay2: dict = {}
            async for r in db.orders.aggregate([
                {"$match": {**_q_ty, "$or": [
                    {"marketplace_order_date": {"$exists": False}},
                    {"marketplace_order_date": {"$in": ["", None]}}]}},
                {"$group": {"_id": {"$substr": [{"$toString": "$created_at"}, 0, 7]},
                            "n": {"$sum": 1}}},
            ]):
                _ay2[str(r.get("_id") or "?")] = int(r.get("n") or 0)
            kapsam["tarihi_olmayanlarin_ay_dagilimi"] = dict(sorted(_ay2.items()))
            kapsam["ticimax_kopyasi_trendyol"] = await db.orders.count_documents(
                {**_q_ty, "imported_from": "ticimax_history"})
            # Trendyol API'si sınırlı geriye gidiyor; eski aylarda kayıtlarımızın
            # büyük kısmı Ticimax'tan TAŞINAN geçmiş veri olabilir. O aylarda
            # "API ile birebir tutuyor mu" sorusu ancak bu kırılımla cevaplanır.
            _tay: dict = {}
            async for r in db.orders.aggregate([
                {"$match": {**_q_ty}},
                {"$addFields": {"_ay": {"$substr": [{"$toString": {
                    "$ifNull": ["$marketplace_order_date", "$created_at"]}}, 0, 7]},
                    "_tic": {"$cond": [{"$eq": ["$imported_from", "ticimax_history"]}, 1, 0]}}},
                {"$group": {"_id": "$_ay", "toplam": {"$sum": 1},
                            "ticimax": {"$sum": "$_tic"}}},
            ]):
                _tay[str(r.get("_id") or "?")] = {
                    "toplam": int(r.get("toplam") or 0),
                    "ticimax_tasima": int(r.get("ticimax") or 0),
                    "api_kaynakli": int(r.get("toplam") or 0) - int(r.get("ticimax") or 0)}
            kapsam["ay_bazinda_kaynak"] = dict(sorted(_tay.items()))
            kapsam["not"] = ("'KALAN_tarihi_olan' 0 DEĞİLSE göç eksik kalmıştır. "
                             "'siparis_tarihi_YOK' kayıtlar pazaryeri tarihi taşımadığı "
                             "için rapor created_at'e düşer — bunlar kaydırılmadı "
                             "(kaydırmak uydurma olurdu).")
        except Exception as _ke:
            kapsam = {"hata": f"{type(_ke).__name__}: {str(_ke)[:200]}"}

        return {"gun": int(days), "kanallar": out,
                "ty_duzeltme_kapsami": kapsam,
                "olcut": ("TR akşam zirvesi 20:00-23:00 TR = 17:00-20:00 UTC. "
                          "'zirve_3saat_tr' 20:00-23:00 civarındaysa saat DOĞRU. "
                          "Bir kanalın zirvesi TR 23:00-02:00'ye kaymışsa o kanalın "
                          "tarihleri 3 saat ileri kaydediliyor demektir."),
                "mod_notu": ("mod_dolulugu = marketplace_order_date dolu olan siparişlerin "
                             "oranı. %0 ise rapor created_at'e düşer; created_at senkron "
                             "anıysa sipariş saati gerçek değildir.")}
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _ty_siparis_sorgu(nos: str = "") -> dict:
    """SALT OKUNUR: verilen sipariş numaralarını DOĞRUDAN Trendyol API'sine sorar.

    Bizde olup Trendyol'un dönem listesinde görünmeyen siparişlerin gerçek durumu
    (Trendyol'daki sipariş tarihi, paket statüsü, tutarı) ancak Trendyol'a numarayla
    sorularak öğrenilir. Yalnız GET; hiçbir kayıt değiştirilmez, sipariş çekme
    akışına dokunulmaz.
    """
    try:
        _l = [x.strip() for x in str(nos or "").replace(";", ",").split(",") if x.strip()][:20]
        if not _l:
            return {"hata": "ty_siparis_q gerekli"}
        from routes.integrations_trendyol import get_trendyol_config
        from routes.integrations_common import _ms_to_iso
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from trendyol_client import TrendyolClient
        cfg = await get_trendyol_config()
        cli = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                             api_secret=cfg["api_secret"], mode=cfg["mode"])
        out = {}
        for _n in _l:
            try:
                resp = await cli.get_orders(order_number=_n, size=50, page=0)
                pk = (resp or {}).get("content") or []
                out[_n] = {
                    "trendyolda_BULUNDU": bool(pk),
                    "paketler": [{
                        "statu": p.get("shipmentPackageStatus") or p.get("status"),
                        "siparis_tarihi_utc": _ms_to_iso(p.get("orderDate")),
                        "tutar": p.get("totalPrice"),
                        "adet": sum(int((ln or {}).get("quantity") or 1)
                                    for ln in (p.get("lines") or [])),
                    } for p in pk],
                }
            except Exception as _qe:
                out[_n] = {"hata": f"{type(_qe).__name__}: {str(_qe)[:160]}"}
            _b = await db.orders.find_one(
                {"order_number": _n},
                {"_id": 0, "status": 1, "total": 1, "created_at": 1,
                 "marketplace_order_date": 1, "imported_from": 1, "source": 1})
            out[_n]["bizde"] = _b
        return out
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _iptal_tarih_teshis(mode: str = "1") -> dict:
    """SALT OKUNUR: şüpheli iptal tarihlerinin (cancelled_at) KURU ÇALIŞTIRMA teşhisi.

    Kök neden (2026-09-23 ~13:55 UTC): Trendyol statü geçişi ZATEN iptal olan eski
    siparişlere de 'şimdi' damgası bastı (cancelled_at_source=trendyol_status_pass).
    Bu blok, tahmini/şüpheli kaynaklı kayıtları damga zamanına göre KÜMELER ve her
    sipariş için kanıta dayalı DOĞRU tarih önerir. HİÇBİR ŞEY YAZMAZ — veri onarımı
    ayrıca, kullanıcı onayıyla yapılacak; bu onun kuru çalıştırmasıdır.

    Kanıt sırası (en güvenilirden):
      1) Trendyol paketinin packageHistories Cancelled/UnSupplied zamanı (canlı GET;
         yalnız mode 'ty' / 'ty<N>' ile, en çok N≤150 sipariş),
      2) Trendyol lastModifiedDate (paket iptal statüsündeyken; canlı GET),
      3) status_history içindeki cancelled/cancel_refunded girişi (en erkeni),
      4) stock_movements iptal/iade-atla hareketi (en erkeni),
      5) siparişte saklı marketplace_last_modified (pazaryeri statüsü iptalse).
    Damgayla ±15 dk içindeki kanıt BAĞIMSIZ sayılmaz (aynı hatalı turda üretilmiş
    olabilir) → 'damga_ile_ayni' olarak ayrı raporlanır, öneri üretmez.
    Aylar TR yerel saatine göre (UTC+3) hesaplanır.
    """
    try:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        import asyncio as _aio
        _SRC = ["trendyol_status_pass", "trendyol_reconcile", "trendyol_cron",
                "updated_at_fallback"]
        _CST = ("cancelled", "cancel_refunded")
        _MV = ["order_cancelled", "auto_cancel_expired", "havale_auto_cancel",
               "restock_skipped_no_deduction"]
        _TOL = _td(minutes=15)

        def _p(s):
            try:
                s = str(s or "").strip()
                if not s:
                    return None
                d = _dt.fromisoformat(s.replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=_tz.utc)
                return d.astimezone(_tz.utc)
            except Exception:
                return None

        def _tr_ay(s):
            d = _p(s)
            return (d + _td(hours=3)).strftime("%Y-%m") if d else ""

        def _kume(s):          # 10 dakikalık UTC kovası: "2026-09-23 13:5x"
            d = _p(s)
            return (d.strftime("%Y-%m-%d %H:%M")[:15] + "x") if d else "?"

        # ── 1) Şüpheli kayıtlar ──────────────────────────────────────────────
        rows = []
        async for o in db.orders.find(
                {"cancelled_at_source": {"$in": _SRC}},
                {"_id": 0, "id": 1, "order_number": 1, "platform": 1, "status": 1,
                 "cancelled_at": 1, "cancelled_at_source": 1, "created_at": 1,
                 "marketplace_order_date": 1, "marketplace_status": 1,
                 "marketplace_last_modified": 1, "trendyol_status_raw": 1,
                 "status_history.status": 1, "status_history.at": 1}).limit(20000):
            rows.append(o)
        # Küme süzgeci: mode "ty150@2026-09-23T13:5" → yalnız cancelled_at bu önekle
        # başlayan kayıtlar (satır sınırına ve canlı GET kotasına o küme girsin).
        _onek = ""
        if "@" in str(mode or ""):
            mode, _onek = str(mode).split("@", 1)
            _onek = _onek.strip().replace(" ", "T")
        if _onek:
            rows = [o for o in rows if str(o.get("cancelled_at") or "").replace(" ", "T").startswith(_onek)]
        if not rows:
            return {"supheli_kayit": 0, "not": "şüpheli kaynaklı cancelled_at yok",
                    "kume_oneki": _onek}

        # ── 2) Hareket defteri kanıtı (toplu) ────────────────────────────────
        _ids = [str(o.get("id")) for o in rows if o.get("id")]
        _nums = [str(o.get("order_number")) for o in rows if o.get("order_number")]
        mv_id, mv_no = {}, {}
        for i in range(0, max(len(_ids), len(_nums)), 3000):
            _q = []
            if _ids[i:i + 3000]:
                _q.append({"order_id": {"$in": _ids[i:i + 3000]}})
            if _nums[i:i + 3000]:
                _q.append({"order_number": {"$in": _nums[i:i + 3000]}})
            if not _q:
                continue
            async for m in db.stock_movements.find(
                    {"$or": _q, "type": {"$in": _MV}},
                    {"_id": 0, "order_id": 1, "order_number": 1, "created_at": 1}):
                _c = str(m.get("created_at") or "")
                if not _c:
                    continue
                for _k, _d in ((m.get("order_id"), mv_id), (m.get("order_number"), mv_no)):
                    _k = str(_k or "")
                    if _k and (_k not in _d or _c < _d[_k]):
                        _d[_k] = _c

        # ── 3) (İsteğe bağlı) Trendyol canlı GET: packageHistories ───────────
        ty_ev, ty_err = {}, []
        _m = str(mode or "").strip().lower()
        if _m.startswith("ty"):
            try:
                _n = int(_m[2:] or 40)
            except Exception:
                _n = 40
            _n = max(1, min(_n, 150))
            _hedef = [str(o.get("order_number")) for o in rows
                      if str(o.get("platform") or "") == "trendyol" and o.get("order_number")]
            # En kalabalık kümeden başla (asıl hatalı tur) — ilk N sipariş.
            _kc = {}
            for o in rows:
                _kc[_kume(o.get("cancelled_at"))] = _kc.get(_kume(o.get("cancelled_at")), 0) + 1
            _byn = {str(o.get("order_number")): o for o in rows}
            _hedef.sort(key=lambda n: -_kc.get(_kume((_byn.get(n) or {}).get("cancelled_at")), 0))
            _hedef = list(dict.fromkeys(_hedef))[:_n]
            try:
                from routes.integrations_trendyol import get_trendyol_config, _ty_cancel_time_iso
                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
                from trendyol_client import TrendyolClient
                cfg = await get_trendyol_config()
                cli = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                                     api_secret=cfg["api_secret"], mode=cfg["mode"])
                _sem = _aio.Semaphore(5)

                async def _one(num):
                    async with _sem:
                        try:
                            r = await cli.get_orders(order_number=num, size=50, page=0)
                            best = ("", "")
                            for pk in ((r or {}).get("content") or []):
                                if str(pk.get("orderNumber") or "") != num:
                                    continue
                                t, s = _ty_cancel_time_iso(pk)
                                if t and (not best[0] or t < best[0]):
                                    best = (t, s)
                            if best[0]:
                                ty_ev[num] = best
                        except Exception as _qe:
                            ty_err.append([num, f"{type(_qe).__name__}: {str(_qe)[:80]}"])
                await _aio.wait_for(_aio.gather(*[_one(x) for x in _hedef]), timeout=60)
            except Exception as _te:
                ty_err.append(["genel", f"{type(_te).__name__}: {str(_te)[:120]}"])

        # ── 4) Sipariş başına öneri ──────────────────────────────────────────
        kumeler, tasima, kaynak_say = {}, {}, {}
        ornek = []
        oneri_var = ayni = kanitsiz = ay_degisir = 0
        for o in rows:
            _ca = str(o.get("cancelled_at") or "")
            _cad = _p(_ca)
            _od = str(o.get("marketplace_order_date") or o.get("created_at") or "")
            _num = str(o.get("order_number") or "")
            adaylar = []
            if _num in ty_ev:
                adaylar.append((ty_ev[_num][0], "trendyol_" + ty_ev[_num][1]))
            _sh = ""
            for h in (o.get("status_history") or []):
                if str((h or {}).get("status") or "") in _CST:
                    _at = str((h or {}).get("at") or "")
                    if _at and (not _sh or _at < _sh):
                        _sh = _at
            if _sh:
                adaylar.append((_sh, "status_history"))
            _mv = mv_id.get(str(o.get("id") or "")) or mv_no.get(_num) or ""
            if _mv:
                adaylar.append((_mv, "stock_movement"))
            if (str(o.get("marketplace_status") or o.get("trendyol_status_raw") or "")
                    in ("Cancelled", "UnSupplied") and o.get("marketplace_last_modified")):
                adaylar.append((str(o["marketplace_last_modified"]), "marketplace_last_modified"))
            # İLK GEÇERLİ (en güvenilir) kanıt karar verir; damgayı teyit ediyorsa
            # daha zayıf kanıta geçilmez.
            oneri, oneri_src, durum = "", "", "kanit_yok"
            for _t, _s in adaylar:
                _td0 = _p(_t)
                if not _td0:
                    continue
                if _cad and abs(_td0 - _cad) <= _TOL:
                    durum = "damga_ile_ayni"
                else:
                    oneri, oneri_src, durum = _t, _s, "oneri"
                break
            k = _kume(_ca)
            b = kumeler.setdefault(k, {"adet": 0, "kaynak": {}, "siparis_tarihi_min": "",
                                       "siparis_tarihi_max": "", "siparis_ayi": {},
                                       "oneri": 0, "damga_ile_ayni": 0, "kanit_yok": 0})
            b["adet"] += 1
            _src = str(o.get("cancelled_at_source") or "")
            b["kaynak"][_src] = b["kaynak"].get(_src, 0) + 1
            if _od:
                _o19 = _od[:19]
                if not b["siparis_tarihi_min"] or _o19 < b["siparis_tarihi_min"]:
                    b["siparis_tarihi_min"] = _o19
                if not b["siparis_tarihi_max"] or _o19 > b["siparis_tarihi_max"]:
                    b["siparis_tarihi_max"] = _o19
                _oa = _tr_ay(_od)
                b["siparis_ayi"][_oa] = b["siparis_ayi"].get(_oa, 0) + 1
            b[durum] += 1
            if durum == "oneri":
                oneri_var += 1
                kaynak_say[oneri_src] = kaynak_say.get(oneri_src, 0) + 1
                _a0, _a1 = _tr_ay(_ca), _tr_ay(oneri)
                if _a0 != _a1:
                    ay_degisir += 1
                    _kk = f"{_a0} -> {_a1}"
                    tasima[_kk] = tasima.get(_kk, 0) + 1
            elif durum == "damga_ile_ayni":
                ayni += 1
            else:
                kanitsiz += 1
            if len(ornek) < 400:
                ornek.append([_num, str(o.get("platform") or ""), str(o.get("status") or ""),
                              _od[:19], _ca[:19], _src, oneri[:19], oneri_src, durum])
        _ks = sorted(kumeler.items(), key=lambda kv: -kv[1]["adet"])
        return {
            "supheli_kayit": len(rows),
            "kume_oneki": _onek,
            "kaynak_filtresi": _SRC,
            "trendyol_canli": ({"sorgulanan_mod": _m, "bulunan": len(ty_ev),
                                "hatalar": ty_err[:20]} if _m.startswith("ty")
                               else "kapalı (iptal_tarih_q=ty veya ty<N> ile aç, N≤150)"),
            "ozet": {"oneri_var": oneri_var, "damga_ile_ayni": ayni, "kanit_yok": kanitsiz,
                     "ay_degistirecek": ay_degisir, "oneri_kaynaklari": kaynak_say},
            "ay_tasima_tr": dict(sorted(tasima.items(), key=lambda kv: -kv[1])),
            "kumeler": [dict(v, kume_utc=k) for k, v in _ks[:25]],
            "diger_kume_adedi": max(0, len(_ks) - 25),
            "siparisler_kolonlar": ["no", "platform", "statu", "siparis_tarihi",
                                    "mevcut_cancelled_at", "mevcut_kaynak",
                                    "onerilen_tarih", "onerilen_kaynak", "durum"],
            "siparisler": ornek,
            "siparisler_kirpildi": len(rows) > len(ornek),
            "not": ("SALT OKUNUR — hiçbir kayıt değiştirilmedi. 'damga_ile_ayni' = kanıt "
                    "damgayla ±15 dk içinde (gerçek geçiş olabilir ya da aynı hatalı turda "
                    "üretilmiş olabilir; Trendyol canlı kanıtıyla ayırt edin)."),
        }
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _iade_listesi(start_date: str = "", end_date: str = "") -> dict:
    """SALT OKUNUR: dönemdeki iade belgelerimizin KOMPAKT dökümü.

    Trendyol'un "İadeleriniz" Excel'iyle iade iade kıyas için iki kaynak:
      • gider_pusulasi — raporun İADE saydığı otantik belge (numaralı = kesinleşmiş)
      • trendyol_claims — Trendyol'dan senkronlanan iade talepleri ve kalem statüleri
    Aralık, pusulanın kesildiği / talebin açıldığı / onaylandığı tarihlerden HERHANGİ
    biri aralıktaysa kaydı içerir (haziran talebinin pusulası temmuzda kesilmiş olabilir).
    Müşteri kişisel verisi DÖNMEZ; yalnız sipariş no, tarih, tutar, barkod, adet.
    """
    try:
        from routes.reports import _iso_range
        s, e = _iso_range(start_date or None, end_date or None)
        gp = []
        async for g in db.gider_pusulasi.find(
                {"created_at": {"$gte": s, "$lte": e}},
                {"_id": 0, "order_number": 1, "number": 1, "display_number": 1,
                 "created_at": 1, "totals.net": 1, "items.barcode": 1,
                 "items.quantity": 1, "source": 1}):
            gp.append([str(g.get("order_number") or ""),
                       g.get("number") or g.get("display_number") or "",
                       str(g.get("created_at") or "")[:19],
                       (g.get("totals") or {}).get("net"),
                       [[str((i or {}).get("barcode") or ""), (i or {}).get("quantity")]
                        for i in (g.get("items") or [])],
                       g.get("source") or ""])
        cl = []
        async for c in db.trendyol_claims.find(
                {"$or": [{"created_date": {"$gte": s, "$lte": e}},
                         {"return_approved_at": {"$gte": s, "$lte": e}}]},
                {"_id": 0, "order_number": 1, "claim_id": 1, "claim_status": 1,
                 "claim_type": 1, "created_date": 1, "return_approved_at": 1,
                 "refund_amount": 1, "items": 1}):
            _it = []
            for i in (c.get("items") or []):
                i = i or {}
                _it.append([str(i.get("barcode") or ""),
                            i.get("quantity") or 1,
                            i.get("status") or i.get("claim_item_status") or ""])
            cl.append([str(c.get("order_number") or ""), str(c.get("claim_id") or ""),
                       c.get("claim_status") or "", c.get("claim_type") or "",
                       str(c.get("created_date") or "")[:19],
                       str(c.get("return_approved_at") or "")[:19],
                       c.get("refund_amount"), _it])
        return {"aralik": [s, e],
                "pusula_alanlar": ["siparis", "kocan_no", "tarih", "net", "kalemler", "kaynak"],
                "pusulalar": gp,
                "talep_alanlar": ["siparis", "claim_id", "statu", "tip", "acilis",
                                  "onay", "iade_tutari", "kalemler(barkod,adet,statu)"],
                "talepler": cl}
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _ty_ay_listesi(start_date: str = "", end_date: str = "") -> dict:
    """SALT OKUNUR: dönemdeki Trendyol siparişlerimizin KOMPAKT listesi.

    Pazaryerinin kendi Excel'iyle SİPARİŞ SİPARİŞ, iki yönlü kıyas için: Excel'de olup
    bizde olmayan ve bizde olup Excel'de olmayan siparişler ancak bizim tam listemizle
    görülebilir. Rapor ekranıyla AYNI nüfus: etkin sipariş tarihi + ticimax kopya
    elemesi + sipariş no başına tek belge. Yalnız no/statü/tutar/tarih/adet döner;
    müşteri kişisel verisi DÖNMEZ.
    """
    try:
        from routes.reports import _iso_range
        from routes.report_dedup import (merge_match as _mm, canonical_order_stages as _cos,
                                         effective_order_date_match as _eodm,
                                         load_dup_order_numbers as _ldc)
        try:
            await _ldc()
        except Exception:
            pass
        s, e = _iso_range(start_date or None, end_date or None)
        out = []
        async for o in db.orders.aggregate([
            {"$match": _mm({"$and": [_eodm(s, e), {"platform": "trendyol"}]})},
            *_cos(),
            {"$project": {"_id": 0, "order_number": 1, "status": 1, "total": 1,
                          "marketplace_order_date": 1, "created_at": 1,
                          "adet": {"$sum": {"$map": {"input": {"$ifNull": ["$items", []]},
                                                     "as": "i", "in": {"$ifNull": ["$$i.quantity", 1]}}}}}},
        ], allowDiskUse=True):
            try:
                _t = float(o.get("total") or 0)
                _t = _t if _t == _t and abs(_t) != float("inf") else 0.0
            except Exception:
                _t = 0.0
            out.append([str(o.get("order_number") or ""), str(o.get("status") or ""),
                        round(_t, 2),
                        str(o.get("marketplace_order_date") or o.get("created_at") or "")[:19],
                        int(o.get("adet") or 0)])
        return {"aralik": [s, e], "adet": len(out),
                "alanlar": ["siparis_no", "statu", "tutar", "tarih_utc", "urun_adedi"],
                "siparisler": out}
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _pusula_lookup(nos: str) -> dict:
    """SALT OKUNUR: verilen sipariş numaralarına kesilmiş gider pusulalarını döker.

    "Aynı siparişe üç pusula" mükerrer mi, yoksa üç ayrı kısmi iade mi? Bu ancak
    belgelerin KENDİLERİNE bakılarak anlaşılır: koçan numaraları farklı mı, aynı gün
    mü kesilmiş, kalemleri aynı mı. Rapordan iade düşmeden önce bu görülmeli.
    """
    try:
        _nos = [x.strip() for x in str(nos or "").replace(";", ",").split(",") if x.strip()]
        if not _nos:
            return {"hata": "pusula_q gerekli (virgülle sipariş numaraları)"}
        out: dict = {}
        for _n in _nos[:25]:
            _belgeler = []
            async for g in db.gider_pusulasi.find(
                    {"order_number": _n},
                    {"_id": 0, "number": 1, "display_number": 1, "created_at": 1,
                     "source": 1, "totals": 1, "items": 1, "order_number": 1}):
                _it = []
                for i in (g.get("items") or []):
                    _it.append({"barkod": (i or {}).get("barcode"),
                                "ad": str((i or {}).get("name") or "")[:40],
                                "adet": (i or {}).get("quantity")})
                _belgeler.append({
                    "kocan_no": g.get("number") or g.get("display_number"),
                    "tarih": str(g.get("created_at") or "")[:19],
                    "kaynak": g.get("source"),
                    "net": (g.get("totals") or {}).get("net"),
                    "kalemler": _it,
                })
            _belgeler.sort(key=lambda x: str(x.get("tarih") or ""))
            _o = await db.orders.find_one(
                {"order_number": _n},
                {"_id": 0, "order_number": 1, "status": 1, "platform": 1, "total": 1,
                 "created_at": 1, "marketplace_order_date": 1, "items": 1})
            out[_n] = {
                "pusula_sayisi": len(_belgeler),
                "pusulalar": _belgeler,
                "siparis_BULUNDU": bool(_o),
                "siparis": ({"statu": _o.get("status"), "platform": _o.get("platform"),
                             "tutar": _o.get("total"),
                             "tarih": str(_o.get("marketplace_order_date")
                                          or _o.get("created_at") or "")[:19],
                             "kalem_sayisi": len(_o.get("items") or [])} if _o else None),
            }
        return out
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _bozuk_tutar_block() -> dict:
    """SALT OKUNUR: tutarı GEÇERSİZ SAYI (NaN/Infinity) olan siparişleri bulur.

    MongoDB bu değerleri saklayabilir; toplandıklarında sonucun tamamı NaN olur.
    Haziran denetimi böyle çöküyordu (iptal toplamı → net → ortalama sepet → yanıt
    JSON'a yazılamıyor → 500). Raporlar artık bozuk değeri 0 sayıyor, ama KAYIT
    hâlâ bozuk; asıl tutarın ne olması gerektiğine yalnız işletme karar verebilir,
    bu yüzden burada sadece LİSTELENİR, değiştirilmez.
    """
    try:
        _nan = float("nan")
        _inf = float("inf")
        out: dict = {"alanlar": {}, "siparisler": []}
        _gorulen = set()
        for _alan in ("total", "subtotal", "shipping_cost", "discount_amount",
                      "partial_cancel_amount"):
            _q = {"$or": [{_alan: _nan}, {_alan: _inf}, {_alan: -_inf}]}
            _n = await db.orders.count_documents(_q)
            if not _n:
                continue
            out["alanlar"][_alan] = _n
            async for o in db.orders.find(_q, {
                    "_id": 0, "order_number": 1, "status": 1, "platform": 1,
                    "created_at": 1, "marketplace_order_date": 1, "cancelled_at": 1,
                    "total": 1, "items": 1}).limit(25):
                _on = str(o.get("order_number") or "")
                if _on in _gorulen:
                    continue
                _gorulen.add(_on)
                # Kalemlerden hesaplanan tutar — düzeltme için REFERANS (uygulanmaz).
                _hesap = 0.0
                for _it in (o.get("items") or []):
                    try:
                        _q2 = max(1, int((_it or {}).get("quantity") or 1))
                        _pr = float((_it or {}).get("price")
                                    or (_it or {}).get("unit_price") or 0)
                        if _pr == _pr and abs(_pr) != float("inf"):
                            _hesap += _q2 * _pr
                    except Exception:
                        pass
                out["siparisler"].append({
                    "siparis_no": _on, "bozuk_alan": _alan,
                    "statu": o.get("status"), "platform": o.get("platform"),
                    "siparis_tarihi": str(o.get("marketplace_order_date")
                                          or o.get("created_at") or "")[:19],
                    "iptal_tarihi": str(o.get("cancelled_at") or "")[:19],
                    "kalem_sayisi": len(o.get("items") or []),
                    "kalemlerden_hesaplanan_tutar": round(_hesap, 2),
                })
        out["toplam_bozuk_kayit"] = sum(out["alanlar"].values())
        out["not"] = ("Bu kayıtlar DEĞİŞTİRİLMEDİ. Raporlar bozuk değeri 0 sayarak "
                      "devam eder; doğru tutarın ne olacağına işletme karar vermeli. "
                      "'kalemlerden_hesaplanan_tutar' yalnız referanstır.")
        return out
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


def _json_safe(obj, bad: list, path: str = ""):
    """NaN / Infinity değerlerini temizler ve NEREDE bulunduklarını kaydeder.

    Starlette'in JSONResponse'u json.dumps(..., allow_nan=False) kullanır: yanıtta
    tek bir NaN/Infinity varsa serileştirme ANDA patlar ve TÜM /health yanıtı 500
    olur. Bu, endpoint çalıştıktan SONRA olduğu için blok içindeki try/except
    yakalayamaz — haziran denetimi tam olarak böyle düşüyordu (8 sn'de 500, zaman
    aşımı değil). Bozuk sayı None'a çevrilir, yolu 'bozuk_sayilar'da raporlanır.
    """
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            if len(bad) < 40:
                bad.append(path or "(kök)")
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe(v, bad, f"{path}.{k}" if path else str(k))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v, bad, f"{path}[{i}]") for i, v in enumerate(obj)]
    return obj


_RAPOR_Q_YASAK = {"/products/export-xlsx", "/gated/stock-restock-audit", "/profitability-config"}


async def _rapor_sorgu(q: str, start_date: str = "", end_date: str = "", source: str = "") -> dict:
    """rapor_q: Raporlar sayfasının GÖSTERDİĞİ rakamları, sayfanın çağırdığı AYNI uç
    fonksiyonlarıyla üretir (denetim/kıyas için). SALT OKUNUR: yalnız GET rapor uçları;
    yazma bayrağı taşıyan ('apply') ve dosya üreten uçlar hariç. Müşteri kişisel verisi
    rapor asistanının temizleyicisinden geçirilir.
    Biçim: rapor_q=sales-breakdown,sales?group_by=month,by-location?group=city&limit=20"""
    import inspect
    from routes import reports as _R
    from routes import reports_v2 as _R2
    from routes.report_assistant import _cagir, _temizle
    from routes.report_dedup import load_dup_order_numbers as _ldc_rq
    from fastapi.routing import APIRoute
    # Uçlar router bağımlılığı (load_dup_dep) OLMADAN doğrudan çağrılıyor: Ticimax kopya
    # elemesi yüklenmezse soğuk süreçte ay rakamları şişik çıkıyordu. Önce yükle.
    try:
        await _ldc_rq()
    except Exception:
        pass
    uclar = {r.path.replace(_R.router.prefix, "", 1): r.endpoint for r in _R.router.routes
             if isinstance(r, APIRoute) and "GET" in r.methods}
    # Kâr & Stok Değer sayfasının uçları (/admin/reports2/*) "v2/<uç>" adıyla (salt GET).
    uclar.update({"/v2" + r.path.replace(_R2.router.prefix, "", 1): r.endpoint
                  for r in _R2.router.routes if isinstance(r, APIRoute) and "GET" in r.methods})
    ortak = {"start_date": start_date or None, "end_date": end_date or None,
             "source": source or None}
    out: dict = {"aralik": {"start": start_date, "end": end_date, "source": source or "all"}}
    for parca in [p.strip() for p in (q or "").split(",") if p.strip()][:12]:
        yol, _, qs = parca.partition("?")
        yol = "/" + yol.strip("/")
        fn = uclar.get(yol)
        if not fn or yol in _RAPOR_Q_YASAK:
            out[yol] = {"hata": "bilinmeyen/izinsiz uç", "izinli": sorted(set(uclar) - _RAPOR_Q_YASAK)}
            continue
        imza = inspect.signature(fn).parameters
        kw = {k: v for k, v in ortak.items() if k in imza and v is not None}
        for kv in qs.split("&"):
            k, _, v = kv.partition("=")
            if k and k in imza and k not in ("apply", "current_user"):
                if k in ("limit", "days", "window_days", "min_sold", "top", "velocity_days",
                         "horizon_days", "target_cover_days", "min_orders", "min_stock") and v.isdigit():
                    kw[k] = int(v)
                elif k in ("threshold", "min_velocity"):
                    try:
                        kw[k] = float(v)
                    except ValueError:
                        kw[k] = v
                else:
                    kw[k] = v
        try:
            out[yol] = _temizle(await _cagir(fn, {"id": "diag", "role": "super_admin",
                                                   "email": "diag@local"}, **kw))
        except Exception as _e:
            out[yol] = {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}
    return out


async def _asistan_log_block() -> dict:
    """Rapor asistanının son 15 cevabı: hangi sağlayıcı cevapladı, başarısız denemeler
    (ör. Gemini hata metni), süre. Soru/cevap METNİ döndürülmez. Salt okuma."""
    rows = await db.report_assistant_logs.find(
        {}, {"_id": 0, "at": 1, "saglayici": 1, "denemeler": 1, "ms": 1, "araclar": 1}
    ).sort("at", -1).limit(15).to_list(15)
    from routes.report_assistant import _anahtar
    return {"son": rows,
            "anahtar_var": {"gemini": bool(await _anahtar("GEMINI_API_KEY", "gemini|google")),
                            "openai": bool(await _anahtar("OPENAI_API_KEY", "openai"))}}


async def _iade_sayfa_teshis() -> dict:
    """SALT OKUNUR: İadeler sayfasının açılışta çağırdığı uçları sayfanın parametreleriyle
    çalıştırır; yalnız satır sayısı / süre / HATA ve hatanın dosya:satırını döndürür
    (müşteri verisi DÖNDÜRMEZ). "İadeler sayfasına girilmiyor" teşhisi için."""
    import time as _t, traceback as _tb
    from routes.report_assistant import _cagir
    from routes import rooftr_returns as _rr, integrations_trendyol as _it, settings as _st
    _u = {"id": "diag", "role": "super_admin", "email": "diag@local", "is_super_admin": True}
    cagrilar = [
        ("web_iade_listesi", _rr.list_rooftr_return_orders, {"limit": 10000}),
        ("ty_iadeler", _it.get_trendyol_claims, {"page": 1, "limit": 20, "platform": "trendyol"}),
        ("hb_iadeler", _it.get_trendyol_claims, {"page": 1, "limit": 20, "platform": "hepsiburada"}),
        ("amazon_iadeler", _it.get_trendyol_claims, {"page": 1, "limit": 20, "platform": "amazon"}),
        ("site_talepleri", _it.get_trendyol_claims, {"page": 1, "limit": 20, "platform": "meluni"}),
        ("siparis_durumlari", _st.get_order_statuses, {}),
    ]
    out = {}
    for ad, fn_, kw in cagrilar:
        t0 = _t.monotonic()
        try:
            r = await _cagir(fn_, _u, **kw)
            n = None
            if isinstance(r, dict):
                for k in ("orders", "claims", "items", "rows", "statuses"):
                    if isinstance(r.get(k), list):
                        n = len(r[k]); break
                toplam = r.get("total")
            else:
                toplam = None
            out[ad] = {"ok": True, "satir": n, "toplam": toplam, "ms": int((_t.monotonic() - t0) * 1000)}
        except Exception as e:
            fr = [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}" for f in _tb.extract_tb(e.__traceback__)][-4:]
            out[ad] = {"ok": False, "hata": f"{type(e).__name__}: {str(e)[:400]}", "yer": fr,
                       "ms": int((_t.monotonic() - t0) * 1000)}
    return out


async def _ty_urun_probe(barkodlar: str = "") -> dict:
    """SALT OKUNUR (yalnız GET): Trendyol ürün listeleme V1 kapandı (426). Aday V2 uçlarını
    size=1 ile dener, durum kodu + alan adlarını döndürür; verilen barkodların Trendyol'daki
    stok/satış durumunu okur. HİÇBİR YAZMA YAPMAZ."""
    import httpx
    from routes.integrations_trendyol import get_trendyol_config
    from trendyol_client import TrendyolClient
    cfg = await get_trendyol_config()
    if not cfg.get("is_active"):
        return {"hata": "trendyol aktif değil"}
    c = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                       api_secret=cfg["api_secret"], mode=cfg["mode"])
    sid, base = cfg["supplier_id"], c.base_url
    h = c._get_headers()
    h_sf = dict(h, storeFrontCode="TR")
    bcs = [b.strip() for b in (barkodlar or "").split(",") if b.strip()][:10]
    adaylar = [
        ("v1_products", f"/product/sellers/{sid}/products", {"page": 0, "size": 1}),
        ("v1_size100", f"/product/sellers/{sid}/products", {"page": 0, "size": 100}),
        ("v1_size200", f"/product/sellers/{sid}/products", {"page": 0, "size": 200}),
        ("v1_archived_false", f"/product/sellers/{sid}/products", {"page": 0, "size": 1, "archived": "false"}),
        ("v1_size200_archived_false", f"/product/sellers/{sid}/products", {"page": 0, "size": 200, "archived": "false"}),
        ("v1_page5_size100", f"/product/sellers/{sid}/products", {"page": 5, "size": 100}),
        ("v2_approved_size100", f"/product/sellers/{sid}/products/approved", {"page": 0, "size": 100}),
        ("v2_approved", f"/product/sellers/{sid}/products/approved", {"page": 0, "size": 1}),
        ("v2_unapproved", f"/product/sellers/{sid}/products/unapproved", {"page": 0, "size": 1}),
        ("v2_products", f"/product/sellers/{sid}/v2/products", {"page": 0, "size": 1}),
    ]
    for b in bcs[:3]:
        adaylar.append((f"base_info:{b}", f"/product/sellers/{sid}/product/{b}", {}))
        adaylar.append((f"v2_approved?barcode={b}", f"/product/sellers/{sid}/products/approved", {"barcode": b, "page": 0, "size": 5}))

    def _ozet(j):
        if isinstance(j, dict):
            ic = j.get("content") if isinstance(j.get("content"), list) else None
            o = {"anahtarlar": list(j.keys())[:25], "toplam": j.get("totalElements", j.get("total"))}
            if ic is not None:
                o["icerik_adet"] = len(ic)
                if ic and isinstance(ic[0], dict):
                    o["ilk_alanlar"] = list(ic[0].keys())[:60]
                    o["ilk"] = {k: ic[0].get(k) for k in ("barcode", "stockCode", "quantity", "onSale",
                                                         "archived", "approved", "salePrice", "title",
                                                         "productMainId", "locked", "blacklisted")
                                if k in ic[0]}
                    # varyant/iç yapı (V2'de stok varyant altında olabilir)
                    for k in ("variants", "items", "attributes"):
                        if isinstance(ic[0].get(k), list) and ic[0][k] and isinstance(ic[0][k][0], dict):
                            o[f"ilk_{k}_alanlari"] = list(ic[0][k][0].keys())[:40]
                            o[f"ilk_{k}_ornek"] = {kk: ic[0][k][0].get(kk) for kk in
                                                  ("barcode", "quantity", "stock", "onSale", "salePrice", "stockCode")
                                                  if kk in ic[0][k][0]}
            else:
                o["deger"] = {k: j.get(k) for k in ("barcode", "stockCode", "quantity", "onSale", "archived",
                                                   "approved", "salePrice", "title", "locked") if k in j}
            return o
        return {"tip": type(j).__name__}

    out = {"base_url": base}
    async with httpx.AsyncClient(timeout=25.0) as cl:
        for ad, yol, prm in adaylar:
            for etiket, hh in (("", h), ("+storeFront", h_sf)):
                try:
                    r = await cl.get(f"{base}{yol}", headers=hh, params=prm)
                    try:
                        j = r.json()
                    except Exception:
                        j = None
                    out[ad + etiket] = {"durum": r.status_code,
                                        **(_ozet(j) if (j is not None and r.status_code < 400) else
                                           {"govde": (r.text or "")[:240]})}
                except Exception as e:
                    out[ad + etiket] = {"hata": f"{type(e).__name__}: {str(e)[:200]}"}
    return out


async def _barkod_dokum() -> dict:
    """SALT OKUNUR: tüm ürün barkodları (pasif/silinmiş dahil) → stok ve durum. Pazaryeri
    ürün listesiyle barkod barkod kıyas için. Müşteri verisi yok (yalnız katalog)."""
    out = []
    async for p in db.products.find({}, {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "barcode": 1,
                                         "stock": 1, "is_active": 1, "is_deleted": 1,
                                         "variants.barcode": 1, "variants.stock": 1, "variants.size": 1,
                                         "variants.color": 1, "variants.stock_code": 1}):
        base = [str(p.get("id") or "")[:8], (p.get("name") or "")[:60], p.get("stock_code") or "",
                p.get("is_active") is not False, bool(p.get("is_deleted"))]
        vs = p.get("variants") or []
        if vs:
            for v in vs:
                if v.get("barcode"):
                    out.append([str(v["barcode"]).strip(), int(v.get("stock") or 0),
                                v.get("size") or "", v.get("color") or "", v.get("stock_code") or ""] + base)
        elif p.get("barcode"):
            out.append([str(p["barcode"]).strip(), int(p.get("stock") or 0), "", "", ""] + base)
    return {"kolonlar": ["barkod", "stok", "beden", "renk", "varyant_stok_kodu", "urun_id8", "ad",
                         "stok_kodu", "aktif", "silinmis"], "satir": len(out), "barkodlar": out}


async def _hb_yetim_onizleme() -> dict:
    """SALT OKUNUR önizleme — tespit integrations_hepsiburada.hb_yetim_ilanlar'da (süpürmeyle ortak)."""
    from routes.integrations_hepsiburada import hb_yetim_ilanlar
    r = await hb_yetim_ilanlar()
    if isinstance(r.get("yetimler"), list):
        r["yetimler"] = [{k: v for k, v in y.items() if not k.startswith("_")} for y in r["yetimler"][:300]]
    return r


async def _ty_canli_kiyas() -> dict:
    """SALT OKUNUR: Trendyol'un CANLI ürün listesi (V1, size=100, arşiv dahil) ile bizim
    barkodlarımızı barkod barkod kıyaslar (Excel kıyasının canlı hâli). Yazma yok."""
    from routes.integrations_trendyol import get_trendyol_config
    from trendyol_client import TrendyolClient
    cfg = await get_trendyol_config()
    cli = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                         api_secret=cfg["api_secret"], mode=cfg["mode"])
    ty, page = [], 0
    while page < 60:
        r = await cli.get_filtered_products(page=page, size=100)
        c = r.get("content") or []
        ty.extend(c)
        page += 1
        if not c or page >= (r.get("totalPages") or 0):
            break
    biz = {}
    async for p in db.products.find({}, {"_id": 0, "name": 1, "is_active": 1, "is_deleted": 1, "barcode": 1,
                                         "stock": 1, "variants.barcode": 1, "variants.stock": 1}):
        akt = p.get("is_active") is not False and not p.get("is_deleted")
        vs = p.get("variants") or []
        pairs = [(v.get("barcode"), v.get("stock")) for v in vs] if vs else [(p.get("barcode"), p.get("stock"))]
        for bc, st in pairs:
            if not bc:
                continue
            bc = str(bc).strip()
            prev = biz.get(bc)
            if prev is None or (akt and not prev[1]):
                biz[bc] = (int(st or 0), akt, bool(p.get("is_deleted")), (p.get("name") or "")[:50])
    kat = {"aktif_ayni": 0, "aktif_farkli": [], "pasif_silinmis_stoklu": [], "bizde_yok_stoklu": [],
           "pasif_silinmis_sifir": 0, "bizde_yok_sifir_veya_arsiv": 0}
    for t in ty:
        bc = str(t.get("barcode") or "").strip()
        q = int(t.get("quantity") or 0)
        sat = bool(t.get("onSale")) and not t.get("archived")
        b = biz.get(bc)
        row = {"barkod": bc, "ty_stok": q, "satista": sat, "arsiv": bool(t.get("archived")),
               "ad": (t.get("title") or "")[:50]}
        if b is None:
            if q > 0 and not t.get("archived"):
                kat["bizde_yok_stoklu"].append(row)
            else:
                kat["bizde_yok_sifir_veya_arsiv"] += 1
        elif not b[1]:
            if q > 0 and not t.get("archived"):
                kat["pasif_silinmis_stoklu"].append({**row, "biz": b[0], "silinmis": b[2]})
            else:
                kat["pasif_silinmis_sifir"] += 1
        elif b[0] != q:
            kat["aktif_farkli"].append({**row, "biz": b[0]})
        else:
            kat["aktif_ayni"] += 1
    return {"trendyol_barkod": len(ty), **{k: (v if isinstance(v, int) else {"adet": len(v), "liste": v[:80]})
                                           for k, v in kat.items()}}


async def _ty_batch_sonuc(ids: str = "", barkodlar: str = "") -> dict:
    """SALT OKUNUR: Trendyol toplu işlem (batch) sonucu — kalem bazında başarısızlık nedenleri.
    'barkodlar' verilirse yalnız o barkodların kalemleri döndürülür."""
    from routes.integrations_trendyol import get_trendyol_config
    from trendyol_client import TrendyolClient
    cfg = await get_trendyol_config()
    cli = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                         api_secret=cfg["api_secret"], mode=cfg["mode"])
    want = {b.strip() for b in (barkodlar or "").split(",") if b.strip()}
    out = {}
    for bid in [x.strip() for x in (ids or "").split(",") if x.strip()][:6]:
        try:
            r = await cli.get_batch_request_result(bid)
        except Exception as e:
            out[bid] = {"hata": f"{type(e).__name__}: {str(e)[:200]}"}
            continue
        items = r.get("items") or []
        from collections import Counter as _C
        dur = _C(str(i.get("status")) for i in items)
        sec = []
        for i in items:
            rq = i.get("requestItem") or {}
            bc = str(rq.get("barcode") or (rq.get("product") or {}).get("barcode") or "")
            if (want and bc in want) or (not want and str(i.get("status")).upper() != "SUCCESS"):
                sec.append({"barkod": bc, "durum": i.get("status"), "nedenler": i.get("failureReasons"),
                            "istek": {k: rq.get(k) for k in ("quantity", "salePrice", "listPrice")}})
        out[bid] = {"batch_durum": r.get("status"), "kalem": len(items), "durum_dagilimi": dict(dur),
                    "secilen": sec[:60]}
    return out


async def _destek_teshis(mod: str = "", eposta: str = "") -> dict:
    """SALT OKUNUR destek teşhisi. PII DÖNMEZ (e-posta yalnız eşleme için kullanılır).
    mod=kargo  : Ekinoks bitişinden sonra eşik altı ücretsiz kargoyla açılan site siparişleri
    mod=uyelik : hoş geldin kodu/maili zinciri (kural, şablon, sağlayıcı, loglar, popup)
    mod=yavas  : >3 sn yanıtlar (error_logs.slow_response) uç bazında"""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now = _dt.now(_tz.utc)
    out = {"mod": mod}
    if mod == "kargo":
        bas = "2026-09-25T20:59:59"
        q = {"created_at": {"$gte": bas}, "free_shipping_applied": True}
        rows = []
        async for o in db.orders.find(q, {"_id": 0, "order_number": 1, "created_at": 1, "status": 1,
                                          "payment_status": 1, "source": 1, "channel": 1,
                                          "shipping_eligibility_basis": 1, "subtotal": 1,
                                          "free_shipping_waived_fee": 1, "free_shipping_threshold": 1,
                                          "applied_promotions": 1}).sort("created_at", 1):
            basis = o.get("shipping_eligibility_basis")
            if basis is None:
                basis = o.get("subtotal")
            rows.append({"no": o.get("order_number"), "at": o.get("created_at"), "status": o.get("status"),
                         "pay": o.get("payment_status"), "kaynak": o.get("source") or o.get("channel"),
                         "basis": basis, "esik": o.get("free_shipping_threshold"),
                         "feragat": o.get("free_shipping_waived_fee"),
                         "promo_fs": [a.get("code") for a in (o.get("applied_promotions") or [])
                                      if isinstance(a, dict) and a.get("free_shipping")]})
        out["siparisler"] = rows
        out["esik_alti"] = [r for r in rows if (r["basis"] or 0) < 4000]
        return out
    if mod == "uyelik":
        from business_rules import get_rule as _gr
        out["kural"] = {k: await _gr(db, k, None) for k in
                        ("welcome.enabled", "welcome.reward_type", "welcome.reward_value")}
        out["sablon"] = []
        async for t in db.notification_templates.find({"event": "welcome"}, {"_id": 0, "body": 0}):
            out["sablon"].append({k: (str(v)[:120] if isinstance(v, str) else v) for k, v in t.items()
                                  if k in ("event", "channel", "enabled", "is_active", "subject", "updated_at")})
        prov = await db.settings.find_one({"id": "notification_providers"}, {"_id": 0, "email_active": 1}) or {}
        out["saglayici_email_active"] = prov.get("email_active", True)
        st = await db.settings.find_one({"id": "main"}, {"_id": 0, "email_smtp": 1}) or {}
        es = st.get("email_smtp") or {}
        out["email_ayar"] = {"enabled": es.get("enabled"), "username_var": bool(es.get("username")),
                             "password_var": bool(es.get("password")), "host": es.get("host")}
        since = (now - _td(days=14)).isoformat()
        agg = {}
        async for r in db.notification_logs.find({"created_at": {"$gte": since}},
                                                  {"_id": 0, "event": 1, "channel": 1, "status": 1}):
            k = f"{r.get('event')}/{r.get('channel')}/{r.get('status')}"
            agg[k] = agg.get(k, 0) + 1
        out["bildirim_log_14g"] = dict(sorted(agg.items()))
        son_hata = []
        async for r in db.notification_logs.find({"channel": "email", "status": {"$nin": ["sent", "success", "ok"]},
                                                   "created_at": {"$gte": since}},
                                                  {"_id": 0, "event": 1, "status": 1, "response": 1,
                                                   "created_at": 1}).sort("created_at", -1).limit(8):
            son_hata.append({"event": r.get("event"), "status": r.get("status"),
                             "resp": (r.get("response") or "")[:200], "at": r.get("created_at")})
        out["email_son_hatalar"] = son_hata
        out["yeni_uye_7g"] = {}
        async for u in db.users.find({"created_at": {"$gte": (now - _td(days=7)).isoformat()}},
                                     {"_id": 0, "auth_provider": 1, "role": 1}):
            if u.get("role") not in (None, "", "customer", "user"):
                continue
            k = u.get("auth_provider") or "email"
            out["yeni_uye_7g"][k] = out["yeni_uye_7g"].get(k, 0) + 1
        out["bulten_7g"] = {}
        async for n in db.newsletter_subscribers.find({"created_at": {"$gte": (now - _td(days=7)).isoformat()}},
                                                      {"_id": 0, "source": 1}):
            k = n.get("source") or "?"
            out["bulten_7g"][k] = out["bulten_7g"].get(k, 0) + 1
        out["hg_kupon_7g"] = await db.coupons.count_documents(
            {"source": "welcome", "created_at": {"$gte": (now - _td(days=7)).isoformat()}})
        pops = []
        async for pp in db.popups.find({}, {"_id": 0, "name": 1, "title": 1, "is_active": 1, "type": 1,
                                            "popup_type": 1, "button_text": 1, "content": 1}):
            pops.append({k: (str(v)[:160] if isinstance(v, str) else v) for k, v in pp.items()})
        out["popuplar"] = pops
        fo = await db.coupons.find({"is_active": True, "first_order_only": True},
                                   {"_id": 0, "code": 1, "value": 1, "type": 1, "start_at": 1, "end_at": 1,
                                    "auto_apply": 1}).to_list(10)
        out["ilk_siparis_kuponlari"] = fo
        e = (eposta or "").strip().lower()
        if e:
            u = await db.users.find_one({"email": e}, {"_id": 0, "auth_provider": 1, "created_at": 1,
                                                      "email_verified": 1})
            nb = await db.newsletter_subscribers.find_one({"email": e}, {"_id": 0, "source": 1, "created_at": 1})
            hg = await db.coupons.count_documents({"source": "welcome", "email": e})
            out["musteri"] = {"uye": bool(u), "uye_provider": (u or {}).get("auth_provider") or ("email" if u else None),
                              "uye_at": (u or {}).get("created_at"), "dogrulandi": (u or {}).get("email_verified"),
                              "bulten": bool(nb), "bulten_kaynak": (nb or {}).get("source"),
                              "bulten_at": (nb or {}).get("created_at"), "hg_kupon": hg}
        return out
    if mod == "yavas":
        res = {}
        for gun in (0.125, 1, 7):  # 0.125 gün = son 3 saat (düzeltme sonrası durumu ayrı görmek için)
            since = (now - _td(days=gun)).isoformat()
            agg = {}
            async for r in db.error_logs.find({"kind": "slow_response", "created_at": {"$gte": since}},
                                              {"_id": 0, "path": 1, "meta": 1}):
                p = r.get("path") or "?"
                a = agg.setdefault(p, [0, 0])
                a[0] += 1
                a[1] = max(a[1], int((r.get("meta") or {}).get("duration_ms") or 0))
            res["3s" if gun == 0.125 else f"{gun}g"] = sorted(([p, n, mx] for p, (n, mx) in agg.items()), key=lambda x: -x[1])[:30]
        out["yavas"] = res
        return out
    if mod == "meta":
        feeds = await db.xml_feeds.find({}, {"_id": 0, "slug": 1, "target": 1, "in_stock_only": 1,
                                             "group_variants": 1, "name": 1}).to_list(50)
        out["feeds"] = feeds
        ids = [x.strip() for x in (eposta or "").split(",") if x.strip()][:200]
        res = {}
        for cid in ids:
            hits = []
            async for pr in db.products.find({"$or": [{"variants.id": cid}, {"id": cid}]},
                                             {"_id": 0, "id": 1, "is_active": 1, "is_deleted": 1,
                                              "variants": 1, "stock_code": 1}).limit(5):
                v = next((v for v in (pr.get("variants") or []) if str(v.get("id")) == cid), None)
                hits.append({"pid": pr.get("id"), "aktif": pr.get("is_active"), "silik": pr.get("is_deleted"),
                             "sk": pr.get("stock_code"), "vstok": (v or {}).get("stock") if v else None,
                             "urun_id_mi": pr.get("id") == cid})
            res[cid] = hits
        out["idler"] = res
        # Aktif ürünler arasında AYNI varyant id'si birden fazla üründe mi? (katalogda çakışır)
        dup = {}
        tot = 0
        async for pr in db.products.find({"is_active": True, "is_deleted": {"$ne": True}},
                                         {"_id": 0, "id": 1, "variants.id": 1}):
            for v in (pr.get("variants") or []):
                vid = str(v.get("id") or "")
                if not vid:
                    continue
                tot += 1
                dup.setdefault(vid, set()).add(pr.get("id"))
        cak = {k: len(v) for k, v in dup.items() if len(v) > 1}
        out["aktif_varyant_toplam"] = tot
        out["cakisan_varyant_id_sayisi"] = len(cak)
        out["cakisan_ornek"] = sorted(cak.items(), key=lambda x: -x[1])[:15]
        out["bos_varyant_id"] = None
        return out
    if mod == "siparis":
        # Tek sipariş PARA teşhisi (PII YOK): kalemler, tutar alanları, fatura alanları.
        q = (eposta or "").strip()
        ors = [{"order_number": q}, {"hepsiburada_order_number": q}, {"hepsiburada_package_number": q},
               {"marketplace_order_id": q}, {"package_number": q}, {"id": q},
               {"hepsiburada_package_numbers": q}]
        docs = []
        _money_keys = ("order_number", "platform", "status", "payment_status", "created_at",
                       "marketplace_order_date", "subtotal", "discount", "discount_total", "total",
                       "total_amount", "shipping_cost", "marketplace_total", "hepsiburada_order_number",
                       "hepsiburada_package_number", "hepsiburada_package_numbers", "package_number",
                       "invoice_issued", "invoice_number", "invoice_type", "invoice_total",
                       "invoice_amount", "invoice_created_at", "invoice_lines_total",
                       "invoice_payable", "invoice_items_count", "hepsiburada_invoice_uploaded",
                       "hb_merged_packages", "merged_from", "split_from", "cancelled_items",
                       "partial_cancel", "updated_at")
        async for o in db.orders.find({"$or": ors}, {"_id": 0}).limit(10):
            its = []
            for it in (o.get("items") or []):
                its.append({k: it.get(k) for k in (
                    "name", "sku", "barcode", "stock_code", "quantity", "price", "unit_price",
                    "list_price", "sale_price", "discount_amount", "discount", "hb_discount",
                    "merchant_discount", "total", "line_total", "kdv_rate", "vat_rate", "status",
                    "line_status", "package_number", "hb_line_id", "cancelled", "is_cancelled")
                    if it.get(k) not in (None, "")})
            d = {k: o.get(k) for k in _money_keys if o.get(k) not in (None, "")}
            d["kalem_sayisi"] = len(its)
            d["kalemler"] = its
            try:
                d["kalem_brut_toplam"] = round(sum(float(i.get("price") or 0) * int(i.get("quantity") or 1)
                                                   for i in (o.get("items") or [])), 2)
            except Exception:
                pass
            # Fatura ile ilgili TÜM alan adları (değer türüyle) — nereden okunduğunu görmek için
            d["fatura_alanlari"] = {k: (v if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) < 80) else type(v).__name__)
                                    for k, v in o.items() if "invoice" in k or "fatura" in k or "einvoice" in k}
            docs.append(d)
        out["siparisler"] = docs
        try:
            _fl = await db.settings.find_one({"id": "hb4153_fatura_yeniden_v1"}, {"_id": 0})
            if _fl:
                out["yeniden_kesim_is_sonucu"] = _fl
            import re as _re_fk
            _fl2 = await db.settings.find({"id": {"$regex": "^fatura_yeniden_kesim:.*" + _re_fk.escape(q.replace("HB", ""))}},
                                          {"_id": 0}).to_list(5)
            if _fl2:
                out["iptal_sonrasi_kesim"] = _fl2
        except Exception:
            pass
        # Doğan/e-fatura gönderim kaydı varsa (koleksiyon adları sistemden bağımsız denenir)
        for col in ("invoice_logs", "einvoice_logs", "dogan_invoice_logs", "invoices"):
            try:
                rows = []
                async for r in db[col].find({"$or": [{"order_number": q}, {"order_id": {"$in": [x.get("id") for x in []]}}]},
                                            {"_id": 0}).limit(5):
                    rows.append({k: (v if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) < 120) else type(v).__name__)
                                 for k, v in r.items() if k not in ("customer", "buyer", "address", "email", "phone", "tckn")})
                if rows:
                    out[col] = rows
            except Exception:
                pass
        return out
    if mod == "iptal_fatura":
        # SALT OKUNUR: iptal edilmiş siparişlerde Doğan e-Arşiv faturası hâlâ GEÇERLİ mi?
        # (otomatik iptal fonksiyonu hiç çalışmıyordu). Yalnız durum sorgusu, iptal YOK. PII yok.
        ds = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
        from dogan_client import DoganClient
        from fastapi.concurrency import run_in_threadpool
        from zeep.helpers import serialize_object
        cli = DoganClient(username=ds["username"], password=ds["password"], is_test=ds.get("is_test", True))
        q = {"status": {"$in": ["cancelled", "cancel_refunded"]}, "invoice_issued": True,
             "invoice_uuid": {"$nin": [None, ""]}}
        rows = await db.orders.find(q, {"_id": 0, "order_number": 1, "platform": 1, "total": 1,
                                        "invoice_number": 1, "invoice_type": 1, "invoice_uuid": 1,
                                        "invoice_issued_at": 1, "cancelled_at": 1, "status": 1,
                                        "invoice_cancelled": 1, "invoice_cancel_result": 1}).to_list(400)
        out["aday"] = len(rows)
        gecerli, iptal, hata = [], 0, []
        def _st(uuid):
            c = cli._get_earsiv_client()
            r = serialize_object(c.service.GetEArchiveInvoiceStatus(REQUEST_HEADER=cli._make_header(), UUID=uuid)) or {}
            return ((r.get("INVOICE") or [{}])[0] or {}).get("HEADER") or {}
        for o in rows:
            t = str(o.get("invoice_type") or "").lower()
            if t not in ("e-arsiv", "earsiv", "e-arşiv"):
                gecerli.append({"no": o.get("order_number"), "fatura": o.get("invoice_number"), "tip": t,
                                "not": "e-Fatura — otomatik iptal yok, manuel"})
                continue
            try:
                h = await run_in_threadpool(_st, o["invoice_uuid"])
            except Exception as e:
                hata.append({"no": o.get("order_number"), "hata": str(e)[:120]})
                continue
            if str(h.get("PROFILE") or "").upper() == "IPTAL" or str(h.get("STATUS")) == "201":
                iptal += 1
                continue
            gecerli.append({"no": o.get("order_number"), "platform": o.get("platform"), "tutar": o.get("total"),
                            "fatura": o.get("invoice_number"), "fatura_tarihi": str(o.get("invoice_issued_at") or "")[:10],
                            "iptal_tarihi": str(o.get("cancelled_at") or "")[:10], "statu": o.get("status"),
                            "dogan": f"{h.get('STATUS')} {h.get('STATUS_DESC')}"})
        out["dogan_iptal_olan"] = iptal
        out["HALA_GECERLI"] = gecerli
        out["sorgu_hatasi"] = hata[:20]
        return out
    if mod == "varyant_id":
        # SALT OKUNUR: beden id (variants.id) ve Ticimax beden id (variants.urun_id) biçim/tekillik.
        import re as _r
        fmt = lambda v: ("4hane" if _r.fullmatch(r"\d{4}", v) else "sayi" if v.isdigit() else
                         "var-mfg" if v.startswith("var-mfg") else "var-" if v.startswith("var-") else
                         "bos" if not v else "diger")
        c_id, c_uid, eq, both = {}, {}, 0, {}
        uid_map, id_map = {}, {}
        ornek = []
        n_prod = n_var = 0
        async for p in db.products.find({"is_deleted": {"$ne": True}},
                                        {"_id": 0, "id": 1, "name": 1, "is_active": 1, "variants.id": 1,
                                         "variants.urun_id": 1, "variants.barcode": 1, "urun_id": 1}):
            n_prod += 1
            for v in (p.get("variants") or []):
                n_var += 1
                vid = str(v.get("id") or "").strip(); uid = str(v.get("urun_id") or "").strip()
                c_id[fmt(vid)] = c_id.get(fmt(vid), 0) + 1
                c_uid[fmt(uid)] = c_uid.get(fmt(uid), 0) + 1
                k = f"{fmt(vid)}|{fmt(uid)}"; both[k] = both.get(k, 0) + 1
                if vid and vid == uid:
                    eq += 1
                if uid:
                    uid_map.setdefault(uid, set()).add(p["id"])
                if vid:
                    id_map.setdefault(vid, set()).add(p["id"])
                if not vid.isdigit() and len(ornek) < 8:
                    ornek.append({"urun": (p.get("name") or "")[:40], "aktif": p.get("is_active"), "id": vid, "urun_id": uid})
        out.update({"urun": n_prod, "beden": n_var, "id_bicim": c_id, "urun_id_bicim": c_uid,
                    "id_urun_id_birlikte": both, "id_esit_urun_id": eq,
                    "urun_id_cakisan": {k: len(v) for k, v in uid_map.items() if len(v) > 1},
                    "id_cakisan": {k: len(v) for k, v in id_map.items() if len(v) > 1},
                    "ornek_4hane_olmayan": ornek,
                    "max_urun_id": max([int(x) for x in uid_map if x.isdigit()] or [0])})
        out["son_normalize"] = await db.settings.find_one({"id": "varyant_id_normalize"}, {"_id": 0})
        return out
    if mod == "body10":
        doc = await db.settings.find_one({"id": "body_indirim10_v2"}, {"_id": 0}) or {}
        return {"done": bool(doc.get("done")), "at": doc.get("at"), "changed": doc.get("changed"),
                "items": doc.get("items") or []}
    if mod == "uye_tipi_v18":
        a = await db.settings.find_one({"id": "fiyat_guncelleme_v18_uye_tipi"}, {"_id": 0}) or {}
        b = await db.settings.find_one({"id": "elen_body10_geri_v1"}, {"_id": 0}) or {}
        return {"uye_tipi": a, "elen_geri": b}
    if mod == "indirim_temizle":
        return await db.settings.find_one({"id": "indirimli_temizle_body_haric_v1"}, {"_id": 0}) or {}
    if mod == "iys":
        # İYS bildirim durumu (salt okunur; e-posta/telefon MASKELİ).
        import re as _re
        from routes.iys import _iys_config
        cfg = await _iys_config()
        _mask = lambda t: _re.sub(r"\+?\d{7,}", "<tel>", _re.sub(r"[\w.+-]+@[\w.-]+", "<eposta>", str(t or "")))
        tot = await db.iys_consents.count_documents({})
        rep = await db.iys_consents.count_documents({"reported": True})
        codes, resp = {}, {}
        async for r in db.iys_consents.find({"reported": {"$ne": True}},
                                            {"_id": 0, "report_status_code": 1, "report_response": 1,
                                             "created_at": 1, "channels": 1, "source": 1}).sort("created_at", -1).limit(3000):
            k = str(r.get("report_status_code"))
            codes[k] = codes.get(k, 0) + 1
            rr = _mask(r.get("report_response"))[:220] or "(yanıt yok — hiç denenmemiş)"
            resp[rr] = resp.get(rr, 0) + 1
        last_ok = await db.iys_consents.find_one({"reported": True}, {"_id": 0, "reported_at": 1, "created_at": 1},
                                                 sort=[("reported_at", -1)])
        oldest_pending = await db.iys_consents.find_one({"reported": {"$ne": True}}, {"_id": 0, "created_at": 1},
                                                        sort=[("created_at", 1)])
        return {"config": {"username": bool(cfg.get("username")), "password": bool(cfg.get("password")),
                           "appkey": bool(cfg.get("appkey")),
                           "brand_code": bool(cfg.get("brand_code") or cfg.get("iys_code"))},
                "toplam": tot, "bildirilen": rep, "bekleyen": tot - rep, "http_kodlari": codes,
                "yanitlar": sorted(resp.items(), key=lambda x: -x[1])[:8],
                "son_basarili": last_ok, "en_eski_bekleyen": oldest_pending,
                "gunluk_toplu": await db.settings.find_one({"id": "iys_daily_batch"}, {"_id": 0}),
                "url": os.environ.get("NETGSM_IYS_URL") or "https://api.netgsm.com.tr/iys/add"}
    if mod == "fiyat_0929":
        doc = await db.settings.find_one({"id": "fiyat_yukleme_0929"}, {"_id": 0}) or {}
        out = {k: doc.get(k) for k in ("done", "at", "changed", "missing", "indirim_eklenen", "indirim_cikarilan")}
        out["uye_tipi"] = await db.settings.find_one({"id": "fiyat_yukleme_0929_uye_tipi"}, {"_id": 0})
        return out
    if mod == "slug":
        doc = await db.settings.find_one({"id": "slug_kart_id_v1"}, {"_id": 0}) or {}
        return {"done": doc.get("done"), "changed": doc.get("changed"), "clash": doc.get("clash"),
                "ornek": (doc.get("items") or [])[:10]}
    if mod == "fiyat_eritme":
        doc = await db.settings.find_one({"id": "fiyat_eritme_0929"}, {"_id": 0}) or {}
        return {k: doc.get(k) for k in ("done", "at", "changed", "missing", "variant_missing",
                                        "indirim_eklenen", "indirim_cikarilan")}
    if mod == "ty_batch":
        # Trendyol batch sonucu (salt okunur GET) — destek_email parametresi = batch id.
        # İsteğe bağlı: "batch|STOKKODU" → ürünün yerel durumu da döner. PII yok (ürün verisi).
        bid, _, sc = (eposta or "").partition("|")
        out = {}
        try:
            from routes.integrations_trendyol import get_trendyol_config
            from trendyol_client import TrendyolClient
            cfg = await get_trendyol_config()
            cl = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                                api_secret=cfg["api_secret"], mode=cfg.get("mode", "live"))
            data = await cl.get_batch_request_result(bid.strip()) if bid.strip() else {}
            out["batch"] = {"status": data.get("status"), "itemCount": data.get("itemCount"),
                            "items": [{"status": it.get("status"), "failureReasons": it.get("failureReasons"),
                                       "barcode": (it.get("requestItem") or {}).get("barcode")
                                       or ((it.get("requestItem") or {}).get("product") or {}).get("barcode")}
                                      for it in (data.get("items") or [])][:60]}
        except Exception as e:
            out["batch_error"] = str(e)[:300]
        if sc.strip():
            prods = []
            async for pr in db.products.find({"stock_code": sc.strip()}, {"_id": 0, "id": 1, "name": 1, "is_active": 1,
                                                  "is_deleted": 1, "stock": 1, "price": 1, "member_price_1": 1,
                                                  "category_id": 1, "category_ids": 1, "trendyol_category_id": 1,
                                                  "trendyol_attributes": 1, "variants.barcode": 1, "variants.stock": 1,
                                                  "variants.size": 1, "images": 1, "trendyol_status": 1,
                                                  "trendyol_last_error": 1, "trendyol_synced_at": 1}):
                pr["images"] = len(pr.get("images") or [])
                pr["trendyol_attributes"] = len(pr.get("trendyol_attributes") or [])
                prods.append(pr)
            out["urunler"] = prods
        return out
    if mod == "blokaj":
        # Olay döngüsü kilitlenmeleri (son 24 sa): o an çalışan işler/istekler — salt okunur.
        since = (now - _td(hours=24)).isoformat()
        rows = await db.error_logs.find({"kind": "loop_block", "created_at": {"$gte": since}},
                                        {"_id": 0, "created_at": 1, "meta": 1}).sort("created_at", -1).to_list(500)
        jf, rf = {}, {}
        for r in rows:
            m = r.get("meta") or {}
            for j in (m.get("jobs") or []):
                jf[j[0]] = jf.get(j[0], 0) + 1
            for sname in (m.get("syncs") or []):
                jf["sync:" + sname] = jf.get("sync:" + sname, 0) + 1
            for q in (m.get("requests") or [])[:3]:
                rf[q[0]] = rf.get(q[0], 0) + 1
        try:
            from scheduler import JOB_STATS
            slow_jobs = sorted(([k, v] for k, v in JOB_STATS.items()), key=lambda x: -x[1]["max_s"])[:15]
        except Exception:
            slow_jobs = []
        return {"adet": len(rows), "max_lag_ms": max([(r.get("meta") or {}).get("lag_ms", 0) for r in rows] or [0]),
                "is_sikligi": sorted(jf.items(), key=lambda x: -x[1])[:15],
                "istek_sikligi": sorted(rf.items(), key=lambda x: -x[1])[:15],
                "son": rows[:10], "is_sureleri": slow_jobs}
    if mod == "tarama":
        # GENEL SAĞLIK/GÜVENLİK TARAMASI (salt okunur; e-posta/IP MASKELİ).
        import hashlib as _hl
        def _m_ip(ip):
            ip = str(ip or "")
            return (".".join(ip.split(".")[:2]) + ".x.x") if "." in ip else (ip[:6] + "…" if ip else "")
        def _h(x):
            return _hl.sha256(str(x or "").encode()).hexdigest()[:8] if x else ""
        d1 = (now - _td(days=1)).isoformat(); d7 = (now - _td(days=7)).isoformat()
        out = {}
        # 1) BÜYÜK SEPETLER (terk edilmiş sepet takibi)
        big = []
        async for c in db.cart_sessions.find({"$expr": {"$gte": [{"$size": {"$ifNull": ["$items", []]}}, 15]}},
                                             {"_id": 0}).sort("updated_at", -1).limit(200):
            its = c.get("items") or []
            pids = [str(i.get("product_id") or "") for i in its]
            qty = sum(int(i.get("qty") or 1) for i in its if isinstance(i, dict))
            big.append({"sid": _h(c.get("session_id")), "kalem": len(its), "adet": qty,
                        "farkli_urun": len(set(pids)), "bos_urun_id": sum(1 for x in pids if not x),
                        "tutar": round(float(c.get("total") or 0)),
                        "uye": bool(c.get("user_id")), "eposta_var": bool(c.get("email")),
                        "olusturma": str(c.get("created_at") or "")[:16], "guncelleme": str(c.get("updated_at") or "")[:16],
                        "isimler_bos": sum(1 for i in its if not (i.get("name") or "").strip())})
        out["buyuk_sepet"] = {"adet": len(big), "ornek": big[:40]}
        dist = {}
        async for c in db.cart_sessions.aggregate([
                {"$project": {"n": {"$size": {"$ifNull": ["$items", []]}}}},
                {"$bucket": {"groupBy": "$n", "boundaries": [0, 1, 3, 6, 10, 15, 20, 30, 50, 101], "default": "101+",
                             "output": {"c": {"$sum": 1}}}}]):
            dist[str(c["_id"])] = c["c"]
        out["sepet_kalem_dagilimi"] = dist
        out["sepet_toplam"] = await db.cart_sessions.count_documents({})
        # büyük sepetlerde en çok geçen ürünler (id → ad)
        pc = {}
        async for c in db.cart_sessions.find({"$expr": {"$gte": [{"$size": {"$ifNull": ["$items", []]}}, 15]}},
                                             {"_id": 0, "items.product_id": 1}).limit(500):
            for i in c.get("items") or []:
                k = str(i.get("product_id") or "")
                pc[k] = pc.get(k, 0) + 1
        top = sorted(pc.items(), key=lambda x: -x[1])[:10]
        names = {}
        if top:
            async for pr in db.products.find({"id": {"$in": [k for k, _ in top]}}, {"_id": 0, "id": 1, "name": 1}):
                names[pr["id"]] = pr.get("name")
        out["buyuk_sepet_urunleri"] = [[names.get(k, k), n] for k, n in top]
        # büyük sepetten siparişe dönen (üye) var mı
        # 2) HATALAR
        agg = {}
        async for r in db.error_logs.aggregate([{"$match": {"created_at": {"$gte": d1}}},
                                                {"$group": {"_id": {"k": "$kind", "l": "$level"}, "c": {"$sum": 1}}}]):
            agg[f"{r['_id'].get('k')}/{r['_id'].get('l')}"] = r["c"]
        out["hata_24s"] = agg
        top5 = {}
        async for r in db.error_logs.find({"created_at": {"$gte": d1}, "kind": {"$in": ["http_5xx", "exception"]}},
                                          {"_id": 0, "path": 1, "message": 1}).limit(3000):
            k = f"{r.get('path')} | {str(r.get('message') or '')[:120]}"
            top5[k] = top5.get(k, 0) + 1
        out["hata_24s_en_sik"] = sorted(top5.items(), key=lambda x: -x[1])[:15]
        # 3) GÜVENLİK: giriş denemeleri, IP engelleri, uyarılar
        ev = {}
        async for r in db.auth_audit_logs.aggregate([{"$match": {"created_at": {"$gte": d7}}},
                                                     {"$group": {"_id": {"e": "$event", "s": "$success"}, "c": {"$sum": 1}}}]):
            ev[f"{r['_id'].get('e')}/{'ok' if r['_id'].get('s') else 'fail'}"] = r["c"]
        out["auth_7g"] = ev
        ipf = {}
        async for r in db.auth_audit_logs.find({"created_at": {"$gte": d7}, "success": False},
                                               {"_id": 0, "ip": 1, "email": 1, "event": 1}).limit(20000):
            k = _m_ip(r.get("ip"))
            a = ipf.setdefault(k, {"n": 0, "hesap": set()})
            a["n"] += 1
            a["hesap"].add(_h(r.get("email")))
        out["basarisiz_giris_ip_7g"] = sorted(([k, v["n"], len(v["hesap"])] for k, v in ipf.items()), key=lambda x: -x[1])[:15]
        out["ip_engel_aktif"] = await db.ip_blocklist.count_documents({"blocked_until": {"$gte": now.isoformat()}})
        al = {}
        async for r in db.alerts.find({"created_at": {"$gte": d7}}, {"_id": 0, "kind": 1, "level": 1, "title": 1}).limit(2000):
            k = f"{r.get('kind')}/{r.get('level')}: {str(r.get('title') or '')[:80]}"
            al[k] = al.get(k, 0) + 1
        out["uyarilar_7g"] = sorted(al.items(), key=lambda x: -x[1])[:20]
        return out
    if mod == "paylasim":
        # Paylaşılan sepetler (salt okunur): boyut, oluşturma, kaynak; büyük sepet eşleşmesi.
        rows = []
        async for c in db.shared_carts.find({}, {"_id": 0, "id": 1, "items": 1, "created_at": 1, "source": 1,
                                                  "opened_at": 1}).sort("created_at", -1).limit(400):
            its = c.get("items") or []
            rows.append({"id": str(c.get("id"))[:4] + "…", "kalem": len(its), "adet": sum(int(i.get("quantity") or 1) for i in its),
                         "olusturma": str(c.get("created_at") or "")[:16], "kaynak": c.get("source") or "",
                         "_set": sorted({str(i.get("product_id")) for i in its})})
        big = [r for r in rows if r["kalem"] >= 15]
        # büyük sepet oturumlarının ürün kümesi ile eşleşme
        sess = []
        async for c in db.cart_sessions.find({"$expr": {"$gte": [{"$size": {"$ifNull": ["$items", []]}}, 15]}},
                                             {"_id": 0, "items.product_id": 1}).limit(200):
            sess.append(sorted({str(i.get("product_id")) for i in (c.get("items") or [])}))
        for r in big:
            r["ayni_urunlu_oturum"] = sum(1 for sset in sess if sset == r["_set"])
            r["kapsanan_oturum"] = sum(1 for sset in sess if set(r["_set"]) <= set(sset))
        for r in rows:
            r.pop("_set", None)
        return {"toplam": len(rows), "buyuk": big[:30],
                "boyut_dagilimi": {k: sum(1 for r in rows if lo <= r["kalem"] < hi) for k, lo, hi in
                                   (("1", 1, 2), ("2-5", 2, 6), ("6-14", 6, 15), ("15+", 15, 10**6))}}
    if mod == "yanlis_supurge":
        # SALT OKUR: kart süpürgesinin yanlışlıkla kapattığı PAZARYERİ siparişleri (kişisel veri yok).
        _q = {"auto_cancelled": True, "$or": [{"payment_method": "marketplace"},
              {"platform": {"$in": ["trendyol", "hepsiburada", "amazon", "temu", "n11"]}}]}
        _rows = await db.orders.find(_q, {"_id": 0, "order_number": 1, "platform": 1, "status": 1,
                                          "payment_status": 1, "cancelled_at": 1}).sort("cancelled_at", -1).to_list(200)
        return {"adet": len(_rows), "siparisler": _rows}
    if mod == "stok_deger_kontrol":
        # SALT OKUR: Kâr & Stok Değer sayfasının veri tutarlılığı (kişisel veri yok).
        from routes import reports_v2 as _R2S
        sv = await _R2S.stock_valuation(brand=None, category=None, manufacturer=None, _=None)
        t = sv["totals"]
        cats = sv["by_category"]
        _now_d = now.date()
        pur = await _R2S.purchases_by_date(start_date=str(_now_d - _td(days=365)), end_date=str(_now_d), _=None)
        neg = await db.products.count_documents({"is_deleted": {"$ne": True}, "$or": [{"stock": {"$lt": 0}}, {"variants.stock": {"$lt": 0}}]})
        return {
            "toplam": t,
            "kategori_sayisi": len(cats),
            "kategori_adet_toplami": sum(c["units"] for c in cats),
            "kategori_satis_toplami": round(sum(c["sale"] for c in cats), 2),
            "kategori_alis_toplami": round(sum(c.get("cost", 0) for c in cats), 2),
            "kategorisiz": [c for c in cats if c["name"] in ("—", "", "Kategorisiz", None)],
            "maliyet_orani_dusuk_kategoriler": [
                {"ad": c["name"], "adet": c["units"], "maliyetli_adet": c.get("valued_units", 0)}
                for c in cats if c["units"] and (c.get("valued_units", 0) / c["units"]) < 0.8][:30],
            "negatif_stoklu_urun": neg,
            "alim_365g": pur["totals"],
            "alim_eslesmeyen": [r["product"] for r in pur["rows"] if not r["matched"]][:30],
            "alim_marj_negatif": [{"urun": r["product"], "birim_alis": r["unit_cost"], "satis": r["sale_price"]}
                                  for r in pur["rows"] if r.get("margin_pct") is not None and r["margin_pct"] < 0][:30],
        }
    if mod == "vera_kilit":
        return await db.settings.find_one({"id": "vera_imalat_stok_kilidi_v1"}, {"_id": 0}) or {}
    if mod == "anahtar_durum":
        # SALT OKUR: teşhis anahtarı env'i tanımlı mı? DEĞER ASLA DÖNMEZ — yalnız var/yok ve
        # adında AUDIT geçen değişkenlerin ADLARI (yazım hatası tespiti için).
        _v = os.environ.get("SUPPORT_AUDIT_KEY")
        return {"tanimli": _v is not None, "bos_degil": bool((_v or "").strip()),
                "audit_adli_degiskenler": sorted(k for k in os.environ if "AUDIT" in k.upper())}
    if mod == "zayif_sifre":
        return await db.settings.find_one({"id": "zayif_uye_sifre_temizle_v1"}, {"_id": 0}) or {}
    if mod == "fiyat_v18":
        # Tek seferlik site fiyat güncellemesi sonucu (salt okunur; PII yok).
        doc = await db.settings.find_one({"id": "fiyat_guncelleme_v18"}, {"_id": 0}) or {}
        its = doc.get("items") or []
        return {"done": bool(doc.get("done")), "at": doc.get("at"), "changed": doc.get("changed"),
                "missing": doc.get("missing"), "mismatch": doc.get("mismatch"),
                "pazaryeri_sabitlenen": sum(1 for i in its if i.get("pazaryeri_sabit")),
                "varyant_guncellenen_urun": sum(1 for i in its if i.get("varyant")),
                "ornek": its[:8]}
    if mod == "sepet_mail":
        # Terkedilmiş sepet e-postası: kuru çalıştırma sayımı + son gönderimler (PII yok).
        import abandoned_cart_mail as _acm
        dry = await _acm.run(db, dry_run=True)
        last = await db.abandoned_cart_emails.find({}, {"_id": 0, "status": 1, "items": 1, "provider": 1,
                                                        "created_at": 1, "error": 1}).sort("created_at", -1).to_list(20)
        tracked = await db.cart_sessions.count_documents({"user_id": {"$nin": [None, ""]}, "total": {"$gt": 0}})
        return {"kuru_calistirma": dry, "uye_bagli_sepet": tracked, "son_gonderimler": last}
    if mod == "hbtoplam":
        # HB siparişlerinde kayıtlı total ile kalem toplamı (Σ price×qty) uyuşmazlığı (PII yok).
        rows, n = [], 0
        agg = {"uyumsuz": 0, "ilk_kalem_fiyatina_esit": 0, "faturali_uyumsuz": 0,
               "eksik_fatura_tutari_toplam": 0.0}
        async for o in db.orders.find({"platform": "hepsiburada"},
                                      {"_id": 0, "order_number": 1, "total": 1, "subtotal": 1, "status": 1,
                                       "items.price": 1, "items.quantity": 1, "invoice_issued": 1,
                                       "invoice_number": 1, "created_at": 1}):
            n += 1
            its = o.get("items") or []
            try:
                sub = round(sum(float(i.get("price") or 0) * int(i.get("quantity") or 1) for i in its), 2)
                tot = round(float(o.get("total") or 0), 2)
            except Exception:
                continue
            if abs(sub - tot) > 0.5:
                agg["uyumsuz"] += 1
                first = round(float((its[0] if its else {}).get("price") or 0) * int((its[0] if its else {}).get("quantity") or 1), 2)
                if abs(first - tot) <= 0.5:
                    agg["ilk_kalem_fiyatina_esit"] += 1
                if o.get("invoice_issued"):
                    agg["faturali_uyumsuz"] += 1
                    agg["eksik_fatura_tutari_toplam"] += round(sub - tot, 2)
                rows.append({"no": o.get("order_number"), "kalem": len(its), "kalem_toplam": sub, "total": tot,
                             "fark": round(sub - tot, 2), "status": o.get("status"),
                             "fatura": o.get("invoice_number") if o.get("invoice_issued") else None,
                             "at": str(o.get("created_at") or "")[:10]})
        agg["eksik_fatura_tutari_toplam"] = round(agg["eksik_fatura_tutari_toplam"], 2)
        out["hb_siparis"] = n
        out["ozet"] = agg
        out["liste"] = sorted(rows, key=lambda r: r["at"], reverse=True)[:300]
        return out
    if mod == "earsiv":
        # SALT OKUNUR Doğan e-Arşiv sorgusu: operasyon listesi + durum/sorgu operasyonlarının
        # imzaları; "status/get/query" içeren operasyonlarla eski faturanın UUID'si sorgulanır.
        # HİÇBİR gönderme/iptal operasyonu çağrılmaz.
        q = (eposta or "").strip()
        o = await db.orders.find_one({"$or": [{"order_number": q}, {"hepsiburada_order_number": q}]},
                                     {"_id": 0, "order_number": 1, "invoice_uuid": 1, "invoice_number": 1,
                                      "invoice_issued": 1, "invoice_type": 1, "invoice_history": 1})
        out["siparis"] = o
        ds = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
        if not (ds.get("enabled") and ds.get("username")):
            out["hata"] = "Doğan yapılandırılmamış"
            return out
        from dogan_client import DoganClient
        from fastapi.concurrency import run_in_threadpool
        cli = DoganClient(username=ds["username"], password=ds["password"], is_test=ds.get("is_test", True))
        ops = await run_in_threadpool(cli._list_earsiv_operations)
        out["earsiv_ops"] = ops
        sigs = {}
        def _sig(name):
            try:
                c = cli._get_earsiv_client()
                for sv in c.wsdl.services.values():
                    for pt in sv.ports.values():
                        _o = getattr(pt.binding, "_operations", {})
                        if name in _o:
                            return str(_o[name].input.signature())
            except Exception as e:
                return f"err {e}"
            return ""
        # İptal operasyonunun ŞEMASI (çağırmadan): üst imza + iç içe tip imzaları
        def _deep(name):
            try:
                c = cli._get_earsiv_client()
                for sv in c.wsdl.services.values():
                    for pt in sv.ports.values():
                        _o = getattr(pt.binding, "_operations", {})
                        if name in _o:
                            body = _o[name].input.body
                            outp = {"top": str(_o[name].input.signature())}
                            try:
                                for el_name, el in body.type.elements:
                                    try:
                                        outp[el_name] = el.type.signature(schema=c.wsdl.types)
                                    except Exception as e2:
                                        outp[el_name] = f"(sig err {e2})"
                            except Exception as e1:
                                outp["_err"] = str(e1)
                            return outp
            except Exception as e:
                return {"err": str(e)}
            return {}
        out["iptal_semasi"] = await run_in_threadpool(_deep, "CancelEArchiveInvoice")
        cands = [x for x in ops if any(t in x.lower() for t in ("status", "query", "report", "getinvoice", "search"))
                 and not any(t in x.lower() for t in ("cancel", "send", "write", "update", "delete", "insert"))]
        for n in cands[:8]:
            sigs[n] = await run_in_threadpool(_sig, n)
        out["sorgu_imzalari"] = sigs
        uuid = (o or {}).get("invoice_uuid") or ""
        if uuid and cands:
            from zeep.helpers import serialize_object
            res = {}
            for n in cands[:4]:
                sig = sigs.get(n) or ""
                import re as _re
                params = [p.split(":")[0].strip() for p in _re.findall(r"(\w+\s*:\s*[^,]+)", sig)]
                params = [p for p in params if p and p != "REQUEST_HEADER"]
                for pn in params[:2]:
                    for payload in ({"UUID": uuid}, uuid):
                        try:
                            r = await run_in_threadpool(lambda: getattr(cli._get_earsiv_client().service, n)(
                                REQUEST_HEADER=cli._make_header(), **{pn: payload}))
                            res[f"{n}/{pn}"] = str(serialize_object(r))[:1500]
                            break
                        except Exception as e:
                            res[f"{n}/{pn}/{type(payload).__name__}"] = f"err: {str(e)[:200]}"
            out["sorgu_sonuc"] = res
        return out
    return {"detay": "mod=kargo|uyelik|yavas|meta|siparis|hbtoplam|earsiv"}


async def _kupon_teshis(kod: str = "") -> dict:
    """SALT OKUNUR: kupon/kampanya kaydı + ŞU AN geçerli mi (ödeme ile aynı değerlendirme)
    + bu kodla verilen siparişlerin TR gününe göre dağılımı ve son kullanım anı. PII yok."""
    import re as _re
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    kod = (kod or "").strip()
    c = await db.coupons.find_one({"code": {"$regex": f"^{_re.escape(kod)}$", "$options": "i"}}, {"_id": 0})
    if not c:
        return {"bulunamadi": kod}
    alan = {k: c.get(k) for k in ("id", "code", "title", "type", "value", "free_shipping", "auto_apply",
                                  "is_active", "start_at", "end_at", "usage_limit", "redeemed_count",
                                  "min_cart_total", "payment_methods", "created_at", "updated_at")}
    try:
        from routes.coupons import _evaluate_single
        ev = await _evaluate_single(c, 5000.0, [], None, "", "credit_card")
    except TypeError:
        ev = {"not": "imza farklı — değerlendirme atlandı"}
    except Exception as e:
        ev = {"hata": f"{type(e).__name__}: {str(e)[:200]}"}
    gun, son = {}, ""
    async for o in db.orders.find({"coupon_code": {"$regex": f"^{_re.escape(kod)}$", "$options": "i"}},
                                  {"_id": 0, "created_at": 1, "status": 1, "payment_status": 1}):
        ca = str(o.get("created_at") or "")
        try:
            d = (_dt.fromisoformat(ca.replace("Z", "+00:00")) + _td(hours=3)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            d = ca[:16]
        g = d[:10]
        gun[g] = gun.get(g, 0) + 1
        if d > son:
            son = d
    # Otomatik kampanyalar siparişe applied_promotions.coupon_id ile, ödenenler coupon_redemptions'a yazılır.
    def _trg(ca):
        try:
            return (_dt.fromisoformat(str(ca).replace("Z", "+00:00")) + _td(hours=3)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(ca)[:16]
    end = str(c.get("end_at") or "")
    ap_gun, ap_sonra, ap_son = {}, [], ""
    async for o in db.orders.find({"applied_promotions.coupon_id": c.get("id")},
                                  {"_id": 0, "order_number": 1, "created_at": 1, "status": 1, "payment_status": 1}):
        d = _trg(o.get("created_at"))
        ap_gun[d[:10]] = ap_gun.get(d[:10], 0) + 1
        ap_son = max(ap_son, d)
        if end and str(o.get("created_at") or "") > end:
            ap_sonra.append({"no": o.get("order_number"), "tarih_TR": d, "statu": o.get("status"),
                             "odeme": o.get("payment_status")})
    rd_gun, rd_sonra = {}, 0
    async for r in db.coupon_redemptions.find({"coupon_id": c.get("id")}, {"_id": 0, "created_at": 1}):
        d = _trg(r.get("created_at"))
        rd_gun[d[:10]] = rd_gun.get(d[:10], 0) + 1
        if end and str(r.get("created_at") or "") > end:
            rd_sonra += 1
    return {"kupon": alan, "simdi_degerlendirme": ev, "simdi_utc": _dt.now(_tz.utc).isoformat(),
            "siparis_gun_TR": dict(sorted(gun.items())), "son_kullanim_TR": son,
            "otomatik_siparis_gun_TR": dict(sorted(ap_gun.items())), "otomatik_son_TR": ap_son,
            "BITISTEN_SONRA_siparis": ap_sonra[:50], "kullanim_kaydi_gun_TR": dict(sorted(rd_gun.items())),
            "BITISTEN_SONRA_kullanim_kaydi": rd_sonra}


async def _safe_block(coro):
    """Bir teşhis bloğunun hatası TÜM /health yanıtını düşürmesin.

    Teşhis blokları tek bir sözlük içinde yan yana kuruluyor; birinde hata çıkınca
    yanıtın tamamı 500 oluyor ve geri kalan bloklar da okunamıyordu. Salt okuma.
    Ayrıca sonuç JSON'a yazılabilir hâle getirilir (NaN/Infinity temizliği).
    """
    try:
        res = await coro
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}
    try:
        bad: list = []
        res = _json_safe(res, bad)
        if bad and isinstance(res, dict):
            res["bozuk_sayilar"] = {
                "adet": len(bad), "alanlar": bad,
                "not": ("NaN/Infinity JSON'a yazılamaz; bu alanlar None yapıldı. "
                        "Kaynak genellikle sıfıra bölme (boş dönemde ortalama)."),
            }
        return res
    except Exception as _se:
        return {"hata": f"temizleme: {type(_se).__name__}: {str(_se)[:200]}"}


async def _trendyol_live_count(start_date: str = "", end_date: str = "") -> dict:
    """SALT OKUNUR: Trendyol'un KENDİ verisiyle bizim sayımızı karşılaştır.

    "Trendyol panelinde çok farklı bir rakam var" sorusunun tek kesin cevabı:
    aynı dönemi Trendyol'a sorup yanıtı bizim kayıtlarımızla yan yana koymak.

    Trendyol /orders ucu SİPARİŞ değil KARGO PAKETİ döndürür; bir sipariş birden
    çok pakete bölünebilir ve paketler AYNI orderNumber'ı taşır. Bu yüzden üç ayrı
    sayı raporlanır: paket, tekil sipariş numarası, ve bizim kayıtlarımızdaki adet.

    YALNIZ OKUR: Trendyol'a GET atar, hiçbir sipariş oluşturmaz/güncellemez,
    veritabanına yazmaz. Sipariş çekme akışına DOKUNMAZ.
    """
    try:
        from routes.reports import _iso_range
        from routes.integrations_trendyol import get_trendyol_config
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from trendyol_client import TrendyolClient

        s, e = _iso_range(start_date or None, end_date or None)
        cfg = await get_trendyol_config()
        if not cfg.get("is_active"):
            return {"hata": "Trendyol entegrasyonu aktif değil"}
        cli = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                             api_secret=cfg["api_secret"], mode=cfg["mode"])
        _s_dt = _dt.fromisoformat(s.replace("Z", "+00:00"))
        _e_dt = _dt.fromisoformat(e.replace("Z", "+00:00"))
        # ÖNEMLİ: Trendyol tarih süzgeci PAKET tarihine göre çalışır; paket, siparişten
        # GÜNLER SONRA açılabilir (veya sipariş dönem başından önce verilip paketi
        # dönem içinde açılmış olabilir). Sipariş tarihine göre eksiksiz saymak için
        # çekim penceresi iki yana GENİŞLETİLİR, süzme yine orderDate ile yapılır.
        _PAD = _td(days=21)
        _fetch_s, _fetch_e = _s_dt - _PAD, min(_e_dt + _PAD, _dt.now(_tz.utc))

        paketler, sayfa_sayisi, hata = {}, 0, ""
        _dilim_beyan, _dilim_alinan = [], []
        # Trendyol tarih aralığını sınırlar → 10 günlük dilimlere böl.
        _cur = _fetch_s
        while _cur < _fetch_e:
            _nxt = min(_cur + _td(days=10), _fetch_e)
            _dilim_alinan.append(0)
            page = 0
            while page < 60:
                try:
                    resp = await cli.get_orders(
                        start_date_ms=int(_cur.timestamp() * 1000),
                        end_date_ms=int(_nxt.timestamp() * 1000),
                        size=200, page=page, order_by_field="CreatedDate")
                except Exception as _qe:
                    hata = f"{type(_qe).__name__}: {str(_qe)[:200]}"
                    break
                sayfa_sayisi += 1
                chunk = resp.get("content") or []
                # EKSİKSİZLİK KANITI: Trendyol her yanıtta o dilim için toplam kayıt
                # sayısını (totalElements) bildirir. Dilim başına bir kez toplanır;
                # sonunda "Trendyol kaç dedi / biz kaç topladık" karşılaştırılır.
                if page == 0:
                    _dilim_beyan.append(int(resp.get("totalElements") or 0))
                _dilim_alinan[-1] = _dilim_alinan[-1] + len(chunk)
                for t in chunk:
                    _pid = str(t.get("id") or "")
                    if _pid:
                        paketler[_pid] = t
                if page + 1 >= int(resp.get("totalPages") or 0):
                    break
                page += 1
            if hata:
                break
            _cur = _nxt

        # Trendyol'un kendi orderDate'ine göre DÖNEME düşenleri say (TR sınırları s..e)
        #
        # SAAT DİLİMİ (kanıtlanmış): Trendyol'un orderDate ms-epoch'u UTC değil TÜRKİYE
        # yerel saatini taşır — ms UTC gibi çözüldüğünde panelin TR saatiyle BİREBİR aynı
        # rakamlar çıkıyor (örn. 11559154819: panel 21:09, çözüm 21:08:57). Gerçek UTC
        # anı için TY_TR_OFFSET_HOURS çıkarılmalı; aksi halde s..e (gerçek UTC sınırları)
        # ile kıyas 3 saat kayar → ay/gün sınırındaki siparişler yanlış kovaya düşer.
        # integrations_common._ms_to_iso ile AYNI dönüşüm (tek kaynak).
        from routes.integrations_common import TY_TR_OFFSET_HOURS as _TYOFF

        def _iso(ms):
            try:
                return (_dt.fromtimestamp(int(ms) / 1000, tz=_tz.utc)
                        - _td(hours=_TYOFF)).isoformat()
            except Exception:
                return ""
        _in, _durum, _nos, _gun = [], {}, set(), {}
        _adet = 0            # ÜRÜN ADEDİ (panel "Adet" sütunu bunu gösteriyor)
        _adet_iptal = 0
        _brut = 0.0          # paket totalPrice toplamı
        _brut_iptalsiz = 0.0
        _CANCEL_TY = ("Cancelled", "UnSupplied", "Returned", "UnDeliveredAndReturned")
        for t in paketler.values():
            _od = _iso(t.get("orderDate"))
            if not _od or not (s <= _od <= e):
                continue
            _in.append(t)
            _st = str(t.get("shipmentPackageStatus") or t.get("status") or "?")
            _durum[_st] = _durum.get(_st, 0) + 1
            _q = 0
            for _ln in (t.get("lines") or []):
                try:
                    _q += max(1, int(_ln.get("quantity") or 1))
                except Exception:
                    _q += 1
            _adet += _q
            try:
                _tp = float(t.get("totalPrice") or 0)
            except Exception:
                _tp = 0.0
            _brut += _tp
            if _st in _CANCEL_TY:
                _adet_iptal += _q
            else:
                _brut_iptalsiz += _tp
            _nos.add(str(t.get("orderNumber") or ""))
            _g = (_dt.fromisoformat(_od) + _td(hours=3)).date().isoformat()
            _gun[_g] = _gun.get(_g, 0) + 1

        # ── PANEL HANGİ TANIMI SAYIYOR? ─────────────────────────────────────
        # Trendyol'un kendi kaynakları bile birbirini tutmuyor (eylül: panel 1.566,
        # dağılım raporu 1.406, sipariş listesi Excel 1.361). Fark tanımdan gelir:
        # sipariş mi paket mi, hangi TARİHE göre, iptaller dahil mi. Aynı çekilmiş
        # veri kümesi üzerinden tüm makul tanımlar yan yana sayılır ki panelin
        # rakamı hangi satıra denk geliyorsa tanım kesinleşsin. Salt okuma.
        def _ms_tr_gun(ms):
            """ms epoch → TR yerel gün (Trendyol epoch'u TR saatini taşır)."""
            try:
                return _dt.fromtimestamp(int(ms) / 1000, tz=_tz.utc).date().isoformat()
            except Exception:
                return ""
        _s_gun = str(start_date or "")[:10]
        _e_gun = str(end_date or "")[:10]

        def _gun_icinde(g):
            return bool(g) and (not _s_gun or g >= _s_gun) and (not _e_gun or g <= _e_gun)

        _tanim = {}
        try:
            _pk_sip, _pk_pkt, _pk_adet = set(), 0, 0
            _sip_iptalsiz = set()
            for t in paketler.values():
                _g_ord = _ms_tr_gun(t.get("orderDate"))
                _g_pkt = _ms_tr_gun(t.get("packageLastModifiedDate")
                                    or t.get("lastModifiedDate")
                                    or t.get("orderDate"))
                _st2 = str(t.get("shipmentPackageStatus") or t.get("status") or "?")
                _on2 = str(t.get("orderNumber") or "")
                if _gun_icinde(_g_pkt):
                    _pk_pkt += 1
                    if _on2:
                        _pk_sip.add(_on2)
                    for _ln in (t.get("lines") or []):
                        try:
                            _pk_adet += max(1, int(_ln.get("quantity") or 1))
                        except Exception:
                            _pk_adet += 1
                if _gun_icinde(_g_ord) and _st2 not in _CANCEL_TY and _on2:
                    _sip_iptalsiz.add(_on2)
            _tanim = {
                "A_siparis_tarihine_gore_paket": len(_in),
                "B_siparis_tarihine_gore_TEKIL_SIPARIS": len(_nos),
                "C_siparis_tarihine_gore_URUN_ADEDI": _adet,
                "D_siparis_tarihine_gore_IPTALSIZ_SIPARIS": len(_sip_iptalsiz),
                "E_PAKET_tarihine_gore_paket": _pk_pkt,
                "F_PAKET_tarihine_gore_TEKIL_SIPARIS": len(_pk_sip),
                "G_PAKET_tarihine_gore_URUN_ADEDI": _pk_adet,
                "not": ("Panelde gördüğünüz rakam bu satırlardan HANGİSİNE eşitse "
                        "panelin tanımı odur. Bizim raporumuz B satırını kullanır "
                        "(siparişin verildiği tarihe göre tekil sipariş)."),
            }
        except Exception as _te:
            _tanim = {"hata": f"{type(_te).__name__}: {str(_te)[:200]}"}

        # Bizim kayıtlarımız (aynı dönem, aynı ölçüt)
        _bizim = await db.orders.count_documents({
            "platform": "trendyol",
            "$expr": {"$and": [
                {"$gte": [{"$ifNull": ["$marketplace_order_date", "$created_at"]}, s]},
                {"$lte": [{"$ifNull": ["$marketplace_order_date", "$created_at"]}, e]}]}})

        # RAPOR EKRANIYLA BİREBİR AYNI SAYIM.
        # Yukarıdaki _bizim HAM sayımdır: ticimax_history'den gelen eski kopyalar ve aynı
        # sipariş numarasının ikinci belgesi de sayılır. Rapor ekranı bunları eler; bu
        # yüzden ham sayı Trendyol'dan BÜYÜK çıkıp sahte "fazla sipariş" izlenimi veriyordu
        # (haziran: ham 865 / Trendyol 444). Aşağısı raporların kullandığı tam boru hattıdır:
        #   merge_match (ticimax kopyaları) → canonical_order_stages (sipariş no başına tek
        #   belge) → geçerli satış statüleri. Salt okuma.
        _rapor = {"hata": ""}
        try:
            from routes.reports import _EXCLUDED_STATUSES as _EXC
            from routes.report_dedup import (
                merge_match as _mm, canonical_order_stages as _cos,
                effective_order_date_match as _eodm, load_dup_order_numbers as _ldc)
            try:
                await _ldc()          # ticimax kopya listesi (rapor isteğinde dependency yapar)
            except Exception:
                pass
            _base = [
                {"$match": _mm({"$and": [_eodm(s, e), {"platform": "trendyol"}]})},
                *_cos(),
            ]
            _r1 = await db.orders.aggregate(_base + [{"$count": "n"}]).to_list(1)
            _r2 = await db.orders.aggregate(_base + [
                {"$match": {"status": {"$nin": list(_EXC)}}},
                {"$group": {"_id": None, "n": {"$sum": 1},
                            "tutar": {"$sum": {"$ifNull": ["$total", 0]}},
                            "adet": {"$sum": {"$sum": {"$map": {
                                "input": {"$ifNull": ["$items", []]}, "as": "it",
                                "in": {"$ifNull": ["$$it.quantity", 1]}}}}}}},
            ]).to_list(1)
            _d2 = _r2[0] if _r2 else {}
            _rapor = {
                "tekil_belge": int(_r1[0]["n"]) if _r1 else 0,
                "gecerli_satis_siparis": int(_d2.get("n") or 0),
                "gecerli_satis_urun_adedi": int(_d2.get("adet") or 0),
                "gecerli_satis_tutar": round(float(_d2.get("tutar") or 0), 2),
                "hata": "",
                "not": ("'tekil_belge' Trendyol'un 'tekil_siparis_no' değeriyle "
                        "eşleşmeli. 'gecerli_satis_*' rapor ekranındaki Trendyol "
                        "satırıdır: iptal/iade/ödenmemiş elenmiş hâli."),
            }
        except Exception as _re:
            _rapor = {"hata": f"{type(_re).__name__}: {str(_re)[:200]}"}
        _bizde_var = set()
        async for o in db.orders.find(
                {"platform": "trendyol", "order_number": {"$in": list(_nos)[:20000]}},
                {"_id": 0, "order_number": 1}):
            _bizde_var.add(str(o.get("order_number")))
        _eksik = sorted(_nos - _bizde_var)

        return {
            "aralik": {"baslangic": start_date, "bitis": end_date, "utc": [s, e]},
            "trendyol": {
                "paket": len(_in),
                "tekil_siparis_no": len(_nos),
                "cok_paketli_siparis_farki": len(_in) - len(_nos),
                "statu": _durum,
                "gunluk_paket": dict(sorted(_gun.items())),
                # PANEL KARŞILAŞTIRMASI: Trendyol panelindeki "Adet" sütunu SİPARİŞ değil
                # ÜRÜN ADEDİ. Doğru kıyas için kalem adetleri toplanır.
                "urun_adedi_brut": _adet,
                "urun_adedi_iptal_iade": _adet_iptal,
                "urun_adedi_iptalsiz": _adet - _adet_iptal,
                "tutar_paket_toplami": round(_brut, 2),
                "tutar_iptalsiz": round(_brut_iptalsiz, 2),
            },
            "bizde": {
                "kayit": _bizim,
                "ham_kayit_notu": ("HAM sayım: ticimax_history kopyaları ve aynı sipariş "
                                   "numarasının ikinci belgesi DAHİL. Rapor ekranıyla "
                                   "kıyas için 'rapor_ekrani' bloğunu kullanın."),
                "rapor_ekrani": _rapor,
                "trendyolda_olup_bizde_OLMAYAN_siparis": len(_eksik),
                "ornek_eksik": _eksik[:25],
            },
            "PANEL_TANIM_ARAMA": _tanim,
            "cekilen_sayfa": sayfa_sayisi,
            "cekim_penceresi": [_fetch_s.isoformat(), _fetch_e.isoformat()],
            "cekilen_toplam_paket": len(paketler),
            "eksiksizlik": {
                "trendyol_beyan_toplam": sum(_dilim_beyan),
                "biz_aldik_toplam": sum(_dilim_alinan),
                "tekil_paket": len(paketler),
                "not": ("trendyol_beyan = Trendyol'un totalElements toplamı. "
                        "'biz_aldik' ondan KÜÇÜKSE sayfalama eksik demektir. "
                        "'tekil_paket' daha küçükse dilimler örtüşmüştür (zararsız)."),
            },
            "hata": hata,
            "not": ("Trendyol /orders KARGO PAKETİ döndürür; bir sipariş birden çok "
                    "pakete bölünebilir ve paketler AYNI orderNumber'ı taşır. "
                    "'paket' panelle, 'tekil_siparis_no' bizim sayımızla kıyaslanmalı. "
                    "'trendyolda_olup_bizde_OLMAYAN' > 0 ise gerçek veri kaybı var."),
        }
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _stock_drift_block(limit: int = 50) -> dict:
    """SALT OKUNUR · KATALOG GENELİ: hangi üründe ne kadar HAYALET stok var?

    Hayalet stok = düşüm hareketi HİÇ OLMAYAN bir siparişe yapılmış geri-ekleme.
    (Sipariş stoktan hiç düşmemiş, ama iptal/iade edilince stoğa geri eklenmiş.)

    Yöntem:
      1) Tüm geri-ekleme hareketleri taranır (adet ve bağlı sipariş toplanır)
      2) Bu siparişlerin düşüm hareketi olup olmadığına TEK seferde bakılır
      3) Düşümü olmayanların geri-eklemeleri ürün bazında toplanır

    HİÇBİR YAZMA YAPMAZ. Rakam bir ÖNERİDİR — fiziksel sayımla karşılaştırılmalı,
    otomatik düşülmemelidir.
    """
    try:
        RESTORE = ["order_cancelled", "auto_cancel_expired", "havale_auto_cancel",
                   "return_restock", "order_returned", "manual_increment",
                   "backfill_increment", "order_deleted_restock",
                   "influencer_seeding_restock"]
        DEDUCT = ["order_created", "order_imported", "manual_decrement",
                  "backfill_decrement", "influencer_seeding"]

        rows, oids = [], set()
        async for m in db.stock_movements.find(
                {"type": {"$in": RESTORE}},
                {"_id": 0, "order_id": 1, "type": 1, "items": 1, "moves": 1}):
            oid = str(m.get("order_id") or "")
            for it in ((m.get("items") or []) + (m.get("moves") or [])):
                if not isinstance(it, dict):
                    continue
                d = it.get("delta")
                if d is None:
                    d = it.get("qty")          # eski pazaryeri iade satırları
                if d is None and it.get("old") is not None and it.get("new") is not None:
                    try:
                        d = int(it["new"]) - int(it["old"])
                    except Exception:
                        d = None
                try:
                    d = int(d or 0)
                except Exception:
                    d = 0
                if d <= 0:
                    continue
                key = str(it.get("product_id") or "") or ("bc:" + str(it.get("barcode") or ""))
                if not key or key == "bc:":
                    continue
                rows.append((oid, key, d, str(m.get("type") or "?")))
                if oid:
                    oids.add(oid)

        dusenler = set()
        _ol = list(oids)
        for i in range(0, len(_ol), 4000):
            async for m in db.stock_movements.find(
                    {"order_id": {"$in": _ol[i:i + 4000]}, "type": {"$in": DEDUCT}},
                    {"_id": 0, "order_id": 1}):
                dusenler.add(str(m.get("order_id")))

        per: dict = {}
        toplam_h = toplam_g = 0
        for oid, key, d, tip in rows:
            p = per.setdefault(key, {"hayalet": 0, "gercek": 0, "tipler": {}})
            if oid and oid in dusenler:
                p["gercek"] += d
                toplam_g += d
            else:
                p["hayalet"] += d
                p["tipler"][tip] = p["tipler"].get(tip, 0) + d
                toplam_h += d

        en_kotu = sorted(per.items(), key=lambda kv: -kv[1]["hayalet"])[:max(1, min(limit, 300))]
        _pids = [k for k, _ in en_kotu if not k.startswith("bc:")]
        _bcs = [k[3:] for k, _ in en_kotu if k.startswith("bc:")]
        _info: dict = {}
        async for p in db.products.find(
                {"$or": [{"id": {"$in": _pids}}, {"variants.barcode": {"$in": _bcs}}]},
                {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "stock": 1,
                 "is_active": 1, "variants": 1}):
            _tot = sum(int(v.get("stock") or 0) for v in (p.get("variants") or [])) \
                if p.get("variants") else int(p.get("stock") or 0)
            _rec = {"ad": p.get("name"), "stok_kodu": p.get("stock_code"),
                    "aktif": p.get("is_active"), "canli_stok": _tot}
            _info[str(p.get("id"))] = _rec
            for v in (p.get("variants") or []):
                if v.get("barcode"):
                    _info.setdefault("bc:" + str(v["barcode"]), _rec)

        liste = []
        for k, v in en_kotu:
            if v["hayalet"] <= 0:
                continue
            d = dict(_info.get(k) or {"ad": "(ürün bulunamadı)", "canli_stok": None})
            d.update({"anahtar": k, "hayalet_adet": v["hayalet"],
                      "gercek_iade_adet": v["gercek"], "kaynak_tipler": v["tipler"]})
            if d.get("canli_stok") is not None:
                d["duzeltilmis_tahmin"] = d["canli_stok"] - v["hayalet"]
            liste.append(d)

        return {
            "ozet": {"etkilenen_urun": sum(1 for v in per.values() if v["hayalet"] > 0),
                     "toplam_hayalet_adet": toplam_h,
                     "dusumu_olan_siparise_yapilan_gercek_iade": toplam_g,
                     "taranan_geri_ekleme_satiri": len(rows)},
            "not": ("hayalet_adet = stoktan HİÇ DÜŞMEMİŞ bir siparişe yapılan geri-ekleme. "
                    "duzeltilmis_tahmin bir ÖNERİDİR; fiziksel sayımla doğrulanmadan "
                    "stoktan düşülmemelidir."),
            "en_kotu": liste,
        }
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


async def _sales_audit_block(start_date: str = "", end_date: str = "",
                             source: str = "") -> dict:
    """Bağımsız satış denetimi (routes.reports.sales_audit_summary). Tarih verilmezse
    içinde bulunulan TÜRKİYE ayının 1'inden bugüne. Hata sağlık ucunu BOZMAZ.

    `source` verilirse (trendyol/site/hepsiburada/temu) denetim o kanala daraltılır —
    "pazaryeri panelindeki rakam bizimkiyle tutuyor mu" sorusu ancak kanal kanal
    ölçülerek cevaplanabilir."""
    try:
        from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
        from routes.reports import sales_audit_summary as _sas
        from routes.report_dedup import load_dup_order_numbers as _ldc_sa
        try:
            await _ldc_sa()        # kopya elemesi soğuk süreçte de yüklü olsun
        except Exception:
            pass
        _tr_now = _dt2.now(_tz2.utc) + _td2(hours=3)
        _s = start_date or _tr_now.replace(day=1).date().isoformat()
        _e = end_date or _tr_now.date().isoformat()
        return await _sas(_s, _e, (source or None))
    except Exception as _e:
        return {"hata": f"{type(_e).__name__}: {str(_e)[:300]}"}


from starlette.requests import Request as _StarletteRequest  # noqa: E402  (yalnız /health X-Audit-Key)


_HEALTH_PING_CACHE: dict = {}


@api_router.get("/health")
async def health(request: _StarletteRequest, diag: str = "", audit_key: str = "",
                 audit_start: str = "", audit_end: str = "", stock_q: str = "",
                 drift: str = "", ty_count: str = "", orders_q: str = "",
                 clock: str = "", audit_source: str = "", pusula_q: str = "",
                 ty_list: str = "", ty_siparis_q: str = "", iade_list: str = "",
                 rapor_q: str = "", asistan_log: str = "", iptal_tarih_q: str = "",
                 iade_sayfa_q: str = "", ty_urun_probe: str = "", zero_sweep: str = "",
                 barkod_dokum: str = "", hb_yetim_q: str = "", ty_kiyas: str = "",
                 ty_batch_q: str = "", ty_batch_barkod: str = "", kupon_q: str = "",
                 destek_q: str = "", destek_email: str = ""):
    # Denetim 2026-10 #4: anahtar tercihen X-Audit-Key başlığından (URL/log'a düşmez); query
    # param yalnız geriye uyum içindir ve env anahtarı tanımlı değilse hiçbiri işe yaramaz.
    try:
        _hdr_key = (request.headers.get("x-audit-key") or "").strip()
    except Exception:
        _hdr_key = ""
    if _hdr_key:
        audit_key = _hdr_key
    # Deploy teşhisi: hangi commit çalışıyor (Railway RAILWAY_GIT_COMMIT_SHA sağlar).
    _sha = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_SHA") or "")[:12]
    # HAFİF MOD (varsayılan): yalnız sürüm + tek bir hızlı DB ping (5 sn zaman aşımı). Ağır teşhis
    # blokları (log aggregation'ları, koleksiyon taramaları) YALNIZ ?diag=1 ile çalışır — sağlık
    # ucu izleme araçlarınca sık çağrıldığından veritabanını yormamalı.
    # GÜVENLİK (denetim #24): ağır teşhis modu (?diag=1) kimliksiz çalışıyordu (kargo takip, influencer,
    # tanılama verisi + pahalı sorgular). Artık YALNIZ geçerli destek anahtarı (X-Audit-Key) ya da
    # doğrulanmış personel oturumu ile açılır; aksi hâlde sabit ve ucuz canlılık yanıtı döner.
    if diag:
        _diag_ok = _audit_ok(audit_key)
        if not _diag_ok:
            try:
                from routes.deps import optional_admin as _opt_admin
                _diag_ok = (await _opt_admin(request)) is not None
            except Exception:
                _diag_ok = False
        if not _diag_ok:
            diag = ""
    if not diag:
        import asyncio as _aio_h
        import time as _time_h
        # Canlılık sonucu kısa süre önbelleklenir: sık izleme/kötüye kullanım her istekte DB'ye gitmez.
        _hc = _HEALTH_PING_CACHE
        if _hc.get("at") and _time_h.monotonic() - _hc["at"] < 5:
            _db_ok, _db_err = _hc["ok"], _hc["err"]
        else:
            _db_ok, _db_err = True, ""
            try:
                await _aio_h.wait_for(db.command("ping"), timeout=5)
            except Exception as _pe:
                _db_ok, _db_err = False, str(_pe)[:120]
            _hc.update({"at": _time_h.monotonic(), "ok": _db_ok, "err": _db_err})
        return {"status": "healthy" if _db_ok else "degraded", "version": _sha, "db": "ok" if _db_ok else "hata"}
    # Havale 72s taraması sağlığı (PII yok: yalnız zaman/sayaç/hata) — teşhis.
    _hv = {}
    try:
        _hv = await db.settings.find_one({"id": "havale_sweep_health"}, {"_id": 0, "id": 0}) or {}
    except Exception as _e:
        _hv = {"error": f"okunamadı: {str(_e)[:80]}"}
    # Zamanlayıcı canlı mı? (job listesi — yalnız id'ler)
    _jobs = None
    try:
        from scheduler import _scheduler as _sched
        _jobs = sorted(j.id for j in _sched.get_jobs())[:60] if _sched else []
    except Exception as _e:
        _jobs = [f"okunamadı: {str(_e)[:60]}"]
    # Influencer kargo takip taraması sağlığı + MNG WSDL operasyon adları (PII yok) — teşhis.
    _pt = {}
    try:
        _pt = await db.settings.find_one({"id": "influencer_pr_track_health"},
                                         {"_id": 0, "status": 1, "last_finish_at": 1, "candidates": 1, "checked": 1,
                                          "found": 1, "errors": 1, "mng_ops": 1, "mng_ops_at": 1, "by_date": 1, "nz_retro": 1}) or {}
    except Exception as _e:
        _pt = {"error": f"okunamadı: {str(_e)[:80]}"}
    _rp = None   # iade sondası KALDIRILDI (indeks-siz tarama)
    # TEŞHİS (yalnız ?diag=1, DB'ye DOKUNMAZ): kargo etiketi barkodu sunucuda üretilebiliyor mu?
    # python-barcode/Pillow eksikse etiket web-font yedeğine düşer → kurye okuyamayabilir.
    _op = None   # tekil sipariş sondası kaldırıldı (indekssiz telefon taraması)
    _lbc = {}
    try:
        from routes.orders import _render_label_barcode_png_b64 as _lb
        for _c in ("W11676", "INF07426343"):
            _png = _lb(_c)
            _lbc[_c] = {"ok": bool(_png), "png_bytes": (len(_png) * 3 // 4) if _png else 0}
    except Exception as _e:
        _lbc = {"error": str(_e)[:120]}
    # TEŞHİS: takip numarası bekleyen influencer gönderileri (kişisel veri yok; MNG yanıt özeti).
    try:
        _pp = []
        async for _r in db.influencer_pr.find({
            "$and": [
                {"$or": [{"cargo_tracking_no": {"$nin": [None, ""]}}, {"cargo_barcode": {"$nin": [None, ""]}}]},
                {"$or": [{"cargo_gonderi_no": {"$in": [None, ""]}}, {"cargo_gonderi_no": {"$exists": False}}]},
            ]}, {"_id": 0, "influencer_name": 1, "shipped_at": 1, "created_at": 1, "cargo_tracking_no": 1,
                 "cargo_barcode": 1, "cargo_mng_no": 1, "cargo_last_status_text": 1, "cargo_track_debug": 1,
                 "cargo_track_error": 1, "cargo_status_checked_at": 1}).sort("shipped_at", -1).limit(40):
            _pp.append({"kim": str(_r.get("influencer_name") or "")[:30], "gonderim": str(_r.get("shipped_at") or "")[:16],
                        "olusturma": str(_r.get("created_at") or "")[:16], "ref": _r.get("cargo_tracking_no"),
                        "barkod": _r.get("cargo_barcode"), "mng_no": _r.get("cargo_mng_no"),
                        "mng_durum": _r.get("cargo_last_status_text"), "hata": (_r.get("cargo_track_error") or "")[:80],
                        "son_kontrol": str(_r.get("cargo_status_checked_at") or "")[:16],
                        "debug": (_r.get("cargo_track_debug") or "")[:220]})
        _pt = dict(_pt or {}); _pt["pending"] = _pp
    except Exception as _e:
        _pt = dict(_pt or {}); _pt["pending_error"] = str(_e)[:80]
    _ps = None
    try:
        _ps = await db.settings.find_one({"id": "phone_scrub_5337173151"}, {"_id": 0, "done": 1, "at": 1, "counts": 1, "error": 1})
    except Exception:
        pass
    _st = None
    try:
        _st = await db.settings.find_one({"id": "migrations.size_table_order_v1"}, {"_id": 0, "done": 1, "fixed": 1, "at": 1})
    except Exception:
        pass
    _sa = None
    try:
        _sa = await db.settings.find_one({"id": "stock_audit_diag"}, {"_id": 0, "id": 0})
    except Exception:
        pass
    _inst = None   # süreç-etiketi teşhisi KALDIRILDI: indeks-siz log aggregation'ları DB'yi kilitliyordu
    return {"status": "healthy", "version": _sha or None, "havale_sweep": _hv or None,
            "scheduler_jobs": _jobs, "pr_track": _pt or None, "phone_scrub": _ps, "size_table_order": _st,
            "stock_audit": _sa, "instances": _inst,
            "otp_mail_fix": (await db.settings.find_one({"id": "migrations.otp_email_tpl_v2"}, {"_id": 0, "id": 0})),
            "deleted_zero_push": (await db.settings.find_one({"id": "migrations.deleted_zero_push_v1"}, {"_id": 0, "done": 1, "pushed": 1, "at": 1})),
            "trendyol_orphan_zero": (await db.settings.find_one({"id": "trendyol_orphan_zero_last"}, {"_id": 0, "id": 0})),
            "trendyol_orphan_preview": (await db.settings.find_one({"id": "trendyol_orphan_zero_preview"}, {"_id": 0, "id": 0})),
            "vade_refit": (await db.settings.find_one({"id": "migrations.vade_refit_v1"}, {"_id": 0, "id": 0})),
            "sale_min30": (await db.settings.find_one({"id": "migrations.sale_min30_v1"}, {"_id": 0, "id": 0})),
            "sale_min30_revert": (await db.settings.find_one({"id": "migrations.sale_min30_revert_outside_v1"}, {"_id": 0, "id": 0})),
            "label_barcode": _lbc,
            "w11874_fix": (await db.settings.find_one({"id": "migrations.fix_w11874_pm_v1"}, {"_id": 0, "id": 0})),
            "unpaid_card_audit": (await db.settings.find_one({"id": "migrations.audit_unpaid_card_v2"}, {"_id": 0, "id": 0})),
            "member_discount_audit": (await db.settings.find_one({"id": "migrations.audit_member_discount_v1"}, {"_id": 0, "id": 0})),
            "threeds_audit": ((await db.settings.find_one({"id": "migrations.audit_3ds_dropoff_v1"}, {"_id": 0, "id": 0}))
                              if _audit_ok(audit_key) else {"detay": "audit_key gerekli"}),
            "cancelreq_audit": (await db.settings.find_one({"id": "migrations.audit_cancelreq_vis_v1"}, {"_id": 0, "id": 0})),
            "report_perf": (lambda: __import__("routes.reports", fromlist=["REPORT_PERF"]).REPORT_PERF[-10:])(),
            # BAĞIMSIZ satış denetimi — panelin rakamına güvenilmediğinde kıyas için.
            # Ham db.orders üzerinden ayrı aritmetik; panelin sonucu da yanında döner.
            # Ticari veri taşıdığı için audit_key ZORUNLU. Salt-okunur.
            # ty_count=1 isteklerinde audit_start/audit_end YALNIZ Trendyol penceresini
            # tanımlar; ağır satış denetimini de çalıştırmak gereksizdi ve eski aylarda
            # (haziran) zaman aşımına düşüp TÜM /health yanıtını 500'e çeviriyordu.
            # Ayrıca kendi hatasını artık kendi içinde raporlar — tek blok tüm teşhisi
            # düşürmesin.
            "sales_audit": (await _safe_block(_sales_audit_block(audit_start, audit_end,
                                                                 audit_source))
                            if (_audit_ok(audit_key) and not ty_count and not ty_list and not iade_list
                                and not rapor_q and not iptal_tarih_q and not iade_sayfa_q
                                and not ty_urun_probe and not zero_sweep and not barkod_dokum
                                and not hb_yetim_q and not ty_kiyas and not ty_batch_q
                                and not kupon_q and not destek_q)
                            else {"detay": ("ty_count=1 ile birlikte çalıştırılmaz "
                                            "(ayrı istek atın)" if ty_count
                                            else "audit_key gerekli")}),
            # SALT OKUNUR stok izi: "bu ürünün stoğu neden bu değerde?" — hareket
            # defteri + canlı stok karşılaştırması. audit_key ZORUNLU, yazma YOK.
            "stock_trace": ((await _stock_trace_block(stock_q))
                            if (_audit_ok(audit_key) and stock_q)
                            else {"detay": "audit_key + stock_q gerekli"}),
            # KATALOG GENELİ hayalet stok raporu (salt okunur, öneri niteliğinde).
            # Ağır olduğu için yalnız &drift=1 ile çalışır.
            # Tek seferlik göçlerin çalışıp çalışmadığı (görünürlük; salt okuma).
            "gocler": {d.get("id"): {k: v for k, v in d.items() if k != "id"}
                       for d in [await db.settings.find_one({"id": _i}, {"_id": 0})
                                 for _i in ("ty_iptal_tarihi_onarim_v1", "ty_mutabakat_fix_v1", "ty_date_utc_fix_v1",
                                            "restock_delta_backfill_v1",
                                            "cancelled_at_backfill_v1",
                                            "consignment_removed_v1",
                                            "all_orders_2min_v1", "hb_sync_2min_v1")]
                       if d},
            # Trendyol'un KENDİ verisiyle karşılaştırma (salt okuma, yalnız ty_count=1).
            # Sipariş-sipariş eşleştirme (salt okuma).
            "siparis_eslestir": ((await _order_lookup(orders_q))
                                 if (_audit_ok(audit_key) and orders_q)
                                 else {"detay": "audit_key + orders_q gerekli"}),
            "trendyol_sayim": ((await _trendyol_live_count(audit_start, audit_end))
                               if (_audit_ok(audit_key) and ty_count)
                               else {"detay": "audit_key + ty_count=1 gerekli"}),
            # Pazaryeri SAAT denetimi: Trendyol'daki TR-yerel/UTC hatası diğer
            # kanallarda da var mı? (salt okuma, yalnız clock=1)
            # Tek tek sipariş numarasıyla Trendyol API'sine sorgu (salt okuma, GET).
            "ty_siparis_sorgu": ((await _safe_block(_ty_siparis_sorgu(ty_siparis_q)))
                                 if (_audit_ok(audit_key) and ty_siparis_q)
                                 else {"detay": "audit_key + ty_siparis_q gerekli"}),
            # Şüpheli iptal tarihleri (cancelled_at) kuru çalıştırma teşhisi (salt okuma).
            # iptal_tarih_q=1 → yalnız DB kanıtı; iptal_tarih_q=ty / ty<N> → + Trendyol canlı GET.
            "iptal_tarih": ((await _safe_block(_iptal_tarih_teshis(iptal_tarih_q)))
                            if (_audit_ok(audit_key) and iptal_tarih_q)
                            else {"detay": "audit_key + iptal_tarih_q gerekli"}),
            # Dönemin iade belgeleri + talepleri (Excel ile iade iade kıyas; salt okuma).
            "iade_listesi": ((await _safe_block(_iade_listesi(audit_start, audit_end)))
                             if (_audit_ok(audit_key) and iade_list)
                             else {"detay": "audit_key + iade_list=1 gerekli"}),
            # Raporlar sayfasının gösterdiği rakamlar, aynı uçlardan (kıyas; salt okuma).
            "rapor_sorgu": ((await _safe_block(_rapor_sorgu(rapor_q, audit_start, audit_end,
                                                            audit_source)))
                            if (_audit_ok(audit_key) and rapor_q)
                            else {"detay": "audit_key + rapor_q gerekli"}),
            "destek": ((await _safe_block(_destek_teshis(destek_q, destek_email)))
                       if (_audit_ok(audit_key) and destek_q)
                       else {"detay": "audit_key + destek_q=kargo|uyelik|yavas gerekli"}),
            "kupon": ((await _safe_block(_kupon_teshis(kupon_q)))
                      if (_audit_ok(audit_key) and kupon_q)
                      else {"detay": "audit_key + kupon_q=<KOD> gerekli"}),
            "ty_batch": ((await _safe_block(_ty_batch_sonuc(ty_batch_q, ty_batch_barkod)))
                         if (_audit_ok(audit_key) and ty_batch_q)
                         else {"detay": "audit_key + ty_batch_q=<id,..> gerekli"}),
            "ty_kiyas": ((await _safe_block(_ty_canli_kiyas()))
                         if (_audit_ok(audit_key) and ty_kiyas)
                         else {"detay": "audit_key + ty_kiyas=1 gerekli"}),
            "hb_yetim": ((await _safe_block(_hb_yetim_onizleme()))
                         if (_audit_ok(audit_key) and hb_yetim_q)
                         else {"detay": "audit_key + hb_yetim_q=1 gerekli"}),
            "ty_yetim_son": ((await db.settings.find_one({"id": "trendyol_orphan_zero_last"}, {"_id": 0}))
                             if (_audit_ok(audit_key) and zero_sweep) else {"detay": "zero_sweep=1"}),
            "barkod_dokum": ((await _safe_block(_barkod_dokum()))
                             if (_audit_ok(audit_key) and barkod_dokum)
                             else {"detay": "audit_key + barkod_dokum=1 gerekli"}),
            "ty_urun_probe": ((await _safe_block(_ty_urun_probe(ty_urun_probe)))
                              if (_audit_ok(audit_key) and ty_urun_probe)
                              else {"detay": "audit_key + ty_urun_probe=<barkod,..> gerekli"}),
            "pasif_sifirlama": ((await db.settings.find_one({"id": "passive_zero_sweep_last"}, {"_id": 0}))
                                if (_audit_ok(audit_key) and zero_sweep)
                                else {"detay": "audit_key + zero_sweep=1 gerekli"}),
            "iade_sayfa": ((await _safe_block(_iade_sayfa_teshis()))
                           if (_audit_ok(audit_key) and iade_sayfa_q)
                           else {"detay": "audit_key + iade_sayfa_q=1 gerekli"}),
            "asistan_log": ((await _safe_block(_asistan_log_block()))
                            if (_audit_ok(audit_key) and asistan_log)
                            else {"detay": "audit_key + asistan_log=1 gerekli"}),
            # Dönemin Trendyol sipariş listesi (Excel ile iki yönlü kıyas; salt okuma).
            "ty_ay_listesi": ((await _safe_block(_ty_ay_listesi(audit_start, audit_end)))
                              if (_audit_ok(audit_key) and ty_list)
                              else {"detay": "audit_key + ty_list=1 gerekli"}),
            # Bir siparişe kesilmiş gider pusulalarının TAMAMI (mükerrer teşhisi).
            "pusula_dokum": ((await _pusula_lookup(pusula_q))
                             if (_audit_ok(audit_key) and pusula_q)
                             else {"detay": "audit_key + pusula_q gerekli"}),
            # Tutarı GEÇERSİZ SAYI olan siparişler (bir kayıt tüm ayı zehirliyordu).
            "bozuk_tutar": (await _safe_block(_bozuk_tutar_block())
                            if _audit_ok(audit_key)
                            else {"detay": "audit_key gerekli"}),
            "pazaryeri_saat": ((await _marketplace_clock_block())
                               if (_audit_ok(audit_key) and clock)
                               else {"detay": "audit_key + clock=1 gerekli"}),
            "stock_drift": ((await _stock_drift_block())
                            if (_audit_ok(audit_key) and drift)
                            else {"detay": "audit_key + drift=1 gerekli"}),
            "body_merge": (await db.settings.find_one({"id": "migrations.merge_dup_body_v1"}, {"_id": 0, "id": 0})),
            "subset_refund_money": ((await db.settings.find_one({"id": "migrations.audit_subset_refund_v2"}, {"_id": 0, "id": 0}))
                                    if _audit_ok(audit_key) else {"detay": "audit_key gerekli"}),
            "subset_refund_audit": ((await db.settings.find_one({"id": "migrations.audit_subset_refund_v1"}, {"_id": 0, "id": 0}))
                                    if _audit_ok(audit_key) else
                                    {"detay": "audit_key gerekli",
                                     "supheli_adet": ((await db.settings.find_one({"id": "migrations.audit_subset_refund_v1"}, {"_id": 0, "supheli_adet": 1, "toplam_fazla_tl": 1})) or {})}),
            "payment_audit3": ((await db.settings.find_one({"id": "migrations.audit_w11805_refund_v1"}, {"_id": 0, "id": 0}))
                               if _audit_ok(audit_key) else {"detay": "audit_key gerekli"}),
            "payment_audit2": ((await db.settings.find_one({"id": "migrations.audit_card_paid_v2"}, {"_id": 0, "id": 0}))
                               if _audit_ok(audit_key) else {"detay": "audit_key gerekli"}),
            "payment_audit": ((await db.settings.find_one({"id": "migrations.audit_card_paid_v1"}, {"_id": 0, "id": 0}))
                              if _audit_ok(audit_key) else
                              {"detay": "audit_key gerekli",
                               "supheli_adet": ((await db.settings.find_one({"id": "migrations.audit_card_paid_v1"}, {"_id": 0, "supheli_adet": 1})) or {}).get("supheli_adet")}),
            "ty_probe": await _ty_mapping_probe("8078"),
            "return_probe": _rp}

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
