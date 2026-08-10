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
