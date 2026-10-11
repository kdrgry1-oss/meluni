from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Dict, Any
from datetime import datetime, timezone, timedelta

from .deps import db, require_admin, limiter, require_permission
from tenant_config import (
    TenantConfig, get_tenant_config, invalidate as invalidate_tenant_config,
    legacy_write_through,
)
from activity_audit import record_admin_audit, run_audited

router = APIRouter(prefix="/settings", tags=["Settings"])


# =============================================================================
# KARGO ÜCRETİ / ÜCRETSİZ KARGO EŞİĞİ — TEK KAYNAK
# -----------------------------------------------------------------------------
# KÖK SEBEP (kritik): Storefront bu uçtan kargo ücretini "seçili kargo firmasının
# ücreti" (cargo_fees[default_cargo_company], ör. 99 TL) olarak alıyordu; sipariş
# oluşturma (orders.create_order) ise DOĞRUDAN settings.shipping_fee (ör. 90 TL)
# okuyordu. İki taraf farklı kargo ücreti kullandığı için müşterinin ONAYLADIĞI
# tutar ile bankadan ÇEKİLEN tutar 9 TL farklı çıkıyordu. Aynı şekilde eşik de
# istemcide kampanyadan, sunucuda ayardan geliyordu. Artık HER İKİ taraf da bu
# fonksiyonları çağırır → ayrışma imkânsız.
# =============================================================================
def resolve_shipping_fee(settings: dict) -> float:
    """Geçerli kargo ücreti: seçili kargo firmasının ücreti, yoksa genel shipping_fee."""
    s = settings or {}
    cargo_fees = s.get("cargo_fees") or {}
    default_company = s.get("default_cargo_company") or ""
    if default_company and isinstance(cargo_fees, dict) and cargo_fees.get(default_company) not in (None, ""):
        try:
            return float(cargo_fees.get(default_company)) or 0.0
        except Exception:
            pass
    try:
        return float(s.get("shipping_fee") or 0) or 0.0
    except Exception:
        return 0.0


async def resolve_free_shipping_threshold(settings: dict):
    """Ücretsiz kargo eşiği: aktif 'otomatik' free_shipping kampanyalarının EN DÜŞÜK
    min tutarı; kampanya yoksa ayardaki free_shipping_threshold. None = eşik yok."""
    threshold = None
    try:
        from shipping_rules import campaign_live
        async for _c in db.coupons.find({"is_active": True, "free_shipping": True, "auto_apply": True},
                                        {"_id": 0, "min_cart_total": 1, "start_at": 1, "end_at": 1}):
            # Süresi dolmuş/başlamamış kampanya eşiği düşürmez (Ekinoks bitince 0 TL kaldı).
            if not campaign_live(_c):
                continue
            mc = _c.get("min_cart_total")
            if mc in (None, ""):
                continue
            mc = float(mc)
            if threshold is None or mc < threshold:
                threshold = mc
    except Exception:
        threshold = None
    if threshold is None:
        try:
            _t = (settings or {}).get("free_shipping_threshold")
            threshold = float(_t) if _t not in (None, "") else None
        except Exception:
            threshold = None
    return threshold

def _valid_email(value: str) -> bool:
    if not value or len(value) > 254 or any(ch.isspace() for ch in value):
        return False
    local, separator, domain = value.rpartition("@")
    return bool(separator and local and "." in domain and not domain.startswith(".") and not domain.endswith("."))

@router.post("/maintenance/notify")
@(limiter.limit("5/minute;50/day") if limiter else (lambda f: f))
async def maintenance_notify_subscribe(payload: dict, request: Request):
    """Public: bakım modu sırasında 'açılınca haber ver' e-posta toplama.
    DENETİM SEC-5 F-18: hız-sınırı yoktu → sınırsız çöp kayıt. IP limiti eklendi."""
    email = (payload.get("email") or "").strip().lower()
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="Geçerli bir e-posta adresi giriniz.")
    existing = await db.maintenance_subscribers.find_one({"email": email})
    if existing:
        return {"message": "E-posta adresiniz zaten kayıtlı. Açılınca size haber vereceğiz."}
    await db.maintenance_subscribers.insert_one({
        "email": email,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "notified": False,
    })
    return {"message": "Teşekkürler! Site açılır açılmaz size haber vereceğiz."}

@router.get("/maintenance/subscribers")
async def maintenance_subscribers_list(current_user: dict = Depends(require_permission("settings.view"))):
    """Admin: bakım modu e-posta aboneleri."""
    items = await db.maintenance_subscribers.find({}, {"_id": 0}).sort("created_at", -1).to_list(5000)
    return {"total": len(items), "subscribers": items}

@router.get("/maintenance")
async def get_maintenance_status():
    """Public, lightweight maintenance-mode status used by the storefront gate."""
    settings = await db.settings.find_one(
        {"id": "main"},
        {"_id": 0, "maintenance_mode": 1, "maintenance_title": 1, "maintenance_message": 1, "logo_url": 1, "site_name": 1},
    ) or {}
    tenant = await get_tenant_config(db)
    return {
        "maintenance_mode": bool(settings.get("maintenance_mode", False)),
        "maintenance_title": settings.get("maintenance_title") or "Sitemiz sizin için yenileniyor",
        "maintenance_message": settings.get("maintenance_message") or "Çok yakında, daha iyi bir alışveriş deneyimiyle buradayız. Anlayışınız için teşekkür ederiz.",
        "logo_url": tenant["brand"]["logo_url"],
        "site_name": tenant["brand"]["store_name"],
    }

