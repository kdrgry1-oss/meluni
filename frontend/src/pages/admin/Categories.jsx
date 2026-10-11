import React, { useState, useEffect } from "react";
import { Plus, Edit, Trash2, ChevronRight, ChevronDown, RefreshCw } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import SearchableMapSelect from "../../components/admin/SearchableMapSelect";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function AdminCategories() {
  const [categories, setCategories] = useState([]);
  const [trendyolCategories, setTrendyolCategories] = useState([]);
  const [trendyolCatSearch, setTrendyolCatSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [syncingTrendyol, setSyncingTrendyol] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingCategory, setEditingCategory] = useState(null);
  const [expandedNodes, setExpandedNodes] = useState(new Set());
  const [trendyolAttributes, setTrendyolAttributes] = useState([]);
  const [fetchingAttributes, setFetchingAttributes] = useState(false);
  
  const [formData, setFormData] = useState({
    name: "",
    slug: "",
    image_url: "",
    parent_id: "",
    trendyol_category_id: "",
    hepsiburada_category_id: "",
    amazon_category_id: "",
    attribute_mapping: {},
    is_active: true,
    members_only: false,
    sort_order: 0,
  });

  useEffect(() => {
    fetchCategories();
    fetchTrendyolCategories();
  }, []);

  const fetchCategories = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/categories`);
      setCategories(res.data || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchTrendyolCategories = async () => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/integrations/trendyol/categories`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data?.categories && res.data.categories.length > 0) {
        setTrendyolCategories(res.data.categories);
      } else {
        // Kategoriler boşsa otomatik senkronize et
        syncTrendyolCategories();
      }
    } catch (err) {
      console.error("Trendyol categories fetch failed", err);
    }
  };

  const syncTrendyolCategories = async () => {
    setSyncingTrendyol(true);
    try {
      const token = localStorage.getItem('token');
      await axios.post(`${API}/integrations/trendyol/categories/sync`, {}, {
        headers: { Authorization: `Bearer ${token}` },
        timeout: 60000
      });
      // Tekrar çek
      const res = await axios.get(`${API}/integrations/trendyol/categories`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data?.categories) {
        setTrendyolCategories(res.data.categories);
        toast.success(`${res.data.categories.length} Trendyol kategorisi yüklendi`);
      }
    } catch (err) {
      console.error(err);
      toast.error("Trendyol kategorileri çekilemedi");
    } finally {
      setSyncingTrendyol(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      const payload = {
        ...formData,
        parent_id: formData.parent_id || null,
        trendyol_category_id: formData.trendyol_category_id ? parseInt(formData.trendyol_category_id) : null,
        hepsiburada_category_id: formData.hepsiburada_category_id ? parseInt(formData.hepsiburada_category_id) : null,
        amazon_category_id: formData.amazon_category_id ? parseInt(formData.amazon_category_id) : null
      };

      if (editingCategory) {
        await axios.put(`${API}/categories/${editingCategory.id}`, payload);
        toast.success("Kategori güncellendi");
      } else {
        await axios.post(`${API}/categories`, payload);
        toast.success("Kategori eklendi");
      }
      setModalOpen(false);
      resetForm();
      fetchCategories();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Hata oluştu");
    }
  };

  const handleDelete = async (id) => {
    toast('Kategoriyi silmek istediğinize emin misiniz?', {
      action: {
        label: 'Sil',
        onClick: async () => {
          try {
            const token = localStorage.getItem('token');
            await axios.delete(`${API}/categories/${id}`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            toast.success('Kategori silindi');
            fetchCategories();
          } catch (err) {
            toast.error('Silme başarısız');
          }
        }
      },
      cancel: { label: 'İptal', onClick: () => {} },
      duration: 8000,
    });
  };

  const fetchTrendyolAttributes = async (trendyolCatId) => {
    if (!trendyolCatId) { setTrendyolAttributes([]); return; }
    setFetchingAttributes(true);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/integrations/trendyol/categories/${trendyolCatId}/attributes`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setTrendyolAttributes(res.data?.attributes || []);
    } catch (err) {
      console.error(err);
      setTrendyolAttributes([]);
    } finally {
      setFetchingAttributes(false);
    }
  };

  // DENETİM FIX (#22): turuncu buton CANLI Trendyol stok/fiyat push'u tetikliyordu — onaysız
  // ve çift tıklamaya açıktı. Artık önce onay istenir, işlem sırasında buton devre dışı/spinner olur.
  const [pushingCat, setPushingCat] = useState(null);

  const doTrendyolCategoryStockPrice = async (categoryId, categoryName) => {
    setPushingCat(categoryId);
    try {
      const token = localStorage.getItem('token');
      toast.info(`${categoryName} kategorisi için stok/fiyat güncelleniyor...`);
      const res = await axios.post(`${API}/integrations/trendyol/categories/${categoryId}/update-stock-price`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success(res.data?.message || "Stok/fiyat güncellendi");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Stok/fiyat güncelleme başarısız");
    } finally {
      setPushingCat(null);
    }
  };

  const handleTrendyolCategoryStockPrice = (categoryId, categoryName) => {
    if (pushingCat) return;
    toast(`"${categoryName}" kategorisindeki ürünlerin stok/fiyatını CANLI Trendyol'a göndersin mi?`, {
      action: {
        label: 'Gönder',
        onClick: () => doTrendyolCategoryStockPrice(categoryId, categoryName),
      },
      cancel: { label: 'İptal', onClick: () => {} },
      duration: 8000,
    });
  };

  const openEditModal = (category) => {
    setEditingCategory(category);
    setFormData({
      name: category.name,
      slug: category.slug || "",
      image_url: category.image_url || "",
      parent_id: category.parent_id || "",
      trendyol_category_id: category.trendyol_category_id || "",
      hepsiburada_category_id: category.hepsiburada_category_id || "",
      amazon_category_id: category.amazon_category_id || "",
      attribute_mapping: category.attribute_mapping || {},
      is_active: category.is_active,
      members_only: !!category.members_only,
      sort_order: category.sort_order || 0,
    });
    // Trendyol kategori adını yansıt (varsa)
    if (category.trendyol_category_id) {
      const found = trendyolCategories.find(c => String(c.id) === String(category.trendyol_category_id));
      setTrendyolCatSearch(found?.name || "");
      fetchTrendyolAttributes(category.trendyol_category_id);
    } else {
      setTrendyolCatSearch("");
      setTrendyolAttributes([]);
    }
    setModalOpen(true);
  };

  const resetForm = () => {
    setEditingCategory(null);
    setTrendyolAttributes([]);
    setTrendyolCatSearch("");
    setFormData({
      name: "", slug: "", description: "", image_url: "", parent_id: "", trendyol_category_id: "",
      hepsiburada_category_id: "", amazon_category_id: "", attribute_mapping: {}, is_active: true, members_only: false, sort_order: 0
    });
  };

  const generateSlug = (name) => {
    return name.toLowerCase()
      .replace(/ğ/g, 'g').replace(/ü/g, 'u').replace(/ş/g, 's')
      .replace(/ı/g, 'i').replace(/ö/g, 'o').replace(/ç/g, 'c')
      .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  };

  const toggleExpand = (id) => {
    const newExpanded = new Set(expandedNodes);
    if (newExpanded.has(id)) {
      newExpanded.delete(id);
    } else {
      newExpanded.add(id);
    }
    setExpandedNodes(newExpanded);
  };

  // Build tree structure - safe version with null parent_id handling
  const buildTree = (cats, parentId = null, depth = 0) => {
    if (depth > 10) return []; // prevent infinite recursion
    return cats
      .filter((c) => (c.parent_id ?? null) === parentId)
      .sort((a, b) => (a.sort_order ?? 999) - (b.sort_order ?? 999))
      .map((c) => ({
        ...c,
        id: c.id || c.ticimax_id || c._id,
        children: buildTree(cats, c.id || c.ticimax_id, depth + 1),
      }));
  };

  const treeData = buildTree(categories);

  const renderTreeRows = (nodes, level = 0) => {
    return nodes.map((node) => {
      const hasChildren = node.children && node.children.length > 0;
      const isExpanded = expandedNodes.has(node.id) || level === 0;

      return (
        <React.Fragment key={node.id}>
          <tr className="hover:bg-gray-50 border-b">
            <td className="py-3 px-4">
              <div 
                className="flex items-center gap-2" 
                style={{ paddingLeft: `${level * 1.5}rem` }}
              >
                {hasChildren ? (
                  <button onClick={() => toggleExpand(node.id)} className="p-1 hover:bg-gray-200 rounded">
                    {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  </button>
                ) : (
                  <span className="w-6 h-6 inline-block"></span>
                )}
                {node.image_url ? (
                  <img src={node.image_url} alt="" className="w-8 h-8 object-cover rounded bg-gray-100" />
                ) : (
                  <div className="w-8 h-8 bg-gray-100 rounded" />
                )}
                <span className="font-medium">{node.name}</span>
              </div>
            </td>
            <td className="py-3 px-4 text-gray-500">{node.slug}</td>
            <td className="py-3 px-4">{node.sort_order}</td>
            <td className="py-3 px-4">
              <span className={`px-2 py-1 text-xs rounded ${node.is_active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                {node.is_active ? "Aktif" : "Pasif"}
              </span>
            </td>
            <td className="py-3 px-4">
              <div className="flex items-center gap-2">
                <button onClick={() => openEditModal(node)} className="p-1 hover:bg-gray-100 rounded text-blue-600" title="Düzenle">
                  <Edit size={16} />
                </button>
                <button
                  onClick={() => handleTrendyolCategoryStockPrice(node.id, node.name)}
                  disabled={pushingCat === node.id}
                  className="p-1 hover:bg-orange-50 rounded text-orange-500 disabled:opacity-40 disabled:cursor-not-allowed"
                  title="Trendyol Stok/Fiyat Güncelle"
                >
                  <RefreshCw size={16} className={pushingCat === node.id ? "animate-spin" : ""} />
                </button>
                <button onClick={() => handleDelete(node.id)} className="p-1 hover:bg-gray-100 rounded text-red-500" title="Sil">
                  <Trash2 size={16} />
                </button>
              </div>
            </td>
          </tr>
          {hasChildren && isExpanded && renderTreeRows(node.children, level + 1)}
        </React.Fragment>
      );
    });
  };

  return (
    <div data-testid="admin-categories">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-4">
        <div>
          <h1 className="text-2xl font-bold">Kategori Yönetimi</h1>
          <p className="text-gray-500 text-sm mt-1">Hiyerarşik kategori ağacı ve pazaryeri eşleştirmeleri</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={async () => {
              try {
                const r = await axios.post(
                  `${API}/integrations/site/categories/sync-missing-from-products`,
                  {},
                  { headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } }
                );
                const created = r.data?.created_categories || [];
                if (created.length) {
                  toast.success(
                    `${r.data.created_count} eksik kategori bulundu: ${created
                      .map((c) => c.name)
                      .join(", ")} (${r.data.relinked_products} ürün bağlandı)`
                  );
                } else {
                  toast.success("Eksik kategori yok ✓");
                }
                fetchCategories();
              } catch (e) {
                toast.error("Eksik kategori senkronizasyonu başarısız");
              }
            }}
            className="flex items-center gap-2 bg-amber-500 text-white px-3 py-2 rounded hover:bg-amber-600 text-sm"
            title="Ürünlerden eksik kategorileri otomatik oluştur"
            data-testid="backfill-missing-cats"
          >
            <RefreshCw size={16} />
            Eksik Kategorileri Yükle
          </button>
          <button
            onClick={syncTrendyolCategories}
            disabled={syncingTrendyol}
            className="flex items-center gap-2 bg-[#F27A1A] text-white px-4 py-2 rounded hover:bg-[#d96a15] disabled:opacity-50"
          >
            <RefreshCw size={18} className={syncingTrendyol ? "animate-spin" : ""} />
            {syncingTrendyol ? "Çekiliyor..." : `Trendyol Kategorileri (${trendyolCategories.length})`}
          </button>
          <button 
            onClick={() => { resetForm(); setModalOpen(true); }}
            className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800"
          >
            <Plus size={18} />
            Yeni Kategori
          </button>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-sm border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-gray-50 text-gray-600 font-medium border-b">
              <tr>
                <th className="py-3 px-4">Kategori Adı</th>
                <th className="py-3 px-4">Slug</th>
                <th className="py-3 px-4">Sıra</th>
                <th className="py-3 px-4">Durum</th>
                <th className="py-3 px-4">İşlemler</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} className="text-center py-8">Yükleniyor...</td>
                </tr>
              ) : categories.length === 0 ? (
                <tr>
                  <td colSpan={5} className="text-center py-8 text-gray-500">Kategori bulunamadı</td>
                </tr>
              ) : (
                renderTreeRows(treeData)
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingCategory ? "Kategori Düzenle" : "Yeni Kategori"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">Üst Kategori</label>
              <select
                value={formData.parent_id}
                onChange={(e) => setFormData({ ...formData, parent_id: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm"
              >
                <option value="">Ana Kategori (Yok)</option>
                {categories.map((cat) => (
                  <option key={cat.id} value={cat.id} disabled={editingCategory?.id === cat.id}>
                    {cat.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Kategori Adı *</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value, slug: generateSlug(e.target.value) })}
                required
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Slug</label>
              <input
                type="text"
                value={formData.slug}
                onChange={(e) => setFormData({ ...formData, slug: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Trendyol Kategori Eşleşmesi</label>
              <SearchableMapSelect
                optionsUrl={`/category-mapping/trendyol/options`}
                value={{ id: formData.trendyol_category_id || "", name: trendyolCatSearch }}
                onChange={(v) => {
                  const newCatId = v.id || "";
                  setFormData({ ...formData, trendyol_category_id: newCatId, attribute_mapping: {} });
                  setTrendyolCatSearch(v.name || "");
                  if (newCatId) fetchTrendyolAttributes(newCatId);
                }}
                placeholder="Kategori ara... (örn: şort, kadın elbise)"
                treeMode={true}
                data-testid="cat-trendyol-search"
              />
              {formData.trendyol_category_id && (
                <p className="text-xs text-green-600 mt-1">Seçili ID: {formData.trendyol_category_id}</p>
              )}

              {/* Attribute Mapping Section */}
              {formData.trendyol_category_id && (
                <div className="mt-4 border-t pt-4">
                  <div className="flex items-center gap-2 mb-3">
                    <h4 className="text-sm font-semibold text-orange-700">Özellik Eşleştirmesi</h4>
                    {fetchingAttributes && <span className="text-xs text-gray-500 animate-pulse">Yükleniyor...</span>}
                  </div>
                  <p className="text-xs text-gray-500 mb-3">
                    Bu kategorideki ürünlerin özellik anahtarlarını Trendyol özellik ID'leriyle eşleştirin. Böylece ürünleri Trendyol'a gönderirken otomatik doldurmak için kullanılır.
                  </p>
                  {trendyolAttributes.length > 0 ? (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {trendyolAttributes.map((attr) => (
                        <div key={attr.attribute?.id} className={`p-3 rounded border ${attr.attribute?.required ? 'bg-orange-50 border-orange-200' : 'bg-gray-50 border-gray-200'}`}>
                          <label className="block text-xs font-semibold mb-1 text-gray-700">
                            {attr.attribute?.name}
                            {attr.attribute?.required ? <span className="text-red-500 ml-1">*</span> : <span className="text-gray-400 ml-1">(opsiyonel)</span>}
                          </label>
                          <input
                            type="text"
                            value={formData.attribute_mapping[attr.attribute?.id] || ""}
                            onChange={(e) => setFormData(prev => ({
                              ...prev,
                              attribute_mapping: { ...prev.attribute_mapping, [attr.attribute?.id]: e.target.value }
                            }))}
                            placeholder={`Ürün özellik anahtarı (örn: Kumaş)`}
                            className="w-full text-xs border px-2 py-1.5 rounded bg-white"
                          />
                          {attr.attributeValues?.length > 0 && (
                            <p className="text-[10px] text-gray-400 mt-1">Geçerli değerler: {attr.attributeValues.slice(0, 4).map(v => v.name).join(", ")}{attr.attributeValues.length > 4 ? "..." : ""}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : !fetchingAttributes ? (
                    <p className="text-xs text-gray-500 bg-gray-50 rounded p-3">Bu kategori için özellik bulunamadı.</p>
                  ) : null}
                </div>
              )}
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Hepsiburada Eşleşmesi (ID Ara)</label>
              <input
                type="text"
                value={formData.hepsiburada_category_id || ""}
                onChange={(e) => setFormData({ ...formData, hepsiburada_category_id: e.target.value })}
                placeholder="Örn: 12345"
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Amazon Eşleşmesi (ID Ara)</label>
              <input
                type="text"
                value={formData.amazon_category_id || ""}
                onChange={(e) => setFormData({ ...formData, amazon_category_id: e.target.value })}
                placeholder="Örn: 98765"
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Görsel URL</label>
              <input
                type="url"
                value={formData.image_url}
                onChange={(e) => setFormData({ ...formData, image_url: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Sıra</label>
              <input
                type="number"
                value={formData.sort_order}
                onChange={(e) => setFormData({ ...formData, sort_order: parseInt(e.target.value) || 0 })}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={formData.is_active}
                onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              />
              <span className="text-sm">Aktif</span>
            </label>
            <label className="flex items-center gap-2" data-testid="cat-members-only">
              <input
                type="checkbox"
                checked={!!formData.members_only}
                onChange={(e) => setFormData({ ...formData, members_only: e.target.checked })}
              />
              <span className="text-sm">Sadece üyelere özel <span className="text-gray-400">(giriş yapmayan göremez)</span></span>
            </label>
            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => setModalOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50">
                İptal
              </button>
              <button type="submit" className="px-4 py-2 bg-black text-white rounded hover:bg-gray-800">
                {editingCategory ? "Güncelle" : "Ekle"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
