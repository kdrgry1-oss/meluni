import { useState } from "react";
import axios from "axios";
import { Sparkles, Upload, X, Plus, AlertTriangle } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const BACKEND_ORIGIN = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/api\/?$/, "");

/**
 * Ürün kartı içinde görsel üretimi — görsellerin hemen altında durur.
 *
 * Ürünün KENDİ görselleri referans olarak otomatik gönderilir; kullanıcı yalnız
 * yeni ORTAM referansını (stüdyo, zemin, ışık) yükler. Böylece "bu ürünü, bu
 * ortamda, beş açıdan çek" demiş oluyoruz.
 *
 * Üretilen görseller ürüne KENDİLİĞİNDEN eklenmez: önce küçük resim olarak
 * gösterilir, beğenilen tek tek "Ürüne ekle" ile forma alınır. Üretim ücretli
 * ve beğenilmeyen bir sonucun ürün görselini değiştirmesi geri alması zor.
 */
export default function ProductAiImages({ productId, images = [], onAddImage }) {
  const [ortamUrl, setOrtamUrl] = useState("");
  const [yukleniyor, setYukleniyor] = useState(false);
  const [uretiliyor, setUretiliyor] = useState(false);
  const [sonuc, setSonuc] = useState(null);
  const [hata, setHata] = useState("");
  const [eklenen, setEklenen] = useState([]);

  const token = () => localStorage.getItem("token") || "";

  const ortamYukle = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setYukleniyor(true);
    setHata("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await axios.post(`${API}/upload/image`, fd, {
        headers: { Authorization: `Bearer ${token()}`, "Content-Type": "multipart/form-data" },
      });
      const url = res.data?.url;
      if (!url) throw new Error("Sunucu adres döndürmedi");
      setOrtamUrl(url.startsWith("http") ? url : `${BACKEND_ORIGIN}${url}`);
    } catch (err) {
      setHata(err.response?.data?.detail || err.message);
    } finally {
      setYukleniyor(false);
      e.target.value = "";   // aynı dosya tekrar seçilebilsin
    }
  };

  const uret = async () => {
    setUretiliyor(true);
    setHata("");
    setSonuc(null);
    setEklenen([]);
    try {
      const r = await fetch(`${API}/ai-images/products/${productId}/regenerate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        // attach GÖNDERİLMİYOR: ürüne ekleme kararı burada, tek tek verilir.
        body: JSON.stringify({ reference_url: ortamUrl || undefined }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Üretim başarısız");
      setSonuc(d);
    } catch (e) {
      setHata(e.message);
    } finally {
      setUretiliyor(false);
    }
  };

  if (!productId) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 p-4 text-sm text-gray-500">
        <div className="flex items-center gap-2 font-medium text-gray-700 mb-1">
          <Sparkles className="w-4 h-4" /> Görsel Üretimi
        </div>
        Ürünü önce kaydedin; görsel üretimi kayıtlı ürünün görsellerini referans alır.
      </div>
    );
  }

  const uretilen = Object.entries(sonuc?.generated || {});
  const hatalar = Object.entries(sonuc?.errors || {});

  return (
    <div className="rounded-xl border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-1">
        <Sparkles className="w-4 h-4" />
        <h4 className="font-semibold text-sm">Görsel Üretimi (Yapay Zekâ)</h4>
        <span className="text-[10px] uppercase tracking-wide bg-black text-white px-1.5 py-0.5 rounded">
          yalnız yönetim
        </span>
      </div>
      <p className="text-xs text-gray-500 mb-3">
        Ürünün yukarıdaki <b>{images.length}</b> görseli referans olarak
        kendiliğinden gönderilir. Siz yalnız yeni <b>ortam referansını</b> yükleyin
        (stüdyo, zemin, ışık); ürün beş açıdan o ortamda üretilir.
      </p>

      {images.length === 0 && (
        <div className="mb-3 flex gap-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          Üründe hiç görsel yok. Model ürünü tanıyamaz — önce ürün görseli ekleyin.
        </div>
      )}

      <div className="flex flex-wrap items-start gap-3">
        {/* Ortam referansı */}
        <div>
          <div className="text-xs font-medium text-gray-600 mb-1">Ortam referansı</div>
          {ortamUrl ? (
            <div className="relative w-28">
              <img src={ortamUrl} alt="ortam referansı" className="w-28 h-36 object-cover rounded-lg border" />
              <button
                type="button"
                onClick={() => setOrtamUrl("")}
                className="absolute -top-2 -right-2 bg-white border rounded-full p-1 shadow"
                title="Kaldır"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          ) : (
            <label className="w-28 h-36 rounded-lg border-2 border-dashed border-gray-300 flex flex-col items-center justify-center cursor-pointer hover:border-gray-400 text-gray-500">
              <Upload className="w-5 h-5 mb-1" />
              <span className="text-[11px]">{yukleniyor ? "Yükleniyor…" : "Görsel seç"}</span>
              <input type="file" accept="image/*" className="hidden" onChange={ortamYukle} />
            </label>
          )}
          <p className="text-[11px] text-gray-400 mt-1 w-28">
            Boş bırakırsanız mevcut ortam korunur.
          </p>
        </div>

        <div className="pt-5">
          <button
            type="button"
            onClick={uret}
            disabled={uretiliyor || images.length === 0}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <Sparkles className="w-4 h-4" />
            {uretiliyor ? "Üretiliyor… (birkaç dakika)" : "5 Görseli Üret"}
          </button>
          <p className="text-[11px] text-gray-400 mt-1">
            Ön · Sol · Sağ · Arka · Detay
          </p>
        </div>
      </div>

      {hata && (
        <div className="mt-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-800">
          {hata}
        </div>
      )}

      {sonuc?.warning && (
        <div className="mt-3 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900">
          {sonuc.warning}
        </div>
      )}

      {hatalar.length > 0 && (
        <div className="mt-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-800">
          {hatalar.map(([pose, err]) => (
            <div key={pose}>
              <b>{sonuc.labels?.[pose] || pose}:</b> {String(err)}
            </div>
          ))}
        </div>
      )}

      {uretilen.length > 0 && (
        <div className="mt-4">
          <div className="text-xs text-gray-600 mb-2">
            {uretilen.length} görsel üretildi. Beğendiklerinizi ürüne ekleyin —
            eklemedikleriniz ürüne işlenmez.
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {uretilen.map(([pose, url]) => {
              const zatenEklendi = eklenen.includes(url);
              return (
                <div key={pose}>
                  <img src={url} alt={sonuc.labels?.[pose] || pose}
                       className="w-full aspect-[3/4] object-cover rounded-lg border" />
                  <div className="text-[11px] text-center text-gray-600 mt-1">
                    {sonuc.labels?.[pose] || pose}
                  </div>
                  <button
                    type="button"
                    disabled={zatenEklendi}
                    onClick={() => {
                      onAddImage?.(url);
                      setEklenen((p) => [...p, url]);
                    }}
                    className={`mt-1 w-full text-[11px] rounded-lg py-1 inline-flex items-center justify-center gap-1 border ${
                      zatenEklendi
                        ? "bg-green-50 text-green-700 border-green-200"
                        : "hover:bg-gray-50"
                    }`}
                  >
                    {zatenEklendi ? "eklendi" : (<><Plus className="w-3 h-3" /> Ürüne ekle</>)}
                  </button>
                </div>
              );
            })}
          </div>
          {/* Form kaydedilmeden görsel ürüne yazılmaz — kullanıcı bunu bilmeli. */}
          <p className="text-[11px] text-gray-500 mt-2">
            Eklenenler forma alınır; kalıcı olması için ürünü <b>kaydedin</b>.
          </p>
        </div>
      )}
    </div>
  );
}
