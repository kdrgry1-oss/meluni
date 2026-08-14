import { useState } from "react";
import axios from "axios";
import { Sparkles, Upload, X, Plus, AlertTriangle, Wand2, Check, Ruler, Search, Table } from "lucide-react";

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
  const [uretimIlerleme, setUretimIlerleme] = useState("");
  const [sonuc, setSonuc] = useState(null);
  const [hata, setHata] = useState("");
  const [eklenen, setEklenen] = useState([]);
  // Kalite iyileştirme ayrı bir iş: mevcut görselleri Gemini ile netleştirip
  // 1200x1800 üretir. Üretimden farklı olarak yeni poz oluşturmaz.
  const [hdBekle, setHdBekle] = useState(false);
  const [hdSonuc, setHdSonuc] = useState(null);
  const [hdIlerleme, setHdIlerleme] = useState("");
  // Ölçü tablosu ayrı ele alınır: ürün fotoğrafı değil, METİN taşıyan bir
  // belge. Tarama hangi görselin tablo olduğunu söyler, dönüşüm yalnız ona
  // uygulanır.
  const [tarama, setTarama] = useState(null);
  const [taraBekle, setTaraBekle] = useState(false);
  const [ceviriBekle, setCeviriBekle] = useState(-1);
  const [okuBekle, setOkuBekle] = useState(-1);
  const [okunan, setOkunan] = useState(null);

  const token = () => localStorage.getItem("token") || "";

  /** Tek istek. "Failed to fetch" ham hâliyle hiçbir şey anlatmıyor; ağ
   *  seviyesinde düşen istek neredeyse her zaman Cloudflare'in 100 saniyelik
   *  vekil sınırı (524) — sebebi açıkça yazıyoruz. */
  const istek = async (yol, govde) => {
    try {
      const r = await fetch(`${API}/ai-images/products/${productId}/${yol}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify(govde || {}),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "İşlem başarısız");
      return d;
    } catch (e) {
      if (String(e.message || "").toLowerCase().includes("failed to fetch")) {
        throw new Error(
          "Bağlantı koptu — istek büyük ihtimalle 100 saniyelik vekil sınırını " +
          "aştı. Görseller tek tek işleniyor; yine olursa tekrar deneyin.");
      }
      throw e;
    }
  };

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
      // POZLAR TEK TEK: beş pozu tek istekte üretmek vekil sınırını aşıyordu.
      const pozlar = ["front", "left", "right", "back", "detail"];
      const uretilenler = {};
      const hatalar = {};
      let etiketler = {};
      for (let i = 0; i < pozlar.length; i++) {
        setUretimIlerleme(`${i + 1}/${pozlar.length}`);
        // attach GÖNDERİLMİYOR: ürüne ekleme kararı burada, tek tek verilir.
        const d = await istek("regenerate", {
          reference_url: ortamUrl || undefined, poses: [pozlar[i]],
        });
        Object.assign(uretilenler, d.generated || {});
        Object.assign(hatalar, d.errors || {});
        etiketler = { ...etiketler, ...(d.labels || {}) };
        setSonuc({ generated: { ...uretilenler }, errors: { ...hatalar },
                   labels: etiketler, warning: d.warning || "" });
      }
    } catch (e) {
      setHata(e.message);
    } finally {
      setUretiliyor(false);
      setUretimIlerleme("");
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
      // GÖRSELLER TEK TEK. Hepsini tek istekte işlemek 100 saniyelik vekil
      // sınırını aşıyor ve bağlantı "Failed to fetch" ile kopuyordu; üstelik
      // o ana kadar üretilenler de kayboluyordu. Sırayla gidince her istek
      // kısa kalıyor ve ilerleme ekranda birikiyor.
      const hepsi = [];
      let uyari = "";
      for (let i = 0; i < images.length; i++) {
        setHdIlerleme(`${i + 1}/${images.length}`);
        const d = await istek("enhance", { replace: !!replace, indexes: [i] });
        hepsi.push(...(d.results || []));
        uyari = uyari || d.warning || "";
        setHdSonuc({ target: d.target, results: [...hepsi], warning: uyari,
                     replaced: !!replace,
                     ai_count: hepsi.filter((x) => x.ai).length });
      }
      const d = { target: "1200x1800", results: hepsi, warning: uyari,
                  replaced: !!replace, ai_count: hepsi.filter((x) => x.ai).length };
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
      setHdIlerleme("");
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
      const d = await istek("size-chart", { index, replace: true });
      // Tablo okunan değerlerden yeniden çizildi; hangi sayıların basıldığını
      // hemen gösteriyoruz ki kaynakla karşılaştırma tek ekranda yapılsın.
      if (d.satirlar?.length) {
        setOkunan({ olcu_adlari: d.olcu_adlari, satirlar: d.satirlar,
                    warning: d.warning });
      }
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

  // Sınıflandırma yanılabiliyor (Gemini kotası bitince "belirsiz" dönüyor).
  // Kullanıcı görsele bakıp zaten biliyor — elle işaretleyebilsin.
  const elleIsaretle = (index) => {
    setTarama((t) => t && {
      ...t,
      images: t.images.map((it) =>
        it.index === index ? { ...it, tur: it.tur === "olcu" ? "urun" : "olcu" } : it),
      size_chart_count: (t.images || []).filter(
        (it) => (it.index === index ? it.tur !== "olcu" : it.tur === "olcu")).length,
    });
  };

  const olculeriOku = async (index) => {
    setOkuBekle(index);
    setHata("");
    setOkunan(null);
    try {
      const d = await istek("size-table-read", { index, kaydet: true });
      setOkunan(d);
    } catch (e) {
      setHata(e.message);
    } finally {
      setOkuBekle(-1);
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
            {uretiliyor ? `Üretiliyor… ${uretimIlerleme}` : "5 Görseli Üret"}
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
                : "Ölçü tablosu bulunamadı."}
            </div>
            {tarama.warning && (
              <div className="mb-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900">
                {tarama.warning}
              </div>
            )}
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {(tarama.images || []).map((it) => (
                <div key={it.index}>
                  <img src={it.url} alt={`görsel ${it.index + 1}`}
                       className={`w-full aspect-[2/3] object-cover rounded-lg border ${
                         it.tur === "olcu" ? "border-blue-400 ring-1 ring-blue-300" : ""
                       }`} />
                  {it.tur === "olcu" ? (
                    <>
                      {it.cevrildi ? (
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
                      )}
                      {/* Görseli çevirmek yetmez: site beden rehberini VERİ
                          olarak kullanıyor. Ölçüleri okuyup tabloya yazar. */}
                      <button
                        type="button"
                        onClick={() => olculeriOku(it.index)}
                        disabled={okuBekle === it.index}
                        className="mt-1 w-full text-[10px] rounded-lg py-1 border inline-flex items-center justify-center gap-1 hover:bg-gray-50 disabled:opacity-50"
                      >
                        <Table className="w-3 h-3" />
                        {okuBekle === it.index ? "Okunuyor…" : "Ölçüleri tabloya yaz"}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => elleIsaretle(it.index)}
                      title="Sınıflandırma yanıldıysa elle işaretleyin"
                      className="mt-1 w-full text-[10px] rounded-lg py-1 border text-gray-500 hover:bg-gray-50"
                    >
                      {it.tur === "belirsiz" ? "belirsiz — tablo mu?" : "ürün fotoğrafı"}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {okunan && (
        <div className="mt-3 rounded-lg border border-gray-200 p-3">
          <div className="text-xs font-semibold mb-1">
            Okunan ölçüler — {okunan.satirlar?.length || 0} beden
          </div>
          <div className="rounded-lg bg-amber-50 border border-amber-200 px-2 py-1.5 text-[11px] text-amber-900 mb-2">
            {okunan.warning}
          </div>
          <div className="overflow-x-auto">
            <table className="text-xs">
              <thead>
                <tr className="text-left text-gray-500">
                  <th className="pr-3 py-1">Beden</th>
                  {(okunan.olcu_adlari || []).map((a, i) => (
                    <th key={i} className="pr-3 py-1">{a}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(okunan.satirlar || []).map((r, i) => (
                  <tr key={i} className="border-t">
                    <td className="pr-3 py-1 font-medium">{r.beden}</td>
                    {(r.olculer || []).map((o, k) => (
                      <td key={k} className="pr-3 py-1">{o || "—"}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

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
            {hdBekle ? `İşleniyor… ${hdIlerleme}` : "Önizle (ürüne dokunma)"}
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
