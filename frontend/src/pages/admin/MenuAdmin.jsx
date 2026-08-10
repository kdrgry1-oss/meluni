import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Plus, Trash2, ChevronUp, ChevronDown, Save, RotateCcw, CornerDownRight,
} from "lucide-react";
import { DEFAULT_MENU_TABS } from "../../lib/headerMenu";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Basit dizi taşıma (dnd yerine oklarla — menü düzenleme nadir yapılan iş, oklar hem
// masaüstünde hem mobilde sorunsuz)
const move = (arr, from, to) => {
  if (to < 0 || to >= arr.length) return arr;
  const a = [...arr];
  const [x] = a.splice(from, 1);
  a.splice(to, 0, x);
  return a;
};

const uid = () => Math.random().toString(36).slice(2, 8);

const STYLE_OPTIONS = [
  { value: "normal", label: "Normal" },
  { value: "accent", label: "Vurgulu ◆ (Yeni Koleksiyon stili)" },
  { value: "sale", label: "Kırmızı (SALE stili)" },
];

export default function MenuAdmin() {
  const [tabs, setTabs] = useState([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await axios.get(`${API}/page-blocks/header-menu`);
        const t = Array.isArray(res.data?.tabs) && res.data.tabs.length ? res.data.tabs : DEFAULT_MENU_TABS;
        setTabs(JSON.parse(JSON.stringify(t)));
      } catch (e) {
        setTabs(JSON.parse(JSON.stringify(DEFAULT_MENU_TABS)));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const update = (fn) => {
    setTabs((prev) => {
      const next = JSON.parse(JSON.stringify(prev));
      fn(next);
      return next;
    });
    setDirty(true);
  };

  const selected = tabs[selectedIdx];

  const handleSave = async () => {
    for (const t of tabs) {
      if (!String(t.label || "").trim()) { toast.error("Etiketi boş sekme var"); return; }
    }
    setSaving(true);
    try {
      const token = localStorage.getItem("token");
      await axios.put(`${API}/page-blocks/header-menu`, { tabs }, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setDirty(false);
      toast.success("Menü kaydedildi — site üst menüsü güncellendi");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  const resetDefaults = () => {
    if (!window.confirm("Menü, sitenin varsayılan yapısına (Yeni Koleksiyon / Giyim / Aksesuar / Sale) sıfırlansın mı? Kaydetmeden yürürlüğe girmez.")) return;
    setTabs(JSON.parse(JSON.stringify(DEFAULT_MENU_TABS)));
    setSelectedIdx(0);
    setDirty(true);
  };

  if (loading) return <div className="p-8 text-center">Yükleniyor...</div>;

  return (
    <div data-testid="menu-admin" className="max-w-6xl">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">Menü Yönetimi</h1>
          <p className="text-sm text-gray-500 mt-1">Site üst menüsü — sekmeler, mega menü kolonları ve linkler. Mobil menü ve arama ekranı da buradan beslenir.</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={resetDefaults} className="flex items-center gap-2 border px-3 py-2 rounded text-sm hover:bg-gray-50">
            <RotateCcw size={15} /> Varsayılana Sıfırla
          </button>
          <button onClick={handleSave} disabled={saving || !dirty}
                  className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800 disabled:opacity-40">
            <Save size={16} /> {saving ? "Kaydediliyor..." : "Kaydet & Yayınla"}
          </button>
        </div>
      </div>

      {/* Önizleme şeridi */}
      <div className="bg-white border rounded-lg px-6 py-4 mb-6 overflow-x-auto">
        <div className="flex items-center gap-6 whitespace-nowrap">
          {tabs.filter((t) => t.active !== false).map((t, i) => (
            <span key={i} className={`text-xs uppercase tracking-[0.2em] flex items-center gap-1.5 ${t.style === "sale" ? "text-red-700" : ""} ${t.style === "accent" ? "font-medium" : "font-normal"}`}>
              {t.style === "accent" && <span className="inline-block w-[5px] h-[5px] rotate-45 bg-black/60" />}
              {t.label || "—"}
              {t.type === "mega" && <span className="text-[9px] text-gray-400 normal-case tracking-normal">▾</span>}
            </span>
          ))}
          {tabs.some((t) => t.active === false) && (
            <span className="text-[10px] text-gray-400">(+{tabs.filter((t) => t.active === false).length} gizli)</span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-6 items-start">
        {/* Sol: sekme listesi */}
        <div className="bg-white border rounded-lg p-3 space-y-2">
          <div className="text-xs font-semibold text-gray-500 uppercase px-1 mb-1">Sekmeler</div>
          {tabs.map((t, i) => (
            <div key={t.id || i}
                 onClick={() => setSelectedIdx(i)}
                 className={`flex items-center gap-2 p-2.5 rounded border cursor-pointer ${i === selectedIdx ? "border-black bg-gray-50" : "border-gray-200 hover:border-gray-400"} ${t.active === false ? "opacity-45" : ""}`}>
              <div className="flex flex-col gap-0.5">
                <button onClick={(e) => { e.stopPropagation(); update((n) => { const m = move(n, i, i - 1); n.length = 0; n.push(...m); }); setSelectedIdx(Math.max(0, i - 1)); }}
                        disabled={i === 0} className="text-gray-400 hover:text-black disabled:opacity-20"><ChevronUp size={13} /></button>
                <button onClick={(e) => { e.stopPropagation(); update((n) => { const m = move(n, i, i + 1); n.length = 0; n.push(...m); }); setSelectedIdx(Math.min(tabs.length - 1, i + 1)); }}
                        disabled={i === tabs.length - 1} className="text-gray-400 hover:text-black disabled:opacity-20"><ChevronDown size={13} /></button>
              </div>
              <div className="flex-1 min-w-0">
                <div className={`text-sm truncate ${t.style === "sale" ? "text-red-700" : ""}`}>{t.label || "—"}</div>
                <div className="text-[10px] text-gray-400">{t.type === "mega" ? `Mega menü · ${(t.columns || []).reduce((s, c) => s + (c.items || []).length, 0)} link` : "Düz link"}{t.active === false ? " · gizli" : ""}</div>
              </div>
              <button onClick={(e) => {
                        e.stopPropagation();
                        if (!window.confirm(`"${t.label}" sekmesi silinsin mi?`)) return;
                        update((n) => n.splice(i, 1));
                        setSelectedIdx((s) => Math.max(0, Math.min(s > i ? s - 1 : s, tabs.length - 2)));
                      }}
                      className="text-gray-300 hover:text-red-600"><Trash2 size={14} /></button>
            </div>
          ))}
          <button onClick={() => { update((n) => n.push({ id: `tab-${uid()}`, label: "YENİ SEKME", type: "link", link: "/", style: "normal", active: true })); setSelectedIdx(tabs.length); }}
                  className="w-full flex items-center justify-center gap-2 border-2 border-dashed rounded p-2.5 text-sm text-gray-500 hover:border-black hover:text-black">
            <Plus size={15} /> Sekme Ekle
          </button>
        </div>

        {/* Sağ: seçili sekme düzenleme */}
        {selected ? (
          <div className="bg-white border rounded-lg p-5 space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Etiket</label>
                <input value={selected.label} onChange={(e) => update((n) => { n[selectedIdx].label = e.target.value; })}
                       className="w-full border px-3 py-2 rounded" placeholder="GİYİM" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Sekme Linki</label>
                <input value={selected.link || ""} onChange={(e) => update((n) => { n[selectedIdx].link = e.target.value; })}
                       className="w-full border px-3 py-2 rounded" placeholder="/giyim" />
                <p className="text-[11px] text-gray-400 mt-1">Sekmeye tıklanınca gidilen sayfa. Mega menüde açılır panelin yedek linki de budur.</p>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Tip</label>
                <select value={selected.type}
                        onChange={(e) => update((n) => {
                          n[selectedIdx].type = e.target.value;
                          if (e.target.value === "mega" && !(n[selectedIdx].columns || []).length) {
                            n[selectedIdx].columns = [{ title: n[selectedIdx].label || "KOLON", link: n[selectedIdx].link || "/", items: [] }];
                          }
                        })}
                        className="w-full border px-3 py-2 rounded bg-white">
                  <option value="link">Düz link</option>
                  <option value="mega">Mega menü (açılır panel)</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Stil</label>
                <select value={selected.style || "normal"} onChange={(e) => update((n) => { n[selectedIdx].style = e.target.value; })}
                        className="w-full border px-3 py-2 rounded bg-white">
                  {STYLE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={selected.active !== false}
                     onChange={(e) => update((n) => { n[selectedIdx].active = e.target.checked; })} />
              Menüde göster
            </label>

            {selected.type === "mega" && (
              <div className="pt-2 border-t">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold">Mega Menü Kolonları</h3>
                  <button onClick={() => update((n) => { (n[selectedIdx].columns = n[selectedIdx].columns || []).push({ title: "YENİ KOLON", link: "/", items: [] }); })}
                          className="flex items-center gap-1.5 text-xs border px-2.5 py-1.5 rounded hover:bg-gray-50">
                    <Plus size={13} /> Kolon Ekle
                  </button>
                </div>
                <p className="text-[11px] text-gray-400 mb-3">Tek kolon = Aksesuar tarzı geniş liste · Birden çok kolon = Giyim tarzı başlıklı gruplar. Sağdaki 3 ürün kartı otomatiktir: üzerine gelinen linkin kategorisinden çok satanlar gösterilir.</p>
                <div className="space-y-4">
                  {(selected.columns || []).map((col, ci) => (
                    <div key={ci} className="border rounded-lg p-3 bg-gray-50/60">
                      <div className="flex items-center gap-2 mb-2">
                        <div className="flex flex-col gap-0.5">
                          <button onClick={() => update((n) => { n[selectedIdx].columns = move(n[selectedIdx].columns, ci, ci - 1); })}
                                  disabled={ci === 0} className="text-gray-400 hover:text-black disabled:opacity-20"><ChevronUp size={13} /></button>
                          <button onClick={() => update((n) => { n[selectedIdx].columns = move(n[selectedIdx].columns, ci, ci + 1); })}
                                  disabled={ci === (selected.columns || []).length - 1} className="text-gray-400 hover:text-black disabled:opacity-20"><ChevronDown size={13} /></button>
                        </div>
                        <input value={col.title} onChange={(e) => update((n) => { n[selectedIdx].columns[ci].title = e.target.value; })}
                               className="flex-1 border px-2.5 py-1.5 rounded text-sm font-medium" placeholder="KOLON BAŞLIĞI (örn. ÜST GİYİM)" />
                        <input value={col.link || ""} onChange={(e) => update((n) => { n[selectedIdx].columns[ci].link = e.target.value; })}
                               className="w-44 border px-2.5 py-1.5 rounded text-xs" placeholder="Tümünü Gör linki" title="Başlık ve 'Tümünü Gör' bu linke gider" />
                        <button onClick={() => { if (window.confirm("Kolon silinsin mi?")) update((n) => { n[selectedIdx].columns.splice(ci, 1); }); }}
                                className="text-gray-300 hover:text-red-600"><Trash2 size={15} /></button>
                      </div>
                      <div className="space-y-1.5 pl-6">
                        {(col.items || []).map((item, ii) => (
                          <div key={ii} className="flex items-center gap-2">
                            <CornerDownRight size={12} className="text-gray-300 shrink-0" />
                            <div className="flex flex-col gap-0">
                              <button onClick={() => update((n) => { n[selectedIdx].columns[ci].items = move(n[selectedIdx].columns[ci].items, ii, ii - 1); })}
                                      disabled={ii === 0} className="text-gray-400 hover:text-black disabled:opacity-20 leading-none"><ChevronUp size={12} /></button>
                              <button onClick={() => update((n) => { n[selectedIdx].columns[ci].items = move(n[selectedIdx].columns[ci].items, ii, ii + 1); })}
                                      disabled={ii === (col.items || []).length - 1} className="text-gray-400 hover:text-black disabled:opacity-20 leading-none"><ChevronDown size={12} /></button>
                            </div>
                            <input value={item.name} onChange={(e) => update((n) => { n[selectedIdx].columns[ci].items[ii].name = e.target.value; })}
                                   className="w-40 border px-2 py-1 rounded text-sm" placeholder="Elbise" />
                            <input value={item.link || ""} onChange={(e) => update((n) => { n[selectedIdx].columns[ci].items[ii].link = e.target.value; })}
                                   className="flex-1 border px-2 py-1 rounded text-xs font-mono" placeholder="/elbise" />
                            <button onClick={() => update((n) => { n[selectedIdx].columns[ci].items.splice(ii, 1); })}
                                    className="text-gray-300 hover:text-red-600"><Trash2 size={13} /></button>
                          </div>
                        ))}
                        <button onClick={() => update((n) => { (n[selectedIdx].columns[ci].items = n[selectedIdx].columns[ci].items || []).push({ name: "", link: "/" }); })}
                                className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-black mt-1">
                          <Plus size={12} /> Link Ekle
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-sm text-gray-400 p-8 text-center border rounded-lg bg-white">Soldan bir sekme seçin</div>
        )}
      </div>

      {dirty && (
        <div className="mt-4 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 inline-block">
          Kaydedilmemiş değişiklikler var — yayına almak için "Kaydet &amp; Yayınla"ya basın.
        </div>
      )}
    </div>
  );
}
