import { useState, useEffect, useMemo } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Users, Search, UserPlus, Eye, Mail, Phone, ShoppingCart, TrendingUp,
  Crown, Star, UserCheck, UserX, X, Trash2, Edit,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const SEGMENT_META = {
  vip: { label: "VIP", color: "bg-amber-100 text-amber-800 border-amber-200", icon: Crown },
  returning: { label: "Sadık", color: "bg-emerald-100 text-emerald-800 border-emerald-200", icon: Star },
  new: { label: "Yeni", color: "bg-blue-100 text-blue-800 border-blue-200", icon: UserCheck },
  prospect: { label: "Aday", color: "bg-gray-100 text-gray-700 border-gray-200", icon: UserX },
};

function SegmentBadge({ seg }) {
  const m = SEGMENT_META[seg] || SEGMENT_META.prospect;
  const Icon = m.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-medium ${m.color}`}>
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
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ email: "", first_name: "", last_name: "", phone: "", password: "" });
  // DENETİM FIX (#41): üye gruplarını yükle — detay modalında gruba atama için.
  const [memberGroups, setMemberGroups] = useState([]);

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page, limit: "25" });
      if (search) params.set("search", search);
      if (segment) params.set("segment", segment);
      if (source) params.set("source", source);
      const { data } = await axios.get(`${API}/admin/members?${params}`, { headers: authHeaders() });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
    } catch (e) { toast.error("Üyeler yüklenemedi"); }
    finally { setLoading(false); }
  };

  const loadStats = async () => {
    try {
      const { data } = await axios.get(`${API}/admin/members/stats`, { headers: authHeaders() });
      setStats(data);
    } catch (_) {}
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [page, segment, source]);
  useEffect(() => { loadStats(); }, []);
  useEffect(() => {
    axios.get(`${API}/admin/member-groups`, { headers: authHeaders() })
      .then((r) => setMemberGroups(r.data?.items || []))
      .catch(() => {});
  }, []);

  const assignGroup = async (memberId, groupId) => {
    try {
      await axios.put(`${API}/admin/members/${memberId}`, { group_id: groupId || null }, { headers: authHeaders() });
      setDetail((d) => d ? { ...d, member: { ...d.member, group_id: groupId || null } } : d);
      toast.success("Üye grubu güncellendi");
    } catch (e) { toast.error(e?.response?.data?.detail || "Grup atanamadı"); }
  };

  const openDetail = async (id) => {
    setDetailId(id);
    setDetail(null);
    try {
      const { data } = await axios.get(`${API}/admin/members/${id}`, { headers: authHeaders() });
      setDetail(data);
    } catch (_) { toast.error("Detay alınamadı"); }
  };

  const handleCreate = async () => {
    if (!form.email) return toast.warning("E-posta zorunlu");
    try {
      await axios.post(`${API}/admin/members`, form, { headers: authHeaders() });
      toast.success("Üye eklendi");
      setShowCreate(false);
      setForm({ email: "", first_name: "", last_name: "", phone: "", password: "" });
      load(); loadStats();
    } catch (e) { toast.error(e?.response?.data?.detail || "Eklenemedi"); }
  };

  const handleDelete = async (id) => {
    if (!await window.appConfirm("Bu üye silinsin mi?")) return;
    try {
      await axios.delete(`${API}/admin/members/${id}`, { headers: authHeaders() });
      toast.success("Üye silindi");
      load(); loadStats();
      if (detailId === id) setDetailId(null);
    } catch (_) { toast.error("Silinemedi"); }
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
        <button
          onClick={() => setShowCreate(true)}
          data-testid="add-member-btn"
          className="inline-flex items-center gap-2 px-4 py-2 bg-black text-white rounded-lg hover:bg-gray-800 text-sm font-medium"
        >
          <UserPlus size={16} /> Yeni Üye
        </button>
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
        <button onClick={() => { setPage(1); load(); }} className="px-4 py-2 bg-gray-900 text-white text-sm rounded-lg hover:bg-gray-700">Ara</button>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600 text-xs uppercase">
            <tr>
              <th className="text-left p-3">Üye</th>
              <th className="text-left p-3">İletişim</th>
              <th className="text-center p-3">Sipariş</th>
              <th className="text-right p-3">Toplam Harcama</th>
              <th className="text-center p-3">Segment</th>
              <th className="text-left p-3">Kaynak</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Henüz üye yok.</td></tr>
            ) : items.map((m) => (
              <tr key={m.id} className="border-t hover:bg-gray-50" data-testid={`member-row-${m.id}`}>
                <td className="p-3">
                  <div className="font-medium">{m.first_name || m.last_name ? `${m.first_name || ""} ${m.last_name || ""}`.trim() : "—"}</div>
                  <div className="text-xs text-gray-500 mt-0.5">#{m.id.slice(0, 8)}</div>
                </td>
                <td className="p-3">
                  <div className="flex items-center gap-1 text-gray-700"><Mail size={12} /> {m.email}</div>
                  {m.phone && <div className="flex items-center gap-1 text-gray-500 text-xs mt-0.5"><Phone size={11} /> {m.phone}</div>}
                </td>
                <td className="p-3 text-center font-medium">{m.orders_count}</td>
                <td className="p-3 text-right font-semibold tabular-nums">₺{(m.total_spent || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2 })}</td>
                <td className="p-3 text-center"><SegmentBadge seg={m.segment} /></td>
                <td className="p-3 text-xs text-gray-500">{m.acquisition_source || "—"}</td>
                <td className="p-3 text-right">
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
          <div className="w-full max-w-2xl bg-white h-full overflow-y-auto" onClick={(e) => e.stopPropagation()}>
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
                    <div className="text-sm text-gray-500">{detail.member.email} {detail.member.phone && ` · ${detail.member.phone}`}</div>
                    <div className="mt-2 flex gap-2 items-center">
                      <SegmentBadge seg={detail.member.segment} />
                      <span className="text-xs text-gray-500">Katılım: {new Date(detail.member.created_at).toLocaleDateString("tr-TR")}</span>
                    </div>
                    {/* DENETİM FIX (#41): üye grubu atama — grubun indirimi checkout'ta uygulanır */}
                    <div className="mt-2 flex items-center gap-2">
                      <span className="text-xs text-gray-500">Üye Grubu:</span>
                      <select
                        value={detail.member.group_id || ""}
                        onChange={(e) => assignGroup(detail.member.id, e.target.value)}
                        className="text-xs border border-gray-200 rounded px-2 py-1 bg-white"
                      >
                        <option value="">— Grupsuz —</option>
                        {memberGroups.map((g) => (
                          <option key={g.id} value={g.id}>
                            {g.name}{g.discount_percent ? ` (%${g.discount_percent})` : ""}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div className="bg-gray-50 rounded-lg p-3">
                    <div className="text-xs text-gray-500 uppercase">Sipariş</div>
                    <div className="text-2xl font-bold">{detail.member.orders_count}</div>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3">
                    <div className="text-xs text-gray-500 uppercase">Harcama</div>
                    <div className="text-2xl font-bold">₺{(detail.member.total_spent || 0).toLocaleString("tr-TR")}</div>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3">
                    <div className="text-xs text-gray-500 uppercase">Son Sipariş</div>
                    <div className="text-sm font-medium mt-1">{detail.member.last_order_at ? new Date(detail.member.last_order_at).toLocaleDateString("tr-TR") : "—"}</div>
                  </div>
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
                ["password", "Geçici Şifre (boş = Facette123!)", "text"],
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
