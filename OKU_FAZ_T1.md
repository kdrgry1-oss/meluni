# FAZ T1 — Trendyol özellik cache'i TEK KAYNAK (dağıtım)

## Ne değişti? (tek dosya: backend/routes/integrations.py · 30 ekleme / 4 silme)
1. **Editör "özellikleri yenile" → push'un cache'ini de tazeler.**
   `get_trendyol_category_attributes` artık `trendyol_attributes` ile birlikte,
   gönderimin okuduğu `trendyol_category_attributes` koleksiyonunu da aynı veriyle yazar.
   Yanıta `count` (özellik) ve `value_count` (izinli değer) eklendi.
2. **Push emniyeti:** `_get_attr_meta` kanonik cache boşsa `trendyol_attributes`'a düşer.
   Böylece daha önce yalnız editörden yenilenmiş kategoriler için push artık eksik şemayla göndermez.

## Neden güvenli?
- Hiçbir mevcut okuyucu/yazıcı kaldırılmadı (attributes.py, kategori-eşleme aynen çalışır).
- Gönderim (resolve/payload) mantığı DEĞİŞMEDİ — sadece okunan cache'in dolu/güncel olması garanti altına alındı.
- Cache zaten doğruysa TY payload'u birebir aynı. Sadece daha önce BOZUK (boş cache) olan
  kategorilerde özellikler artık doğru çözülür — bu zaten istenen düzeltme.
- `ast.parse` ✓ geçti.

## Dağıtım
1. ZIP'i ~/Downloads/facette_deploy içine aç.
2. `backend/routes/integrations.py` dosyasını repodaki aynı yola kopyala (üzerine yaz).
3. (Opsiyonel) `docs/TRENDYOL_DENETIM_VE_PLAN.md` dosyasını repo köküne/doc'a koy — istemiyorsan kopyalama.
4. commit + push.
5. Railway boot teyidi: log'da `[scheduler] Background scheduler started` satırını gör → YEŞİL.

## Doğrulama (deploy sonrası)
- Ürün editöründe bir TY kategorisinde "özellikleri yenile" → yanıtta `count`/`value_count` gelir.
- Aynı kategoride bir ürünü Trendyol'a gönder → özellikler tam gitmeli (önce eksik gidenler dahil).

## Sıradaki (bu pakette YOK — senin onayını bekliyor)
- T2: gizli ayarları UI'da göster (stok kodu/barkod aktarma filtresi, stok/fiyat anahtarı).
- T3: white-label sabitleri (kargo `10`, marka `975755`, KDV) ayara taşı.
- Not: Ayrıca `category_mapping.py`'nin TY tazeleme bloğu client'ın liste dönüşünde `.get`
  çağırıyor (muhtemelen o "canlı çek" butonunu hataya düşürüyor). T1 bunu editör yoluyla
  zaten aşıyor; istersen ayrı bir mini-fix yaparım.
