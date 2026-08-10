# META CAPI — Post-Deploy Verification (Kod Denetimi)

**Proje:** Facette · **Tarih:** 10.08.2026
**Yöntem:** Kod tabanı (backend + frontend) satır-satır incelendi. **Canlı production DB/log erişimi bu ortamda yok** — bu yüzden gerçek runtime sayıları (son 50–100 Purchase, hata oranı vb.) kodun içinden üretilemez. Bunun yerine, o sayıları **production'a karşı** hesaplayan **salt-okunur admin denetim aracı kuruldu** (event akışına dokunmaz — freeze korunur): `GET /api/marketing-pixels/capi/audit` + panelde **CAPI Loglar → 🔎 Denetim** sekmesi.

İşaretler: ✅ sistemde var/doğru · 🔧 yoktu → kuruldu · ❌ yapılamıyor (+ açıklama).

---

## 1. `facette_sid` persistence  — ✅

| Soru | Cevap | Kanıt |
|---|---|---|
| Storage | **localStorage** (`facette_sid`) + bellek aynası `window.__FACETTE_SID__`. Cookie DEĞİL. | `attribution.js:16,105` |
| TTL | **Yok** (localStorage kalıcı; bu anahtara expiry kodlanmamış) | `attribution.js` |
| Yeni session'da korunuyor mu | **Evet** — localStorage, sessionStorage değil; tarayıcı yeniden açılınca aynı ID | `attribution.js:105` |
| Rotate koşulu | **Sadece yoksa** — client mevcut değeri gönderir; server yalnız `session_id` boşsa yeni UUID üretir | `attribution.py:163` |
| Browser/server eşleşme | Aynı client değeri **hem** pixel/`/capi/event` mirror'ına **hem** siparişe taşınır; server cookie'den OKUMAZ, sipariş snapshot'ından okur | `Checkout.jsx:560,762`, `orders.py:1815-1821` |
| Order'a taşınma | Payload `attribution_session_id` → `order.attribution.session_id` | `orders.py:1044-1046` |
| Ham ID loglanıyor mu | **Hayır** — logda yalnız `external_id: bool`. Meta'ya **SHA-256 hash'li** gider | `orchestrator.py:112`, `hash_utils.py:229` |

```text
Storage: localStorage (key: facette_sid) + window.__FACETTE_SID__
TTL: yok (kalıcı)
Yeni session'da korunuyor mu: evet
Rotate koşulu: yalnızca değer yoksa (localStorage temizlenirse)
Browser/server eşleşme yöntemi: aynı client değeri; siparişe snapshot (cookie'den okunmaz)
Order'a taşınma yöntemi: order.attribution.session_id
Ham ID loglanıyor mu: hayır (SHA-256 hash'li gönderilir)
```
> ⚠️ Tek not: misafir external_id, `marketing.capi_guest_external_id` (varsayılan AÇIK) toggle'ına bağlı. Kapatılırsa server misafirde external_id göndermez → browser/server asimetrisi olabilir. Şu an açık.

---

## 2. Son 50–100 server Purchase — PII'siz coverage audit  — 🔧 KURULDU

Kodda coverage tablosu **yoktu**; ama ham veri zaten PII'siz saklanıyor (`capi_event_logs.match_signals` — booleans + `ip_version`, ham değer yok). Bu tabloyu **son N Purchase** üzerinden hesaplayan salt-okunur uç kuruldu.

- **Panel:** CAPI Loglar → **🔎 Denetim** → üstteki coverage kartları (`has_email/phone/fbp/fbc/external_id/ip/user_agent`, `Meta API başarılı`, `IPv4/IPv6`).
- **API:** `GET /api/marketing-pixels/capi/audit?provider=meta&sample=100` → `purchase_coverage`.
- **`_fbp` kaynak dağılımı** da eklendi (`fbp_source`): `order_snapshot` / `attribution_fallback` / `none` — orders JOIN ile (`click_ids.fbp` snapshot mı, attribution session fallback mı).

❌ **Gerçek sayıları bu rapora yazamıyorum** — canlı DB'ye bu ortamdan erişemiyorum. Sayıları panelde **Denetim** sekmesini açınca (veya endpoint'i çağırınca) production'dan **birebir** göreceksiniz.

---

## 3. Purchase source-path dağılımı  — 🔧 KURULDU

Her Purchase'ın hangi akıştan gittiği **order'da** tutuluyor (`orders.capi_purchase_source`): `iyzico_webhook`, `iyzico_callback`, `iyzico_card`, `admin_status_confirmed`, `offline_payment_confirm`, `recovery_admin`. Logda değil → **join** ile eklendi.

- **Panel/`API`:** `purchase_source_path` → akış başına adet + email/fbp/external_id sinyal sayıları.
- Böylece “eksik match sinyali belirli bir akışta mı yoğunlaşıyor” sorusu cevaplanır.

