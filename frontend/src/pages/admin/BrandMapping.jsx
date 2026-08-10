/**
 * =============================================================================
 * BrandMapping.jsx — Multi-Marketplace Marka Eşleştirme
 * =============================================================================
 *
 * AMAÇ:
 *   Ticimax "Marka Eşleştir" ekranının multi-pazaryeri versiyonu. Üstte
 *   pazaryeri sekmeleri (Trendyol, Hepsiburada, Temu, ...) + her sekme için
 *   sistem markası ↔ pazaryeri marka eşleştirme tablosu.
 *
 * AKSİYONLAR:
 *   - Otomatik Eşleştir: Sistem markasının adını pazaryeri değeri olarak alır.
 *   - Eşleşmeyenlere Değer Ata: Tüm "unmatched" satırlara tek seferde değer.
 *   - Manuel Satır Düzenleme: Pazaryeri marka ID + adı input'larıyla.
 *   - Hepsini Sıfırla: Pazaryeri için tüm eşleşmeleri siler.
 *
 * BACKEND:
 *   GET    /api/brand-mapping/{mp}
 *   POST   /api/brand-mapping/{mp}/auto-match
 *   POST   /api/brand-mapping/{mp}/{brand_id}
 *   DELETE /api/brand-mapping/{mp}/{brand_id}
 *   POST   /api/brand-mapping/{mp}/reset-all
 * =============================================================================
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { RefreshCw, CheckCircle2, Circle, Zap, Trash2, Save, Search } from "lucide-react";
import SearchableMapSelect from "../../components/admin/SearchableMapSelect";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function BrandMapping() {
  const [marketplaces, setMarketplaces] = useState([]);
  const [active, setActive] = useState("trendyol");
  const [data, setData] = useState({ items: [], total: 0, matched: 0, unmatched: 0 });
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");  // all | matched | unmatched
  const [editRow, setEditRow] = useState(null);
  const [editVal, setEditVal] = useState({ id: "", name: "" });
  const [selected, setSelected] = useState(new Set());

  const token = useMemo(() => localStorage.getItem("token"), []);
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  useEffect(() => {
    axios.get(`${API}/marketplace-hub/marketplaces`, auth)
      .then((r) => setMarketplaces(r.data?.marketplaces || []))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/brand-mapping/${active}`, auth);
      setData(r.data || { items: [], total: 0, matched: 0, unmatched: 0 });
    } catch {
      toast.error("Liste yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (active) load(); /* eslint-disable-next-line */ }, [active]);

  const autoMatch = async () => {
    if (!await window.appConfirm(`${active} için tüm markaları otomatik eşleştirmek üzeresiniz. Devam?`)) return;
    try {
      const r = await axios.post(`${API}/brand-mapping/${active}/auto-match`, {}, auth);
      toast.success(r.data?.message || "Otomatik eşleştirildi");
      load();
    } catch {
      toast.error("Otomatik eşleştirme başarısız");
    }
  };

  const resetAll = async () => {
    if (!await window.appConfirm(`${active} için TÜM eşleşmeler silinecek. Devam?`)) return;
    try {
      const r = await axios.post(`${API}/brand-mapping/${active}/reset-all`, {}, auth);
      toast.success(`${r.data?.deleted || 0} eşleşme silindi`);
      load();
    } catch {
      toast.error("Sıfırlama başarısız");
    }
  };

  const saveRow = async (row) => {
    if (!editVal.name) { toast.info("Pazaryeri markası seçin veya yazın"); return; }
    try {
      await axios.post(
        `${API}/brand-mapping/${active}/${row.brand_id}`,
        { marketplace_brand_id: editVal.id || null, marketplace_brand_name: editVal.name },
        auth
      );
      toast.success("Eşleşme kaydedildi");
      setEditRow(null);
      load();
    } catch {
      toast.error("Kaydedilemedi");
    }
  };

  const clearRow = async (row) => {
    if (!await window.appConfirm(`"${row.brand_name}" eşleşmesi silinsin mi?`)) return;
    try {
      await axios.delete(`${API}/brand-mapping/${active}/${row.brand_id}`, auth);
      toast.success("Eşleşme silindi");
      load();
    } catch {
      toast.error("Silinemedi");
    }
  };

  const bulkDelete = async () => {
    const ids = Array.from(selected);
    if (!ids.length) { toast.info("Kayıt seçin"); return; }
    if (!await window.appConfirm(`${ids.length} marka eşleşmesi silinsin mi?`)) return;
    try {
      const r = await axios.post(`${API}/brand-mapping/${active}/bulk-delete`, { brand_ids: ids }, auth);
      toast.success(`${r.data?.deleted || 0} eşleşme silindi`);
      setSelected(new Set());
      load();
    } catch { toast.error("Toplu silme başarısız"); }
  };

  const toggleOne = (id) => {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };
  const toggleAll = (items) => {
    const allIds = items.map((r) => r.brand_id);
    const allSelected = allIds.every((id) => selected.has(id)) && allIds.length;
    setSelected((s) => {
      const n = new Set(s);
      if (allSelected) allIds.forEach((id) => n.delete(id));
      else allIds.forEach((id) => n.add(id));
      return n;
    });
  };

  const filteredItems = useMemo(() => {
    let items = data.items;
    if (filter !== "all") items = items.filter((i) => i.status === filter);
    if (search) {
      const s = search.toLocaleLowerCase("tr");
      items = items.filter((i) =>
        (i.brand_name || "").toLocaleLowerCase("tr").includes(s) ||
        (i.marketplace_brand_name || "").toLocaleLowerCase("tr").includes(s)
      );
    }
    return items;
  }, [data.items, filter, search]);

  return (
    <div data-testid="brand-mapping-page">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">Marka Eşleştirme</h1>
          <p className="text-sm text-gray-500 mt-1">
            Sistem markalarınızı pazaryerlerindeki marka değerleriyle eşleştirin.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={autoMatch}
            className="flex items-center gap-1 px-3 py-2 bg-orange-600 text-white rounded-lg text-sm hover:bg-orange-700"
            data-testid="brand-auto-match">
            <Zap size={14} /> Otomatik Eşleştir
          </button>
          <button onClick={resetAll}
            className="flex items-center gap-1 px-3 py-2 border border-red-300 text-red-600 rounded-lg text-sm hover:bg-red-50"
            data-testid="brand-reset-all">
            <Trash2 size={14} /> Hepsini Sıfırla
          </button>
          <button onClick={load}
            className="flex items-center gap-1 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
            <RefreshCw size={14} /> Yenile
          </button>
        </div>
      </div>

      {/* Pazaryeri sekmeleri */}
      <div className="flex items-center gap-1 border-b border-gray-200 mb-4 overflow-x-auto">
        {marketplaces.map((m) => {
          const isActive = m.key === active;
          return (
            <button
              key={m.key}
              onClick={() => setActive(m.key)}
              className={`px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
                isActive ? "border-orange-500 text-orange-600" : "border-transparent text-gray-600 hover:text-black"
              }`}
              data-testid={`brand-mp-tab-${m.key}`}
            >
              <span className="inline-flex items-center gap-1.5">
                <span className="w-4 h-4 rounded-full flex items-center justify-center text-white text-[8px] font-black"
                      style={{ backgroundColor: m.color || "#6b7280" }}>
                  {m.name.slice(0, 1).toUpperCase()}
                </span>
                {m.name}
              </span>
            </button>
          );
        })}
      </div>

      {/* Özet kartlar */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="bg-white border rounded-xl p-4">
          <div className="text-xs text-gray-500 uppercase">Toplam Marka</div>
          <div className="text-2xl font-black mt-1">{data.total}</div>
        </div>
        <div className="bg-green-50 border border-green-200 rounded-xl p-4">
          <div className="text-xs text-green-700 uppercase">Eşleşti</div>
          <div className="text-2xl font-black text-green-800 mt-1">{data.matched}</div>
        </div>
        <div className="bg-red-50 border border-red-200 rounded-xl p-4">
          <div className="text-xs text-red-700 uppercase">Eşleşmedi</div>
          <div className="text-2xl font-black text-red-800 mt-1">{data.unmatched}</div>
        </div>
      </div>

      {/* Arama + filtre */}
      <div className="flex items-center gap-3 mb-3">
        <div className="relative flex-1 max-w-md">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Marka adı ara..."
            className="w-full border border-gray-200 rounded-lg pl-8 pr-3 py-1.5 text-sm"
            data-testid="brand-search" />
        </div>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}
          className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white">
          <option value="all">Tümü</option>
          <option value="matched">Eşleşti</option>
          <option value="unmatched">Eşleşmedi</option>
        </select>
      </div>

      {/* Toplu seçim bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 bg-orange-50 border border-orange-200 rounded-lg px-3 py-2 mb-3">
          <span className="text-sm font-semibold text-orange-900">{selected.size} seçili</span>
          <button onClick={bulkDelete}
            className="text-xs bg-red-600 hover:bg-red-700 text-white px-3 py-1.5 rounded-lg font-medium"
            data-testid="brand-bulk-delete">
            <Trash2 size={11} className="inline mr-1" /> Seçilileri Sil
          </button>
          <button onClick={() => setSelected(new Set())}
            className="text-xs text-gray-700 hover:text-black">Seçimi Temizle</button>
        </div>
      )}

      {/* Tablo */}
      <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
        <table className="admin-table admin-table-compact">
          <thead>
            <tr>
              <th className="w-8">
                <input type="checkbox"
                  checked={filteredItems.length > 0 && filteredItems.every((r) => selected.has(r.brand_id))}
                  onChange={() => toggleAll(filteredItems)}
                  data-testid="brand-toggle-all" />
              </th>
              <th>Durum</th>
              <th>Sistem Markası</th>
              <th>Pazaryeri Marka</th>
              <th>Son Güncelleme</th>
              <th className="w-32">İşlemler</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="text-center py-8 text-sm text-gray-400">Yükleniyor...</td></tr>
            ) : filteredItems.length === 0 ? (
              <tr><td colSpan={6} className="text-center py-10 text-sm text-gray-400">Kayıt yok</td></tr>
            ) : (
              filteredItems.map((row) => {
                const isEditing = editRow === row.brand_id;
                return (
                  <tr key={row.brand_id}>
                    <td>
                      <input type="checkbox"
                        checked={selected.has(row.brand_id)}
                        onChange={() => toggleOne(row.brand_id)}
                        data-testid={`brand-select-${row.brand_id}`} />
                    </td>
                    <td>
                      {row.status === "matched" ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-semibold bg-green-100 text-green-700 px-2 py-0.5 rounded-full">
                          <CheckCircle2 size={10} /> Eşleşti
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[11px] font-semibold bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                          <Circle size={10} /> Bekliyor
                        </span>
                      )}
                    </td>
                    <td className="font-semibold text-sm">{row.brand_name}</td>
                    <td>
                      {isEditing ? (
                        <SearchableMapSelect
                          optionsUrl={`/brand-mapping/${active}/options`}
                          value={editVal}
                          onChange={(v) => setEditVal(v)}
                          placeholder={`${active} markası ara veya yaz...`}
                          data-testid={`brand-search-${row.brand_id}`}
                        />
                      ) : (
                        <div className="text-sm">
                          {row.marketplace_brand_name || <span className="text-gray-400">-</span>}
                          {row.marketplace_brand_id && (
                            <span className="text-[10px] text-gray-400 font-mono ml-2">#{row.marketplace_brand_id}</span>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="text-[11px] text-gray-400">
                      {row.updated_at ? new Date(row.updated_at).toLocaleString("tr-TR") : "-"}
                    </td>
                    <td>
                      {isEditing ? (
                        <div className="flex gap-1">
                          <button onClick={() => saveRow(row)}
                            className="p-1.5 bg-black text-white rounded hover:bg-gray-800"
                            data-testid={`brand-save-${row.brand_id}`}>
                            <Save size={14} />
                          </button>
                          <button onClick={() => setEditRow(null)}
                            className="px-2 text-xs text-gray-500 hover:text-black">
                            İptal
                          </button>
                        </div>
                      ) : (
                        <div className="flex gap-1">
                          <button
                            onClick={() => {
                              setEditRow(row.brand_id);
                              setEditVal({
                                id: row.marketplace_brand_id || "",
                                name: row.marketplace_brand_name || row.brand_name,
                              });
                            }}
                            className="text-xs text-orange-600 hover:underline"
                            data-testid={`brand-edit-${row.brand_id}`}
                          >
                            Düzenle
                          </button>
                          {row.status === "matched" && (
                            <button
                              onClick={() => clearRow(row)}
                              className="text-xs text-red-600 hover:underline"
                              data-testid={`brand-clear-${row.brand_id}`}
                            >
                              Sil
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
