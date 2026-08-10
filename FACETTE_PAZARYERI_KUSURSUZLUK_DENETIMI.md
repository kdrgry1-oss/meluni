# FACETTE — Pazaryeri Entegrasyon Kusursuzluk Denetimi
### Faz 0 — Derin Analiz (Trendyol + Hepsiburada)
**Kapsam:** Ürün aktarımı · Stok/Fiyat güncelleme · Sipariş çekme · Fatura · İade/İptal
**Amaç:** Bu 5 akışı %100 kusursuz hale getirmenin yolunu çıkarmak. Bu dosya yapılacakların kalıcı planıdır; faz faz ilerlenir.

**Önem işaretleri:** 🔴 KRİTİK (yanlış para/yanlış veri) · 🟠 CİDDİ (akış kırılır / sessiz kayıp) · 🟡 İYİLEŞTİRME
**Kanıt durumu:** ✅TEYİT (kodda satır numarasıyla görüldü) · 🔍SERTİFİKA (ilgili fazda hat-hat doğrulanacak)

---

## 0) ACIMASIZ ÖZET (önce bunu oku)

Bu sistem FACETTE'in "her şey yolundaysa" senaryosunda çalışıyor. Ama **"%100 kusursuz" değil.** Şu an gerçekte olan: birbirine bağlı sözleşmesi (contract) olmayan, ayrı ayrı çalışan ~90 uç noktası. Ortak bir **idempotency (çift-gönderim koruması), retry (tekrar deneme), mutabakat (reconciliation) ve hata-görünürlüğü** katmanı YOK. Akışların her biri tek tek "çalışıyor" görünür ama kenar durumlarda (taksit, kısmi iade, başarısız push, çift sipariş) sessizce yanlış sonuç üretiyor.

**En kritik 6 bulgu (detayları aşağıda):**
1. 🔴 **Taksitli iade/gider pusulası yanlış tutar** — gerçek `paidPrice` (vade farkı dahil) hesaba katılmıyor. ✅TEYİT
2. 🔴 **Fiyatlama "marj motoru" değil, tek global yüzde** — ürün/kategori bazlı marj ve komisyon-farkındalı fiyat yok. ✅TEYİT
3. 🟠 **Sipariş kaynağı sipariş-no ön-ekinden tahmin ediliyor** ("TY…"/"HB…") — kırılgan, gerçek kaynak alanı değil. ✅TEYİT (mimari)
4. 🟠 **İade sistemi 3 parçaya bölünmüş** (orders.py + ticimax_returns.py + rooftr_returns.py) — tek doğru yol yok. ✅TEYİT
5. 🟠 **Push/stok/fiyat'ta idempotency ve kalıcı hata-kuyruğu yok** — başarısız ürün sessizce kaybolabilir, çift gönderim riski. 🔍SERTİFİKA
6. 🟠 **Stok için iki ayrı yol** (`inventory-sync` toplu + `{id}/sync-inventory` tekil) — tek kaynak garantisi doğrulanmalı. 🔍SERTİFİKA

**Verdict:** Çekirdek push mantığı (Trendyol `resolve_attributes`, HB `_build_hb_product_item`) **değerli ve korunmalı.** Etrafındaki güvenilirlik/fiyat/mutabakat katmanı **yeniden kurulmalı.** Aşağıdaki faz planı bunu yapar.

---

## 1) ÜRÜN AKTARIMI (Trendyol + Hepsiburada)

### Mevcut (uç noktalar)
- Trendyol: `/trendyol/products/validate`, `/trendyol/products/sync`, `/trendyol/batch/{id}`, `/trendyol/products/batch-status/{id}`, `/trendyol/ghost-scanner`, `/trendyol/barcode-duplicates`, `/trendyol/archive-barcodes`.
- HB: `/hepsiburada/products/validate`, `/hepsiburada/products/sync`, `/hepsiburada/products/tracking/{id}`, `/hepsiburada/products/by-status`, `/hepsiburada/products/autofill-attributes`, `/hepsiburada/listings*`.
- Çekirdek: TY `resolve_attributes` (integrations.py:1485), HB `_build_hb_product_item` (4283). Sabit varsayılanlar `facette_defaults.py` üzerinden gap-fill (bkz. FACETTE_SABIT_VARSAYILANLAR.md).

