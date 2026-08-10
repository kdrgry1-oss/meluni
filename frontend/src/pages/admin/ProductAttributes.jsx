import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Plus, Trash2, Edit2, Search, DownloadCloud, RefreshCw, X, Check } from 'lucide-react';
import { toast } from 'sonner';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const authHeaders = () => {
  const token = localStorage.getItem("token");
  return { Authorization: `Bearer ${token}` };
};

export default function ProductAttributes() {
  const [attributes, setAttributes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [selectedAttr, setSelectedAttr] = useState(null);

  const [searchAttr, setSearchAttr] = useState('');
  const [searchValue, setSearchValue] = useState('');

  // Modals / Inline Edit State
  const [isAddingAttr, setIsAddingAttr] = useState(false);
  const [newAttrName, setNewAttrName] = useState('');

  const [newValueName, setNewValueName] = useState('');

  useEffect(() => {
    fetchAttributes();
  }, []);

  const fetchAttributes = async () => {
    try {
      setLoading(true);
      const res = await axios.get(`${API}/attributes`, { headers: authHeaders() });
      setAttributes(res.data.attributes || []);
      
      // If an attribute is currently selected, refresh its data too
      if (selectedAttr) {
        const updated = res.data.attributes.find((a) => a.id === selectedAttr.id);
        if (updated) setSelectedAttr(updated);
        else setSelectedAttr(null);
      }
    } catch (err) {
      toast.error('Özellikler yüklenemedi');
    } finally {
      setLoading(false);
    }
  };

  const syncFromTrendyol = async () => {
    try {
      setSyncing(true);
      const res = await axios.post(`${API}/attributes/sync-from-trendyol`, {}, { headers: authHeaders() });
      toast.success(res.data.message || 'Senkronizasyon tamamlandı');
      fetchAttributes();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Senkronizasyon başarısız oldu');
    } finally {
      setSyncing(false);
    }
  };

  const syncFromProducts = async () => {
    try {
      setSyncing(true);
      const res = await axios.post(`${API}/attributes/sync-from-products`, {}, { headers: authHeaders() });
      toast.success(res.data.message || 'Ürünlerden toplama tamamlandı');
      fetchAttributes();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'İşlem başarısız oldu');
    } finally {
      setSyncing(false);
    }
  };

  const handleAddAttribute = async (e) => {
    e.preventDefault();
    if (!newAttrName.trim()) return;
    try {
      const res = await axios.post(
        `${API}/attributes`,
        { name: newAttrName.trim(), values: [] },
        { headers: authHeaders() }
      );
      toast.success('Özellik eklendi');
      setNewAttrName('');
      setIsAddingAttr(false);
      fetchAttributes();
      setSelectedAttr(res.data.attribute);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Ekleme başarısız');
    }
  };

  const handleDeleteAttribute = async (id, name) => {
    toast(`"${name}" silinsin mi?`, {
      action: {
        label: 'Sil',
        onClick: async () => {
          try {
            await axios.delete(`${API}/attributes/${id}`, { headers: authHeaders() });
            toast.success('Özellik silindi');
            if (selectedAttr?.id === id) setSelectedAttr(null);
            fetchAttributes();
          } catch (err) {
            toast.error('Silinemedi');
          }
        }
      },
      cancel: { label: 'İptal', onClick: () => {} },
      duration: 8000,
    });
  };

  const handleAddValue = async (e) => {
    e.preventDefault();
    if (!newValueName.trim() || !selectedAttr) return;

    const currentValues = selectedAttr.values || [];
    if (currentValues.some(v => v.toLowerCase() === newValueName.trim().toLowerCase())) {
      toast.error('Bu değer zaten mevcut');
      return;
    }

    const updatedValues = [...currentValues, newValueName.trim()];
    try {
      await axios.put(
        `${API}/attributes/${selectedAttr.id}`,
        { name: selectedAttr.name, values: updatedValues },
        { headers: authHeaders() }
      );
      toast.success('Değer eklendi');
      setNewValueName('');
      fetchAttributes();
    } catch (err) {
      toast.error('Değer eklenemedi');
    }
  };

  const handleDeleteValue = async (valToRemove) => {
    if (!selectedAttr) return;

    toast(`"${valToRemove}" değeri silinsin mi?`, {
      action: {
        label: 'Sil',
        onClick: async () => {
          const updatedValues = selectedAttr.values.filter(v => v !== valToRemove);
          try {
            await axios.put(
              `${API}/attributes/${selectedAttr.id}`,
              { name: selectedAttr.name, values: updatedValues },
              { headers: authHeaders() }
            );
            toast.success('Değer silindi');
            fetchAttributes();
          } catch (err) {
            toast.error('Değer silinemedi');
          }
        }
      },
      cancel: { label: 'İptal', onClick: () => {} },
      duration: 8000,
    });
  };

  const filteredAttributes = attributes.filter(a =>
    a.name.toLowerCase().includes(searchAttr.toLowerCase())
  );

  const filteredValues = selectedAttr?.values?.filter(v =>
    v.toLowerCase().includes(searchValue.toLowerCase())
  ).sort() || [];

  return (
    <div className="p-6 max-w-7xl mx-auto h-[calc(100vh-64px)] overflow-hidden flex flex-col">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between mb-6 gap-4 shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Ürün Özellikleri</h1>
          <p className="text-gray-500 text-sm mt-1">
            Ürün varyantları (Beden, Renk) için özellik ve değer havuzunu yönetin.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={syncFromProducts}
            disabled={syncing}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-300 rounded-lg shadow-sm text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 disabled:opacity-50"
          >
            <RefreshCw size={16} className={syncing ? 'animate-spin' : ''} />
            Mevcut Ürünlerden Topla
          </button>
          <button
            onClick={syncFromTrendyol}
            disabled={syncing}
            className="flex items-center gap-2 px-4 py-2 bg-orange-500 text-white rounded-lg shadow-sm text-sm font-medium hover:bg-orange-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 disabled:opacity-50"
          >
            <DownloadCloud size={16} className={syncing ? 'animate-bounce' : ''} />
            Trendyol'dan Aktar
          </button>
        </div>
      </div>

      <div className="flex bg-white rounded-xl shadow-sm border border-gray-200 flex-1 overflow-hidden min-h-0">
        
        {/* Left Pane - Attributes List */}
        <div className="w-1/3 border-r border-gray-200 flex flex-col bg-gray-50/50">
          <div className="p-4 border-b border-gray-200 shrink-0">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
              <input
                type="text"
                placeholder="Özellik ara..."
                value={searchAttr}
                onChange={(e) => setSearchAttr(e.target.value)}
                className="w-full pl-9 pr-4 py-2 bg-white border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-500/50 focus:border-orange-500"
              />
            </div>
          </div>
          
          <div className="flex-1 overflow-y-auto p-2">
            {loading ? (
              <div className="p-4 text-center text-sm text-gray-500">Yükleniyor...</div>
            ) : filteredAttributes.length === 0 ? (
              <div className="p-4 text-center text-sm text-gray-500">Özellik bulunamadı.</div>
            ) : (
              <div className="space-y-1">
                {filteredAttributes.map((attr) => (
                  <div
                    key={attr.id}
                    onClick={() => { setSelectedAttr(attr); setSearchValue(''); setIsAddingAttr(false); }}
                    className={`group flex items-center justify-between p-3 rounded-lg cursor-pointer transition-colors ${selectedAttr?.id === attr.id ? 'bg-orange-50 border border-orange-200' : 'hover:bg-gray-100 border border-transparent'}`}
                  >
                    <div>
                      <h3 className={`font-medium ${selectedAttr?.id === attr.id ? 'text-orange-700' : 'text-gray-800'}`}>
                        {attr.name}
                      </h3>
                      <p className="text-xs text-gray-500 mt-0.5">{attr.values?.length || 0} değer</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeleteAttribute(attr.id, attr.name); }}
                      className="hidden group-hover:block p-1.5 text-red-500 hover:bg-red-100 rounded"
                      title="Sil"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          
          <div className="p-4 border-t border-gray-200 bg-white shrink-0">
            {isAddingAttr ? (
              <form onSubmit={handleAddAttribute} className="flex gap-2">
                <input
                  type="text"
                  autoFocus
                  placeholder="Yeni Özellik Adı"
                  value={newAttrName}
                  onChange={(e) => setNewAttrName(e.target.value)}
                  className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
                />
                <button type="submit" disabled={!newAttrName.trim()} className="bg-orange-500 text-white p-2 rounded-lg hover:bg-orange-600 disabled:opacity-50">
                  <Check size={16} />
                </button>
                <button type="button" onClick={() => { setIsAddingAttr(false); setNewAttrName(''); }} className="bg-gray-100 text-gray-600 p-2 rounded-lg hover:bg-gray-200">
                  <X size={16} />
                </button>
              </form>
            ) : (
              <button
                onClick={() => setIsAddingAttr(true)}
                className="w-full flex items-center justify-center gap-2 py-2 border border-dashed border-gray-300 rounded-lg text-sm font-medium text-gray-600 hover:text-orange-600 hover:border-orange-300 hover:bg-orange-50 transition-colors"
              >
                <Plus size={16} />
                Yeni Özellik Ekle
              </button>
            )}
          </div>
        </div>

        {/* Right Pane - Values List */}
        <div className="w-2/3 flex flex-col min-h-0 bg-white">
          {!selectedAttr ? (
            <div className="flex-1 flex flex-col items-center justify-center text-gray-400">
              <RefreshCw size={48} className="mb-4 text-gray-200" />
              <p>Değerleri görmek için soldan bir özellik seçin</p>
            </div>
          ) : (
            <>
              <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between shrink-0">
                <h2 className="text-lg font-semibold text-gray-800">{selectedAttr.name} Değerleri</h2>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
                  <input
                    type="text"
                    placeholder="Değer ara..."
                    value={searchValue}
                    onChange={(e) => setSearchValue(e.target.value)}
                    className="w-48 pl-9 pr-4 py-1.5 bg-gray-50 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
                  />
                </div>
              </div>

              <div className="flex-1 overflow-y-auto p-6">
                <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                  {filteredValues.map((val, idx) => (
                    <div
                      key={idx}
                      className="group flex items-center justify-between bg-white border border-gray-200 p-3 rounded-lg hover:border-orange-300 hover:shadow-sm transition-all"
                    >
                      <span className="text-sm font-medium text-gray-700 truncate" title={val}>{val}</span>
                      <button
                        onClick={() => handleDeleteValue(val)}
                        className="opacity-0 group-hover:opacity-100 p-1 text-red-500 hover:bg-red-50 rounded transition-opacity"
                        title="Sil"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                </div>
                {filteredValues.length === 0 && (
                  <div className="text-center py-12 text-gray-500 text-sm">
                    Bu özellikte henüz bir değer yok veya aramaya uygun değer bulunamadı.
                  </div>
                )}
              </div>

              <div className="p-4 border-t border-gray-200 bg-gray-50 shrink-0">
                <form onSubmit={handleAddValue} className="max-w-md flex gap-2">
                  <input
                    type="text"
                    placeholder="Yeni değer ekle..."
                    value={newValueName}
                    onChange={(e) => setNewValueName(e.target.value)}
                    className="flex-1 border border-gray-300 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
                  />
                  <button
                    type="submit"
                    disabled={!newValueName.trim()}
                    className="flex items-center gap-2 px-4 py-2 bg-gray-800 text-white rounded-lg text-sm font-medium hover:bg-gray-900 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-900 disabled:opacity-50"
                  >
                    <Plus size={16} />
                    Ekle
                  </button>
                </form>
              </div>
            </>
          )}
        </div>

      </div>
    </div>
  );
}
