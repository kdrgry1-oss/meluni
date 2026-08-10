/**
 * Catalog extras — 7 sayfa tek dosyada: Brands, Tags, MemberGroups,
 * Announcements, Popups, StockAlerts, HavaleNotifications, SupportTickets,
 * ShippingRules+PaymentDiscounts, CurrencyRates, BulkMail, ExtraReports.
 */
import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Plus, Trash2, Edit, RefreshCw, Send, Tag, Store, Users, BellRing, MessageSquare, Mail, CreditCard, Truck, Banknote, DollarSign } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, LineChart, Line } from "recharts";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const h = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

// ======= Generic CRUD admin page builder =======
function useCrud(endpoint) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}${endpoint}`, { headers: h() });
      setItems(data.items || []);
    } finally { setLoading(false); }
  };
  const save = async (payload) => {
    const { data } = await axios.post(`${API}${endpoint}`, payload, { headers: h() });
    toast.success("Kaydedildi"); load(); return data;
  };
  const update = async (id, payload) => {
    await axios.put(`${API}${endpoint}/${id}`, payload, { headers: h() });
    toast.success("Güncellendi"); load();
  };
  const del = async (id) => {
    if (!await window.appConfirm("Silinsin mi?")) return;
    await axios.delete(`${API}${endpoint}/${id}`, { headers: h() });
    toast.success("Silindi"); load();
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);
  return { items, loading, load, save, update, del };
}

function SimpleCrudPage({ title, icon: Icon, endpoint, fields, extraColumns = [] }) {
  const crud = useCrud(endpoint);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({});
  const open = (item = null) => {
    setEditing(item); setForm(item ? { ...item } : { is_active: true });
  };
  const submit = async () => {
    if (editing?.id) await crud.update(editing.id, form);
    else await crud.save(form);
    setEditing(null); setForm({});
  };
  return (
    <div className="space-y-5" data-testid={`${endpoint.replace(/\//g, "-")}-page`}>
      <div className="flex justify-between items-center flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Icon /> {title}</h1>
        </div>
        <button onClick={() => open()} className="px-4 py-2 bg-black text-white rounded-lg text-sm inline-flex items-center gap-1"><Plus size={14} /> Yeni</button>
      </div>
      <div className="bg-white border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              {fields.filter((f) => !f.hidden_in_list).map((f) => <th key={f.key} className="text-left p-3">{f.label}</th>)}
              {extraColumns.map((c) => <th key={c.key} className="text-left p-3">{c.label}</th>)}
              <th className="text-center p-3">Aktif</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {crud.items.length === 0 ? <tr><td colSpan={fields.length + 2} className="p-6 text-center text-gray-400">Kayıt yok.</td></tr>
              : crud.items.map((it) => (
                <tr key={it.id} className="border-t">
                  {fields.filter((f) => !f.hidden_in_list).map((f) => <td key={f.key} className="p-3">{f.render ? f.render(it[f.key], it) : (it[f.key] ?? "—")}</td>)}
                  {extraColumns.map((c) => <td key={c.key} className="p-3">{c.render(it)}</td>)}
                  <td className="p-3 text-center">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${it.is_active ? "bg-green-100 text-green-700" : "bg-gray-200 text-gray-500"}`}>{it.is_active ? "Aktif" : "Pasif"}</span>
                  </td>
                  <td className="p-3 text-right">
                    <button onClick={() => open(it)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded"><Edit size={14} /></button>
                    <button onClick={() => crud.del(it.id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded"><Trash2 size={14} /></button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      {editing !== null && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setEditing(null)}>
          <div className="bg-white rounded-xl w-full max-w-lg p-5 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-bold text-lg mb-4">{editing?.id ? "Düzenle" : "Yeni"}</h3>
            <div className="space-y-3">
              {fields.map((f) => (
                <div key={f.key}>
                  <label className="text-xs text-gray-600">{f.label}{f.required && " *"}</label>
                  {f.type === "textarea" ? (
                    <textarea rows={3} value={form[f.key] || ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" />
                  ) : f.type === "number" ? (
                    <input type="number" value={form[f.key] || ""} onChange={(e) => setForm({ ...form, [f.key]: parseFloat(e.target.value) || 0 })} className="w-full mt-1 px-3 py-2 border rounded text-sm" />
                  ) : f.type === "checkbox" ? (
                    <label className="flex items-center gap-2 mt-1"><input type="checkbox" checked={!!form[f.key]} onChange={(e) => setForm({ ...form, [f.key]: e.target.checked })} /> <span className="text-sm">{f.label}</span></label>
                  ) : f.type === "color" ? (
                    <input type="color" value={form[f.key] || "#000000"} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} className="w-full mt-1 px-3 py-1 border rounded" />
                  ) : (
                    <input type={f.type || "text"} value={form[f.key] || ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" />
                  )}
                </div>
              ))}
              <label className="flex items-center gap-2"><input type="checkbox" checked={!!form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> <span className="text-sm">Aktif</span></label>
            </div>
            <div className="flex justify-end gap-2 mt-5 pt-4 border-t">
              <button onClick={() => setEditing(null)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">Vazgeç</button>
              <button onClick={submit} className="px-4 py-2 bg-black text-white rounded-lg text-sm">Kaydet</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ========== Page exports ==========

export function Brands() {
  return <SimpleCrudPage title="Marka Yönetimi" icon={Store} endpoint="/brands"
    fields={[
      { key: "name", label: "Marka Adı", required: true },
      { key: "slug", label: "Slug (URL)" },
      { key: "description", label: "Açıklama", type: "textarea", hidden_in_list: true },
      { key: "image", label: "Logo URL", hidden_in_list: true },
      { key: "sort_order", label: "Sıra", type: "number" },
    ]} />;
}

export function ProductTags() {
  return <SimpleCrudPage title="Etiket Yönetimi" icon={Tag} endpoint="/product-tags"
    fields={[
      { key: "name", label: "Etiket Adı", required: true },
      { key: "bg_color", label: "Arka Plan", type: "color", render: (v) => <span className="inline-block w-5 h-5 rounded border" style={{ background: v || "#eee" }} /> },
      { key: "text_color", label: "Yazı", type: "color", hidden_in_list: true },
      { key: "icon", label: "Icon (lucide)", hidden_in_list: true },
      { key: "sort_order", label: "Sıra", type: "number", hidden_in_list: true },
    ]} />;
}

export function MemberGroups() {
  return <SimpleCrudPage title="Üye Grupları (B2B/VIP)" icon={Users} endpoint="/admin/member-groups"
    fields={[
      { key: "name", label: "Grup Adı", required: true },
      { key: "discount_percent", label: "İndirim %", type: "number" },
      { key: "is_b2b", label: "B2B Grubu", type: "checkbox", hidden_in_list: true },
      { key: "description", label: "Açıklama", type: "textarea", hidden_in_list: true },
    ]} />;
}

export function Announcements() {
  return <SimpleCrudPage title="Duyuru Yönetimi" icon={BellRing} endpoint="/admin/announcements"
    fields={[
      { key: "name", label: "Başlık", required: true },
      { key: "content", label: "İçerik", type: "textarea" },
      { key: "link", label: "Link (ops.)" },
      { key: "bg_color", label: "Arka Plan", type: "color" },
      { key: "position", label: "Konum (top/bottom)" },
      { key: "start_at", label: "Başlangıç", type: "datetime-local", hidden_in_list: true },
      { key: "end_at", label: "Bitiş", type: "datetime-local", hidden_in_list: true },
    ]} />;
}

export function Popups() {
  return <SimpleCrudPage title="Süreli Popup Yönetimi" icon={BellRing} endpoint="/admin/popups"
    fields={[
      { key: "name", label: "Popup Adı / Başlık", required: true },
      { key: "content", label: "Metin / HTML İçerik", type: "textarea" },
      { key: "newsletter", label: "Bülten kayıt formu göster (e-posta + KVKK onayı)", type: "checkbox" },
      { key: "subtitle", label: "Alt açıklama (bülten formunda)", type: "textarea", hidden_in_list: true },
      { key: "button_text", label: "Buton yazısı (bülten formu)", hidden_in_list: true },
      { key: "image", label: "Görsel URL" },
      { key: "link", label: "Yönlendirme (bülten kapalıysa)" },
      { key: "delay_seconds", label: "Saniye sonra göster", type: "number" },
      { key: "trigger", label: "Tetikleyici (exit_intent/scroll/time)" },
      { key: "show_once", label: "Sadece 1 kez", type: "checkbox", hidden_in_list: true },
    ]} />;
}

// Stock/Price Alerts (no CRUD create — customers register themselves)
export function StockAlerts() {
  const [items, setItems] = useState([]);
  const [type, setType] = useState("");
  const load = async () => {
    const { data } = await axios.get(`${API}/admin/alerts`, { headers: h(), params: type ? { type } : {} });
    setItems(data.items || []);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [type]);
  const del = async (id) => { if (!await window.appConfirm("Silinsin mi?")) return; await axios.delete(`${API}/admin/alerts/${id}`, { headers: h() }); load(); };

  return (
    <div className="space-y-5" data-testid="stock-alerts-page">
      <h1 className="text-2xl font-bold flex items-center gap-2"><BellRing /> Stok & Fiyat Alarm Hatırlatma</h1>
      <p className="text-sm text-gray-500 -mt-3">Müşterilerin "Stoğa gelince / fiyat düşünce haber ver" kayıtları.</p>
      <div className="flex gap-2">
        {[["", "Tümü"], ["stock", "Stok Alarmı"], ["price", "Fiyat Alarmı"]].map(([v, l]) => (
          <button key={v || "all"} onClick={() => setType(v)} className={`px-3 py-1.5 text-sm rounded ${type === v ? "bg-black text-white" : "bg-white border"}`}>{l}</button>
        ))}
      </div>
      <div className="bg-white border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Tip</th>
              <th className="text-left p-3">Ürün</th>
              <th className="text-left p-3">Email / Tel</th>
              <th className="text-right p-3">Hedef Fiyat</th>
              <th className="text-center p-3">Stok Şimdi</th>
              <th className="text-left p-3">Tarih</th>
              <th className="text-right p-3"></th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? <tr><td colSpan={7} className="p-6 text-center text-gray-400">Kayıt yok.</td></tr>
              : items.map((a) => (
                <tr key={a.id} className="border-t">
                  <td className="p-3"><span className={`text-xs px-2 py-0.5 rounded ${a.type === "stock" ? "bg-amber-100 text-amber-800" : "bg-blue-100 text-blue-800"}`}>{a.type === "stock" ? "Stok" : "Fiyat"}</span></td>
                  <td className="p-3">{a.product?.name || "—"} <div className="text-xs text-gray-400 font-mono">{a.product?.stock_code}</div></td>
                  <td className="p-3 text-xs">{a.email}<br/><span className="text-gray-400">{a.phone}</span></td>
                  <td className="p-3 text-right font-semibold">{a.target_price ? `₺${a.target_price}` : "—"}</td>
                  <td className="p-3 text-center">{a.product?.stock ?? "—"}</td>
                  <td className="p-3 text-xs text-gray-500">{new Date(a.created_at).toLocaleDateString("tr-TR")}</td>
                  <td className="p-3 text-right"><button onClick={() => del(a.id)} className="text-red-600 hover:bg-red-50 p-1.5 rounded"><Trash2 size={14} /></button></td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Havale / EFT Notifications
export function HavaleNotifications() {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState("pending");
  const load = async () => {
    const { data } = await axios.get(`${API}/admin/havale-notifications`, { headers: h(), params: { status } });
    setItems(data.items || []);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [status]);
  const setStatusFor = async (id, s) => {
    await axios.put(`${API}/admin/havale-notifications/${id}`, { status: s }, { headers: h() });
    toast.success(s === "confirmed" ? "Onaylandı, sipariş ödendi işaretlendi" : s === "rejected" ? "Reddedildi" : "Güncellendi");
    load();
  };
  return (
    <div className="space-y-5" data-testid="havale-page">
      <h1 className="text-2xl font-bold flex items-center gap-2"><Banknote /> Havale / EFT Bildirimleri</h1>
      <p className="text-sm text-gray-500 -mt-3">Müşterilerin havale/EFT yaptığına dair bildirimleri onaylayın.</p>
      <div className="flex gap-2">
        {[["pending", "Beklemede"], ["confirmed", "Onaylı"], ["rejected", "Reddedildi"]].map(([v, l]) => (
          <button key={v} onClick={() => setStatus(v)} className={`px-3 py-1.5 text-sm rounded ${status === v ? "bg-black text-white" : "bg-white border"}`}>{l}</button>
        ))}
      </div>
      <div className="bg-white border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Sipariş No</th>
              <th className="text-left p-3">Gönderen</th>
              <th className="text-left p-3">Banka</th>
              <th className="text-right p-3">Tutar</th>
              <th className="text-left p-3">Havale Tarihi</th>
              <th className="text-left p-3">Not</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? <tr><td colSpan={7} className="p-6 text-center text-gray-400">Bildirim yok.</td></tr>
              : items.map((n) => (
                <tr key={n.id} className="border-t">
                  <td className="p-3 font-mono">{n.order?.order_number || n.order_id}</td>
                  <td className="p-3">{n.sender_name}</td>
                  <td className="p-3">{n.bank}</td>
                  <td className="p-3 text-right font-semibold">₺{n.amount?.toLocaleString("tr-TR")}</td>
                  <td className="p-3 text-xs">{n.transfer_date}</td>
                  <td className="p-3 text-xs text-gray-500">{n.note}</td>
                  <td className="p-3 text-right">
                    {status === "pending" && <>
                      <button onClick={() => setStatusFor(n.id, "confirmed")} className="text-xs px-2 py-1 bg-green-100 text-green-800 rounded mr-1">Onayla</button>
                      <button onClick={() => setStatusFor(n.id, "rejected")} className="text-xs px-2 py-1 bg-red-100 text-red-800 rounded">Reddet</button>
                    </>}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Support Tickets
export function Tickets() {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState("open");
  const [sel, setSel] = useState(null);
  const [reply, setReply] = useState("");
  const load = async () => {
    const { data } = await axios.get(`${API}/admin/tickets`, { headers: h(), params: { status } });
    setItems(data.items || []);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [status]);
  const sendReply = async () => {
    if (!reply.trim()) return;
    await axios.put(`${API}/admin/tickets/${sel.id}`, { reply, status: "in_progress" }, { headers: h() });
    toast.success("Cevap gönderildi"); setReply(""); load(); setSel(null);
  };
  const updateStatus = async (s) => {
    await axios.put(`${API}/admin/tickets/${sel.id}`, { status: s }, { headers: h() });
    toast.success("Durum güncellendi"); load(); setSel(null);
  };
  const del = async (id) => { if (!await window.appConfirm("Silinsin mi?")) return; await axios.delete(`${API}/admin/tickets/${id}`, { headers: h() }); load(); };

  return (
    <div className="space-y-5" data-testid="tickets-page">
      <h1 className="text-2xl font-bold flex items-center gap-2"><MessageSquare /> Destek Talepleri (Ticket)</h1>
      <div className="flex gap-2">
        {[["open", "Açık"], ["in_progress", "Devam Eden"], ["resolved", "Çözüldü"], ["closed", "Kapalı"]].map(([v, l]) => (
          <button key={v} onClick={() => setStatus(v)} className={`px-3 py-1.5 text-sm rounded ${status === v ? "bg-black text-white" : "bg-white border"}`}>{l}</button>
        ))}
      </div>
      <div className="bg-white border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">No</th>
              <th className="text-left p-3">Konu</th>
              <th className="text-left p-3">E-posta</th>
              <th className="text-center p-3">Öncelik</th>
              <th className="text-left p-3">Tarih</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? <tr><td colSpan={6} className="p-6 text-center text-gray-400">Ticket yok.</td></tr>
              : items.map((t) => (
                <tr key={t.id} className="border-t">
                  <td className="p-3 font-mono text-xs">{t.ticket_number}</td>
                  <td className="p-3 font-medium">{t.subject}</td>
                  <td className="p-3 text-xs">{t.email}</td>
                  <td className="p-3 text-center"><span className={`text-xs px-2 py-0.5 rounded ${t.priority === "urgent" ? "bg-red-100 text-red-700" : t.priority === "high" ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-600"}`}>{t.priority}</span></td>
                  <td className="p-3 text-xs text-gray-500">{new Date(t.created_at).toLocaleDateString("tr-TR")}</td>
                  <td className="p-3 text-right">
                    <button onClick={() => setSel(t)} className="text-xs px-2 py-1 bg-blue-100 text-blue-800 rounded">Aç</button>
                    <button onClick={() => del(t.id)} className="text-red-600 p-1 ml-1"><Trash2 size={13} /></button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      {sel && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setSel(null)}>
          <div className="bg-white rounded-xl w-full max-w-2xl p-5 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-start mb-4">
              <div>
                <h3 className="font-bold text-lg">{sel.subject}</h3>
                <div className="text-xs text-gray-500">{sel.ticket_number} · {sel.email}</div>
              </div>
              <button onClick={() => setSel(null)} className="text-gray-400">✕</button>
            </div>
            <div className="bg-gray-50 p-3 rounded text-sm whitespace-pre-wrap">{sel.message}</div>
            <div className="mt-4 space-y-2">
              {(sel.replies || []).map((r) => (
                <div key={r.id} className="bg-blue-50 p-2 rounded text-sm">
                  <div className="text-xs text-blue-700 font-semibold">{r.by} · {new Date(r.created_at).toLocaleString("tr-TR")}</div>
                  <div>{r.message}</div>
                </div>
              ))}
            </div>
            <textarea rows={3} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Cevabınız..." className="w-full mt-3 px-3 py-2 border rounded text-sm" />
            <div className="flex justify-between gap-2 mt-2">
              <div className="flex gap-1">
                <button onClick={() => updateStatus("resolved")} className="text-xs px-2 py-1 bg-green-100 text-green-800 rounded">Çözüldü</button>
                <button onClick={() => updateStatus("closed")} className="text-xs px-2 py-1 bg-gray-200 rounded">Kapat</button>
              </div>
              <button onClick={sendReply} className="px-4 py-2 bg-black text-white rounded text-sm"><Send size={13} className="inline mr-1" /> Gönder</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Shipping + payment rules (two tabs one page)
export function ShippingPaymentRules() {
  const [tab, setTab] = useState("shipping");
  const [ship, setShip] = useState([]);
  const [pay, setPay] = useState([]);
  const [form, setForm] = useState({});
  const [show, setShow] = useState(false);

  const loadAll = async () => {
    const [s, p] = await Promise.all([
      axios.get(`${API}/admin/rules/shipping`, { headers: h() }),
      axios.get(`${API}/admin/rules/payment-discounts`, { headers: h() }),
    ]);
    setShip(s.data.items || []);
    setPay(p.data.items || []);
  };
  useEffect(() => { loadAll(); }, []);
  const save = async () => {
    const ep = tab === "shipping" ? "/admin/rules/shipping" : "/admin/rules/payment-discounts";
    await axios.post(`${API}${ep}`, form, { headers: h() });
    toast.success("Kaydedildi"); setShow(false); setForm({}); loadAll();
  };
  const del = async (id) => {
    if (!await window.appConfirm("Silinsin mi?")) return;
    const ep = tab === "shipping" ? "/admin/rules/shipping" : "/admin/rules/payment-discounts";
    await axios.delete(`${API}${ep}/${id}`, { headers: h() });
    loadAll();
  };

  const data = tab === "shipping" ? ship : pay;

  return (
    <div className="space-y-5" data-testid="rules-page">
      <h1 className="text-2xl font-bold flex items-center gap-2"><Truck /> Kargo & Ödeme Kampanyaları</h1>
      <div className="flex gap-2">
        <button onClick={() => setTab("shipping")} className={`px-4 py-2 text-sm rounded ${tab === "shipping" ? "bg-black text-white" : "bg-white border"}`}><Truck size={14} className="inline mr-1" /> Kargo Kuralları</button>
        <button onClick={() => setTab("payment")} className={`px-4 py-2 text-sm rounded ${tab === "payment" ? "bg-black text-white" : "bg-white border"}`}><CreditCard size={14} className="inline mr-1" /> Ödeme Tipi İndirimleri</button>
        <button onClick={() => { setForm({ is_active: true }); setShow(true); }} className="ml-auto px-4 py-2 bg-black text-white text-sm rounded inline-flex items-center gap-1"><Plus size={13} /> Yeni</button>
      </div>
      <div className="bg-white border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              {tab === "shipping" ? <>
                <th className="text-left p-3">Ad</th><th className="text-right p-3">Min Sepet</th><th className="text-right p-3">Kargo Ücreti</th><th className="text-center p-3">Bedava</th>
              </> : <>
                <th className="text-left p-3">Ödeme</th><th className="text-left p-3">Etiket</th><th className="text-right p-3">İndirim</th><th className="text-right p-3">Min Sepet</th>
              </>}
              <th className="text-center p-3">Aktif</th><th className="text-right p-3"></th>
            </tr>
          </thead>
          <tbody>
            {data.length === 0 ? <tr><td colSpan={6} className="p-6 text-center text-gray-400">Kural yok.</td></tr>
              : data.map((r) => (
                <tr key={r.id} className="border-t">
                  {tab === "shipping" ? <>
                    <td className="p-3 font-medium">{r.name}</td>
                    <td className="p-3 text-right">₺{r.min_cart}</td>
                    <td className="p-3 text-right">{r.free_shipping ? "—" : `₺${r.shipping_cost}`}</td>
                    <td className="p-3 text-center">{r.free_shipping ? "✓" : "—"}</td>
                  </> : <>
                    <td className="p-3 font-medium">{r.payment_method}</td>
                    <td className="p-3">{r.label}</td>
                    <td className="p-3 text-right">{r.discount_type === "percent" ? `%${r.discount_value}` : `₺${r.discount_value}`}</td>
                    <td className="p-3 text-right">₺{r.min_cart || 0}</td>
                  </>}
                  <td className="p-3 text-center"><span className={`text-xs px-2 py-0.5 rounded ${r.is_active ? "bg-green-100 text-green-700" : "bg-gray-200"}`}>{r.is_active ? "Aktif" : "Pasif"}</span></td>
                  <td className="p-3 text-right"><button onClick={() => del(r.id)} className="p-1 text-red-600 hover:bg-red-50 rounded"><Trash2 size={14} /></button></td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      {show && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setShow(false)}>
          <div className="bg-white rounded-xl w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-bold text-lg mb-4">{tab === "shipping" ? "Kargo Kuralı" : "Ödeme Tipi İndirimi"}</h3>
            <div className="space-y-3">
              {tab === "shipping" ? (
                <>
                  <div><label className="text-xs">Ad *</label><input value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
                  <div><label className="text-xs">Min Sepet Tutarı</label><input type="number" value={form.min_cart || 0} onChange={(e) => setForm({ ...form, min_cart: parseFloat(e.target.value) })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
                  <div><label className="text-xs">Kargo Ücreti</label><input type="number" value={form.shipping_cost || 0} onChange={(e) => setForm({ ...form, shipping_cost: parseFloat(e.target.value) })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
                  <label className="flex items-center gap-2"><input type="checkbox" checked={!!form.free_shipping} onChange={(e) => setForm({ ...form, free_shipping: e.target.checked })} /><span className="text-sm">Kargo bedava</span></label>
                </>
              ) : (
                <>
                  <div><label className="text-xs">Ödeme Yöntemi</label>
                    <select value={form.payment_method || "havale"} onChange={(e) => setForm({ ...form, payment_method: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm">
                      <option value="havale">Havale/EFT</option>
                      <option value="iyzico">Kredi Kartı (Iyzico)</option>
                      <option value="cash_on_delivery">Kapıda Ödeme</option>
                    </select>
                  </div>
                  <div><label className="text-xs">İndirim Tipi</label>
                    <select value={form.discount_type || "percent"} onChange={(e) => setForm({ ...form, discount_type: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm">
                      <option value="percent">Yüzde (%)</option>
                      <option value="fixed">Sabit (₺)</option>
                    </select>
                  </div>
                  <div><label className="text-xs">Değer</label><input type="number" value={form.discount_value || 0} onChange={(e) => setForm({ ...form, discount_value: parseFloat(e.target.value) })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
                  <div><label className="text-xs">Etiket (ör: "Havaleye %3")</label><input value={form.label || ""} onChange={(e) => setForm({ ...form, label: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
                  <div><label className="text-xs">Min Sepet</label><input type="number" value={form.min_cart || 0} onChange={(e) => setForm({ ...form, min_cart: parseFloat(e.target.value) })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
                </>
              )}
              <label className="flex items-center gap-2"><input type="checkbox" checked={!!form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /><span className="text-sm">Aktif</span></label>
            </div>
            <div className="flex justify-end gap-2 mt-4 pt-3 border-t">
              <button onClick={() => setShow(false)} className="px-3 py-2 text-sm">Vazgeç</button>
              <button onClick={save} className="px-4 py-2 bg-black text-white rounded text-sm">Kaydet</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Currency Rates
export function CurrencyRates() {
  const [data, setData] = useState(null);
  const load = async () => {
    const { data } = await axios.get(`${API}/admin/currency/rates`, { headers: h() });
    setData(data);
  };
  const refresh = async () => {
    await axios.post(`${API}/admin/currency/refresh`, {}, { headers: h() });
    toast.success("Kurlar güncellendi"); load();
  };
  useEffect(() => { load(); }, []);
  return (
    <div className="space-y-5" data-testid="currency-page">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold flex items-center gap-2"><DollarSign /> Döviz Kurları</h1>
        <button onClick={refresh} className="px-4 py-2 bg-black text-white rounded-lg text-sm inline-flex items-center gap-1"><RefreshCw size={14} /> Güncelle</button>
      </div>
      <div className="text-sm text-gray-500">Kaynak: {data?.source || "—"} · Son güncelleme: {data?.updated_at ? new Date(data.updated_at).toLocaleString("tr-TR") : "—"}</div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {Object.entries(data?.rates || {}).map(([k, v]) => (
          <div key={k} className="bg-gradient-to-br from-slate-800 to-slate-700 text-white rounded-xl p-5">
            <div className="text-xs uppercase opacity-70">1 {k}</div>
            <div className="text-3xl font-bold mt-1">₺{v}</div>
          </div>
        ))}
        {Object.keys(data?.rates || {}).length === 0 && <div className="col-span-4 text-center text-gray-400 py-8">Henüz kur yok, "Güncelle" butonuna basın.</div>}
      </div>
    </div>
  );
}

// Bulk Mail
export function BulkMail() {
  const [segment, setSegment] = useState("all");
  const [subject, setSubject] = useState("");
  const [html, setHtml] = useState("<h1>Merhaba</h1><p>...</p>");
  const [sending, setSending] = useState(false);
  const [campaigns, setCampaigns] = useState([]);

  const load = async () => {
    const { data } = await axios.get(`${API}/admin/email/campaigns`, { headers: h() });
    setCampaigns(data.items || []);
  };
  useEffect(() => { load(); }, []);
  const send = async () => {
    if (!subject.trim() || !html.trim()) return toast.warning("Konu ve içerik zorunlu");
    setSending(true);
    try {
      const { data } = await axios.post(`${API}/admin/email/send-bulk`, { segment, subject, html }, { headers: h() });
      toast.success(`${data.result.success} gönderildi / ${data.result.failed} başarısız`);
      load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Hata"); }
    finally { setSending(false); }
  };

  return (
    <div className="space-y-5" data-testid="bulk-mail-page">
      <h1 className="text-2xl font-bold flex items-center gap-2"><Mail /> Toplu Mail Gönder</h1>
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-900">
        Resend API üzerinden gönderim. <strong>.env dosyasında RESEND_API_KEY tanımlı olmalı.</strong> Tanımlı değilse gönderim başarısız olur ama kayıt tutulur. Gönderici: <code>RESEND_FROM</code> (varsayılan <code>onboarding@resend.dev</code>).
      </div>
      <div className="bg-white border rounded-xl p-5 space-y-3">
        <div>
          <label className="text-xs text-gray-600">Hedef Segment</label>
          <select value={segment} onChange={(e) => setSegment(e.target.value)} className="w-full mt-1 px-3 py-2 border rounded text-sm">
            <option value="all">Tüm Aktif Üyeler</option>
            <option value="newsletter">Newsletter Onaylılar</option>
            <option value="abandoned">Terkedilmiş Sepet Sahipleri</option>
          </select>
        </div>
        <div><label className="text-xs">Konu</label><input value={subject} onChange={(e) => setSubject(e.target.value)} className="w-full mt-1 px-3 py-2 border rounded text-sm" placeholder="Yaz kampanyası %30 indirim" /></div>
        <div><label className="text-xs">HTML İçerik (inline CSS)</label><textarea rows={10} value={html} onChange={(e) => setHtml(e.target.value)} className="w-full mt-1 px-3 py-2 border rounded text-sm font-mono text-xs" /></div>
        <button onClick={send} disabled={sending} className="px-5 py-2.5 bg-black text-white rounded-lg inline-flex items-center gap-2 disabled:opacity-50">
          <Send size={15} /> {sending ? "Gönderiliyor..." : "Gönder"}
        </button>
      </div>

      <div className="bg-white border rounded-xl overflow-hidden">
        <h3 className="font-semibold p-4 pb-2">Geçmiş Kampanyalar</h3>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr><th className="text-left p-3">Konu</th><th className="text-left p-3">Segment</th><th className="text-right p-3">Alıcı</th><th className="text-right p-3">Başarılı</th><th className="text-right p-3">Başarısız</th><th className="text-left p-3">Tarih</th></tr>
          </thead>
          <tbody>
            {campaigns.length === 0 ? <tr><td colSpan={6} className="p-6 text-center text-gray-400">Henüz kampanya yok.</td></tr>
              : campaigns.map((c) => (
                <tr key={c.id} className="border-t">
                  <td className="p-3 font-medium">{c.subject}</td>
                  <td className="p-3 text-xs">{c.segment}</td>
                  <td className="p-3 text-right">{c.recipient_count}</td>
                  <td className="p-3 text-right text-green-700 font-semibold">{c.success}</td>
                  <td className="p-3 text-right text-red-700">{c.failed}</td>
                  <td className="p-3 text-xs text-gray-500">{new Date(c.sent_at).toLocaleString("tr-TR")}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Extra reports: Hourly, City, Profit, Stock Movements
export function ExtraReports() {
  const [hourly, setHourly] = useState([]);
  const [cities, setCities] = useState([]);
  const [profit, setProfit] = useState([]);
  const [moves, setMoves] = useState([]);
  const [source, setSource] = useState("all");
  useEffect(() => {
    (async () => {
      // Saatlik + il bazında rapor KAYNAĞA göre ayrılır (site/trendyol/hepsiburada/temu).
      const results = await Promise.allSettled([
        axios.get(`${API}/admin/reports-extra/hourly`, { headers: h(), params: { source } }),
        axios.get(`${API}/admin/reports-extra/by-city`, { headers: h(), params: { source } }),
        axios.get(`${API}/admin/reports-extra/profit`, { headers: h() }),
        axios.get(`${API}/admin/reports-extra/stock-movements`, { headers: h() }),
      ]);
      const pick = (r) => (r.status === "fulfilled" ? r.value.data : null);
      setHourly(pick(results[0])?.rows || []);
      setCities(pick(results[1])?.rows || []);
      setProfit(pick(results[2])?.rows || []);
      setMoves(pick(results[3])?.rows || []);
    })();
  }, [source]);
  return (
    <div className="space-y-6" data-testid="extra-reports-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold">Gelişmiş Raporlar</h1>
        <select value={source} onChange={(e) => setSource(e.target.value)} className="px-3 py-1.5 border rounded text-sm" title="Saatlik ve il bazında satışı kaynağa göre ayır">
          <option value="all">Tüm Kaynaklar</option>
          <option value="site">Site (Kendi)</option>
          <option value="trendyol">Trendyol</option>
          <option value="hepsiburada">Hepsiburada</option>
          <option value="temu">Temu</option>
        </select>
      </div>

      <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-900">
        <span className="font-semibold">Bu raporda:</span> Dört farklı kesit bir arada — <b>saatlik satış</b> (son 7 gün, en yoğun saatler), <b>il bazında satış</b> (son 30 gün), <b>ürün karlılık raporu</b> (satılan, ciro, maliyet, kâr ve marj %) ve <b>stok hareketi</b> (son 30 günde en çok çıkan ürünler). Karlılık tablosundan hangi ürünlerin gerçekte kâr bıraktığını, düşük marjlıları fark edip fiyat/maliyet aksiyonu alabilirsiniz (kâr için ürünlere <b>alış maliyeti</b> girilmiş olmalı).
      </div>

      <div className="bg-white rounded-xl border p-5">
        <h3 className="font-semibold mb-3">Saatlik Satış (Son 7 Gün)</h3>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={hourly}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="hour" tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}:00`} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip labelFormatter={(v) => `${v}:00`} />
            <Bar dataKey="revenue" fill="#3b82f6" name="Ciro (₺)" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-white rounded-xl border">
        <h3 className="font-semibold p-5 pb-3">İl Bazında Satış (Son 30 Gün)</h3>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="text-left p-3">İl</th><th className="text-right p-3">Sipariş</th><th className="text-right p-3">Ciro</th></tr></thead>
          <tbody>{cities.slice(0, 20).map((c) => (
            <tr key={c.city} className="border-t"><td className="p-3">{c.city}</td><td className="p-3 text-right">{c.orders}</td><td className="p-3 text-right font-semibold">₺{c.revenue.toLocaleString("tr-TR")}</td></tr>
          ))}</tbody>
        </table>
      </div>

      <div className="bg-white rounded-xl border">
        <h3 className="font-semibold p-5 pb-3">Ürün Karlılık Raporu</h3>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="text-left p-3">Ürün</th><th className="text-right p-3">Satılan</th><th className="text-right p-3">Ciro</th><th className="text-right p-3">Maliyet</th><th className="text-right p-3">Kar</th><th className="text-right p-3">Marj %</th></tr></thead>
          <tbody>{profit.slice(0, 20).map((p, i) => (
            <tr key={i} className="border-t">
              <td className="p-3 font-medium">{p.name}</td>
              <td className="p-3 text-right">{p.qty}</td>
              <td className="p-3 text-right">₺{p.revenue.toLocaleString("tr-TR")}</td>
              <td className="p-3 text-right text-gray-500">₺{p.cost.toLocaleString("tr-TR")}</td>
              <td className="p-3 text-right font-semibold text-emerald-700">₺{p.profit.toLocaleString("tr-TR")}</td>
              <td className="p-3 text-right">{p.margin_pct}%</td>
            </tr>
          ))}{profit.length === 0 && <tr><td colSpan={6} className="p-4 text-center text-gray-400">Maliyet (cost_price) ürünlere girilmediği için veri yok.</td></tr>}</tbody>
        </table>
      </div>

      <div className="bg-white rounded-xl border">
        <h3 className="font-semibold p-5 pb-3">Stok Hareket (Son 30 Gün - En Çok Çıkan)</h3>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="text-left p-3">Ürün</th><th className="text-left p-3">Stok Kodu</th><th className="text-center p-3">Mevcut</th><th className="text-right p-3">Çıkan</th></tr></thead>
          <tbody>{moves.slice(0, 20).map((m) => (
            <tr key={m.product_id} className="border-t"><td className="p-3">{m.name}</td><td className="p-3 font-mono text-xs">{m.stock_code}</td><td className="p-3 text-center">{m.current_stock}</td><td className="p-3 text-right font-semibold">{m.units_out}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}
