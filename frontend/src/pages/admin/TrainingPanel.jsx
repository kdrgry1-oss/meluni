/**
 * Eğitim / Yardım paneli — sistemdeki tüm özellikler için "Ne / Nerede / Nasıl"
 * rehberi. İçerik DÜZENLENEBİLİR: admin panelinden kaydedilen içerik (API) varsa
 * o gösterilir; yoksa lib/trainingContent.js'teki paketlenmiş VARSAYILAN kullanılır.
 * Böylece sistemi kullanan her firma kendi yönergelerini girebilir (SaaS).
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import {
  GraduationCap, Search, ChevronRight, MapPin, ExternalLink, Lightbulb,
  Pencil, Plus, Trash2, Save, X, RotateCcw,
  LayoutDashboard, ShoppingCart, Package, TrendingUp, Factory, PenTool,
  Users, Megaphone, FileText, Cable, Settings,
} from "lucide-react";
import { TRAINING, trainingSearchIndex } from "../../lib/trainingContent";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const ICONS = {
  LayoutDashboard, ShoppingCart, Package, TrendingUp, Factory, PenTool,
  Users, Megaphone, FileText, Cable, Settings,
};

const linesToArr = (s) => (s || "").split("\n").map((x) => x.trim()).filter(Boolean);
const arrToLines = (a) => (Array.isArray(a) ? a.join("\n") : "");
const deepCopy = (x) => JSON.parse(JSON.stringify(x));

function buildIndex(sections) {
  const rows = [];
  for (const sec of sections) {
    for (const it of sec.items || []) {
      const hay = [sec.title, it.title, it.what, it.where, ...(it.how || []), ...(it.tips || [])]
        .join(" ").toLocaleLowerCase("tr");
      rows.push({ sectionKey: sec.key, sectionTitle: sec.title, item: it, hay });
    }
  }
  return rows;
}

export default function TrainingPanel() {
  const [q, setQ] = useState("");
  const [sections, setSections] = useState(TRAINING);   // gösterilen içerik
  const [isCustom, setIsCustom] = useState(false);       // DB'de özel içerik var mı
  const [active, setActive] = useState(TRAINING[0]?.key || "");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);              // düzenleme kopyası
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    axios.get(`${API}/help-content`, { headers: authHeaders() })
      .then((r) => {
        if (Array.isArray(r.data?.sections) && r.data.sections.length) {
          setSections(r.data.sections);
          setIsCustom(true);
          setActive(r.data.sections[0]?.key || "");
        }
      })
      .catch(() => {});
  }, []);

  const view = editing ? draft : sections;
  const index = useMemo(() => buildIndex(view || []), [view]);
  const query = q.trim().toLocaleLowerCase("tr");
  const searchResults = useMemo(() => (query ? index.filter((r) => r.hay.includes(query)) : null), [query, index]);
  const activeSection = (view || []).find((s) => s.key === active) || (view || [])[0];

  // ---- düzenleme ----
  const startEdit = () => { setDraft(deepCopy(sections)); setEditing(true); setQ(""); };
  const cancelEdit = () => { setEditing(false); setDraft(null); };

  const save = async () => {
    setSaving(true);
    try {
      const clean = (draft || []).map((s) => ({
        key: s.key, title: s.title, icon: s.icon, intro: s.intro,
        items: (s.items || []).map((it) => ({
          title: it.title, path: it.path, what: it.what, where: it.where,
          how: Array.isArray(it.how) ? it.how : linesToArr(it._howText),
          tips: Array.isArray(it.tips) ? it.tips : linesToArr(it._tipsText),
        })),
      }));
      await axios.put(`${API}/admin/help-content`, { sections: clean }, { headers: authHeaders() });
      setSections(clean); setIsCustom(true); setEditing(false); setDraft(null);
      toast.success("Yardım içeriği kaydedildi");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kaydedilemedi");
    } finally { setSaving(false); }
  };

  const resetToDefault = async () => {
    if (!window.confirm("Özel içerik silinsin ve varsayılan yönergelere dönülsün mü?")) return;
    try {
      await axios.delete(`${API}/admin/help-content`, { headers: authHeaders() });
      setSections(TRAINING); setIsCustom(false); setActive(TRAINING[0]?.key || "");
      setEditing(false); setDraft(null);
      toast.success("Varsayılana döndürüldü");
    } catch { toast.error("İşlem başarısız"); }
  };

  // draft mutasyon yardımcıları
  const upSec = (i, patch) => setDraft((d) => d.map((s, si) => (si === i ? { ...s, ...patch } : s)));
  const upItem = (si, ii, patch) => setDraft((d) => d.map((s, x) => x !== si ? s : {
    ...s, items: s.items.map((it, y) => (y === ii ? { ...it, ...patch } : it)),
  }));
  const addItem = (si) => setDraft((d) => d.map((s, x) => x !== si ? s : {
    ...s, items: [...(s.items || []), { title: "Yeni başlık", path: "", what: "", where: "", how: [], tips: [] }],
  }));
  const delItem = (si, ii) => setDraft((d) => d.map((s, x) => x !== si ? s : { ...s, items: s.items.filter((_, y) => y !== ii) }));
  const addSection = () => setDraft((d) => [...d, { key: `bolum-${d.length + 1}`, title: "Yeni Bölüm", icon: "FileText", intro: "", items: [] }]);
  const delSection = (si) => { if (window.confirm("Bu bölüm silinsin mi?")) setDraft((d) => d.filter((_, x) => x !== si)); };

  // ---- görünüm kartı (okuma) ----
  const Card = ({ it, sectionTitle }) => (
    <div className="bg-white border rounded-xl p-4 mb-3">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-semibold text-gray-900">{it.title}</h3>
        {it.path && (
          <Link to={it.path} className="shrink-0 inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 border border-blue-100 rounded-lg px-2 py-1">
            Aç <ExternalLink size={12} />
          </Link>
        )}
      </div>
      {sectionTitle && <div className="text-[11px] uppercase tracking-wide text-gray-400 mt-0.5">{sectionTitle}</div>}
      {it.what && <p className="text-sm text-gray-700 mt-2">{it.what}</p>}
      {it.where && (
        <div className="flex items-center gap-1.5 text-xs text-gray-500 mt-2">
          <MapPin size={13} className="text-gray-400" /> <span><b>Nerede:</b> {it.where}</span>
        </div>
      )}
      {Array.isArray(it.how) && it.how.length > 0 && (
        <div className="mt-2">
          <div className="text-xs font-semibold text-gray-600 mb-1">Nasıl yapılır</div>
          <ol className="list-decimal list-inside space-y-0.5 text-sm text-gray-700">
            {it.how.map((step, i) => <li key={i}>{step}</li>)}
          </ol>
        </div>
      )}
      {Array.isArray(it.tips) && it.tips.length > 0 && (
        <div className="mt-2 space-y-1">
          {it.tips.map((tip, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-2 py-1.5">
              <Lightbulb size={13} className="text-amber-500 mt-0.5 shrink-0" /> <span>{tip}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  // ---- düzenleme kartı ----
  const EditItem = ({ si, ii, it }) => (
    <div className="bg-white border rounded-xl p-3 mb-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <input value={it.title} onChange={(e) => upItem(si, ii, { title: e.target.value })}
          placeholder="Başlık" className="flex-1 border rounded px-2 py-1.5 text-sm font-semibold" />
        <button onClick={() => delItem(si, ii)} className="text-red-500 hover:text-red-700 p-1" title="Sil"><Trash2 size={15} /></button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-2">
        <input value={it.path || ""} onChange={(e) => upItem(si, ii, { path: e.target.value })}
          placeholder="Bağlantı (ör. /admin/urunler) — opsiyonel" className="border rounded px-2 py-1.5 text-xs" />
        <input value={it.where || ""} onChange={(e) => upItem(si, ii, { where: e.target.value })}
          placeholder="Nerede bulunur" className="border rounded px-2 py-1.5 text-xs" />
      </div>
      <textarea value={it.what || ""} onChange={(e) => upItem(si, ii, { what: e.target.value })}
        placeholder="Ne işe yarar (açıklama)" rows={2} className="w-full border rounded px-2 py-1.5 text-sm mb-2" />
      <textarea
        value={it._howText != null ? it._howText : arrToLines(it.how)}
        onChange={(e) => upItem(si, ii, { _howText: e.target.value, how: linesToArr(e.target.value) })}
        placeholder="Nasıl yapılır — her satır bir adım" rows={3} className="w-full border rounded px-2 py-1.5 text-sm mb-2" />
      <textarea
        value={it._tipsText != null ? it._tipsText : arrToLines(it.tips)}
        onChange={(e) => upItem(si, ii, { _tipsText: e.target.value, tips: linesToArr(e.target.value) })}
        placeholder="İpuçları — her satır bir ipucu" rows={2} className="w-full border rounded px-2 py-1.5 text-sm" />
    </div>
  );

  return (
    <div className="space-y-5" data-testid="training-panel-page">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><GraduationCap /> Eğitim & Yardım</h1>
          <p className="text-sm text-gray-500 mt-1">
            Sistemdeki her özellik için <b>ne işe yarar</b>, <b>nerede bulunur</b> ve <b>nasıl yapılır</b>.
            {isCustom ? " (Firmanıza özel içerik)" : " (Varsayılan yönergeler — düzenleyip kendinize göre kaydedebilirsiniz)"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!editing ? (
            <>
              <button onClick={startEdit} className="inline-flex items-center gap-1.5 px-3 py-2 bg-gray-900 text-white rounded-lg text-sm hover:bg-black">
                <Pencil size={14} /> Düzenle
              </button>
              {isCustom && (
                <button onClick={resetToDefault} className="inline-flex items-center gap-1.5 px-3 py-2 border rounded-lg text-sm text-gray-600 hover:bg-gray-50" title="Varsayılana dön">
                  <RotateCcw size={14} /> Varsayılan
                </button>
              )}
            </>
          ) : (
            <>
              <button onClick={save} disabled={saving} className="inline-flex items-center gap-1.5 px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700 disabled:opacity-50">
                <Save size={14} /> {saving ? "Kaydediliyor…" : "Kaydet"}
              </button>
              <button onClick={cancelEdit} className="inline-flex items-center gap-1.5 px-3 py-2 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">
                <X size={14} /> Vazgeç
              </button>
            </>
          )}
        </div>
      </div>

      {!editing && (
        <div className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Özellik ara… (ör. iade, fatura, kupon, koleksiyon, satış hızı)"
            className="w-full border rounded-xl pl-9 pr-3 py-2.5 text-sm" />
        </div>
      )}

      {/* ---- DÜZENLEME MODU ---- */}
      {editing ? (
        <div className="space-y-6">
          {(draft || []).map((s, si) => (
            <div key={si} className="bg-gray-50 border rounded-xl p-4">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <input value={s.title} onChange={(e) => upSec(si, { title: e.target.value })}
                  placeholder="Bölüm adı" className="border rounded px-2 py-1.5 text-sm font-bold w-52" />
                <input value={s.key} onChange={(e) => upSec(si, { key: e.target.value })}
                  placeholder="anahtar" className="border rounded px-2 py-1.5 text-xs w-32 text-gray-500" />
                <select value={s.icon || "FileText"} onChange={(e) => upSec(si, { icon: e.target.value })}
                  className="border rounded px-2 py-1.5 text-xs">
                  {Object.keys(ICONS).map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
                <button onClick={() => delSection(si)} className="ml-auto text-red-500 hover:text-red-700 p-1 text-xs inline-flex items-center gap-1">
                  <Trash2 size={14} /> Bölümü sil
                </button>
              </div>
              <input value={s.intro || ""} onChange={(e) => upSec(si, { intro: e.target.value })}
                placeholder="Bölüm açıklaması" className="w-full border rounded px-2 py-1.5 text-sm mb-3" />
              {(s.items || []).map((it, ii) => <EditItem key={ii} si={si} ii={ii} it={it} />)}
              <button onClick={() => addItem(si)} className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-dashed rounded-lg text-sm text-gray-600 hover:bg-white">
                <Plus size={14} /> Madde ekle
              </button>
            </div>
          ))}
          <button onClick={addSection} className="inline-flex items-center gap-1.5 px-4 py-2 border border-dashed rounded-lg text-sm text-gray-700 hover:bg-gray-50">
            <Plus size={15} /> Yeni bölüm ekle
          </button>
        </div>
      ) : searchResults ? (
        <div>
          <div className="text-xs text-gray-500 mb-2">{searchResults.length} sonuç</div>
          {searchResults.length === 0 && (
            <div className="text-sm text-gray-400 bg-white border rounded-xl p-6 text-center">Sonuç yok. Farklı bir kelime deneyin.</div>
          )}
          {searchResults.map((r, i) => <Card key={i} it={r.item} sectionTitle={r.sectionTitle} />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
          <aside className="lg:col-span-1">
            <div className="bg-white border rounded-xl p-2 sticky top-4">
              {(view || []).map((s) => {
                const Icon = ICONS[s.icon] || FileText;
                const isActive = s.key === active;
                return (
                  <button key={s.key} onClick={() => setActive(s.key)}
                    className={`w-full flex items-center gap-2 text-left px-3 py-2 rounded-lg text-sm mb-0.5 ${isActive ? "bg-blue-50 text-blue-700 font-semibold" : "text-gray-700 hover:bg-gray-50"}`}>
                    <Icon size={16} className={isActive ? "text-blue-600" : "text-gray-400"} />
                    <span className="flex-1">{s.title}</span>
                    <span className="text-[10px] text-gray-400">{(s.items || []).length}</span>
                    {isActive && <ChevronRight size={14} />}
                  </button>
                );
              })}
            </div>
          </aside>
          <section className="lg:col-span-3">
            {activeSection && (
              <>
                <div className="bg-gradient-to-r from-blue-50 to-white border border-blue-100 rounded-xl p-4 mb-4">
                  <h2 className="font-bold text-gray-900">{activeSection.title}</h2>
                  {activeSection.intro && <p className="text-sm text-gray-600 mt-0.5">{activeSection.intro}</p>}
                </div>
                {(activeSection.items || []).map((it, i) => <Card key={i} it={it} />)}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
