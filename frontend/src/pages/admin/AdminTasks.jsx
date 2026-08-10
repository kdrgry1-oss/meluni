import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { toast } from "sonner";
import { CheckSquare, Clock, AlertCircle, Plus, Trash2, X, Sparkles, ChevronRight, MessageSquare, Star, Banknote, Truck, BellRing, ShoppingCart, Mail, Megaphone, TrendingUp, Package, Image as ImageIcon, FileText, DollarSign, Cable } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const h = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const ICON_MAP = { MessageSquare, Star, Banknote, Truck, BellRing, ShoppingCart, Mail, Megaphone, TrendingUp, Package, Image: ImageIcon, FileText, DollarSign, Cable };

const CATEGORY_META = {
  customer_support: { label: "Müşteri", color: "bg-blue-100 text-blue-700 border-blue-200" },
  orders: { label: "Sipariş", color: "bg-green-100 text-green-700 border-green-200" },
  stock: { label: "Stok", color: "bg-amber-100 text-amber-800 border-amber-200" },
  marketing: { label: "Pazarlama", color: "bg-pink-100 text-pink-700 border-pink-200" },
  reporting: { label: "Rapor", color: "bg-violet-100 text-violet-700 border-violet-200" },
  content: { label: "İçerik", color: "bg-cyan-100 text-cyan-700 border-cyan-200" },
  seo: { label: "SEO", color: "bg-emerald-100 text-emerald-700 border-emerald-200" },
  settings: { label: "Ayar", color: "bg-gray-100 text-gray-700 border-gray-200" },
  integrations: { label: "Entegrasyon", color: "bg-orange-100 text-orange-700 border-orange-200" },
  other: { label: "Diğer", color: "bg-gray-100 text-gray-600" },
};

const PRIORITY_META = {
  urgent: { label: "Acil", dot: "bg-red-500" },
  high: { label: "Yüksek", dot: "bg-orange-500" },
  normal: { label: "Normal", dot: "bg-blue-400" },
  low: { label: "Düşük", dot: "bg-gray-400" },
};

const FREQ_LABELS = {
  once: "Tek sefer", daily: "Günlük", weekly: "Haftalık", biweekly: "2 Haftada Bir",
  monthly: "Aylık", quarterly: "3 Ayda Bir", yearly: "Yıllık", custom: "Özel",
};

function TaskRow({ task, onComplete, onSnooze, onDelete }) {
  const Icon = task.icon ? ICON_MAP[task.icon] || CheckSquare : CheckSquare;
  const cat = CATEGORY_META[task.category] || CATEGORY_META.other;
  const prio = PRIORITY_META[task.priority] || PRIORITY_META.normal;
  return (
    <div className="bg-white border rounded-xl p-4 flex items-start gap-3 hover:shadow-sm transition" data-testid={`task-${task.id}`}>
      <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center flex-shrink-0">
        <Icon size={18} className="text-slate-700" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2 flex-wrap">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${prio.dot}`} title={prio.label} />
              <h4 className="font-semibold">{task.title}</h4>
            </div>
            {task.description && <p className="text-xs text-gray-500 mt-0.5">{task.description}</p>}
            <div className="flex gap-1.5 items-center mt-2 flex-wrap">
              <span className={`text-[10px] px-1.5 py-0.5 rounded border ${cat.color}`}>{cat.label}</span>
              <span className="text-[10px] text-gray-500 bg-gray-50 px-1.5 py-0.5 rounded border">⟳ {FREQ_LABELS[task.frequency] || task.frequency}</span>
              {task.last_completed_at && <span className="text-[10px] text-gray-400">Son: {new Date(task.last_completed_at).toLocaleDateString("tr-TR")}</span>}
              {task.completion_count > 0 && <span className="text-[10px] text-emerald-600">✓ {task.completion_count}x</span>}
            </div>
          </div>
        </div>
      </div>
      <div className="flex flex-col gap-1 items-end">
        {task.action_path && (
          <Link to={task.action_path} className="text-xs text-blue-600 hover:bg-blue-50 px-2 py-1 rounded inline-flex items-center gap-1">
            Git <ChevronRight size={11} />
          </Link>
        )}
        <button onClick={() => onComplete(task.id)} data-testid={`complete-${task.id}`}
          className="text-xs px-3 py-1 bg-emerald-600 text-white rounded hover:bg-emerald-700 inline-flex items-center gap-1">
          <CheckSquare size={12} /> Tamamla
        </button>
        <div className="flex gap-1">
          <button onClick={() => onSnooze(task.id)} className="text-xs text-gray-500 hover:bg-gray-50 px-2 py-0.5 rounded inline-flex items-center gap-1">
            <Clock size={10} /> +1g
          </button>
          <button onClick={() => onDelete(task.id)} className="text-gray-400 hover:text-red-500 p-0.5"><Trash2 size={12} /></button>
        </div>
      </div>
    </div>
  );
}

