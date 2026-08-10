/**
 * =============================================================================
 * ProductionPlan.jsx — İmalat Planı (18 Sütunlu Tablo) — FAZ 7
 * =============================================================================
 *
 * Kullanıcının istediği tabloyu birebir karşılar.
 *  - Satır eklerken ürün seçilirse collection / price / color otomatik dolar
 *  - payment_date girince planned_delivery +21 gün otomatik hesaplanır
 *  - Inline + Final QC: pass=yeşil, fail=kırmızı + resim yükleme
 *  - delivered_qty değiştiğinde +%/-% otomatik hesaplanır
 *
 * Backend: /api/production-plan  (GET / POST / PUT / DELETE)
 * =============================================================================
 */
import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Plus, Save, Trash2, Search, Upload, RotateCw, Download, FileUp } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const COLS = [
  { k: "seq_no", label: "1. Sıra", w: 50, readOnly: true },
  { k: "manufacturer_name", label: "2. Üretici", w: 140, type: "manufacturer" },
  { k: "collection", label: "3. Koleksiyon", w: 130, type: "collection" },
  { k: "model_no", label: "4. Model No", w: 110, type: "text" },
  { k: "product_description", label: "5. Ürün Açıklaması", w: 200, type: "product" },
  { k: "order_qty", label: "6. Sipariş Adedi", w: 95, type: "number" },
  { k: "price", label: "7. Fiyat", w: 90, type: "number" },
  { k: "payment_date", label: "8. Ödeme Tarihi", w: 135, type: "date" },
  { k: "planned_delivery", label: "9. Planlanan Teslimat", w: 145, type: "date" },
  { k: "color", label: "10. Renk", w: 90, type: "text" },
  { k: "ok_date", label: "11. Okey Tarihi", w: 135, type: "date" },
  { k: "sample_ok_date", label: "12. Numune Okey", w: 135, type: "date" },
  { k: "cut_qty", label: "13. Kesim", w: 80, type: "number" },
  { k: "wash_barcode_date", label: "14. Yıkama+Barkod", w: 135, type: "date" },
  { k: "inline_qc", label: "15. İnline Kontrol", w: 175, type: "qc" },
  { k: "final_qc", label: "16. Final Kontrol", w: 175, type: "qc" },
  { k: "actual_delivery", label: "17. Gerçek Teslim", w: 150, type: "delivery" },
  { k: "delivered_qty", label: "18. Teslim Adedi", w: 120, type: "delivered" },
];

