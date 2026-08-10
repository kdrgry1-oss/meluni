// frontend/src/utils/sizeSort.js
// -----------------------------------------------------------------------------
// Beden sıralama yardımcısı (storefront).
// Standart bedenler (XS, S, M, L, XL...) doğru sırada gelir; KOMBİNE bedenler
// (S/M, XS/S, M-L, 36/38) ilk parçalarının sırasına göre standart bedenlerin
// ARASINA yerleştirilir — alfabetik olarak en sona atılmaz.
// -----------------------------------------------------------------------------

const SIZE_ORDER = [
  "xxxxxs", "xxxxs", "xxxs", "xxs", "2xs", "xs", "s", "m", "l", "xl",
  "xxl", "2xl", "xxxl", "3xl", "xxxxl", "4xl", "xxxxxl", "5xl",
  "std", "standart", "tekbeden", "tekebat", "freesize", "onesize",
];

function _flat(s) {
  // Standart eşleşme için: küçük harf + boşluk/noktalama temizliği
  return String(s || "").toLowerCase().trim().replace(/[\s\-_./]/g, "");
}

// [bucket, deger] döner. Önce bucket'a, eşitse deger'e göre sıralanır.
export function sizeRank(name) {
  const raw = String(name || "").toLowerCase().trim();
  const n = _flat(raw);
  if (!n) return [9, ""];

  // 1) Tam standart beden
  const idx = SIZE_ORDER.indexOf(n);
  if (idx >= 0) return [0, idx];

  // 2) Kombine beden (ayraç KORUNARAK ham isimden): S/M, XS/S, M-L, 36/38, 36-38
  const cm = raw.match(/^([a-z0-9]+)\s*[/\-]\s*([a-z0-9]+)$/);
  if (cm) {
    const a = cm[1], b = cm[2];
    const ia = SIZE_ORDER.indexOf(a);
    const ib = SIZE_ORDER.indexOf(b);
    if (ia >= 0) {
      // ilk parça standart beden → onun hemen ardına; ikinci parça tie-breaker
      return [0, ia + (ib >= 0 ? (ib + 1) / 1000 : 0.5)];
    }
    const na = parseInt(a, 10), nb = parseInt(b, 10);
    if (!Number.isNaN(na)) return [1, na + (Number.isNaN(nb) ? 0 : nb / 1000)];
  }

  // 3) Saf sayısal (36, 38, 40)
  const m = n.match(/^(\d+)$/);
  if (m) return [1, parseInt(m[1], 10)];

  // 4) Yaş/ay grubu (2-3 yaş, 0-2 ay) → sonlara
  if (/(yas|yaş|ay)/.test(n)) return [4, n];

  // 5) Diğer
  return [3, n];
}

export function sortLikeSize(arr, getName) {
  const get = getName || ((x) => x);
  return [...(arr || [])].sort((x, y) => {
    const ra = sizeRank(get(x));
    const rb = sizeRank(get(y));
    if (ra[0] !== rb[0]) return ra[0] - rb[0];
    if (typeof ra[1] === "number" && typeof rb[1] === "number") return ra[1] - rb[1];
    return String(ra[1]).localeCompare(String(rb[1]), "tr");
  });
}
