# FACETTE E-Commerce & ERP Platform — Build Specification

> **Hedef:** Bu doküman, sistemin sıfırdan inşa edilmesi için tüm fonksiyonel, teknik ve operasyonel gereksinimleri içerir. Tek başına bir yazılımcı/ekibe verildiğinde, başka bir kaynağa ihtiyaç duymadan ürünü teslim edebilmelidir.
>
> **Tahmini efor:** 1 senior full-stack geliştirici için ~14-16 hafta · 2 kişilik ekip için ~8-10 hafta.
> **Stack zorunlu:** React 19 + FastAPI + MongoDB (alternatif önerileri ilgili bölümde).
> **Bütçe varsayımları:** Pazaryeri/ödeme/SOAP API anahtarları müşteri tarafından sağlanır.

---

## İçindekiler

- [0. Proje Briefi](#0-proje-briefi)
- [1. İş Hedefleri & KPI'lar](#1-iş-hedefleri--kpilar)
- [2. Kullanıcı Personaları](#2-kullanıcı-personaları)
- [3. Fonksiyonel Gereksinimler (Modüller)](#3-fonksiyonel-gereksinimler-modüller)
- [4. Non-Functional Gereksinimler](#4-non-functional-gereksinimler)
- [5. Teknoloji Seçimi](#5-teknoloji-seçimi)
- [6. Sistem Mimarisi](#6-sistem-mimarisi)
- [7. Veri Modeli (DB Şeması)](#7-veri-modeli-db-şeması)
- [8. API Sözleşmeleri](#8-api-sözleşmeleri)
- [9. 3. Parti Entegrasyon Spesifikasyonları](#9-3-parti-entegrasyon-spesifikasyonları)
- [10. UI/UX Gereksinimleri](#10-uiux-gereksinimleri)
- [11. Geliştirme Yol Haritası (16 hafta)](#11-geliştirme-yol-haritası-16-hafta)
- [12. Kabul Kriterleri (Definition of Done)](#12-kabul-kriterleri-definition-of-done)
- [13. Teslim Çıktıları](#13-teslim-çıktıları)
- [14. Risk & Bağımlılıklar](#14-risk--bağımlılıklar)
- [EK A — Test Senaryoları](#ek-a--test-senaryoları)
- [EK B — Örnek Kullanıcı Akışları](#ek-b--örnek-kullanıcı-akışları)

---

## 0. Proje Briefi

### 0.1 Tek Cümlede Ne İnşa Ediyoruz?

> "Facette" üst-segment kadın giyim markası için, Ticimax altyapısını tamamlayan / dönüştüren; **müşteri storefront'u + admin/ERP paneli + pazaryeri (Trendyol/Hepsiburada) entegrasyonu**'nu tek çatı altında toplayan, üretici (B2B) ve son müşteri (B2C) hesaplarını yöneten bir e-ticaret platformu inşa edilecek.

### 0.2 Neden Bu Sistem?

- Mevcut **Ticimax** çözümü ürün yönetiminde yeterli ama:
  - Trendyol push'unda **barkod cache sıkışması** ve **kategori attribute eşleştirme** sorunları yaşıyor.
  - Premium marka deneyimi için yeterli **özelleştirme** sunmuyor.
  - Birden fazla pazaryeri & kâr oranı yönetimi karmaşık.
- Yeni sistem bu üç problemi çözecek: **güvenilir push + premium UI + tek panel ERP**.

### 0.3 Domain ve Marka

- Marka: **FACETTE** (kadın giyim — bluz, etek, takım, ceket, pantolon, elbise).
- Domain: `facette.com.tr`.
- Yıllık ürün sayısı: ~500 SKU (parent), 1500-2000 varyant (renk × beden).
- Aylık sipariş hacmi (hedef): 2.000-5.000.
- Aktif müşteri: 10.000+ (mevcut Ticimax veritabanından göç edilecek).

---

## 1. İş Hedefleri & KPI'lar

| Hedef | KPI | Mevcut | 6 Ay Sonra Hedef |
|---|---|---|---|
| Trendyol push başarı oranı | % | ~65% (cache sorunları) | **≥ 98%** |
| Sipariş işleme süresi | dk | ~15dk (manuel) | **< 3dk (otomatik)** |
| Site dönüşüm oranı | % | %1.2 (Ticimax tema) | **≥ %2.5** (premium tema) |
| Müşteri başına ortalama sipariş | TL | ~1.800 TL | **≥ 2.400 TL** |
| Stok-fiyat senkron gecikme | dk | ~60dk (Excel) | **< 5dk (canlı)** |
| Admin operasyon yetkinliği | RBAC | yok | **5 rol + audit log** |

---

## 2. Kullanıcı Personaları

### 2.1 Müşteri (Anonim/Üye Ziyaretçi) — "Ayşe, 28"

- Mobil ağırlıklı (~70% mobil), Instagram'dan gelir.
- Hızlı ürün filtreleme, renk varyantları, ücretsiz iade.
- Sepete ekle → 3DS ödeme → 3 dk içinde bitsin.
- Sipariş durumu push notification ister.

### 2.2 Admin / Ürün Yöneticisi — "Murat"

- Günde 20+ ürün düzenleyebilir.
- Trendyol'a tek tık aktarım, hata olursa neden olduğunu bilmek ister.
- Excel ile toplu import yapmak ister.
- Stok hareketleri ve maliyeti takip eder.

### 2.3 Operasyon / Müşteri Hizmetleri — "Selin"

- Siparişe iade, ürün soru/cevap, kargo takip.
- Müşteri segmentleri, kupon yönetimi.
- IYS izinli üyelere kampanya gönderme.

### 2.4 Yönetici / Sahip — "İsmail Bey"

- Günlük cirosu, kâr oranı, en çok satan ürün dashboard'u.
- Pazaryeri kârlılığı karşılaştırma raporu.
- RBAC + güvenlik audit log'u.

### 2.5 Geliştirici / Sistem Yöneticisi

- Sağlık check endpoint'leri.
- Audit log + error log.
- Vault'lu key yönetimi.

---

## 3. Fonksiyonel Gereksinimler (Modüller)

> **Notasyon:** [P1] = MVP zorunlu · [P2] = 2. sürüm · [P3] = future.
> **AC:** Acceptance Criteria.

### 3.1 [P1] Müşteri Storefront

| Özellik | Açıklama | AC |
|---|---|---|
| Anasayfa | Banner slider, kategori grid, kampanya blokları, en çok satanlar. | Mobile-first; LCP < 2.5s; CMS'den editable. |
| Kategori (PLP) | Filtre (kategori, renk, beden, fiyat aralığı, indirim), sıralama (popüler, fiyat, yeni), sayfalama. | Filtreler URL'e yansır (deep link); ürün kartı hover'da 2. görsel. |
| Ürün Detay (PDP) | Görsel galeri (zoom, swipe), renk/beden seçimi, sepete ekle, ölçü tablosu, açıklama, ilgili ürünler. | Renk değişince URL değişir; stoğu olmayan beden disable. |
| Sepet | Sepetteki ürünler, miktar değiştirme, kupon kodu, kargo hesabı. | LocalStorage + (üye ise) server-side persist. |
| Üye Girişi / Kayıt | Email + parola, social login opsiyonel, OTP'li şifre sıfırlama. | Brute-force koruması (5 deneme/15dk). |
| Ödeme (Checkout) | Adres seçimi/ekleme, kargo seçimi, ödeme yöntemi (kredi kartı 3DS, havale), KVKK onayı. | 3DS doğrulamasından sonra sipariş oluşur; havale 48 saatte otomatik iptal. |
| Sipariş Takip | Üyesiz sipariş kodu + email ile sorgulama. | Kargo takip linki tıklanabilir. |
| Hesabım | Siparişlerim, adreslerim, favoriler, iade talepleri. | İade formu içinde sebep + görsel yüklenebilir. |
| Statik Sayfalar | Hakkımızda, KVKK, Mesafeli Satış, İade Politikası. | CMS'den editable. |
| Site Arama | Ürün adı, stok kodu, barkod ile. | < 300ms response. |

### 3.2 [P1] Admin Panel — Ürün Yönetimi

| Özellik | Açıklama |
|---|---|
| Ürün listesi | 50/100/200 satır sayfalama, çoklu filtre (kategori, marka, aktif, kategori, fiyat), arama (ad/SKU/barkod). |
| Ürün ekleme/düzenleme | Modal — Tabs: Temel, Fiyat, Görseller, Stok, Varyantlar, SEO, Özellikler (attributes), Ölçü Tablosu, Kombin, Trendyol Ayarları. |
| **Açıklama editörü** | "Kaynak / Önizleme / Bölünmüş" toggle; HTML editör. Trendyol'a giderken HTML temizlenir. |
| Varyantlar | size, color, barcode, stock_code, stock, price, sale_price — satır ekle/sil. |
| Görseller | Drag-drop sıralama, ana görsel seçimi, max 10 görsel. |
| SKU üretici | "FCFW######" / "FCSS######" otomatik üretim butonu. |
| Toplu işlemler | Toplu aktif/pasif yapma, çoklu silme, çoklu Trendyol'a aktarım. |
| Ürün kopyalama | Mevcut ürünü yeni isimle klonla. |
| Barkod sorunları | Eksik/duplike barkod tarayıcı + otomatik düzeltme. |

### 3.3 [P1] Admin Panel — Sipariş Yönetimi

| Özellik | Açıklama |
|---|---|
| Sipariş listesi | Filtre: tarih, durum (yeni, hazırlanıyor, kargoda, teslim, iptal), pazaryeri kaynağı, ödeme. |
| Sipariş detayı | Müşteri, adres, kalemler, ödeme, kargo takip, iade talepleri. |
| Durum güncelleme | Tek tık ile durum değiştir (otomatik müşteri bildirimi). |
| Kargo etiketi | MNG/PTT/Yurtiçi tek tık PDF çıktısı + tracking numarası. |
| İade yönetimi | Müşteri talebi → admin onay/red → ücret iade tetikleyici. |
| Ticimax sipariş import | Telefonlu + marketplace hariç site siparişlerini çek. |
| Trendyol sipariş import | Trendyol siparişlerini ön-izle → seçili olanları DB'ye al. |

### 3.4 [P1] Admin Panel — Pazaryeri Entegrasyonu

| Özellik | Açıklama |
|---|---|
| Trendyol push | Tek tık veya toplu push; 4 kademeli akıllı fallback (bkz §9.4). |
| Batch durumu | `batch_id` ile durum sorgulama; başarısız sebepleriyle listelenir. |
| Kategori eşleştirme | Lokal kategori ↔ Trendyol/HB kategori + attribute eşleşmesi. |
| Marka eşleştirme | Lokal marka ↔ Trendyol brand_id. |
| Ghost scanner | Trendyol cache'inde sıkışmış ürünleri tarama + arşivleme. |
| Stuck queue | Cache'de sıkışan ürünler için arkaplan retry kuyruğu (her 60dk). |
| Soru-cevap | Trendyol müşteri soruları + admin yanıt. |
| İade (claim) | Trendyol iade taleplerinin yönetimi. |

### 3.5 [P1] Admin Panel — Ticimax Entegrasyonu

| Özellik | Açıklama |
|---|---|
| Excel toplu import | Ticimax export Excel'i drag-drop ile yükleme; preview + merge. |
| ID ile tek ürün çekme | URUNKARTIID girip storefront/SOAP'tan tek ürün getirme. |
| Stok senkron | Ticimax → DB tek yönlü stok senkronu. |
| Sipariş senkron | Ticimax site siparişlerini periyodik çekme (marketplace hariç). |
| Üye import | Ticimax üyelerini one-time DB'ye taşıma. |

### 3.6 [P1] Admin Panel — Müşteri / Üye Yönetimi

| Özellik | Açıklama |
|---|---|
| Üye listesi | Filtre: aktif/pasif, segment, son giriş, AlışverişYaptı. |
| Üye detayı | Sipariş geçmişi, adresler, IYS izin, blokaj. |
| Müşteri segmentasyonu | Otomatik segment: VIP (>10K TL), Sleeping (90 gün), Yeni (<30 gün). |
| IYS senkron | SMS/Email izinli üyeleri İYS'ye gönder. |
| Bloklu müşteriler | Sahte sipariş veren / iade istismarcı blok. |
| Risk skoru | Sipariş başarısızlık oranı → risk puanı. |

### 3.7 [P1] Admin Panel — Ayarlar & RBAC

| Özellik | Açıklama |
|---|---|
| Genel ayarlar | Site adı, currency, vergi oranı, kâr oranı (`main.trendyol_markup`), site logo. |
| Kullanıcı & Rol | 5 rol: admin, manager, support, viewer, vendor. Endpoint bazlı yetki. |
| Secrets Vault | Fernet ile şifrelenmiş API key store; admin UI üzerinden ekle/güncelle. |
| Sistem sağlığı | Backend/Mongo/3rd party bağlantı sağlık check; error_log özeti. |
| Audit log | Kim, ne zaman, ne yaptı (login, ürün düzenle, ayar değiştir). |
| Pixel ayarları | GA4, Meta Pixel, TikTok Pixel, Google Ads tag injection. |
| SEO meta | Site geneli + sayfa bazlı meta title/desc/keywords. |

### 3.8 [P2] Admin Panel — Raporlar & Analitik

| Rapor | Detay |
|---|---|
| Satış raporu | Gün/hafta/ay; pazaryeri kırılımı; ödeme yöntemi kırılımı. |
| Ürün raporu | En çok satan, stoksuz, iade oranı yüksek. |
| Stok raporu | Anlık stok, hareketler, kritik altı. |
| Üye raporu | Yeni üye, segment dağılımı, IYS izin durumu. |
| Marketplace kârlılık | Trendyol komisyon - kargo - iade → net kâr. |
| Atıf raporu | UTM kaynağı → ciro. |

### 3.9 [P2] Admin Panel — Pazarlama

| Özellik | Açıklama |
|---|---|
| Kupon yönetimi | Tek kullanımlık / çok kullanımlık, %-/TL- indirim, kategori/ürün kısıtı. |
| Kampanya | Sepet bazlı (3 al 2 öde), kategori bazlı (% indirim). |
| Email kampanya | Resend ile transactional + segmentli kampanya. |
| Push notification | Capacitor + FCM ile kampanya bildirimi. |
| Terkedilmiş sepet | 24 saat sonra otomatik hatırlatma maili. |
| Banner | Anasayfa banner editor (CMS). |

### 3.10 [P2] Admin Panel — Üretim & Maliyet (B2B)

| Özellik | Açıklama |
|---|---|
| Üretim planı | Hangi ürün ne kadar üretilecek, ne zaman; tedarikçi atama. |
| Malzeme listesi | Kumaş, aksesuar, ip vb. — maliyet hesabı. |
| Tedarikçi (vendor) | Tedarikçi profil, sipariş geçmişi, ödeme. |
| Maliyet kartı | Ürün bazlı: kumaş + iş + kar = satış fiyatı önerisi. |

### 3.11 [P2] AI Asistan & Müşteri Chatbot

| Özellik | Açıklama |
|---|---|
| Admin AI asistan | "Bu ürün için Trendyol attribute önersene" gibi prompt'lar. |
| Müşteri chatbot | Storefront alt-sağ köşede; kargo, iade, beden danışmanlığı. |
| Knowledge base | Sık sorulan sorular ve cevaplar AI tarafından kullanılır. |

### 3.12 [P3] Mobil Uygulama

- Capacitor 7 wrap (Android + iOS).
- Push notification (FCM).
- Storefront'un native experience'ı.

---

## 4. Non-Functional Gereksinimler

### 4.1 Performans

- **Storefront:** LCP < 2.5s, FID < 100ms, CLS < 0.1 (Web Vitals).
- **API:** P95 < 500ms (cache hit), P95 < 1.5s (Mongo hit).
- **Trendyol push:** 100 ürün < 60sn (batch).

### 4.2 Ölçeklenebilirlik

- 50K SKU + 100K üye'ye kadar tek-instance Mongo + tek FastAPI worker yeterli.
- 200K üzeri için: Mongo replica set + FastAPI uvicorn 4-worker + Redis cache zorunlu.

### 4.3 Availability

- Hedef uptime: %99.5 (yıllık ~44 saat downtime).
- Otomatik restart (Supervisor / systemd / K8s).

### 4.4 Güvenlik

- Tüm endpoint'ler HTTPS.
- JWT HS256, 24h expiry, refresh token.
- Bcrypt cost 12.
- Brute-force koruması: IP başına 5 deneme/15dk.
- API key'ler **vault**'ta (Fernet AES).
- OWASP Top 10 önlemleri (SQL injection N/A — Mongo; XSS için React escape; CSRF için sameSite cookie).
- CORS whitelist.

### 4.5 Veri & Yedekleme

- Mongo günlük tam yedek + saatlik oplog tail.
- Yedek retention: 30 gün.
- Görseller: Object storage (Cloudflare R2 önerilir) — versionlu silme.

### 4.6 Loglama & Monitoring

- Tüm 3rd party istek/yanıt → `integration_logs` collection.
- Hata logları → `error_logs` + SMTP alert (throttle: 5dk).
- Audit log: login, role change, admin action.

### 4.7 Lokalizasyon

- TR ana dil. EN opsiyonel.
- TL currency primary.

### 4.8 Erişilebilirlik

- WCAG 2.1 AA hedef.
- Tüm interaktif element'lerde `data-testid` (otomasyon için).

---

## 5. Teknoloji Seçimi

### 5.1 Zorunlu Stack

| Katman | Teknoloji | Sebep |
|---|---|---|
| **Frontend Framework** | React 19 + React Router 7 | Geniş ekosistem, hot reload, Capacitor uyumu. |
| **CSS Framework** | Tailwind CSS 3 | Hızlı stil + dark mode + responsive. |
| **UI Library** | Shadcn/UI (Radix tabanlı) | Erişilebilir + özelleştirilebilir + lisanssız (kod kopyalıyorsun). |
| **State Mgmt** | React Context + useState (custom hook'lar) | Redux overkill; 500 ürünlük admin için yeterli. |
| **HTTP Client** | axios | Interceptor, response transform desteği. |
| **Toast** | sonner | Shadcn ile entegre. |
| **Backend Framework** | FastAPI 0.110+ | Async, OpenAPI doc otomatik, Pydantic validation. |
| **DB** | MongoDB 6+ (Motor async) | Esnek şema (varyant array, attribute object). |
| **SOAP Client** | zeep | Ticimax / Doğan e-Dönüşüm. |
| **HTTP Client (BE)** | httpx | Async REST çağrıları (Trendyol, vb.). |
| **Schedule** | APScheduler + asyncio | Cron + background loops. |
| **Auth** | python-jose (JWT) + passlib[bcrypt] | Standart. |
| **Excel** | pandas + xlrd + openpyxl | .xls + .xlsx desteği. |
| **HTML parse** | beautifulsoup4 | Storefront scrape için. |
| **Vault** | cryptography (Fernet) | AES-128 + HMAC. |
| **Process Mgmt** | Supervisor veya systemd | Backend + frontend auto-restart. |

### 5.2 İsteğe Bağlı / Önerilen

| İhtiyaç | Önerilen |
|---|---|
| **Object Storage** | Cloudflare R2 (S3 uyumlu, ucuz) veya MinIO self-hosted. |
| **Email** | Resend.com (transactional + marketing). |
| **SMS** | Netgsm veya İleti Merkezi (TR yerel). |
| **AI** | OpenAI GPT-4o / Claude Sonnet 4.5 (admin asistan + müşteri chatbot). |
| **Cache** | Redis 7 (opsiyonel — in-memory fallback). |
| **CDN** | Cloudflare (TR pop'leri var). |
| **Monitoring** | Sentry (frontend + backend) + UptimeRobot. |
| **CI/CD** | GitHub Actions + Docker. |
| **Mobile** | Capacitor 7 (web kodunu sarmalama). |

### 5.3 Alternatifler (Müşteri tercih ederse)

| Alternatif | Trade-off |
|---|---|
| Next.js (yerine React+Vite) | SSR/SEO daha güçlü ama K8s deployment daha karmaşık. |
| PostgreSQL (yerine Mongo) | Strict schema; ama esnek varyant/attribute zorlaşır. |
| Django (yerine FastAPI) | Built-in admin var ama async/REST için FastAPI daha hızlı. |

---

## 6. Sistem Mimarisi

### 6.1 Servis Topolojisi

```
                       ┌────────────────────┐
                       │  Cloudflare CDN +  │
                       │  WAF + SSL         │
                       └────────┬───────────┘
                                │
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
        ┌────────────┐   ┌────────────┐  ┌────────────┐
        │ React SPA  │   │  Mobile    │  │ Pazaryeri  │
        │ (web)      │   │  App       │  │ Webhooks   │
        │            │   │ (Capacitor)│  │            │
        └─────┬──────┘   └──────┬─────┘  └──────┬─────┘
              │                 │               │
              └─────────────────┼───────────────┘
                                ▼
                    ┌───────────────────────┐
                    │  Nginx Reverse Proxy  │
                    │   /api/* → :8001      │
                    │   /* → :3000          │
                    └──────────┬────────────┘
                               │
                  ┌────────────┼────────────┐
                  ▼            ▼            ▼
            ┌──────────┐ ┌──────────┐ ┌──────────┐
            │ FastAPI  │ │ FastAPI  │ │ FastAPI  │
            │ Worker 1 │ │ Worker 2 │ │ Worker N │
            └─────┬────┘ └─────┬────┘ └─────┬────┘
                  │            │            │
                  └────────────┼────────────┘
                               │
              ┌────────────────┼──────────────────┐
              ▼                ▼                  ▼
        ┌──────────┐    ┌──────────────┐   ┌──────────┐
        │ MongoDB  │    │ Redis Cache  │   │ Object   │
        │ (replica)│    │              │   │ Storage  │
        └──────────┘    └──────────────┘   │ (R2/S3)  │
                                            └──────────┘

      Arkaplan İşler (asyncio + APScheduler):
      ┌──────────────────────────────────────────┐
      │ • Trendyol Retry Queue   (60 dk)         │
      │ • Havale 48h Auto-Cancel (saatlik)       │
      │ • Ticimax Sipariş Sync   (30 dk)         │
      │ • Stok Senkron           (5 dk)          │
      │ • Email Campaign Queue   (anlık + retry) │
      └──────────────────────────────────────────┘
```

### 6.2 Klasör Yapısı (Hedef)

```
/app
├── backend/
│   ├── server.py                    # Ana entry
│   ├── core/                        # Config, security, deps
│   │   ├── config.py
│   │   ├── security.py              # JWT, bcrypt
│   │   ├── deps.py                  # FastAPI dependencies
│   │   └── exceptions.py
│   ├── models/                      # Pydantic modelleri
│   │   ├── product.py, order.py, user.py, …
│   ├── routes/                      # Endpoint modülleri
│   │   ├── auth/, products/, orders/, integrations/
│   │   │   ├── trendyol/
│   │   │   │   ├── push.py
│   │   │   │   ├── batch.py
│   │   │   │   ├── ghost_scanner.py
│   │   │   │   └── retry_queue.py
│   │   │   ├── ticimax/
│   │   │   └── iyzico.py
│   │   └── admin/
│   ├── services/                    # İş mantığı
│   │   ├── trendyol_service.py
│   │   ├── ticimax_service.py
│   │   ├── payment_service.py
│   │   └── notification_service.py
│   ├── clients/                     # 3rd party API wrapper'ları
│   │   ├── ticimax_soap.py
│   │   ├── trendyol_rest.py
│   │   ├── iyzico.py
│   │   ├── dogan.py
│   │   └── resend.py
│   ├── utils/                       # Helper fonksiyonlar
│   │   ├── slug.py, html_clean.py, validators.py
│   ├── scripts/                     # CLI import/sync scriptleri
│   │   └── *.py
│   ├── tests/                       # pytest
│   ├── requirements.txt
│   └── .env
│
├── frontend/
│   ├── src/
│   │   ├── App.jsx                  # Router root
│   │   ├── lib/
│   │   │   ├── api.js               # Axios instance + interceptor
│   │   │   ├── auth.js              # JWT helpers
│   │   │   └── utils.js
│   │   ├── components/
│   │   │   ├── ui/                  # Shadcn primitifleri
│   │   │   ├── storefront/          # Header, Footer, ProductCard, …
│   │   │   └── admin/               # Pagination, AppConfirm, …
│   │   ├── pages/
│   │   │   ├── storefront/          # Home, Cart, Checkout, ProductDetail
│   │   │   └── admin/
│   │   │       ├── Products/        # Products.jsx + alt bileşenler
│   │   │       ├── Orders/
│   │   │       ├── Settings/
│   │   │       └── …
│   │   ├── hooks/                   # useAuth, useDebounce, useToast
│   │   └── contexts/                # AuthContext, CartContext
│   ├── package.json
│   └── .env
│
├── docs/                            # Bu doküman + sistem dökümanı
├── docker/                          # Dockerfile + docker-compose
├── .github/workflows/               # CI/CD
└── README.md
```

---

## 7. Veri Modeli (DB Şeması)

> **Notasyon:** PK = unique identifier; idx = indexed.

### 7.1 `users` (Admin + müşteri kullanıcı)

```jsonc
{
  "id": "uuid",                          // PK, idx
  "email": "user@example.com",           // unique idx
  "password_hash": "bcrypt",
  "name": "Ahmet Yılmaz",
  "phone": "+905551234567",              // idx
  "role": "admin|manager|support|viewer|customer|vendor",
  "is_active": true,
  "last_login_at": "ISO datetime",
  "created_at": "ISO datetime"
}
```

### 7.2 `customers` (Müşteri profili)

```jsonc
{
  "id": "uuid",                          // PK, idx
  "user_id": "uuid",                     // FK users (opsiyonel)
  "email": "string",                     // idx
  "phone": "string",                     // idx
  "full_name": "string",
  "addresses": [
    { "id": "uuid", "type": "shipping|billing",
      "name": "...", "phone": "...",
      "address_line1": "...", "city": "...", "district": "...",
      "is_default": true }
  ],
  "segment": "VIP|Regular|Sleeping|New",
  "risk_score": 0,
  "lifetime_value": 0.0,
  "total_orders": 0,
  "iys_sms_consent": false,
  "iys_email_consent": false,
  "is_blocked": false,
  "ticimax_uye_id": 12345,               // sparse idx (mevcutsa)
  "created_at": "ISO datetime"
}
```

### 7.3 `products`

```jsonc
{
  "id": "uuid",                          // PK
  "urun_karti_id": "2839",               // Ticimax UrunKartiID, idx
  "slug": "velora-dantelli-saten-takim-haki",  // unique idx
  "name": "Velora Dantelli Saten Takım Haki",
  "color": "Haki",
  "stock_code": "FCSS2000002",           // idx (multiple products share)
  "sku": "FCSS2000002",
  "brand": "FACETTE",
  "vendor": "FACETTE",                   // tedarikçi adı
  "category_id": "uuid",                 // FK categories, idx
  "category_name": "Takım",
  "breadcrumb": "GİYİM > Üst Giyim > Takım",

  "price": 2800.0,                       // KDV dahil
  "sale_price": 2380.0,
  "member_price_1": 2800.0,              // üye fiyatı
  "cost_price": 0.0,                     // alış
  "vat_rate": 10.0,
  "currency": "TRY",

  "description": "<p>HTML…</p>",
  "short_description": "kısa metin",

  "variants": [
    { "id": "uuid",                      // unique varyant id
      "size": "XS|S|M|L|XL|M/L|...",
      "color": "Haki",
      "barcode": "8684483526152",        // unique idx
      "stock_code": "FCSS2000002",
      "urun_id": "8381",                 // Ticimax UrunID
      "stock": 26,
      "price": 2800.0,
      "sale_price": 2380.0,
      "is_active": true }
  ],

  "images": ["https://r2.../1.jpg", "https://r2.../2.jpg"],
  "main_image": "https://r2.../1.jpg",

  "attributes": {                        // Trendyol payload
    "Kumaş Tipi": "Saten",
    "Yaka": "V Yaka",
    "Kol Boyu": "Uzun Kol"
  },

  "trendyol": {
    "brand_id": 975755,
    "category_id": 411,
    "markup": null,                       // override (null ise main.trendyol_markup)
    "last_pushed_at": "ISO",
    "last_batch_id": "string"
  },

  "seo": {
    "meta_title": "…", "meta_description": "…", "meta_keywords": "…"
  },

  "is_active": true,
  "is_published": true,                   // storefront görünür mü
  "is_new": true, "is_featured": false,
  "created_at": "ISO", "updated_at": "ISO"
}
```

### 7.4 `categories`

```jsonc
{
  "id": "uuid",                          // PK
  "name": "Takım",
  "slug": "takim",                       // unique idx
  "parent_id": "uuid|null",              // tree
  "depth": 2,
  "order": 1,
  "image": "url",
  "is_active": true,
  "seo": { "meta_title": "...", ... }
}
```

### 7.5 `orders`

```jsonc
{
  "id": "uuid",                          // PK
  "order_number": "FCT-2026-00001234",   // unique idx
  "customer_id": "uuid",                 // idx
  "source": "site|trendyol|hepsiburada|temu",  // idx
  "marketplace_order_id": "TY12345",     // (eğer kaynaksa)

  "status": "new|preparing|shipped|delivered|cancelled|refunded",
  "payment_status": "pending|paid|failed|refunded",
  "payment_method": "credit_card|havale|cod",
  "payment_provider": "iyzico|...",
  "payment_transaction_id": "string",

  "items": [
    { "product_id": "uuid", "variant_id": "uuid",
      "name": "...", "barcode": "...", "size": "...", "color": "...",
      "quantity": 1, "unit_price": 2380.0, "total": 2380.0,
      "vat_rate": 10.0 }
  ],

  "subtotal": 2380.0,
  "discount": 0.0,
  "coupon_code": "WELCOME10",
  "shipping_cost": 30.0,
  "total": 2410.0,

  "shipping_address": { /* OrderAddress */ },
  "billing_address": { /* OrderAddress */ },

  "shipping_provider": "mng|aras|yurtici|...",
  "tracking_number": "string",
  "tracking_url": "string",

  "notes": "string",
  "internal_notes": "admin için",

  "created_at": "ISO", "updated_at": "ISO",
  "paid_at": "ISO", "shipped_at": "ISO", "delivered_at": "ISO"
}
```

### 7.6 Diğer Önemli Koleksiyonlar

| Collection | Amaç | Önemli Alanlar |
|---|---|---|
| `attributes` | Sistem attribute tanımları | `id, name, type, values[]` |
| `attribute_library` | Trendyol attribute cache | `category_id, attribute_id, values[]` |
| `variant_colors` | Renk kütüphanesi | `name, hex_code, image` |
| `variant_sizes` | Beden kütüphanesi | `name, order` |
| `size_tables` | Ölçü tablosu | `product_id, rows[], columns[]` |
| `category_mappings` | Pazaryeri kategori eşlemesi | `local_category_id, marketplace, marketplace_category_id, attribute_mappings{}` |
| `trendyol_categories` | Trendyol kategori cache | `id, name, parent_id, has_attributes` |
| `trendyol_sync_logs` | Push logları | `batch_id, status, products[], created_at` |
| `trendyol_stuck_queue` | Retry kuyruğu | `product_id, reason, attempt_count, next_try_at` |
| `coupons` | Kupon | `code, type, value, conditions{}, usage_limit, usage_count` |
| `stock_movements` | Stok in/out | `product_id, variant_id, delta, reason, ref_id, created_at` |
| `settings` | Modül ayarları | `_id (str = module), data{}` |
| `vault_secrets` | Şifreli key store | `key_name, encrypted_value, updated_at` |
| `roles` | RBAC | `name, permissions[]` |
| `auth_audit_logs` | Login/logout | `user_id, action, ip, user_agent, at` |
| `integration_logs` | 3rd party istek/yanıt | `service, endpoint, request, response, status, duration_ms` |
| `notification_logs` | Email/SMS/Push | `to, channel, template, status, error` |
| `notification_templates` | Şablonlar | `event, subject, body_html, body_text` |
| `cart_sessions` | Üye olmadan sepet | `session_id, items[], expires_at` |
| `reviews` | Ürün yorumları | `product_id, customer_id, rating, comment, approved` |
| `seo_meta` | Sayfa SEO override | `path, title, description, keywords` |
| `seo_redirects` | 301 yönlendirme | `from_path, to_path` |

### 7.7 Index Stratejisi (Mongo)

```js
db.users.createIndex({email: 1}, {unique: true})
db.users.createIndex({phone: 1})
db.customers.createIndex({email: 1}, {sparse: true})
db.customers.createIndex({phone: 1}, {sparse: true})
db.products.createIndex({slug: 1}, {unique: true})
db.products.createIndex({stock_code: 1})
db.products.createIndex({urun_karti_id: 1})
db.products.createIndex({"variants.barcode": 1})
db.products.createIndex({category_id: 1, is_active: 1, is_published: 1})
db.products.createIndex({name: "text", description: "text", stock_code: "text"})
db.orders.createIndex({order_number: 1}, {unique: true})
db.orders.createIndex({customer_id: 1, created_at: -1})
db.orders.createIndex({source: 1, status: 1})
db.orders.createIndex({marketplace_order_id: 1}, {sparse: true})
```

---

## 8. API Sözleşmeleri

> **Konvansiyon:** Tüm response'lar JSON. Hata formatı:
> ```jsonc
> { "detail": "Hata açıklaması", "code": "VALIDATION_ERROR", "fields": {} }
> ```
> Auth gerekli endpoint'lerde `Authorization: Bearer <jwt>` header.

### 8.1 Auth

```
POST   /api/auth/register
  Body: { email, password, full_name, phone }
  Resp: { token, user }

POST   /api/auth/login
  Body: { email, password }
  Resp: { token, user, expires_at }

POST   /api/auth/forgot-password
  Body: { email }
  Resp: { message: "OTP gönderildi" }

POST   /api/auth/reset-password
  Body: { email, otp, new_password }
  Resp: { message }

POST   /api/auth/refresh
  Header: Bearer <expired_token>
  Resp: { token }

GET    /api/auth/me
  Resp: { user }
```

### 8.2 Ürünler (Storefront)

```
GET    /api/products
  Query: page, page_size, search, category_slug, color, size,
         price_min, price_max, sort (popular|price_asc|price_desc|newest)
  Resp: { items: [Product], total, page, page_size }

GET    /api/products/{slug}
  Resp: { product, related_products: [] }

GET    /api/categories/tree
  Resp: { tree: [Category] }   // hiyerarşik

GET    /api/cart   (auth opsiyonel — session_id query)
POST   /api/cart/items
  Body: { variant_id, quantity }
DELETE /api/cart/items/{variant_id}
PUT    /api/cart/items/{variant_id}
  Body: { quantity }

POST   /api/cart/apply-coupon
  Body: { code }
```

### 8.3 Sipariş (Storefront)

```
POST   /api/orders/checkout
  Body: { shipping_address, billing_address, payment_method, items, coupon }
  Resp: { order, payment_url (iyzico 3DS) }

POST   /api/orders/iyzico/callback   (server-to-server)

GET    /api/orders/{order_number}
  (auth veya tracking_token)

GET    /api/orders/track/{tracking_code}
  Resp: { status, history[], shipping }
```

### 8.4 Admin Ürünler

```
GET    /api/admin/products
  Query: page, search, filters
  Resp: { items, total }

POST   /api/admin/products
  Body: Product (variant'larla beraber)

PUT    /api/admin/products/{id}
DELETE /api/admin/products/{id}
POST   /api/admin/products/{id}/duplicate

POST   /api/admin/products/bulk-update
  Body: { product_ids: [], updates: { price, stock, is_active } }

GET    /api/admin/products/barcode-issues
POST   /api/admin/products/barcode-fix
```

### 8.5 Admin Trendyol

```
GET    /api/admin/integrations/trendyol/status

POST   /api/admin/integrations/trendyol/products/validate
  Body: { product_ids: [] }
  Resp: { valid: [], errors: [] }

POST   /api/admin/integrations/trendyol/products/sync
  Body: { product_ids: [] }
  Resp: { batch_id, count }

GET    /api/admin/integrations/trendyol/batch/{batch_id}
  Resp: { status, items: [{barcode, status, errors[]}] }

POST   /api/admin/integrations/trendyol/ghost-scanner
POST   /api/admin/integrations/trendyol/archive-barcodes
  Body: { barcodes: [] }

POST   /api/admin/integrations/trendyol/retry-queue/run-now

GET    /api/admin/integrations/trendyol/sync-logs
  Query: page, status, date_from, date_to
```

### 8.6 Admin Ticimax

```
GET    /api/admin/integrations/ticimax/status

POST   /api/admin/integrations/ticimax/excel-upload
  Form-data: file (.xls/.xlsx)
  Resp: { preview: [], parent_count, variant_count }

POST   /api/admin/integrations/ticimax/excel-import
  Body: { file_id, mode: "merge|replace" }
  Resp: { updated, created, errors }

POST   /api/admin/integrations/ticimax/products/pull-by-kart-id
  Body: { kart_ids: [2839, 2840] }
  Resp: { results: [{kart_id, status, product_id}] }

POST   /api/admin/integrations/ticimax/orders/sync
  Body: { date_from, date_to }

POST   /api/admin/integrations/ticimax/members/sync
  Body: { pages: 50 }
```

### 8.7 Admin Sipariş

```
GET    /api/admin/orders
  Query: page, status, source, date_from, date_to
PUT    /api/admin/orders/{id}/status
  Body: { status, internal_notes }
POST   /api/admin/orders/{id}/shipping-label
  Resp: { pdf_url, tracking_number }
POST   /api/admin/orders/{id}/refund
  Body: { amount, reason }
```

### 8.8 Webhooks

```
POST   /api/webhooks/iyzico/payment-result
POST   /api/webhooks/trendyol/order
POST   /api/webhooks/trendyol/claim
POST   /api/webhooks/ticimax/order   (planlı)
```

---

## 9. 3. Parti Entegrasyon Spesifikasyonları

### 9.1 Ticimax SOAP

- **Base URL:** `https://{domain}/Servis/UrunServis.svc?wsdl` (+ `SiparisServis.svc`, `UyeServis.svc`)
- **Auth:** her çağrıda `UyeKodu` parametresi (WS API Key).
- **Rate limit:** 12 saniye/çağrı (genelde 13sn beklenmesi önerilir).
- **Önemli çağrılar:**

```python
# SOAP Client örneği
from zeep import Client, Settings
from zeep.transports import Transport

client = Client(
    "https://www.facette.com.tr/Servis/UrunServis.svc?wsdl",
    settings=Settings(strict=False, xml_huge_tree=True),
    transport=Transport(timeout=90, operation_timeout=180),
)

# Kategori
client.service.SelectKategori(UyeKodu="...", kategoriID=0, dil="tr", parentID=0)

# Ürün
ff = client.get_type("ns2:UrunFiltre")
sf = client.get_type("ns2:UrunSayfalama")
client.service.SelectUrun(
    UyeKodu="...",
    f=ff(Aktif=1),
    s=sf(BaslangicIndex=0, KayitSayisi=200, KayitSayisinaGoreGetir=True)
)

# Sipariş — int filtrelerde -1 = "filtre yok", 0 = "değeri 0 olanlar"
fkw = {"UrunGetir": True, "OdemeGetir": True, "EntegrasyonAktarildi": -1,
       "SiparisDurumu": -1, "PazaryeriIhracat": -1, "SiparisID": -1, ...}
```

- **Yetki kontrolü:** `SelectUrun` yetki yokken sessizce boş döner. Önceden `SelectKategori` probe edilmeli.
- **Storefront Scrape (Plan B):** UrunServis yetkisi yoksa: sitemap.xml → ürün URL → `productDetailModel` JS nesnesi parse → `products[]`, `productVariantData[]`, `productImages[]` → DB.

### 9.2 Trendyol REST

- **Base URL:** `https://api.trendyol.com/sapigw/suppliers/{supplier_id}/`
- **Auth:** Basic Auth (base64 of `{api_key}:{api_secret}`).
- **Rate limit:** ~600 istek/dakika.
- **Önemli endpoint'ler:**

```
POST   /v2/products                                  → create/update batch
PUT    /integration/inventory/sellers/{id}/products/price-and-inventory
POST   /v2/products/archive
GET    /v2/products/batch-requests/{batch_id}
GET    /product-categories                           → kategori tree
GET    /product-categories/{id}/attributes           → attribute list
GET    /brands                                       → marka arama
GET    /orders                                       → sipariş listele
GET    /questions/filter                             → soru-cevap
GET    /claims                                       → iade
```

### 9.3 İyzico

- **SDK:** `iyzipay-python` veya manuel REST.
- **3DS akış:**
  1. `POST /payment/3dsecure/initialize` → `paymentPageUrl` ya da HTML form.
  2. Müşteri 3DS doğrulaması → `callback_url`'e POST.
  3. Backend `POST /payment/3dsecure/auth` ile finalize.
  4. Sipariş `paid`.
- **Test ortamı:** `https://sandbox-api.iyzipay.com`.

### 9.4 Trendyol Push — 4 Kademeli Fallback (CRITICAL)

> Bu, sistemin **en kritik iş mantığı**. Doğru implement edilmezse %30+ push başarısız olur.

```python
async def push_product_to_trendyol(product, config):
    # 1) Normal create/update
    resp = await trendyol_post(f"/v2/products", body)
    if resp.status_code == 200 and batch_ok(resp):
        return "OK"

    # 2) Price-and-Inventory fallback (cache'de ürün varsa)
    if "already exists" in resp.text or "duplicate" in resp.text:
        pi_resp = await trendyol_put(
            f"/integration/inventory/sellers/{sid}/products/price-and-inventory",
            { "items": [{"barcode": v.barcode, "quantity": v.stock,
                        "listPrice": v.list_price, "salePrice": v.sale_price}
                       for v in product.variants] })
        if pi_resp.ok:
            return "OK_PI"

    # 3) Deep Cross-Conflict (başka satıcının barkodu)
    if "barcode_conflict" in resp.text:
        # Eski barkodları arşivle (bizden kaynaklı self-conflict)
        await trendyol_post("/v2/products/archive",
            { "items": [{"barcode": old_bc} for old_bc in our_old_barcodes] })
        # Yeniden gönder
        resp2 = await trendyol_post("/v2/products", body)
        if resp2.ok:
            return "OK_AFTER_ARCHIVE"

    # 4) Stuck queue
    await db.trendyol_stuck_queue.insert_one({
        "product_id": product.id,
        "reason": resp.text,
        "attempt_count": 0,
        "next_try_at": now + timedelta(hours=1),
    })
    return "QUEUED"
```

**Arkaplan loop:** her 60 dk `trendyol_stuck_queue`'yu tarar, kademeleri yeniden dener; `attempt_count >= 10` ise admin'e alert.

### 9.5 Doğan e-Dönüşüm (E-Fatura)

- 4 ayrı WSDL: Auth, E-Fatura, E-Arşiv, E-İrsaliye.
- Session token (15dk TTL) cache'lenmeli.
- Sipariş `paid` → otomatik fatura kesme (cron job).

### 9.6 İYS (İleti Yönetim Sistemi)

- Üye SMS/Email izinleri İYS'ye senkronize edilmeli (yasal zorunluluk).
- `iys_permissions` collection ile yerel cache.

---

## 10. UI/UX Gereksinimleri

### 10.1 Tasarım Dili

- **Storefront tema:** Premium / minimalist (Miu Miu, COS, Massimo Dutti referans).
  - Bol beyaz alan, büyük tipografi, monochrome (siyah-beyaz-bej).
  - Sans-serif (örn: Inter, GT Walsheim).
  - Animasyonlar: ürün kartı hover'da yumuşak crossfade.
- **Admin paneli:** Veri yoğun, fonksiyonel (Linear, Stripe Dashboard referans).
  - Sol sidebar nav, üst topbar (kullanıcı + bildirimler).
  - Tablolar yoğun bilgi (50 satır/sayfa), sticky header.

### 10.2 Responsive

- Storefront: mobile-first; breakpoints 640/768/1024/1280/1536.
- Admin: 1280+ optimum; 768'de sidebar collapse.

### 10.3 Erişilebilirlik

- WCAG 2.1 AA.
- Tüm form input'larda `<label>`.
- Klavye navigasyonu tüm UI'da.
- Modal'da focus trap.
- `data-testid` her interaktif element'te (test otomasyonu için).

### 10.4 Sayfa Listesi (Storefront)

| Path | Bileşen | Notlar |
|---|---|---|
| `/` | Home | Banner slider, kategoriler, en yeni, kampanyalar. |
| `/kategori/:slug` | Category (PLP) | Filtre sidebar + ürün grid. |
| `/urun/:slug` | ProductDetail (PDP) | Galeri, varyant, sepet. |
| `/sepet` | Cart | Sepet özet + kupon. |
| `/odeme` | Checkout | 3 adım: adres / kargo / ödeme. |
| `/order-success/:orderNumber` | OrderSuccess | Teşekkür sayfası. |
| `/giris` | Login | Üye girişi + sosyal login. |
| `/uye-ol` | Register | Üye kayıt formu. |
| `/hesabim` | Account | Sipariş geçmişi, adres, iade. |
| `/siparis-takip` | TrackOrder | Üyesiz takip (kod + email). |
| `/arama` | Search | Arama sonuç listesi. |
| `/sayfa/:slug` | StaticPage | KVKK, hakkımızda vb. |

### 10.5 Sayfa Listesi (Admin)

> Tüm sayfaların `/admin/*` altında ve `AdminLayout` (sidebar + topbar) içinde.

**Ana modüller:**

- **Katalog:** `/admin/urunler`, `/admin/kategoriler`, `/admin/varyantlar`, `/admin/urun-ozellikleri`, `/admin/olcu-tablolari`, `/admin/yorumlar`.
- **Sipariş:** `/admin/siparisler`, `/admin/iadeler`, `/admin/terkedilmis-sepet`.
- **Müşteri:** `/admin/uyeler`, `/admin/musteri-segmentleri`, `/admin/bloklu-musteriler`, `/admin/cariler` (tedarikçi).
- **Pazaryeri:** `/admin/pazaryerleri`, `/admin/kategori-eslestir`, `/admin/marka-eslestir`, `/admin/trendyol-loglar`, `/admin/trendyol-hayalet`, `/admin/aktarilamayanlar`, `/admin/barkod-sorunlari`.
- **Pazarlama:** `/admin/kuponlar`, `/admin/kampanyalar`, `/admin/bannerlar`, `/admin/sayfa-tasarimi`, `/admin/iys`.
- **Raporlar:** `/admin/raporlar/satis`, `/urun`, `/stok`, `/uye`, `/iade-ve-trend`.
- **Sistem:** `/admin/ayarlar`, `/admin/kullanicilar`, `/admin/secrets-vault`, `/admin/sistem-sagligi`, `/admin/guvenlik-paneli`, `/admin/entegrasyon-loglari`, `/admin/otomasyon`.
- **AI:** `/admin/ai-asistan`.
- **Üretim:** `/admin/imalat`, `/admin/uretim-plani`.
- **Mobil:** `/admin/mobil-uygulama`.

---

## 11. Geliştirme Yol Haritası (16 hafta)

### Sprint 0 — Kurulum (1 hafta)

- [ ] Git repo, klasör yapısı, README.
- [ ] Docker compose (Mongo + Backend + Frontend).
- [ ] .env şablonu + CI/CD iskelet.
- [ ] Frontend Tailwind + Shadcn + Router kurulum.
- [ ] Backend FastAPI + Motor + Pydantic + auth iskelet.
- [ ] `users`, `roles` collection + JWT auth çalışıyor.

**Çıktı:** "Hello World" frontend + backend + login endpoint.

### Sprint 1 — Çekirdek Veri Modeli (1 hafta)

- [ ] `categories`, `products`, `variants` modelleri.
- [ ] CRUD endpoint'leri (`/api/admin/products`).
- [ ] Index'ler kurulu.
- [ ] Seed script (10 örnek ürün).

**Çıktı:** Postman'den ürün CRUD çalışıyor.

### Sprint 2 — Admin Ürün UI (2 hafta)

- [ ] Admin login + AdminLayout (sidebar/topbar).
- [ ] Ürün listesi (filtre, search, pagination).
- [ ] Ürün ekleme/düzenleme modal (tüm tab'lar).
- [ ] **DescriptionEditor** (Kaynak/Önizleme/Bölünmüş).
- [ ] Görsel upload (object storage).

**Çıktı:** Admin'den ürün eklenip, varyant + görsel + açıklama girilebiliyor.

### Sprint 3 — Storefront MVP (2 hafta)

- [ ] Header, Footer, Home.
- [ ] Category (PLP) + Product (PDP) sayfaları.
- [ ] Cart (localStorage + server cart_sessions).
- [ ] Login/Register.
- [ ] Basit checkout (ödeme yok).

**Çıktı:** Müşteri ürün bulup sepete atıp checkout başlatabiliyor.

### Sprint 4 — İyzico Ödeme & Sipariş (2 hafta)

- [ ] İyzico 3DS entegrasyonu.
- [ ] `orders` collection + checkout completion.
- [ ] Sipariş email bildirimi (Resend).
- [ ] Admin sipariş ekranı (liste + detay).
- [ ] Order status workflow + audit.

**Çıktı:** Test kartla bitmiş sipariş.

### Sprint 5 — Ticimax Entegrasyonu (2 hafta)

- [ ] Ticimax SOAP client (`zeep`).
- [ ] Excel upload + import.
- [ ] Sipariş senkron (SOAP).
- [ ] Üye senkron (Ticimax → DB).
- [ ] Storefront scraper (yedek yöntem).
- [ ] CLI scriptler.

**Çıktı:** Mevcut Ticimax veritabanı yeni sisteme göç edilmiş.

### Sprint 6 — Trendyol Entegrasyonu (2 hafta) ⚠ KRİTİK

- [ ] Trendyol REST client.
- [ ] Kategori + marka cache.
- [ ] Kategori/marka eşleştirme UI.
- [ ] **4 kademeli fallback push** (bkz §9.4).
- [ ] Batch poll + log.
- [ ] Ghost scanner + arşivleme.
- [ ] Stuck queue + 60dk retry loop.
- [ ] Trendyol sipariş import.

**Çıktı:** %98+ push başarı oranı.

### Sprint 7 — RBAC, Vault, Audit (1 hafta)

- [ ] 5 rol + permission matrix.
- [ ] `auth_audit_logs` + admin UI.
- [ ] Secrets vault (Fernet) + admin UI.
- [ ] CORS + brute force koruması.

**Çıktı:** Manager rolü ürün düzenleyebiliyor, sipariş silemiyor.

### Sprint 8 — İade, Kupon, Kampanya (1 hafta)

- [ ] İade workflow (müşteri talep → admin onay).
- [ ] Kupon (kategori/ürün kısıtlı).
- [ ] Kampanya (sepet bazlı %).
- [ ] Email kampanya (Resend).

### Sprint 9 — Raporlar & Dashboard (1 hafta)

- [ ] Admin dashboard (KPI tile).
- [ ] Satış / ürün / stok / üye raporları.
- [ ] Marketplace kârlılık.
- [ ] CSV export.

### Sprint 10 — AI Asistan, IYS, Pixel (1 hafta)

- [ ] OpenAI bağlantısı (admin asistan).
- [ ] Müşteri chatbot.
- [ ] IYS senkron.
- [ ] Pixel injection.
- [ ] Sistem sağlığı sayfası.

### Sprint 11 — Mobil + Polish (1 hafta)

- [ ] Capacitor wrap (Android + iOS).
- [ ] Push notification (FCM).
- [ ] Performans optimizasyonu (lazy load, code split).
- [ ] Erişilebilirlik denetimi.
- [ ] Beta yayın.

### Sprint 12 — UAT & Go-Live (1 hafta)

- [ ] Müşteri UAT.
- [ ] Yük testi (k6 ile 100 concurrent).
- [ ] Yedekleme prosedürü.
- [ ] Dokümantasyon.
- [ ] Production deployment.

---

## 12. Kabul Kriterleri (Definition of Done)

### 12.1 Her Sprint İçin

- [ ] Tüm acceptance criteria karşılanmış.
- [ ] Backend pytest > 70% coverage (yeni kod için).
- [ ] Frontend kritik akış e2e test (Playwright).
- [ ] PR review + merge.
- [ ] Sentry hata yok.
- [ ] Demo video kaydı (5dk).

### 12.2 MVP Go-Live İçin

- [ ] %99.5 uptime hedef test ortamında doğrulanmış.
- [ ] Trendyol push başarı oranı %98+.
- [ ] LCP < 2.5s (Lighthouse mobil).
- [ ] OWASP Top 10 audit geçilmiş.
- [ ] Yedekleme + restore prosedürü test edilmiş.
- [ ] 5 admin kullanıcı UAT onayı.
- [ ] 50 test siparişi başarıyla işlenmiş.
- [ ] KVKK + İYS uyumlu.
- [ ] Türkçe çeviri tamamlanmış.

---

## 13. Teslim Çıktıları

### 13.1 Kod

- GitHub repo (private), monorepo (backend + frontend + docs).
- README, CONTRIBUTING, .env.example.
- Docker compose ile tek komutla çalıştırılabilir.

### 13.2 Dokümantasyon

- `/docs/SYSTEM_DOCS.md` — sistem dökümanı (kod tabanı bittiğinde güncellenecek).
- `/docs/API.md` — OpenAPI'den üretilmiş.
- `/docs/RUNBOOK.md` — operasyonel senaryolar (DB restore, key rotation).
- `/docs/DEPLOYMENT.md` — sunucu hazırlama + go-live adımları.

### 13.3 Test

- pytest test suite (backend).
- Playwright e2e (kritik akışlar: login, sepet, checkout, push).
- k6 yük testi senaryoları.

### 13.4 Operasyon

- Production environment hazır (DNS, SSL, monitoring).
- Sentry + UptimeRobot + Cloudflare WAF kurulu.
- İlk 30 gün ücretsiz destek (bug fix).

---

## 14. Risk & Bağımlılıklar

| Risk | Etki | Olasılık | Önlem |
|---|---|---|---|
| **Ticimax UrunServis yetkisi alınmaz** | Yüksek | Orta | Storefront scraper (Plan B) hazır. |
| **Trendyol API rate limit** | Orta | Orta | Batch queueing + retry queue. |
| **İyzico 3DS test ortamı çalışmaz** | Orta | Düşük | Sandbox + canlı switch erken yapılır. |
| **Mongo schema migration sorunları** | Yüksek | Düşük | Schema versioning + migration scripts. |
| **Mevcut Ticimax veritabanı kalitesi düşük** | Yüksek | Orta | Veri temizleme/normalize scriptleri Sprint 5'te. |
| **Müşteri scope creep** | Yüksek | Yüksek | Bu doc + sprint sonu demo + change request prosedürü. |
| **Production downtime go-live'da** | Yüksek | Düşük | Blue-green deploy + Cloudflare instant rollback. |

### 14.1 Bağımlılıklar (Müşteri Sağlayacak)

- [ ] Ticimax WS API anahtarı + UrunServis yetkisi.
- [ ] Trendyol Satıcı API key + secret + supplier_id.
- [ ] İyzico merchant hesabı.
- [ ] Doğan e-Dönüşüm üyeliği.
- [ ] Domain + SSL.
- [ ] Resend.com hesabı (email).
- [ ] Cloudflare hesabı.
- [ ] Mevcut Ticimax veri export'u (Excel).

---

## EK A — Test Senaryoları

### A.1 E2E (Playwright)

1. **Müşteri sepet akışı:**
   - Anasayfa → kategori → ürün detay → renk/beden seç → sepete ekle → checkout → 3DS → sipariş başarılı.
2. **Admin ürün düzenleme:**
   - Login → ürün liste → düzenle → açıklama editöründe HTML gir → kaydet → Trendyol'a aktar → batch başarı.
3. **Trendyol push fallback:**
   - Eski barkodlu ürün → push → "already exists" → otomatik PI fallback → başarılı.
4. **Üyesiz sipariş takip:**
   - `/siparis-takip` → kod + email → status sayfası.

### A.2 Performans (k6)

```javascript
// 100 concurrent kullanıcı, 5 dk
import http from 'k6/http';
export const options = { vus: 100, duration: '5m' };
export default function () {
  http.get(`${__ENV.BASE}/api/products?page=1`);
  http.get(`${__ENV.BASE}/api/categories/tree`);
}
```

Beklenen: P95 < 1s, error rate < %1.

### A.3 Güvenlik

- OWASP ZAP otomatik tarama.
- Brute force test: aynı IP'den 10 login deneme → 429.
- JWT manipülasyon → 401.
- SQL/NoSQL injection: `email='; --` → güvenli.

---

## EK B — Örnek Kullanıcı Akışları

### B.1 Müşteri: Sepete Ekleme → 3DS Ödeme → Sipariş

```
1. Müşteri /kategori/elbise sayfasına gelir.
2. "Velora Saten Takım" ürününe tıklar → /urun/velora-saten-takim-haki.
3. Beden "M" seçer (stok 29). Sepete ekle butonu enable olur.
4. Sepete ekle → CartDrawer açılır, "Sepete eklendi" toast.
5. /sepet sayfasında miktar=1, toplam 2380 TL.
6. "WELCOME10" kuponu girilir → indirim 238 TL düşülür.
7. "Ödemeye geç" → /odeme → adres seç → kargo seç (MNG 30 TL) → kredi kartı.
8. 3DS pop-up → iyzico sandbox → "Onayla".
9. Backend POST /api/orders/iyzico/callback alır → order.status = paid.
10. /order-success/FCT-2026-00001234 sayfası açılır.
11. Müşteriye email gider (Resend).
12. Admin paneline yeni sipariş push notification düşer.
```

### B.2 Admin: Yeni Ürün → Trendyol'a Aktar

```
1. Admin /admin/urunler → "Yeni Ürün" butonu.
2. Modal açılır. Tabs: Temel, Fiyat, Görseller, …
3. Temel: ad, kategori, marka, açıklama (DescriptionEditor — kaynak modunda HTML yazar, önizleme'de canlı görür).
4. Fiyat: 2800 TL liste, 2380 TL indirimli, vat=10.
5. Varyantlar: XS/S/M/L için ayrı satır, barkod ve stok girilir.
6. Görseller: 6 görsel drag-drop yüklenir, ana görsel seçilir.
7. SEO: meta title/desc/keywords.
8. Trendyol Ayarları: kategori eşleştirilir, attribute'lar otomatik dolar.
9. "Kaydet" → ürün DB'ye yazılır.
10. Ürün satırı checkbox'lanır → "Trendyol'a Aktar" butonu.
11. Backend: 4 kademeli fallback push çalışır.
12. /admin/trendyol-loglar'da batch_id görünür, "OK" status'la kapanır.
```

### B.3 Müşteri Hizmetleri: İade Talebi

```
1. Müşteri /hesabim → sipariş → "İade Et" → sebep + foto → gönder.
2. claim_id oluşur, status = pending.
3. Admin /admin/iadeler ekranında yeni claim görür.
4. İncele → onayla → backend iade ücretini İyzico API ile geri gönderir.
5. Müşteriye "İadeniz onaylandı, 3-5 iş günü içinde hesabınıza yatacak" emaili.
6. order.status = refunded; stoğa iade.
```

---

**Doküman sürümü: 1.0 — 26 Şubat 2026**
**Hazırlayan: Facette Tech Team**

> Bu doküman, yazılımcının/ajansın projeyi sıfırdan inşa etmesi için yeterli teknik ve fonksiyonel bilgiyi içerir. Belirsiz noktalar için müşteri ile sprint sonu demo'larda toplanıp netleştirilmesi önerilir.
