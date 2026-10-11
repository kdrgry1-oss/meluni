/**
 * KvkkRequests.jsx — KVKK Talepleri (veri dökümü + hesap silme) — yetki: admin.compliance.
 * Backend: GET  /api/admin/kvkk/requests
 *          POST /api/admin/kvkk/requests/{id}/apply   {force?, note?}
 *          POST /api/admin/kvkk/requests/{id}/close   {note}
 *          POST /api/admin/kvkk/reapply-tombstones    (süper admin — yedekten dönüş sonrası)
 * Müşterinin e-postası listede yalnız MASKELİ görünür (silme sonrası düz e-posta tutulmaz).
 */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { UserX, RefreshCw, RotateCcw, Check, X } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const STATUS_META = {
  completed: { label: "Tamamlandı", cls: "bg-green-50 text-green-700 border-green-200", dot: "bg-green-600" },
  pending_open_orders: { label: "Açık sipariş bekliyor", cls: "bg-amber-50 text-amber-800 border-amber-200", dot: "bg-amber-500" },
  processing: { label: "İşleniyor", cls: "bg-blue-50 text-blue-700 border-blue-200", dot: "bg-blue-600" },
  closed: { label: "Kapatıldı", cls: "bg-gray-50 text-gray-600 border-gray-200", dot: "bg-gray-400" },
};
const TYPE_LABEL = { delete: "Hesap silme", export: "Veri dökümü" };
const SOURCE_LABEL = { self: "Müşteri", admin: "Personel", admin_request: "Personel (talep)", reapply: "Yeniden uygulama" };

const fmt = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", timeZone: "Europe/Istanbul" });
  } catch { return iso; }
};

