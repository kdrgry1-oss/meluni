/**
 * EmailMarketing.jsx — AWS SES tabanlı e-posta pazarlama paneli.
 * ================================================================
 * SES ayarları (Zoho'dan AYRI kanal — işlemsel maile dokunmaz) + rıza vermiş
 * bülten abonelerine toplu kampanya gönderimi + geçmiş. Alıcılar backend'de
 * yalnız active + consent olanlardan seçilir; her maile abonelikten-çık linki eklenir.
 */
import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Mail, Send, Save, Users, RefreshCw, CheckCircle2, AlertTriangle } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const h = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

export default function EmailMarketing() {
  const [cfg, setCfg] = useState({ enabled: false, region: "", access_key: "", secret_key: "", from_email: "", from_name: "", configuration_set: "" });
  const [configured, setConfigured] = useState(false);
  const [aud, setAud] = useState({ total: 0, eligible: 0 });
  const [testTo, setTestTo] = useState("");
  const [camp, setCamp] = useState({ subject: "", html: "" });
  const [campaigns, setCampaigns] = useState([]);
  const [busy, setBusy] = useState("");

  const load = async () => {
    try {
      const [s, a, c] = await Promise.all([
        axios.get(`${API}/admin/email-marketing/settings`, { headers: h() }),
        axios.get(`${API}/admin/email-marketing/audience`, { headers: h() }),
        axios.get(`${API}/admin/email-marketing/campaigns`, { headers: h() }),
      ]);
      setCfg((p) => ({ ...p, ...s.data }));
      setConfigured(!!s.data.configured);
      setAud(a.data || { total: 0, eligible: 0 });
      setCampaigns(c.data?.campaigns || []);
    } catch (e) { toast.error("Yüklenemedi"); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const saveCfg = async () => {
    setBusy("save");
    try {
      await axios.put(`${API}/admin/email-marketing/settings`, cfg, { headers: h() });
      toast.success("SES ayarları kaydedildi");
      load();
    } catch (e) { toast.error(e.response?.data?.detail || "Kaydedilemedi"); }
    finally { setBusy(""); }
  };

  const sendTest = async () => {
    if (!testTo.trim()) { toast.error("Test e-posta adresi girin"); return; }
    setBusy("test");
    try {
      const r = await axios.post(`${API}/admin/email-marketing/test`, { to: testTo.trim() }, { headers: h() });
      toast.success("Test maili gönderildi");
    } catch (e) { toast.error(e.response?.data?.detail || "Gönderilemedi"); }
    finally { setBusy(""); }
  };

  const sendCampaign = async () => {
    if (!camp.subject.trim() || !camp.html.trim()) { toast.error("Konu ve içerik zorunlu"); return; }
    if (!window.confirm(`${aud.eligible} rıza vermiş aboneye "${camp.subject}" kampanyası gönderilecek. Onaylıyor musun?`)) return;
    setBusy("campaign");
    try {
      const r = await axios.post(`${API}/admin/email-marketing/campaigns`, camp, { headers: h() });
      toast.success(`Kampanya başlatıldı — ${r.data?.eligible} aboneye gönderiliyor (arka planda)`);
      setCamp({ subject: "", html: "" });
      setTimeout(load, 1500);
    } catch (e) { toast.error(e.response?.data?.detail || "Başlatılamadı"); }
    finally { setBusy(""); }
  };

  const field = (label, key, type = "text", ph = "") => (
    <label className="block">
      <span className="text-xs font-medium text-gray-600">{label}</span>
      <input type={type} value={cfg[key] || ""} placeholder={ph}
        onChange={(e) => setCfg((p) => ({ ...p, [key]: e.target.value }))}
        className="mt-1 w-full border rounded-lg px-3 py-2 text-sm" />
    </label>
  );

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-center gap-2">
        <Mail size={22} /><h1 className="text-xl font-bold">E-posta Pazarlama (AWS SES)</h1>
      </div>

      {/* Kitle */}
      <div className="bg-white border rounded-xl p-4 flex items-center gap-4">
        <Users size={20} className="text-gray-500" />
        <div className="flex-1">
          <p className="text-sm font-semibold">{aud.eligible} rıza vermiş aktif abone</p>
          <p className="text-xs text-gray-500">Toplam {aud.total} kayıt · yalnız KVKK/İYS onaylı ve aktif olanlara gönderilir</p>
        </div>
        <span className={`text-xs px-2 py-1 rounded-full font-semibold ${configured ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-700"}`}>
          {configured ? "SES hazır" : "SES ayarı eksik"}
        </span>
      </div>

      {/* SES Ayarları */}
      <div className="bg-white border rounded-xl p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-sm">SES Ayarları</h2>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={!!cfg.enabled} onChange={(e) => setCfg((p) => ({ ...p, enabled: e.target.checked }))} className="w-4 h-4 accent-black" />
            Aktif
          </label>
        </div>
        <div className="grid md:grid-cols-2 gap-3">
          {field("AWS Bölgesi", "region", "text", "eu-west-1")}
          {field("Gönderen adresi (doğrulanmış)", "from_email", "text", "club@facette.com.tr")}
          {field("IAM Access Key ID", "access_key")}
          {field("IAM Secret Access Key", "secret_key", "password", "••••••")}
          {field("Gönderen adı", "from_name", "text", "Facette")}
          {/* Yanıt adresi: görünen gönderen gerçek bir posta kutusu olmayabilir
              (club@... yalnız gönderim için). Müşteri yanıtı kaybolmasın diye
              okunan bir adrese yönlendirilir. */}
          {field("Yanıt adresi (ops.)", "reply_to", "text", "info@facette.com.tr")}
          {field("Configuration Set (ops.)", "configuration_set", "text", "açılma/bounce takibi")}
        </div>
        <div className="flex flex-wrap items-center gap-3 pt-2">
          <button onClick={saveCfg} disabled={busy === "save"} className="inline-flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50">
            <Save size={15} /> Kaydet
          </button>
          <div className="flex items-center gap-2">
            <input value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder="test@ornek.com"
              className="border rounded-lg px-3 py-2 text-sm" />
            <button onClick={sendTest} disabled={busy === "test"} className="inline-flex items-center gap-2 border px-4 py-2 rounded-lg text-sm font-semibold hover:bg-gray-50 disabled:opacity-50">
              <Send size={15} /> Test gönder
            </button>
          </div>
        </div>
        <p className="text-[11px] text-gray-400 leading-relaxed">
          İşlemsel e-postalar (sipariş/şifre) Zoho'dan gitmeye devam eder — burası yalnız pazarlama kanalıdır.
          SES ilk açılışta "sandbox" modundadır; toplu gönderim için AWS'den "production access" onayı alınmalıdır.
        </p>
      </div>

      {/* Kampanya oluştur */}
      <div className="bg-white border rounded-xl p-4 space-y-3">
        <h2 className="font-semibold text-sm">Yeni Kampanya</h2>
        <input value={camp.subject} onChange={(e) => setCamp((p) => ({ ...p, subject: e.target.value }))}
          placeholder="Konu (ör. Yeni Sezon %20 İndirim Başladı)" className="w-full border rounded-lg px-3 py-2 text-sm" />
        <textarea value={camp.html} onChange={(e) => setCamp((p) => ({ ...p, html: e.target.value }))}
          placeholder="HTML içerik — <h2>Merhaba</h2><p>...</p> (marka şablonu + abonelikten-çık linki otomatik eklenir)"
          rows={8} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" />
        <div className="flex justify-end">
          <button onClick={sendCampaign} disabled={busy === "campaign" || !configured} className="inline-flex items-center gap-2 bg-black text-white px-5 py-2 rounded-lg text-sm font-bold hover:bg-gray-800 disabled:opacity-50">
            <Send size={15} /> {aud.eligible} aboneye gönder
          </button>
        </div>
      </div>

      {/* Geçmiş */}
      <div className="bg-white border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-sm">Kampanya Geçmişi</h2>
          <button onClick={load} className="text-gray-400 hover:text-black"><RefreshCw size={16} /></button>
        </div>
        {campaigns.length === 0 ? (
          <p className="text-sm text-gray-400">Henüz kampanya yok.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-gray-500 border-b">
                <th className="py-2">Konu</th><th>Durum</th><th className="text-right">Gönderildi</th><th className="text-right">Hata</th><th className="text-right">Toplam</th><th className="text-right">Tarih</th>
              </tr></thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="py-2 pr-2">{c.subject}</td>
                    <td>
                      <span className={`inline-flex items-center gap-1 text-xs font-semibold ${c.status === "sent" ? "text-green-600" : c.status === "sending" ? "text-blue-600" : c.status === "failed" ? "text-red-600" : "text-gray-500"}`}>
                        {c.status === "sent" ? <CheckCircle2 size={13} /> : c.status === "failed" ? <AlertTriangle size={13} /> : null}
                        {c.status}
                      </span>
                    </td>
                    <td className="text-right">{c.sent || 0}</td>
                    <td className="text-right text-red-500">{c.failed || 0}</td>
                    <td className="text-right">{c.total || 0}</td>
                    <td className="text-right text-xs text-gray-400">{c.created_at ? new Date(c.created_at).toLocaleString("tr-TR") : ""}</td>
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
