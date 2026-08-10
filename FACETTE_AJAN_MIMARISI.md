# FACETTE — Ajan Mimarisi

> Büyük e-ticaret platformunu (FastAPI + React + MongoDB) bağımsız, sözleşmeli ajanlara bölmek için referans doküman.
> `★` = senin ilk listende olmayan, eklenmesi önerilen ajan/altyapı.

---

## 1. Bölme İlkesi

Ajanları "kim ne yapıyor"a göre değil, üç kurala göre böl:

1. **Birlikte bozulan şeyler birlikte sahiplenilir.** Bir bug birden çok parçayı patlatıyorsa, o parçalar tek ajandır.
2. **Her ajanın net bir sözleşmesi (girdi → çıktı) vardır.** Ajanlar birbirinin iç koduna elini sokmaz; sadece tanımlı arayüzlerden/olaylardan konuşur. (Senin "yeni coupling yasak" prensibinin doğrudan uygulaması.)
3. **Her ajanın bir "definition of done"u vardır.** Doğrulama zorunlu: `.py` için `ast.parse`, JSX/JS için esbuild.

Omurga = **sipariş durum makinesi + veri sözleşmeleri**. Her ajan bu ortak sözleşmeye uyduğu sürece diğerlerinden habersiz çalışabilir.

---

## 2. Çekirdek Ajanlar (kod sahibi olanlar)

| Ajan | Sorumluluk | Faz |
|---|---|---|
| 🏗️ **Mimar / Orkestratör** | Sınırlar, veri modeli, ajanlar arası sözleşmeler. Kod yazmaz; kuplaj kaçaklarını engeller, "bu iş kimin?" kararını verir. | P0 |
| 🛒 **Sipariş Çekirdeği** | Sepet, checkout akışı, sipariş durum makinesi (omurga). Sipariş kaynağı tespiti (TY/HB/Site prefix kuralı) burada. | P0 |
| 💳 **Ödeme** | 3DS, kart + havale, idempotency, iade/iptal tetikleme. İzole ve sıkı sözleşmeli — ödeme hataları felaket. | P0 |
| 📦 **Stok & Envanter** ★ | Stok rezervasyonu (checkout sırasında kilitleme), overselling önleme, çok-kanal stok tutarlılığı (Site + TY + HB aynı stoğu paylaşır). Race condition'ların yaşadığı yer. | P0 |
| 🏷️ **Kampanya & Fiyatlandırma** ★ | Kupon, indirim, `min_quantity`, "X al Y öde" (`nth_discount`) kural motoru. Sepet + sipariş + paneli birden etkilediği için kendi ajanı olmalı. | P0 |
| 👤 **Müşteri & Hesap** ★ | Üyelik, profil, adres defteri, sipariş geçmişi, misafir checkout doğrulama, KVKK veri talepleri. | P1 |
| 🔁 **İade & İptal** ★ | Ters akış: RMA, ödemeye iade, stoğa geri ekleme, pazaryeri iade senkronu. Kendi durum makinesi olduğu için Sipariş Çekirdeği'nden ayrı. (İptaller paneli bunun ön yüzü.) | P1 |
| 🔍 **Arama & Keşif** ★ | Ürün arama, faceted filtreleme, autocomplete, "bunu mu demek istediniz", relevance sıralama. "Lüks ve hızlı" hissin yarısı buradan gelir. | P1 |
| 🤖 **AI Müşteri Asistanı** | Katalog + sipariş verisi üzerinde cevap üretir. Kendi guardrail'leri: sipariş durumu uydurmamak, PII sızdırmamak. Diğer ajanlardan sadece **okur**. | P1 |
| 📊 **Raporlama & Analitik** | Dashboard, gelişmiş raporlar, Meta CAPI/atıf. Ağır okuma yükü → checkout'un yazma yolundan ayrı (replika/ayrı yol). | P1 |
| 🎨 **Vitrin / UX** | Storefront, PageSpeed, lazy-loading, checkout deneyimi. "Lüks ve hızlı" hissin diğer yarısı. | P0 |
| 🛠️ **Yönetim Paneli** | Orders, Campaigns, İptaller, ürün formu. Vitrinden farklı UX öncelikleri → ayrı ajan. | P0 |
| 📝 **İçerik & SEO / CMS** ★ | Kategori sayfaları, banner yönetimi, blog, SEO meta, landing page'ler. Organik trafik için. | P2 |
| ⭐ **Yorum & Değerlendirme** ★ | Ürün yorumları, moderasyon, AI ile sahte yorum tespiti. Lüks algı için sosyal kanıt. | P2 |

