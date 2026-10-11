import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import {
  TrendingUp, Package, ShoppingCart, Users, DollarSign,
  BarChart3, Calendar, ArrowUp, ArrowDown, RefreshCw,
  CheckSquare, AlertCircle, ChevronRight,
  Layers, Boxes, MessageSquare, CreditCard, ShoppingBag
} from "lucide-react";
import { ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import axios from "axios";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authH = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

function TasksWidget() {
  const [data, setData] = useState({ due_now: [], totals: {} });
  const [summary, setSummary] = useState({ due_now: 0, overdue: 0, completed_this_week: 0 });
  useEffect(() => {
    (async () => {
      try {
        const [a, s] = await Promise.all([
          axios.get(`${API}/admin/tasks`, { headers: authH(), params: { include_future: false } }),
          axios.get(`${API}/admin/tasks/summary`, { headers: authH() }),
        ]);
        setData(a.data); setSummary(s.data);
      } catch (_) {}
    })();
  }, []);
  const complete = async (id) => {
    try {
      await axios.post(`${API}/admin/tasks/${id}/complete`, {}, { headers: authH() });
      toast.success("Tamamlandı");
      setData((d) => ({ ...d, due_now: d.due_now.filter((t) => t.id !== id) }));
    } catch (_) {}
  };
  return (
    <div className="bg-white rounded-xl border p-5" data-testid="dashboard-tasks-widget">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold flex items-center gap-2"><CheckSquare size={16} className="text-emerald-600" /> Bugün Yapılacaklar</h3>
        <Link to="/admin/gorevler" className="text-xs text-blue-600 hover:underline inline-flex items-center">Tümü <ChevronRight size={12} /></Link>
      </div>
      <div className="flex gap-3 text-xs mb-3">
        <span className="flex items-center gap-1 text-red-600"><AlertCircle size={12} /> {summary.overdue} gecikmiş</span>
        <span className="text-gray-600">· {summary.due_now} bekliyor</span>
        <span className="text-emerald-700">· ✓ {summary.completed_this_week} bu hafta</span>
      </div>
      <div className="space-y-1.5 max-h-64 overflow-y-auto">
        {(data.due_now || []).length === 0 ? (
          <div className="text-center text-gray-400 py-4 text-sm">🎉 Tüm görevler tamam!</div>
        ) : data.due_now.slice(0, 6).map((t) => (
          <div key={t.id} className="flex items-center justify-between gap-2 p-2 bg-gray-50 rounded text-sm">
            <div className="flex-1 min-w-0">
              <div className="font-medium truncate">{t.title}</div>
              <div className="text-[10px] text-gray-500">{t.frequency === "daily" ? "Günlük" : t.frequency === "weekly" ? "Haftalık" : t.frequency}</div>
            </div>
            {t.action_path && <Link to={t.action_path} className="text-xs text-blue-600 hover:underline">Git</Link>}
            <button onClick={() => complete(t.id)} className="text-xs bg-emerald-600 text-white px-2 py-1 rounded hover:bg-emerald-700">✓</button>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState({
    total_orders: 0,
    total_revenue: 0,
    total_products: 0,
    total_customers: 0,
    pending_orders: 0,
    shipped_orders: 0,
    recent_orders: [],
    top_products: [],
    daily_series: [],
    payment_type_breakdown: {},
    abandoned_carts: { count: 0, item_count: 0, total_value: 0 },
    total_categories: 0,
    total_stock: 0,
    pending_messages: 0,
    avg_cart: 0,
    order_status_breakdown: {}
  });
  const [dateRange, setDateRange] = useState("0"); // days — varsayılan: bugün (0)
  const [platform, setPlatform] = useState("all"); // all | site | trendyol | hepsiburada | ...

  useEffect(() => {
    fetchStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateRange, platform]);

  const fetchStats = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };
      
      const res = await axios.get(`${API}/admin/dashboard-stats?days=${dateRange}&platform=${platform}`, { headers });
      setStats(res.data);
    } catch (err) {
      // Gerçek veri gelemezse SAHTE demo verisi GÖSTERME (yanıltıcı olur) — sıfırla + uyar.
      toast.error("İstatistikler yüklenemedi");
      setStats((s) => ({
        ...s,
        total_orders: 0, total_revenue: 0, total_products: 0, total_customers: 0,
        pending_orders: 0, shipped_orders: 0, orders_today: 0, revenue_today: 0,
        growth_orders: 0, growth_revenue: 0, recent_orders: [], top_products: [],
        daily_series: [], payment_type_breakdown: {},
        abandoned_carts: { count: 0, item_count: 0, total_value: 0 },
        total_categories: 0, total_stock: 0, pending_messages: 0, avg_cart: 0,
        order_status_breakdown: {},
      }));
    } finally {
      setLoading(false);
    }
  };

  const fmtTL = (n) => `₺${Number(n || 0).toLocaleString("tr-TR", { maximumFractionDigits: 0 })}`;
  const PAYMENT_LABELS = {
    card: "Kredi Kartı", credit_card: "Kredi Kartı", iyzico: "Kredi Kartı",
    bank_transfer: "Havale/EFT", havale: "Havale/EFT", eft: "Havale/EFT",
    cash_on_delivery: "Kapıda Ödeme", kapida: "Kapıda Ödeme", cod: "Kapıda Ödeme",
    marketplace: "Pazaryeri", trendyol: "Trendyol", hepsiburada: "Hepsiburada", "diğer": "Diğer",
  };

  const trendLabel = ({ "0": "düne göre", "7": "önceki haftaya göre", "30": "geçen aya göre",
    "90": "önceki döneme göre", "365": "geçen yıla göre" })[dateRange] || "önceki döneme göre";
  const rangeLabel = ({ "0": "bugün", "7": "son 7 gün", "30": "son 30 gün",
    "90": "son 90 gün", "365": "son 1 yıl" })[dateRange] || `son ${dateRange} gün`;
  const StatCard = ({ title, value, icon: Icon, trend, trendValue, color }) => (
    <div className="bg-white rounded-xl border p-6 hover:shadow-lg transition-shadow">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-gray-500 mb-1">{title}</p>
          <p className="text-2xl font-bold">{value}</p>
          {trend !== undefined && (
            <div className={`flex items-center gap-1 mt-2 text-sm ${trend >= 0 ? 'text-green-600' : 'text-red-600'}`}>
              {trend >= 0 ? <ArrowUp size={14} /> : <ArrowDown size={14} />}
              <span>{Math.abs(trendValue || trend)}%</span>
              <span className="text-gray-400 text-xs">{trendLabel}</span>
            </div>
          )}
        </div>
        <div className={`p-3 rounded-lg ${color || 'bg-gray-100'}`}>
          <Icon size={24} className={color ? 'text-white' : 'text-gray-600'} />
        </div>
      </div>
    </div>
  );

  const STATUS_COLORS = {
    pending: "bg-yellow-500",
    confirmed: "bg-blue-500",
    shipped: "bg-purple-500",
    delivered: "bg-green-500",
    cancelled: "bg-red-500"
  };

  // Ham İngilizce anahtar GÖSTERİLMESİN diye tam Türkçe fallback (backend katalogla aynı).
  const STATUS_LABELS = {
    pending: "Onay Bekliyor",
    awaiting_payment: "Ödeme Bekleniyor",
    payment_notified: "Ödeme Bildirimi Alındı",
    confirmed: "Onaylandı",
    preparing: "Hazırlanıyor",
    processing: "İşleme Alındı",
    ready_to_ship: "Kargoya Hazır",
    shipped: "Kargoya Verildi",
    in_transit: "Taşınıyor",
    out_for_delivery: "Dağıtımda",
    delivered: "Teslim Edildi",
    undelivered: "Teslim Edilemedi",
    return_requested: "İade Talebi Oluşturuldu",
    return_approved: "İade Onaylandı",
    return_rejected: "İade Reddedildi",
    return_in_transit: "İade Kargoda",
    returned: "İade Tamamlandı",
    refunded: "İade Bedeli Ödendi",
    partial_refunded: "Kısmi İade Yapıldı",
    cancelled: "İptal Edildi",
    cancel_refunded: "Ödemeli İptal Onaylandı",
    payment_failed: "Ödeme Alınamadı",
  };

  // Durum listesi: backend Türkçe etiket+renk verirse onu kullan; yoksa dict + fallback etiket.
  const statusList = (stats.order_status_list && stats.order_status_list.length)
    ? stats.order_status_list
    : Object.entries(stats.order_status_breakdown || {}).map(([key, count]) => ({
        key, count, label: STATUS_LABELS[key] || key, color: null,
      }));
  const statusLabel = (k) => {
    const hit = (stats.order_status_list || []).find((s) => s.key === k);
    return (hit && hit.label) || STATUS_LABELS[k] || k;
  };

  const totalStatusOrders = statusList.reduce((a, s) => a + (s.count || 0), 0);

  return (
    <div data-testid="admin-dashboard">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">Mağaza performansı ve istatistikleri</p>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            className="border px-3 py-2 rounded-lg text-sm"
            title="Platforma göre filtrele"
          >
            <option value="all">Tüm Platformlar</option>
            <option value="site">Sadece Site</option>
            <option value="trendyol">Trendyol</option>
            <option value="hepsiburada">Hepsiburada</option>
            <option value="ticimax">Ticimax</option>
            <option value="temu">Temu</option>
          </select>
          <select
            value={dateRange}
            onChange={(e) => setDateRange(e.target.value)}
            className="border px-3 py-2 rounded-lg text-sm"
          >
            <option value="0">Bugün</option>
            <option value="7">Son 7 Gün</option>
            <option value="30">Son 30 Gün</option>
            <option value="90">Son 90 Gün</option>
            <option value="365">Son 1 Yıl</option>
          </select>
          <button
            onClick={fetchStats}
            disabled={loading}
            className="p-2 border rounded-lg hover:bg-gray-50"
          >
            <RefreshCw size={18} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          title={dateRange === "0" ? "Bugünkü Sipariş" : "Sipariş"}
          value={stats.orders_in_range ?? stats.total_orders}
          icon={ShoppingCart}
          trend={stats.growth_orders}
          color="bg-blue-500"
        />
        <StatCard
          title={dateRange === "0" ? "Bugünkü Gelir" : "Gelir"}
          value={`₺${(stats.total_revenue || 0).toLocaleString('tr-TR')}`}
          icon={DollarSign}
          trend={stats.growth_revenue}
          color="bg-green-500"
        />
        <StatCard
          title="Satıştaki Ürün (Aktif)"
          value={stats.total_products}
          icon={Package}
          color="bg-purple-500"
        />
        <StatCard
          title="Toplam Üye"
          value={stats.total_customers}
          icon={Users}
          color="bg-orange-500"
        />
      </div>

      {/* Secondary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        <div className="bg-gradient-to-br from-yellow-50 to-yellow-100 rounded-xl p-5 border border-yellow-200">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-yellow-200 rounded-lg">
              <Package size={20} className="text-yellow-700" />
            </div>
            <div>
              <p className="text-sm text-yellow-700">Bekleyen Siparişler</p>
              <p className="text-2xl font-bold text-yellow-800">{stats.pending_orders}</p>
            </div>
          </div>
        </div>
        <div className="bg-gradient-to-br from-purple-50 to-purple-100 rounded-xl p-5 border border-purple-200">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-purple-200 rounded-lg">
              <TrendingUp size={20} className="text-purple-700" />
            </div>
            <div>
              <p className="text-sm text-purple-700">Kargodaki Siparişler</p>
              <p className="text-2xl font-bold text-purple-800">{stats.shipped_orders}</p>
            </div>
          </div>
        </div>
        <div className="bg-gradient-to-br from-green-50 to-green-100 rounded-xl p-5 border border-green-200">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-green-200 rounded-lg">
              <DollarSign size={20} className="text-green-700" />
            </div>
            <div>
              <p className="text-sm text-green-700">Bugünkü Gelir</p>
              <p className="text-2xl font-bold text-green-800">₺{(stats.revenue_today || 0).toLocaleString('tr-TR')}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Katalog + sepet ortalaması + cevap bekleyen (Ticimax "İstatistikler" seti) */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard title="Sepet Ortalaması" value={fmtTL(stats.avg_cart)} icon={ShoppingBag} color="bg-indigo-500" />
        <StatCard title="Satıştaki Kategori" value={stats.total_categories || 0} icon={Layers} color="bg-teal-500" />
        <StatCard title="Satıştaki Toplam Stok" value={(stats.total_stock || 0).toLocaleString("tr-TR")} icon={Boxes} color="bg-cyan-500" />
        <Link to="/admin/tickets" className="block">
          <StatCard title="Cevap Bekleyen Mesaj" value={stats.pending_messages || 0} icon={MessageSquare} color="bg-rose-500" />
        </Link>
      </div>

      {/* Sipariş Karşılaştırma Grafiği — günlük sipariş adedi + ciro (çift eksen) */}
      <div className="bg-white rounded-xl border p-6 mb-8">
        <h3 className="font-semibold mb-4 flex items-center gap-2">
          <BarChart3 size={18} /> Sipariş Karşılaştırma Grafiği
          <span className="text-xs font-normal text-gray-400">· {dateRange === "0" ? "bugün + son 14 gün" : rangeLabel}</span>
        </h3>
        {(stats.daily_series || []).length === 0 ? (
          <div className="text-center text-gray-400 py-12 text-sm">Seçili aralıkta veri yok</div>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={stats.daily_series || []}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d) => (d || "").slice(5)} />
              <YAxis yAxisId="left" tick={{ fontSize: 11 }} allowDecimals={false} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
              <Tooltip
                formatter={(v, n) => (n === "Ciro (₺)" ? fmtTL(v) : v)}
                labelFormatter={(d) => new Date(d).toLocaleDateString("tr-TR")}
              />
              <Legend />
              <Bar yAxisId="left" dataKey="orders" fill="#3b82f6" name="Sipariş" radius={[3, 3, 0, 0]} maxBarSize={28} />
              <Line yAxisId="right" type="monotone" dataKey="revenue" stroke="#10b981" strokeWidth={2} dot={false} name="Ciro (₺)" />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Terk Edilen Sepet + Ödeme Tipine Göre Siparişler */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        <div className="bg-white rounded-xl border p-6">
          <h3 className="font-semibold mb-4 flex items-center gap-2">
            <ShoppingCart size={18} className="text-amber-600" /> Terk Edilen Sepet İstatistikleri
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="text-center p-3 bg-amber-50 rounded-lg border border-amber-100">
              <p className="text-2xl font-bold text-amber-800">{stats.abandoned_carts?.count || 0}</p>
              <p className="text-xs text-amber-700 mt-1">Sepet Adet</p>
            </div>
            <div className="text-center p-3 bg-amber-50 rounded-lg border border-amber-100">
              <p className="text-2xl font-bold text-amber-800">{stats.abandoned_carts?.item_count || 0}</p>
              <p className="text-xs text-amber-700 mt-1">Ürün Adet</p>
            </div>
            <div className="text-center p-3 bg-amber-50 rounded-lg border border-amber-100">
              <p className="text-xl font-bold text-amber-800">{fmtTL(stats.abandoned_carts?.total_value)}</p>
              <p className="text-xs text-amber-700 mt-1">Sepet Tutarı</p>
            </div>
          </div>
          <Link to="/admin/terkedilmis-sepet" className="text-xs text-blue-600 hover:underline inline-flex items-center mt-3">
            Detaylar <ChevronRight size={12} />
          </Link>
        </div>

        <div className="bg-white rounded-xl border p-6">
          <h3 className="font-semibold mb-4 flex items-center gap-2">
            <CreditCard size={18} className="text-blue-600" /> Ödeme Tipine Göre Siparişler
          </h3>
          {Object.keys(stats.payment_type_breakdown || {}).length === 0 ? (
            <div className="text-center text-gray-400 py-6 text-sm">Seçili aralıkta sipariş yok</div>
          ) : (
            <div className="space-y-2">
              {Object.entries(stats.payment_type_breakdown || {})
                .sort((a, b) => (b[1].count || 0) - (a[1].count || 0))
                .map(([pt, v]) => (
                  <div key={pt} className="flex items-center justify-between py-2 border-b last:border-0 text-sm">
                    <span className="text-gray-700">{PAYMENT_LABELS[pt] || pt}</span>
                    <span className="flex items-center gap-3">
                      <span className="text-gray-500">{v.count} sipariş</span>
                      <span className="font-medium w-24 text-right">{fmtTL(v.revenue)}</span>
                    </span>
                  </div>
                ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
        {/* "Bugün Yapılacaklar" (görevler) widget'ı kullanıcı isteğiyle kaldırıldı. */}
        <div className="lg:col-span-2 bg-white rounded-xl border p-6">
          <h3 className="font-semibold mb-4 flex items-center gap-2">
            <BarChart3 size={18} />
            Sipariş Durumu Dağılımı
          </h3>
          <div className="space-y-3">
            {statusList.map((s) => {
              const percentage = totalStatusOrders > 0 ? (s.count / totalStatusOrders * 100) : 0;
              return (
                <div key={s.key}>
                  <div className="flex items-center justify-between text-sm mb-1">
                    <span className="text-gray-600">{s.label}</span>
                    <span className="font-medium">{s.count} ({percentage.toFixed(0)}%)</span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{ width: `${percentage}%`, backgroundColor: s.color || "#9CA3AF" }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Top Products */}
        <div className="bg-white rounded-xl border p-6">
          <h3 className="font-semibold mb-4 flex items-center gap-2">
            <TrendingUp size={18} />
            En Çok Satan Ürünler
          </h3>
          <div className="space-y-3">
            {(stats.top_products || []).map((product, idx) => (
              <div key={idx} className="flex items-center justify-between py-2 border-b last:border-0">
                <div className="flex items-center gap-3">
                  <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                    idx === 0 ? 'bg-yellow-100 text-yellow-700' :
                    idx === 1 ? 'bg-gray-100 text-gray-700' :
                    idx === 2 ? 'bg-orange-100 text-orange-700' :
                    'bg-gray-50 text-gray-500'
                  }`}>
                    {idx + 1}
                  </span>
                  <span className="text-sm truncate max-w-[200px]">{product.name}</span>
                </div>
                <div className="text-right">
                  <p className="text-sm font-medium">₺{product.revenue?.toLocaleString('tr-TR')}</p>
                  <p className="text-xs text-gray-500">{product.sold} adet</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Recent Orders */}
        <div className="bg-white rounded-xl border p-6 lg:col-span-2">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold flex items-center gap-2">
              <Calendar size={18} />
              Son Siparişler
            </h3>
            <a href="/admin/siparisler" className="text-sm text-blue-600 hover:underline">
              Tümünü Gör
            </a>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-3 font-medium text-gray-500">Sipariş No</th>
                  <th className="text-left py-3 font-medium text-gray-500">Tarih</th>
                  <th className="text-left py-3 font-medium text-gray-500">Durum</th>
                  <th className="text-right py-3 font-medium text-gray-500">Tutar</th>
                </tr>
              </thead>
              <tbody>
                {(stats.recent_orders || []).map((order) => (
                  <tr key={order.id} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="py-3 font-medium">{order.order_number}</td>
                    <td className="py-3 text-gray-500">
                      {new Date(order.created_at).toLocaleDateString('tr-TR')}
                    </td>
                    <td className="py-3">
                      <span className={`text-xs px-2 py-1 rounded-full ${
                        order.status === 'pending' ? 'bg-yellow-100 text-yellow-700' :
                        order.status === 'shipped' ? 'bg-purple-100 text-purple-700' :
                        order.status === 'confirmed' ? 'bg-blue-100 text-blue-700' :
                        order.status === 'delivered' ? 'bg-green-100 text-green-700' :
                        'bg-gray-100 text-gray-700'
                      }`}>
                        {statusLabel(order.status)}
                      </span>
                    </td>
                    <td className="py-3 text-right font-medium">
                      ₺{order.total?.toLocaleString('tr-TR')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
