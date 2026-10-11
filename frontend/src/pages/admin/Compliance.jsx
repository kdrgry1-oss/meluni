/**
 * Amazon DPP (Veri Koruma Politikası) Uyum sayfası.
 * Güvenlik anketi <-> sistem kontrolleri eşleştirmesi + PII saklama yönetimi.
 */
import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import { ShieldCheck, CheckCircle, Server, FileText, Cog, Trash2, RefreshCw, Play, Smartphone, KeyRound, Copy } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const STATUS_META = {
  implemented: { label: "Sistemde", icon: <CheckCircle size={13} />, cls: "bg-green-50 text-green-700 border-green-200" },
  policy: { label: "Doküman", icon: <FileText size={13} />, cls: "bg-blue-50 text-blue-700 border-blue-200" },
  infrastructure: { label: "Altyapı", icon: <Server size={13} />, cls: "bg-purple-50 text-purple-700 border-purple-200" },
  process: { label: "Süreç", icon: <Cog size={13} />, cls: "bg-amber-50 text-amber-700 border-amber-200" },
};

export default function Compliance({ embedded = false }) {
  const [checklist, setChecklist] = useState([]);
  const [retention, setRetention] = useState(null);
  const [days, setDays] = useState(30);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([
        axios.get(`${API}/compliance/dpp-checklist`, auth()),
        axios.get(`${API}/compliance/pii-retention/status`, auth()),
      ]);
      setChecklist(c.data.items || []);
      setRetention(r.data);
      setDays(r.data.days || 30);
    } catch {
      toast.error("Yüklenemedi");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const saveConfig = async (enabled) => {
    try {
      await axios.post(`${API}/compliance/pii-retention/config`,
        { enabled: enabled ?? retention.enabled, days: Number(days), platforms: retention.platforms }, auth());
      toast.success("Ayar kaydedildi");
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    }
  };

  const runPurge = async () => {
    setRunning(true);
    try {
      const r = await axios.post(`${API}/compliance/pii-retention/run`, {}, auth());
      toast.success(`PII anonimleştirme çalıştı (toplam ${r.data.total_redacted_orders} sipariş)`);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Çalıştırılamadı");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className={embedded ? "max-w-5xl" : "p-6 max-w-5xl mx-auto"} data-testid="compliance-page">
      <div className="mb-5">
        <h1 className={`${embedded ? "text-lg" : "text-2xl"} font-bold flex items-center gap-2`}>
          <ShieldCheck className="text-green-600" size={24} /> Amazon DPP Uyum
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Amazon Veri Koruma Politikası güvenlik anketini sistemdeki kontrollerle eşleştirir.
          "Restricted" rol başvurusunda bu maddeleri referans alabilirsin.
        </p>
      </div>

      {/* MFA */}
      <MfaCard />

      {/* PII Retention */}
      {retention && (
        <div className="bg-white border rounded-xl p-5 mb-6" data-testid="pii-retention-card">
          <h2 className="font-semibold flex items-center gap-2 mb-3">
            <Trash2 size={16} className="text-red-500" /> PII Saklama & Otomatik Silme
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 text-sm">
            <Stat label="Durum" value={retention.enabled ? "Aktif" : "Kapalı"} ok={retention.enabled} />
            <Stat label="Saklama Süresi" value={`${retention.days} gün`} />
            <Stat label="Anonimleştirilen" value={retention.redacted_orders} />
            <Stat label="Bekleyen (uygun)" value={retention.eligible_pending} />
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Gün (sipariş gönderiminden sonra)</label>
              <input type="number" min="1" max="365" value={days} onChange={(e) => setDays(e.target.value)}
                className="inp w-28" data-testid="pii-days-input" />
            </div>
            <button onClick={() => saveConfig()} className="bg-black text-white px-4 py-2 rounded-lg text-sm" data-testid="pii-save-btn">Kaydet</button>
            <button onClick={() => saveConfig(!retention.enabled)} className="border px-4 py-2 rounded-lg text-sm">
              {retention.enabled ? "Devre Dışı Bırak" : "Aktifleştir"}
            </button>
            <button onClick={runPurge} disabled={running} className="inline-flex items-center gap-1 border px-4 py-2 rounded-lg text-sm text-red-600 hover:bg-red-50 disabled:opacity-50" data-testid="pii-run-btn">
              <Play size={14} /> {running ? "Çalışıyor..." : "Şimdi Çalıştır"}
            </button>
            <button onClick={load} className="border p-2 rounded-lg"><RefreshCw size={14} /></button>
          </div>
          <p className="text-xs text-gray-400 mt-2">
            Amazon kuralı: PII, sipariş gönderiminden ≤30 gün sonra silinmeli. Otomatik görev her gün 03:00 UTC çalışır.
          </p>
        </div>
      )}

      {/* Checklist */}
      <h2 className="font-semibold mb-3">Güvenlik Anketi Eşleştirmesi ({checklist.length})</h2>
      <div className="space-y-2" data-testid="dpp-checklist">
        {checklist.map((it, i) => {
          const m = STATUS_META[it.status] || STATUS_META.process;
          return (
            <div key={i} className="bg-white border rounded-lg p-3 flex items-start gap-3">
              <span className={`shrink-0 inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border ${m.cls}`}>
                {m.icon} {m.label}
              </span>
              <div className="flex-1">
                <p className="text-sm font-medium text-gray-800">{it.q}</p>
                <p className="text-xs text-gray-500 mt-0.5">{it.note}</p>
              </div>
              <span className="text-xs font-semibold text-green-700 shrink-0">{it.answer}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MfaCard() {
  const { user, loginWithToken } = useAuth();
  const [enabled, setEnabled] = useState(null);
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  // Güvenlik onayı: doğrulama ayarı değişiklikleri mevcut şifreyi ister (son 5 dk içinde giriş
  // yapıldıysa sunucu şifresiz de kabul eder). Faktör değişince diğer oturumlar kapanır ve bu
  // oturum için TAZE token döner — yerleştirilmezse panel oturumu düşer.
  const [pw, setPw] = useState("");
  const reauth = () => (pw ? { current_password: pw } : {});
  const adoptFreshToken = (r) => {
    if (r?.data?.token) loginWithToken(r.data.token, user);
  };

  // Kişisel doğrulama kanalı tercihi (kullanıcı isteği: herkes kendi varsayılanını seçsin)
  const [ch, setCh] = useState(null);      // {channel_default, sms_available, email_available, ...}
  const [chBusy, setChBusy] = useState(false);
  // MFA kurtarma kodları (BL-003): yalnız üretildiği an bir kez gösterilir.
  const [rcCodes, setRcCodes] = useState(null);
  const [rcBusy, setRcBusy] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/auth/mfa/status`, auth());
      setEnabled(r.data.mfa_enabled);
      setCh(r.data);
    } catch { /* ignore */ }
  }, []);

  const saveChannel = async (c) => {
    setChBusy(true);
    try {
      await axios.post(`${API}/auth/mfa/channel`, { channel: c, ...reauth() }, auth());
      toast.success(c === "email" ? "Varsayılan: E-posta ✓" : "Varsayılan: SMS ✓");
      loadStatus();
    } catch (e) { toast.error(e.response?.data?.detail || "Kaydedilemedi"); }
    finally { setChBusy(false); }
  };
  useEffect(() => { loadStatus(); }, [loadStatus]);

  const generateRecovery = async () => {
    const msg = "Yeni kodlar üretilince ESKİ kurtarma kodlarının tümü geçersiz olur. Devam edilsin mi?";
    if (ch?.recovery_codes_total > 0
      && !(await (window.appConfirm ? window.appConfirm(msg) : Promise.resolve(window.confirm(msg))))) return;
    setRcBusy(true);
    try {
      const r = await axios.post(`${API}/auth/mfa/recovery-codes`, { ...reauth() }, auth());
      setRcCodes(r.data?.codes || []);
      toast.success("Kurtarma kodları oluşturuldu — şimdi güvenli bir yere kaydedin");
      loadStatus();
    } catch (e) { toast.error(e.response?.data?.detail || "Kodlar oluşturulamadı"); }
    finally { setRcBusy(false); }
  };

  const copyRecovery = async () => {
    try {
      await navigator.clipboard.writeText((rcCodes || []).join("\n"));
      toast.success("Kodlar panoya kopyalandı");
    } catch { toast.error("Kopyalanamadı — kodları elle not edin"); }
  };

  const startSetup = async () => {
    setBusy(true);
    try {
      const r = await axios.post(`${API}/auth/mfa/setup`, { ...reauth() }, auth());
      setSetup(r.data);
    } catch (e) { toast.error(e.response?.data?.detail || "Kurulum başlatılamadı"); }
    finally { setBusy(false); }
  };

  const enable = async () => {
    setBusy(true);
    try {
      const r = await axios.post(`${API}/auth/mfa/enable`, { code, ...reauth() }, auth());
      adoptFreshToken(r);
      toast.success("MFA etkinleştirildi ✓");
      setSetup(null); setCode(""); setPw(""); loadStatus();
    } catch (e) { toast.error(e.response?.data?.detail || "Kod doğrulanamadı"); }
    finally { setBusy(false); }
  };

  const disable = async () => {
    const c = prompt("MFA'yı kapatmak için Authenticator kodunu gir:");
    if (!c) return;
    try {
      const r = await axios.post(`${API}/auth/mfa/disable`, { code: c, ...reauth() }, auth());
      adoptFreshToken(r);
      toast.success("MFA kapatıldı");
      setPw(""); loadStatus();
    } catch (e) { toast.error(e.response?.data?.detail || "Kapatılamadı"); }
  };

  return (
    <div className="bg-white border rounded-xl p-5 mb-6" data-testid="mfa-card">
      <h2 className="font-semibold flex items-center gap-2 mb-3">
        <Smartphone size={16} className="text-indigo-500" /> Çok Faktörlü Doğrulama (MFA / 2FA)
        {enabled && <span className="text-[11px] bg-green-50 text-green-700 px-2 py-0.5 rounded-full">Aktif</span>}
      </h2>
      <div className="flex flex-col md:flex-row md:items-center gap-2 mb-4">
        <label htmlFor="mfa-reauth-pw" className="text-sm text-gray-700 shrink-0">Mevcut şifre</label>
        <input id="mfa-reauth-pw" type="password" autoComplete="current-password" value={pw}
          onChange={(e) => setPw(e.target.value)} placeholder="Güvenlik onayı için"
          className="inp md:w-64" data-testid="mfa-reauth-password" />
        <span className="text-xs text-gray-500">Son 5 dakika içinde giriş yaptıysanız boş bırakabilirsiniz.</span>
      </div>
      {enabled ? (
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-600">Hesabın TOTP MFA ile korunuyor. Girişte Authenticator kodu istenir.</p>
          <button onClick={disable} className="text-sm text-red-600 border px-3 py-1.5 rounded-lg hover:bg-red-50" data-testid="mfa-disable-btn">Devre Dışı Bırak</button>
        </div>
      ) : setup ? (
        <div className="flex flex-col md:flex-row gap-5 items-start">
          <img src={setup.qr_code} alt="MFA QR" className="w-40 h-40 border rounded" data-testid="mfa-qr" />
          <div className="flex-1">
            <p className="text-sm text-gray-600 mb-1">1. Google Authenticator / Authy ile QR'ı tara.</p>
            <p className="text-xs text-gray-400 mb-2 break-all">Manuel anahtar: <span className="font-mono">{setup.secret}</span></p>
            <p className="text-sm text-gray-600 mb-2">2. Uygulamadaki 6 haneli kodu gir:</p>
            <div className="flex gap-2">
              <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} maxLength={6}
                placeholder="000000" className="inp w-32 text-center tracking-widest" data-testid="mfa-enable-code" />
              <button onClick={enable} disabled={busy || code.length !== 6} className="bg-black text-white px-4 py-2 rounded-lg text-sm disabled:opacity-50" data-testid="mfa-enable-btn">Etkinleştir</button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-600">Amazon DPP için önerilir. Authenticator uygulamasıyla 2 adımlı doğrulama.</p>
          <button onClick={startSetup} disabled={busy} className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-indigo-700 disabled:opacity-50" data-testid="mfa-setup-btn">
            {busy ? "..." : "MFA Kur"}
          </button>
        </div>
      )}

      {/* GİRİŞ DOĞRULAMA KANALI — kişiye özel varsayılan. Girişte kod bu kanaldan gelir;
          giriş ekranından diğer kanal her zaman alternatif olarak seçilebilir. */}
      <div className="mt-4 pt-4 border-t">
        <p className="text-sm font-medium text-gray-800 mb-1">Giriş doğrulama kodu nereye gelsin?</p>
        <p className="text-xs text-gray-500 mb-2">
          Kendi varsayılanınızı seçin. Giriş ekranında diğer seçenek her zaman kullanılabilir.
        </p>
        <div className="flex gap-2 flex-wrap">
          <button type="button" disabled={chBusy || !ch?.sms_available} onClick={() => saveChannel("sms")}
            data-testid="mfa-ch-sms"
            className={`px-3 py-1.5 rounded-lg text-sm border transition-colors disabled:opacity-40
              ${ch?.channel_default === "sms" ? "bg-black text-white border-black" : "border-gray-300 text-gray-700 hover:border-black"}`}>
            SMS{ch?.phone_masked ? ` (${ch.phone_masked})` : ""}
          </button>
          <button type="button" disabled={chBusy || !ch?.email_available} onClick={() => saveChannel("email")}
            data-testid="mfa-ch-email"
            className={`px-3 py-1.5 rounded-lg text-sm border transition-colors disabled:opacity-40
              ${ch?.channel_default === "email" ? "bg-black text-white border-black" : "border-gray-300 text-gray-700 hover:border-black"}`}>
            E-posta{ch?.email_masked ? ` (${ch.email_masked})` : ""}
          </button>
        </div>
        {ch && !ch.sms_available && <p className="text-[11px] text-amber-600 mt-1.5">Hesapta kayıtlı telefon yok — SMS seçilemiyor.</p>}
        {ch && !ch.email_available && <p className="text-[11px] text-amber-600 mt-1.5">Hesapta kayıtlı e-posta yok — e-posta seçilemiyor.</p>}
      </div>

      {/* KURTARMA KODLARI — telefon/uygulama kaybolursa girişte kod yerine kullanılır (tek kullanımlık). */}
      {ch?.recovery_codes_supported && (
        <div className="mt-4 pt-4 border-t" data-testid="mfa-recovery-section">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-2">
            <div>
              <p className="text-sm font-medium text-gray-800 inline-flex items-center gap-2">
                <KeyRound size={14} className="text-indigo-500" /> Kurtarma kodları
              </p>
              <p className="text-xs text-gray-500 mt-1">
                {ch.recovery_codes_total > 0
                  ? `${ch.recovery_codes_remaining} / ${ch.recovery_codes_total} kod kullanılabilir`
                    + (ch.recovery_codes_generated_at
                      ? ` · oluşturma: ${new Date(ch.recovery_codes_generated_at).toLocaleDateString("tr-TR")}` : "")
                  : "Henüz oluşturulmadı. Telefonunuza/uygulamanıza erişemezseniz girişte bu kodlarla doğrulama yapabilirsiniz."}
              </p>
            </div>
            <button type="button" onClick={generateRecovery} disabled={rcBusy} data-testid="mfa-recovery-generate"
              className="shrink-0 border px-3 py-1.5 rounded-lg text-sm hover:border-black disabled:opacity-50">
              {rcBusy ? "..." : (ch.recovery_codes_total > 0 ? "Yeniden Oluştur" : "Kodları Oluştur")}
            </button>
          </div>
          {ch.recovery_codes_total > 0 && ch.recovery_codes_remaining <= 3 && !rcCodes && (
            <p className="text-[11px] text-amber-600 mt-1.5">Az kod kaldı — yeni kodlar oluşturmanız önerilir.</p>
          )}
          {rcCodes && rcCodes.length > 0 && (
            <div className="mt-3 border border-amber-200 bg-amber-50 rounded-lg p-4" data-testid="mfa-recovery-codes">
              <p className="text-xs text-amber-800 mb-3">
                Bu kodlar <b>yalnız bir kez</b> gösterilir. Her biri tek kullanımlıktır. Güvenli bir yere kaydedin.
              </p>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                {rcCodes.map((c) => (
                  <span key={c} className="font-mono text-sm text-center bg-white border rounded px-2 py-1.5">{c}</span>
                ))}
              </div>
              <div className="flex justify-end gap-2 mt-3">
                <button type="button" onClick={copyRecovery}
                  className="inline-flex items-center gap-1.5 border bg-white px-3 py-1.5 rounded-lg text-sm hover:border-black">
                  <Copy size={14} /> Kopyala
                </button>
                <button type="button" onClick={() => setRcCodes(null)} data-testid="mfa-recovery-done"
                  className="bg-black text-white px-3 py-1.5 rounded-lg text-sm">
                  Kaydettim
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, ok }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className={`text-lg font-bold ${ok === true ? "text-green-700" : ok === false ? "text-gray-400" : ""}`}>{value}</div>
    </div>
  );
}