def _default_main_settings() -> dict:
    """Ana ayar belgesi henüz yoksa panelde gösterilecek varsayılan (VERİTABANINA YAZILMAZ —
    ilk kayıt Ayarlar sayfasındaki 'Kaydet' ile, yetkili POST /settings üzerinden oluşur)."""
    return {
        "id": "main",
        "site_name": "Mağaza",
        "logo_url": "",
        "free_shipping_limit": 500,
        "rotating_texts": [],
        "contact_email": "",
        "contact_phone": "",
        "address": "",
        "payment_methods": {"credit_card": True, "bank_transfer": True, "cash_on_delivery": False},
        "barcode_range_start": "",
        "barcode_range_end": "",
        "default_vat_rate": 10,
        "trendyol_markup": 0,
        "company_info": {
            "company_name": "",
            "tax_office": "",
            "tax_number": "",
            "address": "",
            "city": "",
            "website": "",
            "phone": "",
            "email": ""
        }
    }


# Vitrinin ana ayar belgesinden GERÇEKTEN okuduğu alanlar (frontend: lib/shipping.js → kargo ücreti/
# eşik; pages/Checkout.jsx → ödeme yöntemleri + havale indirimi; components/CookieConsent.jsx →
# çerez bildirimi metinleri). Yeni bir vitrin alanı gerekiyorsa BURAYA bilinçli olarak eklenir.
_PUBLIC_COOKIE_KEYS = ("heading", "body", "policy_url", "policy_label")
_PUBLIC_PAYMENT_KEYS = ("credit_card", "bank_transfer", "cash_on_delivery")


@router.get("")
async def get_settings():
    """VİTRİN (herkese açık) ayarları — YALNIZ vitrinin kullandığı alanlar.

    GÜVENLİK: Eskiden ana ayar belgesinin TAMAMI (Trendyol fiyat farkı %, barkod aralığı, firma/vergi
    bilgisi, tenant_config…) kimliksiz dönüyor ve belge yoksa anonim GET varsayılan kayıt YAZIYORDU.
    Artık beyaz liste döner ve bu uç veritabanına HİÇBİR ZAMAN yazmaz. Tam belge: GET /settings/admin."""
    settings = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    pm = settings.get("payment_methods") if isinstance(settings.get("payment_methods"), dict) else {}
    cc = settings.get("cookie_consent") if isinstance(settings.get("cookie_consent"), dict) else {}
    return {
        # Kargo — TEK KAYNAK (sipariş oluşturma ile aynı fonksiyonlar; bkz. resolve_shipping_fee)
        "shipping_fee": resolve_shipping_fee(settings),
        "free_shipping_threshold": await resolve_free_shipping_threshold(settings),
        "payment_methods": {k: bool(pm[k]) for k in _PUBLIC_PAYMENT_KEYS if k in pm},
        "bank_transfer_discount_pct": settings.get("bank_transfer_discount_pct"),
        "cookie_consent": {k: str(cc.get(k) or "") for k in _PUBLIC_COOKIE_KEYS if k in cc},
    }


@router.get("/admin")
async def get_settings_admin(current_user: dict = Depends(require_permission("settings.view"))):
    """PANEL: ana ayar belgesinin tamamı (Ayarlar → Genel). Belge yoksa varsayılanlar döner
    (yazılmaz). Yanıt biçimi eski GET /settings ile aynıdır (Ayarlar sayfası geri kaydeder)."""
    settings = await db.settings.find_one({"id": "main"}, {"_id": 0}) or _default_main_settings()
    # Kargo bilgisi (sabit kod yerine ayardan) — TEK KAYNAK (bkz. resolve_shipping_fee)
    settings["shipping_fee"] = resolve_shipping_fee(settings)
    settings["free_shipping_threshold"] = await resolve_free_shipping_threshold(settings)
    settings["tenant_config"] = await get_tenant_config(db)
    return settings


@router.get("/product-defaults")
async def get_product_defaults(current_user: dict = Depends(require_permission("products.view"))):
    """PANEL / Ürünler: ürün formunun ihtiyaç duyduğu iki varsayılan — yeni ürün KDV oranı ve
    Trendyol fiyat farkı (%). Ürün yetkilisinin tüm ayar belgesini ya da Trendyol API bilgilerini
    görmesine gerek kalmasın diye ayrı (en az yetki). Trendyol farkı önceliği
    GET /integrations/trendyol/settings ile aynıdır: ana ayar → Trendyol entegrasyon kartı."""
    main = await db.settings.find_one({"id": "main"}, {"_id": 0, "default_vat_rate": 1, "trendyol_markup": 1}) or {}
    markup = main.get("trendyol_markup")
    if markup in (None, ""):
        ty = await db.settings.find_one({"id": "trendyol"}, {"_id": 0, "default_markup": 1}) or {}
        markup = ty.get("default_markup")
    try:
        markup = float(markup or 0)
    except (TypeError, ValueError):
        markup = 0.0
    vat = main.get("default_vat_rate")
    return {"default_vat_rate": vat if vat not in (None, "") else _default_main_settings()["default_vat_rate"],
            "trendyol_markup": markup}

