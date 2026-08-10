# FACETTE E-Commerce & ERP — Sistem Dokümantasyonu

> **Sürüm:** 2026.02 · **Son güncelleme:** 26 Şubat 2026
> **Sahibi:** facette.com.tr · **Stack:** React 19 + FastAPI + MongoDB

---

## İçindekiler

1. [Genel Bakış](#1-genel-bakış)
2. [Mimari & Bileşenler](#2-mimari--bileşenler)
3. [Klasör Yapısı](#3-klasör-yapısı)
4. [Backend (FastAPI)](#4-backend-fastapi)
5. [Frontend (React)](#5-frontend-react)
6. [Veritabanı (MongoDB) Şeması](#6-veritabanı-mongodb-şeması)
7. [3. Parti Entegrasyonlar](#7-3-parti-entegrasyonlar)
8. [Ticimax Senkronizasyon Akışları](#8-ticimax-senkronizasyon-akışları)
9. [Trendyol Senkronizasyon Akışı (Fallback Sistemi)](#9-trendyol-senkronizasyon-akışı-fallback-sistemi)
10. [Arkaplan Zamanlanmış İşler (Scheduler)](#10-arkaplan-zamanlanmış-i̇şler-scheduler)
11. [Ortam Değişkenleri](#11-ortam-değişkenleri)
12. [Geliştirme & Çalıştırma](#12-geliştirme--çalıştırma)
13. [Sık Kullanılan Scripts (CLI)](#13-sık-kullanılan-scripts-cli)
14. [Güvenlik](#14-güvenlik)
15. [Bilinen Kısıtlar & Roadmap](#15-bilinen-kısıtlar--roadmap)

---

## 1. Genel Bakış

**Facette**, üst segment kadın giyim markası için inşa edilmiş entegre bir e-ticaret yönetim platformudur. Sistem üç ana sütun üzerine kuruludur:

| Sütun | Açıklama |
|---|---|
| **Storefront (Müşteri)** | facette.com.tr için inşa edilen Miu Miu tarzı premium tema — ürün listeleme, ürün detay, sepet, ödeme, üye girişi/kayıt, sipariş takip. |
| **Admin Paneli (ERP)** | 60+ yönetim sayfası — ürün, sipariş, üye, kupon, kampanya, üretim planı, stok hareketi, raporlar, AI asistan, RBAC vb. |
| **Pazaryeri Entegrasyonu** | Ticimax (SOAP) ↔ Trendyol/Hepsiburada/Temu (REST) köprüsü. Ürün, stok, fiyat, sipariş ve barkod senkronizasyonu. |

**Boyutlar (kabaca):**
- Backend Python kod tabanı: ~50K satır (server.py 523, integrations.py 6139, products.py 1215, orders.py 1885, …).
- 65+ FastAPI route modülü, 70+ MongoDB collection (503 ürün, 2353 sipariş, 10.5K müşteri, 10.5K user).
- 60+ admin React sayfası.
- 3 farklı pazaryerine (Trendyol/Hepsiburada/Temu) için kategori & marka eşleştirme.

---

## 2. Mimari & Bileşenler

```
┌─────────────────────────────────────────────────────────────────┐
│                         FACETTE PLATFORM                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  STOREFRONT (React 19)            ADMIN PANEL (React 19)         │
│  ─────────────────────            ──────────────────────         │
│   Home, Cart, Checkout,            60+ sayfa: Products,          │
│   ProductDetail, Login,            Orders, Members, Reports,     │
│   MiuMiuTheme                      AI Assistant, RBAC, vb.       │
│            │                                  │                   │
│            └──────────────┬───────────────────┘                   │
│                           ▼                                       │
│                  ┌────────────────┐                               │
│                  │   FastAPI      │  uvicorn @ :8001              │
│                  │   /api/* router│  (Supervisor managed)         │
│                  └────┬───────┬───┘                               │
│                       │       │                                   │
│      ┌────────────────┘       └─────────────────┐                │
│      ▼                                          ▼                │
│  ┌─────────┐                            ┌────────────────────┐   │
│  │ MongoDB │                            │  3rd Party APIs    │   │
│  │ (70+    │                            │  • Ticimax SOAP    │   │
│  │ colls)  │                            │  • Trendyol REST   │   │
│  └─────────┘                            │  • İyzico, Doğan   │   │
│                                          │  • Hepsiburada, IYS│   │
│                                          │  • OpenAI/Claude   │   │
│                                          │    via Emergent    │   │
│                                          │  • Resend (Email)  │   │
│                                          │  • Capacitor (App) │   │
│                                          └────────────────────┘   │
│                                                                  │
│  ARKAPLAN İŞLERİ (APScheduler + asyncio)                         │
│  ──────────────────────────────────────                          │
│  • Trendyol Retry Queue Loop          (her 60 dk)                │
│  • Havale 48h auto-cancel             (saatlik)                  │
│  • Sipariş / stok senkronizasyonu     (manuel + cron)            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Tech Stack özetı

| Katman | Teknoloji |
|---|---|
| **Frontend** | React 19, React Router 7, Axios, Tailwind, Shadcn/UI, Radix, Lucide Icons, Sonner toasts, dnd-kit, Capacitor 7 (mobil). |
| **Backend** | FastAPI 0.110, Motor (async Mongo), Pydantic, Zeep (SOAP), httpx, pandas, xlrd/openpyxl, BeautifulSoup, APScheduler, emergentintegrations (LLM). |
| **DB** | MongoDB (PyMongo Motor async driver). |
| **Auth** | JWT (HS256) + bcrypt + httpOnly access token. RBAC roller (admin, manager, support, viewer). |
| **Cache** | Redis (opsiyonel) → fallback olarak in-memory. |
| **Vault** | Fernet (AES-128) ile şifrelenmiş `vault_secrets` collection — Trendyol/Iyzico/IYS gibi servis keyleri. |
| **Deployment** | Kubernetes pod (preview ortam). Supervisor frontend (3000) + backend (8001) süreçlerini yönetir. |

---

## 3. Klasör Yapısı

```
/app
├── backend/                          # FastAPI sunucu
│   ├── server.py                     # Ana entry, lifespan, router include
│   ├── models.py                     # Pydantic modelleri
│   ├── ticimax_client.py             # SOAP wrapper (Ürün/Sipariş/Üye)
│   ├── ticimax_order_parser.py       # SOAP sipariş → DB dönüşümü
│   ├── trendyol_client.py            # REST client
│   ├── dogan_client.py               # E-Fatura/E-Arşiv SOAP
│   ├── mng_kargo_client.py           # MNG kargo entegrasyonu
│   ├── cache.py                      # Redis veya in-memory cache
│   ├── scheduler.py                  # APScheduler kurulumu
│   ├── permissions.py                # RBAC
│   ├── notification_service.py       # Resend Email + Push
│   ├── il_mapping.py                 # TR şehir/ilçe haritalama
│   ├── routes/                       # 65+ route modülü (aşağıda detay)
│   │   ├── auth.py, products.py, orders.py, categories.py
│   │   ├── integrations.py           # ⚠ 6139 satır — Trendyol/Ticimax akışları
│   │   ├── trendyol_retry_queue.py   # Saatlik retry kuyruğu
│   │   ├── integrations_iyzico.py    # Ödeme
│   │   ├── integrations_dogan.py     # E-Fatura
│   │   ├── integrations_temu.py      # Temu pazaryeri
│   │   ├── ai_assistant.py           # OpenAI/Claude (Emergent)
│   │   ├── ai_chatbot.py             # Müşteri chatbot
│   │   ├── manufacturing.py          # Üretim planı
│   │   ├── secrets_vault.py          # AES vault
│   │   └── …
│   ├── scripts/                      # CLI senkronizasyon scriptleri
│   │   ├── ticimax_full_resync.py    # Excel SSOT resync
│   │   ├── ticimax_pull_via_storefront.py  # Storefront scrape (yeni)
│   │   ├── ticimax_pull_by_kart_ids.py     # SOAP fetch
│   │   ├── sync_ticimax_variants.py
│   │   ├── enrich_attrs_from_ticimax_master.py
│   │   ├── replace_barcodes_by_name_size.py
│   │   └── 15+ diğer
│   ├── tests/                        # pytest test dosyaları
│   ├── utils/attr_parser.py          # Description'dan attribute çıkarımı
│   ├── data/, uploads/, imports/     # Local file storage
│   ├── requirements.txt
│   └── .env
│
├── frontend/                         # React 19
│   ├── src/
│   │   ├── App.js                    # Router (110+ rota)
│   │   ├── pages/                    # Public + admin
│   │   │   ├── Home.jsx, Cart.jsx, Checkout.jsx, ProductDetail.jsx
│   │   │   ├── storefront/MiuMiuTheme.jsx
│   │   │   └── admin/                # 60+ admin sayfa
│   │   │       ├── Products.jsx, Orders.jsx, Categories.jsx
│   │   │       ├── CategoryMapping.jsx, BrandMapping.jsx
│   │   │       ├── Settings.jsx, AdminLayout.jsx
│   │   │       └── …
│   │   ├── components/
│   │   │   ├── Header.jsx, Footer.jsx, CartDrawer.jsx
│   │   │   ├── ProductCard.jsx
│   │   │   ├── admin/                # Admin yardımcı bileşenler
│   │   │   │   ├── Pagination.jsx, AppConfirm.jsx
│   │   │   │   ├── product-form/SearchableAttribute.jsx, SeoTab.jsx, StockTab.jsx
│   │   │   │   └── …
│   │   │   └── ui/                   # 40+ Shadcn primitif
│   │   └── lib/                      # axios instance, utils
│   ├── package.json
│   └── .env                          # REACT_APP_BACKEND_URL, vb.
│
├── memory/                           # Agent handoff & PRD
│   ├── PRD.md, CHANGELOG.md, ROADMAP.md
│   └── test_credentials.md
└── docs/
    └── SYSTEM_DOCS.md                # BU DOSYA
```

---

## 4. Backend (FastAPI)

### 4.1 Genel Çalışma Mantığı

- `server.py` içinde `FastAPI` uygulaması oluşturulur, `lifespan` hook'unda:
  - Logger config
  - Background scheduler (`scheduler.start_scheduler()`)
  - Trendyol retry queue loop (`asyncio.create_task(trendyol_retry_bg_loop)`)
  - Shutdown'da temizlik
- Tüm route'lar `/api` prefix'i altında toplanır (`api_router = APIRouter(prefix="/api")`).
- 50+ route modülü `include_router` ile mount edilir.
- CORS: `CORS_ORIGINS` env'inden okunur (virgüllü liste).
- Auth: JWT (HS256). `routes/auth.py` içinde `verify_token` dep.

### 4.2 Route Modülleri (Önemli Olanlar)

| Modül | Sorumluluk | Endpoint Sayısı |
|---|---|---|
| `auth.py` | Login, kayıt, şifre sıfırlama, OTP. | ~12 |
| `products.py` | Ürün CRUD, varyantlar, görseller, attributes. | ~30 |
| `orders.py` | Sipariş yönetimi, kargo entegrasyonu, iadeler. | ~40 |
| `integrations.py` | Trendyol + Ticimax — kategori, marka, ürün push, batch poll, fallback, ghost scanner. | **130+** |
| `trendyol_retry_queue.py` | Saatlik retry loop + manuel tetikleme. | 3 |
| `integrations_iyzico.py` | Ödeme oluşturma, callback, 3DS. | ~10 |
| `integrations_dogan.py` | E-Fatura / E-Arşiv senkronizasyonu. | ~8 |
| `integrations_temu.py` | Temu pazaryeri ürün/stok. | ~10 |
| `iys_integration.py` | İYS izin yönetimi (SMS/Email pazarlama). | ~8 |
| `category_mapping.py` | Lokal kategori ↔ Trendyol/HB/Temu kategori eşleşmesi + attribute mapping. | ~15 |
| `brand_mapping.py` | Marka eşleşmesi (Trendyol brand_id). | ~5 |
| `manufacturing.py` | Üretim planı, tedarikçi siparişleri, malzeme listesi. | ~15 |
| `members.py` | Üye CRUD, segmentasyon, IYS senkronu. | ~12 |
| `coupons.py` | Kupon kuralları, kullanım, kontrol. | ~10 |
| `bulk_ops.py` | Toplu fiyat/stok değiştirme, Excel export. | ~6 |
| `secrets_vault.py` | AES-256 ile şifrelenmiş key yönetimi. | ~5 |
| `ai_assistant.py` | OpenAI/Claude bot — ürün önerisi, açıklama üretimi. | ~6 |
| `ai_chatbot.py` | Müşteri tarafı chatbot (storefront). | ~4 |
| `webhooks.py` | Trendyol/Hepsiburada/Iyzico webhook receiver. | ~5 |
| `system_health.py` | Sağlık check, log özetleri, alert SMTP. | ~5 |
| `security_dashboard.py` | Login denemeleri, IP blocklist, audit logs. | ~6 |

### 4.3 Önemli API Endpoint'leri

**Ürün & Stok**
- `GET /api/products?page=1&search=…&category=…` → liste (paginasyonlu).
- `POST /api/products` → yeni ürün.
- `PUT /api/products/{id}` → güncelle.
- `POST /api/products/{id}/duplicate` → kopyala.
- `GET /api/products/barcode-issues` → eksik/duplike barkod tarayıcı.

**Ticimax**
- `GET /api/integrations/ticimax/status` → bağlantı sağlığı.
- `POST /api/integrations/ticimax/categories/import` → kategori toplu import.
- `POST /api/integrations/ticimax/variants/sync` → varyant senkron.

**Trendyol**
- `POST /api/integrations/trendyol/products/validate` → push öncesi doğrulama.
- `POST /api/integrations/trendyol/products/sync` → toplu push (4 kademeli fallback).
- `GET /api/integrations/trendyol/batch/{batch_id}` → batch durumu.
- `POST /api/integrations/trendyol/ghost-scanner` → Trendyol cache'inde sıkışmış ürünleri tara.
- `POST /api/integrations/trendyol/archive-barcodes` → sıkışan barkodları arşivle.
- `POST /api/integrations/trendyol/retry-queue/run-now` → stuck queue'yu hemen tetikle.

**Sipariş**
- `POST /api/integrations/trendyol/orders/import` → Trendyol siparişlerini çek.
- `POST /api/orders/sync-ticimax` → Ticimax siparişlerini çek (telefonlu, site-only).

**Auth**
- `POST /api/auth/login` (admin & customer).
- `POST /api/auth/register`, `POST /api/auth/forgot-password`, `POST /api/auth/reset-password`.

### 4.4 Modeller (`models.py`)

Başlıca Pydantic modelleri:
- `User`, `UserCreate` — JWT subject.
- `Category`, `CategoryCreate` — tree (parent_id).
- `ProductVariant` — `size, color, barcode, stock_code, urun_id, stock, price, sale_price`.
- `Product`, `ProductBase` — ana ürün dokümanı (variants[] gömülü).
- `Order`, `OrderAddress`, `OrderBase`, `CartItem`.
- `Banner`, `HomepageBlock`, `MenuItem`.

> Not: Sistem MongoDB üzerine `BaseDocument`/`PyObjectId` pattern'i yerine **UUID `id` string field**'ı kullanır. `_id` API yanıtlarından her zaman kaldırılır (`.find({}, {"_id": 0})`).

---

## 5. Frontend (React)

### 5.1 Genel Çalışma Mantığı

- **React 19 + React Router 7** — `App.js` içinde 110+ rota tanımlıdır.
- Public rotalar (`/`, `/kategori/:slug`, `/urun/:slug`, `/sepet`, `/odeme`, `/hesabim`, `/giris`, `/siparis-takip`) ortak `Header` + `Footer` layout altında.
- Admin rotaları `/admin/*` — `AdminLayout` içinde sidebar/topbar + nested route'lar.
- Backend URL'i **yalnızca** `process.env.REACT_APP_BACKEND_URL` üzerinden alınır.
- Tüm admin sayfalarında axios `${REACT_APP_BACKEND_URL}/api/...` formatıyla çağrı yapılır.
- Toast bildirimi: `sonner` (Shadcn entegre).
- UI bileşenleri: Shadcn/UI primitifleri `/app/frontend/src/components/ui/`. Radix tabanlı, Tailwind ile özelleştirilmiş.

### 5.2 Önemli Admin Sayfaları

| Sayfa | Rota | Açıklama |
|---|---|---|
| **Products.jsx** | `/admin/urunler` | Ürün listesi + düzenleme modalı. Tabs: Temel, Fiyat, Görseller, Stok, Varyantlar, SEO, Özellikler, Ölçü Tablosu, Kombin, Trendyol Ayarları. **DescriptionEditor** (Kaynak/Önizleme/Bölünmüş) burada. |
| Orders.jsx | `/admin/siparisler` | Sipariş listesi + detay drawer + kargo etiketi. |
| Categories.jsx | `/admin/kategoriler` | Kategori ağacı CRUD. |
| Variants.jsx | `/admin/varyantlar` | Renk/Beden kütüphanesi. |
| CategoryMapping.jsx | `/admin/kategori-eslestir` | Lokal ↔ pazaryeri kategori + attribute eşleştirme. |
| BrandMapping.jsx | `/admin/marka-eslestir` | Marka ID eşlemesi. |
| Settings.jsx | `/admin/ayarlar` | Genel ayarlar (kâr oranı, vergi, vb.). |
| AIAssistant.jsx | `/admin/ai-asistan` | OpenAI/Claude bot. |
| Members.jsx | `/admin/uyeler` | Müşteri yönetimi + IYS. |
| Manufacturing.jsx | `/admin/imalat` | Üretim planı, tedarikçi. |
| MarketplaceHub.jsx | `/admin/pazaryerleri` | Tüm pazaryerleri tek panel. |
| TrendyolGhostScanner.jsx | `/admin/trendyol-hayalet` | Cache'de sıkışmış ürünleri tara. |
| BulkPriceStock.jsx | `/admin/toplu-fiyat-stok` | Toplu fiyat/stok güncelleme. |
| SystemHealth.jsx | `/admin/sistem-sagligi` | Backend/Mongo/3rd party sağlık. |
| Reports*.jsx | `/admin/raporlar/*` | Satış, ürün, stok, üye raporları. |

### 5.3 Public (Storefront) Sayfaları

| Sayfa | Rota | Açıklama |
|---|---|---|
| Home.jsx | `/` | Anasayfa — banner, kategori, kampanya blokları. |
| Category.jsx | `/kategori/:slug` | PLP, filtre + sıralama. |
| ProductDetail.jsx | `/urun/:slug` | PDP — varyant seçimi, sepete ekleme, beden tablosu. |
| Cart.jsx | `/sepet` | Sepet. |
| Checkout.jsx | `/odeme` | İyzico 3DS ödeme. |
| Account.jsx | `/hesabim` | Üye paneli — siparişler, adresler. |
| MiuMiuTheme.jsx | `/tema`, `/tema/:slug` | Miu Miu tarzı premium tema (geliştirilmekte). |

---

## 6. Veritabanı (MongoDB) Şeması

> **Toplam:** 70+ collection. Aşağıdaki tablo en önemlilerini özetler.

### 6.1 Çekirdek Koleksiyonlar

| Collection | Belge Sayısı (anlık) | Açıklama |
|---|---|---|
| `products` | 503 | Tüm ürünler. Varyantlar `variants[]` array olarak gömülü. |
| `categories` | 38 | Kategori ağacı (parent_id, depth). |
| `attributes` | 52 | Sistem genel özellikleri. |
| `attribute_library` | 109 | Trendyol özellik kütüphanesi (cache). |
| `variant_colors` | 39 | Renk kütüphanesi. |
| `variant_sizes` | 36 | Beden kütüphanesi. |
| `variant_options` | 72 | Birleşik varyant kombinasyonları. |
| `size_tables` | 4 | Ölçü tablosu. |

### 6.2 Sipariş & Müşteri

| Collection | Belge Sayısı | Açıklama |
|---|---|---|
| `orders` | 2.353 | Siparişler (site + pazaryeri). |
| `customers` | 10.522 | Müşteri profilleri. |
| `users` | 10.486 | Admin + login kullanıcıları. |
| `member_groups` | 0 | Segmentler. |
| `blocked_customers` | 4 | Blok listesi. |
| `customer_risk` (router) | n/a | Risk skoru hesaplaması. |

### 6.3 Pazaryeri & Entegrasyon

| Collection | Belge | Açıklama |
|---|---|---|
| `category_mappings` | 29 | Lokal ↔ pazaryeri kategori eşleşmeleri. |
| `trendyol_categories` | 16 | Trendyol kategori cache. |
| `trendyol_attributes` | 74 | Trendyol attribute cache. |
| `trendyol_category_attributes` | 26 | Kategori-attribute ilişkisi. |
| `trendyol_sync_logs` | 689 | Push işlem logları. |
| `trendyol_stuck_queue` | 12 | Cache'de sıkışmış ürün retry kuyruğu. |
| `trendyol_questions` | 303 | Trendyol soru-cevap. |
| `trendyol_claims` | 2.801 | İade talepleri. |
| `hepsiburada_category_attributes` | 1 | HB cache. |
| `integration_logs` | 3.870 | Tüm 3rd party istek/cevap logları. |
| `cargo_logs` | 12 | Kargo etiket logları. |

### 6.4 Sistem & Operasyon

| Collection | Belge | Açıklama |
|---|---|---|
| `settings` | 17 | Modül bazlı ayar dokümanları (`main`, `trendyol`, `ticimax`, vb.). |
| `vault_secrets` | 1 | Fernet ile şifrelenmiş key store. |
| `roles` | 5 | RBAC rolleri. |
| `auth_audit_logs` | 511 | Giriş/çıkış log. |
| `error_logs` | 144 | Backend hata log. |
| `notification_logs` | 97 | Email/Push log. |
| `notification_templates` | 30 | Email/SMS şablonları. |
| `email_campaigns` | 2 | Kampanya çıkışları. |
| `attribution_sessions` | 296 | Müşteri kaynak takip. |
| `marketing_pixels` | 1 | Pixel ayarları. |
| `themes` | 1 | Tema config. |
| `page_blocks` | 7 | Sayfa tasarım blokları. |

### 6.5 Stok & Üretim

| Collection | Belge | Açıklama |
|---|---|---|
| `stock_movements` | 33 | Tüm stok in/out hareketleri. |
| `stock_alerts` | 0 | Kritik stok uyarıları. |
| `manufacturing` | 1 | Üretim planı belgeleri. |
| `production_plan` | 2 | Üretim sırası. |
| `product_costs` | 0 | Maliyet detayları. |

### 6.6 `products` Belge Şeması (Tipik)

```jsonc
{
  "id": "bb60d95b-f43e-4453-…",          // UUID
  "urun_karti_id": "2839",                // Ticimax UrunKartiID (parent)
  "name": "Velora Dantelli Saten Takım Haki",
  "slug": "velora-dantelli-saten-takim-haki",
  "color": "Haki",
  "stock_code": "FCSS2000002",
  "sku": "FCSS2000002",
  "brand": "FACETTE",
  "vendor": "FACETTE",
  "category_id": "55",
  "category_name": "Takım",
  "breadcrumb": "GİYİM > Üst Giyim > Takım",
  "price": 2800.0,                        // KDV dahil liste fiyatı
  "sale_price": 2380.0,                   // KDV dahil indirimli
  "member_price_1": 2800.0,
  "cost_price": 0.0,
  "vat_rate": 10.0,
  "description": "<p>…</p>",              // HTML destekli (Trendyol'a düz metin gönderilir)
  "variants": [
    { "size": "XS", "color": "Haki", "barcode": "8684483526152",
      "stock_code": "FCSS2000002", "urun_id": "8381",
      "stock": 26, "price": 2800.0, "sale_price": 2380.0 },
    …
  ],
  "images": ["https://static.ticimax.cloud/…/buyuk/...jpg", …],
  "attributes": { /* Trendyol attribute payload */ },
  "trendyol_brand_id": 975755,
  "trendyol_markup": 20,                  // SSOT: main.trendyol_markup
  "is_active": true,
  "is_published": true,
  "created_at": "2026-02-26T18:12:34Z",
  "updated_at": "2026-02-26T18:14:01Z"
}
```

---

## 7. 3. Parti Entegrasyonlar

| Servis | Kullanım | Auth Yöntemi | Durum |
|---|---|---|---|
| **Ticimax SOAP** | Ürün/Sipariş/Üye/Kategori senkron — `UrunServis.svc`, `SiparisServis.svc`, `UyeServis.svc`. | `UyeKodu` (WS API Key). | 🟢 Kısmi (UrunServis yetki kısıtlı; storefront scraper bypass). |
| **Trendyol REST** | Marketplace push, batch poll, ghost scan, soru-cevap, iade. | API Key + Secret + Supplier ID. | 🟢 Aktif (4 kademeli fallback). |
| **Hepsiburada** | Kategori cache + ürün push (planlı). | API Key + Secret. | 🟡 Hazır, kullanılmıyor. |
| **Temu** | Ürün push. | TEST KEY. | 🟡 Test. |
| **İyzico** | 3DS ödeme. | API Key + Secret Key. | 🟡 Test moddaki keyler. |
| **Doğan E-Dönüşüm** | E-Fatura + E-Arşiv + E-İrsaliye SOAP. | Connector WS. | 🟡 WSDL bağlı, key user-supplied. |
| **MNG Kargo** | Kargo etiketi. | Sk_secret_*. | 🟡 Test. |
| **İYS** | SMS/Email izin yönetimi. | User API Key. | 🔴 Mock (real key bekleniyor). |
| **Resend** | Transactional email. | `RESEND_API_KEY`. | 🔴 Boş (key gerekli). |
| **OpenAI / Claude / Gemini** | AI Asistan + chatbot + açıklama üretimi. | `EMERGENT_LLM_KEY` (universal). | 🟢 Aktif. |
| **Capacitor** | Mobil uygulama (Android/iOS). | n/a. | 🟢 Build hazır. |
| **Firebase FCM** | Push notification. | `FCM_SERVER_KEY` (yok). | 🔴 Plan. |
| **Cloudflare R2** | Object storage (görsel/dosya). | n/a. | 🟡 Roadmap. |

---

## 8. Ticimax Senkronizasyon Akışları

Ticimax SOAP servislerinden gelen veri, üç farklı yöntemle DB'ye işlenir:

### 8.1 Yöntem 1 — Sipariş & Üye Senkronu (SOAP)

- **Modül:** `ticimax_client.py` + `ticimax_order_parser.py`
- **Endpoint:**
  - `SelectSiparis(UyeKodu, f:WebSiparisFiltre, s:WebSiparisSayfalama)` → siparişler (filtre: tarih, marketplace exclusion, telefonlu).
  - `SelectSiparisUrun(UyeKodu, siparisId, iptalEdilmisUrunler)` → satır kalemleri.
  - `SelectUyeler(UyeKodu, filtre:UyeFiltre, sayfalama:UyeSayfalama)` → üyeler.
  - `SelectUyeAdres(UyeKodu, adresId, uyeId)` → adresler.
- **Önemli:** Tüm int filtre alanları için `-1` = "filtre yok"; `0` "değeri 0 olanları getir" demek. Yanlış kullanım sessizce boş döner.
- **Marketplace ayıklama:** `IsMarketplace`, `PazaryeriIhracat`, `Kaynak`, `PazaryeriButikId` post-filter ile temizlenir.

### 8.2 Yöntem 2 — Ürün Excel SSOT Resync

- **Script:** `scripts/ticimax_full_resync.py`
- **Akış:**
  1. Kullanıcı Ticimax admin panelinden "Ürün Excel Export" indirir (`TicimaxExport (X).xls`).
  2. Pandas/xlrd ile parse edilir, `URUNKARTIID`'ye göre gruplanır → her parent ürünü temsil eder.
  3. DB'de match: önce `urun_karti_id`, yoksa `stock_code + color`, son çare `name+color` regex.
  4. Tüm alanlar Excel'den yazılır (Single Source of Truth):
     - `urun_karti_id, stock_code, sku, vendor, description, breadcrumb, category_*`
     - `price, sale_price, member_price_1, cost_price, vat_rate`
     - `variants[].{size, color, barcode, stock_code, urun_id, price, sale_price}`
- **Son resync (Iter 75):** 1192 satır → 393 parent, 332 güncelleme + 61 yeni ürün, 0 orphan.

### 8.3 Yöntem 3 — Storefront Scrape (Yeni, Iter 77)

- **Script:** `scripts/ticimax_pull_via_storefront.py`
- **Neden:** Ticimax `UrunServis` SOAP servisi WS Key'e "Ürün Servis" yetkisi vermeyince **SelectUrun sessizce boş döner**.
- **Akış:**
  1. `https://www.facette.com.tr/sitemap/products/0.xml` → 281 ürün URL'i.
  2. URL slug sonundaki `-<ID>` suffix → hedef URUNKARTIID eşlemesi.
  3. Her ürün detay sayfasındaki `var productDetailModel = {…}` JS nesnesi brace-balance ile parse.
  4. `productDetailModel.products[]` (varyant + barkod + stok + fiyat + KDV), `productVariantData[]` (renk/beden), `productImages[]`, `breadCrumb` → DB schema'sına çevrilir, upsert edilir.
- **Avantaj:** API yetkisi gerektirmez, canlı storefront ile tamamen tutarlı.
- **Kullanım:**
  ```bash
  python3 scripts/ticimax_pull_via_storefront.py 2839 2840 2879 2889
  ```
- **Son test:** 23 ID, 68 varyant, 0 hata.

---

## 9. Trendyol Senkronizasyon Akışı (Fallback Sistemi)

Trendyol ürün push'unun başarısızlık olasılıkları çoktur:
- Kategori/attribute uyumsuzluğu
- Kâr oranı/fiyat hesabı yanlış
- Aynı barkod cache'de sıkışmış olabilir
- Self-conflict (aynı stockCode birden fazla varyantta)
- Cross-conflict (başka satıcının ürünü aynı barkodda)

Sistem bu sorunları **4 kademeli akıllı fallback** ile çözer:

```
1. POST /sapigw/suppliers/{id}/v2/products
   └─ Başarısız ise →
2. PUT  /integration/inventory/sellers/{id}/products/price-and-inventory
   (Sadece fiyat & stok güncelle — yeni ürün yaratmaya gerek yok)
   └─ Cross-conflict ise →
3. POST /sapigw/suppliers/{id}/v2/products/archive  (eski barkodları arşivle)
   + POST /sapigw/suppliers/{id}/v2/products  (yeniden gönder)
   └─ Hâlâ "Cache'de sıkışmış" ise →
4. → trendyol_stuck_queue collection'ına ekle
   → trendyol_retry_queue.py her 60 dk otomatik dener
```

### 9.1 Önemli Detaylar

- **Batch poll:** `IN_PROGRESS` ve `INPROGRESS` (boşluksuz) status normalize edilmiştir.
- **Parent vs varyant stockCode:** Sadece `productMainId = parent stock_code` Trendyol'a gönderilir; varyantların kendi stockCode'u kullanılmaz.
- **Kâr oranı SSOT:** `settings.main.trendyol_markup` esas alınır; ürün üzerindeki override sadece istisna.
- **Description:** HTML temizleme güçlendirildi (`<br>`, `</p>`, `<li>` → newline, entity unescape, paragraf koruma).
- **Image limit:** Trendyol için max 8 görsel.
- **vatRate default:** 20 (Ürünün `vat_rate` field'ı sağlanırsa onu kullanır).
- **cargoCompanyId:** 10 (MNG Kargo).

### 9.2 Ghost Scanner

- **Endpoint:** `POST /api/integrations/trendyol/ghost-scanner`
- **Amaç:** Trendyol satıcı paneli "Cache'de var" der ama ürün listemizde yok. Bu ürünleri tespit eder.
- **Aksiyon:** `archive-barcodes` ile temizler ya da stuck queue'ya alır.

---

## 10. Arkaplan Zamanlanmış İşler (Scheduler)

`server.py` lifespan içinde başlatılır:

### 10.1 Trendyol Retry Queue Loop

- **Modül:** `routes/trendyol_retry_queue.py`
- **Çalışma:** `asyncio.create_task(background_retry_loop)` → her 60 dk bir döngü.
- **Tetikleyici:** `trendyol_stuck_queue` collection'undaki bekleyen ürünleri 4 kademeli fallback'le yeniden dener.
- **Manuel tetikleme:** `POST /api/integrations/trendyol/retry-queue/run-now`.
- **Log:** `trendyol_sync_logs` collection'ı.

### 10.2 Havale Auto-Cancel (APScheduler)

- **Modül:** `scheduler.py` → `start_scheduler()`
- **Görev:** 48 saat ödenmemiş havale siparişlerini otomatik iptal et.
- **Frekans:** Saatlik cron.

### 10.3 (Roadmap) Ticimax Order Webhook

- Şu an manuel `POST /api/orders/sync-ticimax` ile.
- Hedef: webhook ile anlık DB'ye düşme + stoktan düşme.

---

## 11. Ortam Değişkenleri

### 11.1 `backend/.env` (Hassas)

```ini
# Veritabanı
MONGO_URL=mongodb://localhost:27017
DB_NAME=facette

# Auth
JWT_SECRET=********
CORS_ORIGINS=https://self-host-test.preview.emergentagent.com,…

# Emergent LLM (OpenAI/Claude/Gemini)
EMERGENT_LLM_KEY=sk-emergent-********

# İyzico
IYZICO_MODE=sandbox
IYZICO_API_KEY=*
IYZICO_SECRET_KEY=*
IYZICO_BASE_URL=https://sandbox-api.iyzipay.com

# Trendyol
TRENDYOL_MODE=production
TRENDYOL_API_KEY=ERet4fsWtkfxAPWPunGR
TRENDYOL_API_SECRET=*
TRENDYOL_SUPPLIER_ID=*

# Doğan e-Dönüşüm
GIB_MODE=*
GIB_USERNAME=*
GIB_PASSWORD=*
GIB_VKN=*
GIB_COMPANY_NAME=*

# Resend (Email — boş)
RESEND_API_KEY=
RESEND_FROM=

# Secrets Vault (Fernet)
SECRETS_MASTER_KEY=********

# Alert SMTP
ALERT_TO_EMAIL=*
ALERT_THROTTLE_SECONDS=300
ALERT_SMTP_HOST=*
ALERT_SMTP_PORT=587
ALERT_SMTP_USER=*
ALERT_SMTP_PASSWORD=*
ALERT_SMTP_FROM=*

# Cache (opsiyonel)
REDIS_URL=
CACHE_DEFAULT_TTL=300
```

### 11.2 `frontend/.env`

```ini
REACT_APP_BACKEND_URL=https://self-host-test.preview.emergentagent.com
WDS_SOCKET_PORT=443
ENABLE_HEALTH_CHECK=true
```

> ⚠ **Önemli:** `MONGO_URL`, `DB_NAME`, `REACT_APP_BACKEND_URL` **asla** silinmemeli/değiştirilmemelidir.

### 11.3 Ayrıca DB Tarafında Saklanan Keyler

`settings` collection içinde `_id`'leri:
- `ticimax`: `{ api_key }`
- `trendyol`: `{ api_key, api_secret }`
- `dogan_edonusum`: WSDL URL'leri
- `iyzico`, `mng`, `temu`, `hepsiburada`: API key/secret

---

## 12. Geliştirme & Çalıştırma

### 12.1 Lokal Geliştirme

Sistem Kubernetes pod içinde **Supervisor** ile yönetilir:

```bash
# Tüm servislerin durumu
sudo supervisorctl status

# Backend log
tail -f /var/log/supervisor/backend.*.log

# Frontend log
tail -f /var/log/supervisor/frontend.*.log

# Restart (sadece .env veya bağımlılık değişiminde)
sudo supervisorctl restart backend
sudo supervisorctl restart frontend
```

> Backend ve frontend **hot reload** ile çalışır; normal kod değişikliği restart gerektirmez.

### 12.2 Servis Portları

| Servis | İç Port | Dış Erişim |
|---|---|---|
| FastAPI | `0.0.0.0:8001` | `<REACT_APP_BACKEND_URL>/api/*` |
| React Dev | `0.0.0.0:3000` | `<REACT_APP_BACKEND_URL>/*` (non-/api) |
| MongoDB | `localhost:27017` | Sadece backend içinden. |

### 12.3 Bağımlılık Yönetimi

```bash
# Backend
pip install <paket>
pip freeze > backend/requirements.txt

# Frontend
yarn add <paket>     # NPM kullanmayın!
```

### 12.4 Test

```bash
# Backend pytest
cd /app/backend && pytest tests/

# Manuel API test
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
TOKEN=$(curl -s -X POST "$API_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@facette.com","password":"admin123"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s "$API_URL/api/products?page=1" -H "Authorization: Bearer $TOKEN"
```

### 12.5 Test Hesabı

```
Admin Paneli:
   URL    : <REACT_APP_BACKEND_URL>/admin/login
   Email  : admin@facette.com
   Şifre  : admin123
```

---

## 13. Sık Kullanılan Scripts (CLI)

Tüm scriptler `backend/scripts/` altında ve `python3 scripts/X.py [args]` ile çalıştırılır.

| Script | Amaç |
|---|---|
| `ticimax_full_resync.py` | Excel SSOT ile DB'yi senkronize et (332+ ürün). |
| `ticimax_pull_via_storefront.py` | URUNKARTIID listesinden storefront scrape ile çek. |
| `ticimax_pull_by_kart_ids.py` | SOAP ile çek (UrunServis yetkisi gerekir). |
| `sync_ticimax_variants.py` | Sadece varyant senkron. |
| `enrich_attrs_from_ticimax_master.py` | Attribute master'dan ürün özellikleri zenginleştir. |
| `replace_barcodes_by_name_size.py` | İsim+beden eşleşmesi ile barkod düzelt. |
| `fix_colors_from_name.py` | Ürün adından renk çıkar. |
| `split_multicolor_docs_by_kartid.py` | Tek dokümanda çok renkli ürünleri böl. |
| `merge_duplicate_product_docs.py` | Duplike ürün dokümanlarını birleştir. |
| `apply_default_attrs.py` | Default Trendyol attribute uygula. |
| `reset_admin.py` | Admin şifresini sıfırla. |

---

## 14. Güvenlik

### 14.1 Auth

- **JWT (HS256)** + `bcrypt` ile şifre hash.
- Token süresi: standart 24 saat.
- `auth_audit_logs` collection: her login/logout/şifre değişimi loglanır.
- `ip_blocklist`: Brute-force koruması.

### 14.2 RBAC

- 5 rol (`admin, manager, support, viewer, vendor`).
- `permissions.py` → endpoint bazlı yetki dekoratörü.
- Admin sayfası `/admin/kullanicilar` üzerinden yönetilir.

### 14.3 Secrets Vault

- Fernet (AES-128 CBC + HMAC-SHA256).
- `SECRETS_MASTER_KEY` env'den.
- DB'de `vault_secrets` collection — admin paneli `/admin/secrets-vault` ile yönetilir.

### 14.4 Webhook Güvenliği

- Trendyol/Hepsiburada webhook'larında signature doğrulama.
- İyzico callback'de `iyziEventType + signature` kontrolü.

### 14.5 CORS

- `CORS_ORIGINS` env'inden okunur, sadece tanımlı origin'ler kabul edilir.

### 14.6 Hassas Veri Kuralları

- Ticimax/Trendyol API key'leri sadece DB `settings` collection veya `vault_secrets`'te.
- Frontend'e **asla** ham API key gönderilmez.
- `.env` dosyaları git'te değil (`.gitignore`).

---

## 15. Bilinen Kısıtlar & Roadmap

### 15.1 Bilinen Kısıtlar

- **Ticimax UrunServis Yetkisi:** WS Key'e ürün servisi yetkisi yok → SelectUrun boş döner. Storefront scraper bypass mevcut.
- **`integrations.py` 6139 satır:** Modülerleştirme refactoru bekliyor.
- **İYS gerçek key yok:** SMS/Email izin senkronu mock.
- **Resend key boş:** Transactional email gönderilemiyor.
- **Firebase FCM yok:** Push notification roadmap'te.

### 15.2 Roadmap

**P1**
- [ ] Admin Panel "Ticimax Excel Upload" sayfası (drag-drop import).
- [ ] "Ticimax'tan ID ile Tek Ürün Çek" admin butonu (storefront scraper UI tetikleyici).
- [ ] Miu Miu Storefront Faz 2 & 3 (PLP, PDP, Sepet, Favoriler, Üye Ol/Giriş, İyzico Checkout).
- [ ] Cloudflare R2 Object Storage entegrasyonu.

**P2**
- [ ] Ticimax Order Webhook (anlık sipariş düşme + stok güncelleme).
- [ ] İYS gerçek key testleri.
- [ ] Firebase FCM ile push notification.
- [ ] `integrations.py` refactor (Trendyol modüllerini ayrı klasöre).
- [ ] Rich-text editör (TipTap/Quill) ile ürün açıklaması (HTML yazmadan format).

**P3**
- [ ] Multi-store (alt marka) desteği.
- [ ] Pazaryeri kâr raporlama gelişmiş analitik.
- [ ] AI-destekli kategori önerme.

---

## EK A — Yararlı Dosya Referansları

| Konu | Dosya |
|---|---|
| Trendyol push akışı | `/app/backend/routes/integrations.py` (1275–2150 satırları) |
| Description HTML temizleme | `/app/backend/routes/integrations.py` (1280–1303) |
| Trendyol retry loop | `/app/backend/routes/trendyol_retry_queue.py` |
| Ticimax SOAP wrapper | `/app/backend/ticimax_client.py` |
| Ürün modeli | `/app/backend/models.py` (ProductBase, ProductVariant) |
| Admin ürün ekranı | `/app/frontend/src/pages/admin/Products.jsx` |
| Storefront scraper | `/app/backend/scripts/ticimax_pull_via_storefront.py` |
| Excel SSOT resync | `/app/backend/scripts/ticimax_full_resync.py` |
| Lifespan & router include | `/app/backend/server.py` (103–202, 386–470) |

---

**Bu doküman canlıdır.** Yeni özellik veya entegrasyon eklendiğinde ilgili bölüm güncellenmelidir.
*Son üretim: 26 Şubat 2026 — Otomatik dokümantasyon (E1 agent).*
