"""
=============================================================================
notifications.py — Admin bildirim ayarları ve şablon CRUD
=============================================================================
Endpoints:
  GET  /api/notifications/providers           → mevcut config
  POST /api/notifications/providers           → kaydet (sms_active, whatsapp_active, email_active, providers{})
  GET  /api/notifications/providers/catalog   → kanal+sağlayıcı listesi (UI için)

  GET  /api/notifications/templates           → tüm event×channel şablonları
  POST /api/notifications/templates           → tek şablonu kaydet (upsert)
  POST /api/notifications/templates/seed      → default şablonları oluştur (boş değilse dokunmaz)

  POST /api/notifications/test                → test gönderimi
  GET  /api/notifications/logs                → son N log
=============================================================================
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .deps import db, require_admin
from notification_service import (
    SMS_PROVIDERS,
    DEFAULT_EVENTS,
    CHANNELS,
    send_notification,
    test_provider,
    render_template,
    _get_template,
)
from email_layout import email_shell, info_row

router = APIRouter(prefix="/notifications", tags=["notifications"])


class ProviderConfigReq(BaseModel):
    sms_active: Optional[str] = None
    whatsapp_active: bool = False
    email_active: bool = True
    providers: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class TemplateReq(BaseModel):
    event: str
    channel: str  # sms|email|whatsapp
    enabled: bool = True
    subject: Optional[str] = ""
    body: str = ""
    meta_template_name: Optional[str] = None
    meta_template_lang: Optional[str] = "tr"
    meta_template_params: Optional[List[str]] = None


class TestReq(BaseModel):
    channel: str
    provider_key: Optional[str] = None
    to: str
    message: str = "{store_name} test bildirimi ✓"


class TestTemplateReq(BaseModel):
    event: str
    channel: str  # sms|whatsapp|email
    to: str
    order_number: Optional[str] = None  # verilirse bu siparişi baz al; boşsa en son kargolanan


@router.get("/providers/catalog")
async def get_catalog(current_user: dict = Depends(require_admin)):
    return {
        "sms_providers": SMS_PROVIDERS,
        "channels": CHANNELS,
        "events": DEFAULT_EVENTS,
    }


@router.get("/providers")
async def get_providers(current_user: dict = Depends(require_admin)):
    cfg = await db.settings.find_one({"id": "notification_providers"}, {"_id": 0})
    if not cfg:
        cfg = {
            "id": "notification_providers",
            "sms_active": None,
            "whatsapp_active": False,
            "email_active": True,
            "providers": {},
        }
    # Secret alanları maskele (ekranda görünsün ama ham şekilde değil)
    SECRET_FIELDS = {"password", "auth_token", "api_hash", "api_key", "access_token", "api_secret", "app_secret", "page_access_token"}
    masked = dict(cfg)
    prov = {}
    for pkey, fields in (cfg.get("providers") or {}).items():
        prov[pkey] = {}
        for f, v in (fields or {}).items():
            if f in SECRET_FIELDS and v:
                s = str(v)
                prov[pkey][f] = (s[:2] + "****" + s[-2:]) if len(s) > 6 else "****"
                prov[pkey][f"__has_{f}"] = True
            else:
                prov[pkey][f] = v
    masked["providers"] = prov
    return masked


@router.post("/providers")
async def save_providers(req: ProviderConfigReq, current_user: dict = Depends(require_admin)):
    # Mevcut config (gizli alanlar için). Eğer UI maskeli bir değeri aynen geri gönderdiyse
    # orijinal değeri koru (yani "xx****yy" gönderilmişse değiştirmiyor sayılır).
    existing = await db.settings.find_one({"id": "notification_providers"}, {"_id": 0}) or {}
    existing_provs = existing.get("providers", {})
    SECRET_FIELDS = {"password", "auth_token", "api_hash", "api_key", "access_token", "api_secret", "app_secret", "page_access_token"}
    merged_provs: Dict[str, Dict[str, Any]] = {}
    for pkey, fields in req.providers.items():
        merged = dict(fields or {})
        old = existing_provs.get(pkey, {}) or {}
        for f in list(merged.keys()):
            if f in SECRET_FIELDS:
                val = merged[f]
                # UI'den MASKELİ (xx****yy) geldi → dokunulmadı, eski değeri koru.
                # BOŞ geldi → kullanıcı SİLMEK istedi → boş bırak (eskiyi koruma).
                # (Eski davranış boş'u da koruyordu → secret alanı hiç temizlenemiyordu.)
                if isinstance(val, str) and "****" in val:
                    if old.get(f):
                        merged[f] = old[f]
        # __has_ bayraklarını DB'ye yazma
        merged = {k: v for k, v in merged.items() if not k.startswith("__has_")}
        merged_provs[pkey] = merged

    data = {
        "id": "notification_providers",
        "sms_active": req.sms_active,
        "whatsapp_active": req.whatsapp_active,
        "email_active": req.email_active,
        "providers": merged_provs,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": current_user.get("email", ""),
    }
    await db.settings.update_one({"id": "notification_providers"}, {"$set": data}, upsert=True)
    return {"success": True, "message": "Bildirim sağlayıcı ayarları kaydedildi"}


@router.get("/templates")
async def list_templates(current_user: dict = Depends(require_admin)):
    rows = await db.notification_templates.find({}, {"_id": 0}).to_list(length=None)
    return {"templates": rows}


@router.post("/templates")
async def upsert_template(req: TemplateReq, current_user: dict = Depends(require_admin)):
    if req.channel not in CHANNELS:
        raise HTTPException(status_code=400, detail="Geçersiz kanal")
    data = req.model_dump()
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["updated_by"] = current_user.get("email", "")
    await db.notification_templates.update_one(
        {"event": req.event, "channel": req.channel},
        {"$set": data},
        upsert=True,
    )
    return {"success": True}


_DEFAULT_TEMPLATES = {
    # (event, channel) → payload
    ("order_confirmed", "sms"):
        "Merhaba {customer_name}, {order_number} numarali siparisiniz onaylandi. {store_name}",
    ("order_shipped", "sms"):
        "Siparisiniz kargoya verildi. Kargo takip: {tracking_number}. {store_name}",
    ("order_delivered", "sms"):
        "Siparisiniz teslim edildi. Bizi tercih ettiginiz icin tesekkurler. {store_name}",
    ("order_undelivered", "sms"):
        "Kargonuz teslim edilemedi, subede bekliyor. Takip: {tracking_number}. {store_name}",
    ("order_cancelled", "sms"):
        "{order_number} numarali siparisiniz iptal edildi. Bilgi: destek@meluni.com.tr",
    ("order_awaiting_payment", "sms"):
        "Merhaba {customer_name}, {order_number} numarali siparisiniz alindi. Havale/EFT odemenizi bekliyoruz; detaylar e-postanizda. {store_name}",
    ("order_payment_reminder", "sms"):
        "Sayin {customer_name}, {order_number} numarali siparisinizin havale/EFT odemesini henuz alamadik. {bank_name} IBAN {bank_iban} ({bank_account_holder}), Tutar {amount}. Odeme bildirimi: {payment_url} {store_name}",
    ("order_payment_notified", "sms"):
        "Merhaba {customer_name}, {order_number} numarali siparisiniz icin odeme bildiriminiz alindi, kontrol ediliyor. {store_name}",
    ("order_return_requested", "sms"):
        "Merhaba {customer_name}, {order_number} numarali siparisiniz icin iade talebiniz olusturuldu. Iade kodu: {return_code}. {store_name}",
    ("order_return_approved", "sms"):
        "Merhaba {customer_name}, {order_number} numarali siparisinize ait iadeniz onaylandi. Iade bedeli: {refund_amount} TL. {store_name}",
    ("order_return_rejected", "sms"):
        "Merhaba {customer_name}, {order_number} numarali iade talebiniz degerlendirildi. Detaylar e-postanizda. {store_name}",
    ("order_refunded", "sms"):
        "Merhaba {customer_name}, {order_number} numarali siparisiniz icin iade bedeli {refund_amount} TL hesabiniza iade edildi. {store_name}",
    ("order_partial_refunded", "sms"):
        "Merhaba {customer_name}, {order_number} numarali siparisiniz icin {refund_amount} TL kismi iade yapildi. {store_name}",
    ("password_reset_otp", "sms"):
        "{store_name} dogrulama kodunuz: {otp_code} (5 dk gecerli).",
    ("abandoned_cart", "sms"):
        "Sepetinizde urunler kaldi! Siparis tamamlama baglantisi: {cart_url}",
}


# =============================================================================
# Markalı HTML e-posta şablonları — tümü email_layout.email_shell ile üretilir
# (üstte MELUNİ logosu · ortada bilgiler · altta INSTAGRAM·TIKTOK + telif).
# {customer_name} vb. placeholder'lar korunur; gönderimde render_template doldurur.
# =============================================================================

# Sipariş onay maili gövdesi: sipariş no + tarih + kalemler + toplamlar + adres
_CONFIRMED_BODY = (
    info_row("Sipariş Numarası", "{order_number}")
    + '<div style="font-size:11px;color:#9a9a93;margin:4px 0 14px;padding:0 2px;">Sipariş tarihi: {order_date}</div>'
    + "{items_html}"
    + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:13px;color:#444;margin-top:6px;">'
      '<tr><td style="padding:6px 0;">Ara Toplam</td><td style="text-align:right;padding:6px 0;">{subtotal} TL</td></tr>'
      '<tr><td style="padding:6px 0;">Kargo</td><td style="text-align:right;padding:6px 0;">{shipping_cost} TL</td></tr>'
      '<tr><td style="padding:6px 0;color:#b08968;">İndirim</td><td style="text-align:right;padding:6px 0;color:#b08968;">-{discount} TL</td></tr>'
      '<tr><td style="padding:14px 0 0;font-weight:600;color:#1a1a1a;border-top:1px solid #eee;">Toplam</td>'
      '<td style="text-align:right;padding:14px 0 0;font-weight:600;color:#1a1a1a;border-top:1px solid #eee;">{amount}</td></tr>'
      '</table>'
    + '<div style="margin-top:24px;">'
      '<div style="font-size:11px;letter-spacing:1.5px;color:#9a9a93;text-transform:uppercase;margin:0 0 8px;">Teslimat Adresi</div>'
      '<div style="font-size:13px;color:#444;line-height:1.6;">{shipping_full_name}<br/>{shipping_address}<br/>{shipping_district} / {shipping_city}<br/>{shipping_phone}</div>'
      '</div>'
)

_EMAIL_HTML_TEMPLATES = {
    "order_confirmed": {
        "subject": "Siparişin alındı · {order_number}",
        "body": email_shell(
            icon="✓", eyebrow="SİPARİŞİN ALINDI", title="Siparişin alındı",
            intro_html="Sevgili {customer_name}, siparişin için teşekkür ederiz. Hazırlanmaya başladığında seni bilgilendireceğiz.",
            body_html=_CONFIRMED_BODY,
            cta_text="SİPARİŞİMİ GÖRÜNTÜLE", cta_url="{order_link}",
            preheader="Siparişin alındı · {order_number}",
        ),
    },
    "order_pending": {
        "subject": "Siparişin alındı · {order_number}",
        "body": email_shell(
            icon="✓", eyebrow="SİPARİŞİN ALINDI", title="Siparişin alındı",
            intro_html="Sevgili {customer_name}, {order_number} numaralı siparişin alındı ve onay bekliyor. Onaylandığında seni bilgilendireceğiz.",
            cta_text="SİPARİŞİMİ GÖRÜNTÜLE", cta_url="{order_link}",
        ),
    },
    "order_awaiting_payment": {
        "subject": "Siparişin alındı · Ödeme bekleniyor · {order_number}",
        "body": email_shell(
            icon="₺", eyebrow="ÖDEME BEKLENİYOR", title="Ödemeni bekliyoruz",
            intro_html="Sevgili {customer_name}, {order_number} numaralı siparişini aldık. Havale/EFT ödemen tarafımıza ulaştığında siparişin hazırlanmaya başlanır.",
            body_html=info_row("Sipariş Tutarı", "{amount}"),
            cta_text="SİPARİŞİMİ GÖRÜNTÜLE", cta_url="{order_link}",
        ),
    },
    "order_payment_reminder": {
        "subject": "Ödeme hatırlatması · {order_number}",
        "body": email_shell(
            icon="₺", eyebrow="ÖDEME HATIRLATMA", title="Ödemeni bekliyoruz",
            intro_html="Sevgili {customer_name}, {order_number} numaralı siparişinin havale/EFT ödemesini henüz alamadık. Siparişini kaçırmamak için ödemeni tamamlaman yeterli.",
            body_html=(
                info_row("Sipariş Tutarı", "{amount}")
                + '<div style="border:1px solid #eee;border-radius:8px;padding:14px;margin:14px 0;">'
                  '<div style="font-weight:700;margin-bottom:8px;">Havale / EFT Bilgileri</div>'
                  '<div>Banka: {bank_name}</div><div>Şube: {bank_branch}</div>'
                  '<div>IBAN: <b>{bank_iban}</b></div><div>Hesap Sahibi: {bank_account_holder}</div></div>'
            ),
            cta_text="ÖDEME BİLDİRİMİ YAP", cta_url="{payment_url}",
            preheader="Ödeme hatırlatması · {order_number}",
        ),
    },
    "order_payment_notified": {
        "subject": "Ödeme bildirimin alındı · {order_number}",
        "body": email_shell(
            icon="✓", eyebrow="ÖDEME BİLDİRİMİ", title="Bildirimin alındı",
            intro_html="Sevgili {customer_name}, {order_number} numaralı siparişin için ödeme bildirimini aldık. Ödemen doğrulandığında siparişin hazırlanmaya başlanır.",
        ),
    },
    "order_preparing": {
        "subject": "Siparişin hazırlanıyor · {order_number}",
        "body": email_shell(
            icon="✓", eyebrow="SİPARİŞ DURUMU", title="Siparişin hazırlanıyor",
            intro_html="Sevgili {customer_name}, {order_number} numaralı siparişin özenle hazırlanıyor. Kargoya verildiğinde seni bilgilendireceğiz.",
        ),
    },
    "order_packed": {
        "subject": "Siparişin paketlendi · {order_number}",
        "body": email_shell(
            icon="✓", eyebrow="SİPARİŞ DURUMU", title="Siparişin paketlendi",
            intro_html="Sevgili {customer_name}, {order_number} numaralı siparişin paketlendi ve kargoya verilmek üzere hazır.",
        ),
    },
    "order_ready_to_ship": {
        "subject": "Siparişin kargoya hazır · {order_number}",
        "body": email_shell(
            icon="↗", eyebrow="SİPARİŞ DURUMU", title="Kargoya hazır",
            intro_html="Sevgili {customer_name}, {order_number} numaralı siparişin kargoya verilmek üzere hazır. Çok yakında yola çıkıyor.",
        ),
    },
    "order_shipped": {
        "subject": "Kargoya verildi · {order_number}",
        "body": email_shell(
            icon="↗", eyebrow="KARGO", title="Kargoya verildi",
            intro_html="Sevgili {customer_name}, siparişin {cargo_provider} kargosuna teslim edildi.",
            body_html=info_row("Kargo Takip Numarası", "{tracking_number}"),
            cta_text="KARGO TAKİBİ", cta_url="{tracking_link}",
            preheader="Siparişin yola çıktı · {tracking_number}",
        ),
    },
    "order_in_transit": {
        "subject": "Kargon yolda · {order_number}",
        "body": email_shell(
            icon="↗", eyebrow="KARGO", title="Kargon yolda",
            intro_html="Sevgili {customer_name}, siparişin sana doğru yolda. Takip numaranla durumu izleyebilirsin.",
            body_html=info_row("Kargo Takip Numarası", "{tracking_number}"),
            cta_text="KARGO TAKİBİ", cta_url="{tracking_link}",
        ),
    },
    "order_out_for_delivery": {
        "subject": "Siparişin dağıtımda · {order_number}",
        "body": email_shell(
            icon="↗", eyebrow="KARGO", title="Siparişin dağıtımda",
            intro_html="Sevgili {customer_name}, siparişin bugün adresine teslim edilmek üzere dağıtıma çıktı.",
            body_html=info_row("Kargo Takip Numarası", "{tracking_number}"),
            cta_text="KARGO TAKİBİ", cta_url="{tracking_link}",
        ),
    },
    "order_delivered": {
        "subject": "Siparişin teslim edildi · {order_number}",
        "body": email_shell(
            icon="★", eyebrow="TESLİMAT", title="Siparişin teslim edildi",
            intro_html="Merhaba {customer_name}, {order_number} numaralı siparişin teslim edildi. Bizi tercih ettiğin için teşekkür ederiz.",
        ),
    },
    "order_undelivered": {
        "subject": "Kargon şubede bekliyor · {order_number}",
        "body": email_shell(
            icon="◷", eyebrow="KARGO", title="Kargon şubede bekliyor",
            intro_html="Merhaba {customer_name}, kargon teslim edilemedi ve şubede seni bekliyor. Takip numaranla detayları görebilirsin.",
            body_html=info_row("Kargo Takip Numarası", "{tracking_number}"),
        ),
    },
    "order_cancelled": {
        "subject": "Siparişin iptal edildi · {order_number}",
        "body": email_shell(
            icon="✕", eyebrow="SİPARİŞ", title="Siparişin iptal edildi",
            intro_html="Merhaba {customer_name}, {order_number} numaralı siparişin iptal edilmiştir. Ödemen alındıysa iade edilecektir.",
        ),
    },
    "order_returned": {
        "subject": "İaden tamamlandı · {order_number}",
        "body": email_shell(
            icon="✓", eyebrow="İADE", title="İaden tamamlandı",
            intro_html="Merhaba {customer_name}, {order_number} numaralı siparişine ait iaden tarafımıza ulaştı ve işleme alındı. İade bedelin kısa süre içinde tarafına aktarılacaktır.",
        ),
    },
    "order_refunded": {
        "subject": "İade bedelin ödendi · {order_number}",
        "body": email_shell(
            icon="₺", eyebrow="İADE", title="İade bedelin ödendi",
            intro_html="Merhaba {customer_name}, {order_number} numaralı siparişin için iade bedeli hesabına/kartına iade edilmiştir. Bankana bağlı olarak hesabına yansıması birkaç iş günü sürebilir.",
        ),
    },
    "abandoned_cart": {
        "subject": "Sepetinde ürünler kaldı",
        "body": email_shell(
            icon="→", eyebrow="SEPETİN", title="Sepetinde ürünler kaldı",
            intro_html="Beğendiğin ürünler seni bekliyor. Siparişini tamamlamak için aşağıdaki butona dokunman yeterli.",
            cta_text="SEPETE DÖN", cta_url="{cart_url}",
        ),
    },
    "order_return_in_transit": {
        "subject": "İaden kargoda · {order_number}",
        "body": email_shell(
            icon="↩", eyebrow="İADE", title="İaden kargoda",
            intro_html="Merhaba {customer_name}, {order_number} numaralı siparişine ait iade kargon yola çıktı. Bize ulaştığında işleme alıp seni bilgilendireceğiz.",
            body_html=info_row("Kargo Takip Numarası", "{tracking_number}"),
        ),
    },
    "wishlist_back_in_stock": {
        "subject": "Favorindeki ürün tekrar stokta · {product_name}",
        "body": email_shell(
            icon="★", eyebrow="TEKRAR STOKTA", title="Beklediğin ürün geri geldi",
            intro_html="Merhaba {customer_name}, favori listendeki <b>{product_name}</b> tekrar stoklarımızda. Tükenmeden tamamlamak istersen aşağıdan inceleyebilirsin.",
            cta_text="ÜRÜNÜ İNCELE", cta_url="{product_link}",
            note_title="Stoklarla sınırlı",
            note_html="Bu ürün yeniden hızla tükenebilir; kaçırmamak için kısa sürede karar vermeni öneririz.",
            preheader="Favorindeki ürün tekrar stokta",
        ),
    },
    "welcome": {
        "subject": "Aramıza hoş geldin · MELUNİ",
        "body": email_shell(
            icon="✓", eyebrow="HOŞ GELDİN", title="Aramıza hoş geldin",
            intro_html="Merhaba {customer_name}, MELUNİ ailesine katıldığın için teşekkür ederiz. Yeni sezon parçalarını ve sana özel fırsatları keşfetmeye hemen başlayabilirsin.",
            body_html="{coupon_block}",
            cta_text="ALIŞVERİŞE BAŞLA", cta_url="{site_url}",
        ),
    },
}


# MELUNİ Muli marka e-posta şablonları (email_templates.py) — eski ZaraHome tasarımını override eder.
try:
    from email_templates import MELUNI_EMAIL_TEMPLATES as _FET
    _EMAIL_HTML_TEMPLATES = _FET
except Exception:
    pass


@router.post("/templates/seed")
async def seed_templates(
    force: bool = False,
    current_user: dict = Depends(require_admin),
):
    """Default şablonları oluştur. force=true ise mevcut zengin olmayan
    e-posta şablonlarını da günceller (override)."""
    created = 0
    updated = 0
    for ev in DEFAULT_EVENTS:
        ev_key = ev["key"]
        for ch in CHANNELS:
            existing = await db.notification_templates.find_one({"event": ev_key, "channel": ch}, {"_id": 0})
            # Zengin email şablonu varsa onu kullan
            if ch == "email" and ev_key in _EMAIL_HTML_TEMPLATES:
                rich = _EMAIL_HTML_TEMPLATES[ev_key]
                body = rich["body"]
                subj = rich["subject"]
            else:
                body = _DEFAULT_TEMPLATES.get((ev_key, ch), "")
                subj = ev["name"] if ch == "email" else ""
                if ch == "email" and not body:
                    body = email_shell(
                        eyebrow="MELUNİ", title=ev["name"],
                        intro_html="Merhaba {customer_name}, " + ev["name"].lower() + " bildirimini sizinle paylaşıyoruz.",
                    )

            if existing:
                # force ile yeniden seedle (manuel düzenlenmemişlere yeniden uygula)
                if not force:
                    continue
                if existing.get("manually_edited"):
                    continue  # admin manuel değiştirdiyse dokunma
                await db.notification_templates.update_one(
                    {"event": ev_key, "channel": ch},
                    {"$set": {
                        "subject": subj,
                        "body": body,
                        "enabled": bool(body),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }}
                )
                updated += 1
            else:
                await db.notification_templates.insert_one({
                    "event": ev_key,
                    "channel": ch,
                    "enabled": bool(body),
                    "subject": subj,
                    "body": body,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                created += 1
    return {"success": True, "created": created, "updated": updated}


@router.post("/test")
async def send_test(req: TestReq, current_user: dict = Depends(require_admin)):
    res = await test_provider(db, req.channel, req.provider_key, req.to, req.message)
    return res


@router.post("/test-template")
async def send_test_template(req: TestTemplateReq, current_user: dict = Depends(require_admin)):
    """
    Seçilen sipariş durumunun (event) GERÇEK şablonunu, en son kargoya verilen
    siparişin gerçek verisiyle doldurup verilen numaraya/e-postaya gönderir.
    Böylece her durumda SMS/WhatsApp/E-posta'nın tam nasıl gideceği görülür.
    """
    # Sipariş no verildiyse O siparişi baz al; verilmediyse en son kargoya verileni.
    req_no = (req.order_number or "").strip()
    if req_no:
        order = await db.orders.find_one({"order_number": req_no}, {"_id": 0})
        if not order:
            # numara biçimi/harf farkını tolere et (tam eşleşme, büyük/küçük harf duyarsız)
            import re as _re
            order = await db.orders.find_one(
                {"order_number": {"$regex": f"^{_re.escape(req_no)}$", "$options": "i"}}, {"_id": 0}
            )
        if not order:
            raise HTTPException(status_code=404, detail=f"Sipariş bulunamadı: {req_no}")
    else:
        # En son kargoya verilen siparişi baz al (tüm değişkenler bu siparişte dolu)
        order = await db.orders.find_one({"status": "shipped"}, {"_id": 0}, sort=[("shipped_at", -1)])
        if not order:
            order = await db.orders.find_one(
                {"cargo_tracking_number": {"$nin": [None, ""]}}, {"_id": 0}, sort=[("updated_at", -1)]
            )
        if not order:
            order = await db.orders.find_one({}, {"_id": 0}, sort=[("created_at", -1)])
        if not order:
            raise HTTPException(status_code=404, detail="Baz alınacak sipariş bulunamadı")

    addr = order.get("shipping_address") or {}
    full_name = (
        f"{addr.get('first_name','')} {addr.get('last_name','')}".strip()
        or addr.get("name") or addr.get("full_name") or "Müşterimiz"
    )
    cargo = order.get("cargo") or {}
    real_tn = (
        cargo.get("mng_nz_barkod") or cargo.get("mng_nz_gonderi_no")
        or cargo.get("mng_gonderi_no") or order.get("cargo_tracking_number") or ""
    ).strip()
    track_link = cargo.get("tracking_link") or order.get("cargo_tracking_url") or ""
    ev_name = next((e["name"] for e in DEFAULT_EVENTS if e["key"] == req.event), req.event)

    variables = {
        "customer_name": full_name,
        "name": full_name,
        "first_name": addr.get("first_name", ""),
        "order_number": order.get("order_number") or "",
        "tracking_number": real_tn,
        "tracking_link": track_link,
        "tracking_url": track_link,
        "cargo_provider": cargo.get("provider_name") or order.get("cargo_provider_name") or "MNG Kargo",
        "amount": float(order.get("total") or 0),
        "total": float(order.get("total") or 0),
        "status_label": ev_name,
        "otp_code": "123456",
        "cart_url": "https://meluni.com.tr/sepet",
    }

    to_phone = req.to.strip() if req.channel in ("sms", "whatsapp") else None
    to_email = req.to.strip() if req.channel == "email" else None

    res = await send_notification(
        db, req.event,
        to_phone=to_phone, to_email=to_email,
        variables=variables, channels=[req.channel],
    )

    # Render edilmiş önizleme (panelde göstermek için)
    preview = ""
    try:
        tpl = await _get_template(db, req.event, req.channel)
        if tpl:
            preview = render_template(tpl.get("body", "") or "", variables)
    except Exception:
        pass

    return {
        "success": True,
        "based_on_order": order.get("order_number"),
        "event": req.event,
        "event_name": ev_name,
        "channel": req.channel,
        "variables": variables,
        "preview": preview,
        "result": res,
    }


@router.get("/logs")
async def list_logs(limit: int = Query(50, ge=1, le=500), current_user: dict = Depends(require_admin)):
    rows = (
        await db.notification_logs.find({}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(length=limit)
    )
    return {"logs": rows}
