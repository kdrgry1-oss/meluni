# FACETTE — 5 maddelik düzeltme paketi

## 1) Görsel yüklenmiyor — KÖK NEDEN DÜZELTİLDİ
- **frontend/src/pages/admin/Products.jsx** ve **PageDesign.jsx**: `API.replace('/api','')`
  hatalıydı → `"https://api.facette.com.tr/api"` içindeki İLK `/api` (yani `//api`) silinip
  `https:/.facette.com.tr/api` (bozuk URL) üretiliyordu. Bu yalnızca yükleme R2 yerine
  DB/disk fallback'e düşünce (relatif URL dönünce) tetikleniyordu → bazı üründe görsel var,
  bazında yok. `BACKEND_ORIGIN` ile doğru origin türetildi.
- `fixImg()` yardımcısı: geçmişte kaydedilmiş bozuk URL'leri RENDER anında onarır (liste + galeri).
- Görsel inputu artık ayrı `imageInputRef` kullanıyor (Excel-import ref'iyle çakışma giderildi).
- **backend/services/r2_storage.py**: `is_enabled()` artık `R2_PUBLIC_URL` de ister (eksikse
  R2 kapalı sayılır → güvenilir DB/disk servisi). 
- **backend/routes/upload.py**: `_serve` yalnızca mutlak `http(s)` r2_url'e yönlenir.
  > NOT: Railway'de **R2_PUBLIC_URL** env'i set mi kontrol et — yoksa zaten bu fix devreye girip
  > DB/disk'ten servis edecek; istersen R2_PUBLIC_URL ekleyince CDN'e döner.

## 2) Maris/Vesper aynı Ürün Kart ID — her renk artık BENZERSİZ
- **backend/routes/products.py**: `urun_karti_id` artık `csv_card_id`'ye fallback ETMİYOR.
  Çok renkli üründe her renk benzersiz kart id alır (ilk renk taban, sonrakiler max+1).
  Renk kardeşliği `csv_card_id` (paylaşımlı) ile korunur → storefront "Diğer Renkler" bozulmaz.
  (Trendyol productMainId = stock_code olduğu için etkilenmez.)
- **frontend/src/pages/admin/Products.jsx** (`handleSubmit`): çok renkli kayıtta tüm renkler
  aynı `csv_card_id`'yi paylaşır; ilk renk taban kart id, sonrakiler urun_karti_id GÖNDERMEZ
  (backend +1 ile otomatik atar).
- **EKRANDAKİ MEVCUT ÇİFTLER İÇİN** → tek seferlik migration:
  `cd backend && python -m scripts.dedupe_card_ids`        (DRY-RUN, önce bunu çalıştır/incele)
  `cd backend && python -m scripts.dedupe_card_ids --apply` (uygula)
  Her grupta en eski ürün taban id'de kalır, kardeşler max+1 ile benzersizleşir; `csv_card_id`
  korunur (renkler bağlı kalır), `slug` değişmez (linkler kırılmaz).

## 3) Yeni üründe Teknik Detay paneli kaldırıldı
- **frontend/src/pages/admin/Products.jsx**: `TeknikDetayPanel` artık SADECE mevcut ürün
  düzenlenirken görünür; yeni ürün oluştururken gizli. `resetForm`'a `setTechnicalDetails({})`
  eklendi (önceki düzenlemeden teknik detay yeni ürüne taşınmaz).

## 4) AI açıklama — önizleme de düzenlenebilir
- **frontend/src/pages/admin/Products.jsx** (`DescriptionEditor`): Önizleme artık
  `contentEditable` (WYSIWYG). İmleci tıklayıp doğrudan yazabilirsin; değişiklik kaynağa da
  işlenir. İmleç sıçraması ref+effect ile önlendi. **index.css**'e boşken placeholder kuralı eklendi.

## 5) Kargo barkodu → durum "Hazırlanıyor" (Kargoya Verildi DEĞİL)
- **backend/routes/orders.py** (`create_cargo_barcode`): barkod oluşturunca durum artık
  `preparing` (Hazırlanıyor). Prematüre `order_shipped` bildirimi kaldırıldı; yerine config'e
  bağlı `order_preparing` bildirimi (Ayarlar > Sipariş Durumları'nda açıksa).
- "Kargoya Verildi" (`shipped`) geçişi + bildirimi, **scheduler `_dhl_cargo_poll_tick`** zaten
  gerçek DHL/MNG takip kodu + hareket statüsü yakalayınca yapıyor (değişiklik gerekmedi).

---
Deploy sonrası Railway yeşil sinyali: `[scheduler] Background scheduler started`
