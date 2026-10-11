/**
 * =============================================================================
 * SecurityDashboard.jsx — Güvenlik Paneli (Admin)
 * =============================================================================
 * Iteration 33'teki audit log altyapısının üstüne kuruludur.
 * Backend: /api/admin/security/{summary, top-failed-emails, top-failed-ips,
 *          timeline, recent-events, unlock-user, alert-acks, drills}
 * SOC-01: onay bekleyen uyarılar ("Onayla") + alarm tatbikatı ("Tatbikat Başlat") — yalnız süper admin.
 * =============================================================================
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  ShieldCheck, ShieldAlert, AlertTriangle, RefreshCw, Lock, Unlock,
  Activity, Eye, Mail, Globe, KeyRound, UserX, Link2, Archive, LogOut, BellRing, UserCheck
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const formatTime = (iso) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return iso; }
};

// Süre (sn) → "4 dk 12 sn"
const formatDuration = (secs) => {
  if (secs === null || secs === undefined) return "—";
  const s = Math.max(0, Math.round(Number(secs) || 0));
  const m = Math.floor(s / 60);
  return m ? `${m} dk ${s % 60} sn` : `${s} sn`;
};

const DRILL_BADGE = {
  "geçti": { cls: "bg-green-100 text-green-800", label: "Geçti" },
  "kaldı": { cls: "bg-red-100 text-red-800", label: "Kaldı" },
  "bekliyor": { cls: "bg-amber-100 text-amber-800", label: "Bekliyor" },
};

const DrillBadge = ({ status }) => {
  const b = DRILL_BADGE[status] || { cls: "bg-gray-100 text-gray-600", label: "Yok" };
  return (
    <span className={`inline-flex items-center justify-center shrink-0 w-16 px-2 py-0.5 rounded text-xs font-medium ${b.cls}`}>
      {b.label}
    </span>
  );
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
  // IP+hesap kilitleri, denetim zinciri (tamper-evident) durumu, işlem meşguliyeti
  const [ipLocks, setIpLocks] = useState([]);
  const [chain, setChain] = useState(null);
  const [selftest, setSelftest] = useState(null);
  // SOC-01: onay bekleyen uyarılar + alarm tatbikatı (yalnız süper admin; yetkisizse kart gizli)
  const [acks, setAcks] = useState(null);
  const [drills, setDrills] = useState(null);
  const [busy, setBusy] = useState("");
  const { user, loginWithToken } = useAuth();

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
      const [lk, ch, st, ak, dr] = await Promise.allSettled([
        axios.get(`${API}/admin/security/locks`, auth()),
        axios.get(`${API}/admin/security/audit-chain`, auth()),
        axios.get(`${API}/admin/security/selftest`, auth()),
        axios.get(`${API}/admin/security/alert-acks`, auth()),
        axios.get(`${API}/admin/security/drills`, auth()),
      ]);
      setIpLocks(lk.status === "fulfilled" ? (lk.value.data?.ip_account_locks || []) : []);
      setChain(ch.status === "fulfilled" ? ch.value.data : null);
      setSelftest(st.status === "fulfilled" ? st.value.data : null);
      setAcks(ak.status === "fulfilled" ? ak.value.data : null);
      setDrills(dr.status === "fulfilled" ? dr.value.data : null);
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

  const clearIpLock = async (row) => {
    if (!window.confirm(`${row.email} hesabının ${row.ip || "tüm IP"} kilidi kaldırılsın mı?`)) return;
    try {
      await axios.post(`${API}/admin/security/locks/clear`, { email: row.email, ip: row.ip }, auth());
      toast.success("Kilit kaldırıldı");
      load();
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    }
  };

  const runChain = async (kind) => {
    setBusy(kind);
    try {
      if (kind === "export") {
        const r = await axios.post(`${API}/admin/security/audit-chain/export`, {}, auth());
        if (r.data?.ok) toast.success(`Arşive aktarıldı (${r.data.written || 0} kayıt)`);
        else toast.error(`Arşivlenemedi: ${r.data?.skipped || r.data?.error || "bilinmeyen"}`);
      } else {
        const r = await axios.post(`${API}/admin/security/audit-chain/verify`, { full: kind === "full" }, auth());
        const c = r.data?.chain || {};
        if (c.ok) toast.success(`Zincir sağlam (${c.checked} kayıt doğrulandı)`);
        else toast.error(`Zincir BOZUK: ${(c.problems || []).length} sorun — süper adminlere uyarı gönderildi`);
      }
      load();
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    } finally {
      setBusy("");
    }
  };

  const testAlert = async () => {
    setBusy("alert");
    try {
      const r = await axios.post(`${API}/admin/security/alerts/test`, {}, auth());
      const rows = r.data?.recipients || [];
      const ok = rows.filter((x) => x.ok).length;
      if (!rows.length) toast.error("Alıcı bulunamadı (İşletme Kuralı / süper admin / firma e-postası)");
      else if (ok === rows.length) toast.success(`Test uyarısı ${ok} alıcıya iletildi (gönderen: ${r.data?.sender || "—"})`);
      else toast.error(`${rows.length - ok}/${rows.length} alıcıya iletilemedi: ${rows.filter((x) => !x.ok).map((x) => `${x.to} (${x.error})`).join(", ")}`);
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    } finally {
      setBusy("");
    }
  };

  const runSelftest = async () => {
    setBusy("selftest");
    try {
      const r = await axios.post(`${API}/admin/security/selftest/run`, {}, auth());
      setSelftest(r.data);
      if (r.data?.fail) toast.error(`Öz-test: ${r.data.fail} hata, ${r.data.warn || 0} uyarı`);
      else toast.success(`Öz-test tamamlandı: ${r.data?.warn || 0} uyarı`);
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    } finally {
      setBusy("");
    }
  };

  const ackAlert = async (row) => {
    setBusy(`ack:${row.id}`);
    try {
      const r = await axios.post(`${API}/admin/security/alert-acks/${encodeURIComponent(row.id)}/ack`, {}, auth());
      const d = r.data?.drill;
      if (d) toast.success(`Tatbikat onaylandı (${formatDuration(r.data?.ack_seconds)}) — sonuç: ${(DRILL_BADGE[d.status] || {}).label || d.status}`);
      else toast.success(`Uyarı onaylandı (${formatDuration(r.data?.ack_seconds)})`);
      load();
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    } finally {
      setBusy("");
    }
  };

  const startDrill = async () => {
    if (!window.confirm("Tüm güvenlik uyarısı alıcılarına [TATBİKAT] etiketli KRİTİK bir uyarı e-postası gönderilecek. Alıcılardan biri 15 dakika içinde 'Gördüm' bağlantısına tıklamalı ya da burada onaylamalı. Başlatılsın mı?")) return;
    setBusy("drill");
    try {
      const r = await axios.post(`${API}/admin/security/drills/start`, {}, auth());
      if (r.data?.status === "kaldı") toast.error(`Tatbikat uyarısı teslim edilemedi: ${r.data?.result_reason || ""}`);
      else toast.success(`Tatbikat başladı: ${r.data?.delivered_ok || 0}/${r.data?.delivered_total || 0} alıcıya gönderildi`);
      load();
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    } finally {
      setBusy("");
    }
  };

  const revokeAll = async () => {
    if (!window.confirm("TÜM kullanıcıların (personel ve müşteriler) oturumları ANINDA sonlandırılacak. Herkes yeniden giriş yapmak zorunda kalacak. Emin misiniz?")) return;
    setBusy("revoke");
    try {
      const r = await axios.post(`${API}/admin/security/revoke-all-sessions`, {}, auth());
      if (r.data?.token) loginWithToken(r.data.token, user);
      toast.success("Tüm oturumlar sonlandırıldı");
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    } finally {
      setBusy("");
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
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
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
        <Card icon={Lock} label="IP+Hesap Kilidi" value={summary?.ip_account_locks ?? 0}
              tone={summary?.ip_account_locks ? "warn" : "default"} />
        <Card icon={UserCheck} label="Onay Bekleyen İşlem" value={summary?.pending_approvals ?? 0}
              sub="Kullanıcılar & Roller → Onay Bekleyenler"
              tone={summary?.pending_approvals ? "info" : "default"} />
      </div>

      {/* Canlı güvenlik öz-testi — yalnız süper admin görür */}
      {selftest && (
        <div data-testid="security-selftest" className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3 mb-4">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-gray-700" /> Güvenlik Öz-Testi
              <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                !selftest.at ? "bg-gray-100 text-gray-600"
                  : selftest.fail ? "bg-red-100 text-red-800"
                    : selftest.warn ? "bg-amber-100 text-amber-800" : "bg-green-100 text-green-800"}`}>
                {!selftest.at ? "Çalışmadı" : selftest.fail ? `${selftest.fail} hata` : selftest.warn ? `${selftest.warn} uyarı` : "Tamam"}
              </span>
            </h3>
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-500 tabular-nums">
                {selftest.at ? new Date(selftest.at).toLocaleString("tr-TR") : "—"}
              </span>
              <button onClick={runSelftest} disabled={!!busy} data-testid="selftest-run-btn"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50">
                <ShieldCheck className="w-3.5 h-3.5" /> {busy === "selftest" ? "Çalışıyor…" : "Şimdi Çalıştır"}
              </button>
            </div>
          </div>
          <div className="divide-y divide-gray-100">
            {(selftest.checks || []).map((c) => (
              <div key={c.name} className="flex items-start gap-3 py-2 text-sm">
                <span className={`inline-flex items-center justify-center shrink-0 w-14 px-2 py-0.5 rounded text-xs font-medium ${
                  c.status === "ok" ? "bg-green-100 text-green-800"
                    : c.status === "warn" ? "bg-amber-100 text-amber-800" : "bg-red-100 text-red-800"}`}>
                  {c.status === "ok" ? "Tamam" : c.status === "warn" ? "Uyarı" : "Hata"}
                </span>
                <div className="min-w-0">
                  <div className="font-medium text-gray-900">{c.name}</div>
                  <div className="text-xs text-gray-600 break-words">{c.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SOC-01: Uyarı onayları (insan teyidi) + alarm tatbikatı — yalnız süper admin görür */}
      {(acks || drills) && (
        <div data-testid="security-alert-acks" className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3 mb-4">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <BellRing className="w-4 h-4 text-gray-700" /> Uyarı Onayları & Alarm Tatbikatı
              <span className={`inline-flex items-center shrink-0 whitespace-nowrap px-2 py-0.5 rounded text-xs font-medium ${
                (acks?.pending_critical || 0) ? "bg-red-100 text-red-800"
                  : (acks?.pending || []).length ? "bg-amber-100 text-amber-800" : "bg-green-100 text-green-800"}`}>
                {(acks?.pending || []).length ? `${acks.pending.length} onay bekliyor` : "Tamam"}
              </span>
            </h3>
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-500 tabular-nums">
                Yükseltme: {acks?.escalation_minutes ?? drills?.escalation_minutes ?? 15} dk
              </span>
              <button onClick={startDrill} disabled={!!busy || !drills} data-testid="drill-start-btn"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50">
                <ShieldAlert className="w-3.5 h-3.5" /> {busy === "drill" ? "Gönderiliyor…" : "Tatbikat Başlat"}
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">Onay bekleyen uyarı</div>
              <div className="text-lg font-light tabular-nums mt-1">{(acks?.pending || []).length}</div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">Son tatbikat</div>
              <div className="flex items-center h-7 text-sm font-mono mt-1">{formatTime(drills?.last?.started_at)}</div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">Sonuç (≤ {drills?.pass_minutes ?? 15} dk onay)</div>
              <div className="flex items-center h-7 mt-1"><DrillBadge status={drills?.last?.status} /></div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">Onay süresi</div>
              <div className="text-lg font-light tabular-nums mt-1">{formatDuration(drills?.last?.ack_seconds)}</div>
            </div>
          </div>
          {drills && (
            <p className="text-xs text-gray-500 mt-3">
              Aylık otomatik tatbikat: {drills.monthly_enabled
                ? `açık — sonraki ${formatTime(drills.next_monthly_at)}`
                : "kapalı (İşletme Kuralları → Güvenlik)"}
              {drills.recent_success ? "" : " · Son 35 günde başarılı tatbikat yok"}
            </p>
          )}

          <div className="mt-4">
            <div className="text-xs font-medium text-gray-500 mb-1">Onay bekleyen uyarılar (son 7 gün)</div>
            {(acks?.pending || []).length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-4">Onay bekleyen uyarı yok</p>
            ) : (
              <div className="divide-y divide-gray-100">
                {acks.pending.map((a) => (
                  <div key={a.id} data-testid={`alert-ack-row-${a.id}`} className="flex items-center gap-3 py-2 text-sm">
                    <span className={`inline-flex items-center justify-center shrink-0 w-14 px-2 py-0.5 rounded text-xs font-medium ${
                      a.level === "critical" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800"}`}>
                      {a.level === "critical" ? "Kritik" : "Uyarı"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-medium text-gray-900 truncate">
                          {a.is_drill ? String(a.title || "").replace(/^\[TATBİKAT\]\s*/, "") : a.title}
                        </span>
                        {a.is_drill && (
                          <span className="inline-flex items-center shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">TATBİKAT</span>
                        )}
                      </div>
                      <div className="text-xs text-gray-500 tabular-nums">
                        {formatTime(a.created_at)} · {a.kind}
                        {a.escalated_at ? ` · YANIT YOK gönderildi ${formatTime(a.escalated_at)}` : ""}
                      </div>
                    </div>
                    <button onClick={() => ackAlert(a)} disabled={!!busy} data-testid={`alert-ack-btn-${a.id}`}
                      className="inline-flex items-center gap-1.5 shrink-0 px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50">
                      <ShieldCheck className="w-3.5 h-3.5" /> {busy === `ack:${a.id}` ? "Kaydediliyor…" : "Onayla"}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {(drills?.history || []).length > 0 && (
            <div className="mt-4">
              <div className="text-xs font-medium text-gray-500 mb-1">Tatbikat geçmişi</div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-3 py-2 text-xs font-medium">Başlangıç</th>
                      <th className="text-left px-3 py-2 text-xs font-medium">Tetik</th>
                      <th className="text-left px-3 py-2 text-xs font-medium">Onaylayan</th>
                      <th className="text-right px-3 py-2 text-xs font-medium">Teslim</th>
                      <th className="text-right px-3 py-2 text-xs font-medium">Onay süresi</th>
                      <th className="text-right px-3 py-2 text-xs font-medium">Sonuç</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drills.history.map((d) => (
                      <tr key={d.id} className="border-b border-gray-100 hover:bg-gray-50">
                        <td className="px-3 py-1.5 font-mono text-xs whitespace-nowrap">{formatTime(d.started_at)}</td>
                        <td className="px-3 py-1.5 text-xs">{d.trigger === "aylık" ? "Aylık (otomatik)" : `Elle · ${d.started_by || "—"}`}</td>
                        <td className="px-3 py-1.5 text-xs">{d.acked_by || "—"}{d.late_ack ? " (geç)" : ""}</td>
                        <td className="px-3 py-1.5 text-xs text-right tabular-nums">{d.delivered_ok ?? 0}/{d.delivered_total ?? 0}</td>
                        <td className="px-3 py-1.5 text-xs text-right tabular-nums">{formatDuration(d.ack_seconds)}</td>
                        <td className="px-3 py-1.5">
                          <div className="flex justify-end" title={d.result_reason || ""}><DrillBadge status={d.status} /></div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Denetim zinciri (tamper-evident) + arşiv + acil işlemler — yalnız süper admin görür */}
      {chain && (
        <div data-testid="security-audit-chain" className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3 mb-4">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <Link2 className="w-4 h-4 text-gray-700" /> Denetim Zinciri & Arşiv
              <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                chain.last_verify_ok === false ? "bg-red-100 text-red-800"
                  : chain.last_verify_ok ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}`}>
                {chain.last_verify_ok === false ? "BOZUK" : chain.last_verify_ok ? "Sağlam" : "Doğrulanmadı"}
              </span>
            </h3>
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => runChain("verify")} disabled={!!busy} data-testid="chain-verify-btn"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50">
                <ShieldCheck className="w-3.5 h-3.5" /> Doğrula
              </button>
              <button onClick={() => runChain("full")} disabled={!!busy} data-testid="chain-full-verify-btn"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50">
                <ShieldCheck className="w-3.5 h-3.5" /> Tam Doğrula
              </button>
              <button onClick={() => runChain("export")} disabled={!!busy || !chain.archive_enabled} data-testid="chain-export-btn"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50">
                <Archive className="w-3.5 h-3.5" /> Şimdi Arşivle
              </button>
              <button onClick={testAlert} disabled={!!busy} data-testid="alert-test-btn"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50">
                <BellRing className="w-3.5 h-3.5" /> Test Uyarısı
              </button>
              <button onClick={revokeAll} disabled={!!busy} data-testid="revoke-all-btn"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded text-xs hover:bg-red-700 disabled:opacity-50">
                <LogOut className="w-3.5 h-3.5" /> Tüm Oturumları Sonlandır
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">Kayıt (zincir başı)</div>
              <div className="text-lg font-light tabular-nums mt-1">{chain.head_seq}</div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">Arşivlenen</div>
              <div className="text-lg font-light tabular-nums mt-1">{chain.exported_seq}</div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">Son arşiv aktarımı</div>
              <div className="text-sm font-mono mt-1">{chain.archive_enabled ? formatTime(chain.last_export_at) : "Arşiv kapalı"}</div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">Son doğrulama</div>
              <div className="text-sm font-mono mt-1">{formatTime(chain.last_verify_at)}</div>
            </div>
          </div>
          {chain.last_export_error && (
            <p className="text-xs text-red-600 mt-3">Son arşiv hatası: {chain.last_export_error} ({formatTime(chain.last_export_error_at)})</p>
          )}
          {chain.last_verify_ok === false && (chain.last_verify_problems || []).length > 0 && (
            <p className="text-xs text-red-600 mt-3">
              Sorunlar: {(chain.last_verify_problems || []).slice(0, 5).map((p) => `#${p.seq} ${p.type}`).join(", ")}
            </p>
          )}
        </div>
      )}

      {/* IP+hesap kilitleri (aynı IP'den aynı hesaba 15 dk'da 5 hatalı deneme) */}
      {ipLocks.length > 0 && (
        <div data-testid="security-ip-locks" className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold flex items-center gap-2 mb-3">
            <Lock className="w-4 h-4 text-amber-700" /> IP + Hesap Kilitleri ({ipLocks.length})
          </h3>
          <div className="space-y-2">
            {ipLocks.map((row) => (
              <div key={`${row.email}|${row.ip}`} className="flex items-center justify-between bg-white border border-amber-200 rounded px-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{row.email}</div>
                  <div className="text-xs text-gray-500">
                    IP: <span className="font-mono">{row.ip || "—"}</span> · Kilit Sonu: {formatTime(row.locked_until)} · Hatalı: {row.count}
                  </div>
                </div>
                <button onClick={() => clearIpLock(row)}
                  className="flex items-center gap-1 px-3 py-1 bg-black text-white text-xs rounded hover:bg-gray-800">
                  <Unlock className="w-3 h-3" /> Kilidi Aç
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

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
