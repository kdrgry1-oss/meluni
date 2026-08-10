# FACETTE — Pazaryeri Entegrasyon Mimarisi

> Yönetişim dokümanı · Tek Kaynak / Adaptör modeli
> Sahip ajanlar: 🔌 ENTEGRASYON (birincil) · 🏗️ MİMAR · ⚡ PERFORMANS · 🔒 GÜVENLİK · 🚢 DEVOPS
> `FACETTE_AJAN_MIMARISI.md` protokolüne tabidir. Ticimax-bağımsız; yeni kuplaj yasaktır.

---

## 0. ALTIN KURALLAR (ihlal edilemez)

1. **Tek Kaynak.** Ürünün tüm değerleri *kanonik üründe* nötr durur. Her kanal buradan okur.
2. **Trendyol yalnızca yapısal referanstır.** Yeni bir kanal için veri üretirken **Trendyol'dan veri çekilmez**, Trendyol'un payload formatı kopyalanmaz. Kanal, **kanonikten** okur ve **kendi API'sinin istediği** formatta gönderir.
3. **Her kanalın özellik şeması KENDİ API'sinden gelir.** Bir kanalın *zorunlu özellikleri* ve *izinli değerleri*, o kanalın kategori-özellik endpoint'inden çekilir. **Hepsiburada özellikleri Trendyol'dan alınmaz.**
   > Mevcut hata: HB ve Temu özellik alanları Trendyol'dan besleniyor. HB **bu çalışmada** kendi şemasına çevrilir. **Temu'ya şimdilik dokunulmaz** (teknik borç olarak işaretli).
4. **Trendyol bozulmaz.** Tüm değişiklikler eklemeli ve geri alınabilir. Trendyol'a giden değer birebir aynı kalır.
5. **Kanallar birbirinden bağımsızdır.** Bir kanalın çözümleyici/özellik/varsayılan ayarı diğerini etkilemez.

---

## 1. Katmanlar

```
┌──────────────────────────────────────────────────────────┐
│  KANONİK ÜRÜN  (products koleksiyonu, platform-bağımsız)  │
│  başlık · stok · fiyat · açıklama · marka · nötr özellik  │
└───────────────┬──────────────────────────────────────────┘
                │ okur (tek yön)
┌───────────────▼──────────────────────────────────────────┐
│  ÇÖZÜMLEYİCİ  (Resolver)                                  │
│  "alan nereden gelir + dönüşüm + varsayılan"              │
│  öncelik: Genel → Kanal → Ürün                            │
└───────┬───────────────┬───────────────┬──────────────────┘
        │               │               │
┌───────▼─────┐  ┌──────▼──────┐  ┌─────▼───────┐
│ TY ADAPTÖR  │  │ HB ADAPTÖR  │  │ N11 ADAPTÖR │  …
│ (referans)  │  │ kendi şeması│  │ (boş şablon)│
└─────────────┘  └─────────────┘  └─────────────┘
  her adaptör kanonikten okur · KENDİ şemasıyla · KENDİ formatında gönderir
```

**Adaptör hiçbir zaman başka adaptörden veya başka kanalın şemasından veri okumaz.** Tek girdi: kanonik + o kanalın kendi şeması.

---

## 2. Trendyol referans analizi (mevcut çalışan akış)

> Alan adları FACETTE'deki gerçek Trendyol entegrasyon kodundan teyit edilecek (kod paketi gelince §2.3 birebir hizalanır). Yapı doğru.

### 2.1 Bağlantı / kimlik
- Auth: Seller ID + API Key/Secret, env/secret'ta. Ürün dokümanında asla.
- Endpoint kökü: `integration/.../sellers/{sellerId}/...`

### 2.2 Eşleştirme anahtarları
| Anahtar | İş | FACETTE karşılığı |
|---|---|---|
| `barcode` | Stok/fiyat güncellemede eşleme anahtarı | `variant.barcode` |
| `stockCode` | Satıcının kendi stok kodu | `variant.stock_code` |
| `productMainId` | Varyant gruplama (model kodu) | `urun_id` |

Aktarma filtresi (hangi ürünler gönderilsin): *stok koduna göre* / *barkoda göre* — korunur, her kanala taşınır (§5 `match`).

