"""
business_rules.py — Merkezî İşletme Kuralları / Ayarlar motoru (çok-kiracılı SaaS).

AMAÇ:
  Kodda gömülü olan tüm işletme kurallarını (iptal süreleri, ücretler, eşikler,
  çalışma saatleri vb.) TEK yerden admin panelinden yönetilebilir kılmak. Böylece
  bu SaaS'ı kullanan her firma kendi kurallarını değiştirebilir.

TASARIM:
  - RULE_CATALOG: her kuralın meta bilgisi (grup, etiket, tip, varsayılan, seçenekler,
    yardım metni). Kod bu katalogdaki ANAHTAR + VARSAYILAN ile okur.
  - db.settings id="business_rules" tek dokümanda kullanıcı değerleri saklanır.
  - get_rule(key): kullanıcı değeri varsa onu, yoksa katalog varsayılanını döner (cache'li).
  - Yeni bir kuralı yönetilebilir yapmak = kataloğa bir satır ekleyip kodda
    sabit yerine `await get_rule("...")` çağırmak.

NOT: "options" alanı, admin panelinde o ayarın DEĞİŞTİRME ALTERNATİFLERİ olarak
     gösterilir (kullanıcı isteğine göre hazır seçenekler). type='number' için
     serbest giriş + öneri; type='select' için sabit seçenekler; type='toggle' için
     aç/kapa; type='time' için saat; type='multiselect' için çoklu seçim.
"""
from typing import Any, Dict
import time as _time

