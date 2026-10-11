import { useState, useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import axios from "axios";
import Header from "../components/Header";
import Footer from "../components/Footer";
import ProductCard from "../components/ProductCard";
import { dedupeColorGroups } from "../lib/colorGroups";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const PAGE_SIZE = 24;

// SALE menüsündeki kampanya sayfası (/kampanya/:id): kampanyanın kapsamındaki ürünler.
// Kapsam sunucuda kampanya motoruyla aynı kurala göre çözülür (/products?campaign=…).
export default function CampaignProducts() {
  const { id } = useParams();
  const [info, setInfo] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [products, setProducts] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState("default");

  useEffect(() => {
    let alive = true;
    setNotFound(false);
    axios.get(`${API}/sale-menu/campaign/${encodeURIComponent(id)}`)
      .then(({ data }) => {
        if (!alive) return;
        setInfo(data);
        try { document.title = `${data.title} | MELUNI`; } catch (_) { /* noop */ }
      })
      .catch(() => { if (alive) setNotFound(true); });
    return () => { alive = false; };
  }, [id]);

  useEffect(() => { setPage(1); }, [id, sort]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const params = { campaign: id, page, limit: PAGE_SIZE };
    if (sort === "price_asc") Object.assign(params, { sort: "price", order: "asc" });
    if (sort === "price_desc") Object.assign(params, { sort: "price", order: "desc" });
    if (sort === "newest") Object.assign(params, { sort: "created_at", order: "desc" });
    axios.get(`${API}/products`, { params })
      .then(({ data }) => {
        if (!alive) return;
        const list = data?.products || [];
        setProducts((prev) => (page === 1 ? list : [...prev, ...list]));
        setTotal(Number(data?.total || 0));
      })
      .catch(() => { if (alive && page === 1) setProducts([]); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [id, page, sort]);

  const shown = dedupeColorGroups(products);

  return (
    <div className="min-h-screen bg-white">
      <Header />
      <main className="max-w-[1800px] mx-auto px-3 md:px-8 pt-6 md:pt-10 pb-16" data-testid="campaign-page">
        {notFound ? (
          <div className="text-center py-24">
            <p className="text-gray-500">Bu kampanya sona erdi.</p>
            <Link to="/sale" className="inline-block mt-4 text-sm underline hover:no-underline">SALE ürünlerine göz at</Link>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3 mb-6 md:mb-8">
              <div>
                <h1 className="text-lg md:text-2xl font-light tracking-[0.18em] uppercase text-red-700">
                  {info?.title || " "}
                </h1>
                {total > 0 && <p className="text-xs text-gray-500 mt-1">{total} ürün</p>}
              </div>
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value)}
                className="text-xs border border-black/15 px-3 py-2 bg-white"
                aria-label="Sırala"
              >
                <option value="default">Önerilen</option>
                <option value="newest">En yeniler</option>
                <option value="price_asc">Fiyat: artan</option>
                <option value="price_desc">Fiyat: azalan</option>
              </select>
            </div>

            {loading && page === 1 ? (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-x-[2px] gap-y-3 md:gap-y-4">
                {[...Array(8)].map((_, i) => (
                  <div key={i} className="animate-pulse">
                    <div className="aspect-[2/3] bg-gray-100 mb-3" />
                    <div className="h-3 bg-gray-100 w-2/3 mb-2" />
                    <div className="h-4 bg-gray-100 w-1/3" />
                  </div>
                ))}
              </div>
            ) : shown.length === 0 ? (
              <div className="text-center py-16 text-gray-500">Bu kampanyada şu an ürün bulunmuyor.</div>
            ) : (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-x-[2px] gap-y-3 md:gap-y-4">
                {shown.map((product, idx) => (
                  <ProductCard key={product.id} product={product} listName={`kampanya-${id}`} index={idx} />
                ))}
              </div>
            )}

            {products.length < total && (
              <div className="text-center mt-10">
                <button
                  type="button"
                  onClick={() => setPage((p) => p + 1)}
                  disabled={loading}
                  className="px-8 py-3 border border-black text-xs tracking-[0.2em] uppercase hover:bg-black hover:text-white transition-colors disabled:opacity-50"
                >
                  {loading ? "Yükleniyor…" : "Daha fazla göster"}
                </button>
              </div>
            )}
          </>
        )}
      </main>
      <Footer />
    </div>
  );
}