@router.post("")
async def update_settings(
    settings_data: dict,
    request: Request,
    current_user: dict = Depends(require_permission("settings.site"))
):
    """Update global settings"""
    before_main = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    before_tenant = await db.settings.find_one({"id": "tenant_config"}, {"_id": 0}) or {}
    settings_data.pop("_id", None)
    # GÜVENLİK (INJ-01, mass assignment): gövdedeki `id` ana ayar belgesini YENİDEN ANAHTARLIYORDU
    # ($set id="iyzico" → settings.site yetkilisi kendi API anahtarıyla sahte iyzico belgesi üretip
    # integrations.iyzico yetkisini baypas edebiliyordu). Belge kimliği yalnız sunucuda: "main".
    settings_data.pop("id", None)
    # New admin clients write the canonical nested document. Keep it out of
    # settings.main so there is only one authoritative copy.
    tenant_payload = settings_data.pop("tenant_config", None)
    if tenant_payload is not None:
        tenant = TenantConfig.model_validate(tenant_payload).model_dump()
        await db.settings.update_one(
            {"id": "tenant_config"}, {"$set": tenant}, upsert=True
        )
        # Temporary dual-write keeps old order/shipping/invoice consumers live
        # while they are migrated independently to get_tenant_config().
        settings_data.update(legacy_write_through(tenant))
        invalidate_tenant_config(db)
    existing = await db.settings.find_one({"id": "main"})
    if not existing:
        settings_data["id"] = "main"
        await db.settings.insert_one(settings_data)
    else:
        await db.settings.update_one({"id": "main"}, {"$set": settings_data})

    # BEYAZ ETİKET: firma bilgisi cache'ini geçersiz kıl → düzenleme anında e-posta/e-fatura
    # markasına yansısın (60 sn beklemeden).
    try:
        from company import invalidate as _co_inv
        _co_inv()
    except Exception:
        pass
    after_main = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    await record_admin_audit(
        db, action="settings.update", entity_type="settings", entity_id="main",
        before=before_main, after=after_main, current_user=current_user, request=request,
        source="settings",
    )
    if tenant_payload is not None:
        await record_admin_audit(
            db, action="tenant_config.update", entity_type="settings", entity_id="tenant_config",
            before=before_tenant, after=tenant, current_user=current_user, request=request,
            source="settings",
        )
    return {"message": "Ayarlar güncellendi"}


@router.get("/tenant-config")
async def read_tenant_config(current_user: dict = Depends(require_permission("settings.view"))):
    """Canonical white-label settings; contains secret references, never secrets."""
    return await get_tenant_config(db, use_cache=False)


@router.put("/tenant-config")
async def write_tenant_config(
    payload: dict,
    request: Request,
    current_user: dict = Depends(require_permission("settings.site")),
):
    before = await db.settings.find_one({"id": "tenant_config"}, {"_id": 0}) or {}
    tenant = TenantConfig.model_validate(payload).model_dump()
    await db.settings.update_one({"id": "tenant_config"}, {"$set": tenant}, upsert=True)
    await db.settings.update_one(
        {"id": "main"}, {"$set": legacy_write_through(tenant), "$setOnInsert": {"id": "main"}}, upsert=True
    )
    invalidate_tenant_config(db)
    await record_admin_audit(
        db, action="tenant_config.update", entity_type="settings", entity_id="tenant_config",
        before=before, after=tenant, current_user=current_user, request=request, source="settings",
    )
    return {"success": True, "schema_version": tenant["schema_version"]}


@router.get("/tenant-config/migration-preview")
async def preview_tenant_config_migration(current_user: dict = Depends(require_permission("settings.view"))):
    """Read-only migration preview. Legal/CMS documents are intentionally excluded."""
    from tenant_config import migrate_tenant_config
    return await migrate_tenant_config(db, apply=False)


# =============================================================================
# Ödeme Tipleri — Havale/EFT banka hesaplari + odeme entegrasyon ozeti
# Veri: db.settings._id="payment" { bank_accounts: [ {id,bank_name,branch,iban,
#       account_holder,is_default} ] }
# =============================================================================
import uuid as _uuid


def _ensure_single_default(banks):
    if banks and not any(b.get("is_default") for b in banks):
        banks[0]["is_default"] = True
    return banks


@router.get("/payment-overview")
async def payment_overview(current_user: dict = Depends(require_permission("settings.view"))):
    """Ödeme Tipleri sayfasi: aktif odeme entegrasyonlari + havale banka hesaplari.
    bank_accounts = YÖNETİM görünümü (bekleme süresindeki yeni/değişen hesaplar
    pending_activation_at ile işaretli dâhil); vitrin/e-posta yalnız etkin listeyi görür."""
    await promote_due_bank_changes()
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    banks = _bank_admin_view(pay)
    iyz = await db.settings.find_one({"id": "iyzico"}, {"_id": 0}) or {}
    integrations = [{
        "key": "iyzico",
        "name": "iyzico (Kredi / Banka Kartı)",
        "configured": bool(iyz.get("api_key") and iyz.get("api_secret")),
        "active": bool(iyz.get("is_active")),
        "settings_path": "/admin/entegrasyonlar",
    }, {
        "key": "bank_transfer",
        "name": "Havale / EFT",
        "configured": bool(pay.get("bank_accounts")),
        "active": bool(pay.get("bank_accounts")),
        "settings_path": "/admin/odeme-tipleri",
    }]
    return {"integrations": integrations, "bank_accounts": banks,
            "pending_bank_changes": _pending_public(pay)}


