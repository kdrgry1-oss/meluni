/**
 * Aynı modelin (aynı stok kodu / csv_card_id) farklı renk ürünlerini listede
 * TEK karta indirger. Backend her ürüne `color_group` anahtarı ekler; aynı
 * gruptan sadece İLK görülen ürün kalır (o kart renk swatch'leriyle hepsini
 * temsil eder). Grup anahtarı yoksa ürün olduğu gibi bırakılır.
 */
export function dedupeColorGroups(products) {
  if (!Array.isArray(products)) return [];
  const seen = new Set();
  const out = [];
  for (const p of products) {
    const key = p && p.color_group;
    if (key) {
      if (seen.has(key)) continue;
      seen.add(key);
    }
    out.push(p);
  }
  return out;
}
