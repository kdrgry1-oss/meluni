// Ürün listesi YAZDIRMA görünümü — admin "Ürünler" ekranındaki görünümün (görsel, kart ID,
// ad, kategori, stok kodu, bedenler, sezon, fiyat, stok, durum) A4 için düzenlenmiş hâli
// + elle not almak için çizgili NOT sütunu. Ayrı bir pencerede açılır ki panel CSS'i
// baskıyı bozmasın; görseller yüklenince yazdırma penceresi kendiliğinden açılır.
//
// Firma adı/logosu koddan DEĞİL, tenant ayarlarından gelir (beyaz etiket).

const esc = (v) => String(v ?? "")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const tl = (n) => (Number(n) || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₺";

// Beden sırası: bilinen harf bedenleri → sayısal → diğerleri (ekrandaki doğal sıra).
const SIZE_ORDER = ["XXS", "XS", "XS/S", "S", "S/M", "M", "M/L", "L", "L/XL", "XL", "XXL", "2XL", "3XL", "STD"];
const sizeRank = (s) => {
  const u = String(s || "").trim().toUpperCase();
  const i = SIZE_ORDER.indexOf(u);
  if (i >= 0) return i;
  const n = parseFloat(u);
  return Number.isFinite(n) ? 100 + n : 1000;
};

function variantCells(p) {
  const vs = Array.isArray(p.variants) ? p.variants : [];
  if (!vs.length) {
    const sz = Array.isArray(p.sizes) ? p.sizes : [];
    return sz.length ? sz.map((s) => `<span class="chip">${esc(s)}</span>`).join("") : '<span class="muted">—</span>';
  }
  return [...vs]
    .sort((a, b) => sizeRank(a.size) - sizeRank(b.size))
    .map((v) => {
      const st = Number(v.stock ?? 0);
      const lbl = [v.color && vs.some((x) => x.color !== v.color) ? v.color : "", v.size || "—"]
        .filter(Boolean).join(" ");
      return `<span class="chip${st <= 0 ? " zero" : ""}">${esc(lbl)} <b>${st}</b></span>`;
    }).join("");
}

function priceCell(pv) {
  if (!pv) return "—";
  if (pv.hasDiscount) {
    return `<div class="old">${tl(pv.list)}</div><div class="new">${tl(pv.display)}</div>`;
  }
  return `<div>${tl(pv.display)}</div>`;
}

/**
 * @param {object} o
 * @param {Array}  o.products     yazdırılacak ürünler (ekrandaki sırayla)
 * @param {Function} o.priceView  lib/price priceView (ekranla aynı fiyat kuralı)
 * @param {Function} o.fixImg     ekrandaki görsel URL düzeltici
 * @param {Function} o.categoryText ürün → "Kategori · Kategori" metni (ekrandakiyle aynı)
 * @param {string} o.storeName, o.logoUrl, o.filterText
 */
export function buildProductPrintHtml({ products, priceView, fixImg, categoryText, storeName, logoUrl, filterText }) {
  const now = new Date().toLocaleString("tr-TR", { timeZone: "Europe/Istanbul", dateStyle: "long", timeStyle: "short" });
  const totalStock = products.reduce((a, p) => a + (Number(p.stock) || 0), 0);
  const rows = products.map((p, i) => {
    const img = p.images && p.images[0] ? fixImg(p.images[0]) : "";
    const active = p.is_active !== false;
    const stock = Number(p.stock ?? 0);
    return `<tr>
      <td class="no">${i + 1}</td>
      <td class="img">${img ? `<img src="${esc(img)}" alt="" loading="eager">` : '<div class="noimg">Görsel yok</div>'}</td>
      <td class="name"><div class="pname">${esc(p.name || "—")}</div>
        <div class="muted">${esc(categoryText(p) || "")}</div>
        <div class="mono small">Kart ID: ${esc(p.urun_karti_id || "—")}</div></td>
      <td class="mono">${esc(p.stock_code || p.sku || "—")}</td>
      <td class="sizes">${variantCells(p)}</td>
      <td>${esc(p.season || "—")}</td>
      <td class="price">${priceCell(priceView(p))}</td>
      <td class="stock${stock <= 0 ? " zero" : ""}">${stock}</td>
      <td><span class="st ${active ? "on" : "off"}">${active ? "Aktif" : "Pasif"}</span></td>
      <td class="note"></td>
    </tr>`;
  }).join("");

  return `<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>${esc(storeName || "Ürün Listesi")} — Ürün Listesi</title>
<style>
  @page { size: A4 landscape; margin: 9mm 8mm 11mm 8mm;
          @bottom-right { content: "Sayfa " counter(page) " / " counter(pages); font: 8pt sans-serif; color: #666; } }
  * { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { font-family: "Nunito", "Segoe UI", Arial, sans-serif; color: #111; margin: 0; font-size: 9pt; }
  .bar { position: sticky; top: 0; background: #111827; color: #fff; padding: 10px 16px; display: flex;
         gap: 12px; align-items: center; flex-wrap: wrap; z-index: 5; font-size: 13px; }
  .bar button { background: #f97316; color: #fff; border: 0; border-radius: 6px; padding: 7px 16px; font-weight: 700; cursor: pointer; font-size: 13px; }
  .bar label { display: flex; gap: 5px; align-items: center; cursor: pointer; }
  .bar .msg { opacity: .8; }
  .wrap { padding: 12px 16px; }
  header { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 2px solid #111;
           padding-bottom: 6px; margin-bottom: 8px; }
  header .brand { display: flex; align-items: center; gap: 10px; }
  header .brand img { height: 30px; }
  header h1 { margin: 0; font-size: 16pt; letter-spacing: .12em; }
  header .sub { font-size: 9pt; color: #444; margin-top: 2px; }
  header .meta { text-align: right; font-size: 8.5pt; color: #333; line-height: 1.5; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  thead { display: table-header-group; }
  th { background: #111827; color: #fff; text-align: left; font-size: 7.5pt; text-transform: uppercase;
       letter-spacing: .04em; padding: 5px 4px; }
  td { border-bottom: 1px solid #d1d5db; padding: 4px; vertical-align: middle; word-wrap: break-word; }
  tr { page-break-inside: avoid; break-inside: avoid; }
  tbody tr:nth-child(even) td { background: #fafafa; }
  .no { width: 22px; color: #888; text-align: center; font-size: 7.5pt; }
  col.c-img { width: var(--imgw); }
  td.img img { width: calc(var(--imgw) - 8px); height: calc((var(--imgw) - 8px) * 1.4); object-fit: cover;
               border-radius: 3px; border: 1px solid #e5e7eb; display: block; }
  .noimg { width: calc(var(--imgw) - 8px); height: calc((var(--imgw) - 8px) * 1.4); background: #f3f4f6;
           color: #9ca3af; font-size: 7pt; display: flex; align-items: center; justify-content: center; text-align: center; border-radius: 3px; }
  .pname { font-weight: 700; font-size: 9pt; line-height: 1.25; }
  .muted { color: #6b7280; font-size: 7.5pt; }
  .mono { font-family: "Consolas", "Menlo", monospace; font-size: 8pt; }
  .small { font-size: 7pt; color: #6b7280; margin-top: 2px; }
  .chip { display: inline-block; border: 1px solid #d1d5db; border-radius: 4px; padding: 1px 4px; margin: 1px 2px 1px 0;
          font-size: 7.5pt; white-space: nowrap; }
  .chip b { font-weight: 800; }
  .chip.zero { color: #b91c1c; border-color: #fca5a5; background: #fef2f2; }
  .price .old { text-decoration: line-through; color: #9ca3af; font-size: 7.5pt; }
  .price .new { color: #dc2626; font-weight: 800; }
  .stock { text-align: center; font-weight: 800; font-size: 10pt; }
  .stock.zero { color: #b91c1c; }
  .st { font-size: 7pt; font-weight: 700; padding: 1px 5px; border-radius: 3px; }
  .st.on { background: #dcfce7; color: #166534; } .st.off { background: #f3f4f6; color: #6b7280; }
  td.note { background-image: repeating-linear-gradient(to bottom, transparent 0, transparent 13px, #cbd5e1 13px, #cbd5e1 14px) !important; }
  body.nonote col.c-note, body.nonote th.note, body.nonote td.note { display: none; }
  footer { margin-top: 8px; font-size: 8pt; color: #555; display: flex; justify-content: space-between; }
  @media print { .bar { display: none; } .wrap { padding: 0; } }
</style></head>
<body style="--imgw: 22mm">
<div class="bar">
  <button onclick="window.print()">🖨 Yazdır</button>
  <label>Görsel:
    <select onchange="document.body.style.setProperty('--imgw', this.value)" style="border-radius:4px;padding:3px 6px;width:auto;color:#111">
      <option value="13mm">Küçük (sayfaya daha çok ürün)</option>
      <option value="22mm" selected>Orta</option>
      <option value="32mm">Büyük</option>
    </select></label>
  <label><input type="checkbox" id="note" checked onchange="document.body.classList.toggle('nonote', !this.checked)"> Not sütunu</label>
  <span class="msg" id="msg">Görseller yükleniyor…</span>
</div>
<div class="wrap">
<header>
  <div class="brand">${logoUrl ? `<img src="${esc(logoUrl)}" alt="">` : ""}
    <div><h1>${esc(storeName || "")}</h1><div class="sub">Ürün Listesi</div></div></div>
  <div class="meta">${esc(now)}<br>${products.length} ürün · toplam stok ${totalStock}<br>${esc(filterText || "Tüm ürünler")}</div>
</header>
<table>
  <colgroup><col style="width:22px"><col class="c-img"><col style="width:22%"><col style="width:9%">
    <col style="width:21%"><col style="width:8%"><col style="width:8%"><col style="width:5%">
    <col style="width:5.5%"><col class="c-note" style="width:14%"></colgroup>
  <thead><tr><th>#</th><th>Görsel</th><th>Ürün</th><th>Stok Kodu</th><th>Bedenler (stok)</th>
    <th>Sezon</th><th>Fiyat</th><th>Stok</th><th>Durum</th><th class="note">Not</th></tr></thead>
  <tbody>${rows}</tbody>
</table>
<footer><span>${esc(storeName || "")} · ${esc(now)}</span><span>${products.length} ürün</span></footer>
</div>
<script>
  (function () {
    var imgs = Array.prototype.slice.call(document.images), left = imgs.length, done = false;
    var msg = document.getElementById('msg');
    function ready() {
      if (done) return; done = true;
      msg.textContent = 'Hazır — ' + ${products.length} + ' ürün. Yazdır düğmesine basın ya da Ctrl+P.';
      setTimeout(function () { window.print(); }, 300);
    }
    if (!left) return ready();
    imgs.forEach(function (im) {
      if (im.complete) { if (--left === 0) ready(); return; }
      im.addEventListener('load', function () { if (--left === 0) ready(); });
      im.addEventListener('error', function () { if (--left === 0) ready(); });
    });
    setTimeout(ready, 25000);   // yavaş görsel baskıyı sonsuza kadar bekletmesin
  })();
</script>
</body></html>`;
}
