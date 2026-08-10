# Roof — Teknik Altyapı Özeti

**Başvuran:** Roof (özel yazılım & pazaryeri entegrasyon sağlayıcısı)
**Referans platform:** Facette (canlı e-ticaret sistemi)
**Amaç:** Temu Partner/Developer API entegrasyonu

---

## 1. Kim olduğumuz

Roof, büyümekte olan e-ticaret markalarına **uçtan uca özel yazılım** ve **çok kanallı pazaryeri entegrasyonu** çözümleri geliştiren bir yazılım sağlayıcısıdır. Markaların sipariş, stok, fiyat, ürün listeleme (listing), kargo, faturalama ve finansal mutabakat süreçlerini tek bir bulut platformu üzerinden yönetmelerini sağlarız.

Şu an canlı işleyen referans platformumuz **Facette**, gerçek siparişleri ve ödemeleri işleyen bir üretim (production) sistemidir. Temu entegrasyonunu, halihazırda Trendyol ve Hepsiburada için çalışan aynı entegrasyon çekirdeğine eklemek üzere başvuruyoruz.

## 2. Genel mimari

| Katman | Teknoloji |
|---|---|
| Backend / API | Python 3.11, **FastAPI** (async), REST |
| Veritabanı | **MongoDB** (Atlas), Motor async driver |
| Hosting / Deploy | **Railway** (konteynerli, otomatik deploy), TLS 1.2+ |
| Frontend | React (yönetim paneli + mağaza), **Cloudflare** (CDN + WAF) |
| Zamanlanmış işler | Uygulama-içi scheduler (senkron, mutabakat, kurtarma cron'ları) |

Mimari **sağlayıcı-bağımsızdır (provider-agnostic)**: her pazaryeri ve dış servis, ortak bir entegrasyon çekirdeği (kimlik doğrulama → istek → yanıt normalizasyonu → loglama → retry) üzerinden çalışır. Yeni bir pazaryeri (ör. Temu) eklemek, çekirdeğe yeni bir adaptör eklemek demektir; ödeme/sipariş/güvenlik akışları değişmez.

## 3. Mevcut ve aktif entegrasyonlar

**Pazaryerleri**
- **Trendyol** — sipariş içe aktarma, stok/fiyat senkronu, iptal/iade mutabakatı (aktif)
- **Hepsiburada** — sipariş/stok/fiyat senkronu (aktif)
- **Temu** — entegrasyon adaptörü mevcut; Partner API onayı sonrası tam senkron hedeflenir
- **Amazon SP-API** — LWA/OAuth 2.0 ile bağlantı, PII'siz sipariş özeti; stok/fiyat/listing yazma ve finans/fiyat okuma uçları hazır (kademeli, feature-flag'li devreye alma)

**Ödeme & operasyon**
- **iyzico** — 3DS kart ödeme, webhook/callback ile sunucu-taraflı ödeme doğrulama (aktif, canlı)
- **MNG Kargo** — barkod/etiket üretimi, kargo durum webhook'ları (aktif)
- **NetGSM** — SMS bildirim & OTP (aktif)
- **Doğan e-Dönüşüm** — e-fatura/e-arşiv (aktif)

**Pazarlama & mesajlaşma**
- **Conversions API (server-side):** Meta, TikTok, Google Ads, Pinterest, Snapchat — tek orkestratör, olay dedup'ı ve retry ile (aktif)
- **WhatsApp Business Cloud API**, Instagram & Messenger — müşteri iletişimi (aktif)

## 4. Entegrasyon güvenilirliği (reliability)

