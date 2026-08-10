# TikTok CAPI / EMQ — Production Doğrulama

**Proje:** Facette · **Tarih:** 10.08.2026
**Yöntem:** Kod tabanı incelendi (TikTok adapter + ortak CAPI altyapısı). **Canlı production DB/log ve canlı test-siparişi bu ortamdan mümkün değil** → runtime doğrulaması için TikTok'u da kapsayan salt-okunur denetim aracı **provider seçmeli** hale getirildi (event akışına dokunmaz — §0 güvenliği korunur). Sayıları panelden okuyacaksınız.

İşaret: ✅ çalışıyor (kodla kanıtlı) · ⚠️ kısmi / canlı doğrulama gerek · ❌ sorun.

---

## Production CAPI  — ⚠️ (araç hazır, sayı panelden)

- **TikTok server Purchase logu var mı / doğru pixel / ok / status / response:** Bu ortamdan canlı DB okunamıyor. **CAPI Loglar → 🔎 Denetim → provider: TikTok** (veya `GET /api/marketing-pixels/capi/logs?provider=tiktok&event_name=purchase`) ile birebir görürsünüz.
- **Kod tarafı ✅:** TikTok adapter mevcut ve doğru: endpoint `open_api/v1.3/event/track/`, `purchase → CompletePayment`, `Access-Token` header, `event_source_id = pixel_id`. `ok = (HTTP 200 ve response.code == 0)` olarak değerlendiriliyor (`tiktok.py:159`). Loglama ortak orchestrator'da (`capi_event_logs`, `match_signals` dahil).
- **Log yoksa kontrol:** pixel'in `capi_enabled` + `access_token` + `is_active` alanları (production config — panelden). Adapter kodu hazır; eksikse gönderim hiç denenmez.

```text
TikTok server Purchase logu var mı: → Denetim panel (provider=TikTok)
Son örnek request başarılı mı (ok): → panel
ok / status / response: capi_event_logs.ok/status/response
Doğru pixel/dataset mi: capi_event_logs.pixel_doc_id (panelde)
```

## Kontrollü test (A: organik, B: reklam tıklamalı)  — ⚠️ (canlı sipariş gerek)

Bu ortamdan **canlı sipariş oluşturamam**. Kod akışı hazır:
- Browser event → `dataLayer.js` (fbq/TikTok pixel anında), server mirror `/capi/event`.
- Server Purchase → `orders.py::dispatch_purchase_capi` tüm aktif provider'lara fan-out (Meta + TikTok birlikte).
- **Aynı `event_id = order_number`** hem browser hem server'da (`orders.py:1855`) → TikTok native dedup.
- **Reload/webhook/callback duplicate:** engelli (aşağıda §dedup).

Test edeceğiniz: panelde iki sipariş sonrası provider=TikTok Denetim'de `total_server_purchase` artışı + `ok`.

## Browser ↔ Server dedup  — ✅ (kod)

- **Sipariş başına tek server Purchase:** atomik `find_one_and_update({capi_purchase_sent:{$ne:true}})` (`orders.py:1776`). Webhook + callback + admin aynı anda tetiklese bile yalnız biri gönderir.
- **`event_id = order_number`** browser + server ortak → TikTok aynı siparişi iki Purchase saymaz.
- Yeniden yazılmadı, yalnız doğrulandı (§0 gereği).

## Match signals (PII'siz)  — kod ✅ / runtime ⚠️

`tiktok.py::_build_user` alan eşlemesi (hepsi mevcut):
| Sinyal | TikTok alanı | Durum |
|---|---|---|
| Email | `email` (SHA-256) | ✅ kod · runtime ⚠️ panel |
| Phone | `phone_number` (E.164+SHA-256, `hash_utils`) | ✅ kod · runtime ⚠️ |
| External ID | `external_id` (üye=customer/user id, misafir=`facette_sid`/`attribution_session_id`) | ✅ kod · runtime ⚠️ |
| TTCLID | `ttclid` | ✅ kod (yalnız reklam tıklamalı) |
| TTP | `ttp` (cookie `_ttp`) | ✅ kod · runtime ⚠️ |
| IP | `ip` = `client_ip_address` (gerçek client IP, CF-Connecting-IP/IPv6) | ✅ kod |
| User-Agent | `user_agent` = `client_user_agent` (gerçek tarayıcı UA) | ✅ kod |

