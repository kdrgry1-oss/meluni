import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Gift, Plus, Search, Ban, CheckCircle2, Copy, Pencil, X } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

// C2 — Hediye Çeki / Mağaza Kredisi yönetimi.
// gift  : koda sahip herkes kullanabilir (hediye)
// credit: yalnız tanımlı müşteri e-postası kullanabilir (mağaza kredisi / iade kredisi)
export default function GiftCards() {
  const [items, setItems] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ code: "", value_type: "amount", amount: "", percent: "", max_amount: "", usage_limit: "1", kind: "gift", customer_email: "", note: "", expires_days: "" });
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/admin/gift-cards`, { headers: authHeaders(), params: { q, limit: 300 } });
      setItems(data.items || []);
    } catch { toast.error("Liste yüklenemedi"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const create = async () => {
    const isPct = form.value_type === "percent";
    const amount = Number(form.amount);
    const percent = Number(form.percent);
    if (!isPct && (!amount || amount <= 0)) { toast.error("Geçerli tutar girin"); return; }
    if (isPct && (!percent || percent <= 0 || percent > 100)) { toast.error("Yüzde 1-100 arasında olmalı"); return; }
    if (form.kind === "credit" && !form.customer_email.trim()) { toast.error("Mağaza kredisi için müşteri e-postası zorunlu"); return; }
    setCreating(true);
    try {
      const { data } = await axios.post(`${API}/admin/gift-cards`, {
        code: form.code.trim().toUpperCase(),
        value_type: form.value_type,
        amount: isPct ? 0 : amount,
        percent: isPct ? percent : 0,
        max_amount: isPct ? (Number(form.max_amount) || 0) : 0,
        usage_limit: isPct ? (Number(form.usage_limit) || 1) : 1,
        kind: form.kind,
        customer_email: form.customer_email.trim(),
        note: form.note.trim(),
        expires_days: Number(form.expires_days) || 0,
      }, { headers: authHeaders() });
      toast.success(`Oluşturuldu: ${data.code}`);
      try { await navigator.clipboard.writeText(data.code); toast.info("Kod panoya kopyalandı"); } catch { /* pano izni yok */ }
      setShowForm(false);
      setForm({ code: "", value_type: "amount", amount: "", percent: "", max_amount: "", usage_limit: "1", kind: "gift", customer_email: "", note: "", expires_days: "" });
      load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Oluşturulamadı"); }
    finally { setCreating(false); }
  };

  const toggleStatus = async (card) => {
    const next = card.status === "active" ? "disabled" : "active";
    try {
      await axios.put(`${API}/admin/gift-cards/${card.id}`, { status: next }, { headers: authHeaders() });
      toast.success(next === "disabled" ? "Devre dışı bırakıldı" : "Aktifleştirildi");
      load();
    } catch { toast.error("Güncellenemedi"); }
  };

  // Düzenleme modalı — kod, tip, müşteri, süre, not, bakiye / yüzde-maks-hak
  const [edit, setEdit] = useState(null);
  const [saving, setSaving] = useState(false);
  const openEdit = (c) => setEdit({
    id: c.id, code: c.code || "", value_type: c.value_type || "amount", kind: c.kind || "gift",
    customer_email: c.customer_email || "", note: c.note || "",
    expires_days: c.expires_at ? String(Math.max(0, Math.ceil((new Date(c.expires_at) - Date.now()) / 86400000))) : "",
    set_balance: c.balance != null ? String(c.balance) : "", percent: c.percent != null ? String(c.percent) : "",
    max_amount: c.max_amount != null ? String(c.max_amount) : "", usage_limit: c.usage_limit != null ? String(c.usage_limit) : "1",
    expires_changed: false,
  });
  const saveEdit = async () => {
    if (!edit) return;
    setSaving(true);
    try {
      const body = { code: edit.code.trim().toUpperCase(), kind: edit.kind, customer_email: edit.customer_email.trim(), note: edit.note.trim() };
      if (edit.expires_changed) body.expires_days = Number(edit.expires_days) || 0;
      if (edit.value_type === "percent") {
        body.percent = Number(edit.percent) || 0; body.max_amount = Number(edit.max_amount) || 0; body.usage_limit = Number(edit.usage_limit) || 1;
      } else if (edit.set_balance !== "") {
        body.set_balance = Number(edit.set_balance) || 0;
      }
      await axios.put(`${API}/admin/gift-cards/${edit.id}`, body, { headers: authHeaders() });
      toast.success("Hediye çeki güncellendi");
      setEdit(null); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Güncellenemedi"); }
    finally { setSaving(false); }
  };

  const copy = async (code) => {
    try { await navigator.clipboard.writeText(code); toast.success("Kopyalandı"); } catch { /* pano yok */ }
  };

  return (
    <div className="space-y-5" data-testid="gift-cards-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Gift /> Hediye Çekleri</h1>
          <p className="text-sm text-gray-500 mt-1">
            Hediye çeki (herkes kullanır) veya mağaza kredisi (yalnız tanımlı müşteri) oluşturun.
            Bakiye kısmi kullanılır; kalan sonraki siparişe devreder. Sipariş iptalinde bakiye otomatik iade edilir.
          </p>
        </div>
        <button onClick={() => setShowForm(!showForm)} data-testid="new-gift-card-btn"
          className="inline-flex items-center gap-2 px-4 py-2 bg-black text-white rounded-lg text-sm hover:bg-gray-800">
          <Plus size={15} /> Yeni Çek
        </button>
      </div>

      {showForm && (
        <div className="bg-white border rounded-xl p-4 grid sm:grid-cols-6 gap-3 items-end">
          <div>
            <label className="block text-xs font-bold text-gray-500 mb-1">Kod (ops.)</label>
            <input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
              placeholder="boş = otomatik (ör. YILBASI20)" className="w-full border rounded px-3 py-2 text-sm font-mono" data-testid="gift-card-code" />
          </div>
          <div>
            <label className="block text-xs font-bold text-gray-500 mb-1">Değer türü</label>
            <select value={form.value_type} onChange={(e) => setForm({ ...form, value_type: e.target.value })}
              className="w-full border rounded px-3 py-2 text-sm" data-testid="gift-card-value-type">
              <option value="amount">Tutar (TL bakiye)</option>
              <option value="percent">Yüzde (%) indirim</option>
            </select>
          </div>
          {form.value_type === "percent" ? (
            <>
              <div>
                <label className="block text-xs font-bold text-gray-500 mb-1">Yüzde (%)</label>
                <input type="number" min="1" max="100" value={form.percent} onChange={(e) => setForm({ ...form, percent: e.target.value })}
                  placeholder="20" className="w-full border rounded px-3 py-2 text-sm" data-testid="gift-card-percent" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-500 mb-1">Maks. indirim (TL, ops.)</label>
                <input type="number" value={form.max_amount} onChange={(e) => setForm({ ...form, max_amount: e.target.value })}
                  placeholder="0 = sınırsız" className="w-full border rounded px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-500 mb-1">Kullanım hakkı</label>
                <input type="number" min="1" value={form.usage_limit} onChange={(e) => setForm({ ...form, usage_limit: e.target.value })}
                  placeholder="1" className="w-full border rounded px-3 py-2 text-sm" />
              </div>
            </>
          ) : (
            <div>
              <label className="block text-xs font-bold text-gray-500 mb-1">Tutar (TL)</label>
              <input type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })}
                placeholder="500" className="w-full border rounded px-3 py-2 text-sm" data-testid="gift-card-amount" />
            </div>
          )}
          <div>
            <label className="block text-xs font-bold text-gray-500 mb-1">Tip</label>
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}
              className="w-full border rounded px-3 py-2 text-sm">
              <option value="gift">Hediye çeki (herkes)</option>
              <option value="credit">Mağaza kredisi (müşteriye özel)</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-bold text-gray-500 mb-1">Müşteri e-postası {form.kind === "credit" ? "(zorunlu)" : "(ops.)"}</label>
            <input value={form.customer_email} onChange={(e) => setForm({ ...form, customer_email: e.target.value })}
              placeholder="musteri@ornek.com" className="w-full border rounded px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-xs font-bold text-gray-500 mb-1">Geçerlilik (gün, 0=süresiz)</label>
            <input type="number" value={form.expires_days} onChange={(e) => setForm({ ...form, expires_days: e.target.value })}
              placeholder="365" className="w-full border rounded px-3 py-2 text-sm" />
          </div>
          <button onClick={create} disabled={creating} data-testid="create-gift-card-btn"
            className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700 disabled:opacity-50">
            {creating ? "Oluşturuluyor..." : "Oluştur"}
          </button>
          <div className="sm:col-span-5">
            <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })}
              placeholder="Not (ör. 'İade telafisi W10618' / 'Yılbaşı hediyesi')" className="w-full border rounded px-3 py-2 text-sm" />
          </div>
        </div>
      )}

      <div className="flex gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()}
            placeholder="Kod / e-posta / not ara..." className="w-full pl-8 pr-3 py-2 border rounded-lg text-sm" />
        </div>
        <button onClick={load} className="px-3 py-2 border rounded-lg text-sm hover:bg-gray-50">Ara</button>
      </div>

      <div className="bg-white border rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Kod</th>
              <th className="text-left p-3">Tip</th>
              <th className="text-right p-3">Bakiye / İlk Tutar</th>
              <th className="text-left p-3">Müşteri</th>
              <th className="text-left p-3">Durum</th>
              <th className="text-left p-3">Not</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Henüz hediye çeki yok.</td></tr>
            ) : items.map((c) => (
              <tr key={c.id} className="border-t">
                <td className="p-3 font-mono text-xs font-bold">
                  <span className="inline-flex items-center gap-1.5">
                    {c.code}
                    <button onClick={() => copy(c.code)} className="text-gray-400 hover:text-black" title="Kopyala"><Copy size={12} /></button>
                  </span>
                </td>
                <td className="p-3">{c.kind === "credit" ? "Mağaza kredisi" : "Hediye çeki"}{c.value_type === "percent" ? <span className="ml-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-200">%{Number(c.percent)}</span> : null}</td>
                <td className="p-3 text-right tabular-nums">
                  {c.value_type === "percent" ? (
                    <>
                      <b className={Number(c.uses_left) > 0 ? "text-emerald-700" : "text-gray-400"}>%{Number(c.percent)}</b>
                      {Number(c.max_amount) > 0 && <span className="text-gray-400"> (maks {Number(c.max_amount).toFixed(0)} TL)</span>}
                      <div className="text-[10px] text-gray-400">kalan hak {Number(c.uses_left ?? 0)} / {Number(c.usage_limit ?? 1)}{Number(c.used_total) > 0 ? ` · kullanılan ${Number(c.used_total).toFixed(2)} TL` : ""}</div>
                    </>
                  ) : (
                    <>
                      <b className={Number(c.balance) > 0 ? "text-emerald-700" : "text-gray-400"}>{Number(c.balance).toFixed(2)}</b>
                      <span className="text-gray-400"> / {Number(c.initial_amount).toFixed(2)} TL</span>
                    </>
                  )}
                </td>
                <td className="p-3 text-xs">{c.customer_email || "—"}</td>
                <td className="p-3">
                  <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${
                    c.status === "active" ? "bg-emerald-50 text-emerald-700 border-emerald-200" : "bg-red-50 text-red-600 border-red-200"}`}>
                    {c.status === "active" ? "Aktif" : (c.status === "used" ? "Kullanıldı" : "Pasif")}
                  </span>
                  {c.expires_at && <div className="text-[10px] text-gray-400 mt-0.5">Son: {c.expires_at.slice(0, 10)}</div>}
                </td>
                <td className="p-3 text-xs text-gray-500 max-w-[200px] truncate" title={c.note}>{c.note || "—"}</td>
                <td className="p-3 text-right">
                  <button onClick={() => openEdit(c)} className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded border text-gray-700 border-gray-300 hover:bg-gray-50 mr-1" data-testid={`gift-card-edit-${c.id}`}>
                    <Pencil size={11} /> Düzenle
                  </button>
                  <button onClick={() => toggleStatus(c)}
                    className={`inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded border ${
                      c.status === "active" ? "text-red-600 border-red-200 hover:bg-red-50" : "text-emerald-700 border-emerald-200 hover:bg-emerald-50"}`}>
                    {c.status === "active" ? <><Ban size={11} /> Devre dışı</> : <><CheckCircle2 size={11} /> Aktifleştir</>}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {edit && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={() => setEdit(null)}>
          <div className="bg-white rounded-xl w-full max-w-2xl p-5 shadow-2xl" onClick={(e) => e.stopPropagation()} data-testid="gift-card-edit-modal">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-lg">Hediye Çekini Düzenle</h2>
              <button onClick={() => setEdit(null)} className="p-1.5 hover:bg-gray-100 rounded"><X size={18} /></button>
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-bold text-gray-500 mb-1">Kod</label>
                <input value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value.toUpperCase() })} className="w-full border rounded px-3 py-2 text-sm font-mono" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-500 mb-1">Tip</label>
                <select value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })} className="w-full border rounded px-3 py-2 text-sm">
                  <option value="gift">Hediye çeki (herkes)</option>
                  <option value="credit">Mağaza kredisi (müşteriye özel)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-500 mb-1">Müşteri e-postası {edit.kind === "credit" ? "(zorunlu)" : "(ops.)"}</label>
                <input value={edit.customer_email} onChange={(e) => setEdit({ ...edit, customer_email: e.target.value })} className="w-full border rounded px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-500 mb-1">Geçerlilik (bugünden itibaren gün, 0 = süresiz)</label>
                <input type="number" min="0" value={edit.expires_days} onChange={(e) => setEdit({ ...edit, expires_days: e.target.value, expires_changed: true })} className="w-full border rounded px-3 py-2 text-sm" />
              </div>
              {edit.value_type === "percent" ? (
                <>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 mb-1">Yüzde (%)</label>
                    <input type="number" min="1" max="100" value={edit.percent} onChange={(e) => setEdit({ ...edit, percent: e.target.value })} className="w-full border rounded px-3 py-2 text-sm" />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 mb-1">Maks. indirim (TL, 0 = sınırsız)</label>
                    <input type="number" min="0" value={edit.max_amount} onChange={(e) => setEdit({ ...edit, max_amount: e.target.value })} className="w-full border rounded px-3 py-2 text-sm" />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 mb-1">Kullanım hakkı (toplam)</label>
                    <input type="number" min="1" value={edit.usage_limit} onChange={(e) => setEdit({ ...edit, usage_limit: e.target.value })} className="w-full border rounded px-3 py-2 text-sm" />
                  </div>
                </>
              ) : (
                <div>
                  <label className="block text-xs font-bold text-gray-500 mb-1">Bakiye (TL)</label>
                  <input type="number" min="0" value={edit.set_balance} onChange={(e) => setEdit({ ...edit, set_balance: e.target.value })} className="w-full border rounded px-3 py-2 text-sm" />
                </div>
              )}
              <div className="sm:col-span-2">
                <label className="block text-xs font-bold text-gray-500 mb-1">Not</label>
                <input value={edit.note} onChange={(e) => setEdit({ ...edit, note: e.target.value })} className="w-full border rounded px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5 pt-4 border-t">
              <button onClick={() => setEdit(null)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">Vazgeç</button>
              <button onClick={saveEdit} disabled={saving} className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50" data-testid="gift-card-save-edit">{saving ? "Kaydediliyor…" : "Kaydet"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
