/**
 * =============================================================================
 * SecurityDashboard.jsx — Güvenlik Paneli (Admin)
 * =============================================================================
 * Iteration 33'teki audit log altyapısının üstüne kuruludur.
 * Backend: /api/admin/security/{summary, top-failed-emails, top-failed-ips,
 *          timeline, recent-events, unlock-user}
 * =============================================================================
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  ShieldCheck, ShieldAlert, AlertTriangle, RefreshCw, Lock, Unlock,
  Activity, Eye, Mail, Globe, KeyRound, UserX
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const formatTime = (iso) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return iso; }
};

const Card = ({ icon: Icon, label, value, sub, tone = "default" }) => {
  const tones = {
    default: "bg-white border-gray-200 text-gray-900",
    success: "bg-green-50 border-green-200 text-green-900",
    danger:  "bg-red-50 border-red-200 text-red-900",
    warn:    "bg-amber-50 border-amber-200 text-amber-900",
    info:    "bg-blue-50 border-blue-200 text-blue-900",
  };
  return (
    <div data-testid={`security-card-${label}`} className={`border rounded-lg p-4 ${tones[tone]}`}>
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium tracking-wide opacity-70">{label}</div>
        {Icon ? <Icon className="w-4 h-4 opacity-60" /> : null}
      </div>
      <div className="text-3xl font-light mt-2 tabular-nums">{value ?? "—"}</div>
      {sub ? <div className="text-xs opacity-70 mt-1">{sub}</div> : null}
    </div>
  );
};

export default function SecurityDashboard() {
  const [windowHours, setWindowHours] = useState(24);
  const [summary, setSummary] = useState(null);
  const [topEmails, setTopEmails] = useState([]);
  const [topIps, setTopIps] = useState([]);
  const [recent, setRecent] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState({ event: "", success: "", email: "", ip: "" });
  // IP Blocklist (Iter36)
  const [ipList, setIpList] = useState([]);
  const [newIp, setNewIp] = useState({ ip: "", hours: 24, permanent: false, reason: "" });

  const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

  const load = async () => {
    setLoading(true);
    try {
      const [s, te, ti, re, ipl] = await Promise.all([
        axios.get(`${API}/admin/security/summary?window_hours=${windowHours}`, auth()),
        axios.get(`${API}/admin/security/top-failed-emails?window_hours=${windowHours}&limit=10`, auth()),
        axios.get(`${API}/admin/security/top-failed-ips?window_hours=${windowHours}&limit=10`, auth()),
        axios.get(`${API}/admin/security/recent-events?limit=100${
          filter.event ? `&event=${filter.event}` : ""
        }${filter.success !== "" ? `&success=${filter.success}` : ""
        }${filter.email ? `&email=${encodeURIComponent(filter.email)}` : ""
        }${filter.ip ? `&ip=${encodeURIComponent(filter.ip)}` : ""}`, auth()),
        axios.get(`${API}/admin/security/ip-blocklist`, auth()),
      ]);
      setSummary(s.data);
      setTopEmails(te.data.items || []);
      setTopIps(ti.data.items || []);
      setRecent(re.data.items || []);
      setIpList(ipl.data.items || []);
    } catch (e) {
      toast.error("Yüklenemedi: " + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [windowHours]);

  const unlockUser = async (email) => {
    if (!confirm(`${email} hesabının kilidini açmak istediğinize emin misiniz?`)) return;
    try {
      await axios.post(`${API}/admin/security/unlock-user`, { email }, auth());
      toast.success(`${email} kilidi açıldı`);
      load();
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    }
  };

  const blockIp = async () => {
    if (!newIp.ip.trim()) { toast.error("IP adresi gerekli"); return; }
    try {
      await axios.post(`${API}/admin/security/ip-blocklist`, newIp, auth());
      toast.success(`${newIp.ip} engellendi`);
      setNewIp({ ip: "", hours: 24, permanent: false, reason: "" });
      load();
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    }
  };

  const unblockIp = async (ip) => {
    if (!confirm(`${ip} IP banı kaldırılsın mı?`)) return;
    try {
      await axios.delete(`${API}/admin/security/ip-blocklist/${encodeURIComponent(ip)}`, auth());
      toast.success(`${ip} engeli kaldırıldı`);
      load();
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    }
  };

  return (
    <div data-testid="security-dashboard" className="space-y-6 p-6 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="bg-black p-2.5 rounded-lg">
            <ShieldCheck className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-light tracking-tight">Güvenlik Paneli</h1>
            <p className="text-sm text-gray-500">Audit log + lockout + brute force tespiti</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <select
            data-testid="security-window-select"
            value={windowHours}
            onChange={(e) => setWindowHours(Number(e.target.value))}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-white"
          >
            <option value={1}>Son 1 saat</option>
            <option value={24}>Son 24 saat</option>
            <option value={168}>Son 7 gün</option>
            <option value={720}>Son 30 gün</option>
          </select>
          <button
            data-testid="security-refresh-btn"
            onClick={load}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-1.5 border border-gray-300 rounded text-sm hover:bg-gray-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Yenile
          </button>
        </div>
      </div>

      {/* Summary KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card icon={Activity} label="Toplam Olay" value={summary?.total_events ?? 0}
              sub={`Son ${summary?.window_hours ?? 24} saat`} />
        <Card icon={ShieldCheck} label="Başarılı Giriş" value={summary?.successful_logins ?? 0} tone="success" />
        <Card icon={ShieldAlert} label="Başarısız Giriş" value={summary?.failed_logins ?? 0} tone="danger" />
        <Card icon={Lock} label="Aktif Kilitli Hesap" value={summary?.active_lockouts ?? 0} tone={summary?.active_lockouts ? "warn" : "default"} />
        <Card icon={KeyRound} label="Yeni Kayıt" value={summary?.registrations ?? 0} tone="info" />
        <Card icon={UserX} label="Şifre Değiş. Hata" value={summary?.password_change_failures ?? 0}
              tone={summary?.password_change_failures ? "warn" : "default"} />
        <Card icon={AlertTriangle} label="NoSQL Injection Try" value={summary?.nosql_injection_attempts ?? 0}
              tone={summary?.nosql_injection_attempts ? "warn" : "default"} />
        <Card icon={ShieldAlert} label="Lockout Blokları" value={summary?.lockout_blocked_attempts ?? 0}
              tone={summary?.lockout_blocked_attempts ? "warn" : "default"} />
      </div>

      {/* Locked accounts list */}
      {summary?.locked_users?.length > 0 && (
        <div data-testid="security-locked-list" className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold flex items-center gap-2 mb-3">
            <Lock className="w-4 h-4 text-amber-700" />
            Şu an Kilitli Hesaplar ({summary.locked_users.length})
          </h3>
          <div className="space-y-2">
            {summary.locked_users.map((u) => (
              <div key={u.email} className="flex items-center justify-between bg-white border border-amber-200 rounded px-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{u.email}</div>
                  <div className="text-xs text-gray-500">
                    Kilit Sonu: {formatTime(u.locked_until)} · Hatalı: {u.failed_attempts}
                  </div>
                </div>
                <button
                  data-testid={`unlock-btn-${u.email}`}
                  onClick={() => unlockUser(u.email)}
                  className="flex items-center gap-1 px-3 py-1 bg-black text-white text-xs rounded hover:bg-gray-800"
                >
                  <Unlock className="w-3 h-3" /> Kilidi Aç
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* IP Blocklist (Iter36 — brute force IP-level ban) */}
      <div data-testid="security-ip-blocklist" className="bg-white border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-semibold flex items-center gap-2 mb-3">
          <Globe className="w-4 h-4 text-red-700" />
          IP Engel Listesi ({ipList.length})
          <span className="text-xs text-gray-400 font-normal ml-2">
            Otomatik: 50+ başarısız login/saat → 24 saat ban
          </span>
        </h3>

        {/* Manuel ekleme formu */}
        <div className="bg-gray-50 border border-gray-200 rounded p-3 mb-3 grid grid-cols-1 md:grid-cols-5 gap-2">
          <input
            data-testid="ipblock-ip-input"
            placeholder="IP adresi (ör: 1.2.3.4)"
            value={newIp.ip}
            onChange={(e) => setNewIp({ ...newIp, ip: e.target.value })}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm md:col-span-2 font-mono"
          />
          <input
            data-testid="ipblock-hours-input"
            type="number"
            min="1"
            max="8760"
            placeholder="Saat (24)"
            value={newIp.hours}
            disabled={newIp.permanent}
            onChange={(e) => setNewIp({ ...newIp, hours: parseInt(e.target.value, 10) || 24 })}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm disabled:bg-gray-100"
          />
          <label className="flex items-center gap-2 text-sm">
            <input
              data-testid="ipblock-permanent-cb"
              type="checkbox"
              checked={newIp.permanent}
              onChange={(e) => setNewIp({ ...newIp, permanent: e.target.checked })}
            />
            Kalıcı
          </label>
          <button
            data-testid="ipblock-add-btn"
            onClick={blockIp}
            className="bg-red-600 hover:bg-red-700 text-white rounded px-3 py-1.5 text-sm font-medium"
          >
            IP'yi Engelle
          </button>
          <input
            data-testid="ipblock-reason-input"
            placeholder="Sebep (opsiyonel)"
            value={newIp.reason}
            onChange={(e) => setNewIp({ ...newIp, reason: e.target.value })}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm md:col-span-5"
          />
        </div>

        {/* Aktif ban listesi */}
        {ipList.length === 0 ? (
          <p className="text-sm text-gray-400 text-center py-4">Aktif IP engeli yok</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-3 py-2 text-xs font-medium">IP</th>
                  <th className="text-left px-3 py-2 text-xs font-medium">Tip</th>
                  <th className="text-left px-3 py-2 text-xs font-medium">Bitiş</th>
                  <th className="text-left px-3 py-2 text-xs font-medium">Sebep</th>
                  <th className="text-left px-3 py-2 text-xs font-medium">Tetik Sayı</th>
                  <th className="text-right px-3 py-2 text-xs font-medium">İşlem</th>
                </tr>
              </thead>
              <tbody>
                {ipList.map((ip) => (
                  <tr key={ip.ip} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="px-3 py-1.5 font-mono text-xs">{ip.ip}</td>
                    <td className="px-3 py-1.5">
                      {ip.permanent ? (
                        <span className="px-2 py-0.5 bg-red-100 text-red-800 rounded text-xs">KALICI</span>
                      ) : ip.auto_blocked ? (
                        <span className="px-2 py-0.5 bg-amber-100 text-amber-800 rounded text-xs">OTOMATİK</span>
                      ) : (
                        <span className="px-2 py-0.5 bg-blue-100 text-blue-800 rounded text-xs">MANUEL</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 font-mono text-xs">
                      {ip.permanent ? "—" : formatTime(ip.blocked_until)}
                    </td>
                    <td className="px-3 py-1.5 text-xs text-gray-600 truncate max-w-xs">{ip.reason || "—"}</td>
                    <td className="px-3 py-1.5 text-xs">{ip.trigger_count || "—"}</td>
                    <td className="px-3 py-1.5 text-right">
                      <button
                        data-testid={`ipblock-unblock-${ip.ip}`}
                        onClick={() => unblockIp(ip.ip)}
                        className="px-2 py-0.5 bg-black text-white rounded text-xs hover:bg-gray-800"
                      >
                        Kaldır
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Top failed emails + IPs */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold flex items-center gap-2 mb-3">
            <Mail className="w-4 h-4 text-gray-700" /> Çok Saldırılan E-postalar
          </h3>
          <div className="space-y-1.5">
            {topEmails.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-4">Veri yok</p>
            ) : topEmails.map((row, i) => (
              <div key={i} className="flex items-center justify-between text-sm py-1 border-b border-gray-100">
                <div className="font-mono text-xs truncate max-w-[55%]">{row.email}</div>
                <div className="text-right">
                  <span className="font-semibold tabular-nums">{row.count}</span>
                  <span className="text-xs text-gray-400 ml-2">{row.distinct_ips} IP</span>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold flex items-center gap-2 mb-3">
            <Globe className="w-4 h-4 text-gray-700" /> Şüpheli IP'ler
          </h3>
          <div className="space-y-1.5">
            {topIps.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-4">Veri yok</p>
            ) : topIps.map((row, i) => (
              <div key={i} className="flex items-center justify-between text-sm py-1 border-b border-gray-100">
                <div className="font-mono text-xs">{row.ip || "(unknown)"}</div>
                <div className="text-right">
                  <span className="font-semibold tabular-nums">{row.count}</span>
                  <span className="text-xs text-gray-400 ml-2">{row.distinct_emails} e-posta</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Recent events */}
      <div className="bg-white border border-gray-200 rounded-lg">
        <div className="p-4 border-b border-gray-200">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <Eye className="w-4 h-4" /> Son Olaylar (max 100)
            </h3>
            <button
              data-testid="security-events-refresh"
              onClick={load}
              className="text-xs text-gray-500 hover:text-black"
            >
              Yenile
            </button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <select data-testid="filter-event" value={filter.event}
                    onChange={(e) => setFilter({ ...filter, event: e.target.value })}
                    className="border border-gray-300 rounded px-2 py-1 text-xs">
              <option value="">Tüm Eventler</option>
              <option value="login">login</option>
              <option value="register">register</option>
              <option value="password_change">password_change</option>
              <option value="admin_unlock">admin_unlock</option>
            </select>
            <select data-testid="filter-success" value={filter.success}
                    onChange={(e) => setFilter({ ...filter, success: e.target.value })}
                    className="border border-gray-300 rounded px-2 py-1 text-xs">
              <option value="">Başarılı/Başarısız (hepsi)</option>
              <option value="true">Sadece Başarılı</option>
              <option value="false">Sadece Başarısız</option>
            </select>
            <input data-testid="filter-email" placeholder="E-posta"
                   value={filter.email}
                   onChange={(e) => setFilter({ ...filter, email: e.target.value })}
                   className="border border-gray-300 rounded px-2 py-1 text-xs" />
            <input data-testid="filter-ip" placeholder="IP"
                   value={filter.ip}
                   onChange={(e) => setFilter({ ...filter, ip: e.target.value })}
                   className="border border-gray-300 rounded px-2 py-1 text-xs" />
          </div>
          <div className="mt-2 text-right">
            <button data-testid="filter-apply" onClick={load}
                    className="px-3 py-1 bg-black text-white text-xs rounded">Uygula</button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-3 py-2 font-medium text-xs">Zaman</th>
                <th className="text-left px-3 py-2 font-medium text-xs">Event</th>
                <th className="text-center px-3 py-2 font-medium text-xs">Sonuç</th>
                <th className="text-left px-3 py-2 font-medium text-xs">E-posta</th>
                <th className="text-left px-3 py-2 font-medium text-xs">IP</th>
                <th className="text-left px-3 py-2 font-medium text-xs">Sebep</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 ? (
                <tr><td colSpan={6} className="text-center text-gray-400 py-6">Kayıt yok</td></tr>
              ) : recent.map((r) => (
                <tr key={r.id || `${r.created_at}-${r.email}`} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="px-3 py-1.5 font-mono text-xs whitespace-nowrap">{formatTime(r.created_at)}</td>
                  <td className="px-3 py-1.5">
                    <span className="inline-block px-2 py-0.5 bg-gray-100 rounded text-xs">{r.event}</span>
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    {r.success ? (
                      <span className="inline-block w-2 h-2 bg-green-500 rounded-full" title="success"/>
                    ) : (
                      <span className="inline-block w-2 h-2 bg-red-500 rounded-full" title="fail"/>
                    )}
                  </td>
                  <td className="px-3 py-1.5 font-mono text-xs">{r.email || "—"}</td>
                  <td className="px-3 py-1.5 font-mono text-xs">{r.ip || "—"}</td>
                  <td className="px-3 py-1.5 text-xs text-gray-600">{(r.meta || {}).reason || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