# =============================================================================
# KURAL KATALOĞU
# =============================================================================
# group: admin panelinde bölüm başlığı
# key: kod ve DB anahtarı
# label: kullanıcıya görünen ad
# type: number | select | toggle | time | multiselect | text
# default: kod varsayılanı (kullanıcı değeri yoksa)
# unit: gösterim birimi (saat, ₺, % ...)
# options: number → öneri değerleri; select/multiselect → seçenek listesi
# help: kısa açıklama
RULE_CATALOG = [
    # ---- Sipariş & İptal ----
    {"group": "Sipariş & İptal", "key": "order.havale_cancel_hours", "label": "Havale/EFT ödenmemiş sipariş otomatik iptal süresi",
     "type": "number", "default": 72, "unit": "saat", "options": [24, 48, 72, 96, 120],
     "help": "Havale/EFT ile ödenip bu süre içinde ödeme gelmezse sipariş otomatik iptal edilir ve stok iade edilir."},
    {"group": "Sipariş & İptal", "key": "order.havale_reminder_hours", "label": "Havale/EFT ödeme hatırlatma süresi",
     "type": "number", "default": 24, "unit": "saat", "options": [0, 6, 12, 24, 48],
     "help": "Havale/EFT siparişinde bu süre içinde ödeme/dekont gelmezse müşteriye 'Siparişiniz Alındı · Ödeme Bekleniyor' bildirimi (SMS/e-posta) OTOMATİK bir kez daha gönderilir. 0 = kapalı."},
    {"group": "Sipariş & İptal", "key": "order.max_unpaid_open_orders", "label": "Kişi başı açık ödenmemiş sipariş sınırı",
     "type": "number", "default": 3, "unit": "adet", "options": [0, 2, 3, 5, 10],
     "help": "Aynı üye/e-posta/telefon son 24 saatte bu kadar 'Ödeme Bekleniyor' siparişe ulaşınca yenisi açılmaz (stok kilitleme koruması). Aynı IP için sınır 3 katıdır. 0 = kapalı."},
    {"group": "Sipariş & İptal", "key": "order.unpaid_card_cancel_hours", "label": "Ödenmemiş kart siparişi otomatik iptal süresi",
     "type": "number", "default": 3, "unit": "saat", "options": [1, 2, 3, 6, 12, 24],
     "help": "3DS başlatılıp ödemesi tamamlanmayan kart siparişleri bu süre sonra 'Ödeme Alınamadı' olur ve stok iade edilir."},
    {"group": "Sipariş & İptal", "key": "order.unpaid_card_cancel_minutes", "label": "Ödenmemiş kart siparişi iptal süresi (dakika)",
     "type": "number", "default": 45, "unit": "dakika", "options": [15, 30, 45, 60, 120, 180],
     "help": "3DS başlatılıp tamamlanmayan kart siparişi bu süre sonunda 'Ödeme Alınamadı' olur ve STOK İADE EDİLİR. "
             "Gerçekten çekilmiş ödeme webhook (saniyeler) ve mutabakat (15 dk) ile bu süreden önce 'Ödendi' olur; "
             "geç ortaya çıkarsa stok yeniden düşülür. 0 = bu kuralı yok say (saat bazlı eski ayar kullanılır)."},
    {"group": "Sipariş & İptal", "key": "return.stale_request_days", "label": "Kargoya verilmeyen iade talebi kapanma süresi (gün)",
     "type": "number", "default": 14, "unit": "gün", "options": [7, 14, 21, 30, 45],
     "help": "Müşteri iade talebi açıp ürünü bu süre içinde KARGOYA VERMEZSE talep kapanır ve "
             "sipariş normal (teslim edilmiş) duruma döner: iade sayfasından düşer, sipariş "
             "listesinde ve ciroda görünür. Kargoya verilmiş, onaylanmış ya da parası iade "
             "edilmiş iadeler ETKİLENMEZ. 0 = bu süpürme kapalı."},
    {"group": "Sipariş & İptal", "key": "order.abandoned_card_cancel_minutes", "label": "Hiç ödeme denemesi olmayan kart siparişi iptal süresi",
     "type": "number", "default": 15, "unit": "dakika", "options": [5, 10, 15, 30, 60],
     "help": "Müşteri kart adımına hiç gelmeden (iyzico kaydı oluşmadan) yarıda bıraktığı siparişler bu süre sonunda "
             "kapatılır ve stok hemen serbest kalır. Doğrulanacak bir ödeme olmadığı için uzun beklemeye gerek yoktur."},
    {"group": "Sipariş & İptal", "key": "order.cancel_reason_required", "label": "İade/iptal talebinde sebep zorunlu",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken müşteri iade/iptal sebebini doldurmadan talebi gönderemez."},
    {"group": "Sipariş & İptal", "key": "order.block_oversell", "label": "Stok yetersizse siparişi engelle (oversell koruması)",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken sepetteki bir üründen stokta kalandan fazlası sipariş edilemez; stok yetmezse sipariş oluşturulmaz. Kapatılırsa ön-sipariş/backorder olur (stok eksiye düşebilir). Pazaryeri (Trendyol/Hepsiburada) siparişleri her hâlükârda engellenmez."},
    {"group": "Sipariş & İptal", "key": "order.max_qty_per_item", "label": "Kalem başı azami sipariş adedi",
     "type": "number", "default": 50, "unit": "adet", "options": [10, 20, 50, 100, 500],
     "help": "Tek bir üründen tek siparişte istenebilecek azami adet. Kötü niyetli dev miktarlı siparişlere karşı koruma; aşılırsa sipariş reddedilir."},
    {"group": "Sipariş & İptal", "key": "order.enforce_total_match", "label": "Onaylanan tutarla eşleşme zorunlu",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken, müşterinin kasada onayladığı tutar ile sunucunun hesapladığı tutar uyuşmazsa sipariş REDDEDİLİR (ödeme alınmaz). Müşteriden onaylamadığı bir tutarın çekilmesini engeller. Kapatmak yalnızca acil durumda önerilir: kapalıyken uyuşmazlık log'lanır ama sunucunun hesapladığı tutar çekilir."},

    # ---- Kargo & Teslimat ----
    {"group": "Kargo & Teslimat", "key": "shipping.cod_fee", "label": "Kapıda ödeme ücreti",
     "type": "number", "default": 10, "unit": "₺", "options": [0, 10, 15, 20, 25],
     "help": "Kapıda ödeme seçilince sipariş toplamına eklenen hizmet bedeli."},
    {"group": "Kargo & Teslimat", "key": "shipping.same_day_cutoff", "label": "Aynı gün kargo son saati",
     "type": "time", "default": "12:00", "options": ["09:00", "10:00", "10:30", "12:00", "14:00", "16:00"],
     "help": "Mesai gününde bu saate kadar verilen siparişler aynı gün kargolanır (ürün kartındaki geri sayım bu saate göre çalışır)."},
    # Trendyol termin takvimi (kullanıcı, 2026-09-30) — trendyol_delivery.py uygular.
    {"group": "Kargo & Teslimat", "key": "trendyol.delivery_auto", "label": "Trendyol termin takvimi (otomatik)",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken Trendyol ürünlerinin kargoya veriliş süresi aşağıdaki takvime göre otomatik güncellenir."},
    {"group": "Kargo & Teslimat", "key": "trendyol.same_day_start_sunday", "label": "Trendyol aynı gün kargo başlangıcı (Pazar)",
     "type": "time", "default": "12:00", "options": ["00:00", "09:00", "12:00", "15:00", "18:00"],
     "help": "Pazar bu saatten itibaren aynı gün kargo açılır."},
    {"group": "Kargo & Teslimat", "key": "trendyol.same_day_end_friday", "label": "Trendyol aynı gün kargo bitişi (Cuma)",
     "type": "time", "default": "15:00", "options": ["10:00", "12:00", "14:00", "15:00", "16:00", "18:00"],
     "help": "Cuma bu saatte aynı gün kargo kapanır."},
    {"group": "Kargo & Teslimat", "key": "trendyol.delivery_duration_same_day", "label": "Trendyol termin — aynı gün kargo döneminde",
     "type": "number", "default": 1, "unit": "gün", "options": [1],
     "help": "Aynı gün kargo yalnız 1 günlük terminde geçerlidir."},
    {"group": "Kargo & Teslimat", "key": "trendyol.delivery_duration_other", "label": "Trendyol termin — diğer zamanlarda",
     "type": "number", "default": 2, "unit": "gün", "options": [1, 2, 3],
     "help": "Cuma bitişinden Pazar başlangıcına kadar."},
    {"group": "Kargo & Teslimat", "key": "shipping.work_days", "label": "Çalışma (kargo) günleri",
     "type": "multiselect", "default": [1, 2, 3, 4, 5], "options": [1, 2, 3, 4, 5, 6, 7],
     "help": "Kargonun çıktığı günler (1=Pzt … 7=Paz). Resmî tatiller ayrıca hariç tutulur."},
    {"group": "Kargo & Teslimat", "key": "shipping.exclude_official_holidays", "label": "Resmî tatillerde kargo çıkma",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken resmî tatil günleri 'aynı gün/yarın kargo' hesabından hariç tutulur."},

    # ---- Ödeme & İndirim ----
    # NOT: Havale/EFT indirimi ayrı yerde (Genel Ayarlar > Ödeme) yönetilir; burada mükerrer tutulmaz.
    {"group": "Ödeme & İndirim", "key": "payment.points_redeem_max_pct", "label": "Puan ile ödenebilecek azami oran",
     "type": "number", "default": 10, "unit": "%", "options": [5, 10, 15, 20, 25],
     "help": "Müşteri sipariş tutarının en fazla bu oranını puanla ödeyebilir."},
    {"group": "Ödeme & İndirim", "key": "product.gift_wrap_price", "label": "Hediye paketi ücreti",
     "type": "number", "default": 130, "unit": "₺", "options": [0, 50, 100, 130, 150, 200],
     "help": "Hediye paketi seçilince eklenen ücret."},

    # ---- İade ----
    {"group": "İade & Değişim", "key": "return.window_days", "label": "İade/değişim süresi",
     "type": "number", "default": 14, "unit": "gün", "options": [7, 14, 15, 30],
     "help": "Teslimattan sonra müşterinin iade/değişim talebi açabileceği gün sayısı."},
    {"group": "Güvenlik", "key": "security.admin_mfa_required", "label": "Admin için MFA (2FA) zorunlu",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "AÇIK: MFA kurmamış admin girişte MFA kurulumuna (SMS veya Authenticator) yönlendirilir; "
             "kurmadan panele geçemez (Amazon DPP). Kilitlenmez — kurulum akışı zorunludur, oturum "
             "verilir. ACİL KAPATMA: Railway env ADMIN_MFA_ENFORCE=off (panel gerekmez)."},
    {"group": "Güvenlik", "key": "security.alert_emails", "label": "Güvenlik uyarısı alıcıları",
     "type": "text", "default": "",
     "help": "Virgülle ayrılmış e-posta adresleri. Boşsa aktif süper adminlere gider."},
    {"group": "Güvenlik", "key": "security.alert_from", "label": "Güvenlik uyarısı gönderen adresi",
     "type": "text", "default": "",
     "help": "Resend'de doğrulanmış alan adından bir adres, örn. info@alanadiniz.com.tr. Boşsa RESEND_FROM ortam değişkeni kullanılır."},
    {"group": "Güvenlik", "key": "security.ack_escalation_minutes", "label": "Kritik uyarı onay süresi (yükseltme)",
     "type": "number", "default": 15, "unit": "dk", "options": [5, 10, 15, 30, 60],
     "help": "Kritik güvenlik uyarısı bu süre içinde 'Gördüm' bağlantısı ya da Güvenlik Paneli → Onayla ile "
             "onaylanmazsa BİR KEZ tüm alıcılara 'YANIT YOK' önekiyle yeniden gönderilir (ve aşağıdaki numaralara "
             "kısa SMS gider). En az 5, en çok 1440 dk. Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.alert_sms_phones", "label": "Güvenlik uyarısı SMS numaraları",
     "type": "text", "default": "",
     "help": "Virgülle ayrılmış cep numaraları (en çok 3). KRİTİK güvenlik/hata uyarısında ANINDA ve 15 dk "
             "onaylanmayan uyarıda yeniden SMS gider. Boşsa aktif süper adminlerin profil telefonu kullanılır. SMS "
             "sağlayıcısı etkin değilse gönderilmez. Küresel SMS tavanına tabidir; saatte en çok 5, günde 20 anlık "
             "SMS + günde 10 yükseltme SMS'i. Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.alert_page_enabled", "label": "Kritik uyarıda dakikalık tekrar SMS",
     "type": "toggle", "default": False, "options": [True, False],
     "help": "AÇIK: kritik uyarı SMS'inden sonra yetkili yönetici panele girene kadar SMS dakikada bir tekrarlanır. "
             "KAPALI (varsayılan): yalnız ilk anlık SMS gider. Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.alert_page_stop_emails", "label": "Tekrar SMS'i durduracak yönetici e-postaları",
     "type": "text", "default": "",
     "help": "Kritik uyarı SMS'inden sonra bu e-postalardan biriyle PANELE GİRİLENE (ya da uyarı onaylanana) kadar "
             "SMS her dakika tekrarlanır. Yalnız personel (yönetici) hesabı sayılır. Boşsa herhangi bir aktif süper "
             "admin. 3 saatten sonra aralık 10 dk olur; günde en çok 300 tekrar SMS'i. Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.alert_page_interval_sec", "label": "Tekrar SMS aralığı",
     "type": "number", "default": 60, "unit": "sn", "options": [60, 120, 300, 600],
     "help": "Kritik uyarıda panele girilmediği sürece tekrar SMS'i aralığı (en az 60 sn). Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.pii_reveal_mfa_exempt", "label": "SMS/MFA doğrulamalı personele toplu görüntüleme engeli uygulama",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "AÇIK (varsayılan): SMS kodu / doğrulama uygulamasıyla giriş yapan personel, bir saatte çok sayıda IBAN/"
             "TC/telefon/adres açsa da ENGELLENMEZ; görüntülemeler denetime yazılır ve tek bilgi e-postası gider. "
             "KAPALI: eşik aşılınca 1 saat engel + kritik uyarı. Kurtarma koduyla girişte muafiyet yoktur. "
             "Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.alert_sms_instant", "label": "Kritik uyarıda anında SMS",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "AÇIK: kritik güvenlik/hata uyarısı (sızma şüphesi, toplu kişisel veri görüntüleme, vitrin "
             "kurcalama, hata patlaması, ödeme mutabakatı uyuşmazlığı…) oluştuğu anda yukarıdaki numaralara kısa "
             "SMS gider; içerikte kişisel veri yoktur. Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.monthly_drill_enabled", "label": "Aylık güvenlik alarm tatbikatı",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken her ayın ilk Pazartesi 10:07'de (İstanbul) gerçek uyarı hattından [TATBİKAT] etiketli "
             "kritik bir uyarı gider; 15 dk içinde 'Gördüm' ile onaylanırsa tatbikat GEÇTİ sayılır (SOC-01). "
             "Elle tatbikat: Güvenlik Paneli → Tatbikat başlat."},
    {"group": "Güvenlik", "key": "sms.global_hourly_cap", "label": "Küresel SMS tavanı (saatlik)",
     "type": "number", "default": 300, "unit": "SMS", "options": [100, 200, 300, 500, 1000],
     "help": "Bir saatte gönderilebilecek toplam SMS (bildirim + doğrulama kodları). Aşılırsa rutin SMS'ler "
             "durur, yöneticilere güvenlik uyarısı gider; yönetici giriş kodları küçük bir yedek payla gitmeye "
             "devam eder (SMS pompalama / maliyet koruması). Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.register_exists_daily_cap_per_net",
     "label": "Kayıt taraması: ağ başına günlük 'zaten kayıtlı' tavanı", "type": "number", "default": 10,
     "unit": "deneme", "options": [5, 10, 20, 50],
     "help": "Aynı ağdan (IP bloğu) 24 saatte bu kadar 'bu e-posta/telefon zaten kayıtlı' yanıtı alınınca o ağ, "
             "kayıtlı olup olmadığına bakılmadan 'çok fazla deneme' yanıtı alır ve yöneticilere uyarı gider "
             "(hesap listesi taramasını engeller). Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.register_attempt_daily_cap_per_net",
     "label": "Kayıt taraması: ağ başına günlük kayıt denemesi tavanı", "type": "number", "default": 40,
     "unit": "deneme", "options": [20, 40, 100, 300],
     "help": "Aynı ağdan 24 saatte yapılabilecek toplam kayıt denemesi. Ortak ağ (AVM, ofis) kullanan gerçek "
             "müşterileri etkilememesi için cömert tutulur. Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "security.register_exists_hourly_alert",
     "label": "Kayıt taraması: saatlik küresel uyarı eşiği", "type": "number", "default": 60,
     "unit": "deneme", "options": [30, 60, 120, 300],
     "help": "Tüm ağlardan bir saatte bu kadar 'zaten kayıtlı' yanıtı verilirse dağıtık tarama şüphesiyle kritik "
             "uyarı gönderilir. Yalnız süper admin değiştirebilir."},
    {"group": "KVKK / Gizlilik", "key": "privacy.order_pii_retention_days",
     "label": "Kapanmış siparişte müşteri telefonu/adresi saklama süresi", "type": "number", "default": 730,
     "unit": "gün", "options": [365, 730, 1095, 1825],
     "help": "Teslim edilmiş / iptal / iadesi sonuçlanmış siparişlerde bu süre dolunca (her gece) açık adres "
             "silinir, telefon yalnız son 2 hane kalır; il/ilçe, ad-soyad, fatura bilgileri (VKN/TCKN şifreli, "
             "VUK saklama), tutar ve fatura no korunur — e-faturanın kendisi entegratörde saklanır. Açık iade/"
             "itiraz süreci olan sipariş atlanır. Pazaryeri siparişleri dahil. En az 365 gün (daha küçük değer "
             "365 sayılır). İlk çalışma KURU yapılır ve süper adminlere rapor gider. Yalnız süper admin değiştirebilir."},
    {"group": "KVKK / Gizlilik", "key": "privacy.order_pii_minimize_enabled",
     "label": "Sipariş iletişim verisi minimizasyonu (gece işi)", "type": "toggle", "default": True,
     "options": [True, False],
     "help": "Kapatılırsa yukarıdaki saklama süresi işi çalışmaz (kayıtlar değişmez). Yalnız süper admin değiştirebilir."},
    {"group": "Güvenlik", "key": "sms.global_daily_cap", "label": "Küresel SMS tavanı (günlük)",
     "type": "number", "default": 2000, "unit": "SMS", "options": [500, 1000, 2000, 5000, 10000],
     "help": "Bir günde (UTC) gönderilebilecek toplam SMS. Aşılırsa rutin SMS'ler durur ve uyarı gider; "
             "yönetici giriş kodları küçük bir yedek payla gitmeye devam eder. Yalnız süper admin değiştirebilir."},
    {"group": "Pazarlama & İzleme", "key": "marketing.capi_consent_gate", "label": "KVKK: Pazarlama onayı olmadan Meta/CAPI'ye gönderme",
     "type": "toggle", "default": False, "options": [True, False],
     "help": "AÇIK olduğunda; ziyaretçi çerez bildiriminde 'pazarlama' onayı vermedikçe Meta Pixel "
             "ve Conversions API'ye event GÖNDERİLMEZ (KVKK/GDPR uyumu). KAPALI (varsayılan) = mevcut "
             "davranış (onaydan bağımsız gönderilir). Açmadan önce çerez bildirimi kabul oranını göz "
             "önünde bulundurun — açık onay düşükse Meta event hacmi ciddi düşebilir."},
    {"group": "Pazarlama & İzleme", "key": "marketing.capi_guest_external_id", "label": "CAPI: Misafir siparişlerde external_id = ziyaretçi kimliği (meluni_sid)",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "AÇIK (varsayılan): üye olmayan (misafir) siparişlerin server-side Meta/TikTok Purchase "
             "event'inde external_id, siparişe bağlı stabil first-party ziyaretçi kimliği (attribution "
             "session_id = tarayıcıdaki meluni_sid) olarak gönderilir. Tarayıcı Purchase'ı da aynı değeri "
             "kullandığından browser↔server eşleşir ve external_id coverage (EMQ) yükselir. Yalnız additive "
             "bir eşleşme sinyalidir; ödeme/sipariş/dedup akışını ETKİLEMEZ. KAPALI = yalnız üyelerde external_id "
             "(eski davranış) — anlık geri alma."},
    {"group": "Fatura", "key": "invoice.free_shipping_as_discount", "label": "Ücretsiz kargoyu faturada iskonto göster",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "AÇIK: 4000 TL+ (eşik) veya kupon ile kargo bedava verilen SİTE siparişlerinde faturaya "
             "(e-Arşiv + e-Fatura) 'Kargo Bedeli' + eşit 'Ücretsiz Kargo Kampanyası' iskonto satırı "
             "eklenir; net 0, toplam/matrah/KDV DEĞİŞMEZ (satır-seviyesi iskonto, GİB-güvenli). "
             "Pazaryeri siparişlerine uygulanmaz. GİB reddederse ANINDA KAPATIN (deploy gerekmez)."},
    {"group": "İade & Değişim", "key": "return.presume_delivered_after_days", "label": "Varsayılan teslim (kargo durumu gelmezse)",
     "type": "number", "default": 5, "unit": "gün", "options": [3, 4, 5, 7, 10],
     "help": "Kargo firması teslimat durumunu raporlamazsa (MNG/DHL statü takılırsa), kargoya "
             "verilme/sipariş tarihinden bu kadar gün geçmiş ÖDENMİŞ siparişler teslim edilmiş "
             "sayılır → müşteri iade başlatabilir (admin yine onaylar). 0 = kapalı."},

    # ---- Ürün & Vitrin ----
    {"group": "Ürün & Vitrin", "key": "product.max_combine", "label": "Azami 'Stilini Tamamla' ürün sayısı",
     "type": "number", "default": 12, "unit": "adet", "options": [4, 6, 8, 12, 16],
     "help": "Bir ürüne elle atanabilecek maksimum kombin ('Stilini Tamamla') ürün sayısı."},
    {"group": "Ürün & Vitrin", "key": "product.fit_recommendation_enabled", "label": "Kalıba göre beden önerisi göster",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Ürün kalıbına göre 'bir beden büyük/küçük alın' önerisini ürün kartında gösterir."},
    {"group": "Ürün & Vitrin", "key": "product.tier_campaign_badge_enabled",
     "label": "Ürün kartında sepet-tutarı kampanya ibaresi",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Aktif otomatik 'X TL üzerine Y TL indirim' kampanyalarını ürün kartında fiyatın altında "
             "kırmızı ibare olarak gösterir (ürün fiyatının üstündeki en yakın eşik)."},

    # ---- Bildirim & Oturum ----
    {"group": "Ödeme & İndirim", "key": "payment.card_velocity_max_fails", "label": "Kart-testi: azami başarısız ödeme (IP)",
     "type": "number", "default": 8, "unit": "deneme", "options": [5, 8, 10, 15, 20],
     "help": "Aynı IP'den belirlenen pencerede bu kadar başarısız kart denemesi olursa ödeme geçici bloklanır (çalıntı kart doğrulama/card-testing saldırısına karşı)."},
    {"group": "Ödeme & İndirim", "key": "payment.card_velocity_window_min", "label": "Kart-testi: sayım penceresi",
     "type": "number", "default": 15, "unit": "dakika", "options": [5, 10, 15, 30, 60],
     "help": "Başarısız kart denemelerinin sayıldığı zaman penceresi. Pencere içindeki denemeler eşik ile karşılaştırılır."},
    {"group": "Ödeme & İndirim", "key": "payment.allow_non3ds_card_pay", "label": "3D Secure'süz kart ödemesine izin ver",
     "type": "toggle", "default": False, "options": [True, False],
     "help": "KAPALI (varsayılan, önerilen): tüm kart ödemeleri 3D Secure (SMS onayı) ile alınır. AÇIK yapılırsa "
             "3D Secure'süz doğrudan kart çekimi ucu çalışır — çalıntı kartla alışveriş ve itiraz (chargeback) "
             "riski SİZE geçer, banka sorumluluğu üstlenmez. Yalnız iyzico özel olarak istediyse ve kısa süreli "
             "açın; iş bitince hemen kapatın."},
    # COM12: iyzico mutabakat defteri (security/payment_ledger.py) — firma sözleşmesine göre düzenlenir.
    {"group": "Ödeme & İndirim", "key": "payment.expected_commission_rate_max", "label": "iyzico komisyon oranı üst sınırı (mutabakat)",
     "type": "number", "default": 5, "unit": "%", "options": [0, 3, 3.5, 4, 4.5, 5, 6],
     "help": "Günlük iyzico mutabakat defterinde TEK ÇEKİM kart işleminin komisyon oranı (sabit işlem ücreti "
             "hariç) bu değeri aşarsa uyuşmazlık kaydı açılır ve uyarı gider. iyzico sözleşmenizdeki orana "
             "küçük bir pay ekleyerek girin. Taksitli işlemler (vade farkı içerdiği için) kontrol edilmez. 0 = kapalı."},
    {"group": "Ödeme & İndirim", "key": "payment.ledger_payout_grace_days", "label": "iyzico hesaba geçiş gecikme toleransı",
     "type": "number", "default": 2, "unit": "gün", "options": [0, 1, 2, 3, 5, 7],
     "help": "iyzico'nun bildirdiği beklenen hesaba geçiş (ödeme) tarihinden bu kadar gün sonra tutar hâlâ "
             "hesaba geçmiş görünmüyorsa mutabakat defterinde 'hesaba geçiş gecikti' uyuşmazlığı açılır. "
             "Yalnız iyzico hesaba geçiş bilgisini raporluyorsa çalışır. 0 = kapalı."},
    # Denetim #25: personel yapay zekâ kullanımı (security/ai_budget.py). 0 = sınırsız.
    {"group": "Yapay Zekâ", "key": "ai.user_hourly_limit", "label": "Kişi başı saatlik yapay zekâ işlemi",
     "type": "number", "default": 120, "unit": "işlem", "options": [30, 60, 120, 300, 0],
     "help": "Bir personelin bir saatte yapabileceği yapay zekâ işlemi (toplu işlerde ürün/soru başına 1)."},
    {"group": "Yapay Zekâ", "key": "ai.daily_limit", "label": "Günlük toplam yapay zekâ işlemi",
     "type": "number", "default": 2000, "unit": "işlem", "options": [500, 1000, 2000, 5000, 0],
     "help": "Tüm personelin 24 saatte yapabileceği toplam yapay zekâ işlemi (sağlayıcı faturasını sınırlar)."},
    {"group": "Yapay Zekâ", "key": "ai.user_concurrency", "label": "Kişi başı eşzamanlı yapay zekâ isteği",
     "type": "number", "default": 2, "unit": "istek", "options": [1, 2, 3, 5, 0],
     "help": "Aynı personelin aynı anda çalıştırabileceği yapay zekâ isteği sayısı."},
    {"group": "Panel & Oturum", "key": "panel.auto_logout_minutes", "label": "Panel otomatik çıkış (inaktivite)",
     "type": "number", "default": 1440, "unit": "dakika", "options": [15, 30, 60, 120, 480, 1440, 0],
     "help": "Panelde bu süre işlem yapılmazsa otomatik çıkış yapılır ve filtreler sıfırlanır. "
             "1440 = 24 saat (aynı cihazda gün boyu oturum açık kalır). 0 = kapalı."},
    {"group": "Panel & Oturum", "key": "panel.max_session_days", "label": "Panel oturumu en uzun süre",
     "type": "number", "default": 7, "unit": "gün", "options": [1, 3, 7, 14, 30],
     "help": "Panelde ETKİN çalışan personelin oturumu arka planda kendiliğinden yenilenir (çalışırken çıkış "
             "olmaz). Güvenlik için ilk girişten bu kadar gün sonra yeniden giriş istenir."},
    # ADM-15: asgari native uygulama sürümü (security/app_version_policy.py). Boş = zorlama yok.
    {"group": "Panel & Oturum", "key": "app.min_version_admin", "label": "Yönetim uygulaması asgari sürümü",
     "type": "text", "default": "", "options": [],
     "help": "Örn. 1.4.0. Bu sürümün altındaki iOS/Android YÖNETİM uygulaması panel API'sini kullanamaz; "
             "personel 'Uygulamanın güncellenmesi gerekiyor' ekranını görür. Web paneli etkilenmez. "
             "Boş = zorlama yok. Yalnız süper admin değiştirebilir."},
    {"group": "Panel & Oturum", "key": "app.min_version_customer", "label": "Müşteri uygulaması asgari sürümü",
     "type": "text", "default": "", "options": [],
     "help": "Örn. 1.2.0. Bu sürümün altındaki iOS/Android MÜŞTERİ uygulaması güncelleme ekranına düşer "
             "(mağaza bağlantısı Mobil Uygulama ayarlarından gelir). Web sitesi etkilenmez. "
             "Boş = zorlama yok. Yalnız süper admin değiştirebilir."},

    # ---- Vitrin & Ürün Sayfası (storefront görünüm anahtarları — canlı bağlı) ----
    {"group": "Vitrin & Ürün Sayfası", "key": "product.size_guide_enabled", "label": "Ürün sayfasında beden tablosu göster",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Kapalıyken ürün sayfasındaki 'Beden Tablosu' butonu ve ölçü tablosu gizlenir."},
    {"group": "Vitrin & Ürün Sayfası", "key": "product.complete_the_look_enabled", "label": "'Stilini Tamamla' önerilerini göster",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Ürün sayfasındaki kombin ('Stilini Tamamla') öneri bölümünü açar/kapatır."},
    {"group": "Vitrin & Ürün Sayfası", "key": "product.shipping_countdown_enabled", "label": "Kargo geri sayımını göster",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Ürün sayfasında 'bu saate kadar sipariş ver, yarın kargoda' geri sayımını açar/kapatır."},
    {"group": "Vitrin & Ürün Sayfası", "key": "storefront.announcement_bar_enabled", "label": "Üst duyuru şeridini göster",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Sitenin en üstündeki duyuru/kampanya şeridini açar/kapatır."},
    {"group": "Vitrin & Ürün Sayfası", "key": "product.social_share_enabled", "label": "Ürün sayfasında sosyal paylaşım butonları",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Ürün sayfasında WhatsApp/X/Facebook paylaşım ve bağlantı kopyalama butonlarını gösterir."},
    {"group": "Vitrin & Ürün Sayfası", "key": "product.low_stock_badge_enabled", "label": "'Son X ürün!' stok aciliyet rozeti",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Seçili bedenin stoğu eşiğin altına düşünce ürün sayfasında 'Son X ürün!' uyarısı gösterir."},
    {"group": "Vitrin & Ürün Sayfası", "key": "product.low_stock_badge_threshold", "label": "Stok aciliyet rozeti eşiği",
     "type": "number", "default": 5, "unit": "adet", "options": [3, 5, 8, 10],
     "help": "Seçili beden stoğu bu değere veya altına düşünce 'Son X ürün!' rozeti çıkar."},

    # ---- Destek & İletişim ----
    {"group": "Destek & İletişim", "key": "storefront.whatsapp_enabled", "label": "WhatsApp destek butonu (yüzen)",
     "type": "toggle", "default": False, "options": [True, False],
     "help": "Sitenin sağ altında yüzen WhatsApp destek butonu gösterir. Açmak için numara da girin."},
    {"group": "Destek & İletişim", "key": "storefront.whatsapp_number", "label": "WhatsApp numarası (uluslararası)",
     "type": "text", "default": "", "options": [],
     "help": "Uluslararası biçimde, başında 90 ile ve boşluksuz. Örn: 905321234567. Boşsa buton görünmez."},
    {"group": "Destek & İletişim", "key": "storefront.whatsapp_message", "label": "WhatsApp hazır mesajı",
     "type": "text", "default": "Merhaba, yardımcı olur musunuz?", "options": [],
     "help": "Müşteri butona tıklayınca WhatsApp'ta hazır gelen mesaj."},

    # ---- Pazarlama & Stok ----
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_email_enabled", "label": "Terkedilmiş sepet e-postası",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken sepete ürün ekleyip satın almayan, e-posta ticari ileti izni olan ÜYELERE \"Seçtiklerin seni bekliyor\" e-postası gider (izinsiz/üye olmayana asla gitmez)."},
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_delay_hours", "label": "Terkedilmiş sepet — e-posta gecikmesi",
     "type": "number", "default": 12, "unit": "saat", "options": [3, 6, 12, 24],
     "help": "Sepet son güncellendikten bu kadar saat sonra (bu arada sipariş verilmemişse) hatırlatma e-postası gönderilir."},
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_cooldown_days", "label": "Terkedilmiş sepet — aynı üyeye tekrar gönderim aralığı",
     "type": "number", "default": 3, "unit": "gün", "options": [1, 3, 7, 14],
     "help": "Bir üyeye bu süre içinde en fazla 1 terkedilmiş sepet e-postası gider (spam etkisi olmasın)."},
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_email_subject", "label": "Terkedilmiş sepet — e-posta konusu",
     "type": "text", "default": "Seçtiklerin seni bekliyor!", "options": [],
     "help": "Terkedilmiş sepet e-postasının konu satırı."},
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_email_title", "label": "Terkedilmiş sepet — e-posta başlığı",
     "type": "text", "default": "SEÇTİKLERİN SENİ BEKLİYOR!", "options": [],
     "help": "E-postanın içindeki büyük başlık."},
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_email_tagline", "label": "Pazarlama e-postası alt sloganı",
     "type": "text", "default": "inspired by who you are.", "options": [],
     "help": "E-posta altında marka adının altında görünen slogan. Boşsa gösterilmez."},
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_max_hours", "label": "Terkedilmiş sepet — en geç hatırlatma",
     "type": "number", "default": 48, "unit": "saat", "options": [24, 48, 72, 96],
     "help": "Bu süreden daha eski terk edilmiş sepetlere hatırlatma gönderilmez (çok geç kalmış sepetler atlanır)."},
    {"group": "Pazarlama & Stok", "key": "stock.low_stock_alert_threshold", "label": "Düşük stok uyarı eşiği",
     "type": "number", "default": 3, "unit": "adet", "options": [1, 2, 3, 5, 10],
     "help": "Ürün-varyant stoğu bu değere veya altına düşünce günlük düşük-stok uyarı e-postasına dahil edilir."},
    {"group": "Pazarlama & Stok", "key": "stock.alert_email", "label": "Stok uyarı e-postası alıcısı",
     "type": "text", "default": "", "options": [],
     "help": "Düşük stok ve stok tükenme uyarıları bu adrese gider. Boş bırakılırsa firma iletişim "
             "e-postası (İşletme/Firma Bilgileri) kullanılır; o da yoksa admin kullanıcılara gider."},
    {"group": "Pazarlama & Stok", "key": "stock.amazon_full_sync", "label": "Amazon'a tüm aktif ürünleri gönder",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken Amazon stok senkronu yalnız DEĞİŞEN varyantları değil, tüm aktif ürünleri sürekli "
             "yeniden gönderir (stok sapmasını kendi kendine onarır). Kapalıyken yalnız değişenler gider."},
    {"group": "Pazarlama & Stok", "key": "stock.amazon_sync_batch", "label": "Amazon tur başına SKU adedi",
     "type": "number", "default": 120, "unit": "SKU", "options": [40, 80, 120, 200, 400],
     "help": "Amazon senkronu her turda (1 dk) en fazla bu kadar SKU gönderir ve kaldığı yerden devam eder. "
             "Yüksek değer katalogu daha hızlı tarar ama Amazon hız sınırına takılma riskini artırır."},

    # ---- Sadakat & Referans (Bölüm C) ----
    {"group": "Sadakat & Referans", "key": "loyalty.enabled", "label": "Sadakat puanı programı",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken ödemesi onaylanan üye siparişleri puan kazanır (1 puan = 1 TL); puanlar ödemede kullanılır. Geriye dönük (özellik açılmadan önceki) siparişlere puan yazılmaz."},
    {"group": "Sadakat & Referans", "key": "loyalty.earn_rate_pct", "label": "Puan kazanma oranı (%)",
     "type": "number", "default": 2,
     "help": "Sipariş tutarının yüzde kaçı puan olarak yazılır (Silver kademe; Gold/Platinum çarpanla artar)."},
    {"group": "Sadakat & Referans", "key": "loyalty.tier_gold_threshold", "label": "Gold kademe eşiği (₺/12 ay)",
     "type": "number", "default": 10000,
     "help": "Son 12 ay harcaması bu tutarı geçen üye Gold olur (puan çarpanı uygulanır)."},
    {"group": "Sadakat & Referans", "key": "loyalty.tier_platinum_threshold", "label": "Platinum kademe eşiği (₺/12 ay)",
     "type": "number", "default": 25000,
     "help": "Son 12 ay harcaması bu tutarı geçen üye Platinum olur."},
    {"group": "Sadakat & Referans", "key": "loyalty.gold_multiplier", "label": "Gold puan çarpanı",
     "type": "number", "default": 1.5,
     "help": "Gold üyeler puanı bu çarpanla kazanır (örn. 1.5 = %50 fazla)."},
    {"group": "Sadakat & Referans", "key": "loyalty.platinum_multiplier", "label": "Platinum puan çarpanı",
     "type": "number", "default": 2,
     "help": "Platinum üyeler puanı bu çarpanla kazanır."},
    {"group": "Sadakat & Referans", "key": "giftcard.enabled", "label": "Hediye çeki / mağaza kredisi",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken ödeme sayfasında hediye çeki kodu girilebilir; bakiye kısmi kullanılabilir, kalan sonraki siparişe devreder. Çekler admin panelinden (Pazarlama > Hediye Çekleri) oluşturulur."},
    {"group": "Sadakat & Referans", "key": "referral.enabled", "label": "Referans (arkadaşını getir) programı",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken müşteri kendi referans koduyla arkadaş davet eder. Ödül kuponları, davet edilenin ilk siparişi "
             "ÖDENİP TESLİM EDİLDİKTEN ve iade süresi (İade & Değişim → İade/değişim süresi) iade/iptal olmadan "
             "dolduktan SONRA verilir; iade edilen ya da iptal olan siparişte ödül çıkmaz (sahte hesapla ödül "
             "toplamaya karşı)."},
    {"group": "Sadakat & Referans", "key": "referral.reward_type", "label": "Referans ödül tipi",
     "type": "select", "default": "fixed", "options": ["fixed", "percent"],
     "help": "Kupon tipi: fixed = sabit ₺ indirim, percent = yüzde indirim."},
    {"group": "Sadakat & Referans", "key": "referral.referrer_reward_value", "label": "Davet EDEN ödülü",
     "type": "number", "default": 100, "unit": "₺/%", "options": [50, 75, 100, 150, 10, 15],
     "help": "Davet eden kişiye verilecek kupon değeri (ödül tipine göre ₺ veya %)."},
    {"group": "Sadakat & Referans", "key": "referral.referee_reward_value", "label": "Davet EDİLEN ödülü",
     "type": "number", "default": 100, "unit": "₺/%", "options": [50, 75, 100, 150, 10, 15],
     "help": "Davet edilen (yeni müşteri) kişiye verilecek kupon değeri."},
    {"group": "Sadakat & Referans", "key": "referral.min_order_total", "label": "Referans ödülü için asgari sipariş",
     "type": "number", "default": 0, "unit": "₺", "options": [0, 250, 500, 750, 1000],
     "help": "Davet edilenin ilk siparişi bu tutarın altındaysa ödül tetiklenmez (0 = tutar şartı yok). "
             "Öneri: en az toplam ödül değerinin (davet eden + davet edilen kuponu) 3–5 katı — ör. 100 ₺ + 100 ₺ "
             "ödülde 750–1000 ₺. Böylece ucuz ürünle ödül toplama kârsız olur; 0 bırakmak önerilmez."},
    {"group": "Sadakat & Referans", "key": "reward.coupon_valid_days", "label": "Ödül kuponu geçerlilik süresi",
     "type": "number", "default": 90, "unit": "gün", "options": [30, 60, 90, 180, 365],
     "help": "Referans/doğum günü/hoş geldin ödül kuponlarının kaç gün geçerli olacağı."},
    {"group": "Sadakat & Referans", "key": "birthday.enabled", "label": "Doğum günü kuponu otomasyonu",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "Açıkken doğum tarihi kayıtlı müşterilere doğum günlerinde otomatik indirim kuponu e-postası gider."},
    {"group": "Sadakat & Referans", "key": "birthday.reward_type", "label": "Doğum günü ödül tipi",
     "type": "select", "default": "fixed", "options": ["fixed", "percent"],
     "help": "Doğum günü kuponu tipi: fixed = sabit ₺, percent = yüzde."},
    {"group": "Sadakat & Referans", "key": "birthday.reward_value", "label": "Doğum günü ödül değeri",
     "type": "number", "default": 100, "unit": "₺/%", "options": [50, 100, 150, 10, 15, 20],
     "help": "Doğum günü kuponunun değeri (tipine göre ₺ veya %)."},
    {"group": "Sadakat & Referans", "key": "welcome.enabled", "label": "Hoş geldin kuponu (kayıt sonrası)",
     "type": "toggle", "default": False, "options": [True, False],
     "help": "Açıkken yeni üye olan müşteriye kayıt sonrası bir 'hoş geldin' indirim kuponu e-postası gider."},
    {"group": "Sadakat & Referans", "key": "welcome.reward_type", "label": "Hoş geldin ödül tipi",
     "type": "select", "default": "percent", "options": ["percent", "fixed"],
     "help": "Hoş geldin kuponu tipi: percent = yüzde, fixed = sabit ₺."},
    {"group": "Sadakat & Referans", "key": "welcome.reward_value", "label": "Hoş geldin ödül değeri",
     "type": "number", "default": 10, "unit": "₺/%", "options": [5, 10, 15, 50, 100],
     "help": "Hoş geldin kuponunun değeri (tipine göre ₺ veya %)."},
    {"group": "Raporlar", "key": "report.velocity_green_min", "label": "Satış hızı: YEŞİL alt sınırı",
     "type": "number", "default": 13, "unit": "adet/hafta", "options": [3, 5, 8, 10, 13, 15, 20],
     "help": "Ürün raporunda haftalık satış hızı bu değerin ÜZERİNDE veya eşitse ürün YEŞİL (hızlı) sayılır."},
    {"group": "Raporlar", "key": "report.velocity_yellow_min", "label": "Satış hızı: SARI alt sınırı",
     "type": "number", "default": 5, "unit": "adet/hafta", "options": [1, 2, 3, 5, 8, 10],
     "help": "Haftalık hız bu değer ile yeşil sınırı arasındaysa SARI (orta); altındaysa KIRMIZI (yavaş)."},
    {"group": "Raporlar", "key": "report.reorder_cover_weeks", "label": "RPT uyarısı: kritik kapsama (hafta)",
     "type": "number", "default": 4, "unit": "hafta", "options": [3, 4, 5, 6],
     "help": "Kalan stok, haftalık satış hızı × bu hafta sayısının ALTINA inince günlük RPT (tekrar üretim) "
             "uyarısı gönderilir. Varsayılan 4 = 21 gün üretim süresi + 1 hafta güvenlik payı."},
    {"group": "Raporlar", "key": "report.kohort_bugune_kadar",
     "label": "Satış raporu neti: dönem siparişlerinin SONRADAN kesinleşen iade/iptalleri de düşülsün",
     "type": "toggle", "default": True,
     "help": "Açık: Seçili dönemin siparişlerine dönem bittikten sonra (bugüne kadar) kesilen iade ve iptaller de "
             "o dönemin netinden düşülür (Trendyol paneliyle aynı). Kapalı: yalnız seçili tarih aralığının sonuna "
             "kadar kesinleşenler düşülür. Geçmiş ayların net rakamı açıkken daha düşük görünür."},
]

_CATALOG_BY_KEY = {r["key"]: r for r in RULE_CATALOG}

# --- basit cache (30 sn) — her istekte DB'ye gitmemek için ---
_cache: Dict[str, Any] = {}
_cache_at = 0.0
_CACHE_TTL = 30.0


async def _load(db) -> Dict[str, Any]:
    global _cache, _cache_at
    now = _time.time()
    if (now - _cache_at) < _CACHE_TTL and _cache:
        return _cache
    doc = await db.settings.find_one({"id": "business_rules"}, {"_id": 0}) or {}
    _cache = doc.get("values") or {}
    _cache_at = now
    return _cache


def _default(key: str):
    r = _CATALOG_BY_KEY.get(key) or {}
    return r.get("default")


async def get_rule(db, key: str, fallback=None):
    """Kuralı DB'den (kullanıcı değeri) yoksa katalog varsayılanından döner."""
    try:
        vals = await _load(db)
    except Exception:
        vals = {}
    if key in vals and vals[key] is not None:
        return vals[key]
    d = _default(key)
    return d if d is not None else fallback


def invalidate():
    global _cache_at
    _cache_at = 0.0


async def get_all_for_admin(db):
    """Admin paneli için: katalog meta + mevcut değerler (gruplanmış)."""
    vals = await _load(db)
    groups: Dict[str, list] = {}
    for r in RULE_CATALOG:
        cur = vals.get(r["key"], r["default"])
        item = {**r, "value": cur}
        groups.setdefault(r["group"], []).append(item)
    return [{"group": g, "rules": items} for g, items in groups.items()]


async def save_rules(db, patch: dict):
    """Yalnız katalogdaki anahtarları kaydeder (bilinmeyen anahtar reddedilir)."""
    clean = {}
    for k, v in (patch or {}).items():
        if k in _CATALOG_BY_KEY:
            clean[k] = v
    if clean:
        cur = await db.settings.find_one({"id": "business_rules"}, {"_id": 0}) or {}
        vals = cur.get("values") or {}
        vals.update(clean)
        await db.settings.update_one(
            {"id": "business_rules"}, {"$set": {"values": vals}}, upsert=True
        )
        invalidate()
    return {"saved": list(clean.keys())}


# =============================================================================
# RESMÎ TATİLLER (TR) — 2026-2030 (aynı gün/yarın kargo hesabı için)
# Sabit tarihli millî bayramlar + dinî bayramlar (Ramazan/Kurban, yaklaşık resmî tarihler).
# =============================================================================
TR_OFFICIAL_HOLIDAYS = {
    # sabit millî
    "2026-01-01","2026-04-23","2026-05-01","2026-05-19","2026-07-15","2026-08-30","2026-10-29",
    "2027-01-01","2027-04-23","2027-05-01","2027-05-19","2027-07-15","2027-08-30","2027-10-29",
    "2028-01-01","2028-04-23","2028-05-01","2028-05-19","2028-07-15","2028-08-30","2028-10-29",
    "2029-01-01","2029-04-23","2029-05-01","2029-05-19","2029-07-15","2029-08-30","2029-10-29",
    "2030-01-01","2030-04-23","2030-05-01","2030-05-19","2030-07-15","2030-08-30","2030-10-29",
    # Ramazan Bayramı (arife dahil yaklaşık)
    "2026-03-20","2026-03-21","2026-03-22",
    "2027-03-10","2027-03-11","2027-03-12",
    "2028-02-26","2028-02-27","2028-02-28","2028-02-29",
    "2029-02-14","2029-02-15","2029-02-16",
    "2030-02-04","2030-02-05","2030-02-06",
    # Kurban Bayramı (yaklaşık)
    "2026-05-27","2026-05-28","2026-05-29","2026-05-30",
    "2027-05-16","2027-05-17","2027-05-18","2027-05-19",
    "2028-05-05","2028-05-06","2028-05-07","2028-05-08",
    "2029-04-24","2029-04-25","2029-04-26","2029-04-27",
    "2030-04-13","2030-04-14","2030-04-15","2030-04-16",
}
