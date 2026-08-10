import { useState, useEffect } from "react";
import { Plus, Edit, Trash2, Power, Percent, Gift, Truck, Tag, ShoppingBag, Users, Clock, Sparkles, AlertTriangle } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Hazır kampanya şablonları
const CAMPAIGN_TEMPLATES = [
  {
    id: "percent-10", name: "%10 İndirim", icon: Percent, color: "bg-rose-500",
    description: "Sepette %10 iskonto uygulayan klasik kampanya.",
    payload: { type: "percentage", value: 10, min_order_amount: 0, code: "INDIRIM10", usage_limit: 0 },
  },
  {
    id: "percent-20-500", name: "500₺+ Sepette %20", icon: Percent, color: "bg-orange-500",
    description: "500₺ üzeri alışverişlere %20 indirim.",
    payload: { type: "percentage", value: 20, min_order_amount: 500, code: "BUYUK20", usage_limit: 0 },
  },
  {
    id: "free-shipping", name: "Ücretsiz Kargo", icon: Truck, color: "bg-emerald-500",
    description: "Tüm siparişlerde ücretsiz kargo.",
    payload: { type: "free_shipping", value: 0, min_order_amount: 0, code: "KARGO0", usage_limit: 0, auto_apply: true },
  },
  {
    id: "free-shipping-300", name: "300₺+ Ücretsiz Kargo", icon: Truck, color: "bg-teal-500",
    description: "Minimum 300₺ alışverişlerde ücretsiz teslimat.",
    payload: { type: "free_shipping", value: 0, min_order_amount: 300, code: "KARGOBEDAVA", usage_limit: 0, auto_apply: true },
  },
  {
    id: "fixed-50", name: "50₺ İndirim Çeki", icon: Tag, color: "bg-sky-500",
    description: "Her siparişte sabit 50₺ düşüm (min 150₺).",
    payload: { type: "fixed", value: 50, min_order_amount: 150, code: "CEK50", usage_limit: 0 },
  },
  {
    id: "welcome-15", name: "Hoşgeldin (İlk Sipariş)", icon: Gift, color: "bg-pink-500",
    description: "Sadece ilk siparişe özel %15, kişi başı 1 kez.",
    payload: { type: "percentage", value: 15, min_order_amount: 0, code: "HOSGELDIN15", usage_limit: 0, first_order_only: true, usage_limit_per_user: 1 },
  },
  {
    id: "second-item-50", name: "2. Ürüne %50", icon: ShoppingBag, color: "bg-amber-500",
    description: "2 ürün alana, EN UCUZ ürüne %50 indirim.",
    payload: { type: "nth_discount", value: 0, min_order_amount: 0, code: "IKINCI50", usage_limit: 0, buy_quantity: 2, free_quantity: 1, get_discount: 50, min_quantity: 2 },
  },
  {
    id: "buy3-pay2", name: "3 Al 2 Öde", icon: ShoppingBag, color: "bg-amber-600",
    description: "Her 3 üründe en ucuz olan bedava (100% indirim).",
    payload: { type: "nth_discount", value: 0, min_order_amount: 0, code: "3AL2ODE", usage_limit: 0, buy_quantity: 3, free_quantity: 1, get_discount: 100, min_quantity: 3 },
  },
  {
    id: "vip-30", name: "VIP Müşteri %30", icon: Users, color: "bg-violet-500",
    description: "VIP segment için %30 özel kampanya (kişi başı 1).",
    payload: { type: "percentage", value: 30, min_order_amount: 0, code: "VIP30", usage_limit: 100, usage_limit_per_user: 1 },
  },
  {
    id: "flash-sale", name: "Flaş Satış %40", icon: Clock, color: "bg-red-600",
    description: "Kısa süreli büyük indirim. Süreyi mutlaka kısaltın!",
    payload: { type: "percentage", value: 40, min_order_amount: 0, code: "FLAS40", usage_limit: 500 },
  },
];

