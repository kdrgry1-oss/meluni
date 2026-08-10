import { useState, useRef } from "react";
import { Upload, CheckCircle, XCircle, AlertCircle, Search, Link, Check, ChevronDown } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function AttributeImport() {
  const [importing, setImporting] = useState(false);
  const [results, setResults] = useState(null);    // parsed xlsx rows
  const [attrTypes, setAttrTypes] = useState([]);  // column headers from xlsx
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const fileRef = useRef();

  // Trendyol attribute matching
  const [matchModalOpen, setMatchModalOpen] = useState(false);
  const [matchingAttrType, setMatchingAttrType] = useState(null); // {type, rowIndex, attrIndex}
  const [trendyolAttrs, setTrendyolAttrs] = useState([]);         // available Trendyol categories for attr lookup
  const [trendyolAttrValues, setTrendyolAttrValues] = useState([]); // values for selected attr
  const [selectedTrendyolAttrId, setSelectedTrendyolAttrId] = useState(null);

  // Global attribute type → Trendyol attribute mapping (for auto-apply)
  const [attrTypeMapping, setAttrTypeMapping] = useState({});  // { "Renk": { trendyol_attr_id, trendyol_attr_name } }
  const [attrValueMapping, setAttrValueMapping] = useState({}); // { "Renk::Kırmızı": { trendyol_value_id, trendyol_value_name } }

  const handleFileChange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setImporting(true);
    setSaved(false);
    setResults(null);
    try {
      const token = localStorage.getItem("token");
      const fd = new FormData();
      fd.append("file", file);
      const res = await axios.post(`${API}/products/attributes/import-xlsx`, fd, {
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "multipart/form-data",
        },
      });
      setResults(res.data.results || []);
      setAttrTypes(res.data.attribute_types || []);
      toast.success(`${res.data.total_rows} satır işlendi, ${res.data.matched} ürün eşleşti`);
    } catch (err) {
      toast.error(err.response?.data?.detail || "XLSX okunamadı");
    } finally {
      setImporting(false);
    }
  };

  const handleSaveAll = async () => {
    if (!results) return;
    const matched = results.filter((r) => r.matched_product_id);
    if (!matched.length) {
      toast.error("Eşleşen ürün yok");
      return;
    }
    setSaving(true);
    try {
      const token = localStorage.getItem("token");
      const updates = matched.map((r) => ({
        product_id: r.matched_product_id,
        attributes: r.attributes.map((a) => ({
          type: a.type,
          value: a.value,
          trendyol_attr_id: attrTypeMapping[a.type]?.trendyol_attr_id || null,
          trendyol_attr_name: attrTypeMapping[a.type]?.trendyol_attr_name || null,
          trendyol_value_id: attrValueMapping[`${a.type}::${a.value}`]?.trendyol_value_id || null,
          trendyol_value_name: attrValueMapping[`${a.type}::${a.value}`]?.trendyol_value_name || null,
        })),
      }));
      await axios.post(`${API}/products/attributes/save-bulk`, { updates }, {
        headers: { Authorization: `Bearer ${token}` },
      });
      toast.success(`${updates.length} ürün güncellendi`);
      setSaved(true);
    } catch (err) {
      toast.error("Kaydetme başarısız");
    } finally {
      setSaving(false);
    }
  };

  // Open a modal to map a specific attribute type to Trendyol
  const openAttrMatchModal = async (attrType) => {
    setMatchingAttrType(attrType);
    setMatchModalOpen(true);
    setTrendyolAttrs([]);
    setTrendyolAttrValues([]);
    setSelectedTrendyolAttrId(null);

    // Fetch Trendyol categories to find attributes - we'll use the cached DB categories
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/integrations/trendyol/categories`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      // DENETİM FIX: uç {categories:[...]} döndürüyor; res.data.slice (obje) çalışmıyordu → liste hep boştu.
      setTrendyolAttrs((res.data?.categories || []).slice(0, 200));
    } catch (err) {
      console.error(err);
    }
  };

  const [selectedCatId, setSelectedCatId] = useState(null);
  const [catAttrList, setCatAttrList] = useState([]);
  const [loadingCatAttrs, setLoadingCatAttrs] = useState(false);

  const fetchCategoryAttributes = async (catId) => {
    setSelectedCatId(catId);
    setLoadingCatAttrs(true);
    setCatAttrList([]);
    setSelectedTrendyolAttrId(null);
    setTrendyolAttrValues([]);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/integrations/trendyol/categories/${catId}/attributes`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setCatAttrList(res.data?.attributes || res.data?.categoryAttributes || []);
    } catch (err) {
      toast.error("Kategori özellikleri alınamadı");
    } finally {
      setLoadingCatAttrs(false);
    }
  };

  const [catSearch, setCatSearch] = useState("");

  const selectTrendyolAttr = (attr) => {
    setSelectedTrendyolAttrId(attr.attribute.id);
    setTrendyolAttrValues(attr.attributeValues || []);
    // Save type mapping
    setAttrTypeMapping((prev) => ({
      ...prev,
      [matchingAttrType]: {
        trendyol_attr_id: attr.attribute.id,
        trendyol_attr_name: attr.attribute.name,
      },
    }));
  };

  const selectTrendyolValue = (localValue, trendyolVal) => {
    const key = `${matchingAttrType}::${localValue}`;
    setAttrValueMapping((prev) => ({
      ...prev,
      [key]: {
        trendyol_value_id: trendyolVal.id,
        trendyol_value_name: trendyolVal.name,
      },
    }));
  };

  // Collect all unique values for current matching attr type
  const uniqueValuesForType = matchingAttrType
    ? [...new Set((results || []).flatMap((r) =>
        r.attributes.filter((a) => a.type === matchingAttrType).map((a) => a.value)
      ))]
    : [];

  const filteredCats = trendyolAttrs.filter((c) =>
    !catSearch || (c.name || c.displayName || "").toLowerCase().includes(catSearch.toLowerCase())
  );

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">XLSX Özellik Import</h1>
        <p className="text-sm text-gray-500 mt-1">
          Stok koduna göre ürünlere özellik (renk, beden, kumaş vb.) ekleyin ve Trendyol özellikleriyle eşleştirin.
        </p>
      </div>

      {/* Upload Area */}
      <div
        className="border-2 border-dashed border-gray-300 rounded-xl p-10 text-center cursor-pointer hover:border-black hover:bg-gray-50 transition-all mb-8"
        onClick={() => fileRef.current?.click()}
      >
        <Upload className="mx-auto mb-3 text-gray-400" size={40} />
        <p className="font-medium text-gray-700">XLSX dosyası seçin veya buraya sürükleyin</p>
        <p className="text-xs text-gray-400 mt-1">
          İlk satır başlık olmalı. Stok kodu sütunu + özellik sütunları (Renk, Beden, Kumaş vb.)
        </p>
        {importing && <p className="mt-3 text-blue-600 font-medium animate-pulse">Ayrıştırılıyor...</p>}
        <input ref={fileRef} type="file" accept=".xlsx,.xls" className="hidden" onChange={handleFileChange} />
      </div>

      {results && (
        <>
          {/* Summary Bar */}
          <div className="flex items-center gap-4 mb-5 p-4 bg-gray-50 rounded-xl border">
            <div className="flex items-center gap-2 text-green-700 font-medium">
              <CheckCircle size={18} />
              {results.filter((r) => r.matched_product_id).length} eşleşti
            </div>
            <div className="flex items-center gap-2 text-red-500 font-medium">
              <XCircle size={18} />
              {results.filter((r) => !r.matched_product_id).length} eşleşmedi
            </div>
            <div className="flex-1" />
            {/* Attribute type mapping buttons */}
            <div className="flex flex-wrap gap-2">
              {attrTypes.map((type) => {
                const mapped = attrTypeMapping[type];
                return (
                  <button
                    key={type}
                    onClick={() => openAttrMatchModal(type)}
                    className={`flex items-center gap-1 px-3 py-1.5 rounded-full border text-xs font-medium transition-all ${
                      mapped
                        ? "bg-green-50 border-green-400 text-green-700"
                        : "bg-orange-50 border-orange-300 text-orange-700"
                    }`}
                  >
                    {mapped ? <Check size={12} /> : <Link size={12} />}
                    {type}
                    {mapped && <span className="text-gray-400">→ {mapped.trendyol_attr_name}</span>}
                  </button>
                );
              })}
            </div>
            <button
              onClick={handleSaveAll}
              disabled={saving || saved}
              className="px-4 py-2 bg-black text-white rounded-lg text-sm hover:bg-gray-800 disabled:opacity-50"
            >
              {saved ? "✓ Kaydedildi" : saving ? "Kaydediliyor..." : "Tümünü Kaydet"}
            </button>
          </div>

          {/* Results Table */}
          <div className="bg-white rounded-xl border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Stok Kodu</th>
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Ürün</th>
                    {attrTypes.map((t) => (
                      <th key={t} className="text-left px-4 py-3 font-medium text-gray-600">{t}</th>
                    ))}
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Durum</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {results.map((row, i) => (
                    <tr key={i} className={!row.matched_product_id ? "bg-red-50" : ""}>
                      <td className="px-4 py-3 font-mono text-xs">{row.stock_code}</td>
                      <td className="px-4 py-3 max-w-[180px] truncate">
                        {row.matched_product_name || <span className="text-gray-400 italic">Eşleşmedi</span>}
                      </td>
                      {attrTypes.map((type) => {
                        const attr = row.attributes.find((a) => a.type === type);
                        const valKey = `${type}::${attr?.value}`;
                        const mappedVal = attrValueMapping[valKey];
                        return (
                          <td key={type} className="px-4 py-3">
                            {attr ? (
                              <div>
                                <span className="font-medium">{attr.value}</span>
                                {mappedVal && (
                                  <span className="block text-xs text-green-600">→ {mappedVal.trendyol_value_name}</span>
                                )}
                              </div>
                            ) : (
                              <span className="text-gray-300">—</span>
                            )}
                          </td>
                        );
                      })}
                      <td className="px-4 py-3">
                        {row.matched_product_id ? (
                          <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 px-2 py-0.5 rounded-full">
                            <CheckCircle size={12} /> Eşleşti
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-red-600 bg-red-50 px-2 py-0.5 rounded-full">
                            <AlertCircle size={12} /> Bulunamadı
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Trendyol Attribute Matching Modal */}
      <Dialog open={matchModalOpen} onOpenChange={setMatchModalOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>"{matchingAttrType}" → Trendyol Özellik Eşleştirme</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {/* Step 1: Pick Trendyol category to find the attribute */}
            <div>
              <p className="text-sm font-medium mb-2">1. Trendyol kategorisi seçin (özellik arama için):</p>
              <input
                type="text"
                placeholder="Kategori ara..."
                value={catSearch}
                onChange={(e) => setCatSearch(e.target.value)}
                className="w-full border px-3 py-2 rounded text-sm mb-2"
              />
              <div className="max-h-40 overflow-y-auto border rounded">
                {filteredCats.slice(0, 50).map((cat) => (
                  <button
                    key={cat.id}
                    onClick={() => fetchCategoryAttributes(cat.id)}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-50 border-b last:border-0 ${
                      selectedCatId === cat.id ? "bg-blue-50 font-medium" : ""
                    }`}
                  >
                    {cat.name || cat.displayName} <span className="text-xs text-gray-400">#{cat.id}</span>
                  </button>
                ))}
                {filteredCats.length === 0 && (
                  <p className="p-3 text-sm text-gray-400">Kategori bulunamadı</p>
                )}
              </div>
            </div>

            {/* Step 2: Pick the Trendyol attribute */}
            {catAttrList.length > 0 && (
              <div>
                <p className="text-sm font-medium mb-2">2. Hangi Trendyol özelliğine eşleştirilsin?</p>
                <div className="max-h-40 overflow-y-auto border rounded">
                  {catAttrList.map((attr) => (
                    <button
                      key={attr.attribute?.id}
                      onClick={() => selectTrendyolAttr(attr)}
                      className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-50 border-b last:border-0 ${
                        selectedTrendyolAttrId === attr.attribute?.id ? "bg-blue-50 font-medium" : ""
                      }`}
                    >
                      {attr.attribute?.name}
                      {attr.required && <span className="ml-2 text-xs text-red-500">Zorunlu</span>}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {loadingCatAttrs && <p className="text-sm text-gray-500 animate-pulse">Özellikler yükleniyor...</p>}

            {/* Step 3: Map each local value to a Trendyol value */}
            {trendyolAttrValues.length > 0 && uniqueValuesForType.length > 0 && (
              <div>
                <p className="text-sm font-medium mb-2">3. Her değer için Trendyol karşılığını seçin:</p>
                <div className="space-y-2">
                  {uniqueValuesForType.map((localVal) => {
                    const key = `${matchingAttrType}::${localVal}`;
                    const mapped = attrValueMapping[key];
                    // Try auto-match: find Trendyol value with same name (case-insensitive)
                    const autoMatch = trendyolAttrValues.find(
                      (v) => v.name?.toLowerCase() === localVal?.toLowerCase()
                    );
                    return (
                      <div key={localVal} className="flex items-center gap-3 border rounded p-2">
                        <span className="font-medium text-sm min-w-[100px]">{localVal}</span>
                        <span className="text-gray-400 text-xs">→</span>
                        <select
                          className="flex-1 border rounded px-2 py-1 text-sm"
                          value={mapped?.trendyol_value_id || (autoMatch ? autoMatch.id : "")}
                          onChange={(e) => {
                            const chosen = trendyolAttrValues.find(
                              (v) => String(v.id) === String(e.target.value)
                            );
                            if (chosen) selectTrendyolValue(localVal, chosen);
                          }}
                        >
                          <option value="">— Seçin —</option>
                          {trendyolAttrValues.map((v) => (
                            <option key={v.id} value={v.id}>
                              {v.name}
                            </option>
                          ))}
                        </select>
                        {(mapped || autoMatch) && (
                          <span className="text-xs text-green-600 flex items-center gap-1">
                            <Check size={12} />
                            {autoMatch && !mapped ? "Otomatik" : "Eşleşti"}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {trendyolAttrValues.length === 0 && selectedTrendyolAttrId && (
              <p className="text-sm text-gray-500">Bu özelliğin serbest metin değerleri var (değer listesi yok).</p>
            )}

            <div className="flex justify-end pt-2 border-t">
              <button
                onClick={() => setMatchModalOpen(false)}
                className="px-4 py-2 bg-black text-white rounded text-sm"
              >
                Tamam, Uygula
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
