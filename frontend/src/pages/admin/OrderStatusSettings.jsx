import { useState, useEffect, useCallback, useMemo } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, MessageSquare, Mail, Eye, Plus, Trash2, Bell } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function Toggle({ checked, onChange, color }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${checked ? "" : "bg-gray-200"}`}
      style={checked ? { backgroundColor: color || "#111" } : undefined}
    >
      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
    </button>
  );
}

export default function OrderStatusSettings() {
  const [rows, setRows] = useState([]);
  const [orig, setOrig] = useState({});   // çekirdek durumların ORİJİNAL etiketleri (diff için)
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get(`${API}/settings/order-statuses`, auth());
      const list = res.data?.statuses || [];
      setRows(list);
      setOrig(Object.fromEntries(
        list.filter((r) => !r.is_custom).map((r) => [r.key, { label: r.label, customer_label: r.customer_label }])
      ));
    } catch (e) {
      toast.error("Durum ayarları yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const patch = (key, field, val) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, [field]: val } : r)));

  // Özel durumun bağlanabileceği bildirim event'leri (çekirdek durumların event'lerinden türetilir).
  const eventOptions = useMemo(() => {
    const seen = new Map();
    rows.filter((r) => !r.is_custom && r.event).forEach((r) => {
      if (!seen.has(r.event)) seen.set(r.event, r.label);
    });
    return [{ key: "", name: "— Bildirim gönderme —" }, ...Array.from(seen, ([key, name]) => ({ key, name: `${name} (${key})` }))];
  }, [rows]);

  const eventName = (ev) => {
    if (!ev) return "Bildirim yok";
    const r = rows.find((x) => x.event === ev);
    return r ? r.label : ev;
  };

  const addCustom = () => {
    const id = "ozel_" + Math.random().toString(36).slice(2, 7);
    setRows((rs) => [...rs, {
      key: id, label: "Yeni Durum", customer_label: "Yeni Durum",
      event: "", color: "#6B7280", group: "Özel", is_custom: true,
      active: true, sms: false, email: false,
    }]);
    toast.message("Özel durum eklendi — düzenleyip Kaydet'e bas.");
  };

  const removeRow = (key) => setRows((rs) => rs.filter((r) => r.key !== key));

  const save = async () => {
    try {
      setSaving(true);
      const active = rows.filter((r) => r.active).map((r) => r.key);
      const notify = {};
      rows.forEach((r) => { notify[r.key] = { sms: !!r.sms, email: !!r.email }; });
      const custom = rows.filter((r) => r.is_custom).map((r) => ({
        key: r.key, label: r.label, customer_label: r.customer_label,
        event: r.event || null, color: r.color, group: r.group,
      }));
      const labels = {};
      rows.forEach((r) => {
        if (r.is_custom) return;
        const o = orig[r.key] || {};
        if (r.label !== o.label || r.customer_label !== o.customer_label) {
          labels[r.key] = { label: r.label, customer_label: r.customer_label };
        }
      });
      await axios.post(`${API}/settings/order-statuses`, { active, notify, custom, labels }, auth());
      toast.success("Sipariş durumu ayarları kaydedildi");
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  // grup sırası — bilinen gruplar önce, geri kalan (Özel vb.) sona
  const knownOrder = ["Başlangıç", "Hazırlık", "Kargo", "İade", "Son"];
  const groups = useMemo(() => {
    const names = [...new Set(rows.map((r) => r.group))];
    names.sort((a, b) => {
      const ia = knownOrder.indexOf(a), ib = knownOrder.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });
    return names.map((g) => ({ name: g, items: rows.filter((r) => r.group === g) })).filter((g) => g.items.length);
  }, [rows]);

  const inputCls = "w-full bg-transparent border border-transparent hover:border-gray-200 focus:border-gray-300 focus:bg-white rounded px-1.5 py-1 text-sm outline-none transition-colors";

  return (
    <div data-testid="order-status-settings" className="max-w-5xl">
      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Sipariş Durumları</h1>
          <p className="text-sm text-gray-500 mt-1">
            Tüm sipariş/iade durumları buradan yönetilir (tek kaynak). Hangileri <b>görünsün</b>, hangi durumda müşteriye
            {" "}<b>SMS</b> / <b>e-posta</b> gitsin, etiketler ne olsun seç; <b>yeni durum</b> ekle.
          </p>
        </div>
        <button onClick={save} disabled={saving}
          data-testid="save-statuses"
          className="inline-flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50 shrink-0">
          <Save size={16} /> {saving ? "Kaydediliyor..." : "Kaydet"}
        </button>
      </div>

      {loading ? (
        <div className="p-8 text-center text-gray-400">Yükleniyor...</div>
      ) : (
        <div className="space-y-6">
          {groups.map((g) => (
            <div key={g.name}>
              <h2 className="text-xs font-bold text-gray-400 uppercase tracking-wide mb-2">{g.name}</h2>
              <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
                <div className="hidden sm:grid grid-cols-[1fr_auto_auto_auto] gap-4 px-4 py-2 bg-gray-50 text-[11px] font-bold text-gray-400 uppercase">
                  <span>Durum · Etiket · Bildirim</span>
                  <span className="inline-flex items-center gap-1 justify-center w-20"><Eye size={12} /> Görünür</span>
                  <span className="inline-flex items-center gap-1 justify-center w-16"><MessageSquare size={12} /> SMS</span>
                  <span className="inline-flex items-center gap-1 justify-center w-16"><Mail size={12} /> Mail</span>
                </div>
                <div className="divide-y">
                  {g.items.map((r) => (
                    <div key={r.key} className="grid grid-cols-[1fr_auto_auto_auto] gap-4 px-4 py-3 items-start">
                      <div className="min-w-0 space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: r.color }} />
                          <input value={r.label} onChange={(e) => patch(r.key, "label", e.target.value)}
                            className={`${inputCls} font-semibold text-gray-900`} placeholder="Panel etiketi" />
                          {r.is_custom && (
                            <span className="text-[10px] bg-amber-100 text-amber-700 rounded px-1.5 py-0.5 shrink-0">özel</span>
                          )}
                        </div>
                        <div className="flex items-center gap-1 pl-4.5">
                          <span className="text-[11px] text-gray-400 shrink-0">Müşteriye:</span>
                          <input value={r.customer_label} onChange={(e) => patch(r.key, "customer_label", e.target.value)}
                            className={`${inputCls} text-xs text-gray-600`} placeholder="Müşteri etiketi" />
                        </div>
                        <div className="flex items-center gap-2 pl-4 flex-wrap">
                          <Bell size={11} className="text-gray-300 shrink-0" />
                          {r.is_custom ? (
                            <>
                              <select value={r.event || ""} onChange={(e) => patch(r.key, "event", e.target.value)}
                                className="text-[11px] border border-gray-200 rounded px-1.5 py-0.5 bg-white max-w-[220px]">
                                {eventOptions.map((o) => <option key={o.key} value={o.key}>{o.name}</option>)}
                              </select>
                              <input value={r.color} onChange={(e) => patch(r.key, "color", e.target.value)}
                                className="w-20 text-[11px] border border-gray-200 rounded px-1.5 py-0.5" placeholder="#RRGGBB" title="Renk" />
                              <input value={r.group} onChange={(e) => patch(r.key, "group", e.target.value)}
                                className="w-24 text-[11px] border border-gray-200 rounded px-1.5 py-0.5" placeholder="Grup" title="Grup" />
                              <button onClick={() => removeRow(r.key)} title="Durumu sil"
                                className="text-rose-500 hover:text-rose-700 p-0.5"><Trash2 size={13} /></button>
                            </>
                          ) : (
                            <span className="text-[11px] text-gray-400">Bildirim: <b className="text-gray-500">{eventName(r.event)}</b></span>
                          )}
                        </div>
                      </div>
                      <div className="w-20 flex justify-center pt-1"><Toggle checked={r.active} color={r.color} onChange={(v) => patch(r.key, "active", v)} /></div>
                      <div className="w-16 flex justify-center pt-1"><Toggle checked={r.sms} color={r.color} onChange={(v) => patch(r.key, "sms", v)} /></div>
                      <div className="w-16 flex justify-center pt-1"><Toggle checked={r.email} color={r.color} onChange={(v) => patch(r.key, "email", v)} /></div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}

          <button onClick={addCustom}
            className="inline-flex items-center gap-2 px-4 py-2 border border-dashed border-gray-300 text-gray-600 rounded-lg text-sm hover:border-gray-400 hover:bg-gray-50">
            <Plus size={15} /> Yeni Durum Ekle
          </button>
        </div>
      )}

      <p className="text-[11px] text-gray-400 mt-4">
        Bu liste sipariş, iade ve iptal ekranlarındaki durum menülerini <b>tek kaynaktan</b> besler — “Görünür” kapalı durumlar o menülerde çıkmaz.
        Mesaj içeriklerini <b>Ayarlar → Bildirim Şablonları</b>'ndan düzenle. Özel durumlar seçtiğin <b>bildirim</b> event'ini tetikler (SMS/Mail açıksa).
      </p>
    </div>
  );
}
