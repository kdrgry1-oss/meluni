# FACETTE — Ticimax Çıkış Planı (çökme-güvenli)

> Hedef: sıfır senkron, sıfır veri çekme, kod/UI/config/DB'de Ticimax kalmaması.
> **Kural: hiçbir aşamada `server.py` boot'u bozulmaz.** Silme her zaman EN SON, referanslar sıfırlanınca.

---

## 0. NEDEN GEÇEN SEFER ÇÖKTÜ (kök neden)

`server.py` başlangıçta 5 Ticimax router'ını **import ediyor** (satır ~82-86) ve kaydediyor (~611-620):
```
from routes.ticimax_stock_sync import router as ticimax_stock_sync_router
... (category_sync, member_sync, product_pull, returns)
api_router.include_router(ticimax_stock_sync_router)
...
```
Bir Ticimax dosyasını **silersen**, bu import satırı `ImportError` verir → FastAPI app boot edemez → **Railway'de site komple düşer.**
Çözüm: önce import + include'ları temizle, SONRA dosyayı sil. Sıra bozulursa çöker.

---

## 1. YÜZEY HARİTASI (silmeden önce bilinmesi gerekenler)

**Backend — startup-kritik (server.py):** 5 router import + include (stock_sync, category_sync, member_sync, product_pull, returns).
**Backend — dosyalar:** `ticimax_client.py`, `ticimax_order_parser.py`, `ticimax_schema.py`, `ticimax_member_sync.py` + 5 route dosyası. `integrations.py` içinde **15** adet `/ticimax/*` ucu.
**Scheduler:** `_ticimax_sync_orders` (sipariş çekme — **Aşama 1'de KAPATILDI**), `_ticimax_sync_stock` (zaten kapalı). Fonksiyonlar lazy-import; duruyorlar, zararsız.
**Frontend:** ~23 dosya `ticimax` içeriyor. Özel sayfalar: `TicimaxExcelUpload.jsx`, `TicimaxReturns.jsx`; nav: adminNav "Ticimax Excel Yükle"; `AutomationStatus` Ticimax kartı; `Integrations/MarketplaceHub/Orders/Returns` vb.
**DB alanları (DOKUNMA — veri):** `source: "ticimax"`, `imported_from: "ticimax"/"ticimax_cron"`, `ticimax_order_id` (~39 referans). Bunlar geçmiş sipariş verisi; silme/yeniden adlandırma yalnız veri taşıma teyitliyse.

---

## 2. AŞAMALAR (her biri geri alınabilir)

### Aşama 1 — Otomatik veri çekmeyi durdur ✅ (BU PAKET)
- Scheduler'da `ticimax_orders_sync` job kaydı **yorumlandı** (fonksiyon duruyor).
- Stok senkronu zaten kapalıydı.
- **Import bozulmadı, dosya silinmedi → site boot eder.**
- Sonuç: Ticimax'tan otomatik sipariş/stok çekme YOK. Manuel admin uçları hâlâ var ama tetiklenmedikçe çalışmaz.
- **Geri alma:** scheduler'daki bloğu yorumdan çıkar.

### Aşama 2 — UI'dan gizle (kod silmeden)
- adminNav'dan "Ticimax Excel Yükle" girişini kaldır; `AutomationStatus` Ticimax kartını gizle.
- `TicimaxExcelUpload.jsx` / `TicimaxReturns.jsx` route'larını menüden çıkar (dosya dursun).
- Backend route'lara dokunma (çağrılmazsa zararsız).
- **Test:** frontend build (esbuild) geçmeli; menüde Ticimax görünmemeli.

### Aşama 3 — Kaynak etiketi: "Ticimax/TicimaxWeb" → "Site" / "Web"
- Yalnız **gösterim** katmanında (UI label) yap. **DB'deki `source` değerlerini DEĞİŞTİRME** (sipariş kaynağı zaten prefix ile belirleniyor: `TY…`/`HB…`/prefix yok=Site).
- Filtre/sorgularda `source:"ticimax"` kullanan yerleri tek tek gözden geçir; gösterim "Site" olsun, sorgu mantığı bozulmasın.
- **Test:** sipariş listesi kaynak kolonu doğru; mevcut Ticimax kaynaklı siparişler "Site" görünür.

### Aşama 4 — Route'ları devre dışı bırak (dosya silmeden)
- `server.py`'de **önce** `include_router(ticimax_*)` satırlarını yorumla; **sonra** ilgili `import` satırlarını yorumla. İkisi birlikte → `/ticimax/*` uçları 404, ama import yok = hata yok.
- `integrations.py` içindeki 15 `/ticimax/*` ucu: erişilse de kullanılmıyor; istenirse bunlar da kaldırılır (ayrı, dikkatli adım).
- **Test (zorunlu):** `python -c "import server"` benzeri boot denemesi VEYA push sonrası Railway logunda `[scheduler] Background scheduler started` satırını gör. Görünmüyorsa boot patlamıştır → geri al.

### Aşama 5 — Dosyaları sil (EN SON, referans sıfırsa)
- Silmeden önce **şu komutla referans kontrolü** yap:
  ```bash
  grep -rniE "ticimax" backend/server.py backend/scheduler.py
  ```
  Çıktı boşsa (yalnız yorum kalmışsa) silmek güvenli.
- Sırayla sil: önce 5 route dosyası, sonra `ticimax_member_sync.py`, en son `ticimax_client.py / _order_parser / _schema`.
- Her silmeden sonra boot testi.
- DB temizliği (Ticimax kaynaklı kayıtlar / `ticimax_order_id` alanı) **yalnız veri taşıma teyitliyse** ve yedek alındıktan sonra.

---

## 3. HER PUSH ÖNCESİ ÇÖKME-ÖNLEME CHECKLIST
1. `cd backend && python -c "import ast,glob;[ast.parse(open(f).read()) for f in glob.glob('**/*.py',recursive=True)];print('syntax ok')"` → syntax temiz mi.
2. `grep -rniE "ticimax" backend/server.py` → sildiğin dosyaya import kalmış mı (kalmışsa SİLME).
3. Push sonrası Railway logu: `[scheduler] Background scheduler started` göründü mü (boot OK işareti).
4. Site health + bir sipariş sayfası açılıyor mu.
5. Patlarsa: son commit'i revert et (`git revert HEAD`), geri al.

---

## 4. DURUM
- **Aşama 1: TAMAM** (otomatik çekme kapalı, site güvende).
- **Aşama 2: TAMAM** (UI'dan gizlendi — adminNav "Ticimax Excel Yükle" girişi + AutomationStatus "Ticimax" kartı kaldırıldı. **Yalnız frontend**, backend boot'una dokunulmadı, esbuild geçti. Dosyalar ve `/admin/ticimax-excel` route'u duruyor; menüde görünmüyor.)
- **Aşama 3: TAMAM** (UI temizliği — Entegrasyonlar sayfasındaki "Ticimax" kartı (API key + "Canlı Mod") ve Entegrasyon Logları filtresindeki `ticimax` seçeneği kaldırıldı. Sipariş kaynak etiketi zaten "Web" gösteriyordu (Orders rozeti `Web`; kaynak filtresinde ticimax yok) → DB `source` değerlerine ve sorgu mantığına **dokunulmadı**. `TicimaxReturns` bileşeni "Web Sitesi" iade akışını çalıştıran **aktif** bileşendir → DOKUNULMADI. Yalnız frontend, esbuild geçti.)
- **Aşama 4 (kısmi): TAMAM** (ilk backend dokunuşu — `server.py`'de `ticimax_category_sync` / `ticimax_member_sync` / `ticimax_product_pull` router'larının **import + include** satırları `# [A4-ticimax-off]` ile yorumlandı. Bu 3 değişken yalnız server.py'de kullanılıyor + uçlarının aktif frontend çağıranı yok → boot patlamaz. **stock_sync KORUNDU** (AutomationStatus'taki "Ticimax Stok Senkronla" butonu hâlâ `/admin/ticimax/sync-stock` çağırıyor). **returns KORUNDU** (aktif "Web Sitesi" iade akışı `/admin/ticimax/return-orders`, `/orders/refresh-dates`, `/returns/{id}/open`, `/return-orders/export` kullanıyor). Boot gate: Railway logunda `[scheduler] Background scheduler started`.)
- **Aşama 4b: TAMAM** (AutomationStatus'tan `ManualSyncButtons` ("Ticimax Stok Senkronla") render+fonksiyonu kaldırıldı **+** `ticimax_stock_sync` router'ı (import+include) `# [A4b-ticimax-off]` ile kapatıldı. Frontend+backend aynı paket, **kümülatif** (server.py Aşama 4 değişikliklerini de içerir). Artık **yalnız `ticimax_returns_router` aktif** (site iade akışı). esbuild + ast.parse geçti. Boot gate: Railway `[scheduler] Background scheduler started`.)
- **Aşama 4c:** `integrations.py` içindeki ~15 `/ticimax/*` ucu — **AMA** returns akışının kullandığı `/integrations/ticimax/orders/import` HARİÇ. Dikkatli, ayrı adım.
- **Aşama 5a: TAMAM (kısmi silme)** — referanssız 3 route dosyası silindi: `routes/ticimax_category_sync.py`, `routes/ticimax_member_sync.py`, `routes/ticimax_product_pull.py`. (server.py importları Aşama 4'te yorumlu olduğu için boot'a etkisi yok. `git rm` ile; önce `grep ^[^#]*ticimax_(category_sync|member_sync|product_pull) server.py` boş çıktığı doğrulandı.)
- **ÇIKIŞ — Otomatik Ticimax bağı KESİLDİ (22 Haz 2026): TAMAM.** Aşama 1'de "yorumlandı" varsayılan `ticimax_orders_sync` cron'u **gerçekte hâlâ aktifti** (her 6 saatte Ticimax SOAP'tan site siparişi çekiyordu). Kanıt: en yeni `imported_from=ticimax_cron` siparişi **2026-06-05** (17 gün önce) → akış kurumuş; site siparişleri artık React/iyzico checkout'tan geliyor. `scheduler.py`'deki `add_job(_ticimax_sync_orders, ...)` bloğu `# [ticimax-off 2026-06-22]` ile kapatıldı (fonksiyon tanımlı kalır, çağrılmaz). **Artık sisteme giden otomatik Ticimax SOAP bağı YOK.** VERİ DOKUNULMADI (28.569 geçmiş ticimax-kaynaklı sipariş + tüm ürünler + `source:"ticimax"` etiketi korunur — Grup B). Bu, GÜVENLİ SIRA #4'ün cron kısmını kapatır. **Kalan SOAP çağrı-noktaları on-demand** (returns refresh + manuel import uçları) → tetiklenmedikçe çalışmaz; sonraki adımda dikkatle nötrlenecek (returns DB'den okuyor mu önce doğrulanacak). Boot gate: Railway `[scheduler] Background scheduler started`.
- **ÇIKIŞ — On-demand Ticimax SOAP da KESİLDİ (22 Haz 2026): TAMAM.** Cron'dan sonra kalan tek SOAP noktaları — returns `/orders/refresh-dates` + integrations manuel order import/repair — de artık Ticimax'a gitmiyor. `ticimax_client.py`'de 3 zeep client factory (`_urun/_siparis/_uye_client`) `TICIMAX_LIVE` bayrağıyla guard'landı: bayrak yoksa `RuntimeError` fırlatır. TÜM çağıranlar SOAP'u **try/except ile sardığından 500 yok** (returns `{success:false}` döner; integrations hatayı yakalar). Boot-safe (factory'ler lazy, top-level network yok). VERİ DOKUNULMADI. **Artık hiçbir kod yolu Ticimax'a bağlanmıyor** — returns ana liste/export/open + tüm aktif sayfalar DB'den okuyor. Acil yeniden açma: env `TICIMAX_LIVE=1`. Boot gate: `[scheduler] Background scheduler started`.
- **REBRAND — `ticimax_schema.py` → `product_schema.py` (22 Haz 2026): TAMAM.** Şema dosyası/modülü de-brand edildi: `git mv` + `products.py` (import 10/820 + alias `TICIMAX_BOOL_COLS`→`PRODUCT_BOOL_COLS`) + 2 ölü script import'u + `models.py` yorum-ref'i. **Sadece `products.py` + 2 script import ediyordu → atomik, boot-safe.** Endpoint URL `/meta/ticimax-schema` ve frontend `ticimaxSchema` state'i **KASITLI bırakıldı** (frontend/backend AYRI deploy → URL rename = yarış riski; iç/görünmez). **`ticimax_fields` DB alanı DOKUNULMADI** (Grup B veri, 31.845 üründe; products.py'de 20+ ref kalır — büyük migration olmadan kaldırılamaz). Deploy: eski dosya için `git rm backend/ticimax_schema.py`. Boot gate: `[scheduler] Background scheduler started`.
- **ÇIKIŞ — Ölü scheduler fonksiyonları + `ticimax_stock_sync.py` SİLİNDİ (22 Haz 2026): TAMAM.** `scheduler.py`'den ölü `_ticimax_sync_orders` (612-710) + `_ticimax_sync_stock` fonksiyonları ve yorumlu add_job blokları kaldırıldı; boot log'undaki stale `+ Ticimax orders every 6h` ibaresi temizlendi (prefix `Background scheduler started` korundu). Bu, `routes/ticimax_stock_sync.py`'nin tek importer'ıydı → dosya `git rm` ile **silindi** (router zaten server.py'de yorumluydu). Diğer 13 scheduler job'u (Trendyol/HB/kargo/PII vb.) **dokunulmadı**. ast.parse geçti, boot-safe. Kalan ticimax çekirdek: `ticimax_client.py`/`ticimax_order_parser.py` (integrations manuel import uçları + returns refresh-dates hâlâ import ediyor → onlar çözülmeden silinemez), `ticimax_returns.py` (aktif site-iade → rename adayı). Boot gate: `[scheduler] Background scheduler started`.
- **REBRAND — `ticimax_returns.py` → `rooftr_returns.py` (22 Haz 2026): TAMAM.** Aktif site/iade siparişleri akışı. `git mv` + **server.py** import/include (86+620) `rooftr_returns_router`'a güncellendi (ATOMİK, boot-kritik). Router prefix zaten `/admin/rooftr`, tag `rooftr-returns`, frontend `RooftrReturns.jsx` `/admin/rooftr/*` çağırıyor → **path değişmedi, frontend yarışı YOK.** İç fonksiyonlar `list/export/open_ticimax_*` → `*_rooftr_*` (dışarıdan çağrılmıyordu). Docstring (eski dosya adı + bayat `/admin/ticimax` path) + ödeme etiketi `Diğer (Ticimax)`→`Diğer` düzeltildi. **Kalan ticimax (meşru, dokunulmadı):** veri alanları `ticimax_order_id` / `platform=source="ticimax"` / yazılan `source="ticimax_bridge"` (Grup B), dış modül `from ticimax_client import` (refresh-dates SOAP ucu), akış-açıklama yorumları. ast.parse (server.py + rooftr_returns.py) geçti. Deploy: `git rm backend/routes/ticimax_returns.py`. Boot gate: `[scheduler] Background scheduler started`.
- **ÇIKIŞ — Ölü SOAP uçları stub'landı + `ticimax_order_parser.py` SİLİNDİ (22 Haz 2026): TAMAM.** 3 ölü Ticimax SOAP endpoint'i imzaları korunarak `{success:false, message:'...kapatildi'}` stub'ına indirgendi: `import_ticimax_orders` (/integrations/rooftr/orders/import — RooftrReturns 'sipariş çek' butonu artık temiz 'kapalı' der), `backfill_broken_ticimax_orders` (/ticimax/orders/backfill — frontend'de çağıran yoktu), `refresh_order_dates` (/admin/rooftr/orders/refresh-dates — RooftrReturns döngüsü success:false görünce kırılır). Bu, `ticimax_order_parser.py`'nin tek importer'larıydı → dosya `git rm` ile **silindi** (boot-safe, server.py import etmiyordu). ast.parse (integrations.py + rooftr_returns.py) geçti. **Boot-kritik:** integrations.py boot'ta server.py'ye include ediliyor → boot-test şart. Deploy: `git rm backend/ticimax_order_parser.py`. 
- **`ticimax_client.py` KASITLI BIRAKILDI (silinmedi):** hâlâ `sync_ticimax_teknik_detay` (/site/teknik-detay/sync) ucunun **SOAP-refresh yolu** (`use_cache=False` → `scripts/enrich_attrs_from_ticimax_master.fetch_master`) + 2 standalone script (`scripts/ticimax_pull_by_kart_ids.py`, `sync_ticimax_variants.py`) kullanıyor. O ucun `use_cache=True` yolu `db.ticimax_attribute_master`'dan ürün teknik alanlarını doldurur — **işlevsel + değerli, bozulmamalı.** ticimax_client zaten dormant/guard'lı (TICIMAX_LIVE off → raise) → zararsız. Silmek o işlevsel ucun kütüphanesine cerrahi gerektirir → risk/getiri kötü, ERTELENDİ.
- **PERF §7 adım 1a — PostHog 3rd-party hafifletme (22 Haz 2026): TAMAM.** `index.html`'de PostHog `session_recording` → **`disable_session_recording: true`** (recorder.js artık hiç yüklenmez; mobilde en pahalı 3rd-party iş + ağ kazancı). `posthog.init(...)` **`requestIdleCallback`'e ertelendi** (timeout 4s, fallback setTimeout 2.5s) → ilk render ana iş parçacığını blokemiyor (TBT düşer). PostHog event'leri idle'da fire eder (analytics çalışır, sadece replay yok). node --check geçti. **Frontend-only → Cloudflare Pages, Railway boot riski YOK.** Deploy sonrası hard-refresh. **GTM'e DOKUNULMADI** (consent/timing riski) → adım 1b (GTM idle-defer) ayrı, opsiyonel. Sıradaki büyük iş: §7-2 bundle böl (412 KB main.js) / §7-3 homepage prerender.
- **PERF §7 adım 1b — GTM idle-defer (22 Haz 2026): TAMAM → §7-1 (3rd-party) TÜMÜYLE BİTTİ.** `index.html`'de GTM `gtm.js` yüklemesi `requestIdleCallback`'e ertelendi (timeout 4s, fallback setTimeout 2.5s); `window.dataLayer` **erken init** edildi → erken event'ler kuyruğa girer, GTM idle'da yüklenince işlenir. Güvenlik: storefront'ta **GTM'e bağlı consent banner YOK** (grep ile doğrulandı) + `lib/dataLayer.js` zaten `dataLayer ||= []` ile kendini koruyor + Purchase server-side CAPI'den (dedup) gider → pixel timing riski ihmal edilebilir. node --check geçti. Frontend-only, boot riski yok. 
- **PERF §7 DURUM özeti:** §7-1 ✅ (PostHog recording off + PostHog & GTM idle-defer). §7-4 görsel **zaten yapılmış** (lib/img.js WebP/AVIF, hero `fetchPriority=high`+`eager`, alt-fold `lazy`). §7-2 bundle: eager path **zaten yağsız** (App+Home+Header/Footer/ProductCard lean, recharts admin-only/ayrı chunk) → görünür kazanç yok. **Kalan tek büyük lever: §7-3 homepage prerender (react-snap) — ayrı proje, en çok iş/risk.** Kolay+orta perf kazançları HARVEST EDİLDİ.
- **PERF §7 — API host preconnect (22 Haz 2026): TAMAM.** `index.html`'e `api.facette.com.tr` için `preconnect crossorigin` + `dns-prefetch` eklendi. Homepage mount'ta `/api/products` + `/api/page-blocks` çekiyor (client-render SPA'da içeriği gate'leyen kritik yol); host'a preconnect yoktu → DNS+TLS handshake fetch anında oluyordu. Artık bağlantı erken ısınır → kritik veri ~100-300ms daha erken gelir (mobilde LCP'ye yardım). Sıfır risk (resource hint). node gerekmez. 
- **PERF §7-3 prerender — DÜRÜST KARAR: react-snap UYGULANMADI.** Bu homepage veri-çekmeli; react-snap build anında ya loading-state ya **stale veri** yakalar + Cloudflare Pages build'inde puppeteer/Chrome gerektirir → **build'i kırma riski**. App-shell skeleton de hero'nun değişken boyutu yüzünden **CLS/flash** riski taşır (createRoot doğrulandı, teknik mümkün ama temiz değil). **Temiz quick-win'ler tükendi** (3rd-party defer ✅, görsel ✅, lazy ✅, API preconnect ✅). Gerçek FCP/LCP sıçraması için tek yol **SSR (Next.js/Node SSR) = ayrı mimari proje**, zip-paket işi değil. Gerçekçi mevcut hedef: mobil ~75-85, masaüstü 95-100.

