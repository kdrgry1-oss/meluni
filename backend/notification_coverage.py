"""
Bildirim KAPSAM raporu — hangi SMS / e-posta bildirim tipi KODDA nereden tetikleniyor
(otomatik mı, elle mi, hiç mi), canlı loglarda gerçekten gitmiş mi, neler eksik/mükerrer.

Kaynak envanter (statik) bu dosyadadır; canlı sayaçlar `notification_logs`'tan (son N gün)
okunur. HTML çıktısı yazdırılabilir (PDF) — Admin → Bildirim Şablonları → "Kapsam Raporu".

BAKIM: Yeni bir send_notification(...) çağrısı eklediğinizde/ kaldırdığınızda
ilgili event'in `triggers` listesini güncelleyin.
"""
from datetime import datetime, timezone, timedelta
import html as _h

# mode: auto (sistem kendiliğinden), manual (personel bir düğmeye/duruma basınca), none
# scope: customer / admin
COVERAGE = [
    # ---------------- Sipariş yaşam döngüsü ----------------
    {"key": "order_confirmed", "name": "Sipariş Onaylandı", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → confirmed",
     "triggers": [
         "Kart ödemesi iyzico tarafından onaylanınca (payment.py → _notify_paid_order_confirmed) — otomatik",
         "Kapıda ödeme / hediye çekiyle tamamen ödenmiş siparişte oluşturma anında (orders.create_order) — otomatik",
         "Personel sipariş durumunu 'Onaylandı'ya çekince (PUT /orders/{id}/status) — elle",
     ],
     "notes": "Havale onayında bu değil 'order_payment_approved' şablonu gider (aşağıya bkz.)."},
    {"key": "order_awaiting_payment", "name": "Siparişiniz Alındı · Ödeme Bekleniyor (Havale)", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → awaiting_payment (boşsa e-posta + SMS zorla)",
     "triggers": [
         "Havale/EFT siparişi oluşturulunca banka bilgileriyle (orders.create_order) — otomatik",
         "Ödeme N saat içinde gelmezse OTOMATİK HATIRLATMA (scheduler.send_havale_payment_reminders; İşletme Kuralları → order.havale_reminder_hours, varsayılan 24 s) — otomatik (yeni)",
         "Personel durumu 'Ödeme Bekleniyor'a çekince (PUT /status) — elle",
     ]},
    {"key": "order_payment_reminder", "name": "Ödeme Hatırlatma (Havale · elle gönder)", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş detayındaki düğme (kanal seçimi düğmede)",
     "triggers": ["Sipariş detayında 'Ödeme hatırlat' düğmesi (POST /orders/{id}/send-payment-reminder) — elle"],
     "notes": "MÜKERRER RİSKİ: 'order_awaiting_payment' şablonunun SMS metni de hatırlatma dilinde yazılmış; otomatik hatırlatma o şablonu kullanır. İkisinin metni ayrışmalı (ilk mesaj = banka bilgisi, hatırlatma = 'ödemeniz ulaşmadı')."},
    {"key": "order_payment_notified", "name": "Ödeme Bildirimi Alındı", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → payment_notified",
     "triggers": ["Müşteri 'Ödeme yaptım / dekont' bildirince (POST /orders/{id}/payment-notify) — otomatik"]},
    {"key": "order_payment_approved", "name": "Ödemenizi Aldık (Havale Onayı)", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → confirmed",
     "triggers": ["Personel havale ödemesini onaylayınca (POST /orders/{id}/confirm-payment → sipariş confirmed) — personel eylemi, gönderim otomatik"],
     "notes": "'order_confirmed' ile aynı kanal ayarını paylaşır; iki ayrı 'onay' şablonu var (kart = order_confirmed, havale = order_payment_approved)."},
    {"key": "order_pending", "name": "Sipariş Alındı (Onay Bekliyor)", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → pending (varsayılan KAPALI)",
     "triggers": ["Yalnız personel durumu 'Onay Bekliyor'a çekerse (PUT /status) — elle"],
     "notes": "Siparişler artık 'pending' BAŞLAMAZ (kart+havale → awaiting_payment). Fiilen ölü şablon; kaldırılabilir."},
    {"key": "order_preparing", "name": "Sipariş Hazırlanıyor", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → preparing (varsayılan KAPALI)",
     "triggers": ["Personel durumu 'Hazırlanıyor'a çekince (PUT /status ve toplu düzenleme) — elle"]},
    {"key": "order_packed", "name": "Sipariş Paketleniyor", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → processing (varsayılan KAPALI)",
     "triggers": ["Personel durumu 'İşleme Alındı'ya çekince (PUT /status) — elle"],
     "notes": "Ad uyumsuz: şablon 'Paketleniyor', statü 'İşleme Alındı'. Biri diğerine göre yeniden adlandırılmalı."},
    {"key": "order_ready_to_ship", "name": "Kargoya Hazır", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → ready_to_ship (varsayılan KAPALI)",
     "triggers": ["Yalnız personel durum değişimi (PUT /status) — elle"],
     "notes": "Kargo taraması bu ara durumu üretmez; pratikte tetiklenmez."},
    {"key": "order_shipped", "name": "Sipariş Kargoya Verildi", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → shipped",
     "triggers": [
         "DHL/MNG kargo taraması (30 dk): takip no + ilk okutma hareketi görülünce (scheduler._dhl_cargo_poll_tick) — otomatik",
         "Personel 'Kargoya ver' (takip no girişi, POST /orders/{id}/ship) — elle",
         "Personel durumu 'Kargoya Verildi'ye çekince (PUT /status) — elle",
     ],
     "notes": "Pazaryeri (Trendyol/HB) siparişlerine site bildirimi gitmez — pazaryeri kendisi bilgilendirir (doğru davranış)."},
    {"key": "order_in_transit", "name": "Kargo Taşınıyor", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → in_transit (varsayılan KAPALI)",
     "triggers": ["Yalnız personel durum değişimi — elle"], "notes": "Kargo taraması bu durumu üretmez; pratikte tetiklenmez."},
    {"key": "order_out_for_delivery", "name": "Dağıtımda", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → out_for_delivery (varsayılan KAPALI)",
     "triggers": ["Yalnız personel durum değişimi — elle"],
     "notes": "DHL taraması 'dağıtımda' hareketini ayrıştırıp bu event'i atabilir (eklenebilir)."},
    {"key": "order_delivered", "name": "Sipariş Teslim Edildi", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → delivered",
     "triggers": [
         "DHL/MNG kargo taraması teslim bilgisi görünce (scheduler._dhl_cargo_poll_tick) — otomatik",
         "Personel durumu 'Teslim Edildi'ye çekince (PUT /status) — elle",
     ]},
    {"key": "order_undelivered", "name": "Teslim Edilemedi (Şubede Bekliyor)", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → undelivered (varsayılan SMS/e-posta KAPALI)",
     "triggers": ["Personel 'Teslim edilemedi' işaretleyince (POST /orders/{id}/undeliver) ve durum değişimi — elle"],
     "notes": "Kargo taraması 'teslim edilemedi' hareketini otomatik işlemiyor; eklenebilir."},
    {"key": "order_cancelled", "name": "Sipariş İptal Edildi", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → cancelled (manuel iptalde SMS ayardan bağımsız ZORUNLU)",
     "triggers": [
         "Havale ödemesi 72 saatte gelmeyince otomatik iptal (scheduler.auto_cancel_unpaid_havale_orders) — otomatik",
         "Personel siparişi iptal edince (PUT /status → cancelled) — elle",
     ],
     "notes": "Kart siparişinde ödeme başarısız/yarım kaldığında 3 saat sonra sipariş 'payment_failed' olur; bu durumda müşteriye HİÇ bildirim gitmez (aşağıya bkz.)."},
    {"key": "order_cancel_refunded", "name": "İptal Bedeli İade Edildi", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → cancel_refunded",
     "triggers": ["Personel durumu 'Ödemeli İptal Onaylandı'ya çekince (PUT /status) — elle"]},
    {"key": "order_payment_failed", "name": "Ödeme Alınamadı (KATALOGDA YOK)", "scope": "customer", "mode": "none",
     "channels_cfg": "Sipariş Durumları → payment_failed (varsayılan KAPALI) — şablon kataloğunda tanımlı DEĞİL",
     "triggers": ["Kart siparişi 3 saatte ödenmeyince (scheduler.auto_cancel_unpaid_card_orders) statü payment_failed olur ama bildirim şablonu olmadığı için HİÇ gönderilmez"],
     "notes": "EKSİK: 'Ödemeniz alınamadı, siparişinizi tamamlamak için tekrar deneyin' bildirimi dönüşüm kurtarır. Kataloğa eklenip cron'a bağlanmalı."},
    # ---------------- İade ----------------
    {"key": "order_return_requested", "name": "İade Talebi Oluşturuldu", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → return_requested",
     "triggers": ["Müşteri iade talebi açınca iade kodu + kargo barkoduyla (orders._notify_return) — otomatik",
                  "Personel durumu 'İade Talebi'ne çekince — elle"]},
    {"key": "order_return_approved", "name": "İade Onaylandı", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → return_approved",
     "triggers": ["Personel iadeyi onaylayınca (POST /orders/returns/{id}/approve; tutar/sebep değişkenleriyle) — personel eylemi, gönderim otomatik"]},
    {"key": "order_return_rejected", "name": "İade Reddedildi", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → return_rejected",
     "triggers": ["Personel iadeyi reddedince (POST /orders/returns/{id}/reject) — personel eylemi, gönderim otomatik"]},
    {"key": "order_return_in_transit", "name": "İade Kargoda", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → return_in_transit (varsayılan KAPALI)",
     "triggers": ["İade kargosu MNG'de ilk okutulunca (scheduler._return_cargo_poll_tick) — otomatik",
                  "Personel iade durumunu 'Kargoda'ya çekince — elle"]},
    {"key": "order_returned", "name": "İade Tamamlandı", "scope": "customer", "mode": "manual",
     "channels_cfg": "Sipariş Durumları → returned (varsayılan KAPALI)",
     "triggers": ["Personel iade durumunu 'Teslim alındı/İade tamamlandı'ya çekince — elle"],
     "notes": "İade kargosu depoya ulaşınca tarama BİLEREK göndermez (para henüz iade edilmedi). Müşteri 'İade Bedeli Ödendi' ile bilgilenir → bu şablon fiilen kullanılmıyor; 'refunded' ile birleştirilebilir."},
    {"key": "order_refunded", "name": "İade Bedeli Ödendi", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → refunded",
     "triggers": ["Personel iade bedelini ödedi işaretleyince (iade durumu refunded) — personel eylemi, gönderim otomatik"]},
    {"key": "order_partial_refunded", "name": "Kısmi İade Yapıldı", "scope": "customer", "mode": "auto",
     "channels_cfg": "Sipariş Durumları → partial_refunded",
     "triggers": ["Kısmi iade bedeli ödendi işaretlenince (iade durumu partial_refunded) — personel eylemi, gönderim otomatik"]},
    # ---------------- Üyelik / pazarlama ----------------
    {"key": "password_reset_otp", "name": "Şifre Sıfırlama Kodu", "scope": "customer", "mode": "auto",
     "channels_cfg": "Her zaman SMS (kritik event; şablon pasif olsa da gider)",
     "triggers": ["Müşteri şifremi unuttum → telefon (auth.forgot_password) — otomatik"]},
    {"key": "welcome", "name": "Hoş Geldin (Üyelik)", "scope": "customer", "mode": "auto",
     "channels_cfg": "Yalnız e-posta (kodda channels=['email'])",
     "triggers": ["Üyelik oluşturulunca hoş geldin kuponu bloğuyla (auth.register) — otomatik"],
     "notes": "SMS şablonu paneldeyse boşa: kod SMS kanalını hiç çağırmıyor. İstenirse SMS de açılabilir."},
    {"key": "abandoned_cart", "name": "Sepette Ürün Kaldı", "scope": "customer", "mode": "none",
     "channels_cfg": "Paneldeki şablon KULLANILMIYOR",
     "triggers": ["Terk edilmiş sepet maili günlük 10:00 UTC gider AMA kendi sabit HTML'i ve Resend ile (scheduler._send_abandoned_cart_reminders) — panel şablonu/aktif anahtarı devre dışı"],
     "notes": "MÜKERRER TANIM: Bildirim Şablonları'ndaki metin ve 'Aktif' kutusu bu maili etkilemez. Cron send_notification'a bağlanmalı (SMS de eklenebilir)."},
    {"key": "wishlist_back_in_stock", "name": "Favori Ürün Tekrar Stokta", "scope": "customer", "mode": "auto",
     "channels_cfg": "Yalnız e-posta",
     "triggers": ["Favorideki ürün stoğa girince (scheduler stok taraması) — otomatik",
                  "'Gelince haber ver' kayıtları için beden bazında (scheduler) — otomatik"]},
    # ---------------- Admin ----------------
    {"key": "stock_alert", "name": "Stok Uyarısı (Admin)", "scope": "admin", "mode": "none",
     "channels_cfg": "Paneldeki şablon KULLANILMIYOR",
     "triggers": ["Günlük kritik stok maili gider AMA kendi HTML'i ile (scheduler._send_daily_stock_alert) — panel şablonu devre dışı"],
     "notes": "MÜKERRER TANIM (abandoned_cart ile aynı sorun)."},
    {"key": "admin_new_order", "name": "Yeni Sipariş (Admin)", "scope": "admin", "mode": "none",
     "channels_cfg": "Paneldeki şablon KULLANILMIYOR",
     "triggers": ["Admin yeni sipariş e-postası (orders._send_admin_new_order_email) + mobil push (push.send_push_to_admins) doğrudan gider — panel şablonu devre dışı"],
     "notes": "MÜKERRER TANIM. Kart siparişinde ödeme onayında, havale/kapıda ödemede oluşturmada gider (doğru)."},
]

