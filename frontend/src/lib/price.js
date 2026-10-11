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
  // KURAL (kullanıcı): ürün kartında İNDİRİMLİ FİYAT (sale_price) girili ise KAMPANYA UYGULANMAZ —
  // indirimli fiyat geçerlidir. Kampanya yalnız indirimli fiyatı OLMAYAN üründe uygulanır.
  // (Sepet motoru coupons._compute_discount ve rozet _apply_campaign_badge de aynı kuralı uygular.)
  let ref, display, campApplied = 0;
  if (sale != null) {
    ref = list;
    display = sale;                        // indirimli fiyat geçerli, kampanya iptal
  } else if (campPct > 0) {
    ref = list;
    display = Math.round(list * (1 - campPct / 100) * 100) / 100;
    campApplied = campPct;
  } else {
    ref = list;
    display = list;
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
    campaignPct: campApplied,   // UYGULANAN kampanya yüzdesi (indirimli fiyat varsa 0 — kampanya iptal)
    campaignLabel: campApplied > 0 ? (p.campaign_label || "") : "",
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


/**
 * Sepet özeti (sepet sayfası + çekmece ORTAK). `promoDiscount` = /coupons/evaluate
 * total_discount: kalem fiyatı (sale_price tabanı) ÜZERİNDEN hesaplanır → indirimli-fiyat
 * farkını İÇERMEZ; yalnız kampanya/kupon indirimidir. Eskiden bu tutar tüm ürün-seviyesi
 * indirimden (indirimli fiyat farkı DAHİL) düşülüyordu → indirimli ürünlerde "3 Al 2 Öde"
 * gibi sepet kampanyası ekranda hiç düşülmüyordu (ödeme adımı ve sipariş doğruydu).
 * Kalemde gösterilen otomatik yüzde (campaignPct) evaluate'in içinde de olduğundan yalnız
 * o kısım mahsup edilir.
 */
export function cartSummary(items, promoDiscount) {
  let listSum = 0, moneySum = 0, effSum = 0;
  for (const it of items || []) {
    const v = cartLineView(it);
    const q = Number(it.quantity || 0);
    listSum += v.listUnit * q;
    moneySum += Number(it.price || 0) * q;
    effSum += v.unit * q;
  }
  const r2 = (x) => Math.round(x * 100) / 100;
  const saleDisc = Math.max(0, listSum - moneySum);          // indirimli fiyat farkı
  const lineCampDisc = Math.max(0, moneySum - effSum);       // kalemde gösterilen otomatik %
  const extraDisc = Math.max(0, Number(promoDiscount || 0) - lineCampDisc);  // sepet-seviyesi kampanya/kupon
  const productDisc = saleDisc + lineCampDisc;
  return {
    listSum: r2(listSum), effSum: r2(effSum), productDisc: r2(productDisc),
    extraDisc: r2(extraDisc), totalDisc: r2(productDisc + extraDisc),
    grand: r2(Math.max(0, effSum - extraDisc)),
  };
}
