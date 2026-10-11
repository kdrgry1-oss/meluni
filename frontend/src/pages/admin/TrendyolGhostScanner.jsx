/**
 * TrendyolGhostScanner.jsx
 *
 * Üç kritik Trendyol denetim aracı tek sayfada:
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
 *
 *  (3) Sitede var, Trendyol'da yok:
 *     (2)'nin tersi. Sitede AKTİF olan ürün/bedenlerden Trendyol'da CANLI
 *     olmayanları (hiç yüklenmemiş / arşivli / onaysız / barkodsuz) listeler.
 *     Salt-okunur; Trendyol'a hiçbir yazma yapmaz.
 *     Endpoint: POST /api/integrations/trendyol/coverage-gaps
 */
import { useState } from "react";
import axios from "axios";
import { AlertTriangle, Ghost, RefreshCw, Trash2, Search, Database, CheckCircle2, Package } from "lucide-react";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

// coverage-gaps -> missing[].reason kodları
const GAP_REASON_TR = {
  yok: "Trendyol'da hiç yok",
  arsivli: "Trendyol'da arşivli",
  onaysiz: "Onay bekliyor / onaysız",
  barkodsuz: "Varyantın barkodu yok",
  barkod_uyusmuyor: "Barkod, Trendyol'da stok kodu olarak geçiyor",
};
const GAP_REASON_CLS = {
  yok: "bg-red-50 text-red-700",
  arsivli: "bg-gray-100 text-gray-700",
  onaysiz: "bg-amber-50 text-amber-700",
  barkodsuz: "bg-orange-50 text-orange-700",
  barkod_uyusmuyor: "bg-blue-50 text-blue-700",
};

