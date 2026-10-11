/**
 * ReportScopeBadge — Rapor sayfalarında tutarların iptal/iade siparişlerini kapsayıp
 * kapsamadığını AÇIKÇA gösteren küçük etiket. Her rapor sayfasında/sekmesinde,
 * o raporun backend mantığına göre uygun türü kullanılır.
 *
 * Türler:
 *   exclude    → İptal ve iade HARİÇ (ciro/adet bu ikisini düşer) — çoğu satış raporu
 *   cancelOnly → İptal hariç, iade DAHİL (yalnız iptaller düşülür)
 *   returns    → Kabul edilmiş iade talepleri (pusula değil) — iade analizi ekranları
 *   stock      → Anlık stok verisi (iptal/iade tutarları etkilemez)
 *   stockVelocity → Anlık stok + satış hızı; hız (ve ondan türeyen tükenme/öneri) NET
 *                   satıştan, yani iptal & iade HARİÇ hesaplanır
 *   mixed      → Brüt, iptal, iade ve net alanlar ayrı ayrı gösterilir
 *   gross      → Brüt: iptal ve iade DAHİL, yalnız ödenmemiş siparişler hariç
 */
const _MAP = {
  exclude:    { txt: "İptal & iade HARİÇ",        cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  cancelOnly: { txt: "İptal hariç · iade DAHİL",  cls: "bg-amber-50 text-amber-700 ring-amber-200" },
  // Yalnız İade Sebepleri / En Çok İade Edilen Beden & Ürün ekranlarında kullanılır:
  // bu uçlar gider pusulasını DEĞİL kabul edilmiş iade taleplerini (Trendyol Accepted +
  // onaylı site iadesi) sipariş tarihine göre sayar — Satış kartıyla birebir tutmayabilir.
  returns:    { txt: "Kabul edilmiş iade talepleri · sipariş tarihine göre (pusula şartı yok)", cls: "bg-sky-50 text-sky-700 ring-sky-200" },
  stock:      { txt: "Anlık stok — iptal/iade etkilemez", cls: "bg-gray-100 text-gray-600 ring-gray-200" },
  stockVelocity: { txt: "Anlık stok · satış hızı iptal & iade HARİÇ", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  mixed:      { txt: "Brüt · iptal · iade · net ayrı", cls: "bg-indigo-50 text-indigo-700 ring-indigo-200" },
  gross:      { txt: "Brüt · iptal & iade DAHİL (ödenmemiş hariç)", cls: "bg-slate-100 text-slate-700 ring-slate-200" },
};

export default function ReportScopeBadge({ kind = "exclude", className = "" }) {
  const m = _MAP[kind] || _MAP.exclude;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ring-inset ${m.cls} ${className}`}
      title="Bu rapordaki tutarların iptal/iade siparişlerini kapsama durumu"
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current opacity-70" aria-hidden="true" />
      {m.txt}
    </span>
  );
}
