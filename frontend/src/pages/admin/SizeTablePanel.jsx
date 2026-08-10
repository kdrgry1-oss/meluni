import { useState, useEffect } from "react";
import axios from "axios";
import { Ruler, Plus, Trash2, Download, Save, Wand2, Copy } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Default measurement columns for apparel
const DEFAULT_COLUMNS = ["Göğüs", "Bel", "Kalça", "Omuz", "Kol Boyu", "Boy"];

export default function SizeTablePanel({ productId, productName = "", variants = [], onToast }) {
  const [sizes, setSizes] = useState([]);
  const [columns, setColumns] = useState(DEFAULT_COLUMNS);
  const [values, setValues] = useState({});
  // "suud" örneği: Ürün Bedeni + Manken ölçüleri
  const [productSize, setProductSize] = useState("");
  const [modelInfo, setModelInfo] = useState({ Boy: "", Göğüs: "", Bel: "", Basen: "" });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [pulling, setPulling] = useState(false);

  // "Diğer renkten getir": renk kardeşinin manken ölçülerini SADECE istenildiğinde getirir.
  // Bu ürüne otomatik yazılmaz; getirilen değerler alanlara doldurulur, admin Kaydet'e basınca kalıcı olur.
  // Manuel girilen değerleri korumak için: yalnız BOŞ alanlar doldurulur (dolu alan ezilmez).
  const pullFromSibling = async () => {
    setPulling(true);
    try {
      const token = localStorage.getItem("token");
      const r = await axios.get(`${API}/size-tables/${productId}/sibling-model-info`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.data?.found) {
        const inc = r.data.model_info || {};
        setModelInfo((prev) => {
          const next = { ...prev };
          ["Boy", "Göğüs", "Bel", "Basen"].forEach((k) => {
            // yalnız boş alanı doldur — manuel girilmiş değeri EZME
            if (!String(prev[k] || "").trim() && String(inc[k] || "").trim()) next[k] = inc[k];
          });
          return next;
        });
        if (r.data.product_size && !String(productSize || "").trim()) setProductSize(r.data.product_size);
        onToast?.("Manken ölçüleri renk kardeşinden getirildi (boş alanlar dolduruldu). Kaydetmeyi unutmayın.", "ok");
      } else {
        onToast?.("Dolu manken ölçüsü olan renk kardeşi bulunamadı.", "err");
      }
    } catch {
      onToast?.("Getirilemedi", "err");
    } finally {
      setPulling(false);
    }
  };

  useEffect(() => {
    if (productId) fetchTable();
  }, [productId]);

  const fetchTable = async () => {
    if (!productId) return;
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/size-tables/${productId}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      const d = res.data;
      // HER ZAMAN yanıttan uygula/sıfırla — renk kardeşleri arasında geçişte ÖNCEKİ rengin
      // manken/ürün-bedeni değerleri sızmasın (karışma bugı). Ölçü tablosu (sizes/columns/values)
      // miras alınabilir ama MANKEN yalnız ürünün KENDİ tablosundan gelir; yoksa BOŞ kalır.
      setSizes(d.sizes || []);
      setColumns(d.columns && d.columns.length ? d.columns : DEFAULT_COLUMNS);
      setValues(d.values || {});
      setProductSize(d.product_size || "");
      setModelInfo({ Boy: "", Göğüs: "", Bel: "", Basen: "", ...(d.model_info || {}) });
    } catch (err) {
      /* Tablo yoksa (404) her şeyi temizle — stale değer kalmasın */
      setSizes([]); setColumns(DEFAULT_COLUMNS); setValues({});
      setProductSize(""); setModelInfo({ Boy: "", Göğüs: "", Bel: "", Basen: "" });
    } finally {
      setLoading(false);
    }
  };

  // "Varyantları Getir" butonu - varyantların bedenlerini tabloya getirir
  const loadSizesFromVariants = () => {
    const uniqueSizes = [];
    (variants || []).forEach(v => {
      const s = (v.size || v.name || "").trim();
      if (s && !uniqueSizes.includes(s)) uniqueSizes.push(s);
    });
    if (uniqueSizes.length === 0) {
      onToast?.("Varyant bulunamadı. Önce Varyantlar sekmesinden beden ekleyin.", "err");
      return;
    }
    setSizes(uniqueSizes);
    // Initialize values for missing sizes
    const newVals = { ...values };
    uniqueSizes.forEach(s => {
      if (!newVals[s]) newVals[s] = {};
      columns.forEach(c => {
        if (newVals[s][c] === undefined) newVals[s][c] = "";
      });
    });
    setValues(newVals);
    onToast?.(`${uniqueSizes.length} beden tabloya eklendi`);
  };

  const addSize = () => {
    const s = window.prompt("Beden adı (örn: M, 42, 3XL):");
    if (!s) return;
    if (sizes.includes(s)) { onToast?.("Bu beden zaten var", "err"); return; }
    setSizes([...sizes, s]);
    setValues({ ...values, [s]: Object.fromEntries(columns.map(c => [c, ""])) });
  };

  const removeSize = (size) => {
    setSizes(sizes.filter(s => s !== size));
    const v = { ...values };
    delete v[size];
    setValues(v);
  };

  const addColumn = () => {
    const c = window.prompt("Yeni ölçü sütun adı (örn: Yaka, Paça):");
    if (!c) return;
    if (columns.includes(c)) { onToast?.("Bu sütun zaten var", "err"); return; }
    setColumns([...columns, c]);
  };

  const removeColumn = (col) => {
    setColumns(columns.filter(c => c !== col));
    const v = { ...values };
    Object.keys(v).forEach(s => { delete v[s][col]; });
    setValues(v);
  };

  const setCell = (size, col, val) => {
    setValues({ ...values, [size]: { ...(values[size] || {}), [col]: val } });
  };

  const saveTable = async () => {
    if (!productId) { onToast?.("Önce ürünü kaydedin", "err"); return; }
    setSaving(true);
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/size-tables/${productId}`,
        { sizes, columns, values, product_size: productSize, model_info: modelInfo },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      onToast?.("Ölçü tablosu kaydedildi");
    } catch (err) {
      onToast?.(err.response?.data?.detail || "Kaydedilemedi", "err");
    } finally {
      setSaving(false);
    }
  };

  const generateImage = async () => {
    if (!productId) { onToast?.("Önce ürünü kaydedin", "err"); return; }
    if (sizes.length === 0) { onToast?.("Önce beden ekleyin", "err"); return; }
    setGenerating(true);
    try {
      const token = localStorage.getItem("token");
      // Save first to ensure latest data is on server
      await axios.post(`${API}/size-tables/${productId}`,
        { sizes, columns, values, product_size: productSize, model_info: modelInfo },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      const res = await axios.post(`${API}/size-tables/${productId}/generate-image`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      // B3: görseli otomatik indir (JPEG) — dosya adı ÜRÜN ADI (kullanıcı isteği).
      if (res.data?.data_url) {
        const a = document.createElement("a");
        a.href = res.data.data_url;
        const _slug = (productName || "").trim()
          .replace(/ğ/g, "g").replace(/ü/g, "u").replace(/ş/g, "s").replace(/ı/g, "i")
          .replace(/ö/g, "o").replace(/ç/g, "c").replace(/[ĞÜŞİÖÇ]/g, (c) => ({ "Ğ": "G", "Ü": "U", "Ş": "S", "İ": "I", "Ö": "O", "Ç": "C" }[c]))
          .replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "");
        a.download = `${_slug || `beden-tablosu-${productId}`}.jpg`;
        document.body.appendChild(a); a.click(); a.remove();
      }
      onToast?.(`Görsel oluşturuldu (${Math.round(res.data.image_bytes / 1024)} KB), indirildi ve ürünün son görseli olarak eklendi.`);
    } catch (err) {
      onToast?.(err.response?.data?.detail || "Görsel oluşturulamadı", "err");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="bg-white p-8 rounded-xl border shadow-sm" data-testid="size-table-panel">
      <div className="flex justify-between items-start mb-6 flex-wrap gap-3">
        <div>
          <h3 className="font-bold text-xl mb-1 flex items-center gap-2">
            <Ruler className="text-pink-600" /> Ölçü Tablosu
          </h3>
          <p className="text-xs text-gray-500 max-w-2xl">
            Bedenleri varyanttan getirin, ölçüleri girin. "Görsel Oluştur" ile 1200×1800 PNG
            otomatik üretilir ve ürünün son görseli olarak eklenir (entegratörlere aktarılır).
            Müşteriye bu görsel gösterilmez; site tarafında beden seçeneklerinin altında
            HTML beden tablosu olarak görünür.
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button type="button" onClick={loadSizesFromVariants} data-testid="load-variants-btn"
            className="flex items-center gap-1 px-3 py-1.5 bg-pink-50 text-pink-700 rounded text-xs font-bold hover:bg-pink-100">
            <Wand2 size={13} /> Varyantları Getir ({(variants || []).length})
          </button>
          <button type="button" onClick={addSize}
            className="flex items-center gap-1 px-3 py-1.5 bg-gray-50 text-gray-700 rounded text-xs font-bold hover:bg-gray-100">
            <Plus size={13} /> Beden Ekle
          </button>
          <button type="button" onClick={addColumn}
            className="flex items-center gap-1 px-3 py-1.5 bg-gray-50 text-gray-700 rounded text-xs font-bold hover:bg-gray-100">
            <Plus size={13} /> Sütun Ekle
          </button>
        </div>
      </div>

      {!productId && (
        <div className="bg-amber-50 border border-amber-200 p-3 rounded text-xs text-amber-800 mb-4">
          Ürün henüz kaydedilmedi. Önce ürünü oluşturun, sonra bu sekmeden ölçü tablosunu ekleyin.
        </div>
      )}

      {/* Ürün Bedeni + Manken ölçüleri — her renk kendi mankenini korur (otomatik kopyalanmaz). */}
      <div className="mb-5 bg-gray-50/60 border rounded-lg p-3">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <p className="text-[11px] text-gray-500">
            <b className="text-gray-600">Ürün bedeni + Manken ölçüleri</b> ·{" "}
            <span className="text-gray-400">bu renge özeldir, boş bırakabilirsiniz — girilen değerler asla ezilmez.</span>
          </p>
          <button type="button" onClick={pullFromSibling} disabled={pulling}
            title="Aynı modelin başka bir renginin manken ölçülerini getirir (yalnız boş alanları doldurur)"
            className="shrink-0 inline-flex items-center gap-1 text-[11px] border border-gray-300 rounded px-2 py-1 hover:border-black hover:bg-white disabled:opacity-50">
            <Copy size={12} /> {pulling ? "Getiriliyor…" : "Diğer renkten getir"}
          </button>
        </div>
        <div className="grid sm:grid-cols-5 gap-3">
          <div>
            <label className="block text-[11px] font-bold text-gray-500 uppercase mb-1">Ürün Bedeni</label>
            <input value={productSize} onChange={(e) => setProductSize(e.target.value)} placeholder="34"
              className="w-full border px-2 py-1.5 rounded text-sm focus:outline-none focus:border-pink-500" />
          </div>
          {["Boy", "Göğüs", "Bel", "Basen"].map((k) => (
            <div key={k}>
              <label className="block text-[11px] font-bold text-gray-500 uppercase mb-1">Manken {k} (cm)</label>
              <input value={modelInfo[k] || ""} onChange={(e) => setModelInfo({ ...modelInfo, [k]: e.target.value })}
                placeholder="—" className="w-full border px-2 py-1.5 rounded text-sm focus:outline-none focus:border-pink-500" />
            </div>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="text-center py-8 text-gray-400">Yükleniyor...</div>
      ) : sizes.length === 0 ? (
        <div className="text-center py-12 bg-gray-50 border-2 border-dashed rounded-lg">
          <Ruler className="mx-auto mb-3 text-gray-300" size={48} />
          <p className="text-sm font-bold text-gray-500 mb-2">Henüz beden eklenmedi</p>
          <p className="text-xs text-gray-400 mb-4">Varyantlardan otomatik çekmek için yukarıdaki butonu kullanın</p>
        </div>
      ) : (
        <div className="overflow-x-auto border rounded-lg">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-3 py-2 text-xs font-bold text-gray-500 uppercase w-28">Beden</th>
                {columns.map(c => (
                  <th key={c} className="text-left px-3 py-2 text-xs font-bold text-gray-500 uppercase">
                    <div className="flex items-center justify-between gap-1">
                      <span>{c} (cm)</span>
                      <button type="button" onClick={() => removeColumn(c)} className="text-red-400 hover:text-red-600" title="Sütunu Sil">
                        <Trash2 size={11} />
                      </button>
                    </div>
                  </th>
                ))}
                <th className="w-10"></th>
              </tr>
            </thead>
            <tbody>
              {sizes.map(size => (
                <tr key={size} className="border-b">
                  <td className="px-3 py-2 font-bold text-pink-700 bg-pink-50/50">{size}</td>
                  {columns.map(c => (
                    <td key={c} className="px-2 py-1">
                      <input
                        type="text"
                        value={values[size]?.[c] || ""}
                        onChange={e => setCell(size, c, e.target.value)}
                        className="w-full border px-2 py-1 rounded text-sm focus:outline-none focus:border-pink-500"
                        placeholder="—"
                        data-testid={`size-cell-${size}-${c}`}
                      />
                    </td>
                  ))}
                  <td className="px-2 py-1">
                    <button type="button" onClick={() => removeSize(size)} className="text-red-400 hover:text-red-600" title="Bedeni Sil">
                      <Trash2 size={13} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex gap-2 pt-6 mt-4 border-t">
        <button type="button" onClick={saveTable} disabled={!productId || saving} data-testid="save-sizetable-btn"
          className="flex items-center gap-2 px-4 py-2 bg-gray-700 text-white rounded text-sm font-bold hover:bg-gray-800 disabled:opacity-50">
          <Save size={14} /> {saving ? "Kaydediliyor..." : "Tabloyu Kaydet"}
        </button>
        <button type="button" onClick={generateImage} disabled={!productId || sizes.length === 0 || generating} data-testid="generate-sizetable-img-btn"
          className="flex items-center gap-2 px-4 py-2 bg-pink-600 text-white rounded text-sm font-bold hover:bg-pink-700 disabled:opacity-50">
          <Download size={14} /> {generating ? "Oluşturuluyor..." : "Görsel Oluştur (1200×1800) + Ürüne Ekle"}
        </button>
      </div>

      <div className="mt-4 bg-blue-50 border border-blue-200 p-3 rounded text-xs text-blue-800">
        <b>Nasıl çalışır?</b> Görsel, ürünün son görseli olarak eklenir ve <b>Trendyol / Hepsiburada / Temu</b> entegrasyonlarına otomatik aktarılır.
        Facette sitesinde müşteriye bu görsel gösterilmez — bunun yerine beden seçim alanının altında stil ile
        uyumlu bir <b>HTML ölçü tablosu</b> gösterilir.
      </div>
    </div>
  );
}