function GapTable({ rows, testId }) {
  if (!rows || rows.length === 0) {
    return <p className="text-sm text-gray-500 py-3">Bu grupta kayıt yok. 👍</p>;
  }
  return (
    <div className="border rounded overflow-x-auto" data-testid={testId}>
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-left">
          <tr>
            <th className="p-2">Ürün</th>
            <th className="p-2">Stok Kodu</th>
            <th className="p-2">TY'de canlı beden</th>
            <th className="p-2">Eksik beden</th>
            <th className="p-2">Eksik varyantlar</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((g) => (
            <tr key={g.product_id} className="border-t align-top">
              <td className="p-2">
                <a
                  href={`/admin/urunler/${g.product_id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-purple-700 hover:underline"
                >
                  {g.name || g.product_id}
                </a>
              </td>
              <td className="p-2 font-mono text-xs">{g.stock_code || "—"}</td>
              <td className="p-2">{g.variants_live_on_trendyol}</td>
              <td className="p-2 font-semibold text-red-600">{g.missing_count}</td>
              <td className="p-2">
                <div className="flex flex-wrap gap-1">
                  {(g.missing || []).map((m, i) => (
                    <span
                      key={`${g.product_id}-${m.barcode || m.stock_code || i}`}
                      className={`px-2 py-0.5 rounded text-xs ${GAP_REASON_CLS[m.reason] || "bg-gray-100 text-gray-700"}`}
                      title={`${GAP_REASON_TR[m.reason] || m.reason} · barkod: ${m.barcode || "yok"} · stok: ${m.stock}`}
                    >
                      {m.size || "—"}
                      {m.color ? ` / ${m.color}` : ""} · {GAP_REASON_TR[m.reason] || m.reason} ({m.stock})
                    </span>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function TrendyolGhostScanner() {
  // Derin bağlantı: /admin/trendyol-hayalet?tab=gaps → doğrudan ilgili sekme açılır.
  const [tab, setTab] = useState(() => {
    try {
      const t = new URLSearchParams(window.location.search).get("tab");
      if (t === "gaps" || t === "ghosts" || t === "duplicates") return t;
    } catch {
      /* URLSearchParams yoksa varsayılana düş */
    }
    return "duplicates";
  }); // "duplicates" | "ghosts" | "gaps"

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

  // --- Sitede var, Trendyol'da yok (coverage gaps) ---
  const [gapRes, setGapRes] = useState(null);
  const [gapLoading, setGapLoading] = useState(false);
  const [gapOnlyStock, setGapOnlyStock] = useState(true);
  const [gapPageLimit, setGapPageLimit] = useState(60);

  const runCoverageScan = async () => {
    setGapLoading(true);
    try {
      const r = await axios.post(
        `${API}/integrations/trendyol/coverage-gaps`,
        { only_in_stock: gapOnlyStock, page_limit: gapPageLimit },
        auth()
      );
      if (r.data?.error) {
        toast.error(r.data.error);
        setGapRes(null);
        return;
      }
      setGapRes(r.data || null);
      toast.success(
        `${r.data?.active_products_scanned || 0} aktif ürün tarandı — ` +
          `${r.data?.fully_missing_products_count || 0} ürün hiç yok, ` +
          `${r.data?.partial_products_count || 0} üründe eksik beden var.`
      );
    } catch (e) {
      toast.error("Tarama hatası: " + (e.response?.data?.detail || e.message));
    } finally {
      setGapLoading(false);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto" data-testid="ghost-scanner-page">
      <div className="flex items-center gap-3 mb-6">
        <Ghost className="w-7 h-7 text-purple-600" />
        <div>
          <h1 className="text-2xl font-semibold">Trendyol Hayalet Ürün Tarayıcı</h1>
          <p className="text-sm text-gray-500">
            Trendyol'daki yetim kayıtları, DB barkod duplikatlarını ve sitede olup Trendyol'da olmayan bedenleri tespit eder.
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
        <button
          onClick={() => setTab("gaps")}
          data-testid="tab-gaps"
          className={`px-4 py-2 -mb-px border-b-2 text-sm flex items-center gap-2 ${tab === "gaps" ? "border-purple-600 text-purple-700" : "border-transparent text-gray-500"}`}
        >
          <Package className="w-4 h-4" /> Sitede var, Trendyol'da yok
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

      {tab === "gaps" && (
        <div data-testid="gaps-panel">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
            <p className="text-sm text-gray-600 max-w-3xl">
              <Package className="w-4 h-4 inline text-purple-600 mr-1" />
              Sitede <b>aktif</b> olan ürün/bedenlerden Trendyol'da <b>canlı olmayanları</b> listeler
              (hiç yüklenmemiş, arşivli, onaysız veya barkodsuz). Salt-okunur tarama — Trendyol'a
              hiçbir şey yazılmaz. Aktarmak için ürünü açıp <b>Trendyol'a Gönder</b>'i kullan.
            </p>
            <div className="flex items-center gap-3">
              <label className="text-sm flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={gapOnlyStock}
                  onChange={(e) => setGapOnlyStock(e.target.checked)}
                  data-testid="gaps-only-stock"
                />
                Sadece stoğu olanlar
              </label>
              <label className="text-sm flex items-center gap-1">
                Sayfa limiti
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={gapPageLimit}
                  onChange={(e) => setGapPageLimit(Number(e.target.value) || 60)}
                  className="w-16 border rounded px-2 py-1 text-sm"
                />
              </label>
              <button
                onClick={runCoverageScan}
                disabled={gapLoading}
                data-testid="btn-scan-gaps"
                className="px-3 py-1.5 bg-purple-600 text-white rounded text-sm flex items-center gap-1 disabled:opacity-50"
              >
                <Search className={`w-4 h-4 ${gapLoading ? "animate-pulse" : ""}`} />
                {gapLoading ? "Taranıyor…" : "Tara"}
              </button>
            </div>
          </div>

          {gapRes && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4" data-testid="gaps-summary">
                {[
                  ["Taranan aktif ürün", gapRes.active_products_scanned, "text-gray-800"],
                  ["Trendyol kaydı", gapRes.trendyol_products_scanned, "text-gray-800"],
                  ["Hiç yok (ürün)", gapRes.fully_missing_products_count, "text-red-600"],
                  ["Eksik bedenli ürün", gapRes.partial_products_count, "text-amber-600"],
                  ["Toplam eksik varyant", gapRes.total_missing_variants, "text-red-600"],
                ].map(([lbl, val, cls]) => (
                  <div key={lbl} className="border rounded p-3 bg-white">
                    <div className="text-xs text-gray-500">{lbl}</div>
                    <div className={`text-xl font-semibold ${cls}`}>{val ?? 0}</div>
                  </div>
                ))}
              </div>

              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-red-500" />
                Trendyol'da hiç canlı bedeni olmayan ürünler ({gapRes.fully_missing_products_count || 0})
              </h3>
              <div className="mb-6">
                <GapTable rows={gapRes.fully_missing} testId="gaps-fully-missing" />
              </div>

              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-amber-500" />
                Kısmen yüklü — bazı bedenleri eksik ({gapRes.partial_products_count || 0})
              </h3>
              <GapTable rows={gapRes.partial} testId="gaps-partial" />

              {(gapRes.fully_missing_products_count > 300 || gapRes.partial_products_count > 300) && (
                <p className="text-xs text-gray-500 mt-3">
                  Not: Her liste en fazla 300 ürün gösterir (eksik beden sayısına göre sıralı).
                </p>
              )}
            </>
          )}

          {!gapRes && !gapLoading && (
            <p className="text-sm text-gray-500 py-6">
              Taramayı başlatmak için <b>Tara</b>'ya bas. Trendyol'un tüm ürün kataloğu çekildiği için
              işlem 10-60 saniye sürebilir.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
