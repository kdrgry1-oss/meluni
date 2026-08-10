/**
 * CategoryMapping.jsx — Multi-Marketplace Kategori Eşleştirme
 *
 * Sistem kategorilerini her pazaryerindeki kategori ID'leriyle eşleştirir.
 * BrandMapping.jsx ile aynı üst yapı (13 pazaryeri sekmesi + tablo).
 *
 * Bağlantılar:
 *   - /api/category-mapping/{marketplace}
 *   - /api/categories (sistem kategorileri)
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { RefreshCw, CheckCircle2, Circle, Save, Search, Trash2, Settings, Sliders, Zap, Download, Plus } from "lucide-react";
import SearchableMapSelect from "../../components/admin/SearchableMapSelect";
import StockPriceUpdatePanel from "../../components/admin/StockPriceUpdatePanel";
import {
  AdvancedAttributeMatchModal,
  AdvancedValueMatchModal,
} from "../../components/admin/MarketplaceAdvancedMatch";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function CategoryMapping() {
  const [marketplaces, setMarketplaces] = useState([]);
  const [active, setActive] = useState("trendyol");
  const [data, setData] = useState({ items: [], total: 0, matched: 0, unmatched: 0 });
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [editRow, setEditRow] = useState(null);
  const [editVal, setEditVal] = useState({ id: "", name: "" });
  const [selected, setSelected] = useState(new Set());
  const [attrMatchFor, setAttrMatchFor] = useState(null);
  const [valueMatchFor, setValueMatchFor] = useState(null);
  const [bulkAttrLoading, setBulkAttrLoading] = useState(false);
  const [bulkAttrReport, setBulkAttrReport] = useState(null);
  const [showExcluded, setShowExcluded] = useState(false);
  const [temuSyncing, setTemuSyncing] = useState(false);

  const token = useMemo(() => localStorage.getItem("token"), []);
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  useEffect(() => {
    axios.get(`${API}/marketplace-hub/marketplaces`, auth)
      .then((r) => setMarketplaces(r.data?.marketplaces || []))
      .catch(() => {});
    // eslint-disable-next-line
  }, []);

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(
        `${API}/category-mapping/${active}?show_excluded=${showExcluded}`,
        auth,
      );
      setData(r.data);
    } catch { toast.error("Liste yüklenemedi"); }
    finally { setLoading(false); }
  };
  useEffect(() => { if (active) load(); /* eslint-disable-next-line */ }, [active, showExcluded]);

  const saveRow = async (row) => {
    if (!editVal.name) { toast.info("Pazaryeri kategorisi seçin"); return; }
    try {
      await axios.post(`${API}/category-mapping/${active}/${row.category_id}`,
        { marketplace_category_id: editVal.id || null, marketplace_category_name: editVal.name }, auth);
      toast.success("Kaydedildi");
      setEditRow(null); load();
    } catch { toast.error("Kaydedilemedi"); }
  };

  const clearRow = async (row) => {
    if (!await window.appConfirm(
      `"${row.category_name}" bu pazaryeri eşleştirme listesinden GİZLENECEK.\n\n` +
      `Eşleştirme silinir ve kategori artık ${active} için listede görünmez.\n` +
      `(Tekrar göstermek için sayfanın üstündeki "Gizlenenleri Göster" butonunu kullanın.)\n\n` +
      `Onaylıyor musunuz?`
    )) return;
    await axios.delete(`${API}/category-mapping/${active}/${row.category_id}`, auth);
    toast.success("Kategori bu pazaryeri için gizlendi"); load();
  };

  const restoreCategory = async (row) => {
    await axios.post(`${API}/category-mapping/${active}/${row.category_id}/include`, {}, auth);
    toast.success("Kategori tekrar gösterildi"); load();
  };

  const syncTemuCategories = async () => {
    setTemuSyncing(true);
    try {
      const r = await axios.post(`${API}/category-mapping/temu/sync-categories`, {}, auth);
      toast.success(r.data?.message || "Temu kategorileri çekildi");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Temu kategorileri çekilemedi");
    } finally {
      setTemuSyncing(false);
    }
  };

  const bulkDelete = async () => {
    const ids = Array.from(selected);
    if (!ids.length) { toast.info("Kayıt seçin"); return; }
    if (!await window.appConfirm(`${ids.length} kategori eşleşmesi silinsin mi?`)) return;
    try {
      const r = await axios.post(`${API}/category-mapping/${active}/bulk-delete`, { category_ids: ids }, auth);
      toast.success(`${r.data?.deleted || 0} eşleşme silindi`);
      setSelected(new Set());
      load();
    } catch { toast.error("Toplu silme başarısız"); }
  };
  const toggleOne = (id) => {
    setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  };
  const toggleAll = (items) => {
    const allIds = items.map((r) => r.category_id);
    const allSelected = allIds.every((id) => selected.has(id)) && allIds.length;
    setSelected((s) => {
      const n = new Set(s);
      if (allSelected) allIds.forEach((id) => n.delete(id));
      else allIds.forEach((id) => n.add(id));
      return n;
    });
  };

  const resetAll = async () => {
    if (!await window.appConfirm(`${active} için TÜM kategori eşleşmeleri silinecek.`)) return;
    const r = await axios.post(`${API}/category-mapping/${active}/reset-all`, {}, auth);
    toast.success(`${r.data.deleted} kayıt silindi`); load();
  };

  const bulkAutoMatchAttributes = async () => {
    if (!await window.appConfirm(
      `${active} için matched tüm kategorilerin özellik eşleştirmesi otomatik yapılacak.\n\n` +
      `• Mevcut manuel eşleştirmeler korunur (ezilmez), sadece BOŞ olanlar doldurulur.\n` +
      `• Trendyol için canlı API'den attribute'lar çekilir, diğer pazaryerleri için yerel cache kullanılır.\n\n` +
      `Devam edilsin mi?`
    )) return;
    setBulkAttrLoading(true);
    setBulkAttrReport(null);
    try {
      const r = await axios.post(`${API}/category-mapping/${active}/bulk-auto-match-attributes`, {}, auth);
      setBulkAttrReport(r.data);
      toast.success(r.data?.message || "Otomatik eşleştirme tamamlandı");
      load();
    } catch (e) {
      toast.error("Otomatik eşleştirme hatası: " + (e.response?.data?.detail || e.message));
    } finally {
      setBulkAttrLoading(false);
    }
  };

  const bulkAutoSetup = async () => {
    if (!await window.appConfirm(
      `${active} için matched TÜM kategorilerde özellik + DEĞER + şirket/ortak default otomatik kurulacak.\n\n` +
      `• Mevcut manuel eşleştirmeler KORUNUR (ezilmez), sadece boş olanlar doldurulur.\n` +
      `• Renk/Beden gibi DEĞERLER de eşleştirilir.\n\n` +
      `Devam edilsin mi?`
    )) return;
    setBulkAttrLoading(true);
    try {
      const r = await axios.post(`${API}/category-mapping/${active}/bulk-auto-setup`, {}, { ...auth, timeout: 600000 });
      const d = r.data || {};
      toast.success(d.message || "Tam eşleştirme tamamlandı", { duration: 8000 });
      load();
    } catch (e) {
      toast.error("Tam eşleştirme hatası: " + (e.response?.data?.detail || e.message));
    } finally {
      setBulkAttrLoading(false);
    }
  };

  const bulkFillCompanyDefaults = async () => {
    if (!await window.appConfirm(
      `${active} için TÜM matched kategorilerin "Üretici / İthalatçı Adı / Adres / Mail" alanları\n` +
      `Ayarlar > Şirket Bilgisi'nden otomatik doldurulacak.\n\n` +
      `• Mevcut manuel default değerler KORUNUR (ezilmez), sadece boş olanlar doldurulur.\n` +
      `• Önce Ayarlar'da şirket bilgilerini eksiksiz doldurun (özellikle email).\n\n` +
      `Devam edilsin mi?`
    )) return;
    try {
      const r = await axios.post(`${API}/category-mapping/${active}/bulk-fill-company-defaults`, {}, auth);
      toast.success(r.data?.message || "Şirket alanları dolduruldu");
      load();
    } catch (e) {
      toast.error("Toplu doldurma hatası: " + (e.response?.data?.detail || e.message));
    }
  };

  const filtered = useMemo(() => {
    if (!search) return data.items;
    const s = search.toLocaleLowerCase("tr");
    return data.items.filter((i) =>
      (i.category_name || "").toLocaleLowerCase("tr").includes(s) ||
      (i.marketplace_category_name || "").toLocaleLowerCase("tr").includes(s)
    );
  }, [data.items, search]);

  return (
    <div data-testid="category-mapping-page">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">Kategori Eşleştirme</h1>
          <p className="text-sm text-gray-500 mt-1">
            Sistem kategorilerinizi pazaryerlerinin kategori ağacıyla eşleştirin. Her pazaryeri için ayrı sekme.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={bulkAutoSetup}
            disabled={bulkAttrLoading || data.matched === 0}
            className="flex items-center gap-1 px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title="Matched tüm kategorilerde özellik + değer + şirket/ortak default'u birlikte otomatik kur"
            data-testid="cat-bulk-auto-setup">
            {bulkAttrLoading ? <RefreshCw size={14} className="animate-spin" /> : <Zap size={14} />}
            {bulkAttrLoading ? "Eşleşiyor..." : "Tümünü Eşleştir (Özellik+Değer)"}
          </button>
          <button onClick={bulkAutoMatchAttributes}
            disabled={bulkAttrLoading || data.matched === 0}
            className="flex items-center gap-1 px-3 py-2 bg-stone-900 text-white rounded-lg text-sm font-semibold hover:bg-stone-800 disabled:opacity-50 disabled:cursor-not-allowed"
            title="Matched tüm kategorilerdeki özellikleri sistem özellikleriyle otomatik eşleştir"
            data-testid="cat-bulk-auto-match">
            {bulkAttrLoading ? <RefreshCw size={14} className="animate-spin" /> : <Zap size={14} />}
            {bulkAttrLoading ? "Eşleşiyor..." : "Tümünü Otomatik Eşleştir"}
          </button>
          <button onClick={bulkFillCompanyDefaults}
            disabled={data.matched === 0}
            className="flex items-center gap-1 px-3 py-2 border border-stone-300 text-stone-700 bg-white rounded-lg text-sm font-semibold hover:bg-stone-50 disabled:opacity-50"
            title="Tüm matched kategorilerin Üretici/İthalatçı alanlarını şirket bilgilerinden doldur"
            data-testid="cat-bulk-fill-company">
            <Settings size={14} /> Tümüne Şirket Doldur
          </button>
          <button onClick={resetAll}
            className="flex items-center gap-1 px-3 py-2 border border-stone-300 text-[#B0413A] rounded-lg text-sm hover:bg-stone-50"
            data-testid="cat-reset-all">
            <Trash2 size={14} /> Hepsini Sıfırla
          </button>
          <button onClick={load}
            className="flex items-center gap-1 px-3 py-2 border border-stone-300 rounded-lg text-sm hover:bg-stone-50">
            <RefreshCw size={14} /> Yenile
          </button>
        </div>
      </div>

      <div className="flex items-center gap-1 border-b border-gray-200 mb-4 overflow-x-auto">
        {marketplaces.map((m) => {
          const isActive = m.key === active;
          return (
            <button key={m.key} onClick={() => setActive(m.key)}
              className={`px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
                isActive ? "border-stone-900 text-stone-900" : "border-transparent text-stone-500 hover:text-stone-900"
              }`} data-testid={`cat-mp-tab-${m.key}`}>
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

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <div className="bg-white border border-stone-200 rounded-xl p-4">
          <div className="text-xs text-stone-500 uppercase tracking-wide">Toplam Kategori</div>
          <div className="text-2xl font-black mt-1 text-stone-900">{data.total}</div>
        </div>
        <div className="bg-white border border-stone-200 rounded-xl p-4">
          <div className="text-xs text-stone-500 uppercase tracking-wide">Eşleşti</div>
          <div className="text-2xl font-black mt-1 text-[#3F7A52]">{data.matched}</div>
        </div>
        <div className="bg-white border border-stone-200 rounded-xl p-4">
          <div className="text-xs text-stone-500 uppercase tracking-wide">Eşleşmedi</div>
          <div className="text-2xl font-black mt-1 text-[#B0413A]">{data.unmatched}</div>
        </div>
      </div>

      {active === "temu" && (
        <div className="flex items-center justify-between gap-3 mb-4 p-3 bg-white border border-stone-200 rounded-xl">
          <div className="text-sm text-stone-600">
            Temu kategori ağacını canlı API'den çek ve eşleştirme aramasını aktifleştir.
            <span className="text-stone-400"> (Yetkilendirme + access token gerektirir.)</span>
          </div>
          <button onClick={syncTemuCategories} disabled={temuSyncing}
            className="flex items-center gap-1 px-3 py-2 bg-stone-900 text-white rounded-lg text-sm font-semibold hover:bg-stone-800 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
            data-testid="cat-temu-sync">
            {temuSyncing ? <RefreshCw size={14} className="animate-spin" /> : <Download size={14} />}
            {temuSyncing ? "Çekiliyor..." : "Temu Kategorilerini Çek"}
          </button>
        </div>
      )}

      {active === "hepsiburada" && <HepsiburadaBaseFieldPanel auth={auth} />}
      {active === "hepsiburada" && <HepsiburadaOrderPull auth={auth} />}
      {active === "hepsiburada" && <HepsiburadaAutofillPanel auth={auth} />}

      {/* Filtreli Toplu Aktarım Paneli */}
      <FilteredPushPanel marketplace={active} auth={auth} categories={data.items} />

      {/* Stok / Fiyat Güncelleme Paneli (tüm pazaryeri sekmelerinde görünür) */}
      <StockPriceUpdatePanel marketplace={active} auth={auth} />

      <div className="flex items-center gap-3 mb-3">
        <div className="relative max-w-md flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Kategori ara..."
            className="w-full border border-gray-200 rounded-lg pl-8 pr-3 py-1.5 text-sm"
            data-testid="cat-search" />
        </div>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={showExcluded}
            onChange={(e) => setShowExcluded(e.target.checked)}
            className="rounded"
            data-testid="cat-show-excluded"
          />
          <span>Gizlenenleri Göster {data.excluded ? `(${data.excluded})` : ""}</span>
        </label>
      </div>

      {/* Toplu seçim bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 bg-stone-50 border border-stone-200 rounded-lg px-3 py-2 mb-3">
          <span className="text-sm font-semibold text-stone-700">{selected.size} seçili</span>
          <button onClick={bulkDelete}
            className="text-xs bg-red-600 hover:bg-red-700 text-white px-3 py-1.5 rounded-lg font-medium"
            data-testid="cat-bulk-delete">
            <Trash2 size={11} className="inline mr-1" /> Seçilileri Sil
          </button>
          <button onClick={() => setSelected(new Set())}
            className="text-xs text-gray-700 hover:text-black">Seçimi Temizle</button>
        </div>
      )}

      <div className="bg-white border rounded-xl shadow-sm overflow-x-auto">
        <table className="admin-table admin-table-compact">
          <thead>
            <tr>
              <th className="w-8">
                <input type="checkbox"
                  checked={filtered.length > 0 && filtered.every((r) => selected.has(r.category_id))}
                  onChange={() => toggleAll(filtered)}
                  data-testid="cat-toggle-all" />
              </th>
              <th>Durum</th>
              <th>Sistem Kategorisi</th>
              <th>Pazaryeri Kategorisi</th>
              <th className="w-56">İşlemler</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="text-center py-8 text-sm text-gray-400">Yükleniyor...</td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={5} className="text-center py-10 text-sm text-gray-400">Kayıt yok</td></tr>
            ) : (
              filtered.map((row) => {
                const isEditing = editRow === row.category_id;
                return (
                  <tr key={row.category_id}>
                    <td>
                      <input type="checkbox"
                        checked={selected.has(row.category_id)}
                        onChange={() => toggleOne(row.category_id)}
                        data-testid={`cat-select-${row.category_id}`} />
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
                    <td className="text-sm">
                      <div className="font-semibold">{row.category_name}</div>
                      {row.category_path && row.category_path !== row.category_name && (
                        <div className="text-xs text-gray-500 mt-0.5" title={row.category_path}>
                          {row.category_path}
                        </div>
                      )}
                    </td>
                    <td>
                      {isEditing ? (
                        <SearchableMapSelect
                          optionsUrl={`/category-mapping/${active}/options`}
                          value={editVal}
                          onChange={(v) => setEditVal(v)}
                          placeholder={`${active} kategorisi ara... (örn: şort, kadın elbise)`}
                          treeMode={active === "trendyol"}
                          data-testid={`cat-search-${row.category_id}`}
                        />
                      ) : (
                        <div className="text-sm">
                          {row.marketplace_category_name || <span className="text-gray-400">-</span>}
                          {row.marketplace_category_id && (
                            <span className="text-[10px] text-gray-400 font-mono ml-2">#{row.marketplace_category_id}</span>
                          )}
                        </div>
                      )}
                    </td>
                    <td>
                      {isEditing ? (
                        <div className="flex gap-1">
                          <button onClick={() => saveRow(row)} className="p-1.5 bg-black text-white rounded hover:bg-gray-800">
                            <Save size={14} />
                          </button>
                          <button onClick={() => setEditRow(null)} className="px-2 text-xs text-gray-500 hover:text-black">İptal</button>
                        </div>
                      ) : (
                        <div className="flex gap-1 flex-wrap">
                          <button onClick={() => {
                            setEditRow(row.category_id);
                            setEditVal({ id: row.marketplace_category_id || "", name: row.marketplace_category_name || "" });
                          }} className="text-xs text-stone-500 hover:underline"
                            data-testid={`cat-edit-${row.category_id}`}>Düzenle</button>

                          {row.status === "matched" && row.marketplace_category_id && (
                            <>
                              <button
                                onClick={() => setAttrMatchFor(row)}
                                className="text-xs text-stone-600 hover:underline flex items-center gap-0.5"
                                title="Özellik Eşle (Zorunlu Trendyol özellikleri ile sistem özelliklerinin bağlanması)"
                                data-testid={`cat-attr-${row.category_id}`}
                              >
                                <Settings size={10} /> Özellik
                              </button>
                              <button
                                onClick={() => setValueMatchFor(row)}
                                className="text-xs text-stone-500 hover:underline flex items-center gap-0.5"
                                title="Değer Eşle (Kırmızı ↔ Red gibi)"
                                data-testid={`cat-val-${row.category_id}`}
                              >
                                <Sliders size={10} /> Değer
                              </button>
                            </>
                          )}
                          {row.excluded ? (
                            <button onClick={() => restoreCategory(row)}
                              className="text-xs text-green-600 hover:underline"
                              data-testid={`cat-restore-${row.category_id}`}>
                              ↺ Geri Getir
                            </button>
                          ) : (
                            <button onClick={() => clearRow(row)}
                              className="text-xs text-red-600 hover:underline"
                              data-testid={`cat-clear-${row.category_id}`}>Sil</button>
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

      {/* Gelişmiş Eşleştirme Modalları — tüm pazaryerleri için çalışır */}
      <AdvancedAttributeMatchModal
        open={!!attrMatchFor}
        onClose={(ok) => { setAttrMatchFor(null); if (ok) load(); }}
        marketplace={active}
        category={attrMatchFor}
      />
      <AdvancedValueMatchModal
        open={!!valueMatchFor}
        onClose={(ok) => { setValueMatchFor(null); if (ok) load(); }}
        marketplace={active}
        category={valueMatchFor}
      />

      {/* Bulk Auto-Match Raporu */}
      <Dialog open={!!bulkAttrReport} onOpenChange={() => setBulkAttrReport(null)}>
        <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Zap size={18} className="text-stone-700" />
              Otomatik Özellik Eşleştirme Raporu — {bulkAttrReport?.marketplace}
            </DialogTitle>
          </DialogHeader>

          {bulkAttrReport && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-2">
                <div className="bg-white border rounded-lg p-3">
                  <div className="text-[10px] text-gray-500 uppercase">İşlenen Kategori</div>
                  <div className="text-xl font-black mt-1">{bulkAttrReport.processed}</div>
                </div>
                <div className="bg-green-50 border border-green-200 rounded-lg p-3">
                  <div className="text-[10px] text-green-700 uppercase">Yeni Eşleşme</div>
                  <div className="text-xl font-black text-green-800 mt-1">{bulkAttrReport.total_new_mappings}</div>
                </div>
                <div className="bg-gray-50 border rounded-lg p-3">
                  <div className="text-[10px] text-gray-500 uppercase">Toplam Matched</div>
                  <div className="text-xl font-black mt-1">{bulkAttrReport.total_categories}</div>
                </div>
              </div>

              <div className="mt-3 bg-stone-50 border border-stone-200 rounded-lg px-3 py-2 text-xs text-stone-600">
                {bulkAttrReport.message}
              </div>

              <div className="flex-1 overflow-auto border rounded-lg mt-3">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 sticky top-0 border-b">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium text-gray-600">Kategori</th>
                      <th className="text-right px-3 py-2 font-medium text-gray-600 w-20">MP Özellik</th>
                      <th className="text-right px-3 py-2 font-medium text-gray-600 w-20">Yeni</th>
                      <th className="text-center px-3 py-2 font-medium text-gray-600 w-16">Kaynak</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(bulkAttrReport.details || []).length === 0 ? (
                      <tr><td colSpan={4} className="text-center py-6 text-gray-400">Kayıt yok</td></tr>
                    ) : (
                      bulkAttrReport.details.map((d) => (
                        <tr key={d.category_id} className="border-b hover:bg-gray-50">
                          <td className="px-3 py-2">
                            <div className="font-medium">{d.category_name}</div>
                            {d.note && <div className="text-[10px] text-amber-600">{d.note}</div>}
                          </td>
                          <td className="px-3 py-2 text-right text-gray-600">{d.total_mp_attrs}</td>
                          <td className="px-3 py-2 text-right font-semibold">
                            {d.new > 0 ? (
                              <span className="text-green-600">+{d.new}</span>
                            ) : (
                              <span className="text-gray-300">0</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {d.fetched ? (
                              <span className="text-[9px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded">CANLI</span>
                            ) : d.total_mp_attrs ? (
                              <span className="text-[9px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">CACHE</span>
                            ) : (
                              <span className="text-[9px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">YOK</span>
                            )}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              <div className="flex justify-end pt-3 border-t mt-3">
                <button onClick={() => setBulkAttrReport(null)}
                  className="px-4 py-2 bg-black text-white rounded text-sm hover:bg-gray-800"
                  data-testid="bulk-report-close">
                  Kapat
                </button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}


/* ───────── Filtreli Toplu Aktarım Paneli ───────── */
function FilteredPushPanel({ marketplace, auth, categories = [] }) {
  const [stockCodes, setStockCodes] = useState("");
  const [cardIds, setCardIds] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(false);
  const [validating, setValidating] = useState(false);
  const [lastResult, setLastResult] = useState(null);
  const [validation, setValidation] = useState(null);
  const [showInvalidOnly, setShowInvalidOnly] = useState(true);
  const [batchDetail, setBatchDetail] = useState(null);
  const [batchLoading, setBatchLoading] = useState(false);
  const [hbTracking, setHbTracking] = useState(null);
  const [hbTrackingLoading, setHbTrackingLoading] = useState(false);
  const [recon, setRecon] = useState(null);
  const [reconLoading, setReconLoading] = useState(false);
  const [selectedCatIds, setSelectedCatIds] = useState([]);
  const [catFilterOpen, setCatFilterOpen] = useState(false);

  const supportedMarketplaces = ["trendyol", "hepsiburada"]; // Trendyol + Hepsiburada
  if (!supportedMarketplaces.includes(marketplace)) return null;

  // Sadece matched kategoriler — boş bırakırsa Tümü demektir
  const matchedCats = (categories || []).filter(
    (c) => c.status === "matched" && c.marketplace_category_id
  );

  const buildBody = () => {
    const codes = stockCodes
      .split(/[\s,;\n]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const cards = cardIds
      .split(/[\s,;\n]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const body = {};
    if (codes.length) {
      body.stock_codes = codes;
      body.barcodes = codes;
    }
    if (cards.length) {
      body.card_ids = cards;
    }
    if (dateFrom) body.date_from = dateFrom;
    if (dateTo) body.date_to = dateTo;
    if (selectedCatIds.length > 0) {
      body.category_filters = selectedCatIds.map((id) => ({ category_id: id, filters: {} }));
    }
    return body;
  };

  const onValidate = async () => {
    setValidating(true);
    setValidation(null);
    const t = toast.loading("Doğrulanıyor...");
    try {
      const body = buildBody();
      const res = await axios.post(
        `${API}/integrations/${marketplace}/products/validate`,
        body,
        { ...auth, timeout: 120000 },
      );
      toast.dismiss(t);
      setValidation(res.data);
      const d = res.data || {};
      if (d.invalid_count === 0) {
        toast.success(`Tümü hazır (${d.valid_count} ürün)`);
      } else {
        toast.warning(
          `${d.valid_count} hazır, ${d.invalid_count} eksik`,
        );
      }
    } catch (e) {
      toast.dismiss(t);
      toast.error(e.response?.data?.detail || "Doğrulama başarısız");
    } finally {
      setValidating(false);
    }
  };

  const setCategoryDefault = async (productId, attr, value) => {
    const t = toast.loading(`'${attr} = ${value}' kategoriye ekleniyor...`);
    try {
      const res = await axios.post(
        `${API}/integrations/hepsiburada/products/${productId}/set-category-default`,
        { attr, value },
        auth,
      );
      toast.dismiss(t);
      toast.success(res.data?.message || "Kategori sabiti eklendi");
      onValidate();
    } catch (e) {
      toast.dismiss(t);
      toast.error(e.response?.data?.detail || "Eklenemedi");
    }
  };

  const setProductAttribute = async (productId, attr, value) => {
    const t = toast.loading(`'${attr} = ${value}' bu ürüne uygulanıyor...`);
    try {
      const res = await axios.post(
        `${API}/integrations/hepsiburada/products/${productId}/set-product-attribute`,
        { attr, value },
        auth,
      );
      toast.dismiss(t);
      toast.success(res.data?.message || "Uygulandı");
      onValidate();
    } catch (e) {
      toast.dismiss(t);
      toast.error(e.response?.data?.detail || "Uygulanamadı");
    }
  };

  const loadBatchDetail = async (batchId) => {
    if (!batchId) return;
    setBatchLoading(true);
    setBatchDetail(null);
    try {
      const r = await axios.get(
        `${API}/integrations/${marketplace}/batch/${batchId}`,
        { ...auth, timeout: 30000 }
      );
      setBatchDetail(r.data);
    } catch (e) {
      toast.error("Batch detayı alınamadı: " + (e.response?.data?.detail || e.message));
    } finally {
      setBatchLoading(false);
    }
  };

  const loadHbTracking = async (tid) => {
    if (!tid) return;
    setHbTrackingLoading(true);
    setHbTracking(null);
    try {
      const r = await axios.get(
        `${API}/integrations/hepsiburada/products/tracking/${encodeURIComponent(tid)}`,
        { ...auth, timeout: 30000 }
      );
      setHbTracking(r.data?.data ?? r.data ?? {});
    } catch (e) {
      toast.error("İçe aktarım durumu alınamadı: " + (e.response?.data?.detail || e.message));
    } finally {
      setHbTrackingLoading(false);
    }
  };

  const loadReconcile = async () => {
    setReconLoading(true);
    setRecon(null);
    try {
      const r = await axios.get(
        `${API}/integrations/hepsiburada/reconcile/preview?markup=25&active_only=true`,
        { ...auth, timeout: 180000 }
      );
      setRecon(r.data || {});
    } catch (e) {
      toast.error("Mutabakat alınamadı: " + (e.response?.data?.detail || e.message));
    } finally {
      setReconLoading(false);
    }
  };

  const exportCsv = (rows, name) => {
    if (!rows || !rows.length) return;
    const head = Object.keys(rows[0]);
    const esc = (x) => `"${String(x ?? "").replace(/"/g, '""')}"`;
    const csv = [head.join(","), ...rows.map((r) => head.map((h) => esc(r[h])).join(","))].join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
  };

  const onSubmit = async () => {
    const body = buildBody();
    const codes = body.stock_codes || [];
    const cards = body.card_ids || [];
    if (!codes.length && !cards.length && !dateFrom && !dateTo) {
      toast.error("Stok kodu / Kart ID veya tarih aralığı girin");
      return;
    }
    setLoading(true);
    setHbTracking(null);
    const t = toast.loading(`${marketplace} aktarımı başlatılıyor...`);
    try {
      const res = await axios.post(
        `${API}/integrations/${marketplace}/products/sync`,
        body,
        { ...auth, timeout: 180000 },
      );
      toast.dismiss(t);
      const d = res.data || {};
      setLastResult(d);
      if (d.successful > 0) {
        toast.success(
          `${d.successful} ürün gönderildi` +
          (d.failed ? ` · ${d.failed} hata` : ""),
        );
      } else {
        toast.error(d.message || "0 ürün gönderildi — detay aşağıda");
      }
    } catch (e) {
      toast.dismiss(t);
      const data = e.response?.data || {};
      setLastResult(data);
      toast.error(data.detail || data.message || e.message || "Aktarım başarısız");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-stone-50 border border-stone-200 rounded-xl p-4 mb-4" data-testid="filtered-push-panel">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="font-bold text-stone-800 text-sm">Filtreli Aktarım — {marketplace.toUpperCase()}</div>
          <div className="text-xs text-stone-500 mt-0.5">
            Tarih aralığı veya stok kodu yazıp seçili pazaryerine aktarın. <b>Önce "Doğrula" ile zorunlu alanları kontrol edin.</b>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <a
            href="/admin/barkod-sorunlari"
            target="_blank"
            rel="noreferrer"
            className="text-xs bg-white border border-stone-300 text-stone-700 hover:bg-stone-50 px-3 py-1.5 rounded font-semibold whitespace-nowrap"
            data-testid="open-barcode-issues-btn"
          >
            ⚠️ Barkod Sorunları
          </a>
          {marketplace === "trendyol" && (
            <>
              <a
                href="/admin/trendyol-hayalet"
                target="_blank"
                rel="noreferrer"
                className="text-xs bg-white border border-stone-300 text-stone-700 hover:bg-stone-50 px-3 py-1.5 rounded font-semibold whitespace-nowrap"
                data-testid="open-ghost-scanner-btn"
              >
                👻 Hayalet Tarayıcı
              </a>
              <a
                href="/admin/trendyol-loglar"
                target="_blank"
                rel="noreferrer"
                className="text-xs bg-white border border-stone-300 text-stone-700 hover:bg-stone-50 px-3 py-1.5 rounded font-semibold whitespace-nowrap"
                data-testid="open-sync-history-btn"
              >
                📋 Aktarım Geçmişi
              </a>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-start">
        <div className="md:col-span-3">
          <label className="text-xs font-medium text-gray-600 block mb-1">Eklenme Tarihi (Başlangıç)</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="w-full border rounded px-2 py-1.5 text-sm bg-white"
            data-testid="push-date-from"
          />
        </div>
        <div className="md:col-span-3">
          <label className="text-xs font-medium text-gray-600 block mb-1">Eklenme Tarihi (Bitiş)</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="w-full border rounded px-2 py-1.5 text-sm bg-white"
            data-testid="push-date-to"
          />
        </div>
        <div className="md:col-span-4">
          <label className="text-xs font-medium text-gray-600 block mb-1">
            Stok Kodu / Barkod (her satıra veya virgülle)
          </label>
          <textarea
            value={stockCodes}
            onChange={(e) => setStockCodes(e.target.value)}
            rows={2}
            placeholder="FCSS2700005, 8684483528521"
            className="w-full border rounded px-2 py-1.5 text-sm bg-white font-mono"
            data-testid="push-stock-codes"
          />
        </div>
        <div className="md:col-span-2 flex flex-col gap-2 mt-5">
          <button
            onClick={onValidate}
            disabled={validating || loading}
            data-testid="push-validate-btn"
            className="bg-white border border-stone-300 text-stone-700 hover:bg-stone-50 text-sm font-medium px-4 py-2 rounded disabled:opacity-50"
          >
            {validating ? "Doğrulanıyor..." : "1. Doğrula"}
          </button>
          <button
            onClick={onSubmit}
            disabled={loading || validating}
            data-testid="push-submit-btn"
            className="bg-stone-900 hover:bg-stone-800 text-white text-sm font-medium px-4 py-2 rounded disabled:opacity-50"
          >
            {loading ? "Gönderiliyor..." : `2. ${marketplace.toUpperCase()}'a Gönder`}
          </button>
          {(stockCodes || cardIds || dateFrom || dateTo || selectedCatIds.length > 0) && (
            <button
              onClick={() => { setStockCodes(""); setCardIds(""); setDateFrom(""); setDateTo(""); setLastResult(null); setValidation(null); setSelectedCatIds([]); }}
              className="text-xs text-gray-500 hover:underline"
            >
              Temizle
            </button>
          )}
        </div>
      </div>

      {/* Ürün Kart ID ile aktarım — stok kodu/barkod gibi kart id de yazılabilir */}
      <div className="mt-3">
        <label className="text-xs font-medium text-gray-600 block mb-1">
          Ürün Kart ID (her satıra veya virgülle)
          <span className="text-gray-400 font-normal ml-1">— Kart ID girerek de ürün aktarabilirsiniz</span>
        </label>
        <textarea
          value={cardIds}
          onChange={(e) => setCardIds(e.target.value)}
          rows={2}
          placeholder="12345, 12346, 12347"
          className="w-full border rounded px-2 py-1.5 text-sm bg-white font-mono"
          data-testid="push-card-ids"
        />
      </div>

      {/* Kategori filtresi */}
      <div className="mt-3 relative">
        <label className="text-xs font-medium text-gray-600 block mb-1">
          Kategori Kapsamı
          <span className="text-gray-400 font-normal ml-1">
            (boş = tüm eşleşmiş kategoriler · {matchedCats.length} kategori mevcut)
          </span>
        </label>
        <button
          type="button"
          onClick={() => setCatFilterOpen(!catFilterOpen)}
          className="w-full text-left border bg-white rounded px-2 py-1.5 text-sm hover:bg-gray-50 flex items-center justify-between"
          data-testid="push-cat-filter-btn"
        >
          <span className="truncate">
            {selectedCatIds.length === 0
              ? "Tüm eşleşmiş kategoriler"
              : selectedCatIds.length <= 3
                ? selectedCatIds.map((id) => matchedCats.find((c) => c.category_id === id)?.category_name || id).join(", ")
                : `${selectedCatIds.length} kategori seçili`}
          </span>
          <span className="text-gray-400 text-xs">{catFilterOpen ? "▲" : "▼"}</span>
        </button>
        {catFilterOpen && (
          <div className="absolute z-30 mt-1 w-full bg-white border rounded-lg shadow-lg max-h-64 overflow-auto">
            <div className="sticky top-0 bg-gray-50 border-b px-3 py-1.5 flex items-center justify-between">
              <button
                type="button"
                onClick={() => setSelectedCatIds([])}
                className="text-[11px] text-gray-600 hover:underline"
              >
                Hepsini temizle
              </button>
              <button
                type="button"
                onClick={() => setSelectedCatIds(matchedCats.map((c) => c.category_id))}
                className="text-[11px] text-stone-600 hover:underline font-semibold"
              >
                Hepsini seç
              </button>
            </div>
            {matchedCats.length === 0 ? (
              <div className="px-3 py-3 text-xs text-gray-400">Eşleşmiş kategori yok</div>
            ) : matchedCats.map((c) => {
              const checked = selectedCatIds.includes(c.category_id);
              return (
                <label key={c.category_id} className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-stone-50 cursor-pointer border-b last:border-b-0">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      setSelectedCatIds((p) => checked ? p.filter((x) => x !== c.category_id) : [...p, c.category_id]);
                    }}
                    data-testid={`push-cat-opt-${c.category_id}`}
                  />
                  <span className="font-medium flex-1 truncate">{c.category_name}</span>
                  <span className="text-[10px] text-gray-400 font-mono">→ {c.marketplace_category_name?.split(" > ").pop() || c.marketplace_category_id}</span>
                </label>
              );
            })}
          </div>
        )}
      </div>

      {/* Validation Report */}
      {validation && (
        <div className="mt-4 bg-white border rounded-lg overflow-hidden" data-testid="validation-report">
          <div className="grid grid-cols-1 sm:grid-cols-3 divide-x border-b">
            <div className="px-4 py-2.5">
              <div className="text-[10px] text-gray-500 uppercase">Toplam</div>
              <div className="text-lg font-black">{validation.total}</div>
            </div>
            <div className="px-4 py-2.5 bg-green-50">
              <div className="text-[10px] text-green-700 uppercase">Hazır</div>
              <div className="text-lg font-black text-green-700">{validation.valid_count}</div>
            </div>
            <div className="px-4 py-2.5 bg-red-50">
              <div className="text-[10px] text-red-700 uppercase">Eksik</div>
              <div className="text-lg font-black text-red-700">{validation.invalid_count}</div>
            </div>
          </div>

          {(validation.top_missing_attrs || []).length > 0 && (
            <div className="px-4 py-2 border-b bg-amber-50">
              <div className="text-[11px] font-bold text-amber-800 uppercase mb-1">En Çok Eksik Olan Zorunlu Özellikler</div>
              <div className="flex flex-wrap gap-1.5">
                {validation.top_missing_attrs.map((m) => (
                  <span key={m.name} className="inline-flex items-center gap-1 bg-white border border-amber-300 text-amber-800 text-[11px] px-2 py-0.5 rounded">
                    {m.name} <b className="text-amber-900">×{m.count}</b>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="px-3 py-2 flex items-center justify-between border-b">
            <label className="text-xs text-gray-600 flex items-center gap-1 cursor-pointer">
              <input
                type="checkbox"
                checked={showInvalidOnly}
                onChange={(e) => setShowInvalidOnly(e.target.checked)}
                data-testid="validation-only-invalid"
              />
              Sadece eksikleri göster
            </label>
            <button
              onClick={() => setValidation(null)}
              className="text-xs text-gray-500 hover:underline"
            >Raporu Kapat</button>
          </div>

          <div className="max-h-72 overflow-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  <th className="px-3 py-1.5 text-left text-gray-600 font-medium">Ürün</th>
                  <th className="px-3 py-1.5 text-left text-gray-600 font-medium w-32">Stok Kodu</th>
                  <th className="px-3 py-1.5 text-left text-gray-600 font-medium">Eksikler</th>
                </tr>
              </thead>
              <tbody>
                {(validation.results || [])
                  .filter((r) => (showInvalidOnly ? !r.is_valid : true))
                  .slice(0, 200)
                  .map((r) => (
                    <tr key={r.id} className={`border-t ${r.is_valid ? "" : "bg-red-50/40"}`}>
                      <td className="px-3 py-1.5">
                        <div className="font-medium">{r.name}</div>
                        <div className="text-[10px] text-gray-500">{r.category_name}</div>
                      </td>
                      <td className="px-3 py-1.5 font-mono text-[10px]">{r.stock_code || "-"}</td>
                      <td className="px-3 py-1.5">
                        {r.is_valid ? (
                          <span className="inline-flex items-center gap-1 text-green-700 font-semibold text-[11px]">✓ Hazır</span>
                        ) : (
                          <div className="space-y-0.5">
                            {(r.errors || []).map((e, i) => (
                              <div key={i} className="inline-block mr-1 bg-red-100 text-red-800 text-[10px] px-1.5 py-0.5 rounded">{e}</div>
                            ))}
                            {(r.missing_required_attrs || []).length > 0 && (
                              <div className="mt-1 bg-red-50 border border-red-300 rounded-md px-2 py-1.5">
                                <div className="text-[10px] font-bold text-red-700 uppercase mb-1">
                                  Hepsiburada zorunlu — eksik ({r.missing_required_attrs.length})
                                </div>
                                <div className="space-y-1">
                                  {r.missing_required_attrs.map((m, mi) => (
                                    <div key={mi} className="flex flex-wrap items-center gap-1">
                                      <span className="inline-flex items-center bg-red-100 border border-red-300 text-red-800 text-[10px] font-bold px-1.5 py-0.5 rounded">
                                        {m.name}
                                      </span>
                                      {marketplace === "hepsiburada" && (m.ty_found || []).length > 0 && (
                                        <>
                                          <span className="text-[9px] text-emerald-600 font-bold">TY:</span>
                                          {m.ty_found.map((vv, ti) => (
                                            <button
                                              key={ti}
                                              onClick={() => setProductAttribute(r.product_id, m.name, vv)}
                                              title="Trendyol'da bulundu — yalnız bu ürüne uygula"
                                              className="inline-flex items-center bg-emerald-50 border border-emerald-400 text-emerald-700 hover:bg-emerald-100 text-[9px] font-bold px-1 py-0.5 rounded cursor-pointer transition-colors"
                                            >
                                              {vv}
                                            </button>
                                          ))}
                                        </>
                                      )}
                                      {(m.valid_values || []).filter((vv) => !(m.ty_found || []).includes(vv)).length > 0 && (
                                        <>
                                          <span className="text-[9px] text-gray-400">→</span>
                                          {m.valid_values.filter((vv) => !(m.ty_found || []).includes(vv)).map((vv, vi) => (
                                            marketplace === "hepsiburada" ? (
                                              <button
                                                key={vi}
                                                onClick={() => setCategoryDefault(r.product_id, m.name, vv)}
                                                title="Kategori sabiti yap (tüm kategoriye uygula)"
                                                className="inline-flex items-center bg-white border border-gray-300 text-gray-600 hover:bg-emerald-50 hover:border-emerald-400 hover:text-emerald-700 text-[9px] px-1 py-0.5 rounded cursor-pointer transition-colors"
                                              >
                                                {vv}
                                              </button>
                                            ) : (
                                              <span key={vi} className="inline-flex items-center bg-white border border-gray-300 text-gray-600 text-[9px] px-1 py-0.5 rounded">
                                                {vv}
                                              </span>
                                            )
                                          ))}
                                          {m.value_count > (m.valid_values || []).length && (
                                            <span className="text-[9px] text-gray-400">+{m.value_count - m.valid_values.length}</span>
                                          )}
                                        </>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                            {(r.unmatched_values || []).length > 0 && (
                              <div className="mt-1" data-testid={`unmatched-${r.stock_code}`}>
                                <div className="text-[10px] font-semibold text-gray-700 mb-0.5">Karşılığı olmayan değerler (eşleştirilecek):</div>
                                <div className="flex flex-wrap gap-1">
                                  {r.unmatched_values.map((u, k) => (
                                    <span key={k} className="inline-flex items-center gap-1 bg-amber-50 border border-amber-300 rounded px-1.5 py-0.5 text-[10px]">
                                      <span className="text-gray-600">{u.attr_name}:</span>
                                      <span className="font-semibold text-amber-800">{u.local_value}</span>
                                      {u.required && <span className="text-red-600 font-bold">*</span>}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                            {(r.warnings || []).length > 0 && (
                              <div className="text-[10px] text-gray-500 mt-0.5">
                                Uyarı: {r.warnings.join(", ")}
                              </div>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {(validation.results || []).filter((r) => (showInvalidOnly ? !r.is_valid : true)).length > 200 && (
              <div className="text-center py-2 text-[11px] text-gray-500 bg-gray-50 border-t">
                İlk 200 ürün gösteriliyor — kalanı toplu görmek için stok kodu/tarih ile filtreleyin.
              </div>
            )}
          </div>
        </div>
      )}

      {lastResult && (
        <div className="mt-3 bg-white border rounded-lg overflow-hidden" data-testid="push-last-result">
          <div className={`px-3 py-2 text-xs flex items-center justify-between ${lastResult.successful > 0 ? "bg-green-50 border-b border-green-200" : "bg-red-50 border-b border-red-200"}`}>
            <div>
              <b>Son aktarım:</b>{" "}
              <span className="text-green-700 font-bold">{lastResult.successful || 0}</span> başarı
              {(lastResult.failed > 0) && <span className="text-red-700 font-bold"> · {lastResult.failed} hata</span>}
              {lastResult.batchRequestId && <span className="text-gray-500 font-mono ml-2">Batch: {lastResult.batchRequestId}</span>}
            </div>
            <button onClick={() => setLastResult(null)} className="text-xs text-gray-500 hover:underline">Kapat</button>
          </div>
          {lastResult.message && (
            <div className="px-3 py-2 text-xs text-gray-700 border-b">{lastResult.message}</div>
          )}
          {lastResult.environment && marketplace === "hepsiburada" && (
            <div className={`px-3 py-2 text-xs border-b ${lastResult.is_test ? "bg-red-100 border-red-300 text-red-900" : "bg-emerald-50 border-emerald-200 text-emerald-800"}`}>
              {lastResult.is_test ? (
                <span><b>⚠️ SANDBOX (TEST) ortamına gönderildi.</b> Ürünler gerçek Hepsiburada mağazanda
                görünmez. Düzelt: <b>Pazaryerleri/Entegrasyonlar → Hepsiburada → Ortam = “Production (Canlı)” → Kaydet</b>,
                sonra tekrar aktar. <span className="font-mono text-[10px]">({lastResult.host})</span></span>
              ) : (
                <span><b>✓ CANLI (production)</b> ortamına gönderildi. <span className="font-mono text-[10px]">({lastResult.host})</span></span>
              )}
            </div>
          )}
          {lastResult.batchRequestId && (
            <div className="px-3 py-2 border-b flex items-center justify-between bg-stone-50">
              <div className="text-xs text-stone-700">
                <b>Trendyol Batch'i ardışık işliyor</b> — gerçek SUCCESS/FAILED durumu için detayları çekin.
              </div>
              <button
                onClick={() => loadBatchDetail(lastResult.batchRequestId)}
                disabled={batchLoading}
                className="text-xs bg-stone-900 hover:bg-stone-800 text-white px-3 py-1 rounded font-semibold disabled:opacity-50"
                data-testid="load-batch-detail-btn"
              >
                {batchLoading ? "Yükleniyor..." : "Batch Detayını Yükle"}
              </button>
            </div>
          )}
          {lastResult.tracking_id && marketplace === "hepsiburada" && (
            <div className="px-3 py-2 border-b bg-orange-50">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs text-orange-900">
                  <b>HB importu asenkron çalışır</b> — &ldquo;başarı&rdquo; yalnızca <u>gönderildi</u> demektir.
                  Ürün gerçekten oluştu mu yoksa reddedildi mi (ve sebebi) görmek için durumu sorgulayın.
                  <div className="font-mono text-[10px] text-orange-600 mt-0.5">Takip No: {lastResult.tracking_id}</div>
                </div>
                <button
                  onClick={() => loadHbTracking(lastResult.tracking_id)}
                  disabled={hbTrackingLoading}
                  className="text-xs bg-orange-600 hover:bg-orange-700 text-white px-3 py-1 rounded font-semibold disabled:opacity-50 shrink-0"
                  data-testid="load-hb-tracking-btn"
                >
                  {hbTrackingLoading ? "Sorgulanıyor..." : "İçe Aktarım Durumunu Sorgula"}
                </button>
              </div>
              {hbTracking && (() => {
                const d = hbTracking || {};
                const items = Array.isArray(d) ? d : (d.data || d.items || d.results || []);
                const status = d.status || d.trackingStatus || d.state || "";
                return (
                  <div className="mt-2 bg-white border rounded p-2">
                    {status && <div className="text-xs mb-1"><b>Durum:</b> {String(status)}</div>}
                    {Array.isArray(items) && items.length > 0 ? (
                      <div className="space-y-1 max-h-60 overflow-auto">
                        {items.map((it, i) => {
                          const sku = it.merchantSku || it.sku || it.productId || "-";
                          const st = it.status || it.state || "";
                          const errsRaw = it.errors || it.failureReasons || it.validationResults || it.messages || [];
                          const errTxt = (Array.isArray(errsRaw) ? errsRaw : [errsRaw])
                            .map((e2) => (typeof e2 === "string" ? e2 : (e2?.message || e2?.reason || e2?.description || (e2 ? JSON.stringify(e2) : ""))))
                            .filter(Boolean).join(" · ");
                          const ok = !!String(st).toUpperCase().match(/SUCCESS|CREATED|MATCHED|DONE|OK|APPROVED/);
                          return (
                            <div key={i} className={`text-[10px] border rounded px-2 py-1 ${ok ? "bg-green-50 border-green-200" : "bg-red-50 border-red-200"}`}>
                              <div className="flex justify-between gap-2">
                                <span className="font-mono">{String(sku)}</span>
                                <span className={`font-bold ${ok ? "text-green-700" : "text-red-700"}`}>{String(st) || (ok ? "OK" : "?")}</span>
                              </div>
                              {errTxt && <div className="text-red-800 mt-0.5 break-words">{errTxt}</div>}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="text-[11px] text-gray-500">
                        Henüz işlenmemiş olabilir — birkaç saniye sonra tekrar sorgulayın. Reddedilenler
                        ayrıca <b>Statü = REJECTED</b> listesinde sebepleriyle görünür.
                      </div>
                    )}
                    <details className="mt-1">
                      <summary className="text-[10px] text-gray-400 cursor-pointer hover:text-black">Ham yanıt</summary>
                      <pre className="text-[9px] bg-gray-50 p-2 rounded overflow-auto max-h-48">{JSON.stringify(hbTracking, null, 2)}</pre>
                    </details>
                  </div>
                );
              })()}
            </div>
          )}
          {batchDetail && (
            <div className="px-3 py-2 border-b bg-gray-50" data-testid="batch-detail">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center mb-2">
                <div className="bg-white border rounded p-1.5">
                  <div className="text-[9px] text-gray-500 uppercase">Status</div>
                  <div className="text-xs font-bold">{batchDetail.status}</div>
                </div>
                <div className="bg-green-50 border border-green-200 rounded p-1.5">
                  <div className="text-[9px] text-green-700 uppercase">Başarılı</div>
                  <div className="text-xs font-bold text-green-700">{batchDetail.success_count}</div>
                </div>
                <div className="bg-red-50 border border-red-200 rounded p-1.5">
                  <div className="text-[9px] text-red-700 uppercase">Hatalı</div>
                  <div className="text-xs font-bold text-red-700">{batchDetail.failed_count}</div>
                </div>
                <div className="bg-white border rounded p-1.5">
                  <div className="text-[9px] text-gray-500 uppercase">Toplam</div>
                  <div className="text-xs font-bold">{batchDetail.item_count}</div>
                </div>
              </div>
              {(batchDetail.top_failures || []).length > 0 && (
                <div className="mb-2">
                  <div className="text-[10px] font-bold text-red-800 uppercase mb-1">En Çok Görülen Hatalar</div>
                  <div className="space-y-0.5">
                    {batchDetail.top_failures.map((f, i) => (
                      <div key={i} className="text-[10px] bg-white border border-red-200 rounded px-2 py-1 flex items-start justify-between gap-2">
                        <span className="text-red-900 flex-1">{f.reason}</span>
                        <span className="bg-red-100 text-red-700 px-1.5 py-0.5 rounded font-bold shrink-0">×{f.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <details>
                <summary className="text-[10px] text-gray-500 cursor-pointer hover:text-black">Tüm Item Detayları ({batchDetail.items?.length || 0})</summary>
                <div className="mt-1 max-h-60 overflow-auto bg-white border rounded">
                  <table className="w-full text-[10px]">
                    <tbody>
                      {(batchDetail.items || []).map((it, i) => (
                        <tr key={i} className={`border-b ${it.status === "SUCCESS" ? "" : "bg-red-50/30"}`}>
                          <td className="px-2 py-1 font-mono">{it.requestItem?.barcode || it.requestItem?.product?.barcode || "-"}</td>
                          <td className="px-2 py-1">
                            {it.status === "SUCCESS" ? (
                              <span className="text-green-700 font-bold">✓ SUCCESS</span>
                            ) : (
                              <div>
                                <span className="text-red-700 font-bold">✗ {it.status}</span>
                                {(it.failureReasons || []).slice(0, 2).map((fr, j) => (
                                  <div key={j} className="text-red-900 mt-0.5 break-words">{fr}</div>
                                ))}
                              </div>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </div>
          )}
          {(lastResult.errors || []).length > 0 && (
            <div className="px-3 py-2">
              <div className="text-[11px] font-bold text-red-800 uppercase mb-1">
                Hatalar ({lastResult.errors.length})
              </div>
              <ul className="space-y-1 max-h-60 overflow-auto">
                {lastResult.errors.slice(0, 50).map((er, i) => (
                  <li key={i} className="text-[11px] bg-red-50 text-red-900 border border-red-100 rounded px-2 py-1 font-mono break-words">
                    {typeof er === "string" ? er : JSON.stringify(er)}
                  </li>
                ))}
                {lastResult.errors.length > 50 && (
                  <li className="text-[10px] text-gray-500">... {lastResult.errors.length - 50} hata daha (Entegrasyon Logları'na bakın)</li>
                )}
              </ul>
            </div>
          )}
          {lastResult.trendyol_response && (
            <details className="px-3 py-2 border-t">
              <summary className="text-[11px] text-gray-500 cursor-pointer hover:text-black">Trendyol Ham Cevabı (debug)</summary>
              <pre className="text-[10px] mt-1 bg-gray-50 p-2 rounded border max-h-40 overflow-auto font-mono whitespace-pre-wrap">
                {JSON.stringify(lastResult.trendyol_response, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
      {marketplace === "hepsiburada" && (
        <div className="mt-3 bg-white border rounded-lg overflow-hidden" data-testid="hb-reconcile">
          <div className="px-3 py-2 bg-indigo-50 border-b flex items-center justify-between gap-2">
            <div className="text-xs text-indigo-900">
              <b>Hepsiburada Mutabakat (Önizleme)</b> — sitedeki ürünlerle HB kataloğunu barkod/SKU ile karşılaştırır.
              <span className="block text-[10px] text-indigo-500">Salt-okunur; hiçbir şey göndermez/silmez. Fiyat %25 marjla hesaplanır.</span>
            </div>
            <button onClick={loadReconcile} disabled={reconLoading}
              className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1 rounded font-semibold disabled:opacity-50 shrink-0"
              data-testid="hb-reconcile-btn">
              {reconLoading ? "Hesaplanıyor..." : "Mutabakatı Çalıştır"}
            </button>
          </div>
          {recon && (
            <div className="p-3 text-xs">
              {recon.is_test && (
                <div className="mb-2 bg-red-100 border border-red-300 text-red-900 rounded px-2 py-1">
                  ⚠️ SANDBOX okunuyor — bu rapor gerçek mağazayı yansıtmaz. Önce Ortam=Canlı yapın.
                </div>
              )}
              {recon.hb_truncated && (
                <div className="mb-2 bg-amber-50 border border-amber-200 text-amber-800 rounded px-2 py-1">
                  HB kataloğu sayfa sınırına takıldı ({recon.hb_pages_scanned} sayfa) — liste eksik olabilir.
                </div>
              )}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                <div className="bg-gray-50 border rounded p-2 text-center"><div className="text-[9px] text-gray-500 uppercase">Site SKU</div><div className="text-sm font-bold">{recon.site_sku_count ?? 0}</div></div>
                <div className="bg-gray-50 border rounded p-2 text-center"><div className="text-[9px] text-gray-500 uppercase">HB Katalog</div><div className="text-sm font-bold">{recon.hb_catalog_count ?? 0}</div></div>
                <div className="bg-green-50 border border-green-200 rounded p-2 text-center"><div className="text-[9px] text-green-700 uppercase">Eşleşen</div><div className="text-sm font-bold text-green-700">{recon.matched_count ?? 0}</div></div>
                <div className="bg-orange-50 border border-orange-200 rounded p-2 text-center"><div className="text-[9px] text-orange-700 uppercase">Eksik → Aktarılacak</div><div className="text-sm font-bold text-orange-700">{recon.missing_on_hb?.count ?? 0}</div></div>
              </div>

              <div className="mb-3">
                <div className="flex items-center justify-between mb-1">
                  <div className="text-[11px] font-bold text-orange-800 uppercase">Eksik (sitede var, HB'de yok) — %25 marjla aktarılacak</div>
                  <button onClick={() => exportCsv(recon.missing_on_hb?.items, "hb_eksik_urunler.csv")}
                    disabled={!recon.missing_on_hb?.count}
                    className="text-[10px] bg-orange-600 hover:bg-orange-700 text-white px-2 py-0.5 rounded disabled:opacity-40">CSV indir</button>
                </div>
                <div className="max-h-56 overflow-auto border rounded">
                  <table className="w-full text-[10px]">
                    <thead className="bg-gray-50 sticky top-0"><tr><th className="text-left px-2 py-1">merchantSku</th><th className="text-left px-2 py-1">Ürün</th><th className="text-right px-2 py-1">Fiyat (%25)</th><th className="text-right px-2 py-1">Stok</th></tr></thead>
                    <tbody>
                      {(recon.missing_on_hb?.items || []).slice(0, 250).map((r, i) => (
                        <tr key={i} className="border-t"><td className="px-2 py-1 font-mono">{r.merchantSku}</td><td className="px-2 py-1 truncate max-w-[180px]">{r.name}</td><td className="px-2 py-1 text-right">{r.price}</td><td className="px-2 py-1 text-right">{r.stock}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {recon.missing_on_hb?.count > 250 && <div className="text-[10px] text-gray-400 mt-0.5">İlk 250 gösterildi; tamamı CSV'de.</div>}
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <div className="text-[11px] font-bold text-red-800 uppercase">HB'de var, barkodunla eşleşmiyor — silinecek/listelenecek</div>
                  <button onClick={() => exportCsv(recon.orphan_on_hb?.items, "hb_eslesmeyen_urunler.csv")}
                    disabled={!recon.orphan_on_hb?.count}
                    className="text-[10px] bg-red-600 hover:bg-red-700 text-white px-2 py-0.5 rounded disabled:opacity-40">CSV indir</button>
                </div>
                <div className="max-h-56 overflow-auto border rounded">
                  <table className="w-full text-[10px]">
                    <thead className="bg-gray-50 sticky top-0"><tr><th className="text-left px-2 py-1">merchantSku</th><th className="text-left px-2 py-1">Barkod</th><th className="text-left px-2 py-1">Ürün</th><th className="text-left px-2 py-1">Statü</th></tr></thead>
                    <tbody>
                      {(recon.orphan_on_hb?.items || []).slice(0, 250).map((r, i) => (
                        <tr key={i} className="border-t"><td className="px-2 py-1 font-mono">{r.merchantSku}</td><td className="px-2 py-1 font-mono">{r.barcode}</td><td className="px-2 py-1 truncate max-w-[160px]">{r.name}</td><td className="px-2 py-1">{r.status}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {recon.orphan_on_hb?.count > 250 && <div className="text-[10px] text-gray-400 mt-0.5">İlk 250 gösterildi; tamamı CSV'de.</div>}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Hepsiburada Sipariş Çek (OMS) — geçmiş siparişleri tarih/sipariş-no ile içe aktar
// ──────────────────────────────────────────────────────────────────────────────
function HepsiburadaBaseFieldPanel({ auth }) {
  const [fields, setFields] = useState([]);
  const [sources, setSources] = useState([]);
  const [markup, setMarkup] = useState(0);
  const [priceSource, setPriceSource] = useState("price");
  const [priceSources, setPriceSources] = useState([]);
  const [commonAttrs, setCommonAttrs] = useState([]);
  const [globalDefaults, setGlobalDefaults] = useState({});
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const r = await axios.get(`${API}/integrations/hepsiburada/base-field-mappings`, auth);
        setFields(r.data?.fields || []);
        setSources(r.data?.sources || []);
        setMarkup(r.data?.markup || 0);
        setPriceSource(r.data?.price_source || "price");
        setPriceSources(r.data?.price_sources || []);
        setCommonAttrs(r.data?.common_attrs || []);
        setGlobalDefaults(r.data?.global_attr_defaults || {});
      } catch (e) {
        // sessiz geç
      } finally {
        setLoaded(true);
      }
    })();
  }, []);

  const update = (key, patch) =>
    setFields((fs) => fs.map((f) => (f.key === key ? { ...f, ...patch } : f)));

  const save = async () => {
    setSaving(true);
    const t = toast.loading("Kaydediliyor...");
    try {
      const mappings = {};
      fields.forEach((f) => {
        mappings[f.key] = { source: f.source || "", default: f.default || "" };
      });
      await axios.post(
        `${API}/integrations/hepsiburada/base-field-mappings`,
        { mappings, markup: Number(markup) || 0, price_source: priceSource,
          global_attr_defaults: globalDefaults },
        auth
      );
      toast.success("Varsayılan alan eşleştirmesi + kâr marjı kaydedildi", { id: t });
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kaydedilemedi", { id: t });
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) return null;
  const mk = Number(markup) || 0;
  const previewPrice = (1000 * (1 + mk / 100)).toLocaleString("tr-TR", { maximumFractionDigits: 2 });
  return (
    <div className="bg-stone-50 border border-stone-200 rounded-xl p-4 mb-4" data-testid="hb-basefields-panel">
      <div className="flex items-center justify-between gap-2 cursor-pointer" onClick={() => setOpen((o) => !o)}>
        <div>
          <div className="font-bold text-stone-800 text-sm flex items-center gap-1">
            <Settings size={14} /> Hepsiburada Varsayılan Alan Eşleştirme & Fiyat
          </div>
          <div className="text-xs text-stone-500 mt-0.5">
            Stok kodu, ürün adı, açıklama, barkod, marka, desi, görsel gibi temel alanların hangi ürün-kartı
            değerinden çekileceğini ve <b>kâr marjını</b> bir kez burada belirle. <b>Tüm kategorilerde geçerli.</b>
          </div>
        </div>
        <span className="text-stone-500 text-xs whitespace-nowrap">{open ? "Gizle ▲" : "Göster ▼"}</span>
      </div>
      {open && (
        <div className="mt-3 space-y-2">
          <div className="bg-white rounded-lg p-3 border-2 border-green-200">
            <div className="font-bold text-green-900 text-sm mb-1">💰 Fiyat & Kâr Marjı</div>
            <div className="text-xs text-gray-600 mb-2">
              HB'ye gönderilen fiyat, aşağıdaki kaynaktan alınıp bu oran kadar artırılır. (Stok/Fiyat gönderiminde uygulanır.)
            </div>
            <div className="flex items-center gap-2 flex-wrap mb-2">
              <span className="w-28 text-sm font-semibold text-gray-800">Fiyat Kaynağı</span>
              <span className="text-gray-400 text-xs">←</span>
              <select
                value={priceSource}
                onChange={(e) => setPriceSource(e.target.value)}
                className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 flex-1 min-w-[220px] bg-white"
              >
                {(priceSources.length
                  ? priceSources
                  : [{ value: "auto", label: "Otomatik" }]
                ).map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm text-gray-700">HB fiyatı = Fiyat kaynağı × (1 +</span>
              <input
                type="number"
                min="0"
                step="0.1"
                value={markup}
                onChange={(e) => setMarkup(e.target.value)}
                className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 w-24 text-right font-semibold"
              />
              <span className="text-sm text-gray-700">% ÷ 100)</span>
            </div>
            <div className="text-xs text-green-700 mt-2 font-semibold">
              Örnek: 1.000 ₺ ürün → HB'ye gönderilen fiyat: {previewPrice} ₺
              {mk > 0 ? ` (+%${mk})` : " (marj yok)"}
            </div>
          </div>
          {commonAttrs.length > 0 && (
            <div className="bg-white rounded-lg p-3 border border-stone-200">
              <div className="font-bold text-stone-800 text-sm mb-1">🚻 Ortak Özellikler (Tüm Kategoriler)</div>
              <div className="text-xs text-gray-600 mb-2">
                Burada seçtiğin değer <b>tüm kategorilerde</b> bu HB özelliğine uygulanır (her kategorinin
                kendi değer listesine göre çözülür). Örn. tüm ürünlerin kadın ürünü ise: Cinsiyet → Kadın.
                Listede değer yoksa önce ilgili kategoride <b>"hepsiburada Canlı Çek"</b> yap.
              </div>
              {commonAttrs.map((c) => (
                <div key={c.key} className="flex items-center gap-2 flex-wrap mb-2">
                  <span className="w-28 text-sm font-semibold text-gray-800">{c.label}</span>
                  <span className="text-gray-400 text-xs">←</span>
                  {c.values && c.values.length ? (
                    <select
                      value={globalDefaults[c.key] || ""}
                      onChange={(e) => setGlobalDefaults((p) => ({ ...p, [c.key]: e.target.value }))}
                      className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 flex-1 min-w-[200px] bg-white"
                    >
                      <option value="">— Seçilmedi (kategori bazında çözülür) —</option>
                      {c.values.map((v) => <option key={v} value={v}>{v}</option>)}
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={globalDefaults[c.key] || ""}
                      onChange={(e) => setGlobalDefaults((p) => ({ ...p, [c.key]: e.target.value }))}
                      placeholder="Değer yaz (ör. Kadın) — Canlı Çek sonrası liste otomatik gelir"
                      className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 flex-1 min-w-[200px]"
                    />
                  )}
                </div>
              ))}
            </div>
          )}
          {fields.map((f) => (
            <div key={f.key} className="flex items-center gap-2 flex-wrap bg-white rounded-lg p-2 border border-stone-200">
              <div className="w-44 text-sm font-semibold text-gray-800">{f.label}</div>
              <span className="text-gray-400 text-xs">←</span>
              <select
                value={f.source || ""}
                onChange={(e) => update(f.key, { source: e.target.value })}
                className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 flex-1 min-w-[160px] bg-white"
              >
                {sources.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
              {f.source === "__default" && (
                <input
                  type="text"
                  value={f.default || ""}
                  onChange={(e) => update(f.key, { default: e.target.value })}
                  placeholder="Varsayılan değer..."
                  className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 w-44"
                />
              )}
            </div>
          ))}
          <div className="flex justify-end pt-1">
            <button
              onClick={save}
              disabled={saving}
              className="text-sm bg-stone-900 hover:bg-stone-800 text-white px-4 py-2 rounded-lg font-semibold disabled:opacity-60"
            >
              {saving ? "Kaydediliyor..." : "Eşleştirmeyi Kaydet"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function HepsiburadaAutofillPanel({ auth }) {
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState(null);
  const run = async () => {
    if (!window.confirm(
      "Tüm HB-eşleşmiş kategorilerdeki ürünlerin Hepsiburada özellik alanları, ürün verisinden " +
      "otomatik doldurulacak. Mevcut (manuel) değerler korunur. Devam edilsin mi?"
    )) return;
    setLoading(true);
    setRes(null);
    const t = toast.loading("HB özellikleri otomatik dolduruluyor...");
    try {
      const r = await axios.post(`${API}/integrations/hepsiburada/products/autofill-attributes`, {}, auth);
      setRes(r.data);
      toast.success(r.data?.message || "Dolduruldu", { id: t });
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Otomatik doldurma başarısız", { id: t });
    } finally {
      setLoading(false);
    }
  };
  return (
    <div className="bg-stone-50 border border-stone-200 rounded-xl p-4 mb-4" data-testid="hb-autofill-panel">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex-1 min-w-[260px]">
          <div className="font-bold text-stone-800 text-sm flex items-center gap-1">
            <Zap size={14} /> Ürün HB Özelliklerini Otomatik Doldur
          </div>
          <div className="text-xs text-stone-600 mt-0.5">
            Eşleşmiş kategorilerdeki ürünlerin Hepsiburada özelliklerini (Cinsiyet, Materyal, Marka,
            Kalıp vb.) ürün verisinden otomatik türetir. Renk/Beden gönderimde varyanttan gelir.
            <b> Mevcut değerler korunur</b> — yalnız boş alanlar doldurulur.
          </div>
        </div>
        <button
          onClick={run}
          disabled={loading}
          className="text-sm bg-stone-900 hover:bg-stone-800 text-white px-4 py-2 rounded-lg font-semibold disabled:opacity-60 whitespace-nowrap"
          data-testid="hb-autofill-btn"
        >
          {loading ? "Dolduruluyor..." : "Otomatik Doldur"}
        </button>
      </div>
      {res && (
        <div className="text-xs text-stone-600 mt-2 font-medium">
          {res.updated_products} üründe {res.filled_values} özellik dolduruldu · {res.scanned} ürün tarandı
        </div>
      )}
    </div>
  );
}

function HepsiburadaOrderPull({ auth }) {
  const iso = (d) => d.toISOString().slice(0, 10);
  const [begin, setBegin] = useState(iso(new Date(Date.now() - 30 * 864e5)));
  const [end, setEnd] = useState(iso(new Date()));
  const [orderNo, setOrderNo] = useState("");
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState(null);
  const [raws, setRaws] = useState([]);
  const [sel, setSel] = useState(new Set());
  const [importing, setImporting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [result, setResult] = useState(null);
  const [rawSample, setRawSample] = useState(null);
  const [err, setErr] = useState("");

  const fmtTL = (n) => `${(Number(n) || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} TL`;

  const pull = async (ov) => {
    setLoading(true); setErr(""); setResult(null); setRows(null); setSel(new Set());
    try {
      let body;
      if (ov && ov.orderNumber) {
        body = { order_number: ov.orderNumber }; // tek sipariş no ile (en hızlı, kesin)
      } else if (ov && ov.noDate) {
        body = {}; // tarihsiz: HB "ödemesi tamamlanmış" (Open) listesi — offset+limit ile
      } else {
        const bb = (ov && ov.b) || begin, ee = (ov && ov.e) || end;
        body = (orderNo.trim() && !ov)
          ? { order_number: orderNo.trim() }
          : { begin_date: `${bb}T00:00:00`, end_date: `${ee}T23:59:59` };
      }
      const r = await axios.post(`${API}/integrations/hepsiburada/orders/preview`, body, { ...auth, timeout: 60000 });
      if (r.data && r.data.success === false) {
        setErr((r.data.error || "Çekme başarısız") + (r.data.attempted_url ? `\n↳ ${r.data.attempted_url}` : ""));
        setRows(null); setRawSample(r.data.raw_sample || null);
        return;
      }
      const pv = r.data?.preview || [];
      setRows(pv); setRaws(r.data?.orders || []); setRawSample(r.data?.raw_sample || null);
      const s = new Set(); pv.forEach((p, i) => { if (!p._already_imported) s.add(i); }); setSel(s);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Çekme başarısız");
    } finally { setLoading(false); }
  };

  const toggle = (i) => { const s = new Set(sel); s.has(i) ? s.delete(i) : s.add(i); setSel(s); };
  const allOn = rows && rows.length > 0 && sel.size === rows.length;
  const toggleAll = () => setSel(allOn ? new Set() : new Set((rows || []).map((_, i) => i)));

  const createTest = async () => {
    setCreating(true); setErr(""); setResult(null);
    try {
      const r = await axios.post(`${API}/integrations/hepsiburada/orders/create-test`, {}, { ...auth, timeout: 60000 });
      if (r.data && r.data.success === false) {
        setErr((r.data.error || "Test siparişi oluşturulamadı") + (r.data.attempted_url ? `\n↳ ${r.data.attempted_url}` : ""));
        return;
      }
      setOrderNo("");
      await new Promise((res) => setTimeout(res, 1500));
      await pull({ orderNumber: r.data.order_number }); // siparişi numarasıyla çek (hızlı, kesin)
      setResult({ created: r.data.order_number, skus: r.data.used_skus });
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Test siparişi oluşturulamadı");
    } finally { setCreating(false); }
  };

  const doImport = async () => {
    if (sel.size === 0) return;
    setImporting(true); setResult(null);
    try {
      const orders = [...sel].map((i) => raws[i]).filter(Boolean);
      const r = await axios.post(`${API}/integrations/hepsiburada/orders/import-selected`, { orders }, { ...auth, timeout: 60000 });
      setResult(r.data);
      await pull();
    } catch (e) {
      setResult({ error: e?.response?.data?.detail || "Aktarım başarısız" });
    } finally { setImporting(false); }
  };

  return (
    <div className="border border-stone-200 rounded-xl mb-4 overflow-hidden">
      <div className="px-4 py-2.5 bg-stone-50 border-b border-stone-200 flex items-center gap-2">
        <Download size={16} className="text-stone-500" />
        <span className="text-sm font-semibold text-stone-700">Hepsiburada Sipariş Çek</span>
        <span className="text-xs text-stone-600">— geçmiş siparişleri tarih veya sipariş no ile sisteme aktar</span>
      </div>
      <div className="p-4 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-gray-600 mb-1">Başlangıç</label>
            <input type="date" value={begin} onChange={(e) => setBegin(e.target.value)} className="border rounded-lg px-2.5 py-1.5 text-sm" />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Bitiş</label>
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="border rounded-lg px-2.5 py-1.5 text-sm" />
          </div>
          <div className="flex-1 min-w-[180px]">
            <label className="block text-xs text-gray-600 mb-1">veya Sipariş No (opsiyonel)</label>
            <input value={orderNo} onChange={(e) => setOrderNo(e.target.value)} placeholder="Tek sipariş için no gir" className="w-full border rounded-lg px-2.5 py-1.5 text-sm" />
          </div>
          <button onClick={() => pull()} disabled={loading} className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-stone-900 text-white text-sm font-semibold hover:bg-stone-800 disabled:opacity-60">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> {loading ? "Çekiliyor…" : "Çek"}
          </button>
          <button onClick={createTest} disabled={creating || loading} title="SIT/Sandbox modunda HB stub üzerinde test siparişi oluşturur ve panele çeker" className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg border border-stone-300 text-stone-600 bg-white text-sm font-semibold hover:bg-stone-50 disabled:opacity-60">
            <Plus size={14} /> {creating ? "Oluşturuluyor…" : "Test Sipariş Oluştur (SIT)"}
          </button>
        </div>

        {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 whitespace-pre-line">{err}</div>}

        {result && (
          <div className={`text-sm rounded-lg px-3 py-2 border ${result.error ? "text-red-700 bg-red-50 border-red-200" : "text-green-800 bg-green-50 border-green-200"}`}>
            {result.error
              ? result.error
              : (result.created
                  ? `Test siparişi oluşturuldu: ${result.created}${(result.skus && result.skus.length) ? ` (SKU: ${result.skus.join(", ")})` : ""} · listeden çekiliyor…`
                  : `Aktarıldı: ${result.imported} · Güncellendi: ${result.updated}${(result.errors && result.errors.length) ? ` · Hata: ${result.errors.length}` : ""}`)}
          </div>
        )}

        {rows && rows.length === 0 && !loading && <div className="text-sm text-gray-500">Bu kriterlerde sipariş bulunamadı.</div>}

        {rows && rows.length > 0 && (
          <>
            <div className="flex items-center justify-between">
              <div className="text-xs text-gray-600">{rows.length} sipariş · {sel.size} seçili</div>
              <button onClick={doImport} disabled={importing || sel.size === 0} className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-stone-900 text-white text-sm font-semibold hover:bg-stone-800 disabled:opacity-60">
                <Download size={14} /> {importing ? "Aktarılıyor…" : `Seçili ${sel.size} Siparişi Aktar`}
              </button>
            </div>
            <div className="border rounded-lg overflow-x-auto max-h-96">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-600 text-xs uppercase sticky top-0">
                  <tr>
                    <th className="px-3 py-2 w-8 text-left"><input type="checkbox" checked={allOn} onChange={toggleAll} /></th>
                    <th className="px-3 py-2 text-left font-medium">Sipariş No</th>
                    <th className="px-3 py-2 text-left font-medium">Müşteri</th>
                    <th className="px-3 py-2 text-right font-medium">Kalem</th>
                    <th className="px-3 py-2 text-right font-medium">Tutar</th>
                    <th className="px-3 py-2 text-left font-medium">Durum</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p, i) => (
                    <tr key={i} className={`border-t border-gray-100 ${p._already_imported ? "bg-gray-50" : ""}`}>
                      <td className="px-3 py-2"><input type="checkbox" checked={sel.has(i)} onChange={() => toggle(i)} /></td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-900">{p.hepsiburada_order_number || p.order_number}</td>
                      <td className="px-3 py-2 text-gray-900">{p.shipping_address?.first_name} {p.shipping_address?.last_name}</td>
                      <td className="px-3 py-2 text-right text-gray-900">{p.items?.length || 0}</td>
                      <td className="px-3 py-2 text-right text-gray-900">{fmtTL(p.total)}</td>
                      <td className="px-3 py-2">
                        {p._already_imported
                          ? <span className="text-[11px] px-2 py-0.5 rounded bg-gray-200 text-gray-700">Zaten aktarıldı</span>
                          : <span className="text-[11px] px-2 py-0.5 rounded bg-amber-100 text-amber-800">Yeni</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {rawSample && (
          <details className="text-xs">
            <summary className="text-gray-500 cursor-pointer hover:text-black">Hepsiburada ham cevabı (debug — alan adları)</summary>
            <pre className="text-[10px] mt-1 bg-gray-50 p-2 rounded border max-h-48 overflow-auto font-mono whitespace-pre-wrap">{JSON.stringify(rawSample, null, 2)}</pre>
          </details>
        )}
      </div>
    </div>
  );
}
