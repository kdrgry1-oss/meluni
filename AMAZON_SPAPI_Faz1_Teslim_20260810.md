# Amazon SP-API — İlk Faz Teslim Paketi

**Proje:** Facette / Roof · **Tarih:** 10.08.2026
İşaret: ✅ çalışıyor · 🔧 kod hazır (ALLOW_WRITE=0 → dry-run; canlı test SKU sende) · ⚠️ kısmi · ❌ panel/ops (sende)

> **Kritik:** Yazma uçları varsayılan **KAPALI** (`AMAZON_ALLOW_WRITE=0`) → Amazon'a hiçbir şey yazılmaz, "ne gönderileceğini" döner (dry-run). Canlı yazma env ile açılır, anında geri alınır. PII/Restricted guard aynen korunur.

---

## A. Fonksiyonel Durum

| İşlev | Durum | Not |
|---|---|---|
| Stok sync | 🔧 kod hazır | `POST /api/amazon/spapi/inventory/{sku}` |
| Fiyat sync | 🔧 kod hazır | `POST /api/amazon/spapi/price/{sku}` |
| Listing management | 🔧 kod hazır | `PATCH /api/amazon/spapi/listing/{sku}` |
| PII'siz order sync | ✅ aktif | `GET /api/amazon/spapi/orders` |
| Finance sync | 🔧 kod hazır | `GET /api/amazon/spapi/finances` |

Canlı doğrulama (test SKU → Amazon başarılı response) **sende**: `AMAZON_ALLOW_WRITE=1` yapıp bir test SKU ile çağır; dönen `status`/`response`'u panelden/loglardan gör.

## B. Endpoint — Role Tablosu