GAPS = [
    "Kart ödemesi başarısız/yarım kalan siparişte (payment_failed) müşteriye hiçbir bildirim yok → 'Ödemeniz alınamadı, tekrar deneyin' SMS/e-postası (dönüşüm kurtarma) eklenebilir.",
    "Sepet hatırlatma yalnız e-posta ve sabit metin; panel şablonuna bağlanıp SMS seçeneği eklenebilir.",
    "Teslimattan N gün sonra 'Ürününüzü değerlendirin / yorum yazın' bildirimi yok.",
    "DHL taramasında 'dağıtımda' ve 'teslim edilemedi (şubede)' hareketleri otomatik bildirime bağlanmıyor (yalnız kargoya verildi + teslim edildi).",
    "Havale onayında yalnız 'Ödemenizi Aldık' gider; kart siparişindeki 'Sipariş Onaylandı' ile içerik birleştirilebilir (tek şablon).",
    "WhatsApp kanalı: hiçbir şablon aktif değil ve sağlayıcı yapılandırılmamış görünüyor; kullanılmayacaksa panelden gizlenebilir.",
    "Hoş geldin bildirimi yalnız e-posta; SMS istenirse kod tarafında kanal açılmalı.",
    "Pazaryeri (Trendyol/Hepsiburada) siparişlerine site SMS'i gitmez — bilinçli tercih; raporda bilgi amaçlı.",
]

