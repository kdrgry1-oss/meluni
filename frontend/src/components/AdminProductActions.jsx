import { useState } from "react";
import { Eye, EyeOff, Trash2, Loader2, Package } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * Ürün sayfasında yönetim şeridi — YALNIZ yönetim görür.
 *
 * Karttaki (AdminProductOverlay) ile aynı iki eylem, ama burada satır hâlinde:
 * ürün sayfasında görsele bindirmek yerine yazıyla anlatmak daha okunur.
 * Ayrıca toplam stok da burada gösteriliyor; beden bazlı adetler beden
 * düğümlerinin üzerinde rozet olarak duruyor.
 */
export default function AdminProductActions({ product, className = "" }) {
  const [aktif, setAktif] = useState(product?.is_active !== false);
  const [kaldirildi, setKaldirildi] = useState(false);
  const [bekle, setBekle] = useState("");
  const [hata, setHata] = useState("");

  const token = () => localStorage.getItem("token") || "";

  const toplamStok = (product?.variants || []).reduce(
    (t, v) => t + (Number(v.stock) || 0), 0) || Number(product?.stock) || 0;

  const toggle = async () => {
    setBekle("toggle");
    setHata("");
    try {
      const r = await fetch(`${API}/products/${product.id}/toggle-active`, {
        method: "POST", headers: { Authorization: `Bearer ${token()}` },
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Değiştirilemedi");
      setAktif(d.is_active);
    } catch (e) {
      setHata(e.message);
    } finally {
      setBekle("");
    }
  };

  const kaldir = async () => {
    if (!window.confirm(
      `"${product.name}" vitrinden kaldırılacak ve tedarikçi beslemesinden bir ` +
      `daha GELMEYECEK.\n\nDevam edilsin mi?`
    )) return;
    setBekle("block");
    setHata("");
    try {
      const r = await fetch(`${API}/products/${product.id}/block`, {
        method: "POST", headers: { Authorization: `Bearer ${token()}` },
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Kaldırılamadı");
      setKaldirildi(true);
    } catch (e) {
      setHata(e.message);
    } finally {
      setBekle("");
    }
  };

  return (
    <div className={`rounded-lg border border-amber-300 bg-amber-50/50 px-3 py-2 ${className}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide bg-black text-white px-1.5 py-0.5 rounded">
          yalnız yönetim
        </span>

        <span className="text-xs text-gray-700 inline-flex items-center gap-1">
          <Package className="w-3.5 h-3.5" />
          toplam stok: <b>{toplamStok}</b>
        </span>

        <span className={`text-xs font-semibold ${aktif ? "text-green-700" : "text-gray-500"}`}>
          {kaldirildi ? "kaldırıldı" : aktif ? "aktif" : "pasif"}
        </span>

        {!kaldirildi && (
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={toggle}
              disabled={!!bekle}
              className="px-2.5 py-1 rounded-lg border bg-white text-xs inline-flex items-center gap-1.5 hover:bg-gray-50 disabled:opacity-50"
            >
              {bekle === "toggle" ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : aktif ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              {aktif ? "Pasife al" : "Aktife al"}
            </button>
            <button
              type="button"
              onClick={kaldir}
              disabled={!!bekle}
              title="Vitrinden kaldır ve beslemenin bir daha getirmesini engelle"
              className="px-2.5 py-1 rounded-lg border border-red-200 bg-white text-xs text-red-600 inline-flex items-center gap-1.5 hover:bg-red-50 disabled:opacity-50"
            >
              {bekle === "block" ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : <Trash2 className="w-3.5 h-3.5" />}
              Kaldır
            </button>
          </div>
        )}
      </div>

      {kaldirildi && (
        <p className="text-[11px] text-red-700 mt-1">
          Ürün vitrinden kaldırıldı ve kara listeye alındı — besleme bir daha getirmeyecek.
        </p>
      )}
      {hata && <p className="text-[11px] text-red-700 mt-1">{hata}</p>}
    </div>
  );
}