# GÜVENLİK (denetim 2026-10): IBAN değiştirmek müşteri havalesini başka hesaba yönlendirebilir
# (en kritik dolandırıcılık yüzeyi). Eskiden her personel (require_admin) değiştirebiliyordu.
# Artık: 'settings.payment' yetkisi (süper admin '*' geçer) + denetim kaydı + TÜM süper
# adminlere uyarı e-postası. IBAN uyarıda/denetimde yalnız SON 4 HANE ile görünür.
_BANK_PERM = "settings.payment"


def _iban_tail(v) -> str:
    v = "".join(str(v or "").split())
    return ("…" + v[-4:]) if len(v) >= 4 else ("…" if v else "")


def _bank_public(acc: dict) -> dict:
    a = acc or {}
    return {"id": a.get("id"), "bank_name": a.get("bank_name"), "iban_tail": _iban_tail(a.get("iban")),
            "account_holder_set": bool(a.get("account_holder")), "is_default": bool(a.get("is_default"))}


async def _bank_change_side_effects(action: str, before: dict, after: dict, current_user: dict, request,
                                    extra_note: str = "") -> None:
    """Uyarı e-postası (denetim artık run_audited ile işlemden ÖNCE, fail-closed yazılır)."""
    try:
        from security.events import notify_super_admins
        _who = str(current_user.get("email") or current_user.get("id") or "?")
        _b, _a = _bank_public(before) if before else {}, _bank_public(after) if after else {}
        body = (f"İşlem: {action}\nYapan: {_who}\n"
                f"Önce: {_b.get('bank_name') or '-'} {_b.get('iban_tail') or ''} (varsayılan={_b.get('is_default')})\n"
                f"Sonra: {_a.get('bank_name') or '-'} {_a.get('iban_tail') or ''} (varsayılan={_a.get('is_default')})\n"
                + (f"Vitrinde/e-postalarda geçerlilik: {extra_note}\n" if extra_note else "")
                + "\nBu değişikliği siz yapmadıysanız HEMEN panelden kontrol edin: havale ödemeleri bu hesaba yönlenir.")
        _now_fp = datetime.now(timezone.utc).isoformat()
        await notify_super_admins("bank_account_change", f"Banka hesabı (IBAN) değişikliği: {action}", body,
                                  fingerprint=f"bank_change:{_now_fp}", level="critical",
                                  meta={"action": action})
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).warning(f"bank change alert failed: {type(_e).__name__}")


# ── IBAN BEKLEME SÜRESİ (cooling-off) ──
# Yeni eklenen ya da IBAN'ı/alıcı adı değişen hesap HEMEN etkin olmaz: settings.payment.
# bank_accounts_pending'e pending_activation_at = şimdi + IBAN_COOLING_OFF_MINUTES (vars. 60) ile
# yazılır. Vitrin (/settings/public/bank-default), sipariş e-postaları (orders.py) ve WhatsApp
# asistanı settings.payment.bank_accounts (ETKİN liste) okur → süre dolana kadar ÖNCEKİ varsayılan
# görünmeye devam eder. Süresi dolan değişiklikler promote_due_bank_changes() ile etkin listeye
# taşınır (zamanlayıcı + vitrin/yönetim okumalarında tembel).
_BANK_COOL_DEFAULT_MIN = 60


def _cooling_minutes() -> int:
    try:
        v = int(float((__import__("os").environ.get("IBAN_COOLING_OFF_MINUTES") or "").strip() or _BANK_COOL_DEFAULT_MIN))
        return max(0, min(v, 7 * 24 * 60))
    except Exception:
        return _BANK_COOL_DEFAULT_MIN


def _norm_iban(v) -> str:
    return "".join(str(v or "").split()).upper()


def _bank_admin_view(pay: dict) -> list:
    banks = [dict(b) for b in (pay.get("bank_accounts") or [])]
    idx = {b.get("id"): i for i, b in enumerate(banks)}
    for p in (pay.get("bank_accounts_pending") or []):
        acc = dict(p.get("account") or {})
        acc["pending_activation_at"] = p.get("pending_activation_at")
        acc["status"] = "pending_activation"
        if acc.get("id") in idx:
            banks[idx[acc["id"]]] = acc
        else:
            banks.append(acc)
    return banks


def _pending_public(pay: dict) -> list:
    return [{"id": (p.get("account") or {}).get("id"), "bank_name": (p.get("account") or {}).get("bank_name"),
             "iban_tail": _iban_tail((p.get("account") or {}).get("iban")),
             "pending_activation_at": p.get("pending_activation_at")}
            for p in (pay.get("bank_accounts_pending") or [])]


async def _payment_cas(mutator, attempts: int = 8) -> dict:
    """settings.payment üzerinde iyimser kilitli (bank_rev) oku-değiştir-yaz."""
    for _ in range(attempts):
        pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {"id": "payment"}
        rev = pay.get("bank_rev")
        new = mutator({**pay, "bank_accounts": [dict(b) for b in (pay.get("bank_accounts") or [])],
                       "bank_accounts_pending": [dict(p) for p in (pay.get("bank_accounts_pending") or [])]})
        if new is None:
            return pay
        flt = {"id": "payment", "bank_rev": rev} if rev is not None else \
            {"id": "payment", "bank_rev": {"$exists": False}}
        res = await db.settings.update_one(
            flt, {"$set": {"id": "payment", "bank_accounts": new["bank_accounts"],
                           "bank_accounts_pending": new["bank_accounts_pending"],
                           "bank_rev": int(rev or 0) + 1}},
            upsert=not await db.settings.find_one({"id": "payment"}, {"_id": 1}))
        if getattr(res, "matched_count", 0) or getattr(res, "upserted_id", None):
            new["bank_rev"] = int(rev or 0) + 1
            return new
    raise HTTPException(status_code=409, detail="Banka hesapları aynı anda değiştirildi; tekrar deneyin")