### 2.3 Ürün aktarma — alan haritası (referans)
| Trendyol alanı | Kanonik kaynak | Dönüşüm / not |
|---|---|---|
| `barcode` | `variant.barcode` | benzersiz |
| `title` | `title` | trim, kanal limiti |
| `productMainId` | `urun_id` | varyant gruplama |
| `brandId` | `brand` | marka tablosundan ID |
| `categoryId` | kategori eşleme | |
| `quantity` | `variant.quantity` | `max(0, q − rezerv)` |
| `stockCode` | `variant.stock_code` | |
| `listPrice` | `pricing.list_price` | |
| `salePrice` | `pricing.sale_price` | + kanal markup (TY %0) |
| `vatRate` | `pricing.vat_rate` | varsayılan %10 |
| `currencyType` | sabit | `TRY` |
| `description` | `description.html` | fallback → `plain` |
| `images[]` | `images[]` | |
| `attributes[]` | `attributes[]` (nötr) | TY şeması + eşleme ile ID'ye (§6) |

### 2.4 Stok / fiyat güncelleme
- Endpoint `price-and-inventory` · anahtar `barcode` · `{barcode, quantity, salePrice, listPrice}` · mod **delta**.

### 2.5 Sipariş çekme
- `orders` · kaynak **prefix** ile: `TY…` = Trendyol.

### 2.6 Müşteri sorusu
- `qna/questions` · adaptör sözleşmesi `pull_questions()`.

---

## 3. Kanonik ürün modeli (nötr)

```jsonc
{
  "urun_id": "1180",
  "title": "Midi Etek",
  "brand": "FACETTE",
  "description": { "html": "...", "plain": "..." },
  "pricing": { "list_price": 449.90, "sale_price": 329.90, "vat_rate": 10, "currency": "TRY" },
  "attributes": [
    { "key": "Renk",     "value": "Siyah" },
    { "key": "Materyal", "value": "Pamuk" },
    { "key": "Etek Boyu","value": "Midi" }
  ],
  "images": ["https://.../1.jpg"],
  "variants": [ { "barcode": "869...01", "stock_code": "FCT-ETK-1180-S", "quantity": 12, "size": "S" } ],
  "marketplace": {
    "trendyol":   { "content_id": "...", "category_id": 522, "status": "active" },
    "hepsiburada":{ "listing_id": "...", "category_id": "...", "status": "pending" }
  }
}
```

`attributes[]` nötr. `marketplace.*` yalnız bağ/iz tutar; bir kanal başka kanalın bloğunu okumaz.

---

## 4. Çözümleyici (Resolver) + Kullanıcı tanımlı varsayılanlar

Her alan için: **kaynak + dönüşüm + varsayılan**. Öncelik: `Genel → Kanal → Ürün`.

### 4.1 Varsayılanlar kullanıcı tarafından yönetilir
Cinsiyet=`Kadın`, Menşei=`Türkiye` yalnızca örnek; UI'dan düzenlenir **ve yeni varsayılan eklenir**:

```jsonc
"defaults": {
  "gender":   "Kadın",
  "origin":   "Türkiye",
  "warranty": "2 yıl"      // örnek: kullanıcı ekledi
}
```
Alan boşsa defaults devreye girer; doluysa kanonik kazanır. Önizlemede "varsayılandan dolduruldu" (amber).

### 4.2 Stok & Fiyat eşleme
```jsonc
"stock_price": {
  "stock_source": "variant.quantity",
  "reserve": 2,
  "price_source": "pricing.sale_price",
  "list_price_source": "pricing.list_price",
  "markup_pct": 0,
  "update_key": "barcode",
  "update_mode": "delta"
}
```

---

## 5. Pazaryeri başına yapılandırma şablonu

> Her kanal bağımsız doldurulur. **Hiçbir kanal başka kanalın config/şema/payload'undan değer almaz.**

### 5.1 Trendyol (referans — dolu)
```yaml
channel: trendyol
match: { link_key: barcode, push_filter: stock_code }
fields:
  title:       { source: title, transform: "trim|max:100" }
  brand:       { source: brand, map: brand_table }
  quantity:    { source: variant.quantity, transform: "max(0,q-reserve)" }
  stock_code:  { source: variant.stock_code }
  list_price:  { source: pricing.list_price }
  sale_price:  { source: pricing.sale_price, markup_pct: 0 }
  vat_rate:    { source: pricing.vat_rate, default: 10 }
  currency:    { const: TRY }
  description: { source: description.html, fallback: description.plain }
  images:      { source: images }
defaults: { gender: "Kadın", origin: "Türkiye" }
attributes:
  schema: trendyol_category_attributes   # ZORUNLU + izinli değerler TY API'sinden
  map: attribute_map(trendyol)           # nötr key → TY attributeId/valueId
  allow_custom: false                    # TY: serbest değer yok (attr bazında değişebilir)
update: { key: barcode, fields: [quantity, salePrice, listPrice], mode: delta }
```

