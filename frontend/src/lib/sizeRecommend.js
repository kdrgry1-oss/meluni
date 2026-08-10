// Üyenin boy/kilo bilgisinden EN UYGUN bedeni önerir (kadın hazır giyim, kaba heuristik).
// Amaç: ürün sayfasında "M — Sizin için öneriliyor" rozeti göstermek. Kesin ölçü değildir;
// mağaza beden tablosuyla ince ayar yapılabilir.
const SIZES = ["XS", "S", "M", "L", "XL", "XXL"];
// Harf beden ↔ Türk kadın numarası karşılığı (numaralı bedenli ürünlerde de eşleşsin)
const LETTER_TO_NUMERIC = { XS: "34", S: "36", M: "38", L: "40", XL: "42", XXL: "44" };

export function recommendLetterSize(heightCm, weightKg) {
  const h = Number(heightCm) || 0;
  const w = Number(weightKg) || 0;
  if (!w) return null; // kilo yoksa öneri yok

  // Ağırlık bazlı temel band (kadın hazır giyim)
  let band;
  if (w < 50) band = 0;        // XS
  else if (w < 58) band = 1;   // S
  else if (w < 67) band = 2;   // M
  else if (w < 78) band = 3;   // L
  else if (w < 92) band = 4;   // XL
  else band = 5;               // XXL

  // Boy düzeltmesi: uzun boyda aynı kilo daha ince oturur → bir beden küçük;
  // kısa boyda → bir beden büyük.
  if (h >= 178 && band > 0) band -= 1;
  else if (h > 0 && h <= 158 && band < 5) band += 1;

  return SIZES[Math.max(0, Math.min(5, band))];
}

// Verilen beden etiketi (harf ya da numara) kullanıcıya önerilen bedenle eşleşiyor mu?
export function isRecommendedSize(sizeLabel, heightCm, weightKg) {
  const rec = recommendLetterSize(heightCm, weightKg);
  if (!rec) return false;
  const s = String(sizeLabel || "").trim().toUpperCase().replace(/\s+/g, "");
  if (!s) return false;
  if (s === rec) return true;
  if (LETTER_TO_NUMERIC[rec] === s) return true;
  // "M/38" gibi birleşik etiketler
  if (s.includes(rec) || s.includes(LETTER_TO_NUMERIC[rec])) return true;
  return false;
}