---

## 3. Entegrasyon Ailesi

Hepsi aynı deseni paylaşır: dış API + retry + webhook + idempotency + kimlik bilgisi yönetimi. **Ortak bir adaptör sözleşmesi** altında dursunlar; her biri o kontratı uygular.

| Ajan | Not | Faz |
|---|---|---|
| 📦 **Kargo** | Etiket üretimi, takip, teslimat durumu webhook'ları. | P0 |
| 🧾 **Fatura (e-Arşiv / e-Fatura, UBL)** | Doğan UBL/XSLT — ağır. `cbc:Note` formatı, il/ilçe çift-yazım dikkati. | P0 |
| 🏬 **Pazaryeri (Trendyol / HB)** | Sipariş çekme, stok/fiyat itme, iade senkronu. **Baştan Ticimax-bağımsız** kurulur; yeni coupling yasak. | P1 |
| 📨 **Bildirim (SMS / e-posta)** | İşlemsel bildirimler: sipariş onayı, kargo çıkış, iade. | P0 |
| 🤝 **CRM & Pazarlama Otomasyonu** ★ | Segmentasyon, terk edilmiş sepet, win-back, kampanya gönderimi. Bildirim'i *kullanır* ama strateji katmanı ayrı. | P2 |

---

## 4. Muhafız Ajanlar (dikey sahiplenmez, hepsini denetler)

