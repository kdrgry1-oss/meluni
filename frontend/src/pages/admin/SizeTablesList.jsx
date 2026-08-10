import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { Ruler, Search, ChevronRight } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

export default function SizeTablesList() {
  const [products, setProducts] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const { data } = await axios.get(`${API}/products?limit=500&admin_view=1`, { headers: authHeaders() });
        setProducts(data.products || data || []);
      } finally { setLoading(false); }
    })();
  }, []);

  const filtered = products.filter((p) => !search || p.name?.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="space-y-5" data-testid="size-tables-list-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Ruler /> Ölçü Tabloları</h1>
          <p className="text-sm text-gray-500 mt-1">Ürün başına ölçü tablosu oluşturun. Arka planda 1200×1800 PNG hazırlanır, müşteriye HTML olarak gösterilir.</p>
        </div>
        <div className="relative">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Ürün ara..."
            className="pl-9 pr-3 py-2 border rounded-lg text-sm"
            data-testid="size-table-search"
          />
        </div>
      </div>

      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3 w-12">#</th>
              <th className="text-left p-3">Ürün</th>
              <th className="text-left p-3">Stok Kodu</th>
              <th className="text-right p-3">Aksiyon</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={4} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={4} className="p-8 text-center text-gray-400">Ürün bulunamadı.</td></tr>
            ) : filtered.slice(0, 200).map((p, i) => (
              <tr key={p.id} className="border-t hover:bg-gray-50">
                <td className="p-3 text-gray-400">{i + 1}</td>
                <td className="p-3 font-medium">{p.name}</td>
                <td className="p-3 text-gray-500 font-mono text-xs">{p.stock_code || "—"}</td>
                <td className="p-3 text-right">
                  <button
                    onClick={() => navigate(`/admin/urunler?sizeTable=${p.id}`)}
                    data-testid={`open-size-table-${p.id}`}
                    className="inline-flex items-center gap-1 px-3 py-1.5 bg-black text-white rounded text-xs hover:bg-gray-800"
                  >
                    Ölçü Tablosu <ChevronRight size={13} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-400">Ölçü tablosu düzenleme ekranı ürün düzenleme sayfasında açılır.</p>
    </div>
  );
}
