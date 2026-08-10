/**
 * =============================================================================
 * NotificationTemplates.jsx — Event × Kanal şablon editörü
 * =============================================================================
 *   GET /api/notifications/templates   → {templates: [...]}
 *   POST /api/notifications/templates  → upsert tek template
 *   POST /api/notifications/templates/seed → default'ları oluştur
 *
 * Değişken etiketleri: {customer_name} {order_number} {amount}
 *   {tracking_number} {otp_code} {cart_url}
 * =============================================================================
 */
import { useEffect, useMemo, useState, useRef } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, RefreshCw, Mail, MessageSquare, Phone, Send } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const CHANNEL_META = {
  sms: { label: "SMS", icon: Phone, color: "text-blue-600" },
  email: { label: "E-posta", icon: Mail, color: "text-purple-600" },
  whatsapp: { label: "WhatsApp", icon: MessageSquare, color: "text-green-600" },
};

// Değişken paleti — tıkla-ekle. Her değişken ilgili bildirim türünde dolar
// (ör. {bank_iban} sadece havale, {return_code} sadece iade bildiriminde).
// star: en sık ihtiyaç duyulan (kargo takip linki).
const VAR_GROUPS = [
  { title: "Müşteri & Sipariş", items: [
    ["{customer_name}", "Müşteri adı"],
    ["{order_number}", "Sipariş numarası"],
    ["{amount}", "Sipariş tutarı (ör. 1.234,00 TL)"],
    ["{status_label}", "Durumun müşteriye görünen adı"],
    ["{order_link}", "Müşterinin sipariş takip sayfası"],
  ]},
  { title: "Kargo", items: [
    ["{tracking_link}", "Kargo takip linki — tıklanır (deep-link)", true],
    ["{tracking_number}", "Kargo takip numarası"],
    ["{cargo_provider}", "Kargo firması"],
  ]},
  { title: "Havale / Ödeme", items: [
    ["{bank_name}", "Banka adı"],
    ["{bank_iban}", "IBAN"],
    ["{bank_account_holder}", "Hesap sahibi"],
    ["{bank_branch}", "Şube"],
    ["{payment_url}", "Ödeme bildirimi sayfası linki"],
  ]},
  { title: "İade", items: [
    ["{return_code}", "İade kargo kodu"],
    ["{return_barcode_img}", "İade barkod görseli"],
    ["{valid_until}", "Kodun son geçerlilik tarihi"],
  ]},
  { title: "Üyelik · Favori · Sepet", items: [
    ["{product_name}", "Ürün adı (favori tekrar stokta)"],
    ["{product_link}", "Ürün linki (favori tekrar stokta)"],
    ["{site_url}", "Site adresi (hoş geldin)"],
    ["{cart_url}", "Sepet linki (sepet hatırlatma)"],
    ["{otp_code}", "Doğrulama kodu (OTP)"],
  ]},
];

