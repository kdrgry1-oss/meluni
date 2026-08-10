// Renk adı (TR/EN) → CSS rengi. Ürün swatch'lerinde görsel yerine SOLID renk
// kutucuğu çizmek için kullanılır. Eşleşme yoksa null döner (çağıran taraf
// gri placeholder veya çok-renkli işaret gösterebilir).

const COLOR_MAP = {
  // Nötrler
  siyah: "#111111", black: "#111111",
  beyaz: "#ffffff", white: "#ffffff", ak: "#ffffff",
  gri: "#9ca3af", gray: "#9ca3af", grey: "#9ca3af",
  "açık gri": "#d1d5db", "acik gri": "#d1d5db", "light gray": "#d1d5db",
  "koyu gri": "#4b5563", "dark gray": "#4b5563",
  antrasit: "#383838", anthracite: "#383838", "füme": "#6b7280", fume: "#6b7280",
  gümüş: "#c0c0c0", gumus: "#c0c0c0", silver: "#c0c0c0",

  // Kırmızı / pembe / bordo
  "kırmızı": "#dc2626", kirmizi: "#dc2626", red: "#dc2626",
  bordo: "#7f1d1d", burgundy: "#7f1d1d", "şarabi": "#6d1a2a", sarabi: "#6d1a2a",
  pembe: "#ec4899", pink: "#ec4899",
  "açık pembe": "#f9a8d4", "pudra": "#f3c5c5", powder: "#f3c5c5",
  "fuşya": "#d6246e", fusya: "#d6246e", fuchsia: "#d6246e", "fuchsia pink": "#d6246e",
  somon: "#fa8072", salmon: "#fa8072",
  "mürdüm": "#5b1a3a", murdum: "#5b1a3a", plum: "#5b1a3a",
  "gül kurusu": "#b76e79", "gul kurusu": "#b76e79", "rose": "#b76e79",

  // Turuncu / sarı / kahve / bej
  turuncu: "#f97316", orange: "#f97316",
  "kiremit": "#b45f3a", terracotta: "#b45f3a", taba: "#9c5b34",
  "sarı": "#facc15", sari: "#facc15", yellow: "#facc15",
  "hardal": "#d4a017", mustard: "#d4a017",
  altin: "#d4af37", "altın": "#d4af37", gold: "#d4af37", dore: "#d4af37", "dore": "#d4af37",
  kahverengi: "#7c4a2d", kahve: "#7c4a2d", brown: "#7c4a2d",
  "açık kahve": "#a47148", camel: "#c19a6b", deve: "#c19a6b",
  bej: "#e3d5b8", beige: "#e3d5b8", krem: "#f5f0e1", cream: "#f5f0e1",
  ekru: "#f0ead6", ecru: "#f0ead6", naturel: "#efe8d8", natural: "#efe8d8",
  vizon: "#9b8579", mink: "#9b8579", "açık vizon": "#bcae9f",
  haki: "#6b6b3a", khaki: "#6b6b3a", "yağ yeşili": "#5b5e33",

  // Yeşil
  "yeşil": "#16a34a", yesil: "#16a34a", green: "#16a34a",
  "açık yeşil": "#86efac", "koyu yeşil": "#14532d", "dark green": "#14532d",
  zumrut: "#046307", "zümrüt": "#046307", emerald: "#046307",
  "su yeşili": "#a7e8d2", mint: "#a7e8d2", nane: "#a7e8d2",
  fistik: "#9bcd5a", "fıstık": "#9bcd5a", olive: "#808000", "zeytin": "#808000",
  petrol: "#1b5566", "petrol yeşili": "#1b5566",

  // Mavi
  mavi: "#2563eb", blue: "#2563eb",
  lacivert: "#1e2a52", navy: "#1e2a52", "navy blue": "#1e2a52",
  "açık mavi": "#93c5fd", "light blue": "#93c5fd", "bebek mavisi": "#bcd9f2",
  "koyu mavi": "#1e3a8a", "dark blue": "#1e3a8a",
  turkuaz: "#14b8a6", turquoise: "#14b8a6", "deniz mavisi": "#1f6f8b",
  indigo: "#4338ca", "kot": "#3b5b80", denim: "#3b5b80", jean: "#3b5b80",
  "bebe mavi": "#bcd9f2",

  // Mor / lila
  mor: "#7c3aed", purple: "#7c3aed", violet: "#7c3aed",
  lila: "#c4b5fd", lilac: "#c4b5fd", "açık mor": "#c4b5fd",
  leylak: "#b39ddb", lavanta: "#b9a7d9", lavender: "#b9a7d9",
};

// Çok renkli / desenli ifadeler — tek renk verilemez, conic-gradient ile gösterilir.
const MULTI = ["ekose", "çizgili", "cizgili", "desenli", "çiçekli", "cicekli", "leopar",
  "kareli", "puantiye", "etnik", "batik", "çok renkli", "cok renkli", "multi", "renkli",
  "karışık", "karisik", "baskılı", "baskili", "print", "floral", "striped", "plaid"];

function normalize(raw) {
  return (raw || "")
    .toString()
    .toLocaleLowerCase("tr")
    .replace(/\s*(rengi|renk|color)\s*$/i, "")
    .replace(/[._/]+/g, " ")
    .trim();
}

/**
 * Renk adından CSS rengi döndürür.
 * @returns {{ type: "solid", value: string } | { type: "multi" } | null}
 */
export function resolveColor(raw) {
  const n = normalize(raw);
  if (!n) return null;
  if (COLOR_MAP[n]) return { type: "solid", value: COLOR_MAP[n].trim() };
  // Çok-renkli / desenli mi?
  for (const m of MULTI) if (n.includes(m)) return { type: "multi" };
  // İçinde bilinen bir renk geçiyor mu? (ör. "koyu lacivert", "mat siyah")
  const words = n.split(" ");
  // Önce iki kelimelik tam eşleşmeleri dene (zaten yukarıda denendi), sonra tekil kelime
  for (let i = words.length - 1; i >= 0; i--) {
    if (COLOR_MAP[words[i]]) return { type: "solid", value: COLOR_MAP[words[i]].trim() };
  }
  return null;
}

// Açık renkler için kenarlık gerekiyor mu? (beyaz/krem zeminde görünmez kalmasın)
export function needsBorder(hex) {
  if (!hex || hex[0] !== "#") return true;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.82;
}

// Conic gradient — çok renkli swatch arka planı.
export const MULTI_GRADIENT =
  "conic-gradient(#dc2626,#facc15,#16a34a,#2563eb,#7c3aed,#ec4899,#dc2626)";