> Retry worker bağımsız Purchase üretmez; yalnız `capi_event_queue`'daki başarısız event'i yeniden dener (`orchestrator.py:193`). Yani source-path'ler yukarıdaki 6 akışla sınırlı.

---

## 4. Deploy sonrası production sağlık kontrolü  — 🔧 KURULDU (latency hariç ❌)

**Denetim** sekmesi + `audit.health` şunları verir (pencere seçilebilir: 24s/72s/7g/30g):

```text
Meta CAPI error rate: health.error_rate_pct  (ok/error/total)
Retry/dead-letter: health.retry_success · health.queue_pending · health.dead_letter
Order/checkout errors: (bu modülün dışında — ayrı; aşağıya bakın)
Payment webhook/callback errors: capi hata durum kodları → health.error_status_distribution
Duplicate/idempotency issue: YOK olması beklenir — order-level atomik `capi_purchase_sent` bayrağı + Meta event_id dedup (orders.py:1776, 1855)
ViewContent server volume: health.viewcontent_server_volume
ViewContent latency: ❌ ölçülmüyor
Other provider regression: by_event provider filtresiyle karşılaştırılabilir
```

- **Duplicate/idempotency:** ✅ İki katman koruma var — sipariş başına **tek** server Purchase (`find_one_and_update {capi_purchase_sent:{$ne:true}}`) + `event_id = order_number` ile Meta native dedup. Denetimde aynı order için ikinci Purchase logu görürseniz sorun demektir; normalde görülmez.
- ❌ **Latency (gönderim süresi):** hiçbir yerde per-event süre loglanmıyor. Ölçüm eklemek **event akış kodunu değiştirir** → freeze gereği **yapılmadı**. İstenirse ayrı onayla eklenir (küçük, düşük riskli).
- **Checkout / sipariş oluşturma / iyzico hata oranı** bu CAPI modülünün dışında (ayrı loglar). Anormallik şüphesi varsa söyleyin, sipariş/ödeme tarafı için ayrı bir sağlık sorgusu çıkarırım.

---

## 5. ViewContent `_fbp` timing  — ✅

```text
Flag/değer: window.__FCT_VC_FBP_WAIT_MS__ · varsayılan 1200 ms (dataLayer.js:109,276)
Server event kaybı: HAYIR — bekleme sonunda _fbp bulunmasa da event tek sefer gönderilir (dataLayer.js:280-285)
Duplicate: HAYIR — pushEvent'te tek POST; ikinci server event üretilmez
Browser akışı değişti mi: HAYIR — fbq pixel bekleme bloğundan ÖNCE, anında ateşlenir (dataLayer.js:162-179)
Median ek gecikme: ölçülmüyor (yalnız server mirror; tavan 1200 ms, _fbp gelince erken çıkar — 120 ms adımlarla poll)
event_time korunuyor mu: EVET — event_time bekleme ÖNCESİ sabitlenir, timestamp kaymaz (dataLayer.js:122; meta.py:184-190)
```
Bounded-wait yalnız `view_item` için ve yalnız `_fbp` yoksa çalışır.

---

## 6. IPv6 backend doğrulaması  — ✅

- `CAPI_PREFER_CF_IPV6` → **env var, varsayılan `"1"` (açık)** (`deps.py:305`). Kapatma: `=0`.
- IP çözümleme sırası (edge güvenilirse): **`CF-Connecting-IPv6`** (flag açıksa) → `CF-Connecting-IP` → `X-Forwarded-For[0]` → TCP peer (`deps.py:288-320`).
- `ip_version` loglama: `match_signals.ip_version` = `":" varsa 6, doluysa 4, boşsa None` (`orchestrator.py:116`).
- ❌/✅ **IPv6 parser hatası:** parser **yok** — IP opak string olarak işleniyor, `try/except` ile sarılı → malformed IPv6 **raise edemez** (parse edilmediği için). IPv4 event'lerde regresyon yok.
- Son 50–100 event'te gerçek `ip_version=6` oranı: **Denetim** sekmesindeki IPv6 kartından (production).
- **Cloudflare paneli değiştirilmedi.**

---

## 7. Tespit edilen gerçek sorun  — YOK (kod tarafında)

Kod incelemesinde production'ı bozan bir hata/regresyon **bulunmadı**. Değişmezler (tek server Purchase, hash'leme, dedup, retry/dead-letter, event_time koruması, IPv6) yerinde.

### Bu denetimde yapılan tek değişiklik (freeze uyumlu)
**Salt-okunur** admin denetim aracı eklendi — event gönderme akışına **hiç dokunmaz**, yeni event üretmez/göndermez:
- `GET /api/marketing-pixels/capi/audit` (admin, PII'siz)
- Panel: **CAPI Loglar → 🔎 Denetim (Meta Purchase)**

Bununla §2/§3/§4 sayılarını production'dan **canlı** okuyabilirsiniz. Latency ölçümü bilinçli olarak eklenmedi (akışı değiştirir).
