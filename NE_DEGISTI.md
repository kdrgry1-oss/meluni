# FACETTE — düzeltme paketi (kümülatif)

## YENİ: Toplu kargo barkodu DÜZELTİLDİ + "Kargo Barkodu Yazdır" butonu
- HATA: "Toplu Barkod Oluştur" çalışmıyordu. Kök neden ROUTE ÇAKIŞMASI — tekil
  `/{order_id}/cargo-barcode` route'u daha önce tanımlı olduğu için `/bulk/cargo-barcode`
  isteği order_id='bulk' sanılıp tekil route'a düşüyor, gönderdiğin sipariş listesi yok
  sayılıyordu. Düzeltme: toplu path tek segment yapıldı → `/orders/bulk-cargo-barcode`
  (frontend + backend). Ayrıca sonuç bildirimi netleşti (kaç oluştu / kaç başarısız + ilk hata).
- YENİ BUTON: Bir veya birden fazla sipariş seçilince, en üstteki toplu bar'da
  **"Kargo Barkodu Yazdır"** butonu çıkar. Seçili tüm siparişlerin kargo etiketlerini
  TEK yazdırılabilir pencerede (her etiket ayrı sayfa, 100x120mm) birleştirir.

## Kart ID — buton YOK, otomatik
- Yeni ürünlerde otomatik benzersiz Ürün Kart ID (renk kardeşleri sırayla max+1).
- Mevcut tek çift kayıt: ürünü aç → "Ürün Kart ID" alanını boş numarayla değiştir → Kaydet.

## Ürün görsellerini SÜRÜKLE-BIRAK ile yükleme
- "Ürün Galerisi" kartına dosya sürükleyip bırakma; sadece resim kabul; sıralama korunur.

## (Önceki) düzeltmeler
1. Görsel yüklenmiyor — bozuk URL → `BACKEND_ORIGIN` + `fixImg()` + R2 sertleştirme.
2. Renk başına benzersiz Ürün Kart ID (yeni üründe).
3. Yeni üründe Teknik Detay paneli gizli + resetForm temizliği.
4. AI açıklama önizlemesi düzenlenebilir + placeholder.
5. Kargo barkodu → durum "Hazırlanıyor"; "Kargoya Verildi" gerçek takip kodu gelince.

Deploy sonrası yeşil sinyal: `[scheduler] Background scheduler started`