### Bulunan sorunlar
- 🟠 **Kalıcı hata kuyruğu yok (🔍SERTİFİKA).** Bir ürün push'ta reddedilirse (eksik zorunlu özellik, eşleşmeyen değer), bu kalıcı bir "başarısız" durumuna düşüp tekrar denenmiyor; batch/trackingId sorgusu var ama **ürün-bazlı kalıcı sonuç + otomatik retry** garanti değil. Sonuç: "aktardım sandım, gitmemiş" vakaları.
- 🟠 **Idempotency yok (🔍SERTİFİKA).** Aynı ürünü iki kez push'larsan ikinci gönderimi engelleyen bir anahtar (barcode+hash) yok → mükerrer/çakışan listeler.
- 🟡 **Zorunlu-özellik ön-validasyonu marketplace şemasına göre tam mı? (🔍SERTİFİKA).** TY tarafında "değerin karşılığı yok → engelle" var (957). HB tarafında kategori zorunlu alan eksikse push öncesi net "şu alan zorunlu, boş" raporu üretiliyor mu, doğrulanmalı.
- 🟡 **Görsel/varyant bütünlüğü:** barkod↔varyant↔görsel eşlemesi tek yerden mi doğrulanıyor (`_resolve_stock_code` 548, `_dedupe_products_by_stock_code` 494 var) — kenar durumları sertifikalanmalı.

### %100 için gerekenler
1. Her ürün için **kalıcı push-durumu** koleksiyonu: `{tenant, marketplace, barcode, payload_hash, status(queued/sent/accepted/rejected), reason, batch_id/tracking_id, attempts, last_error}`.
2. **Idempotency anahtarı** = `marketplace+barcode+payload_hash`; aynı hash tekrar gönderilmez.
3. **Push-öncesi şema validasyonu**: kategori zorunlu alanlarını gerçek şemadan (TY attrs / `db.hepsiburada_category_attributes`) okuyup eksikleri tek listede göster, eksikse göndermeyi engelle veya işaretle.
4. **Otomatik retry** (üstel bekleme) + ölü-mektup (dead-letter) listesi: 3 denemeden sonra "manuel müdahale" kuyruğu.
5. **Tek "Aktarım Sağlık" ekranı**: kaç ürün gönderildi / kabul / red / bekliyor + red sebebi düz Türkçe.

---

## 2) STOK & FİYAT GÜNCELLEME

### Mevcut
- Trendyol: `/trendyol/products/inventory-sync` (toplu), `/trendyol/products/{id}/sync-inventory` (tekil).
- HB: `/hepsiburada/products/{id}/update-stock-price`, `/hepsiburada/categories/{id}/update-stock-price`, `/hepsiburada/products/inventory-sync`, `/hepsiburada/listings/update`.
- Fiyat tabanı: `_mp_base_price` (integrations.py:93) = `member_price_1` yoksa `price`. Marj: tek global `trendyol_markup`/`default_markup` (124-133). KDV: `default_vat_rate`.

### Bulunan sorunlar
- 🔴 **Fiyatlama bir "marj motoru" DEĞİL (✅TEYİT).** Tek bir global yüzde uygulanıyor. Yok olanlar:
  - Ürün/kategori/marka bazlı farklı marj,
  - **Komisyon-farkındalı** fiyat (her pazaryeri/kategori komisyonu farklı → net kâra göre liste fiyatı),
  - Kargo+hizmet bedeli içeren maliyet tabanı,
  - Yuvarlama kuralı (x,99), taban/tavan fiyat, kampanya fiyatı (indirimli vs liste).
  - "Her firma istediği marjla aktarsın" hedefi bu motor olmadan **karşılanamaz.**
