import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { Check } from "lucide-react";
import { toast } from "sonner";
import { useCart } from "../context/CartContext";
import { priceView, fmtTL } from "../lib/price";
import { sortLikeSize } from "../utils/sizeSort";
import SizeChartModal from "./SizeChartModal";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * FullLookAddAll — kombinin parçaları satır satır (görsel · ad · renk · fiyat/indirim · beden).
 * Bedene tıkla → seçilir; tekrar tıkla → seçim kalkar. "Sepete Ekle" bedenlerin sağında, o parçayı
 * seçili bedenle sepete ekler. "Beden Tablosu" ürün sayfasındaki açılır pencerenin aynısını açar.
 */
export default function FullLookAddAll({ products = [] }) {
  const { addItem, setIsOpen } = useCart();
  const rows = useMemo(() => (products || []).map((p) => {
    const vs = Array.isArray(p.variants) ? p.variants : [];
    const map = new Map();
    for (const v of vs) {
      const s = String(v?.size || "").trim();
      if (!s) continue;
      if (!map.has(s)) map.set(s, { size: s, variant: v, stock: Number(v.stock) || 0 });
      else map.get(s).stock += Number(v.stock) || 0;
    }
    const sizes = sortLikeSize(Array.from(map.values()), (x) => x.size);
    const totalStock = vs.length ? vs.reduce((a, v) => a + (Number(v.stock) || 0), 0) : Number(p.stock) || 0;
    return { p, sizes, hasSizes: sizes.length > 0, soldOut: totalStock <= 0, price: priceView(p) };
  }), [products]);

  // sel: { [productId]: size } — seçili beden; ikinci tıklama seçimi kaldırır
  const [sel, setSel] = useState({});
  useEffect(() => { setSel({}); }, [rows]);
  const toggle = (r, size) => setSel((prev) => (prev[r.p.id] === size
    ? Object.fromEntries(Object.entries(prev).filter(([k]) => k !== r.p.id))
    : { ...prev, [r.p.id]: size }));

  // Beden tablosu (ürün sayfasıyla aynı kaynak: /size-tables-public/{id}); ürün başına önbellek
  const [chart, setChart] = useState(null);          // { product, data, img }
  const [chartCache, setChartCache] = useState({});
  const openChart = async (r) => {
    const pid = r.p.id;
    let entry = chartCache[pid];
    if (!entry) {
      let data = null;
      try {
        const res = await axios.get(`${API}/size-tables-public/${pid}`);
        if (res.data?.exists) data = res.data;
      } catch { /* tablo yok */ }
      const img = (r.p.images || []).find((x) => x && typeof x === "object" && x.is_size_table && x.url)?.url || null;
      entry = { data, img };
      setChartCache((prev) => ({ ...prev, [pid]: entry }));
    }
    if (!entry.data && !entry.img) { toast("Bu ürün için beden tablosu bulunmuyor"); return; }
    setChart({ product: r.p, ...entry });
  };

  const addOne = (r) => {
    if (r.soldOut) return;
    if (r.hasSizes) {
      const sz = sel[r.p.id];
      const sv = sz ? r.sizes.find((x) => x.size === sz) : null;
      if (!sv || sv.stock <= 0) { toast.error("Önce beden seçin"); return; }
      addItem(r.p, sv.variant);
    } else {
      addItem(r.p);
    }
    toast.success(`${r.p.name} sepete eklendi`);
    try { setIsOpen(true); } catch { /* çekmece yoksa */ }
  };

  if (!rows.length) return null;

  return (
    <div className="full-look-shop" data-testid="full-look-add-all">
      <div className="divide-y divide-stone-200">
        {rows.map((r) => {
          const href = `/urun/${encodeURIComponent(r.p.slug || r.p.id)}`;
          const pct = r.price.hasDiscount ? (Number(r.price.discountPct) || Math.round((1 - r.price.display / r.price.list) * 100)) : 0;
          return (
            <div key={r.p.id} className="py-6 flex gap-4 md:gap-7 min-w-0 max-w-full" data-testid={`fl-row-${r.p.id}`}>
              <Link to={href} className="block shrink-0 w-[96px] sm:w-[112px] md:w-[150px] overflow-hidden" aria-label={r.p.name}>
                {r.p.images?.[0]
                  ? <img src={typeof r.p.images[0] === "object" ? (r.p.images[0].url || "") : r.p.images[0]} alt={r.p.name} loading="lazy" className="block w-full h-auto" />
                  : <span className="block w-full aspect-[3/4] bg-white" />}
              </Link>
              <div className="min-w-0 flex-1">
                <Link to={href} className="block text-[17px] md:text-[19px] leading-snug text-stone-900 hover:underline underline-offset-4">{r.p.name}</Link>
                {r.p.color && <div className="mt-1 text-[13px] text-stone-500">{r.p.color}</div>}
                {/* Fiyat satırı: solda fiyatlar, sağda "Beden Tablosu" (çizginin üstünde) */}
                {/* Dar ekranda taşmasın: satır kırılabilir, "Beden Tablosu" gerekirse alt satıra sağa iner */}
                <div className="mt-3 flex flex-wrap items-end justify-between gap-x-3 gap-y-1">
                  {/* Mobil: üstü çizili fiyat kendi satırında, altında indirimli fiyat + %rozet; masaüstü: yan yana */}
                  <div className="flex flex-col items-start gap-y-1 md:flex-row md:flex-wrap md:items-center md:gap-x-3">
                    {r.price.hasDiscount && <del className="text-[13px] text-stone-400">{fmtTL(r.price.list)}</del>}
                    <span className="flex items-center gap-2">
                      <span className={`text-[18px] md:text-[20px] font-medium whitespace-nowrap ${r.price.hasDiscount ? "text-red-700" : "text-stone-900"}`}>{fmtTL(r.price.display)}</span>
                      {pct > 0 && <span className="text-[12px] px-2 py-0.5 bg-red-50 text-red-700 rounded-sm">%{pct}</span>}
                    </span>
                  </div>
                  {r.hasSizes && !r.soldOut && (
                    <button type="button" onClick={() => openChart(r)} className="ml-auto text-xs underline underline-offset-2 hover:no-underline whitespace-nowrap" data-testid={`fl-size-chart-${r.p.id}`}>
                      Beden Tablosu
                    </button>
                  )}
                </div>
                <div className="mt-3 pt-3 border-t border-stone-200">
                  {r.soldOut ? (
                    <span className="text-[11px] tracking-[0.1em] uppercase text-stone-400">Tükendi</span>
                  ) : (
                    <>
                      {/* Masaüstü: bedenler solda, "Sepete Ekle" aynı satırda sağda. Mobil: düğme bedenlerin altında,
                          bedenlerle aynı sol hizada ve tam genişlikte. */}
                      <div className="flex flex-col gap-3 md:flex-row md:flex-wrap md:items-center">
                        {r.hasSizes ? (
                          <div className="flex flex-nowrap gap-1.5 overflow-x-auto pb-1 -mb-1 max-w-full [scrollbar-width:none]" role="radiogroup" aria-label={`${r.p.name} beden`}>
                            {r.sizes.map((s) => {
                              const active = sel[r.p.id] === s.size;
                              return (
                                <button key={s.size} type="button" disabled={s.stock <= 0} aria-pressed={active}
                                  onClick={() => toggle(r, s.size)}
                                  className={`shrink-0 min-w-[42px] md:min-w-[56px] px-2 md:px-3 py-2 text-[12px] md:text-[13px] border transition-colors ${
                                    active ? "bg-stone-900 text-white border-stone-900"
                                      : s.stock <= 0 ? "border-stone-200 text-stone-300 line-through cursor-not-allowed"
                                      : "border-stone-200 bg-white text-stone-800 hover:border-stone-900"}`}
                                  data-testid={`fl-size-${r.p.id}-${s.size}`}>
                                  {s.size}
                                </button>
                              );
                            })}
                          </div>
                        ) : (
                          <span className="text-[11px] tracking-[0.1em] uppercase text-stone-400 inline-flex items-center gap-1"><Check size={12} /> Tek beden</span>
                        )}
                        <button type="button" onClick={() => addOne(r)}
                          className="w-full md:w-auto md:ml-auto inline-flex items-center justify-center bg-black text-white px-5 py-3 md:py-2.5 text-[11px] uppercase tracking-[0.2em] hover:bg-black/85 transition-colors"
                          data-testid={`fl-add-one-${r.p.id}`}>
                          Sepete Ekle
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {chart && <SizeChartModal product={chart.product} data={chart.data} img={chart.img} onClose={() => setChart(null)} />}
    </div>
  );
}
