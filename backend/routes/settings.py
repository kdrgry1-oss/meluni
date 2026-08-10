from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from datetime import datetime, timezone
import re

from .deps import db, require_admin

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
        async for _c in db.coupons.find({"is_active": True, "free_shipping": True, "auto_apply": True},
                                        {"_id": 0, "min_cart_total": 1}):
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

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

@router.post("/maintenance/notify")
async def maintenance_notify_subscribe(payload: dict):
    """Public: bakım modu sırasında 'açılınca haber ver' e-posta toplama."""
    email = (payload.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
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
async def maintenance_subscribers_list(current_user: dict = Depends(require_admin)):
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
    return {
        "maintenance_mode": bool(settings.get("maintenance_mode", False)),
        "maintenance_title": settings.get("maintenance_title") or "Sitemiz sizin için yenileniyor",
        "maintenance_message": settings.get("maintenance_message") or "Çok yakında, daha iyi bir alışveriş deneyimiyle buradayız. Anlayışınız için teşekkür ederiz.",
        "logo_url": settings.get("logo_url") or "",
        "site_name": settings.get("site_name") or "FACETTE",
    }

@router.get("")
async def get_settings():
    """Get global settings"""
    settings = await db.settings.find_one({"id": "main"}, {"_id": 0})
    if not settings:
        settings = {
            "id": "main",
            "site_name": "FACETTE",
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
        await db.settings.insert_one(settings.copy())

    # Storefront kargo bilgisi (sabit kod yerine ayardan) — TEK KAYNAK (bkz. resolve_shipping_fee)
    settings["shipping_fee"] = resolve_shipping_fee(settings)
    settings["free_shipping_threshold"] = await resolve_free_shipping_threshold(settings)

    return settings

@router.post("")
async def update_settings(
    settings_data: dict,
    current_user: dict = Depends(require_admin)
):
    """Update global settings"""
    settings_data.pop("_id", None)
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
    return {"message": "Ayarlar güncellendi"}


# =============================================================================
# Ödeme Tipleri — Havale/EFT banka hesaplari + odeme entegrasyon ozeti
# Veri: db.settings._id="payment" { bank_accounts: [ {id,bank_name,branch,iban,
#       account_holder,is_default} ] }
# =============================================================================
import uuid as _uuid


def _seed_default_banks():
    return [{
        "id": _uuid.uuid4().hex[:12],
        "bank_name": "TÜRKİYE İŞ BANKASI",
        "branch": "CUMHURİYET CADDESİ ESENYURT ŞUBESİ",
        "iban": "TR86 0006 4000 0011 4540 1414 67",
        "account_holder": "FACETTE DIŞ TİC. A.Ş",
        "is_default": True,
    }]


def _ensure_single_default(banks):
    if banks and not any(b.get("is_default") for b in banks):
        banks[0]["is_default"] = True
    return banks


@router.get("/payment-overview")
async def payment_overview(current_user: dict = Depends(require_admin)):
    """Ödeme Tipleri sayfasi: aktif odeme entegrasyonlari + havale banka hesaplari."""
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    banks = pay.get("bank_accounts") or []
    if not banks:
        banks = _seed_default_banks()
        await db.settings.update_one({"id": "payment"},
                                     {"$set": {"id": "payment", "bank_accounts": banks}}, upsert=True)
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
        "configured": bool(banks),
        "active": bool(banks),
        "settings_path": "/admin/odeme-tipleri",
    }]
    return {"integrations": integrations, "bank_accounts": banks}


@router.post("/bank-accounts")
async def upsert_bank_account(payload: Dict[str, Any], current_user: dict = Depends(require_admin)):
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {"id": "payment", "bank_accounts": []}
    banks = pay.get("bank_accounts") or []
    acc_id = str(payload.get("id") or "").strip() or _uuid.uuid4().hex[:12]
    acc = {
        "id": acc_id,
        "bank_name": str(payload.get("bank_name") or "").strip(),
        "branch": str(payload.get("branch") or "").strip(),
        "iban": str(payload.get("iban") or "").strip().upper(),
        "account_holder": str(payload.get("account_holder") or "").strip(),
        "is_default": bool(payload.get("is_default")),
    }
    if not acc["bank_name"] or not acc["iban"]:
        raise HTTPException(status_code=400, detail="Banka adı ve IBAN zorunlu")
    found = False
    for i, b in enumerate(banks):
        if b.get("id") == acc_id:
            banks[i] = acc
            found = True
            break
    if not found:
        banks.append(acc)
    if acc["is_default"]:
        for b in banks:
            b["is_default"] = (b.get("id") == acc_id)
    _ensure_single_default(banks)
    await db.settings.update_one({"id": "payment"},
                                 {"$set": {"id": "payment", "bank_accounts": banks}}, upsert=True)
    return {"bank_accounts": banks}


