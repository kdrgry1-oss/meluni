// =============================================================================
// DeletedOrders.jsx — Silinen Siparişler (arşiv)
// -----------------------------------------------------------------------------
// Silinen siparişler deleted_orders koleksiyonuna taşınır; bu sayfa oradan
// listeler ve "Geri Al" ile orders'a geri taşır. Ana sipariş listesi/raporlar
// bu kayıtlardan etkilenmez.
// =============================================================================
import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { Trash2, RotateCcw, Search, RefreshCw } from "lucide-react";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function fmtDate(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString("tr-TR", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return String(ts);
  }
}

function money(v) {
  const n = Number(v);
  if (!isFinite(n)) return "";
  return n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " TL";
}

// Kaynak: sipariş no önekinden (TY→Trendyol, HB→Hepsiburada, yoksa Site)
function channelLabel(o) {
  const on = (o.order_number || "").toUpperCase();
  const plat = (o.platform || "").toLowerCase();
  if (on.startsWith("TY") || plat === "trendyol") return "Trendyol";
  if (on.startsWith("HB") || plat === "hepsiburada") return "Hepsiburada";
  return "Site";
}

export default function DeletedOrders() {
  const [orders, setOrders] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [restoringId, setRestoringId] = useState("");

  const fetchDeleted = useCallback(async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/orders/deleted`, {
        params: { search, limit: 100 },
        headers: { Authorization: `Bearer ${token}` },
      });
      setOrders(res.data?.orders || []);
      setTotal(res.data?.total || 0);
    } catch {
      toast.error("Silinen siparişler yüklenemedi");
      setOrders([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    fetchDeleted();
  }, [fetchDeleted]);

  const applySearch = () => setSearch(searchInput.trim());

  const restore = async (order) => {
    if (!order?.id) return;
    setRestoringId(order.id);
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/orders/${order.id}/restore`, {}, {
        headers: { Authorization: `Bearer ${token}` },
      });
      toast.success(`Sipariş geri alındı${order.order_number ? `: ${order.order_number}` : ""}`);
      setOrders((prev) => prev.filter((o) => o.id !== order.id));
      setTotal((t) => Math.max(0, t - 1));
    } catch {
      toast.error("Geri alma başarısız");
    } finally {
      setRestoringId("");
    }
  };

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-2 mb-1">
        <Trash2 className="w-5 h-5 text-red-600" />
        <h1 className="text-xl font-semibold">Silinen Siparişler</h1>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Silinen siparişler buraya arşivlenir. "Geri Al" ile sipariş yeniden aktif listeye taşınır.
        Ana sipariş listesi ve raporlar bu kayıtlardan etkilenmez.
      </p>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") applySearch(); }}
            placeholder="Sipariş no / telefon / e-posta ara…"
            className="w-full pl-9 pr-3 py-2 border rounded-lg text-sm"
          />
        </div>
        <button
          onClick={applySearch}
          className="px-3 py-2 text-sm bg-gray-800 text-white rounded-lg hover:bg-gray-900"
        >
          Ara
        </button>
        <button
          onClick={fetchDeleted}
          className="px-3 py-2 text-sm border rounded-lg hover:bg-gray-50 flex items-center gap-1"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Yenile
        </button>
        <span className="text-sm text-gray-500 ml-auto">{total} kayıt</span>
      </div>

      <div className="border rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left px-3 py-2 font-medium">Sipariş No</th>
              <th className="text-left px-3 py-2 font-medium">Müşteri</th>
              <th className="text-left px-3 py-2 font-medium">Kaynak</th>
              <th className="text-right px-3 py-2 font-medium">Tutar</th>
              <th className="text-left px-3 py-2 font-medium">Silinme</th>
              <th className="text-right px-3 py-2 font-medium">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">Yükleniyor…</td></tr>
            )}
            {!loading && orders.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">Silinen sipariş yok.</td></tr>
            )}
            {!loading && orders.map((o) => {
              const addr = o.shipping_address || {};
              const name = (addr.full_name || `${addr.first_name || ""} ${addr.last_name || ""}`).trim() || "—";
              return (
                <tr key={o.id} className="border-t hover:bg-gray-50">
                  <td className="px-3 py-2 font-medium">{o.order_number || "—"}</td>
                  <td className="px-3 py-2">
                    <div>{name}</div>
                    <div className="text-xs text-gray-400">{addr.phone || addr.email || ""}</div>
                  </td>
                  <td className="px-3 py-2">{channelLabel(o)}</td>
                  <td className="px-3 py-2 text-right">{money(o.total)}</td>
                  <td className="px-3 py-2">
                    <div>{fmtDate(o.deleted_at)}</div>
                    {o.deleted_by && <div className="text-xs text-gray-400">{o.deleted_by}</div>}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => restore(o)}
                      disabled={restoringId === o.id}
                      className="inline-flex items-center gap-1 px-3 py-1.5 text-xs bg-emerald-600 text-white rounded hover:bg-emerald-700 disabled:opacity-50"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                      {restoringId === o.id ? "Geri alınıyor…" : "Geri Al"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
