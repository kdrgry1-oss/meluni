/**
 * BarcodeIssues.jsx
 *
 * Barkodu eksik veya belirsiz (önceki sync sırasında yanlışlıkla
 * stok kodundan kopyalanmış) ürünleri listeler ve manuel düzeltmeyi sağlar.
 *
 * Endpoint: GET /api/integrations/products/barcode-issues
 *           POST /api/integrations/products/barcode-fix
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { AlertTriangle, CheckCircle2, RefreshCw, Save, Search } from "lucide-react";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

export default function BarcodeIssues() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState({}); // { product_id: { main: "", variants: { var_id: "" } } }
  const [saving, setSaving] = useState({}); // { product_id: bool }

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/integrations/products/barcode-issues?limit=2000`, auth());
      setItems(r.data?.items || []);
      setDraft({});
    } catch (e) {
      toast.error("Liste yüklenemedi: " + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onMainChange = (pid, val) => {
    setDraft((d) => ({ ...d, [pid]: { ...(d[pid] || {}), main: val } }));
  };
  const onVarChange = (pid, vid, val) => {
    setDraft((d) => ({
      ...d,
      [pid]: { ...(d[pid] || {}), variants: { ...(d[pid]?.variants || {}), [vid]: val } },
    }));
  };

  const saveOne = async (pid) => {
    const drf = draft[pid] || {};
    const payload = { product_id: pid };
    if (drf.main && drf.main.trim()) payload.main_barcode = drf.main.trim();
    const vars = Object.entries(drf.variants || {})
      .filter(([, v]) => v && v.trim())
      .map(([variant_id, barcode]) => ({ variant_id, barcode: barcode.trim() }));
    if (vars.length) payload.variants = vars;
    if (!payload.main_barcode && !vars.length) {
      toast.error("Hiç değer girmediniz");
      return;
    }
    setSaving((s) => ({ ...s, [pid]: true }));
    try {
      await axios.post(`${API}/integrations/products/barcode-fix`, payload, auth());
      toast.success("Barkod güncellendi");
      // Listeyi yenile ama scroll'u koru — sadece bu ürünü çıkar
      setItems((it) => it.filter((x) => x.id !== pid));
      setDraft((d) => { const cp = { ...d }; delete cp[pid]; return cp; });
    } catch (e) {
      toast.error("Kaydetme başarısız: " + (e.response?.data?.detail || e.message));
    } finally {
      setSaving((s) => ({ ...s, [pid]: false }));
    }
  };

  const filtered = items.filter((it) => {
    if (!search.trim()) return true;
    const q = search.toLocaleLowerCase("tr");
    return (
      (it.name || "").toLocaleLowerCase("tr").includes(q) ||
      (it.stock_code || "").toLocaleLowerCase("tr").includes(q) ||
      (it.category_name || "").toLocaleLowerCase("tr").includes(q)
    );
  });

  return (
    <div className="p-6 max-w-7xl mx-auto" data-testid="barcode-issues-page">
      <div className="flex items-center justify-between mb-6 gap-4">
        <div>
          <h1 className="text-2xl font-black flex items-center gap-2">
            <AlertTriangle className="text-amber-500" />
            Barkod Eksik / Belirsiz Ürünler
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Bu ürünler önceki XML/CSV sync'inde yanlışlıkla stok kodu barkod olarak kopyalanmıştı.
            Ticimax admin panelinden doğru barkodu kopyalayıp aşağıdaki kutulara yapıştırın.
            Trendyol push'unda bu ürünler şu an OTOMATİK OLARAK ATLANIYOR.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800 disabled:opacity-50"
          data-testid="barcode-issues-refresh"
        >
          <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          Yenile
        </button>
      </div>

      <div className="bg-white border rounded-lg overflow-hidden">
        <div className="p-3 border-b flex items-center gap-2 bg-gray-50">
          <Search size={16} className="text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Ürün adı, stok kodu veya kategori ara..."
            className="flex-1 bg-transparent text-sm focus:outline-none"
            data-testid="barcode-issues-search"
          />
          <span className="text-xs text-gray-500">
            {filtered.length} / {items.length} ürün
          </span>
        </div>

        {loading && items.length === 0 ? (
          <div className="p-8 text-center text-gray-400">Yükleniyor...</div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center text-gray-400 flex flex-col items-center gap-2">
            <CheckCircle2 className="text-green-500" size={32} />
            <span>{items.length === 0 ? "Tüm ürünlerin barkodu temiz! 🎉" : "Aramaya uygun ürün yok"}</span>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-xs text-gray-500 sticky top-0">
                <tr>
                  <th className="text-left px-4 py-2">Ürün</th>
                  <th className="text-left px-4 py-2 w-44">Stok Kodu</th>
                  <th className="text-left px-4 py-2">Ana Barkod</th>
                  <th className="text-left px-4 py-2">Varyantlar</th>
                  <th className="text-right px-4 py-2 w-24">İşlem</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((it) => (
                  <tr key={it.id} className="border-t hover:bg-amber-50/30" data-testid={`barcode-row-${it.id}`}>
                    <td className="px-4 py-3 align-top">
                      <div className="font-medium text-gray-900">{it.name}</div>
                      <div className="text-[10px] text-gray-400 mt-0.5">
                        {it.category_name && <span>{it.category_name} · </span>}
                        {it.source && <span className="font-mono">{it.source}</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top font-mono text-xs text-gray-700">{it.stock_code || "-"}</td>
                    <td className="px-4 py-3 align-top">
                      {it.main_barcode_uncertain ? (
                        <input
                          type="text"
                          value={draft[it.id]?.main || ""}
                          onChange={(e) => onMainChange(it.id, e.target.value)}
                          placeholder="Doğru barkod..."
                          className="border border-amber-300 bg-amber-50 px-2 py-1 rounded text-sm font-mono w-40 focus:outline-none focus:border-amber-500"
                          data-testid={`main-bc-input-${it.id}`}
                        />
                      ) : (
                        <span className="font-mono text-xs text-green-700">{it.main_barcode || "-"}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top">
                      {(it.bad_variants || []).length === 0 ? (
                        <span className="text-xs text-gray-400">—</span>
                      ) : (
                        <div className="space-y-1">
                          {(it.bad_variants || []).map((v) => (
                            <div key={v.id} className="flex items-center gap-2">
                              <span className="text-[10px] text-gray-500 w-32 truncate">
                                {v.color && <span className="font-medium">{v.color}</span>}
                                {v.color && v.size && " · "}
                                {v.size}
                                {!v.color && !v.size && (v.stock_code || v.id)}
                              </span>
                              <input
                                type="text"
                                value={draft[it.id]?.variants?.[v.id] || ""}
                                onChange={(e) => onVarChange(it.id, v.id, e.target.value)}
                                placeholder="Barkod..."
                                className="border border-amber-300 bg-amber-50 px-2 py-1 rounded text-xs font-mono w-36 focus:outline-none focus:border-amber-500"
                                data-testid={`var-bc-input-${it.id}-${v.id}`}
                              />
                            </div>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top text-right">
                      <button
                        onClick={() => saveOne(it.id)}
                        disabled={saving[it.id]}
                        className="inline-flex items-center gap-1 bg-emerald-600 text-white px-3 py-1.5 rounded text-xs hover:bg-emerald-700 disabled:opacity-50"
                        data-testid={`save-bc-${it.id}`}
                      >
                        <Save size={12} />
                        {saving[it.id] ? "..." : "Kaydet"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
