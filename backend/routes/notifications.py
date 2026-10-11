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

from .deps import db, require_admin, require_permission
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
from security.crypto import encrypt as _enc, is_encrypted

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
async def get_catalog(current_user: dict = Depends(require_permission("settings.view"))):
    return {
        "sms_providers": SMS_PROVIDERS,
        "channels": CHANNELS,
        "events": DEFAULT_EVENTS,
    }


@router.get("/providers")
async def get_providers(current_user: dict = Depends(require_permission("settings.view"))):
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
    from notification_service import PROVIDER_SECRET_FIELDS as SECRET_FIELDS  # denetim #6: tek liste
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
async def save_providers(req: ProviderConfigReq, current_user: dict = Depends(require_permission("settings.edit"))):
    # Mevcut config (gizli alanlar için). Eğer UI maskeli bir değeri aynen geri gönderdiyse
    # orijinal değeri koru (yani "xx****yy" gönderilmişse değiştirmiyor sayılır).
    existing = await db.settings.find_one({"id": "notification_providers"}, {"_id": 0}) or {}
    existing_provs = existing.get("providers", {})
    from notification_service import PROVIDER_SECRET_FIELDS as SECRET_FIELDS  # denetim #6: tek liste
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
        # DENETİM (kimlik B-1): sır alanlarını at-rest Fernet ile şifrele. Zaten şifreli
        # (v1:…) veya boş değerlere dokunma → idempotent, migration güvenli.
        for f in SECRET_FIELDS:
            v = merged.get(f)
            if isinstance(v, str) and v and not is_encrypted(v):
                merged[f] = _enc(v)
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
async def list_templates(current_user: dict = Depends(require_permission("settings.view"))):
    rows = await db.notification_templates.find({}, {"_id": 0}).to_list(length=None)
    return {"templates": rows}


@router.post("/templates")
async def upsert_template(req: TemplateReq, current_user: dict = Depends(require_permission("settings.edit"))):
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
        "Merhaba {customer_name}, {order_number} numaralı Meluni siparişiniz onaylandı. İlginiz için teşekkür ederiz.",
    ("order_shipped", "sms"):
        "Merhaba {customer_name}, {order_number} numaralı Meluni siparişiniz kargoya verilmiştir. Kargo gönderinizi aşağıdaki link üzerinden takip edebilirsiniz: {tracking_url}",
    ("order_delivered", "sms"):
        "Merhaba {customer_name}, {order_number} numaralı Meluni siparişiniz başarıyla teslim edilmiştir. Siparişinizi keyifle kullanmanızı dileriz. Bizi tercih ettiğiniz için teşekkür ederiz.",
    ("order_undelivered", "sms"):
        "Merhaba {customer_name}, {order_number} numaralı Meluni kargonuz teslim edilemedi, şubede sizi bekliyor. Takip: {tracking_url}",
    ("order_cancelled", "sms"):
        "Merhaba {customer_name}, {order_number} numaralı Meluni siparişiniz iptal edilmiştir. Bilgi için bizimle iletişime geçebilirsiniz.",
    ("order_awaiting_payment", "sms"):
        "Merhaba {customer_name}, {order_number} numaralı Meluni siparişiniz alındı. Havale/EFT ödemenizi bekliyoruz; ödeme detayları e-postanızda. Meluni",
    ("order_payment_reminder", "sms"):
        "Merhaba {customer_name}, {order_number} no'lu siparişiniz için havale/EFT ödemenizin henüz ulaşmadığını hatırlatmak isteriz. Ödemenizi tamamlamanızın ardından siparişiniz işleme alınacaktır. İlginiz için teşekkür ederiz.",
    ("order_payment_notified", "sms"):
        "Merhaba {customer_name}, {order_number} numaralı siparişiniz için ödeme bildiriminiz alındı, kontrol ediliyor. Meluni",
    ("order_payment_approved", "sms"):
        "Merhaba {customer_name}, {order_number} numaralı siparişinizin havale/EFT ödemesini aldık. Siparişiniz onaylandı ve işleme alınmıştır. İlginiz için teşekkür ederiz.",
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
        "Meluni dogrulama kodunuz: {otp_code} (5 dk gecerli).",
    ("login_verification_code", "sms"):
        "Meluni giris dogrulama kodunuz: {otp_code} (5 dk gecerli).",
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

