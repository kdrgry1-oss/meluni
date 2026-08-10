# 🚀 FACETTE COMMERCE OS
## Türkiye'nin Modaya Özel Akıllı E‑Ticaret İşletim Sistemi

> **"Tek bir merkezden tüm pazaryerlerini, lojistiği, müşteri iletişimini ve faturayı yönetin — yapay zekâ ile otomatikleştirin, satışlarınızı katlayın."**

---

## 🎯 NE YAPAR?

**Facette Commerce OS**, çok kanallı moda markaları için hem **e‑ticaret admin paneli**, hem **ERP**, hem de **müşteri deneyimi otomasyon platformu** olarak çalışan; Türkiye pazarının ihtiyaçlarına göre özel tasarlanmış uçtan uca bir işletim sistemidir.

Tek panel, **8+ pazaryeri**, **5+ kargo firması**, **e‑Fatura/e‑Arşiv**, **AI müşteri temsilcisi**, **mobil uygulama altyapısı** ve **kurumsal düzeyde siber güvenlik** ile geliyor.

---

## 💎 TEMEL VAAT

| Sorun | Bizim Çözümümüz |
|---|---|
| Trendyol, Hepsiburada, Temu, sitenizin siparişleri 5 farklı yerde | **Tek panelde** birleştirilmiş sipariş yönetimi |
| 230+ müşteri sorusu cevap bekliyor, insan zamanı yetmiyor | **AI asistan** %85+ güvenle otomatik cevaplıyor, riskli olanları size bırakıyor |
| Stok güncellemesi 6 farklı entegrasyonda manuel | **Tek tıkla** Ticimax → tüm pazaryerlerine senkron |
| İade/iptal süreçleri kaotik, gider pusulası unutuluyor | **Otomatik gider pusulası** + iyzico kısmi iade + Doğan e‑Dönüşüm |
| Cron job'lar çalışıyor mu, çalışmıyor mu, görünmüyor | **Otomasyon Durumu** paneli her senkronu canlı izliyor |
| OWASP/PCI‑DSS uyumu, brute force saldırıları | **Kurumsal güvenlik katmanı** + IP blocklist + audit log |

---

## 🔥 BAŞLICA YETENEKLER

### 1. 🛒 Çok Kanallı Sipariş & Stok Yönetimi
- **Trendyol** entegrasyonu (Sipariş, Stok, Fiyat, Soru‑Cevap, İade, Yorum)
- **Hepsiburada** & **Temu** (uyumlu sipariş + soru akışı)
- **Ticimax** çift yönlü senkron (Ürün, Kategori, Stok, Sipariş, Müşteri)
- **XML/CSV** ürün besleme
- **Kendi siteniz** (React storefront + checkout)
- 4 saatte bir otomatik stok senkronu — barkod/stok kodu öncelikli akıllı eşleştirme

### 2. 🤖 AI Müşteri Temsilcisi (Akıllı Soru Yanıtlayıcı)
- **Sohbet ile eğitim:** "S: Kargo kaç günde gelir? C: 2-3 iş gününde teslim edilir." yazıp bota öğretirsiniz, KB'ye otomatik kaydeder
- **Talimat sistemi:** "Müşteriye her zaman çok kibar ol" der, persona'ya işler
- **Toplu eğitim:** Geçmişte cevapladığınız 1000+ soruyu tek tıkla bilgi bankasına aktarın
- **Otomatik yanıt:** Confidence ≥ %85 olanları otomatik gönderir, altındakileri size onaya getirir
- **Yetersiz cevap dedektörü:** AI cevabı önce kalite kontrol, kısa/kaçamak cevapları kuyruklar
- **Çoklu kanal:** Trendyol Q&A, Hepsiburada müşteri mesajları, kendi siteniz, WhatsApp, Instagram (entegrasyon için hazır altyapı)
- **Backend:** OpenAI GPT‑5.2 + RAG (KB injection) + persona

### 3. 📦 Lojistik & Kargo Otomasyonu
- **DHL E‑Commerce** (eski adıyla MNG) webhook entegrasyonu
- **Aras, Yurtiçi, PTT, Hepsijet, Trendyol Express, Sürat, UPS** (jenerik altyapı)
- Kargo etiketi otomatik üretimi
- Webhook ile teslimat durumu real‑time güncelleme
- Failed transfer'lar için ayrı yönetim ekranı

