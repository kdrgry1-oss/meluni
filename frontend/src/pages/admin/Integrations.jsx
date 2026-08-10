import { useState, useEffect } from "react";
import { Store, CreditCard, Truck, MessageSquare, FileText, RefreshCw, Check, X, AlertCircle, Upload, Download, Package, Database, ShoppingBag, Users } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";


const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function Integrations() {
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [statuses, setStatuses] = useState({
    iyzico: { configured: false, mode: "sandbox" },
    trendyol: { configured: false, mode: "sandbox" },
    hepsiburada: { configured: false, mode: "sandbox" },
    temu: { configured: false, mode: "sandbox" },
    mng: { configured: true, mode: "live" },
    netgsm: { configured: false, mode: "sandbox" },
    gib: { configured: false, mode: "test" }
  });
  const [trendyolOrders, setTrendyolOrders] = useState([]);
  const [trendyolModalOpen, setTrendyolModalOpen] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [trendyolSettings, setTrendyolSettings] = useState({
    supplier_id: "",
    api_key: "",
    api_secret: "",
    mode: "sandbox",
    is_active: false,
    default_markup: 0,
    default_brand_id: "",
    default_cargo_company_id: "",
    default_vat_rate: ""
  });

  // Hepsiburada state
  const [hbModalOpen, setHbModalOpen] = useState(false);
  const [hbSettings, setHbSettings] = useState({
    merchant_id: "", secret_key: "", dev_username: "", api_key: "", api_secret: "",
    mode: "sandbox", is_active: false, default_markup: 0
  });
  const [hbTesting, setHbTesting] = useState(false);

  // Temu state
  const [temuModalOpen, setTemuModalOpen] = useState(false);
  const [temuSettings, setTemuSettings] = useState({
    merchant_id: "", username: "", api_key: "", api_secret: "",
    mode: "sandbox", is_active: false, default_markup: 0
  });
  const [temuTesting, setTemuTesting] = useState(false);

  // Iyzico state
  const [iyzicoModalOpen, setIyzicoModalOpen] = useState(false);
  const [iyzicoSettings, setIyzicoSettings] = useState({
    api_key: "", api_secret: "", mode: "sandbox", is_active: false
  });
  const [iyzicoTesting, setIyzicoTesting] = useState(false);

  // Cargo providers state (generic)
  const [cargoModalOpen, setCargoModalOpen] = useState(false);
  const [activeCargoProvider, setActiveCargoProvider] = useState(null);
  const [cargoSettings, setCargoSettings] = useState({
    username: "", password: "", api_key: "", customer_code: "",
    api_secret: "", mode: "sandbox", is_active: false
  });

  // XML Feed state
  const [xmlImporting, setXmlImporting] = useState(false);
  const [xmlLastSync, setXmlLastSync] = useState(null);

  // Integration Logs State
  const [logsModalOpen, setLogsModalOpen] = useState(false);
  const [integrationLogs, setIntegrationLogs] = useState([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logFilters, setLogFilters] = useState({ platform: "", status: "" });
  const [doganTesting, setDoganTesting] = useState(false);

  const fetchLogs = async () => {
    setLogsLoading(true);
    try {
      const token = localStorage.getItem('token');
      const params = new URLSearchParams();
      if (logFilters.platform) params.append("platform", logFilters.platform);
      if (logFilters.status) params.append("status", logFilters.status);
      
      const res = await axios.get(`${API}/integrations/integration-logs?${params.toString()}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data.success) {
        setIntegrationLogs(res.data.logs || []);
      }
    } catch (err) {
      toast.error("Loglar alınamadı");
    } finally {
      setLogsLoading(false);
    }
  };

  useEffect(() => {
    if (logsModalOpen) {
      fetchLogs();
    }
  }, [logsModalOpen, logFilters]);

  useEffect(() => {
    fetchStatuses();
  }, []);

  const fetchStatuses = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };

      // Fetch integration statuses
      const [paymentRes, trendyolRes, gibRes, xmlRes, hbRes, temuRes] = await Promise.all([
        axios.get(`${API}/integrations/payment/status`, { headers }).catch(() => ({ data: { configured: false, mode: "sandbox" } })),
        axios.get(`${API}/integrations/trendyol/status`, { headers }).catch(() => ({ data: { configured: false, mode: "sandbox" } })),
        axios.get(`${API}/integrations/dogan/settings`, { headers }).catch(() => ({ data: { enabled: false } })),
        axios.get(`${API}/integrations/xml/status`, { headers }).catch(() => ({ data: { last_sync: null } })),
        axios.get(`${API}/integrations/hepsiburada/status`, { headers }).catch(() => ({ data: { configured: false, mode: "sandbox" } })),
        axios.get(`${API}/integrations/temu/status`, { headers }).catch(() => ({ data: { configured: false, mode: "sandbox" } }))
      ]);

      setStatuses(prev => ({
        ...prev,
        iyzico: paymentRes.data,
        trendyol: trendyolRes.data,
        hepsiburada: hbRes.data,
        temu: temuRes.data,
        gib: { configured: gibRes.data?.enabled || false, mode: gibRes.data?.is_test ? "test" : "live" }
      }));
      setXmlLastSync(xmlRes.data?.last_sync || null);
    } catch (err) {
      console.error("Status fetch error:", err);
    } finally {
      setLoading(false);
    }
  };

  const fetchTrendyolSettings = async () => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/integrations/trendyol/settings?t=${Date.now()}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setTrendyolSettings(res.data);
      setTrendyolModalOpen(true);
    } catch (err) {
      toast.error("Ayarlar alınamadı");
    }
  };

  const saveTrendyolSettings = async (e) => {
    e.preventDefault();
    setSavingSettings(true);
    try {
      const token = localStorage.getItem('token');
      await axios.post(`${API}/integrations/trendyol/settings`, trendyolSettings, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success("Trendyol ayarları kaydedildi");
      setTrendyolModalOpen(false);
      fetchStatuses();
    } catch (err) {
      toast.error("Ayarlar kaydedilemedi");
    } finally {
      setSavingSettings(false);
    }
  };

  // -- Generic marketplace (Hepsiburada / Temu) settings loaders --
  const fetchMarketplaceSettings = async (mp, setter, openSetter) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/integrations/${mp}/settings?t=${Date.now()}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setter(res.data);
      openSetter(true);
    } catch (err) {
      toast.error("Ayarlar alınamadı");
    }
  };

  const saveMarketplaceSettings = async (mp, settings, closeFn) => {
    setSavingSettings(true);
    try {
      const token = localStorage.getItem('token');
      await axios.post(`${API}/integrations/${mp}/settings`, settings, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success(`${mp === 'hepsiburada' ? 'Hepsiburada' : 'Temu'} ayarları kaydedildi`);
      closeFn(false);
      fetchStatuses();
    } catch (err) {
      toast.error("Ayarlar kaydedilemedi");
    } finally {
      setSavingSettings(false);
    }
  };

  const testMarketplaceConnection = async (mp, setTesting) => {
    setTesting(true);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/${mp}/test-connection`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data.success) toast.success(res.data.message);
      else toast.error(res.data.message);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Test başarısız");
    } finally {
      setTesting(false);
    }
  };

  const handleTrendyolSync = async () => {
    setSyncing(true);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/trendyol/products/sync`, null, {
        headers: { Authorization: `Bearer ${token}` }
      });
      
      if (res.data.success) {
        toast.success(`${res.data.products_sent} ürün Trendyol'a gönderildi`);
      } else {
        toast.error(res.data.error || "Senkronizasyon başarısız");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Senkronizasyon hatası");
    } finally {
      setSyncing(false);
    }
  };

  const handleTrendyolInventorySync = async () => {
    setSyncing(true);
    toast.info("Stok/Fiyat senkronizasyonu başlatılıyor...");
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/trendyol/products/inventory-sync`, null, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data.success) {
        toast.success(res.data.message);
      } else {
        toast.error(res.data.message || "Senkronizasyon başarısız");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Senkronizasyon hatası");
    } finally {
      setSyncing(false);
    }
  };

  const handleTrendyolCategoriesSync = async () => {
    setSyncing(true);
    toast.info("Trendyol kategorileri çekiliyor...");
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/trendyol/categories/sync`, null, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data.success) {
        toast.success(res.data.message);
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kategori çekme hatası");
    } finally {
      setSyncing(false);
    }
  };

  const handleTrendyolBrandsSync = async () => {
    setSyncing(true);
    toast.info("Trendyol markaları çekiliyor...");
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/trendyol/brands/sync`, null, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data.success) {
        toast.success(res.data.message);
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Marka çekme hatası");
    } finally {
      setSyncing(false);
    }
  };

  const handleTrendyolImport = async () => {
    setImporting(true);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/trendyol/orders/import`, null, {
        headers: { Authorization: `Bearer ${token}` }
      });
      
      if (res.data.success) {
        toast.success(res.data.message);
      } else {
        toast.error("İçe aktarma başarısız");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "İçe aktarma hatası");
    } finally {
      setImporting(false);
    }
  };



  // ---- XML Feed Handler ----
  const handleXmlImport = async () => {
    setXmlImporting(true);
    toast.info("XML feed'den ürünler çekiliyor...");
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/xml/products/import`, null, {
        headers: { Authorization: `Bearer ${token}` },
        timeout: 120000
      });
      if (res.data.success) {
        toast.success(res.data.message || `${res.data.total} ürün aktarıldı`);
        setXmlLastSync(new Date().toISOString());
      } else {
        toast.error("XML aktarımı başarısız");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "XML import hatası");
    } finally {
      setXmlImporting(false);
    }
  };

  const integrations = [
    {
      id: "iyzico",
      name: "Iyzico",
      description: "Online ödeme altyapısı - Kredi kartı, banka kartı ödemeleri ve kart iadeleri",
      icon: <CreditCard className="w-8 h-8" />,
      status: statuses.iyzico,
      color: "blue",
      instructions: "Iyzico Merchant panelinden API Key ve Secret Key alınız. Kart iadeleri için zorunlu.",
      envKeys: ["IYZICO_API_KEY", "IYZICO_SECRET_KEY", "IYZICO_MODE"],
      actions: [
        { label: "Ayarları Yapılandır", icon: <CreditCard size={16} />, onClick: () => fetchMarketplaceSettings('iyzico', setIyzicoSettings, setIyzicoModalOpen), loading: false },
        { label: "Bağlantı Test Et", icon: <RefreshCw size={16} />, onClick: () => testMarketplaceConnection('iyzico', setIyzicoTesting), loading: iyzicoTesting, disabled: !statuses.iyzico?.configured }
      ]
    },
    {
      id: "mng",
      name: "DHL E-Commerce",
      description: "Kargo takip, etiket basımı, gönderi oluşturma",
      icon: <Truck className="w-8 h-8" />,
      status: statuses.mng,
      color: "green",
      instructions: "DHL E-Commerce (eski MNG Kargo) Müşteri Portalı'ndan API kullanıcı + şifresi alınız.",
      envKeys: [],
      actions: [
        { label: "Ayarları Yapılandır", icon: <Truck size={16} />, onClick: () => fetchMarketplaceSettings('mng', setCargoSettings, (v) => { setActiveCargoProvider('mng'); setCargoModalOpen(v); }) },
      ]
    },
    {
      id: "aras",
      name: "Aras Kargo",
      description: "Aras Kargo API - gönderi oluşturma, takip, etiket",
      icon: <Truck className="w-8 h-8" />,
      status: statuses.aras || { configured: false, mode: "sandbox" },
      color: "blue",
      instructions: "Aras Kargo Entegrasyon Merkezi'nden kullanıcı adı, şifre ve müşteri kodu alınız.",
      envKeys: [],
      actions: [
        { label: "Ayarları Yapılandır", icon: <Truck size={16} />, onClick: () => fetchMarketplaceSettings('aras', setCargoSettings, (v) => { setActiveCargoProvider('aras'); setCargoModalOpen(v); }) },
      ]
    },
    {
      id: "yurtici",
      name: "Yurtiçi Kargo",
      description: "Yurtiçi Kargo entegrasyonu - etiket, takip, teslimat",
      icon: <Truck className="w-8 h-8" />,
      status: statuses.yurtici || { configured: false, mode: "sandbox" },
      color: "red",
      instructions: "Yurtiçi Kargo API hesabınızdan WS User/Password alınız.",
      envKeys: [],
      actions: [
        { label: "Ayarları Yapılandır", icon: <Truck size={16} />, onClick: () => fetchMarketplaceSettings('yurtici', setCargoSettings, (v) => { setActiveCargoProvider('yurtici'); setCargoModalOpen(v); }) },
      ]
    },
    {
      id: "ptt",
      name: "PTT Kargo",
      description: "PTT Kargo - devlet entegrasyonu, en ekonomik gönderi",
      icon: <Truck className="w-8 h-8" />,
      status: statuses.ptt || { configured: false, mode: "sandbox" },
      color: "yellow",
      instructions: "PTT Kargo Müşteri Hesap Sözleşmesi kurumsal müşteri numarası ile aktif olur.",
      envKeys: [],
      actions: [
        { label: "Ayarları Yapılandır", icon: <Truck size={16} />, onClick: () => fetchMarketplaceSettings('ptt', setCargoSettings, (v) => { setActiveCargoProvider('ptt'); setCargoModalOpen(v); }) },
      ]
    },
    {
      id: "hepsijet",
      name: "HepsiJet",
      description: "HepsiJet Hızlı Teslimat - Hepsiburada'nın kargosu",
      icon: <Truck className="w-8 h-8" />,
      status: statuses.hepsijet || { configured: false, mode: "sandbox" },
      color: "orange",
      instructions: "HepsiJet Merchant Panel > API Sayfası > Key/Secret alınız.",
      envKeys: [],
      actions: [
        { label: "Ayarları Yapılandır", icon: <Truck size={16} />, onClick: () => fetchMarketplaceSettings('hepsijet', setCargoSettings, (v) => { setActiveCargoProvider('hepsijet'); setCargoModalOpen(v); }) },
      ]
    },
    {
      id: "trendyol_express",
      name: "Trendyol Express",
      description: "Trendyol Express - Marketplace ve kendi siteniz için",
      icon: <Truck className="w-8 h-8" />,
      status: statuses.trendyol_express || { configured: false, mode: "sandbox" },
      color: "orange",
      instructions: "Trendyol Satıcı Paneli > Entegrasyon Bilgileri'nden alınabilir.",
      envKeys: [],
      actions: [
        { label: "Ayarları Yapılandır", icon: <Truck size={16} />, onClick: () => fetchMarketplaceSettings('trendyol_express', setCargoSettings, (v) => { setActiveCargoProvider('trendyol_express'); setCargoModalOpen(v); }) },
      ]
    },
    {
      id: "surat",
      name: "Sürat Kargo",
      description: "Sürat Kargo - etiket basımı, takip",
      icon: <Truck className="w-8 h-8" />,
      status: statuses.surat || { configured: false, mode: "sandbox" },
      color: "purple",
      instructions: "Sürat Kargo Kurumsal Müşteri Hesabı ile API erişimi sağlanır.",
      envKeys: [],
      actions: [
        { label: "Ayarları Yapılandır", icon: <Truck size={16} />, onClick: () => fetchMarketplaceSettings('surat', setCargoSettings, (v) => { setActiveCargoProvider('surat'); setCargoModalOpen(v); }) },
      ]
    },
    {
      id: "gib",
      name: "Doğan e-Dönüşüm",
      description: "E-Fatura, E-Arşiv, E-İrsaliye - Doğan e-Dönüşüm entegrasyonu",
      icon: <FileText className="w-8 h-8" />,
      status: statuses.gib,
      color: "blue",
      instructions: "Doğan e-Dönüşüm test ortamı yapılandırıldı. Bağlantı test edildi.",
      envKeys: [],
      actions: [
        { label: "Bağlantı Test Et", icon: <RefreshCw size={16} />, onClick: async () => {
          setDoganTesting(true);
          try {
            const token = localStorage.getItem("token");
            const res = await axios.post(`${API}/integrations/dogan/test-connection`, {}, { headers: { Authorization: `Bearer ${token}` } });
            if (res.data.success) toast.success(res.data.message || "Bağlantı başarılı");
            else toast.error(res.data.message || "Bağlantı hatası");
          } catch (err) { toast.error(err.response?.data?.detail || "Test hatası"); }
          finally { setDoganTesting(false); }
        }, loading: doganTesting },
      ]
    },
    {
      id: "netgsm",
      name: "Netgsm SMS",
      description: "SMS bildirimleri, sipariş durumu güncellemeleri",
      icon: <MessageSquare className="w-8 h-8" />,
      status: statuses.netgsm,
      color: "purple",
      instructions: "Netgsm panel'den API bilgilerini alınız.",
      envKeys: ["NETGSM_USERNAME", "NETGSM_PASSWORD", "NETGSM_HEADER"]
    }
  ];

  return (
    <div data-testid="integrations-page">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Entegrasyonlar</h1>
          <p className="text-sm text-gray-500 mt-1">Pazaryeri ve ödeme entegrasyonlarını yönetin</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setLogsModalOpen(true)}
            className="flex items-center gap-2 px-4 py-2 bg-gray-100 border text-sm rounded hover:bg-gray-200"
          >
            <FileText size={16} /> Entegrasyon Logları
          </button>
          <button 
            onClick={fetchStatuses}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 border rounded hover:bg-gray-50 text-sm"
          >
            <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
            Durumu Güncelle
          </button>
        </div>
      </div>

      {/* Integration Cards */}
      <div className="grid md:grid-cols-2 gap-6">
        {integrations.map((integration) => (
          <div 
            key={integration.id}
            className={`bg-white rounded-lg border-2 p-6 transition-all ${
              integration.status.configured 
                ? "border-green-200 bg-green-50/30" 
                : "border-gray-200"
            }`}
          >
            <div className="flex items-start gap-4">
              {/* Icon */}
              <div className={`p-3 rounded-lg ${
                integration.status.configured 
                  ? "bg-green-100 text-green-600" 
                  : "bg-gray-100 text-gray-400"
              }`}>
                {integration.icon}
              </div>

              {/* Content */}
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-semibold text-lg">{integration.name}</h3>
                  {integration.status.configured ? (
                    <span className="flex items-center gap-1 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">
                      <Check size={12} /> Aktif
                    </span>
                  ) : integration.status.mode === "planned" ? (
                    <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                      Yakında
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full">
                      <AlertCircle size={12} /> Yapılandırılmamış
                    </span>
                  )}
                </div>
                
                <p className="text-sm text-gray-600 mb-3">{integration.description}</p>

                {/* Mode Badge */}
                {integration.status.mode && integration.status.mode !== "planned" && (
                  <div className="mb-3">
                    <span className={`text-xs px-2 py-1 rounded ${
                      integration.status.mode === "live" 
                        ? "bg-green-100 text-green-700" 
                        : "bg-yellow-100 text-yellow-700"
                    }`}>
                      {integration.status.mode === "live" ? "🟢 Canlı Mod" : "🟡 Test Modu (Sandbox)"}
                    </span>
                  </div>
                )}

                {/* Instructions */}
                <div className="text-xs text-gray-500 bg-gray-50 rounded p-2 mb-3">
                  {integration.instructions}
                </div>

                {/* Env Keys */}
                {integration.envKeys.length > 0 && (
                  <div className="text-xs space-y-1 mb-3">
                    <p className="font-medium text-gray-700">Gerekli Çevre Değişkenleri:</p>
                    <div className="flex flex-wrap gap-1">
                      {integration.envKeys.map(key => (
                        <code key={key} className="bg-gray-100 px-1.5 py-0.5 rounded font-mono">
                          {key}
                        </code>
                      ))}
                    </div>
                  </div>
                )}

                {/* Actions */}
                {integration.actions && (
                  <div className="flex flex-wrap gap-2 mt-4 pt-3 border-t">
                    {integration.actions.map((action, idx) => {
                      const btnClass = `flex items-center gap-2 px-3 py-1.5 text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                         integration.id === 'trendyol' && action.label.includes('Ayarlar')
                          ? 'bg-gray-100 text-gray-800 hover:bg-gray-200 border border-gray-300'
                          : 'bg-black text-white hover:bg-gray-800'
                      }`;
                      if (action.href) {
                        return (
                          <a
                            key={idx}
                            href={action.href}
                            className={btnClass}
                            data-testid={`integration-action-${integration.id}-${idx}`}
                          >
                            {action.icon}
                            {action.label}
                          </a>
                        );
                      }
                      return (
                        <button
                          key={idx}
                          onClick={action.onClick}
                          disabled={action.loading || action.disabled}
                          className={btnClass}
                          data-testid={`integration-action-${integration.id}-${idx}`}
                        >
                          {action.loading ? (
                            <RefreshCw size={14} className="animate-spin" />
                          ) : (
                            action.icon
                          )}
                          {action.label}
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Removed duplicate "Ayarlanmamış" actions code since it handles disabled dynamically */}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* XML Feed Import */}
      <div className="mt-6 bg-white rounded-lg border-2 border-emerald-200 p-6">
        <div className="flex items-start gap-4">
          <div className="p-3 rounded-lg bg-emerald-100 text-emerald-600">
            <Download className="w-8 h-8" />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              <h3 className="font-semibold text-lg">XML Feed (Google Shopping)</h3>
              <span className="flex items-center gap-1 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">
                <Check size={12} /> Aktif
              </span>
              <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">🟢 Canlı</span>
            </div>
            <p className="text-sm text-gray-600 mb-2">
              Facette XML export feed'inden tüm ürünleri tek tıkla içe aktar
            </p>
            {xmlLastSync && (
              <p className="text-xs text-gray-400 mb-2">Son senkronizasyon: {new Date(xmlLastSync).toLocaleString("tr-TR")}</p>
            )}
            <div className="text-xs text-gray-500 bg-gray-50 rounded p-2 mb-4 font-mono break-all">
              facette.com.tr/XMLExport/7BECCB0A782647BFAB843E68AD11E468
            </div>
            <div className="flex flex-wrap gap-3 pt-3 border-t">
              <button
                onClick={handleXmlImport}
                disabled={xmlImporting}
                className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors"
              >
                {xmlImporting ? <RefreshCw size={14} className="animate-spin" /> : <Download size={14} />}
                {xmlImporting ? "XML'den Çekiliyor..." : "XML'den Ürünleri Aktar"}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Generic Cargo Provider Modal (MNG/Aras/Yurtiçi/PTT/HepsiJet/Trendyol Express/SürAt) */}
      <Dialog open={cargoModalOpen} onOpenChange={(o) => { setCargoModalOpen(o); if (!o) setActiveCargoProvider(null); }}>
        <DialogContent data-testid="cargo-settings-modal">
          <DialogHeader>
            <DialogTitle>
              {activeCargoProvider ? `${activeCargoProvider.toUpperCase()} Kargo API Ayarları` : "Kargo Ayarları"}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); saveMarketplaceSettings(activeCargoProvider, cargoSettings, setCargoModalOpen); }} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Kullanıcı Adı</label>
                <input data-testid="cargo-username" type="text" value={cargoSettings.username || ''}
                  onChange={(e) => setCargoSettings({ ...cargoSettings, username: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Şifre</label>
                <input data-testid="cargo-password" type="password" value={cargoSettings.password || ''}
                  onChange={(e) => setCargoSettings({ ...cargoSettings, password: e.target.value })}
                  placeholder={cargoSettings.password === "********" ? "********" : ""}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Müşteri/Merchant Kodu</label>
                <input data-testid="cargo-merchant" type="text" value={cargoSettings.merchant_id || cargoSettings.customer_code || ''}
                  onChange={(e) => setCargoSettings({ ...cargoSettings, merchant_id: e.target.value, customer_code: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">API Key (varsa)</label>
                <input type="text" value={cargoSettings.api_key || ''}
                  onChange={(e) => setCargoSettings({ ...cargoSettings, api_key: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-bold text-gray-600 mb-1">API Secret (varsa)</label>
                <input type="password" value={cargoSettings.api_secret || ''}
                  onChange={(e) => setCargoSettings({ ...cargoSettings, api_secret: e.target.value })}
                  placeholder={cargoSettings.api_secret === "********" ? "********" : ""}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-xs font-bold text-gray-600 mb-1">Mod</label>
              <select value={cargoSettings.mode || "sandbox"}
                onChange={e => setCargoSettings({ ...cargoSettings, mode: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm bg-white">
                <option value="sandbox">Test Modu</option>
                <option value="live">Canlı Mod</option>
              </select>
            </div>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={!!cargoSettings.is_active}
                onChange={(e) => setCargoSettings({ ...cargoSettings, is_active: e.target.checked })} />
              <span className="text-sm">Entegrasyon Aktif</span>
            </label>
            <div className="bg-blue-50 border border-blue-200 p-3 rounded text-xs text-blue-800">
              Kargo API'si yapılandırıldıktan sonra: sipariş onayında otomatik etiket basımı, takip numarası ataması ve müşteri SMS bildirimi aktif olur.
            </div>
            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => setCargoModalOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50">İptal</button>
              <button type="submit" disabled={savingSettings} data-testid="cargo-save-btn"
                className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50">
                {savingSettings ? "Kaydediliyor..." : "Kaydet"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Iyzico Settings Modal */}
      <Dialog open={iyzicoModalOpen} onOpenChange={setIyzicoModalOpen}>
        <DialogContent data-testid="iyzico-settings-modal">
          <DialogHeader>
            <DialogTitle>Iyzico API Ayarları</DialogTitle>
          </DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); saveMarketplaceSettings('iyzico', iyzicoSettings, setIyzicoModalOpen); }} className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">API Key</label>
              <input data-testid="iyzico-api-key" type="text" required value={iyzicoSettings.api_key || ''}
                onChange={(e) => setIyzicoSettings({ ...iyzicoSettings, api_key: e.target.value })}
                placeholder="sandbox-xxxxxx veya api-xxxxxx"
                className="w-full border px-3 py-2 rounded text-sm" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">API Secret</label>
              <input data-testid="iyzico-api-secret" type="password" value={iyzicoSettings.api_secret || ''}
                onChange={(e) => setIyzicoSettings({ ...iyzicoSettings, api_secret: e.target.value })}
                placeholder={iyzicoSettings.api_secret === "********" ? "********" : "Yeni Secret Key"}
                className="w-full border px-3 py-2 rounded text-sm" />
              <p className="text-xs text-gray-500 mt-1">Sadece güncellemek istediğinizde doldurun</p>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Mod</label>
              <select value={iyzicoSettings.mode || "sandbox"}
                onChange={e => setIyzicoSettings({ ...iyzicoSettings, mode: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm bg-white">
                <option value="sandbox">Sandbox (Test)</option>
                <option value="live">Canlı Mod</option>
              </select>
            </div>
            <label className="flex items-center gap-2 mt-2">
              <input data-testid="iyzico-is-active" type="checkbox" checked={!!iyzicoSettings.is_active}
                onChange={(e) => setIyzicoSettings({ ...iyzicoSettings, is_active: e.target.checked })} />
              <span className="text-sm">Entegrasyon Aktif</span>
            </label>
            <div className="bg-blue-50 border border-blue-200 p-3 rounded text-xs text-blue-800">
              <b>Not:</b> Iyzico entegrasyonu kart ödemeleri ve iade sürecinde kullanılacaktır. Müşterinin Iyzico üzerinden yaptığı ödemelerin kısmi/tam iadesi "Iade" sayfasından tek tıkla yapılabilir.
            </div>
            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => setIyzicoModalOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50">İptal</button>
              <button data-testid="iyzico-save-btn" type="submit" disabled={savingSettings} className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
                {savingSettings ? "Kaydediliyor..." : "Kaydet"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Hepsiburada Settings Modal */}

      {/* Temu Settings Modal */}


      {/* Help Section */}
      <div className="mt-8 bg-blue-50 border border-blue-200 rounded-lg p-6">
        <h3 className="font-semibold text-blue-900 mb-2">Entegrasyon Yapılandırması</h3>
        <div className="text-sm text-blue-800 space-y-2">
          <p>
            <strong>1.</strong> İlgili servisin kontrol panelinden API anahtarlarınızı alın.
          </p>
          <p>
            <strong>2.</strong> Sunucunuzdaki <code className="bg-blue-100 px-1 rounded">/app/backend/.env</code> dosyasına gerekli değişkenleri ekleyin.
          </p>
          <p>
            <strong>3.</strong> Backend servisini yeniden başlatın: <code className="bg-blue-100 px-1 rounded">sudo supervisorctl restart backend</code>
          </p>
          <p>
            <strong>4.</strong> Entegrasyon durumunu bu sayfadan kontrol edin.
          </p>
        </div>
      </div>

      {/* Integration Logs Modal */}
      <Dialog open={logsModalOpen} onOpenChange={setLogsModalOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileText size={20} /> Entegrasyon Logları
            </DialogTitle>
          </DialogHeader>
          
          <div className="flex flex-col gap-4 overflow-hidden h-full mt-4">
            {/* Filters */}
            <div className="flex gap-4 p-4 border rounded-lg bg-gray-50">
              <div className="flex flex-col gap-1 w-1/3">
                <label className="text-xs font-medium text-gray-600">Platform</label>
                <select 
                  className="border rounded px-3 py-1.5 text-sm"
                  value={logFilters.platform}
                  onChange={(e) => setLogFilters({...logFilters, platform: e.target.value})}
                >
                  <option value="">Tümü</option>
                  <option value="trendyol">Trendyol</option>
                </select>
              </div>
              <div className="flex flex-col gap-1 w-1/3">
                <label className="text-xs font-medium text-gray-600">Durum</label>
                <select 
                  className="border rounded px-3 py-1.5 text-sm"
                  value={logFilters.status}
                  onChange={(e) => setLogFilters({...logFilters, status: e.target.value})}
                >
                  <option value="">Tümü</option>
                  <option value="success">Başarılı</option>
                  <option value="error">Hatalı</option>
                  <option value="warning">Uyarı</option>
                </select>
              </div>
              <div className="flex items-end flex-1">
                 <button onClick={fetchLogs} className="flex items-center gap-1 bg-black text-white px-4 py-1.5 rounded text-sm hover:bg-gray-800">
                    <RefreshCw size={14} className={logsLoading ? "animate-spin" : ""} /> Yenile
                 </button>
              </div>
            </div>

            {/* List */}
            <div className="flex-1 overflow-auto border rounded-lg bg-white relative">
               <table className="w-full text-sm text-left">
                  <thead className="bg-gray-50 sticky top-0 z-10 border-b">
                     <tr>
                        <th className="py-2 px-4 whitespace-nowrap">Tarih</th>
                        <th className="py-2 px-4">Platform</th>
                        <th className="py-2 px-4">Olay/Bölüm</th>
                        <th className="py-2 px-4">Referans</th>
                        <th className="py-2 px-4">Durum</th>
                        <th className="py-2 px-4 w-1/3">Detay</th>
                     </tr>
                  </thead>
                  <tbody>
                     {logsLoading && integrationLogs.length === 0 ? (
                        <tr><td colSpan={6} className="text-center py-8 text-gray-500">Yükleniyor...</td></tr>
                     ) : integrationLogs.length === 0 ? (
                        <tr><td colSpan={6} className="text-center py-8 text-gray-500">Log kaydı bulunamadı.</td></tr>
                     ) : (
                        integrationLogs.map(log => (
                           <tr key={log._id || Math.random()} className="border-b hover:bg-gray-50">
                              <td className="py-2 px-4 whitespace-nowrap text-xs text-gray-500">
                                 {new Date(log.created_at).toLocaleString('tr-TR')}
                              </td>
                              <td className="py-2 px-4 font-medium uppercase text-xs">{log.platform}</td>
                              <td className="py-2 px-4">{log.event_type}</td>
                              <td className="py-2 px-4 font-mono text-xs">{log.reference_id}</td>
                              <td className="py-2 px-4">
                                 <span className={`px-2 py-0.5 rounded text-xs ${
                                    log.status === 'error' ? 'bg-red-100 text-red-700' : 
                                    log.status === 'warning' ? 'bg-yellow-100 text-yellow-700' : 
                                    'bg-green-100 text-green-700'
                                 }`}>
                                    {log.status === 'error' ? 'Hata' : log.status === 'warning' ? 'Uyarı' : 'Başarılı'}
                                 </span>
                              </td>
                              <td className="py-2 px-4 text-xs text-gray-600 max-w-xs truncate" title={log.message}>
                                 {log.message}
                              </td>
                           </tr>
                        ))
                     )}
                  </tbody>
               </table>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