---

## 5. READ-ONLY ANALİZ — TAM ÇIKIŞ NEDEN TEK HAMLE DEĞİL

Ticimax çekirdek modülleri **aktif koda bağlı**, hemen silinemez:
- `ticimax_schema.py` → `routes/products.py` **boot'ta top-level** `from ticimax_schema import BOOL_COLS` yapıyor. Silersen ImportError → site açılmaz. (önce products.py'den koparılmalı)
- `ticimax_client.py`, `ticimax_order_parser.py` → aktif sipariş import + iade akışı (lazy import) kullanıyor.

`integrations.py`'deki 15 `/ticimax/*` ucundan bazıları **hâlâ aktif sayfalardan çağrılıyor:**
- `Categories.jsx` → `/ticimax/categories/sync-missing-from-products`
- `Products.jsx` → `/ticimax/teknik-detay/sync`
- `TicimaxReturns` (site iade) → `/ticimax/orders/import`, `/admin/ticimax/return-orders`
- `Integrations.jsx` → `/ticimax/status` (sayfa açılışında hâlâ GET; kart kaldırıldı ama handler/effect kaldı — kozmetik)

**Kalan silinemez:** `ticimax_stock_sync.py` (scheduler.py:619 lazy ref — önce o satır temizlenmeli), çekirdek 3 modül, integrations.py uçları, `models.py: ticimax_fields` (ÜRÜN VERİSİ — DOKUNMA).

