/**
 * =============================================================================
 * MarketingPixels.jsx — Pixel + Server-Side CAPI (Conversions API) Yönetimi
 * =============================================================================
 *   • Browser pixel snippet enjeksiyonu (mevcut: GA4 / Meta / TT / Pinterest /
 *     Snapchat / GTM / Yandex / Hotjar / Clarity).
 *   • Server-side CAPI: Meta CAPI, Google Ads Enhanced, TikTok Events API,
 *     Pinterest Conversions API, Snapchat Conversions API.
 *   • Access Token vault'a şifreli yazılır; "Test Bağlantı" canlı dener.
 *   • Stuck queue durumu + manuel retry butonu.
 *   • Multi-tenant ready: tenant_id alanı ile satış için ayrı hesap izole.
 * =============================================================================
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Plus, Trash2, Save, Code, KeyRound, Activity, RefreshCw, Zap } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const PROVIDER_HINTS = {
  ga4: "ör. G-XXXXXXXXXX",
  meta: "Pixel ID — ör. 1234567890123456",
  google_ads: "Measurement ID — ör. G-XXXX  (Ads bağlantısı için)",
  tiktok: "Pixel Code — ör. C1234567890",
  pinterest: "Ad Account ID — ör. 549764159123",
  snapchat: "Pixel ID — ör. abcd1234-1234-1234-1234-abcdef123456",
  gtm: "Container ID — ör. GTM-XXXX",
  yandex: "ör. 12345678",
  hotjar: "ör. 3456789",
  clarity: "ör. ab1234cd",
  custom: "— gerek yok, aşağıya HTML yapıştır —",
};

const TOKEN_HINTS = {
  meta: "Graph API Access Token (System User önerilir)",
  google_ads: "GA4 Measurement Protocol API Secret",
  tiktok: "TikTok Business API Access-Token",
  pinterest: "Pinterest API Bearer Token (Conversions scope)",
  snapchat: "Snap Marketing API Access Token",
};

const CAPI_TOKEN_GUIDE = {
  meta: {
    title: "Meta CAPI Token nasıl alınır?",
    steps: [
      "Events Manager'a git (business.facebook.com/events_manager).",
      "Veri kaynağını (Pixel/Dataset) seç → Ayarlar (Settings).",
      "Conversions API bölümüne in → 'Erişim Belirteci Oluştur' (Generate access token).",
      "Çıkan token 'EAA' ile başlar ve ~180+ karakterdir. Kopyala, üstteki Access Token alanına yapıştır.",
      "Pixel/Dataset ID'yi de yukarıdaki 'Etiket / Pixel ID' alanına gir, kaydet ve 'Bağlantıyı Test Et' ile doğrula.",
    ],
    link: "https://business.facebook.com/events_manager2",
    linkText: "Events Manager'ı aç →",
  },
  google_ads: {
    title: "Google Ads / GA4 Measurement Protocol API Secret nasıl alınır?",
    steps: [
      "Google Analytics → Yönetici (Admin) → Veri Akışları → web akışını seç.",
      "'Measurement Protocol API secrets' → 'Create' ile secret oluştur.",
      "Secret value'yu kopyala, Access Token alanına yapıştır.",
      "Etiket alanına Measurement ID (G-XXXX) gir, kaydet ve test et.",
    ],
    link: "https://analytics.google.com/",
    linkText: "Google Analytics'i aç →",
  },
  ga4: {
    title: "GA4 Measurement Protocol API Secret nasıl alınır?",
    steps: [
      "Google Analytics → Yönetici (Admin) → Veri Akışları (Data Streams) → web akışını seç.",
      "'Measurement Protocol API secrets' → 'Create' ile yeni secret oluştur.",
      "Secret value'yu kopyala, Access Token alanına yapıştır.",
      "Etiket alanına Measurement ID (G-XXXX) gir, kaydet ve test et.",
    ],
    link: "https://analytics.google.com/",
    linkText: "Google Analytics'i aç →",
  },
  tiktok: {
    title: "TikTok Events API Access Token nasıl alınır?",
    steps: [
      "TikTok Events Manager → Pixel/Event seç → Settings → 'Generate Access Token'.",
      "Token'ı kopyala, Access Token alanına yapıştır; Pixel Code'u Etiket alanına gir.",
    ],
    link: "https://ads.tiktok.com/i18n/events_manager",
    linkText: "TikTok Events Manager'ı aç →",
  },
  pinterest: {
    title: "Pinterest Conversions API Token nasıl alınır?",
    steps: [
      "Pinterest Ads → Conversions → API access → 'Generate token' (conversions scope).",
      "Token'ı Access Token alanına yapıştır; Tag ID'yi Etiket alanına gir.",
    ],
    link: "https://ads.pinterest.com/",
    linkText: "Pinterest Ads'i aç →",
  },
  snapchat: {
    title: "Snapchat Conversions API Token nasıl alınır?",
    steps: [
      "Snapchat Ads Manager → Events Manager → Pixel → Conversions API → token oluştur.",
      "Token'ı Access Token alanına yapıştır; Pixel ID'yi Etiket alanına gir.",
    ],
    link: "https://ads.snapchat.com/",
    linkText: "Snapchat Ads'i aç →",
  },
};


const CAPI_PROVIDERS = ["meta", "ga4", "google_ads", "tiktok", "pinterest", "snapchat"];

const blankForm = {
  provider: "meta", name: "", tag_id: "",
  head_snippet: "", body_snippet: "", is_active: true,
  capi_enabled: true, access_token: "",
  vault_key: "", env_token_key: "",
  test_event_code: "", tenant_id: "",
};

export default function MarketingPixels() {
  const [providers, setProviders] = useState([]);
  const [items, setItems] = useState([]);
  const [form, setForm] = useState(blankForm);
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [queueStatus, setQueueStatus] = useState(null);
  const [runningQueue, setRunningQueue] = useState(false);

  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const load = async () => {
    const [p, l, q] = await Promise.all([
      axios.get(`${API}/marketing-pixels/providers`, auth),
      axios.get(`${API}/marketing-pixels`, auth),
      axios.get(`${API}/marketing-pixels/capi/queue/status`, auth).catch(() => null),
    ]);
    setProviders(p.data?.providers || []);
    setItems(l.data?.items || []);
    if (q?.data) setQueueStatus(q.data);
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const isCapi = CAPI_PROVIDERS.includes(form.provider);

  const save = async () => {
    setSaving(true);
    try {
      const payload = { ...form };
      if (editing) payload.id = editing;
      // Default vault_key when capi_enabled + provider chosen — convenience
      if (payload.capi_enabled && !payload.vault_key && !payload.env_token_key && payload.access_token) {
        payload.vault_key = `capi_${payload.provider}_${(payload.tag_id || "").slice(-6) || "default"}`;
      }
      await axios.post(`${API}/marketing-pixels`, payload, auth);
      toast.success(editing ? "Pixel güncellendi" : "Pixel eklendi");
      setForm(blankForm); setEditing(null); setShowAdvanced(false);
      await load();
    } catch (e) {
      toast.error("Kaydedilemedi: " + (e?.response?.data?.detail || e.message));
    } finally { setSaving(false); }
  };

  const testConnection = async () => {
    setTesting(true);
    try {
      const payload = { ...form };
      if (editing) payload.id = editing;
      const res = await axios.post(`${API}/marketing-pixels/test-connection`, payload, auth);
      if (res.data?.ok) {
        toast.success(`Bağlantı başarılı (HTTP ${res.data.status}). Reklam panelinde test event'i görüyor olmalısınız.`);
      } else {
        toast.error(`Test başarısız: ${JSON.stringify(res.data?.error || res.data?.response)?.slice(0, 200)}`);
      }
    } catch (e) {
      toast.error("Test hatası: " + (e?.response?.data?.detail || e.message));
    } finally { setTesting(false); }
  };

  const edit = (px) => {
    setEditing(px.id);
    setForm({
      provider: px.provider,
      name: px.name || "",
      tag_id: px.tag_id || "",
      head_snippet: px.head_snippet || "",
      body_snippet: px.body_snippet || "",
      is_active: !!px.is_active,
      capi_enabled: !!px.capi_enabled,
      access_token: "",  // server returns masked '***'; user re-enters to update
      vault_key: px.vault_key || "",
      env_token_key: px.env_token_key || "",
      test_event_code: px.test_event_code || "",
      tenant_id: px.tenant_id || "",
    });
    setShowAdvanced(!!(px.head_snippet && !px.tag_id));
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const remove = async (id) => {
    if (!await window.appConfirm("Bu pixel'i silmek istediğinize emin misiniz?")) return;
    await axios.delete(`${API}/marketing-pixels/${id}`, auth);
    toast.success("Silindi");
    await load();
  };

  const toggle = async (px) => {
    await axios.post(`${API}/marketing-pixels`, { ...px, id: px.id, is_active: !px.is_active }, auth);
    await load();
  };

  const runQueue = async () => {
    setRunningQueue(true);
    try {
      const res = await axios.post(`${API}/marketing-pixels/capi/queue/run-now`, {}, auth);
      toast.success(`Kuyruk işlendi: ${res.data?.ok || 0} başarılı / ${res.data?.failed || 0} hatalı`);
      await load();
    } catch (e) {
      toast.error("Kuyruk hatası: " + (e?.response?.data?.detail || e.message));
    } finally { setRunningQueue(false); }
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6" data-testid="marketing-pixels-page">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Code size={20} /> Pixel & Server-Side CAPI Yönetimi
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Pixel ID + Access Token girin → hem tarayıcı taraflı hem sunucu taraflı (CAPI) takip otomatik çalışır.
            AdBlocker / iOS 14+ kayıplarını telafi eder.
          </p>
        </div>
        {/* CAPI Queue durumu */}
        {queueStatus && (
          <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs">
            <Activity size={14} className="text-slate-500" />
            <span><b>{queueStatus.pending}</b> bekleyen</span>
            <span className="text-red-600"><b>{queueStatus.dead}</b> ölü</span>
            <span className="text-green-700"><b>{queueStatus.total_logs}</b> log</span>
            <button onClick={runQueue} disabled={runningQueue}
              className="inline-flex items-center gap-1 ml-2 px-2 py-1 bg-black text-white rounded text-xs disabled:opacity-60"
              data-testid="capi-queue-run-now">
              <RefreshCw size={12} className={runningQueue ? "animate-spin" : ""} />
              Kuyruğu Şimdi Tetikle
            </button>
          </div>
        )}
      </div>

      {/* Form */}
      <section className="bg-white rounded-lg border border-gray-200 p-5 space-y-4">
        <h2 className="font-semibold flex items-center gap-2">
          {editing ? "Pixel Güncelle" : <><Plus size={16} /> Yeni Pixel Ekle</>}
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-gray-600 mb-1">Sağlayıcı</label>
            <select value={form.provider}
              onChange={(e) => setForm({ ...form, provider: e.target.value, capi_enabled: CAPI_PROVIDERS.includes(e.target.value) })}
              className="w-full border px-3 py-2 text-sm rounded bg-white" data-testid="pixel-provider">
              {providers.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}{p.supports_capi ? "  ⚡ CAPI" : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Etiket / Pixel ID</label>
            <input value={form.tag_id} onChange={(e) => setForm({ ...form, tag_id: e.target.value })}
              placeholder={PROVIDER_HINTS[form.provider] || ""}
              className="w-full border px-3 py-2 text-sm rounded font-mono" data-testid="pixel-tag-id" />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Ad (etiket)</label>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={`ör. ${form.provider.toUpperCase()} Ana`}
              className="w-full border px-3 py-2 text-sm rounded" data-testid="pixel-name" />
          </div>
        </div>

        {/* CAPI bölümü */}
        {isCapi && (
          <div className="border-t pt-4 space-y-3 bg-amber-50 -mx-5 px-5 pb-4 border-amber-200">
            <h3 className="flex items-center gap-2 text-sm font-bold text-amber-900">
              <KeyRound size={14} /> Server-Side (CAPI) Ayarları
              <span className="text-[10px] bg-amber-200 text-amber-900 px-1.5 py-0.5 rounded ml-1">SaaS / White-label uyumlu</span>
            </h3>

            {/* Token nasıl alınır rehberi */}
            {CAPI_TOKEN_GUIDE[form.provider] && (
              <div className="bg-white border border-amber-200 rounded p-3 text-xs text-gray-700" data-testid="capi-token-guide">
                <div className="font-semibold text-amber-900 mb-1 flex items-center gap-1">
                  <KeyRound size={12} /> {CAPI_TOKEN_GUIDE[form.provider].title}
                </div>
                <ol className="list-decimal list-inside space-y-0.5">
                  {CAPI_TOKEN_GUIDE[form.provider].steps.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ol>
                {CAPI_TOKEN_GUIDE[form.provider].link && (
                  <a href={CAPI_TOKEN_GUIDE[form.provider].link} target="_blank" rel="noreferrer"
                    className="inline-block mt-2 text-amber-800 underline font-medium">
                    {CAPI_TOKEN_GUIDE[form.provider].linkText || "İlgili sayfayı aç →"}
                  </a>
                )}
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div className="md:col-span-2">
                <label className="block text-xs text-gray-700 mb-1">Access Token  <span className="text-amber-700">(şifreli vault'a kaydedilir)</span></label>
                <input type="password" value={form.access_token}
                  onChange={(e) => setForm({ ...form, access_token: e.target.value })}
                  placeholder={editing ? "Değiştirmek için yeni token girin (boş = mevcut korunur)" : (TOKEN_HINTS[form.provider] || "")}
                  className="w-full border px-3 py-2 text-sm rounded font-mono"
                  data-testid="capi-access-token" />
              </div>
              <div>
                <label className="block text-xs text-gray-700 mb-1">Test Event Code <span className="text-gray-500">(opsiyonel)</span></label>
                <input value={form.test_event_code}
                  onChange={(e) => setForm({ ...form, test_event_code: e.target.value })}
                  placeholder="ör. TEST00001"
                  className="w-full border px-3 py-2 text-sm rounded font-mono"
                  data-testid="capi-test-event-code" />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-gray-700 mb-1">Vault Key <span className="text-gray-500">(otomatik)</span></label>
                <input value={form.vault_key}
                  onChange={(e) => setForm({ ...form, vault_key: e.target.value })}
                  placeholder="auto"
                  className="w-full border px-3 py-2 text-xs rounded font-mono bg-white" />
              </div>
              <div>
                <label className="block text-xs text-gray-700 mb-1">Env Variable Key <span className="text-gray-500">(öncelikli)</span></label>
                <input value={form.env_token_key}
                  onChange={(e) => setForm({ ...form, env_token_key: e.target.value })}
                  placeholder="ör. META_CAPI_TOKEN"
                  className="w-full border px-3 py-2 text-xs rounded font-mono bg-white" />
              </div>
              <div>
                <label className="block text-xs text-gray-700 mb-1">Tenant ID <span className="text-gray-500">(SaaS — boş = tek mağaza)</span></label>
                <input value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  placeholder="ör. facette / brandX"
                  className="w-full border px-3 py-2 text-xs rounded font-mono bg-white" />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={form.capi_enabled}
                onChange={(e) => setForm({ ...form, capi_enabled: e.target.checked })}
                data-testid="capi-enabled-toggle" />
              <span><b>CAPI aktif</b> — bu sağlayıcı için sunucu taraflı gönderim yapılsın</span>
            </label>
            <button type="button" onClick={testConnection} disabled={testing}
              className="inline-flex items-center gap-1 bg-amber-700 text-white px-3 py-1.5 rounded text-xs hover:bg-amber-800 disabled:opacity-60"
              data-testid="capi-test-connection">
              <Zap size={12} /> {testing ? "Test gönderiliyor…" : "Bağlantıyı Test Et (Test Event)"}
            </button>
          </div>
        )}

        <div className="flex items-center justify-between pt-2">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={form.is_active}
              onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              data-testid="pixel-active-toggle" />
            Aktif (snippet & CAPI birlikte)
          </label>
          <button onClick={() => setShowAdvanced(!showAdvanced)}
            className="text-xs text-blue-600 hover:underline" data-testid="pixel-advanced-toggle">
            {showAdvanced ? "Gelişmiş modu gizle" : "Gelişmiş (özel HTML/JS)"}
          </button>
        </div>

        {showAdvanced && (
          <div className="space-y-2">
            <label className="block text-xs text-gray-600">&lt;head&gt; içine eklenecek kod (script/meta/noscript)</label>
            <textarea rows={6} value={form.head_snippet}
              onChange={(e) => setForm({ ...form, head_snippet: e.target.value })}
              className="w-full border px-3 py-2 text-xs font-mono rounded"
              placeholder="<script>...</script>" data-testid="pixel-head-snippet" />
            <label className="block text-xs text-gray-600">&lt;body&gt; sonuna eklenecek kod (opsiyonel)</label>
            <textarea rows={3} value={form.body_snippet}
              onChange={(e) => setForm({ ...form, body_snippet: e.target.value })}
              className="w-full border px-3 py-2 text-xs font-mono rounded"
              data-testid="pixel-body-snippet" />
          </div>
        )}

        <div className="flex items-center gap-2 pt-2">
          <button onClick={save} disabled={saving}
            className="inline-flex items-center gap-1 bg-black text-white px-4 py-2 rounded text-sm disabled:opacity-60"
            data-testid="pixel-save-btn">
            <Save size={14} /> {saving ? "Kaydediliyor..." : editing ? "Güncelle" : "Ekle"}
          </button>
          {editing && (
            <button onClick={() => { setEditing(null); setForm(blankForm); setShowAdvanced(false); }}
              className="text-sm text-gray-500 hover:text-black" data-testid="pixel-cancel-btn">İptal</button>
          )}
        </div>
      </section>

      {/* Liste */}
      <section className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="bg-gray-50 px-4 py-2 text-xs uppercase font-semibold flex items-center justify-between">
          <span>Eklenmiş Pixel'ler ({items.length})</span>
        </div>
        {items.length === 0 ? (
          <div className="p-6 text-sm text-gray-500" data-testid="pixel-empty-state">Henüz pixel eklenmedi.</div>
        ) : (
          <ul className="divide-y">
            {items.map((px) => (
              <li key={px.id} className="p-4 flex items-start justify-between gap-3 hover:bg-gray-50">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-sm">{px.name}</span>
                    <span className="text-[10px] uppercase bg-gray-100 px-1.5 py-0.5 rounded">{px.provider}</span>
                    {px.is_active ? (
                      <span className="text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded">Aktif</span>
                    ) : (
                      <span className="text-[10px] bg-gray-200 text-gray-600 px-1.5 py-0.5 rounded">Pasif</span>
                    )}
                    {px.capi_enabled && (
                      <span className="text-[10px] bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded font-bold">⚡ CAPI</span>
                    )}
                    {px._has_token && (
                      <span className="text-[10px] bg-blue-100 text-blue-800 px-1.5 py-0.5 rounded">🔑 Token</span>
                    )}
                    {px.tenant_id && (
                      <span className="text-[10px] bg-purple-100 text-purple-800 px-1.5 py-0.5 rounded">{px.tenant_id}</span>
                    )}
                  </div>
                  {px.tag_id && <div className="text-xs text-gray-500 font-mono mt-1">{px.tag_id}</div>}
                  {px.test_event_code && <div className="text-[10px] text-amber-700 font-mono mt-0.5">TEST: {px.test_event_code}</div>}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => toggle(px)}
                    className="text-xs px-2 py-1 rounded border hover:bg-gray-100"
                    data-testid={`pixel-toggle-${px.id}`}>
                    {px.is_active ? "Pasifleştir" : "Aktif Et"}
                  </button>
                  <button onClick={() => edit(px)}
                    className="text-xs px-2 py-1 rounded border hover:bg-blue-50"
                    data-testid={`pixel-edit-${px.id}`}>
                    Düzenle
                  </button>
                  <button onClick={() => remove(px.id)}
                    className="text-red-600 hover:bg-red-50 p-1.5 rounded"
                    data-testid={`pixel-delete-${px.id}`}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Yardım kutusu */}
      <section className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-xs text-blue-900 space-y-2">
        <p className="font-bold">⚡ Server-Side Tracking (CAPI) Nedir, Neden Önemli?</p>
        <ul className="list-disc pl-5 space-y-1">
          <li><b>AdBlocker bypass:</b> Reklam engelleyiciler tarayıcıdaki pixel'i engelleyebilir; sunucu taraflı gönderim engellenemez.</li>
          <li><b>iOS 14+ ITP:</b> Apple cihazlarda 7 günlük cookie sınırı sunucu taraflı gönderimle aşılır.</li>
          <li><b>Dedup:</b> Her event'in benzersiz <code>event_id</code>'si var → reklam platformları çift saymaz.</li>
          <li><b>Offline conversions:</b> Havale ödemesi onaylandığında ve iadelerde CAPI tetiklenir (ROAS düzeltimi).</li>
          <li><b>Retry:</b> CAPI çağrısı başarısız olursa kuyruğa alınır, 30 dk'da bir otomatik tekrar denenir.</li>
        </ul>
      </section>
    </div>
  );
}