export default function ProductionPlan() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [manufacturers, setManufacturers] = useState([]);
  const [products, setProducts] = useState([]);
  const [collections, setCollections] = useState([]);
  const [dirty, setDirty] = useState({}); // {rowId: true}
  const [saving, setSaving] = useState({});
  const dirtyTimers = useRef({});
  // DENETİM FIX (#30/#31): satır bazında biriken kirli alanlar. Debounce timer'ı bu ref'teki
  // TÜM alanları birlikte gönderir; eskiden yalnız SON alan kaydediliyordu → hızlı çoklu-alan
  // düzenlemesinde (ve üretici seçiminde manufacturer_id) veri kaybı oluyordu.
  const pendingPatch = useRef({}); // {rowId: {field: value, ...}}

  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const load = async () => {
    setLoading(true);
    try {
      const [r, m, p, c] = await Promise.all([
        axios.get(`${API}/production-plan?search=${encodeURIComponent(search)}`, auth),
        axios.get(`${API}/vendors?vendor_type=manufacturer`, auth),
        axios.get(`${API}/products?limit=500&admin_view=1`, auth),
        axios.get(`${API}/production-plan/collections`, auth),
      ]);
      setRows(r.data?.items || []);
      setManufacturers(m.data?.vendors || m.data?.items || m.data || []);
      setProducts(p.data?.products || p.data?.items || []);
      setCollections(c.data?.collections || []);
    } catch (e) {
      toast.error("Yüklenemedi: " + (e?.response?.data?.detail || e.message));
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const addRow = async () => {
    try {
      const r = await axios.post(`${API}/production-plan`, {}, auth);
      setRows((rs) => [...rs, r.data.row]);
      toast.success("Yeni satır eklendi");
    } catch (e) { toast.error("Eklenemedi"); }
  };

  const saveRow = async (row, patch) => {
    const id = row.id;
    setSaving((s) => ({ ...s, [id]: true }));
    try {
      const r = await axios.put(`${API}/production-plan/${id}`, patch, auth);
      setRows((rs) => rs.map((x) => x.id === id ? {
        ...x, ...patch,
        delay_days: r.data.delay_days,
        qty_diff_pct: r.data.qty_diff_pct,
        planned_delivery: r.data.planned_delivery ?? x.planned_delivery,
      } : x));
      setDirty((d) => { const { [id]: _, ...rest } = d; return rest; });
    } catch (e) {
      toast.error("Satır kaydedilemedi");
    } finally { setSaving((s) => ({ ...s, [id]: false })); }
  };

  // Birden çok alanı tek seferde biriktirmek için (ör. üretici seçimi id+name)
  const localPatchMany = (row, patchObj) => {
    setRows((rs) => rs.map((x) => x.id === row.id ? { ...x, ...patchObj } : x));
    setDirty((d) => ({ ...d, [row.id]: true }));
    // Biriken patch'e ekle (önceki kirli alanlar korunur)
    pendingPatch.current[row.id] = { ...(pendingPatch.current[row.id] || {}), ...patchObj };
    // Debounced auto-save — timer ateşlenince biriken TÜM alanları birlikte gönder
    if (dirtyTimers.current[row.id]) clearTimeout(dirtyTimers.current[row.id]);
    dirtyTimers.current[row.id] = setTimeout(() => {
      const accumulated = pendingPatch.current[row.id];
      pendingPatch.current[row.id] = {};
      if (accumulated && Object.keys(accumulated).length) {
        setRows((cur) => {
          const latest = cur.find((r) => r.id === row.id);
          if (latest) saveRow(latest, accumulated);
          return cur;
        });
      }
    }, 800);
  };

  const localPatch = (row, field, value) => localPatchMany(row, { [field]: value });

  const removeRow = async (row) => {
    if (!await window.appConfirm("Bu satırı silmek istediğinize emin misiniz?")) return;
    try {
      await axios.delete(`${API}/production-plan/${row.id}`, auth);
      setRows((rs) => rs.filter((x) => x.id !== row.id));
      toast.success("Satır silindi");
    } catch (e) { toast.error("Silinemedi"); }
  };

  const handleProductSelect = async (row, productId) => {
    const p = products.find((x) => x.id === productId);
    if (!p) return;
    const patch = {
      product_id: productId,
      product_description: p.name || "",
      price: Number(p.purchase_price || 0),
      collection: p.collection || row.collection || "",
      color: p.color || row.color || "",
    };
    localPatch(row, "product_id", productId);
    setRows((rs) => rs.map((x) => x.id === row.id ? { ...x, ...patch } : x));
    setDirty((d) => ({ ...d, [row.id]: true }));
    await saveRow(row, patch);
  };

  const handleManufacturerSelect = (row, id) => {
    const v = manufacturers.find((x) => x.id === id);
    // DENETİM FIX (#31): id ve name TEK patch'te gönderilir — eskiden iki ayrı debounce
    // çağrısı çakışıp manufacturer_id kayboluyordu.
    localPatchMany(row, { manufacturer_id: id, manufacturer_name: v?.name || "" });
  };

  // Resim upload — QC için basit base64 url
  const handleQcImage = async (row, field, file) => {
    if (!file) return;
    // base64 önizleme (küçük önizleme için) + gerçek upload /api/uploads olmalı
    const reader = new FileReader();
    reader.onload = async () => {
      const url = reader.result;
      const current = row[field] || {};
      const updated = { ...current, image_url: url };
      localPatch(row, field, updated);
      await saveRow(row, { [field]: updated });
      toast.success("Resim eklendi");
    };
    reader.readAsDataURL(file);
  };

  const setQc = (row, field, patch) => {
    const current = row[field] || {};
    const updated = { ...current, ...patch };
    localPatch(row, field, updated);
  };

  const filtered = useMemo(() => {
    if (!search) return rows;
    const s = search.toLowerCase();
    return rows.filter((r) =>
      (r.model_no || "").toLowerCase().includes(s) ||
      (r.product_description || "").toLowerCase().includes(s) ||
      (r.manufacturer_name || "").toLowerCase().includes(s)
    );
  }, [rows, search]);

  const dateCell = (row, field) => (
    <input type="date" value={(row[field] || "").slice(0, 10)}
      onChange={(e) => localPatch(row, field, e.target.value)}
      className="w-full border px-1 py-1 text-xs rounded"
      data-testid={`pp-${field}-${row.id}`} />
  );

  const qcCell = (row, field) => {
    const v = row[field] || {};
    const pass = v.result === "pass";
    const fail = v.result === "fail";
    return (
      <div className="space-y-1">
        <input type="date" value={(v.date || "").slice(0, 10)}
          onChange={(e) => setQc(row, field, { date: e.target.value })}
          className={`w-full border px-1 py-1 text-xs rounded ${pass ? "bg-green-50 text-green-700 border-green-300" : fail ? "bg-red-50 text-red-700 border-red-300" : ""}`}
          data-testid={`pp-${field}-date-${row.id}`} />
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setQc(row, field, { result: "pass" })}
            className={`text-[10px] px-1.5 py-0.5 rounded ${pass ? "bg-green-600 text-white" : "bg-gray-100 hover:bg-green-100"}`}
            data-testid={`pp-${field}-pass-${row.id}`}>Geçti</button>
          <button type="button" onClick={() => setQc(row, field, { result: "fail" })}
            className={`text-[10px] px-1.5 py-0.5 rounded ${fail ? "bg-red-600 text-white" : "bg-gray-100 hover:bg-red-100"}`}
            data-testid={`pp-${field}-fail-${row.id}`}>Kaldı</button>
          <label className="cursor-pointer text-[10px] px-1.5 py-0.5 rounded bg-blue-50 hover:bg-blue-100 inline-flex items-center gap-0.5">
            <Upload size={10} />
            <input type="file" accept="image/*" className="hidden"
              onChange={(e) => handleQcImage(row, field, e.target.files?.[0])} />
          </label>
        </div>
        {v.image_url && (
          <a href={v.image_url} target="_blank" rel="noreferrer">
            <img src={v.image_url} alt="" className="w-full h-8 object-cover rounded" />
          </a>
        )}
      </div>
    );
  };

  const deliveryCell = (row) => {
    const delay = row.delay_days;
    const color = delay == null ? "" : delay > 0 ? "bg-red-50 text-red-700 border-red-300" : "bg-green-50 text-green-700 border-green-300";
    return (
      <div>
        <input type="date" value={(row.actual_delivery || "").slice(0, 10)}
          onChange={(e) => localPatch(row, "actual_delivery", e.target.value)}
          className={`w-full border px-1 py-1 text-xs rounded ${color}`}
          data-testid={`pp-actual_delivery-${row.id}`} />
        {delay != null && (
          <div className={`text-[10px] mt-0.5 ${delay > 0 ? "text-red-600" : "text-green-600"}`}>
            {delay > 0 ? `+${delay} gün gecikme` : delay === 0 ? "Zamanında" : `${Math.abs(delay)} gün erken`}
          </div>
        )}
      </div>
    );
  };

  const deliveredCell = (row) => {
    const pct = row.qty_diff_pct;
    return (
      <div>
        <input type="number" min="0" value={row.delivered_qty ?? ""}
          onChange={(e) => localPatch(row, "delivered_qty", Number(e.target.value))}
          className="w-full border px-1 py-1 text-xs rounded"
          data-testid={`pp-delivered_qty-${row.id}`} />
        {pct != null && (
          <div className={`text-[10px] mt-0.5 ${pct >= 0 ? "text-green-600" : "text-red-600"}`}>
            {pct >= 0 ? `+%${pct}` : `%${pct}`}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="p-5 space-y-4" data-testid="production-plan-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">İmalat Planı</h1>
          <p className="text-xs text-gray-500 mt-1">18 sütunlu üretim takip tablosu. Ürün seçilirse alanlar otomatik doldurulur.</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search size={14} className="absolute left-2 top-2.5 text-gray-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Ara..." className="pl-7 pr-3 py-1.5 border rounded text-sm" data-testid="pp-search" />
          </div>
          <button onClick={load} className="inline-flex items-center gap-1 bg-gray-100 hover:bg-gray-200 px-3 py-1.5 rounded text-sm" data-testid="pp-refresh">
            <RotateCw size={14} /> Yenile
          </button>
          <button onClick={async () => {
            try {
              const res = await fetch(`${API}/production-plan/export`, { headers: { Authorization: `Bearer ${token}` } });
              if (!res.ok) { toast.error("Export hatası"); return; }
              const blob = await res.blob();
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url; a.download = `imalat-plani-${Date.now()}.xlsx`;
              document.body.appendChild(a); a.click();
              document.body.removeChild(a); URL.revokeObjectURL(url);
              toast.success("Excel indirildi");
            } catch { toast.error("Export hatası"); }
          }} className="inline-flex items-center gap-1 bg-green-600 text-white hover:bg-green-700 px-3 py-1.5 rounded text-sm" data-testid="pp-export">
            <Download size={14} /> Excel
          </button>
          <label className="inline-flex items-center gap-1 bg-blue-600 text-white hover:bg-blue-700 px-3 py-1.5 rounded text-sm cursor-pointer" data-testid="pp-import-label">
            <FileUp size={14} /> Import
            <input type="file" accept=".xlsx,.xlsm" className="hidden"
              data-testid="pp-import-input"
              onChange={async (e) => {
                const f = e.target.files?.[0]; if (!f) return;
                const fd = new FormData(); fd.append("file", f);
                try {
                  const r = await axios.post(`${API}/production-plan/import`, fd, {
                    headers: { Authorization: `Bearer ${token}`, "Content-Type": "multipart/form-data" },
                  });
                  toast.success(`${r.data.created} yeni + ${r.data.updated} güncelleme`);
                  if (r.data.errors?.length) toast.warning(`${r.data.errors.length} hata — konsolu kontrol edin`);
                  console.log("Import errors:", r.data.errors);
                  await load();
                } catch (err) { toast.error("Import hatası: " + (err?.response?.data?.detail || err.message)); }
                e.target.value = "";
              }} />
          </label>
          <button onClick={addRow} className="inline-flex items-center gap-1 bg-black text-white px-3 py-1.5 rounded text-sm" data-testid="pp-add">
            <Plus size={14} /> Satır Ekle
          </button>
        </div>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
        <table className="text-xs" style={{ minWidth: "2200px" }}>
          <thead className="bg-gray-50 sticky top-0">
            <tr>
              {COLS.map((c) => (
                <th key={c.k} style={{ width: c.w, minWidth: c.w }}
                  className="text-left p-2 border-b font-semibold text-gray-700">
                  {c.label}
                </th>
              ))}
              <th className="p-2 border-b" style={{ width: 45 }}></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {loading ? (
              <tr><td colSpan={COLS.length + 1} className="p-6 text-center text-gray-400">Yükleniyor...</td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={COLS.length + 1} className="p-6 text-center text-gray-400">Kayıt yok. "Satır Ekle" ile başlayın.</td></tr>
            ) : filtered.map((row) => (
              <tr key={row.id} className="hover:bg-gray-50" data-testid={`pp-row-${row.id}`}>
                <td className="p-1 text-center">{row.seq_no}</td>
                <td className="p-1">
                  <select value={row.manufacturer_id || ""} onChange={(e) => handleManufacturerSelect(row, e.target.value)}
                    className="w-full border px-1 py-1 text-xs rounded bg-white" data-testid={`pp-mfg-${row.id}`}>
                    <option value="">-</option>
                    {manufacturers.map((m) => (<option key={m.id} value={m.id}>{m.name}</option>))}
                  </select>
                </td>
                <td className="p-1">
                  <input list={`col-opts-${row.id}`} value={row.collection || ""}
                    onChange={(e) => localPatch(row, "collection", e.target.value)}
                    className="w-full border px-1 py-1 text-xs rounded" data-testid={`pp-col-${row.id}`} />
                  <datalist id={`col-opts-${row.id}`}>
                    {collections.map((c) => (<option key={c} value={c} />))}
                  </datalist>
                </td>
                <td className="p-1">
                  <input value={row.model_no || ""} onChange={(e) => localPatch(row, "model_no", e.target.value)}
                    className="w-full border px-1 py-1 text-xs rounded" data-testid={`pp-model-${row.id}`} />
                </td>
                <td className="p-1">
                  <select value={row.product_id || ""} onChange={(e) => handleProductSelect(row, e.target.value)}
                    className="w-full border px-1 py-1 text-xs rounded bg-white mb-1" data-testid={`pp-prod-${row.id}`}>
                    <option value="">— Ürün seç —</option>
                    {products.map((p) => (<option key={p.id} value={p.id}>{p.name}</option>))}
                  </select>
                  <input value={row.product_description || ""} onChange={(e) => localPatch(row, "product_description", e.target.value)}
                    className="w-full border px-1 py-1 text-xs rounded" />
                </td>
                <td className="p-1">
                  <input type="number" min="0" value={row.order_qty ?? ""} onChange={(e) => localPatch(row, "order_qty", Number(e.target.value))}
                    className="w-full border px-1 py-1 text-xs rounded" data-testid={`pp-oq-${row.id}`} />
                </td>
                <td className="p-1">
                  <input type="number" step="0.01" min="0" value={row.price ?? ""} onChange={(e) => localPatch(row, "price", Number(e.target.value))}
                    className="w-full border px-1 py-1 text-xs rounded" data-testid={`pp-price-${row.id}`} />
                </td>
                <td className="p-1">{dateCell(row, "payment_date")}</td>
                <td className="p-1 bg-blue-50/30">{dateCell(row, "planned_delivery")}</td>
                <td className="p-1">
                  <input value={row.color || ""} onChange={(e) => localPatch(row, "color", e.target.value)}
                    className="w-full border px-1 py-1 text-xs rounded" data-testid={`pp-color-${row.id}`} />
                </td>
                <td className="p-1">{dateCell(row, "ok_date")}</td>
                <td className="p-1">{dateCell(row, "sample_ok_date")}</td>
                <td className="p-1">
                  <input type="number" min="0" value={row.cut_qty ?? ""} onChange={(e) => localPatch(row, "cut_qty", Number(e.target.value))}
                    className="w-full border px-1 py-1 text-xs rounded" data-testid={`pp-cut-${row.id}`} />
                </td>
                <td className="p-1">{dateCell(row, "wash_barcode_date")}</td>
                <td className="p-1">{qcCell(row, "inline_qc")}</td>
                <td className="p-1">{qcCell(row, "final_qc")}</td>
                <td className="p-1">{deliveryCell(row)}</td>
                <td className="p-1">{deliveredCell(row)}</td>
                <td className="p-1 text-center">
                  <div className="flex items-center gap-1">
                    {saving[row.id] ? <Save size={14} className="text-blue-500 animate-pulse" /> :
                      dirty[row.id] ? <Save size={14} className="text-orange-500" /> : null}
                    <button onClick={() => removeRow(row)}
                      className="text-red-600 hover:bg-red-50 p-1 rounded" data-testid={`pp-del-${row.id}`}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
