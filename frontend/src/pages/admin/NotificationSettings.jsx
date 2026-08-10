/**
 * =============================================================================
 * NotificationSettings.jsx — Bildirim sağlayıcı ayarları
 * =============================================================================
 * SMS sağlayıcıları (Netgsm, İletiMerkezi, Twilio, VatanSMS, Verimor...)
 * WhatsApp Meta Cloud API credential
 * E-posta (Resend - mevcut) aktif/pasif
 *
 * Endpoint'ler:
 *   GET /api/notifications/providers/catalog  — SMS sağlayıcı listesi
 *   GET /api/notifications/providers          — mevcut config
 *   POST /api/notifications/providers         — kaydet
 *   POST /api/notifications/test              — test gönder
 * =============================================================================
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, Send, MessageSquare, Mail, Phone } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const FIELD_MAP = {
  netgsm:       [["username","Kullanıcı Adı"],["password","Şifre"],["header","Başlık (Sender)"]],
  iletimerkezi: [["api_key","API Key"],["api_hash","API Hash"],["header","Başlık (Sender)"]],
  twilio:       [["account_sid","Account SID"],["auth_token","Auth Token"],["from_number","Kaynak Numara (+905...)"]],
  vatansms:     [["api_id","API ID"],["api_key","API Key"],["header","Başlık (Sender)"]],
  verimor:      [["username","Kullanıcı Adı"],["password","Şifre"],["header","Başlık"]],
  mutlucep:     [["username","Kullanıcı"],["password","Şifre"],["header","Başlık"]],
  mobildev:     [["username","Kullanıcı"],["password","Şifre"],["header","Başlık"]],
  "postagüvercini":[["username","Kullanıcı"],["password","Şifre"],["header","Başlık"]],
};

export default function NotificationSettings() {
  const [catalog, setCatalog] = useState({ sms_providers: [], events: [] });
  const [cfg, setCfg] = useState({
    sms_active: null, whatsapp_active: false, email_active: true, providers: {},
  });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testChannel, setTestChannel] = useState("sms");
  const [testTo, setTestTo] = useState("");
  const [testMsg, setTestMsg] = useState("Facette test ✓");
  const [pushTitle, setPushTitle] = useState("");
  const [pushBody, setPushBody] = useState("");
  const [pushSending, setPushSending] = useState(false);

  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  useEffect(() => {
    axios.get(`${API}/notifications/providers/catalog`, auth).then(r => setCatalog(r.data));
    axios.get(`${API}/notifications/providers`, auth).then(r => {
      const d = r.data || {};
      setCfg({
        sms_active: d.sms_active || null,
        whatsapp_active: !!d.whatsapp_active,
        email_active: d.email_active !== false,
        providers: d.providers || {},
      });
    });
    // eslint-disable-next-line
  }, []);

  const setProvField = (provKey, field, val) => {
    setCfg(p => ({
      ...p,
      providers: { ...p.providers, [provKey]: { ...(p.providers[provKey] || {}), [field]: val } },
    }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await axios.post(`${API}/notifications/providers`, cfg, auth);
      toast.success("Bildirim sağlayıcı ayarları kaydedildi");
    } catch (e) {
      toast.error("Kaydedilemedi: " + (e?.response?.data?.detail || e.message));
    } finally { setSaving(false); }
  };

  const handleTest = async () => {
    if (!testTo) { toast.error("Test için numara/mail gerekli"); return; }
    setTesting(true);
    try {
      const r = await axios.post(`${API}/notifications/test`, {
        channel: testChannel,
        provider_key: testChannel === "sms" ? cfg.sms_active : null,
        to: testTo,
        message: testMsg,
      }, auth);
      if (r.data.success) toast.success("Test başarılı: " + (r.data.response || "").slice(0, 160));
      else toast.error("Test başarısız: " + (r.data.response || "").slice(0, 200));
    } catch (e) {
      toast.error("Hata: " + (e?.response?.data?.detail || e.message));
    } finally { setTesting(false); }
  };

  const handlePushSend = async () => {
    if (!pushTitle.trim()) { toast.error("Başlık gerekli"); return; }
    setPushSending(true);
    try {
      const r = await axios.post(`${API}/push/send`, { title: pushTitle, body: pushBody }, auth);
      if (r.data.success) toast.success(`Push gönderildi (${r.data.sent} cihaz)`);
      else toast.error("Gönderilemedi: " + (r.data.error || ""));
    } catch (e) {
      toast.error("Hata: " + (e?.response?.data?.detail || e.message));
    } finally { setPushSending(false); }
  };

  const handleTestOrder = async () => {
    setPushSending(true);
    try {
      const r = await axios.post(`${API}/push/test-order`, {}, auth);
      if (r.data.success) toast.success(`Sahte sipariş bildirimi gönderildi (${r.data.sent} cihaz)`);
      else toast.error("Gönderilemedi");
    } catch (e) {
      toast.error("Hata: " + (e?.response?.data?.detail || e.message));
    } finally { setPushSending(false); }
  };

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6" data-testid="notification-settings-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Bildirim Ayarları</h1>
          <p className="text-sm text-gray-500 mt-1">SMS, E-posta ve WhatsApp kanalları için sağlayıcı kimlik bilgileri.</p>
        </div>
        <button onClick={handleSave} disabled={saving}
          className="inline-flex items-center gap-2 bg-black text-white px-4 py-2 rounded text-sm disabled:opacity-60"
          data-testid="save-notification-providers">
          <Save size={16} /> {saving ? "Kaydediliyor..." : "Kaydet"}
        </button>
      </div>

      {/* SMS */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center gap-2 mb-4">
          <Phone size={18} className="text-blue-600" />
          <h2 className="font-semibold">SMS Sağlayıcı</h2>
          <span className="text-xs text-gray-500 ml-2">(Sadece bir sağlayıcı aktif olabilir)</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-5">
          {catalog.sms_providers.map(p => (
            <button key={p.key} onClick={() => setCfg(c => ({ ...c, sms_active: p.key }))}
              className={`text-left px-3 py-2 rounded border text-sm ${cfg.sms_active === p.key ? "bg-black text-white border-black" : "bg-white hover:border-black"}`}
              data-testid={`sms-provider-${p.key}`}>
              {p.name}
            </button>
          ))}
        </div>
        {cfg.sms_active && FIELD_MAP[cfg.sms_active] && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {FIELD_MAP[cfg.sms_active].map(([f, lbl]) => (
              <div key={f}>
                <label className="block text-xs text-gray-600 mb-1">{lbl}</label>
                <input value={cfg.providers?.[cfg.sms_active]?.[f] || ""}
                  onChange={(e) => setProvField(cfg.sms_active, f, e.target.value)}
                  className="w-full border border-gray-200 rounded px-3 py-2 text-sm"
                  data-testid={`sms-field-${f}`} />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* WhatsApp */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <MessageSquare size={18} className="text-green-600" />
            <h2 className="font-semibold">WhatsApp Business (Meta Cloud API)</h2>
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={cfg.whatsapp_active}
              onChange={(e) => setCfg(c => ({ ...c, whatsapp_active: e.target.checked }))}
              data-testid="whatsapp-active-toggle" />
            Aktif
          </label>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {[["phone_number_id", "Phone Number ID"], ["access_token", "Permanent Access Token"], ["api_version", "API Versiyon (ör. v23.0)"], ["verify_token", "Webhook Verify Token (kendin belirle)"], ["app_secret", "App Secret (imza doğrulama, ops.)"]].map(([f, lbl]) => (
            <div key={f} className={f === "access_token" ? "md:col-span-2" : ""}>
              <label className="block text-xs text-gray-600 mb-1">{lbl}</label>
              <input value={cfg.providers?.whatsapp_meta?.[f] || ""}
                onChange={(e) => setProvField("whatsapp_meta", f, e.target.value)}
                className="w-full border border-gray-200 rounded px-3 py-2 text-sm"
                data-testid={`wa-field-${f}`} />
            </div>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm cursor-pointer mt-4 pt-4 border-t border-gray-100">
          <input type="checkbox"
            checked={cfg.providers?.whatsapp_meta?.ai_autoreply === true}
            onChange={(e) => setProvField("whatsapp_meta", "ai_autoreply", e.target.checked)}
            data-testid="whatsapp-ai-autoreply-toggle" />
          <span><strong>AI otomatik yanıt</strong> — gelen WhatsApp mesajlarına yapay zekâ, gerçek bir müşteri temsilcisi gibi cevap versin (düşük güvende insana devreder). Webhook Meta'da tanımlı olmalı.</span>
        </label>
        <div className="mt-4 pt-4 border-t border-gray-100">
          <label className="block text-xs text-gray-600 mb-1">Temsilci (canlı devir) numarası</label>
          <input value={cfg.providers?.whatsapp_meta?.handoff_notify_phone || ""}
            onChange={(e) => setProvField("whatsapp_meta", "handoff_notify_phone", e.target.value)}
            placeholder="ör. +90 5xx xxx xx xx"
            className="w-full md:w-1/2 border border-gray-200 rounded px-3 py-2 text-sm"
            data-testid="wa-field-handoff_notify_phone" />
          <p className="text-[11px] text-gray-500 mt-2">
            AI bir soruyu cevaplayamadığında, müşterinin sorusunu <strong>bu numaraya</strong> WhatsApp'tan iletir.
            O alarm mesajını <strong>yanıtlayıp</strong> (kaydır/reply) cevabı yazdığınızda, cevap otomatik olarak
            müşteriye gönderilir. (Bu esnada müşteriye mesaj gitmez; sessiz beklenir.) Alarm ulaşmazsa panel push +
            firma e-postası ile de haber verilir. <strong>Not:</strong> işletme hattının kendi numarası olmamalı;
            cevap verecek temsilcinin kişisel WhatsApp numarası olmalı.
          </p>
        </div>
        <div className="mt-4 pt-4 border-t border-gray-100">
          <label className="block text-xs text-gray-600 mb-1">WhatsApp'tan sipariş aldırma</label>
          <div className="flex flex-col md:flex-row gap-3 md:items-center">
            <select value={cfg.providers?.whatsapp_meta?.ordering_mode || "off"}
              onChange={(e) => setProvField("whatsapp_meta", "ordering_mode", e.target.value)}
              className="border border-gray-200 rounded px-3 py-2 text-sm"
              data-testid="wa-ordering-mode">
              <option value="off">Kapalı</option>
              <option value="test">Test (sadece izinli numaralar)</option>
              <option value="live">Canlı (tüm müşteriler)</option>
            </select>
            <input value={cfg.providers?.whatsapp_meta?.ordering_test_phones || ""}
              onChange={(e) => setProvField("whatsapp_meta", "ordering_test_phones", e.target.value)}
              placeholder="Test numaraları (virgülle): +90 5xx…, +90 5yy…"
              className="flex-1 border border-gray-200 rounded px-3 py-2 text-sm"
              data-testid="wa-ordering-test-phones" />
          </div>
          <p className="text-[11px] text-gray-500 mt-2">
            Açıkken Buse, müşterinin bilgilerini (ad-soyad, adres, beden, adet, e-posta + izin) alıp
            <strong> siparişi sizin adınıza oluşturur</strong> (fiyat sunucudan; <strong>kart bilgisi asla istenmez</strong>).
            <strong>Havale/EFT</strong> (banka bilgisi otomatik) ve <strong>kredi kartı</strong> (iyzico güvenli ödeme linki —
            kart yalnız iyzico'da girilir) desteklenir; isteyen müşteriye <strong>üyelik</strong> de açılır.
            <strong> Test</strong> modunda yalnız buraya yazdığınız numaralar (ve temsilci numarası) sipariş oluşturabilir —
            canlı müşteriler etkilenmez. E-posta/SMS izinleri İYS'ye bildirilir.
          </p>
        </div>
        <p className="text-[11px] text-gray-500 mt-2">
          Webhook Callback URL: <code>{`${(process.env.REACT_APP_BACKEND_URL||'').replace(/\/$/,'')}/api/whatsapp/webhook`}</code> — Meta uygulamasında bu URL + yukarıdaki Verify Token ile "messages" alanına abone olun.
        </p>
      </section>

      {/* Messenger + Instagram (Meta Messenger Platform) */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center gap-2 mb-4">
          <MessageSquare size={18} className="text-pink-600" />
          <h2 className="font-semibold">Messenger + Instagram DM (Meta)</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {[["page_access_token", "Page Access Token (Sayfa + bağlı IG)"], ["verify_token", "Webhook Verify Token (kendin belirle)"], ["app_secret", "App Secret (imza doğrulama, ops.)"], ["page_id", "Page ID (ops.)"]].map(([f, lbl]) => (
            <div key={f} className={f === "page_access_token" ? "md:col-span-2" : ""}>
              <label className="block text-xs text-gray-600 mb-1">{lbl}</label>
              <input value={cfg.providers?.meta_messaging?.[f] || ""}
                onChange={(e) => setProvField("meta_messaging", f, e.target.value)}
                className="w-full border border-gray-200 rounded px-3 py-2 text-sm"
                data-testid={`meta-msg-field-${f}`} />
            </div>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm cursor-pointer mt-4 pt-4 border-t border-gray-100">
          <input type="checkbox"
            checked={cfg.providers?.meta_messaging?.ai_autoreply === true}
            onChange={(e) => setProvField("meta_messaging", "ai_autoreply", e.target.checked)}
            data-testid="meta-msg-ai-autoreply-toggle" />
          <span><strong>AI otomatik yanıt</strong> — Instagram ve Messenger DM'lerine yapay zekâ, gerçek bir müşteri temsilcisi gibi cevap versin (düşük güvende insana devreder).</span>
        </label>
        <p className="text-[11px] text-gray-500 mt-2">
          Webhook Callback URL: <code>{`${(process.env.REACT_APP_BACKEND_URL||'').replace(/\/$/,'')}/api/meta-messaging/webhook`}</code> — Meta uygulamasında Messenger ve Instagram ürünlerinin Webhooks bölümünde bu URL + Verify Token ile "messages" alanına abone olun. IG hesabı, Sayfa'ya bağlı olmalı.
        </p>
      </section>

      {/* Email */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Mail size={18} className="text-purple-600" />
            <h2 className="font-semibold">E-posta Bildirimleri</h2>
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={cfg.email_active}
              onChange={(e) => setCfg(c => ({ ...c, email_active: e.target.checked }))}
              data-testid="email-active-toggle" />
            Aktif
          </label>
        </div>
        <p className="text-xs text-gray-500 mt-2">E-posta gönderimi <b>Zoho SMTP</b> ile yapılır. Hesap ve şifre için: <a href="/admin/ayarlar/eposta" className="text-blue-600 hover:underline">Ayarlar → E-posta (SMTP)</a></p>
      </section>

      {/* Test */}
      <section className="bg-gray-50 rounded-lg border border-gray-200 p-5">
        <h3 className="font-semibold mb-3 flex items-center gap-2"><Send size={16} /> Test Gönderimi</h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
          <div>
            <label className="block text-xs text-gray-600 mb-1">Kanal</label>
            <select value={testChannel} onChange={(e) => setTestChannel(e.target.value)}
              className="w-full border border-gray-200 rounded px-3 py-2 text-sm bg-white"
              data-testid="test-channel-select">
              <option value="sms">SMS</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="email">E-posta</option>
            </select>
          </div>
          <div className="md:col-span-2">
            <label className="block text-xs text-gray-600 mb-1">Alıcı ({testChannel === "email" ? "mail" : "telefon"})</label>
            <input value={testTo} onChange={(e) => setTestTo(e.target.value)}
              placeholder={testChannel === "email" ? "ornek@mail.com" : "05551234567"}
              className="w-full border border-gray-200 rounded px-3 py-2 text-sm"
              data-testid="test-to-input" />
          </div>
          <button onClick={handleTest} disabled={testing}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm disabled:opacity-60"
            data-testid="test-send-btn">
            {testing ? "Gönderiliyor..." : `Test ${testChannel === "email" ? "E-posta" : testChannel === "whatsapp" ? "WhatsApp" : "SMS"} Gönder`}
          </button>
        </div>
        <textarea value={testMsg} onChange={(e) => setTestMsg(e.target.value)}
          rows={2}
          className="w-full mt-3 border border-gray-200 rounded px-3 py-2 text-sm"
          placeholder="Mesaj metni"
          data-testid="test-msg-input" />
      </section>

      {/* Uygulama Push Bildirimi */}
      <section className="bg-gray-50 rounded-lg border border-gray-200 p-5">
        <h3 className="font-semibold mb-1 flex items-center gap-2"><Send size={16} /> Uygulama Push Bildirimi</h3>
        <p className="text-xs text-gray-500 mb-3">Mobil admin uygulamasına giriş yapmış tüm cihazlara anlık bildirim gönderir.</p>
        <div className="grid grid-cols-1 gap-3">
          <input value={pushTitle} onChange={(e) => setPushTitle(e.target.value)}
            placeholder="Başlık (örn. Duyuru)"
            className="w-full border border-gray-200 rounded px-3 py-2 text-sm"
            data-testid="push-title-input" />
          <textarea value={pushBody} onChange={(e) => setPushBody(e.target.value)} rows={2}
            placeholder="Mesaj metni"
            className="w-full border border-gray-200 rounded px-3 py-2 text-sm"
            data-testid="push-body-input" />
          <div className="flex flex-wrap gap-3">
            <button onClick={handlePushSend} disabled={pushSending}
              className="bg-black text-white px-4 py-2 rounded text-sm disabled:opacity-60"
              data-testid="push-send-btn">
              {pushSending ? "Gönderiliyor..." : "Push Gönder"}
            </button>
            <button onClick={handleTestOrder} disabled={pushSending}
              className="bg-amber-500 text-white px-4 py-2 rounded text-sm disabled:opacity-60"
              data-testid="push-test-order-btn">
              🛍️ Sahte Sipariş Bildirimi (Test)
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