async def promote_due_bank_changes() -> int:
    """Bekleme süresi dolan IBAN değişikliklerini etkin listeye taşır. Zamanlayıcıdan (5 dk) ve
    vitrin/yönetim okumalarından çağrılır. Asla hata fırlatmaz."""
    now_iso = datetime.now(timezone.utc).isoformat()
    moved = {"n": 0, "items": []}

    def _mut(pay):
        due = [p for p in pay["bank_accounts_pending"] if str(p.get("pending_activation_at") or "") <= now_iso]
        if not due:
            return None
        banks = pay["bank_accounts"]
        for p in due:
            acc = dict(p.get("account") or {})
            acc.pop("pending_activation_at", None)
            acc.pop("status", None)
            replaced = False
            for i, b in enumerate(banks):
                if b.get("id") == acc.get("id"):
                    banks[i] = acc
                    replaced = True
                    break
            if not replaced:
                banks.append(acc)
            if acc.get("is_default"):
                for b in banks:
                    b["is_default"] = (b.get("id") == acc.get("id"))
            moved["items"].append(acc)
        _ensure_single_default(banks)
        pay["bank_accounts_pending"] = [p for p in pay["bank_accounts_pending"] if p not in due]
        moved["n"] = len(due)
        return pay
    try:
        await _payment_cas(_mut)
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).warning(f"IBAN promote failed: {type(_e).__name__}")
        return 0
    for acc in moved["items"]:
        try:
            await record_admin_audit(db, action="settings.bank_account_activate", entity_type="bank_account",
                                     entity_id=str(acc.get("id") or ""), after=_bank_public(acc),
                                     source="system.iban_cooling_off")
            await _bank_change_side_effects("activate", None, acc, {"email": "sistem (bekleme süresi doldu)"}, None,
                                            extra_note="şimdi etkin")
        except Exception:
            pass
    return moved["n"]


def get_effective_bank_accounts(pay: dict) -> list:
    """Vitrin/e-posta için ETKİN hesaplar (bekleyenler hariç). Okuyucular için tek filtre."""
    return [dict(b) for b in ((pay or {}).get("bank_accounts") or []) if not b.get("pending_activation_at")]


async def _apply_bank_op(op: dict, actor: dict, request, *, single=False, approval_id="", approver=None) -> dict:
    """op: {kind: upsert|delete|default, account?, acc_id?}. Fail-closed denetimli uygular."""
    kind = op.get("kind")
    cool_min = _cooling_minutes()
    act_at = (datetime.now(timezone.utc) + timedelta(minutes=cool_min)).isoformat()
    result = {"cooling": False}
    pay0 = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    eff0 = {b.get("id"): b for b in (pay0.get("bank_accounts") or [])}
    pend0 = {(p.get("account") or {}).get("id"): p.get("account") for p in (pay0.get("bank_accounts_pending") or [])}
    acc_id = (op.get("account") or {}).get("id") or op.get("acc_id")
    before = pend0.get(acc_id) or eff0.get(acc_id)

    def _mut(pay):
        banks, pend = pay["bank_accounts"], pay["bank_accounts_pending"]
        if kind == "upsert":
            acc = dict(op["account"])
            cur = next((b for b in banks if b.get("id") == acc["id"]), None)
            pend[:] = [p for p in pend if (p.get("account") or {}).get("id") != acc["id"]]
            sensitive = (cur is None or _norm_iban(cur.get("iban")) != _norm_iban(acc.get("iban"))
                         or str(cur.get("account_holder") or "").strip() != str(acc.get("account_holder") or "").strip())
            if sensitive and cool_min > 0:
                pend.append({"account": acc, "pending_activation_at": act_at,
                             "requested_by": str(actor.get("id") or ""),
                             "created_at": datetime.now(timezone.utc).isoformat()})
                result["cooling"] = True
                # Yalnız ad/şube gibi zararsız alanlar etkin kayıtta hemen güncellenmez (tutarlılık).
            else:
                for i, b in enumerate(banks):
                    if b.get("id") == acc["id"]:
                        banks[i] = acc
                        break
                else:
                    banks.append(acc)
                if acc.get("is_default"):
                    for b in banks:
                        b["is_default"] = (b.get("id") == acc["id"])
                _ensure_single_default(banks)
        elif kind == "delete":
            pay["bank_accounts"] = [b for b in banks if b.get("id") != op["acc_id"]]
            pay["bank_accounts_pending"] = [p for p in pend if (p.get("account") or {}).get("id") != op["acc_id"]]
            _ensure_single_default(pay["bank_accounts"])
        elif kind == "default":
            if any(b.get("id") == op["acc_id"] for b in banks):
                for b in banks:
                    b["is_default"] = (b.get("id") == op["acc_id"])
            else:
                hit = False
                for p in pend:
                    if (p.get("account") or {}).get("id") == op["acc_id"]:
                        p["account"]["is_default"] = True
                        hit = True
                if not hit:
                    raise HTTPException(status_code=404, detail="Hesap bulunamadı")
                result["cooling"] = True
        return pay
    out = {}

    async def _perform():
        out["pay"] = await _payment_cas(_mut)
    after_acc = op.get("account") if kind == "upsert" else (eff0.get(acc_id) or pend0.get(acc_id))
    meta = {"kind": kind, "requested_by": actor.get("id")}
    if single:
        meta["single_admin_exception"] = True
    if approval_id:
        meta.update({"approval_id": approval_id, "approved_by": (approver or {}).get("id")})
    await run_audited(db, action=f"settings.bank_account_{kind}", entity_type="bank_account",
                      entity_id=str(acc_id or ""), perform=_perform,
                      before=_bank_public(before) if before else None,
                      after=_bank_public(after_acc) if (after_acc and kind != "delete") else None,
                      current_user=approver or actor, request=request, source="admin.settings", metadata=meta)
    note = (f"{cool_min} dk bekleme süresinden sonra ({act_at[:16].replace('T', ' ')} UTC) etkin olur"
            if result["cooling"] else "hemen etkin")
    await _bank_change_side_effects({"upsert": "update" if before else "create"}.get(kind, kind),
                                    before, after_acc if kind != "delete" else None, approver or actor,
                                    request, extra_note=note)
    pay = out.get("pay") or {}
    return {"bank_accounts": _bank_admin_view(pay), "pending_bank_changes": _pending_public(pay),
            "cooling_off": result["cooling"], "activation_note": note}


