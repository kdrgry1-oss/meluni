import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { ShieldCheck, Search, Download, Mail, MessageSquare } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

/**
 * Pazarlama İzinleri — e-posta / SMS ticari ileti izni verenler ve reddedenler.
 * Kaynak: İYS izin kayıtları + bülten aboneleri + kara liste (backend birleştirir).
 */
export default function Consents() {
  const [summary, setSummary] = useState(null);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [channel, setChannel] = useState("");
  const [status, setStatus] = useState("onay");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page, limit: "50" });
      if (channel) params.set("channel", channel);
      if (status) params.set("status", status);
      if (q.trim()) params.set("q", q.trim());
      const { data } = await axios.get(`${API}/admin/consents?${params}`, { headers: authHeaders() });
      setItems(data.items || []); setTotal(data.total || 0); setPages(data.pages || 1);
    } catch { toast.error("İzinler yüklenemedi"); }
    finally { setLoading(false); }
  }, [page, channel, status, q]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    axios.get(`${API}/admin/consents/summary`, { headers: authHeaders() }).then((r) => setSummary(r.data)).catch(() => {});
  }, []);

  const exportXlsx = async () => {
    setExporting(true);
    try {
      const params = new URLSearchParams();
      if (channel) params.set("channel", channel);
      if (status) params.set("status", status);
      if (q.trim()) params.set("q", q.trim());
      const r = await axios.get(`${API}/admin/consents/export.xlsx?${params}`, { headers: authHeaders(), responseType: "blob" });
      const url = URL.createObjectURL(r.data); const a = document.createElement("a");
      a.href = url; a.download = "pazarlama-izinleri.xlsx"; a.click(); URL.revokeObjectURL(url);
    } catch { toast.error("Excel oluşturulamadı"); }
    finally { setExporting(false); }
  };

  const fmt = (v) => (v ? new Date(v).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");

  return (
    <div className="space-y-5" data-testid="consents-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><ShieldCheck /> Pazarlama İzinleri</h1>
          <p className="text-sm text-gray-500 mt-1">E-posta ve SMS ticari ileti izni verenler / reddedenler (İYS kayıtları + bülten aboneleri; alıcı başına en son karar geçerli).</p>
        </div>
        <button onClick={exportXlsx} disabled={exporting} className="inline-flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium disabled:opacity-50" data-testid="consents-export">
          <Download size={16} /> {exporting ? "Hazırlanıyor…" : "Excel"}
        </button>
      </div>

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            ["E-posta izni VEREN", summary.email?.onay, "from-emerald-600 to-emerald-500", Mail],
            ["E-posta RET / kara liste", summary.email?.ret, "from-gray-600 to-gray-500", Mail],
            ["SMS izni VEREN", summary.sms?.onay, "from-indigo-600 to-indigo-500", MessageSquare],
            ["SMS RET", summary.sms?.ret, "from-gray-600 to-gray-500", MessageSquare],
          ].map(([l, v, cls, Icon]) => (
            <div key={l} className={`rounded-xl p-4 text-white bg-gradient-to-br ${cls}`}>
              <div className="flex items-center gap-2 text-xs opacity-90"><Icon size={14} /> {l}</div>
              <div className="text-2xl font-bold mt-1">{v ?? 0}</div>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (setPage(1), load())}
            placeholder="e-posta / telefon ara…" className="pl-9 pr-3 py-2 border rounded-lg text-sm w-64" />
        </div>
        <select value={channel} onChange={(e) => { setChannel(e.target.value); setPage(1); }} className="border rounded-lg px-2 py-2 text-sm">
          <option value="">Tüm kanallar</option><option value="email">E-posta</option><option value="sms">SMS</option>
        </select>
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="border rounded-lg px-2 py-2 text-sm">
          <option value="onay">İzin verenler</option><option value="ret">Reddedenler / kara liste</option><option value="">Hepsi</option>
        </select>
        <button onClick={() => { setPage(1); load(); }} className="px-4 py-2 bg-gray-900 text-white text-sm rounded-lg hover:bg-gray-700">Ara</button>
        <span className="text-sm text-gray-500 ml-auto">{total} kayıt</span>
      </div>

      <div className="bg-white border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Kanal</th><th className="text-left p-3">Alıcı</th><th className="text-left p-3">Ad Soyad</th>
              <th className="text-left p-3">Durum</th><th className="text-left p-3">Kaynak</th><th className="text-left p-3">Kayıt</th><th className="text-left p-3">Tarih</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Kayıt yok</td></tr>
            ) : items.map((r, i) => (
              <tr key={`${r.channel}-${r.recipient}-${i}`} className="border-t">
                <td className="p-3">{r.channel === "email" ? <span className="inline-flex items-center gap-1"><Mail size={13} /> E-posta</span> : <span className="inline-flex items-center gap-1"><MessageSquare size={13} /> SMS</span>}</td>
                <td className="p-3 font-mono text-xs">{r.recipient}</td>
                <td className="p-3">{r.name || "—"}</td>
                <td className="p-3">
                  <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${r.status === "onay" ? "bg-emerald-50 text-emerald-700 border-emerald-200" : "bg-red-50 text-red-600 border-red-200"}`}>
                    {r.status === "onay" ? "İZİN VERDİ" : (r.suppressed ? "KARA LİSTE" : "RET")}
                  </span>
                </td>
                <td className="p-3 text-xs text-gray-600">{r.source || "—"}</td>
                <td className="p-3 text-xs text-gray-500">{r.origin}{r.reported === false ? <span className="text-amber-600"> · İYS'ye bildirilmedi</span> : null}</td>
                <td className="p-3 text-xs text-gray-600 whitespace-nowrap">{fmt(r.at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {pages > 1 && (
          <div className="flex items-center justify-between p-3 border-t text-sm">
            <span className="text-gray-500">Sayfa {page} / {pages}</span>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="px-3 py-1 border rounded disabled:opacity-30">Önceki</button>
              <button disabled={page >= pages} onClick={() => setPage(page + 1)} className="px-3 py-1 border rounded disabled:opacity-30">Sonraki</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