export default function NotificationTemplates() {
  const [catalog, setCatalog] = useState({ events: [], channels: [] });
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(null); // "event|channel"
  const [testTo, setTestTo] = useState("");
  const [testOrderNo, setTestOrderNo] = useState("");
  const [testEvent, setTestEvent] = useState("order_shipped");
  const [testSending, setTestSending] = useState(null);
  const [testResult, setTestResult] = useState(null);
  // Değişken paleti için: son odaklanılan editör alanı (Konu/Mesaj) takibi.
  const activeElRef = useRef(null);
  const activeMetaRef = useRef(null); // { event, channel, field }

  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const load = async () => {
    setLoading(true);
    const [cat, tpl] = await Promise.all([
      axios.get(`${API}/notifications/providers/catalog`, auth),
      axios.get(`${API}/notifications/templates`, auth),
    ]);
    setCatalog(cat.data);
    setTemplates(tpl.data?.templates || []);
    setLoading(false);
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const map = useMemo(() => {
    const m = {};
    for (const t of templates) m[`${t.event}|${t.channel}`] = t;
    return m;
  }, [templates]);

  const update = (event, channel, patch) => {
    setTemplates(ts => {
      const key = `${event}|${channel}`;
      const existing = ts.find(t => t.event === event && t.channel === channel);
      if (existing) return ts.map(t => (t.event === event && t.channel === channel) ? { ...t, ...patch } : t);
      return [...ts, { event, channel, enabled: true, subject: "", body: "", ...patch }];
    });
  };

  const save = async (event, channel) => {
    const t = map[`${event}|${channel}`] || { event, channel, enabled: true, subject: "", body: "" };
    setSaving(`${event}|${channel}`);
    try {
      await axios.post(`${API}/notifications/templates`, t, auth);
      toast.success(`Kaydedildi: ${event} / ${channel}`);
    } catch (e) {
      toast.error("Hata: " + (e?.response?.data?.detail || e.message));
    } finally { setSaving(null); }
  };

  const seed = async () => {
    try {
      const r = await axios.post(`${API}/notifications/templates/seed`, {}, auth);
      toast.success(`${r.data.created} varsayılan şablon oluşturuldu`);
      await load();
    } catch (e) { toast.error("Seed hatası"); }
  };

  const seedForce = async () => {
    if (!window.confirm("Tüm e-posta şablonları yeni FACETTE tasarımına (logo · içerik · INSTAGRAM·TIKTOK) güncellenecek. Manuel düzenlediğiniz şablonlara dokunulmaz. Devam edilsin mi?")) return;
    try {
      const r = await axios.post(`${API}/notifications/templates/seed?force=true`, {}, auth);
      toast.success(`${r.data.updated || 0} şablon güncellendi · ${r.data.created || 0} yeni oluşturuldu`);
      await load();
    } catch (e) { toast.error("Güncelleme hatası: " + (e?.response?.data?.detail || e.message)); }
  };

  const sendTest = async (channel) => {
    if (!testTo.trim()) { toast.error("Önce telefon numarası veya e-posta girin"); return; }
    setTestSending(channel);
    setTestResult(null);
    try {
      const r = await axios.post(`${API}/notifications/test-template`, {
        event: testEvent,
        channel,
        to: testTo.trim(),
        order_number: testOrderNo.trim(),
      }, auth);
      const ok = r.data?.success !== false;
      setTestResult({ channel, ok, data: r.data });
      if (ok) toast.success(`${CHANNEL_META[channel].label} gönderildi · baz sipariş: ${r.data?.based_on_order || "-"}`);
      else toast.error(`${CHANNEL_META[channel].label}: ${r.data?.error || r.data?.detail || "gönderilemedi"}`);
    } catch (e) {
      setTestResult({ channel, ok: false, data: e?.response?.data || { error: e.message } });
      toast.error("Hata: " + (e?.response?.data?.detail || e.message));
    } finally { setTestSending(null); }
  };

  // Paletten değişkeni, odaklanılan alanda imlecin olduğu yere ekle.
  const insertVar = (token) => {
    const meta = activeMetaRef.current;
    const el = activeElRef.current;
    if (!meta || !el) { toast.error("Önce bir metin alanına (Konu / Mesaj) tıklayın"); return; }
    const cur = el.value || "";
    const start = el.selectionStart ?? cur.length;
    const end = el.selectionEnd ?? cur.length;
    const next = cur.slice(0, start) + token + cur.slice(end);
    update(meta.event, meta.channel, { [meta.field]: next });
    setTimeout(() => { try { el.focus(); const p = start + token.length; el.setSelectionRange(p, p); } catch (_e) { /* noop */ } }, 0);
  };

  if (loading) return <div className="p-6 text-gray-500">Yükleniyor...</div>;

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6" data-testid="notification-templates-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Bildirim Şablonları</h1>
          <p className="text-sm text-gray-500 mt-1">Her event × kanal için metni özelleştirin.</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={seedForce} className="inline-flex items-center gap-2 bg-gray-900 text-white hover:bg-black px-3 py-2 rounded text-sm" data-testid="notif-seed-force">
            <RefreshCw size={14} /> E-postaları Yeni Tasarıma Güncelle
          </button>
          <button onClick={seed} className="inline-flex items-center gap-2 bg-gray-100 hover:bg-gray-200 px-3 py-2 rounded text-sm">
            <RefreshCw size={14} /> Default Şablonları Oluştur
          </button>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold text-sm">Değişkenler</h2>
          <span className="text-[11px] text-gray-500">Bir <b>Konu/Mesaj</b> alanına tıkla, sonra değişkene tıkla → imlecin olduğu yere eklenir.</span>
        </div>
        <div className="space-y-3">
          {VAR_GROUPS.map((g) => (
            <div key={g.title}>
              <div className="text-[11px] font-bold text-gray-400 uppercase tracking-wide mb-1">{g.title}</div>
              <div className="flex flex-wrap gap-1.5">
                {g.items.map(([token, desc, star]) => (
                  <button key={token} type="button" onClick={() => insertVar(token)} title={desc}
                    className={`inline-flex items-center gap-1 px-2 py-1 rounded border text-[11px] font-mono transition-colors ${star ? "bg-amber-50 border-amber-300 text-amber-800 hover:bg-amber-100" : "bg-gray-50 border-gray-200 text-gray-700 hover:bg-gray-100"}`}>
                    {star ? "★ " : ""}{token}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <p className="text-[11px] text-gray-400 mt-3">
          Her değişken ilgili bildirim türünde dolar (ör. <span className="font-mono">{"{bank_iban}"}</span> sadece havale,
          {" "}<span className="font-mono">{"{return_code}"}</span> sadece iade, <span className="font-mono">{"{product_name}"}</span> favori-stokta bildiriminde).
          {" "}<span className="font-mono">{"{tracking_link}"}</span> kargo takip linkidir ve <span className="font-mono">{"{tracking_url}"}</span> ile aynıdır.
        </p>
      </div>

      <div className="space-y-3">
        {catalog.events.map(ev => (
          <div key={ev.key} className="bg-white border border-gray-200 rounded-lg">
            <div className="bg-gray-50 px-4 py-3 border-b font-semibold">{ev.name} <span className="text-xs text-gray-500">({ev.key})</span></div>
            <div className="grid grid-cols-1 lg:grid-cols-3 divide-x divide-gray-100">
              {["sms", "email", "whatsapp"].map(ch => {
                const meta = CHANNEL_META[ch];
                const Ico = meta.icon;
                const t = map[`${ev.key}|${ch}`] || { enabled: false, subject: "", body: "" };
                return (
                  <div key={ch} className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Ico size={16} className={meta.color} />
                        <span className="font-medium text-sm">{meta.label}</span>
                      </div>
                      <label className="flex items-center gap-1 text-xs cursor-pointer">
                        <input type="checkbox" checked={!!t.enabled}
                          onChange={(e) => update(ev.key, ch, { enabled: e.target.checked })}
                          data-testid={`tpl-enable-${ev.key}-${ch}`} />
                        Aktif
                      </label>
                    </div>
                    {ch === "email" && (
                      <input value={t.subject || ""} onChange={(e) => update(ev.key, ch, { subject: e.target.value })}
                        onFocus={(e) => { activeElRef.current = e.target; activeMetaRef.current = { event: ev.key, channel: ch, field: "subject" }; }}
                        placeholder="Konu"
                        className="w-full border border-gray-200 rounded px-2 py-1.5 text-xs"
                        data-testid={`tpl-subject-${ev.key}`} />
                    )}
                    <textarea value={t.body || ""} onChange={(e) => update(ev.key, ch, { body: e.target.value })}
                      onFocus={(e) => { activeElRef.current = e.target; activeMetaRef.current = { event: ev.key, channel: ch, field: "body" }; }}
                      rows={4} placeholder={`${meta.label} mesajı (değişken kullanabilirsiniz)`}
                      className="w-full border border-gray-200 rounded px-2 py-1.5 text-xs font-mono"
                      data-testid={`tpl-body-${ev.key}-${ch}`} />
                    <button onClick={() => save(ev.key, ch)} disabled={saving === `${ev.key}|${ch}`}
                      className="w-full bg-black text-white py-1.5 rounded text-xs disabled:opacity-60 inline-flex items-center justify-center gap-1"
                      data-testid={`tpl-save-${ev.key}-${ch}`}>
                      <Save size={12} /> {saving === `${ev.key}|${ch}` ? "..." : "Kaydet"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* ===== TEST GÖNDERİMİ ===== */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3" data-testid="notification-test-panel">
        <div className="flex items-center gap-2">
          <Send size={16} className="text-gray-700" />
          <h2 className="font-semibold">Test Gönderimi</h2>
        </div>
        <p className="text-xs text-gray-500">
          Bir sipariş durumu seçin, <b>sipariş no</b> ve telefon/e-posta girin: o durumun <b>gerçek şablonu</b>,
          girdiğiniz siparişin <b>gerçek verisiyle</b> (isim, sipariş no, takip no…) doldurulup gönderilir —
          böylece her durumda bildirimin tam nasıl gideceğini görürsünüz. Sipariş no boş bırakılırsa
          en son kargoya verilen sipariş baz alınır. SMS/WhatsApp için telefon, e-posta testi için e-posta girin.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <select value={testEvent} onChange={(e) => setTestEvent(e.target.value)}
            className="w-full border border-gray-200 rounded px-3 py-2 text-sm bg-white"
            data-testid="notif-test-event">
            {(catalog.events || []).map(ev => (
              <option key={ev.key} value={ev.key}>{ev.name}</option>
            ))}
          </select>
          <input value={testOrderNo} onChange={(e) => setTestOrderNo(e.target.value)}
            placeholder="Sipariş No (örn. 913BS3894E)"
            className="w-full border border-gray-200 rounded px-3 py-2 text-sm"
            data-testid="notif-test-orderno" />
          <input value={testTo} onChange={(e) => setTestTo(e.target.value)}
            placeholder="Telefon (5XXXXXXXXX) veya e-posta"
            className="w-full border border-gray-200 rounded px-3 py-2 text-sm"
            data-testid="notif-test-to" />
        </div>
        <div className="flex flex-wrap gap-2">
          {["sms", "whatsapp", "email"].map(ch => {
            const meta = CHANNEL_META[ch];
            const Ico = meta.icon;
            return (
              <button key={ch} onClick={() => sendTest(ch)} disabled={!!testSending}
                className="inline-flex items-center gap-2 px-3 py-2 rounded text-sm border border-gray-200 hover:bg-gray-50 disabled:opacity-60"
                data-testid={`notif-test-${ch}`}>
                <Ico size={14} className={meta.color} />
                {testSending === ch ? "Gönderiliyor..." : `Test ${meta.label} Gönder`}
              </button>
            );
          })}
        </div>
        {testResult && (
          <div className={`text-xs rounded p-3 border ${testResult.ok ? "bg-green-50 border-green-200 text-green-800" : "bg-red-50 border-red-200 text-red-800"}`}
            data-testid="notif-test-result">
            <b>{CHANNEL_META[testResult.channel]?.label} sonucu:</b>{" "}
            {testResult.ok ? "Gönderildi ✓" : "Başarısız ✗"}
            {testResult.data?.based_on_order && (
              <div className="mt-1">
                Baz alınan sipariş: <b>{testResult.data.based_on_order}</b>
                {testResult.data?.event_name ? ` · ${testResult.data.event_name}` : ""}
              </div>
            )}
            {testResult.data?.preview && (
              <div className="mt-1">
                Giden mesaj:
                <pre className="mt-0.5 whitespace-pre-wrap break-words text-[11px] bg-white/70 rounded p-2 border border-gray-200 text-gray-800">{testResult.data.preview}</pre>
              </div>
            )}
            <details className="mt-1">
              <summary className="cursor-pointer opacity-70">Teknik detay</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all text-[11px] opacity-80">{JSON.stringify(testResult.data?.result ?? testResult.data, null, 2)}</pre>
            </details>
          </div>
        )}
      </div>
    </div>
  );
}