| İşlem | API / Endpoint | Gerekli Role | Restricted? | Çalışıyor mu? |
|---|---|---|---|---|
| Stok gönderme | `PATCH /listings/2021-08-01/items/{seller}/{sku}` (fulfillment_availability) | Product Listing | Hayır | 🔧 dry-run (canlı test sende) |
| Fiyat gönderme | `PATCH /listings/2021-08-01/items/{seller}/{sku}` (purchasable_offer) | Product Listing | Hayır | 🔧 dry-run |
| Listing güncelleme | `PATCH /listings/2021-08-01/items/{seller}/{sku}` | Product Listing | Hayır | 🔧 dry-run |
| Sipariş özeti alma | `GET /orders/v0/orders` | Inventory and Order Tracking | Hayır (PII scrub'lı) | ✅ aktif |
| Finansal veri alma | `GET /finances/v0/financialEvents` | Finance and Accounting | Hayır | 🔧 kod hazır |
| Amazon fiyatını okuma | `GET /products/pricing/v0/price` | Pricing | Hayır | 🔧 kod hazır (read) |
| Offer / Buy Box alma | `GET /products/pricing/v0/listings/{sku}/offers` | Pricing | Hayır | 🔧 kod hazır (read) |

**Açık netleştirmeler:**
- **Pricing rolü gerçekten gerekli mi?** Kendi fiyatımızı **YAZMAK** için **HAYIR** — fiyat, Listings Items API (Product Listing rolü) ile yazılır. Pricing rolü **yalnız** rakip/Buy Box **fiyatı OKUMAK** için gerekir (Amazon fiyatı okuma + Offer/Buy Box). İlk fazda fiyat okuma zorunlu değilse Pricing rolü talep edilmeyebilir.
- **Product Listing rolü:** stok + fiyat + listing **yazma** (Listings Items PATCH).
- **Inventory and Order Tracking:** sipariş çekme (`/orders/v0/orders`).
- **Finance and Accounting:** `/finances/v0/*`.
- **İlk fazda Restricted endpoint / RDT var mı?** **HAYIR.** RDT üretilmiyor, BuyerInfo/ShippingAddress çağrılmıyor; Restricted yollar `AMAZON_ALLOW_RESTRICTED=0` ile 403.

## C. PII Durumu

| Kontrol | Sonuç | Kanıt |
|---|---|---|
| Restricted endpoint | **NO** ✅ | `_assert_restricted_allowed` + `AMAZON_ALLOW_RESTRICTED=0` (amazon_spapi.py) |
| RDT | **NO** ✅ | `/tokens` restricted marker → engelli; hiç RDT üretilmiyor |
| Buyer PII DB'de | **NO** ✅ | `orders` yalnız özet alanlar (id/status/tarih/tutar); `_scrub_pii` beklenmedik PII'yi söker |
| Buyer PII loglarda | **NO** ✅ | `_log_spapi_call` yalnız path+status+zaman; istek gövdesi/alıcı loglanmaz |

Beklenen ilk faz sonucu ile birebir: Restricted **NO**, RDT **NO**, Buyer PII DB **NO**, Buyer PII log **NO**.

## D. Panel / Altyapı Durumu

| Konu | Durum | Kanıt / Eksikse ne yapılmalı (ETA sende) |
|---|---|---|
| GitHub repo private | ✅ YES | Private repo; `.env` gitignore'da, takip edilmiyor, history'de yok |
| CI güvenlik taraması (gitleaks/CodeQL/deps) | ⚠️ PARTIAL | Workflow tanımlı VE doğru; ancak **GitHub Actions faturası/dakikası** yüzünden çalışamıyor (runner alınamıyor, duration 0). Kod hatası değil. Fatura açılınca çalışır. |
| DAST / infra vulnerability scan | ❌ NO | Aylık (30 gün) dinamik/infra tarama aracı yok. Kurulmalı (Öncelik-2). |
| Atlas network access | ❌ (sende) | Koddan görülemez. Panelden: `0.0.0.0/0` KAPAT, IP allowlist / private endpoint, DB user minimum yetki. |
| Atlas backup encryption / region | ❌ (sende) | Panelden: backup açık+encrypted, farklı region, retention süresi. |
| Railway region / TLS | ⚠️ | TLS 1.2+ ✅ (Railway HTTPS varsayılan). Region + prod erişim listesi + deploy log retention panelden doğrulanmalı. |
| Log retention (12 ay) | ⚠️ PARTIAL | SP-API audit logu (`spapi_call_logs`) otomatik silinmiyor (≥12 ay sağlanır). **Dikkat:** CAPI panelinde "30+ gün temizle" ucu var — o **pazarlama** logu, audit/SP-API değil. Formal 12-ay politikası yazılmalı; audit loglarına **otomatik-silme (TTL) EKLENMEMELİ**. |
| Backup restore testi | ❌ NO | Yapılmadı (izole ortama restore + RTO/RPO ölçümü). Öncelik-2. |

## E. Şifreleme / Secret (§10 teyidi)

- **Algoritma:** **AES-128-CBC + HMAC-SHA256 (Fernet)** ✅ (`security/crypto.py` — modül dokümanı bunu açıkça belirtir; AES-256-GCM DEĞİL).
- **Key storage:** `SECRETS_MASTER_KEY` env değişkeni (32 byte base64url) — **kaynak kod ve DB'den ayrı** ✅. Fallback JWT_SECRET türetme (production için önerilmez).
- **Secret store:** Railway environment (hosting secret-store).
- **Rotation:** `rotate_secret()` ile satır-bazı yeniden şifreleme mevcut ✅.
- **Audit:** kimlik-bilgisi kaydı/değişikliği `_log_spapi_call("config_save")` ile loglanır (secret **değeri** loglanmaz).

---

## Bu teslimde eklenen kod (additive, feature-flag'li, rollback kolay)
`backend/routes/amazon_spapi.py`:
- `_spapi_send` (PATCH/PUT/POST, `ALLOW_WRITE=0` → dry-run) + `_require_seller_id`
- `POST /inventory/{sku}`, `POST /price/{sku}`, `PATCH /listing/{sku}` (yazma)
- `GET /finances`, `GET /pricing/{sku}`, `GET /offers/{sku}` (okuma)
- `GET /write-status` (dry-run/live modu)
Mevcut OAuth, PII/Restricted guard, `_scrub_pii`, audit log **değişmedi**.

## Öncelik-1 kalan (sende — canlı/panel)
1. `AMAZON_ALLOW_WRITE=1` + test SKU ile stok/fiyat canlı doğrulama.
2. GitHub Actions faturasını aç → güvenlik taramaları yeşile dönsün.
3. Atlas: `0.0.0.0/0` kapat + IP allowlist/private endpoint + DB user min yetki.
4. Railway: region/erişim listesi/deploy-log doğrula.
5. 12-ay log retention politikasını yazılı hale getir (audit'e TTL ekleme).

## Öncelik-2 (Restricted faz öncesi)
Backup restore testi + quarterly plan · aylık infra/DAST scan · backup region/encryption doğrulaması · KMS/key-management · bağımsız penetration test.
