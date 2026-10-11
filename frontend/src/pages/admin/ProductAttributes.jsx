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
  const [selAttrIds, setSelAttrIds] = useState(new Set());   // toplu ÖZELLİK seçimi
  const [selVals, setSelVals] = useState(new Set());         // toplu DEĞER seçimi

  // ── ÖZELLİK AYAR KARTI durumu ──────────────────────────────────────────────
  const [requiredIn, setRequiredIn] = useState({ grouped: {}, loading: false });
  const [defaultValueDraft, setDefaultValueDraft] = useState('');
  const [savingSetting, setSavingSetting] = useState(false);
  const [categories, setCategories] = useState([]);        // kategori-bazlı zorunlu seçici için
  const [catPickerOpen, setCatPickerOpen] = useState(false);
  const [catSearch, setCatSearch] = useState('');

  useEffect(() => {
    fetchAttributes();
    // Kategori-bazlı "bizim için zorunlu" seçici için yerel kategoriler.
    axios.get(`${API}/categories`, { headers: authHeaders() })
      .then((res) => setCategories(Array.isArray(res.data) ? res.data : (res.data?.categories || [])))
      .catch(() => setCategories([]));
  }, []);

  // Seçili özellik değişince: ayar kartı alanlarını doldur + zorunlu-pazaryeri listesini çek.
  useEffect(() => {
    if (!selectedAttr?.id) {
      setRequiredIn({ grouped: {}, loading: false });
      setDefaultValueDraft('');
      return;
    }
    setDefaultValueDraft(selectedAttr.default_value || '');
    let cancelled = false;
    setRequiredIn({ grouped: {}, loading: true });
    axios.get(`${API}/attributes/${selectedAttr.id}/required-in`, { headers: authHeaders() })
      .then((res) => { if (!cancelled) setRequiredIn({ grouped: res.data.grouped || {}, loading: false }); })
      .catch(() => { if (!cancelled) setRequiredIn({ grouped: {}, loading: false }); });
    return () => { cancelled = true; };
  }, [selectedAttr?.id]);

  // Ayar kartı KISMİ kaydı: yalnız değişen alan(lar) PUT edilir (name/values korunur).
  const patchSetting = async (fields, okMsg) => {
    if (!selectedAttr?.id) return;
    try {
      setSavingSetting(true);
      await axios.put(`${API}/attributes/${selectedAttr.id}`, fields, { headers: authHeaders() });
      // Optimistik: yerel state'i güncelle (yeniden çekmeye gerek yok — hızlı his)
      setSelectedAttr((prev) => (prev ? { ...prev, ...fields } : prev));
      setAttributes((prev) => prev.map((a) => (a.id === selectedAttr.id ? { ...a, ...fields } : a)));
      if (okMsg) toast.success(okMsg);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Ayar kaydedilemedi');
      // Hata halinde sunucu gerçeğiyle tazele
      fetchAttributes();
    } finally {
      setSavingSetting(false);
    }
  };

  const saveDefaultValue = () => {
    const next = (defaultValueDraft || '').trim();
    if (next === (selectedAttr?.default_value || '')) return;  // değişmediyse boşuna PUT etme
    patchSetting({ default_value: next }, next ? 'Varsayılan değer kaydedildi' : 'Varsayılan değer temizlendi');
  };

  // Kategori-bazlı "bizim için zorunlu": bu özelliğin category_required listesine
  // yerel kategori id ekle/çıkar → anında PUT (senkron yansıma).
  const toggleCategoryRequired = (catId) => {
    if (!selectedAttr?.id) return;
    const cur = Array.isArray(selectedAttr.category_required) ? selectedAttr.category_required.map(String) : [];
    const id = String(catId);
    const next = cur.includes(id) ? cur.filter((c) => c !== id) : [...cur, id];
    patchSetting({ category_required: next }, cur.includes(id) ? 'Kategori zorunluluğu kaldırıldı' : 'Kategoride zorunlu yapıldı');
  };

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
    // SCOPE: yalnız SEÇİLİ özelliğin Trendyol değerleri çekilir; diğer özellikler kirlenmez.
    if (!selectedAttr?.name) {
      toast.error('Önce soldan bir özellik seçin — Trendyol değerleri yalnız o özelliğe eklenir.');
      return;
    }
    try {
      setSyncing(true);
      const res = await axios.post(
        `${API}/attributes/sync-from-trendyol`,
        { attribute_name: selectedAttr.name },
        { headers: authHeaders() }
      );
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

  const toggleIn = (setter) => (key) => setter((prev) => {
    const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n;
  });
  const toggleAttrSel = toggleIn(setSelAttrIds);
  const toggleValSel = toggleIn(setSelVals);

  const bulkDeleteAttrs = async () => {
    const ids = Array.from(selAttrIds);
    if (!ids.length) return;
    if (!window.confirm(`${ids.length} özellik silinsin mi?`)) return;
    let ok = 0;
    for (const id of ids) {
      try { await axios.delete(`${API}/attributes/${id}`, { headers: authHeaders() }); ok++; } catch { /* atla */ }
    }
    if (selectedAttr && ids.includes(selectedAttr.id)) setSelectedAttr(null);
    setSelAttrIds(new Set());
    toast.success(`${ok}/${ids.length} özellik silindi`);
    fetchAttributes();
  };
  const bulkDeleteVals = async () => {
    if (!selectedAttr) return;
    const vals = Array.from(selVals);
    if (!vals.length) return;
    if (!window.confirm(`${vals.length} değer silinsin mi?`)) return;
    const updated = (selectedAttr.values || []).filter(v => !selVals.has(v));
    try {
      await axios.put(`${API}/attributes/${selectedAttr.id}`,
        { name: selectedAttr.name, values: updated }, { headers: authHeaders() });
      toast.success(`${vals.length} değer silindi`);
      setSelVals(new Set());
      fetchAttributes();
    } catch { toast.error('Silinemedi'); }
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
            disabled={syncing || !selectedAttr?.name}
            title={selectedAttr?.name
              ? `Yalnız "${selectedAttr.name}" özelliğine Trendyol değerleri eklenir`
              : 'Önce soldan bir özellik seçin'}
            className="flex items-center gap-2 px-4 py-2 bg-orange-500 text-white rounded-lg shadow-sm text-sm font-medium hover:bg-orange-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 disabled:opacity-50"
          >
            <DownloadCloud size={16} className={syncing ? 'animate-bounce' : ''} />
            {selectedAttr?.name ? `Trendyol'dan Aktar (${selectedAttr.name})` : "Trendyol'dan Aktar"}
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
                {selAttrIds.size > 0 && (
                  <div className="flex items-center justify-between px-2 py-1.5 mb-1 bg-red-50 border border-red-200 rounded-lg">
                    <span className="text-xs font-medium text-red-700">{selAttrIds.size} özellik seçili</span>
                    <div className="flex gap-2">
                      <button onClick={() => setSelAttrIds(new Set())} className="text-xs text-gray-500 hover:underline">Vazgeç</button>
                      <button onClick={bulkDeleteAttrs} className="text-xs font-semibold text-red-600 hover:underline flex items-center gap-1"><Trash2 size={12} /> Seçilenleri Sil</button>
                    </div>
                  </div>
                )}
                {filteredAttributes.map((attr) => (
                  <div
                    key={attr.id}
                    onClick={() => { setSelectedAttr(attr); setSearchValue(''); setIsAddingAttr(false); setSelVals(new Set()); }}
                    className={`group flex items-center justify-between p-3 rounded-lg cursor-pointer transition-colors ${selectedAttr?.id === attr.id ? 'bg-orange-50 border border-orange-200' : 'hover:bg-gray-100 border border-transparent'}`}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <input
                        type="checkbox"
                        checked={selAttrIds.has(attr.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggleAttrSel(attr.id)}
                        className="w-4 h-4 accent-red-500 shrink-0"
                        title="Toplu silme için seç"
                      />
                      <div className="min-w-0">
                        <h3 className={`font-medium truncate ${selectedAttr?.id === attr.id ? 'text-orange-700' : 'text-gray-800'}`}>
                          {attr.name}
                        </h3>
                        <p className="text-xs text-gray-500 mt-0.5">{attr.values?.length || 0} değer</p>
                      </div>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeleteAttribute(attr.id, attr.name); }}
                      className="hidden group-hover:block p-1.5 text-red-500 hover:bg-red-100 rounded shrink-0"
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
                {/* ── ÖZELLİK AYAR KARTI ─────────────────────────────────────── */}
                <div className="mb-6 rounded-xl border border-gray-200 bg-gradient-to-b from-gray-50 to-white p-5 shadow-sm">
                  <h3 className="text-sm font-bold text-gray-800 mb-4 flex items-center gap-2">
                    <span className="inline-block w-1.5 h-4 bg-orange-500 rounded-full" />
                    "{selectedAttr.name}" Ayar Kartı
                  </h3>

                  {/* 1) Zorunlu olduğu pazaryeri/kategoriler (salt-okunur) */}
                  <div className="mb-4">
                    <label className="block text-xs font-semibold text-gray-600 mb-1.5">
                      Zorunlu olduğu pazaryeri / kategoriler
                    </label>
                    {requiredIn.loading ? (
                      <div className="text-xs text-gray-400">Yükleniyor...</div>
                    ) : Object.keys(requiredIn.grouped).length === 0 ? (
                      <div className="text-xs text-gray-500 bg-gray-100 rounded-lg px-3 py-2">
                        Hiçbir pazaryerinde zorunlu değil.
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(requiredIn.grouped).map(([mp, cats]) => {
                          const label = { trendyol: 'Trendyol', hepsiburada: 'Hepsiburada', temu: 'Temu' }[mp] || mp;
                          return (
                            <div key={mp} className="text-xs bg-red-50 border border-red-200 text-red-800 rounded-lg px-3 py-1.5">
                              <span className="font-semibold">{label}:</span> {cats.join(', ')}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  {/* 2) Tüm sistemde varsayılan değer (input) */}
                  <div className="mb-4">
                    <label className="block text-xs font-semibold text-gray-600 mb-1.5">
                      Tüm sistemde varsayılan değer
                    </label>
                    {(selectedAttr.values || []).length > 0 ? (
                      <select
                        value={defaultValueDraft}
                        disabled={savingSetting}
                        onChange={(e) => { setDefaultValueDraft(e.target.value); }}
                        onBlur={saveDefaultValue}
                        className="w-full max-w-sm border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-orange-500 disabled:opacity-50"
                      >
                        <option value="">— Yok (boş) —</option>
                        {[...(selectedAttr.values || [])].sort().map((v, i) => (
                          <option key={i} value={v}>{v}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={defaultValueDraft}
                        disabled={savingSetting}
                        placeholder="Örn. Yetişkin (boş bırakılırsa varsayılan uygulanmaz)"
                        onChange={(e) => setDefaultValueDraft(e.target.value)}
                        onBlur={saveDefaultValue}
                        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); } }}
                        className="w-full max-w-sm border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500 disabled:opacity-50"
                      />
                    )}
                    <p className="text-[11px] text-gray-400 mt-1">
                      Push sırasında ürün bu özelliği taşımıyorsa bu değer kullanılır. Boşaltırsanız
                      sistem bir daha dayatmaz.
                    </p>
                  </div>

                  {/* 3 & 4) Toggle'lar */}
                  <div className="flex flex-col sm:flex-row gap-3">
                    <button
                      type="button"
                      disabled={savingSetting}
                      onClick={() => patchSetting(
                        { show_in_product_card: !(selectedAttr.show_in_product_card !== false) },
                        'Kaydedildi'
                      )}
                      className={`flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50 ${selectedAttr.show_in_product_card !== false ? 'bg-green-50 border-green-300 text-green-800' : 'bg-gray-50 border-gray-300 text-gray-500'}`}
                    >
                      <span>Ürün kartında göster</span>
                      <span className={`inline-flex items-center h-5 w-9 rounded-full transition-colors ${selectedAttr.show_in_product_card !== false ? 'bg-green-500' : 'bg-gray-300'}`}>
                        <span className={`inline-block h-4 w-4 bg-white rounded-full shadow transform transition-transform ${selectedAttr.show_in_product_card !== false ? 'translate-x-4' : 'translate-x-0.5'}`} />
                      </span>
                    </button>

                    <button
                      type="button"
                      disabled={savingSetting}
                      onClick={() => patchSetting(
                        { our_required: !(selectedAttr.our_required === true) },
                        'Kaydedildi'
                      )}
                      className={`flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50 ${selectedAttr.our_required === true ? 'bg-orange-50 border-orange-300 text-orange-800' : 'bg-gray-50 border-gray-300 text-gray-500'}`}
                    >
                      <span>Bizim için zorunlu</span>
                      <span className={`inline-flex items-center h-5 w-9 rounded-full transition-colors ${selectedAttr.our_required === true ? 'bg-orange-500' : 'bg-gray-300'}`}>
                        <span className={`inline-block h-4 w-4 bg-white rounded-full shadow transform transition-transform ${selectedAttr.our_required === true ? 'translate-x-4' : 'translate-x-0.5'}`} />
                      </span>
                    </button>
                  </div>
                  <p className="text-[11px] text-gray-400 mt-2">
                    "Bizim için zorunlu": pazaryeri zorunlu tutmasa da bu özellik boşsa doğrulama/eksik
                    raporunda uyarı verilir.
                  </p>

                  {/* 5) İLGİLİ KATEGORİDE ZORUNLU (kategori-bazlı) */}
                  <div className="mt-4 pt-4 border-t border-gray-200">
                    <label className="block text-xs font-semibold text-gray-600 mb-1.5">
                      İlgili kategoride zorunlu
                    </label>
                    <div className="flex flex-wrap gap-2 mb-2">
                      {(selectedAttr.category_required || []).length === 0 ? (
                        <span className="text-xs text-gray-500 bg-gray-100 rounded-lg px-3 py-1.5">
                          Belirli bir kategoride zorunlu değil.
                        </span>
                      ) : (
                        (selectedAttr.category_required || []).map((cid) => {
                          const c = categories.find((x) => String(x.id) === String(cid));
                          return (
                            <span key={cid} className="inline-flex items-center gap-1.5 text-xs bg-orange-50 border border-orange-200 text-orange-800 rounded-lg px-2.5 py-1.5">
                              {c?.name || cid}
                              <button
                                type="button"
                                disabled={savingSetting}
                                onClick={() => toggleCategoryRequired(cid)}
                                className="text-orange-500 hover:text-orange-700 disabled:opacity-50"
                                title="Kaldır"
                              >
                                <X size={12} />
                              </button>
                            </span>
                          );
                        })
                      )}
                    </div>
                    <div className="relative">
                      <button
                        type="button"
                        disabled={savingSetting}
                        onClick={() => setCatPickerOpen((o) => !o)}
                        className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg border border-gray-300 bg-white text-gray-700 hover:border-orange-400 disabled:opacity-50"
                      >
                        <Plus size={13} /> Kategori ekle
                      </button>
                      {catPickerOpen && (
                        <div className="absolute z-20 mt-1 w-72 max-h-72 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg p-2">
                          <input
                            type="text"
                            autoFocus
                            placeholder="Kategori ara..."
                            value={catSearch}
                            onChange={(e) => setCatSearch(e.target.value)}
                            className="w-full mb-2 px-2.5 py-1.5 border border-gray-300 rounded-md text-xs focus:outline-none focus:ring-2 focus:ring-orange-500"
                          />
                          {categories
                            .filter((c) => (c.name || '').toLowerCase().includes(catSearch.toLowerCase()))
                            .slice(0, 200)
                            .map((c) => {
                              const on = (selectedAttr.category_required || []).map(String).includes(String(c.id));
                              return (
                                <button
                                  key={c.id}
                                  type="button"
                                  disabled={savingSetting}
                                  onClick={() => toggleCategoryRequired(c.id)}
                                  className={`w-full text-left flex items-center justify-between px-2.5 py-1.5 rounded-md text-xs disabled:opacity-50 ${on ? 'bg-orange-50 text-orange-800' : 'hover:bg-gray-50 text-gray-700'}`}
                                >
                                  <span className="truncate">{c.name}</span>
                                  {on && <Check size={13} className="text-orange-600 shrink-0" />}
                                </button>
                              );
                            })}
                          {categories.length === 0 && (
                            <div className="text-xs text-gray-400 px-2 py-3 text-center">Kategori yüklenemedi.</div>
                          )}
                        </div>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-400 mt-2">
                      Seçilen kategori(ler)deki ürünlerde bu özellik boşsa doğrulama panelinde
                      "bu kategoride zorunlu" uyarısı verilir. "Bizim için zorunlu"dan farkı: tüm sistem
                      değil, yalnız seçtiğiniz kategori(ler).
                    </p>
                  </div>

                  {/* 6) ÜRÜN VERİSİNDEN ÇEK (kartta gösterme) */}
                  <div className="mt-4 pt-4 border-t border-gray-200">
                    <button
                      type="button"
                      disabled={savingSetting}
                      onClick={() => patchSetting(
                        { collect_from_products: !(selectedAttr.collect_from_products === true) },
                        'Kaydedildi'
                      )}
                      className={`w-full sm:w-auto flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50 ${selectedAttr.collect_from_products === true ? 'bg-blue-50 border-blue-300 text-blue-800' : 'bg-gray-50 border-gray-300 text-gray-500'}`}
                    >
                      <span>Ürün verisinden çek (kartta gösterme)</span>
                      <span className={`inline-flex items-center h-5 w-9 rounded-full transition-colors ${selectedAttr.collect_from_products === true ? 'bg-blue-500' : 'bg-gray-300'}`}>
                        <span className={`inline-block h-4 w-4 bg-white rounded-full shadow transform transition-transform ${selectedAttr.collect_from_products === true ? 'translate-x-4' : 'translate-x-0.5'}`} />
                      </span>
                    </button>
                    <p className="text-[11px] text-gray-400 mt-2">
                      Değerler ürün verisinden (varyant/özellik) otomatik toplanır — Beden/Renk gibi.
                      Ürün kartında gizlemek için ayrıca "Ürün kartında göster"i kapatın.
                    </p>
                  </div>
                </div>

                {selVals.size > 0 && (
                  <div className="flex items-center justify-between px-3 py-2 mb-3 bg-red-50 border border-red-200 rounded-lg">
                    <span className="text-xs font-medium text-red-700">{selVals.size} değer seçili</span>
                    <div className="flex gap-3">
                      <button onClick={() => setSelVals(new Set())} className="text-xs text-gray-500 hover:underline">Vazgeç</button>
                      <button onClick={bulkDeleteVals} className="text-xs font-semibold text-red-600 hover:underline flex items-center gap-1"><Trash2 size={12} /> Seçilenleri Sil</button>
                    </div>
                  </div>
                )}
                <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                  {filteredValues.map((val, idx) => (
                    <div
                      key={idx}
                      className={`group flex items-center justify-between bg-white border p-3 rounded-lg hover:shadow-sm transition-all ${selVals.has(val) ? 'border-red-300 bg-red-50/50' : 'border-gray-200 hover:border-orange-300'}`}
                    >
                      <label className="flex items-center gap-2 min-w-0 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={selVals.has(val)}
                          onChange={() => toggleValSel(val)}
                          className="w-4 h-4 accent-red-500 shrink-0"
                          title="Toplu silme için seç"
                        />
                        <span className="text-sm font-medium text-gray-700 truncate" title={val}>{val}</span>
                      </label>
                      <button
                        onClick={() => handleDeleteValue(val)}
                        className="opacity-0 group-hover:opacity-100 p-1 text-red-500 hover:bg-red-50 rounded transition-opacity shrink-0"
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
