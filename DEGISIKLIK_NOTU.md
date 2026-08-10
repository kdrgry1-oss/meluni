# Paket 5 (KÜMÜLATİF) — Meta Feed + Kupon + SEO + Taksit + Güven/Teslimat + Taksonomi

> Önceki tüm paketleri içerir. Tek başına deploy edilebilir.

## YENİ (bu pakette): Trendyol TOPLU yorum çekme — 4-5 yıldız, tüm ürünler
- backend/routes/integrations_trendyol_qna.py:
  • Mevcut tekil scrape altyapısı korundu; ÜZERİNE toplu otomatik akış eklendi.
  • sync_all_trendyol_reviews_core(min_rating=4, limit, dry_run): Trendyol getProducts
    (approved) ile barcode→contentId haritası kurar → her site ürününün barcode'ları
    (ana + varyant) eşlenir → bulunan contentId'ler için public yorum API'sinden
    >= min_rating yorumlar çekilir → product_reviews'a yazılır (external_id ile dedup,
    idempotent) → ürün rating/review_count güncellenir.
  • Yeni endpoint: POST /api/integrations/trendyol/reviews/sync-all (admin)
    Body: { "min_rating": 4, "limit": 0, "dry_run": false }
  • Trendyol alan adı: getProducts response'unda contentId (productContentId DEĞİL) — doğrulandı.
