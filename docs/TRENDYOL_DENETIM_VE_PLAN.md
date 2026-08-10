# FACETTE — Trendyol Tam Denetimi + Pazaryeri Birleştirme Planı
### (kod-temelli · satılabilirlik odaklı · rakip kıyaslı)

> Amaç: **önce Trendyol'u bitir** (memnun olduğun hız/doğruluk korunur), sonra HB'yi aynı çatıya al.
> Kırmızı çizgi: mevcut çalışan sisteme paralel ikinci bir sistem KURULMAZ → çakışmayı bu önler.
> Yeni şart: **başkası Facette'yi alıp kendi sistemini koddan elini sürmeden aktive edebilmeli.**

---

## A. TRENDYOL — TAM DENETİM (gerçek kod)

### A.1 Çalışan ve iyi olan (dokunma)
- Barkod anahtarlı haberleşme — pazaryerlerinde en sağlam yöntem (rakip yorumları da bunu doğruluyor).
- Güvenilirlik makinesi **HB'de yok, TY'de var:** `trendyol_retry_queue` (başarısız gönderimi kuyruğa al), `get_trendyol_batch_status` (parti sonucu izleme), `trendyol_ghost_scanner` (TY'de olup yerelde olmayan/yetim kalan barkodları tara), `trendyol_barcode_duplicates` (çakışan barkod tespiti).
- `resolve_attributes`: kategori `attribute_mappings` + `value_mappings` + `default_mappings` + isimle otomatik eşleme + `allow_custom`/`required`/`valid_value_ids` doğrulaması. Olgun.

### A.2 BULGU 1 — Özellik/değer iki ayrı cache'te (TAM ve GÜNCEL mi? → kısmen)
> Senin sorun: "Trendyol özellikleri ve değerleri tam ve güncel çekiliyor mu tüm yerlerde?"

| Yer | Çağırdığı endpoint | Yazdığı/okuduğu koleksiyon |
|---|---|---|
| Ürün editörü (`Products.jsx`, `Categories.jsx`) | `/integrations/trendyol/categories/{id}/attributes?refresh=true` | **`trendyol_attributes`** |
| Kategori eşleme (`MarketplaceAdvancedMatch.jsx`) | `/category-mapping/trendyol/{id}/attributes` | **`trendyol_category_attributes`** |
| **Gerçek ÜRÜN GÖNDERİMİ** (`_get_attr_meta`, push) | — | **`trendyol_category_attributes`** okur |

**Sonuç:** Gönderim, *kategori-eşleme* cache'ini okuyor. Ürün editöründen "özellikleri yenile" dersen **başka** cache tazelenir; gönderim onu görmez. Yeni TY değerleri (örn. yeni renk/beden enum) editörde görünür ama push eski/eksik şemayla gidebilir → **bazı özellikler eksik gönderilir, kullanıcı nedenini göremez.**
**Düzeltme (Trendyol'u bitir #1):** Tek cache (`trendyol_category_attributes`) + tek "Özellikleri Trendyol'dan yenile" butonu; editör ve push aynı kaynağı okur. Risk: sıfır (gönderim mantığı değişmez, sadece kaynak birleşir). Yenilemenin **tam** çektiğini göstermek için panelde "N özellik · M değer · son güncelleme" rozeti.

### A.3 BULGU 2 — Alan kaynakları koda gömülü (HB'de config)
`title←name`, `description←description/short_description`, `listPrice=salePrice=calculate_trendyol_price(price)`, `vatRate←20`, `cargoCompanyId←10 (MNG)`, `brandId←... or 975755`, `images←[:8]`. Düzenlenebilir değil. (HB'de bunlar `base_field_mappings` ile UI'dan seçiliyor.)

### A.4 BULGU 3 — White-label engelleri (SATILABİLİRLİK için kritik)
Koda gömülü ve **alıcı değiştiremez:**
- `cargoCompanyId: 10` (MNG sabit — 3 yerde) → alıcı kendi kargosunu seçemez.
- `brandId ... or 975755` (3 yerde) → alıcının markası yok.
- `vatRate` varsayılan 20, `listPrice = salePrice` (ayrı liste fiyatı/indirim yok).
**Düzeltme:** bunlar **kanal ayarına** taşınır (kargo firması, varsayılan marka, KDV, supplier). Koddan okumayı bırak, ayardan oku — değer aynı kalır, ama artık alıcı UI'dan değiştirir.

### A.5 BULGU 4 — TY ↔ HB asimetrisi (iki yönlü)
| Yetenek | TY | HB | Hedef |
|---|---|---|---|
| Config-tabanlı alan kaynağı | ❌ kod | ✅ `base_field_mappings` | TY de config okur |
| Kullanıcı varsayılanları paneli | ⚠️ kategori içinde | ✅ `global_attr_defaults` | ortak panel, **kullanıcı genişletir** |
| Retry kuyruğu / batch izleme / ghost tarama | ✅ | ❌ | HB devralır |
| Barkod tekilleştirme | ✅ | ❌ | HB devralır |
> "Önce Trendyol'u bitir" = TY'yi HB'nin config seviyesine **çıkar**; sonra HB'yi TY'nin güvenilirlik seviyesine çıkar. İkisi de kazanır.

---

## B. RAKİP KIYASI → FACETTE İLKELERİ
(Ticimax, T-Soft, Softtr, IdeaSoft kullanıcı yorumlarından çıkan TEKRAR EDEN dertler → Facette ne yapmalı)

| Rakipte tekrar eden şikayet | Facette'nin tavrı (fırsat) |
|---|---|
| Stok/varyant senkronu güvenilmez; bir kanal güncellenir öbürü güncellenmez; "olmayan ürünü satma" → mağaza puanı düşer | **#1 değer önerisi.** TY retry-queue + batch izleme + ghost tarama'yı **tüm kanallara** taşı. "Sessiz başarısızlık" yok; her gönderimin durumu görünür. |
| Ürünü 1 kez çekeriz, gerisi sana kalır; göç bir kâbus | Tekrarlanabilir, self-servis içe aktarma + **kurulum sihirbazı**. |
| Her özellik ekstra ücret (iade/renk/pazaryeri başına) | Satılan pakette **özellikler dahil**; sürpriz ücret yok. |
| Entegrasyon ekranları karışık ve hatalı | **Senin de derdin bu.** Tek "Pazaryeri Merkezi", sade UI, anlamlı durum renkleri. |
| Destek zayıf; eğitim videosuna yönlendirme; kullanıcı yalnız | Alıcı **kendi başına** aktive edebilmeli → uygulama-içi rehber, net hata mesajı, "neyi neden gönderemedi" raporu. |
| Yüklenen görsel/bilgi sessizce geri siliniyor | (Senin bilinen "auto-commit snapshot revert" sorununla aynı sınıf) → onaylı değişiklik geri alınamaz; değişiklik izi. |
| Hafta sonu kesinti, lisans kapanması | Kendi altyapın (Railway/CF); kanal bağımsız; kesintide diğerleri çalışır. |

**Rakiplerin reklam ettiği "standart" (Facette'de zaten var, parite):** tek panel (stok/fiyat/sipariş/kargo/fatura/yorum), satışta tüm kanallarda anlık stok düşümü, kanal başına fiyat kuralı (komisyona göre markup), toplu aktarım + Excel, kategori/özellik eşleme, sipariş→e-fatura, kargo kodu geri besleme, 14 gün demo + aynı gün aktivasyon.
→ **Facette farkı:** güvenilirlik + sade UI + kendi başına aktive edilebilirlik (white-label).

---

## C. SATILABİLİRLİK (WHITE-LABEL) GEREKSİNİMLERİ
Bir alıcı koda dokunmadan kendi sistemini kurabilmeli:
1. **Kanal kimliği UI'dan:** supplier_id/api_key/secret + test bağlantısı (TY'de var, kalıp standartlaşır).
2. **Marka / kargo / KDV / supplier UI'dan** (A.4'teki sabitler ayara taşınır).
3. **Kurulum sihirbazı:** Kanal bağla → Kategori eşle → Alan kaynağı onayla → Varsayılanları gir → Test ürünü gönder → Canlı. Her adım yeşil olmadan ilerlemez.
4. **"Neyi neden gönderemedi" raporu:** zorunlu+çözülemeyen özellik → kullanıcıya tam liste (manuel doldursun). Rakiplerin en çok eksik bıraktığı şey bu.
5. **Sıfır kod-sabiti:** mağaza/marka/kargo/supplier'a dair hiçbir değer kodda kalmaz.

---

## D. YAPILACAKLARIN ÖNİZLEMESİ (sıralı, geri-alınabilir)

### FAZ T — ÖNCE TRENDYOL'U BİTİR (TY'ye risk: sıfır, payload diff=0)
- **T1.** Çift cache'i birleştir → tek kaynak + tek yenile butonu + "N özellik/M değer/son güncelleme" rozeti. *(BULGU 1)*
- **T2.** Gizli ayarları yüzeye çıkar: aktarma filtresi (stok kodu/barkod), stok/fiyat anahtarı, rezerv, markup — hepsi TY ayar ekranında görünür (backend zaten var). *(senin "göremedim"lerin)*
- **T3.** White-label sabitlerini ayara taşı: kargo firması, varsayılan marka, KDV, supplier. Koddan okumayı bırak; değer birebir aynı. *(BULGU 3+4)*
- **T4.** TY "Alan Eşleştirme"yi göster (HB'deki gibi) — başta **salt-okunur/tohumlu**; düzenleme açılırsa **payload diff=0** testi şart.
- **DoD:** TY'ye giden payload bayttan bayta aynı (regresyon testi). `ast.parse` + esbuild geçer.

### FAZ H — SONRA HEPSİBURADA'YI ÇATIYA AL
- **H1.** TY güvenilirlik makinesini HB'ye ver: retry kuyruğu, batch izleme, ghost tarama, barkod tekilleştirme.
- **H2.** HB'yi aynı "Pazaryeri Merkezi" kabuğuna ve aynı 4 sekmeye taşı (kendi şeması/değerleriyle).
- **H3.** Varsayılan Özellikler panelini genişlet: `cinsiyet` sabitini kaldır, **"+ Varsayılan ekle"** (menşei, garanti, yaş…).

### FAZ S — SATILABİLİRLİK
- **S1.** Kurulum sihirbazı (C.3).
- **S2.** "Neyi neden gönderemedi" raporu (C.4).
- **S3.** Yeni kanal şablonu (N11/Amazon) — aynı kabuk, kendi şeması, TY'den veri çekmeden.

> Temu'ya dokunulmaz (teknik borç). Ticimax verisi taşınmadan temizlik yapılmaz.

---

## E. AJAN ÖNERİLERİ ("şu da olsa daha iyi olur")
- **🔒 GÜVENLİK:** Çok-kiracılı (multi-tenant) satılacaksa kanal anahtarları kiracı-bazlı secret'ta izole olmalı; bir alıcının anahtarı diğerine sızmamalı. Şimdiden `settings.id="trendyol"` global → ileride `tenant_id` boyutu eklemek kolay olsun diye config şeması buna hazır kurulmalı.
- **⚡ PERFORMANS:** Stok/fiyat güncellemede **delta** (yalnız değişeni gönder) + kanal başına hız limiti; rakiplerin "biri güncellenir öbürü güncellenmez" derdi çoğu zaman rate-limit'e takılıp sessiz düşmekten. Kuyruk + görünür durum bunu çözer.
- **🏗️ MİMAR:** "Alan Eşleştirme" + "Resolver" tek servis olsun (`MarketplaceService`); kanonik bir kez okunur, N kanala dağıtılır. Yeni kanal = yeni adaptör + config; çekirdek değişmez. Satışta "yeni kanal eklemek" tek dosya olur.
- **📊 İŞ:** Satılabilirlik için kanal-başına kâr/komisyon görünürlüğü (`MarketplaceProfit` var) sihirbaza bağlanırsa alıcı "bu kanal kârlı mı" görür — güçlü satış argümanı.
- **🧪 TEST:** TY payload "altın örnek" (golden snapshot) testi — her değişiklikten önce eski==yeni payload. "Trendyol bozulmaz" garantisi koda değil teste bağlanır.
- **📦 GÖÇ:** Tekrarlanabilir içe aktarma (rakiplerin "1 kez çekeriz" derdi) — alıcı kendi ürünlerini istediği zaman yeniden çekebilsin; sessiz revert olmasın (değişiklik izi).