### TAM ÇIKIŞ İÇİN GÜVENLİ SIRA (büyük iş, ayrı ayrı paketler)
1. **Aktif uçları yeniden adlandır — Aşama 5b: TAMAM (doğrulandı + sed).** İki uç INCELENDI, ikisi de **YEREL** (Ticimax API'sine gitmiyor):
   - `/ticimax/categories/sync-missing-from-products` → ürünlerin `category_name`'inden eksik kategorileri yerel DB'ye ekler (docstring: "Ticimax API'sini tekrar çağırmadan"). **→ `/site/categories/sync-missing-from-products`**
   - `/ticimax/teknik-detay/sync?use_cache=true` → yerel `ticimax_attribute_master` cache'inden ürün özelliklerini metin eşleştirir (buton hep use_cache=true; SOAP yalnız use_cache=false'ta, kullanılmıyor). **→ `/site/teknik-detay/sync`**
   - Yeniden adlandırma sed ile (backend decorator + tek frontend çağıran birlikte). Her uç tek çağıranlı (doğrulandı), çakışma yok, ast.parse+esbuild geçti. **NOT:** `db.ticimax_attribute_master` koleksiyonu ve `scripts/enrich_attrs_from_ticimax_master.py` adı hâlâ "ticimax" — bunlar VERİ/script, sonraki kozmetik pas.