async def _bank_gate(op: dict, summary: str, current_user: dict, request):
    """İki kişi kuralı (BL-027). Bekleyen onay → 202; aksi halde hemen uygular."""
    # AUTH24: kurtarma koduyla girişten sonra (yeni faktör bağlanana kadar, ≤24 sa) IBAN değiştirilemez.
    from .deps import assert_no_recovery_restriction
    await assert_no_recovery_restriction(current_user, "settings.bank_account")
    from security import approvals as _ap
    from fastapi.responses import JSONResponse
    decision = await _ap.gate("settings.bank_account", params={"op": op}, summary=summary,
                              current_user=current_user, request=request, entity_type="bank_account",
                              entity_id=str((op.get("account") or {}).get("id") or op.get("acc_id") or ""))
    if decision["mode"] == "pending":
        pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
        body = _ap.pending_response(decision)
        body.update({"bank_accounts": _bank_admin_view(pay), "pending_bank_changes": _pending_public(pay)})
        return JSONResponse(status_code=202, content=body)
    return await _apply_bank_op(op, current_user, request, single=decision["single_admin_exception"])


async def _exec_bank_op(params, requester, approver, request, approval_id=""):
    res = await _apply_bank_op(dict(params.get("op") or {}), requester or {}, request,
                               approval_id=approval_id, approver=approver)
    return {"cooling_off": bool(res.get("cooling_off"))}


try:
    from security import approvals as _approvals_mod
    _approvals_mod.register("settings.bank_account", _exec_bank_op, "Banka hesabı (IBAN) değişikliği")
except Exception:  # pragma: no cover
    pass


@router.post("/bank-accounts")
async def upsert_bank_account(payload: Dict[str, Any], request: Request,
                              current_user: dict = Depends(require_permission(_BANK_PERM))):
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    acc_id = str(payload.get("id") or "").strip() or _uuid.uuid4().hex[:12]
    acc = {
        "id": acc_id,
        "bank_name": str(payload.get("bank_name") or "").strip(),
        "branch": str(payload.get("branch") or "").strip(),
        "iban": _norm_iban(payload.get("iban")),
        "account_holder": str(payload.get("account_holder") or "").strip(),
        "is_default": bool(payload.get("is_default")),
    }
    if not acc["bank_name"] or not acc["iban"]:
        raise HTTPException(status_code=400, detail="Banka adı ve IBAN zorunlu")
    cur = next((b for b in _bank_admin_view(pay) if b.get("id") == acc_id), None)
    if cur:
        _cmp = {k: cur.get(k) for k in acc}
        _cmp["iban"] = _norm_iban(_cmp.get("iban"))
        _cmp["is_default"] = bool(_cmp.get("is_default"))
        if _cmp == acc:
            return {"bank_accounts": _bank_admin_view(pay), "pending_bank_changes": _pending_public(pay)}
    summary = (f"Banka hesabı {'güncelleme' if cur else 'ekleme'}: {acc['bank_name']} {_iban_tail(acc['iban'])}"
               + (" (varsayılan)" if acc["is_default"] else ""))
    return await _bank_gate({"kind": "upsert", "account": acc}, summary, current_user, request)


@router.delete("/bank-accounts/{acc_id}")
async def delete_bank_account(acc_id: str, request: Request,
                              current_user: dict = Depends(require_permission(_BANK_PERM))):
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    cur = next((b for b in _bank_admin_view(pay) if b.get("id") == acc_id), None)
    if not cur:
        return {"bank_accounts": _bank_admin_view(pay), "pending_bank_changes": _pending_public(pay)}
    return await _bank_gate({"kind": "delete", "acc_id": acc_id},
                            f"Banka hesabı silme: {cur.get('bank_name') or ''} {_iban_tail(cur.get('iban'))}",
                            current_user, request)