- **IP/UA:** server IP / generic UA DEĞİL — order'da saklanan gerçek `customer_ip` + `user_agent` kullanılıyor.
- **Phone normalizasyonu:** §6 gereği **DEĞİŞTİRİLMEDİ**. Server E.164+SHA-256 (`hash_utils`). Browser/server aynı normalize değeri mi → canlı matching sonucu panelden bakılmalı (⚠️). `+90` düzeltmesi ancak canlı test gerektiğini gösterirse, **feature-flag altında + browser/server birlikte** yapılır. (TikTok adapter'da alan-bazlı rollback flag altyapısı zaten var: `field_flags`, `tiktok.py:35-47`.)
- **TTCLID capture ✅:** reklam URL'inden (`attribution.js:61`) → attribution session → order snapshot/`click_ids` → server event (`build_user_data(ttclid=...)`). `_ttp` cookie'den (`attribution.js:79`). Organik Purchase'ta TTCLID olmaması **hata değil**.

## Purchase payload  — ✅ (kod)

`tiktok.py::_build_properties`:
- `value` numeric (float) ✅
- `currency = "TRY"` (varsayılan) ✅
- `contents[]`: `content_id`, `content_name`, `quantity` (int), `price` (float), `sku` ✅
- `content_type = "product"` ✅
- `order_id` ✅
- ek: `coupon_code`, `discount`, `shipping`, `tax`, `payment_method` (varsa)

## Sonuç

| Başlık | Durum |
|---|---|
| TikTok adapter (payload/user/dedup/event map) kod | ✅ Çalışıyor |
| Production'da başarılı TikTok server Purchase kanıtı | ⚠️ Panelden doğrulanmalı (Denetim → TikTok) |
| Kontrollü canlı test (browser+server, aynı event_id, duplicate yok) | ⚠️ Canlı sipariş gerekiyor |
| Match signal alan eşlemesi (email/phone/ext_id/ttclid/ttp/ip/ua) | ✅ kod · ⚠️ runtime oranları panelden |
| TTCLID reklam→order→server taşınması | ✅ kod |
| Purchase payload (value/currency/contents) | ✅ Çalışıyor |
| Phone normalizasyon parite | ⚠️ Canlı matching ile doğrula (değiştirilmedi) |
| Regresyon (checkout/payment/Meta/attribution) | ✅ Yok — yalnız salt-okunur araç eklendi |

### Bu aşamada yapılan tek değişiklik (§0 uyumlu, additive, rollback kolay)
- `/api/marketing-pixels/capi/audit` **provider parametreli** (meta/tiktok/…); coverage'a `ttclid` + `ttp` eklendi.
- Panel **CAPI Loglar → 🔎 Denetim**'e **provider seçici** eklendi; TikTok seçilince fbp yerine ttclid/ttp kartları.
- Event gönderme, dedup, phone normalizasyon, attribution, Meta ortak kodu **DEĞİŞMEDİ**.
- Rollback: bu iki dosya değişikliğini geri almak yeterli (event akışına dokunmadığı için risk ~0).

### Kabul kriterine göre eksik kalan (yalnız canlı adımlar)
Şunlar için **senin bir test siparişi** vermen / paneli açman gerekiyor (kod hazır, ben canlı çalıştıramıyorum):
1. Production'da TikTok'a başarılı server Purchase (Denetim → TikTok → `ok`).
2. Kontrollü Purchase'ta browser+server aynı `event_id`, duplicate yok.
3. Reklam tıklamalı testte TTCLID server Purchase'a taşınmış (Denetim → `has_ttclid` > 0).
4. email/phone/external_id/ttp/IP/UA oranları beklenen seviyede.

Panelde bir sorun görürsen (ör. `ok=false`, ya da `has_phone` çok düşük) ekran görüntüsü at — TikTok `response` gövdesinden kesin nedeni + en küçük düzeltmeyi + rollback'i çıkarırım.
