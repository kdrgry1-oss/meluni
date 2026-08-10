/**
 * IysAdmin.jsx — İYS (İleti Yönetim Sistemi) yönetim paneli
 *
 * İYS, NetGSM İş Ortağı API'si üzerinden kullanılır — doğrudan İYS API kimliği GEREKMEZ.
 * NetGSM kullanıcı adı/şifresi Ayarlar → Bildirimler → Netgsm'den okunur;
 * bu sayfadan yalnızca İYS Marka Kodu (+ varsa appkey) tanımlanır.
 *
 * Backend: /api/admin/iys/{status,settings,test-connection,query,query-batch,register}
 * Not: İzin kaynağı (source) İYS'nin HS_* değerlerinden biri OLMALIDIR.
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  ShieldCheck, ShieldAlert, RefreshCw, Search, UserPlus, Save,
  CheckCircle2, XCircle, Wifi, ListChecks,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const MESSAGE_TYPES = [
  { v: "MESAJ", l: "SMS (MESAJ)" },
  { v: "EPOSTA", l: "E-Posta (EPOSTA)" },
  { v: "ARAMA", l: "Arama (ARAMA)" },
];
const SOURCES = [
  "HS_WEB", "HS_FIZIKSEL_ORTAM", "HS_ISLAK_IMZA", "HS_CAGRI_MERKEZI",
  "HS_SOSYAL_MEDYA", "HS_EPOSTA", "HS_MESAJ", "HS_MOBIL", "HS_EORTAM",
  "HS_ETKINLIK", "HS_2015", "HS_ATM", "HS_KARAR",
];

const statusBadge = (s) => {
  if (s === "ONAY") return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700"><CheckCircle2 size={12} /> ONAY</span>;
  if (s === "RET") return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-700"><XCircle size={12} /> RET</span>;
  if (s === "KAYIT_YOK") return <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-700">KAYIT YOK</span>;
  return <span className="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">{s || "?"}</span>;
};

export default function IysAdmin() {
  const token = useMemo(() => localStorage.getItem("token"), []);
  const auth = useMemo(() => ({ headers: { Authorization: `Bearer ${token}` } }), [token]);

  const [status, setStatus] = useState(null);
  const [brandCode, setBrandCode] = useState("");
  const [appkey, setAppkey] = useState("");
  const [savingBrand, setSavingBrand] = useState(false);
  const [testing, setTesting] = useState(false);

  // Tek sorgu
  const [q, setQ] = useState({ recipient: "", recipient_type: "BIREYSEL", message_type: "MESAJ" });
  const [qLoading, setQLoading] = useState(false);
  const [qResult, setQResult] = useState(null);

  // İzin ekleme (ARAMA hariç — backend 400 döner)
  const [reg, setReg] = useState({ recipient: "", recipient_type: "BIREYSEL", message_type: "MESAJ", status: "ONAY", source: "HS_WEB" });
  const [regLoading, setRegLoading] = useState(false);

  // Toplu sorgu
  const [batchText, setBatchText] = useState("");
  const [batchType, setBatchType] = useState("MESAJ");
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchResult, setBatchResult] = useState(null);

  const loadStatus = async () => {
    try {
      const [s, cfg] = await Promise.all([
        axios.get(`${API}/admin/iys/status`, auth),
        axios.get(`${API}/admin/iys/settings`, auth),
      ]);
      setStatus(s.data);
      setBrandCode(cfg.data.brand_code || "");
      setAppkey(cfg.data.appkey || "");
    } catch (e) {
      toast.error("İYS durumu alınamadı: " + (e.response?.data?.detail || e.message));
    }
  };
  useEffect(() => { loadStatus(); /* eslint-disable-next-line */ }, []);

  const saveBrand = async () => {
    if (!brandCode.trim()) return toast.error("Marka kodu boş olamaz");
    setSavingBrand(true);
    try {
      await axios.post(`${API}/admin/iys/settings`,
        { brand_code: brandCode.trim(), appkey: appkey.trim() }, auth);
      toast.success("İYS ayarları kaydedildi");
      loadStatus();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally { setSavingBrand(false); }
  };

  const testConn = async () => {
    setTesting(true);
    try {
      const r = await axios.post(`${API}/admin/iys/test-connection`, {}, auth);
      if (r.data.ok) toast.success(r.data.message);
      else {
        const detail = r.data.attempts
          ? " · " + r.data.attempts.map(a => `${a.mode}→${a.code || a.error}`).join(", ")
          : "";
        toast.error((r.data.message || "Bağlantı başarısız") + detail, { duration: 12000 });
        console.log("İYS test attempts:", r.data.attempts);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally { setTesting(false); }
  };

  const runQuery = async () => {
    if (!q.recipient.trim()) return toast.error("Telefon veya e-posta girin");
    setQLoading(true); setQResult(null);
    try {
      const r = await axios.post(`${API}/admin/iys/query`, q, auth);
      setQResult(r.data);
      if (r.data.source === "not_configured" || r.data.source === "error") toast.error(r.data.message || "Sorgu hatası");
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally { setQLoading(false); }
  };

  const runRegister = async () => {
    if (!reg.recipient.trim()) return toast.error("Telefon veya e-posta girin");
    setRegLoading(true);
    try {
      const r = await axios.post(`${API}/admin/iys/register`, reg, auth);
      r.data.ok
        ? toast.success("İzin kaydedildi ve NetGSM İYS'ye bildirildi")
        : toast.error(r.data.message || "İzin eklenemedi — Teşhis için /api/iys/diagnostics'e bakın");
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally { setRegLoading(false); }
  };

  const runBatch = async () => {
    const recipients = batchText.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    if (!recipients.length) return toast.error("En az bir adres girin");
    if (recipients.length > 50) return toast.error("Tek seferde max 50 adres");
    setBatchLoading(true); setBatchResult(null);
    try {
      const payload = recipients.map(rec => ({ recipient: rec, recipient_type: "BIREYSEL", message_type: batchType }));
      const r = await axios.post(`${API}/admin/iys/query-batch`, payload, auth);
      setBatchResult(r.data);
      toast.success(`${r.data.compliant}/${r.data.total} adres izinli (ONAY)`);
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally { setBatchLoading(false); }
  };

  const inputCls = "w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black/10";
  const selectCls = inputCls;
  const cardCls = "bg-white rounded-xl border shadow-sm p-5";

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <ShieldCheck className="text-emerald-600" /> İYS — İleti Yönetim Sistemi
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            NetGSM İş Ortağı üzerinden izin sorgulama ve yükleme. Ticari SMS/e-posta öncesi ONAY kontrolü yasal zorunluluktur.
            Checkout izinleri otomatik bildirilir; bu sayfa elle sorgu/ekleme ve kampanya öncesi toplu kontrol içindir.
          </p>
        </div>
        <button onClick={loadStatus} className="inline-flex items-center gap-2 px-3 py-2 text-sm border rounded-lg hover:bg-gray-50">
          <RefreshCw size={15} /> Yenile
        </button>
      </div>

      {/* Durum + Ayarlar */}
      <div className="grid md:grid-cols-2 gap-4">
        <div className={cardCls}>
          <h2 className="font-medium mb-3 flex items-center gap-2"><Wifi size={16} /> Bağlantı Durumu</h2>
          {status ? (
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                {status.configured
                  ? <span className="inline-flex items-center gap-1 text-emerald-700"><CheckCircle2 size={16} /> Yapılandırma tamam</span>
                  : <span className="inline-flex items-center gap-1 text-red-600"><ShieldAlert size={16} /> Yapılandırma eksik</span>}
              </div>
              <div className="text-gray-600">Sağlayıcı: <b>NetGSM İYS İş Ortağı</b></div>
              <div className="text-gray-600">
                NetGSM kullanıcı: {status.username_set ? "✓" : "✗"} · Şifre: {status.password_set ? "✓" : "✗"} · appkey: {status.appkey_set ? "✓" : "—"}
              </div>
              <div className="text-gray-600">Marka Kodu: <b>{status.brand_code}</b></div>
              {status.hint && <div className="text-amber-600 text-xs bg-amber-50 rounded p-2 mt-2">{status.hint}</div>}
              <button onClick={testConn} disabled={testing}
                className="mt-2 inline-flex items-center gap-2 px-3 py-2 text-sm bg-black text-white rounded-lg hover:bg-gray-800 disabled:opacity-50">
                {testing ? <RefreshCw size={14} className="animate-spin" /> : <Wifi size={14} />} Bağlantıyı Test Et
              </button>
            </div>
          ) : <div className="text-sm text-gray-400">Yükleniyor…</div>}
        </div>

        <div className={cardCls}>
          <h2 className="font-medium mb-3 flex items-center gap-2"><Save size={16} /> İYS Ayarları</h2>
          <p className="text-xs text-gray-500 mb-3">
            Marka kodu, İYS panelinde markanıza atanan koddur (NetGSM portalında <b>NetİYS</b> altında da görünür).
            Kaydedilen değerler <b>providers.netgsm</b> bloğuna yazılır — checkout bildirimi de aynı yerden okur.
          </p>
          <div className="space-y-2">
            <input value={brandCode} onChange={e => setBrandCode(e.target.value)}
              placeholder="İYS Marka Kodu — örn: 754607" className={inputCls} />
            <input value={appkey} onChange={e => setAppkey(e.target.value)}
              placeholder="NetGSM appkey (opsiyonel)" className={inputCls} />
            <button onClick={saveBrand} disabled={savingBrand}
              className="w-full px-4 py-2 text-sm bg-black text-white rounded-lg hover:bg-gray-800 disabled:opacity-50">
              {savingBrand ? "Kaydediliyor…" : "Kaydet"}
            </button>
          </div>
        </div>
      </div>

      {/* Tek sorgu + İzin ekleme */}
      <div className="grid md:grid-cols-2 gap-4">
        <div className={cardCls}>
          <h2 className="font-medium mb-3 flex items-center gap-2"><Search size={16} /> İzin Sorgula</h2>
          <div className="space-y-3">
            <input value={q.recipient} onChange={e => setQ({ ...q, recipient: e.target.value })}
              placeholder="+905XXXXXXXXX veya eposta@ornek.com" className={inputCls} />
            <div className="grid grid-cols-2 gap-2">
              <select value={q.message_type} onChange={e => setQ({ ...q, message_type: e.target.value })} className={selectCls}>
                {MESSAGE_TYPES.map(m => <option key={m.v} value={m.v}>{m.l}</option>)}
              </select>
              <select value={q.recipient_type} onChange={e => setQ({ ...q, recipient_type: e.target.value })} className={selectCls}>
                <option value="BIREYSEL">Bireysel</option>
                <option value="TACIR">Tacir</option>
              </select>
            </div>
            <button onClick={runQuery} disabled={qLoading}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 text-sm bg-black text-white rounded-lg hover:bg-gray-800 disabled:opacity-50">
              {qLoading ? <RefreshCw size={14} className="animate-spin" /> : <Search size={14} />} Sorgula
            </button>
            {qResult && (
              <div className="text-sm bg-gray-50 rounded-lg p-3 space-y-1">
                <div className="flex items-center gap-2">Durum: {statusBadge(qResult.status)}
                  <span className="text-xs text-gray-400">({qResult.source})</span></div>
                {qResult.consent_source && <div className="text-gray-600 text-xs">Kaynak: {qResult.consent_source}</div>}
                {qResult.consent_date && <div className="text-gray-600 text-xs">İzin Tarihi: {qResult.consent_date}</div>}
                {qResult.message && <div className="text-amber-600 text-xs">{qResult.message}</div>}
              </div>
            )}
          </div>
        </div>

        <div className={cardCls}>
          <h2 className="font-medium mb-3 flex items-center gap-2"><UserPlus size={16} /> İzin Ekle / İptal Et</h2>
          <div className="space-y-3">
            <input value={reg.recipient} onChange={e => setReg({ ...reg, recipient: e.target.value })}
              placeholder="+905XXXXXXXXX veya eposta@ornek.com" className={inputCls} />
            <div className="grid grid-cols-2 gap-2">
              <select value={reg.message_type} onChange={e => setReg({ ...reg, message_type: e.target.value })} className={selectCls}>
                <option value="MESAJ">SMS (MESAJ)</option>
                <option value="EPOSTA">E-Posta (EPOSTA)</option>
              </select>
              <select value={reg.recipient_type} onChange={e => setReg({ ...reg, recipient_type: e.target.value })} className={selectCls}>
                <option value="BIREYSEL">Bireysel</option>
                <option value="TACIR">Tacir</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <select value={reg.status} onChange={e => setReg({ ...reg, status: e.target.value })} className={selectCls}>
                <option value="ONAY">ONAY (izin ver)</option>
                <option value="RET">RET (iptal et)</option>
              </select>
              <select value={reg.source} onChange={e => setReg({ ...reg, source: e.target.value })} className={selectCls}>
                {SOURCES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <button onClick={runRegister} disabled={regLoading}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50">
              {regLoading ? <RefreshCw size={14} className="animate-spin" /> : <UserPlus size={14} />} İYS'ye Gönder
            </button>
            <p className="text-xs text-gray-400">
              İzin denetim izine (iys_consents) kaydedilir ve NetGSM İYS'ye bildirilir.
              Kaynak (source) izni gerçekten aldığınız kanalı yansıtmalıdır — yanlış beyan yasal risktir.
            </p>
          </div>
        </div>
      </div>

      {/* Toplu sorgu */}
      <div className={cardCls}>
        <h2 className="font-medium mb-3 flex items-center gap-2"><ListChecks size={16} /> Toplu İzin Kontrolü <span className="text-xs font-normal text-gray-400">(kampanya öncesi, max 50)</span></h2>
        <div className="grid md:grid-cols-3 gap-3">
          <div className="md:col-span-2">
            <textarea value={batchText} onChange={e => setBatchText(e.target.value)} rows={5}
              placeholder={"Her satıra bir adres:\n+905351234567\n05421234567\neposta@ornek.com"}
              className={inputCls + " font-mono text-xs"} />
          </div>
          <div className="space-y-2">
            <select value={batchType} onChange={e => setBatchType(e.target.value)} className={selectCls}>
              {MESSAGE_TYPES.map(m => <option key={m.v} value={m.v}>{m.l}</option>)}
            </select>
            <button onClick={runBatch} disabled={batchLoading}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 text-sm bg-black text-white rounded-lg hover:bg-gray-800 disabled:opacity-50">
              {batchLoading ? <RefreshCw size={14} className="animate-spin" /> : <ListChecks size={14} />} Kontrol Et
            </button>
            {batchResult && (
              <div className="text-sm bg-gray-50 rounded-lg p-3">
                <b className="text-emerald-700">{batchResult.compliant}</b> izinli ·{" "}
                <b className="text-red-600">{batchResult.non_compliant}</b> izinsiz / bilinmiyor
              </div>
            )}
          </div>
        </div>
        {batchResult?.items?.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b">
                  <th className="py-2 pr-4">Adres</th><th className="py-2 pr-4">Durum</th>
                  <th className="py-2 pr-4">Kaynak</th><th className="py-2">İzin Tarihi</th>
                </tr>
              </thead>
              <tbody>
                {batchResult.items.map((it, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="py-2 pr-4 font-mono text-xs">{it.recipient}</td>
                    <td className="py-2 pr-4">{statusBadge(it.status)}</td>
                    <td className="py-2 pr-4 text-xs text-gray-500">{it.consent_source || "—"}</td>
                    <td className="py-2 text-xs text-gray-500">{it.consent_date || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