@router.post("/bank-accounts/{acc_id}/default")
async def set_default_bank_account(acc_id: str, request: Request,
                                   current_user: dict = Depends(require_permission(_BANK_PERM))):
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    cur = next((b for b in _bank_admin_view(pay) if b.get("id") == acc_id), None)
    if not cur:
        raise HTTPException(status_code=404, detail="Hesap bulunamadı")
    if cur.get("is_default") and not cur.get("pending_activation_at"):
        return {"bank_accounts": _bank_admin_view(pay), "pending_bank_changes": _pending_public(pay)}
    return await _bank_gate({"kind": "default", "acc_id": acc_id},
                            f"Varsayılan havale hesabını değiştirme: {cur.get('bank_name') or ''} "
                            f"{_iban_tail(cur.get('iban'))}", current_user, request)



# =============================================================================
# Sipariş Durumları — sistemde görünür durumlar + durum başına SMS/Mail seçimi
# Veri: db.settings._id="order_status_config" { active:[key], notify:{key:{sms,email}} }
# =============================================================================
@router.get("/order-statuses")
async def get_order_statuses(current_user: dict = Depends(require_admin)):
    from order_statuses import get_status_config, effective_statuses
    cfg = await get_status_config(db)
    active = set(cfg.get("active", []))
    notify = cfg.get("notify") or {}
    out = []
    for s in effective_statuses(cfg):
        nz = notify.get(s["key"], {})
        out.append({
            "key": s["key"], "label": s["label"], "customer_label": s["customer_label"],
            "event": s.get("event"), "color": s["color"], "group": s["group"],
            "is_custom": bool(s.get("is_custom")),
            "active": s["key"] in active,
            "sms": bool(nz.get("sms")), "email": bool(nz.get("email")),
        })
    return {"statuses": out}


