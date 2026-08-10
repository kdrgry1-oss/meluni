/**
 * MarketplaceProfit.jsx — Pazaryeri Karlılık Raporu
 *
 * Her kanal/pazaryeri için brüt ciro, komisyon, kargo, iade ve net kâr.
 * Komisyon ayarları marketplace_accounts.transfer_rules'tan okunur.
 *
 * Backend: /api/analytics-extra/marketplace-profit?days=30
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { TrendingUp, Download } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function MarketplaceProfit() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState(30);

  const token = useMemo(() => localStorage.getItem("token"), []);
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/analytics-extra/marketplace-profit?days=${days}`, auth);
      setData(r.data);
    } catch { toast.error("Yüklenemedi"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [days]);

  const exportCsv = () => {
    if (!data?.items?.length) return;
    const header = ["Kanal", "Sipariş", "Brüt", "Komisyon", "Kargo", "İade", "Net Kâr", "Net Marj %"];
    const rows = data.items.map((i) => [i.channel, i.orders, i.gross, i.commission, i.shipping_cost, i.refunded, i.net, i.net_margin_pct]);
    const csv = [header, ...rows].map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `pazaryeri-karlilik-${days}gun.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div data-testid="marketplace-profit-page">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <TrendingUp size={20} /> Pazaryeri Karlılık Raporu
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Brüt ciro, komisyon, kargo maliyeti ve iadeler düşülerek net kâr hesaplanır. Kanal bazlı kıyaslama.
          </p>
        </div>
        <div className="flex gap-2">
          <select value={days} onChange={(e) => setDays(parseInt(e.target.value))}
            className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white"
            data-testid="profit-days">
            <option value={7}>Son 7 gün</option>
            <option value={30}>Son 30 gün</option>
            <option value={90}>Son 90 gün</option>
            <option value={365}>Son 1 yıl</option>
          </select>
          <button onClick={exportCsv}
            className="flex items-center gap-1 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
            <Download size={14} /> Excel'e Aktar
          </button>
        </div>
      </div>

      <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-900 mb-4">
        <span className="font-semibold">Bu raporda:</span> Her <b>kanal/pazaryeri</b> için (Trendyol, Hepsiburada, Temu, Site…) <b>brüt ciro</b>, <b>komisyon</b>, <b>kargo maliyeti</b> ve <b>iadeler</b> düşülerek <b>net kâr</b> ve <b>net marj %</b> hesaplanır. Hangi pazaryerinin gerçekte kâr bıraktığını, komisyon/kargonun kârınızı ne kadar erittiğini kıyaslayıp kârı en yükseğe çıkaracak kanallara odaklanabilir, sonuçları Excel'e aktarabilirsiniz.
      </div>

      {data?.totals && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div className="bg-white border rounded-xl p-4"><div className="text-xs text-gray-500 uppercase">Toplam Sipariş</div><div className="text-2xl font-black">{data.totals.orders}</div></div>
          <div className="bg-blue-50 border border-blue-200 rounded-xl p-4"><div className="text-xs text-blue-700 uppercase">Brüt Ciro</div><div className="text-2xl font-black text-blue-800">{data.totals.gross.toFixed(2)} ₺</div></div>
          <div className="bg-red-50 border border-red-200 rounded-xl p-4"><div className="text-xs text-red-700 uppercase">Toplam Komisyon+Kargo+İade</div><div className="text-2xl font-black text-red-800">{(data.totals.commission + data.totals.shipping_cost + data.totals.refunded).toFixed(2)} ₺</div></div>
          <div className="bg-green-50 border border-green-200 rounded-xl p-4"><div className="text-xs text-green-700 uppercase">Net Kâr</div><div className="text-2xl font-black text-green-800">{data.totals.net.toFixed(2)} ₺</div></div>
        </div>
      )}

      <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
        <table className="admin-table admin-table-compact">
          <thead>
            <tr>
              <th>Kanal / Pazaryeri</th>
              <th>Sipariş</th>
              <th>Brüt Ciro</th>
              <th>Komisyon</th>
              <th>Kargo Maliyeti</th>
              <th>İade</th>
              <th>Net Kâr</th>
              <th>Net Marj</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="text-center py-8 text-gray-400">Yükleniyor...</td></tr>
            ) : !data?.items?.length ? (
              <tr><td colSpan={8} className="text-center py-10 text-gray-400">Bu dönemde kayıt yok</td></tr>
            ) : (
              data.items.map((i, idx) => (
                <tr key={idx}>
                  <td className="font-semibold text-sm uppercase">{i.channel}</td>
                  <td className="text-sm">{i.orders}</td>
                  <td className="text-sm font-bold text-blue-700">{i.gross.toFixed(2)} ₺</td>
                  <td className="text-sm text-red-600">
                    {i.commission.toFixed(2)} ₺
                    <span className="text-[10px] text-gray-400 ml-1">({i.commission_type === "percent" ? `%${i.commission_rate}` : `${i.commission_rate}₺`})</span>
                  </td>
                  <td className="text-sm text-red-600">{i.shipping_cost.toFixed(2)} ₺</td>
                  <td className="text-sm text-red-600">{i.refunded.toFixed(2)} ₺</td>
                  <td className={`text-sm font-bold ${i.net >= 0 ? "text-green-700" : "text-red-700"}`}>
                    {i.net.toFixed(2)} ₺
                  </td>
                  <td>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold ${
                      i.net_margin_pct >= 20 ? "bg-green-100 text-green-700" :
                      i.net_margin_pct >= 10 ? "bg-yellow-100 text-yellow-800" :
                      "bg-red-100 text-red-700"
                    }`}>
                      %{i.net_margin_pct}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <p className="mt-4 text-[11px] text-gray-400">
        Komisyon oranları <strong>Pazaryerleri Hub</strong> {`>`} her pazaryerinin "Aktarım Kuralları" bölümünden okunur.
        Kanal karşılığı yoksa komisyon %0 hesaplanır.
      </p>
    </div>
  );
}
