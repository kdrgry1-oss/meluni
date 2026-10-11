import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Tags, Plus, Trash2, Edit, Copy, Calendar, Percent, DollarSign, X } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });
// Kuponun başlangıç/bitişi sunucuda UTC ISO olarak saklanır; datetime-local girdisi
// MAĞAZA saatini (İstanbul) gösterir. Ham değeri kırpmak 10:00'ı 07:00 gösterirdi.
const CPN_TZ = "Europe/Istanbul";
const toLocalInput = (v) => {
  if (!v) return "";
  const str = String(v);
  if (str.length === 10 && !str.includes("T")) return `${str}T00:00`;
  if (!/[Z+]/.test(str.slice(10))) return str.slice(0, 16);   // eski kayıt: zaten yerel
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: CPN_TZ, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(str));
    const g = (t) => (parts.find((x) => x.type === t) || {}).value || "00";
    return `${g("year")}-${g("month")}-${g("day")}T${g("hour")}:${g("minute")}`;
  } catch {
    return str.slice(0, 16);
  }
};

const empty = { code: "", title: "", type: "percent", value: 0, min_cart_total: 0, max_discount: 0, usage_limit: 0, usage_limit_per_user: 0, start_at: "", end_at: "", is_active: true, first_order_only: false, free_shipping: false, combinable: true, not_combinable_with: [] };

