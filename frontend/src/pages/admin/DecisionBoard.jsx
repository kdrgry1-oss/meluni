import { useState, useEffect, useRef } from "react";
import axios from "axios";
import { toast } from "sonner";
import { BrainCircuit, RefreshCw, Plus, CheckCircle2, XCircle, Edit3, Save } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const URGENCY = {
  acil: { label: "ACİL", cls: "bg-red-600 text-white" },
  bu_hafta: { label: "BU HAFTA", cls: "bg-orange-500 text-white" },
  bu_ay: { label: "BU AY", cls: "bg-amber-400 text-black" },
  sezon_plani: { label: "SEZON PLANI", cls: "bg-sky-500 text-white" },
};

export default function DecisionBoard({ embed = false }) {
  const [reports, setReports] = useState([]);
  const [selected, setSelected] = useState(null);   // tam rapor dokümanı
  const [period, setPeriod] = useState(30);
  const [generating, setGenerating] = useState(false);
  const pollRef = useRef(null);

  const loadList = async (autoSelect = true) => {
    try {
      const r = await axios.get(`${API}/admin/decision-board`, { headers: authHeaders() });
      const items = r.data.items || [];
      setReports(items);
      if (autoSelect && items.length && !selected) openReport(items[0].id);
      return items;
    } catch { return []; }
  };

  const openReport = async (rid) => {
    try {
      const r = await axios.get(`${API}/admin/decision-board/${rid}`, { headers: authHeaders() });
      setSelected(r.data);
      if (r.data.status === "running") startPolling(rid);
    } catch { toast.error("Rapor açılamadı"); }
  };

  const startPolling = (rid) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const r = await axios.get(`${API}/admin/decision-board/${rid}`, { headers: authHeaders() });
        setSelected(r.data);
        if (r.data.status !== "running") { stopPolling(); setGenerating(false); loadList(false); }
      } catch { /* geçici ağ hatası — bir sonraki tıkta tekrar */ }
    }, 6000);
  };
  const stopPolling = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  useEffect(() => { loadList(); return stopPolling; /* eslint-disable-next-line */ }, []);

  const generate = async () => {
    setGenerating(true);
    try {
      const r = await axios.post(`${API}/admin/decision-board/generate`, { period_days: period }, { headers: authHeaders() });
      toast.success("Kurul toplandı — analiz birkaç dakika sürer");
      await loadList(false);
      openReport(r.data.id);
    } catch (e) {
      setGenerating(false);
      toast.error(e.response?.data?.detail || "Rapor başlatılamadı");
    }
  };

  return (
    <div className="space-y-5" data-testid="decision-board-page">
      <div className="flex justify-between items-center flex-wrap gap-3">
        <div>
          {embed ? (
            <h2 className="text-lg font-bold flex items-center gap-2"><BrainCircuit className="text-violet-600" size={20} /> Karar Destek Kurulu</h2>
          ) : (
            <h1 className="text-2xl font-bold flex items-center gap-2"><BrainCircuit className="text-violet-600" /> Karar Destek Kurulu</h1>
          )}
          <p className="text-sm text-gray-500 mt-1">
            6 uzman (Üretim · Pazarlama · Reklam · Trend · Operasyon · Finans) canlı satış verilerini tartışır,
            şüpheci ajan zayıf önerileri eler, size numaralı kararlar sunulur — her kararın altına yorumunuzu yazın.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select value={period} onChange={(e) => setPeriod(Number(e.target.value))} className="border rounded-lg px-3 py-2 text-sm">
            <option value={30}>Son 30 gün</option>
            <option value={60}>Son 60 gün</option>
            <option value={90}>Son 90 gün</option>
          </select>
          <button onClick={generate} disabled={generating}
            className="inline-flex items-center gap-2 px-4 py-2 bg-violet-600 text-white rounded-lg text-sm font-bold hover:bg-violet-700 disabled:opacity-50"
            data-testid="generate-report-btn">
            <Plus size={15} /> {generating ? "Kurul çalışıyor…" : "Kurulu Topla (Yeni Rapor)"}
          </button>
        </div>
      </div>

      {reports.length > 1 && (
        <div className="flex items-center gap-2 flex-wrap text-sm">
          <span className="text-xs text-gray-500">Geçmiş raporlar:</span>
          {reports.map((r) => (
            <button key={r.id} onClick={() => openReport(r.id)}
              className={`px-3 py-1 rounded-full border text-xs ${selected?.id === r.id ? "bg-black text-white border-black" : "bg-white hover:bg-gray-50"}`}>
              {new Date(r.created_at).toLocaleDateString("tr-TR")} · {r.period_days}g {r.status === "running" ? "⏳" : r.status === "failed" ? "⚠️" : ""}
            </button>
          ))}
        </div>
      )}

      {!selected && (
        <div className="bg-white border rounded-xl p-10 text-center text-gray-400">
          Henüz rapor yok — sağ üstten <b>Kurulu Topla</b> ile ilk raporu oluşturun.
        </div>
      )}

      {selected?.status === "running" && (
        <div className="bg-violet-50 border border-violet-200 rounded-xl p-8 text-center">
          <RefreshCw className="animate-spin mx-auto text-violet-600 mb-3" size={28} />
          <p className="font-semibold text-violet-800">{selected.progress || "Kurul çalışıyor…"}</p>
          <p className="text-xs text-violet-500 mt-1">Bu sayfa otomatik yenilenir — birkaç dakika sürebilir, sayfadan ayrılabilirsiniz.</p>
        </div>
      )}

      {selected?.status === "failed" && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-5 text-sm text-red-700">
          Rapor üretilemedi: {selected.progress}
        </div>
      )}

      {selected?.status === "done" && <ReportView report={selected} onSaved={() => openReport(selected.id)} />}
    </div>
  );
}

