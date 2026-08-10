/**
 * BulkPriceStock.jsx — Toplu Fiyat/Stok Güncelleme (Excel)
 *
 * 3 adımlı akış:
 *   1) Şablon indir (boş Excel)
 *   2) Doldurup yükle → önizleme (dry-run, DB değişmez)
 *   3) "Uygula" ile gerçek güncelleme
 *
 * Backend: /api/bulk-ops/price-stock/{template,preview,apply}
 */
import { useState, useMemo } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Upload, FileSpreadsheet, PlayCircle, CheckCircle2, XCircle } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function BulkPriceStock() {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [applyLoading, setApplyLoading] = useState(false);
  const [result, setResult] = useState(null);

  const token = useMemo(() => localStorage.getItem("token"), []);
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const downloadTemplate = () => {
    const url = `${API}/bulk-ops/price-stock/template`;
    // Auth header için axios ile indir
    axios.get(url, { ...auth, responseType: "blob" })
      .then((r) => {
        const blob = new Blob([r.data]);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "fiyat-stok-sablon.xlsx";
        a.click();
        URL.revokeObjectURL(a.href);
      })
      .catch(() => toast.error("Şablon indirilemedi"));
  };

  const runPreview = async () => {
    if (!file) { toast.error("Önce dosya seçin"); return; }
    setLoading(true); setPreview(null); setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await axios.post(`${API}/bulk-ops/price-stock/preview`, fd, auth);
      setPreview(r.data);
      toast.success("Önizleme hazır — incele ve 'Uygula' ile kaydet");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Önizleme başarısız");
    } finally { setLoading(false); }
  };

  const applyChanges = async () => {
    if (!file) return;
    if (!await window.appConfirm("Değişiklikler veritabanına uygulanacak. Devam?")) return;
    setApplyLoading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await axios.post(`${API}/bulk-ops/price-stock/apply`, fd, auth);
      setResult(r.data);
      toast.success(r.data.message);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Uygulama başarısız");
    } finally { setApplyLoading(false); }
  };

  return (
    <div data-testid="bulk-price-stock-page">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Toplu Fiyat / Stok Güncelleme</h1>
        <p className="text-sm text-gray-500 mt-1">
          Excel şablonunu indir, doldur ve yükle. Önce önizle, sonra uygula — DB sadece "Uygula"ya bastığında güncellenir.
        </p>
      </div>

      {/* 3 adım kartları */}
      <div className="grid md:grid-cols-3 gap-4 mb-6">
        <div className="bg-white border rounded-xl p-5">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-8 h-8 rounded-full bg-orange-100 text-orange-600 flex items-center justify-center font-black">1</div>
            <h3 className="font-semibold">Şablonu İndir</h3>
          </div>
          <p className="text-xs text-gray-500 mb-3">
            Kolonlar: stock_code · barcode · price · sale_price · stock · status
          </p>
          <button onClick={downloadTemplate}
            className="flex items-center gap-1 bg-black text-white px-3 py-2 rounded-lg text-sm hover:bg-gray-800"
            data-testid="bulk-download-template">
            <FileSpreadsheet size={14} /> Excel Şablon İndir
          </button>
        </div>

        <div className="bg-white border rounded-xl p-5">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-8 h-8 rounded-full bg-orange-100 text-orange-600 flex items-center justify-center font-black">2</div>
            <h3 className="font-semibold">Dosya Yükle & Önizle</h3>
          </div>
          <input type="file" accept=".xlsx"
            onChange={(e) => setFile(e.target.files[0])}
            className="w-full text-xs mb-2"
            data-testid="bulk-file-input" />
          <button onClick={runPreview} disabled={!file || loading}
            className="flex items-center gap-1 w-full justify-center px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
            data-testid="bulk-preview-btn">
            <Upload size={14} /> {loading ? "Okunuyor..." : "Önizle (dry-run)"}
          </button>
        </div>

        <div className="bg-white border rounded-xl p-5">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-8 h-8 rounded-full bg-green-100 text-green-600 flex items-center justify-center font-black">3</div>
            <h3 className="font-semibold">Uygula</h3>
          </div>
          <p className="text-xs text-gray-500 mb-3">
            {preview ? `${preview.total_rows - preview.not_found} satır güncellenmeye hazır` : "Önce önizle"}
          </p>
          <button onClick={applyChanges} disabled={!preview || applyLoading}
            className="flex items-center gap-1 w-full justify-center bg-green-600 text-white px-3 py-2 rounded-lg text-sm hover:bg-green-700 disabled:opacity-50"
            data-testid="bulk-apply-btn">
            <PlayCircle size={14} /> {applyLoading ? "Uygulanıyor..." : "Değişiklikleri Uygula"}
          </button>
        </div>
      </div>

      {/* Önizleme özet */}
      {preview && (
        <div className="bg-white border rounded-xl p-5 mb-4" data-testid="bulk-preview-summary">
          <h3 className="font-semibold mb-3">Önizleme Özeti</h3>
          <div className="grid grid-cols-4 gap-3 mb-4">
            <div className="bg-gray-50 rounded p-3"><div className="text-xs text-gray-500">Toplam Satır</div><div className="text-xl font-black">{preview.total_rows}</div></div>
            <div className="bg-green-50 rounded p-3"><div className="text-xs text-green-600">Stok Kodu Eşleşen</div><div className="text-xl font-black text-green-700">{preview.matched_by_stock_code}</div></div>
            <div className="bg-blue-50 rounded p-3"><div className="text-xs text-blue-600">Barkod Eşleşen</div><div className="text-xl font-black text-blue-700">{preview.matched_by_barcode}</div></div>
            <div className="bg-red-50 rounded p-3"><div className="text-xs text-red-600">Bulunamayan</div><div className="text-xl font-black text-red-700">{preview.not_found}</div></div>
          </div>

          <div className="max-h-96 overflow-y-auto border rounded">
            <table className="admin-table admin-table-compact">
              <thead><tr><th>Satır</th><th>Ürün</th><th>Ref</th><th>Değişiklikler</th><th>Durum</th></tr></thead>
              <tbody>
                {preview.preview.map((row, idx) => (
                  <tr key={idx}>
                    <td className="text-xs">#{row.row}</td>
                    <td className="text-xs">{row.product_name || "-"}</td>
                    <td className="text-xs font-mono">{row.ref || "-"}</td>
                    <td className="text-xs font-mono text-gray-600">
                      {row.updates ? JSON.stringify(row.updates) : "-"}
                    </td>
                    <td>
                      {row.error ? (
                        <span className="inline-flex items-center gap-1 text-[10px] text-red-700 bg-red-50 px-1.5 py-0.5 rounded">
                          <XCircle size={10} /> {row.error}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[10px] text-green-700 bg-green-50 px-1.5 py-0.5 rounded">
                          <CheckCircle2 size={10} /> Hazır
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Sonuç */}
      {result && (
        <div className="bg-green-50 border border-green-200 rounded-xl p-5" data-testid="bulk-apply-result">
          <h3 className="font-semibold text-green-900 flex items-center gap-2">
            <CheckCircle2 size={18} /> Güncelleme Tamamlandı
          </h3>
          <p className="text-sm text-green-800 mt-2">
            Güncellenen: <b>{result.applied}</b> · Bulunamayan: <b>{result.failed}</b> · Atlanan: <b>{result.skipped}</b>
          </p>
        </div>
      )}
    </div>
  );
}
