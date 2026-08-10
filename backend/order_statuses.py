"""
order_statuses.py — Merkezi sipariş durum kataloğu + ayar/bildirim yardımcıları.

- ORDER_STATUS_CATALOG: sistemdeki TÜM olası durumlar
  (key, admin etiketi, müşteri etiketi, bildirim event'i, renk, varsayılan aktif/sms/email).
- Ayar (db.settings._id="order_status_config"):
    { active: [key,...], notify: { key: {"sms":bool,"email":bool} } }
  Admin "Sipariş Durumları" sayfasından hangi durumlar sistemde görünsün +
  hangilerinde SMS/Mail gitsin seçer.
- get_status_config(db): kayıtlı ayarı varsayılanlarla birleştirir; ilgili event'ler
  için eksik bildirim şablonlarını (idempotent) tohumlar.
"""
from datetime import datetime, timezone
import re as _re

# key | label(admin) | customer_label | event | color | group | default_active | default_sms | default_email
ORDER_STATUS_CATALOG = [
    {"key": "pending", "label": "Onay Bekliyor", "customer_label": "Siparişiniz Alındı", "event": "order_pending", "color": "#9CA3AF", "group": "Başlangıç", "default_active": True, "default_sms": False, "default_email": False},
    {"key": "awaiting_payment", "label": "Ödeme Bekleniyor (Havale/EFT)", "customer_label": "Siparişiniz Alındı · Ödeme Bekleniyor", "event": "order_awaiting_payment", "color": "#F59E0B", "group": "Başlangıç", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "payment_notified", "label": "Ödeme Bildirimi Alındı", "customer_label": "Ödeme Bildiriminiz Alındı", "event": "order_payment_notified", "color": "#FBBF24", "group": "Başlangıç", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "confirmed", "label": "Onaylandı", "customer_label": "Siparişiniz Onaylandı", "event": "order_confirmed", "color": "#10B981", "group": "Hazırlık", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "preparing", "label": "Hazırlanıyor", "customer_label": "Siparişiniz Hazırlanıyor", "event": "order_preparing", "color": "#3B82F6", "group": "Hazırlık", "default_active": False, "default_sms": False, "default_email": False},
    {"key": "processing", "label": "İşleme Alındı", "customer_label": "Siparişiniz İşleme Alındı", "event": "order_packed", "color": "#6366F1", "group": "Hazırlık", "default_active": False, "default_sms": False, "default_email": False},
    {"key": "ready_to_ship", "label": "Kargoya Hazır", "customer_label": "Siparişiniz Kargoya Hazırlanıyor", "event": "order_ready_to_ship", "color": "#0EA5E9", "group": "Hazırlık", "default_active": False, "default_sms": False, "default_email": False},
    {"key": "shipped", "label": "Kargoya Verildi", "customer_label": "Siparişiniz Kargoya Verildi", "event": "order_shipped", "color": "#8B5CF6", "group": "Kargo", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "in_transit", "label": "Taşınıyor", "customer_label": "Siparişiniz Yolda", "event": "order_in_transit", "color": "#A855F7", "group": "Kargo", "default_active": False, "default_sms": False, "default_email": False},
    {"key": "out_for_delivery", "label": "Dağıtımda", "customer_label": "Siparişiniz Dağıtımda", "event": "order_out_for_delivery", "color": "#D946EF", "group": "Kargo", "default_active": False, "default_sms": False, "default_email": False},
    {"key": "delivered", "label": "Teslim Edildi", "customer_label": "Siparişiniz Teslim Edildi", "event": "order_delivered", "color": "#059669", "group": "Kargo", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "undelivered", "label": "Teslim Edilemedi", "customer_label": "Teslimat Başarısız (Şubede)", "event": "order_undelivered", "color": "#F97316", "group": "Kargo", "default_active": True, "default_sms": False, "default_email": False},
    {"key": "return_requested", "label": "İade Talebi Oluşturuldu", "customer_label": "İade Talebiniz Oluşturuldu", "event": "order_return_requested", "color": "#F43F5E", "group": "İade", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "return_approved", "label": "İade Onaylandı", "customer_label": "İade Talebiniz Onaylandı", "event": "order_return_approved", "color": "#FB7185", "group": "İade", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "return_rejected", "label": "İade Reddedildi", "customer_label": "İade Talebiniz Değerlendirildi", "event": "order_return_rejected", "color": "#9CA3AF", "group": "İade", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "return_in_transit", "label": "İade Kargoda", "customer_label": "İadeniz Kargoda", "event": "order_return_in_transit", "color": "#EC4899", "group": "İade", "default_active": False, "default_sms": False, "default_email": False},
    {"key": "returned", "label": "İade Tamamlandı", "customer_label": "İadeniz Tamamlandı", "event": "order_returned", "color": "#BE123C", "group": "İade", "default_active": True, "default_sms": False, "default_email": False},
    {"key": "refunded", "label": "İade Bedeli Ödendi", "customer_label": "İade Bedeliniz Ödendi", "event": "order_refunded", "color": "#9F1239", "group": "İade", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "partial_refunded", "label": "Kısmi İade Yapıldı", "customer_label": "Kısmi İadeniz Yapıldı", "event": "order_partial_refunded", "color": "#DB2777", "group": "İade", "default_active": True, "default_sms": True, "default_email": True},
    {"key": "cancelled", "label": "İptal Edildi", "customer_label": "Siparişiniz İptal Edildi", "event": "order_cancelled", "color": "#6B7280", "group": "Son", "default_active": True, "default_sms": False, "default_email": False},
    {"key": "cancel_refunded", "label": "Ödemeli İptal Onaylandı", "customer_label": "İptal Bedeliniz İade Edildi", "event": "order_cancel_refunded", "color": "#7C3AED", "group": "Son", "default_active": True, "default_sms": True, "default_email": True},
    # Ödemesi HİÇ alınamamış (başarısız kart) sipariş. "İptal Edildi"den AYRI: para alınmadığı için
    # İADE GEREKMEZ. Müşteriye bildirim GİTMEZ (default pasif). Ana listede görünmez.
    {"key": "payment_failed", "label": "Ödeme Alınamadı", "customer_label": "Ödeme Alınamadı", "event": "order_payment_failed", "color": "#EF4444", "group": "Son", "default_active": False, "default_sms": False, "default_email": False},
]