2. **Returns akışını yeniden adlandır:** `/admin/ticimax/*` → `/admin/site-iade/*` + `/ticimax/orders/import` bağımlılığını çöz (tek atomik backend+frontend paket).
3. **Integrations.jsx ölü handler/effect temizliği** (frontend, güvenli).
4. **scheduler.py** ölü `_ticimax_sync_orders` / `_ticimax_sync_stock` fonksiyonlarını kaldır → `ticimax_stock_sync.py` silinebilir hale gelir.
5. **Çekirdek koparma:** products.py'den `ticimax_schema` importunu çöz → schema/client/parser silinebilir.
6. **EN SON:** `models.py: ticimax_fields` yalnız veri taşıma teyitliyse ve yedekle.

> Her adım: ast.parse + (frontend) esbuild + push sonrası Railway `[scheduler] Background scheduler started` + ilgili sayfa açılış testi.

---

## 6. REBRAND İZİ — "ticimax" → "Rooftr" (de-branding)

[KARAR] Sahip: *"adı ticimax olan herşeyi rooftr yap."* "rooftr" yazımı teyitli.
Bu iz, ÇIKIŞ izinden ayrı: çıkış = işlevi söker; rebrand = kalan "ticimax" adlarını **Rooftr** yapar. Aşamalı + çökme-güvenli.

