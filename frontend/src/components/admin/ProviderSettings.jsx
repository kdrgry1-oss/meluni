/**
 * =============================================================================
 * ProviderSettings.jsx — Jenerik Provider Seçim + Form Sayfası
 * =============================================================================
 *
 * AMAÇ:
 *   E-Fatura entegratörleri ve kargo firmaları için TEK BİR component ile
 *   provider seçimi + dinamik credential form'u yönetmek. Ticimax'in
 *   e-Arşiv/E-Fatura ayarları sayfasındaki mantıkla aynı:
 *     1) Soldan bir provider seç.
 *     2) Sağda yalnızca o provider'ın ihtiyaç duyduğu alanlar çıkar.
 *     3) Kaydet → bundan sonra "aktif provider" olarak işaretlenir.
 *     4) Opsiyonel: "Bağlantıyı Test Et" butonu.
 *
 * PROPS:
 *   - kind       : "einvoice" | "cargo"
 *   - title      : Sayfa başlığı (ör. "E-Arşiv / E-Fatura Ayarları").
 *   - subtitle   : Sayfa alt açıklaması.
 *
 * BACKEND:
 *   GET  /api/provider-settings/{kind}/schemas  → tüm provider şemaları
 *   GET  /api/provider-settings/{kind}/config   → aktif provider + kayıtlı
 *                                                  credential'lar
 *   POST /api/provider-settings/{kind}/config   → değişiklikleri kaydet
 *   POST /api/provider-settings/{kind}/test     → bağlantı testi (mock)
 *
 * KULLANAN SAYFALAR:
 *   - EInvoiceSettings.jsx → <ProviderSettings kind="einvoice" .../>
 *   - CargoSettings.jsx    → <ProviderSettings kind="cargo" .../>
 *
 * NEDEN JENERİK?
 *   İki iş akışı da özdeş: seç → credential gir → kaydet → test et. Ayrı
 *   iki sayfa yazmak duplikasyon yaratır; yeni provider eklemek için
 *   yalnızca backend şemasını güncellemek yeterli olmalı.
 * =============================================================================
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  CheckCircle2,
  Circle,
  ExternalLink,
  Save,
  Zap,
  Search,
  ShieldCheck,
  Eye,
  EyeOff,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * DynamicField — Şemadaki tek bir alanı (text/password/number/select) render eder.
 *   Şifreler için göz simgesi ile görünürlük toggle edilebilir.
 */
