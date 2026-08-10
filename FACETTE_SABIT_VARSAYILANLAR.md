# FACETTE — Sabit Varsayılan Özellikler (Tüm Pazaryerleri)

> Kalıcı referans. Bu konuda her seansta sıfırdan yorum yapma; **kuralı buradan oku.**

## 0) ÖNEMLİ — HB şeması erişilebilir
Hepsiburada kategori özellik şeması **cache'lidir ve okunabilir**:
- Koleksiyon: `db.hepsiburada_category_attributes`
- Çekme/okuma: `_fetch_hb_category_attributes` (category_mapping.py), `_hb_category_attributes_for` (integrations.py)
- HB'ye canlı ürün aktarımı yapıldı; şema elimizde.
**Claude "HB şemasına erişemiyorum" DEMEZ.** Hangi özelliğin HB'de olup olmadığı tahminle değil, bu cache'ten / formdaki HB bölümünden okunarak söylenir.

## 1) Sabit varsayılan değerler (9 adet, isimle)
Tek kaynak: `backend/facette_defaults.py`

| Özellik | Değer | Trendyol | Hepsiburada |
|---|---|---|---|
| Menşei | Türkiye (TR) | ✅ | ✅ (HB'de "Menşei" varsa) |
| Cinsiyet | Kadın | ✅ | ✅ |
| Yaş Grubu | Yetişkin | ✅ | ⚠️ kategoriye göre |
| Ortam | Casual/Günlük | ✅ | ❌ HB'de hane yok |
| Koleksiyon | Casual/Günlük | ✅ | ❌ HB'de hane yok |
| Ek Özellik | Mevcut Değil | ✅ | ❌ HB'de hane yok |
| Kutu Durumu | Kutu Yok | ✅ | ❌ HB'de hane yok |
| Persona | Fashion Forward | ✅ | ❌ HB'de hane yok |
| Performans | Cool & Comfort | ✅ | ❌ HB'de hane yok |
| Sürdürülebilirlik Detayı | Hayır | ✅ | ❌ HB'de hane yok |

**Gerçek:** 9'un hepsi Trendyol'da var. HB'de **sadece eşleşenler** (gerçekçi olarak Cinsiyet, Menşei, kategoriye göre Yaş Grubu) gider. Geri kalanlar Trendyol'a özgü kavramlardır — HB şemasında karşılığı YOKTUR, gönderilmez (bu hata değil, doğru davranış). Bu 6'sı formda **manuel-doldurulabilir** olarak HB bölümünde de görünür ama push'a gitmez.

## 2) Kural (push)
Değerler **isimle** tutulur (value_id değil). Her pazaryeri push çekirdeği değeri **o pazaryerinin o kategorideki özellik listesinde ada göre arar**:
- Varsa → kendi value_id'sine çözer (TR↔Türkiye) ve gönderir.
- Yoksa → sessizce atlar. **Mevcut akış asla bozulmaz.**
- **GAP-FILL:** ürün/kategori o özelliği zaten taşıyorsa DOKUNULMAZ.

Bu, Trendyol mantığının aynısıdır; HB de aynı isim-çözümlemeyle çalışır.

## 3) GPSR — Üretici / İthalatçı (ayrı alan DEĞİL, free-text attribute)
İsim-eşleme **TEK KAYNAK**: `facette_defaults.company_field_for_attr(attr_name)` → `company_name|email|address|None`.
- Push gap-fill: `facette_company_value()` bunu FACETTE_COMPANY ile kullanır.
- Kategori şirket-doldurma: `category_mapping._resolve_company_value()` bunu settings.main.company_info ile kullanır (mükerrer mantık kaldırıldı, delege eder).

Eşleme:
- "Üretici/İthalatçı … Adı" → **FACETTE DIŞ TİCARET A.Ş.**
- "… Mail …" → **info@facette.com.tr**
- "… Adres …" → **İkitelli O.S.B. İmsan San. Sit. D BLOK NO:3**
- Birincil/İkincil/Üçüncül İthalatçı → aynı değerler.

## 4) Enjeksiyon noktaları (kod)
- Ortak kaynak: `backend/facette_defaults.py` → `facette_fixed_value_for(attr_name)`
- **Trendyol:** `integrations.py` → `resolve_attributes` sonundaki son pass (sadece `processed` olmayan TY özelliğine).
- **Hepsiburada:** `integrations.py` → `_build_hb_product_item` → KATMAN 2 raw zinciri: `… or gad.get(anorm) or facette_fixed_value_for(aname)`. Döngü `for a in hb_attrs_list:` olduğu için **yalnız HB'nin gerçekten sahip olduğu özelliklere** uygulanır.
- **Temu:** `integrations_temu.py` → `temu_create_product` gap-fill. (Not: Temu ürün-create hâlâ passthrough scaffold; gerçek builder bağlanınca devreye girer.)

## 5) Form (Products.jsx)
- Yeni üründe 9 sabit `formData.attributes`'a seed edilir; her kayıtta boş kalanlar gap-fill (kullanıcı değeri ezilmez).
- `renderSection` her pazaryeri bölümünde 9 sabiti **dolu** gösterir: değer önce pazaryerine-özel haritadan, yoksa nötr `formData.attributes`'tan, yoksa sabitten (`_effVal`). Bölüm listesinde yoksa dolu satır olarak enjekte edilir (`fixedRows`).
- **Form'da dolu görünmesi ≠ pazaryerine gitmesi.** Push kuralı §2'dir. HB'de hane yoksa form dolu olsa da gitmez.
- Gizli özellikler (`hiddenAttrNames`, normalize): beden/renk/web color/yaka + Alt/Üst Silüet, Kesim, Özellik, Stil, Ürün İçerik Bilgisi, Kumaş, Yıkama Talimatı, Materyal Analiz Testi, Kimyasal Analiz Testi, Silüet, Ürün Tipi, Ürün Detayı. **Alt-Üst Takım HARİÇ (gizlenmez).** Sadece form görünümü; veri korunur, push etkilenmez.

## 6) Sabit kurallar
- Tüm `.py` → `ast.parse` OK; tüm `.jsx` → `esbuild` OK.
- Claude `git push` çalıştırmaz; zip → `/mnt/user-data/outputs/`, Kadir deploy eder.
- Repoyu baştan sona tarama; sadece gereken fonksiyonu oku.
