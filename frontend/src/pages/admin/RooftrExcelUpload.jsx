import { useState, useRef } from "react";
import { Upload, CheckCircle, AlertTriangle, FileSpreadsheet, RefreshCw, Plus, Layers } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function RooftrExcelUpload() {
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [fileName, setFileName] = useState("");
  const fileRef = useRef();

  const doUpload = async (file) => {
    if (!file) return;
    const lower = file.name.toLowerCase();
    if (!lower.endsWith(".xls") && !lower.endsWith(".xlsx")) {
      toast.error("Sadece .xls veya .xlsx dosyası yükleyin");
      return;
    }
    setImporting(true);
    setResult(null);
    setFileName(file.name);
    try {
      const token = localStorage.getItem("token");
      const fd = new FormData();
      fd.append("file", file);
      const res = await axios.post(`${API}/integrations/rooftr/products/upload-excel`, fd, {
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "multipart/form-data" },
        timeout: 180000,
      });
      setResult(res.data);
      toast.success(res.data.message || "İçe aktarım tamamlandı");
    } catch (err) {
      toast.error(err.response?.data?.detail || "İçe aktarım başarısız");
    } finally {
      setImporting(false);
    }
  };

  const handleFileChange = (e) => doUpload(e.target.files[0]);
  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    doUpload(e.dataTransfer.files[0]);
  };

  const stats = result?.stats || {};

  return (
    <div className="p-6 max-w-4xl mx-auto" data-testid="rooftr-excel-upload-page">
      <div className="mb-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <FileSpreadsheet className="text-green-600" size={26} /> Rooftr Excel Ürün Aktarımı
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          RooftrExport (.xls/.xlsx) dosyasını sürükleyip bırakın. URUNKARTIID bazında ürünler
          barkod, stok kodu, fiyat, indirimli fiyat, üye fiyatı, KDV, varyant ve kategori bilgileriyle
          tam senkronize edilir (eşleşen güncellenir, yeni olan eklenir).
        </p>
      </div>

      {/* Upload Area */}
      <div
        data-testid="rooftr-excel-dropzone"
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all mb-6 ${
          dragOver ? "border-black bg-gray-100" : "border-gray-300 hover:border-black hover:bg-gray-50"
        }`}
      >
        <Upload className="mx-auto mb-3 text-gray-400" size={44} />
        <p className="font-medium text-gray-700">Excel dosyasını buraya sürükleyin veya tıklayın</p>
        <p className="text-xs text-gray-400 mt-2">
          Beklenen sütunlar: URUNKARTIID, URUNID, STOKKODU, BARKOD, URUNADI, ACIKLAMA,
          BREADCRUMBKAT, TEDARIKCI, ALISFIYATI, SATISFIYATI, INDIRIMLIFIYAT, UYETIPIFIYAT1, KDVORANI, RENK, BEDEN
        </p>
        {importing && (
          <p className="mt-4 text-blue-600 font-medium animate-pulse flex items-center justify-center gap-2">
            <RefreshCw className="animate-spin" size={16} /> {fileName} işleniyor...
          </p>
        )}
        <input
          ref={fileRef}
          type="file"
          accept=".xls,.xlsx"
          className="hidden"
          data-testid="rooftr-excel-file-input"
          onChange={handleFileChange}
        />
      </div>

      {result && (
        <div className="bg-white rounded-xl border p-6" data-testid="rooftr-excel-result">
          <div className="flex items-center gap-2 mb-4 text-green-700 font-semibold">
            <CheckCircle size={20} /> {result.message}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard icon={<Layers size={18} />} label="Excel'deki Ürün" value={stats.parents_in_excel} color="gray" />
            <StatCard icon={<RefreshCw size={18} />} label="Güncellenen" value={stats.parents_updated_db} color="blue" />
            <StatCard icon={<Plus size={18} />} label="Yeni Eklenen" value={stats.parents_created_new} color="green" />
            <StatCard icon={<Layers size={18} />} label="Varyant" value={stats.variants_total} color="purple" />
          </div>

          {stats.errors?.length > 0 && (
            <div className="mt-5">
              <div className="flex items-center gap-2 text-amber-600 font-medium mb-2">
                <AlertTriangle size={16} /> {stats.errors.length} hata
              </div>
              <div className="max-h-48 overflow-y-auto bg-amber-50 border border-amber-200 rounded p-3 text-xs font-mono space-y-1">
                {stats.errors.map((e, i) => (
                  <div key={i} className="text-amber-800">{e}</div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatCard({ icon, label, value, color }) {
  const colors = {
    gray: "bg-gray-50 text-gray-700 border-gray-200",
    blue: "bg-blue-50 text-blue-700 border-blue-200",
    green: "bg-green-50 text-green-700 border-green-200",
    purple: "bg-purple-50 text-purple-700 border-purple-200",
  };
  return (
    <div className={`rounded-lg border p-4 ${colors[color]}`}>
      <div className="flex items-center gap-2 text-xs font-medium opacity-80">{icon} {label}</div>
      <div className="text-2xl font-bold mt-1">{value ?? 0}</div>
    </div>
  );
}