@router.delete("/bank-accounts/{acc_id}")
async def delete_bank_account(acc_id: str, current_user: dict = Depends(require_admin)):
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    banks = [b for b in (pay.get("bank_accounts") or []) if b.get("id") != acc_id]
    _ensure_single_default(banks)
    await db.settings.update_one({"id": "payment"},
                                 {"$set": {"id": "payment", "bank_accounts": banks}}, upsert=True)
    return {"bank_accounts": banks}


@router.post("/bank-accounts/{acc_id}/default")
async def set_default_bank_account(acc_id: str, current_user: dict = Depends(require_admin)):
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    banks = pay.get("bank_accounts") or []
    if not any(b.get("id") == acc_id for b in banks):
        raise HTTPException(status_code=404, detail="Hesap bulunamadı")
    for b in banks:
        b["is_default"] = (b.get("id") == acc_id)
    await db.settings.update_one({"id": "payment"},
                                 {"$set": {"id": "payment", "bank_accounts": banks}}, upsert=True)
    return {"bank_accounts": banks}



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
async def save_order_statuses(payload: Dict[str, Any], current_user: dict = Depends(require_admin)):
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
    pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    banks = pay.get("bank_accounts") or []
    bank = next((b for b in banks if b.get("is_default")), None) or (banks[0] if banks else None)
    return {"bank": bank}


# =============================================================================
# Gönderici / Depo Adresi — kargo & iade etiketlerinde kullanılır (settings.store_info)
# =============================================================================
@router.get("/store-info")
async def get_store_info(current_user: dict = Depends(require_admin)):
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
async def save_store_info(payload: Dict[str, Any], current_user: dict = Depends(require_admin)):
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
    return {"success": True}


# =============================================================================
# E-posta (SMTP / Zoho Mail) Ayarları — settings.id="email_smtp"
# Tüm bildirim & toplu e-postalar bu hesaptan gönderilir. Şifre DB'de tutulur,
# API yanıtında ASLA dönülmez (yalnızca password_set bilgisi).
# =============================================================================
@router.get("/email-smtp")
async def get_email_smtp(current_user: dict = Depends(require_admin)):
    s = await db.settings.find_one({"id": "email_smtp"}, {"_id": 0}) or {}
    return {
        "enabled": bool(s.get("enabled", False)),
        "host": s.get("host", "smtp.zoho.eu"),
        "port": int(s.get("port", 465)),
        "secure": s.get("secure", "ssl"),
        "username": s.get("username", ""),
        "from_name": s.get("from_name", "FACETTE"),
        # Sipariş maillerinin ayrı gönderen kimliği (ör. siparis@...). Boşsa sipariş
        # mailleri de varsayılan gönderenden gider. Pazarlama (SES) ile işlemsel (Zoho)
        # itibar ayrımının tamamlayıcısı: sipariş mailleri şifre/pazarlamadan ayrışır.
        "order_from_email": s.get("order_from_email", ""),
        "order_from_name": s.get("order_from_name", ""),
        "order_reply_to": s.get("order_reply_to", ""),
        "password_set": bool(s.get("password")),
    }


@router.post("/email-smtp")
async def save_email_smtp(payload: Dict[str, Any], current_user: dict = Depends(require_admin)):
    existing = await db.settings.find_one({"id": "email_smtp"}, {"_id": 0}) or {}
    pwd = payload.get("password")
    data = {
        "id": "email_smtp",
        "enabled": bool(payload.get("enabled", False)),
        "host": (str(payload.get("host") or "smtp.zoho.eu")).strip(),
        "port": int(payload.get("port") or 465),
        "secure": (str(payload.get("secure") or "ssl")).strip().lower(),
        "username": (str(payload.get("username") or "")).strip(),
        "from_name": (str(payload.get("from_name") or "FACETTE")).strip(),
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
    return {"success": True}


@router.post("/email-smtp/test")
async def test_email_smtp(payload: Dict[str, Any], current_user: dict = Depends(require_admin)):
    to = (str(payload.get("to") or "")).strip()
    if not to:
        raise HTTPException(status_code=400, detail="Test için alıcı e-posta gerekli")
    from email_smtp import send_smtp_email, get_smtp_config, _endpoint
    cfg = await get_smtp_config(db)
    res = await send_smtp_email(
        db, to,
        "Facette SMTP Test",
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
