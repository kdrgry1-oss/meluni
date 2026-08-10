import { useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { TrendingUp, RefreshCw, Clock } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * Pazaryeri Stok / Fiyat Güncelleme Paneli.
 * Tüm pazaryeri sekmelerinde görünür. Backend stok/fiyat gönderimi yalnız
 * Trendyol için mevcut olduğundan, diğer pazaryerlerinde panel "backend
 * entegrasyonu gerekiyor" şeffaf durumunda (buton pasif) gösterilir.
 * Yalnız stok adedi ve fiyat gönderilir — ürün açıklaması/görseli değişmez.
 *
 * Faz T2: Trendyol'da "stok kodu / barkod ile" hedefli güncelleme eklendi.
 * Backend inventory-sync artık barcodes/stock_codes payload'unu kabul ediyor;
 * payload boşken davranış eskisiyle aynı (tüm aktif).
 *
 * Görünüm: sade (stone) tema — nötr gri + tek koyu vurgu (stone-900).
 * İşlev birebir korunur; yalnız stil minimalist hale getirildi.
 */
const SUPPORTED = ["trendyol", "hepsiburada"];

export default function StockPriceUpdatePanel({ marketplace, auth }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [mode, setMode] = useState("all");           // "all" | "codes"
  const [codeType, setCodeType] = useState("barcode"); // "barcode" | "stock_code"
  const [codesText, setCodesText] = useState("");

  const mp = (marketplace || "").toUpperCase();
  const supported = SUPPORTED.includes(marketplace);
  // Kod-bazlı hedefleme şimdilik yalnız Trendyol backend'inde var (önce Trendyol).
  const codeTargeting = marketplace === "trendyol";

  const parseCodes = () =>
    codesText.split(/[\n,;\s]+/).map((s) => s.trim()).filter(Boolean);

  const send = async (payload, confirmMsg) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setLoading(true);
    setResult(null);
    const t = toast.loading(`Stok/fiyat ${mp}'a gönderiliyor...`);
    try {
      const r = await axios.post(`${API}/integrations/${marketplace}/products/inventory-sync`, payload, auth);
      setResult(r.data);
      toast.success(r.data?.message || "Stok/fiyat gönderildi", { id: t });
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Stok/fiyat gönderilemedi", { id: t });
    } finally {
      setLoading(false);
    }
  };

  const syncAll = () =>
    send({}, `Tüm aktif ürünlerin güncel stok ve fiyatı ${mp}'a gönderilecek. Devam edilsin mi?`);

  const syncCodes = () => {
    const codes = parseCodes();
    if (codes.length === 0) {
      toast.error("Önce barkod / stok kodu girin");
      return;
    }
    const payload = codeType === "barcode" ? { barcodes: codes } : { stock_codes: codes };
    send(payload, `${codes.length} ürünün stok ve fiyatı ${mp}'a gönderilecek. Devam edilsin mi?`);
  };

  if (!supported) {
    return (
      <div className="mb-4 border border-stone-200 bg-stone-50 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-1">
          <Clock size={16} className="text-stone-400" />
          <div className="font-bold text-stone-600 text-sm">Stok / Fiyat Güncelle — {mp}</div>
        </div>
        <p className="text-xs text-stone-500">
          {mp} için stok/fiyat güncelleme backend entegrasyonu henüz eklenmedi
          (şu an yalnız Trendyol aktif). Bu pazaryerinin ürün/stok API'si bağlandığında
          bu alan otomatik aktifleşecek.
        </p>
      </div>
    );
  }

  const segBtn = (active) =>
    `px-3 py-1.5 text-xs ${active ? "bg-stone-900 text-white" : "bg-white text-stone-600 hover:bg-stone-50"}`;

  return (
    <div className="mb-4 border border-stone-200 bg-white rounded-xl p-4">
      <div className="flex items-center gap-2 mb-1">
        <TrendingUp size={16} className="text-stone-700" />
        <div className="font-bold text-stone-800 text-sm">Stok / Fiyat Güncelle — {mp}</div>
      </div>
      <p className="text-xs text-stone-500 mb-3">
        Güncel <b className="text-stone-700">stok adedi ve fiyatını</b> {mp}'a gönderir (barkod eşleşmesiyle).
        Ürün açıklaması/görseli değişmez — yalnızca stok ve fiyat güncellenir.
      </p>

      {codeTargeting && (
        <div className="inline-flex rounded-lg border border-stone-300 overflow-hidden mb-3">
          <button onClick={() => setMode("all")} className={segBtn(mode === "all")}>Tüm aktif</button>
          <button onClick={() => setMode("codes")} className={`border-l border-stone-300 ${segBtn(mode === "codes")}`}>
            Stok kodu / barkod ile
          </button>
        </div>
      )}

      {codeTargeting && mode === "codes" && (
        <div className="mb-3">
          <div className="inline-flex rounded-lg border border-stone-300 overflow-hidden mb-2">
            <button onClick={() => setCodeType("barcode")} className={segBtn(codeType === "barcode")}>Barkod</button>
            <button onClick={() => setCodeType("stock_code")} className={`border-l border-stone-300 ${segBtn(codeType === "stock_code")}`}>
              Stok kodu
            </button>
          </div>
          <textarea
            value={codesText}
            onChange={(e) => setCodesText(e.target.value)}
            rows={4}
            placeholder={codeType === "barcode" ? "8680000000001\n8680000000002" : "FCT-1021-S\nFCT-1021-M"}
            className="w-full text-xs font-mono border border-stone-300 rounded-lg p-2 bg-white"
          />
          <div className="text-[11px] text-stone-500 mt-1">
            {parseCodes().length} kod girildi (satır, virgül veya boşlukla ayırabilirsiniz)
          </div>
        </div>
      )}

      <button
        onClick={codeTargeting && mode === "codes" ? syncCodes : syncAll}
        disabled={loading}
        className="inline-flex items-center gap-2 bg-stone-900 text-white text-sm px-4 py-2 rounded-lg hover:bg-stone-800 disabled:opacity-50"
      >
        <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        {loading
          ? "Gönderiliyor..."
          : codeTargeting && mode === "codes"
          ? "Seçili Kodları Gönder"
          : "Tüm Stok + Fiyatı Gönder"}
      </button>

      {result && (
        <div className="mt-3 text-xs text-stone-700 bg-stone-50 border border-stone-200 rounded-lg p-2">
          {result.message || (result.success ? "Gönderildi" : JSON.stringify(result))}
          {result.batch_id && <span className="ml-2 font-mono text-stone-500">Batch: {result.batch_id}</span>}
          {typeof result.updated === "number" && <span className="ml-2 text-stone-500">({result.updated} ürün)</span>}
        </div>
      )}
    </div>
  );
}