- backend/scripts/sync_trendyol_reviews.py (YENİ): container CLI.
    DRY-RUN: python -m scripts.sync_trendyol_reviews --min-rating 4 --dry-run
    CANLI  : python -m scripts.sync_trendyol_reviews --min-rating 4
    Test   : python -m scripts.sync_trendyol_reviews --min-rating 4 --limit 20
  • Çekilen yorumlar approved:True (storefront'ta görünür); source=trendyol_public.

## YENİ (bu pakette): Meta feed — g:size kaldırıldı (kontrol raporu 2026-06-18)
- backend/routes/products.py grouped feed (google/generic): <g:size> BASIMI KALDIRILDI.
  Eski feed 66 üründe tutarsız "STD" size basıyordu; rapor "hiç size yazma, renk-düzeyi temiz feed" dedi.
  Doğrulama (yedek XML): 309 item / 309 item_group_id / 309 color KORUNDU, 66 STD size → 0.
- DOKUNULMAYAN (rapor kuralı): g:id, item_group_id, color, diğer alanlar; feature-flag; facebook varyant-feed.
  Boş <g:size></g:size> basılmıyor (satır tamamen kaldırıldı).

## YENİ (bu pakette): Anasayfa arama — Zara BİREBİR (zara.com/tr tarayıcıdan incelendi)
- frontend/src/components/Header.jsx:
  • Tetikleyici (Zara birebir): lupa + "ARA" sola yaslı, altında ~2x uzun ince çizgi (w-24 / md:w-32).
    Çizgi butonun KENDİ genişliğinde (absolute değil) → kalp/favori ikonuna asla değmez. Mobilde de aynı.
  • Overlay artık Zara'nın gerçek arama ekranını (/search/home) birebir taklit ediyor:
    - Üst bar: sol "FACETTE" logo (anasayfaya link) + sağ "Kapat ✕".
    - 3 kolon grid (md+): SOL dikey kategori menüsü (Giyim/Aksesuar/Sale/Ana Sayfa) |
      ORTA büyük input ("Ne arıyorsunuz?", alt çizgi, yazınca X temizle / boşken lupa) |
      SAĞ hesap nav (Sepet(n) → drawer / Hesabım|Giriş Yap / Favoriler(n)).
    - Mobil (md altı): kategori+hesap linkleri input altında yatay wrap.
    - ALT: boş aramada "İlginizi çekebilecek diğer ürünler" + ürün grid (GET /products?sort=popular&limit=8);
      yazınca "Ürünler" canlı sonuç grid + "Tüm sonuçları gör". Zara'da boş aramada da ürün gösteriliyor — birebir.
  • Fonksiyon korundu: searchOpen, GET /products?search (canlı), GET /search/popular, submitSearch → /arama?q=,
    setIsOpen (sepet drawer), user (Hesabım/Giriş), favCount/itemCount sayaçları.
  • Yeni state suggestedProducts; overlay açılınca popüler ürünler çekilir. closeSearch helper (DRY).
  • Zara davranışı: ESC + scroll kilidi + yumuşak açılış (fade+iniş) korundu.

## YENİ (bu pakette): Duplike kategori birleştirme HAZIRLIĞI (script + slug_aliases link koruması)
- backend/scripts/merge_duplicate_categories.py (YENİ): clean_categories.py raporunun UYGULAMA adımı.
  DRY-RUN varsayılan; --apply yalnızca GÜVENLİ duplike'leri (aynı isim + aynı parent) birleştirir.
  Farklı parent'taki aynı isimliler ATLANIR. --apply önce /tmp/category_merge_backup_<ts>.json geri-dönüş
  log'u yazar (etkilenen ürünlerin eski category_ids/slug/name'i), sonra ürünleri ana'ya taşır
  (category_ids dedup'lı), duplike slug'ı ana'nın slug_aliases'ına ekler, duplike kategoriyi siler.
  Akış: clean_categories (rapor) → merge DRY-RUN → merge --apply.
- backend/routes/categories.py get_category: slug_aliases ile de arar → birleştirilen eski kategori linki kırılmaz.
- backend/routes/products.py slug→category_ids çevirisi: slug_aliases eşleşmesi eklendi → eski kategori URL'i
  ana kategorinin ürünlerini gösterir (tam link koruması).

## YENİ (bu pakette): P2-13 yorum/puan — storefront PDP entegrasyonu
- Backend reviews API (extras.py: POST /reviews, GET /reviews/product/:id, admin moderasyon) ZATEN vardı;
  admin sayfası (ProductReviews.jsx → /admin/yorumlar) ZATEN bağlıydı. EKSİK olan storefront UI eklendi.
- frontend/src/pages/ProductDetail.jsx: "Değerlendirmeler" bölümü — ortalama yıldız + onaylı yorum listesi
  (kullanıcı adı, yıldız, başlık, yorum, tarih, FACETTE admin yanıtı) + giriş yapan kullanıcıya yorum formu
  (1-5 yıldız + başlık + yorum). POST sonrası "moderasyon sonrası yayınlanır" bilgisi. Giriş yoksa /giris linki.
- Backend modeline birebir uyumlu (rating 1-5, title≤120, comment≤2000, status pending→approved).

## YENİ (bu pakette): P3-19 çift success route → tek canonical + 301
- frontend/public/_redirects (YENİ): /siparis-tamamlandi/* → /order-success/:splat 301 (Cloudflare gerçek 301, SEO tek canonical).
- frontend/src/App.js: Navigate+useParams import; /siparis-tamamlandi/:orderNumber artık OrderSuccess yerine
  <LegacyOrderRedirect> (SPA-içi client redirect, _redirects'in SPA fallback'i). /order-success canonical kalır.

## YENİ (bu pakette): backend/scripts/clean_categories.py — kategori temizlik scripti
- DRY-RUN varsayılan (hiçbir şey değişmez): test/placeholder kategori + bağlı ürün sayısı,
  duplike gruplar (aksesuar + aksesuar-aksesuar) + önerilen ANA + 301 önerisi, bozuk slug'lar raporlanır.
- `--apply` SADECE güvenli işler: ürünsüz test kategorisini siler + bozuk slug'ı düzeltir (eski slug slug_aliases'a).
- Duplike BİRLEŞTİRME (ürün taşıma) OTOMATİK YAPILMAZ — geri alınamaz; rapora göre onayla.
- Çalıştırma (backend container): `python -m scripts.clean_categories`  (uygula: `--apply`)

## backend/routes/products.py — Meta feed item_group_id + color + size (flag; ?group=off ile geri al). g:id sabit.
## backend/routes/categories.py — get_categories visible_only param (storefront pasif+test gizle; admin korunur).
## frontend SlugRouter/MiuMiuTheme/Category — /categories?visible_only=true.
## frontend Checkout — kupon otomatik indirim onayı + katlanır alan + güven şeridi + tahmini teslimat.
## frontend ProductDetail — JSON-LD duplicate fix + taksit + görünür tahmini teslimat.
## frontend Cart — taksit bilgisi.

## Doğrulama
- products.py / categories.py / clean_categories.py: ast.parse OK; feed sim + slug/test/duplike mantığı doğrulandı
  (İç Giyim→ic-giyim, Şort→sort; HB_CAT_TEST yakalanıyor; Giyim/kontes korunuyor).
- 6 frontend dosyası: esbuild (jsx=automatic) exit 0.
