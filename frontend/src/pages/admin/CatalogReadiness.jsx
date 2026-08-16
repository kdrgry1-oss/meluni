import { useEffect, useState } from "react";
import axios from "axios";
import { RefreshCw, CheckCircle2, Circle } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * Katalog Hazırlık — kategori kategori ilerleme.
 *
 * NEDEN: Takım kategorisini pazaryerine hazır hâle getirmek beş ayrı iş aldı
 * (ölçü tablosu, tablo görseli, görsellerin R2'ye taşınması, öznitelikler,
 * zorunlu temel alanlar). Aynı işler tüm kategorilere uygulanacak ama nerede
 * kalındığı hiçbir yerde görünmüyordu — her seferinde betik çalıştırıp elle
 * saymak gerekiyordu. Bu sayfa bakılacak tek yer.
 */
const ADIMLAR = [
  ["olcu_verisi", "Ölçü verisi"],
  ["tablo_gorseli", "Tablo görseli"],
  ["kendi_gorseli", "Kendi görselimiz"],
  ["oznitelik", "Öznitelikler"],
  ["temel_alan", "Sezon + kalıp"],
];

function Bar({ yuzde, renk = "bg-emerald-500" }) {
  return (
    <div className="h-2 w-full bg-gray-100 rounded-full overflow-hidden">
      <div className={`h-full ${renk} transition-all`} style={{ width: `${Math.min(100, yuzde)}%` }} />
    </div>
  );
}

export default function CatalogReadiness() {
  const [veri, setVeri] = useState(null);
  const [yukleniyor, setYukleniyor] = useState(true);
  const [hata, setHata] = useState("");
  const [ara, setAra] = useState("");

  const yukle = async () => {
    setYukleniyor(true);
    setHata("");
    try {
      const t = localStorage.getItem("token");
      const r = await axios.get(`${API}/catalog-readiness`, {
        headers: { Authorization: `Bearer ${t}` },
      });
      setVeri(r.data);
    } catch (e) {
      setHata(e.response?.data?.detail || e.message);
    } finally {
      setYukleniyor(false);
    }
  };

  useEffect(() => { yukle(); }, []);

  const g = veri?.genel;
  const kategoriler = (veri?.kategoriler || []).filter((k) =>
    !ara || (k.kategori || "").toLocaleLowerCase("tr").includes(ara.toLocaleLowerCase("tr")));

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Katalog Hazırlık</h1>
          <p className="text-sm text-gray-500">
            Her kategoride pazaryerine hazır olma durumu. Yüzde, beş adımın ürün
            başına ortalamasıdır.
          </p>
        </div>
        <button onClick={yukle} disabled={yukleniyor}
          className="px-4 py-2 rounded-lg border text-sm inline-flex items-center gap-2 hover:bg-gray-50 disabled:opacity-50">
          <RefreshCw className={`w-4 h-4 ${yukleniyor ? "animate-spin" : ""}`} /> Yenile
        </button>
      </div>

      {hata && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{hata}</div>
      )}

      {g && (
        <div className="border rounded-2xl p-5 bg-white">
          <div className="flex items-baseline justify-between mb-2">
            <h2 className="font-semibold">Genel durum</h2>
            <span className="text-3xl font-bold">%{g.yuzde}</span>
          </div>
          <Bar yuzde={g.yuzde} />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-5 text-sm">
            <div><div className="text-gray-500">Ürün</div><div className="font-semibold">{g.urun}</div></div>
            <div><div className="text-gray-500">Kategori</div><div className="font-semibold">{g.kategori}</div></div>
            <div><div className="text-gray-500">Trendyol'a eşleşmiş kategori</div>
              <div className="font-semibold">{g.eslesmis_kategori} / {g.kategori}</div></div>
            <div><div className="text-gray-500">Trendyol'a gönderilmiş ürün</div>
              <div className="font-semibold">{g.trendyola_gitti}</div></div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-5">
            {ADIMLAR.map(([k, ad]) => (
              <div key={k} className="text-xs">
                <div className="flex justify-between mb-1">
                  <span className="text-gray-600">{ad}</span>
                  <span className="font-semibold">{g[k]}</span>
                </div>
                <Bar yuzde={Math.round((g[k] / Math.max(1, g.urun)) * 100)} renk="bg-blue-500" />
              </div>
            ))}
          </div>
        </div>
      )}

      <input value={ara} onChange={(e) => setAra(e.target.value)} placeholder="Kategori ara…"
        className="w-full md:w-80 border rounded-lg px-3 py-2 text-sm" />

      <div className="space-y-3">
        {kategoriler.map((k) => (
          <div key={k.kategori_id || k.kategori} className="border rounded-xl p-4 bg-white">
            <div className="flex items-center justify-between gap-3 mb-2">
              <div className="flex items-center gap-2 min-w-0">
                {k.trendyol_eslesti
                  ? <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                  : <Circle className="w-4 h-4 text-gray-300 shrink-0" />}
                <span className="font-semibold truncate">{k.kategori}</span>
                <span className="text-xs text-gray-400">{k.urun} ürün</span>
                {!k.trendyol_eslesti && (
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                    KATEGORİ EŞLEŞMEMİŞ
                  </span>
                )}
              </div>
              <span className="text-lg font-bold shrink-0">%{k.yuzde}</span>
            </div>
            <Bar yuzde={k.yuzde} />
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-3">
              {ADIMLAR.map(([alan, ad]) => (
                <div key={alan} className="text-[11px]">
                  <div className="flex justify-between mb-1">
                    <span className="text-gray-500">{ad}</span>
                    <span>{k[alan]}/{k.urun}</span>
                  </div>
                  <Bar yuzde={k[`${alan}_yuzde`]} renk="bg-blue-400" />
                </div>
              ))}
            </div>
          </div>
        ))}
        {!yukleniyor && !kategoriler.length && (
          <p className="text-sm text-gray-400">Kategori bulunamadı.</p>
        )}
      </div>
    </div>
  );
}
