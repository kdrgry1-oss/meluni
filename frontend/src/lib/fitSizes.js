// Standart (tek) bedenli ürünler — "hangi bedenlere uyar" yardımcıları.
// Panel (ürün formu) ve vitrin (ürün sayfası) AYNI kuralı kullanır.

// Panelde seçilebilen hazır bedenler (sıra = vitrinde aralık sırası).
export const FIT_SIZE_OPTIONS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL"];

const _ORDER = ["XXS", "XS", "XS/S", "S", "S/M", "M", "M/L", "L", "L/XL", "XL", "XXL", "2XL", "3XL"];
const _STD = new Set(["STD", "STANDART", "STANDARD", "TEK BEDEN", "TEKBEDEN", "ONE SIZE", "ONESIZE", "TEK"]);

const _norm = (s) => String(s || "").trim().toLocaleUpperCase("tr").replace(/\s+/g, " ");

// Ürünün TÜM varyantları standart beden mi? (en az bir varyant olmalı)
export function isStandardSized(variants) {
  const vs = (variants || []).filter((v) => v && String(v.size || "").trim());
  return vs.length > 0 && vs.every((v) => _STD.has(_norm(v.size)));
}

// Seçimi beden sırasına dizer (bilinmeyen/özel bedenler sona, girildiği sırayla).
export function sortFitSizes(list) {
  const arr = [...new Set((list || []).map(_norm).filter(Boolean))];
  return arr.sort((a, b) => {
    const ia = _ORDER.indexOf(a); const ib = _ORDER.indexOf(b);
    if (ia === -1 && ib === -1) return 0;
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

// Vitrin metni: ["S","M","L"] → "Bu ürün S – L bedenler arası uyumludur."
export function fitSizesText(list) {
  const s = sortFitSizes(list);
  if (!s.length) return "";
  if (s.length === 1) return `Bu ürün ${s[0]} bedene uyumludur.`;
  return `Bu ürün ${s[0]} – ${s[s.length - 1]} bedenler arası uyumludur.`;
}
