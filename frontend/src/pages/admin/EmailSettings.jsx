import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Mail, Save, Send, KeyRound } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * E-posta (Zoho ZeptoMail) Ayarları
 * ---------------------------------
 * Railway giden SMTP portlarını engellediği için e-posta gönderimi
 * ZeptoMail HTTPS API'si (443) üzerinden yapılır.
 * Backend sözleşmesi: settings.id="email_smtp"
 *   username = gönderen e-posta, password = ZeptoMail Send Mail Token,
 *   host     = api.zeptomail.com | api.zeptomail.eu (bölge)
 */
export default function EmailSettings() {
  const [cfg, setCfg] = useState({ enabled: true, username: "", from_name: "FACETTE",
    order_from_email: "", order_from_name: "", order_reply_to: "" });
  const [region, setRegion] = useState("com"); // com | eu
  const [token, setToken] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testTo, setTestTo] = useState("");
  const [testing, setTesting] = useState(false);
  const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

  useEffect(() => {
    (async () => {
      try {
        const res = await axios.get(`${API}/settings/email-smtp`, auth());
        const d = res.data || {};
        setCfg({
          enabled: d.enabled !== false,
          username: d.username || "",
          from_name: d.from_name || "FACETTE",
          order_from_email: d.order_from_email || "",
          order_from_name: d.order_from_name || "",
          order_reply_to: d.order_reply_to || "",
        });
        setRegion(((d.host || "").toLowerCase().includes("eu")) ? "eu" : "com");
        setTokenSet(!!d.password_set);
      } catch (e) {
        toast.error("Ayarlar yüklenemedi");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const save = async () => {
    if (!cfg.username.trim()) { toast.error("Gönderen e-posta adresi gerekli"); return; }
    if (!tokenSet && !token.trim()) { toast.error("ZeptoMail Send Mail Token gerekli"); return; }
    try {
      setSaving(true);
      const payload = {
        enabled: cfg.enabled,
        username: cfg.username.trim(),
        from_name: cfg.from_name,
        order_from_email: (cfg.order_from_email || "").trim(),
        order_from_name: (cfg.order_from_name || "").trim(),
        order_reply_to: (cfg.order_reply_to || "").trim(),
        host: region === "eu" ? "api.zeptomail.eu" : "api.zeptomail.com",
      };
      if (token.trim()) payload.password = token.trim();
      await axios.post(`${API}/settings/email-smtp`, payload, auth());
      toast.success("E-posta ayarları kaydedildi");
      if (token.trim()) { setTokenSet(true); setToken(""); }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  const sendTest = async () => {
    if (!testTo.trim()) { toast.error("Test için alıcı e-posta girin"); return; }
    try {
      setTesting(true);
      const r = await axios.post(`${API}/settings/email-smtp/test`, { to: testTo.trim() }, { ...auth(), timeout: 35000 });
      if (r.data?.success) {
        toast.success("Test e-postası gönderildi — gelen kutunu kontrol et");
      } else {
        // ZeptoMail'in GERÇEK hata metni (Cloudflare 5xx maskesini aşmak için 200+body geldi).
        toast.error(`ZeptoMail hatası [${r.data?.endpoint || "?"}]: ${r.data?.error || "bilinmeyen"}`, { duration: 20000 });
      }
    } catch (e) {
      toast.error("İstek başarısız: " + (e.response?.data?.detail || e.message || "sunucuya ulaşılamadı"), { duration: 15000 });
    } finally {
      setTesting(false);
    }
  };

  const field = "w-full border rounded-lg px-3 py-2 text-sm focus:border-stone-900 outline-none";
  const lbl = "block text-[11px] font-bold text-gray-500 uppercase mb-1";

  return (
    <div data-testid="admin-email-settings" className="max-w-2xl p-4 md:p-6">
      <div className="flex items-start justify-between mb-2 gap-4">
        <div className="flex items-center gap-2">
          <Mail size={22} className="text-gray-600" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900">E-posta (Zoho ZeptoMail)</h1>
            <p className="text-sm text-gray-500 mt-1">Tüm bildirim ve toplu e-postalar ZeptoMail API ile bu adresten gönderilir.</p>
          </div>
        </div>
        <button onClick={save} disabled={saving}
          className="inline-flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50 shrink-0">
          <Save size={16} /> {saving ? "Kaydediliyor…" : "Kaydet"}
        </button>
      </div>

      {loading ? (
        <div className="p-10 text-center text-gray-400">Yükleniyor…</div>
      ) : (
        <div className="space-y-4">
          <div className="bg-white border rounded-xl p-5 shadow-sm space-y-4">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} className="accent-black" />
              E-posta gönderimi aktif
            </label>

            <div>
              <label className={lbl}>Gönderen E-posta Adresi</label>
              <input className={field} placeholder="info@facette.com.tr" value={cfg.username}
                onChange={(e) => setCfg({ ...cfg, username: e.target.value.trim() })} autoComplete="off" />
              <p className="text-[11px] text-gray-400 mt-1">ZeptoMail'de doğrulanmış domain altında bir adres olmalı (facette.com.tr).</p>
            </div>

            <div>
              <label className={lbl}>ZeptoMail Send Mail Token {tokenSet && <span className="text-green-600 normal-case font-normal">· kayıtlı (değiştirmek için yenisini yapıştır)</span>}</label>
              <div className="relative">
                <KeyRound size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input type="password" className={`${field} pl-9`} placeholder={tokenSet ? "••••••• (değiştirmek için yapıştır)" : "Zoho-enczapikey ... veya ham token"}
                  value={token} onChange={(e) => setToken(e.target.value)} autoComplete="new-password" />
              </div>
              <p className="text-[11px] text-gray-400 mt-1">ZeptoMail → Mail Agent → <b>Setup Info / Send Mail Token</b> bölümünden alınır. Başında "Zoho-enczapikey" olsa da olmasa da çalışır.</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={lbl}>Bölge</label>
                <select className={field} value={region} onChange={(e) => setRegion(e.target.value)}>
                  <option value="com">Global (api.zeptomail.com)</option>
                  <option value="eu">Avrupa (api.zeptomail.eu)</option>
                </select>
              </div>
              <div>
                <label className={lbl}>Gönderen Adı</label>
                <input className={field} value={cfg.from_name} onChange={(e) => setCfg({ ...cfg, from_name: e.target.value })} placeholder="FACETTE" />
              </div>
            </div>
            <p className="text-[11px] text-gray-400">
              ZeptoMail hesabını <code>zeptomail.zoho.eu</code> üzerinden açtıysan Avrupa'yı seç; aksi halde Global doğrudur.
            </p>
          </div>

          {/* Sipariş maillerinin AYRI gönderen kimliği. Boş bırakılırsa sipariş mailleri
              de yukarıdaki varsayılan gönderenden gider (davranış değişmez). */}
          <div className="bg-white border rounded-xl p-5">
            <div className="text-sm font-semibold mb-1">Sipariş Maili Göndereni (opsiyonel)</div>
            <p className="text-[11px] text-gray-500 mb-3 leading-relaxed">
              Sipariş yaşam-döngüsü mailleri (onay, kargo, teslim, iptal, iade, havale) bu adresten gider.
              Şifre sıfırlama, üyelik ve pazarlama mailleri <b>etkilenmez</b>. Boş bırakırsan sipariş
              mailleri de yukarıdaki adresten gider. <br/>
              <b>Not:</b> Adres doğrulanmış domaine ait olmalı (<code>@facette.com.tr</code> zaten doğrulu).
              Yanıt adresini takip ettiğin bir kutuya (ör. <code>info@…</code>) ayarla — müşteri sipariş
              mailine cevap yazarsa oraya düşer.
            </p>
            <div className="grid md:grid-cols-3 gap-4">
              <div>
                <label className={lbl}>Sipariş Gönderen E-posta</label>
                <input className={field} placeholder="siparis@facette.com.tr" autoComplete="off"
                  value={cfg.order_from_email}
                  onChange={(e) => setCfg({ ...cfg, order_from_email: e.target.value.trim() })} />
              </div>
              <div>
                <label className={lbl}>Sipariş Gönderen Adı</label>
                <input className={field} placeholder="FACETTE Sipariş"
                  value={cfg.order_from_name}
                  onChange={(e) => setCfg({ ...cfg, order_from_name: e.target.value })} />
              </div>
              <div>
                <label className={lbl}>Sipariş Yanıt Adresi (Reply-To)</label>
                <input className={field} placeholder="info@facette.com.tr" autoComplete="off"
                  value={cfg.order_reply_to}
                  onChange={(e) => setCfg({ ...cfg, order_reply_to: e.target.value.trim() })} />
              </div>
            </div>
          </div>

          <div className="bg-gray-50 border rounded-xl p-5">
            <div className="text-sm font-medium mb-2">Test E-postası Gönder</div>
            <div className="flex flex-wrap items-center gap-2">
              <input className={`${field} flex-1 min-w-[220px]`} placeholder="ornek@mail.com" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
              <button onClick={sendTest} disabled={testing}
                className="inline-flex items-center gap-2 px-4 py-2 border rounded-lg text-sm hover:bg-gray-100 disabled:opacity-50">
                <Send size={15} /> {testing ? "Gönderiliyor…" : "Test Gönder"}
              </button>
            </div>
            <p className="text-[11px] text-gray-400 mt-2">Önce kaydet, sonra test et. Hata olursa ZeptoMail'in tam yanıtı burada bildirim olarak görünür.</p>
          </div>
        </div>
      )}
    </div>
  );
}
