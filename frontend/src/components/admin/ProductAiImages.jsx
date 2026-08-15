import { useState } from "react";
import axios from "axios";
import { Sparkles, Upload, X, Plus, AlertTriangle, Wand2, Check, Ruler } from "lucide-react";

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
  // belge. TEK İŞ olduğu için tek düğme: tabloyu bul, Türkçeleştir, görselin
  // yerine yaz, ölçüleri beden tablosuna kaydet. Sıra sunucuda; panelde
  // kullanıcının takip etmesi gereken bir adım listesi yok.
  const [duzeltBekle, setDuzeltBekle] = useState(false);
  // Ölçü tablosu ÜRÜN düzeyindedir: renk değişince beden ölçüsü değişmez.
  // Ama tablo yalnız üzerinde çalışılan renge yazılıyordu; diğer renkler
  // İngilizce tabloyla ya da tablosuz kalıyordu.
  const [kardesBekle, setKardesBekle] = useState(false);
  const [kardesSonuc, setKardesSonuc] = useState("");
  const [duzeltIlerleme, setDuzeltIlerleme] = useState("");
  const [secimGerekli, setSecimGerekli] = useState(false);
  const [okunan, setOkunan] = useState(null);
  // Elle giriş: okuma adımı modele bağlı, çizim tamamen bizde. Model
  // okuyamadığında tablo hiç üretilemiyordu; ölçüler elle girilince
  // krediye ihtiyaç duymadan oluşuyor.
  const [elleAcik, setElleAcik] = useState(false);
  const [elleMetin, setElleMetin] = useState(
    "Beden;Göğüs;Bel;Kalça\nS;88;70;96\nM;92;74;100\nL;96;78;104");
  const [elleBekle, setElleBekle] = useState(false);

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

  /** UZUN İŞ: sunucu işi arka planda başlatır, biz durumunu sorarız.
   *
   *  Cloudflare vekili isteği 100 saniyede kesiyor. İş sunucuda bitse bile
   *  bağlantı koptuğu için sonuç panele ulaşmıyordu — ekranda hep "bağlantı
   *  koptu" görünüyordu. Artık istek anında dönüyor, sonucu yoklamayla
   *  alıyoruz; iş ne kadar sürerse sürsün sonuç kaybolmuyor.
   *
   *  ilerlet: bekleme sırasında ekrana yazılacak metni günceller.
   */
  const uzunIstek = async (yol, govde, ilerlet) => {
    const bas = await istek(yol, govde);
    if (!bas?.job_id) return bas;              // sunucu doğrudan sonuç döndüyse
    const basladi = Date.now();
    // Üst sınır 15 dk: sunucu ölse bile panel sonsuza kadar dönmesin.
    while (Date.now() - basladi < 15 * 60 * 1000) {
      await new Promise((r) => setTimeout(r, 3000));
      const gecen = Math.round((Date.now() - basladi) / 1000);
      ilerlet?.(`${gecen} sn`);
      let d;
      try {
        const r = await fetch(`${API}/ai-images/jobs/${bas.job_id}`, {
          headers: { Authorization: `Bearer ${token()}` },
        });
        d = await r.json();
        if (!r.ok) throw new Error(d.detail || "Durum okunamadı");
      } catch {
        continue;                              // geçici ağ hatası — yoklamayı sürdür
      }
      if (d.durum === "bitti") return d.sonuc;
      if (d.durum === "hata") throw new Error(d.hata || "İşlem başarısız");
    }
    throw new Error("İş 15 dakikada bitmedi; sunucu kayıtlarına bakın.");
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
        const d = await uzunIstek("regenerate", {
          reference_url: ortamUrl || undefined, poses: [pozlar[i]],
        }, (g) => setUretimIlerleme(`${i + 1}/${pozlar.length} · ${g}`));
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
        const d = await uzunIstek("enhance", { replace: !!replace, indexes: [i] },
                                  (g) => setHdIlerleme(`${i + 1}/${images.length} · ${g}`));
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

  /** TEK DÜĞME: tabloyu bul → Türkçeleştir → görselin yerine yaz → kaydet.
   *  Sıra sunucuda (size-table-fix). Hangi görselin tablo olduğu bulunamazsa
   *  sunucu 422 döner ve kullanıcıdan görseli seçmesini isteriz. */
  /** Aynı stok kodlu renk kardeşlerine de aynı tablo görselini yaz. */
  const kardeslereYaz = async () => {
    setKardesBekle(true);
    setHata("");
    setKardesSonuc("");
    try {
      const d = await istek("size-chart-to-siblings", {});
      setKardesSonuc(d.mesaj || "tamam");
    } catch (e) {
      setHata(e.message);
    } finally {
      setKardesBekle(false);
    }
  };

  const olcuDuzelt = async (index) => {
    setDuzeltBekle(true);
    setHata("");
    setOkunan(null);
    try {
      const d = await uzunIstek("size-table-fix",
        typeof index === "number" ? { index } : {}, setDuzeltIlerleme);
      setOkunan(d);
      setSecimGerekli(false);
      const yeni = [...images];
      if (d.index < yeni.length) yeni[d.index] = d.url;
      onReplaceImages?.(yeni);
    } catch (e) {
      setHata(e.message);
      // Tablo görselini kendisi bulamadıysa seçtiriyoruz.
      if (/seçip|seçin/i.test(e.message || "")) setSecimGerekli(true);
    } finally {
      setDuzeltBekle(false);
      setDuzeltIlerleme("");
    }
  };

  const elleUret = async (index) => {
    const satirlar_ = elleMetin.trim().split("\n").map((r) => r.split(";").map((x) => x.trim()));
    if (satirlar_.length < 2) {
      setHata("En az bir başlık satırı ve bir beden satırı girin.");
      return;
    }
    const [baslik, ...geri] = satirlar_;
    const olcu_adlari = baslik.slice(1);
    const satirlar = geri
      .filter((r) => r[0])
      .map((r) => ({ beden: r[0], olculer: r.slice(1) }));
    setElleBekle(true);
    setHata("");
    try {
      const d = await istek("size-table-build", {
        olcu_adlari, satirlar,
        index: typeof index === "number" ? index : undefined,
        replace: typeof index === "number", kaydet: true,
      });
      setOkunan({ olcu_adlari: d.olcu_adlari, satirlar: d.satirlar,
                  warning: "Tablo girdiğiniz ölçülerden üretildi." });
      if (d.replaced && typeof index === "number") {
        const yeni = [...images];
        if (index < yeni.length) yeni[index] = d.url;
        onReplaceImages?.(yeni);
      }
      setElleAcik(false);
    } catch (e) {
      setHata(e.message);
    } finally {
      setElleBekle(false);
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

      {/* ── Ölçü tablosu ──
          Beş ayrı düğme vardı (ara → çevir → oku → elle işaretle → elle gir);
          her biri ayrı başarısız oluyor, kullanıcı sırayı takip etmek zorunda
          kalıyordu. İş tek: "şu tabloyu Türkçe yap". Düğme de tek. */}
      <div className="mt-4 pt-4 border-t">
        <div className="flex items-center gap-2 mb-1">
          <Ruler className="w-4 h-4" />
          <h5 className="font-semibold text-sm">Ölçü Tablosu</h5>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          Tedarikçi görsellerinin arasında beden tablosu da geliyor ve çoğu
          İngilizce. Tek tuşla: tablo görseli bulunur, ölçüler okunur, tablo
          Türkçe yeniden çizilip <b>yalnız o görselin</b> yerine yazılır ve
          ölçüler beden tablosuna kaydedilir. Ürün fotoğraflarına dokunulmaz.
        </p>

        <button
          type="button"
          onClick={() => olcuDuzelt(undefined)}
          disabled={duzeltBekle || images.length === 0}
          className="px-3 py-1.5 rounded-lg bg-blue-600 text-white text-sm inline-flex items-center gap-1.5 disabled:opacity-50"
        >
          <Ruler className="w-4 h-4" />
          {duzeltBekle
            ? `Çalışıyor… ${duzeltIlerleme}`
            : "Ölçü tablosunu Türkçeleştir"}
        </button>

        {/* Ölçü tablosu ÜRÜN düzeyindedir; renk değişince beden ölçüsü
            değişmez. Tablo yalnız üzerinde çalışılan renge yazılıyordu. */}
        <button
          type="button"
          onClick={kardeslereYaz}
          disabled={kardesBekle}
          className="ml-2 px-3 py-1.5 rounded-lg border text-sm inline-flex items-center gap-1.5 hover:bg-gray-50 disabled:opacity-50"
        >
          <Ruler className="w-4 h-4" />
          {kardesBekle ? "Yazılıyor…" : "Bu tabloyu renk kardeşlerine de yükle"}
        </button>
        {kardesSonuc && (
          <div className="mt-2 text-xs text-green-800 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
            {kardesSonuc}
          </div>
        )}

        {/* Yalnız tablo görseli bulunamadığında çıkar — gereksiz yere
            her seferinde altı küçük resim göstermiyoruz. */}
        {secimGerekli && (
          <div className="mt-3">
            <div className="text-xs text-gray-600 mb-2">
              Hangi görsel ölçü tablosu? Üzerine tıklayın.
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {images.map((u, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => olcuDuzelt(i)}
                  disabled={duzeltBekle}
                  className="rounded-lg border hover:border-blue-500 disabled:opacity-50"
                >
                  <img src={u} alt={`görsel ${i + 1}`}
                       className="w-full aspect-[2/3] object-cover rounded-lg" />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Son çare: model okuyamıyorsa ölçüler elle girilir, tablo yine
            sunucuda çizilir (modele hiç gidilmez). */}
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setElleAcik((v) => !v)}
            className="text-xs underline text-gray-600 hover:text-black"
          >
            {elleAcik ? "elle girişi kapat" : "ölçüleri elle gir"}
          </button>
          {elleAcik && (
            <div className="mt-2 rounded-lg border border-gray-200 p-3">
              <p className="text-[11px] text-gray-500 mb-2">
                Her satır bir beden; sütunlar <b>noktalı virgülle</b> ayrılır.
                İlk satır başlıktır.
              </p>
              <textarea
                className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs"
                rows={5}
                value={elleMetin}
                onChange={(e) => setElleMetin(e.target.value)}
              />
              <div className="flex flex-wrap gap-2 mt-2">
                <button
                  type="button"
                  onClick={() => elleUret(okunan?.index)}
                  disabled={elleBekle}
                  className="px-3 py-1.5 rounded-lg bg-gray-900 text-white text-xs disabled:opacity-50"
                >
                  {elleBekle
                    ? "Üretiliyor…"
                    : typeof okunan?.index === "number"
                      ? `${okunan.index + 1}. görselin yerine yaz`
                      : "tabloyu üret"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {okunan && (
        <div className="mt-3 rounded-lg border border-gray-200 p-3">
          <div className="text-xs font-semibold mb-1">
            Okunan ölçüler — {okunan.satirlar?.length || 0} beden
          </div>
          {/* Ne yapıldığı tek tek yazılır: hangi görsel seçildi, ölçüler
              nereden geldi, kredi harcandı mı. */}
          {okunan.adimlar?.length > 0 && (
            <ul className="text-[11px] text-gray-600 mb-2 list-disc pl-4">
              {okunan.adimlar.map((a, i) => <li key={i}>{a}</li>)}
            </ul>
          )}
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