_BY_KEY = {s["key"]: s for s in ORDER_STATUS_CATALOG}
CONFIG_ID = "order_status_config"
# Şablon tohumlama versiyonu — DEFAULT_STATUS_TEMPLATES'e yeni event/email şablonu
# eklendikçe artır; get_status_config bu sürümü görünce eksikleri yeniden tohumlar.
SEED_VERSION = 3


def all_status_keys():
    return [s["key"] for s in ORDER_STATUS_CATALOG]


def event_for(key):
    s = _BY_KEY.get(key)
    return s["event"] if s else None


def customer_label_for(key):
    s = _BY_KEY.get(key)
    return s["customer_label"] if s else key


def default_active_keys():
    return [s["key"] for s in ORDER_STATUS_CATALOG if s.get("default_active")]


def _default_notify():
    return {s["key"]: {"sms": bool(s.get("default_sms")), "email": bool(s.get("default_email"))}
            for s in ORDER_STATUS_CATALOG}


_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
    "ş": "s", "Ş": "s", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


def _slug_key(raw):
    s = str(raw or "").strip().lower().translate(_TR_MAP)
    return _re.sub(r"[^a-z0-9_]+", "_", s).strip("_")


def _norm_custom(raw):
    """Admin'in eklediği özel durumları doğrula/normalle. Çekirdek key'lerle çakışan
    veya geçersiz olanları eler. Liste döner."""
    out, seen = [], set()
    core = set(all_status_keys())
    for c in (raw or []):
        if not isinstance(c, dict):
            continue
        # key verilmişse onu, yoksa label'dan türet (Türkçe → ascii slug)
        key = _slug_key(c.get("key") or c.get("label") or "")
        if not key or key in core or key in seen:
            continue
        seen.add(key)
        label = (str(c.get("label") or key).strip())[:80]
        clabel = (str(c.get("customer_label") or label).strip())[:120]
        event = (str(c.get("event") or "").strip()) or None
        color = (str(c.get("color") or "#6B7280").strip())[:9]
        group = (str(c.get("group") or "Özel").strip())[:40]
        out.append({"key": key, "label": label, "customer_label": clabel,
                    "event": event, "color": color, "group": group, "is_custom": True})
    return out


