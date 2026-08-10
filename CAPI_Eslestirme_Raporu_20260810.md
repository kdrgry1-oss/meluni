# CAPI Eşleştirme (Match Quality / EMQ) Raporu

**Proje:** Facette · **Tarih:** 10.08.2026
**Kapsam:** Server-side Conversions API — Meta + TikTok. Amaç: her siparişin (Purchase) reklam platformuyla **eşleşme (matching) kalitesini** ve hangi sinyallerin gönderildiğini göstermek.

> **Not:** Ham e-posta/telefon/IP değeri bu raporda YOK. Sinyaller Meta/TikTok'a **SHA-256 hash'li** gider; bu rapor yalnız "var/yok" ve kaynak bilgisi verir. **Canlı yüzdeler** panelden okunur: **CAPI Loglar → 🔎 Denetim → provider seç** (aşağıdaki §5 tablosunu oradan doldur).

---

## 1. Temel eşleştirme mekanizması

- **Tek olay kimliği (dedup):** Browser pixel ve server (CAPI) aynı Purchase için **`event_id = order_number`** kullanır → platform iki kaydı **tek** sayar (çift sayım yok).
- **Sipariş başına tek server Purchase:** atomik `capi_purchase_sent` bayrağı — webhook + callback + admin aynı anda tetiklese bile yalnız biri gönderilir.
- **Tüm PII hash'li:** e-posta/telefon/external_id normalize edilip **SHA-256** ile gönderilir (`hash_utils`). IP ve UA ham (platform kuralı gereği ham istenir).

## 2. Meta eşleştirme sinyalleri

| Sinyal | Meta alanı | Kaynak | Hash? | Durum |
|---|---|---|---|---|
| E-posta | `em` | sipariş / adres e-postası | SHA-256 | ✅ |
| Telefon | `ph` | sipariş / adres telefonu (E.164) | SHA-256 | ✅ |
| Dış kimlik | `external_id` | üye: customer/user id · misafir: `facette_sid` | SHA-256 | ✅ |
| Facebook browser id | `fbp` | order `click_ids.fbp` → attribution session fallback | ham | ✅ |
| Facebook click id | `fbc` | order `click_ids.fbc` → fallback | ham | ✅ |
| IP | `client_ip_address` | gerçek client IP (CF-Connecting-IP / IPv6) | ham | ✅ |
| User-Agent | `client_user_agent` | gerçek tarayıcı UA | ham | ✅ |

- **`_fbp` düşük çıkarsa:** kaynak dağılımı panelde görünür (order snapshot vs attribution fallback vs none). ViewContent tarafında `_fbp` için 1200 ms sınırlı bekleme var (event_time kaymadan).

## 3. TikTok eşleştirme sinyalleri

| Sinyal | TikTok alanı | Kaynak | Hash? | Durum |
|---|---|---|---|---|
| E-posta | `email` | sipariş e-postası | SHA-256 | ✅ |
| Telefon | `phone_number` | sipariş telefonu (E.164) | SHA-256 | ✅ |
| Dış kimlik | `external_id` | üye id · misafir `facette_sid` | SHA-256 | ✅ |
| TikTok click id | `ttclid` | reklam URL'i → attribution → order | ham | ✅ (yalnız reklam tıklamalı) |
| TikTok cookie | `ttp` | `_ttp` cookie → order | ham | ✅ |
| IP | `ip` | gerçek client IP | ham | ✅ |
| User-Agent | `user_agent` | gerçek tarayıcı UA | ham | ✅ |

- **Organik Purchase'ta `ttclid` olmaması normaldir** (yalnız reklam tıklamasında dolar).
- Alan-bazlı **rollback flag** altyapısı mevcut (bir sinyali kapatmak gerekirse).

## 4. Kaynak (source-path) — eksik sinyal nerede yoğunlaşıyor?

Her Purchase şu akışlardan birinden gönderilir: `iyzico_webhook`, `iyzico_callback`, `iyzico_card`, `admin_status_confirmed`, `offline_payment_confirm`, `recovery_admin`. Panelde **source-path dağılımı** her akış için email/fbp(ttclid)/external_id sinyal sayısını gösterir → eksik eşleşme belirli bir akışta mı yoğunlaşıyor görülür.

## 5. CANLI ORANLAR — panelden doldurulacak

**CAPI Loglar → 🔎 Denetim → provider = Meta / TikTok**, "Son 100 Purchase":

| Sinyal | Meta % | TikTok % |
|---|---|---|
| has_email | … | … |
| has_phone | … | … |
| has_external_id | … | … |
| has_fbp / has_ttclid | … (fbp) | … (ttclid) |
| has_fbc / has_ttp | … (fbc) | … (ttp) |
| has_ip | … | … |
| has_user_agent | … | … |
| API başarılı (ok) | … | … |
| IPv4 / IPv6 | … / … | … / … |

Sağlık (seçili pencere): toplam event, hata oranı, retry, dead-letter, ViewContent hacmi — aynı ekranda.

## 6. Yorum kılavuzu (oranlar gelince)

- **email/phone yüksek (≥%90)** olmalı — server sipariş verisinden geldiği için genelde tam. Düşükse: siparişte e-posta/telefon eksik veya normalize sorunu.
- **external_id ~%100** beklenir (üye id ya da misafir `facette_sid`). Düşükse misafir fallback toggle'ını kontrol et (`marketing.capi_guest_external_id` açık mı).
- **fbp/ttp orta-yüksek**, **fbc/ttclid düşük** normaldir (yalnız reklam tıklamalı trafikte dolar).
- **ip/ua ~%100** olmalı.
- **ok (API başarılı) ~%100** olmalı; düşükse token/pixel/dataset hatası → `response` gövdesine bakılır.

---

### Sonuç
Eşleştirme mimarisi **eksiksiz kurulu**: her iki platformda da email/phone/external_id/ip/ua + platforma özel click id'ler gönderiliyor, hepsi doğru hash'leniyor, dedup `event_id=order_number` ile garantili. **Gerçek eşleşme kalitesini** görmek için §5 tablosunu panelden doldur (veya ekran görüntüsü at) — sayılara bakıp zayıf sinyal varsa en küçük düzeltmeyi çıkarırım.
