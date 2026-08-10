import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { MessageSquare, CheckCircle2, XCircle, Clock, Star, Trash2, Download, Store } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

// Trendyol Facette mağazasından 4-5★ yorumları toplu çekme paneli. Ürünleri barkodla
// Trendyol listelemesine eşleştirir, public storefront'tan yorumları çeker, product_reviews'a yazar.
const SYNC_STATUS_META = {
  cekildi:   { label: "Çekildi",     cls: "bg-emerald-100 text-emerald-800 border-emerald-200" },
  yorum_yok: { label: "Yorum yok",   cls: "bg-gray-100 text-gray-600 border-gray-200" },
  eslesmedi: { label: "Eşleşmedi",   cls: "bg-amber-100 text-amber-800 border-amber-200" },
  hata:      { label: "Hata",        cls: "bg-red-100 text-red-700 border-red-200" },
};

function TrendyolReviewSync() {
  const [minRating, setMinRating] = useState(3);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [workerUrl, setWorkerUrl] = useState("");
  const [cfgSaved, setCfgSaved] = useState(false);
  const [byProduct, setByProduct] = useState(null);
  // Arka plan senkron durumu: {state:{status,done_products,total_products,...}, counts, products}
  const [sync, setSync] = useState(null);
  const [statusFilter, setStatusFilter] = useState("");

  const loadByProduct = () => {
    axios.get(`${API}/integrations/trendyol/reviews/by-product?limit=500`, { headers: authHeaders() })
      .then((r) => setByProduct(r.data))
      .catch(() => {});
  };

  const loadSyncStatus = () => {
    return axios.get(`${API}/integrations/trendyol/reviews/sync-status`, { headers: authHeaders() })
      .then((r) => { setSync(r.data); return r.data; })
      .catch(() => null);
  };

  // Senkron çalışırken 4 sn'de bir durumu tazele; bitince ürün listesini yenile.
  useEffect(() => {
    if (sync?.state?.status !== "running") return;
    const t = setInterval(async () => {
      const d = await loadSyncStatus();
      if (d && d.state?.status !== "running") {
        clearInterval(t);
        loadByProduct();
        if (d.state?.status === "done") toast.success("Yorum senkronu tamamlandı");
        else if (d.state?.status === "error") toast.error(`Senkron hata verdi: ${d.state?.error || ""}`);
      }
    }, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sync?.state?.status]);

  useEffect(() => {
    axios.get(`${API}/integrations/trendyol/reviews/fetch-config`, { headers: authHeaders() })
      .then((r) => {
        setWorkerUrl(r.data?.review_worker_url || "");
        if (r.data?.review_min_rating) setMinRating(Number(r.data.review_min_rating));
      })
      .catch(() => {});
    loadByProduct();
    loadSyncStatus();
  }, []);

  const saveWorker = async () => {
    try {
      // Alt yıldız da kaydedilir → HAFTALIK otomatik çekim de aynı eşiği kullanır.
      await axios.put(`${API}/integrations/trendyol/reviews/fetch-config`,
        { review_worker_url: workerUrl.trim(), review_min_rating: Number(minRating) }, { headers: authHeaders() });
      setCfgSaved(true); setTimeout(() => setCfgSaved(false), 2000);
      toast.success("Kaydedildi");
    } catch { toast.error("Kaydedilemedi"); }
  };

  // Senkron artık ARKA PLANDA çalışır (uzun tarama HTTP zaman aşımına takılıp
  // "senkron başarısız" görünüyordu). Tekrar basıldığında yalnız daha önce yorum
  // çekilemeyen ürünler denenir (only_missing) — "Tümünü baştan" ile hepsi.
  const run = async (onlyMissing = true) => {
    setBusy(true); setResult(null);
    try {
      const { data } = await axios.post(
        `${API}/integrations/trendyol/reviews/sync-all`,
        { min_rating: Number(minRating), limit: 0, dry_run: false, only_missing: onlyMissing },
        { headers: authHeaders(), timeout: 30000 },
      );
      toast.success(data?.message || "Senkron arka planda başladı");
      await loadSyncStatus();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Senkron başlatılamadı");
    } finally { setBusy(false); }
  };

  return (
    <div className="bg-white border rounded-xl p-4" data-testid="trendyol-review-sync">
      <div className="flex items-center gap-2 mb-1">
        <Store size={18} className="text-orange-500" />
        <h2 className="text-sm font-bold uppercase tracking-wider">Trendyol Yorumları (Facette Mağazası)</h2>
      </div>
      <p className="text-xs text-gray-500 mb-3">
        Site ürünlerini barkodla Trendyol listelemene eşleştirir ve <b>{minRating}★ ve üzeri</b> yorumları çeker.
        Yorumlar ürün sayfasında müşteri yorumlarıyla birlikte görünür. Uzun sürebilir.
      </p>

      {/* 530 çözümü: Cloudflare Worker vekili. Trendyol sunucu IP'lerini engellediği için
          zorunlu. Boşsa doğrudan denenir (büyük ihtimalle 530 verir). */}
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-3">
        <label className="block text-xs font-semibold text-amber-800 mb-1">
          Cloudflare Worker URL (530 engelini aşmak için — önerilir)
        </label>
        <div className="flex gap-2">
          <input
            value={workerUrl}
            onChange={(e) => setWorkerUrl(e.target.value)}
            placeholder="https://facette-ty.hesabin.workers.dev"
            className="flex-1 border border-amber-300 rounded px-3 py-2 text-sm"
          />
          <button onClick={saveWorker} className="px-3 py-2 bg-amber-600 text-white rounded-lg text-sm hover:bg-amber-700">
            {cfgSaved ? "Kaydedildi ✓" : "Kaydet"}
          </button>
        </div>
        <p className="text-[11px] text-amber-700 mt-1.5">
          Trendyol, sunucumuzun IP'sini engelliyor (530). Ücretsiz Cloudflare Worker kurup URL'sini buraya
          yapıştır — yorumlar oradan çekilir. Boş bırakırsan çekim çoğunlukla başarısız olur.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm flex items-center gap-2">
          Alt yıldız:
          <select value={minRating} onChange={(e) => setMinRating(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm">
            <option value={3}>3, 4 ve 5 yıldız</option>
            <option value={4}>4 ve 5 yıldız</option>
            <option value={5}>Sadece 5 yıldız</option>
          </select>
        </label>
        <button onClick={() => run(true)} disabled={busy || sync?.state?.status === "running"}
          className="inline-flex items-center gap-1.5 px-4 py-2 bg-orange-600 text-white rounded-lg text-sm hover:bg-orange-700 disabled:opacity-50"
          title="Daha önce yorumu çekilemeyen ürünler denenir">
          <Download size={14} className={busy ? "animate-pulse" : ""} />
          {sync?.state?.status === "running" ? "Çekiliyor (arka planda)…" : "Yorumları Çek (yalnız eksikler)"}
        </button>
        <button onClick={() => run(false)} disabled={busy || sync?.state?.status === "running"}
          className="inline-flex items-center gap-1.5 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
          title="Tüm ürünler baştan taranır (uzun sürer)">
          Tümünü Baştan Çek
        </button>
      </div>

      {/* Canlı ilerleme — senkron arka planda çalışırken */}
      {sync?.state?.status === "running" && (
        <div className="mt-3 bg-orange-50 border border-orange-200 rounded-lg p-3" data-testid="review-sync-progress">
          <div className="flex justify-between text-xs text-orange-800 mb-1.5">
            <span className="font-semibold">
              Senkron çalışıyor… {sync.state.done_products ?? 0} / {sync.state.total_products ?? "?"} ürün
              {sync.state.skipped_already > 0 && <> · {sync.state.skipped_already} ürün zaten çekilmiş (atlandı)</>}
            </span>
            <span>{sync.state.inserted ?? 0} yorum eklendi</span>
          </div>
          <div className="h-1.5 bg-orange-200 rounded overflow-hidden">
            <div className="h-full bg-orange-600 transition-all duration-500"
              style={{ width: `${Math.min(100, ((sync.state.done_products || 0) / Math.max(1, sync.state.total_products || 1)) * 100)}%` }} />
          </div>
          {sync.state.last_product && (
            <p className="text-[11px] text-orange-700 mt-1.5">Şu an: {sync.state.last_product}</p>
          )}
        </div>
      )}
      {sync?.state?.status === "error" && (
        <div className="mt-3 bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700">
          Son senkron hata verdi: {sync.state.error || "bilinmeyen hata"} — "Yorumları Çek" ile yeniden deneyin (yalnız eksikler denenir).
        </div>
      )}

      {/* Ürün bazında çekildi/çekilemedi listesi */}
      {sync?.products?.length > 0 && (
        <div className="mt-4 border-t pt-3" data-testid="review-sync-product-status">
          <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
            <h3 className="text-sm font-bold">Senkron Durumu — Ürün Bazında</h3>
            <div className="flex gap-1.5 flex-wrap">
              <button onClick={() => setStatusFilter("")}
                className={`text-[11px] px-2 py-1 rounded border ${statusFilter === "" ? "bg-black text-white border-black" : "bg-white text-gray-600"}`}>
                Tümü ({sync.products.length})
              </button>
              {Object.entries(SYNC_STATUS_META).map(([k, m]) => (
                (sync.counts?.[k] || 0) > 0 && (
                  <button key={k} onClick={() => setStatusFilter(k)}
                    className={`text-[11px] px-2 py-1 rounded border ${statusFilter === k ? "bg-black text-white border-black" : m.cls}`}>
                    {m.label} ({sync.counts[k]})
                  </button>
                )
              ))}
            </div>
          </div>
          <div className="max-h-96 overflow-y-auto border rounded-lg">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase sticky top-0">
                <tr>
                  <th className="text-left p-2.5">Ürün</th>
                  <th className="text-left p-2.5">Durum</th>
                  <th className="text-right p-2.5">Çekilen</th>
                  <th className="text-right p-2.5">Eklenen</th>
                </tr>
              </thead>
              <tbody>
                {sync.products.filter((r) => !statusFilter || r.status === statusFilter).map((r) => {
                  const m = SYNC_STATUS_META[r.status] || { label: r.status, cls: "bg-gray-100 text-gray-600" };
                  return (
                    <tr key={r.product_id} className="border-t">
                      <td className="p-2.5">
                        <a href={`/admin/urunler/${r.product_id}`} className="hover:underline">{r.name || r.product_id}</a>
                        {r.error && <div className="text-[10px] text-red-500 truncate max-w-md">{r.error}</div>}
                      </td>
                      <td className="p-2.5">
                        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${m.cls}`}>{m.label}</span>
                      </td>
                      <td className="p-2.5 text-right tabular-nums">{r.fetched ?? 0}</td>
                      <td className="p-2.5 text-right tabular-nums font-semibold">{r.inserted ?? 0}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-gray-400 mt-1.5">
            "Yorumları Çek" tekrar basıldığında yalnız <b>Çekildi olmayan</b> (eşleşmedi / yorum yok / hata) ürünler yeniden denenir.
          </p>
        </div>
      )}
      {result && (
        <div className="mt-3 text-xs bg-gray-50 border rounded-lg p-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
          <div><span className="text-gray-500">Trendyol ürün:</span> <b>{result.trendyol_products_indexed ?? "—"}</b></div>
          <div><span className="text-gray-500">Eşleşen ürün:</span> <b>{result.matched_products ?? "—"}</b></div>
          <div><span className="text-gray-500">Eşleşmeyen:</span> <b>{result.unmatched_products ?? "—"}</b></div>
          <div><span className="text-gray-500">Çekilen yorum:</span> <b>{result.total_fetched ?? "—"}</b></div>
          <div><span className="text-gray-500">Eklenen:</span> <b className="text-emerald-600">{result.total_inserted ?? "—"}</b></div>
          <div><span className="text-gray-500">Güncellenen:</span> <b className="text-blue-600">{result.total_updated ?? "—"}</b></div>
          <div><span className="text-gray-500">Zaten var:</span> <b>{result.skipped_existing ?? "—"}</b></div>
          <div><span className="text-gray-500">Düşük puan:</span> <b>{result.skipped_low_rating ?? "—"}</b></div>
          <div><span className="text-gray-500">Mod:</span> <b>{result.dry_run ? "önizleme" : "gerçek"}</b></div>
          {Array.isArray(result.errors) && result.errors.length > 0 && (
            <div className="col-span-full text-red-600">Hatalar: {result.errors.length} (ilk: {JSON.stringify(result.errors[0])?.slice(0, 120)})</div>
          )}
        </div>
      )}

      {/* Haftalık otomatik çekim bilgisi */}
      <p className="text-[11px] text-gray-400 mt-3">
        🔁 Bu çekim <b>haftada bir otomatik</b> de çalışır (kayıtlı alt yıldız eşiğiyle — "Kaydet" ile ayarlanır).
        Yeni yorumlar eklenir, mevcutlar tekrar eklenmez.
      </p>

      {/* Hangi ürüne kaç yorum çekildi — liste */}
      {byProduct && byProduct.products?.length > 0 && (
        <div className="mt-4 border-t pt-3">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-bold">Çekilen Yorumlar — Ürün Bazında ({byProduct.product_count} ürün · {byProduct.total_reviews} yorum)</h3>
            <button onClick={loadByProduct} className="text-xs text-gray-500 hover:text-black">Yenile</button>
          </div>
          <div className="max-h-96 overflow-y-auto border rounded-lg">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase sticky top-0">
                <tr><th className="text-left p-2.5">Ürün</th><th className="text-right p-2.5">Yorum</th><th className="text-right p-2.5">Ort. Puan</th></tr>
              </thead>
              <tbody>
                {byProduct.products.map((p) => (
                  <tr key={p.product_id} className="border-t">
                    <td className="p-2.5">
                      <a href={`/admin/urunler/${p.product_id}`} className="hover:underline">{p.name}</a>
                    </td>
                    <td className="p-2.5 text-right font-semibold">{p.count}</td>
                    <td className="p-2.5 text-right">{p.avg ? `${p.avg} ★` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ProductReviews() {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState("pending");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/admin/reviews`, { headers: authHeaders(), params: { status, limit: 100 } });
      setItems(data.items || []);
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [status]);

  const moderate = async (rid, newStatus) => {
    try {
      await axios.put(`${API}/admin/reviews/${rid}`, { status: newStatus }, { headers: authHeaders() });
      toast.success(newStatus === "approved" ? "Onaylandı" : "Reddedildi");
      load();
    } catch (_) { toast.error("Hata"); }
  };

  const del = async (rid) => {
    if (!await window.appConfirm("Yorum silinsin mi?")) return;
    await axios.delete(`${API}/admin/reviews/${rid}`, { headers: authHeaders() });
    toast.success("Silindi"); load();
  };

  return (
    <div className="space-y-5" data-testid="reviews-page">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><MessageSquare /> Ürün Yorumları</h1>
        <p className="text-sm text-gray-500 mt-1">Müşteri yorumlarını moderasyondan geçirin, Trendyol yorumlarını çekin.</p>
      </div>

      <TrendyolReviewSync />

      <div className="flex gap-2">
        {[
          ["pending", "Beklemede", Clock, "bg-amber-500"],
          ["approved", "Onaylı", CheckCircle2, "bg-emerald-500"],
          ["rejected", "Reddedildi", XCircle, "bg-red-500"],
        ].map(([k, lbl, Icon, color]) => (
          <button key={k} onClick={() => setStatus(k)}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm ${status === k ? `${color} text-white` : "bg-white border text-gray-700"}`}
            data-testid={`reviews-tab-${k}`}>
            <Icon size={14} /> {lbl}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {loading ? <div className="text-center text-gray-400 py-8">Yükleniyor…</div>
          : items.length === 0 ? <div className="text-center text-gray-400 py-8 bg-white border rounded-xl">Bu durumda yorum yok.</div>
          : items.map((r) => (
            <div key={r.id} className="bg-white border rounded-xl p-4 flex gap-4">
              <div className="flex-shrink-0">
                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500 to-purple-500 text-white flex items-center justify-center font-bold">
                  {r.user_name?.[0]?.toUpperCase() || "?"}
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div>
                    <div className="font-semibold">{r.user_name}</div>
                    <div className="text-xs text-gray-500">Ürün: <span className="text-gray-800">{r.product_name}</span> · {new Date(r.created_at).toLocaleDateString("tr-TR")}</div>
                  </div>
                  <div className="flex gap-0.5">
                    {[1,2,3,4,5].map((n) => (
                      <Star key={n} size={14} className={n <= r.rating ? "fill-yellow-400 text-yellow-400" : "text-gray-300"} />
                    ))}
                  </div>
                </div>
                {r.title && <div className="font-medium mt-2">{r.title}</div>}
                <div className="text-sm text-gray-700 mt-1 whitespace-pre-wrap">{r.comment}</div>
                {r.admin_reply && <div className="mt-2 p-2 bg-blue-50 text-blue-900 text-xs rounded"><strong>Cevabımız:</strong> {r.admin_reply}</div>}
                <div className="flex gap-2 mt-3">
                  {status !== "approved" && (
                    <button onClick={() => moderate(r.id, "approved")} data-testid={`approve-${r.id}`}
                      className="text-xs px-3 py-1 bg-emerald-100 text-emerald-800 border border-emerald-200 rounded hover:bg-emerald-200">Onayla</button>
                  )}
                  {status !== "rejected" && (
                    <button onClick={() => moderate(r.id, "rejected")}
                      className="text-xs px-3 py-1 bg-red-100 text-red-800 border border-red-200 rounded hover:bg-red-200">Reddet</button>
                  )}
                  <button onClick={() => del(r.id)} className="text-xs px-3 py-1 text-gray-600 hover:bg-gray-100 rounded ml-auto inline-flex items-center gap-1">
                    <Trash2 size={12} /> Sil
                  </button>
                </div>
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}