### 4. 🧾 e‑Fatura & e‑Arşiv (Yasal Uyum)
- **Doğan e‑Dönüşüm** entegrasyonu (canlı + test)
- e‑Fatura, e‑Arşiv, e‑İrsaliye otomatik kesim
- VKN doğrulama (mükellef sorgu)
- **Gider Pusulası** otomatik üretimi (iadelerde)
- GİB uyumlu serileştirme

### 5. 💳 Ödeme & Kısmi İade
- **iyzico** entegrasyonu (canlı creds ile)
- **Stripe** desteği
- **Kısmi iade UI:** Kargo bedeli kesintisi ile esnek iade modeli (slider + canlı net hesap)
- 3D Secure + recurring payment hazır altyapı

### 6. 👥 Müşteri Segmentasyonu & Pazarlama (RFM)
- Otomatik **RFM segmentasyonu**: Champions, Loyal, At‑Risk, Hibernating vb.
- **Resend** entegrasyonu ile toplu e‑posta kampanyaları
- Segment‑bazlı kampanya hedeflemesi
- Otomatik abandoned cart e‑postaları
- Newsletter aboneliği yönetimi

### 7. 🛡️ Kurumsal Güvenlik (OWASP / PCI‑DSS)
- **JWT** strict (HS256, alg=none açığı kapalı, iat/iss/exp claim'leri)
- **bcrypt** rounds=12 şifre hashleme (legacy md5/sha1 reddediliyor)
- **NoSQL injection** önleme (`safe_str`, `is_safe_email` regex'leri)
- **Rate limiting** (slowapi): login 10/dk, register 5/dk, OTP 3/dk
- **Account lockout** (5 hata → 15 dk lock)
- **IP blocklist** (1 saatte 50+ fail → 24h ban) + manuel/permanent ban
- **Security headers:** CSP, HSTS, X‑Frame‑Options=DENY, Permissions‑Policy
- **Audit log** koleksiyonu (her login/register/password‑change kaydı)
- **Security Dashboard** admin paneli (KPI, top failed emails/IPs, kilitli hesaplar, IP ban listesi)

### 8. 📱 Mobil Uygulama Altyapısı (iOS & Android Ready)
- **Capacitor** & **React Native** uyumlu API katmanı
- `/api/app/version-check` (force update detection)
- Device registration (FCM/APNs push token)
- Runtime config (feature flags, branding) — uzaktan kontrol
- Admin paneliden push notification gönderme (broadcast / segment / kullanıcı)
- Deep linking (`facette://order/123`)

### 9. ⚙️ Otomasyon İzleme (Operasyonel Şeffaflık)
- **Cron Otomasyon Durumu** paneli — her senkronun son çalışma zamanı, başarı oranı, hata logu
- Ticimax stok sync (4h), Trendyol sipariş (2dk), abandoned cart cron'ları
- **Integration Logs**: her dış API çağrısı kaydı (kim, ne zaman, kaç ms, response)

### 10. 🎨 Yönetilebilir Storefront (Suud Collection Stili)
- **CMS bloklu sayfa tasarımı** (banner, half_banner, video, countdown, ürün listesi)
- Mobile/Desktop görünürlük ayarları (block bazlı)
- **Mega Menü** (delay'li hover, ürün galerisi)
- **Countdown Bar** (kampanya geri sayım, yönetilebilir)
- **Footer Designer** (CMS'den blok bazlı düzenleme)
- **Minimal Siyah/Beyaz Checkout** (yüksek dönüşümlü, 3 adımda biten)
- Müşteri hesap sayfası (kurumsal fatura alanları VKN/tax_office, şifre sıfırlama, OTP)
- PDP varyant kutucukları (renk swatch + beden)

---

## 🏗️ TEKNİK MİMARİ

```
┌─────────────────────────────────────────────────────────────┐
│  React 19 SPA  │  Capacitor (iOS/Android)  │  Storefront   │
└────────────────┴──────┬─────────────────────┴───────────────┘
                       │ HTTPS + JWT + CORS strict
┌──────────────────────▼──────────────────────────────────────┐
│            FastAPI (Python 3.11) + Motor (Async MongoDB)   │
│  - Strict JWT decode      - Rate limiter (slowapi)         │
│  - Security headers MW    - Audit log                      │
│  - Integration logs       - Background scheduler (cron)    │
└──┬─────────┬───────────┬──────────┬──────────┬──────────────┘
   │         │           │          │          │
┌──▼──┐  ┌───▼───┐  ┌────▼────┐  ┌──▼───┐  ┌───▼────┐
│Mongo│  │ Resend │  │ Iyzico  │  │ FCM  │  │ OpenAI │
│ DB  │  │SendGrid│  │ Stripe  │  │ APNs │  │ Claude │
└─────┘  └────────┘  └─────────┘  └──────┘  └────────┘
                            │
        ┌───────────────────┴───────────────────┐
        │ Trendyol │ Ticimax │ Hepsiburada │ Temu│
        │ DHL/MNG  │ Doğan e‑Dönüşüm │ XML feeds │
        └─────────────────────────────────────────┘
```

### Teknoloji Stack
- **Backend**: FastAPI, Motor (async MongoDB), Zeep (SOAP), APScheduler, Pydantic v2, slowapi, bcrypt, PyJWT
- **Frontend**: React 19, Tailwind CSS, Shadcn/UI, Lucide icons, Sonner (toast), Axios
- **Infra**: Kubernetes, MongoDB, Supervisor, Cloudflare, Hot reload geliştirme
- **AI**: OpenAI GPT‑5.2 (cevap üretimi), GPT‑5‑mini (kalite kontrol + intent), emergentintegrations
- **Mobil**: Capacitor (Faz 1) → React Native (Faz 2) yol haritası

---

## 📊 SAYILARLA SİSTEM

| Metrik | Değer |
|---|---|
| Backend dosya satır | 50,000+ (modüler) |
| API endpoint sayısı | 250+ |
| Mongo koleksiyonu | 60+ |
| Pazaryeri entegrasyonu | 4 (canlı) + 5 (uyumlu) |
| Kargo entegrasyonu | 9 |
| Cron job | 6 (her birinin canlı izlemesi var) |
| Test kapsamı (son 5 iterasyon) | 100+ test, 100% pass |
| Güvenlik audit log eventi (mevcut) | 130+ |
| Trendyol senkronlanmış soru | 303 |

---

## 💼 KİME UYAR?

### ✅ İdeal Müşteri
- **Yıllık ciro 5M₺ – 250M₺** arası moda/giyim/aksesuar markaları
- 3+ pazaryerinde satış yapan
- 5,000+ aktif müşteri segmenti olan
- Ticimax, IdeaSoft, T‑Soft gibi ana platform ile çalışıp **özel ihtiyaçları** olan
- "Generic SaaS yetmiyor, kendi iş kurallarımı işletmek istiyorum" diyen

### 🎯 Çözdüğü Acı Noktalar
1. **"3 farklı admin paneline giriyorum"** → Tek panel
2. **"Müşteri sorularına yetişemiyoruz"** → AI %85+ güvenle cevaplıyor
3. **"Stoklar 6 yerde farklı"** → Otomatik senkron
4. **"İade süreci kabus"** → Tek tıkla iyzico kısmi iade + gider pusulası
5. **"Hangi cron çalışıyor bilmiyorum"** → Otomasyon Durumu canlı izliyor
6. **"Brute force saldırı altındayız"** → IP blocklist + lockout
7. **"Mobil app yapıcaktık ama backend yetmiyor"** → Capacitor‑ready API katmanı

---

## 🚀 ROI HESABI (Örnek Müşteri)

**Senaryo:** Aylık 8,000 sipariş, 1,200 müşteri sorusu, 4 pazaryeri

| Süreç | Önce (manuel) | Sonra (Facette OS) | Tasarruf/ay |
|---|---|---|---|
| Müşteri sorusu yanıtlama | 60 saat (1 kişi) | 8 saat | **52 saat** |
| Stok senkron | 20 saat | otomatik | **20 saat** |
| Sipariş indirme/işleme | 30 saat | otomatik | **30 saat** |
| İade + gider pusulası | 24 saat | 4 saat | **20 saat** |
| **TOPLAM** | **134 saat** | **12 saat** | **🔥 122 saat/ay** |

→ **2 tam zamanlı çalışanın yükü** azaltıldı.
→ **Aylık 80,000₺ – 120,000₺ personel maliyeti tasarrufu**.

---

## 🛠️ GELİŞTİRME ROADMAP

### ✅ Tamamlanmış (v1.0)
- Tüm temel entegrasyonlar (Trendyol, Ticimax, DHL, Doğan, iyzico)
- AI müşteri temsilcisi (sohbetle eğitim + auto‑answer)
- Kurumsal güvenlik (JWT + bcrypt + IP blocklist + audit)
- Mobil API katmanı (Capacitor‑ready)
- RFM segmentasyon + Resend kampanyaları
- Otomasyon Durumu paneli
- Security Dashboard
- Kısmi iade UI

### 🔄 Yakın Vade (v1.1 — 2-4 hafta)
- Capacitor wrap → App Store + Play Store yayını
- FCM HTTP v1 (legacy → modern push API)
- Trendyol akıllı kampanya yöneticisi
- AI ile akıllı fiyat optimizasyonu
- WhatsApp Business API entegrasyonu

### 🌟 Orta Vade (v2.0 — 2-3 ay)
- React Native premium native app
- Live shop / canlı yayın alışverişi
- Voice AI (sesli müşteri temsilcisi)
- Predictive stock analytics
- Marketplace listing optimizer (AI başlık + açıklama üretici)

---

## 🔐 GÜVENLİK & UYUM

| Standart | Durum |
|---|---|
| **OWASP Top 10** | ✅ Tamamı kapsanıyor |
| **PCI‑DSS veri minimizasyonu** | ✅ Hiç kart datası store edilmiyor (iyzico tokenize) |
| **KVKK** | ✅ Veri export, sil, güncelle endpoint'leri |
| **GDPR uyumlu** | ✅ Audit log + retention politikası |
| **Brute force koruması** | ✅ Account + IP level dual layer |
| **NoSQL injection** | ✅ Pydantic + safe_str regex |
| **XSS / CSP** | ✅ Strict Content Security Policy |
| **HSTS** | ✅ 1 yıl + includeSubDomains |
| **Audit trail** | ✅ Her auth event collection'da |

---

## 📦 LİSANSLAMA / FİYATLAMA ÖNERİSİ

### Tier'lar (önerilen)
| Plan | Aylık | İçerik |
|---|---|---|
| **Starter** | 9,900₺ | 1 pazaryeri, 5,000 sipariş/ay, AI 200 yanıt/ay |
| **Growth** | 24,900₺ | 4 pazaryeri, 25,000 sipariş, AI 2,000 yanıt, mobile |
| **Enterprise** | 79,900₺+ | Sınırsız, white‑label, dedicated infra, SLA |
| **One‑time license** | 750,000₺ | Source code + 1 yıl destek |

### Implementation
- 4 hafta onboarding (creds bağlantı + custom branding + training)
- Aylık SLA opsiyonel (24/7 destek + bug fix)
- Custom feature development saat başı 1,500₺

---

## 🏆 NEDEN FACETTE OS?

| Rakip Çözüm | Eksik | Bizim Avantajımız |
|---|---|---|
| **Shopify Plus** | TR pazaryeri eksik, yüksek maliyet | Trendyol/Ticimax native, %70 daha ucuz |
| **WooCommerce** | Performans, güvenlik | FastAPI async, OWASP uyumlu |
| **IdeaSoft / T‑Soft** | Kapalı kod, AI yok | Açık ekstensible, AI built‑in |
| **Custom proje** | 12-18 ay süre, 2-5M₺ | Hazır, 4 haftada çalışır |
| **Channel manager (Logo/Mikro)** | Sadece sync, UX yok | Sync + admin UX + mobile + AI |

---

## 🎬 DEMO SENARYOSU (Sales Pitch)

> *"Şu anda Trendyol'da 230 sorunuz cevap bekliyor değil mi? Bunu açın bakalım..."*
>
> *(Demo açar → AI Asistan → Otomatik Yanıt → 'Test Çalıştır' → 10 saniye sonra 7 yüksek‑güvenli cevap üretir, 3'ünü insan onayına bırakır)*
>
> *"Şu anda 7 cevabın gönder butonuna tek tıklamayla 230'da çalışacak. Yarın bu sayı 800 olacak ve siz her gün 10 dakikada bitireceksiniz. **Müşteriniz cevabını 12 saatte değil, 30 saniyede alacak.** Trendyol algoritması bunu görüyor ve sıralamada size yukarı atıyor — direkt satış demek bu."*

---

## 📞 İLETİŞİM

**Müşteri Demo & Pricing**: hello@facette.com.tr
**Teknik Sorular**: tech@facette.com.tr
**Partner Programı**: partners@facette.com.tr

---

*Bu doküman Facette Commerce OS v1.0 (Mayıs 2026) sürümü için hazırlanmıştır.*
*🇹🇷 Türkiye'de tasarlandı, küresel ölçeğe hazır.*