def merge_config(saved):
    """Kayıtlı ayarı varsayılanlarla birleştirir.
    Dönen: { active:[key], notify:{key:{sms,email}}, custom:[...], labels:{key:{label,customer_label}} }
    custom = admin'in eklediği özel durumlar; labels = çekirdek/özel durum etiket override'ları.
    """
    saved = saved or {}
    custom = _norm_custom(saved.get("custom"))
    valid = set(all_status_keys()) | {c["key"] for c in custom}
    active = saved.get("active")
    if not isinstance(active, list):
        active = default_active_keys()
    active = [k for k in active if k in valid]
    if not active:
        active = default_active_keys()
    notify = _default_notify()
    for c in custom:
        notify.setdefault(c["key"], {"sms": False, "email": False})
    for k, v in (saved.get("notify") or {}).items():
        if k in valid and isinstance(v, dict):
            notify[k] = {"sms": bool(v.get("sms")), "email": bool(v.get("email"))}
    labels = {}
    for k, v in (saved.get("labels") or {}).items():
        if k in valid and isinstance(v, dict):
            lv = {}
            if v.get("label"):
                lv["label"] = (str(v["label"]).strip())[:80]
            if v.get("customer_label"):
                lv["customer_label"] = (str(v["customer_label"]).strip())[:120]
            if lv:
                labels[k] = lv
    return {"active": active, "notify": notify, "custom": custom, "labels": labels}


def effective_statuses(cfg):
    """Çekirdek + özel durumları, etiket override'ları uygulanmış halde döner (tek kaynak)."""
    cfg = cfg or {}
    labels = cfg.get("labels") or {}
    out = []
    for s in ORDER_STATUS_CATALOG:
        lo = labels.get(s["key"]) or {}
        out.append({
            "key": s["key"], "label": lo.get("label", s["label"]),
            "customer_label": lo.get("customer_label", s["customer_label"]),
            "event": s["event"], "color": s["color"], "group": s["group"], "is_custom": False,
        })
    for c in (cfg.get("custom") or []):
        lo = labels.get(c["key"]) or {}
        out.append({
            "key": c["key"], "label": lo.get("label", c["label"]),
            "customer_label": lo.get("customer_label", c["customer_label"]),
            "event": c.get("event"), "color": c.get("color", "#6B7280"),
            "group": c.get("group", "Özel"), "is_custom": True,
        })
    return out


def valid_keys(cfg):
    """get_status_config çıktısı için geçerli (çekirdek + özel) durum key'leri kümesi."""
    return {s["key"] for s in effective_statuses(cfg)}


def event_for_cfg(key, cfg):
    """Durum → bildirim event'i (çekirdek katalog VEYA özel durumun seçili event'i)."""
    s = _BY_KEY.get(key)
    if s:
        return s["event"]
    for c in ((cfg or {}).get("custom") or []):
        if c["key"] == key:
            return c.get("event")
    return None


def customer_label_for_cfg(key, cfg):
    """Müşteri etiketi — override / özel durum farkındalıklı."""
    lo = ((cfg or {}).get("labels") or {}).get(key) or {}
    if lo.get("customer_label"):
        return lo["customer_label"]
    s = _BY_KEY.get(key)
    if s:
        return s["customer_label"]
    for c in ((cfg or {}).get("custom") or []):
        if c["key"] == key:
            return c.get("customer_label") or key
    return key


