/**
 * Chargebacks.jsx — Ödeme İtirazları (chargeback / dispute) takibi (COM11 / FIN-E03).
 *
 * Banka/iyzico bir tahsilata itiraz bildirdiğinde personel itirazı siparişe bağlı olarak
 * kaydeder; savunma son tarihi, banka referansı, sebep kodu ve delil notlarını izler; sonucu
 * (Kazanıldı / Kaybedildi / Kabul edildi) işler. Yetki: `returns.refund_pay` ya da süper admin.
 * Sipariş ve ödeme durumu bu ekrandan DEĞİŞMEZ (yalnız itiraz kaydı tutulur).
 */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { AlertTriangle, Plus, Search, Pencil, X } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const STATUS = {
  open: { label: "Açık", cls: "bg-amber-50 text-amber-700 border-amber-200" },
  won: { label: "Kazanıldı", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  lost: { label: "Kaybedildi", cls: "bg-red-50 text-red-600 border-red-200" },
  accepted: { label: "Kabul edildi", cls: "bg-gray-50 text-gray-600 border-gray-200" },
};
const STATUS_KEYS = ["open", "won", "lost", "accepted"];

const EMPTY_FORM = {
  order_number: "", amount: "", currency: "TRY", reason_code: "", bank_ref: "",
  deadline: "", status: "open", evidence_notes: "", outcome_notes: "",
};

const money = (v, cur) => `${Number(v || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur || "TRY"}`;
const fmtDate = (iso) => { try { return new Date(iso).toLocaleString("tr-TR"); } catch { return iso || ""; } };

function StatusBadge({ status }) {
  const s = STATUS[status] || { label: status || "—", cls: "bg-gray-50 text-gray-600 border-gray-200" };
  return (
    <span className={`inline-flex items-center text-[10px] font-semibold px-1.5 py-0.5 rounded border ${s.cls}`}>
      {s.label}
    </span>
  );
}

function DeadlineCell({ row }) {
  if (!row.deadline) return <span className="text-gray-400">—</span>;
  const d = row.days_left;
  let badge = null;
  if (row.status === "open" && d !== null && d !== undefined) {
    const cls = d < 0 ? "bg-red-50 text-red-600 border-red-200"
      : d <= 3 ? "bg-amber-50 text-amber-700 border-amber-200"
        : "bg-gray-50 text-gray-600 border-gray-200";
    const txt = d < 0 ? "süresi geçti" : d === 0 ? "bugün" : `${d} gün`;
    badge = <span className={`inline-flex items-center text-[10px] font-semibold px-1.5 py-0.5 rounded border ${cls}`}>{txt}</span>;
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="tabular-nums">{row.deadline}</span>
      {badge}
    </span>
  );
}

export default function Chargebacks() {
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({});
  const [openAmount, setOpenAmount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [due, setDue] = useState("");
  const [modal, setModal] = useState(null); // {mode: 'create'|'edit', form, row}
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = { limit: 300 };
      if (status) params.status = status;
      if (query) params.q = query;
      if (due === "overdue") params.overdue = true;
      if (due === "soon") params.due_within_days = 3;
      const { data } = await axios.get(`${API}/admin/chargebacks`, { headers: authHeaders(), params });
      setItems(data.items || []);
      setCounts(data.counts || {});
      setOpenAmount(Number(data.open_amount || 0));
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Liste yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, [status, query, due]);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => setModal({ mode: "create", form: { ...EMPTY_FORM }, row: null });
  const openEdit = async (row) => {
    let full = row;
    try {
      const { data } = await axios.get(`${API}/admin/chargebacks/${row.id}`, { headers: authHeaders() });
      full = data.chargeback || row;
    } catch { /* liste satırıyla devam */ }
    setModal({
      mode: "edit",
      row: full,
      form: {
        ...EMPTY_FORM,
        order_number: full.order_number || "",
        amount: String(full.amount ?? ""),
        currency: full.currency || "TRY",
        reason_code: full.reason_code || "",
        bank_ref: full.bank_ref || "",
        deadline: full.deadline || "",
        status: full.status || "open",
        evidence_notes: full.evidence_notes || "",
        outcome_notes: full.outcome_notes || "",
      },
    });
  };
  const setField = (k, v) => setModal((m) => ({ ...m, form: { ...m.form, [k]: v } }));

  const save = async () => {
    if (!modal) return;
    const f = modal.form;
    const amount = Number(f.amount);
    if (!amount || amount <= 0) { toast.error("Geçerli bir tutar girin"); return; }
    if (modal.mode === "create" && !f.order_number.trim()) { toast.error("Sipariş numarası zorunlu"); return; }
    setSaving(true);
    try {
      if (modal.mode === "create") {
        await axios.post(`${API}/admin/chargebacks`, {
          order_number: f.order_number.trim(), amount, currency: f.currency.trim() || "TRY",
          reason_code: f.reason_code.trim(), bank_ref: f.bank_ref.trim(), deadline: f.deadline,
          status: f.status, evidence_notes: f.evidence_notes,
        }, { headers: authHeaders() });
        toast.success("İtiraz kaydedildi");
      } else {
        await axios.patch(`${API}/admin/chargebacks/${modal.row.id}`, {
          amount, reason_code: f.reason_code.trim(), bank_ref: f.bank_ref.trim(), deadline: f.deadline,
          status: f.status, evidence_notes: f.evidence_notes, outcome_notes: f.outcome_notes,
        }, { headers: authHeaders() });
        toast.success("İtiraz güncellendi");
      }
      setModal(null);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  const inputCls = "w-full border rounded px-3 py-2 text-sm";
  const labelCls = "block text-xs font-bold text-gray-500 mb-1";

  return (
    <div className="space-y-5" data-testid="chargebacks-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold inline-flex items-center gap-2"><AlertTriangle /> Ödeme İtirazları</h1>
          <p className="text-sm text-gray-500 mt-1">
            Banka/iyzico itirazlarını (chargeback) siparişe bağlı kaydedin; son tarihi ve delilleri takip edin.
            Son tarihi 3 gün içinde olan açık itirazlar için yöneticilere her gün e-posta uyarısı gider.
            Sipariş ve ödeme durumu bu ekrandan değişmez.
          </p>
        </div>
        <button onClick={openCreate} data-testid="new-chargeback-btn"
          className="inline-flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm hover:bg-gray-800">
          <Plus size={14} /> Yeni İtiraz
        </button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {STATUS_KEYS.map((k) => (
          <button key={k} onClick={() => setStatus(status === k ? "" : k)}
            className={`text-left bg-white border rounded-xl px-4 py-3 ${status === k ? "border-black" : "hover:border-gray-400"}`}>
            <div className="text-xs text-gray-500">{STATUS[k].label}</div>
            <div className="text-xl font-bold tabular-nums">{Number(counts[k] || 0)}</div>
          </button>
        ))}
        <div className="bg-white border rounded-xl px-4 py-3">
          <div className="text-xs text-gray-500">Açık itiraz tutarı</div>
          <div className="text-xl font-bold tabular-nums">{money(openAmount)}</div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && setQuery(q.trim())}
            placeholder="Sipariş no / banka ref / sebep kodu…" className="w-full pl-8 pr-3 py-2 border rounded-lg text-sm"
            data-testid="chargeback-search" />
        </div>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
          <option value="">Tüm durumlar</option>
          {STATUS_KEYS.map((k) => <option key={k} value={k}>{STATUS[k].label}</option>)}
        </select>
        <select value={due} onChange={(e) => setDue(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
          <option value="">Tüm son tarihler</option>
          <option value="soon">Son tarihi 3 gün içinde</option>
          <option value="overdue">Süresi geçmiş</option>
        </select>
        <button onClick={() => setQuery(q.trim())} className="px-3 py-2 border rounded-lg text-sm hover:bg-gray-50">Ara</button>
      </div>

      <div className="bg-white border rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Sipariş</th>
              <th className="text-right p-3">Tutar</th>
              <th className="text-left p-3">Sebep kodu</th>
              <th className="text-left p-3">Banka ref.</th>
              <th className="text-left p-3">Son tarih</th>
              <th className="text-left p-3">Durum</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Kayıtlı ödeme itirazı yok.</td></tr>
            ) : items.map((r) => (
              <tr key={r.id} className="border-t align-middle">
                <td className="p-3 font-mono text-xs font-bold">{r.order_number || "—"}</td>
                <td className="p-3 text-right tabular-nums">{money(r.amount, r.currency)}</td>
                <td className="p-3 text-xs">{r.reason_code || "—"}</td>
                <td className="p-3 text-xs font-mono">{r.bank_ref || "—"}</td>
                <td className="p-3 text-xs"><DeadlineCell row={r} /></td>
                <td className="p-3"><StatusBadge status={r.status} /></td>
                <td className="p-3 text-right">
                  <button onClick={() => openEdit(r)} data-testid={`chargeback-edit-${r.id}`}
                    className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded border text-gray-700 border-gray-300 hover:bg-gray-50">
                    <Pencil size={11} /> Düzenle
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" role="dialog" aria-modal="true">
          <div className="bg-white rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between px-5 py-4 border-b">
              <h2 className="text-lg font-bold">
                {modal.mode === "create" ? "Yeni Ödeme İtirazı" : `İtiraz · ${modal.row?.order_number || ""}`}
              </h2>
              <button onClick={() => setModal(null)} className="inline-flex items-center justify-center w-8 h-8 rounded hover:bg-gray-100" aria-label="Kapat">
                <X size={16} />
              </button>
            </div>
            <div className="p-5 grid sm:grid-cols-2 gap-4">
              <div>
                <label className={labelCls}>Sipariş no {modal.mode === "create" ? "(zorunlu)" : ""}</label>
                <input value={modal.form.order_number} disabled={modal.mode !== "create"}
                  onChange={(e) => setField("order_number", e.target.value)} placeholder="W12345"
                  className={`${inputCls} font-mono disabled:bg-gray-50`} data-testid="chargeback-order" />
              </div>
              <div>
                <label className={labelCls}>Tutar</label>
                <div className="flex items-center gap-2">
                  <input type="number" min="0" step="0.01" value={modal.form.amount}
                    onChange={(e) => setField("amount", e.target.value)} className={inputCls} data-testid="chargeback-amount" />
                  <input value={modal.form.currency} disabled={modal.mode !== "create"}
                    onChange={(e) => setField("currency", e.target.value.toUpperCase())}
                    className="w-20 border rounded px-3 py-2 text-sm disabled:bg-gray-50" />
                </div>
              </div>
              <div>
                <label className={labelCls}>Sebep kodu</label>
                <input value={modal.form.reason_code} onChange={(e) => setField("reason_code", e.target.value)}
                  placeholder="ör. 4837 / 10.4 / Fraud" className={inputCls} />
              </div>
              <div>
                <label className={labelCls}>Banka / iyzico referansı</label>
                <input value={modal.form.bank_ref} onChange={(e) => setField("bank_ref", e.target.value)}
                  className={`${inputCls} font-mono`} />
              </div>
              <div>
                <label className={labelCls}>Savunma son tarihi</label>
                <input type="date" value={modal.form.deadline} onChange={(e) => setField("deadline", e.target.value)}
                  className={inputCls} />
              </div>
              <div>
                <label className={labelCls}>Durum</label>
                <select value={modal.form.status} onChange={(e) => setField("status", e.target.value)} className={inputCls}>
                  {STATUS_KEYS.map((k) => <option key={k} value={k}>{STATUS[k].label}</option>)}
                </select>
              </div>
              <div className="sm:col-span-2">
                <label className={labelCls}>Delil notları (kargo takip, teslim kanıtı, yazışmalar…)</label>
                <textarea rows={4} value={modal.form.evidence_notes} onChange={(e) => setField("evidence_notes", e.target.value)}
                  className={inputCls} />
              </div>
              {modal.mode === "edit" && (
                <div className="sm:col-span-2">
                  <label className={labelCls}>Sonuç notu</label>
                  <textarea rows={2} value={modal.form.outcome_notes} onChange={(e) => setField("outcome_notes", e.target.value)}
                    className={inputCls} />
                </div>
              )}
              {modal.mode === "edit" && Array.isArray(modal.row?.history) && modal.row.history.length > 0 && (
                <div className="sm:col-span-2">
                  <div className={labelCls}>Geçmiş</div>
                  <ul className="text-xs text-gray-600 space-y-1">
                    {modal.row.history.slice().reverse().map((h, i) => (
                      <li key={`${h.at}-${i}`} className="flex items-center gap-2">
                        <span className="tabular-nums text-gray-400">{fmtDate(h.at)}</span>
                        <span>{h.by || "—"}</span>
                        <span>{h.action === "create" ? "kayıt açtı" : `güncelledi${h.fields?.length ? ` (${h.fields.join(", ")})` : ""}`}</span>
                        {h.status && <StatusBadge status={h.status} />}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t">
              <button onClick={() => setModal(null)} className="px-4 py-2 border rounded-lg text-sm hover:bg-gray-50">Vazgeç</button>
              <button onClick={save} disabled={saving} data-testid="chargeback-save"
                className="px-4 py-2 bg-black text-white rounded-lg text-sm hover:bg-gray-800 disabled:opacity-50">
                {saving ? "Kaydediliyor…" : "Kaydet"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