function ReportView({ report, onSaved }) {
  return (
    <div className="space-y-5">
      <div className="bg-white border rounded-xl p-5">
        <h2 className="text-sm font-bold uppercase tracking-wider mb-2">📊 Durum Özeti</h2>
        <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{report.summary}</p>
      </div>

      {report.methodology && (
        <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-5">
          <h2 className="text-sm font-bold uppercase tracking-wider mb-2 text-indigo-800">⚖️ Kurulun Metodoloji Kararı: Satış Hızı</h2>
          <p className="text-sm text-indigo-900 leading-relaxed whitespace-pre-line">{report.methodology}</p>
        </div>
      )}

      <div className="space-y-4">
        <h2 className="text-lg font-bold">✅ Kararlar ({(report.decisions || []).length})</h2>
        {(report.decisions || []).map((d) => (
          <DecisionCard key={d.id} reportId={report.id} decision={d} onSaved={onSaved} />
        ))}
      </div>

      {report.conflicts && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-5">
          <h2 className="text-sm font-bold uppercase tracking-wider mb-2 text-amber-800">⚔️ Kurulda Uzlaşılamayan Konular</h2>
          <p className="text-sm text-amber-900 leading-relaxed whitespace-pre-line">{report.conflicts}</p>
        </div>
      )}

      {report.rejected && (
        <div className="bg-gray-50 border rounded-xl p-5">
          <h2 className="text-sm font-bold uppercase tracking-wider mb-2 text-gray-500">🗑️ Elenen Öneriler (şüpheci ajan çürüttü)</h2>
          <p className="text-xs text-gray-500 leading-relaxed whitespace-pre-line">{report.rejected}</p>
        </div>
      )}
    </div>
  );
}

function DecisionCard({ reportId, decision, onSaved }) {
  const [verdict, setVerdict] = useState(decision.user_verdict || "");
  const [comment, setComment] = useState(decision.user_comment || "");
  const [saving, setSaving] = useState(false);
  const dirty = verdict !== (decision.user_verdict || "") || comment !== (decision.user_comment || "");
  const u = URGENCY[decision.urgency] || URGENCY.bu_ay;

  const save = async () => {
    setSaving(true);
    try {
      await axios.put(`${API}/admin/decision-board/${reportId}/decisions/${decision.id}`,
        { user_verdict: verdict, user_comment: comment }, { headers: authHeaders() });
      toast.success(`${decision.id} yorumu kaydedildi`);
      onSaved?.();
    } catch { toast.error("Kaydedilemedi"); }
    finally { setSaving(false); }
  };

  return (
    <div className="bg-white border rounded-xl p-5" data-testid={`decision-${decision.id}`}>
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <h3 className="font-bold text-[15px]">
          <span className="text-violet-600 mr-2">{decision.id}</span>{decision.title}
        </h3>
        <span className={`px-2.5 py-1 rounded-full text-[10px] font-bold tracking-wider ${u.cls}`}>{u.label}</span>
      </div>
      <div className="mt-3 space-y-2 text-sm">
        <p><span className="font-semibold text-gray-600">Ne yapılacak:</span> {decision.action}</p>
        <p className="text-gray-600"><span className="font-semibold">Neden:</span> {decision.rationale}</p>
        {(decision.products || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="font-semibold text-gray-600 text-xs">İlgili ürünler:</span>
            {decision.products.map((p) => (
              <span key={p} className="px-2 py-0.5 bg-gray-100 border rounded-full text-xs">{p}</span>
            ))}
          </div>
        )}
        {decision.counterpoint && (
          <p className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded p-2">
            <b>Karşı görüş:</b> {decision.counterpoint}
          </p>
        )}
      </div>

      {/* Patron yorumu */}
      <div className="mt-4 border-t pt-3">
        <div className="flex items-center gap-2 flex-wrap mb-2">
          <span className="text-xs font-bold text-gray-500">Senin kararın:</span>
          {[
            { v: "katiliyorum", label: "Katılıyorum", icon: <CheckCircle2 size={13} />, on: "bg-emerald-600 text-white border-emerald-600" },
            { v: "reddediyorum", label: "Reddediyorum", icon: <XCircle size={13} />, on: "bg-red-600 text-white border-red-600" },
            { v: "duzenle", label: "Düzenleyerek", icon: <Edit3 size={13} />, on: "bg-amber-500 text-white border-amber-500" },
          ].map((b) => (
            <button key={b.v} onClick={() => setVerdict(verdict === b.v ? "" : b.v)}
              className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-semibold transition
                ${verdict === b.v ? b.on : "bg-white text-gray-500 border-gray-300 hover:border-gray-400"}`}>
              {b.icon} {b.label}
            </button>
          ))}
          {decision.commented_at && !dirty && (
            <span className="text-[10px] text-gray-400 ml-auto">kaydedildi · {new Date(decision.commented_at).toLocaleString("tr-TR")}</span>
          )}
        </div>
        <div className="flex gap-2">
          <input value={comment} onChange={(e) => setComment(e.target.value)}
            placeholder="Yorumun… (ör. 'önce 200 adetle başlayalım', 'fiyatı düşürmeden dene')"
            className="flex-1 border rounded-lg px-3 py-2 text-sm" data-testid={`comment-${decision.id}`} />
          <button onClick={save} disabled={saving || !dirty}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm font-semibold disabled:opacity-40 hover:bg-gray-800">
            <Save size={14} /> Kaydet
          </button>
        </div>
      </div>
    </div>
  );
}
