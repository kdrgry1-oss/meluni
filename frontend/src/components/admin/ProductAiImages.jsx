import { useState } from "react";
import axios from "axios";
import { Sparkles, Upload, X, Plus, AlertTriangle, Wand2, Check, Ruler, Search } from "lucide-react";

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
export default function ProductAiImages({ productId, images = [], onAddImage, onReplaceImages }) {
  const [ortamUrl, setOrtamUrl] = useState("");
  const [yukleniyor, setYukleniyor] = useState(false);
  const [uretiliyor, setUretiliyor] = useState(false);
  const [sonuc, setSonuc] = useState(null);
  const [hata, setHata] = useState("");
  const [eklenen, setEklenen] = useState([]);
  // Kalite iyileştirme ayrı bir iş: mevcut görselleri Gemini ile netleştirip
  // 1200x1800 üretir. Üretimden farklı olarak yeni poz oluşturmaz.
  const [hdBekle, setHdBekle] = useState(false);
  const [hdSonuc, setHdSonuc] = useState(null);
  // Ölçü tablosu ayrı ele alınır: ürün fotoğrafı değil, METİN taşıyan bir
  // belge. Tarama hangi görselin tablo olduğunu söyler, dönüşüm yalnız ona
  // uygulanır.
  const [tarama, setTarama] = useState(null);
  const [taraBekle, setTaraBekle] = useState(false);
  const [ceviriBekle, setCeviriBekle] = useState(-1);

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

  const iyilestir = async (replace) => {
    if (replace && !window.confirm(
      "Ürünün mevcut görselleri iyileştirilmiş 1200x1800 sürümleriyle " +
      "DEĞİŞTİRİLECEK.\n\nDevam edilsin mi?"
    )) return;
    setHdBekle(true);
    setHata("");
    setHdSonuc(null);
    try {
      const r = await fetch(`${API}/ai-images/products/${productId}/enhance`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({ replace: !!replace }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "İyileştirme başarısız");
      setHdSonuc(d);
      // KRİTİK: sunucu ürünü güncelledi ama AÇIK FORM hâlâ eski adresleri
      // tutuyor. "Değişiklikleri Kaydet"e basıldığında o eski liste geri
      // yazılıyor ve iyileştirme boşa gidiyordu. Formu da tazeliyoruz.
      if (replace && d.results?.length) {
        const yeni = [...images];
        for (const r2 of d.results) {
          if (r2.url && r2.index >= 0 && r2.index < yeni.length) yeni[r2.index] = r2.url;
        }
        onReplaceImages?.(yeni);
      }
    } catch (e) {
      setHata(e.message);
    } finally {
      setHdBekle(false);
    }
  };

  const tara = async () => {
    setTaraBekle(true);
    setHata("");
    setTarama(null);
    try {
      const r = await fetch(`${API}/ai-images/products/${productId}/scan`, {
        headers: { Authorization: `Bearer ${token()}` },
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Tarama başarısız");
      setTarama(d);
    } catch (e) {
      setHata(e.message);
    } finally {
      setTaraBekle(false);
    }
  };

  const olcuCevir = async (index) => {
    if (!window.confirm(
      "Bu ölçü tablosu Türkçeleştirilip ürünün görseliyle DEĞİŞTİRİLECEK.\n\n" +
      "Sonrasında tablodaki ölçüleri aslıyla karşılaştırın — sayı hatası " +
      "müşteriye yanlış beden aldırır.\n\nDevam edilsin mi?"
    )) return;
    setCeviriBekle(index);
    setHata("");
    try {
      const r = await fetch(`${API}/ai-images/products/${productId}/size-chart`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({ index, replace: true }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Çeviri başarısız");
      const yeni = [...images];
      if (index < yeni.length) yeni[index] = d.url;
      onReplaceImages?.(yeni);
      setTarama((t) => t && {
        ...t,
        images: t.images.map((it) =>
          it.index === index ? { ...it, url: d.url, cevrildi: true } : it),
      });
    } catch (e) {
      setHata(e.message);
    } finally {
      setCeviriBekle(-1);
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

      {/* ── Ölçü tablosu ── */}
      <div className="mt-4 pt-4 border-t">
        <div className="flex items-center gap-2 mb-1">
          <Ruler className="w-4 h-4" />
          <h5 className="font-semibold text-sm">Ölçü Tablosu</h5>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          Tedarikçi görsellerinin arasında beden tablosu da geliyor ve çoğu
          İngilizce. Tarama hangi görselin tablo olduğunu bulur; çeviri
          <b> yalnız o görsele</b> uygulanır, ürün fotoğraflarına dokunmaz.
        </p>

        <button
          type="button"
          onClick={tara}
          disabled={taraBekle || images.length === 0}
          className="px-3 py-1.5 rounded-lg border text-sm inline-flex items-center gap-1.5 hover:bg-gray-50 disabled:opacity-50"
        >
          <Search className="w-4 h-4" />
          {taraBekle ? "Taranıyor…" : "Ölçü tablosu ara"}
        </button>

        {tarama && (
          <div className="mt-3">
            <div className="text-xs text-gray-600 mb-2">
              {tarama.size_chart_count > 0
                ? `${tarama.size_chart_count} ölçü tablosu bulundu.`
                : "Ölçü tablosu bulunamadı — tüm görseller ürün fotoğrafı."}
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {(tarama.images || []).map((it) => (
                <div key={it.index}>
                  <img src={it.url} alt={`görsel ${it.index + 1}`}
                       className={`w-full aspect-[2/3] object-cover rounded-lg border ${
                         it.tur === "olcu" ? "border-blue-400 ring-1 ring-blue-300" : ""
                       }`} />
                  {it.tur === "olcu" ? (
                    it.cevrildi ? (
                      <div className="mt-1 text-[10px] text-center text-green-700">
                        Türkçeleştirildi
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => olcuCevir(it.index)}
                        disabled={ceviriBekle === it.index}
                        className="mt-1 w-full text-[10px] rounded-lg py-1 bg-blue-600 text-white disabled:opacity-50"
                      >
                        {ceviriBekle === it.index ? "Çevriliyor…" : "Türkçeye çevir"}
                      </button>
                    )
                  ) : (
                    <div className="mt-1 text-[10px] text-center text-gray-400">
                      ürün fotoğrafı
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Kalite iyileştirme ── */}
      <div className="mt-4 pt-4 border-t">
        <div className="flex items-center gap-2 mb-1">
          <Wand2 className="w-4 h-4" />
          <h5 className="font-semibold text-sm">Kalite İyileştirme (Gemini)</h5>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          Tedarikçi görselleri 630x840 ve sıkıştırma izli; vitrinde büyüyünce
          dağılıyor. Bu araç <b>yeni poz üretmez</b> — mevcut görselleri
          netleştirip <b>1200x1800</b> olarak yeniden yazar. Kadraj, duruş,
          arka plan ve renkler korunur.
        </p>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => iyilestir(false)}
            disabled={hdBekle || images.length === 0}
            className="px-3 py-1.5 rounded-lg border text-sm inline-flex items-center gap-1.5 hover:bg-gray-50 disabled:opacity-50"
          >
            <Wand2 className="w-4 h-4" />
            {hdBekle ? "İşleniyor…" : "Önizle (ürüne dokunma)"}
          </button>
          <button
            type="button"
            onClick={() => iyilestir(true)}
            disabled={hdBekle || images.length === 0}
            className="px-3 py-1.5 rounded-lg bg-gray-900 text-white text-sm inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <Check className="w-4 h-4" />
            İyileştir ve Değiştir
          </button>
        </div>

        {hdSonuc && (
          <div className="mt-3">
            <div className="text-xs text-gray-600 mb-2">
              {(hdSonuc.results || []).filter((r) => r.url).length} görsel
              {" "}{hdSonuc.target} üretildi
              {hdSonuc.ai_count ? ` (${hdSonuc.ai_count} tanesi modelden geçti)` : ""}
              {hdSonuc.replaced
                ? " ve ürüne yazıldı. Bu ürünün görselleri artık tedarikçi aktarımında değiştirilmeyecek."
                : " — ürün değişmedi."}
            </div>
            {hdSonuc.warning && (
              <div className="mb-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900">
                {hdSonuc.warning}
              </div>
            )}
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {(hdSonuc.results || []).map((r, i) => (
                <div key={i}>
                  {r.url ? (
                    <>
                      <img src={r.url} alt={`iyileştirilmiş ${r.index + 1}`}
                           className="w-full aspect-[2/3] object-cover rounded-lg border" />
                      <div
                        className={`text-[10px] text-center mt-0.5 ${
                          r.ai ? "text-green-700" : "text-amber-700"
                        }`}
                        title={r.note || ""}
                      >
                        {r.tur === "olcu" ? "ölçü tablosu · TR" : (r.ai ? "netleştirildi" : "yalnız ölçek")}
                      </div>
                    </>
                  ) : (
                    <div className="aspect-[2/3] rounded-lg border border-red-200 bg-red-50 p-1 text-[10px] text-red-700 flex items-center text-center">
                      {r.error}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