### A/B AYRIMI (zorunlu — körlemesine global rename YASAK)
**🟢 Grup A — KOD (Rooftr yapılır, aşamalı):** dosya adları (`ticimax_*.py`, `Ticimax*.jsx`), fonksiyon/değişken adları, route yolları, yorumlar, **UI metinleri**, log mesajları.
**🔴 Grup B — VERİ (kodda DOKUNULMAZ; rename = veri uyumsuzluğu → "veriyi koruma" kuralını bozar):**
- `ticimax_fields` → ürün dökümanı alanı, AKTİF (URUNKARTIID, filtre `tf_/tfmin_`, arama; products.py 403-1379).
- `db.ticimax_attribute_master` → koleksiyon adı (category_mapping.py 1162/1817, integrations.py 5148).
- `source:"ticimax"`, `imported_from:"ticimax_cron"`, `ticimax_order_id` → mevcut siparişlerde kayıtlı (integrations.py 4871/5173/5561/5833/5901+).
> Grup B yalnız ileride **yedekli, ayrı, riskli DB migration** ile çevrilir. Kodda asla.

### R-AŞAMALARI (güvenliden riskliye)
- **R1 — Görünür UI metinleri → Rooftr: TAMAM (esbuild geçti).**
  - `TicimaxReturns.jsx` (Returns "Web Sitesi" sekmesinde aktif): 5 toast/tooltip/empty-state metni `Ticimax'tan…` → `Rooftr'dan…`; empty-state buton referansı gerçek etiketle eşitlendi ("Siparişleri Çek").
  - `TicimaxExcelUpload.jsx`: başlık "Ticimax Excel Ürün Aktarımı" → "Rooftr Excel Ürün Aktarımı"; "TicimaxExport" → "RooftrExport".
  - Kapsam DIŞI (bilerek): Integrations.jsx 344-445 toast'ları = ölü handler (kart Aşama 3'te kaldırıldı, tetiklenmez) → R3'te komple silinecek. Identifier/yorum/route → sonraki R-aşamaları.