- 🟠 **İki stok yolu — tek kaynak garantisi yok (🔍SERTİFİKA).** Toplu `inventory-sync` ile tekil `sync-inventory` aynı hesabı mı yapıyor, yoksa farklı yerlerden mi stok okuyor? İkisi ayrışırsa kanal-arası stok tutarsızlığı olur.
- 🟠 **Stok emniyet payı / rezervasyon yok (🔍SERTİFİKA).** Site + TY + HB aynı envanteri paylaşıyorsa, eşzamanlı satışta **aşırı satış (oversell)** riski. Emniyet stoğu / kanal başına ayırma yok.
- 🟡 **Fiyat/stok push başarısı doğrulanıyor mu?** Gönderdik ama pazaryeri reddetti senaryosunda kalıcı hata + retry yok (push ile aynı eksik).
- 🟡 **Mutabakat:** HB'de `/hepsiburada/reconcile/preview` var (iyi). Trendyol'da "pazaryerindeki fiyat/stok ≠ bizdeki" karşılaştırması var mı, yoksa kurulmalı.

### %100 için gerekenler
1. **Merkezi Fiyat Motoru** (yeni modül `pricing.py`): girdi = maliyet/taban + KDV + pazaryeri komisyonu + kargo + hedef marj + yuvarlama → çıktı = listePrice & salePrice. Kural seti: global → marka → kategori → ürün (en spesifik kazanır). Komisyon tablosu pazaryeri+kategori bazlı.
2. **Tek stok kaynağı + emniyet payı:** tüm kanallara giden stok tek fonksiyondan; `available = on_hand - reserved - safety_buffer`.
3. **Fiyat/stok için de kalıcı sonuç + retry** (push ile aynı altyapı).
4. **Çift yönlü mutabakat:** her pazaryeri için "bizdeki vs onlardaki" fiyat/stok farkı raporu + tek tıkla düzelt.

---

## 3) SİPARİŞ ÇEKME (OMS)

### Mevcut
- Trendyol: `/trendyol/orders/preview`, `/trendyol/orders/import-selected`, `/trendyol/orders/import`.
- HB: `/hepsiburada/orders/preview`, `/hepsiburada/orders/import-selected`, `/hepsiburada/orders/import-by-number`, `/hepsiburada/oms-diag`, paket akışı: `/hepsiburada/packages` (+invoice/label/cargo/deliver), `/hepsiburada/lineitems/{id}/cancel`.
- Panel sipariş tarafı: orders.py (4859 satır) — durum, kargo, fatura, iade.

### Bulunan sorunlar
- 🟠 **Sipariş kaynağı ön-ekten tahmin (✅TEYİT, mimari).** "TY…" = Trendyol, "HB…" = Hepsiburada, öneksiz = Site. Bu **veri değil tahmin.** Pazaryeri numara formatını değiştirirse ya da önek çakışırsa kaynak yanlış etiketlenir (fatura tipi, komisyon, iade akışı buna bağlı → zincirleme hata). Her siparişte açık `source` + `marketplace_order_id` alanı olmalı.
- 🟠 **Idempotent içe-aktarım (🔍SERTİFİKA).** Aynı siparişi iki kez "import" edersen çift sipariş oluşur mu? `marketplace_order_id` üzerinden upsert garantisi doğrulanmalı.
- 🟠 **Otomatik çekme döngüsü (🔍SERTİFİKA).** Siparişler scheduler ile periyodik mi çekiliyor yoksa manuel preview→import mi? Manuelse kaçan/gecikan sipariş riski.
- 🟡 **Durum eşleme (status mapping):** pazaryeri durumları (Created/Picking/Invoiced/Shipped/Delivered/Cancelled) panel durumlarına tam ve tek tablodan mı eşleniyor?