const blankForm = () => ({
  name: "", type: "percentage", value: 0, min_order_amount: 0, code: "",
  start_date: new Date().toISOString().split('T')[0],
  end_date: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
  is_active: true, auto_apply: false, usage_limit: 0,
  first_order_only: false, usage_limit_per_user: 0, min_quantity: 0,
  buy_quantity: 2, free_quantity: 1, get_discount: 50, bundle_price: 0,
  priority: 0, combinable: false, stack_group: "", combinable_with: [],
  categories: [], products: [], payment_methods: [],
});

export default function AdminCampaigns() {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [formData, setFormData] = useState(blankForm());
  // Madde 4 — kapsam (kategori/ürün) seçici
  const [allCategories, setAllCategories] = useState([]);
  const [prodQuery, setProdQuery] = useState("");
  const [prodResults, setProdResults] = useState([]);
  const [prodNames, setProdNames] = useState({}); // id -> ad (chip gösterimi)

  useEffect(() => { fetchCampaigns(); }, []);
  useEffect(() => {
    axios.get(`${API}/categories`).then((r) => setAllCategories(r.data || [])).catch(() => setAllCategories([]));
  }, []);

  const toggleCategory = (id) => {
    const cur = formData.categories || [];
    setFormData({ ...formData, categories: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] });
  };
  const searchProducts = async (q) => {
    setProdQuery(q);
    if (!q || q.trim().length < 2) { setProdResults([]); return; }
    try {
      const r = await axios.get(`${API}/products?search=${encodeURIComponent(q.trim())}&limit=8`);
      setProdResults(r.data?.products || []);
    } catch { setProdResults([]); }
  };
  const addProduct = (p) => {
    const cur = formData.products || [];
    if (!cur.includes(p.id)) setFormData({ ...formData, products: [...cur, p.id] });
    setProdNames((prev) => ({ ...prev, [p.id]: p.name }));
    setProdQuery(""); setProdResults([]);
  };
  const removeProduct = (id) => setFormData({ ...formData, products: (formData.products || []).filter((x) => x !== id) });
  const toggleCombinableWith = (id) => {
    const cur = formData.combinable_with || [];
    setFormData({ ...formData, combinable_with: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] });
  };
  const togglePaymentMethod = (key) => {
    const cur = formData.payment_methods || [];
    setFormData({ ...formData, payment_methods: cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key] });
  };

  const fetchCampaigns = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/campaigns`);
      setCampaigns(res.data || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      // Tarihler GÜN olarak (YYYY-MM-DD) gönderilir. Eskiden new Date(...).toISOString()
      // ile "günün BAŞI 00:00Z"a çevriliyordu → seçilen bitiş günü TAMAMEN kayboluyor,
      // bitiş olarak BUGÜN seçilince kampanya ANINDA "süresi dolmuş" sayılıyordu (rozet
      // %10 görünüp sepette indirim uygulanmaması bunun sonucuydu). Backend zaten
      // yalnız-tarih end_at'i o günün SONUNA (23:59:59) normalize eder (coupons L1).
      const data = {
        ...formData,
        start_date: formData.start_date || null,
        end_date: formData.end_date || null,
      };
      if (editingId) {
        await axios.put(`${API}/campaigns/${editingId}`, data);
        toast.success("Kampanya güncellendi");
      } else {
        await axios.post(`${API}/campaigns`, data);
        toast.success("Kampanya oluşturuldu");
      }
      setModalOpen(false);
      resetForm();
      fetchCampaigns();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Hata oluştu");
    }
  };

  const resetForm = () => { setFormData(blankForm()); setEditingId(null); };

  const openEdit = (c) => {
    setEditingId(c.id);
    setFormData({
      name: c.name || "",
      type: c.type || "percentage",
      value: c.value || 0,
      min_order_amount: c.min_order_amount || 0,
      code: c.code || "",
      start_date: c.start_date ? new Date(c.start_date).toISOString().split('T')[0] : blankForm().start_date,
      end_date: c.end_date ? new Date(c.end_date).toISOString().split('T')[0] : blankForm().end_date,
      is_active: c.is_active !== false,
      auto_apply: !!c.auto_apply,
      usage_limit: c.usage_limit || 0,
      first_order_only: !!c.first_order_only,
      usage_limit_per_user: c.usage_limit_per_user || 0,
      min_quantity: c.min_quantity || 0,
      buy_quantity: c.buy_quantity || 2,
      free_quantity: c.free_quantity || 1,
      get_discount: c.get_discount || 50,
      bundle_price: c.bundle_price || 0,
      priority: c.priority || 0,
      combinable: !!c.combinable,
      stack_group: c.stack_group || "",
      combinable_with: c.combinable_with || [],
      categories: c.categories || [],
      products: c.products || [],
      payment_methods: c.payment_methods || [],
    });
    setModalOpen(true);
  };

  const handleDelete = async (c) => {
    if (!window.confirm(`"${c.name || c.code}" kampanyası silinsin mi?`)) return;
    try {
      await axios.delete(`${API}/campaigns/${c.id}`);
      toast.success("Kampanya silindi");
      fetchCampaigns();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Silinemedi");
    }
  };

  const handleToggle = async (c) => {
    try {
      await axios.put(`${API}/campaigns/${c.id}`, { ...c, is_active: !c.is_active });
      toast.success(c.is_active ? "Pasife alındı" : "Aktifleştirildi");
      fetchCampaigns();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Güncellenemedi");
    }
  };

  const getTypeLabel = (type) => ({
    percentage: "Yüzde İndirim",
    fixed: "Sabit İndirim",
    free_shipping: "Ücretsiz Kargo",
    nth_discount: "X Al Y Öde",
    bundle: "Ürün Paketi",
  }[type] || type);

  const valueLabel = (c) => {
    if (c.type === "percentage") return `%${c.value}`;
    if (c.type === "fixed") return `${c.value} TL`;
    if (c.type === "nth_discount") return `${c.buy_quantity || 2} al · en ucuz ${c.free_quantity || 1}'e %${c.get_discount || 0}`;
    if (c.type === "bundle") return `Paket: ${c.bundle_price || 0} TL`;
    return "-";
  };

  // Riskli tanım uyarısı (form içi)
  const riskWarning = (() => {
    if (formData.type === "percentage" && Number(formData.value) >= 40) return "Yüksek indirim oranı (%40+) — marjı kontrol edin.";
    if (formData.type === "nth_discount" && Number(formData.get_discount) >= 100 && (!formData.min_quantity && !formData.buy_quantity)) return "100% indirim + adet koşulu yok — ürün bedava gidebilir.";
    if (!Number(formData.usage_limit) && !formData.first_order_only && formData.type !== "free_shipping") return "Toplam kullanım limiti yok — sınırsız kullanılabilir.";
    return null;
  })();

  const applyTemplate = (tmpl) => {
    const p = tmpl.payload;
    setEditingId(null);
    setFormData({
      ...blankForm(),
      name: tmpl.name,
      type: p.type,
      value: p.value || 0,
      min_order_amount: p.min_order_amount || 0,
      code: p.code || "",
      auto_apply: p.auto_apply || p.type === "free_shipping",
      usage_limit: p.usage_limit || 0,
      first_order_only: !!p.first_order_only,
      usage_limit_per_user: p.usage_limit_per_user || 0,
      min_quantity: p.min_quantity || 0,
      buy_quantity: p.buy_quantity || 2,
      free_quantity: p.free_quantity || 1,
      get_discount: p.get_discount || 50,
    });
    setModalOpen(true);
    toast.success(`"${tmpl.name}" şablonu yüklendi. Kontrol edip kaydedin.`);
  };

  const inputCls = "w-full border px-3 py-2 rounded text-sm";
  const lblCls = "block text-sm font-medium mb-1";

  return (
    <div data-testid="admin-campaigns">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Kampanyalar</h1>
        <button
          onClick={() => { resetForm(); setModalOpen(true); }}
          data-testid="new-campaign-btn"
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800"
        >
          <Plus size={18} /> Yeni Kampanya
        </button>
      </div>

      {/* Şablon galerisi */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles size={18} className="text-amber-500" />
          <h2 className="text-sm font-bold uppercase tracking-wider text-gray-700">Hazır Kampanya Şablonları</h2>
          <span className="text-xs text-gray-400">— kutucuğa tıklayın, formu otomatik doldursun</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3" data-testid="campaign-templates">
          {CAMPAIGN_TEMPLATES.map(tmpl => {
            const Icon = tmpl.icon;
            return (
              <button
                key={tmpl.id}
                onClick={() => applyTemplate(tmpl)}
                data-testid={`campaign-tmpl-${tmpl.id}`}
                className="text-left bg-white border rounded-xl p-4 hover:shadow-lg hover:-translate-y-0.5 transition-all group"
              >
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center text-white mb-3 ${tmpl.color} group-hover:scale-110 transition-transform`}>
                  <Icon size={18} />
                </div>
                <p className="font-bold text-sm text-gray-900 mb-1">{tmpl.name}</p>
                <p className="text-[11px] text-gray-500 leading-relaxed line-clamp-3">{tmpl.description}</p>
                <div className="mt-2 flex items-center gap-1 text-[10px] text-gray-400">
                  <code className="bg-gray-100 px-1.5 py-0.5 rounded font-mono">{tmpl.payload.code}</code>
                  {tmpl.payload.min_order_amount > 0 && (<span>· min {tmpl.payload.min_order_amount}₺</span>)}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Kampanya Adı</th>
              <th>Tür</th>
              <th>Değer</th>
              <th>Kupon Kodu</th>
              <th>Koşul</th>
              <th>Kullanım</th>
              <th>Tarih Aralığı</th>
              <th>Durum</th>
              <th>İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={9} className="text-center py-8">Yükleniyor...</td></tr>
            ) : campaigns.length === 0 ? (
              <tr><td colSpan={9} className="text-center py-8 text-gray-500">Kampanya bulunamadı</td></tr>
            ) : (
              campaigns.map((c) => (
                <tr key={c.id}>
                  <td className="font-medium">{c.name}</td>
                  <td>{getTypeLabel(c.type)}</td>
                  <td>{valueLabel(c)}</td>
                  <td><code className="bg-gray-100 px-2 py-1 text-sm">{c.code || "-"}</code></td>
                  <td className="text-xs text-gray-600">
                    {c.first_order_only && <span className="inline-block bg-pink-100 text-pink-700 px-1.5 py-0.5 rounded mr-1">İlk sipariş</span>}
                    {c.min_quantity > 0 && <span className="inline-block bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded mr-1">min {c.min_quantity} ürün</span>}
                    {c.min_order_amount > 0 && <span>min {c.min_order_amount}₺</span>}
                    {!c.first_order_only && !c.min_quantity && !c.min_order_amount && <span className="text-gray-300">—</span>}
                  </td>
                  <td className="text-sm">{c.redeemed_count || 0}{c.usage_limit ? ` / ${c.usage_limit}` : ""}</td>
                  <td className="text-sm">
                    {c.start_date ? new Date(c.start_date).toLocaleDateString('tr-TR') : "-"} – {c.end_date ? new Date(c.end_date).toLocaleDateString('tr-TR') : "-"}
                  </td>
                  <td>
                    <span className={`px-2 py-1 text-xs rounded ${c.is_active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                      {c.is_active ? "Aktif" : "Pasif"}
                    </span>
                  </td>
                  <td>
                    <div className="flex items-center gap-1">
                      <button onClick={() => handleToggle(c)} title={c.is_active ? "Pasife al" : "Aktifleştir"} className={`p-1.5 rounded hover:bg-gray-100 ${c.is_active ? "text-green-600" : "text-gray-400"}`}><Power size={16} /></button>
                      <button onClick={() => openEdit(c)} title="Düzenle" className="p-1.5 rounded text-blue-600 hover:bg-blue-50"><Edit size={16} /></button>
                      <button onClick={() => handleDelete(c)} title="Sil" className="p-1.5 rounded text-red-600 hover:bg-red-50"><Trash2 size={16} /></button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Modal */}
      <Dialog open={modalOpen} onOpenChange={(o) => { setModalOpen(o); if (!o) resetForm(); }}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? "Kampanyayı Düzenle" : "Yeni Kampanya"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className={lblCls}>Kampanya Adı *</label>
              <input type="text" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} required className={inputCls} />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={lblCls}>Tür</label>
                <select value={formData.type} onChange={(e) => setFormData({ ...formData, type: e.target.value })} className={inputCls}>
                  <option value="percentage">Yüzde İndirim</option>
                  <option value="fixed">Sabit İndirim (TL)</option>
                  <option value="free_shipping">Ücretsiz Kargo</option>
                  <option value="nth_discount">X Al Y Öde / N. Ürüne İndirim</option>
                  <option value="bundle">Ürün Paketi (tek fiyat)</option>
                </select>
              </div>
              {formData.type !== "nth_discount" && formData.type !== "free_shipping" && formData.type !== "bundle" && (
                <div>
                  <label className={lblCls}>Değer {formData.type === "percentage" ? "(%)" : "(TL)"}</label>
                  <input type="number" value={formData.value} onChange={(e) => setFormData({ ...formData, value: parseFloat(e.target.value) || 0 })} className={inputCls} />
                </div>
              )}
              {formData.type === "bundle" && (
                <div>
                  <label className={lblCls}>Paket Fiyatı (TL)</label>
                  <input type="number" min="1" value={formData.bundle_price} onChange={(e) => setFormData({ ...formData, bundle_price: parseFloat(e.target.value) || 0 })} className={inputCls} data-testid="bundle-price-input" />
                </div>
              )}
            </div>

            {formData.type === "bundle" && (
              <p className="text-[11px] bg-violet-50 border border-violet-200 text-violet-700 rounded p-2.5">
                📦 <b>Ürün Paketi:</b> Aşağıdan paket ürünlerini seçin (en az 2). Müşteri seçilen ürünlerin
                <b> hepsini</b> sepete eklediğinde, her birinden 1'er adet toplam liste fiyatı yerine
                <b> paket fiyatı</b> üzerinden hesaplanır (fark otomatik indirim olur). "Otomatik uygula" açık olmalı.
              </p>
            )}

            {formData.type === "nth_discount" && (
              <div className="grid grid-cols-3 gap-3 bg-amber-50 border border-amber-200 rounded p-3">
                <div>
                  <label className={lblCls}>Kaç ürün alınca</label>
                  <input type="number" min="1" value={formData.buy_quantity} onChange={(e) => setFormData({ ...formData, buy_quantity: parseInt(e.target.value) || 1 })} className={inputCls} />
                </div>
                <div>
                  <label className={lblCls}>Kaç ürüne</label>
                  <input type="number" min="1" value={formData.free_quantity} onChange={(e) => setFormData({ ...formData, free_quantity: parseInt(e.target.value) || 1 })} className={inputCls} />
                </div>
                <div>
                  <label className={lblCls}>İndirim (%)</label>
                  <input type="number" min="1" max="100" value={formData.get_discount} onChange={(e) => setFormData({ ...formData, get_discount: parseFloat(e.target.value) || 0 })} className={inputCls} />
                </div>
                <p className="col-span-3 text-[11px] text-amber-700">
                  Örn: 2 al / 1 ürüne / %50 = ikinci üründe %50. İndirim her zaman <b>en ucuz ürün(ler)e</b> uygulanır. 3 al / 1 / %100 = "3 al 2 öde".
                </p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={lblCls}>Kupon Kodu</label>
                <input type="text" value={formData.code} onChange={(e) => setFormData({ ...formData, code: e.target.value.toUpperCase() })} placeholder="INDIRIM10" className={`${inputCls} uppercase`} />
              </div>
              <div>
                <label className={lblCls}>Min. Sipariş Tutarı (₺)</label>
                <input type="number" value={formData.min_order_amount} onChange={(e) => setFormData({ ...formData, min_order_amount: parseFloat(e.target.value) || 0 })} className={inputCls} />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className={lblCls}>Min. Ürün Adedi</label>
                <input type="number" min="0" value={formData.min_quantity} onChange={(e) => setFormData({ ...formData, min_quantity: parseInt(e.target.value) || 0 })} className={inputCls} />
              </div>
              <div>
                <label className={lblCls}>Toplam Kullanım Limiti</label>
                <input type="number" min="0" value={formData.usage_limit} onChange={(e) => setFormData({ ...formData, usage_limit: parseInt(e.target.value) || 0 })} placeholder="0 = sınırsız" className={inputCls} />
              </div>
              <div>
                <label className={lblCls}>Kişi Başı Limit</label>
                <input type="number" min="0" value={formData.usage_limit_per_user} onChange={(e) => setFormData({ ...formData, usage_limit_per_user: parseInt(e.target.value) || 0 })} placeholder="0 = sınırsız" className={inputCls} />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="accent-black" checked={!!formData.first_order_only} onChange={(e) => setFormData({ ...formData, first_order_only: e.target.checked })} />
              <span><b>Sadece ilk siparişe özel</b> — müşterinin daha önce (iptal hariç) siparişi yoksa geçerli. Hoşgeldin kampanyaları için işaretleyin.</span>
            </label>

            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="accent-black" checked={!!formData.auto_apply} onChange={(e) => setFormData({ ...formData, auto_apply: e.target.checked })} />
              <span>Otomatik uygula (kod gerekmez) — özellikle "Ücretsiz Kargo" için.</span>
            </label>

            {/* Madde 4 — kampanya motoru alanları */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={lblCls}>Öncelik (büyük = önce uygulanır)</label>
                <input type="number" value={formData.priority} onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) || 0 })} className={inputCls} />
              </div>
              <div>
                <label className={lblCls}>Stack Grubu (opsiyonel)</label>
                <input type="text" value={formData.stack_group} onChange={(e) => setFormData({ ...formData, stack_group: e.target.value })} placeholder="örn. kargo" className={inputCls} />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="accent-black" checked={!!formData.combinable} onChange={(e) => setFormData({ ...formData, combinable: e.target.checked })} />
              <span>Birleştirilebilir — diğer birleştirilebilir kampanyalarla üst üste uygulanır (kapalıysa tek başına/münhasır).</span>
            </label>

            {formData.combinable && (
              <div className="border rounded-lg p-3 bg-gray-50/50">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-600 mb-2">Hangi kampanyalarla birleşsin? <span className="font-normal normal-case text-gray-400">(boş = tüm birleştirilebilirlerle · seçim karşılıklı olmalı)</span></div>
                <div className="max-h-32 overflow-y-auto space-y-1 border rounded bg-white p-2">
                  {campaigns.filter((c) => c.id !== editingId).length === 0 && <div className="text-xs text-gray-400">Başka kampanya yok</div>}
                  {campaigns.filter((c) => c.id !== editingId).map((c) => (
                    <label key={c.id} className="flex items-center gap-2 text-xs cursor-pointer">
                      <input type="checkbox" className="accent-black" checked={(formData.combinable_with || []).includes(c.id)} onChange={() => toggleCombinableWith(c.id)} />
                      <span>{c.name || c.code}{c.combinable ? "" : <span className="text-amber-600"> (bu kampanya birleştirilemez işaretli)</span>}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* Ödeme yöntemine özel kampanya — boş = tüm yöntemler */}
            <div className="border rounded-lg p-3 bg-gray-50/50">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-600 mb-2">Ödeme yöntemi <span className="font-normal normal-case text-gray-400">(boş = tüm yöntemlerde geçerli · örn. sadece Havale/EFT seçersen indirim yalnızca havalede biner)</span></div>
              <div className="flex flex-wrap gap-x-4 gap-y-2">
                {[
                  { key: "bank_transfer", label: "Havale / EFT" },
                  { key: "credit_card", label: "Banka & Kredi Kartı" },
                  { key: "cash_on_delivery", label: "Kapıda Ödeme" },
                ].map((pm) => (
                  <label key={pm.key} className="flex items-center gap-2 text-xs cursor-pointer">
                    <input type="checkbox" className="accent-black" checked={(formData.payment_methods || []).includes(pm.key)} onChange={() => togglePaymentMethod(pm.key)} />
                    <span>{pm.label}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Madde 4 — Kapsam (kategori/ürün). Boş = tüm sepete uygulanır. */}
            <div className="border rounded-lg p-3 space-y-3 bg-gray-50/50">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-600">Kapsam (opsiyonel — boşsa tüm sepete uygulanır)</div>
              <div>
                <label className={lblCls}>Kategoriler</label>
                <div className="max-h-32 overflow-y-auto border rounded bg-white p-2 space-y-1">
                  {allCategories.length === 0 && <div className="text-xs text-gray-400">Kategori bulunamadı</div>}
                  {allCategories.map((cat) => (
                    <label key={cat.id} className="flex items-center gap-2 text-xs cursor-pointer">
                      <input type="checkbox" className="accent-black" checked={(formData.categories || []).includes(cat.id)} onChange={() => toggleCategory(cat.id)} />
                      <span>{cat.name}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <label className={lblCls}>Ürünler</label>
                {(formData.products || []).length > 0 && (
                  <div className="flex flex-wrap gap-1 mb-1.5">
                    {(formData.products || []).map((pid) => (
                      <span key={pid} className="inline-flex items-center gap-1 bg-stone-900 text-white text-[11px] rounded px-2 py-0.5">
                        {prodNames[pid] || pid}
                        <button type="button" onClick={() => removeProduct(pid)} className="text-white/70 hover:text-white">×</button>
                      </span>
                    ))}
                  </div>
                )}
                <input type="text" value={prodQuery} onChange={(e) => searchProducts(e.target.value)} placeholder="Ürün ara (en az 2 harf)…" className={inputCls} />
                {prodResults.length > 0 && (
                  <div className="border rounded bg-white mt-1 max-h-40 overflow-y-auto">
                    {prodResults.map((p) => (
                      <button type="button" key={p.id} onClick={() => addProduct(p)} className="w-full text-left px-2 py-1.5 text-xs hover:bg-gray-50 border-b last:border-0">
                        {p.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {riskWarning && (
              <div className="flex items-start gap-2 text-xs bg-red-50 border border-red-200 text-red-700 rounded p-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" /> <span>{riskWarning}</span>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={lblCls}>Başlangıç Tarihi</label>
                <input type="date" value={formData.start_date} onChange={(e) => setFormData({ ...formData, start_date: e.target.value })} className={inputCls} />
              </div>
              <div>
                <label className={lblCls}>Bitiş Tarihi</label>
                <input type="date" value={formData.end_date} onChange={(e) => setFormData({ ...formData, end_date: e.target.value })} className={inputCls} />
              </div>
            </div>

            <label className="flex items-center gap-2">
              <input type="checkbox" checked={formData.is_active} onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })} />
              <span className="text-sm">Aktif</span>
            </label>

            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => { setModalOpen(false); resetForm(); }} className="px-4 py-2 border rounded hover:bg-gray-50">İptal</button>
              <button type="submit" className="px-4 py-2 bg-black text-white rounded hover:bg-gray-800">{editingId ? "Güncelle" : "Oluştur"}</button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
