/**
 * ReportsExtended.jsx — Yeni gelişmiş rapor seti
 *
 * Tab'lar:
 *   1. Stok Değer  (alış + satış değeri)
 *   2. Hızlı Satan  (fast movers)
 *   3. Yavaş Satan  (slow movers + dead stock)
 *   4. İade Oranı Uyarısı
 *   5. Net Kâr (kanal bazlı)
 *   6. Maliyet Yönetimi (per ürün manuel maliyet girişi)
 */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Package, TrendingUp, TrendingDown, AlertTriangle, DollarSign, Wallet, RefreshCw,
  Save, Search, Box, Activity, Zap, Factory, CalendarClock,
} from "lucide-react";
import ReportScopeBadge from "../../components/ReportScopeBadge";

// Her sekmenin iptal/iade kapsama türü (rozet için)
const _TAB_SCOPE = {
  stock: "stock", forecast: "stock", costs: "stock",
  fast: "exclude", slow: "exclude",
  returns: "returns",
  profit: "cancelOnly",
};

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const fmtMoney = (v) => "₺" + (Number(v || 0)).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = (v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`);
const fmtNum = (v) => Number(v || 0).toLocaleString("tr-TR");

const TABS = [
  { key: "stock", label: "Stok Değer", icon: Box },
  { key: "forecast", label: "Üretim Önerisi", icon: Factory },
  { key: "fast", label: "Hızlı Satan", icon: TrendingUp },
  { key: "slow", label: "Yavaş Satan", icon: TrendingDown },
  { key: "returns", label: "İade Oranı", icon: AlertTriangle },
  { key: "profit", label: "Net Kâr (Kanal)", icon: Wallet },
  { key: "costs", label: "Maliyet Girişi", icon: DollarSign },
];

export default function ReportsExtended() {
  const [tab, setTab] = useState("stock");

  return (
    <div data-testid="reports-extended-page" className="space-y-6">
      <div>
        <h1 className="text-2xl font-light text-gray-900 flex items-center gap-2 flex-wrap">Gelişmiş Raporlar <ReportScopeBadge kind={_TAB_SCOPE[tab] || "exclude"} /></h1>
        <p className="text-sm text-gray-500 mt-1">Stok değer, satış hızı, iade oranı uyarısı, kanal bazlı net kâr ve maliyet yönetimi.</p>
      </div>

      <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-900">
        <span className="font-semibold">Bu raporda:</span> Kârlılık ve stok verimliliğini yönetirsiniz — <b>Stok Değer</b> (alış/satış değeri ve potansiyel kâr marjı), <b>Üretim Önerisi</b> (satış hızına göre tükenecek ürünler ve önerilen üretim adedi), <b>Hızlı/Yavaş Satan</b> ürünler, <b>İade Oranı</b> uyarısı, kanal bazlı <b>Net Kâr</b> ve ürün <b>Maliyet Girişi</b>. "Yavaş Satan / Ölü Stok" ile uzun süredir satmayıp paranızı bağlayan ürünleri fark eder, "Net Kâr" ve "Maliyet Girişi" ile hangi kanalın gerçekte kâr bıraktığını görüp kârınızı artıracak kararlar alırsınız.
      </div>

      <div className="border-b border-gray-200 flex gap-1 overflow-x-auto">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              data-testid={`rep-tab-${t.key}`}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
                active ? "border-gray-900 text-gray-900" : "border-transparent text-gray-500 hover:text-gray-900"
              }`}
            >
              <Icon className="w-4 h-4" />
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "stock" && <StockValuation />}
      {tab === "forecast" && <StockoutForecast />}
      {tab === "fast" && <FastMovers />}
      {tab === "slow" && <SlowMovers />}
      {tab === "returns" && <ReturnRateAlerts />}
      {tab === "profit" && <ProfitByChannel />}
      {tab === "costs" && <CostManagement />}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