### %100 için gerekenler
1. Her siparişte zorunlu alanlar: `source`, `marketplace`, `marketplace_order_id`, `marketplace_status`, `raw_payload`. Kaynak **asla** numaradan tahmin edilmez.
2. **Idempotent upsert** `marketplace_order_id` benzersiz indeksiyle.
3. **Otomatik periyodik çekme** + son-çekme zaman damgası + "kaç dk gecikme" sağlık göstergesi.
4. **Tek durum-eşleme tablosu** (pazaryeri→panel) ve tek yönlü doğru akış.

---

## 4) FATURA (e-Arşiv / e-Fatura / Gider Pusulası)

### Mevcut
- `orders.py`: `/{order_id}/create-invoice` (1730), `/{order_id}/mark-invoiced`, `/{order_id}/reset-invoice`, `/{order_id}/invoice/print`, `/bulk-create-invoice`, `_havale_invoice_block` (1717).
- Sağlayıcı: `dogan_client.py` (e-fatura/e-arşiv SOAP). HB paket faturası: `PUT /hepsiburada/packages/{no}/invoice`.
- Yönlendirme kuralı (memory): mikro-ihracat → e-Arşiv İSTİSNA; VKN+GİB kayıtlı → e-Fatura (EFC); değilse e-Arşiv (FCT).
- Gider pusulası (iade): `orders.py /returns/{return_id}/gider-pusulasi` (4669).

### Bulunan sorunlar
- 🔴 **Taksitli gider pusulası/iade tutarı yanlış (✅TEYİT).** `_compute_refund_breakdown` (orders.py:4328) tutarı `order.subtotal`/kalem `price`'ından hesaplıyor; **müşterinin gerçekte ödediği `paidPrice` (vade farkı dahil) hiç okunmuyor.** 6 taksitle 1.120 ödeyen müşteriye belge/iade 1.000 çıkar. Düzeltme: iade/gider pusulası tabanı, taksitliyse `order.paidPrice` (iyzico'dan doğrulanmış) olmalı; tek çekimde `subtotal`.
- 🔴 **KDV oranı geçmiş hatası (memory/pattern).** Kadın hazır giyim %10 yerine %20 default'una düşmüş, 384 e-Arşiv toplu düzeltildi. Bu, **KDV'nin tek merkezden ürün/kategori bazlı belirlenmediğinin** kanıtı → fatura KDV'si kategori bazlı tek kaynaktan gelmeli.
- 🟠 **Fatura tipi yönlendirmesi sipariş-kaynağına bağlı (🔍SERTİFİKA).** Kaynak ön-ekten tahmin edildiği için (bkz. §3) yanlış kaynak → yanlış fatura tipi riski.
- 🟡 **Idempotency:** aynı siparişe iki kez fatura kesilmesini engelleyen kilit (zaten faturalı ise blokla) doğrulanmalı (`mark-invoiced`/`reset-invoice` var ama yarış durumu?).

### %100 için gerekenler
1. **İade/gider pusulası tabanını düzelt:** taksitli → `paidPrice`; tek çekim → `subtotal`. `_compute_refund_breakdown`'a `paid_basis` parametresi.
2. **KDV tek kaynak:** kategori→KDV tablosu; fatura ve pazaryeri fiyatı aynı tablodan okusun (çift tanım yok).
3. **Fatura tipi**, tahmin edilen kaynaktan değil, siparişteki kesin `source`+VKN/GİB durumundan belirlensin.
4. **Idempotent fatura:** "zaten faturalı" kilidi + tekilleştirme.

---

## 5) İADE & İPTAL

