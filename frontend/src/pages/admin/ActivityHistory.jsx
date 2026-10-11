import { useEffect, useState } from "react";
import axios from "axios";
import { Activity, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const initialFilters = { user: "", event: "", source: "", date_from: "", date_to: "", success: "" };

const dateText = (value) => {
  const parsed = value ? new Date(value) : null;
  return parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleString("tr-TR") : (value || "—");
};

export default function ActivityHistory() {
  const [filters, setFilters] = useState(initialFilters);
  const [applied, setApplied] = useState(initialFilters);
  const [data, setData] = useState({ items: [], total: 0, source_counts: {} });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const params = { page, limit: 100 };
      Object.entries(applied).forEach(([key, value]) => { if (value !== "") params[key] = value; });
      const response = await axios.get(`${API}/admin/activity`, {
        params, headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
      setData(response.data || { items: [], total: 0, source_counts: {} });
    } catch (error) {
      toast.error(error.response?.status === 403
        ? "Bu sayfa için audit.read yetkisi gerekiyor."
        : "İşlem geçmişi alınamadı.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [page, applied]);
  const apply = (event) => { event.preventDefault(); setPage(1); setApplied({ ...filters }); };
  const set = (key) => (event) => setFilters((current) => ({ ...current, [key]: event.target.value }));
  const pages = Math.max(1, Math.ceil((data.total || 0) / 100));

  return (
    <div className="p-6 space-y-5" data-testid="activity-history-page">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2"><Activity size={24} /> Kullanıcı İşlem Geçmişi</h1>
          <p className="text-sm text-gray-500 mt-1">Giriş, sipariş olayı, stok hareketi ve yönetim değişikliklerinin salt-okunur birleşik görünümü.</p>
        </div>
        <button onClick={load} className="border rounded-lg px-3 py-2 text-sm flex items-center gap-2 hover:bg-gray-50">
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Yenile
        </button>
      </div>

      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
        {data.coverage || "Geçmiş kayıtlarda bulunmayan kullanıcı, başarı veya oturum bilgileri tahmin edilmez. Kaynak koleksiyon her satırda gösterilir."}
        {data.scan_capped_sources?.length > 0 && <div className="mt-1 font-medium">Yoğun kaynaklarda son 2.000 kayıt tarandı: {data.scan_capped_sources.join(", ")}. Tarih filtresiyle aralığı daraltın.</div>}
      </div>

      <form onSubmit={apply} className="bg-white border rounded-xl p-4 grid grid-cols-1 md:grid-cols-7 gap-3">
        <input aria-label="Kullanıcı" value={filters.user} onChange={set("user")} placeholder="Kullanıcı / e-posta" className="border rounded px-3 py-2 text-sm" />
        <input aria-label="Olay" value={filters.event} onChange={set("event")} placeholder="Olay" className="border rounded px-3 py-2 text-sm" />
        <input aria-label="Kaynak" value={filters.source} onChange={set("source")} placeholder="Kaynak" className="border rounded px-3 py-2 text-sm" />
        <input aria-label="Başlangıç tarihi" type="date" value={filters.date_from} onChange={set("date_from")} className="border rounded px-3 py-2 text-sm" />
        <input aria-label="Bitiş tarihi" type="date" value={filters.date_to} onChange={set("date_to")} className="border rounded px-3 py-2 text-sm" />
        <select aria-label="Başarı" value={filters.success} onChange={set("success")} className="border rounded px-3 py-2 text-sm">
          <option value="">Tüm sonuçlar</option><option value="true">Başarılı</option><option value="false">Başarısız</option>
        </select>
        <button className="bg-gray-950 text-white rounded px-3 py-2 text-sm flex items-center justify-center gap-2"><Search size={15} /> Uygula</button>
      </form>

      <div className="bg-white border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr>
              <th className="p-3 text-left">Tarih / Saat</th><th className="p-3 text-left">Kullanıcı</th>
              <th className="p-3 text-left">Olay</th><th className="p-3 text-left">Kaynak</th>
              <th className="p-3 text-left">Varlık</th><th className="p-3 text-left">Sonuç</th><th className="p-3 text-left">Özet</th>
            </tr></thead>
            <tbody className="divide-y">
              {loading ? <tr><td colSpan="7" className="p-10 text-center text-gray-500">Yükleniyor…</td></tr>
                : data.items.length === 0 ? <tr><td colSpan="7" className="p-10 text-center text-gray-500">Kayıt bulunamadı.</td></tr>
                : data.items.map((item, index) => <tr key={`${item.origin_collection}-${item.id}-${index}`} className="align-top hover:bg-gray-50">
                  <td className="p-3 whitespace-nowrap">{dateText(item.date)}</td>
                  <td className="p-3" title={[item.context?.ip, item.context?.user_agent].filter(Boolean).join(" · ")}><div>{item.actor?.email || item.actor?.id || "Kayıtlı değil"}</div><div className="text-xs text-gray-400">{item.actor?.login_method || "—"}{item.context?.session_hash ? ` · ${item.context.session_hash}` : ""}</div></td>
                  <td className="p-3 font-medium">{item.event}</td>
                  <td className="p-3"><div>{item.source}</div><div className="text-xs text-gray-400">{item.origin_collection}</div></td>
                  <td className="p-3"><div>{item.entity?.type || "—"}</div><div className="font-mono text-xs text-gray-400">{item.entity?.id || "—"}</div></td>
                  <td className="p-3">{item.success === true ? <span className="text-emerald-700">Başarılı</span>
                    : item.success === false ? <span className="text-red-700">Başarısız</span>
                    : <span className="text-gray-400">Kayıtlı değil</span>}</td>
                  <td className="p-3 max-w-sm"><div>{item.summary}</div>{item.legacy_incomplete && <div className="text-[11px] text-amber-600 mt-1">Eski kayıt — bazı alanlar yok</div>}
                    {item.details && Object.keys(item.details).length > 0 && <details className="mt-1 text-xs text-gray-500"><summary className="cursor-pointer">Maskelenmiş değişiklik</summary><pre className="mt-1 whitespace-pre-wrap break-all max-h-48 overflow-auto">{JSON.stringify(item.details, null, 2)}</pre></details>}
                  </td>
                </tr>)}
            </tbody>
          </table>
        </div>
        <div className="border-t p-3 flex items-center justify-between text-sm">
          <span>{data.total || 0} kayıt</span><div className="flex gap-2 items-center">
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="border rounded px-3 py-1 disabled:opacity-40">Önceki</button>
            <span>{page} / {pages}</span>
            <button disabled={page >= pages} onClick={() => setPage((p) => p + 1)} className="border rounded px-3 py-1 disabled:opacity-40">Sonraki</button>
          </div>
        </div>
      </div>
    </div>
  );
}