- **Otomatik token yönetimi:** OAuth/LWA refresh → access token yenileme, önbellek ve süre kontrolü.
- **Retry + dead-letter kuyruğu:** Başarısız dış çağrılar üstel backoff ile (1/5/15/60/240 dk) tekrar denenir; maksimum denemeden sonra "dead-letter" olarak işaretlenip yöneticiye görünür kılınır — sessiz veri kaybı olmaz.
- **İdempotency & dedup:** Aynı sipariş/olay iki kez işlenmez (atomik "gönderildi" bayrağı + olay kimliği eşleştirmesi).
- **Atomik stok yönetimi:** Stok, sipariş oluşturulmadan önce koşullu-atomik olarak düşülür; karşılanamayacak sipariş hiç oluşmaz (oversell koruması). İptal/iadede stok idempotent biçimde iade edilir.
- **Merkezi denetim logu:** Dış API çağrıları tip/durum/zaman ile loglanır; hata oranı ve kuyruk durumu yönetim panelinden izlenir.

## 5. Güvenlik altyapısı

**Kimlik bilgisi & sır yönetimi**
- Tüm dış API anahtarları/refresh token'lar uygulama katmanında **AES-128-CBC + HMAC-SHA256 (Fernet)** ile şifreli bir vault'ta saklanır (kimliği doğrulanmış simetrik şifreleme).
- Ana şifreleme anahtarı (`SECRETS_MASTER_KEY`) **kaynak kod ve veritabanından ayrı**, hosting ortamının secret-store'unda (environment değişkeni) tutulur. Anahtar rotasyonu desteklidir (satır bazında yeniden şifreleme).
- Uygulama sırları repository'de **tutulmaz** (`.env` sürüm kontrolü dışında, geçmişte de yok).

**Erişim & kimlik doğrulama**
- **Rol bazlı erişim kontrolü (RBAC):** ince taneli, modül/işlem düzeyinde izinler.
- Yönetici hesaplarında **çok faktörlü doğrulama (MFA):** TOTP + SMS OTP.
- İstek **rate-limiting**, OAuth akışlarında **CSRF/state** doğrulaması.

**Veri gizliliği (PII) & uyum**
- **PII-minimizasyonu:** Sistemde yalnız iş için gereken veri tutulur. Örneğin Amazon SP-API tarafında **Restricted/PII uçları varsayılan KAPALI** (feature-flag ile); beklenmedik PII yanıtları **saklanmadan/loglanmadan otomatik temizlenir (scrub)**; istek gövdesi ve alıcı bilgisi loglanmaz.
- **KVKK/İYS uyumu:** İzin (consent) yönetimi ve ticari ileti izinlerinin İYS'ye bildirimi.
- Kişisel veri ve sır içeren yanıt gövdeleri **loglanmaz**.

**Uygulama & kod güvenliği (DevSecOps)**
- Repository **private**.
- CI güvenlik taraması: **gitleaks** (koda sızmış sır), **CodeQL** (SAST), **bağımlılık açık taraması**.
- Uçtan uca **TLS**, Cloudflare WAF + uygulama katmanı rate-limit/ban.

## 6. Temu entegrasyonu için planlanan kapsam (PII'siz ilk faz)

- Ürün **listeleme/güncelleme** (listing), **stok** ve **fiyat** senkronizasyonu.
- **Sipariş** özeti alma ve durum senkronu.
- **Finansal** mutabakat (tutar/komisyon/ücret) — kişisel veri içermeyen alanlar.
- Alıcıya ait kişisel veri (ad/adres/telefon/e-posta) **ilk fazda çekilmez/saklanmaz**; gerektiğinde Temu'nun kısıtlı-veri (restricted) onay süreci ayrıca ele alınır.

## 7. Özet

Roof'un altyapısı; çok kanallı e-ticaret entegrasyonunu **güvenli (şifreli sır vault'u, RBAC, MFA, PII-minimizasyonu, denetim logu)**, **güvenilir (idempotency, retry/dead-letter, atomik stok)** ve **ölçeklenebilir (provider-agnostic çekirdek)** biçimde yürütmek üzere tasarlanmıştır. Trendyol, Hepsiburada, Amazon SP-API, iyzico ve çok sağlayıcılı CAPI entegrasyonları üretimde çalışmaktadır; Temu'yu da aynı standartlarla entegre etmeyi hedefliyoruz.
