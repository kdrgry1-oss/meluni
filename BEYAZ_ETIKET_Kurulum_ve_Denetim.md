# Beyaz Etiket — Yeni Marka Kurulum Rehberi + Sabit Değer Denetimi

**Sistem:** Roof e-ticaret platformu (Facette referans kurulumu) · **Tarih:** 10.08.2026
**Amaç:** Bu sistemi **Facette'e özel hiçbir sabit değere dokunmadan**, yalnız ayarlardan yeni bir marka için çalıştırmak.

---

## 1. Mimari — firma kimliği tek kaynaktan gelir

Firma-özel her değer `backend/company.py` üzerinden **öncelik zinciriyle** birleşir:

```
Gömülü FACETTE varsayılanı  →  env (SITE_URL)  →  settings.main üst-alanları  →  settings.main.company_info (admin)
```

En özel olan (admin'in panelden girdiği `company_info`) kazanır. Tüketiciler (e-posta kabuğu, e-fatura tedarikçi, GPSR üretici, footer, site linkleri) bu tek kaynaktan okur. **E-posta kabuğu** (logo/wordmark/sosyal footer) her gönderimde `set_brand()` ile dinamik gelir. **CORS** env ile (`CORS_ORIGINS`), sabit değil. **Backend URL** frontend'de `REACT_APP_BACKEND_URL` env placeholder'ı (www→api fallback'li).

Sonuç: Yeni marka için **kod değiştirmeden** çoğu şey ayardan geçer. Kalan sabit sızıntılar §4'te.

## 2. Yeni marka kurulum adımları

### 2.1 Deployment (env değişkenleri)
Yeni markanın Railway (backend) + Cloudflare (frontend) ortamında:

| Env | Ne için | Örnek |
|---|---|---|
| `SITE_URL` | Storefront taban URL (e-posta/feed linkleri) | `https://marka.com` |
| `REACT_APP_BACKEND_URL` | Frontend → backend API | `https://api.marka.com` |
| `CORS_ORIGINS` | İzinli origin'ler (virgüllü) | `https://marka.com,https://www.marka.com` |
| `SECRETS_MASTER_KEY` | Sır vault ana anahtarı (32 byte base64url) — **yeni markaya YENİ üret** | `Fernet.generate_key()` |
| `MONGO_URL` / DB | Yeni markanın kendi veritabanı | (ayrı Atlas cluster/DB) |
| `JWT_SECRET` | Oturum imzası — yeni üret | — |
| `AMAZON_ALLOW_RESTRICTED` / `AMAZON_ALLOW_WRITE` | Amazon guard'ları | `0` (ilk faz) |

> **Kritik:** Her marka **ayrı DB + ayrı SECRETS_MASTER_KEY + ayrı JWT_SECRET** kullanmalı (veri ve sır izolasyonu).