function StockValuation() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/reports2/stock-valuation`, auth());
      setData(r.data);
    } catch (e) { toast.error(e?.response?.data?.detail || "Yüklenemedi"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="text-gray-500 text-sm">Hesaplanıyor...</div>;
  if (!data) return null;
  const t = data.totals;
  const missing = data.missing_cost || [];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <KPI testid="kpi-units" label="Toplam Adet" value={fmtNum(t.units)} icon={Package} />
        <KPI testid="kpi-cost-value" label="Alış Değeri" value={fmtMoney(t.cost_value)} icon={DollarSign} tone="info" />
        <KPI testid="kpi-sale-value" label="Satış Değeri" value={fmtMoney(t.sale_value)} icon={Wallet} tone="ok" />
        <KPI testid="kpi-potential-profit" label="Potansiyel Kâr" value={fmtMoney(t.potential_profit)} icon={TrendingUp} tone="ok" />
        <KPI testid="kpi-margin" label="Marj" value={fmtPct(t.potential_margin_pct)} icon={Activity} tone="ok" />
        {t.missing_count > 0 && (
          <KPI testid="kpi-missing" label="Alış Girilmemiş" value={`${fmtNum(t.missing_count)} ürün`} icon={AlertTriangle} tone="warn" />
        )}
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <Table title="Marka Bazlı" rows={data.by_brand}
          cols={[{ k: "name", l: "Marka" }, { k: "units", l: "Adet", num: true },
                 { k: "cost", l: "Alış", money: true }, { k: "sale", l: "Satış", money: true }]} />
        <Table title="Kategori Bazlı" rows={data.by_category}
          cols={[{ k: "name", l: "Kategori" }, { k: "units", l: "Adet", num: true },
                 { k: "cost", l: "Alış", money: true }, { k: "sale", l: "Satış", money: true }]} />
      </div>

      {missing.length > 0 && (
        <div className="border border-amber-200 rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 bg-amber-50 border-b border-amber-200 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-600" />
            <span className="text-sm font-semibold text-amber-800">
              Alış fiyatı girilmemiş ürünler ({fmtNum(t.missing_count)})
            </span>
            <span className="text-xs text-amber-700 ml-1">
              — alış değerine dahil edilmedi. Ürünü açıp “Alış fiyatı” alanını doldurun.
            </span>
          </div>
          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-600 text-xs uppercase sticky top-0">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">Ürün</th>
                  <th className="px-4 py-2 text-left font-medium">Stok Kodu</th>
                  <th className="px-4 py-2 text-right font-medium">Stok</th>
                  <th className="px-4 py-2 text-right font-medium">Satış Fiyatı</th>
                  <th className="px-4 py-2 text-right font-medium">Satış Değeri</th>
                </tr>
              </thead>
              <tbody>
                {missing.map((m) => (
                  <tr key={m.id} className="border-t border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-900">
                      <a href={`/admin/urunler/${m.id}`} target="_blank" rel="noopener noreferrer"
                         className="text-blue-700 hover:underline">{m.name}</a>
                    </td>
                    <td className="px-4 py-2 text-gray-600 font-mono text-xs">{m.stock_code || "—"}</td>
                    <td className="px-4 py-2 text-right text-gray-900">{fmtNum(m.units)}</td>
                    <td className="px-4 py-2 text-right text-gray-900">{fmtMoney(m.sale_price)}</td>
                    <td className="px-4 py-2 text-right font-medium text-gray-900">{fmtMoney(m.sale_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="text-xs text-gray-500">
        💡 Alış değeri ürünlerin <b>Alış fiyatı</b> (purchase_price) alanından hesaplanır; girilmemiş ürünler tahmin edilmez,
        yukarıda listelenir. Satış değeri tüm stoklu ürünlerin güncel satış fiyatından (indirimli varsa o) hesaplanır.
      </p>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
function StockoutForecast() {
  const [velocityDays, setVelocityDays] = useState(30);
  const [horizonDays, setHorizonDays] = useState(60);
  const [coverDays, setCoverDays] = useState(60);
  const [data, setData] = useState({ items: [], summary: {} });
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(
        `${API}/admin/reports2/stockout-forecast?velocity_days=${velocityDays}&horizon_days=${horizonDays}&target_cover_days=${coverDays}&min_velocity=0.05`,
        auth()
      );
      setData(r.data);
    } catch (e) { toast.error(e?.response?.data?.detail || "Yüklenemedi"); }
    finally { setLoading(false); }
  }, [velocityDays, horizonDays, coverDays]);
  useEffect(() => { load(); }, [load]);

  const s = data.summary || {};
  return (
    <div className="space-y-4" data-testid="forecast-panel">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-sm flex items-start gap-2">
        <CalendarClock className="w-4 h-4 mt-0.5 flex-shrink-0 text-blue-700" />
        <div>
          Son <strong>{velocityDays}</strong> gün satış ortalamasına göre <strong>{horizonDays}</strong> gün içinde tükenecek ürünler.
          Önerilen üretim adedi <strong>{coverDays}</strong> günlük stok hedefine göre hesaplanır.
          Bu ürünleri "Üretim" menüsünden imalat planına ekleyebilirsiniz.
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-sm text-gray-600 flex items-center gap-1">Geçmiş veri (gün):
          <select value={velocityDays} onChange={(e)=>setVelocityDays(+e.target.value)} className="border rounded px-2 py-1 text-sm">
            <option value={14}>14</option><option value={30}>30</option><option value={60}>60</option><option value={90}>90</option>
          </select>
        </label>
        <label className="text-sm text-gray-600 flex items-center gap-1">Tükenme süresi:
          <select value={horizonDays} onChange={(e)=>setHorizonDays(+e.target.value)} className="border rounded px-2 py-1 text-sm">
            <option value={30}>30 gün</option><option value={60}>60 gün</option><option value={90}>90 gün</option><option value={120}>120 gün</option><option value={180}>180 gün</option>
          </select>
        </label>
        <label className="text-sm text-gray-600 flex items-center gap-1">Hedef stok süresi:
          <select value={coverDays} onChange={(e)=>setCoverDays(+e.target.value)} className="border rounded px-2 py-1 text-sm">
            <option value={30}>30 gün</option><option value={60}>60 gün</option><option value={90}>90 gün</option><option value={120}>120 gün</option>
          </select>
        </label>
        <button onClick={async () => {
          try {
            const r = await axios.post(`${API}/admin/production-hooks/send-stockout-alert`, {}, auth());
            const d = r.data.alert?.delivered || {};
            toast.success(`${r.data.items_count} ürün için uyarı gönderildi (${d.smtp ? "SMTP ✓" : ""}${d.resend ? "Resend ✓" : ""} in-app ✓)`);
          } catch (e) { toast.error(e?.response?.data?.detail || "Gönderilemedi"); }
        }} className="px-3 py-1.5 bg-amber-600 text-white rounded text-sm hover:bg-amber-700 flex items-center gap-1" data-testid="send-alert-email">
          📧 Email Uyarı Gönder
        </button>
        <button onClick={async () => {
          const items = data.items.filter(it => it.suggested_production_qty > 0).map(it => ({
            product_id: it.product_id, product_name: it.name,
            quantity: it.suggested_production_qty, stockout_date: it.stockout_date,
            daily_velocity: it.daily_velocity, severity: it.severity,
          }));
          if (!items.length) { toast.error("Üretim önerisi olan ürün yok"); return; }
          try {
            const r = await axios.post(`${API}/admin/production-hooks/add-to-plan`, items, auth());
            toast.success(`${r.data.added + r.data.updated} ürün üretim planına eklendi`);
          } catch (e) { toast.error("Eklenemedi"); }
        }} className="px-3 py-1.5 bg-emerald-700 text-white rounded text-sm hover:bg-emerald-800 flex items-center gap-1" data-testid="add-to-plan-all">
          🏭 Tümünü Üretim Planına Ekle
        </button>
        <button onClick={load} className="ml-auto text-sm text-blue-700 hover:underline flex items-center gap-1">
          <RefreshCw className={`w-3.5 h-3.5 ${loading?"animate-spin":""}`}/>Yenile
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <KPI testid="forecast-critical" label="🔴 Kritik (≤14g)" value={fmtNum(s.critical)} tone="danger" />
        <KPI testid="forecast-high" label="🟠 Yüksek (≤30g)" value={fmtNum(s.high)} tone="warn" />
        <KPI testid="forecast-warning" label="🟡 Uyarı" value={fmtNum(s.warning)} tone="info" />
        <KPI testid="forecast-units" label="Toplam Üretim Adedi" value={fmtNum(s.total_production_units)} icon={Factory} />
        <KPI testid="forecast-value" label="Üretim Değeri" value={fmtMoney(s.total_production_value)} icon={DollarSign} tone="info" />
      </div>

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-600">
            <tr>
              <th className="text-left px-3 py-2">Ürün</th>
              <th className="text-right px-3 py-2">Mevcut Stok</th>
              <th className="text-right px-3 py-2">Günlük Satış</th>
              <th className="text-center px-3 py-2">🗓️ Tükenir</th>
              <th className="text-right px-3 py-2">Kalan Gün</th>
              <th className="text-right px-3 py-2">⚒️ Üretim Önerisi</th>
              <th className="text-right px-3 py-2">Değer</th>
              <th className="text-center px-3 py-2">Durum</th>
              <th className="text-center px-3 py-2">İşlem</th>
            </tr>
          </thead>
          <tbody data-testid="forecast-table">
            {data.items.length === 0 ? (
              <tr><td colSpan={9} className="text-center text-gray-500 py-8">
                {loading ? "Hesaplanıyor..." : "🎉 Tüm hızlı satan ürünlerin stoğu yeterli, üretim gerektiren ürün yok."}
              </td></tr>
            ) : data.items.map((it) => (
              <tr key={it.product_id} className={`border-t hover:bg-gray-50 ${
                it.severity === "critical" ? "bg-red-50/50" :
                it.severity === "high" ? "bg-orange-50/50" : ""
              }`}>
                <td className="px-3 py-2">
                  <div className="font-medium">{it.name}</div>
                  {it.stock_code && <div className="text-xs text-gray-500 font-mono">SKU: {it.stock_code}</div>}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtNum(it.current_stock)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{it.daily_velocity.toFixed(2)}</td>
                <td className="px-3 py-2 text-center font-medium tabular-nums">{it.stockout_date_tr}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  <span className={`font-bold ${it.severity === "critical" ? "text-red-700" : it.severity === "high" ? "text-orange-700" : "text-amber-700"}`}>
                    {it.days_until_stockout}g
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums font-semibold text-emerald-700">
                  {it.suggested_production_qty > 0 ? `+${fmtNum(it.suggested_production_qty)}` : "—"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-600">
                  {it.suggested_production_qty > 0 ? fmtMoney(it.suggested_production_value) : "—"}
                </td>
                <td className="px-3 py-2 text-center">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                    it.severity === "critical" ? "bg-red-100 text-red-800" :
                    it.severity === "high" ? "bg-orange-100 text-orange-800" :
                    "bg-amber-100 text-amber-800"
                  }`}>
                    {it.severity === "critical" ? "ACİL ÜRET" : it.severity === "high" ? "Üret" : "İzle"}
                  </span>
                </td>
                <td className="px-3 py-2 text-center">
                  {it.suggested_production_qty > 0 && (
                    <button
                      onClick={async () => {
                        try {
                          await axios.post(`${API}/admin/production-hooks/add-to-plan`, [{
                            product_id: it.product_id, product_name: it.name,
                            quantity: it.suggested_production_qty, stockout_date: it.stockout_date,
                            daily_velocity: it.daily_velocity, severity: it.severity,
                          }], auth());
                          toast.success(`+${it.suggested_production_qty} ${it.name.slice(0,30)} planına eklendi`);
                        } catch (e) { toast.error("Eklenemedi"); }
                      }}
                      className="text-xs text-emerald-700 hover:underline whitespace-nowrap"
                      data-testid={`plan-add-${it.product_id}`}
                    >
                      + Plana Ekle
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-gray-500">
        💡 Sistem, sipariş kalemlerindeki ürün adlarını ürün veritabanınızla akıllı eşleştirir.
        Sadece <strong>günde 0.05+ adet</strong> satan ürünler analiz edilir.
      </p>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
function FastMovers() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState({ items: [] });
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/reports2/fast-movers?days=${days}&top=100`, auth());
      setData(r.data);
    } catch (e) { toast.error("Yüklenemedi"); } finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [days]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="text-sm text-gray-600">Periyot:</label>
        <select value={days} onChange={(e) => setDays(+e.target.value)} className="border rounded px-2 py-1 text-sm" data-testid="fast-days-select">
          <option value={7}>Son 7 gün</option>
          <option value={30}>Son 30 gün</option>
          <option value={60}>Son 60 gün</option>
          <option value={90}>Son 90 gün</option>
        </select>
        <button onClick={load} className="ml-auto text-sm text-blue-700 hover:underline flex items-center gap-1"><RefreshCw className={`w-3.5 h-3.5 ${loading?"animate-spin":""}`}/>Yenile</button>
      </div>
      <Table testid="fast-table" rows={data.items}
        cols={[
          { k: "name", l: "Ürün" }, { k: "stock_code", l: "SKU" },
          { k: "sold_qty", l: "Satılan", num: true },
          { k: "daily_velocity", l: "Günlük Hız", num: true },
          { k: "stock", l: "Stok", num: true },
          { k: "days_until_stockout", l: "Tükenir (gün)", num: true },
          { k: "revenue", l: "Ciro", money: true },
        ]} />
    </div>
  );
}

function SlowMovers() {
  const [days, setDays] = useState(60);
  const [data, setData] = useState({ items: [] });
  const [dead, setDead] = useState({ items: [] });
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const [a, b] = await Promise.all([
        axios.get(`${API}/admin/reports2/slow-movers?days=${days}&min_stock=1&limit=200`, auth()),
        axios.get(`${API}/admin/reports2/dead-stock?days=90`, auth()),
      ]);
      setData(a.data); setDead(b.data);
    } catch (e) { toast.error("Yüklenemedi"); } finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [days]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <label className="text-sm text-gray-600">Yavaş Satan Eşik (gün):</label>
        <select value={days} onChange={(e) => setDays(+e.target.value)} className="border rounded px-2 py-1 text-sm">
          <option value={30}>30</option><option value={60}>60</option><option value={90}>90</option>
        </select>
        <span className="text-xs text-gray-500">Günlük velocity &lt; 0.1 olanlar</span>
        <button onClick={load} className="ml-auto text-sm text-blue-700 hover:underline flex items-center gap-1"><RefreshCw className={`w-3.5 h-3.5 ${loading?"animate-spin":""}`}/>Yenile</button>
      </div>
      <div className="border-b pb-2">
        <h3 className="font-medium text-sm mb-2">Yavaş Satanlar — Stoğa Bağlanmış Para</h3>
        <Table rows={data.items}
          cols={[
            { k: "name", l: "Ürün" }, { k: "stock_code", l: "SKU" },
            { k: "stock", l: "Stok", num: true }, { k: "sold_qty_period", l: "Satış", num: true },
            { k: "daily_velocity", l: "Günlük Hız" }, { k: "tied_value", l: "Bağlı Para", money: true },
          ]} />
      </div>
      <div>
        <h3 className="font-medium text-sm mb-2">Ölü Stok — 90 gündür hiç satılmamış ({dead.total || 0} ürün)</h3>
        <Table rows={dead.items}
          cols={[
            { k: "name", l: "Ürün" }, { k: "stock_code", l: "SKU" },
            { k: "stock", l: "Stok", num: true }, { k: "tied_value", l: "Bağlı Para", money: true },
          ]} />
      </div>
    </div>
  );
}

function ReturnRateAlerts() {
  const [threshold, setThreshold] = useState(20);
  const [days, setDays] = useState(90);
  const [minOrders, setMinOrders] = useState(5);
  const [data, setData] = useState({ items: [] });
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/reports2/return-rate?threshold=${threshold}&days=${days}&min_orders=${minOrders}`, auth());
      setData(r.data);
    } catch (e) { toast.error("Yüklenemedi"); } finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [threshold, days, minOrders]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-sm text-gray-600 flex items-center gap-1">Eşik:
          <input type="number" min={1} max={100} value={threshold} onChange={(e)=>setThreshold(+e.target.value)} className="border rounded px-2 py-1 text-sm w-20" data-testid="ret-threshold" />%</label>
        <label className="text-sm text-gray-600 flex items-center gap-1">Periyot:
          <select value={days} onChange={(e)=>setDays(+e.target.value)} className="border rounded px-2 py-1 text-sm">
            <option value={30}>30g</option><option value={60}>60g</option><option value={90}>90g</option><option value={180}>180g</option>
          </select></label>
        <label className="text-sm text-gray-600 flex items-center gap-1">Min Sipariş:
          <input type="number" min={1} max={100} value={minOrders} onChange={(e)=>setMinOrders(+e.target.value)} className="border rounded px-2 py-1 text-sm w-20" /></label>
        <button onClick={load} className="ml-auto text-sm text-blue-700 hover:underline flex items-center gap-1"><RefreshCw className={`w-3.5 h-3.5 ${loading?"animate-spin":""}`}/>Yenile</button>
      </div>
      <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm">
        ⚠️ {data.total ?? 0} ürün iade oranı eşiği aşıyor. Bu ürünleri inceleyin — beden/kalite/açıklama sorunu olabilir.
      </div>
      <Table rows={data.items} testid="returns-table"
        cols={[
          { k: "name", l: "Ürün" },
          { k: "sold", l: "Satış", num: true },
          { k: "returned", l: "İade", num: true },
          { k: "return_rate_pct", l: "Oran %", num: true, render: (r) => (
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${r.severity==="critical"?"bg-red-100 text-red-800":r.severity==="high"?"bg-orange-100 text-orange-800":"bg-amber-100 text-amber-800"}`}>{r.return_rate_pct}%</span>
            ) },
        ]} />
    </div>
  );
}

function ProfitByChannel() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState({ items: [], totals: {} });
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/reports2/profit-by-channel?days=${days}`, auth());
      setData(r.data);
    } catch (e) { toast.error("Yüklenemedi"); } finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [days]);

  const t = data.totals || {};
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <label className="text-sm text-gray-600">Periyot:</label>
        <select value={days} onChange={(e) => setDays(+e.target.value)} className="border rounded px-2 py-1 text-sm">
          <option value={7}>Son 7 gün</option><option value={30}>Son 30 gün</option><option value={90}>Son 90 gün</option>
        </select>
        <button onClick={load} className="ml-auto text-sm text-blue-700 hover:underline flex items-center gap-1"><RefreshCw className={`w-3.5 h-3.5 ${loading?"animate-spin":""}`}/>Yenile</button>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <KPI label="Sipariş" value={fmtNum(t.orders)} />
        <KPI label="Ciro" value={fmtMoney(t.revenue)} tone="info" />
        <KPI label="Maliyet" value={fmtMoney(t.cost)} />
        <KPI label="Komisyon" value={fmtMoney(t.commission)} />
        <KPI label="Net Kâr" value={fmtMoney(t.net_profit)} tone={t.net_profit >= 0 ? "ok" : "danger"} />
        <KPI label="Marj" value={fmtPct(t.margin_pct)} tone="ok" />
      </div>
      <Table rows={data.items} testid="profit-table"
        cols={[
          { k: "channel", l: "Kanal", render: (r) => <span className="font-mono uppercase">{r.channel}</span> },
          { k: "orders", l: "Sipariş", num: true },
          { k: "revenue", l: "Ciro", money: true },
          { k: "cost", l: "Maliyet", money: true },
          { k: "commission", l: `Komisyon (~${"%"})`, money: true },
          { k: "refunds", l: "İade", money: true },
          { k: "net_profit", l: "Net Kâr", money: true, render: (r) => (
              <span className={r.net_profit >= 0 ? "text-emerald-700 font-semibold" : "text-red-700 font-semibold"}>{fmtMoney(r.net_profit)}</span>
            ) },
          { k: "margin_pct", l: "Marj", render: (r) => fmtPct(r.margin_pct) },
        ]} />
      <p className="text-xs text-gray-500">
        💡 Komisyon oranları varsayılan: Trendyol 18%, HB 17%, N11 12%, Site 3%. Gerçek değerler için "Maliyet Girişi" sekmesinden ürün maliyetlerini girin.
      </p>
    </div>
  );
}

