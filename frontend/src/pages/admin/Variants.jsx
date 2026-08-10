import React, { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { 
  ArrowUp, 
  ArrowDown, 
  Save, 
  RefreshCw, 
  Trash2, 
  Search, 
  Plus, 
  Settings2, 
  Palette, 
  Maximize2 
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const authHeaders = () => {
  const token = localStorage.getItem("token");
  return { Authorization: `Bearer ${token}` };
};

export default function AdminVariants() {
  const [activeTab, setActiveTab] = useState("size"); // 'size' or 'color'
  const [variants, setVariants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [aggregating, setAggregating] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [newValue, setNewValue] = useState("");
  const [selectedIds, setSelectedIds] = useState(new Set()); // toplu seçim
  const [bulkDeleting, setBulkDeleting] = useState(false);

  useEffect(() => {
    setSelectedIds(new Set()); // tür değişince seçim sıfırlansın
    fetchVariants();
  }, [activeTab]);

  const fetchVariants = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/variants/${activeTab}`, {
        headers: authHeaders()
      });
      const sorted = res.data.sort((a, b) => a.sort_order - b.sort_order);
      setVariants(sorted);
    } catch (err) {
      console.error(err);
      toast.error("Varyantlar yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  const handleAggregate = async () => {
    setAggregating(true);
    try {
      const res = await axios.post(`${API}/variants/aggregate`, {}, {
        headers: authHeaders()
      });
      toast.success(res.data.message || "Sistemdeki varyantlar başarıyla toplandı.");
      fetchVariants();
    } catch (err) {
      console.error(err);
      toast.error("Varyantlar toplanırken bir hata oluştu");
    } finally {
      setAggregating(false);
    }
  };

  const handleAddValue = async (e) => {
    e.preventDefault();
    if (!newValue.trim()) return;

    if (variants.some(v => v.value.toLowerCase() === newValue.trim().toLowerCase())) {
      toast.error("Bu değer zaten mevcut");
      return;
    }

    try {
      const nextOrder = variants.length > 0 ? Math.max(...variants.map(v => v.sort_order)) + 1 : 1;
      await axios.post(`${API}/variants`, {
        type: activeTab,
        value: newValue.trim(),
        sort_order: nextOrder
      }, { headers: authHeaders() });
      
      toast.success("Yeni değer eklendi");
      setNewValue("");
      fetchVariants();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Ekleme başarısız");
    }
  };

  // DENETİM FIX: taşıma, filtrelenmiş listenin index'iyle değil variant.id ile GERÇEK
  // index üzerinden yapılır (arama aktifken yanlış varyant taşınıyordu).
  const moveUp = (id) => {
    const index = variants.findIndex(v => v.id === id);
    if (index <= 0) return;
    const newVariants = [...variants];
    [newVariants[index - 1], newVariants[index]] = [newVariants[index], newVariants[index - 1]];
    newVariants.forEach((v, i) => v.sort_order = i + 1);
    setVariants(newVariants);
  };

  const moveDown = (id) => {
    const index = variants.findIndex(v => v.id === id);
    if (index < 0 || index >= variants.length - 1) return;
    const newVariants = [...variants];
    [newVariants[index], newVariants[index + 1]] = [newVariants[index + 1], newVariants[index]];
    newVariants.forEach((v, i) => v.sort_order = i + 1);
    setVariants(newVariants);
  };

  const handleManualOrderChange = (index, newOrder) => {
    const parsed = parseInt(newOrder);
    if (isNaN(parsed) || parsed < 1) return;
    
    const newVariants = [...variants];
    newVariants[index].sort_order = parsed;
    newVariants.sort((a, b) => a.sort_order - b.sort_order);
    newVariants.forEach((v, i) => v.sort_order = i + 1);
    setVariants(newVariants);
  };

  const handleDelete = async (variantId, valName) => {
    toast(`"${valName}" silinsin mi?`, {
      action: {
        label: 'Sil',
        onClick: async () => {
          try {
            await axios.delete(`${API}/variants/${variantId}`, {
              headers: authHeaders()
            });
            toast.success("Varyant silindi");
            fetchVariants();
          } catch (err) {
            toast.error("Silme işlemi başarısız");
          }
        }
      },
      cancel: { label: 'İptal', onClick: () => {} },
      duration: 5000,
    });
  };

  const toggleSelect = (id) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = (ids) => {
    setSelectedIds(prev => {
      const allSelected = ids.length > 0 && ids.every(id => prev.has(id));
      return allSelected ? new Set() : new Set(ids);
    });
  };
  const handleBulkDelete = () => {
    const count = selectedIds.size;
    if (!count) return;
    toast(`Seçili ${count} değeri silmek istediğinize emin misiniz?`, {
      action: {
        label: 'Toplu Sil',
        onClick: async () => {
          setBulkDeleting(true);
          try {
            const res = await axios.post(`${API}/variants/bulk-delete`,
              { ids: Array.from(selectedIds) }, { headers: authHeaders() });
            toast.success(res.data.message || `${count} değer silindi`);
            setSelectedIds(new Set());
            fetchVariants();
          } catch (err) {
            toast.error("Toplu silme başarısız");
          } finally {
            setBulkDeleting(false);
          }
        }
      },
      cancel: { label: 'İptal', onClick: () => {} },
      duration: 6000,
    });
  };

  const handleSaveOrder = async () => {
    setSaving(true);
    try {
      const updateData = variants.map(v => ({ id: v.id, sort_order: v.sort_order }));
      await axios.put(`${API}/variants/reorder`, updateData, {
        headers: authHeaders()
      });
      toast.success("Sıralama başarıyla kaydedildi");
    } catch (err) {
      toast.error("Kaydetme başarısız");
    } finally {
      setSaving(false);
    }
  };

  const filteredVariants = variants.filter(v => 
    v.value.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="p-6 max-w-7xl mx-auto h-[calc(100vh-64px)] overflow-hidden flex flex-col" data-testid="admin-variants">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between mb-6 gap-4 shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Varyant Yönetimi</h1>
          <p className="text-gray-500 text-sm mt-1">
            Beden ve Renklerin listelenme sırasını ve değer havuzunu buradan yönetin.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleAggregate}
            disabled={aggregating}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-300 rounded-lg shadow-sm text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 disabled:opacity-50"
            title="Sistemdeki tüm ürünlerden varyant değerlerini toplar"
          >
            <RefreshCw size={16} className={aggregating ? 'animate-spin' : ''} />
            Mevcut Ürünlerden Topla
          </button>
          <button
            onClick={handleSaveOrder}
            disabled={saving || loading}
            className="flex items-center gap-2 px-4 py-2 bg-orange-500 text-white rounded-lg shadow-sm text-sm font-medium hover:bg-orange-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 disabled:opacity-50"
          >
            <Save size={18} />
            Sıralamayı Kaydet
          </button>
        </div>
      </div>

      <div className="flex bg-white rounded-xl shadow-sm border border-gray-200 flex-1 overflow-hidden min-h-0">
        {/* Left Pane - Types */}
        <div className="w-1/4 border-r border-gray-200 flex flex-col bg-gray-50/50">
          <div className="p-4 border-b border-gray-200 bg-white shrink-0">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">Seçenek Türü</h3>
          </div>
          <div className="p-2 space-y-1">
            <div
              onClick={() => { setActiveTab("size"); setSearchTerm(""); }}
              className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-all ${activeTab === 'size' ? 'bg-orange-50 border border-orange-200 text-orange-700 shadow-sm' : 'hover:bg-gray-100 border border-transparent text-gray-600'}`}
            >
              <Maximize2 size={18} className={activeTab === 'size' ? 'text-orange-500' : 'text-gray-400'} />
              <div className="flex-1">
                <p className="font-semibold text-sm text-inherit">Bedenler</p>
                <p className="text-[10px] opacity-70">Boyut ve Ölçü Varyantları</p>
              </div>
            </div>
            <div
              onClick={() => { setActiveTab("color"); setSearchTerm(""); }}
              className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-all ${activeTab === 'color' ? 'bg-orange-50 border border-orange-200 text-orange-700 shadow-sm' : 'hover:bg-gray-100 border border-transparent text-gray-600'}`}
            >
              <Palette size={18} className={activeTab === 'color' ? 'text-orange-500' : 'text-gray-400'} />
              <div className="flex-1">
                <p className="font-semibold text-sm text-inherit">Renkler</p>
                <p className="text-[10px] opacity-70">Görsel ve Ton Varyantları</p>
              </div>
            </div>
          </div>
          <div className="mt-auto p-4 border-t border-gray-200 bg-white opacity-50">
            <div className="flex items-center gap-2 text-xs text-gray-500 italic">
              <Settings2 size={14} />
              Yükleme sırası önceliklidir
            </div>
          </div>
        </div>

        {/* Right Pane - Values */}
        <div className="w-3/4 flex flex-col min-h-0 bg-white">
          <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between shrink-0 bg-white">
            <h2 className="text-lg font-bold text-gray-800 flex items-center gap-3">
              {activeTab === 'size' ? 'Beden' : 'Renk'} Değerleri Havuzu
              {selectedIds.size > 0 && (
                <button
                  onClick={handleBulkDelete}
                  disabled={bulkDeleting}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-red-500 text-white rounded-lg text-xs font-bold hover:bg-red-600 disabled:opacity-50 transition-colors"
                  title="Seçili değerleri toplu sil"
                >
                  <Trash2 size={14} />
                  Seçili {selectedIds.size} değeri sil
                </button>
              )}
            </h2>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
              <input
                type="text"
                placeholder="Değerlerde ara..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-64 pl-9 pr-4 py-2 bg-gray-50 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-500/50 focus:border-orange-500"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-0 admin-table-container">
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 z-10 bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 w-10 text-center">
                    <input
                      type="checkbox"
                      className="w-4 h-4 accent-orange-500 cursor-pointer"
                      title="Tümünü seç"
                      checked={filteredVariants.length > 0 && filteredVariants.every(v => selectedIds.has(v.id))}
                      onChange={() => toggleSelectAll(filteredVariants.map(v => v.id))}
                    />
                  </th>
                  <th className="px-6 py-3 text-xs font-bold text-gray-400 uppercase tracking-wider w-24 text-center">Sıra</th>
                  <th className="px-6 py-3 text-xs font-bold text-gray-400 uppercase tracking-wider">Değer</th>
                  <th className="px-6 py-3 text-xs font-bold text-gray-400 uppercase tracking-wider w-40 text-center">İşlemler</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {loading ? (
                  <tr>
                    <td colSpan={4} className="py-20 text-center">
                      <div className="flex flex-col items-center gap-2">
                        <RefreshCw className="animate-spin text-gray-400" size={32} />
                        <span className="text-sm text-gray-500 font-medium">Yükleniyor...</span>
                      </div>
                    </td>
                  </tr>
                ) : filteredVariants.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="py-20 text-center">
                      <p className="text-gray-400 text-sm">Burada henüz bir değer yok.</p>
                    </td>
                  </tr>
                ) : (
                  filteredVariants.map((variant, index) => (
                    <tr key={variant.id} className={`group transition-colors ${selectedIds.has(variant.id) ? 'bg-orange-50/60' : 'hover:bg-gray-50/80'}`}>
                      <td className="px-4 py-3 text-center">
                        <input
                          type="checkbox"
                          className="w-4 h-4 accent-orange-500 cursor-pointer"
                          checked={selectedIds.has(variant.id)}
                          onChange={() => toggleSelect(variant.id)}
                        />
                      </td>
                      <td className="px-6 py-3 text-center">
                        <input
                          type="number"
                          value={variant.sort_order}
                          onChange={(e) => handleManualOrderChange(index, e.target.value)}
                          className="w-14 text-center border border-gray-200 rounded py-1 text-sm focus:ring-1 focus:ring-orange-500 outline-none bg-white font-medium"
                        />
                      </td>
                      <td className="px-6 py-3">
                        <span className="text-base font-medium text-gray-700">{variant.value}</span>
                      </td>
                      <td className="px-6 py-3">
                        <div className="flex justify-center items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={() => moveUp(variant.id)}
                            disabled={!!searchTerm}
                            title={searchTerm ? "Sıralama için aramayı temizleyin" : "Yukarı Taşı"}
                            className="p-2 bg-white border border-gray-200 rounded-md hover:border-orange-200 hover:text-orange-600 disabled:opacity-30 transition-all shadow-sm"
                          >
                            <ArrowUp size={14} />
                          </button>
                          <button
                            onClick={() => moveDown(variant.id)}
                            disabled={!!searchTerm}
                            title={searchTerm ? "Sıralama için aramayı temizleyin" : "Aşağı Taşı"}
                            className="p-2 bg-white border border-gray-200 rounded-md hover:border-orange-200 hover:text-orange-600 disabled:opacity-30 transition-all shadow-sm"
                          >
                            <ArrowDown size={14} />
                          </button>
                          <div className="w-px h-4 bg-gray-200 mx-1" />
                          <button 
                            onClick={() => handleDelete(variant.id, variant.value)}
                            className="p-2 text-red-500 bg-white border border-gray-200 rounded-md hover:bg-red-50 hover:border-red-200 transition-all shadow-sm"
                            title="Sil"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="p-4 border-t border-gray-200 bg-gray-50 shrink-0">
            <form onSubmit={handleAddValue} className="max-w-xl flex gap-3">
              <input
                type="text"
                placeholder={`Yeni ${activeTab === 'size' ? 'Beden (örn: L, 42, Standart)' : 'Renk (örn: Siyah, Lacivert)'} ekle...`}
                value={newValue}
                onChange={(e) => setNewValue(e.target.value)}
                className="flex-1 bg-white border border-gray-300 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500/50 focus:border-orange-500"
              />
              <button
                type="submit"
                disabled={!newValue.trim()}
                className="flex items-center gap-2 px-6 py-2 bg-gray-800 text-white rounded-lg text-sm font-bold hover:bg-gray-900 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-900 disabled:opacity-50 transition-all shadow-md"
              >
                <Plus size={18} />
                Havuzuna Ekle
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
