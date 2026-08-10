import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function MaintenanceSubscribers() {
  const [data, setData] = useState({ total: 0, subscribers: [] });

  useEffect(() => {
    const token = localStorage.getItem("token");
    axios
      .get(`${API}/settings/maintenance/subscribers`, { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => setData(res.data))
      .catch(() => {});
  }, []);

  const downloadCsv = () => {
    const rows = ["email,created_at", ...data.subscribers.map((s) => `${s.email},${s.created_at}`)];
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "bakim-aboneleri.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="mt-4 pt-4 border-t border-amber-200 flex items-center justify-between" data-testid="maintenance-subscribers">
      <p className="text-sm text-amber-900">
        <strong data-testid="maintenance-subscriber-count">{data.total}</strong> kişi açılış bildirimi için e-posta bıraktı.
      </p>
      {data.total > 0 && (
        <button
          type="button"
          onClick={downloadCsv}
          className="text-xs bg-amber-500 text-white px-3 py-1.5 rounded font-bold hover:bg-amber-600 transition-colors"
          data-testid="maintenance-subscribers-export"
        >
          CSV İndir
        </button>
      )}
    </div>
  );
}

const CARGO_COMPANIES = [
  { key: "aras", label: "Aras Kargo" },
  { key: "mng", label: "MNG Kargo" },
  { key: "yurtici", label: "Yurtiçi Kargo" },
  { key: "surat", label: "Sürat Kargo" },
  { key: "ptt", label: "PTT Kargo" },
  { key: "ups", label: "UPS" },
  { key: "sendeo", label: "Sendeo" },
  { key: "hepsijet", label: "HepsiJET" },
  { key: "trendyol_express", label: "Trendyol Express" },
];

export default function AdminSettings() {
  const [settings, setSettings] = useState({
    site_name: "FACETTE",
    logo_url: "",
    shipping_fee: 0,
    cargo_fees: {},
    default_cargo_company: "",
    rotating_texts: [],
    contact_email: "",
    contact_phone: "",
    address: "",
    payment_methods: {
      credit_card: true,
      bank_transfer: true,
      cash_on_delivery: false,
    },
    barcode_range_start: "",
    barcode_range_end: "",
    default_vat_rate: 10,
    trendyol_markup: 0,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      const res = await axios.get(`${API}/settings`);
      setSettings(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const token = localStorage.getItem('token');
      await axios.post(`${API}/settings`, settings, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success("Ayarlar kaydedildi");
    } catch (err) {
      toast.error("Kayıt başarısız: " + (err.response?.data?.detail || err.message));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="text-center py-8">Yükleniyor...</div>;
  }

  return (
    <div data-testid="admin-settings">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Site Ayarları</h1>
        <button 
          onClick={handleSave}
          disabled={saving}
          className="bg-black text-white px-6 py-2 rounded hover:bg-gray-800 disabled:opacity-50"
        >
          {saving ? "Kaydediliyor..." : "Kaydet"}
        </button>
      </div>

      <div className="space-y-6">
        {/* Maintenance Mode */}
        <div className="bg-amber-50 p-6 rounded-lg shadow-sm border border-amber-200" data-testid="maintenance-settings">
          <h2 className="text-lg font-medium mb-4 text-amber-900 flex items-center gap-2">
            <span className="w-2 h-6 bg-amber-500 rounded-full inline-block"></span>
            Bakım Modu (Bakım Modu)
          </h2>
          <label className="flex items-center gap-3 cursor-pointer mb-4">
            <input
              type="checkbox"
              checked={settings.maintenance_mode || false}
              onChange={(e) => setSettings({ ...settings, maintenance_mode: e.target.checked })}
              className="w-5 h-5"
              data-testid="maintenance-mode-toggle"
            />
            <span className="text-sm font-medium">
              Bakım modunu etkinleştir (müşteriler tam ekran bakım mesajı görür, admin erişimi açık kalır)
            </span>
          </label>
          {settings.maintenance_mode && (
            <div className="grid md:grid-cols-2 gap-4 mt-2">
              <div className="md:col-span-2">
                <label className="block text-sm font-medium mb-1 text-amber-900">Başlık</label>
                <input
                  type="text"
                  value={settings.maintenance_title || ""}
                  onChange={(e) => setSettings({ ...settings, maintenance_title: e.target.value })}
                  placeholder="Sitemiz sizin için yenileniyor"
                  className="w-full border border-amber-200 px-3 py-2 rounded text-sm focus:outline-none focus:border-amber-500"
                  data-testid="maintenance-title-input"
                />
              </div>
              <div className="md:col-span-2">
                <label className="block text-sm font-medium mb-1 text-amber-900">Mesaj</label>
                <textarea
                  value={settings.maintenance_message || ""}
                  onChange={(e) => setSettings({ ...settings, maintenance_message: e.target.value })}
                  rows={2}
                  placeholder="Çok yakında, daha iyi bir alışveriş deneyimiyle buradayız."
                  className="w-full border border-amber-200 px-3 py-2 rounded text-sm focus:outline-none focus:border-amber-500"
                  data-testid="maintenance-message-input"
                />
              </div>
            </div>
          )}
          <p className="text-xs text-amber-700 mt-3">
            Bakım modu açıkken yalnızca admin hesabıyla giriş yapan kullanıcılar siteyi normal görür. <strong>/admin</strong> paneline erişim her zaman açıktır.
          </p>
          <MaintenanceSubscribers />
        </div>

        {/* General */}
        <div className="bg-white p-6 rounded-lg shadow-sm">
          <h2 className="text-lg font-medium mb-4">Genel Ayarlar</h2>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Site Adı</label>
              <input
                type="text"
                value={settings.site_name}
                onChange={(e) => setSettings({ ...settings, site_name: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Logo URL</label>
              <input
                type="url"
                value={settings.logo_url}
                onChange={(e) => setSettings({ ...settings, logo_url: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div className="md:col-span-2 border rounded-lg p-3 bg-gray-50">
              <label className="block text-sm font-semibold mb-2">Kargo Ücretleri (firma bazında)</label>
              <p className="text-xs text-gray-500 mb-2">Müşteriye yansıyan kargo ücreti, aşağıda seçtiğiniz <b>Varsayılan Kargo Firması</b>'nın ücretidir. Ücretsiz kargo eşiği artık burada değil; <b>Kampanyalar</b> sayfasından "Otomatik uygula" işaretli bir <b>Ücretsiz Kargo</b> kampanyası ile yönetilir (örn. Min. Sipariş Tutarı: 3000).</p>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                {CARGO_COMPANIES.map((cc) => (
                  <div key={cc.key}>
                    <label className="block text-xs text-gray-600 mb-1">{cc.label} (TL)</label>
                    <input
                      type="number"
                      step="0.01"
                      value={(settings.cargo_fees || {})[cc.key] ?? ""}
                      onChange={(e) => setSettings({ ...settings, cargo_fees: { ...(settings.cargo_fees || {}), [cc.key]: e.target.value === "" ? "" : parseFloat(e.target.value) } })}
                      className="w-full border px-2 py-1.5 rounded text-sm"
                    />
                  </div>
                ))}
              </div>
              <div className="mt-3">
                <label className="block text-xs text-gray-600 mb-1">Varsayılan Kargo Firması (müşteriye yansıyan ücret)</label>
                <select
                  value={settings.default_cargo_company || ""}
                  onChange={(e) => setSettings({ ...settings, default_cargo_company: e.target.value })}
                  className="w-full border px-2 py-1.5 rounded text-sm"
                >
                  <option value="">Seçiniz</option>
                  {CARGO_COMPANIES.map((cc) => (<option key={cc.key} value={cc.key}>{cc.label}</option>))}
                </select>
              </div>
            </div>
            <div className="md:col-span-2 border rounded-lg p-3 bg-gray-50">
              <label className="block text-sm font-semibold mb-2">XML Feed'ler (Google / Facebook)</label>
              <div className="mb-2">
                <label className="block text-xs text-gray-600 mb-1">Mağaza Adresi (feed linklerinde kullanılır)</label>
                <input
                  type="text"
                  placeholder="https://facette.com.tr"
                  value={settings.site_url || ""}
                  onChange={(e) => setSettings({ ...settings, site_url: e.target.value })}
                  className="w-full border px-2 py-1.5 rounded text-sm"
                />
              </div>
              <a href="/admin/xml-feedler" className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded text-sm">XML Feed'leri Yönet →</a>
              <p className="text-xs text-gray-500 mt-1.5">Çoklu feed oluşturup her birine amaç (Google Merchant / Facebook Kataloğu) atayabilir, kendi linkini alıp ilgili panelde "planlı çekme" olarak tanımlayabilirsiniz.</p>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Varsayılan KDV Oranı (%)</label>
              <div className="flex gap-2">
                <input
                  type="number"
                  value={settings.default_vat_rate || 10}
                  onChange={(e) => setSettings({ ...settings, default_vat_rate: parseInt(e.target.value) || 0 })}
                  className="flex-1 border px-3 py-2 rounded text-sm"
                />
                <button
                  type="button"
                  onClick={async () => {
                    if (await window.appConfirm(`Tüm ürünlerin KDV oranını %${settings.default_vat_rate} olarak güncellemek istediğinize emin misiniz?`)) {
                      try {
                        const token = localStorage.getItem('token');
                        const res = await axios.post(`${API}/products/bulk-update-vat`, { vat_rate: settings.default_vat_rate }, {
                          headers: { Authorization: `Bearer ${token}` }
                        });
                        toast.success(res.data.message);
                      } catch (err) {
                        toast.error("İşlem başarısız");
                      }
                    }
                  }}
                  className="bg-orange-500 text-white px-3 py-2 rounded text-xs font-bold hover:bg-orange-600 transition-colors"
                >
                  Tüm Ürünlere Uygula
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Trendyol Integration Settings */}
        <div className="bg-orange-50 p-6 rounded-lg shadow-sm border border-orange-100">
          <h2 className="text-lg font-medium mb-4 text-orange-900 flex items-center gap-2">
            <span className="w-2 h-6 bg-orange-500 rounded-full inline-block"></span>
            Trendyol Entegrasyon Ayarları
          </h2>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1 text-orange-900">Global Trendyol Kâr Oranı (%)</label>
              <div className="flex gap-2">
                <input
                  type="number"
                  value={settings.trendyol_markup || 0}
                  onChange={(e) => setSettings({ ...settings, trendyol_markup: parseFloat(e.target.value) || 0 })}
                  className="w-full border-orange-200 border px-3 py-2 rounded text-sm focus:outline-none focus:border-orange-500 font-bold text-orange-700"
                  placeholder="Örn: 20"
                />
              </div>
              <p className="text-xs text-orange-600 mt-2">
                Trendyol fiyatlamasında "Global oranı kullan" seçilen ürünlerde otomatik eklenecek varsayılan markup (kâr / komisyon) yüzdesi.
              </p>
            </div>
          </div>
        </div>

        {/* Contact */}
        <div className="bg-white p-6 rounded-lg shadow-sm">
          <h2 className="text-lg font-medium mb-4">İletişim Bilgileri</h2>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">E-posta</label>
              <input
                type="email"
                value={settings.contact_email}
                onChange={(e) => setSettings({ ...settings, contact_email: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Telefon</label>
              <input
                type="tel"
                value={settings.contact_phone}
                onChange={(e) => setSettings({ ...settings, contact_phone: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-medium mb-1">Adres</label>
              <textarea
                value={settings.address}
                onChange={(e) => setSettings({ ...settings, address: e.target.value })}
                rows={2}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
          </div>
        </div>

        {/* Çerez Bildirimi (Cookie Consent) — sitede alt kısımda çıkan çerez barı metinleri */}
        <div className="bg-white p-6 rounded-lg shadow-sm">
          <h2 className="text-lg font-medium mb-1">Çerez Bildirimi</h2>
          <p className="text-xs text-gray-500 mb-4">Sitenin altında çıkan çerez izni barındaki metinler. Boş bırakılırsa varsayılan kullanılır.</p>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Başlık</label>
              <input
                type="text"
                value={settings.cookie_consent?.heading || ""}
                onChange={(e) => setSettings({ ...settings, cookie_consent: { ...(settings.cookie_consent || {}), heading: e.target.value } })}
                placeholder="Gizliliğinize önem veriyoruz"
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Gizlilik Politikası linki</label>
              <input
                type="text"
                value={settings.cookie_consent?.policy_url || ""}
                onChange={(e) => setSettings({ ...settings, cookie_consent: { ...(settings.cookie_consent || {}), policy_url: e.target.value } })}
                placeholder="/sayfa/gizlilik"
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-medium mb-1">Açıklama metni</label>
              <textarea
                value={settings.cookie_consent?.body || ""}
                onChange={(e) => setSettings({ ...settings, cookie_consent: { ...(settings.cookie_consent || {}), body: e.target.value } })}
                rows={3}
                placeholder="Deneyimini iyileştirmek, içerikleri kişiselleştirmek ve trafiği analiz etmek için çerezler kullanıyoruz. Tercihini istediğin zaman değiştirebilirsin."
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Link metni</label>
              <input
                type="text"
                value={settings.cookie_consent?.policy_label || ""}
                onChange={(e) => setSettings({ ...settings, cookie_consent: { ...(settings.cookie_consent || {}), policy_label: e.target.value } })}
                placeholder="Gizlilik Politikası"
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
          </div>
        </div>

        {/* Payment Methods */}
        <div className="bg-white p-6 rounded-lg shadow-sm">
          <h2 className="text-lg font-medium mb-4">Ödeme Yöntemleri</h2>
          <div className="space-y-3">
            <label className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={settings.payment_methods?.credit_card || false}
                onChange={(e) => setSettings({ 
                  ...settings, 
                  payment_methods: { ...settings.payment_methods, credit_card: e.target.checked }
                })}
              />
              <span className="text-sm">Kredi Kartı / Banka Kartı</span>
            </label>
            <label className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={settings.payment_methods?.bank_transfer || false}
                onChange={(e) => setSettings({ 
                  ...settings, 
                  payment_methods: { ...settings.payment_methods, bank_transfer: e.target.checked }
                })}
              />
              <span className="text-sm">Havale / EFT</span>
            </label>
            <label className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={settings.payment_methods?.cash_on_delivery || false}
                onChange={(e) => setSettings({ 
                  ...settings, 
                  payment_methods: { ...settings.payment_methods, cash_on_delivery: e.target.checked }
                })}
              />
              <span className="text-sm">Kapıda Ödeme</span>
            </label>
          </div>
        </div>

        {/* Company Info */}
        <div className="bg-white p-6 rounded-lg shadow-sm border-l-4 border-blue-500">
          <h2 className="text-lg font-medium mb-4 flex items-center gap-2">
            <span className="w-2 h-6 bg-blue-500 rounded-full inline-block"></span>
            Şirket Bilgileri
          </h2>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Firma Ünvanı</label>
              <input type="text" value={settings.company_info?.company_name || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), company_name: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm" placeholder="FACETTE DIŞ. TİC.A.Ş" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Vergi Kimlik No (VKN)</label>
              <input type="text" value={settings.company_info?.tax_number || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), tax_number: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm font-mono" placeholder="7810816779" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Vergi Dairesi</label>
              <input type="text" value={settings.company_info?.tax_office || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), tax_office: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm" placeholder="HALKALI VERGİ DAİRESİ BAŞKANLIĞI" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Web Sitesi</label>
              <input type="text" value={settings.company_info?.website || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), website: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm" placeholder="facette.com.tr" />
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-medium mb-1">Adres</label>
              <input type="text" value={settings.company_info?.address || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), address: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm" placeholder="İkitelli O.S.B. İmsan San. Sit. D BLOK NO:3" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">İl / İlçe</label>
              <input type="text" value={settings.company_info?.city || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), city: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm" placeholder="KÜÇÜKÇEKMECE/ İstanbul" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Firma E-postası</label>
              <input type="email" value={settings.company_info?.email || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), email: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm" placeholder="info@facette.com.tr" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Firma Telefonu</label>
              <input type="text" value={settings.company_info?.phone || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), phone: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm" placeholder="0212 000 00 00" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">IBAN</label>
              <input type="text" value={settings.company_info?.iban || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), iban: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm font-mono" placeholder="TR00 0000 0000 0000 0000 0000 00" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">WhatsApp (destek numarası)</label>
              <input type="text" value={settings.company_info?.whatsapp || ""}
                onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), whatsapp: e.target.value}})}
                className="w-full border px-3 py-2 rounded text-sm" placeholder="905000000000" />
            </div>
          </div>
          <div className="mt-5 pt-4 border-t">
            <p className="text-xs font-medium text-gray-600 mb-3">Sosyal Medya (e-posta ve site alt bilgisinde kullanılır)</p>
            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Instagram</label>
                <input type="text" value={settings.company_info?.instagram || ""}
                  onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), instagram: e.target.value}})}
                  className="w-full border px-3 py-2 rounded text-sm" placeholder="https://instagram.com/markaniz" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">TikTok</label>
                <input type="text" value={settings.company_info?.tiktok || ""}
                  onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), tiktok: e.target.value}})}
                  className="w-full border px-3 py-2 rounded text-sm" placeholder="https://tiktok.com/@markaniz" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Facebook</label>
                <input type="text" value={settings.company_info?.facebook || ""}
                  onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), facebook: e.target.value}})}
                  className="w-full border px-3 py-2 rounded text-sm" placeholder="https://facebook.com/markaniz" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">X (Twitter)</label>
                <input type="text" value={settings.company_info?.x || ""}
                  onChange={(e) => setSettings({...settings, company_info: {...(settings.company_info || {}), x: e.target.value}})}
                  className="w-full border px-3 py-2 rounded text-sm" placeholder="https://x.com/markaniz" />
              </div>
            </div>
            <p className="text-[11px] text-gray-400 mt-3">
              Bu bilgiler beyaz-etiket firma kimliğidir: e-posta şablonları (logo/sosyal), e-fatura tedarikçi ve site alt bilgisi bu alanlardan beslenir. Yeni bir firma yalnızca bu formu doldurarak koda dokunmadan geçirilebilir.
            </p>
          </div>
        </div>

        {/* Barcode Settings */}
        <div className="bg-white p-6 rounded-lg shadow-sm">
          <h2 className="text-lg font-medium mb-1">Barkod Aralığı (GTIN-13)</h2>
          <p className="text-xs text-gray-500 mb-4">
            Varyant kaydederken "Oluştur" butonuna basıldığında, sistem bu aralık içinden çakışmayan 13 haneli benzersiz bir barkod üretir.
          </p>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Aralık Başlangıcı</label>
              <input
                type="text"
                value={settings.barcode_range_start || ""}
                onChange={(e) => setSettings({ ...settings, barcode_range_start: e.target.value })}
                placeholder="Örn: 8680000000001"
                className="w-full border px-3 py-2 rounded text-sm font-mono"
                maxLength={13}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Aralık Sonu</label>
              <input
                type="text"
                value={settings.barcode_range_end || ""}
                onChange={(e) => setSettings({ ...settings, barcode_range_end: e.target.value })}
                placeholder="Örn: 8689999999999"
                className="w-full border px-3 py-2 rounded text-sm font-mono"
                maxLength={13}
              />
            </div>
          </div>
          <div className="mt-3 p-3 bg-blue-50 rounded text-xs text-blue-700">
            <strong>Not:</strong> Bu aralığa 13 haneli sayısal değerler giriniz. Sistem, sistemdeki diğer ürünlerin barkodlarıyla çakışmayacak şekilde otomatik olarak bir değer seçtiren bir barkod atar.
          </div>
        </div>
      </div>
    </div>
  );
}
