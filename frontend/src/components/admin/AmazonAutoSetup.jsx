/**
 * Amazon — kategori otomatik eşleme + aktarılmamış ürünleri toplu aktarım.
 * Mevcut aktarım mantığını kullanır; kullanıcının mevcut kategori eşlemelerine dokunmaz.
 */
import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const STATUS_CLS = {
  "eşli": "text-gray-500",
  "eşlenecek": "text-amber-700",
  "eşlendi": "text-emerald-700",
  "tespit edilemedi": "text-red-600",
};

export default function AmazonAutoSetup({ connected }) {
  const [catPlan, setCatPlan] = useState(null);
  const [catBusy, setCatBusy] = useState(false);
  const [pushPrev, setPushPrev] = useState(null);
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);

  const runCats = async (apply) => {
    if (apply && !(await window.appConfirm("Eşlemesi olmayan kategoriler önerilen Amazon ürün tipiyle eşlenecek. Mevcut eşlemelere dokunulmaz. Devam?"))) return;
    setCatBusy(true);
    try {
      const r = await axios.post(`${API}/amazon/spapi/auto/map-categories`, { apply }, auth());
      setCatPlan(r.data);
      if (apply) toast.success(`${r.data.applied} kategori eşlendi`);
    } catch (e) { toast.error(e.response?.data?.detail || "Kategori planı alınamadı"); }
    finally { setCatBusy(false); }
  };

  const loadJob = useCallback(async () => {
    try { const r = await axios.get(`${API}/amazon/spapi/auto/push-status`, auth()); setJob(r.data || null); } catch { /* sessiz */ }
  }, []);
  useEffect(() => { loadJob(); }, [loadJob]);
  useEffect(() => {
    const h = () => setTimeout(loadJob, 1200);
    window.addEventListener("amazon-push-started", h);
    return () => window.removeEventListener("amazon-push-started", h);
  }, [loadJob]);
  useEffect(() => {
    if (job?.status !== "running") return undefined;
    const t = setInterval(loadJob, 6000);
    return () => clearInterval(t);
  }, [job?.status, loadJob]);

  const previewPush = async () => {
    setBusy(true);
    try {
      const r = await axios.post(`${API}/amazon/spapi/auto/push-missing`, { apply: false }, { ...auth(), timeout: 120000 });
      setPushPrev(r.data);
    } catch (e) { toast.error(e.response?.data?.detail || "Ön izleme alınamadı"); }
    finally { setBusy(false); }
  };
  const startPush = async () => {
    if (!(await window.appConfirm("Amazon'da olmayan / eksik bedenli ve stoğu olan tüm ürünler sırayla aktarılacak. Devam?"))) return;
    setBusy(true);
    try {
      const r = await axios.post(`${API}/amazon/spapi/auto/push-missing`, { apply: true }, auth());
      toast(r.data?.already_running ? "Aktarım zaten çalışıyor" : "Aktarım başladı");
      setTimeout(loadJob, 1500);
    } catch (e) { toast.error(e.response?.data?.detail || "Başlatılamadı"); }
    finally { setBusy(false); }
  };

  const running = job?.status === "running";
  const pct = job?.total ? Math.round((100 * (job.done || 0)) / job.total) : 0;

  return (
    <div className="bg-white border rounded-lg p-4 mt-4 space-y-4" data-testid="amazon-auto-setup">
      <h3 className="font-semibold text-sm">⚙️ Otomatik Kurulum & Toplu Aktarım</h3>

      {/* 1) Kategoriler */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-gray-700 w-40 shrink-0">1) Kategori eşleme</span>
          <button onClick={() => runCats(false)} disabled={catBusy || !connected}
            className="inline-flex items-center gap-2 border px-4 py-2 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
            {catBusy ? "Hesaplanıyor…" : "Ön İzleme"}
          </button>
          <button onClick={() => runCats(true)} disabled={catBusy || !connected || !catPlan?.summary?.["eşlenecek"]}
            className="inline-flex items-center gap-2 bg-slate-800 text-white px-4 py-2 rounded-lg text-sm hover:bg-slate-900 disabled:opacity-50">
            Uygula
          </button>
          {catPlan && (
            <span className="text-xs text-gray-600">
              Eşli {catPlan.summary["eşli"] || 0} · Eşlenecek {catPlan.summary["eşlenecek"] || 0} · Eşlendi {catPlan.summary["eşlendi"] || 0} · Tespit edilemedi {catPlan.summary["tespit edilemedi"] || 0}
            </span>
          )}
        </div>
        {catPlan && (
          <div className="border rounded max-h-64 overflow-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 sticky top-0">
                <tr className="text-left text-gray-600">
                  <th className="px-2 py-1.5">Kategori</th>
                  <th className="px-2 py-1.5 text-right">Ürün</th>
                  <th className="px-2 py-1.5">Amazon Tipi</th>
                  <th className="px-2 py-1.5">Kaynak</th>
                  <th className="px-2 py-1.5">Durum</th>
                </tr>
              </thead>
              <tbody>
                {catPlan.rows.map((r) => (
                  <tr key={r.category_id} className="border-t">
                    <td className="px-2 py-1.5">{r.category}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{r.products}</td>
                    <td className="px-2 py-1.5">{r.type_label || r.current || r.suggested || "—"}</td>
                    <td className="px-2 py-1.5 text-gray-500">{r.current ? "mevcut eşleme" : r.source || "—"}</td>
                    <td className={`px-2 py-1.5 font-medium ${STATUS_CLS[r.status] || ""}`}>{r.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 2) Toplu aktarım */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-gray-700 w-40 shrink-0">2) Aktarılmamışları aktar</span>
          <button onClick={previewPush} disabled={busy || running || !connected}
            className="inline-flex items-center gap-2 border px-4 py-2 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
            {busy ? "Hesaplanıyor…" : "Ön İzleme"}
          </button>
          <button onClick={startPush} disabled={busy || running || !connected}
            className="inline-flex items-center gap-2 bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-emerald-700 disabled:opacity-50">
            {running ? "Aktarılıyor…" : "Aktarımı Başlat"}
          </button>
        </div>
        {pushPrev && (
          <div className="text-xs text-gray-700 space-y-2">
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>Aktarılacak ürün ailesi <b>{pushPrev.families}</b></span>
              <span>Stoksuz (atlanır) <b>{pushPrev.zero_stock}</b></span>
              <span>Ürün tipi yok <b className={pushPrev.no_product_type.length ? "text-red-600" : ""}>{pushPrev.no_product_type.length}</b></span>
              <span className="text-gray-500">Katalog çekimi {pushPrev.catalog_finished_at ? new Date(pushPrev.catalog_finished_at).toLocaleString("tr-TR") : "yok"}</span>
            </div>
            <div className="border rounded max-h-56 overflow-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 sticky top-0">
                  <tr className="text-left text-gray-600">
                    <th className="px-2 py-1.5">Stok Kodu</th>
                    <th className="px-2 py-1.5">Ürün</th>
                    <th className="px-2 py-1.5 text-right">Renk</th>
                    <th className="px-2 py-1.5">Amazon Tipi</th>
                  </tr>
                </thead>
                <tbody>
                  {pushPrev.rows.map((r) => (
                    <tr key={r.aile} className="border-t">
                      <td className="px-2 py-1.5 font-mono text-gray-500">{r.aile}</td>
                      <td className="px-2 py-1.5">{r.urunler}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.renk}</td>
                      <td className={`px-2 py-1.5 font-mono ${r.product_type ? "" : "text-red-600"}`}>{r.product_type || "yok"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {job && job.status && job.status !== "idle" && (
          <div className="text-xs space-y-2">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span>Durum <b className={job.status === "ok" ? "text-emerald-700" : job.status === "error" ? "text-red-600" : "text-amber-700"}>{job.status === "running" ? "çalışıyor" : job.status === "ok" ? "bitti" : "hata"}</b>{job.status === "running" && job.step ? ` · ${job.step}` : ""}</span>
              <span>Aile {job.done || 0}/{job.total || 0}</span>
              <span>Gönderilen SKU <b className="text-emerald-700">{job.pushed || 0}</b></span>
              <span>Hatalı SKU <b className={job.failed ? "text-red-600" : ""}>{job.failed || 0}</b></span>
              {job.zero_stock ? <span>Stoksuz atlanan {job.zero_stock}</span> : null}
              {job.dry_run ? <span className="text-amber-700 font-semibold">DRY-RUN (Amazon yazma kapalı)</span> : null}
              {job.finished_at && <span className="text-gray-500">{new Date(job.finished_at).toLocaleString("tr-TR")}</span>}
            </div>
            {running && (
              <div className="h-1.5 bg-gray-100 rounded overflow-hidden">
                <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
              </div>
            )}
            {job.error && <div className="text-red-600">{job.error}</div>}
            {job.reasons?.length > 0 && (
              <div className="border rounded max-h-56 overflow-auto">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr className="text-left text-gray-600">
                      <th className="px-2 py-1.5">Amazon Hata Gerekçesi</th>
                      <th className="px-2 py-1.5 text-right">Adet</th>
                      <th className="px-2 py-1.5">Örnek SKU</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.reasons.map((r) => (
                      <tr key={r.neden} className="border-t">
                        <td className="px-2 py-1.5">{r.neden}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{r.adet}</td>
                        <td className="px-2 py-1.5 font-mono text-gray-500">{r.ornek_sku}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
