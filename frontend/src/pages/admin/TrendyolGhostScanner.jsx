/**
 * TrendyolGhostScanner.jsx
 *
 * İki kritik temizlik aracı tek sayfada:
 *
 *  (1) DB Barkod Duplikatları:
 *     Aynı barkodun birden fazla varyanta atandığı kayıtları listeler.
 *     Bu Trendyol push'ları kronik olarak bloklar.
 *     Endpoint: GET /api/integrations/trendyol/barcode-duplicates
 *
 *  (2) Trendyol Hayalet Ürün Tarayıcı:
 *     Trendyol panelinde olan ama lokal DB'de olmayan barkodları tarar.
 *     Bunlar genelde eski yanlış push'lardan kalan yetim ürünlerdir ve
 *     duplicate çakışmalara sebep olur. Tek tık ile arşivlenir.
 *     Endpoint: POST /api/integrations/trendyol/ghost-scanner
 *               POST /api/integrations/trendyol/archive-barcodes
 */
import { useState } from "react";
import axios from "axios";
import { AlertTriangle, Ghost, RefreshCw, Trash2, Search, Database, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

export default function TrendyolGhostScanner() {
  const [tab, setTab] = useState("duplicates"); // "duplicates" | "ghosts"

  // --- DB Duplicates ---
  const [dupes, setDupes] = useState([]);
  const [dupLoading, setDupLoading] = useState(false);

  const loadDuplicates = async () => {
    setDupLoading(true);
    try {
      const r = await axios.get(`${API}/integrations/trendyol/barcode-duplicates`, auth());
      setDupes(r.data?.duplicates || []);
      toast.success(`${r.data?.total || 0} duplicate barkod bulundu.`);
    } catch (e) {
      toast.error("Liste yüklenemedi: " + (e.response?.data?.detail || e.message));
    } finally {
      setDupLoading(false);
    }
  };

  // --- Ghost Scanner ---
  const [ghosts, setGhosts] = useState([]);
  const [ghostStats, setGhostStats] = useState({ scanned: 0, matched: 0 });
  const [scanning, setScanning] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [includeArchived, setIncludeArchived] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [pageLimit, setPageLimit] = useState(30);

  const runGhostScan = async () => {
    setScanning(true);
    setSelected(new Set());
    try {
      const r = await axios.post(
        `${API}/integrations/trendyol/ghost-scanner`,
        { only_unmatched: true, include_archived: includeArchived, page_limit: pageLimit },
        auth()
      );
      setGhosts(r.data?.ghosts || []);
      setGhostStats({ scanned: r.data?.scanned || 0, matched: r.data?.matched_in_db || 0 });
      toast.success(`Trendyol taraması: ${r.data?.scanned} ürün tarandı, ${r.data?.ghosts_count} hayalet bulundu.`);
    } catch (e) {
      toast.error("Tarama hatası: " + (e.response?.data?.detail || e.message));
    } finally {
      setScanning(false);
    }
  };

  const toggleAll = () => {
    if (selected.size === ghosts.length) setSelected(new Set());
    else setSelected(new Set(ghosts.map((g) => g.barcode)));
  };

  const archiveSelected = async () => {
    if (selected.size === 0) {
      toast.warning("Önce arşivlenecek hayalet barkodları seç.");
      return;
    }
    if (!window.confirm(`${selected.size} hayalet barkod Trendyol'da ARŞİVLENECEK. Onaylıyor musun?`)) return;
    setArchiving(true);
    try {
      const r = await axios.post(
        `${API}/integrations/trendyol/archive-barcodes`,
        { barcodes: Array.from(selected) },
        auth()
      );
      toast.success(`Arşiv batch'i gönderildi: ${r.data?.batchRequestId || "—"}. Trendyol işliyor…`);
      // Refresh list (Trendyol asenkron işliyor; arşivlenen item'lar archived=true olur)
      setTimeout(runGhostScan, 4000);
    } catch (e) {
      toast.error("Arşiv hatası: " + (e.response?.data?.detail || e.message));
    } finally {
      setArchiving(false);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto" data-testid="ghost-scanner-page">
      <div className="flex items-center gap-3 mb-6">
        <Ghost className="w-7 h-7 text-purple-600" />
        <div>
          <h1 className="text-2xl font-semibold">Trendyol Hayalet Ürün Tarayıcı</h1>
          <p className="text-sm text-gray-500">
            Trendyol'daki yetim/eski kayıtları ve DB'deki barkod duplikatlarını tespit edip temizler.
          </p>
        </div>
      </div>

      {/* Tab */}
      <div className="flex gap-2 mb-4 border-b">
        <button
          onClick={() => setTab("duplicates")}
          data-testid="tab-duplicates"
          className={`px-4 py-2 -mb-px border-b-2 text-sm flex items-center gap-2 ${tab === "duplicates" ? "border-purple-600 text-purple-700" : "border-transparent text-gray-500"}`}
        >
          <Database className="w-4 h-4" /> DB Barkod Duplikatları
        </button>
        <button
          onClick={() => setTab("ghosts")}
          data-testid="tab-ghosts"
          className={`px-4 py-2 -mb-px border-b-2 text-sm flex items-center gap-2 ${tab === "ghosts" ? "border-purple-600 text-purple-700" : "border-transparent text-gray-500"}`}
        >
          <Ghost className="w-4 h-4" /> Trendyol Hayalet Ürünler
        </button>
      </div>

      {tab === "duplicates" && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm text-gray-600">
              <AlertTriangle className="w-4 h-4 inline text-amber-500 mr-1" />
              Aynı barkodun 2+ varyantta kullanılması Trendyol push'larını bloklar. Düzeltmek için
              <b> Barkod Sorunları</b> sayfasını kullan.
            </p>
            <button
              onClick={loadDuplicates}
              disabled={dupLoading}
              data-testid="btn-load-duplicates"
              className="px-3 py-1.5 bg-purple-600 text-white rounded text-sm flex items-center gap-1 disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${dupLoading ? "animate-spin" : ""}`} />
              {dupLoading ? "Yükleniyor…" : "Tara"}
            </button>
          </div>

          {dupes.length === 0 && !dupLoading && (
            <div className="text-center py-12 text-gray-400">
              <CheckCircle2 className="w-12 h-12 mx-auto mb-2" />
              Henüz tarama yapılmadı. "Tara" butonuna bas.
            </div>
          )}

          {dupes.length > 0 && (
            <div className="border rounded overflow-hidden">
              <table className="w-full text-sm" data-testid="duplicates-table">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left p-2">Barkod</th>
                    <th className="text-left p-2">Atama Sayısı</th>
                    <th className="text-left p-2">Atandığı Varyantlar</th>
                  </tr>
                </thead>
                <tbody>
                  {dupes.map((d) => (
                    <tr key={d.barcode} className="border-t">
                      <td className="p-2 font-mono">{d.barcode}</td>
                      <td className="p-2">
                        <span className="bg-red-100 text-red-700 px-2 py-0.5 rounded">{d.count}</span>
                      </td>
                      <td className="p-2 text-xs">
                        {d.assignments.slice(0, 5).map((a, i) => (
                          <div key={i} className="text-gray-700">
                            <span className="font-mono text-purple-700">{a.stock_code}</span> ·{" "}
                            {a.name?.slice(0, 50)} · {a.variant_size || "?"}/{a.variant_color || "?"}
                            {!a.is_active && <span className="ml-1 text-gray-400">(pasif)</span>}
                          </div>
                        ))}
                        {d.assignments.length > 5 && (
                          <div className="text-gray-400">+ {d.assignments.length - 5} daha…</div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === "ghosts" && (
        <div>
          <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
            <div className="flex items-center gap-3 text-sm">
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  data-testid="input-include-archived"
                  checked={includeArchived}
                  onChange={(e) => setIncludeArchived(e.target.checked)}
                />
                Arşivlenmiş olanları da göster
              </label>
              <label className="flex items-center gap-1">
                Sayfa limiti:
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={pageLimit}
                  data-testid="input-page-limit"
                  onChange={(e) => setPageLimit(parseInt(e.target.value || "30", 10))}
                  className="w-16 border rounded px-2 py-0.5"
                />
                <span className="text-gray-400">(×200 ürün)</span>
              </label>
            </div>
            <div className="flex gap-2">
              <button
                onClick={runGhostScan}
                disabled={scanning}
                data-testid="btn-run-ghost-scan"
                className="px-3 py-1.5 bg-purple-600 text-white rounded text-sm flex items-center gap-1 disabled:opacity-50"
              >
                <Search className={`w-4 h-4 ${scanning ? "animate-spin" : ""}`} />
                {scanning ? "Taranıyor…" : "Trendyol'u Tara"}
              </button>
              <button
                onClick={archiveSelected}
                disabled={archiving || selected.size === 0}
                data-testid="btn-archive-selected"
                className="px-3 py-1.5 bg-red-600 text-white rounded text-sm flex items-center gap-1 disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" />
                {archiving ? "Arşivleniyor…" : `Seçileni Arşivle (${selected.size})`}
              </button>
            </div>
          </div>

          {ghostStats.scanned > 0 && (
            <div className="mb-3 text-sm text-gray-600 bg-gray-50 border rounded p-2">
              Trendyol'da toplam <b>{ghostStats.scanned}</b> ürün tarandı. <b>{ghostStats.matched}</b> tanesi
              DB ile eşleşti. <b>{ghosts.length}</b> tanesi <span className="text-red-600 font-medium">HAYALET</span>{" "}
              (DB'de yok).
            </div>
          )}

          {ghosts.length === 0 && !scanning && (
            <div className="text-center py-12 text-gray-400">
              <Ghost className="w-12 h-12 mx-auto mb-2" />
              Tarama yap; Trendyol'da olup DB'de olmayan ürünler burada listelenecek.
            </div>
          )}

          {ghosts.length > 0 && (
            <div className="border rounded overflow-hidden">
              <table className="w-full text-sm" data-testid="ghosts-table">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left p-2 w-8">
                      <input
                        type="checkbox"
                        data-testid="checkbox-select-all"
                        checked={selected.size > 0 && selected.size === ghosts.length}
                        onChange={toggleAll}
                      />
                    </th>
                    <th className="text-left p-2">Barkod</th>
                    <th className="text-left p-2">Stok Kodu</th>
                    <th className="text-left p-2">Başlık</th>
                    <th className="text-left p-2">Durum</th>
                    <th className="text-left p-2">Stok</th>
                    <th className="text-left p-2">Fiyat</th>
                  </tr>
                </thead>
                <tbody>
                  {ghosts.map((g) => (
                    <tr key={g.barcode} className="border-t hover:bg-gray-50">
                      <td className="p-2">
                        <input
                          type="checkbox"
                          data-testid={`checkbox-ghost-${g.barcode}`}
                          checked={selected.has(g.barcode)}
                          onChange={() => {
                            const ns = new Set(selected);
                            if (ns.has(g.barcode)) ns.delete(g.barcode);
                            else ns.add(g.barcode);
                            setSelected(ns);
                          }}
                        />
                      </td>
                      <td className="p-2 font-mono">{g.barcode}</td>
                      <td className="p-2 font-mono text-purple-700">{g.stockCode}</td>
                      <td className="p-2 text-xs">{g.title?.slice(0, 60)}</td>
                      <td className="p-2 text-xs">
                        {g.approved && <span className="text-green-700">✓</span>}
                        {g.archived && <span className="text-gray-500"> ARŞ</span>}
                        {!g.onSale && <span className="text-amber-600"> pasif</span>}
                      </td>
                      <td className="p-2">{g.quantity}</td>
                      <td className="p-2">{g.salePrice}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