export default function AdminTasks() {
  const [data, setData] = useState({ due_now: [], upcoming: [], totals: {} });
  const [summary, setSummary] = useState({ due_now: 0, overdue: 0, completed_this_week: 0 });
  const [history, setHistory] = useState({ streak: [], total: 0 });
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", category: "other", frequency: "weekly", priority: "normal", action_path: "", custom_days: 7 });

  const load = async () => {
    const [a, s, hi] = await Promise.all([
      axios.get(`${API}/admin/tasks`, { headers: h() }),
      axios.get(`${API}/admin/tasks/summary`, { headers: h() }),
      axios.get(`${API}/admin/tasks/history`, { headers: h(), params: { days: 30 } }),
    ]);
    setData(a.data);
    setSummary(s.data);
    setHistory(hi.data);
  };
  useEffect(() => { load(); }, []);

  const complete = async (id) => {
    await axios.post(`${API}/admin/tasks/${id}/complete`, {}, { headers: h() });
    toast.success("Görev tamamlandı, tekrar zamanlandı");
    load();
  };
  const snooze = async (id) => {
    await axios.post(`${API}/admin/tasks/${id}/snooze`, { hours: 24 }, { headers: h() });
    toast.success("1 gün ertelendi"); load();
  };
  const del = async (id) => {
    if (!await window.appConfirm("Görev silinsin mi?")) return;
    await axios.delete(`${API}/admin/tasks/${id}`, { headers: h() });
    load();
  };
  const save = async () => {
    if (!form.title) return toast.warning("Başlık zorunlu");
    await axios.post(`${API}/admin/tasks`, form, { headers: h() });
    toast.success("Görev eklendi"); setShowNew(false);
    setForm({ title: "", description: "", category: "other", frequency: "weekly", priority: "normal", action_path: "", custom_days: 7 });
    load();
  };
  const seedDefaults = async () => {
    if (!await window.appConfirm("16 standart görev (varsayılanları) eklensin/sıfırlansın mı?")) return;
    await axios.post(`${API}/admin/tasks/seed-defaults`, {}, { headers: h() });
    toast.success("Varsayılan görevler yüklendi"); load();
  };

  return (
    <div className="space-y-6" data-testid="admin-tasks-page">
      <div className="flex justify-between items-start flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><CheckSquare /> Görevler & Haftalık Checklist</h1>
          <p className="text-sm text-gray-500 mt-1">Düzenli yapılması gereken işleri takip et — tamamladıkça sıradaki zamanı otomatik kurulur.</p>
        </div>
        <div className="flex gap-2">
          <button onClick={seedDefaults} data-testid="seed-defaults-btn"
            className="inline-flex items-center gap-1 px-3 py-2 bg-gradient-to-r from-indigo-500 to-purple-500 text-white rounded-lg text-sm">
            <Sparkles size={14} /> Varsayılanları Yükle
          </button>
          <button onClick={() => setShowNew(true)} data-testid="new-task-btn"
            className="inline-flex items-center gap-1 px-3 py-2 bg-black text-white rounded-lg text-sm">
            <Plus size={14} /> Yeni Görev
          </button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-gradient-to-br from-red-500 to-orange-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Bugün Bekliyor</div>
          <div className="text-3xl font-bold mt-1">{summary.due_now}</div>
        </div>
        <div className="bg-gradient-to-br from-amber-500 to-yellow-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Gecikmiş</div>
          <div className="text-3xl font-bold mt-1">{summary.overdue}</div>
        </div>
        <div className="bg-gradient-to-br from-emerald-600 to-emerald-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Bu Hafta Tamamlanan</div>
          <div className="text-3xl font-bold mt-1">{summary.completed_this_week}</div>
        </div>
        <div className="bg-gradient-to-br from-slate-800 to-slate-700 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Son 30 Gün Toplam</div>
          <div className="text-3xl font-bold mt-1">{history.total}</div>
        </div>
      </div>

      {/* Due now */}
      <div>
        <h3 className="font-bold text-lg mb-3 flex items-center gap-2">
          <AlertCircle className="text-red-500" size={18} /> Şimdi Yapılacaklar ({data.due_now?.length || 0})
        </h3>
        <div className="space-y-2">
          {(data.due_now || []).length === 0 ? (
            <div className="bg-green-50 border border-green-200 rounded-xl p-6 text-center text-green-800">
              🎉 Bugün için bekleyen görev yok! İyi iş.
            </div>
          ) : (data.due_now || []).map((t) => (
            <TaskRow key={t.id} task={t} onComplete={complete} onSnooze={snooze} onDelete={del} />
          ))}
        </div>
      </div>

      {/* Upcoming */}
      {(data.upcoming || []).length > 0 && (
        <div>
          <h3 className="font-bold text-lg mb-3 flex items-center gap-2 text-gray-600">
            <Clock size={18} /> Yaklaşan ({data.upcoming.length})
          </h3>
          <div className="space-y-2 opacity-70">
            {data.upcoming.slice(0, 10).map((t) => (
              <div key={t.id} className="bg-white border rounded-xl p-3 flex items-center justify-between gap-2 text-sm">
                <div>
                  <div className="font-medium">{t.title}</div>
                  <div className="text-xs text-gray-500">
                    {CATEGORY_META[t.category]?.label} · {FREQ_LABELS[t.frequency]} · Sıradaki: {new Date(t.due_at).toLocaleDateString("tr-TR")}
                  </div>
                </div>
                <button onClick={() => del(t.id)} className="text-gray-400 hover:text-red-500 p-1"><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent completions */}
      {history.streak?.length > 0 && (
        <div>
          <h3 className="font-bold text-lg mb-3 flex items-center gap-2 text-emerald-700">
            <CheckSquare size={18} /> Tamamlanma Geçmişi (Son 30 Gün)
          </h3>
          <div className="flex gap-0.5 overflow-x-auto pb-2">
            {history.streak.map((d) => {
              const intensity = Math.min(d.completed / 5, 1);
              return (
                <div key={d.date} className="flex-shrink-0" title={`${d.date}: ${d.completed} görev`}>
                  <div className="w-4 h-4 rounded-sm" style={{ background: `rgba(16, 185, 129, ${0.2 + intensity * 0.8})` }} />
                  <div className="text-[8px] text-gray-400 mt-0.5 text-center">{d.date.slice(5)}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* New task modal */}
      {showNew && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setShowNew(false)}>
          <div className="bg-white rounded-xl w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between mb-4"><h3 className="font-bold text-lg">Yeni Görev</h3><button onClick={() => setShowNew(false)}><X size={18} /></button></div>
            <div className="space-y-3">
              <div><label className="text-xs">Başlık *</label><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} data-testid="task-title-input" className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
              <div><label className="text-xs">Açıklama</label><textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs">Kategori</label>
                  <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm">
                    {Object.entries(CATEGORY_META).map(([v, m]) => <option key={v} value={v}>{m.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs">Öncelik</label>
                  <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm">
                    {Object.entries(PRIORITY_META).map(([v, m]) => <option key={v} value={v}>{m.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs">Tekrar</label>
                  <select value={form.frequency} onChange={(e) => setForm({ ...form, frequency: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm">
                    {Object.entries(FREQ_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                  {/* DENETİM FIX (#44): 'Özel' seçilince custom_days toplanmıyordu → backend sessizce
                      7 güne düşüyordu. Artık kaç günde bir tekrarlanacağı sorulur ve POST edilir. */}
                  {form.frequency === "custom" && (
                    <div className="mt-2">
                      <label className="text-xs">Kaç günde bir</label>
                      <input
                        type="number" min="1"
                        value={form.custom_days ?? 7}
                        onChange={(e) => setForm({ ...form, custom_days: Math.max(1, parseInt(e.target.value) || 1) })}
                        className="w-full mt-1 px-3 py-2 border rounded text-sm"
                      />
                    </div>
                  )}
                </div>
                <div><label className="text-xs">Aksiyon Path</label><input value={form.action_path} onChange={(e) => setForm({ ...form, action_path: e.target.value })} placeholder="/admin/..." className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5 pt-4 border-t">
              <button onClick={() => setShowNew(false)} className="px-4 py-2 text-sm">Vazgeç</button>
              <button onClick={save} data-testid="save-task-btn" className="px-4 py-2 bg-black text-white rounded text-sm">Kaydet</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