DUPLICATES = [
    "order_awaiting_payment ↔ order_payment_reminder: iki şablonun da SMS metni 'hatırlatma' dilinde; ilk mesaj banka bilgisi, hatırlatma ayrı metin olmalı.",
    "order_confirmed ↔ order_payment_approved: iki ayrı 'onaylandı' şablonu, aynı kanal ayarı (confirmed).",
    "order_returned ↔ order_refunded: 'İade Tamamlandı' fiilen gönderilmiyor (bilerek); 'İade Bedeli Ödendi' yeterli → order_returned kaldırılabilir/birleştirilebilir.",
    "order_packed ('Paketleniyor') ↔ statü 'İşleme Alındı': ad uyumsuz.",
    "abandoned_cart / stock_alert / admin_new_order: panelde şablon var ama kod kendi maillerini gönderiyor → yanıltıcı, ya bağlanmalı ya kaldırılmalı.",
    "order_pending / order_ready_to_ship / order_in_transit / order_out_for_delivery: hiçbir otomatik akış üretmiyor; yalnız elle durum değişiminde. İhtiyaç yoksa gizlenebilir.",
]

_MODE_TR = {"auto": "OTOMATİK", "manual": "ELLE", "none": "TETİKLENMİYOR"}
_MODE_CLS = {"auto": "ok", "manual": "warn", "none": "bad"}


