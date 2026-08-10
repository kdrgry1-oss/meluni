import { useEffect, useState, useCallback } from "react";
import axios from "axios";
import { Search, X, GripVertical } from "lucide-react";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * CombineProductsTab — Ürüne kombin ürün ataması yapar.
 *  - Sol: Mevcut atanmış kombin ürünler (sıralı liste)
 *  - Sağ: Aramayla ürün bulup ekleme
 *  - Maks 12 kombin ürün
 *  - Sepet sayfasında müşteriye "Bu ürünlerle yakışanlar" olarak gösterilir
 */
export default function CombineProductsTab({ productId, combineIds = [], onChange }) {
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [combineDetails, setCombineDetails] = useState([]); // ID'lerin detayları
  const [loadingDetails, setLoadingDetails] = useState(false);

  // Atanmış ID'lerin detaylarını yükle
  const loadCombineDetails = useCallback(async () => {
    if (!combineIds || combineIds.length === 0) {
      setCombineDetails([]);
      return;
    }
    setLoadingDetails(true);
    try {
      const token = localStorage.getItem("token");
      const requests = combineIds.map((id) =>
        axios.get(`${API}/products/${id}`, { headers: { Authorization: `Bearer ${token}` } })
          .then((r) => r.data).catch(() => null)
      );
      const all = await Promise.all(requests);
      setCombineDetails(all.filter(Boolean));
    } finally {
      setLoadingDetails(false);
    }
  }, [combineIds]);

  useEffect(() => { loadCombineDetails(); }, [loadCombineDetails]);

  // Arama
  useEffect(() => {
    if (!search || search.length < 2) { setSearchResults([]); return; }
    let cancel = false;
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const token = localStorage.getItem("token");
        const r = await axios.get(`${API}/products?search=${encodeURIComponent(search)}&limit=20`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (!cancel) {
          const items = (r.data?.products || r.data?.items || [])
            .filter((p) => p.id !== productId && !combineIds.includes(p.id));
          setSearchResults(items);
        }
      } catch (e) {
        if (!cancel) setSearchResults([]);
      } finally {
        if (!cancel) setSearching(false);
      }
    }, 350);
    return () => { cancel = true; clearTimeout(t); };
  }, [search, productId, combineIds]);

  const addProduct = (p) => {
    if (combineIds.length >= 12) {
      toast.error("En fazla 12 kombin ürün eklenebilir");
      return;
    }
    if (combineIds.includes(p.id)) return;
    onChange([...combineIds, p.id]);
    setSearch("");
    setSearchResults([]);
    toast.success(`${p.name} kombin ürünlere eklendi`);
  };

  const removeProduct = (id) => {
    onChange(combineIds.filter((cid) => cid !== id));
  };

  const moveUp = (idx) => {
    if (idx === 0) return;
    const next = [...combineIds];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    onChange(next);
  };
  const moveDown = (idx) => {
    if (idx === combineIds.length - 1) return;
    const next = [...combineIds];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    onChange(next);
  };

  return (
    <div className="space-y-6" data-testid="combine-products-tab">
      <div>
        <h3 className="text-base font-medium mb-1">Kombin Ürünler ({combineIds.length}/12)</h3>
        <p className="text-xs text-gray-500">
          Bu ürün sepete eklendiğinde, müşteriye <strong>"Bu ürünlerle yakışanlar"</strong> bölümünde önereceğiniz
          kombin ürünleri seçin. Pantolon ürününe uyumlu bluz/üst gibi cross-sell önerileri için ideal.
        </p>
      </div>

      {/* Auto-combine — geçmiş siparişlerden otomatik öneri */}
      {productId && (
        <div className="bg-stone-900 text-white rounded-lg p-4 flex items-start gap-3" data-testid="auto-combine-card">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium mb-1">⚡ Otomatik Kombin Önerisi</div>
            <div className="text-xs text-stone-300">
              Geçmiş siparişlerde bu ürünle <strong>en sık birlikte satılan</strong> top-8 ürünü otomatik kombin olarak ata.
            </div>
          </div>
          <button type="button" onClick={async () => {
            const token = localStorage.getItem("token");
            try {
              const r = await axios.post(`${API}/products/${productId}/auto-combine`,
                { max: 8, replace: true },
                { headers: { Authorization: `Bearer ${token}` } });
              if (r.data?.success) {
                toast.success(r.data.message || "Otomatik atama tamam");
                onChange(r.data.assigned || []);
              } else {
                toast.error(r.data?.message || "Yeterli sipariş geçmişi yok");
              }
            } catch (e) {
              toast.error(e?.response?.data?.detail || "Otomatik atama başarısız");
            }
          }} className="bg-white text-stone-900 hover:bg-stone-100 px-4 py-2 text-xs tracking-wider uppercase rounded font-medium whitespace-nowrap"
             data-testid="auto-combine-btn">
            Otomatik Ata
          </button>
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-6">
        {/* SOL: Atanmış kombin ürünler */}
        <div>
          <p className="text-xs font-medium text-gray-700 mb-3">Atanmış Kombin Ürünler</p>
          {loadingDetails ? (
            <div className="text-xs text-gray-400">Yükleniyor…</div>
          ) : combineDetails.length === 0 ? (
            <div className="border-2 border-dashed border-gray-200 rounded-lg p-6 text-center text-xs text-gray-400">
              Henüz kombin ürün atanmadı.<br />Sağdaki aramadan ürün seçin.
            </div>
          ) : (
            <ul className="space-y-2">
              {combineDetails.map((p, i) => {
                const img = (p.images && p.images[0]) || p.image || "";
                return (
                  <li key={p.id} data-testid={`combine-item-${p.id}`}
                    className="flex items-center gap-3 border border-gray-200 rounded-lg p-2 hover:bg-stone-50 transition-colors">
                    <GripVertical size={14} className="text-gray-300" />
                    <img src={img} alt={p.name} className="w-12 h-14 object-cover bg-stone-50 rounded" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate">{p.name}</div>
                      <div className="text-[11px] text-gray-500">{(p.price || 0).toFixed(2)} TL · #{p.stock_code || p.id?.slice(0, 6)}</div>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <button type="button" onClick={() => moveUp(i)} disabled={i === 0}
                        className="text-[10px] px-1.5 py-0.5 border rounded hover:bg-gray-100 disabled:opacity-30">↑</button>
                      <button type="button" onClick={() => moveDown(i)} disabled={i === combineDetails.length - 1}
                        className="text-[10px] px-1.5 py-0.5 border rounded hover:bg-gray-100 disabled:opacity-30">↓</button>
                    </div>
                    <button type="button" onClick={() => removeProduct(p.id)}
                      data-testid={`remove-combine-${p.id}`}
                      className="p-1 text-red-500 hover:bg-red-50 rounded">
                      <X size={14} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* SAĞ: Arama + Ekle */}
        <div>
          <p className="text-xs font-medium text-gray-700 mb-3">Ürün Ara ve Ekle</p>
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Ürün adı veya stok kodu… (en az 2 karakter)"
              data-testid="combine-search-input"
              className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-stone-900" />
          </div>
          <div className="mt-3 max-h-96 overflow-y-auto space-y-1.5">
            {searching && <div className="text-xs text-gray-400 py-4 text-center">Aranıyor…</div>}
            {!searching && search.length >= 2 && searchResults.length === 0 && (
              <div className="text-xs text-gray-400 py-4 text-center">Sonuç bulunamadı</div>
            )}
            {searchResults.map((p) => {
              const img = (p.images && p.images[0]) || p.image || "";
              return (
                <button key={p.id} type="button" onClick={() => addProduct(p)}
                  data-testid={`add-combine-${p.id}`}
                  className="w-full flex items-center gap-3 border border-gray-200 rounded-lg p-2 hover:border-stone-900 hover:bg-stone-50 transition-colors text-left">
                  <img src={img} alt="" className="w-12 h-14 object-cover bg-stone-50 rounded" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{p.name}</div>
                    <div className="text-[11px] text-gray-500">{(p.price || 0).toFixed(2)} TL · #{p.stock_code || p.id?.slice(0, 6)}</div>
                  </div>
                  <span className="text-xs text-stone-900 font-medium">+ Ekle</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="bg-stone-50 border border-stone-200 rounded-lg p-3 text-[11px] text-gray-600">
        <strong className="text-stone-900">İpucu:</strong> En sık birlikte satılan ürünleri buraya ekleyin.
        Sepet sayfasında ilk 8 kombin ürün otomatik gösterilir; eksikse <em>indirimdeki ürünler</em> ile tamamlanır.
      </div>
    </div>
  );
}
