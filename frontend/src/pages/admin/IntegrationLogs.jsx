/**
 * =============================================================================
 * IntegrationLogs.jsx — Pazaryeri Entegrasyon Logları
 * =============================================================================
 *
 * AMAÇ:
 *   Ticimax "Loglar" ekranındaki "Ürün Aktarım Logları" + "Sistem Logları"
 *   eşdeğeri. Her pazaryeri API çağrısı (ürün gönderimi, sipariş çekme,
 *   stok/fiyat update, iade, webhook) `integration_logs` koleksiyonuna
 *   yazılır; bu sayfa filtrelenebilir tablo + "Son İşlemler" özet widget'ı
 *   gösterir.
 *
 * FİLTRELER:
 *   - Pazaryeri (trendyol / hepsiburada / temu / ...)
 *   - Aktarım Türü (product_push, order_pull, stock_update, ...)
 *   - Durum (success / failed / partial / queued)
 *   - Tarih aralığı
 *   - Ürün/Sipariş ID (ref_id)
 *
 * BAĞLANTILI BACKEND:
 *   GET  /api/marketplace-hub/logs
 *   GET  /api/marketplace-hub/logs/summary
 *   GET  /api/marketplace-hub/marketplaces
 * =============================================================================
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { CheckCircle2, XCircle, Clock, AlertTriangle, RefreshCw, Download } from "lucide-react";
import Pagination from "../../components/admin/Pagination";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const ACTION_OPTIONS = [
  { value: "product_push", label: "Ürün Aktarımı" },
  { value: "product_update", label: "Ürün Güncelleme" },
  { value: "stock_update", label: "Stok Güncelleme" },
  { value: "price_update", label: "Fiyat Güncelleme" },
  { value: "order_pull", label: "Sipariş Çekme" },
  { value: "order_update", label: "Sipariş Güncelleme" },
  { value: "return_pull", label: "İade Çekme" },
  { value: "webhook_receive", label: "Webhook (Inbound)" },
];

const STATUS_STYLES = {
  success: { label: "Başarılı", cls: "bg-green-100 text-green-700", icon: CheckCircle2 },
  failed: { label: "Hata", cls: "bg-red-100 text-red-700", icon: XCircle },
  partial: { label: "Kısmi", cls: "bg-yellow-100 text-yellow-700", icon: AlertTriangle },
  queued: { label: "Kuyrukta", cls: "bg-gray-100 text-gray-600", icon: Clock },
};

export default function IntegrationLogs() {
  const [searchParams] = useSearchParams();
  const initialMarketplace = searchParams.get("marketplace") || "";
  const [marketplaces, setMarketplaces] = useState([]);
  const [summary, setSummary] = useState([]);
  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const [filters, setFilters] = useState({
    marketplace: initialMarketplace,
    action: "",
    status: "",
    ref_id: "",
    date_from: "",
    date_to: "",
  });

  const token = useMemo(() => localStorage.getItem("token"), []);
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  useEffect(() => {
    axios.get(`${API}/marketplace-hub/marketplaces`, auth)
      .then((r) => setMarketplaces(r.data?.marketplaces || []))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadSummary = async () => {
    try {
      const r = await axios.get(`${API}/marketplace-hub/logs/summary`, auth);
      setSummary(r.data?.items || []);
    } catch {/* sessiz geç */}
  };

  const loadLogs = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.append("page", page);
      params.append("limit", pageSize);
      Object.entries(filters).forEach(([k, v]) => { if (v) params.append(k, v); });
      const r = await axios.get(`${API}/marketplace-hub/logs?${params}`, auth);
      setLogs(r.data?.logs || []);
      setTotal(r.data?.total || 0);
    } catch (err) {
      toast.error("Loglar yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadLogs(); loadSummary(); /* eslint-disable-next-line */ }, [page, pageSize]);

  const applyFilters = () => { setPage(1); loadLogs(); loadSummary(); };

  const exportCsv = () => {
    const header = ["Tarih", "Pazaryeri", "İşlem", "Durum", "Yön", "Ref", "Mesaj"];
    const rows = logs.map((l) => [
      l.created_at, l.marketplace || "-", l.action, l.status, l.direction || "-",
      l.ref_id || "-", (l.message || "").replace(/[\n\r]/g, " "),
    ]);
    const csv = [header, ...rows].map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `entegrasyon-loglari-${Date.now()}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  const marketplaceMeta = useMemo(() => {
    const m = {};
    marketplaces.forEach((mp) => { m[mp.key] = mp; });
    return m;
  }, [marketplaces]);

  return (
    <div data-testid="integration-logs-page">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Entegrasyon Logları</h1>
          <p className="text-sm text-gray-500 mt-1">
            Tüm pazaryerleri ile yapılan API aktarımlarının kayıtları. Hata analizinde en kritik modül.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={exportCsv}
            className="flex items-center gap-1 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50"
            data-testid="logs-export-csv">
            <Download size={14} /> Excel'e Aktar
          </button>
          <button onClick={() => { loadLogs(); loadSummary(); }}
            className="flex items-center gap-1 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
            <RefreshCw size={14} /> Yenile
          </button>
        </div>
      </div>

      {/* Son İşlemler Özet */}
      {summary.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6" data-testid="logs-summary">
          {summary.slice(0, 4).map((s, idx) => {
            const meta = marketplaceMeta[s.marketplace] || {};
            return (
              <div key={idx} className="bg-white border rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-6 h-6 rounded-full flex items-center justify-center text-white text-[9px] font-black"
                       style={{ backgroundColor: meta.color || "#6b7280" }}>
                    {(meta.name || s.marketplace || "?").slice(0, 2).toUpperCase()}
                  </div>
                  <span className="text-xs font-semibold text-gray-700">{meta.name || s.marketplace || "—"}</span>
                </div>
                <div className="text-[11px] text-gray-500 uppercase">{s.action}</div>
                <div className="flex items-end gap-2 mt-1">
                  <span className="text-xl font-black">{s.total}</span>
                  <span className="text-xs text-green-600">✓{s.success}</span>
                  {s.failed > 0 && <span className="text-xs text-red-600">✗{s.failed}</span>}
                </div>
                <div className="text-[10px] text-gray-400 mt-1">
                  {s.last_at ? new Date(s.last_at).toLocaleString("tr-TR") : "-"}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Filtreleme */}
      <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-4 mb-4" data-testid="logs-filters">
        <h3 className="text-sm font-bold text-gray-800 mb-3">Filtreleme</h3>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <div>
            <label className="block text-xs text-gray-600 mb-1">Pazaryeri</label>
            <select value={filters.marketplace}
              onChange={(e) => setFilters({ ...filters, marketplace: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white"
              data-testid="logs-filter-marketplace">
              <option value="">Tümü</option>
              {marketplaces.map((m) => (<option key={m.key} value={m.key}>{m.name}</option>))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Aktarım Türü</label>
            <select value={filters.action}
              onChange={(e) => setFilters({ ...filters, action: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white"
              data-testid="logs-filter-action">
              <option value="">Tümü</option>
              {ACTION_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Durum</label>
            <select value={filters.status}
              onChange={(e) => setFilters({ ...filters, status: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white">
              <option value="">Tümü</option>
              {Object.entries(STATUS_STYLES).map(([k, v]) => (<option key={k} value={k}>{v.label}</option>))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Başlangıç</label>
            <input type="date" value={filters.date_from}
              onChange={(e) => setFilters({ ...filters, date_from: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white" />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Bitiş</label>
            <input type="date" value={filters.date_to}
              onChange={(e) => setFilters({ ...filters, date_to: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white" />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Ürün/Sipariş ID</label>
            <div className="flex gap-1">
              <input value={filters.ref_id}
                onChange={(e) => setFilters({ ...filters, ref_id: e.target.value })}
                placeholder="Ref #"
                className="flex-1 border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white" />
              <button onClick={applyFilters}
                className="bg-black text-white px-3 py-1.5 text-sm rounded-lg hover:bg-gray-800"
                data-testid="logs-apply-filters">
                Listele
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Tablo */}
      <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
        <table className="admin-table admin-table-compact">
          <thead>
            <tr>
              <th>Tarih</th>
              <th>Pazaryeri</th>
              <th>İşlem</th>
              <th>Durum</th>
              <th>Yön</th>
              <th>Ref</th>
              <th>Mesaj</th>
              <th>Süre</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="text-center py-8 text-sm text-gray-400">Yükleniyor...</td></tr>
            ) : logs.length === 0 ? (
              <tr><td colSpan={8} className="text-center py-8 text-sm text-gray-400">Kayıt bulunamadı</td></tr>
            ) : (
              logs.map((l, idx) => {
                const meta = marketplaceMeta[l.marketplace] || {};
                const st = STATUS_STYLES[l.status] || STATUS_STYLES.queued;
                const Icon = st.icon;
                return (
                  <tr key={idx} data-testid={`log-row-${idx}`}>
                    <td className="text-xs text-gray-600 whitespace-nowrap">
                      {l.created_at ? new Date(l.created_at).toLocaleString("tr-TR") : "-"}
                    </td>
                    <td>
                      {l.marketplace ? (
                        <span className="flex items-center gap-1.5">
                          <span className="w-4 h-4 rounded-full flex items-center justify-center text-white text-[8px] font-black"
                                style={{ backgroundColor: meta.color || "#6b7280" }}>
                            {(meta.name || l.marketplace).slice(0, 1).toUpperCase()}
                          </span>
                          <span className="text-sm font-medium">{meta.name || l.marketplace}</span>
                        </span>
                      ) : <span className="text-xs text-gray-400">—</span>}
                    </td>
                    <td className="text-xs font-mono text-gray-700">{l.action}</td>
                    <td>
                      <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${st.cls}`}>
                        <Icon size={10} /> {st.label}
                      </span>
                    </td>
                    <td className="text-[11px] text-gray-500">
                      {l.direction === "inbound" ? "← Gelen" : l.direction === "outbound" ? "Giden →" : "-"}
                    </td>
                    <td className="text-xs font-mono text-gray-600">{l.ref_id || "-"}</td>
                    <td className="text-xs text-gray-600 max-w-md truncate" title={l.message}>{l.message || "-"}</td>
                    <td className="text-xs text-gray-400">{l.duration_ms != null ? `${l.duration_ms}ms` : "-"}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <Pagination
        page={page}
        total={total}
        pageSize={pageSize}
        onChange={setPage}
        onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
        pageSizeOptions={[25, 50, 100, 200]}
        variant="full"
      />
    </div>
  );
}
