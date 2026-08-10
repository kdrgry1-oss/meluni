/**
 * SystemHealth.jsx — Sistem Sağlığı & İzleme Paneli
 * Backend: /api/admin/system/{health, errors, alerts, circuits, cache}
 */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Activity, AlertTriangle, BellRing, CheckCircle2, Database, RefreshCw,
  Server, Wifi, Zap, MailWarning, Clock,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const fmt = (iso) => { try { return new Date(iso).toLocaleString("tr-TR"); } catch { return iso; } };

const StatusPill = ({ status }) => {
  const map = {
    healthy:  { bg: "bg-emerald-100 text-emerald-800 border-emerald-200", icon: CheckCircle2, text: "SAĞLIKLI" },
    degraded: { bg: "bg-amber-100 text-amber-800 border-amber-200",       icon: AlertTriangle, text: "BOZULMA" },
    down:     { bg: "bg-red-100 text-red-800 border-red-200",             icon: AlertTriangle, text: "KESİNTİ" },
  };
  const m = map[status] || map.healthy;
  const Icon = m.icon;
  return (
    <span data-testid="system-status-pill" className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-semibold ${m.bg}`}>
      <Icon className="w-3.5 h-3.5" /> {m.text}
    </span>
  );
};

const Stat = ({ icon: Icon, label, value, hint, tone = "default", testid }) => {
  const tones = {
    default: "bg-white border-gray-200",
    danger:  "bg-red-50 border-red-200 text-red-900",
    warn:    "bg-amber-50 border-amber-200 text-amber-900",
    ok:      "bg-emerald-50 border-emerald-200 text-emerald-900",
    info:    "bg-blue-50 border-blue-200 text-blue-900",
  };
  return (
    <div data-testid={testid} className={`border rounded-lg p-4 ${tones[tone]}`}>
      <div className="flex items-center justify-between text-xs opacity-70">
        <span>{label}</span>
        {Icon ? <Icon className="w-4 h-4" /> : null}
      </div>
      <div className="text-3xl font-light tabular-nums mt-2">{value ?? "—"}</div>
      {hint ? <div className="text-xs opacity-70 mt-1">{hint}</div> : null}
    </div>
  );
};

export default function SystemHealth() {
  const [health, setHealth] = useState(null);
  const [errors, setErrors] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [unread, setUnread] = useState(0);
  const [circuits, setCircuits] = useState({});
  const [cache, setCache] = useState(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [h, er, al, ci, ca] = await Promise.all([
        axios.get(`${API}/admin/system/health`, auth()),
        axios.get(`${API}/admin/system/errors?limit=50`, auth()),
        axios.get(`${API}/admin/system/alerts?limit=50`, auth()),
        axios.get(`${API}/admin/system/circuits`, auth()),
        axios.get(`${API}/admin/system/cache`, auth()),
      ]);
      setHealth(h.data);
      setErrors(er.data.items || []);
      setAlerts(al.data.items || []);
      setUnread(al.data.unread || 0);
      setCircuits(ci.data.breakers || {});
      setCache(ca.data);
    } catch (e) {
      toast.error("Sağlık verisi alınamadı: " + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [autoRefresh, load]);

  const fireTest = async () => {
    try {
      const r = await axios.post(`${API}/admin/system/alerts/test`, {}, auth());
      const d = r.data.delivered || {};
      const channels = [d.smtp && "SMTP", d.resend && "Resend", "in-app"].filter(Boolean).join(", ");
      toast.success(`Test alarmı tetiklendi (${channels})`);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Test başarısız");
    }
  };

  const markRead = async (id) => {
    try {
      await axios.post(`${API}/admin/system/alerts/${id}/read`, {}, auth());
      load();
    } catch (e) {
      toast.error("İşaretleme başarısız");
    }
  };

  return (
    <div data-testid="system-health-page" className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-light text-gray-900">Sistem Sağlığı</h1>
          <p className="text-sm text-gray-500 mt-1">Hata izleme, alarm bildirimleri, ölçeklenebilirlik metrikleri.</p>
        </div>
        <div className="flex items-center gap-2">
          {health ? <StatusPill status={health.status} /> : null}
          <label className="text-xs text-gray-500 flex items-center gap-1">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            Otomatik (15s)
          </label>
          <button data-testid="health-refresh-btn" onClick={load} disabled={loading} className="px-3 py-1.5 border rounded-md text-sm flex items-center gap-1 hover:bg-gray-50">
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Yenile
          </button>
          <button data-testid="health-test-alert-btn" onClick={fireTest} className="px-3 py-1.5 bg-amber-600 text-white rounded-md text-sm flex items-center gap-1 hover:bg-amber-700">
            <BellRing className="w-4 h-4" /> Test Alarmı Gönder
          </button>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat testid="stat-mongo-latency" icon={Database} label="MongoDB Gecikme" value={health ? `${health.mongo_latency_ms}ms` : "—"} tone={health?.mongo_latency_ms < 0 ? "danger" : health?.mongo_latency_ms > 200 ? "warn" : "ok"} />
        <Stat testid="stat-err-5m" icon={AlertTriangle} label="Kritik Hata (5dk)" value={health?.errors?.last_5m ?? 0} tone={health?.errors?.last_5m > 0 ? "danger" : "ok"} />
        <Stat testid="stat-err-1h" icon={Zap} label="Kritik Hata (1s)" value={health?.errors?.last_1h ?? 0} tone={health?.errors?.last_1h > 5 ? "warn" : "default"} />
        <Stat testid="stat-unread-alerts" icon={BellRing} label="Okunmamış Alarm" value={unread} tone={unread > 0 ? "warn" : "default"} />
        <Stat testid="stat-cache" icon={Server} label={`Cache (${cache?.backend || "—"})`} value={cache?.hits ?? "—"} hint={cache?.url_present ? "Redis aktif" : "Redis yok — in-memory"} tone={cache?.backend === "redis" ? "ok" : "info"} />
      </div>

      {/* Alerts */}
      <div className="bg-white border rounded-lg">
        <div className="px-4 py-3 border-b flex items-center justify-between">
          <h3 className="font-medium flex items-center gap-2"><BellRing className="w-4 h-4" /> Son Alarmlar</h3>
          <span className="text-xs text-gray-500">{alerts.length} kayıt</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs text-gray-600">
              <tr><th className="text-left px-3 py-2">Zaman</th><th className="text-left px-3 py-2">Seviye</th><th className="text-left px-3 py-2">Başlık</th><th className="text-left px-3 py-2">Kanal</th><th className="text-left px-3 py-2">Durum</th><th></th></tr>
            </thead>
            <tbody data-testid="alerts-table">
              {alerts.length === 0 ? (
                <tr><td colSpan={6} className="text-center text-gray-500 py-6">Henüz alarm yok</td></tr>
              ) : alerts.map((a) => (
                <tr key={a.id} className={`border-t ${a.read ? "opacity-60" : ""}`}>
                  <td className="px-3 py-2 text-xs whitespace-nowrap"><Clock className="w-3 h-3 inline mr-1" />{fmt(a.created_at)}</td>
                  <td className="px-3 py-2"><span className={`text-xs px-2 py-0.5 rounded-full ${a.level === "critical" ? "bg-red-100 text-red-800" : a.level === "warning" ? "bg-amber-100 text-amber-800" : "bg-blue-100 text-blue-800"}`}>{a.level}</span></td>
                  <td className="px-3 py-2"><div className="font-medium">{a.title}</div><div className="text-xs text-gray-500 line-clamp-1">{a.body}</div></td>
                  <td className="px-3 py-2 text-xs">{a.delivered?.smtp ? <span className="text-emerald-700">✓ SMTP</span> : <MailWarning className="w-3 h-3 inline text-gray-400" />} {a.delivered?.resend ? "✓ Resend" : ""} {a.delivered?.in_app ? "✓ In-app" : ""}</td>
                  <td className="px-3 py-2 text-xs">{a.read ? "okundu" : <span className="text-amber-700">yeni</span>}</td>
                  <td className="px-3 py-2">{!a.read ? <button data-testid={`alert-read-${a.id}`} onClick={() => markRead(a.id)} className="text-xs text-blue-700 hover:underline">Okundu işaretle</button> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Errors */}
      <div className="bg-white border rounded-lg">
        <div className="px-4 py-3 border-b flex items-center justify-between">
          <h3 className="font-medium flex items-center gap-2"><Activity className="w-4 h-4" /> Son Hatalar</h3>
          <span className="text-xs text-gray-500">{errors.length} kayıt</span>
        </div>
        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 sticky top-0 text-xs text-gray-600">
              <tr><th className="text-left px-3 py-2">Zaman</th><th className="text-left px-3 py-2">Seviye</th><th className="text-left px-3 py-2">Tür</th><th className="text-left px-3 py-2">Yol</th><th className="text-left px-3 py-2">Mesaj</th></tr>
            </thead>
            <tbody data-testid="errors-table">
              {errors.length === 0 ? (
                <tr><td colSpan={5} className="text-center text-gray-500 py-6">Hata kaydı yok 🎉</td></tr>
              ) : errors.map((er) => (
                <tr key={er.id} className="border-t">
                  <td className="px-3 py-2 text-xs whitespace-nowrap">{fmt(er.created_at)}</td>
                  <td className="px-3 py-2"><span className={`text-xs px-2 py-0.5 rounded-full ${er.level === "critical" ? "bg-red-100 text-red-800" : er.level === "warning" ? "bg-amber-100 text-amber-800" : "bg-blue-100 text-blue-800"}`}>{er.level}</span></td>
                  <td className="px-3 py-2 text-xs font-mono">{er.kind}</td>
                  <td className="px-3 py-2 text-xs font-mono text-gray-600 truncate max-w-xs">{er.path}</td>
                  <td className="px-3 py-2 text-xs">{er.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Circuit breakers */}
      <div className="bg-white border rounded-lg p-4">
        <h3 className="font-medium flex items-center gap-2 mb-3"><Wifi className="w-4 h-4" /> Devre Kesiciler</h3>
        {Object.keys(circuits).length === 0 ? (
          <p className="text-sm text-gray-500">Henüz aktif devre kesici yok (entegrasyonlar hatasız çalışıyor).</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="circuits-grid">
            {Object.entries(circuits).map(([name, c]) => (
              <div key={name} className={`border rounded p-3 ${c.state === "open" ? "bg-red-50 border-red-200" : c.state === "half-open" ? "bg-amber-50 border-amber-200" : "bg-emerald-50 border-emerald-200"}`}>
                <div className="text-xs font-mono">{name}</div>
                <div className="text-lg font-medium uppercase">{c.state}</div>
                <div className="text-xs opacity-70">Hata: {c.fails}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
