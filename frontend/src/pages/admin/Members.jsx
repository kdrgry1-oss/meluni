import { useState, useEffect, useMemo } from "react";
import axios from "axios";
import { openAdminDocument } from "../../lib/adminDocuments";
import { toast } from "sonner";
import {
  Users, Search, UserPlus, Eye, Mail, Phone, ShoppingCart, TrendingUp,
  Crown, Star, UserCheck, UserX, X, Trash2, Edit,
  RotateCcw, Ban, Download,
} from "lucide-react";
import { BarChart, Bar, XAxis, Tooltip as RTooltip, ResponsiveContainer } from "recharts";

const tl = (n) => "₺" + (Number(n) || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const SEGMENT_META = {
  vip: { label: "VIP", color: "bg-amber-100 text-amber-800 border-amber-200", icon: Crown },
  returning: { label: "Sadık", color: "bg-emerald-100 text-emerald-800 border-emerald-200", icon: Star },
  new: { label: "Yeni", color: "bg-blue-100 text-blue-800 border-blue-200", icon: UserCheck },
  prospect: { label: "Aday", color: "bg-gray-100 text-gray-700 border-gray-200", icon: UserX },
};

// Segment atama kriteri (backend _refresh_member_stats ile AYNI eşikler) → rozet tooltip'i.
function segReason(seg, member) {
  const o = member?.orders_count, s = member?.total_spent;
  const info = (o != null && s != null) ? `  •  bu üye: ${o} sipariş, ${tl(s)}` : "";
  const base = {
    vip: "VIP — toplam harcaması ₺5.000 ve üzeri",
    returning: "Sadık — 2 veya daha fazla sipariş vermiş (harcaması ₺5.000 altı)",
    new: "Yeni — ilk (tek) siparişini vermiş",
    prospect: "Aday — üye olmuş ama henüz hiç siparişi yok",
  }[seg] || "Aday — henüz siparişi yok";
  return base + info;
}

function SegmentBadge({ seg, member }) {
  const m = SEGMENT_META[seg] || SEGMENT_META.prospect;
  const Icon = m.icon;
  return (
    <span title={segReason(seg, member)}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-medium cursor-help ${m.color}`}>
      <Icon size={12} /> {m.label}
    </span>
  );
}

export default function Members() {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [search, setSearch] = useState("");
  const [segment, setSegment] = useState("");
  const [source, setSource] = useState("");
  const [detailId, setDetailId] = useState(null);
  const [detail, setDetail] = useState(null);
  // KVKK: üye telefonu / adres defteri maskeli gelir; "Göster" tam değeri açar (sayaçlı + denetimli).
  const [revealing, setRevealing] = useState(false);
  const revealContact = async (id) => {
    setRevealing(true);
    try {
      const { data } = await axios.get(`${API}/admin/members/${id}/contact`, { headers: authHeaders() });
      setDetail((d) => (d && d.member?.id === id ? {
        ...d,
        member: { ...d.member, phone: data.phone, contact_masked: false },
        addresses: data.addresses?.length ? data.addresses : d.addresses,
      } : d));
    } catch (e) {
      toast.error(e?.response?.data?.detail || "İletişim bilgisi açılamadı");
    } finally {
      setRevealing(false);
    }
  };
  const [m360, setM360] = useState(null);
  const [d360, setD360] = useState({ start: "", end: "" });
  const [full360, setFull360] = useState(false);
  const [sortBy, setSortBy] = useState("created");
  const [sortDir, setSortDir] = useState("desc");
  const [dateField, setDateField] = useState("created");
  const [dStart, setDStart] = useState("");
  const [dEnd, setDEnd] = useState("");

  const toggleSort = (k) => {
    if (sortBy === k) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortBy(k); setSortDir("desc"); }
    setPage(1);
  };
  // Tıklanabilir sıralama başlığı (artan/azalan ok). cls ile hizalama.
  const Th = ({ label, k, cls = "text-left" }) => (
    <th className={`p-3 cursor-pointer select-none hover:text-gray-900 ${cls}`} onClick={() => toggleSort(k)} title="Sırala">
      <span className="inline-flex items-center gap-1">{label}{sortBy === k && <span className="text-[10px]">{sortDir === "asc" ? "▲" : "▼"}</span>}</span>
    </th>
  );

  const open360Print = async () => {
    const qs = ["print=1"];
    if (d360.start) qs.push(`start=${d360.start}`);
    if (d360.end) qs.push(`end=${d360.end}`);
    try { await openAdminDocument(`/admin/members/${detailId}/360/print?${qs.join("&")}`); }
    catch (err) { toast.error(err.message || "Rapor açılamadı"); }
  };

  const export360CSV = () => {
    if (!m360) return;
    const k = m360.kpi, a = m360.advanced || {};
    const L = [];
    L.push(["Müşteri 360", `${detail?.member?.first_name || ""} ${detail?.member?.last_name || ""}`.trim() || detail?.member?.email || ""]);
    L.push(["Dönem", `${d360.start || "…"} - ${d360.end || "…"}`]);
    L.push([]);
    L.push(["Metrik", "Değer"]);
    const rows = [
      ["Net Ciro", k.net_revenue], ["Brüt Ciro", k.gross_revenue], ["Sipariş", k.orders], ["Ort. Sepet (AOV)", k.aov],
      ["İade Tutarı", k.returns_amount], ["İade Adedi", k.returns_count], ["İptal Tutarı", k.cancels_amount], ["İptal Adedi", k.cancels_count],
      ["İade Oranı %", k.return_rate], ["Ürün Adedi", k.items_total], ["Son Sipariş (gün önce)", k.recency_days], ["Üyelik Yaşı (gün)", k.tenure_days],
      ["RFM Segment", a.rfm?.segment], ["RFM (R/F/M)", `${a.rfm?.r}/${a.rfm?.f}/${a.rfm?.m}`], ["Kayıp Riski", a.churn?.level],
      ["CLV (tahmini)", a.clv_estimate], ["Tekrar Alım (gün)", a.repurchase_days],
      ["Favori Beden", (a.fav_size || []).map((x) => `${x.name}(${x.count})`).join(" ")],
      ["Favori Renk", (a.fav_color || []).map((x) => `${x.name}(${x.count})`).join(" ")],
    ];
    rows.forEach((r) => L.push(r));
    L.push([]); L.push(["Sipariş No", "Tarih", "Durum", "Tutar"]);
    (m360.orders || []).forEach((o) => L.push([o.order_number, (o.created_at || "").slice(0, 10), o.status, o.total]));
    const csv = "﻿" + L.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(";")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = `musteri360-${detailId}.csv`; link.click();
    URL.revokeObjectURL(url);
  };
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ email: "", first_name: "", last_name: "", phone: "", password: "" });
  // DENETİM FIX (#41): üye gruplarını yükle — detay modalında gruba atama için.

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page, limit: "25", sort: sortBy, dir: sortDir, date_field: dateField });
      if (search) params.set("search", search);
      if (segment) params.set("segment", segment);
      if (source) params.set("source", source);
      if (dStart) params.set("start", dStart);
      if (dEnd) params.set("end", dEnd);
      const { data } = await axios.get(`${API}/admin/members?${params}`, { headers: authHeaders() });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
    } catch (e) { toast.error("Üyeler yüklenemedi"); }
    finally { setLoading(false); }
  };

  // Excel: ekrandaki filtre + sıralama ile TÜM sayfalar (segment, net sipariş/harcama, iade, kaynak…)
  const [exporting, setExporting] = useState(false);
  const exportMembersXlsx = async () => {
    setExporting(true);
    try {
      const params = new URLSearchParams({ sort: sortBy, dir: sortDir, date_field: dateField });
      if (search) params.set("search", search);
      if (segment) params.set("segment", segment);
      if (source) params.set("source", source);
      if (dStart) params.set("start", dStart);
      if (dEnd) params.set("end", dEnd);
      const r = await axios.get(`${API}/admin/members/export.xlsx?${params}`, { headers: authHeaders(), responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = url; a.download = `uyeler-${new Date().toISOString().slice(0, 10)}.xlsx`; a.click();
      URL.revokeObjectURL(url);
      toast.success("Excel indirildi");
    } catch (e) {
      toast.error(e?.response?.status === 403
        ? "Üye listesini dışa aktarma yetkiniz yok (customers.export)."
        : "Excel oluşturulamadı");
    }
    finally { setExporting(false); }
  };

  const loadStats = async () => {
    try {
      const { data } = await axios.get(`${API}/admin/members/stats`, { headers: authHeaders() });
      setStats(data);
    } catch (_) {}
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [page, segment, source, sortBy, sortDir, dateField, dStart, dEnd]);
  useEffect(() => { loadStats(); }, []);
  const openDetail = async (id) => {
    setDetailId(id);
    setDetail(null);
    setM360(null);
    setD360({ start: "", end: "" });
    setFull360(false);  // yarım sayfa (drawer) — kullanıcı tercihi; "Tam ekran" butonuyla genişletilebilir
    try {
      const { data } = await axios.get(`${API}/admin/members/${id}`, { headers: authHeaders() });
      setDetail(data);
    } catch (_) { toast.error("Detay alınamadı"); }
    fetch360(id, {});
  };

  const fetch360 = async (id, rng) => {
    try {
      const qs = [];
      if (rng?.start) qs.push(`start=${rng.start}`);
      if (rng?.end) qs.push(`end=${rng.end}`);
      const { data } = await axios.get(`${API}/admin/members/${id}/360${qs.length ? `?${qs.join("&")}` : ""}`, { headers: authHeaders() });
      setM360(data);
    } catch (_) { /* sessiz */ }
  };

  const handleCreate = async () => {
    if (!form.email) return toast.warning("E-posta zorunlu");
    try {
      const { data } = await axios.post(`${API}/admin/members`, form, { headers: authHeaders() });
      if (data?.temp_password) {
        toast.success(`Üye eklendi. Geçici şifre: ${data.temp_password} (bir kez gösterilir — müşteriye iletin, Hesabım'dan değiştirebilir)`, { duration: 20000 });
      } else {
        toast.success("Üye eklendi");
      }
      setShowCreate(false);
      setForm({ email: "", first_name: "", last_name: "", phone: "", password: "" });
      load(); loadStats();
    } catch (e) { toast.error(e?.response?.data?.detail || "Eklenemedi"); }
  };

  // KVKK uyumlu silme: müşterinin "Hesabımı sil" akışıyla AYNI anonimleştirme (backend kvkk.anonymize_user).
  // Açık sipariş/iade varsa sunucu 409 döner → personele ikinci (zorla) onay sorulur.
  const handleDelete = async (id) => {
    const ok = await window.appConfirm({
      title: "Üyeyi sil (KVKK)",
      description: "Üyenin kişisel verileri (ad, e-posta, telefon, adresler, favoriler, cihazlar, bülten kaydı) "
        + "kalıcı olarak silinir ve pazarlama izni İYS'ye RET olarak bildirilir. Yasal saklama gereken "
        + "sipariş ve fatura kayıtları kişiden ayrılarak saklanır. Bu işlem geri alınamaz.",
      confirmText: "Evet, Sil",
      cancelText: "Vazgeç",
      variant: "danger",
    });
    if (!ok) return;
    const run = async (force) => {
      await axios.delete(`${API}/admin/members/${id}${force ? "?force=true" : ""}`, { headers: authHeaders() });
      toast.success("Üye silindi (kişisel veriler anonimleştirildi)");
      load(); loadStats();
      if (detailId === id) setDetailId(null);
    };
    try {
      await run(false);
    } catch (e) {
      if (e?.response?.status === 409) {
        const again = await window.appConfirm({
          title: "Açık sipariş / iade var",
          description: e?.response?.data?.detail || "Bu üyenin devam eden işlemleri var. Yine de silinsin mi?",
          confirmText: "Yine de Sil",
          cancelText: "Vazgeç",
          variant: "danger",
        });
        if (!again) return;
        try { await run(true); } catch (e2) { toast.error(e2?.response?.data?.detail || "Silinemedi"); }
        return;
      }
      toast.error(e?.response?.data?.detail || "Silinemedi");
    }
  };

  const statCards = useMemo(() => {
    if (!stats) return [];
    return [
      { label: "Toplam Üye", val: stats.total, color: "from-slate-900 to-slate-700" },
      { label: "Son 30 gün", val: stats.new_last_30_days, color: "from-blue-600 to-blue-500" },
      { label: "VIP", val: stats.segments?.vip || 0, color: "from-amber-600 to-amber-500" },
      { label: "Sadık", val: stats.segments?.returning || 0, color: "from-emerald-600 to-emerald-500" },
      { label: "Yeni", val: stats.segments?.new || 0, color: "from-sky-600 to-sky-500" },
      { label: "Aday", val: stats.segments?.prospect || 0, color: "from-gray-500 to-gray-400" },
    ];
  }, [stats]);

  return (
    <div className="space-y-6" data-testid="members-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Users className="text-slate-700" /> Üyeler</h1>
          <p className="text-sm text-gray-500 mt-1">Kayıtlı üyeleri yönetin, segmentlere ayırın ve kaynaklarını görün.</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={exportMembersXlsx}
            disabled={exporting}
            data-testid="export-members-btn"
            title="Ekrandaki filtre/sıralamayla üye listesini Excel indir (segment, net sipariş/harcama, iade, kaynak…)"
            className="inline-flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium disabled:opacity-50"
          >
            <Download size={16} /> {exporting ? "Hazırlanıyor…" : "Excel"}
          </button>
          <button
            onClick={() => setShowCreate(true)}
            data-testid="add-member-btn"
            className="inline-flex items-center gap-2 px-4 py-2 bg-black text-white rounded-lg hover:bg-gray-800 text-sm font-medium"
          >
            <UserPlus size={16} /> Yeni Üye
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3" data-testid="members-stats">
        {statCards.map((c) => (
          <div key={c.label} className={`rounded-xl p-4 text-white bg-gradient-to-br ${c.color}`}>
            <div className="text-xs uppercase tracking-wide opacity-80">{c.label}</div>
            <div className="text-3xl font-bold mt-1">{c.val}</div>
          </div>
        ))}
      </div>

      {/* Acquisition by channel */}
      {stats?.acquisition_by_channel?.length > 0 && (
        <div className="bg-white rounded-xl border p-4">
          <h3 className="font-semibold text-sm mb-3 text-gray-700">Üye Edinim Kaynakları (First Touch)</h3>
          <div className="flex flex-wrap gap-2">
            {stats.acquisition_by_channel.map((c) => (
              <button
                key={c.channel}
                onClick={() => setSource(source === c.channel ? "" : c.channel)}
                className={`px-3 py-1.5 text-xs rounded-full border transition ${source === c.channel ? "bg-black text-white border-black" : "bg-gray-50 text-gray-700 border-gray-200 hover:border-gray-400"}`}
              >
                {c.channel} <span className="ml-1 opacity-70">({c.members})</span>
              </button>
            ))}
            {source && (
              <button onClick={() => setSource("")} className="px-2 py-1.5 text-xs text-red-600 hover:underline">× kaldır</button>
            )}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap bg-white rounded-xl border p-3">
        <div className="relative flex-1 min-w-[240px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && (setPage(1), load())}
            placeholder="E-posta, ad, soyad veya telefon ara..."
            data-testid="members-search"
            className="w-full pl-9 pr-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-black"
          />
        </div>
        <select value={segment} onChange={(e) => { setSegment(e.target.value); setPage(1); }} className="px-3 py-2 border rounded-lg text-sm">
          <option value="">Tüm Segmentler</option>
          <option value="vip">VIP</option>
          <option value="returning">Sadık</option>
          <option value="new">Yeni</option>
          <option value="prospect">Aday</option>
        </select>
        <select value={dateField} onChange={(e) => { setDateField(e.target.value); setPage(1); }} className="px-2 py-2 border rounded-lg text-sm bg-white" title="Tarih alanı">
          <option value="created">Katılım tarihi</option>
          <option value="last_order">Son sipariş tarihi</option>
        </select>
        <input type="date" value={dStart} onChange={(e) => { setDStart(e.target.value); setPage(1); }} className="px-2 py-2 border rounded-lg text-sm" title="Başlangıç" />
        <span className="text-gray-400 text-sm">–</span>
        <input type="date" value={dEnd} onChange={(e) => { setDEnd(e.target.value); setPage(1); }} className="px-2 py-2 border rounded-lg text-sm" title="Bitiş" />
        {(dStart || dEnd) && <button onClick={() => { setDStart(""); setDEnd(""); setPage(1); }} className="px-2 py-2 border rounded-lg text-sm text-gray-500" title="Tarihi temizle">✕</button>}
        <button onClick={() => { setPage(1); load(); }} className="px-4 py-2 bg-gray-900 text-white text-sm rounded-lg hover:bg-gray-700">Ara</button>
      </div>

      {/* Table — tablet/iPad'de kırpma yerine yatay kaydırma */}
      <div className="bg-white rounded-xl border overflow-x-auto">
        <table className="w-full text-sm min-w-[900px]">
          <thead className="bg-gray-50 text-gray-600 text-xs uppercase">
            <tr>
              <Th label="Üye" k="name" />
              <th className="text-left p-3">İletişim</th>
              <Th label="Sipariş" k="orders" cls="text-center" />
              <Th label="Toplam Harcama" k="gross" cls="text-right" />
              <Th label="Net Harcama" k="spent" cls="text-right" />
              <Th label="Ort. Sepet" k="aov" cls="text-right" />
              <Th label="Segment" k="segment" cls="text-center" />
              <Th label="Son Sipariş" k="last_order" />
              <Th label="Katılım" k="created" />
              <Th label="İade" k="returns" cls="text-center" />
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={11} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={11} className="p-8 text-center text-gray-400">Henüz üye yok.</td></tr>
            ) : items.map((m) => (
              <tr key={m.id} onClick={() => openDetail(m.id)} className="border-t hover:bg-gray-50 cursor-pointer" data-testid={`member-row-${m.id}`}>
                <td className="p-3">
                  <div className="font-medium">{m.first_name || m.last_name ? `${m.first_name || ""} ${m.last_name || ""}`.trim() : "—"}</div>
                  <div className="text-xs text-gray-500 mt-0.5">#{m.id.slice(0, 8)}</div>
                </td>
                <td className="p-3">
                  <div className="flex items-center gap-1 text-gray-700"><Mail size={12} /> {m.email}</div>
                  {m.phone && <div className="flex items-center gap-1 text-gray-500 text-xs mt-0.5"><Phone size={11} /> {m.phone}</div>}
                </td>
                <td className="p-3 text-center font-medium">{m.orders_count}</td>
                {/* Toplam = brüt (sayılan siparişlerin toplamı); Net = onaylı iadeler düşülmüş. */}
                <td className="p-3 text-right tabular-nums text-gray-700" title="Sayılan siparişlerin toplam tutarı (iade düşülmeden)">₺{(m.gross_spent ?? m.total_spent ?? 0).toLocaleString("tr-TR", { minimumFractionDigits: 2 })}</td>
                <td className="p-3 text-right font-semibold tabular-nums" title={`Onaylı iadeler düşülmüş net harcama${(Number(m.gross_spent ?? 0) - Number(m.total_spent ?? 0)) > 0.005 ? ` · düşülen iade ${tl(Number(m.gross_spent) - Number(m.total_spent))}` : ""}`}>
                  ₺{(m.total_spent || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2 })}
                  {(Number(m.gross_spent ?? 0) - Number(m.total_spent ?? 0)) > 0.005 && (
                    <div className="text-[10px] font-normal text-rose-600">−{tl(Number(m.gross_spent) - Number(m.total_spent))} iade</div>
                  )}
                </td>
                <td className="p-3 text-right text-xs text-gray-600 tabular-nums">{m.orders_count > 0 ? "₺" + (Number(m.total_spent || 0) / m.orders_count).toLocaleString("tr-TR", { maximumFractionDigits: 0 }) : "—"}</td>
                <td className="p-3 text-center"><SegmentBadge seg={m.segment} member={m} /></td>
                <td className="p-3 text-xs text-gray-500 whitespace-nowrap">{m.last_order_at ? new Date(m.last_order_at).toLocaleDateString("tr-TR") : "—"}</td>
                <td className="p-3 text-xs text-gray-500 whitespace-nowrap">{m.created_at ? new Date(m.created_at).toLocaleDateString("tr-TR") : "—"}</td>
                <td className="p-3 text-center">
                  {m.returns_count > 0 ? (
                    <span title={`${m.returns_count} iade · ${tl(m.returns_amount)} tutarında`}
                      className="inline-flex items-center gap-1 text-xs font-medium text-rose-700 bg-rose-50 border border-rose-200 rounded-full px-2 py-0.5">
                      <RotateCcw size={11} /> {m.returns_count}
                    </span>
                  ) : (
                    <span className="text-xs text-gray-300">—</span>
                  )}
                </td>
                <td className="p-3 text-right" onClick={(e) => e.stopPropagation()}>
                  <button onClick={() => openDetail(m.id)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded" title="Detay"><Eye size={15} /></button>
                  <button onClick={() => handleDelete(m.id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded" title="Sil"><Trash2 size={15} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* Pagination */}
        {pages > 1 && (
          <div className="flex items-center justify-between p-3 border-t text-sm text-gray-600">
            <div>Toplam {total} üye</div>
            <div className="flex gap-1">
              <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="px-3 py-1 border rounded disabled:opacity-30">Önceki</button>
              <span className="px-3 py-1">{page} / {pages}</span>
              <button disabled={page >= pages} onClick={() => setPage(page + 1)} className="px-3 py-1 border rounded disabled:opacity-30">Sonraki</button>
            </div>
          </div>
        )}
      </div>

      {/* Detail drawer */}
      {detailId && (
        <div className="fixed inset-0 z-50 bg-black/40 flex justify-end" onClick={() => setDetailId(null)}>
          <div className={`w-full ${full360 ? "max-w-full" : "max-w-2xl"} bg-white h-full overflow-y-auto transition-[max-width]`} onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b p-4 sticky top-0 bg-white">
              <h3 className="font-bold text-lg">Üye Detayı</h3>
              <button onClick={() => setDetailId(null)} className="p-1.5 hover:bg-gray-100 rounded"><X size={18} /></button>
            </div>
            {!detail ? (
              <div className="p-8 text-center text-gray-400">Yükleniyor…</div>
            ) : (
              <div className="p-5 space-y-5">
                <div className="flex items-start gap-4">
                  <div className="w-14 h-14 rounded-full bg-gradient-to-br from-gray-800 to-gray-600 text-white flex items-center justify-center text-xl font-bold">
                    {(detail.member.first_name?.[0] || detail.member.email?.[0] || "?").toUpperCase()}
                  </div>
                  <div className="flex-1">
                    <div className="text-xl font-bold">{detail.member.first_name} {detail.member.last_name}</div>
                    <div className="text-sm text-gray-500 inline-flex items-center gap-2 flex-wrap">
                      <span>{detail.member.email} {detail.member.phone && ` · ${detail.member.phone}`}</span>
                      {detail.member.contact_masked && (
                        <button type="button" onClick={() => revealContact(detail.member.id)} disabled={revealing}
                          className="inline-flex items-center gap-1 px-2 py-0.5 text-xs border rounded hover:bg-gray-50 disabled:opacity-50"
                          title="Tam telefon ve adresler (KVKK: görüntüleme kayıt altına alınır)" data-testid="member-contact-reveal">
                          {revealing ? "Açılıyor…" : "Göster"}
                        </button>
                      )}
                    </div>
                    <div className="mt-2 flex gap-2 items-center">
                      <SegmentBadge seg={detail.member.segment} member={detail.member} />
                      <span className="text-xs text-gray-500">Katılım: {new Date(detail.member.created_at).toLocaleDateString("tr-TR")}</span>
                    </div>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div className="bg-gray-50 rounded-lg p-3">
                    <div className="text-xs text-gray-500 uppercase">Sipariş</div>
                    <div className="text-2xl font-bold">{detail.member.orders_count}</div>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3">
                    <div className="text-xs text-gray-500 uppercase">Net Harcama</div>
                    <div className="text-2xl font-bold">₺{(detail.member.total_spent || 0).toLocaleString("tr-TR")}</div>
                    {(Number(detail.member.gross_spent ?? 0) - Number(detail.member.total_spent ?? 0)) > 0.005 && (
                      <div className="text-[11px] text-gray-500">Toplam {tl(detail.member.gross_spent)} · iade −{tl(Number(detail.member.gross_spent) - Number(detail.member.total_spent))}</div>
                    )}
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3">
                    <div className="text-xs text-gray-500 uppercase">Son Sipariş</div>
                    <div className="text-sm font-medium mt-1">{detail.member.last_order_at ? new Date(detail.member.last_order_at).toLocaleDateString("tr-TR") : "—"}</div>
                  </div>
                </div>

                {/* ── MÜŞTERİ 360 — tarih bazlı analiz ── */}
                <div className="border-t pt-4">
                  <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
                    <h4 className="font-semibold text-sm flex items-center gap-1"><TrendingUp size={14} /> Müşteri 360 — Tarih Bazlı Analiz</h4>
                    <div className="flex items-center gap-1.5">
                      <input type="date" value={d360.start} onChange={(e) => setD360((s) => ({ ...s, start: e.target.value }))} className="border rounded px-2 py-1 text-xs" />
                      <span className="text-gray-400 text-xs">–</span>
                      <input type="date" value={d360.end} onChange={(e) => setD360((s) => ({ ...s, end: e.target.value }))} className="border rounded px-2 py-1 text-xs" />
                      <button onClick={() => fetch360(detailId, d360)} className="px-2 py-1 bg-gray-900 text-white rounded text-xs">Uygula</button>
                      {(d360.start || d360.end) && <button onClick={() => { setD360({ start: "", end: "" }); fetch360(detailId, {}); }} className="px-2 py-1 border rounded text-xs">Temizle</button>}
                      <span className="w-px h-5 bg-gray-200 mx-1" />
                      <button onClick={open360Print} title="PDF olarak yazdır" className="px-2 py-1 border rounded text-xs hover:bg-gray-50">PDF</button>
                      <button onClick={export360CSV} title="Excel (CSV) indir" className="px-2 py-1 border rounded text-xs hover:bg-gray-50">Excel</button>
                      <button onClick={() => setFull360((f) => !f)} title="Tam ekran" className="px-2 py-1 border rounded text-xs hover:bg-gray-50">{full360 ? "Küçült" : "Tam ekran"}</button>
                    </div>
                  </div>
                  {!m360 ? <div className="text-xs text-gray-400 py-4 text-center">Analiz yükleniyor…</div> : (
                    <>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                        {[
                          { l: "Net Ciro", v: tl(m360.kpi.net_revenue), c: "text-emerald-700", s: `Brüt ${tl(m360.kpi.gross_revenue)}` },
                          { l: "Sipariş", v: m360.kpi.orders, s: `${m360.kpi.items_total} ürün` },
                          { l: "Ort. Sepet", v: tl(m360.kpi.aov) },
                          { l: "İade", v: tl(m360.kpi.returns_amount), c: "text-rose-700", s: `${m360.kpi.returns_count} adet` },
                          { l: "İptal", v: tl(m360.kpi.cancels_amount), c: "text-red-700", s: `${m360.kpi.cancels_count} adet` },
                          { l: "İade Oranı", v: `%${m360.kpi.return_rate}` },
                          { l: "Son Sipariş", v: m360.kpi.recency_days != null ? `${m360.kpi.recency_days} gün önce` : "—" },
                          { l: "Üyelik Yaşı", v: m360.kpi.tenure_days != null ? `${m360.kpi.tenure_days} gün` : "—" },
                        ].map((t, i) => (
                          <div key={i} className="bg-gray-50 rounded-lg p-2.5 border border-gray-100">
                            <div className="text-[10px] text-gray-500 uppercase tracking-wide">{t.l}</div>
                            <div className={`text-base font-bold ${t.c || "text-gray-900"}`}>{t.v}</div>
                            {t.s && <div className="text-[10px] text-gray-400">{t.s}</div>}
                          </div>
                        ))}
                      </div>

                      {m360.advanced && (
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200"
                            title="RFM = Recency (Yakınlık: son siparişten bu yana geçen süre) · Frequency (Sıklık: toplam sipariş sayısı) · Monetary (Parasal: toplam harcama). Her biri 1–5 arası puanlanır.">
                            RFM · {m360.advanced.rfm.segment} <span className="opacity-70">(Yakınlık R{m360.advanced.rfm.r} · Sıklık F{m360.advanced.rfm.f} · Parasal M{m360.advanced.rfm.m})</span>
                          </span>
                          {m360.advanced.churn && (
                            <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold border ${m360.advanced.churn.level === "high" ? "bg-red-50 text-red-700 border-red-200" : m360.advanced.churn.level === "medium" ? "bg-amber-50 text-amber-700 border-amber-200" : "bg-emerald-50 text-emerald-700 border-emerald-200"}`} title={m360.advanced.churn.reason}>
                              Kayıp riski: {{ low: "Düşük", medium: "Orta", high: "Yüksek" }[m360.advanced.churn.level] || m360.advanced.churn.level}
                            </span>
                          )}
                          <span className="px-2 py-1 rounded-full text-xs bg-gray-100 text-gray-700 border border-gray-200">CLV ~ {tl(m360.advanced.clv_estimate)}</span>
                          {m360.advanced.repurchase_days && <span className="px-2 py-1 rounded-full text-xs bg-gray-100 text-gray-700 border border-gray-200">Tekrar alım: {m360.advanced.repurchase_days} günde bir</span>}
                          {m360.advanced.fav_size?.length > 0 && <span className="px-2 py-1 rounded-full text-xs bg-gray-100 text-gray-700 border border-gray-200">Favori beden: {m360.advanced.fav_size.map((x) => x.name).join(", ")}</span>}
                          {m360.advanced.fav_color?.length > 0 && <span className="px-2 py-1 rounded-full text-xs bg-gray-100 text-gray-700 border border-gray-200">Favori renk: {m360.advanced.fav_color.map((x) => x.name).join(", ")}</span>}
                        </div>
                      )}

                      {m360.monthly?.length > 0 && (
                        <div className="mt-4">
                          <div className="text-xs font-medium text-gray-600 mb-1">Aylık Ciro</div>
                          <div style={{ width: "100%", height: 140 }}>
                            <ResponsiveContainer>
                              <BarChart data={m360.monthly}>
                                <XAxis dataKey="month" tick={{ fontSize: 10 }} />
                                <RTooltip formatter={(v) => tl(v)} />
                                <Bar dataKey="revenue" fill="#4f46e5" radius={[4, 4, 0, 0]} />
                              </BarChart>
                            </ResponsiveContainer>
                          </div>
                        </div>
                      )}

                      <div className="grid grid-cols-2 gap-3 mt-3">
                        {[["Ödeme Yöntemi", m360.payment_breakdown], ["Kanal", m360.channel_breakdown], ["En Çok Ürün", m360.top_products], ["Kategori", m360.top_categories]].map(([lbl, arr], i) => (
                          arr?.length > 0 ? (
                            <div key={i}>
                              <div className="text-[11px] font-semibold text-gray-600 mb-1">{lbl}</div>
                              <div className="flex flex-wrap gap-1">
                                {arr.map((a, j) => (<span key={j} className="px-1.5 py-0.5 bg-indigo-50 text-indigo-700 border border-indigo-100 rounded text-[10px]" title={a.name}>{(a.name || "").slice(0, 22)} · {a.count}</span>))}
                              </div>
                            </div>
                          ) : null
                        ))}
                        {m360.coupons?.length > 0 && (
                          <div>
                            <div className="text-[11px] font-semibold text-gray-600 mb-1">Kullandığı Kuponlar</div>
                            <div className="flex flex-wrap gap-1">
                              {m360.coupons.map((a, j) => (<span key={j} className="px-1.5 py-0.5 bg-amber-50 text-amber-700 border border-amber-100 rounded text-[10px]">{a.name} · {a.count}</span>))}
                            </div>
                          </div>
                        )}
                      </div>

                      {m360.returns?.length > 0 && (
                        <div className="mt-3">
                          <h4 className="font-semibold text-xs mb-1.5 flex items-center gap-1 text-rose-700"><RotateCcw size={13} /> İadeler ({m360.returns.length})</h4>
                          <div className="space-y-1 max-h-40 overflow-y-auto">
                            {m360.returns.map((r, i) => (
                              <div key={i} className="flex items-center justify-between text-[11px] p-1.5 bg-rose-50/50 rounded border border-rose-100">
                                <span className="font-mono">{r.order_number}<span className="text-gray-400 ml-2">{r.reason ? `${r.reason} · ` : ""}{r.status}{r.records > 1 ? ` (${r.records} kayıt)` : ""}</span></span>
                                <span className="font-semibold text-rose-700">-{tl(r.refund_amount)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {m360.cancels?.length > 0 && (
                        <div className="mt-3">
                          <h4 className="font-semibold text-xs mb-1.5 flex items-center gap-1 text-red-700"><Ban size={13} /> İptaller ({m360.cancels.length})</h4>
                          <div className="space-y-1 max-h-40 overflow-y-auto">
                            {m360.cancels.map((c, i) => (
                              <div key={i} className="flex items-center justify-between text-[11px] p-1.5 bg-red-50/50 rounded border border-red-100">
                                <span className="font-mono">{c.order_number}<span className="text-gray-400 ml-2">{c.reason || ""}</span></span>
                                <span className="font-semibold text-red-700">{tl(c.total)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
                {detail.attribution_summary?.length > 0 && (
                  <div>
                    <h4 className="font-semibold text-sm mb-2 flex items-center gap-1"><TrendingUp size={14} /> Sipariş Kaynakları</h4>
                    <div className="flex flex-wrap gap-2">
                      {detail.attribution_summary.map((a) => (
                        <span key={a.channel} className="px-2 py-1 bg-indigo-50 text-indigo-700 border border-indigo-200 rounded text-xs">
                          {a.channel} · {a.orders}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <h4 className="font-semibold text-sm mb-2 flex items-center gap-1"><ShoppingCart size={14} /> Siparişler ({detail.orders.length})</h4>
                  <div className="space-y-1.5 max-h-72 overflow-y-auto">
                    {detail.orders.length === 0 ? <div className="text-xs text-gray-400 py-2">Henüz sipariş yok</div> : detail.orders.map((o) => (
                      <div key={o.id} className="flex items-center justify-between text-xs p-2 bg-gray-50 rounded border border-gray-100">
                        <div><span className="font-mono font-semibold">{o.order_number}</span><span className="text-gray-500 ml-2">{new Date(o.created_at).toLocaleDateString("tr-TR")}</span></div>
                        <div className="flex items-center gap-3">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] ${o.status === "delivered" ? "bg-green-100 text-green-700" : o.status === "cancelled" ? "bg-red-100 text-red-700" : "bg-gray-200 text-gray-700"}`}>{o.status}</span>
                          <span className="font-semibold">₺{(o.total || 0).toLocaleString("tr-TR")}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                {detail.addresses?.length > 0 && (
                  <div>
                    <h4 className="font-semibold text-sm mb-2">Adresler ({detail.addresses.length})</h4>
                    <div className="space-y-1.5 text-xs">
                      {detail.addresses.map((a) => (
                        <div key={a.id} className="p-2 bg-gray-50 rounded border border-gray-100">
                          <div className="font-medium">{a.title || "Adres"} {a.is_default && <span className="ml-1 text-[10px] bg-blue-100 text-blue-700 px-1 rounded">Varsayılan</span>}</div>
                          <div className="text-gray-600">{a.address}, {a.district} / {a.city}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setShowCreate(false)}>
          <div className="bg-white rounded-xl w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-4">Yeni Üye Ekle</h3>
            <div className="space-y-3">
              {[
                ["email", "E-posta *", "email"],
                ["first_name", "Ad", "text"],
                ["last_name", "Soyad", "text"],
                ["phone", "Telefon", "text"],
                ["password", "Geçici Şifre (boş = rastgele üretilir, en az 8 karakter)", "text"],
              ].map(([k, lbl, typ]) => (
                <div key={k}>
                  <label className="text-xs text-gray-500">{lbl}</label>
                  <input type={typ} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} data-testid={`member-${k}-input`}
                    className="w-full px-3 py-2 border rounded-lg text-sm mt-1" />
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">Vazgeç</button>
              <button onClick={handleCreate} data-testid="save-member-btn" className="px-4 py-2 bg-black text-white rounded-lg text-sm font-medium">Kaydet</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