| Ajan | Sorumluluk | Faz |
|---|---|---|
| 🔒 **Güvenlik** | Auth, PII, rate limit, secret/kimlik yönetimi, PCI kapsamı, fraud. | P0 |
| ⚡ **Performans** | Cache, sorgu optimizasyonu, frontend perf, yük testi. | P0 |
| 🚢 **DevOps** | Railway / Cloudflare deploy, CI, yedek; `zip → unzip → git add -A → commit → push` akışı. | P0 |
| 🗄️ **Veri & Veritabanı** ★ | MongoDB şema, indeks, migration, veri bütünlüğü. **Ticimax veri taşıma + çıkış** bu ajanın işi. | P0 |
| 📈 **Gözlemlenebilirlik / İzleme** ★ | Log, metrik, alerting, hata takibi (ör. Sentry), uptime. "Ödeme şu an düşüyor" bilgisini *gerçek zamanlı* veren ajan. | P0 |
| 🧪 **Test & Kalite (QA)** ★ | Test paketi sahibi, E2E, regresyon, CI kapıları. Orphan import temizliği (`CI=true` unused var'da kırılır). | P1 |

---

## 5. Geliştirmeye Yönelik Eklenmesi Gerekenler (ajan değil, ortak altyapı)

Ajanların birbirinden bağımsız çalışabilmesi için altta bunların olması şart:

1. **Olay/mesaj omurgası (event-driven)** ★ — `siparis.olusturuldu` olayını fatura, kargo, bildirim, CRM ajanları *dinler*. Kuplajı en çok azaltan tek şey. Sipariş ajanı kimin dinlediğini bilmek zorunda kalmaz.
2. **API sözleşmeleri (OpenAPI + paylaşılan tipler)** — front/back arası ve ajanlar arası kontrat. Sözleşme değişmeden iç kod serbestçe değişebilir.
3. **Arka plan kuyruğu / worker** — fatura kesme, kargo etiketi, SMS, export → request'i bloklamadan kuyrukta. Checkout hızını korur.
4. **Idempotency anahtarları** — ödeme ve webhook tekrarlarına karşı. (Senin `purchase_<order_number>` event ID deseninin genele yayılmış hali.)
5. **Secrets vault** — kimlik bilgisi yönetimi tek yerden. (Ticimax credential karışıklıklarını bir daha yaşamamak için.)
6. **Staging ortamı** — prod'a direkt deploy yerine önce test ortamı.
7. **Feature flag** — riskli değişikliği aç/kapa ile güvenli yayınla, geri al.
8. **Audit log** — sipariş durumu, iade, fiyat değişikliği: kim, ne zaman, neyi değiştirdi.
9. **Veri sözlüğü (domain glossary)** — ortak terimler, kaynak isimlendirme ("TicimaxWeb" → "Site/Web").
10. **Her ajan için sözleşme + DoD dokümanı** — aşağıdaki şablonla.

---

## 6. Ajan Sözleşme Şablonu

Her ajan için bir tane doldur:

```markdown
## Ajan: <emoji> <ad>
**Tek cümlede görevi:** ...
**Sahip olduğu dosyalar/dizinler:** backend/..., frontend/...
**Girdi (ne dinler / ne alır):** olaylar, endpoint'ler, parametreler
**Çıktı (ne üretir / hangi olayı yayar):** ...
**Bağımlı olduğu ajanlar (sadece sözleşme üzerinden):** ...
**Dokunmaması gereken yerler:** ...
**Definition of Done:** ast.parse / esbuild geçer; sözleşme kırılmaz; testler yeşil
**Guardrail'ler:** PII, idempotency, rate limit, ...
```

---

## 7. Başlangıç Sırası (hepsini birden açma)

Çok ajan = koordinasyon vergisi. Şu sırayla aç:

- **Adım 1 — Çekirdek + Muhafızlar (P0):** Mimar, Sipariş, Ödeme, Stok, Kampanya, Vitrin, Panel + Güvenlik, Performans, DevOps, Veri, İzleme. Bunlarla sistem ayakta ve güvenli durur.
- **Adım 2 — Olgunlaşma (P1):** Müşteri/Hesap, İade&İptal, Arama, AI Asistan, Raporlama, Pazaryeri, QA.
- **Adım 3 — Ölçeklenince (P2):** İçerik/SEO, Yorum, CRM otomasyonu.

Mevcut panel formatın (🏗️🔒🔌⚡🚢) bu haritanın "özet görünümü"dür — günlük çalışırken paneli, derinlemesine bölerken bu dokümanı kullan.

---

## 8. Guardrail — `integrations.py` regresyon dersi (2026-06-30)

**Olay:** `e052c7f` (HB kart fix: cache `_v` 8→9 + `by-local` endpoint) main'e girdi; sonra `74fd7d0` (returns) **eski base'den** yazıldığı için `backend/routes/integrations.py`'yi toptan ezdi → `_v` 9→8 geri döndü, `by-local` silindi. Frontend sağ kaldı, backend fix uçtu. Prod'da sonuç: kart cache'i her açılışta çöp sayıldı → canlı HB çekildi → flaky/rate-limit'te boş → **ürün özellikleri rastgele "9 sabite" düştü** (kategoriye değil, o anki çekme şansına bağlı tutarsızlık).

**Kural (zorunlu):**
1. `integrations.py` gibi **büyük, çok-sahipli** dosyalara dokunan HER commit, **güncel `origin/main`'den** türetilmeli. Eski base'den yazılmış bir dosyayı asla toptan üzerine yazma.
2. Deploy paketi üretmeden önce **fetch + diff**: `git fetch origin main && git diff origin/main -- <dosya>`. Diff yalnız amaçlanan satırları içermeli; başka bir özelliğin satırlarını eksiye düşürüyorsa **dur** (clobber).
3. Bir özellik commit'i başka bir özelliğin satırlarını siliyorsa (örn returns commit'i HB `_v`/endpoint'ini eksiltiyor) → o commit **stale base** demektir; rebase/yeniden uygula.
4. Kritik davranış işaretçileri (sözleşmeler) — bunlar bir deploy'da kaybolursa regresyon var demektir:
   - HB kart cache okuyucuları: `cached.get("_v") == 10` (yazıcı category_mapping ~467 + okuyucular ~1983/352/4314 — TEK paket içinde atomik).
   - `GET /hepsiburada/category-attributes/by-local` endpoint'i mevcut.
   - Returns köprüleri: `customer_returns`, `trendyol_claims`, gider pusulası satırları mevcut.

**DoD eki:** integrations.py değişikliği içeren her pakette son adım: `python3 -c "import ast; ast.parse(...)"` **+** yukarıdaki 4 sözleşme işaretçisinin `grep` ile mevcudiyet teyidi.
