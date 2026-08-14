import { useState } from "react";
import { Eye, EyeOff, Trash2, Loader2 } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * Vitrinde gezerken ürünü pasife alma / kaldırma — YALNIZ yönetim görür.
 *
 * Neden vitrinde: katalog 4.700 ürün. Hangi ürünün satışa uygun olmadığı çoğu
 * zaman panelde tabloya bakarken değil, müşterinin gördüğü kartı görünce
 * anlaşılıyor. Karar verilen yerde uygulanabilsin diye düğmeler buraya kondu.
 *
 * İki eylem, farklı ağırlıkta:
 *   • Pasife al  — vitrinden kalkar, kayıt durur, geri alınabilir.
 *   • Kaldır     — vitrinden kalkar VE tedarikçi kara listesine yazılır;
 *                  besleme o ürünü bir daha hiç açmaz. Onay ister.
 *
 * Silme kaydı YOK ETMEZ: geçmiş siparişler ürüne referans veriyor olabilir.
 */
export default function AdminProductOverlay({ product, onChanged }) {
  const [aktif, setAktif] = useState(product?.is_active !== false);
  const [kaldirildi, setKaldirildi] = useState(false);
  const [bekle, setBekle] = useState("");
  const [hata, setHata] = useState("");

  const token = () => localStorage.getItem("token") || "";
  const dur = (e) => {
    // Kart bir <Link> içinde; tıklama ürün sayfasını açmasın.
    e.preventDefault();
    e.stopPropagation();
  };

  const toggle = async (e) => {
    dur(e);
    setBekle("toggle");
    setHata("");
    try {
      const r = await fetch(`${API}/products/${product.id}/toggle-active`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Değiştirilemedi");
      setAktif(d.is_active);
      onChanged?.({ id: product.id, is_active: d.is_active });
    } catch (err) {
      setHata(err.message);
    } finally {
      setBekle("");
    }
  };

  const kaldir = async (e) => {
    dur(e);
    // Kara liste kalıcı sonuç doğurur; onaysız yapılmaz.
    if (!window.confirm(
      `"${product.name}" vitrinden kaldırılacak ve tedarikçi beslemesinden bir ` +
      `daha GELMEYECEK.\n\nBu ürünü kalıcı olarak listeden çıkarmak istiyor musunuz?`
    )) return;
    setBekle("block");
    setHata("");
    try {
      const r = await fetch(`${API}/products/${product.id}/block`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Kaldırılamadı");
      setKaldirildi(true);
      onChanged?.({ id: product.id, removed: true });
    } catch (err) {
      setHata(err.message);
    } finally {
      setBekle("");
    }
  };

  if (kaldirildi) {
    return (
      <div className="absolute inset-0 z-30 bg-white/90 flex flex-col items-center justify-center text-center px-3">
        <Trash2 className="w-5 h-5 text-red-600 mb-1" />
        <div className="text-xs font-semibold text-red-700">Kaldırıldı</div>
        <div className="text-[11px] text-gray-500 mt-0.5">
          Beslemeden bir daha gelmeyecek
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Pasif ürün vitrinde müşteriye zaten görünmez; yönetim için kartın
          durumu okunabilir olsun diye üstü soluklaştırılıp etiketleniyor. */}
      {!aktif && (
        <div className="absolute inset-0 z-20 bg-white/60 pointer-events-none flex items-start justify-center pt-10">
          <span className="text-[11px] font-bold uppercase tracking-wide bg-gray-900 text-white px-2 py-0.5 rounded">
            pasif
          </span>
        </div>
      )}

      {/* Sağ üst: kaydet (favori) düğmesi top-3'te duruyor; yönetim
          düğmeleri onun hemen altına, aynı hizaya diziliyor. */}
      <div className="absolute top-11 right-3 z-30 flex flex-col gap-1.5">
        <button
          type="button"
          onClick={toggle}
          disabled={!!bekle}
          title={aktif ? "Pasife al (vitrinden kaldır)" : "Aktife al"}
          className={`w-8 h-8 rounded-full shadow border flex items-center justify-center backdrop-blur transition-colors disabled:opacity-50 ${
            aktif
              ? "bg-white/90 text-gray-700 hover:text-black border-gray-200"
              : "bg-gray-900 text-white border-gray-900"
          }`}
        >
          {bekle === "toggle" ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : aktif ? (
            <Eye className="w-4 h-4" />
          ) : (
            <EyeOff className="w-4 h-4" />
          )}
        </button>

        <button
          type="button"
          onClick={kaldir}
          disabled={!!bekle}
          title="Kaldır — besleme bir daha getirmesin"
          className="w-8 h-8 rounded-full shadow border border-gray-200 bg-white/90 text-gray-500 hover:text-red-600 hover:border-red-200 flex items-center justify-center backdrop-blur transition-colors disabled:opacity-50"
        >
          {bekle === "block" ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Trash2 className="w-4 h-4" />
          )}
        </button>

        {hata && (
          <div className="text-[10px] text-red-700 bg-white/95 rounded px-1.5 py-1 max-w-[9rem] shadow text-right">
            {hata}
          </div>
        )}
      </div>
    </>
  );
}