@router.post("/order-statuses")
async def save_order_statuses(payload: Dict[str, Any], current_user: dict = Depends(require_permission("settings.edit"))):
    """active + notify(kanal) + custom(yeni durumlar) + labels(etiket override) kaydeder.
    merge_config doğrulamayı (geçersiz/çakışan key eleme, slug, kanal bool) yapar."""
    from order_statuses import merge_config, CONFIG_ID
    merged = merge_config({
        "active": payload.get("active"),
        "notify": payload.get("notify"),
        "custom": payload.get("custom"),
        "labels": payload.get("labels"),
    })
    await db.settings.update_one(
        {"id": CONFIG_ID},
        {"$set": {"id": CONFIG_ID,
                  "active": merged["active"], "notify": merged["notify"],
                  "custom": merged["custom"], "labels": merged["labels"],
                  "templates_seeded": True,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return {"success": True, "active_count": len(merged["active"]), "custom_count": len(merged["custom"])}



@router.get("/public/bank-default")
async def public_default_bank():
    """Storefront (dekont sayfasi) icin varsayilan havale hesabi (public; IBAN zaten musteriyle paylasilir)."""
    await promote_due_bank_changes()
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    banks = get_effective_bank_accounts(pay)
    bank = next((b for b in banks if b.get("is_default")), None) or (banks[0] if banks else None)
    if bank:
        bank = {k: bank.get(k) for k in ("id", "bank_name", "branch", "iban", "account_holder", "is_default")}
    return {"bank": bank}


# =============================================================================
# Gönderici / Depo Adresi — kargo & iade etiketlerinde kullanılır (settings.store_info)
# =============================================================================
@router.get("/store-info")
async def get_store_info(current_user: dict = Depends(require_permission("settings.view"))):
    s = await db.settings.find_one({"id": "store_info"}, {"_id": 0}) or {}
    return {
        "sender_name": s.get("sender_name", ""),
        "sender_phone": s.get("sender_phone", ""),
        "sender_address": s.get("sender_address", ""),
        "sender_city": s.get("sender_city", ""),
        "sender_district": s.get("sender_district", ""),
        "sender_tax_no": s.get("sender_tax_no", ""),
    }


@router.post("/store-info")
async def save_store_info(payload: Dict[str, Any], request: Request,
                          current_user: dict = Depends(require_permission("settings.company"))):
    before = await db.settings.find_one({"id": "store_info"}, {"_id": 0}) or {}
    data = {
        "id": "store_info",
        "sender_name": str(payload.get("sender_name") or "").strip(),
        "sender_phone": str(payload.get("sender_phone") or "").strip(),
        "sender_address": str(payload.get("sender_address") or "").strip(),
        "sender_city": str(payload.get("sender_city") or "").strip(),
        "sender_district": str(payload.get("sender_district") or "").strip(),
        "sender_tax_no": str(payload.get("sender_tax_no") or "").strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.settings.update_one({"id": "store_info"}, {"$set": data}, upsert=True)
    await record_admin_audit(
        db, action="store_info.update", entity_type="settings", entity_id="store_info",
        before=before, after=data, current_user=current_user, request=request, source="settings",
    )
    return {"success": True}


# =============================================================================
# E-posta (SMTP / Zoho Mail) Ayarları — settings.id="email_smtp"
# Tüm bildirim & toplu e-postalar bu hesaptan gönderilir. Şifre DB'de tutulur,
# API yanıtında ASLA dönülmez (yalnızca password_set bilgisi).
# =============================================================================
@router.get("/email-smtp")
async def get_email_smtp(current_user: dict = Depends(require_permission("settings.view"))):
    s = await db.settings.find_one({"id": "email_smtp"}, {"_id": 0}) or {}
    tenant = await get_tenant_config(db)
    return {
        "enabled": bool(s.get("enabled", False)),
        "host": s.get("host", "smtp.zoho.eu"),
        "port": int(s.get("port", 465)),
        "secure": s.get("secure", "ssl"),
        "username": s.get("username", ""),
        "from_name": s.get("from_name") or tenant["brand"]["store_name"],
        # Sipariş maillerinin ayrı gönderen kimliği (ör. siparis@...). Boşsa sipariş
        # mailleri de varsayılan gönderenden gider. Pazarlama (SES) ile işlemsel (Zoho)
        # itibar ayrımının tamamlayıcısı: sipariş mailleri şifre/pazarlamadan ayrışır.
        "order_from_email": s.get("order_from_email", ""),
        "order_from_name": s.get("order_from_name", ""),
        "order_reply_to": s.get("order_reply_to", ""),
        "password_set": bool(s.get("password")),
    }


@router.post("/email-smtp")
async def save_email_smtp(payload: Dict[str, Any], request: Request,
                          current_user: dict = Depends(require_permission("settings.emails"))):
    existing = await db.settings.find_one({"id": "email_smtp"}, {"_id": 0}) or {}
    tenant = await get_tenant_config(db)
    pwd = payload.get("password")
    data = {
        "id": "email_smtp",
        "enabled": bool(payload.get("enabled", False)),
        "host": (str(payload.get("host") or "smtp.zoho.eu")).strip(),
        "port": int(payload.get("port") or 465),
        "secure": (str(payload.get("secure") or "ssl")).strip().lower(),
        "username": (str(payload.get("username") or "")).strip(),
        "from_name": (str(payload.get("from_name") or tenant["brand"]["store_name"])).strip(),
        # Sipariş maili gönderen kimliği (opsiyonel; boş → varsayılan gönderen).
        "order_from_email": (str(payload.get("order_from_email") or "")).strip(),
        "order_from_name": (str(payload.get("order_from_name") or "")).strip(),
        "order_reply_to": (str(payload.get("order_reply_to") or "")).strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Şifre yalnızca yeni değer girildiyse güncellenir; boş bırakılırsa mevcut korunur.
    # A1.1: at-rest ŞİFRELE (düz metin saklanmaz). get_smtp_config okuma yolunda çözülür.
    if pwd:
        try:
            from security.crypto import encrypt as _enc, is_encrypted as _ise
            data["password"] = _enc(str(pwd)) if not _ise(str(pwd)) else str(pwd)
        except Exception:
            data["password"] = str(pwd)
    elif existing.get("password"):
        data["password"] = existing["password"]
    await db.settings.update_one({"id": "email_smtp"}, {"$set": data}, upsert=True)
    await record_admin_audit(
        db, action="email_smtp.update", entity_type="settings", entity_id="email_smtp",
        before=existing, after=data, current_user=current_user, request=request, source="settings",
    )
    return {"success": True}


@router.post("/email-smtp/test")
async def test_email_smtp(payload: Dict[str, Any], current_user: dict = Depends(require_permission("settings.edit"))):
    to = (str(payload.get("to") or "")).strip()
    if not to:
        raise HTTPException(status_code=400, detail="Test için alıcı e-posta gerekli")
    from email_smtp import send_smtp_email, get_smtp_config, _endpoint
    cfg = await get_smtp_config(db)
    tenant = await get_tenant_config(db)
    res = await send_smtp_email(
        db, to,
        f"{tenant['brand']['store_name']} SMTP Test",
        "<p>Bu bir test e-postasıdır. Zoho SMTP ayarlarınız çalışıyor 🎉</p>",
    )
    if not res.get("success"):
        # ÖNEMLİ: 5xx DÖNDÜRME. Cloudflare, origin'den gelen 5xx'i kendi HTML hata sayfasıyla
        # değiştirip ZeptoMail'in gerçek hata metnini gizliyordu → panelde hep "sunucuya
        # ulaşılamadı" görünüyordu. 200 + gövdede gerçek hata ile ZeptoMail cevabını iletiyoruz.
        return {
            "success": False,
            "error": str(res.get("response") or "bilinmeyen hata"),
            "endpoint": _endpoint(cfg),
        }
    return {"success": True, "message": "Test e-postası gönderildi"}


# ── Admin menü tercihleri (SUNUCU tarafı — cihazdan bağımsız) ────────────────
# Sorun: menü sıra/gizleme tercihleri localStorage'daydı → başka cihaz/tarayıcıda
# veya veri temizliğinde sıfırlanıp gizlenen sekme (ör. Görevler) geri geliyordu.
# Çözüm: tercih kullanıcının hesabında tutulur; tüm cihazlarda aynı ve kalıcıdır.
@router.get("/admin-menu-prefs")
async def get_admin_menu_prefs(current_user: dict = Depends(require_admin)):
    u = await db.users.find_one({"id": current_user.get("id")}, {"_id": 0, "admin_menu_prefs": 1})
    return (u or {}).get("admin_menu_prefs") or {"order": None, "hidden": []}


@router.put("/admin-menu-prefs")
async def put_admin_menu_prefs(payload: dict, current_user: dict = Depends(require_admin)):
    prefs = {
        "order": payload.get("order") if isinstance(payload.get("order"), list) else None,
        "hidden": [str(x) for x in (payload.get("hidden") or []) if x][:100],
    }
    await db.users.update_one({"id": current_user.get("id")}, {"$set": {"admin_menu_prefs": prefs}})
    return {"success": True, **prefs}