export default function Coupons() {
  const [items, setItems] = useState([]);
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState(null); // null | {} | coupon
  const [form, setForm] = useState(empty);
  // Engelleme seçicisi: otomatik kampanyalar (+ diğer kuponlar). Kupon VARSAYILAN hepsiyle birleşir;
  // yalnız burada işaretlenenlerle kullanılamaz (kullanıcı isteği).
  const [promos, setPromos] = useState([]);
  useEffect(() => {
    axios.get(`${API}/campaigns?include_coupons=1`, { headers: authHeaders() }).then((r) => setPromos(r.data || [])).catch(() => setPromos([]));
  }, []);
  const toggleBlock = (id) => {
    const cur = form.not_combinable_with || [];
    setForm({ ...form, not_combinable_with: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] });
  };

  const load = async () => {
    try {
      const { data } = await axios.get(`${API}/admin/coupons`, { headers: authHeaders(), params: { search, limit: 100 } });
      setItems(data.items || []);
    } catch (_) { toast.error("Yüklenemedi"); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const openNew = () => { setForm(empty); setModal({}); };
  const openEdit = (c) => { setForm({ ...empty, ...c }); setModal(c); };

  const save = async () => {
    if (!form.code) return toast.warning("Kupon kodu zorunlu");
    try {
      if (modal?.id) {
        await axios.put(`${API}/admin/coupons/${modal.id}`, form, { headers: authHeaders() });
      } else {
        await axios.post(`${API}/admin/coupons`, form, { headers: authHeaders() });
      }
      toast.success("Kaydedildi");
      setModal(null); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Hata"); }
  };

  const del = async (id) => {
    if (!await window.appConfirm("Kupon silinsin mi?")) return;
    await axios.delete(`${API}/admin/coupons/${id}`, { headers: authHeaders() });
    toast.success("Silindi"); load();
  };

  const copyCode = (c) => { navigator.clipboard.writeText(c); toast.success("Kopyalandı"); };

  return (
    <div className="space-y-5" data-testid="coupons-page">
      <div className="flex justify-between items-center flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Tags /> Kuponlar</h1>
          <p className="text-sm text-gray-500 mt-1">Kod bazlı indirim kuponları oluşturun ve yönetin.</p>
        </div>
        <button onClick={openNew} data-testid="new-coupon-btn" className="inline-flex items-center gap-2 px-4 py-2 bg-black text-white rounded-lg text-sm">
          <Plus size={15} /> Yeni Kupon
        </button>
      </div>

      <input value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()}
        placeholder="Kupon kodu ara..." className="px-3 py-2 border rounded-lg text-sm w-80" data-testid="coupon-search" />

      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Kod / Başlık</th>
              <th className="text-left p-3">Tip</th>
              <th className="text-right p-3">Değer</th>
              <th className="text-right p-3">Min Sepet</th>
              <th className="text-center p-3">Kullanım</th>
              <th className="text-left p-3">Süre</th>
              <th className="text-center p-3">Durum</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={8} className="p-8 text-center text-gray-400">Henüz kupon yok. "Yeni Kupon" ile başlayın.</td></tr>
            ) : items.map((c) => (
              <tr key={c.id} className="border-t hover:bg-gray-50" data-testid={`coupon-${c.code}`}>
                <td className="p-3">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold bg-gray-900 text-white px-2 py-0.5 rounded">{c.code}</span>
                    <button onClick={() => copyCode(c.code)} className="text-gray-400 hover:text-gray-700"><Copy size={12} /></button>
                  </div>
                  {c.title && <div className="text-xs text-gray-500 mt-1">{c.title}</div>}
                </td>
                <td className="p-3">{c.type === "percent" ? <Percent size={14} className="inline" /> : <DollarSign size={14} className="inline" />} {c.type === "percent" ? "Yüzde" : "Sabit"}</td>
                <td className="p-3 text-right font-semibold">{c.type === "percent" ? `%${c.value}` : `₺${c.value}`}</td>
                <td className="p-3 text-right text-gray-600">₺{c.min_cart_total || 0}</td>
                <td className="p-3 text-center">
                  <span className="text-gray-700 font-medium">{c.redeemed_count || 0}</span>
                  {c.usage_limit ? <span className="text-gray-400"> / {c.usage_limit}</span> : <span className="text-gray-400"> / ∞</span>}
                </td>
                <td className="p-3 text-xs text-gray-500">
                  {c.end_at ? new Date(c.end_at).toLocaleDateString("tr-TR") : "Süresiz"}
                </td>
                <td className="p-3 text-center">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${c.is_active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                    {c.is_active ? "Aktif" : "Pasif"}
                  </span>
                </td>
                <td className="p-3 text-right">
                  <button onClick={() => openEdit(c)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded"><Edit size={14} /></button>
                  <button onClick={() => del(c.id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded"><Trash2 size={14} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal !== null && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setModal(null)}>
          <div className="bg-white rounded-xl w-full max-w-2xl p-6 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-start mb-4">
              <h3 className="text-lg font-bold">{modal?.id ? "Kuponu Düzenle" : "Yeni Kupon"}</h3>
              <button onClick={() => setModal(null)} className="text-gray-400 hover:text-gray-700"><X size={20} /></button>
            </div>

            <div className="grid md:grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-600">Kupon Kodu *</label>
                <input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
                  placeholder="YAZ2026" data-testid="coupon-code-input"
                  className="w-full mt-1 px-3 py-2 border rounded-lg text-sm font-mono" />
              </div>
              <div>
                <label className="text-xs text-gray-600">Başlık (ops.)</label>
                <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
                  placeholder="Yaz Kampanyası" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-600">Tip</label>
                <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}
                  className="w-full mt-1 px-3 py-2 border rounded-lg text-sm">
                  <option value="percent">Yüzde İndirim (%)</option>
                  <option value="fixed">Sabit İndirim (₺)</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-600">Değer *</label>
                <input type="number" value={form.value} onChange={(e) => setForm({ ...form, value: parseFloat(e.target.value) || 0 })}
                  data-testid="coupon-value-input"
                  className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
              </div>
              {form.type === "percent" && (
                <div>
                  <label className="text-xs text-gray-600">Maks. İndirim Tutarı (ops.)</label>
                  <input type="number" value={form.max_discount} onChange={(e) => setForm({ ...form, max_discount: parseFloat(e.target.value) || 0 })}
                    placeholder="0 = limitsiz" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
                </div>
              )}
              <div>
                <label className="text-xs text-gray-600">Minimum Sepet Tutarı</label>
                <input type="number" value={form.min_cart_total} onChange={(e) => setForm({ ...form, min_cart_total: parseFloat(e.target.value) || 0 })}
                  className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-600">Toplam Kullanım Limiti</label>
                <input type="number" value={form.usage_limit} onChange={(e) => setForm({ ...form, usage_limit: parseInt(e.target.value) || 0 })}
                  placeholder="0 = sınırsız" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-600">Kişi Başı Kullanım Limiti</label>
                <input type="number" value={form.usage_limit_per_user} onChange={(e) => setForm({ ...form, usage_limit_per_user: parseInt(e.target.value) || 0 })}
                  placeholder="0 = sınırsız" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-600">Başlangıç Tarihi</label>
                <input type="datetime-local" value={toLocalInput(form.start_at)} onChange={(e) => setForm({ ...form, start_at: e.target.value })}
                  className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-600">Bitiş Tarihi</label>
                <input type="datetime-local" value={toLocalInput(form.end_at)} onChange={(e) => setForm({ ...form, end_at: e.target.value })}
                  className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
              </div>
            </div>

            <div className="mt-4 space-y-2">
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Aktif</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.first_order_only} onChange={(e) => setForm({ ...form, first_order_only: e.target.checked })} /> Sadece ilk siparişe özel</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.free_shipping} onChange={(e) => setForm({ ...form, free_shipping: e.target.checked })} /> Kargo bedava</label>
            </div>

            {/* Birleşme: varsayılan TÜM kampanyalarla çalışır; yalnız engellenecekler seçilir */}
            <div className="mt-4 border rounded-lg p-3 bg-gray-50/50">
              <label className="flex items-center gap-2 text-sm mb-2">
                <input type="checkbox" checked={form.combinable === false} onChange={(e) => setForm({ ...form, combinable: !e.target.checked })} />
                <span><b>Hiçbir kampanyayla birleşmesin</b> (münhasır)</span>
              </label>
              {form.combinable !== false && (
                <>
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-600 mb-2">Şu kampanyalarla KULLANILMASIN <span className="font-normal normal-case text-gray-400">(boş = tüm kampanyalarla birlikte çalışır)</span></div>
                  <div className="max-h-40 overflow-y-auto space-y-1 border rounded bg-white p-2">
                    {promos.filter((c) => c.id !== modal?.id).length === 0 && <div className="text-xs text-gray-400">Kampanya yok</div>}
                    {promos.filter((c) => c.id !== modal?.id).map((c) => (
                      <label key={c.id} className="flex items-center gap-2 text-xs cursor-pointer">
                        <input type="checkbox" className="accent-red-600" checked={(form.not_combinable_with || []).includes(c.id)} onChange={() => toggleBlock(c.id)} />
                        <span>{c.name || c.title || c.code}{c.auto_apply ? <span className="text-gray-400"> · kampanya</span> : <span className="text-gray-400"> · kupon {c.code}</span>}</span>
                      </label>
                    ))}
                  </div>
                </>
              )}
            </div>

            <div className="flex justify-end gap-2 mt-5 pt-4 border-t">
              <button onClick={() => setModal(null)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">Vazgeç</button>
              <button onClick={save} data-testid="save-coupon-btn" className="px-4 py-2 bg-black text-white rounded-lg text-sm">Kaydet</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