### Mevcut
- Panel iade: `orders.py` `/{order_id}/return-request`, `/admin-return`, `/returns/admin/list`, `/returns/{id}/status|approve|reject|refund-preview|gider-pusulasi|refund-pay|reship|reissue-barcode`.
- Pazaryeri iade/talep: Trendyol `/trendyol/claims*` (sync/accept yok/export/diagnostics/repair/dedupe), HB `/hepsiburada/claims`, `/claims/{no}/accept|reject`, `/lineitems/{id}/cancel`.
- Ayrı dosyalar: `ticimax_returns.py` (470), `rooftr_returns.py` (414).

### Bulunan sorunlar
- 🔴 **Taksitli iade tutarı yanlış** (bkz. §4.1 — aynı kök neden).
- 🟠 **İade mantığı 3 parçada (✅TEYİT).** `orders.py` + `ticimax_returns.py` + `rooftr_returns.py`. "Tek doğru iade yolu" yok; Ticimax olanı çıkış planında kaldırılacak ama hâlâ duruyor. Kaynağa göre farklı kod yolu = tutarsız davranış.
- 🟠 **Pazaryeri iadesi ↔ panel iadesi ↔ iyzico iadesi üçlüsü tek transaction'da mı? (🔍SERTİFİKA).** Pazaryerinde iade onaylanıp iyzico iadesi başarısız olursa (ya da tersi) sistem tutarsız kalır mı? Telafi/retry var mı?
- 🟡 **İptal:** sipariş iptalinde stok geri-iadesi (`apply-stock`/`reconcile-stock` var) tüm kanallara yansıyor mu, tek yerden mi?

### %100 için gerekenler
1. **Tek iade durum-makinesi:** kaynaktan bağımsız tek akış (talep→onay→kargo→iyzico iade→gider pusulası→stok iade), her adım kalıcı + idempotent + telafi edilebilir.
2. Taksit-farkındalı iade tabanı (§4.1).
3. Pazaryeri+iyzico iade adımları **saga/telafi** mantığıyla: biri başarısızsa diğeri otomatik geri alınır ya da "manuel müdahale" kuyruğuna düşer.
4. Ticimax iade yolu çıkış planına göre kaldırılır (taşıma bitince — onay bekliyor).

---

## 6) YATAY / SİSTEMİK EKSİKLER (tüm akışları ilgilendirir)

- 🔴 **Fiyat/Marj Motoru yok** → §2.1.
- 🔴 **Para tabanı tutarsızlığı** (subtotal vs paidPrice) → §4.1.
- 🟠 **Idempotency + retry + dead-letter** hiçbir akışta sistemli değil.
- 🟠 **Mutabakat (reconciliation)** sadece HB ürün tarafında var; fiyat/stok/sipariş için genel değil.
- 🟠 **Hata görünürlüğü:** kullanıcı "neden gitmedi"yi düz Türçe göremiyor; teknik trackingId'ler var ama "şu ürün şu zorunlu alan boş olduğu için reddedildi" seviyesinde tek ekran yok.
- 🟠 **Kimlik/Credential tek dokümanda** (`db.settings` id=trendyol/hepsiburada/main) → tek firmaya gömülü; SaaS fazında tenant başına şifreli kasaya taşınmalı (sonraki büyük faz).
- 🟡 **Sipariş kaynağı tahmini** → §3.1.
- 🟡 **Tek-kaynak ilkesi:** sabit varsayılan + GPSR tek kaynağa indirildi (bu seans). Aynı disiplin fiyat/KDV/stok/durum-eşleme için de uygulanmalı.

---

## 7) FAZ PLANI (sıra önemli — para ve veri doğruluğu önce)

> Kural: her faz sonunda DoD = ilgili tüm `.py` `ast.parse` OK, `.jsx` `esbuild` OK, ve fazın "tanım" testleri geçer. Claude push yapmaz; zip → Kadir deploy.