### 2.2 Panel → İşletme Ayarları → Şirket Bilgileri (`company_info`)
Admin panelden doldur (hepsi `company.py`'ye akar):
`store_name, company_name, logo_url, site_url, website, contact_email, contact_phone, whatsapp, address, city, tax_office, tax_number, iban, instagram, facebook, x, tiktok`

### 2.3 Entegrasyon kimlikleri (panelden, şifreli vault'a)
Her biri admin panelden girilir; sır'lar Fernet ile şifrelenir:
- **Ödeme:** iyzico (API key/secret)
- **Kargo:** MNG (kullanıcı/şifre)
- **SMS:** NetGSM (kullanıcı/şifre/başlık) — İYS başlığı markaya ait olmalı
- **e-Fatura:** Doğan e-Dönüşüm
- **Pazaryerleri:** Trendyol, Hepsiburada, Temu (satıcı API anahtarları)
- **Amazon SP-API:** client_id/secret + OAuth consent
- **CAPI/Pixel:** Meta, TikTok, Google Ads, Pinterest, Snapchat (pixel id + access token) — **markanın kendi** pixel'leri
- **WhatsApp Business Cloud:** phone_number_id + kalıcı token + verify_token (markaya özel)
- **E-posta:** SES veya SMTP (from_name/from_email markaya ait)

### 2.4 İçerik & tasarım (panelden)
- Tema/renk, banner/slider, footer, menü, popup — Tasarım modülünden
- CMS sayfaları (KVKK, gizlilik, iade, SSS) — markaya göre düzenle
- E-posta şablonları + bildirim (SMS) şablonları — markaya göre (varsayılanlar §4'te sabit marka içerir!)

## 3. Zaten ayardan gelen (dokunma gerekmez)

- E-posta kabuğu (logo/wordmark/sosyal footer) — her gönderimde dinamik
- CORS (env), backend URL (env), SITE_URL bağlı linkler
- e-Fatura tedarikçi adı/web, GPSR üretici (company_info dolunca)
- Fatura tedarikçi alanları, `from_name` (ayar dolunca)
- Ürün markası/analytics affiliation (ürün markası varsa onu kullanır)

## 4. Bilinen sabit "Facette" sızıntıları (yeni markada düzeltilmeli)

Bunlar `company.py`'yi atlayıp sabit yazıyor → yeni markanın müşterisine **Facette** görünür. Öncelik sırasıyla:

| # | Yer | Sabit | Etki | Kaynak alınmalı |
|---|---|---|---|---|
| 1 | `frontend/public/index.html` | title/meta/OG/Twitter/JSON-LD → FACETTE, facette.com.tr | SEO + sosyal paylaşım + ilk açılış | build env / backend settings |
| 2 | `routes/notifications.py` (SMS varsayılanları) | her sipariş SMS'i "Facette" imzalı, iptal "destek@facette.com" | **En yüksek hacim** | `company.store_name/contact_email` |
| 3 | `routes/iys.py` | OTP SMS "Facette dogrulama kodunuz" | Her giriş/kayıt | `company.store_name` |
| 4 | `order_statuses.py` | sipariş e-postaları "FACETTE'i tercih…" | Teslim/iade e-postaları | `{store_name}` |
| 5 | `routes/footer_template.py` (default) | info@facette, telefon, IG, "Facette Kulübü", © Facette | Footer (ayar boşsa) | `company_info` |
| 6 | `routes/orders.py` (fatura/etiket) | fatura wordmark "FACETTE" + kargo etiket logosu | Fatura + kargo etiketi | `company.store_name/logo_url` |
| 7 | `routes/mobile.py` | App Store/Play URL, destek e-posta, deep-link "facette" | Mobil uygulama config | yeni `settings.mobile` |
| 8 | `OrderSuccess.jsx`, `GizlilikPolitikasi.jsx` | destek@/kvkk@/security@facette.com.tr | Sipariş onayı + gizlilik | `company.contact_email` |
| 9 | `routes/auth.py` | doğrulama/şifre-sıfırlama e-posta konu+metin "FACETTE" | Kimlik e-postaları | `company.store_name` |
| 10 | `frontend/src/lib/img.js` + `routes/payment.py` | CDN host `cdn.facette.com.tr`; ödeme origin allowlist facette.com.tr | Görsel optimizasyonu + ödeme redirect | `company.site_url`/env |
| — | `analytics_extra.py` (Merchant feed), `email_marketing.py` (wordmark), `newsletter.py`, `Home.jsx`/`FAQ.jsx` IG linki | çeşitli "Facette" | Google feed + pazarlama + storefront metni | `store_name`/`instagram` |

**Zararsız (dokunma gerekmez):** localStorage/cookie anahtarları (`facette_sid` vb.), yorumlar, admin placeholder'ları, test user-agent'ları, bootstrap admin kimliği, JWT issuer, vault salt — müşteriye görünmez.

## 5. Kurulum kontrol listesi (yeni marka)

- [ ] Ayrı DB + yeni `SECRETS_MASTER_KEY` + `JWT_SECRET`
- [ ] `SITE_URL`, `REACT_APP_BACKEND_URL`, `CORS_ORIGINS` env
- [ ] Panel → Şirket Bilgileri (`company_info`) tam dolduruldu
- [ ] Entegrasyon kimlikleri (iyzico/MNG/NetGSM/Doğan/pazaryeri/CAPI/WhatsApp/Amazon) markaya ait
- [ ] E-posta from_name/from_email + SMS başlığı (İYS) markaya ait
- [ ] CMS sayfaları (KVKK/gizlilik/iade/SSS) markaya göre
- [ ] Bildirim (SMS) + e-posta şablonları markaya göre (varsayılan marka metnini ez)
- [ ] §4 sabit sızıntıları düzeltildi (aşağıdaki "kod düzeltmesi" fazı)
- [ ] Deploy sonrası smoke: site/giriş/ödeme/sipariş/e-posta/SMS markayı doğru gösteriyor

---

## Öneri: iki aşama
1. **Ayar tarafı** (bu rehber) — kod değiştirmeden çoğu şey geçer.
2. **Kod düzeltmesi** (§4) — 17 sabit sızıntıyı `company.py`'den okuyacak şekilde düzeltmek. Bu, sistemi **tam** beyaz-etiket yapar (yeni markaya hiç "Facette" sızmaz). İstenirse öncelikli 10 maddeyi tek seferde düzeltip tek marka değişkenine bağlarım.
