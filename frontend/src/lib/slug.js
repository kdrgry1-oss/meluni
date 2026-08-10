// Türkçe-duyarlı slug üretimi.
// JS'te "İ".toLowerCase() => "i̇" (i + U+0307 birleşik nokta) ürettiği için naif
// slug'lar "alt-gi̇yi̇m" gibi BOZUK çıkıyordu (kategori linkleri sonuç döndürmüyordu).
// Burada Türkçe harfler önce ASCII'ye map'lenir, sonra kalan birleşik işaretler temizlenir.
// Backend generate_slug() ile birebir aynı sonucu verir (alt-giyim, tisort, sort, ...).
const TR_MAP = {
  ı: "i", İ: "i", I: "i", i: "i",
  ş: "s", Ş: "s",
  ç: "c", Ç: "c",
  ğ: "g", Ğ: "g",
  ö: "o", Ö: "o",
  ü: "u", Ü: "u",
};

export function slugify(name) {
  let s = String(name == null ? "" : name);
  // Türkçe harfleri önce çevir (toLowerCase'in İ→i̇ bug'ına girmeden)
  s = s.replace(/[ıİIişŞçÇğĞöÖüÜ]/g, (c) => TR_MAP[c] || c);
  s = s.toLowerCase();
  // toLowerCase sonrası kalan birleşik işaretleri (U+0300–U+036F) temizle
  s = s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  s = s.replace(/[^a-z0-9\s-]/g, "");
  s = s.replace(/[\s_]+/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
  return s;
}

export default slugify;
