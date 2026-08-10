/**
 * =============================================================================
 * FailedTransfers.jsx — Aktarılamayan Siparişler / Ürünler
 * =============================================================================
 *
 * AMAÇ:
 *   Ticimax "Sipariş Takip > Aktarılamayan Siparişler" ekranı ayna. Pazaryeri
 *   entegrasyon çağrılarında **failed** olan kayıtları gösterir, kullanıcı
 *   incelleyip tek tek veya toplu olarak **yeniden deneme**sini tetikler.
 *
 * VERİ KAYNAĞI:
 *   GET /api/marketplace-hub/logs?status=failed&...  (integration_logs)
 *
 * "Tekrar Aktar" akışı:
 *   - Seçili log kayıtlarının `marketplace` + `action` + `ref_id` bilgisinden
 *     uygun endpoint türetilir:
 *       product_push → POST /api/integrations/{marketplace}/products/{ref}/sync
 *       order_pull   → POST /api/integrations/{marketplace}/orders/import
 *       stock_update → POST /api/integrations/{marketplace}/products/{ref}/sync-inventory
 *   - Her çağrı sonrası yeni bir log kaydı düşer (middleware sayesinde
 *     otomatik). Kullanıcı "Yenile" ile sonuçları görebilir.
 * =============================================================================
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { RefreshCw, Trash2, CheckSquare, Square, AlertTriangle, Play } from "lucide-react";
import Pagination from "../../components/admin/Pagination";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const ACTION_LABELS = {
  product_push: "Ürün Aktarımı",
  stock_update: "Stok Güncelleme",
  price_update: "Fiyat Güncelleme",
  order_pull: "Sipariş Çekme",
  order_update: "Sipariş Güncelleme",
  return_pull: "İade Çekme",
  invoice_create: "Fatura Oluşturma",
};

// Bir log'u "tekrar aktar" için hangi endpoint'e gideceğiz?
function retryEndpoint(log) {
  const mk = log.marketplace;
  const ref = log.ref_id;
  // DENETİM FIX: ürün/stok sync uçları yalnız Trendyol'da var; HB/diğerlerinde 404 verip
  // "hata" sayılıyordu. Desteklenmeyen pazaryeri+aksiyon için null → buton devre dışı.
  switch (log.action) {
    case "product_push":
      return (mk === "trendyol" && ref) ? `${API}/integrations/trendyol/products/${ref}/sync` : null;
    case "stock_update":
      return (mk === "trendyol" && ref) ? `${API}/integrations/trendyol/products/${ref}/sync-inventory` : null;
    case "order_pull":
      return `${API}/integrations/${mk}/orders/import`;
    default:
      return null;
  }
}

export default function FailedTransfers() {
  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selected, setSelected] = useState([]);
  const [retrying, setRetrying] = useState(false);
  const [filters, setFilters] = useState({ marketplace: "", action: "" });
  const [marketplaces, setMarketplaces] = useState([]);

  const token = useMemo(() => localStorage.getItem("token"), []);
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.append("page", page);
      params.append("limit", pageSize);
      params.append("status", "failed");
      if (filters.marketplace) params.append("marketplace", filters.marketplace);
      if (filters.action) params.append("action", filters.action);
      const r = await axios.get(`${API}/marketplace-hub/logs?${params}`, auth);
      setLogs(r.data?.logs || []);
      setTotal(r.data?.total || 0);
      setSelected([]);
    } catch {
      toast.error("Kayıtlar yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    axios.get(`${API}/marketplace-hub/marketplaces`, auth)
      .then((r) => setMarketplaces(r.data?.marketplaces || []))
      .catch(() => {});
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize]);

  const toggle = (idx) =>
    setSelected((p) => (p.includes(idx) ? p.filter((x) => x !== idx) : [...p, idx]));
  const toggleAll = () =>
    setSelected(selected.length === logs.length ? [] : logs.map((_, i) => i));

  const retrySelected = async () => {
    if (selected.length === 0) {
      toast.error("Kayıt seçiniz");
      return;
    }
    setRetrying(true);
    let ok = 0, fail = 0, skipped = 0;
    for (const idx of selected) {
      const log = logs[idx];
      const url = retryEndpoint(log);
      if (!url) { skipped += 1; continue; }
      try {
        await axios.post(url, {}, auth);
        ok += 1;
      } catch { fail += 1; }
    }
    setRetrying(false);
    toast.success(`Yeniden denendi: ${ok} başarılı, ${fail} hata${skipped ? `, ${skipped} atlandı (desteklenmeyen)` : ""}`);
    load();
  };

  const marketplaceMeta = useMemo(() => {
    const m = {};
    marketplaces.forEach((x) => { m[x.key] = x; });
    return m;
  }, [marketplaces]);

  return (
    <div data-testid="failed-transfers-page">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <AlertTriangle size={20} className="text-red-500" />
            Aktarılamayan İşlemler
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Pazaryerine aktarılamamış ürün/sipariş/stok işlemleri — incele ve tek tıkla tekrar dene.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load}
            className="flex items-center gap-1 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
            <RefreshCw size={14} /> Yenile
          </button>
          <button
            onClick={retrySelected}
            disabled={selected.length === 0 || retrying}
            className="flex items-center gap-1 px-4 py-2 bg-orange-600 text-white rounded-lg text-sm hover:bg-orange-700 disabled:opacity-50"
            data-testid="failed-retry-selected"
          >
            <Play size={14} />
            {retrying ? "Deneniyor..." : `Seçilenleri Tekrar Aktar (${selected.length})`}
          </button>
        </div>
      </div>

      {/* Filtreler */}
      <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-4 mb-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div>
            <label className="block text-xs text-gray-600 mb-1">Pazaryeri</label>
            <select value={filters.marketplace}
              onChange={(e) => setFilters({ ...filters, marketplace: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white">
              <option value="">Tümü</option>
              {marketplaces.map((m) => (<option key={m.key} value={m.key}>{m.name}</option>))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Aktarım Türü</label>
            <select value={filters.action}
              onChange={(e) => setFilters({ ...filters, action: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm bg-white">
              <option value="">Tümü</option>
              {Object.entries(ACTION_LABELS).map(([k, v]) => (<option key={k} value={k}>{v}</option>))}
            </select>
          </div>
          <div className="md:col-span-2 flex items-end">
            <button onClick={() => { setPage(1); load(); }}
              className="bg-black text-white px-4 py-1.5 text-sm rounded-lg hover:bg-gray-800"
              data-testid="failed-apply-filters">
              Filtrele
            </button>
          </div>
        </div>
      </div>

      {/* Tablo */}
      <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
        <table className="admin-table admin-table-compact">
          <thead>
            <tr>
              <th className="w-10">
                <button onClick={toggleAll} className="p-1" data-testid="failed-select-all">
                  {selected.length === logs.length && logs.length > 0 ?
                    <CheckSquare size={16} className="text-orange-600" /> : <Square size={16} />}
                </button>
              </th>
              <th>Tarih</th>
              <th>Pazaryeri</th>
              <th>İşlem</th>
              <th>Ref</th>
              <th>Hata Mesajı</th>
              <th className="w-20">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="text-center py-8 text-sm text-gray-400">Yükleniyor...</td></tr>
            ) : logs.length === 0 ? (
              <tr><td colSpan={7} className="text-center py-10 text-sm text-gray-400">
                Başarısız kayıt bulunmuyor. 🎉
              </td></tr>
            ) : (
              logs.map((l, idx) => {
                const meta = marketplaceMeta[l.marketplace] || {};
                const canRetry = !!retryEndpoint(l);
                return (
                  <tr key={idx} data-testid={`failed-row-${idx}`}>
                    <td>
                      <button onClick={() => toggle(idx)} className="p-1">
                        {selected.includes(idx) ?
                          <CheckSquare size={16} className="text-orange-600" /> : <Square size={16} />}
                      </button>
                    </td>
                    <td className="text-xs text-gray-600 whitespace-nowrap">
                      {l.created_at ? new Date(l.created_at).toLocaleString("tr-TR") : "-"}
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1.5">
                        <span className="w-4 h-4 rounded-full flex items-center justify-center text-white text-[8px] font-black"
                              style={{ backgroundColor: meta.color || "#6b7280" }}>
                          {(meta.name || l.marketplace || "?").slice(0, 1).toUpperCase()}
                        </span>
                        <span className="text-sm font-medium">{meta.name || l.marketplace || "-"}</span>
                      </span>
                    </td>
                    <td className="text-xs font-mono text-gray-700">
                      {ACTION_LABELS[l.action] || l.action}
                    </td>
                    <td className="text-xs font-mono text-gray-600">{l.ref_id || "-"}</td>
                    <td className="text-xs text-red-700 max-w-md truncate" title={l.message}>
                      {l.message || "-"}
                    </td>
                    <td>
                      <button
                        onClick={async () => {
                          const url = retryEndpoint(l);
                          if (!url) { toast.error("Bu tür için tekrar desteklenmiyor"); return; }
                          try {
                            await axios.post(url, {}, auth);
                            toast.success("Tekrar denendi");
                            setTimeout(load, 600);
                          } catch {
                            toast.error("Tekrar başarısız");
                          }
                        }}
                        disabled={!canRetry}
                        className="p-1.5 hover:bg-orange-50 rounded text-orange-600 disabled:opacity-30 disabled:cursor-not-allowed"
                        title={canRetry ? "Tekrar Aktar" : "Bu tür için desteklenmiyor"}
                        data-testid={`failed-retry-${idx}`}
                      >
                        <Play size={14} />
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <Pagination
        page={page}
        total={total}
        pageSize={pageSize}
        onChange={setPage}
        onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
        pageSizeOptions={[25, 50, 100, 200]}
        variant="full"
      />
    </div>
  );
}
