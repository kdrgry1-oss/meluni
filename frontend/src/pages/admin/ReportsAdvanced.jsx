/**
 * ReportsAdvanced.jsx — FAZ 8 ileri raporlar + FAZ 7 üretici performansı
 *  - İade: beden / ürün / sebep
 *  - Hızlı satış dedektörü
 *  - Üretici performans tablosu
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { TrendingUp, RotateCcw, Award, Sparkles } from "lucide-react";
import ReportScopeBadge from "../../components/ReportScopeBadge";
import { defaultReportRange, reportCoverageDays } from "../../lib/reportFilters";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function ReportsAdvanced() {
  const reportRange = defaultReportRange();
  const [bySize, setBySize] = useState([]);
  const [byProduct, setByProduct] = useState([]);
  const [reasons, setReasons] = useState([]);
  const [fastSelling, setFastSelling] = useState([]);
  const [mfgPerf, setMfgPerf] = useState([]);
  const [loading, setLoading] = useState(true);
  const [windowDays, setWindowDays] = useState(Math.min(90, reportCoverageDays()));
  const [minSold, setMinSold] = useState(10);

  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const load = async () => {
    setLoading(true);
    try {
      const [s, p, r, f, m] = await Promise.all([
        axios.get(`${API}/admin/reports/returns/by-size`, { ...auth, params: { start_date: reportRange.from, end_date: reportRange.to } }),
        axios.get(`${API}/admin/reports/returns/by-product`, { ...auth, params: { start_date: reportRange.from, end_date: reportRange.to } }),
        axios.get(`${API}/admin/reports/returns/reasons`, { ...auth, params: { start_date: reportRange.from, end_date: reportRange.to } }),
        axios.get(`${API}/admin/reports/fast-selling?window_days=${windowDays}&min_sold=${minSold}`, auth),
        axios.get(`${API}/admin/reports/manufacturer-performance`, auth),
      ]);
      setBySize(s.data?.by_size || []);
      setByProduct(p.data?.items || []);
      setReasons(r.data?.reasons || []);
      setFastSelling(f.data?.items || []);
      setMfgPerf(m.data?.items || []);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6" data-testid="reports-advanced-page">
      <div>
        <h1 className="text-2xl font-semibold">Gelişmiş Raporlar</h1>
        <p className="text-sm text-gray-500 mt-1">İade analizleri, hızlı satış dedektörü ve üretici performansı.</p>
        <p className="text-xs text-gray-400 mt-1">Varsayılan iade dönemi: {reportRange.from} → {reportRange.to}</p>
      </div>

      <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-900">
        <span className="font-semibold">Bu raporda:</span> İade sorunlarınızı ve satış hızınızı derinlemesine incelersiniz — <b>en çok iade edilen bedenler</b>, <b>iade sebepleri</b> ve <b>ürün bazında iade oranı</b> (yüksek oranlılar kırmızı) ile beden/kalite sorunlarını yakalarsınız. <b>Hızlı satış dedektörü</b> seçtiğiniz gün penceresinde belirlenen adedin üzerinde satan ürünleri işaretler (reklam önerisiyle), <b>üretici performans skoru</b> ise teslim gecikmesi ve adet farkına göre tedarikçilerinizi puanlar.
      </div>

      {loading ? <div className="text-gray-500">Yükleniyor...</div> : (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* İade Bedeni */}
        <section className="bg-white rounded-lg border border-gray-200 p-5">
          <h2 className="font-semibold mb-3 flex items-center gap-2 flex-wrap"><RotateCcw size={16} className="text-orange-600" /> En Çok İade Edilen Bedenler <ReportScopeBadge kind="returns" /></h2>
          {bySize.length === 0 ? <p className="text-sm text-gray-500">Kayıt yok.</p> : (
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500">
                <tr><th className="text-left pb-2">Beden</th><th className="text-right pb-2">Adet</th></tr>
              </thead>
              <tbody>
                {bySize.map((x, i) => (
                  <tr key={i} className="border-t">
                    <td className="py-2 font-mono font-semibold">{x.size}</td>
                    <td className="py-2 text-right">{x.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {/* İade Sebepleri */}
        <section className="bg-white rounded-lg border border-gray-200 p-5">
          <h2 className="font-semibold mb-3 flex items-center gap-2 flex-wrap"><RotateCcw size={16} className="text-red-600" /> İade Sebepleri <ReportScopeBadge kind="returns" /></h2>
          {reasons.length === 0 ? <p className="text-sm text-gray-500">Kayıt yok.</p> : (
            <table className="w-full text-sm">
              <tbody>
                {reasons.map((r, i) => (
                  <tr key={i} className="border-t">
                    <td className="py-2">{r.reason}</td>
                    <td className="py-2 text-right font-semibold">{r.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {/* Ürün bazlı iade */}
        <section className="bg-white rounded-lg border border-gray-200 p-5 lg:col-span-2">
          <h2 className="font-semibold mb-3 flex items-center gap-2 flex-wrap"><RotateCcw size={16} className="text-orange-600" /> En Çok İade Edilen Ürünler <ReportScopeBadge kind="returns" /></h2>
          {byProduct.length === 0 ? <p className="text-sm text-gray-500">Kayıt yok.</p> : (
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left pb-2">Ürün</th>
                  <th className="text-right pb-2">İptal Hariç Satış Ürün Adedi</th>
                  <th className="text-right pb-2">İade Ürün Adedi</th>
                  <th className="text-right pb-2">İade % (Trendyol)</th>
                </tr>
              </thead>
              <tbody>
                {byProduct.map((p, i) => (
                  <tr key={i} className="border-t">
                    <td className="py-2">{p.product_name}</td>
                    <td className="py-2 text-right">{p.sold}</td>
                    <td className="py-2 text-right text-orange-600 font-semibold">{p.returned}</td>
                    <td className="py-2 text-right">
                      {p.trendyol_return_rate_pct != null ? (
                        <span title="İade / Brüt Satış; iptaller iade adedine eklenmez" className={`font-semibold ${p.trendyol_return_rate_pct >= 50 ? "text-red-600" : p.trendyol_return_rate_pct >= 20 ? "text-orange-500" : "text-gray-700"}`}>
                          %{p.trendyol_return_rate_pct}
                        </span>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {/* Hızlı satış */}
        <section className="bg-white rounded-lg border border-gray-200 p-5 lg:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold flex items-center gap-2 flex-wrap"><TrendingUp size={16} className="text-green-600" /> Hızlı Satış Dedektörü <ReportScopeBadge kind="cancelOnly" /></h2>
            <div className="flex items-center gap-2 text-xs">
              <label>Pencere (gün):</label>
              <input type="number" value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value) || 14)}
                className="w-16 border px-2 py-1 rounded" data-testid="fs-window" />
              <label>Min satış:</label>
              <input type="number" value={minSold} onChange={(e) => setMinSold(Number(e.target.value) || 10)}
                className="w-16 border px-2 py-1 rounded" data-testid="fs-minsold" />
              <button onClick={load} className="bg-gray-900 text-white px-3 py-1 rounded" data-testid="fs-refresh">Uygula</button>
            </div>
          </div>
          {fastSelling.length === 0 ? (
            <p className="text-sm text-gray-500">Bu pencerede kriteri karşılayan ürün yok.</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {fastSelling.map((p) => (
                <div key={p.product_id} className="border rounded p-3 flex gap-3 hover:border-black transition-colors">
                  {p.image && <img src={p.image} alt="" className="w-16 h-16 object-cover rounded" />}
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm truncate">{p.product_name}</div>
                    <div className="text-xs text-gray-500">{p.product_age_days != null ? `${p.product_age_days} günlük ürün` : "—"} · Stok: {p.stock ?? "?"}</div>
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-lg font-bold text-green-600">{p.sold_in_window}</span>
                      <span className="text-xs text-gray-500">adet / {p.window_days} gün</span>
                    </div>
                    {p.recommend_ads && (
                      <div className="mt-1 inline-flex items-center gap-1 bg-green-100 text-green-700 text-[10px] px-2 py-0.5 rounded-full font-semibold">
                        <Sparkles size={10} /> Reklam Öneriliyor
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Üretici performansı */}
        <section className="bg-white rounded-lg border border-gray-200 p-5 lg:col-span-2">
          <h2 className="font-semibold mb-3 flex items-center gap-2"><Award size={16} className="text-blue-600" /> Üretici Performans Skoru</h2>
          {mfgPerf.length === 0 ? <p className="text-sm text-gray-500">Henüz üretim planında kayıt yok.</p> : (
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left pb-2">Üretici</th>
                  <th className="text-right pb-2">Toplam</th>
                  <th className="text-right pb-2">Teslim</th>
                  <th className="text-right pb-2">Ort. Gecikme</th>
                  <th className="text-right pb-2">Max Gecikme</th>
                  <th className="text-right pb-2">Adet Fark %</th>
                  <th className="text-right pb-2">Skor</th>
                </tr>
              </thead>
              <tbody>
                {mfgPerf.map((m, i) => (
                  <tr key={i} className="border-t">
                    <td className="py-2 font-medium">{m.name}</td>
                    <td className="py-2 text-right">{m.rows}</td>
                    <td className="py-2 text-right">{m.delivered}</td>
                    <td className="py-2 text-right">{m.avg_delay_days != null ? `${m.avg_delay_days} gün` : "—"}</td>
                    <td className="py-2 text-right">{m.max_delay_days != null ? `${m.max_delay_days} gün` : "—"}</td>
                    <td className="py-2 text-right">{m.avg_qty_diff_pct != null ? `%${m.avg_qty_diff_pct}` : "—"}</td>
                    <td className="py-2 text-right">
                      <span className={`px-2 py-0.5 rounded-full font-semibold text-xs ${m.score >= 80 ? "bg-green-100 text-green-700" : m.score >= 50 ? "bg-yellow-100 text-yellow-700" : "bg-red-100 text-red-700"}`}>
                        {m.score}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

      </div>)}
    </div>
  );
}
