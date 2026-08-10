import { useEffect, useState, useMemo } from "react";
import axios from "axios";
import { TrendingUp, Calendar, DollarSign, ShoppingCart, RefreshCw } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const CHANNEL_META = {
  instagram_ads: { label: "Instagram Reklam", color: "bg-pink-500" },
  instagram_organic: { label: "Instagram Organik", color: "bg-pink-300" },
  facebook_ads: { label: "Facebook Reklam", color: "bg-blue-600" },
  facebook_organic: { label: "Facebook Organik", color: "bg-blue-400" },
  tiktok_ads: { label: "TikTok Reklam", color: "bg-black" },
  tiktok_organic: { label: "TikTok Organik", color: "bg-gray-700" },
  google_ads: { label: "Google Reklam", color: "bg-yellow-500" },
  google_organic: { label: "Google Organik", color: "bg-yellow-300" },
  bing_ads: { label: "Bing Reklam", color: "bg-cyan-500" },
  email: { label: "E-posta", color: "bg-indigo-500" },
  sms: { label: "SMS", color: "bg-violet-500" },
  influencer: { label: "Influencer", color: "bg-rose-500" },
  affiliate: { label: "Affiliate", color: "bg-orange-500" },
  direct: { label: "Direkt", color: "bg-gray-500" },
  referral: { label: "Referans", color: "bg-teal-500" },
  trendyol: { label: "Trendyol", color: "bg-orange-600" },
  hepsiburada: { label: "Hepsiburada", color: "bg-red-600" },
  temu: { label: "Temu", color: "bg-amber-700" },
  n11: { label: "n11", color: "bg-purple-600" },
  whatsapp_organic: { label: "WhatsApp", color: "bg-green-500" },
  search_organic: { label: "Arama (Organik)", color: "bg-yellow-200" },
  paid_search: { label: "Ücretli Arama", color: "bg-yellow-600" },
  paid_social: { label: "Ücretli Sosyal", color: "bg-blue-500" },
};

const meta = (c) => CHANNEL_META[c] || { label: c, color: "bg-gray-400" };