function CostManagement() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(false);
  const [edits, setEdits] = useState({});  // product_id → new_cost

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page, limit: 50, only_missing: onlyMissing });
      if (q) params.set("q", q);
      const r = await axios.get(`${API}/admin/product-costs?${params}`, auth());
      setData(r.data);
    } catch (e) { toast.error("Yüklenemedi"); } finally { setLoading(false); }
  }, [q, page, onlyMissing]);
  useEffect(() => { load(); }, [load]);

  const save = async (product_id) => {
    const cost = parseFloat(edits[product_id]);
    if (isNaN(cost) || cost < 0) { toast.error("Geçersiz değer"); return; }
    try {
      await axios.post(`${API}/admin/product-costs`, { product_id, cost_price: cost }, auth());
      toast.success("Kaydedildi");
      setEdits((s) => { const n = { ...s }; delete n[product_id]; return n; });
      load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Hata"); }
  };

  const saveAll = async () => {
    const items = Object.entries(edits)
      .map(([product_id, v]) => ({ product_id, cost_price: parseFloat(v), currency: "TRY" }))
      .filter((x) => !isNaN(x.cost_price) && x.cost_price >= 0);
    if (!items.length) { toast.error("Değişiklik yok"); return; }
    try {
      const r = await axios.post(`${API}/admin/product-costs/bulk`, { items }, auth());
      toast.success(`${r.data.count} ürün maliyeti güncellendi`);
      setEdits({});
      load();
    } catch (e) { toast.error("Toplu kayıt hatası"); }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-2 top-2.5 text-gray-400" />
          <input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Ürün ara (isim/SKU)..." className="border rounded pl-8 pr-3 py-2 text-sm w-72" data-testid="costs-search" />
        </div>
        <label className="text-sm text-gray-600 flex items-center gap-1">
          <input type="checkbox" checked={onlyMissing} onChange={(e) => { setOnlyMissing(e.target.checked); setPage(1); }} />
          Sadece maliyeti olmayanlar
        </label>
        {Object.keys(edits).length > 0 && (
          <button onClick={saveAll} className="ml-auto bg-emerald-700 text-white px-3 py-1.5 rounded text-sm flex items-center gap-1 hover:bg-emerald-800" data-testid="costs-save-all">
            <Save className="w-4 h-4" /> Tümünü Kaydet ({Object.keys(edits).length})
          </button>
        )}
      </div>

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-600">
            <tr>
              <th className="text-left px-3 py-2">Ürün</th>
              <th className="text-left px-3 py-2">SKU</th>
              <th className="text-right px-3 py-2">Stok</th>
              <th className="text-right px-3 py-2">Satış Fiyatı</th>
              <th className="text-right px-3 py-2">Mevcut Maliyet</th>
              <th className="text-right px-3 py-2">Yeni Maliyet (₺)</th>
              <th className="text-right px-3 py-2">Marj %</th>
              <th></th>
            </tr>
          </thead>
          <tbody data-testid="costs-table">
            {data.items.length === 0 ? (
              <tr><td colSpan={8} className="text-center text-gray-500 py-6">Sonuç yok</td></tr>
            ) : data.items.map((it) => {
              const current = it.cost_price ?? "";
              const edited = edits[it.product_id];
              const newVal = edited !== undefined ? parseFloat(edited) : current;
              const margin = (it.price && newVal) ? ((it.price - newVal) / it.price * 100) : null;
              return (
                <tr key={it.product_id} className="border-t">
                  <td className="px-3 py-2 max-w-xs truncate">{it.name}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">{it.stock_code}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(it.stock)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(it.price)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{it.cost_price != null ? fmtMoney(it.cost_price) : <span className="text-amber-600">—</span>}</td>
                  <td className="px-3 py-2 text-right">
                    <input
                      type="number" step="0.01" min="0"
                      value={edited ?? ""}
                      placeholder={current ? String(current) : "0.00"}
                      onChange={(e) => setEdits((s) => ({ ...s, [it.product_id]: e.target.value }))}
                      className="w-24 border rounded px-2 py-1 text-sm text-right"
                      data-testid={`cost-input-${it.product_id}`}
                    />
                  </td>
                  <td className="px-3 py-2 text-right text-xs">
                    {margin != null ? <span className={margin >= 30 ? "text-emerald-700" : "text-amber-700"}>{margin.toFixed(1)}%</span> : "—"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {edited !== undefined && <button onClick={() => save(it.product_id)} className="text-xs text-blue-700 hover:underline">Kaydet</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-gray-600">
        <span>Toplam: {fmtNum(data.total)} ürün</span>
        <div className="flex items-center gap-2">
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="px-3 py-1 border rounded disabled:opacity-50">‹ Önceki</button>
          <span>Sayfa {page}</span>
          <button onClick={() => setPage(p => p + 1)} className="px-3 py-1 border rounded">Sonraki ›</button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Shared components
// ──────────────────────────────────────────────────────────────────────────────
function KPI({ label, value, icon: Icon, tone = "default", testid }) {
  const tones = {
    default: "bg-white border-gray-200 text-gray-900",
    ok: "bg-emerald-50 border-emerald-200 text-emerald-900",
    info: "bg-blue-50 border-blue-200 text-blue-900",
    warn: "bg-amber-50 border-amber-200 text-amber-900",
    danger: "bg-red-50 border-red-200 text-red-900",
  };
  return (
    <div data-testid={testid} className={`border rounded-lg p-3 ${tones[tone]}`}>
      <div className="flex items-center justify-between text-xs opacity-80">
        <span>{label}</span>
        {Icon ? <Icon className="w-4 h-4" /> : null}
      </div>
      <div className="text-2xl font-light tabular-nums mt-1">{value}</div>
    </div>
  );
}

function Table({ rows, cols, title, testid }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      {title ? <div className="px-4 py-2 border-b text-sm font-medium">{title}</div> : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid={testid}>
          <thead className="bg-gray-50 text-xs text-gray-600">
            <tr>{cols.map((c) => (
              <th key={c.k} className={`px-3 py-2 ${c.num || c.money ? "text-right" : "text-left"}`}>{c.l}</th>
            ))}</tr>
          </thead>
          <tbody>
            {(!rows || rows.length === 0) ? (
              <tr><td colSpan={cols.length} className="text-center text-gray-500 py-6">Veri yok</td></tr>
            ) : rows.map((r, i) => (
              <tr key={i} className="border-t hover:bg-gray-50">
                {cols.map((c) => (
                  <td key={c.k} className={`px-3 py-2 ${c.num || c.money ? "text-right tabular-nums" : ""}`}>
                    {c.render ? c.render(r) : c.money ? fmtMoney(r[c.k]) : c.num ? fmtNum(r[c.k]) : (r[c.k] ?? "—")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