# DOĞRULAMA KODU (giriş / şifre sıfırlama) — kodun kendisi BÜYÜK ve net gösterilir.
# Bu şablon yoksa sistem jenerik gövde üretiyordu ve e-postada KOD HİÇ GÖRÜNMÜYORDU.
_OTP_BODY = (
    '<div style="margin:18px 0 6px;text-align:center">'
    '<div style="font-size:11px;letter-spacing:2px;color:#8a8a8a;text-transform:uppercase">Doğrulama kodunuz</div>'
    '<div style="display:inline-block;margin-top:10px;padding:14px 26px;border:1px solid #e3e0da;'
    'border-radius:8px;background:#faf9f7;font-family:\'Courier New\',monospace;font-size:34px;'
    'font-weight:700;letter-spacing:10px;color:#111">{otp_code}</div>'
    '<div style="margin-top:12px;font-size:12px;color:#8a8a8a">Kod <b>5 dakika</b> geçerlidir.</div>'
    '</div>'
)

_EMAIL_HTML_TEMPLATES = {
    "login_verification_code": {
        "subject": "Giriş doğrulama kodunuz: {otp_code}",
        "body": email_shell(
            eyebrow="GÜVENLİK", title="Giriş Doğrulama Kodu",
            intro_html="Merhaba {customer_name}, giriş yapabilmek için aşağıdaki doğrulama kodunu kullanın.",
            body_html=_OTP_BODY,
            note_title="Bu girişi siz yapmadıysanız",
            note_html="Kodu kimseyle paylaşmayın. Şifrenizi değiştirmenizi ve bize bildirmenizi öneririz.",
            preheader="Giriş doğrulama kodunuz: {otp_code}",
        ),
    },
    "password_reset_otp": {
        "subject": "Doğrulama kodunuz: {otp_code}",
        "body": email_shell(
            eyebrow="GÜVENLİK", title="Doğrulama Kodu",
            intro_html="Merhaba {customer_name}, hesabınıza giriş için doğrulama kodunuz aşağıdadır.",
            body_html=_OTP_BODY,
            note_title="Bu isteği siz yapmadıysanız",
            note_html="Kodu kimseyle paylaşmayın ve bu e-postayı dikkate almayın. Şifreniz değişmez.",
            preheader="Doğrulama kodunuz: {otp_code}",
        ),
    },
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
    current_user: dict = Depends(require_permission("settings.edit")),
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


@router.post("/templates/fix-names")
async def fix_template_names(current_user: dict = Depends(require_permission("settings.edit"))):
    """Kayıtlı TÜM şablonlarda (subject + body) sadece ADI basan yer tutucuları
    AD SOYAD basan {customer_name} ile değiştirir. Kullanıcı isteği: bazı özel SMS
    şablonları {first_name} kullandığı için müşteriye yalnız ad gidiyordu; soyad da
    görünsün. İdempotent — tekrar çalıştırmak zarar vermez.

    Değişimler (sıra önemli — önce birleşik kalıp):
      "{first_name} {last_name}" → "{customer_name}"   (çift yazımı engelle)
      "{ad} {soyad}"             → "{customer_name}"
      "{first_name}" / "{ad}" / "{isim}" → "{customer_name}"
    """
    import re as _re
    _COMBINED = [
        (_re.compile(r"\{first_name\}\s+\{last_name\}"), "{customer_name}"),
        (_re.compile(r"\{ad\}\s+\{soyad\}"), "{customer_name}"),
        (_re.compile(r"\{isim\}\s+\{soyisim\}"), "{customer_name}"),
    ]
    _SINGLE = [
        (_re.compile(r"\{first_name\}"), "{customer_name}"),
        (_re.compile(r"\{ad\}"), "{customer_name}"),
        (_re.compile(r"\{isim\}"), "{customer_name}"),
    ]

    def _fix(text):
        if not text or not isinstance(text, str):
            return text, False
        _new = text
        for rx, rep in _COMBINED:
            _new = rx.sub(rep, _new)
        for rx, rep in _SINGLE:
            _new = rx.sub(rep, _new)
        return _new, (_new != text)

    changed = 0
    samples = []
    async for t in db.notification_templates.find({}, {"_id": 0}):
        _set = {}
        for _field in ("subject", "body"):
            _fixed, _did = _fix(t.get(_field))
            if _did:
                _set[_field] = _fixed
        if _set:
            _set["updated_at"] = datetime.now(timezone.utc).isoformat()
            await db.notification_templates.update_one(
                {"event": t.get("event"), "channel": t.get("channel")}, {"$set": _set})
            changed += 1
            if len(samples) < 8:
                samples.append(f"{t.get('event')}·{t.get('channel')}")
    return {"success": True, "changed": changed, "samples": samples}


@router.post("/templates/apply-sms-defaults")
async def apply_sms_defaults(current_user: dict = Depends(require_permission("settings.edit"))):
    """SMS şablonlarını GÜNCEL varsayılan metinlere ZORLA uygular (manually_edited olsa bile üzerine yazar).
    Kullanıcı isteği: sipariş SMS'leri isim/soyisim, sipariş no ve kargo LİNKİ çekmiyordu ({tracking_number}
    yerine {tracking_url}) → tüm sipariş-döngüsü SMS metinleri değişken-dolu yeni sürüme çekilir.
    Yalnız 'sms' kanalı; e-posta/whatsapp'a dokunmaz."""
    applied, items = 0, []
    for (ev, ch), body in _DEFAULT_TEMPLATES.items():
        if ch != "sms" or not body:
            continue
        await db.notification_templates.update_one(
            {"event": ev, "channel": "sms"},
            {"$set": {
                "event": ev, "channel": "sms", "body": body, "enabled": True,
                "manually_edited": False,  # varsayılana sıfırlandı
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "updated_by": current_user.get("email", ""),
            }},
            upsert=True,
        )
        applied += 1
        items.append(ev)
    return {"success": True, "applied": applied, "events": items}


@router.post("/test")
async def send_test(req: TestReq, current_user: dict = Depends(require_permission("settings.edit"))):
    res = await test_provider(db, req.channel, req.provider_key, req.to, req.message)
    return res


@router.post("/test-template")
async def send_test_template(req: TestTemplateReq, current_user: dict = Depends(require_permission("settings.edit"))):
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


@router.get("/coverage")
async def notification_coverage(days: int = Query(90, ge=1, le=365), current_user: dict = Depends(require_permission("settings.view"))):
    """Bildirim KAPSAM raporu (JSON): her bildirim tipi kodda nereden tetikleniyor (otomatik/elle/hiç),
    son N günde SMS/e-posta gerçekten gitmiş mi (notification_logs), eksikler ve mükerrerler."""
    from notification_coverage import live_counts, build_report
    live = await live_counts(db, days=days)
    return build_report(live)


@router.get("/coverage-report")
async def notification_coverage_report(days: int = Query(90, ge=1, le=365), print: int = Query(0),
                                       current_user: dict = Depends(require_permission("settings.view"))):
    """Aynı raporun YAZDIRILABİLİR (PDF) HTML sürümü — Admin → Bildirim Şablonları → Kapsam Raporu."""
    from fastapi.responses import HTMLResponse
    from notification_coverage import live_counts, build_report, render_html
    live = await live_counts(db, days=days)
    html = render_html(build_report(live), auto_print=bool(print))
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/logs")
async def list_logs(limit: int = Query(50, ge=1, le=500), current_user: dict = Depends(require_permission("settings.view"))):
    rows = (
        await db.notification_logs.find({}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(length=limit)
    )
    return {"logs": rows}