export default function Attribution() {
  const today = new Date();
  const [from, setFrom] = useState(new Date(today.getTime() - 30 * 864e5).toISOString().slice(0, 10));
  const [to, setTo] = useState(today.toISOString().slice(0, 10));
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/attribution/stats`, {
        headers: authHeaders(),
        params: { start_date: from, end_date: to + "T23:59:59" },
      });
      setData(data);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const maxRev = useMemo(
    () => Math.max(1, ...(data?.by_channel || []).map((c) => c.revenue)),
    [data]
  );
  const maxTraffic = useMemo(
    () => Math.max(1, ...(data?.traffic_by_channel || []).map((c) => c.sessions)),
    [data]
  );

  return (
    <div className="space-y-6" data-testid="attribution-page">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><TrendingUp /> Kaynak & Funnel Analizi</h1>
          <p className="text-sm text-gray-500 mt-1">Siparişlerin hangi kanaldan geldiğini görün. UTM takibi + referrer algılama.</p>
        </div>
        <div className="flex items-center gap-2 bg-white p-2 border rounded-lg">
          <Calendar size={15} className="text-gray-500" />
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="text-sm px-2 py-1 rounded border-0" />
          <span className="text-gray-400">→</span>
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="text-sm px-2 py-1 rounded border-0" />
          <button onClick={load} data-testid="attribution-refresh" className="px-3 py-1 bg-black text-white text-xs rounded hover:bg-gray-800 inline-flex items-center gap-1">
            <RefreshCw size={12} /> Uygula
          </button>
        </div>
      </div>

      {/* Totals */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-gradient-to-br from-slate-900 to-slate-700 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-70 mb-1">Toplam Sipariş</div>
          <div className="text-3xl font-bold">{data?.totals?.orders || 0}</div>
        </div>
        <div className="bg-gradient-to-br from-emerald-600 to-emerald-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-70 mb-1">Toplam Gelir</div>
          <div className="text-3xl font-bold">₺{(data?.totals?.revenue || 0).toLocaleString("tr-TR")}</div>
        </div>
        <div className="bg-gradient-to-br from-blue-600 to-blue-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-70 mb-1">Ziyaret Oturumu</div>
          <div className="text-3xl font-bold">{(data?.traffic_by_channel || []).reduce((a, b) => a + (b.sessions || 0), 0)}</div>
        </div>
        <div className="bg-gradient-to-br from-violet-600 to-violet-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-70 mb-1">Dönüşüm</div>
          <div className="text-3xl font-bold">
            {(() => {
              const o = data?.totals?.orders || 0;
              const s = (data?.traffic_by_channel || []).reduce((a, b) => a + (b.sessions || 0), 0);
              return s > 0 ? `${((o / s) * 100).toFixed(1)}%` : "—";
            })()}
          </div>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-5">
        {/* Revenue by channel */}
        <div className="bg-white rounded-xl border p-5">
          <h3 className="font-semibold flex items-center gap-2 mb-4"><DollarSign size={16} /> Kanal Bazında Gelir</h3>
          <div className="space-y-3">
            {(data?.by_channel || []).length === 0 ? (
              <div className="text-sm text-gray-400 py-4 text-center">Bu tarih aralığında kaynaklı sipariş yok.</div>
            ) : (data?.by_channel || []).map((c) => {
              const m = meta(c.channel);
              const pct = (c.revenue / maxRev) * 100;
              return (
                <div key={c.channel}>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="font-medium">{m.label}</span>
                    <span className="text-gray-500">{c.orders} sipariş · <span className="text-black font-semibold">₺{c.revenue.toLocaleString("tr-TR")}</span></span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div className={`h-full ${m.color}`} style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Traffic by channel */}
        <div className="bg-white rounded-xl border p-5">
          <h3 className="font-semibold flex items-center gap-2 mb-4"><ShoppingCart size={16} /> Kanal Bazında Ziyaret</h3>
          <div className="space-y-3">
            {(data?.traffic_by_channel || []).length === 0 ? (
              <div className="text-sm text-gray-400 py-4 text-center">Henüz takip oturumu yok.</div>
            ) : (data?.traffic_by_channel || []).map((c) => {
              const m = meta(c.channel);
              const pct = (c.sessions / maxTraffic) * 100;
              return (
                <div key={c.channel}>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="font-medium">{m.label}</span>
                    <span className="text-gray-500">{c.sessions} oturum</span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div className={`h-full ${m.color} opacity-60`} style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Top campaigns */}
      <div className="bg-white rounded-xl border">
        <h3 className="font-semibold p-5 pb-3">En İyi Kampanyalar</h3>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Kampanya</th>
              <th className="text-left p-3">Kaynak</th>
              <th className="text-right p-3">Sipariş</th>
              <th className="text-right p-3">Gelir</th>
            </tr>
          </thead>
          <tbody>
            {(data?.by_campaign || []).length === 0 ? (
              <tr><td colSpan={4} className="p-6 text-center text-gray-400">Kampanya parametresi olan sipariş yok.</td></tr>
            ) : (data?.by_campaign || []).map((c, i) => (
              <tr key={i} className="border-t">
                <td className="p-3 font-medium">{c.campaign}</td>
                <td className="p-3 text-gray-600">{c.source || "—"}</td>
                <td className="p-3 text-right">{c.orders}</td>
                <td className="p-3 text-right font-semibold">₺{c.revenue.toLocaleString("tr-TR")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {loading && <div className="text-center text-sm text-gray-400">Yükleniyor...</div>}

      {/* UTM example guide */}
      <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 text-sm text-blue-900">
        <h4 className="font-semibold mb-2">Kaynak Takibi Nasıl Çalışıyor?</h4>
        <ol className="list-decimal ml-5 space-y-1 text-blue-800">
          <li>Instagram / Google / E-posta reklamlarınıza URL'yi <code className="bg-white px-1 rounded text-xs">?utm_source=instagram&amp;utm_medium=paid_social&amp;utm_campaign=yaz2026</code> olarak ekleyin.</li>
          <li>Ziyaretçi siteye girdiğinde otomatik olarak oturum açılır, kaynak localStorage'a yazılır.</li>
          <li>Sipariş verildiğinde bu kaynak siparişin <strong>attribution</strong> alanına kaydedilir, burada raporlanır.</li>
          <li>UTM yoksa referrer (Instagram/Google/…) otomatik algılanır. UTM ve referrer yoksa "Direkt" kabul edilir.</li>
        </ol>
      </div>
    </div>
  );
}
