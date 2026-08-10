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
    {"group": "Sipariş & İptal", "key": "order.unpaid_card_cancel_hours", "label": "Ödenmemiş kart siparişi otomatik iptal süresi",
     "type": "number", "default": 3, "unit": "saat", "options": [1, 2, 3, 6, 12, 24],
     "help": "3DS başlatılıp ödemesi tamamlanmayan kart siparişleri bu süre sonra 'Ödeme Alınamadı' olur ve stok iade edilir."},
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
    {"group": "Pazarlama & İzleme", "key": "marketing.capi_consent_gate", "label": "KVKK: Pazarlama onayı olmadan Meta/CAPI'ye gönderme",
     "type": "toggle", "default": False, "options": [True, False],
     "help": "AÇIK olduğunda; ziyaretçi çerez bildiriminde 'pazarlama' onayı vermedikçe Meta Pixel "
             "ve Conversions API'ye event GÖNDERİLMEZ (KVKK/GDPR uyumu). KAPALI (varsayılan) = mevcut "
             "davranış (onaydan bağımsız gönderilir). Açmadan önce çerez bildirimi kabul oranını göz "
             "önünde bulundurun — açık onay düşükse Meta event hacmi ciddi düşebilir."},
    {"group": "Pazarlama & İzleme", "key": "marketing.capi_guest_external_id", "label": "CAPI: Misafir siparişlerde external_id = ziyaretçi kimliği (facette_sid)",
     "type": "toggle", "default": True, "options": [True, False],
     "help": "AÇIK (varsayılan): üye olmayan (misafir) siparişlerin server-side Meta/TikTok Purchase "
             "event'inde external_id, siparişe bağlı stabil first-party ziyaretçi kimliği (attribution "
             "session_id = tarayıcıdaki facette_sid) olarak gönderilir. Tarayıcı Purchase'ı da aynı değeri "
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

    # ---- Bildirim & Oturum ----
    {"group": "Ödeme & İndirim", "key": "payment.card_velocity_max_fails", "label": "Kart-testi: azami başarısız ödeme (IP)",
     "type": "number", "default": 8, "unit": "deneme", "options": [5, 8, 10, 15, 20],
     "help": "Aynı IP'den belirlenen pencerede bu kadar başarısız kart denemesi olursa ödeme geçici bloklanır (çalıntı kart doğrulama/card-testing saldırısına karşı)."},
    {"group": "Ödeme & İndirim", "key": "payment.card_velocity_window_min", "label": "Kart-testi: sayım penceresi",
     "type": "number", "default": 15, "unit": "dakika", "options": [5, 10, 15, 30, 60],
     "help": "Başarısız kart denemelerinin sayıldığı zaman penceresi. Pencere içindeki denemeler eşik ile karşılaştırılır."},
    {"group": "Panel & Oturum", "key": "panel.auto_logout_minutes", "label": "Panel otomatik çıkış (inaktivite)",
     "type": "number", "default": 60, "unit": "dakika", "options": [15, 30, 60, 120, 0],
     "help": "Panelde bu süre işlem yapılmazsa otomatik çıkış yapılır ve filtreler sıfırlanır. 0 = kapalı."},

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
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_min_hours", "label": "Terkedilmiş sepet — en erken hatırlatma",
     "type": "number", "default": 2, "unit": "saat", "options": [1, 2, 3, 6],
     "help": "Sepet terk edildikten sonra hatırlatma e-postası için beklenecek en az süre (çok erken gönderim spam etkisi yaratmasın)."},
    {"group": "Pazarlama & Stok", "key": "marketing.abandoned_cart_max_hours", "label": "Terkedilmiş sepet — en geç hatırlatma",
     "type": "number", "default": 48, "unit": "saat", "options": [24, 48, 72, 96],
     "help": "Bu süreden daha eski terk edilmiş sepetlere hatırlatma gönderilmez (çok geç kalmış sepetler atlanır)."},
    {"group": "Pazarlama & Stok", "key": "stock.low_stock_alert_threshold", "label": "Düşük stok uyarı eşiği",
     "type": "number", "default": 3, "unit": "adet", "options": [1, 2, 3, 5, 10],
     "help": "Ürün-varyant stoğu bu değere veya altına düşünce günlük düşük-stok uyarı e-postasına dahil edilir."},

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
     "help": "Açıkken müşteri kendi referans koduyla arkadaş davet eder; davet edilen ilk siparişini verince hem davet eden hem edilen indirim kuponu kazanır."},
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
     "help": "Davet edilenin ilk siparişi bu tutarın altındaysa ödül tetiklenmez (0 = tutar şartı yok)."},
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