function DynamicField({ field, value, onChange }) {
  const [show, setShow] = useState(false);
  const isPassword = field.type === "password";

  if (field.type === "select") {
    return (
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1">
          {field.label} {field.required && <span className="text-red-500">*</span>}
        </label>
        <select
          value={value || ""}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white outline-none focus:border-black"
          data-testid={`provider-field-${field.key}`}
        >
          <option value="">Seçiniz...</option>
          {(field.options || []).map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        {field.help && <p className="text-[11px] text-gray-400 mt-1">{field.help}</p>}
      </div>
    );
  }

  return (
    <div>
      <label className="block text-xs font-semibold text-gray-600 mb-1">
        {field.label} {field.required && <span className="text-red-500">*</span>}
      </label>
      <div className="relative">
        <input
          type={isPassword && !show ? "password" : field.type === "number" ? "number" : "text"}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder || ""}
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white outline-none focus:border-black pr-9"
          data-testid={`provider-field-${field.key}`}
        />
        {isPassword && (
          <button
            type="button"
            onClick={() => setShow((s) => !s)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700"
            tabIndex={-1}
          >
            {show ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        )}
      </div>
      {field.help && <p className="text-[11px] text-gray-400 mt-1">{field.help}</p>}
    </div>
  );
}

export default function ProviderSettings({ kind, title, subtitle }) {
  // ---------------------------------------------------------------------------
  // State:
  // - schemas      : Backend'ten gelen provider listesi + alan şemaları
  // - activeKey    : Seçili (ve sistemde "aktif" olacak) provider'ın anahtarı
  // - providers    : { providerKey: {field: value, ...} } — kullanıcının
  //                   girdiği credential'lar. Tüm provider'lar bir arada
  //                   tutulur; kullanıcı A'dan B'ye geçince girdileri kaybolmaz.
  // - search       : Provider listesi arama kutusu
  // ---------------------------------------------------------------------------
  const [schemas, setSchemas] = useState([]);
  const [activeKey, setActiveKey] = useState(null);
  const [providers, setProviders] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const token = localStorage.getItem("token");
        const auth = { headers: { Authorization: `Bearer ${token}` } };
        const [schRes, cfgRes] = await Promise.all([
          axios.get(`${API}/provider-settings/${kind}/schemas`, auth),
          axios.get(`${API}/provider-settings/${kind}/config`, auth),
        ]);
        if (cancelled) return;
        const list = schRes.data?.providers || [];
        setSchemas(list);
        setProviders(cfgRes.data?.providers || {});
        const ak = cfgRes.data?.active_provider || list[0]?.key || null;
        setActiveKey(ak);
      } catch (err) {
        toast.error("Ayarlar yüklenemedi");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [kind]);

  // Aktif provider'ın şeması (sağ paneli besler)
  const currentSchema = useMemo(
    () => schemas.find((p) => p.key === activeKey),
    [schemas, activeKey]
  );
  const currentConfig = providers[activeKey] || {};

  // Arama filtresi (provider listesi)
  const filteredSchemas = useMemo(() => {
    if (!search) return schemas;
    const s = search.toLocaleLowerCase("tr");
    return schemas.filter((p) =>
      (p.name || "").toLocaleLowerCase("tr").includes(s) ||
      (p.key || "").toLocaleLowerCase("tr").includes(s)
    );
  }, [schemas, search]);

  const updateField = (key, value) => {
    setProviders((prev) => ({
      ...prev,
      [activeKey]: { ...(prev[activeKey] || {}), [key]: value },
    }));
  };

  const save = async () => {
    setSaving(true);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.post(
        `${API}/provider-settings/${kind}/config`,
        { active_provider: activeKey, providers },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      toast.success(res.data?.message || "Kaydedildi");
    } catch (err) {
      toast.error("Kayıt başarısız: " + (err.response?.data?.detail || err.message));
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.post(
        `${API}/provider-settings/${kind}/test`,
        { provider: activeKey, config: currentConfig },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.data?.success) toast.success(res.data.message);
      else toast.error(res.data?.message || "Test başarısız");
    } catch (err) {
      toast.error("Test edilemedi: " + (err.response?.data?.detail || err.message));
    } finally {
      setTesting(false);
    }
  };

  if (loading) return <div className="py-10 text-center text-sm text-gray-500">Yükleniyor…</div>;

  return (
    <div data-testid={`provider-settings-${kind}`}>
      {/* Başlık ----------------------------------------------------------- */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">{title}</h1>
          {subtitle && <p className="text-sm text-gray-500 mt-1">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={test}
            disabled={testing || !activeKey}
            className="flex items-center gap-1 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
            data-testid="provider-settings-test-btn"
          >
            <Zap size={14} />
            {testing ? "Test ediliyor..." : "Bağlantıyı Test Et"}
          </button>
          <button
            onClick={save}
            disabled={saving}
            className="flex items-center gap-1 bg-black text-white px-4 py-2 rounded-lg text-sm hover:bg-gray-800 disabled:opacity-50"
            data-testid="provider-settings-save-btn"
          >
            <Save size={14} />
            {saving ? "Kaydediliyor..." : "Kaydet"}
          </button>
        </div>
      </div>

      {/* Aktif provider bilgi kartı --------------------------------------- */}
      {activeKey && currentSchema && (
        <div className="mb-4 flex items-center gap-3 p-3 bg-green-50 border border-green-200 rounded-lg">
          <ShieldCheck size={18} className="text-green-600" />
          <div className="flex-1 text-sm">
            <span className="font-semibold text-gray-800">{currentSchema.name}</span>
            <span className="text-gray-500 ml-2">aktif entegratör olarak kullanılacak.</span>
          </div>
          {currentSchema.website && (
            <a
              href={currentSchema.website}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-green-700 hover:underline flex items-center gap-1"
            >
              Site <ExternalLink size={12} />
            </a>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* SOL: PROVIDER LİSTESİ ----------------------------------------- */}
        <div className="lg:col-span-4 bg-white border rounded-xl shadow-sm overflow-hidden">
          <div className="p-3 border-b bg-gray-50">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Entegratör ara..."
                className="w-full border border-gray-200 rounded-lg pl-8 pr-3 py-1.5 text-sm outline-none focus:border-black"
                data-testid="provider-search-input"
              />
            </div>
          </div>
          <div className="max-h-[620px] overflow-y-auto">
            {filteredSchemas.map((p) => {
              const isActive = p.key === activeKey;
              const hasConfig = providers[p.key] && Object.values(providers[p.key]).some((v) => v);
              return (
                <button
                  key={p.key}
                  onClick={() => setActiveKey(p.key)}
                  className={`w-full flex items-start gap-3 px-4 py-3 text-left border-b transition-colors ${
                    isActive ? "bg-orange-50 border-l-4 border-l-orange-500" : "hover:bg-gray-50"
                  }`}
                  data-testid={`provider-row-${p.key}`}
                >
                  {isActive ? (
                    <CheckCircle2 size={18} className="text-orange-500 mt-0.5 shrink-0" />
                  ) : (
                    <Circle size={18} className="text-gray-300 mt-0.5 shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm text-gray-900 truncate">{p.name}</span>
                      {hasConfig && !isActive && (
                        <span className="text-[10px] font-bold bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">
                          KAYITLI
                        </span>
                      )}
                    </div>
                    {p.description && (
                      <p className="text-xs text-gray-500 line-clamp-2 mt-0.5">{p.description}</p>
                    )}
                  </div>
                </button>
              );
            })}
            {filteredSchemas.length === 0 && (
              <p className="p-6 text-center text-xs text-gray-400">Sonuç bulunamadı</p>
            )}
          </div>
        </div>

        {/* SAĞ: SEÇİLEN PROVIDER'IN FORMU -------------------------------- */}
        <div className="lg:col-span-8 bg-white border rounded-xl shadow-sm">
          {!currentSchema ? (
            <div className="p-10 text-center text-sm text-gray-400">
              Soldan bir entegratör seçin.
            </div>
          ) : (
            <div className="p-6">
              <div className="flex items-start justify-between mb-5">
                <div>
                  <h2 className="text-lg font-bold text-gray-900">{currentSchema.name}</h2>
                  {currentSchema.description && (
                    <p className="text-xs text-gray-500 mt-1 max-w-xl">
                      {currentSchema.description}
                    </p>
                  )}
                </div>
                {currentSchema.website && (
                  <a
                    href={currentSchema.website}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-gray-500 hover:text-black flex items-center gap-1"
                  >
                    {currentSchema.website.replace(/^https?:\/\//, "")}
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>

              <div className="grid md:grid-cols-2 gap-4">
                {(currentSchema.fields || []).map((field) => (
                  <DynamicField
                    key={field.key}
                    field={field}
                    value={currentConfig[field.key]}
                    onChange={(v) => updateField(field.key, v)}
                  />
                ))}
              </div>

              {/* MNG Webhook Info - sadece kind=cargo + provider=mng için */}
              {kind === "cargo" && (activeKey === "mng" || activeKey === "mng_kargo") && (
                <MngWebhookInfo />
              )}

              <p className="text-[11px] text-gray-400 mt-6 border-t pt-4">
                Şifre ve API anahtarları şifreli saklanmalıdır. Bu sayfadan
                sadece yetkili yöneticiler (admin) erişebilir. Gerçek bağlantı
                (fatura kesme / kargo etiketi) <strong>Bağlantıyı Test Et</strong> başarılı döndüğünde
                aktif olur.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * MngWebhookInfo — MNG Kargo durum güncelleme webhook URL'ini gösterir.
 * Admin bu URL'i MNG paneline tanımlar; kargo durumu değiştiğinde sipariş
 * otomatik `shipped`/`delivered` durumuna geçer.
 */
function MngWebhookInfo() {
  const url = `${process.env.REACT_APP_BACKEND_URL}/api/orders/cargo/mng-webhook`;
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      toast.success("Webhook URL kopyalandı");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Kopyalanamadı");
    }
  };
  return (
    <div className="mt-6 p-4 bg-amber-50 border border-amber-200 rounded-lg" data-testid="mng-webhook-info">
      <h3 className="text-sm font-semibold text-amber-900 mb-2">📡 DHL E-Commerce Kargo Durum Güncelleme Webhook</h3>
      <p className="text-xs text-amber-800 mb-3 leading-relaxed">
        DHL E-Commerce paneline / entegrasyon ekibine aşağıdaki URL'i tanımlatın. Kargo durumu değiştiğinde DHL bu adrese POST isteği gönderir
        ve sipariş otomatik olarak <code className="bg-amber-100 px-1 rounded">shipped</code> / <code className="bg-amber-100 px-1 rounded">delivered</code> durumuna geçer.
      </p>
      <div className="flex items-center gap-2 mb-3">
        <code className="flex-1 bg-white border border-amber-200 rounded px-3 py-2 text-xs font-mono text-gray-800 select-all break-all">
          {url}
        </code>
        <button
          onClick={copy}
          className="px-3 py-2 bg-amber-600 hover:bg-amber-700 text-white text-xs rounded font-medium whitespace-nowrap transition-colors"
          data-testid="copy-mng-webhook-btn"
        >
          {copied ? "✓ Kopyalandı" : "Kopyala"}
        </button>
      </div>
      <details className="text-xs text-amber-800">
        <summary className="cursor-pointer font-medium hover:underline">Beklenen payload formatı (örnek)</summary>
        <pre className="mt-2 bg-white border border-amber-200 rounded p-2 overflow-x-auto font-mono text-[11px]">
{`{
  "BARKOD": "OA341862",        // DHL E-Commerce kargo barkodu
  "ISLEM_KODU": "300",         // 100/200/300/400/500
  "ISLEM_ADI": "Dağıtımda",
  "TARIH": "2026-05-07T22:30:00",
  "REFERANS_NO": "FC1777948407" // Sipariş numarası (opsiyonel)
}`}
        </pre>
        <p className="mt-2">
          <strong>İşlem kodları:</strong>
          <span className="ml-2">100=Şubeye Girdi, 200=Transferde, 300=Dağıtımda, 400=Teslim Edildi, 500=İade</span>
        </p>
      </details>
    </div>
  );
}
