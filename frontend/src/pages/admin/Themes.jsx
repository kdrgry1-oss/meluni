import React, { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  Palette, Eye, CheckCircle2, Trash2, RotateCcw, Plus, GripVertical, Image as ImageIcon,
  Upload, ExternalLink, ArrowLeft, Save,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const BLOCK_TYPES = [
  { value: "announcement_bar", label: "Duyuru Bandı" },
  { value: "hero_fullscreen", label: "Hero (Full-screen)" },
  { value: "editorial_card", label: "Editöryel Kart" },
  { value: "product_scroller", label: "Ürün Şeridi" },
  { value: "lookbook_mosaic", label: "Lookbook (Mozaik)" },
  { value: "newsletter", label: "Bülten" },
  { value: "text_section", label: "Metin Bölümü" },
];

export default function Themes() {
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(null); // theme object being edited

  const fetchList = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/themes`);
      setList(r.data.items || []);
    } catch (e) {
      toast.error("Temalar yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchList(); }, []);

  const activate = async (id) => {
    try {
      await axios.post(`${API}/admin/themes/${id}/activate`);
      toast.success("Tema aktive edildi");
      fetchList();
    } catch { toast.error("Aktivasyon başarısız"); }
  };

  /** Aktif temayı (yoksa ilkini) kopyalayarak yeni bir tema açar. */
  const yeniTema = async () => {
    const kaynak = list.find(t => t.is_active) || list[0];
    const ad = window.prompt("Yeni temanın adı", kaynak ? `${kaynak.name} — kopya` : "Yeni Tema");
    if (!ad) return;
    const slug = ad.toLocaleLowerCase("tr")
      .replace(/ı/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g")
      .replace(/ü/g, "u").replace(/ö/g, "o").replace(/ç/g, "c")
      .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
    try {
      const r = await axios.post(`${API}/admin/themes`, {
        name: ad,
        slug: slug || `tema-${Date.now()}`,
        description: kaynak ? `${kaynak.name} temasından türetildi.` : "",
        preview_image: kaynak?.preview_image || "",
        // Bloklar YENİ kimlikle kopyalanır; kaynak temanın blokları etkilenmez.
        blocks: (kaynak?.blocks || []).map(({ id, ...b }) => ({ ...b })),
        menu: kaynak?.menu || [],
        settings: JSON.parse(JSON.stringify(kaynak?.settings || {})),
      });
      toast.success("Tema oluşturuldu — düzenleyip aktive edebilirsiniz");
      await fetchList();
      setEditing(r.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Tema oluşturulamadı");
    }
  };

  const remove = async (t) => {
    if (t.is_default) { toast.error("Varsayılan tema silinemez"); return; }
    if (!window.confirm(`"${t.name}" temasını silmek istediğinize emin misiniz?`)) return;
    try {
      await axios.delete(`${API}/admin/themes/${t.id}`);
      toast.success("Tema silindi");
      fetchList();
    } catch { toast.error("Silinemedi"); }
  };

  const resetMiumiu = async (id) => {
    if (!window.confirm("Miu Miu temasını fabrika ayarlarına döndürmek istediğinize emin misiniz?")) return;
    try {
      const r = await axios.post(`${API}/admin/themes/${id}/reset`);
      toast.success("Tema sıfırlandı");
      setEditing(r.data);
      fetchList();
    } catch { toast.error("Sıfırlanamadı"); }
  };

  if (editing) {
    return <ThemeEditor theme={editing} onClose={() => { setEditing(null); fetchList(); }} onReset={resetMiumiu} />;
  }

  return (
    <div className="space-y-6" data-testid="themes-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Palette size={24} /> Tema Yönetimi</h1>
          <p className="text-sm text-zinc-500 mt-1">Müşterilere açık ön yüz (storefront) tasarımları. Aktif tema, ziyaretçilerin gördüğü tasarımdır.</p>
        </div>
        {/* Sezonluk tema üretmenin pratik yolu KOPYALAMAK: mevcut temanın
            blokları ve renkleri gelir, üzerinde oynanır, hazır olunca aktive
            edilir. Aktif tema bu sırada bozulmaz. */}
        <button onClick={yeniTema} disabled={loading}
          className="px-4 py-2 text-xs font-semibold bg-zinc-900 text-white rounded hover:bg-black disabled:opacity-50 flex items-center gap-1.5"
          data-testid="btn-new-theme">
          <Plus size={13} /> Yeni Tema
        </button>
      </div>

      {loading ? <div className="text-zinc-500">Yükleniyor…</div> : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {list.map(t => (
            <ThemeCard key={t.id} theme={t} onEdit={() => setEditing(t)} onActivate={() => activate(t.id)} onDelete={() => remove(t)} />
          ))}
        </div>
      )}
    </div>
  );
}

function ThemeCard({ theme, onEdit, onActivate, onDelete }) {
  return (
    <div className="bg-white border border-zinc-200 rounded-md overflow-hidden hover:shadow-sm transition" data-testid={`theme-card-${theme.slug}`}>
      <div className="aspect-[16/10] bg-zinc-100 relative overflow-hidden">
        {theme.preview_image ? (
          <img src={theme.preview_image} alt={theme.name} className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-zinc-400"><ImageIcon size={32} /></div>
        )}
        {theme.is_active && (
          <span className="absolute top-3 left-3 inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-emerald-600 text-white text-[11px] font-semibold uppercase tracking-wider">
            <CheckCircle2 size={12} /> Aktif
          </span>
        )}
      </div>
      <div className="p-4">
        <h3 className="font-semibold text-zinc-900">{theme.name}</h3>
        <p className="text-xs text-zinc-500 mt-1 line-clamp-2 min-h-[32px]">{theme.description || theme.slug}</p>
        <div className="text-[11px] text-zinc-400 mt-2 flex items-center gap-3">
          <span>{(theme.blocks || []).length} blok</span>
          <span>{(theme.menu || []).length} menü</span>
        </div>
        <div className="grid grid-cols-2 gap-2 mt-4">
          <button onClick={onEdit} data-testid={`btn-edit-${theme.slug}`} className="px-3 py-2 text-xs font-medium border border-zinc-900 hover:bg-zinc-900 hover:text-white transition rounded">Düzenle</button>
          <a href={`/tema/${theme.slug}`} target="_blank" rel="noreferrer" data-testid={`btn-preview-${theme.slug}`} className="px-3 py-2 text-xs font-medium border border-zinc-200 hover:bg-zinc-100 transition rounded flex items-center justify-center gap-1.5"><Eye size={13}/> Önizle</a>
          {!theme.is_active && (
            <button onClick={onActivate} data-testid={`btn-activate-${theme.slug}`} className="col-span-2 px-3 py-2 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded">Aktive Et</button>
          )}
          {!theme.is_default && (
            <button onClick={onDelete} data-testid={`btn-delete-${theme.slug}`} className="col-span-2 px-3 py-2 text-xs font-medium border border-rose-200 text-rose-600 hover:bg-rose-50 rounded flex items-center justify-center gap-1.5"><Trash2 size={13}/> Sil</button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Temanın renk ve yazı tipi belirteçleri — vitrinin görünümünü belirler. */
const BELIRTEC_ALANLARI = [
  ["ink", "Metin (mürekkep)", "renk"],
  ["paper", "Zemin", "renk"],
  ["line", "Ayraç / çerçeve", "renk"],
  ["muted", "İkincil metin", "renk"],
  ["accent", "Vurgu", "renk"],
  ["display", "Başlık yazı tipi", "metin"],
  ["sans", "Gövde yazı tipi", "metin"],
  ["wordmark", "Logo yazı tipi", "metin"],
  ["radius", "Köşe yarıçapı", "metin"],
];

function TemaBelirtecleri({ tokens, fontUrl, onChange }) {
  const yaz = (ad, deger) => onChange({ ...tokens, [ad]: deger }, fontUrl);
  return (
    <div className="bg-white border border-zinc-200 rounded p-4">
      <h2 className="text-sm font-semibold text-zinc-700 mb-1">Renk & Tipografi</h2>
      <p className="text-xs text-zinc-500 mb-3">
        Vitrinin görünümü buradan değişir. Boş bırakılan alan varsayılanı kullanır.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {BELIRTEC_ALANLARI.map(([ad, etiket, tip]) => (
          <div key={ad}>
            <label className="text-xs font-semibold text-zinc-700">{etiket}</label>
            <div className="mt-1 flex items-center gap-2">
              {tip === "renk" && (
                <input type="color" value={tokens[ad] || "#000000"}
                  onChange={(e) => yaz(ad, e.target.value)}
                  className="h-9 w-10 border border-zinc-300 rounded cursor-pointer shrink-0" />
              )}
              <input value={tokens[ad] || ""} onChange={(e) => yaz(ad, e.target.value)}
                placeholder={tip === "renk" ? "#100f0c" : "'Jost', system-ui, sans-serif"}
                className="w-full px-3 py-2 border border-zinc-300 rounded text-sm font-mono" />
            </div>
          </div>
        ))}
        <div className="md:col-span-3">
          <label className="text-xs font-semibold text-zinc-700">Web font adresi (Google Fonts)</label>
          <input value={fontUrl} onChange={(e) => onChange(tokens, e.target.value)}
            placeholder="https://fonts.googleapis.com/css2?family=Jost:wght@300;400;500&display=swap"
            className="mt-1 w-full px-3 py-2 border border-zinc-300 rounded text-sm font-mono" />
          <p className="text-[11px] text-zinc-400 mt-1">
            Yazı tipi alanlarında bir web fontu kullandıysanız adresini buraya girin, yoksa yüklenmez.
          </p>
        </div>
      </div>
    </div>
  );
}

function ThemeEditor({ theme: initial, onClose, onReset }) {
  const [theme, setTheme] = useState(initial);
  const [saving, setSaving] = useState(false);

  const setBlocks = (blocks) => setTheme(t => ({ ...t, blocks }));

  const updateBlock = (id, patch) => {
    setBlocks(theme.blocks.map(b => b.id === id ? { ...b, ...patch } : b));
  };

  const moveBlock = (idx, dir) => {
    const next = [...theme.blocks];
    const target = idx + dir;
    if (target < 0 || target >= next.length) return;
    [next[idx], next[target]] = [next[target], next[idx]];
    next.forEach((b, i) => { b.order = i; });
    setBlocks(next);
  };

  const addBlock = () => {
    const newBlock = {
      id: `tmp-${Date.now()}`,
      type: "editorial_card",
      title: "Yeni Blok",
      subtitle: "",
      image: "",
      mobile_image: "",
      link_url: "",
      link_label: "Shop",
      order: theme.blocks.length,
      is_active: true,
      settings: { text_color: "#ffffff", align: "center", overlay: 0.3 },
    };
    setBlocks([...theme.blocks, newBlock]);
  };

  const removeBlock = (id) => {
    if (!window.confirm("Bu bloğu silmek istediğinize emin misiniz?")) return;
    setBlocks(theme.blocks.filter(b => b.id !== id));
  };

  const uploadImage = async (blockId, field, file) => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await axios.post(`${API}/upload`, fd, { headers: { "Content-Type": "multipart/form-data" } });
      const url = r.data.url || r.data.file_url || r.data.path;
      if (url) {
        updateBlock(blockId, { [field]: url.startsWith("http") ? url : `${process.env.REACT_APP_BACKEND_URL}${url}` });
        toast.success("Görsel yüklendi");
      } else { toast.error("Upload yanıtı tanınamadı"); }
    } catch { toast.error("Görsel yüklenemedi"); }
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        name: theme.name,
        slug: theme.slug,
        description: theme.description,
        preview_image: theme.preview_image,
        blocks: theme.blocks,
        menu: theme.menu,
        settings: theme.settings,
      };
      const r = await axios.put(`${API}/admin/themes/${theme.id}`, payload);
      toast.success("Tema kaydedildi");
      setTheme(r.data);
    } catch { toast.error("Kayıt başarısız"); }
    finally { setSaving(false); }
  };

  return (
    <div className="space-y-5" data-testid="theme-editor">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={onClose} className="p-2 hover:bg-zinc-100 rounded" data-testid="btn-back-themes"><ArrowLeft size={18}/></button>
          <div>
            <h1 className="text-xl font-bold">{theme.name}</h1>
            <p className="text-xs text-zinc-500">slug: <code className="px-1.5 py-0.5 bg-zinc-100 rounded">{theme.slug}</code></p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <a href={`/tema/${theme.slug}`} target="_blank" rel="noreferrer" className="px-3 py-2 text-xs font-medium border border-zinc-200 rounded hover:bg-zinc-50 flex items-center gap-1.5"><ExternalLink size={13}/> Önizle</a>
          {theme.slug === "miumiu" && (
            <button onClick={() => onReset(theme.id)} className="px-3 py-2 text-xs font-medium border border-amber-200 text-amber-700 rounded hover:bg-amber-50 flex items-center gap-1.5" data-testid="btn-reset-miumiu"><RotateCcw size={13}/> Fabrika Ayarları</button>
          )}
          <button onClick={save} disabled={saving} className="px-4 py-2 text-xs font-semibold bg-zinc-900 text-white rounded hover:bg-black disabled:opacity-50 flex items-center gap-1.5" data-testid="btn-save-theme"><Save size={13}/> {saving ? "Kaydediliyor…" : "Kaydet"}</button>
        </div>
      </div>

      {/* Meta */}
      <div className="bg-white border border-zinc-200 rounded p-4 grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <label className="text-xs font-semibold text-zinc-700">Tema Adı</label>
          <input value={theme.name} onChange={e => setTheme({ ...theme, name: e.target.value })} className="mt-1 w-full px-3 py-2 border border-zinc-300 rounded text-sm" />
        </div>
        <div>
          <label className="text-xs font-semibold text-zinc-700">Slug (URL)</label>
          <input value={theme.slug} disabled={theme.is_default} onChange={e => setTheme({ ...theme, slug: e.target.value })} className="mt-1 w-full px-3 py-2 border border-zinc-300 rounded text-sm disabled:bg-zinc-50" />
        </div>
        <div>
          <label className="text-xs font-semibold text-zinc-700">Önizleme Görseli (URL)</label>
          <input value={theme.preview_image || ""} onChange={e => setTheme({ ...theme, preview_image: e.target.value })} className="mt-1 w-full px-3 py-2 border border-zinc-300 rounded text-sm" />
        </div>
        <div className="md:col-span-3">
          <label className="text-xs font-semibold text-zinc-700">Açıklama</label>
          <textarea value={theme.description || ""} onChange={e => setTheme({ ...theme, description: e.target.value })} rows={2} className="mt-1 w-full px-3 py-2 border border-zinc-300 rounded text-sm" />
        </div>
      </div>

      {/* Renk & tipografi — TEMANIN ASIL KİMLİĞİ.
          Bunlar mağaza kökünde CSS değişkenine çevrilir; blok içeriği değil,
          vitrinin görünümü buradan değişir. Boş bırakılan alan varsayılana düşer. */}
      <TemaBelirtecleri
        tokens={theme.settings?.tokens || {}}
        fontUrl={theme.settings?.font_url || ""}
        onChange={(tokens, font_url) =>
          setTheme({ ...theme, settings: { ...(theme.settings || {}), tokens, font_url } })}
      />

      {/* Blocks list */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-zinc-700">Bloklar ({theme.blocks.length})</h2>
          <button onClick={addBlock} className="px-3 py-2 text-xs font-medium border border-dashed border-zinc-300 rounded hover:bg-zinc-50 flex items-center gap-1.5" data-testid="btn-add-block"><Plus size={13}/> Blok Ekle</button>
        </div>
        {theme.blocks.map((b, idx) => (
          <BlockRow
            key={b.id}
            block={b}
            idx={idx}
            total={theme.blocks.length}
            onChange={(patch) => updateBlock(b.id, patch)}
            onMove={(dir) => moveBlock(idx, dir)}
            onRemove={() => removeBlock(b.id)}
            onUpload={(field, file) => uploadImage(b.id, field, file)}
          />
        ))}
      </div>
    </div>
  );
}

function BlockRow({ block, idx, total, onChange, onMove, onRemove, onUpload }) {
  return (
    <div className="bg-white border border-zinc-200 rounded p-4" data-testid={`block-row-${block.id}`}>
      <div className="flex items-center gap-3 mb-3">
        <div className="flex flex-col">
          <button onClick={() => onMove(-1)} disabled={idx === 0} className="text-zinc-400 hover:text-zinc-900 disabled:opacity-30 text-xs">▲</button>
          <span className="text-[10px] text-zinc-400 text-center">{idx + 1}</span>
          <button onClick={() => onMove(1)} disabled={idx === total - 1} className="text-zinc-400 hover:text-zinc-900 disabled:opacity-30 text-xs">▼</button>
        </div>
        <GripVertical size={16} className="text-zinc-300" />
        <select value={block.type} onChange={e => onChange({ type: e.target.value })} className="px-2 py-1.5 border border-zinc-200 rounded text-xs font-medium">
          {BLOCK_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <input value={block.title || ""} onChange={e => onChange({ title: e.target.value })} placeholder="Başlık" className="flex-1 px-3 py-1.5 border border-zinc-200 rounded text-sm font-medium" />
        <label className="text-xs flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={!!block.is_active} onChange={e => onChange({ is_active: e.target.checked })} />
          Aktif
        </label>
        <button onClick={onRemove} className="text-rose-600 hover:bg-rose-50 p-1.5 rounded" data-testid={`btn-remove-block-${block.id}`}><Trash2 size={14}/></button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 ml-12">
        <div>
          <label className="text-[11px] font-semibold text-zinc-600 uppercase tracking-wider">Alt başlık / metin</label>
          <input value={block.subtitle || ""} onChange={e => onChange({ subtitle: e.target.value })} className="mt-1 w-full px-3 py-2 border border-zinc-200 rounded text-sm" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px] font-semibold text-zinc-600 uppercase tracking-wider">Buton metni</label>
            <input value={block.link_label || ""} onChange={e => onChange({ link_label: e.target.value })} className="mt-1 w-full px-3 py-2 border border-zinc-200 rounded text-sm" />
          </div>
          <div>
            <label className="text-[11px] font-semibold text-zinc-600 uppercase tracking-wider">Link (URL)</label>
            <input value={block.link_url || ""} onChange={e => onChange({ link_url: e.target.value })} className="mt-1 w-full px-3 py-2 border border-zinc-200 rounded text-sm" />
          </div>
        </div>

        {(block.type === "hero_fullscreen" || block.type === "editorial_card") && (
          <>
            <ImageField label="Masaüstü Görsel" value={block.image} onChange={(v) => onChange({ image: v })} onUpload={(f) => onUpload("image", f)} />
            <ImageField label="Mobil Görsel" value={block.mobile_image} onChange={(v) => onChange({ mobile_image: v })} onUpload={(f) => onUpload("mobile_image", f)} />
          </>
        )}

        {block.type === "product_scroller" && (
          <div className="md:col-span-2 grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] font-semibold text-zinc-600 uppercase tracking-wider">Kategori Slug</label>
              <input value={block.settings?.category_slug || ""} onChange={e => onChange({ settings: { ...block.settings, category_slug: e.target.value } })} className="mt-1 w-full px-3 py-2 border border-zinc-200 rounded text-sm" />
            </div>
            <div>
              <label className="text-[11px] font-semibold text-zinc-600 uppercase tracking-wider">Ürün adedi (limit)</label>
              <input type="number" value={block.settings?.limit || 12} onChange={e => onChange({ settings: { ...block.settings, limit: parseInt(e.target.value || "12", 10) } })} className="mt-1 w-full px-3 py-2 border border-zinc-200 rounded text-sm" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ImageField({ label, value, onChange, onUpload }) {
  const inputRef = React.useRef(null);
  return (
    <div>
      <label className="text-[11px] font-semibold text-zinc-600 uppercase tracking-wider">{label}</label>
      <div className="mt-1 flex items-center gap-2">
        {value && <img src={value} alt="" className="w-14 h-14 object-cover border border-zinc-200 rounded" />}
        <input value={value || ""} onChange={e => onChange(e.target.value)} placeholder="https://… veya yükle" className="flex-1 px-3 py-2 border border-zinc-200 rounded text-xs" />
        <button onClick={() => inputRef.current?.click()} className="px-2 py-2 border border-zinc-300 rounded hover:bg-zinc-50 text-xs flex items-center gap-1"><Upload size={12}/> Yükle</button>
        <input ref={inputRef} type="file" accept="image/*" className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) onUpload(f); }} />
      </div>
    </div>
  );
}