function Badge({ status }) {
  const m = STATUS_META[status] || { label: status || "—", cls: "bg-gray-50 text-gray-600 border-gray-200", dot: "bg-gray-400" };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-medium whitespace-nowrap ${m.cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${m.dot}`} />
      {m.label}
    </span>
  );
}

export default function KvkkRequests() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [counts, setCounts] = useState({});
  const [tombstones, setTombstones] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = new URLSearchParams({ page: String(page), limit: "50" });
      if (type) qs.set("type", type);
      if (status) qs.set("status", status);
      const r = await axios.get(`${API}/admin/kvkk/requests?${qs.toString()}`, auth());
      setItems(r.data?.items || []);
      setTotal(r.data?.total || 0);
      setPages(r.data?.pages || 1);
      setCounts(r.data?.counts || {});
      setTombstones(r.data?.tombstones || 0);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "KVKK talepleri yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, [page, type, status]);

  useEffect(() => { load(); }, [load]);

  const ask = (opts) => (window.appConfirm ? window.appConfirm(opts) : Promise.resolve(window.confirm(opts.description)));

  const apply = async (rq) => {
    const ok = await ask({ title: "Silme talebini uygula",
      description: "Üyenin kişisel verileri anonimleştirilecek; yasal saklama gereken sipariş/fatura kayıtları kişiden ayrılacak. Geri alınamaz.",
      confirmText: "Uygula", cancelText: "Vazgeç", variant: "danger" });
    if (!ok) return;
    setBusy(rq.id);
    try {
      await axios.post(`${API}/admin/kvkk/requests/${rq.id}/apply`, {}, auth());
      toast.success("Silme uygulandı");
      await load();
    } catch (e) {
      if (e?.response?.status === 409 && rq.status === "pending_open_orders") {
        const force = await ask({ title: "Açık sipariş / iade var", description: e?.response?.data?.detail || "Yine de uygulansın mı?",
          confirmText: "Yine de Uygula", cancelText: "Vazgeç", variant: "danger" });
        if (force) {
          try {
            await axios.post(`${API}/admin/kvkk/requests/${rq.id}/apply`, { force: true }, auth());
            toast.success("Silme uygulandı");
            await load();
          } catch (e2) { toast.error(e2?.response?.data?.detail || "Uygulanamadı"); }
        }
      } else {
        toast.error(e?.response?.data?.detail || "Uygulanamadı");
      }
    } finally {
      setBusy("");
    }
  };

  const close = async (rq) => {
    const note = window.prompt("Talebi kapatma notu (zorunlu):", "");
    if (!note || note.trim().length < 3) return;
    setBusy(rq.id);
    try {
      await axios.post(`${API}/admin/kvkk/requests/${rq.id}/close`, { note: note.trim() }, auth());
      toast.success("Talep kapatıldı");
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kapatılamadı");
    } finally {
      setBusy("");
    }
  };

  const reapply = async () => {
    const ok = await ask({ title: "Silmeleri yeniden uygula",
      description: "Yedekten geri dönüş sonrası kullanılır: mezar taşı bulunan hesaplarda geri gelen kişisel veriler yeniden silinir, eski bülten abonelikleri kaldırılır.",
      confirmText: "Çalıştır", cancelText: "Vazgeç", variant: "warning" });
    if (!ok) return;
    setBusy("reapply");
    try {
      const r = await axios.post(`${API}/admin/kvkk/reapply-tombstones`, {}, auth());
      const d = r.data || {};
      toast.success(`Tamamlandı · yeniden anonimleşen üye: ${d.users_reanonymized || 0} · kaldırılan bülten: ${d.newsletter_removed || 0}`);
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Çalıştırılamadı");
    } finally {
      setBusy("");
    }
  };

  const pendingCount = counts["delete:pending_open_orders"] || 0;
  const filterBtn = (val, label, cur, set) => (
    <button key={val || "all"} onClick={() => { setPage(1); set(val); }}
      className={`px-3 py-1.5 rounded-lg text-sm border ${cur === val ? "bg-black text-white border-black" : "bg-white text-gray-700 border-gray-200 hover:bg-gray-50"}`}>
      {label}
    </button>
  );

  return (
    <div data-testid="kvkk-requests" className="space-y-6 p-6 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="bg-black p-2.5 rounded-lg">
            <UserX className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-light tracking-tight">KVKK Talepleri</h1>
            <p className="text-sm text-gray-500">Veri dökümü ve hesap silme talepleri · mezar taşı: {tombstones}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={reapply} disabled={!!busy}
            className="inline-flex items-center gap-2 px-3 py-2 border rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
            <RotateCcw className="w-4 h-4" />
            <span>{busy === "reapply" ? "Çalışıyor…" : "Silmeleri Yeniden Uygula"}</span>
          </button>
          <button onClick={load} disabled={loading}
            className="inline-flex items-center gap-2 px-3 py-2 border rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            <span>Yenile</span>
          </button>
        </div>
      </div>

      {pendingCount > 0 && (
        <div className="border border-amber-200 bg-amber-50 text-amber-900 rounded-lg px-4 py-3 text-sm">
          {pendingCount} silme talebi, açık sipariş/iade tamamlanmayı bekliyor. İşlemler bitince “Uygula” ile silin.
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {filterBtn("", "Tümü", type, setType)}
        {filterBtn("delete", "Hesap silme", type, setType)}
        {filterBtn("export", "Veri dökümü", type, setType)}
        <span className="w-px h-6 bg-gray-200 mx-1" />
        {filterBtn("", "Her durum", status, setStatus)}
        {filterBtn("pending_open_orders", "Bekleyen", status, setStatus)}
        {filterBtn("completed", "Tamamlanan", status, setStatus)}
        {filterBtn("closed", "Kapatılan", status, setStatus)}
      </div>

      <div className="border rounded-lg bg-white overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-xs">
            <tr>
              <th className="text-left font-medium px-4 py-2">Tarih</th>
              <th className="text-left font-medium px-4 py-2">Tür</th>
              <th className="text-left font-medium px-4 py-2">Üye</th>
              <th className="text-left font-medium px-4 py-2">Kaynak</th>
              <th className="text-left font-medium px-4 py-2">Durum</th>
              <th className="text-left font-medium px-4 py-2">Not</th>
              <th className="text-right font-medium px-4 py-2">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {items.map((rq) => {
              const actionable = rq.type === "delete" && ["pending_open_orders", "processing"].includes(rq.status);
              return (
                <tr key={rq.id} className="border-t align-middle">
                  <td className="px-4 py-2 whitespace-nowrap tabular-nums">{fmt(rq.created_at)}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{TYPE_LABEL[rq.type] || rq.type}</td>
                  <td className="px-4 py-2 whitespace-nowrap font-mono text-xs">{rq.email_masked || "—"}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{SOURCE_LABEL[rq.source] || rq.source || "—"}{rq.actor_email ? ` · ${rq.actor_email}` : ""}</td>
                  <td className="px-4 py-2"><Badge status={rq.status} /></td>
                  <td className="px-4 py-2 text-gray-600">
                    {rq.status === "pending_open_orders" && rq.open_orders ? `${rq.open_orders} açık sipariş` : (rq.note || "—")}
                  </td>
                  <td className="px-4 py-2">
                    {actionable ? (
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={() => apply(rq)} disabled={!!busy}
                          className="inline-flex items-center justify-center gap-1.5 w-24 py-1.5 rounded-lg text-xs font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50">
                          <Check className="w-3.5 h-3.5 shrink-0" />
                          <span>Uygula</span>
                        </button>
                        <button onClick={() => close(rq)} disabled={!!busy}
                          className="inline-flex items-center justify-center gap-1.5 w-24 py-1.5 rounded-lg text-xs font-medium border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-50">
                          <X className="w-3.5 h-3.5 shrink-0" />
                          <span>Kapat</span>
                        </button>
                      </div>
                    ) : <div className="text-right text-gray-300">—</div>}
                  </td>
                </tr>
              );
            })}
            {!items.length && (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-gray-400">{loading ? "Yükleniyor…" : "Kayıt yok."}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <div className="flex items-center justify-between text-sm text-gray-600">
          <span>Toplam {total} kayıt</span>
          <div className="flex items-center gap-2">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}
              className="w-24 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-50">Önceki</button>
            <span className="tabular-nums w-16 text-center">{page} / {pages}</span>
            <button onClick={() => setPage((p) => Math.min(pages, p + 1))} disabled={page >= pages}
              className="w-24 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-50">Sonraki</button>
          </div>
        </div>
      )}
    </div>
  );
}