- **R2 — route yolları `/ticimax/*` → `/rooftr/*` (returns alt-sistemi): TAMAM (ast.parse+esbuild geçti).**
  - `ticimax_returns.py` prefix `/admin/ticimax` → `/admin/rooftr` (stock_sync 4b'de kapalı → çakışma yok).
  - `integrations.py` `/ticimax/orders/import` → `/rooftr/orders/import` (path-classifier `/orders/import` substring'iyle korunur).
  - Çağıranlar: `TicimaxReturns.jsx` 6 yol (return-orders, orders/import, refresh-dates, export, returns/open ×2) + `Integrations.jsx:410` (ölü, tutarlılık).
  - **Kalan `/integrations/ticimax/*` route'ları (status, categories/import, products/import, test-connection, members/import vb.) DOKUNULMADI** → hepsi ölü Integrations handler'larından çağrılıyor, R3'te handler'larıyla silinecek.
- **R3 — Ölü kod temizliği: TAMAM (ast.parse + esbuild geçti).**
  - Backend: `integrations.py`'den 10 ölü `/ticimax` route silindi (status, settings, categories/import, variants/sync, test-connection, products/import, members/import, link-orders-to-users, members/import-excel, members) — ast tabanlı, hepsinin iç çağrısı yok doğrulandı (9087→7990 satır).
  - Frontend: `Integrations.jsx` tamamen ticimax'tan arındı (0 referans) — 5 ölü handler (Categories/Products/TestConnection/Orders/Members) + state (`ticimaxImporting*`, `ticimaxStatus`) + status-effect'teki `/ticimax/status` fetch + `statuses.ticimax` anahtarı kaldırıldı. Promise.all 7→6, destructuring hizalı, Trendyol/HB/Temu/iyzico/xml/doğan'a dokunulmadı.
  - Korunanlar: `/rooftr/orders/import` (returns), `/ticimax/products/upload-excel` (Excel sayfası — R-sonra), `/ticimax/orders/backfill` (caller belirsiz — R-sonra).
- **R4 — fonksiyon/değişken + dosya adları (alt-aşamalara bölündü, güvenliden riskliye):**
  - **R4a — frontend dosya adları: TAMAM (esbuild geçti, boot riski YOK).**
    - `git mv`: `TicimaxReturns.jsx`→`RooftrReturns.jsx`, `TicimaxExcelUpload.jsx`→`RooftrExcelUpload.jsx` (frontend/src/pages/admin/).
    - Internal `export default function` adları + import'lar: `Returns.jsx:7,108,450` (import+yorum+JSX) + `AdminApp.jsx:84,101` (import+JSX+route `path="ticimax-excel"`→`"rooftr-excel"`).
    - Repo'da `TicimaxReturns`/`TicimaxExcelUpload` referansı **0**. Kalan ticimax (boot/import DEĞİL → sonraki): `pullFromTicimax` fonksiyon adı + yorumlar + `/ticimax/products/upload-excel` API çağrısı + `data-testid="ticimax-excel-*"`.
  - **R4b — `/ticimax/products/upload-excel` → `/rooftr`: TAMAM (ast.parse + esbuild geçti, boot-safe).** Decorator + fonksiyon adı `upload_ticimax_products_excel`→`upload_rooftr_products_excel` + `RooftrExcelUpload.jsx:29` caller. Classifier güvenli (`server.py:453` `/products/` substring → product_push korunur). **`/ticimax/orders/backfill` DOKUNULMADI:** frontend/scheduler caller YOK (scheduler'daki backfill'ler hep Trendyol), işlevsel olarak Ticimax-SOAP çıkış-izi endpoint'i → rename = churn; ÇIKIŞ izinde silinecek.
  - **R4c (BOOT-KRİTİK, en son, AYRI) — backend dosya adları** `ticimax_returns.py`/`ticimax_schema.py`/`ticimax_client.py`/`ticimax_order_parser.py` → rename. **DİKKAT:** `products.py:10` top-level `from ticimax_schema import BOOL_COLS` + `server.py:86` `from routes.ticimax_returns import` → dosya adı AYNI commit'te import zinciriyle düzelmezse **BOOT PATLAR**. NOT: schema/client/parser çıkış izinde silinecek adaylar → rename yerine çıkışta silmek daha mantıklı olabilir (churn'den kaçın).
  - **R4d — sahibi-biz olan dosyaların kozmetik temizliği: TAMAM (esbuild geçti, boot riski yok).** `RooftrReturns.jsx` (`pullFromTicimax`→`pullFromRooftr` + 3 yorum) + `RooftrExcelUpload.jsx` (`data-testid="ticimax-excel-*"`→`rooftr-excel-*`). Bu 2 dosya artık **%100 ticimax-free**.
  - **R4d-KALAN — KASITLI BIRAKILDI (körlemesine rename YASAK kuralı / A-B ayrımı):**
    - `lib/img.js: static.ticimax.cloud` → **✅ GÖRSEL MIGRATION TAMAMLANDI (22 Haz 2026):** `migrate_product_images_to_r2` DRY-RUN=0, `migrate_pagedesign_images_to_r2` page_blocks=0, `switch_r2_to_cdn` products/page_blocks/files=0 → **tüm görseller `cdn.facette.com.tr`'de** (R2 bucket `facette-images`). Ayrıca `cms.py:131` seed default'undaki 10 hardcoded ticimax URL'i (hero×2/banner/yarım×2/instashop×5) → `cdn.facette.com.tr/pagedesign/*-800.webp` (hepsi HTTP 200 doğrulandı). **Veride VE kodda Ticimax görsel bağımlılığı = SIFIR.** `img.js`'in `static.ticimax.cloud` mantığı artık ölü-yol (zararsız; ileride temizlenebilir, ama taşınamayan görsel kalmadığı için risksiz).
    - `Products.jsx: ticimax_fields / ticimaxSchema / /products/meta/ticimax-schema` + `Categories.jsx: ticimax_id` = **Grup B veri/şema** (ürün formu buna bağlı). **DOKUNULMAZ.**
    - "Ticimax tarzı/aynası/benzeri" yorumları (ProductFilters/BrandMapping/FailedTransfers/MarketplaceHub/Orders vb.) = UI'ların Ticimax panellerinden örneklendiğini anlatan **tarihsel-doğru** yorumlar → "Rooftr" yapmak yanlış olur. Bırakıldı.
> Grup B (veri) tüm R-aşamalarında ELLENMEZ.

---

## 7. PERFORMANS (PageSpeed) İYİLEŞTİRME — EN SON (rebrand + stabilite bitince)

[KARAR] Sahip: *"page speed'i 100 yap ya da plana al, en son yapalım."* → **Plana alındı, en son.**

### Gerçekçi hedef (dürüst beyan)
**Mobil 100 bu mimaride GERÇEKÇİ DEĞİL.** Site CRA **client-render SPA** (`<div id="root">` boş geliyor) — mobil önce JS indirip parse edip render ediyor, içerik ondan sonra. Mobil 100 ancak SSR/prerender ile olur (mimari değişim).
- Gerçekçi: **mobil 43 → 75-85**; **masaüstü 95-100**.
- 90+ mobil: homepage prerender/SSR (büyük iş, ayrı proje).

### Mevcut ölçüm (22 Haz 2026, curl)
- `<div id="root">` boş → client-render SPA (asıl darboğaz).
- `main.js` = **412 KB ham / 128 KB gzip, tek parça** (homepage'de ayrı chunk yok) → mobil CPU parse'ı ana iş parçacığını bloke eder (yüksek TBT).
- `main.css` = 23 KB gzip (render-blocking, normal).
- 3rd-party: **GTM** (`GTM-THJQQDL`, head) + **PostHog `session_recording` AÇIK** (pahalı) + muhtemelen Meta Pixel (GTM üzerinden).
- **İyi olanlar — DOKUNMA:** Inter fontu async (preload+onload+noscript), preconnect/dns-prefetch (cdn/fonts/gtm/posthog), JS `defer`, lazy route'lar, SEO 100 / Best Practices 96.

### Öncelikli düzeltmeler (etki/risk — sırayla, tek tek test)
1. **3rd-party hafiflet (en kolay, en yüksek getiri, düşük risk):** PostHog `session_recording` kapat veya örnekle (örn %10); GTM+PostHog'u ilk etkileşim/idle sonrası geç-yükle → TBT düşer.
2. **JS bundle küçült/böl (412 KB):** içindeki ağır lib'leri tespit + route/komponent lazy → parse süresi.
3. **Homepage prerender (react-snap):** boş `#root` yerine anında içerik → LCP/FCP en büyük kazanım (en çok iş).
4. **Hero/ilk ürün görselleri:** WebP/AVIF + LCP görseline `fetchpriority="high"` + `srcset` + alt-fold `loading="lazy"`.
> Storefront değişiklikleri boot riski taşımaz; ama her deploy sonrası **hard-refresh + gerekirse Cloudflare cache purge** (CSS/cache yarışı = "görünüm bozuldu" belirtisinin kök nedeni, kod değil).


---

## 8. ERTELENENLER — bilerek bırakılan işler (22 Haz 2026)

Ticimax çıkışı **fonksiyonel olarak tamamlandı** (otomatik SOAP bağı kesik + guard'lı, ölü SOAP uçları stub, ölü dosyalar silindi/rename). Aşağıdakiler bilinçli ertelendi: her biri ya risk/getiri kötü, ya canlı veriye dokunur, ya ayrı projedir. Hiçbiri siteyi/çıkışı etkilemez.

### 8.1 `ticimax_client.py` (dormant SOAP client) — SİLİNMEDİ
- **Durum:** `TICIMAX_LIVE=off` + factory guard → hiçbir SOAP çağrısı yapamaz (dormant, zararsız).
- **Neden duruyor:** hâlâ `/site/teknik-detay/sync` ucunun `use_cache=False` (SOAP-refresh) yolu + `scripts/enrich_attrs_from_ticimax_master.py` (+ birkaç script) import ediyor. O ucun `use_cache=True` yolu `db.ticimax_attribute_master` cache'inden ürün teknik alanı doldurur → **ÇALIŞIR, bozulmamalı.**
- **Silmek için:** enrich script'inden SOAP'ı (`fetch_master`) ayıkla + `use_cache=False` dalını stub'la + 2 saf script'i sil → cerrahi, düşük getiri. Acil değil.

### 8.2 `ticimax_fields` ve ticimax-isimli VERİ alanları — DOKUNULMADI ⚠️ (önemli)
**Bu bir SYNC kalıntısı DEĞİL — ürünün KENDİ veri modeli.** Sync öldü ama ürün özellikleri (tedarikçi, kategori breadcrumb, GTIP, indirimli fiyat, ürün-kartı-ID, renk/beden/varyant bağı…) hâlâ her ürün dökümanında `ticimax_fields` objesi içinde **bizim MongoDB'mizde** yaşıyor.
- Storefront filtreleri (tedarikçi/para birimi facet'leri), admin ürün formu, renk-kardeş gruplama, indirimli fiyat → hepsi `ticimax_fields.*` okur.
- **"ticimax_fields okuması" = Ticimax'a bağlanmak DEĞİL; kendi ürün verimizi okumak.** Veri %100 yerel, Ticimax'a sıfır bağımlı. Tek "ticimax" olan: **anahtar adı** (yanıltıcı; orijinal import Ticimax şemasını taklit ettiği için öyle isimlendirilmiş).
- İlgili veri değerleri (kodda asla elle değişmez): `source:"ticimax"` (sipariş kaynak etiketi), `ticimax_order_id` (tarihsel sipariş ID), `db.ticimax_attribute_master` (öznitelik cache).
- **Tamamen ticimax-free kod istenirse:** `ticimax_fields` → `product_fields` rename = **VERİ MIGRATION'u** (31.845 dökümanda anahtar rename + tüm okuma/yazma noktalarını güncelle). Saf kozmetik ama **canlı veriye dokunur** → yedekli + atomik + test edilmiş ayrı migration gerekir. Aceleye gelmez; o yüzden ertelendi.

### 8.3 SSR / homepage prerender (perf §7-3) — AYRI MİMARİ PROJE
- Site client-render SPA; gerçek mobil FCP/LCP sıçraması **SSR** ister (Next.js / Node SSR katmanı).
- **react-snap uygun değil:** veri-çekmeli homepage'de build anında stale/loading yakalar + Cloudflare Pages build'inde puppeteer/Chrome ister → **prod build'i kırma riski.**
- Quick-win'ler bitti (PostHog recording off + PostHog/GTM idle-defer, görsel WebP/fetchpriority, lazy route, API preconnect). Bundan sonrası mimari proje. Gerçekçi mevcut hedef: mobil ~75-85, masaüstü 95-100.

### 8.4 Saf KOZMETİK (atlanabilir, sıfır fonksiyonel değer)
- `backend/scripts/*ticimax*` — eski tek-seferlik CLI araçları (boot-yüklü DEĞİL; `enrich_attrs_from_ticimax_master.py` aktif ucta kullanılıyor, diğerleri ölü ama zararsız).
- Frontend: `ticimaxSchema` state adı (Products.jsx), `lib/img.js`'deki ölü `static.ticimax.cloud` fallback, `/meta/ticimax-schema` endpoint URL'i.
- (Frontend'de görünen diğer "ticimax"lerin çoğu `ticimax_fields` **verisini** okuyan kod → 8.2 gereği değişmez.)

### Özet karar
Çıkış **kapandı.** Yukarıdakiler "kalan iş" değil; **bilinçli kapsam-dışı.** Sıradaki tek anlamlı tetikleyici: (a) kod tabanını harfiyen ticimax-free istemek → 8.2 veri migration'u (ayrı, yedekli oturum), veya (b) mobil perf hedefi iş-kritik olursa → 8.3 SSR projesi.
