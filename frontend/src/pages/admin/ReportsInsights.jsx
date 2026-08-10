/**
 * ReportsInsights.jsx — Gelişmiş Raporlar (İl/İlçe, Kanal, Uzun süredir satılmayan)
 * Tarih aralığı + kaynak filtresiyle: konum, satış kanalı (Instagram/Google/pazaryeri),
 * ve uzun süredir satış görmeyen ürünler.
 */
import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { MapPin, Radio, PackageX, TrendingUp, Clock, CreditCard, Ticket, UserPlus } from "lucide-react";
import ReportScopeBadge from "../../components/ReportScopeBadge";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });
const TRY = (n) => `${Number(n || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₺`;

const CHANNEL_LABELS = {
  trendyol: "Trendyol", hepsiburada: "Hepsiburada", temu: "Temu",
  instagram: "Instagram", google: "Google", meta: "Meta/Facebook",
  tiktok: "TikTok", youtube: "YouTube", pinterest: "Pinterest",
  email: "E-posta", sms: "SMS", direct: "Doğrudan / Site",
};

function todayISO(offsetDays = 0) {
  // Not: sabit bir referans yok; tarayıcı tarihini kullanır (admin aracı).
  const d = new Date(Date.now() - offsetDays * 86400000);
  return d.toISOString().slice(0, 10);
}