def default_sms_texts() -> dict:
    """routes/notifications._DEFAULT_TEMPLATES → {event: sms metni} (statik kopya için)."""
    try:
        from routes.notifications import _DEFAULT_TEMPLATES
        return {k[0]: v for k, v in _DEFAULT_TEMPLATES.items() if k[1] == "sms" and v}
    except Exception:
        return {}


async def live_counts(db, days: int = 90) -> dict:
    """notification_logs → {event: {channel: {status: n}}} + şablon aktiflik {event: {channel: enabled}}"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out: dict = {}
    try:
        async for r in db.notification_logs.aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": {"e": "$event", "c": "$channel", "s": "$status"}, "n": {"$sum": 1},
                        "last": {"$max": "$created_at"}}},
        ]):
            k = r["_id"]
            ev = out.setdefault(str(k.get("e") or ""), {})
            ch = ev.setdefault(str(k.get("c") or ""), {})
            ch[str(k.get("s") or "")] = int(r.get("n") or 0)
            if (r.get("last") or "") > ch.get("_last", ""):
                ch["_last"] = r.get("last") or ""
    except Exception:
        pass
    tpl: dict = {}
    texts: dict = {}   # {event: {"sms": body, "email_subject": subject}}
    try:
        async for t in db.notification_templates.find({}, {"_id": 0, "event": 1, "channel": 1, "enabled": 1, "body": 1, "subject": 1}):
            ev, ch = str(t.get("event") or ""), str(t.get("channel") or "")
            tpl.setdefault(ev, {})[ch] = bool(t.get("enabled", True))
            if ch == "sms":
                texts.setdefault(ev, {})["sms"] = str(t.get("body") or "")
            elif ch == "email":
                texts.setdefault(ev, {})["email_subject"] = str(t.get("subject") or "")
    except Exception:
        pass
    return {"since": since, "days": days, "counts": out, "templates": tpl, "texts": texts}


def build_report(live: dict | None = None, title: str = "Bildirim Kapsam Raporu") -> dict:
    """Envanter + (varsa) canlı sayaçları birleştirir; JSON + HTML döner."""
    counts = (live or {}).get("counts") or {}
    tpls = (live or {}).get("templates") or {}
    texts = (live or {}).get("texts") or {}
    defaults = default_sms_texts()
    rows = []
    for ev in COVERAGE:
        c = counts.get(ev["key"], {})
        def _n(ch, st):
            return int((c.get(ch) or {}).get(st) or 0)
        row = dict(ev)
        row["mode_tr"] = _MODE_TR[ev["mode"]]
        row["sms"] = {"success": _n("sms", "success"), "failed": _n("sms", "failed"), "skipped": _n("sms", "skipped"),
                      "last": (c.get("sms") or {}).get("_last", ""), "enabled": (tpls.get(ev["key"]) or {}).get("sms")}
        row["email"] = {"success": _n("email", "success"), "failed": _n("email", "failed"), "skipped": _n("email", "skipped"),
                        "last": (c.get("email") or {}).get("_last", ""), "enabled": (tpls.get(ev["key"]) or {}).get("email")}
        row["fired"] = (row["sms"]["success"] + row["email"]["success"]) > 0
        _t = texts.get(ev["key"]) or {}
        row["sms_text"] = _t.get("sms") or defaults.get(ev["key"]) or ""
        row["sms_text_source"] = "panel" if _t.get("sms") else ("varsayılan" if defaults.get(ev["key"]) else "")
        row["email_subject"] = _t.get("email_subject") or ""
        rows.append(row)
    unknown = sorted(k for k in counts.keys() if k and k not in {e["key"] for e in COVERAGE})
    return {"title": title, "generated_at": datetime.now(timezone.utc).isoformat(), "live": bool(live),
            "days": (live or {}).get("days"), "rows": rows, "gaps": GAPS, "duplicates": DUPLICATES,
            "unlisted_events_in_logs": unknown}


def render_html(rep: dict, auto_print: bool = False) -> str:
    e = _h.escape
    live = rep.get("live")
    days = rep.get("days") or 90

    def cell(ch: dict, has_live: bool):
        if not has_live:
            return "<td class='c muted'>—</td>"
        parts = []
        if ch["success"]:
            parts.append(f"<b class='okc'>{ch['success']} gitti</b>")
        if ch["failed"]:
            parts.append(f"<span class='badc'>{ch['failed']} hata</span>")
        if ch["skipped"]:
            parts.append(f"<span class='muted'>{ch['skipped']} atlandı</span>")
        if not parts:
            parts.append("<span class='muted'>0</span>")
        en = ch.get("enabled")
        flag = "" if en is None else (" <span class='muted'>· şablon " + ("aktif" if en else "PASİF") + "</span>")
        last = f"<div class='muted small'>son: {e(str(ch['last'])[:16].replace('T', ' '))}</div>" if ch.get("last") else ""
        return f"<td class='c'>{' · '.join(parts)}{flag}{last}</td>"

    trs = []
    for r in rep["rows"]:
        cls = _MODE_CLS[r["mode"]]
        trig = "".join(f"<li>{e(t)}</li>" for t in r["triggers"])
        note = f"<div class='note'>{e(r['notes'])}</div>" if r.get("notes") else ""
        fired = ""
        if live:
            fired = ("<span class='pill ok'>LOGDA VAR</span>" if r["fired"] else "<span class='pill bad'>LOGDA YOK</span>")
        if r.get("sms_text"):
            sms_txt = (f"<div class='smsbox'>{e(r['sms_text'])}</div>"
                       f"<div class='muted small'>kaynak: {e(r['sms_text_source'])}"
                       + (f" · e-posta konusu: {e(r['email_subject'])}" if r.get("email_subject") else "") + "</div>")
        else:
            sms_txt = "<span class='muted'>SMS şablonu yok</span>" + (f"<div class='muted small'>e-posta konusu: {e(r['email_subject'])}</div>" if r.get("email_subject") else "")
        trs.append(
            f"<tr><td><b>{e(r['name'])}</b><div class='muted mono'>{e(r['key'])}</div>"
            f"<div class='muted small'>{e(r['channels_cfg'])}</div></td>"
            f"<td class='c'><span class='pill {cls}'>{r['mode_tr']}</span><div style='margin-top:4px'>{fired}</div></td>"
            f"<td><ul>{trig}</ul>{note}</td>"
            f"<td class='smscol'>{sms_txt}</td>"
            f"{cell(r['sms'], live)}{cell(r['email'], live)}</tr>")
    gaps = "".join(f"<li>{e(g)}</li>" for g in rep["gaps"])
    dups = "".join(f"<li>{e(d)}</li>" for d in rep["duplicates"])
    unl = rep.get("unlisted_events_in_logs") or []
    unl_html = (f"<p class='muted small'>Loglarda görülen ama envanterde olmayan event'ler: {e(', '.join(unl))}</p>" if unl else "")
    n_auto = sum(1 for r in rep["rows"] if r["mode"] == "auto")
    n_man = sum(1 for r in rep["rows"] if r["mode"] == "manual")
    n_none = sum(1 for r in rep["rows"] if r["mode"] == "none")
    live_note = (f"Canlı sayaçlar: son {days} günün bildirim logları (notification_logs)." if live
                 else "Bu kopyada canlı log sayaçları yok (statik kod envanteri). Canlı sürüm: Admin → Bildirim Şablonları → Kapsam Raporu.")
    script = ("<script>window.addEventListener('load',function(){setTimeout(function(){window.print();},400);});</script>" if auto_print else "")
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>{e(rep['title'])}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#111;margin:0;padding:24px;font-size:12px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:14px;margin:22px 0 8px;border-bottom:2px solid #111;padding-bottom:4px}}
.muted{{color:#666}} .small{{font-size:10.5px}} .mono{{font-family:ui-monospace,Menlo,monospace;font-size:10.5px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}} th,td{{border:1px solid #d9d9d9;padding:6px 7px;vertical-align:top;text-align:left}}
th{{background:#111;color:#fff;font-size:11px}} td.c{{text-align:center;white-space:nowrap}}
ul{{margin:0;padding-left:16px}} li{{margin:2px 0}}
.pill{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:700;border:1px solid}}
.pill.ok{{background:#ecfdf5;color:#047857;border-color:#a7f3d0}} .pill.warn{{background:#fffbeb;color:#b45309;border-color:#fde68a}} .pill.bad{{background:#fef2f2;color:#b91c1c;border-color:#fecaca}}
.okc{{color:#047857}} .badc{{color:#b91c1c;font-weight:700}}
.smsbox{{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;background:#f6f6f4;border:1px solid #e3e3df;padding:5px 6px;white-space:pre-wrap;word-break:break-word}}
.note{{margin-top:5px;padding:5px 7px;background:#fffbeb;border-left:3px solid #f59e0b;font-size:11px}}
.sum{{display:flex;gap:12px;margin:10px 0}} .sum div{{border:1px solid #ddd;padding:8px 12px;border-radius:6px}} .sum b{{font-size:18px;display:block}}
@media print{{ body{{padding:10mm}} h2{{page-break-after:avoid}} tr{{page-break-inside:avoid}} @page{{size:A4 landscape;margin:10mm}} }}
</style></head><body>
<h1>{e(rep['title'])}</h1>
<div class="muted">MELUNI · oluşturma: {e(rep['generated_at'][:16].replace('T',' '))} UTC · {e(live_note)}</div>
<div class="sum"><div><b>{len(rep['rows'])}</b>bildirim tipi</div><div><b class="okc">{n_auto}</b>otomatik tetiklenen</div><div><b style="color:#b45309">{n_man}</b>yalnız elle</div><div><b class="badc">{n_none}</b>hiç tetiklenmeyen / şablonu kullanılmayan</div></div>
<h2>1) Bildirim tipleri — nereden tetikleniyor, gidiyor mu?</h2>
<table><thead><tr><th style="width:17%">Bildirim</th><th style="width:8%">Tetik</th><th style="width:27%">Nerede / nasıl tetiklenir</th><th style="width:26%">SMS metni</th><th style="width:11%">SMS (log)</th><th style="width:11%">E-posta (log)</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table>
{unl_html}
<h2>2) Mükerrer / çakışan tanımlar</h2><ul>{dups}</ul>
<h2>3) Eklenebilecekler (eksikler)</h2><ul>{gaps}</ul>
<p class="muted small">Okuma: "gitti" = sağlayıcı başarı döndü; "hata" = sağlayıcı reddetti/ulaşılamadı; "atlandı" = şablon yok ya da pasif (Ayarlar → Sipariş Durumları kanal seçili ama Bildirim Şablonları'nda kapalı). Tetik sütunu KOD envanteridir; "LOGDA VAR/YOK" son {days} günde en az bir başarılı gönderim olup olmadığını gösterir.</p>
{script}
</body></html>"""