### 5.2 Hepsiburada (KENDİ formatı + KENDİ şeması)
```yaml
channel: hepsiburada
match: { link_key: merchant_sku, push_filter: stock_code }
fields:
  title:        { source: title, transform: "trim|max:HB_LIMIT" }
  brand:        { source: brand, map: brand_table_hb }
  stock:        { source: variant.quantity, transform: "max(0,q-reserve)" }
  merchant_sku: { source: variant.stock_code }
  price:        { source: pricing.sale_price, markup_pct: 8 }   # örnek
  description:  { source: description.html, fallback: description.plain }
  images:       { source: images }
defaults: { gender: "Kadın", origin: "Türkiye" }
attributes:
  schema: hepsiburada_category_attributes  # ZORUNLU + izinli değerler HB API'sinden (TY'den DEĞİL)
  map: attribute_map(hepsiburada)          # nötr key → HB özellik adı/değeri
  allow_custom: true                       # HB: bazı alanlarda serbest/manuel değer kabul eder
update: { key: merchant_sku, fields: [price, stock], mode: delta }
```

### 5.3 Yeni kanal (boş şablon)
```yaml
channel: <n11 | amazon | pttavm>
match:      { link_key: <?>, push_filter: <stock_code|barcode> }
fields:     { ... kanonik kaynaklar, kanalın alan adlarıyla ... }
defaults:   { gender: "Kadın", origin: "Türkiye" }
attributes:
  schema: <channel>_category_attributes    # HER ZAMAN o kanalın API'sinden
  map: attribute_map(<channel>)
  allow_custom: <true|false>               # kanal serbest değer kabul ediyor mu
update:     { key: <?>, fields: [...], mode: delta }
```

---

## 6. Özellik Sistemi (detay)

Bu bölüm ürün kartındaki özellik editörünün davranışını ve değer çözümlemesini tanımlar.

### 6.1 Kanal kategori-özellik şeması (her kanal kendi API'sinden)
Her kanal, bir kategori için şu şemayı verir; FACETTE bunu **cache**'ler:
```jsonc
attribute_schema(channel, category_id) -> [
  { "name": "Etek Boyu", "required": true,  "allow_custom": false,
    "allowed_values": ["Mini","Midi","Maxi"] },
  { "name": "Renk",      "required": true,  "allow_custom": false,
    "allowed_values": ["Siyah","Beyaz","Lacivert", ...] },
  { "name": "Desen",     "required": false, "allow_custom": true,  "allowed_values": [...] }
]
```
- **Trendyol** → TY kategori-özellik endpoint'i.
- **Hepsiburada** → HB kategori-özellik endpoint'i (KENDİ değerleriyle). **Trendyol'dan beslenmez.**
- **Temu** → şimdilik dokunulmaz; sonra `temu_category_attributes` ile kendi şemasına geçecek.
- Kategori farklıysa zorunlu set farklıdır: *Etek*'in zorunluları ile *Elbise*'nin zorunluları aynı değildir; her kategori kendi şemasını getirir.

### 6.2 Ürün kartı özellik editörü — davranış
Her kanal sekmesi/bölümü için:
1. Ürünün o kanaldaki eşlenmiş kategorisinin şeması yüklenir.
2. **Zorunlu (required) özellikler en üstte, kırmızı çerçeveli.** Opsiyoneller altta, nötr.
3. **Eksik zorunlu başlık varsa** kanal şemasından getirilip editöre **boş + kırmızı** olarak eklenir (kullanıcı doldursun diye).
4. Değer alanı = **o kanalın izinli değer listesi** (dropdown). Trendyol'un değeri Trendyol'dan, HB'nin değeri HB'den.
5. **Cinsiyet → Kadın**, **Menşei → Türkiye** otomatik dolar (düzenlenebilir varsayılan; §4.1).
6. **Hepsiburada serbest değer:** kullanıcı manuel bir değer yazdıysa, HB'nin izinli listesinde karşılığı yoksa **ve** o alan `allow_custom: true` ise → yazılan değer **custom olarak otomatik eklenip öyle gönderilir**. `allow_custom: false` ise → çözülemez, o kanala gönderim bloke (§6.5).

