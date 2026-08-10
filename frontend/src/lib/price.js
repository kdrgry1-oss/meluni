// TEK KAYNAK — ürün fiyat/indirim görünümü. Vitrin kartı, arama, menü, kombin, öneri,
// kasa-önü, ürün detay… HER YER burayı kullanır ki indirimler tutarlı görünsün.
//
// İndirim sırası (sepet motoruyla AYNI mantık):
//   1) Liste fiyatı (price)
//   2) Ürünün kendi indirimli fiyatı (sale_price) — varsa ve < price
//   3) Sepette otomatik kampanya (campaign_discount_percent) — sale_price üzerine uygulanır
//
// NOT: Kupon kodu / hoşgeldin / havale gibi SEPET-seviyesi indirimler ürün kartında
// gösterilmez (sepette/siparişte ayrı satır olarak görünür); kartta yalnızca ürüne
// bağlı indirim (sale_price + otomatik kampanya) yansır.
export function priceView(product) {
  const p = product || {};
  const list = Number(p.price || 0);            // satış fiyatı (MSRP)
  const spRaw = Number(p.sale_price || 0);
  const sale = spRaw > 0 && spRaw < list ? spRaw : null;   // indirimli fiyat (varsa)
  const campPct = Number(p.campaign_discount_percent || 0);

  // ÜSTÜ ÇİZİLİ (referans) fiyat + KIRMIZI (nihai) fiyat — Kadir kuralı:
  //  • Kampanya VAR + indirimli fiyat GİRİLMİŞ → referans = İNDİRİMLİ fiyat, kırmızı =
  //    indirimli fiyat × (1-kampanya). (Satış fiyatı/MSRP gösterilmez — "1900 yerine indirimli")
  //  • Kampanya VAR + indirimli fiyat YOK → referans = satış fiyatı, kırmızı = satış × (1-kampanya).
  //  • Kampanya YOK → mevcut: satış fiyatı üstü çizili, indirimli (varsa) kırmızı.
  let ref, display;
  if (campPct > 0 && sale != null) {
    ref = sale;
    display = Math.round(sale * (1 - campPct / 100) * 100) / 100;
  } else if (campPct > 0) {
    ref = list;
    display = Math.round(list * (1 - campPct / 100) * 100) / 100;
  } else {
    ref = list;
    display = sale != null ? sale : list;
  }
  const hasDiscount = display < ref - 0.001;
  const discountPct = ref > 0 && display < ref
    ? Math.round(((ref - display) / ref) * 100)
    : 0;
  return {
    list: ref,                  // üstü çizili referans fiyat (kampanya+indirimli → indirimli fiyat)
    display,                    // gösterilecek nihai (kırmızı) fiyat
    hasDiscount,                // indirim var mı
    discountPct,                // rozet yüzdesi (referansa göre)
    salePrice: sale,            // ürünün kendi indirimi (yoksa null)
    campaignPct: campPct,       // otomatik kampanya yüzdesi
    campaignLabel: p.campaign_label || "",
  };
}

// Kısa yardımcı: TL biçimi (virgüllü).
export function fmtTL(n) {
  return (Number(n) || 0).toFixed(2).replace(".", ",") + " TL";
}

// SEPET KALEMİ görünümü — kalem eklenirken saklanan listPrice/price/campaignPct'ten
// birim indirim durumunu çıkarır. Böylece sepete ekler eklemez "indirim uygulandı"
// (üstü çizili liste + indirimli birim) satırda görünür.
//   listUnit : üstü çizili liste birim fiyatı
//   unit     : indirimli birim fiyat (sale_price + otomatik kampanya)
export function cartLineView(item) {
  const it = item || {};
  const money = Number(it.price || 0);                 // sale_price tabanı (para hesabı)
  const camp = Number(it.campaignPct || 0);
  const unit = camp > 0 ? Math.round(money * (1 - camp / 100) * 100) / 100 : money;
  const listUnit = Number(it.listPrice != null ? it.listPrice : money) || unit;
  const hasDiscount = listUnit > unit + 0.001;
  const discountPct = hasDiscount ? Math.round(((listUnit - unit) / listUnit) * 100) : 0;
  return { listUnit, unit, hasDiscount, discountPct, campaignPct: camp };
}