**FAZ 1 — PARA DOĞRULUĞU (en acil) 🔴**
- 1a. İade/gider pusulası tabanını taksit-farkındalı yap (`_compute_refund_breakdown` + gider-pusulasi + refund-preview). Test: 6 taksit / tek çekim / kısmi iade senaryoları.
- 1b. KDV'yi kategori→oran tek tablosuna bağla; fatura + pazaryeri fiyatı aynı tablodan okusun.
- **DoD:** taksitli ve tek-çekim siparişte iade/fatura tutarı kuruşu kuruşuna doğru.

**FAZ 2 — FİYAT/MARJ MOTORU 🔴**
- `pricing.py`: maliyet+KDV+komisyon+kargo+hedef marj+yuvarlama → liste/satış fiyatı. Kural önceliği global→marka→kategori→ürün. Komisyon tablosu (pazaryeri+kategori).
- TY/HB push ve stok/fiyat güncellemesi bu motoru kullanır (tek kaynak).
- **DoD:** "şu ürüne %X marj, şu kategoriye %Y komisyon" girince doğru liste fiyatı; manuel hesap yok.

**FAZ 3 — GÜVENİLİRLİK KATMANI (idempotency + retry + durum) 🟠**
- Ortak `mp_jobs` koleksiyonu (push/stok/fiyat/sipariş/fatura/iade için tek şema): status, attempts, last_error, idempotency_key.
- Otomatik retry (üstel bekleme) + dead-letter + tek "Sağlık" ekranı (düz Türkçe hata).
- **DoD:** başarısız hiçbir işlem sessizce kaybolmaz; çift gönderim engellenir.

**FAZ 4 — SİPARİŞ KAYNAĞI + OMS SAĞLAMLAŞTIRMA 🟠**
- Her siparişe kesin `source`+`marketplace_order_id`+`marketplace_status`+`raw_payload`; ön-ek tahmini kaldırılır.
- Idempotent upsert (benzersiz indeks) + otomatik periyodik çekme + gecikme göstergesi + tek durum-eşleme tablosu.
- **DoD:** çift sipariş imkânsız; kaynak %100 doğru; kaçan sipariş alarmı.

**FAZ 5 — İADE/İPTAL TEK AKIŞ + MUTABAKAT 🟠**
- Tek iade durum-makinesi (saga/telafi); pazaryeri+iyzico+stok adımları atomik-benzeri.
- Fiyat/stok/sipariş mutabakat raporları (bizdeki vs onlardaki) + tek-tık düzelt.
- Ticimax iade yolu (taşıma bitip onay gelince) kaldırılır.
- **DoD:** her iade kaynaktan bağımsız aynı, tutarsız ara durum kalmaz.

**FAZ 6 — (SONRA) ÇOK-KİRACILI / SAAS HAZIRLIĞI**
- Tenant modeli + tenant başına şifreli credential kasası + her sorguda izolasyon + onboarding sihirbazı + site-feed (XML/CSV/Ikas/IdeaSoft…) içe-aktarım + alan-eşleme sihirbazı.
- (Bu, çekirdek 5 akış kusursuzlaştıktan sonra; bugünkü önceliğin dışında.)

---

## 8) FAZ-1'DE HAT-HAT SERTİFİKALANACAK DOSYA/FONKSİYON
- `backend/routes/orders.py`: `_compute_refund_breakdown` (4328), `refund_preview` (4397), `gider-pusulasi` (4669), `create_invoice_for_order` (1730), `_havale_invoice_block` (1717).
- `backend/routes/payment.py`: `_installment_total_price` (390), `paidPrice` yazımı (187-198) — siparişe `paid_price` kalıcı yazılıyor mu doğrula.
- KDV kaynağı: `default_vat_rate` kullanılan tüm noktalar → kategori tablosuna taşı.

**Sıradaki adım:** FAZ 1a'yı (taksitli iade/gider pusulası düzeltmesi) uygulamaya hazırım. Onay verirsen önce siparişte `paid_price`/taksit bilgisinin kalıcı tutulduğunu doğrulayıp, tabanı taksit-farkındalı yapan yamayı çıkarırım.
