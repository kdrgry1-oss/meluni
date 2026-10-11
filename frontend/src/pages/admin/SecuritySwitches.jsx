/**
 * SecuritySwitches.jsx — Acil Anahtarlar (kill switches) — YALNIZ süper admin.
 * Backend: GET /api/admin/security-switches, POST /api/admin/security-switches/{name} {on, reason}
 * Her değişiklik denetim kaydına yazılır ve süper adminlere güvenlik alarmı gider.
 * Anahtar en geç 10 sn içinde tüm sunucu süreçlerinde etkin olur.
 */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { ShieldAlert, RefreshCw, Power } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const fmt = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", timeZone: "Europe/Istanbul" });
  } catch { return iso; }
};

function StatusBadge({ on }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-medium ${
      on ? "bg-red-50 text-red-700 border-red-200" : "bg-green-50 text-green-700 border-green-200"}`}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${on ? "bg-red-600" : "bg-green-600"}`} />
      {on ? "AÇIK" : "Kapalı"}
    </span>
  );
}

export default function SecuritySwitches() {
  const [items, setItems] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(null);   // {name, label} — açma gerekçesi bekleniyor
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState("");
  const { user, loginWithToken } = useAuth();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/security-switches`, auth());
      setItems(r.data?.items || []);
      setHistory(r.data?.history || []);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Anahtarlar yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const send = async (name, on, why) => {
    setBusy(name);
    try {
      const r = await axios.post(`${API}/admin/security-switches/${name}`, { on, reason: why || "" }, auth());
      // Geri yükleme modu kapanınca tüm oturumlar düşer; sunucu bu oturum için taze token döner.
      if (r.data?.token) loginWithToken(r.data.token, user);
      const fin = r.data?.restore_finalize;
      if (fin) {
        const n = fin.staff?.redeactivated || 0;
        toast.success(`Geri yükleme kapatıldı: tüm oturumlar sonlandırıldı, KVKK silmeleri yeniden uygulandı, ${n} personel yeniden pasifleştirildi.`);
      } else {
        toast.success(on ? "Anahtar AÇILDI" : "Anahtar kapatıldı");
      }
      setPending(null);
      setReason("");
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Değiştirilemedi");
    } finally {
      setBusy("");
    }
  };

  const onToggle = async (it) => {
    if (!it.on) {
      setPending({ name: it.name, label: it.label });
      setReason("");
      return;
    }
    const ok = window.appConfirm
      ? await window.appConfirm({ title: "Anahtarı kapat", description: `"${it.label}" kapatılsın mı?`,
          confirmText: "Kapat", cancelText: "Vazgeç", variant: "warning" })
      : window.confirm(`"${it.label}" kapatılsın mı?`);
    if (ok) send(it.name, false, "");
  };

  return (
    <div data-testid="security-switches" className="space-y-6 p-6 max-w-[1100px] mx-auto">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="bg-black p-2.5 rounded-lg">
            <ShieldAlert className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-light tracking-tight">Acil Anahtarlar</h1>
            <p className="text-sm text-gray-500">Olay anında kritik işlemleri tek tıkla durdurun. Değişiklik ≤10 sn içinde etkinleşir.</p>
          </div>
        </div>
        <button onClick={load} disabled={loading}
          className="inline-flex items-center gap-2 px-3 py-2 border rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          <span>Yenile</span>
        </button>
      </div>

      <div className="space-y-3">
        {items.map((it) => (
          <div key={it.name} data-testid={`switch-${it.name}`}
               className={`border rounded-lg p-4 ${it.on ? "border-red-200 bg-red-50/40" : "border-gray-200 bg-white"}`}>
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-gray-900">{it.label}</span>
                  <StatusBadge on={it.on} />
                </div>
                <p className="text-sm text-gray-500 mt-1">{it.description}</p>
                {it.at && (
                  <p className="text-xs text-gray-400 mt-1">
                    Son değişiklik: {fmt(it.at)} · {it.by || "—"}{it.reason ? ` · ${it.reason}` : ""}
                  </p>
                )}
              </div>
              <button onClick={() => onToggle(it)} disabled={!!busy}
                data-testid={`switch-toggle-${it.name}`}
                className={`shrink-0 inline-flex items-center justify-center gap-2 w-28 py-2 rounded-lg text-sm font-medium disabled:opacity-50 ${
                  it.on ? "border border-gray-300 bg-white text-gray-800 hover:bg-gray-50" : "bg-red-600 text-white hover:bg-red-700"}`}>
                <Power className="w-4 h-4 shrink-0" />
                <span>{busy === it.name ? "…" : (it.on ? "Kapat" : "Aç")}</span>
              </button>
            </div>
            {pending?.name === it.name && (
              <div className="mt-4 pt-4 border-t border-gray-200 flex items-center gap-3">
                <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300}
                  placeholder="Gerekçe (zorunlu) — ör. şüpheli iade işlemi incelemesi"
                  className="flex-1 min-w-0 border rounded-lg px-3 py-2 text-sm" data-testid="switch-reason" />
                <button onClick={() => { setPending(null); setReason(""); }} disabled={!!busy}
                  className="shrink-0 w-28 py-2 border rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">Vazgeç</button>
                <button onClick={() => send(it.name, true, reason.trim())}
                  disabled={!!busy || reason.trim().length < 5}
                  className="shrink-0 w-28 py-2 rounded-lg text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                  data-testid="switch-confirm-on">Aç</button>
              </div>
            )}
          </div>
        ))}
        {!items.length && !loading && (
          <div className="border rounded-lg p-6 text-sm text-gray-500 text-center">Anahtar bulunamadı (süper admin yetkisi gerekir).</div>
        )}
      </div>

      <div className="border rounded-lg bg-white">
        <div className="px-4 py-3 border-b text-sm font-medium">Değişiklik geçmişi</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs">
              <tr>
                <th className="text-left font-medium px-4 py-2">Tarih</th>
                <th className="text-left font-medium px-4 py-2">Anahtar</th>
                <th className="text-left font-medium px-4 py-2">Durum</th>
                <th className="text-left font-medium px-4 py-2">Yapan</th>
                <th className="text-left font-medium px-4 py-2">Gerekçe</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h, i) => (
                <tr key={`${h.name}-${h.at}-${i}`} className="border-t">
                  <td className="px-4 py-2 whitespace-nowrap tabular-nums">{fmt(h.at)}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{(items.find((x) => x.name === h.name) || {}).label || h.name}</td>
                  <td className="px-4 py-2"><StatusBadge on={!!h.on} /></td>
                  <td className="px-4 py-2 whitespace-nowrap">{h.by || "—"}</td>
                  <td className="px-4 py-2 text-gray-600">{h.reason || "—"}</td>
                </tr>
              ))}
              {!history.length && (
                <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-400">Henüz değişiklik yok.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