# ---- Varsayılan bildirim şablonları (eksikse tohumlanır; admin düzenleyebilir) ----
DEFAULT_STATUS_TEMPLATES = {
    "order_awaiting_payment": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz alindi. Tutar: {amount}. Havale/EFT icin {bank_name} IBAN {bank_iban} ({bank_account_holder}). Odeme bildirimi: {payment_url}",
        "email_subject": "Siparişiniz Alındı · Ödeme Bekleniyor — {order_number}",
        "email_body": (
            "<p>Merhaba {customer_name},</p>"
            "<p><b>{order_number}</b> numaralı siparişiniz alındı. Siparişiniz onay için ödeme beklemektedir.</p>"
            "<p><b>Tutar:</b> {amount}</p>"
            "<div style='border:1px solid #eee;border-radius:8px;padding:14px;margin:14px 0;'>"
            "<div style='font-weight:700;margin-bottom:8px;'>Havale / EFT Bilgileri</div>"
            "<div>Banka: {bank_name}</div><div>Şube: {bank_branch}</div>"
            "<div>IBAN: <b>{bank_iban}</b></div><div>Hesap Sahibi: {bank_account_holder}</div></div>"
            "<p>Ödemenizi yaptıktan sonra hızlı işleme alabilmemiz için lütfen dekontunuzu iletin:</p>"
            "<p><a href='{payment_url}' style='background:#111;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;'>Ödeme Bildirimi Yap</a></p>"
        ),
    },
    "order_payment_notified": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz icin odeme bildiriminiz alindi, kontrol ediliyor.",
        "email_subject": "Ödeme Bildiriminiz Alındı — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz için ödeme bildiriminiz alındı ve kontrol ediliyor. Onay sonrası bilgilendirileceksiniz.</p>",
    },
    "order_return_requested": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz icin iade talebiniz olusturuldu. Iade kargo kodu: {return_code} (3 gun gecerli).",
        "email_subject": "İade Talebiniz Oluşturuldu — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz için iade talebiniz oluşturuldu.</p><p>İade kargo kodunuz: <b>{return_code}</b> (3 gün geçerli).</p>",
    },
    "order_preparing": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz hazirlaniyor.",
        "email_subject": "Siparişiniz Hazırlanıyor — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz hazırlanıyor. Kargoya verildiğinde ayrıca bilgilendirileceksiniz.</p>",
    },
    "order_ready_to_ship": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz kargoya hazirlaniyor.",
        "email_subject": "Siparişiniz Kargoya Hazırlanıyor — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz kargoya teslim için hazırlanıyor.</p>",
    },
    "order_in_transit": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz yolda. Takip: {tracking_url}",
        "email_subject": "Siparişiniz Yolda — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz yola çıktı.</p><p>Takip linki: <a href='{tracking_url}'>{tracking_url}</a></p>",
    },
    "order_out_for_delivery": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz dagitimda.",
        "email_subject": "Siparişiniz Dağıtımda — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz bugün dağıtıma çıktı, kısa süre içinde teslim edilecektir.</p>",
    },
    "order_return_in_transit": {
        "sms": "Sayin {customer_name}, {order_number} numarali iadeniz kargoda.",
        "email_subject": "İadeniz Kargoda — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişinize ait iadeniz kargoya verildi, tarafımıza ulaştığında bilgilendirileceksiniz.</p>",
    },
    "order_returned": {
        "sms": "Sayin {customer_name}, {order_number} numarali iadeniz tamamlandi.",
        "email_subject": "İadeniz Tamamlandı — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişinize ait iade işleminiz tamamlandı. Varsa iade bedeliniz ayrıca işleme alınacaktır.</p>",
    },
    "order_cancel_refunded": {
        "sms": "Sayin {customer_name}, iptal edilen {order_number} numarali siparisinizin odemesi iade edilmistir. Tutar bankaniza bagli 1-7 is gununde hesabiniza yansir.",
        "email_subject": "İptal Bedeliniz İade Edildi — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p>İptal edilen <b>{order_number}</b> numaralı siparişinizin ödemesi tarafınıza iade edilmiştir.</p><p>İade tutarının hesabınıza/kartınıza yansıması bankanıza bağlı olarak 1-7 iş günü sürebilir.</p><p>FACETTE'i tercih ettiğiniz için teşekkür ederiz.</p>",
    },
    "order_refunded": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisinizin iade bedeli odendi.",
        "email_subject": "İade Bedeliniz Ödendi — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişinizin iade bedeli hesabınıza/kartınıza iade edilmiştir.</p><p>İade tutarının hesabınıza yansıması bankanıza bağlı olarak 1-7 iş günü sürebilir.</p><p>FACETTE'i tercih ettiğiniz için teşekkür ederiz.</p>",
    },
    "order_partial_refunded": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz icin kismi iade yapildi.",
        "email_subject": "Kısmi İadeniz Yapıldı — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz için kısmi iade işlemi gerçekleştirilmiştir.</p><p>İade edilen tutarın hesabınıza/kartınıza yansıması bankanıza bağlı olarak 1-7 iş günü sürebilir.</p><p>Detaylar için hesabım > siparişlerim sayfasını ziyaret edebilirsiniz.</p>",
    },
    "order_confirmed": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz onaylandi ve hazirlaniyor.",
        "email_subject": "Siparişiniz Onaylandı — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz onaylandı ve hazırlanmaya başlandı.</p><p>Kargoya verildiğinde ayrıca bilgilendirileceksiniz.</p>",
    },
    "order_packed": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz paketlendi.",
        "email_subject": "Siparişiniz Paketlendi — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz paketlendi, kargoya teslim için hazır.</p>",
    },
    "order_shipped": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz kargoya verildi. Takip: {tracking_url}",
        "email_subject": "Siparişiniz Kargoya Verildi — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz kargoya verildi.</p><p>Takip linki: <a href='{tracking_url}'>{tracking_url}</a></p>",
    },
    "order_delivered": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz teslim edildi. Bizi tercih ettiginiz icin tesekkurler.",
        "email_subject": "Siparişiniz Teslim Edildi — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz teslim edildi. FACETTE'i tercih ettiğiniz için teşekkür ederiz.</p>",
    },
    "order_undelivered": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz teslim edilemedi. En kisa surede sizinle iletisime gececegiz.",
        "email_subject": "Siparişiniz Teslim Edilemedi — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz teslim edilemedi. Ekibimiz en kısa sürede sizinle iletişime geçecektir.</p>",
    },
    "order_return_approved": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz icin iade talebiniz onaylandi.",
        "email_subject": "İade Talebiniz Onaylandı — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz için iade talebiniz onaylandı.</p>",
    },
    "order_return_rejected": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz icin iade talebiniz degerlendirildi. Detay icin hesabinizi kontrol edin.",
        "email_subject": "İade Talebiniz Değerlendirildi — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz için iade talebiniz değerlendirildi. Detaylar için hesabım > siparişlerim sayfasını ziyaret edebilirsiniz.</p>",
    },
    "order_cancelled": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz iptal edildi.",
        "email_subject": "Siparişiniz İptal Edildi — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz iptal edildi. Ödeme alınmışsa iade süreci başlatılacaktır.</p>",
    },
    "order_pending": {
        "sms": "Sayin {customer_name}, {order_number} numarali siparisiniz alindi.",
        "email_subject": "Siparişiniz Alındı — {order_number}",
        "email_body": "<p>Merhaba {customer_name},</p><p><b>{order_number}</b> numaralı siparişiniz alındı. İşleme alındığında bilgilendirileceksiniz.</p>",
    },
}


