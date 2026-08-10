/**
 * StockAlerts.jsx — Kritik Stok Uyarıları + Yeniden Sipariş Önerileri
 *
 * İki sekme: "Kritik Stok" (threshold altı) + "Yeniden Sipariş Gerekli"
 * (stokta 0 ama son 60 günde satılmış ürünler).
 *
 * Backend: /api/bulk-ops/stock-alerts, /api/bulk-ops/reorder-suggestions
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { AlertTriangle, RefreshCw, Package, TrendingDown, Power } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function StockAlerts() {
  const [tab, setTab] = useState("critical");  // critical | reorder
  const [threshold, setThreshold] = useState(3);
  const [critical, setCritical] = useState(null);
  const [reorder, setReorder] = useState(null);
  const [loading, setLoading] = useState(false);

  const token = useMemo(() => localStorage.getItem("token"), []);
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const loadCritical = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/bulk-ops/stock-alerts?threshold=${threshold}`, auth);
      setCritical(r.data);
    } catch { toast.error("Yüklenemedi"); }
    finally { setLoading(false); }
  };
  const loadReorder = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/bulk-ops/reorder-suggestions`, auth);
      setReorder(r.data);
    } catch { toast.error("Yüklenemedi"); }
    finally { setLoading(false); }
  };

  const deactivateOnMarketplaces = async () => {
    if (!await window.appConfirm("Stoku biten tüm aktif ürünler pazaryerlerinde pasife alınacak (qty=0 gönderilecek). Devam edilsin mi?")) return;
    try {
      const r = await axios.post(`${API}/bulk-ops/stock-alerts/deactivate-on-marketplaces`, { threshold: 0 }, auth);
      toast.success(r.data?.message || "Pasifleme tamamlandı");
    } catch (e) {
      toast.error("Pasifleme hatası: " + (e.response?.data?.detail || e.message));
    }
  };

  useEffect(() => {
    if (tab === "critical") loadCritical(); else loadReorder();
    // eslint-disable-next-line
  }, [tab, threshold]);

  return (
    <div data-testid="stock-alerts-page">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <AlertTriangle size={20} className="text-amber-500" />
            Stok Uyarıları
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Kritik seviyeye düşen ürünler ve yeniden sipariş önerileri.
          </p>
        </div>
        <button onClick={() => tab === "critical" ? loadCritical() : loadReorder()}
          className="flex items-center gap-1 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
          <RefreshCw size={14} /> Yenile
        </button>
      </div>

      {/* Pazaryerinde pasifleme */}
      <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-red-900 flex items-center gap-2">
            <Power size={14} /> Stoksuz Ürünleri Pazaryerinde Pasife Al
          </div>
          <p className="text-xs text-red-700 mt-1">
            Stok miktarı 0 olan aktif ürünler için tüm etkin pazaryerlerine qty=0 güncellemesi gönderilir.
          </p>
        </div>
        <button
          onClick={deactivateOnMarketplaces}
          className="text-xs bg-red-600 hover:bg-red-700 text-white px-3 py-2 rounded-lg font-medium"
          data-testid="deactivate-oos-marketplaces-btn"
        >
          <Power size={12} className="inline mr-1" /> Pazaryerinde Pasife Al
        </button>
      </div>

      <div className="flex gap-1 border-b border-gray-200 mb-4">
        <button onClick={() => setTab("critical")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${
            tab === "critical" ? "border-orange-500 text-orange-600" : "border-transparent text-gray-600"
          }`} data-testid="alerts-tab-critical">
          <Package size={14} className="inline mr-1" /> Kritik Stok {critical ? `(${critical.total})` : ""}
        </button>
        <button onClick={() => setTab("reorder")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${
            tab === "reorder" ? "border-orange-500 text-orange-600" : "border-transparent text-gray-600"
          }`} data-testid="alerts-tab-reorder">
          <TrendingDown size={14} className="inline mr-1" /> Yeniden Sipariş {reorder ? `(${reorder.total})` : ""}
        </button>
      </div>

      {tab === "critical" && (
        <>
          <div className="flex items-center gap-3 mb-3 bg-yellow-50 border border-yellow-200 rounded-lg p-3">
            <span className="text-sm font-medium">Eşik değeri:</span>
            <input type="number" min={0} max={50} value={threshold}
              onChange={(e) => setThreshold(parseInt(e.target.value) || 0)}
              className="w-20 border border-gray-200 rounded px-2 py-1 text-sm text-center"
              data-testid="alerts-threshold" />
            <span className="text-xs text-gray-500">Bu değerin altındaki varyantlar listelenir.</span>
          </div>
          <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
            <table className="admin-table admin-table-compact">
              <thead><tr><th>Görsel</th><th>Ürün</th><th>Varyant</th><th>Stok Kodu</th><th>Barkod</th><th>Stok</th><th>Fiyat</th></tr></thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={7} className="text-center py-8 text-gray-400">Yükleniyor...</td></tr>
                ) : !critical || critical.items.length === 0 ? (
                  <tr><td colSpan={7} className="text-center py-10 text-gray-400">🎉 Kritik stokta ürün yok</td></tr>
                ) : (
                  critical.items.map((i, idx) => (
                    <tr key={idx}>
                      <td>{i.image ? <img src={i.image} alt="" className="w-10 h-14 object-cover rounded border" /> : <span className="text-gray-300">—</span>}</td>
                      <td className="font-semibold text-sm">{i.product_name}</td>
                      <td className="text-xs">{i.variant}</td>
                      <td className="text-xs font-mono">{i.stock_code || "-"}</td>
                      <td className="text-xs font-mono text-gray-500">{i.barcode || "-"}</td>
                      <td>
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold ${
                          i.stock === 0 ? "bg-red-100 text-red-700" :
                          i.stock <= 1 ? "bg-orange-100 text-orange-700" :
                          "bg-yellow-100 text-yellow-700"
                        }`}>{i.stock}</span>
                      </td>
                      <td className="text-xs">{i.price ? `${i.price.toFixed(2)} ₺` : "-"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "reorder" && (
        <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
          <table className="admin-table admin-table-compact">
            <thead><tr><th>Görsel</th><th>Ürün</th><th>Stok Kodu</th><th>Son 60 Gün Satış</th><th>Mevcut Stok</th><th>Son Satış</th></tr></thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="text-center py-8 text-gray-400">Yükleniyor...</td></tr>
              ) : !reorder || reorder.items.length === 0 ? (
                <tr><td colSpan={6} className="text-center py-10 text-gray-400">Yeniden sipariş önerisi yok</td></tr>
              ) : (
                reorder.items.map((i, idx) => (
                  <tr key={idx}>
                    <td>{i.image ? <img src={i.image} alt="" className="w-10 h-14 object-cover rounded border" /> : "—"}</td>
                    <td className="font-semibold text-sm">{i.product_name}</td>
                    <td className="text-xs font-mono">{i.stock_code || "-"}</td>
                    <td><span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-green-100 text-green-700">{i.sold_60_days}</span></td>
                    <td><span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-700">{i.current_stock}</span></td>
                    <td className="text-[11px] text-gray-400">{i.last_sold ? new Date(i.last_sold).toLocaleDateString("tr-TR") : "-"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
