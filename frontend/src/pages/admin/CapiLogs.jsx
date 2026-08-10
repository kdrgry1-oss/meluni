/**
 * =============================================================================
 * CapiLogs.jsx — CAPI (Server-Side Conversions API) Gönderim Logları
 * =============================================================================
 *   • Son N gönderim listesi (provider, event, status, error)
 *   • Filtre: provider, event_name, sadece başarısızlar
 *   • Kuyruk: bekleyen + ölü event'ler, tekrar deneme / silme butonları
 *   • 30 gün öncesi logları temizle
 * =============================================================================
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Activity, RefreshCw, Trash2, CheckCircle2, XCircle, AlertCircle, Filter } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const EVENT_OPTIONS = [
  { value: "", label: "Tüm event'ler" },
  { value: "view_item", label: "View Item" },
  { value: "view_item_list", label: "View Item List" },
  { value: "add_to_cart", label: "Add to Cart" },
  { value: "remove_from_cart", label: "Remove from Cart" },
  { value: "begin_checkout", label: "Begin Checkout" },
  { value: "add_payment_info", label: "Add Payment Info" },
  { value: "purchase", label: "Purchase" },
  { value: "refund", label: "Refund" },
  { value: "lead", label: "Lead" },
  { value: "search", label: "Search" },
  { value: "test_connection", label: "Test Connection" },
];

const PROVIDER_OPTIONS = [
  { value: "", label: "Tüm sağlayıcılar" },
  { value: "meta", label: "Meta" }, { value: "tiktok", label: "TikTok" },
  { value: "google_ads", label: "Google Ads" }, { value: "ga4", label: "GA4" },
  { value: "pinterest", label: "Pinterest" }, { value: "snapchat", label: "Snapchat" },
];

export default function CapiLogs() {
  const [tab, setTab] = useState("logs");          // logs | queue | audit
  const [logs, setLogs] = useState([]);
  const [queue, setQueue] = useState([]);
  const [audit, setAudit] = useState(null);
  const [auditWindow, setAuditWindow] = useState(72);
  const [auditProvider, setAuditProvider] = useState("meta");
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({ provider: "", event_name: "", ok: "", date_from: "", date_to: "" });
  const [queueFilter, setQueueFilter] = useState("");  // "" | "true" (dead) | "false" (pending)
  const [expandedRow, setExpandedRow] = useState(null);

  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const loadLogs = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.append("limit", "100");
      if (filters.provider) params.append("provider", filters.provider);
      if (filters.event_name) params.append("event_name", filters.event_name);
      if (filters.ok !== "") params.append("ok", filters.ok);
      if (filters.date_from) params.append("date_from", filters.date_from);
      if (filters.date_to) params.append("date_to", filters.date_to);
      const res = await axios.get(`${API}/marketing-pixels/capi/logs?${params}`, auth);
      setLogs(res.data?.items || []);
      setTotal(res.data?.total || 0);
    } catch (e) {
      toast.error("Loglar yüklenemedi: " + (e?.response?.data?.detail || e.message));
    } finally { setLoading(false); }
  };

  const exportLogs = async () => {
    try {
      const params = new URLSearchParams();
      if (filters.provider) params.append("provider", filters.provider);
      if (filters.event_name) params.append("event_name", filters.event_name);
      if (filters.ok !== "") params.append("ok", filters.ok);
      if (filters.date_from) params.append("date_from", filters.date_from);
      if (filters.date_to) params.append("date_to", filters.date_to);
      const res = await axios.get(`${API}/marketing-pixels/capi/logs/export?${params}`, { ...auth, responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `capi_logs_${filters.date_from || "all"}_${filters.date_to || "all"}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success("CSV indirildi");
    } catch (e) {
      toast.error("Export başarısız: " + (e?.response?.data?.detail || e.message));
    }
  };

  const loadQueue = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.append("limit", "100");
      if (queueFilter !== "") params.append("dead", queueFilter);
      const res = await axios.get(`${API}/marketing-pixels/capi/queue?${params}`, auth);
      setQueue(res.data?.items || []);
    } catch (e) {
      toast.error("Kuyruk yüklenemedi: " + (e?.response?.data?.detail || e.message));
    } finally { setLoading(false); }
  };

  const loadAudit = async () => {
    setLoading(true);
    try {
      const res = await axios.get(
        `${API}/marketing-pixels/capi/audit?provider=${auditProvider}&sample=100&window_hours=${auditWindow}`, auth);
      setAudit(res.data || null);
    } catch (e) {
      toast.error("Denetim yüklenemedi: " + (e?.response?.data?.detail || e.message));
    } finally { setLoading(false); }
  };

  useEffect(() => {
    if (tab === "logs") loadLogs();
    else if (tab === "queue") loadQueue();
    else if (tab === "audit") loadAudit();
    // eslint-disable-next-line
  }, [tab, filters, queueFilter, auditWindow, auditProvider]);

  const retryOne = async (qid) => {
    try {
      await axios.post(`${API}/marketing-pixels/capi/queue/${qid}/retry`, {}, auth);
      toast.success("Yeniden deneme için kuyruğa alındı");
      await loadQueue();
    } catch (e) { toast.error(e.message); }
  };

  const deleteOne = async (qid) => {
    if (!await window.appConfirm("Bu kuyruk öğesini silmek istediğinize emin misiniz?")) return;
    try {
      await axios.delete(`${API}/marketing-pixels/capi/queue/${qid}`, auth);
      toast.success("Silindi");
      await loadQueue();
    } catch (e) { toast.error(e.message); }
  };

  const runAll = async () => {
    setLoading(true);
    try {
      const res = await axios.post(`${API}/marketing-pixels/capi/queue/run-now`, {}, auth);
      toast.success(`İşlem: ${res.data?.ok || 0} başarılı, ${res.data?.failed || 0} hatalı`);
      await loadQueue();
    } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  };

  const clearOldLogs = async () => {
    if (!await window.appConfirm("30 günden eski tüm logları silmek istediğinize emin misiniz?")) return;
    try {
      const res = await axios.delete(`${API}/marketing-pixels/capi/logs/clear-old?days=30`, auth);
      toast.success(`${res.data?.deleted} log silindi`);
      await loadLogs();
    } catch (e) { toast.error(e.message); }
  };

  const fmtTime = (iso) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString("tr-TR"); } catch { return iso; }
  };

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-5" data-testid="capi-logs-page">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Activity size={20} /> CAPI Loglar & Kuyruk
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Server-side reklam platformlarına (Meta, Google, TikTok, Pinterest, Snapchat) gönderilen tüm event'lerin durumu.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={tab === "logs" ? loadLogs : loadQueue} disabled={loading}
            className="inline-flex items-center gap-1 bg-white border px-3 py-1.5 rounded text-xs hover:bg-gray-50"
            data-testid="capi-refresh">
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Yenile
          </button>
          {tab === "logs" && (
            <button onClick={clearOldLogs}
              className="inline-flex items-center gap-1 bg-red-50 text-red-700 border border-red-200 px-3 py-1.5 rounded text-xs hover:bg-red-100"
              data-testid="capi-clear-old">
              <Trash2 size={12} /> 30+ gün
            </button>
          )}
          {tab === "queue" && (
            <button onClick={runAll} disabled={loading}
              className="inline-flex items-center gap-1 bg-black text-white px-3 py-1.5 rounded text-xs disabled:opacity-60"
              data-testid="capi-run-all">
              <RefreshCw size={12} /> Tümünü Şimdi Dene
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b">
        <button onClick={() => setTab("logs")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${tab === "logs" ? "border-black text-black" : "border-transparent text-gray-500 hover:text-black"}`}
          data-testid="tab-logs">
          📜 Loglar ({total})
        </button>
        <button onClick={() => setTab("queue")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${tab === "queue" ? "border-black text-black" : "border-transparent text-gray-500 hover:text-black"}`}
          data-testid="tab-queue">
          ⏳ Kuyruk ({queue.length})
        </button>
        <button onClick={() => setTab("audit")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${tab === "audit" ? "border-black text-black" : "border-transparent text-gray-500 hover:text-black"}`}
          data-testid="tab-audit">
          🔎 Denetim (Meta Purchase)
        </button>
      </div>

      {/* Filtreler */}
      {tab === "logs" ? (
        <div className="flex items-center gap-2 flex-wrap text-sm">
          <Filter size={14} className="text-gray-500" />
          <select value={filters.provider} onChange={(e) => setFilters({ ...filters, provider: e.target.value })}
            className="border px-2 py-1 rounded text-xs" data-testid="filter-provider">
            {PROVIDER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <select value={filters.event_name} onChange={(e) => setFilters({ ...filters, event_name: e.target.value })}
            className="border px-2 py-1 rounded text-xs" data-testid="filter-event">
            {EVENT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <select value={filters.ok} onChange={(e) => setFilters({ ...filters, ok: e.target.value })}
            className="border px-2 py-1 rounded text-xs" data-testid="filter-ok">
            <option value="">Tüm durumlar</option>
            <option value="true">✓ Başarılı</option>
            <option value="false">✗ Hatalı</option>
          </select>
          <span className="text-gray-400 text-xs ml-1">Tarih:</span>
          <input type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })}
            className="border px-2 py-1 rounded text-xs" data-testid="filter-date-from" title="Başlangıç günü" />
          <span className="text-gray-400 text-xs">–</span>
          <input type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })}
            className="border px-2 py-1 rounded text-xs" data-testid="filter-date-to" title="Bitiş günü (dahil)" />
          {(filters.date_from || filters.date_to) && (
            <button onClick={() => setFilters({ ...filters, date_from: "", date_to: "" })}
              className="text-xs text-gray-500 underline hover:no-underline" data-testid="filter-date-clear">temizle</button>
          )}
          <button onClick={exportLogs}
            className="inline-flex items-center gap-1 bg-emerald-50 text-emerald-700 border border-emerald-200 px-3 py-1 rounded text-xs hover:bg-emerald-100 ml-auto"
            data-testid="capi-export">
            ⬇ Export (CSV)
          </button>
        </div>
      ) : tab === "queue" ? (
        <div className="flex items-center gap-2 text-sm">
          <Filter size={14} className="text-gray-500" />
          <select value={queueFilter} onChange={(e) => setQueueFilter(e.target.value)}
            className="border px-2 py-1 rounded text-xs">
            <option value="">Tümü</option>
            <option value="false">⏳ Aktif bekleyen</option>
            <option value="true">💀 Ölü (max retry)</option>
          </select>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-sm flex-wrap">
          <Filter size={14} className="text-gray-500" />
          <select value={auditProvider} onChange={(e) => setAuditProvider(e.target.value)}
            className="border px-2 py-1 rounded text-xs font-semibold" data-testid="audit-provider">
            <option value="meta">Meta</option>
            <option value="tiktok">TikTok</option>
            <option value="google_ads">Google Ads</option>
            <option value="pinterest">Pinterest</option>
            <option value="snapchat">Snapchat</option>
          </select>
          <span className="text-xs text-gray-500">Sağlık penceresi:</span>
          <select value={auditWindow} onChange={(e) => setAuditWindow(Number(e.target.value))}
            className="border px-2 py-1 rounded text-xs" data-testid="audit-window">
            <option value={24}>Son 24 saat</option>
            <option value={72}>Son 72 saat</option>
            <option value={168}>Son 7 gün</option>
            <option value={720}>Son 30 gün</option>
          </select>
          <span className="text-[11px] text-gray-400">(coverage örneği: son 100 Purchase)</span>
        </div>
      )}

      {/* Denetim (Audit) paneli */}
      {tab === "audit" && (
        <div className="space-y-4" data-testid="audit-panel">
          {!audit ? (
            <div className="bg-white border rounded-lg p-8 text-center text-gray-400 text-sm">
              {loading ? "Denetim hesaplanıyor…" : "Veri yok."}
            </div>
          ) : (
            <>
              {/* Coverage */}
              <div className="bg-white border rounded-lg p-4">
                <h3 className="font-semibold text-sm mb-1">
                  <span className="uppercase bg-gray-900 text-white text-[10px] px-1.5 py-0.5 rounded mr-2">{audit.provider}</span>
                  Son {audit.sample_size} server Purchase — eşleşme sinyali coverage (PII'siz)
                </h3>
                <p className="text-[11px] text-gray-400 mb-3">Üretim: {fmtTime(audit.generated_at)} · yalnız var/yok oranı, ham değer yok.</p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {[
                    ["Toplam Purchase", { n: audit.purchase_coverage?.total_server_purchase, pct: 100 }],
                    ["has_email", audit.purchase_coverage?.has_email],
                    ["has_phone", audit.purchase_coverage?.has_phone],
                    ["has_external_id", audit.purchase_coverage?.has_external_id],
                    ...(auditProvider === "tiktok"
                      ? [["has_ttclid", audit.purchase_coverage?.has_ttclid],
                         ["has_ttp", audit.purchase_coverage?.has_ttp]]
                      : [["has_fbp", audit.purchase_coverage?.has_fbp],
                         ["has_fbc", audit.purchase_coverage?.has_fbc]]),
                    ["has_ip", audit.purchase_coverage?.has_ip],
                    ["has_user_agent", audit.purchase_coverage?.has_user_agent],
                    ["API başarılı", audit.purchase_coverage?.meta_api_ok],
                    ["IPv4", audit.purchase_coverage?.ip_version_4],
                    ["IPv6", audit.purchase_coverage?.ip_version_6],
                  ].map(([label, v]) => (
                    <div key={label} className="border rounded-lg p-2.5">
                      <div className="text-[11px] text-gray-500">{label}</div>
                      <div className="text-lg font-bold">{v?.n ?? 0}
                        {typeof v?.pct === "number" && <span className="text-xs font-normal text-gray-400 ml-1">%{v.pct}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* _fbp kaynağı + source-path */}
              <div className="grid md:grid-cols-2 gap-4">
                {auditProvider === "meta" && (
                <div className="bg-white border rounded-lg p-4">
                  <h3 className="font-semibold text-sm mb-2">_fbp kaynağı <span className="text-[10px] text-gray-400 font-normal">(Meta'ya özel)</span></h3>
                  <table className="w-full text-xs">
                    <tbody>
                      {Object.entries(audit.fbp_source || {}).map(([k, n]) => (
                        <tr key={k} className="border-t">
                          <td className="py-1.5">{{
                            order_snapshot: "Order snapshot / click_ids",
                            attribution_fallback: "Attribution session fallback",
                            none: "None",
                            unknown_no_order: "Order bulunamadı",
                          }[k] || k}</td>
                          <td className="py-1.5 text-right font-bold">{n}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                )}
                <div className="bg-white border rounded-lg p-4">
                  <h3 className="font-semibold text-sm mb-2">Purchase source-path dağılımı</h3>
                  <table className="w-full text-xs">
                    <thead className="text-gray-500"><tr><th className="text-left py-1">Akış</th><th className="text-right py-1">Adet</th><th className="text-right py-1">email</th><th className="text-right py-1">{auditProvider === "tiktok" ? "ttclid" : "fbp"}</th><th className="text-right py-1">ext_id</th></tr></thead>
                    <tbody>
                      {Object.entries(audit.purchase_source_path || {}).map(([k, v]) => (
                        <tr key={k} className="border-t">
                          <td className="py-1.5 font-mono">{k}</td>
                          <td className="py-1.5 text-right font-bold">{v.n}</td>
                          <td className="py-1.5 text-right text-gray-500">{v.email}</td>
                          <td className="py-1.5 text-right text-gray-500">{auditProvider === "tiktok" ? v.ttclid : v.fbp}</td>
                          <td className="py-1.5 text-right text-gray-500">{v.external_id}</td>
                        </tr>
                      ))}
                      {Object.keys(audit.purchase_source_path || {}).length === 0 && (
                        <tr><td colSpan={5} className="py-3 text-center text-gray-400">Kayıt yok</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Sağlık */}
              <div className="bg-white border rounded-lg p-4">
                <h3 className="font-semibold text-sm mb-3">Production sağlık — son {audit.health?.window_hours} saat</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
                  {[
                    ["Toplam event", audit.health?.total_events],
                    ["Başarılı", audit.health?.ok],
                    ["Hata", audit.health?.error],
                    ["Hata oranı", `%${audit.health?.error_rate_pct}`],
                    ["Retry başarı", audit.health?.retry_success],
                    ["Kuyruk bekleyen", audit.health?.queue_pending],
                    ["Dead-letter 💀", audit.health?.dead_letter],
                    ["ViewContent hacmi", audit.health?.viewcontent_server_volume],
                  ].map(([label, val]) => (
                    <div key={label} className="border rounded-lg p-2.5">
                      <div className="text-[11px] text-gray-500">{label}</div>
                      <div className="text-lg font-bold">{val ?? 0}</div>
                    </div>
                  ))}
                </div>
                <div className="grid md:grid-cols-2 gap-4">
                  <div>
                    <div className="text-[11px] text-gray-500 mb-1 uppercase font-bold">Event bazında</div>
                    <table className="w-full text-xs">
                      <tbody>
                        {Object.entries(audit.health?.by_event || {}).map(([k, v]) => (
                          <tr key={k} className="border-t">
                            <td className="py-1 font-mono">{k}</td>
                            <td className="py-1 text-right">{v.n} <span className="text-gray-400">(%{v.ok_pct} ok)</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div>
                    <div className="text-[11px] text-gray-500 mb-1 uppercase font-bold">Hata durum kodları</div>
                    <table className="w-full text-xs">
                      <tbody>
                        {Object.entries(audit.health?.error_status_distribution || {}).map(([k, v]) => (
                          <tr key={k} className="border-t"><td className="py-1 font-mono">HTTP {k}</td><td className="py-1 text-right font-bold">{v}</td></tr>
                        ))}
                        {Object.keys(audit.health?.error_status_distribution || {}).length === 0 && (
                          <tr><td className="py-2 text-gray-400">Hata yok ✓</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
                <p className="text-[11px] text-amber-700 mt-3">⚠️ Per-event gönderim gecikmesi (latency) loglanmıyor — event akışını değiştirmemek için ölçüm eklenmedi.</p>
              </div>
            </>
          )}
        </div>
      )}

      {/* İçerik tablosu */}
      {tab !== "audit" && (
      <div className="bg-white border rounded-lg overflow-hidden">
        {tab === "logs" ? (
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-600 uppercase">
              <tr>
                <th className="text-left px-3 py-2">Zaman</th>
                <th className="text-left px-3 py-2">Provider</th>
                <th className="text-left px-3 py-2">Event</th>
                <th className="text-left px-3 py-2">ID</th>
                <th className="text-center px-3 py-2">Durum</th>
                <th className="text-left px-3 py-2">Mesaj</th>
              </tr>
            </thead>
            <tbody>
              {logs.length === 0 && (
                <tr><td colSpan={6} className="text-center py-8 text-gray-400" data-testid="empty-logs">
                  Henüz log yok. Bir storefront sayfasını ziyaret ederek event tetikleyin.
                </td></tr>
              )}
              {logs.map((l) => (
                <tr key={l.id} className={`border-t hover:bg-gray-50 ${l.is_test ? "bg-amber-50" : ""}`}>
                  <td className="px-3 py-2 text-gray-600 whitespace-nowrap">{fmtTime(l.created_at)}</td>
                  <td className="px-3 py-2"><span className="text-[10px] uppercase bg-gray-100 px-1.5 py-0.5 rounded font-bold">{l.provider}</span></td>
                  <td className="px-3 py-2 font-mono">{l.event_name}</td>
                  <td className="px-3 py-2 font-mono text-gray-400 text-[10px]">{(l.event_id || "").slice(0, 12)}…</td>
                  <td className="px-3 py-2 text-center">
                    {l.ok ? <CheckCircle2 size={16} className="text-green-600 inline" /> : <XCircle size={16} className="text-red-600 inline" />}
                    {l.from_retry && <span className="ml-1 text-[9px] bg-blue-100 text-blue-700 px-1 rounded">RETRY</span>}
                    {l.is_test && <span className="ml-1 text-[9px] bg-amber-200 text-amber-900 px-1 rounded">TEST</span>}
                  </td>
                  <td className="px-3 py-2 text-gray-500 max-w-md truncate cursor-pointer"
                      onClick={() => setExpandedRow(expandedRow === l.id ? null : l.id)}>
                    {expandedRow === l.id ? (
                      <pre className="text-[10px] whitespace-pre-wrap break-all bg-gray-100 p-2 rounded">
                        {JSON.stringify(l.error || l.response, null, 2).slice(0, 1500)}
                      </pre>
                    ) : (
                      <span>{JSON.stringify(l.error || l.response || "").slice(0, 100)}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-600 uppercase">
              <tr>
                <th className="text-left px-3 py-2">Sıradaki Deneme</th>
                <th className="text-left px-3 py-2">Provider</th>
                <th className="text-left px-3 py-2">Event</th>
                <th className="text-center px-3 py-2">Deneme</th>
                <th className="text-left px-3 py-2">Son Hata</th>
                <th className="text-right px-3 py-2">İşlem</th>
              </tr>
            </thead>
            <tbody>
              {queue.length === 0 && (
                <tr><td colSpan={6} className="text-center py-8 text-gray-400" data-testid="empty-queue">
                  Kuyruk boş — tüm CAPI event'leri başarıyla gönderildi.
                </td></tr>
              )}
              {queue.map((q) => (
                <tr key={q.id} className={`border-t hover:bg-gray-50 ${q.dead ? "bg-red-50" : ""}`}>
                  <td className="px-3 py-2 text-gray-600 whitespace-nowrap">{fmtTime(q.next_try_at)}</td>
                  <td className="px-3 py-2"><span className="text-[10px] uppercase bg-gray-100 px-1.5 py-0.5 rounded font-bold">{q.provider}</span></td>
                  <td className="px-3 py-2 font-mono">{q.event_name}</td>
                  <td className="px-3 py-2 text-center">
                    <span className={q.dead ? "bg-red-100 text-red-700 px-1.5 rounded text-[10px] font-bold" : ""}>
                      {q.attempts}{q.dead && " 💀"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-gray-500 max-w-md truncate">
                    {JSON.stringify(q.last_error || "").slice(0, 100)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button onClick={() => retryOne(q.id)}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-blue-50 mr-1"
                      data-testid={`retry-${q.id}`}>
                      <RefreshCw size={11} /> Dene
                    </button>
                    <button onClick={() => deleteOne(q.id)}
                      className="text-red-600 hover:bg-red-50 p-1.5 rounded"
                      data-testid={`delete-${q.id}`}>
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      )}

      <div className="text-xs text-gray-500 flex items-center gap-1">
        <AlertCircle size={12} />
        Arkaplanda her 30 dk'da bir kuyruk otomatik denenir; max 5 deneme (1/5/15/60/240 dk üstel backoff).
      </div>
    </div>
  );
}
