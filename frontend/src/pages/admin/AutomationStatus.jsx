/**
 * =============================================================================
 * AutomationStatus.jsx — Otomasyon Durumu Paneli (Admin)
 * =============================================================================
 * Admin'in arka planda hangi cron işlerinin çalıştığını / ne zaman çalışacağını
 * / son loglarını / marketplace senkron ayarlarını tek ekranda görmesi için.
 *
 * Backend: GET /api/admin/automation/status?log_limit=N
 * =============================================================================
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Activity, Clock, CheckCircle2, AlertCircle, Info, RefreshCw,
  Cpu, Globe, Database, Zap
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const STATUS_COLORS = {
  success: "text-green-700 bg-green-50 border-green-200",
  error:   "text-red-700 bg-red-50 border-red-200",
  info:    "text-blue-700 bg-blue-50 border-blue-200",
};

const formatTime = (iso) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return iso; }
};

const relativeFromNow = (iso) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso).getTime();
    const now = Date.now();
    const diff = d - now;
    const abs = Math.abs(diff);
    const sign = diff > 0 ? "sonra" : "önce";
    if (abs < 60000) return `${Math.round(abs / 1000)} sn ${sign}`;
    if (abs < 3600000) return `${Math.round(abs / 60000)} dk ${sign}`;
    if (abs < 86400000) return `${Math.round(abs / 3600000)} sa ${sign}`;
    return `${Math.round(abs / 86400000)} gün ${sign}`;
  } catch { return iso; }
};

export default function AutomationStatus() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const t = localStorage.getItem("token");
      const r = await axios.get(`${API}/admin/automation/status?log_limit=100`, {
        headers: { Authorization: `Bearer ${t}` },
      });
      setData(r.data);
    } catch {
      toast.error("Otomasyon durumu alınamadı");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  // Auto-refresh every 30s when active tab
  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [autoRefresh]);

  return (
    <div data-testid="automation-status-page">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Activity size={20} /> Otomasyon Durumu
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Arka planda çalışan tüm cron işlerinin, marketplace senkronlarının ve son entegrasyon loglarının canlı özeti.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            30sn'de bir yenile
          </label>
          <button onClick={load} disabled={loading} data-testid="refresh-automation-btn"
            className="flex items-center gap-1.5 px-3 py-2 bg-black text-white rounded-lg text-sm hover:bg-gray-800 disabled:opacity-50">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Yenile
          </button>
        </div>
      </div>

      {!data ? (
        <div className="text-center py-20 text-gray-400">Yükleniyor...</div>
      ) : (
        <>
          {/* Integrations row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5" data-testid="integration-status-cards">
            <IntegrationCard label="Doğan e-Dönüşüm" ok={data.integrations.dogan_configured} hint="e-Fatura / e-Arşiv" />
            <IntegrationCard label="Resend" ok={data.integrations.resend_configured} hint="E-posta gönderimi (kampanyalar)" />
            <IntegrationCard label="Trendyol" ok={(data.marketplaces.find((m) => m.key === "trendyol") || {}).orders_enabled}
              hint={`${(data.marketplaces.find((m) => m.key === "trendyol") || {}).orders_interval_min || "?"} dk'da bir sipariş çek`} />
          </div>

          <div className="grid lg:grid-cols-3 gap-5">
            {/* Cron jobs */}
            <section className="lg:col-span-1 bg-white border rounded-xl p-4">
              <h2 className="text-sm font-bold uppercase tracking-wider mb-3 flex items-center gap-2">
                <Cpu size={16} /> Aktif Cron İşleri
              </h2>
              <div className="space-y-2.5">
                {data.jobs.length === 0 && <p className="text-sm text-gray-400">Hiç job yok</p>}
                {data.jobs.map((j) => (
                  <div key={j.id} className="border border-gray-100 rounded-lg p-3 hover:shadow-sm transition" data-testid={`cron-${j.id}`}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-semibold font-mono text-gray-800 truncate">{j.id}</span>
                      <span className="text-[10px] uppercase tracking-wider bg-gray-100 px-2 py-0.5 rounded">
                        {j.interval_label || "-"}
                      </span>
                    </div>
                    <p className="text-xs text-gray-600 flex items-center gap-1.5">
                      <Clock size={11} /> Sıradaki çalışma:
                      <span className="font-medium">{formatTime(j.next_run)}</span>
                      <span className="text-gray-400">({relativeFromNow(j.next_run)})</span>
                    </p>
                  </div>
                ))}
              </div>
            </section>

            {/* Marketplaces */}
            <section className="lg:col-span-1 bg-white border rounded-xl p-4">
              <h2 className="text-sm font-bold uppercase tracking-wider mb-3 flex items-center gap-2">
                <Globe size={16} /> Pazaryeri Senkron Ayarları
              </h2>
              <div className="space-y-2.5">
                {data.marketplaces.length === 0 && <p className="text-sm text-gray-400">Tanımlı hesap yok</p>}
                {data.marketplaces.map((m) => (
                  <div key={m.key} className="border border-gray-100 rounded-lg p-3" data-testid={`mp-${m.key}`}>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-sm font-bold uppercase">{m.name || m.key}</span>
                      <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded ${m.enabled ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                        {m.enabled ? "Aktif" : "Pasif"}
                      </span>
                    </div>
                    <div className="text-xs text-gray-600 space-y-0.5">
                      <p>📦 Ürün: {m.products_enabled ? `${m.products_interval_min || "?"} dk'da bir` : "kapalı"}</p>
                      <p>🛒 Sipariş: {m.orders_enabled ? `${m.orders_interval_min || "?"} dk'da bir` : "kapalı"}</p>
                      {m.last_orders_sync && (
                        <p className="text-[11px] text-gray-500 pt-1">Son sipariş senkron: {formatTime(m.last_orders_sync)} ({relativeFromNow(m.last_orders_sync)})</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* Log summary */}
            <section className="lg:col-span-1 bg-white border rounded-xl p-4">
              <h2 className="text-sm font-bold uppercase tracking-wider mb-3 flex items-center gap-2">
                <Database size={16} /> Log Özeti (son 100)
              </h2>
              <div className="space-y-2">
                {Object.keys(data.log_summary).length === 0 && <p className="text-sm text-gray-400">Log yok</p>}
                {Object.entries(data.log_summary).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between border-b last:border-0 pb-2 last:pb-0">
                    <span className="text-sm font-semibold uppercase">{k}</span>
                    <div className="flex gap-2 text-xs">
                      <span className="text-green-700 bg-green-50 px-2 py-0.5 rounded">✓ {v.success || 0}</span>
                      {v.error > 0 && <span className="text-red-700 bg-red-50 px-2 py-0.5 rounded">✗ {v.error}</span>}
                      <span className="text-blue-700 bg-blue-50 px-2 py-0.5 rounded">i {v.info || 0}</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>

          {/* Recent logs table */}
          <section className="bg-white border rounded-xl p-4 mt-5">
            <h2 className="text-sm font-bold uppercase tracking-wider mb-3 flex items-center gap-2">
              <Zap size={16} /> Son Entegrasyon Logları
            </h2>
            <div className="overflow-x-auto">
              <table className="admin-table admin-table-compact w-full">
                <thead>
                  <tr>
                    <th className="text-left">Zaman</th>
                    <th className="text-left">Pazaryeri / Sistem</th>
                    <th className="text-left">İşlem</th>
                    <th className="text-left">Durum</th>
                    <th className="text-left">Mesaj</th>
                  </tr>
                </thead>
                <tbody>
                  {data.logs.length === 0 ? (
                    <tr><td colSpan={5} className="text-center py-8 text-gray-400">Log yok</td></tr>
                  ) : data.logs.slice(0, 50).map((l, i) => {
                    const cls = STATUS_COLORS[l.status] || STATUS_COLORS.info;
                    const Icon = l.status === "success" ? CheckCircle2 : l.status === "error" ? AlertCircle : Info;
                    return (
                      <tr key={i} data-testid={`log-row-${i}`}>
                        <td className="text-[11px] text-gray-500 whitespace-nowrap">{formatTime(l.created_at)}</td>
                        <td className="text-xs font-semibold uppercase">{l.marketplace || "—"}</td>
                        <td className="text-xs">{l.action || "—"}</td>
                        <td>
                          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold border ${cls}`}>
                            <Icon size={10} /> {(l.status || "info").toUpperCase()}
                          </span>
                        </td>
                        <td className="text-xs text-gray-700 max-w-md truncate" title={l.message}>{l.message || "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <p className="text-[11px] text-gray-400 mt-4 text-right">
            Son güncelleme: {formatTime(data.now)} · {autoRefresh ? "Otomatik yenileme aktif (30sn)" : "Manuel yenileme"}
          </p>
        </>
      )}
    </div>
  );
}

function IntegrationCard({ label, ok, hint }) {
  return (
    <div className={`border rounded-xl p-3 ${ok ? "bg-green-50 border-green-200" : "bg-gray-50 border-gray-200"}`}>
      <div className="flex items-center gap-2 mb-1">
        <span className={`w-2 h-2 rounded-full ${ok ? "bg-green-500 animate-pulse" : "bg-gray-300"}`} />
        <span className="text-xs uppercase tracking-wider font-bold">{label}</span>
      </div>
      <p className={`text-xs ${ok ? "text-green-700" : "text-gray-500"}`}>{ok ? "Yapılandırılmış" : "Eksik"}</p>
      {hint && <p className="text-[10px] text-gray-400 mt-1">{hint}</p>}
    </div>
  );
}

