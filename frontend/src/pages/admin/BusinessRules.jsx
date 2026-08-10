/**
 * BusinessRules.jsx — İşletme Kuralları (SaaS ayar merkezi)
 * ---------------------------------------------------------------------------
 * Kodda gömülü tüm işletme kuralları (iptal süreleri, ücretler, eşikler, çalışma
 * saatleri…) buradan yönetilir. Her ayar tipine göre kontrol + hazır ALTERNATİF
 * seçenekleri (chip) gösterir. Backend: GET/PUT /api/admin/business-rules.
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, SlidersHorizontal, RotateCcw, Search, ChevronRight } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const WD = { 1: "Pzt", 2: "Sal", 3: "Çar", 4: "Per", 5: "Cum", 6: "Cmt", 7: "Paz" };

export default function BusinessRules() {
  const [groups, setGroups] = useState([]);
  const [vals, setVals] = useState({});
  const [dirty, setDirty] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [active, setActive] = useState("");
  const [q, setQ] = useState("");
  const auth = { headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } };

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/business-rules`, auth);
      const gs = r.data?.groups || [];
      setGroups(gs);
      if (gs.length) setActive((a) => a || gs[0].group);
      const v = {};
      gs.forEach((g) => g.rules.forEach((rl) => { v[rl.key] = rl.value; }));
      setVals(v);
      setDirty({});
    } catch {
      toast.error("Kurallar yüklenemedi");
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  // Arama: tüm gruplarda label/help/key içinde eşleşen kurallar
  const query = q.trim().toLocaleLowerCase("tr");
  const searchHits = useMemo(() => {
    if (!query) return null;
    const hits = [];
    groups.forEach((g) => g.rules.forEach((rl) => {
      const hay = `${g.group} ${rl.label} ${rl.help || ""} ${rl.key}`.toLocaleLowerCase("tr");
      if (hay.includes(query)) hits.push({ group: g.group, rule: rl });
    }));
    return hits;
  }, [query, groups]);
  const activeGroup = groups.find((g) => g.group === active) || groups[0];

  const set = (key, value) => {
    setVals((p) => ({ ...p, [key]: value }));
    setDirty((p) => ({ ...p, [key]: true }));
  };

  const save = async () => {
    const patch = {};
    Object.keys(dirty).forEach((k) => { patch[k] = vals[k]; });
    if (!Object.keys(patch).length) { toast.info("Değişiklik yok"); return; }
    setSaving(true);
    try {
      await axios.put(`${API}/admin/business-rules`, { values: patch }, auth);
      toast.success(`${Object.keys(patch).length} ayar kaydedildi`);
      setDirty({});
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally { setSaving(false); }
  };

  const control = (rl) => {
    const v = vals[rl.key];
    const opts = rl.options || [];
    if (rl.type === "toggle") {
      return (
        <button
          onClick={() => set(rl.key, !v)}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition ${v ? "bg-black" : "bg-gray-300"}`}
          role="switch" aria-checked={!!v}
        >
          <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${v ? "translate-x-6" : "translate-x-1"}`} />
        </button>
      );
    }
    if (rl.type === "select") {
      return (
        <select value={String(v)} onChange={(e) => set(rl.key, e.target.value)}
          className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm bg-white">
          {opts.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
        </select>
      );
    }
    if (rl.type === "time") {
      return (
        <div className="flex items-center gap-2 flex-wrap">
          <input type="time" value={v || ""} onChange={(e) => set(rl.key, e.target.value)}
            className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm bg-white" />
          <div className="flex gap-1">
            {opts.map((o) => (
              <button key={o} onClick={() => set(rl.key, o)}
                className={`text-xs px-2 py-1 rounded-full border ${String(v) === String(o) ? "bg-black text-white border-black" : "border-gray-200 text-gray-500 hover:bg-gray-50"}`}>{o}</button>
            ))}
          </div>
        </div>
      );
    }
    if (rl.type === "multiselect") {
      const arr = Array.isArray(v) ? v : [];
      return (
        <div className="flex gap-1 flex-wrap">
          {opts.map((o) => {
            const on = arr.includes(o);
            return (
              <button key={o} onClick={() => set(rl.key, on ? arr.filter((x) => x !== o) : [...arr, o].sort())}
                className={`text-xs px-2.5 py-1 rounded-full border ${on ? "bg-black text-white border-black" : "border-gray-200 text-gray-500 hover:bg-gray-50"}`}>
                {WD[o] || o}
              </button>
            );
          })}
        </div>
      );
    }
    // number / text
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type={rl.type === "number" ? "number" : "text"}
          value={v ?? ""}
          onChange={(e) => set(rl.key, rl.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)}
          className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm bg-white w-28"
        />
        {rl.unit && <span className="text-sm text-gray-400">{rl.unit}</span>}
        <div className="flex gap-1 flex-wrap">
          {opts.map((o) => (
            <button key={String(o)} onClick={() => set(rl.key, o)}
              className={`text-xs px-2 py-1 rounded-full border ${String(v) === String(o) ? "bg-black text-white border-black" : "border-gray-200 text-gray-500 hover:bg-gray-50"}`}>
              {String(o)}{rl.unit ? ` ${rl.unit}` : ""}
            </button>
          ))}
        </div>
      </div>
    );
  };

  const dirtyCount = Object.keys(dirty).length;

  return (
    <div data-testid="business-rules-page">
      <div className="flex items-center justify-between mb-6 gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <SlidersHorizontal size={20} /> Mağaza Ayarları
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Tüm işletme kuralları ve mağaza ayarları burada. Soldan kategori seç ya da ara; değeri değiştir veya hazır
            alternatiflerden seç; kaydet, anında geçerli olur.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="flex items-center gap-1 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
            <RotateCcw size={14} /> Yenile
          </button>
          <button onClick={save} disabled={saving || !dirtyCount}
            className="flex items-center gap-1 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40">
            <Save size={14} /> {saving ? "Kaydediliyor…" : `Kaydet${dirtyCount ? ` (${dirtyCount})` : ""}`}
          </button>
        </div>
      </div>

      {/* Arama */}
      {!loading && (
        <div className="relative mb-4">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Ayar ara… (ör. kargo, iade, beden, duyuru, puan)"
            className="w-full border rounded-xl pl-9 pr-3 py-2.5 text-sm" />
        </div>
      )}

      {loading ? (
        <div className="text-center py-16 text-gray-400 text-sm">Yükleniyor…</div>
      ) : searchHits ? (
        <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b bg-gray-50/60 font-semibold text-sm">{searchHits.length} sonuç</div>
          <div className="divide-y">
            {searchHits.map(({ group, rule: rl }) => (
              <div key={rl.key} className="px-5 py-4 flex flex-col md:flex-row md:items-center gap-3 md:gap-6">
                <div className="md:w-1/2">
                  <div className="text-[11px] uppercase tracking-wide text-gray-400">{group}</div>
                  <div className="text-sm font-medium flex items-center gap-2">
                    {rl.label}
                    {dirty[rl.key] && <span className="text-[10px] text-amber-600 font-semibold">• değişti</span>}
                  </div>
                  {rl.help && <div className="text-xs text-gray-500 mt-0.5">{rl.help}</div>}
                </div>
                <div className="md:w-1/2">{control(rl)}</div>
              </div>
            ))}
            {searchHits.length === 0 && <div className="px-5 py-8 text-center text-gray-400 text-sm">Sonuç yok.</div>}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
          {/* Kategori menüsü */}
          <aside className="lg:col-span-1">
            <div className="bg-white border rounded-xl p-2 sticky top-4">
              {groups.map((g) => {
                const isActive = g.group === active;
                const dc = g.rules.filter((rl) => dirty[rl.key]).length;
                return (
                  <button key={g.group} onClick={() => setActive(g.group)}
                    className={`w-full flex items-center gap-2 text-left px-3 py-2 rounded-lg text-sm mb-0.5 ${isActive ? "bg-gray-900 text-white font-semibold" : "text-gray-700 hover:bg-gray-50"}`}>
                    <span className="flex-1">{g.group}</span>
                    {dc > 0 && <span className="text-[10px] px-1.5 rounded-full bg-amber-400 text-black">{dc}</span>}
                    <span className={`text-[10px] ${isActive ? "text-gray-300" : "text-gray-400"}`}>{g.rules.length}</span>
                    {isActive && <ChevronRight size={14} />}
                  </button>
                );
              })}
            </div>
          </aside>

          {/* Aktif kategori kuralları */}
          <section className="lg:col-span-3">
            {activeGroup && (
              <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
                <div className="px-5 py-3 border-b bg-gray-50/60 font-semibold text-sm">{activeGroup.group}</div>
                <div className="divide-y">
                  {activeGroup.rules.map((rl) => (
                    <div key={rl.key} className="px-5 py-4 flex flex-col md:flex-row md:items-center gap-3 md:gap-6">
                      <div className="md:w-1/2">
                        <div className="text-sm font-medium flex items-center gap-2">
                          {rl.label}
                          {dirty[rl.key] && <span className="text-[10px] text-amber-600 font-semibold">• değişti</span>}
                        </div>
                        {rl.help && <div className="text-xs text-gray-500 mt-0.5">{rl.help}</div>}
                      </div>
                      <div className="md:w-1/2">{control(rl)}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
