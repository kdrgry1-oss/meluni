import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { FileText, Link2, Plus, Trash2, ArrowRight, Eye, Save } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

export function SeoRedirects() {
  const [items, setItems] = useState([]);
  const [form, setForm] = useState({ from_path: "", to_path: "", status_code: 301 });
  const load = async () => {
    const { data } = await axios.get(`${API}/admin/seo/redirects`, { headers: authHeaders() });
    setItems(data.items || []);
  };
  useEffect(() => { load(); }, []);
  const save = async () => {
    if (!form.from_path || !form.to_path) return toast.warning("Zorunlu alanlar");
    try {
      await axios.post(`${API}/admin/seo/redirects`, form, { headers: authHeaders() });
      toast.success("Eklendi"); setForm({ from_path: "", to_path: "", status_code: 301 }); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Hata"); }
  };
  const del = async (id) => {
    if (!await window.appConfirm("Silinsin mi?")) return;
    await axios.delete(`${API}/admin/seo/redirects/${id}`, { headers: authHeaders() });
    load();
  };
  return (
    <div className="space-y-5" data-testid="seo-redirects-page">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Link2 /> 301/302 Yönlendirmeler</h1>
        <p className="text-sm text-gray-500 mt-1">SEO için eski URL'leri yeni yola yönlendirin.</p>
      </div>

      <div className="bg-white rounded-xl border p-5 flex gap-2 items-end flex-wrap">
        <div className="flex-1 min-w-[200px]">
          <label className="text-xs text-gray-600">Eski URL (from)</label>
          <input value={form.from_path} onChange={(e) => setForm({ ...form, from_path: e.target.value })} placeholder="/eski-kategori"
            data-testid="redirect-from" className="w-full mt-1 px-3 py-2 border rounded text-sm font-mono" />
        </div>
        <div className="flex-1 min-w-[200px]">
          <label className="text-xs text-gray-600">Yeni URL (to)</label>
          <input value={form.to_path} onChange={(e) => setForm({ ...form, to_path: e.target.value })} placeholder="/yeni-kategori"
            data-testid="redirect-to" className="w-full mt-1 px-3 py-2 border rounded text-sm font-mono" />
        </div>
        <div>
          <label className="text-xs text-gray-600">Kod</label>
          <select value={form.status_code} onChange={(e) => setForm({ ...form, status_code: parseInt(e.target.value) })} className="mt-1 px-3 py-2 border rounded text-sm">
            <option value={301}>301 (Kalıcı)</option>
            <option value={302}>302 (Geçici)</option>
          </select>
        </div>
        <button onClick={save} data-testid="add-redirect" className="px-4 py-2 bg-black text-white rounded text-sm inline-flex items-center gap-1"><Plus size={14} /> Ekle</button>
      </div>

      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr><th className="text-left p-3">Eski URL</th><th className="p-3"></th><th className="text-left p-3">Yeni URL</th><th className="text-center p-3">Kod</th><th className="text-center p-3">Hit</th><th className="text-right p-3">İşlem</th></tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={6} className="p-6 text-center text-gray-400">Henüz yönlendirme yok.</td></tr>
            ) : items.map((r) => (
              <tr key={r.id} className="border-t">
                <td className="p-3 font-mono text-xs text-gray-700">{r.from_path}</td>
                <td className="p-3 text-gray-400"><ArrowRight size={14} /></td>
                <td className="p-3 font-mono text-xs text-gray-700">{r.to_path}</td>
                <td className="p-3 text-center"><span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded">{r.status_code}</span></td>
                <td className="p-3 text-center text-gray-600">{r.hits || 0}</td>
                <td className="p-3 text-right">
                  <button onClick={() => del(r.id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded"><Trash2 size={14} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function SeoMeta() {
  const [items, setItems] = useState([]);
  const [form, setForm] = useState({ path: "", title: "", description: "", og_image: "", noindex: false });
  const [config, setConfig] = useState(null);
  const [previewKind, setPreviewKind] = useState("all");
  const [preview, setPreview] = useState([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const load = async () => {
    const { data } = await axios.get(`${API}/admin/seo/meta`, { headers: authHeaders() });
    setItems(data.items || []);
  };
  useEffect(() => { load(); }, []);
  useEffect(() => {
    axios.get(`${API}/admin/seo/config`, { headers: authHeaders() })
      .then(({ data }) => setConfig(data)).catch(() => toast.error("SEO şablonları yüklenemedi"));
  }, []);
  const saveConfig = async () => {
    try {
      const { data } = await axios.put(`${API}/admin/seo/config`, config, { headers: authHeaders() });
      setConfig(data.seo_geo); toast.success("Global SEO/GEO şablonları kaydedildi");
    } catch (e) { toast.error(e?.response?.data?.detail || "Şablonlar kaydedilemedi"); }
  };
  const runPreview = async () => {
    setPreviewLoading(true);
    try {
      const { data } = await axios.get(`${API}/admin/seo/bulk-preview`, {
        headers: authHeaders(), params: { kind: previewKind, limit: 200 },
      });
      setPreview(data.items || []);
      toast.success(`${data.count || 0} kayıt dry-run olarak hazırlandı`);
    } catch (e) { toast.error(e?.response?.data?.detail || "Önizleme oluşturulamadı"); }
    finally { setPreviewLoading(false); }
  };
  const save = async () => {
    if (!form.path) return toast.warning("Path zorunlu");
    await axios.post(`${API}/admin/seo/meta`, form, { headers: authHeaders() });
    toast.success("Kaydedildi"); setForm({ path: "", title: "", description: "", og_image: "", noindex: false }); load();
  };
  const del = async (id) => {
    if (!await window.appConfirm("Silinsin mi?")) return;
    await axios.delete(`${API}/admin/seo/meta/${id}`, { headers: authHeaders() });
    load();
  };
  return (
    <div className="space-y-5" data-testid="seo-meta-page">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><FileText /> SEO Meta Yönetimi</h1>
        <p className="text-sm text-gray-500 mt-1">Global otomatik şablonları yönetin; manuel kayıtlar her zaman önceliklidir.</p>
      </div>

      {config && (
        <div className="bg-white rounded-xl border p-5 space-y-4" data-testid="seo-global-config">
          <div className="flex items-start justify-between gap-3">
            <div><h2 className="font-semibold">Global SEO / GEO Şablonları</h2><p className="text-xs text-gray-500 mt-1">Yalnız kayıtlı gerçek alanlar kullanılır. Desteklenen tokenlar: {'{store_name}'}, {'{product_name}'}, {'{category_name}'}, {'{brand}'}, {'{price}'}, {'{currency}'}, {'{city}'}, {'{country}'}.</p></div>
            <button onClick={saveConfig} className="px-4 py-2 bg-black text-white rounded text-sm inline-flex items-center gap-1"><Save size={14} /> Kaydet</button>
          </div>
          <div className="grid md:grid-cols-2 gap-3">
            <div><label className="text-xs text-gray-600">Global varsayılan başlık</label><input value={config.default_title || ""} onChange={(e) => setConfig({ ...config, default_title: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
            <div><label className="text-xs text-gray-600">Global varsayılan açıklama</label><textarea rows={2} value={config.default_description || ""} onChange={(e) => setConfig({ ...config, default_description: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
            {[
              ["product_title_template", "Ürün başlık şablonu"],
              ["product_description_template", "Ürün açıklama şablonu"],
              ["category_title_template", "Kategori başlık şablonu"],
              ["category_description_template", "Kategori açıklama şablonu"],
            ].map(([key, label]) => <div key={key}><label className="text-xs text-gray-600">{label}</label><textarea rows={2} value={config[key] || ""} onChange={(e) => setConfig({ ...config, [key]: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm font-mono" /></div>)}
            <div><label className="text-xs text-gray-600">Organization şema açıklaması</label><textarea rows={2} value={config.organization_description || ""} onChange={(e) => setConfig({ ...config, organization_description: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
            <div><label className="text-xs text-gray-600">llms.txt mağaza açıklaması</label><textarea rows={2} value={config.llms_description || ""} onChange={(e) => setConfig({ ...config, llms_description: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
            <div><label className="text-xs text-gray-600">Varsayılan OG görsel URL</label><input value={config.default_og_image_url || ""} onChange={(e) => setConfig({ ...config, default_og_image_url: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm" /></div>
            <div><label className="text-xs text-gray-600">Robots varsayılanı</label><select value={config.default_robots || "index,follow"} onChange={(e) => setConfig({ ...config, default_robots: e.target.value })} className="w-full mt-1 px-3 py-2 border rounded text-sm"><option>index,follow</option><option>noindex,follow</option><option>noindex,nofollow</option></select></div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.auto_generate_products !== false} onChange={(e) => setConfig({ ...config, auto_generate_products: e.target.checked })} /> Ürünlerde otomatik üret</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.auto_generate_categories !== false} onChange={(e) => setConfig({ ...config, auto_generate_categories: e.target.checked })} /> Kategorilerde otomatik üret</label>
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl border p-5 space-y-3" data-testid="seo-bulk-preview">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div><h2 className="font-semibold">Ürün / Kategori Toplu Önizleme</h2><p className="text-xs text-gray-500">Salt okunur dry-run; bu buton hiçbir ürünü veya kategoriyi güncellemez.</p></div>
          <div className="flex gap-2"><select value={previewKind} onChange={(e) => setPreviewKind(e.target.value)} className="border rounded px-3 py-2 text-sm"><option value="all">Tümü</option><option value="product">Ürünler</option><option value="category">Kategoriler</option></select><button onClick={runPreview} disabled={previewLoading} className="px-4 py-2 border border-black rounded text-sm inline-flex items-center gap-1 disabled:opacity-50"><Eye size={14} /> {previewLoading ? "Hazırlanıyor…" : "Dry-run Önizle"}</button></div>
        </div>
        {preview.length > 0 && <div className="overflow-auto max-h-[420px] border rounded"><table className="w-full text-xs"><thead className="bg-gray-50 sticky top-0"><tr><th className="text-left p-2">Tür / Kayıt</th><th className="text-left p-2">Başlık önizleme</th><th className="text-left p-2">Açıklama önizleme</th><th className="text-left p-2">Kaynak</th></tr></thead><tbody>{preview.map((row) => <tr key={`${row.kind}-${row.id}`} className="border-t align-top"><td className="p-2"><span className="uppercase text-[9px] text-gray-400">{row.kind}</span><div className="font-medium">{row.name || row.id}</div></td><td className="p-2 max-w-xs">{row.generated?.title || <span className="text-amber-600">Veri yok</span>}</td><td className="p-2 max-w-sm">{row.generated?.description || <span className="text-amber-600">Veri yok</span>}</td><td className="p-2 font-mono text-[10px]">{row.generated?.sources?.title} / {row.generated?.sources?.description}</td></tr>)}</tbody></table></div>}
      </div>

      <div className="bg-white rounded-xl border p-5 space-y-3">
        <h2 className="font-semibold">Manuel URL Override</h2>
        <div>
          <label className="text-xs text-gray-600">URL Path *</label>
          <input value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} placeholder="/kategori/kadin-elbise"
            data-testid="meta-path" className="w-full mt-1 px-3 py-2 border rounded text-sm font-mono" />
        </div>
        <div>
          <label className="text-xs text-gray-600">Title</label>
          <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={60}
            className="w-full mt-1 px-3 py-2 border rounded text-sm" />
          <div className="text-[10px] text-gray-400">{form.title.length}/60</div>
        </div>
        <div>
          <label className="text-xs text-gray-600">Description</label>
          <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={2} maxLength={160}
            className="w-full mt-1 px-3 py-2 border rounded text-sm" />
          <div className="text-[10px] text-gray-400">{form.description.length}/160</div>
        </div>
        <div>
          <label className="text-xs text-gray-600">OG Image URL</label>
          <input value={form.og_image} onChange={(e) => setForm({ ...form, og_image: e.target.value })}
            className="w-full mt-1 px-3 py-2 border rounded text-sm" />
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.noindex} onChange={(e) => setForm({ ...form, noindex: e.target.checked })} /> noindex (Google'a indekslenmesin)</label>
        <button onClick={save} data-testid="save-meta" className="px-4 py-2 bg-black text-white rounded text-sm inline-flex items-center gap-1"><Plus size={14} /> Kaydet / Güncelle</button>
      </div>

      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr><th className="text-left p-3">Path</th><th className="text-left p-3">Title</th><th className="text-center p-3">noindex</th><th className="text-right p-3">İşlem</th></tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={4} className="p-6 text-center text-gray-400">Henüz meta override yok.</td></tr>
            ) : items.map((m) => (
              <tr key={m.id} className="border-t">
                <td className="p-3 font-mono text-xs">{m.path}</td>
                <td className="p-3">{m.title}</td>
                <td className="p-3 text-center">{m.noindex ? "✓" : "—"}</td>
                <td className="p-3 text-right">
                  <button onClick={() => setForm({ ...m })} className="text-xs text-blue-600 hover:underline mr-2">Düzenle</button>
                  <button onClick={() => del(m.id)} className="p-1 text-red-600 hover:bg-red-50 rounded inline"><Trash2 size={13} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