export default function ReportsInsights() {
  const [tab, setTab] = useState("location");
  const [start, setStart] = useState(todayISO(30));
  const [end, setEnd] = useState(todayISO(0));
  const [source, setSource] = useState("all");
  const [locGroup, setLocGroup] = useState("city");
  const [days, setDays] = useState(90);

  const [loc, setLoc] = useState(null);
  const [src, setSrc] = useState(null);
  const [never, setNever] = useState(null);
  const [hour, setHour] = useState(null);
  const [pay, setPay] = useState(null);
  const [coupon, setCoupon] = useState(null);
  const [cust, setCust] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const q = `start_date=${start}T00:00:00Z&end_date=${end}T23:59:59Z`;
    try {
      if (tab === "location") setLoc((await axios.get(`${API}/admin/reports/by-location?${q}&group=${locGroup}&source=${source}&limit=200`, auth())).data);
      else if (tab === "source") setSrc((await axios.get(`${API}/admin/reports/by-source?${q}`, auth())).data);
      else if (tab === "never") setNever((await axios.get(`${API}/admin/reports/never-sold?days=${days}&limit=1000`, auth())).data);
      else if (tab === "hour") setHour((await axios.get(`${API}/admin/reports/by-hour?${q}&source=${source}`, auth())).data);
      else if (tab === "pay") setPay((await axios.get(`${API}/admin/reports/by-payment?${q}&source=${source}`, auth())).data);
      else if (tab === "coupon") setCoupon((await axios.get(`${API}/admin/reports/coupon-performance?${q}`, auth())).data);
      else if (tab === "cust") setCust((await axios.get(`${API}/admin/reports/customer-type?${q}`, auth())).data);
    } catch (_) { /* sessiz */ }
    finally { setLoading(false); }
  }, [tab, start, end, source, locGroup, days]);

  useEffect(() => { load(); }, [load]);

  const maxRev = tab === "location"
    ? Math.max(1, ...((loc?.rows || []).map((x) => x.revenue)))
    : Math.max(1, ...((src?.rows || []).map((x) => x.revenue)));

  return (
    <div className="space-y-5" data-testid="reports-insights">
      <div>
        <h1 className="text-2xl font-semibold flex items-center gap-2"><TrendingUp size={22} /> Gelişmiş Raporlar <ReportScopeBadge kind={tab === "never" ? "stock" : "exclude"} /></h1>
        <p className="text-sm text-gray-500 mt-1">İl/ilçe, satış kanalı (Instagram/Google/pazaryeri) ve uzun süredir satılmayan ürünler. <span className="text-gray-400">(Tüm satış sekmelerinde iptal &amp; iade hariç.)</span></p>
      </div>

      <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-900">
        <span className="font-semibold">Bu raporda:</span> Seçtiğiniz tarih aralığında satışlarınızı farklı açılardan kesersiniz — <b>il/ilçe bazlı</b> (nereden ne kadar satıyorsunuz), <b>satış kanalı</b> (pazaryeri + Instagram/Google/Meta gibi trafik kaynakları), <b>saatlik yoğunluk</b>, <b>ödeme tipi</b>, <b>kupon performansı</b> ve <b>yeni/tekrar eden müşteri</b> kırılımı. <b>Uzun süredir satılmayan</b> sekmesiyle 30–365 gündür hiç satmayan ürünleri ve bunlara bağlanmış stok değerini görüp indirim/tasfiye kararı verebilirsiniz.
      </div>

      {/* Sekmeler */}
      <div className="flex flex-wrap gap-2">
        {[
          ["location", "İl / İlçe", MapPin],
          ["source", "Satış Kanalı", Radio],
          ["hour", "Saatlik", Clock],
          ["pay", "Ödeme Tipi", CreditCard],
          ["coupon", "Kupon", Ticket],
          ["cust", "Yeni/Tekrar Müşteri", UserPlus],
          ["never", "Uzun Süredir Satılmayan", PackageX],
        ].map(([k, lbl, Icon]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm border ${tab === k ? "bg-black text-white border-black" : "bg-white text-gray-700 hover:border-black"}`}>
            <Icon size={14} /> {lbl}
          </button>
        ))}
      </div>

      {/* Filtreler */}
      <div className="bg-white border rounded-xl p-4 flex flex-wrap items-end gap-3">
        {tab !== "never" && (
          <>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Başlangıç</label>
              <input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="border rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Bitiş</label>
              <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="border rounded px-2 py-1.5 text-sm" />
            </div>
          </>
        )}
        {tab === "location" && (
          <>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Grup</label>
              <select value={locGroup} onChange={(e) => setLocGroup(e.target.value)} className="border rounded px-2 py-1.5 text-sm">
                <option value="city">İl bazlı</option>
                <option value="district">İlçe bazlı</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Kaynak</label>
              <select value={source} onChange={(e) => setSource(e.target.value)} className="border rounded px-2 py-1.5 text-sm">
                <option value="all">Tümü</option>
                <option value="site">Site</option>
                <option value="trendyol">Trendyol</option>
                <option value="hepsiburada">Hepsiburada</option>
                <option value="temu">Temu</option>
              </select>
            </div>
          </>
        )}
        {tab === "never" && (
          <div>
            <label className="block text-xs text-gray-500 mb-1">Kaç gündür satış yok</label>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="border rounded px-2 py-1.5 text-sm">
              <option value={30}>30 gün</option>
              <option value={60}>60 gün</option>
              <option value={90}>90 gün</option>
              <option value={180}>180 gün</option>
              <option value={365}>1 yıl</option>
            </select>
          </div>
        )}
        <button onClick={load} className="px-4 py-2 bg-black text-white rounded-lg text-sm">Yenile</button>
        {loading && <span className="text-xs text-gray-400">Yükleniyor…</span>}
      </div>

      {/* İL / İLÇE */}
      {tab === "location" && (
        <div className="bg-white border rounded-xl overflow-hidden">
          <div className="flex items-center justify-between p-4 border-b">
            <h3 className="font-semibold">{locGroup === "city" ? "İl" : "İlçe"} Bazlı Satış</h3>
            <span className="text-sm text-gray-500">Toplam: <b>{TRY(loc?.totals?.revenue)}</b> · {loc?.totals?.orders || 0} sipariş</span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr>
                <th className="text-left p-3">{locGroup === "city" ? "İl" : "İlçe"}</th>
                {locGroup === "district" && <th className="text-left p-3">İl</th>}
                <th className="text-right p-3">Sipariş</th>
                <th className="text-right p-3">Adet</th>
                <th className="text-right p-3">Ciro</th>
                <th className="p-3 w-1/4"></th>
              </tr>
            </thead>
            <tbody>
              {(loc?.rows || []).map((r, i) => (
                <tr key={i} className="border-t">
                  <td className="p-3 font-medium">{r.location}</td>
                  {locGroup === "district" && <td className="p-3 text-gray-500">{r.city || "—"}</td>}
                  <td className="p-3 text-right">{r.orders}</td>
                  <td className="p-3 text-right">{r.items}</td>
                  <td className="p-3 text-right font-semibold">{TRY(r.revenue)}</td>
                  <td className="p-3"><div className="h-2 bg-gray-100 rounded"><div className="h-2 bg-black rounded" style={{ width: `${Math.round((r.revenue / maxRev) * 100)}%` }} /></div></td>
                </tr>
              ))}
              {(!loc?.rows || loc.rows.length === 0) && <tr><td colSpan={6} className="p-6 text-center text-gray-400">Bu aralıkta veri yok.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {/* KANAL */}
      {tab === "source" && (
        <div className="bg-white border rounded-xl overflow-hidden">
          <div className="flex items-center justify-between p-4 border-b">
            <h3 className="font-semibold">Satış Kanalı (Pazaryeri + Trafik Kaynağı)</h3>
            <span className="text-sm text-gray-500">Toplam: <b>{TRY(src?.totals?.revenue)}</b> · {src?.totals?.orders || 0} sipariş</span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr><th className="text-left p-3">Kanal</th><th className="text-right p-3">Sipariş</th><th className="text-right p-3">Ciro</th><th className="p-3 w-1/3"></th></tr>
            </thead>
            <tbody>
              {(src?.rows || []).map((r, i) => (
                <tr key={i} className="border-t">
                  <td className="p-3 font-medium">{CHANNEL_LABELS[r.channel] || r.channel}</td>
                  <td className="p-3 text-right">{r.orders}</td>
                  <td className="p-3 text-right font-semibold">{TRY(r.revenue)}</td>
                  <td className="p-3"><div className="h-2 bg-gray-100 rounded"><div className="h-2 bg-indigo-500 rounded" style={{ width: `${Math.round((r.revenue / maxRev) * 100)}%` }} /></div></td>
                </tr>
              ))}
              {(!src?.rows || src.rows.length === 0) && <tr><td colSpan={4} className="p-6 text-center text-gray-400">Bu aralıkta veri yok.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {/* UZUN SÜREDİR SATILMAYAN */}
      {tab === "never" && (
        <div className="bg-white border rounded-xl overflow-hidden">
          <div className="flex items-center justify-between p-4 border-b">
            <h3 className="font-semibold">Son {never?.days || days} gündür satılmayan ürünler</h3>
            <span className="text-sm text-gray-500"><b>{never?.count || 0}</b> ürün · Bağlı stok değeri <b>{TRY(never?.total_stock_value)}</b></span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr><th className="text-left p-3">Ürün</th><th className="text-left p-3">Stok Kodu</th><th className="text-left p-3">Bedenler (stok)</th><th className="text-right p-3">Toplam Stok</th><th className="text-right p-3">Fiyat</th><th className="text-right p-3">Stok Değeri</th></tr>
            </thead>
            <tbody>
              {(never?.items || []).map((r, i) => (
                <tr key={i} className="border-t">
                  <td className="p-3 font-medium flex items-center gap-2">
                    {r.image ? <img src={r.image} alt="" className="w-8 h-10 object-cover rounded" /> : null}
                    {r.name}
                  </td>
                  <td className="p-3 text-gray-500">{r.stock_code || "—"}</td>
                  <td className="p-3 text-gray-600 text-xs">{r.sizes || "—"}</td>
                  <td className="p-3 text-right">{r.stock}</td>
                  <td className="p-3 text-right">{TRY(r.price)}</td>
                  <td className="p-3 text-right font-semibold">{TRY(r.stock_value)}</td>
                </tr>
              ))}
              {(!never?.items || never.items.length === 0) && <tr><td colSpan={6} className="p-6 text-center text-gray-400">Bu aralıkta satılmayan ürün yok — hepsi satmış! 🎉</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {/* SAATLİK */}
      {tab === "hour" && (
        <div className="bg-white border rounded-xl p-4">
          <h3 className="font-semibold mb-4">Saatlik Satış Dağılımı (TR saati)</h3>
          {(() => {
            const mx = Math.max(1, ...((hour?.rows || []).map((x) => x.orders)));
            return (
              <div className="flex items-end gap-1 h-48">
                {(hour?.rows || []).map((r) => (
                  <div key={r.hour} className="flex-1 flex flex-col items-center justify-end group" title={`${r.hour}:00 · ${r.orders} sipariş · ${TRY(r.revenue)}`}>
                    <div className="w-full bg-indigo-400 group-hover:bg-indigo-600 rounded-t transition-colors" style={{ height: `${Math.round((r.orders / mx) * 100)}%`, minHeight: r.orders ? 2 : 0 }} />
                    <span className="text-[9px] text-gray-400 mt-1">{r.hour}</span>
                  </div>
                ))}
              </div>
            );
          })()}
          <p className="text-xs text-gray-500 mt-3">Toplam {hour?.totals?.orders || 0} sipariş · {TRY(hour?.totals?.revenue)}. En yüksek çubuk = en yoğun saat.</p>
        </div>
      )}

      {/* ÖDEME TİPİ */}
      {tab === "pay" && (
        <div className="bg-white border rounded-xl overflow-hidden">
          <div className="flex items-center justify-between p-4 border-b"><h3 className="font-semibold">Ödeme Tipine Göre Satış</h3><span className="text-sm text-gray-500">Toplam {TRY(pay?.totals?.revenue)}</span></div>
          <table className="w-full text-sm"><thead className="bg-gray-50 text-gray-500 text-xs uppercase"><tr><th className="text-left p-3">Ödeme</th><th className="text-right p-3">Sipariş</th><th className="text-right p-3">Ciro</th></tr></thead>
            <tbody>
              {(pay?.rows || []).map((r, i) => (<tr key={i} className="border-t"><td className="p-3 font-medium">{r.method}</td><td className="p-3 text-right">{r.orders}</td><td className="p-3 text-right font-semibold">{TRY(r.revenue)}</td></tr>))}
              {(!pay?.rows || pay.rows.length === 0) && <tr><td colSpan={3} className="p-6 text-center text-gray-400">Veri yok.</td></tr>}
            </tbody></table>
        </div>
      )}

      {/* KUPON */}
      {tab === "coupon" && (
        <div className="bg-white border rounded-xl overflow-hidden">
          <div className="flex items-center justify-between p-4 border-b"><h3 className="font-semibold">Kupon Performansı</h3><span className="text-sm text-gray-500">Toplam indirim {TRY(coupon?.totals?.discount)} · ciro {TRY(coupon?.totals?.revenue)}</span></div>
          <table className="w-full text-sm"><thead className="bg-gray-50 text-gray-500 text-xs uppercase"><tr><th className="text-left p-3">Kupon</th><th className="text-right p-3">Kullanım</th><th className="text-right p-3">İndirim</th><th className="text-right p-3">Ciro</th></tr></thead>
            <tbody>
              {(coupon?.rows || []).map((r, i) => (<tr key={i} className="border-t"><td className="p-3 font-mono font-medium">{r.coupon}</td><td className="p-3 text-right">{r.orders}</td><td className="p-3 text-right text-red-600">-{TRY(r.discount)}</td><td className="p-3 text-right font-semibold">{TRY(r.revenue)}</td></tr>))}
              {(!coupon?.rows || coupon.rows.length === 0) && <tr><td colSpan={4} className="p-6 text-center text-gray-400">Bu aralıkta kupon kullanımı yok.</td></tr>}
            </tbody></table>
        </div>
      )}

      {/* YENİ / TEKRAR EDEN MÜŞTERİ */}
      {tab === "cust" && (
        <div className="grid sm:grid-cols-3 gap-4">
          <div className="bg-white border rounded-xl p-5">
            <p className="text-xs uppercase tracking-wider text-gray-500">Yeni Müşteri</p>
            <p className="text-3xl font-bold mt-1">{cust?.new?.customers ?? 0}</p>
            <p className="text-sm text-gray-500 mt-1">{cust?.new?.orders ?? 0} sipariş · {TRY(cust?.new?.revenue)}</p>
          </div>
          <div className="bg-white border rounded-xl p-5">
            <p className="text-xs uppercase tracking-wider text-gray-500">Tekrar Eden Müşteri</p>
            <p className="text-3xl font-bold mt-1">{cust?.returning?.customers ?? 0}</p>
            <p className="text-sm text-gray-500 mt-1">{cust?.returning?.orders ?? 0} sipariş · {TRY(cust?.returning?.revenue)}</p>
          </div>
          <div className="bg-black text-white rounded-xl p-5">
            <p className="text-xs uppercase tracking-wider text-white/60">Tekrar Alışveriş Oranı</p>
            <p className="text-3xl font-bold mt-1">%{cust?.repeat_rate ?? 0}</p>
            <p className="text-sm text-white/60 mt-1">Sadık müşteri göstergesi</p>
          </div>
        </div>
      )}
    </div>
  );
}