async def ensure_status_templates(db):
    """Katalog event'leri için eksik bildirim şablonlarını (idempotent) ekler."""
    try:
        for ev, tpl in DEFAULT_STATUS_TEMPLATES.items():
            if tpl.get("sms"):
                ex = await db.notification_templates.find_one({"event": ev, "channel": "sms"}, {"_id": 0})
                if not ex:
                    await db.notification_templates.insert_one({
                        "event": ev, "channel": "sms", "enabled": True,
                        "subject": "", "body": tpl["sms"],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
            if tpl.get("email_body"):
                ex = await db.notification_templates.find_one({"event": ev, "channel": "email"}, {"_id": 0})
                if not ex:
                    await db.notification_templates.insert_one({
                        "event": ev, "channel": "email", "enabled": True,
                        "subject": tpl.get("email_subject", ""), "body": tpl["email_body"],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
    except Exception:
        pass


async def get_status_config(db):
    saved = await db.settings.find_one({"id": CONFIG_ID}, {"_id": 0})
    cfg = merge_config(saved)
    # Şablon tohumlama VERSİYON bazlı: yeni event/email şablonu eklediğimizde
    # SEED_VERSION'ı artırırız; mevcut (canlı) DB'lerde de eksik şablonlar
    # idempotent olarak tamamlanır. ensure_status_templates var olanı bozmaz,
    # sadece eksik (event,channel) kombinasyonlarını ekler.
    if not saved or saved.get("templates_seed_version") != SEED_VERSION:
        await ensure_status_templates(db)
        await db.settings.update_one(
            {"id": CONFIG_ID},
            {"$set": {"id": CONFIG_ID, "templates_seeded": True,
                      "templates_seed_version": SEED_VERSION}},
            upsert=True,
        )
    return cfg