### 6.3 Değer çözümleme algoritması
```python
def resolve_value(channel_attr, value):
    if not value:
        return EMPTY                      # zorunlu ise kırmızı, gönderimi bloklar
    hit = channel_attr.allowed_values.find_ci(value)
    if hit:
        return MATCHED(hit)               # kanalın resmi değeri
    if channel_attr.allow_custom:
        return CUSTOM(value)              # HB serbest-değer yolu: yazılanı ekle & gönder
    return UNRESOLVED                     # yalnız bu kanal için gönderimi bloklar

# editör satırı üretimi
value = product.attributes[neutral_key] or defaults[neutral_key]   # cinsiyet/menşei vb.
row   = resolve_value(channel_schema[attr], value)
```

### 6.4 Nötr ↔ kanal köprüsü
- Ürün değerleri nötr `attributes[]`'te (key/value) durur.
- Her kanal kendi `attribute_map`'i + kendi şemasıyla çözer.
- **Aynı nötr değer farklı kanalda farklı sonuç verebilir:** TY'de eşleşir, HB'de custom olarak gider — bu normaldir, ikisi de aynı nötr kaynaktan türer, biri diğerinden kopyalanmaz.

### 6.5 Gönderim bloklama
- Bir kanalda **zorunlu + çözülemeyen** özellik varsa, ürün **yalnız o kanala** gönderilmez (kırmızı uyarı). Diğer kanallar etkilenmez.

---

## 7. Adaptör sözleşmesi

```python
class MarketplaceAdapter(Protocol):
    def push_product(canonical, config) -> Result
    def update_stock(canonical, config) -> Result
    def update_price(canonical, config) -> Result
    def pull_orders(since) -> list[Order]
    def pull_questions(since) -> list[Question]
    def category_schema(category_id) -> list[Attribute]   # KENDİ API'sinden
    def map_attributes(canonical, config) -> list
```
Tek `MarketplaceService` orkestratör; kanonik bir kez okunur, N adaptöre dağıtılır.

---

## 8. Mevcut Trendyol'da yapılacak MİNİK değişiklikler (düşük risk)

1. **Gölge-yazım:** TY kartındaki özellikleri `products.attributes[]` nötr listeye kopyala (kart değişmez).
2. **Varsayılanları resolver'dan oku:** cinsiyet/menşei sabitleri koddan `defaults`'a (değer aynı).
3. **Eşleştirme modunu UI'da göster:** stok kodu / barkod seçimi panelde görünür.
4. **HB özellik kaynağını ayır:** HB editörü artık `hepsiburada_category_attributes` okur; **Trendyol şemasıyla beslenme kesilir.** (Temu'ya dokunulmaz.)
> 1–3 Trendyol'a sıfır risk. 4, HB'ye özel; Trendyol payload'u değişmez.

---

## 9. Fazlar (geri alınabilir)

| Faz | İş | Trendyol riski |
|---|---|---|
| 1 | Nötr `attributes[]` + gölge-yazım göçü | sıfır |
| 2 | Resolver + defaults + stok/fiyat eşleme | sıfır |
| 3 | HB şema ayrımı + HB özellik editörü (kendi değerleri, custom) | sıfır (ayrı kanal) |
| 4 | Yeni kanal şablonu (N11 vb.) | sıfır |
| — | Temu şema ayrımı | sonraya ertelendi |

---

## 10. Guardrails / DoD

- ❌ Bir kanal başka kanalın şema/payload/`marketplace.*` bloğunu okuyamaz.
- ❌ HB (ve sonra Temu) özellikleri Trendyol'dan beslenemez.
- ❌ Trendyol'a giden değer değişmez (regression: payload diff = 0).
- ✅ Zorunlu + çözülemeyen özellik → yalnız o kanal bloke.
- ✅ Her kanalın zorunlu/izinli değer şeması kendi API'sinden, cache'li.
- ✅ Varsayılanlar (cinsiyet/menşei + kullanıcı ekledikleri) config'te, koda gömülü değil.
- ✅ DoD: `ast.parse` geçer · esbuild geçer.

---

## 11. Açık noktalar / kod için gerekenler

Gerçek kodu yazabilmem için (Faz 3 — HB özellik ayrımı) şu dosyalar lazım:
- **Frontend:** ürün kartı özellik bölümünü çizen bileşen (Trendyol/HB özelliklerinin gösterildiği component).
- **Backend:** Trendyol kategori-özellik (zorunlu + değerler) çeken servis.
- **Backend:** Hepsiburada entegrasyon/aktarma dosyası (şu an özelliği TY'den besleyen yer).
- **Backend:** kategori eşleme + `attribute_map` saklama yeri.

Bunlar gelince §2.3 ve §5.2 birebir hizalanır, HB özellik kaynağı Trendyol'dan koparılır.
